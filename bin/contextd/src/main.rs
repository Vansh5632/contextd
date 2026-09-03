mod cli;

use contextd_core::config::AppConfig;
use contextd_core::protocol::ContextRequest;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};
use store::Store;
use tokio::sync::broadcast;
use tracing::{debug, error, info, warn};
use tracing_subscriber::EnvFilter;
use ulid::Ulid;

/// How many events can be in flight on the bus before slow consumers start
/// missing them. Sized for a burst like a `cargo build` touching many files.
const EVENT_BUS_CAPACITY: usize = 1024;

/// The live state a query is answered against.
///
/// Tiers 1 and 3 live in the `Store`; Tiers 0 and 2 only exist while the daemon
/// is running, so they have to be carried to the query handler explicitly.
#[derive(Clone)]
struct Tiers {
    store: Arc<Store>,
    working: memory::WorkingSet,
    graph: memory::SharedGraph,
}

/// Serve one agent request. Always returns a JSON body: errors are reported in
/// band as `{"error": ...}` so a caller never has to guess why the line is short.
async fn answer_query(
    tiers: &Tiers,
    ollama: Option<&ai::ollama::OllamaClient>,
    request: ContextRequest,
) -> String {
    let store = tiers.store.as_ref();

    let result = match &request {
        ContextRequest::Now { text } => {
            let query = text.as_deref().unwrap_or_default();
            broker::snapshot::build(
                broker::snapshot::SnapshotRequest::new(store, query)
                    .with_ollama(ollama)
                    .with_tiers(Some(&tiers.working), Some(&tiers.graph)),
            )
            .await
            .and_then(|snapshot| Ok(serde_json::to_value(snapshot)?))
        }
        ContextRequest::Search { text, limit } => {
            let limit = ContextRequest::resolve_limit(*limit);
            broker::search::search(store, ollama, text, limit)
                .await
                .and_then(|results| Ok(serde_json::to_value(results)?))
        }
        ContextRequest::Recall { text, limit } => {
            let limit = ContextRequest::resolve_limit(*limit);
            broker::search::recall(store, ollama, text, limit)
                .await
                .and_then(|results| Ok(serde_json::to_value(results)?))
        }
        ContextRequest::Intent { text } => record_intent(tiers, text).await,
    };

    match result {
        Ok(value) => value.to_string(),
        Err(error) => serde_json::json!({ "error": error.to_string() }).to_string(),
    }
}

async fn record_intent(tiers: &Tiers, text: &str) -> anyhow::Result<serde_json::Value> {
    let declared_at_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|elapsed| elapsed.as_millis() as u64)
        .unwrap_or_default();

    // Both tiers: the database so it survives a restart, Tier 0 so the very
    // next briefing reflects it without a read.
    {
        let conn = tiers.store.writer().await;
        store::db::set_intent(&conn, text, declared_at_ms)?;
    }
    tiers.working.set_intent(contextd_core::event::Intent {
        text: text.to_string(),
        declared_at_ms,
    });

    info!("Intent recorded: {text}");
    Ok(serde_json::json!({
        "ok": true,
        "intent": { "text": text, "declared_at_ms": declared_at_ms }
    }))
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // The MCP child speaks JSON-RPC on stdout, so logs go to stderr — always,
    // for every subcommand, since anything on stdout would corrupt the stream.
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env().add_directive(tracing::Level::INFO.into()))
        .with_writer(std::io::stderr)
        .init();

    let args: Vec<String> = std::env::args().skip(1).collect();
    let command = match cli::parse(&args) {
        Ok(command) => command,
        Err(message) => {
            eprintln!("{message}");
            std::process::exit(2);
        }
    };

    // A missing or broken config file must not stop the daemon starting: the
    // defaults are the ones almost everyone runs anyway.
    let config = AppConfig::load_or_default();

    match command {
        cli::Command::Help => {
            print!("{}", cli::USAGE);
            Ok(())
        }
        // Cursor/Claude launch this as a stdio child. The daemon stays in the
        // background; this process only asks it for the briefing.
        cli::Command::Mcp => mcp::stdio::run(config.socket_path).await,
        cli::Command::Intent(text) => cli::run_intent(&config, &text).await,
        cli::Command::Status => cli::run_status(&config).await,
        cli::Command::Install => cli::run_install(&config),
        cli::Command::Uninstall => cli::run_uninstall(),
        cli::Command::Run => run_daemon(config).await,
    }
}

async fn run_daemon(config: AppConfig) -> anyhow::Result<()> {
    info!("Starting contextd daemon...");

    config.ensure_directories()?;

    // 3. Open the database. The Store owns one writer and a pool of readers, so
    // an agent query never waits behind ingest or the pruner.
    let store = Arc::new(store::Store::open(&config)?);
    info!("Database initialized at {:?}", config.db_path);

    // Tier 2 owns its own tables, so it migrates them itself rather than making
    // the storage crate depend on the crate that sits above it.
    {
        let conn = store.writer().await;
        memory::graph_store::migrate(&conn)?;
    }
    let graph = {
        let conn = store.reader()?;
        memory::SharedGraph::load(&conn)
    };
    memory::tier2::start_flush_worker(Arc::clone(&store), graph.clone());

    // AI is optional. Keep the client so we can embed later.
    // If Ollama is down, events still save — embeddings are skipped.
    let ollama = match ai::ollama::OllamaClient::from_config(&config) {
        Ok(client) => {
            if client.check_health().await {
                info!("Ollama is online and reachable. AI Pipeline features are ENABLED.");
            } else {
                warn!("Ollama is offline. Events still save; embeddings will be skipped.");
            }
            Some(client)
        }
        Err(e) => {
            warn!("Failed to initialize Ollama client: {}", e);
            None
        }
    };

    // Everything expensive happens here, off the ingest path.
    let enrichment = background::enrichment::start(Arc::clone(&store), ollama.clone());

    // One daemon run is one session.
    let session_id = Ulid::new().to_string();
    info!("Session {session_id}");

    // Tier 0. Everything here is also in Tier 1; this exists so the common
    // question — "what am I doing right now" — never touches the disk.
    let working = memory::WorkingSet::new(&session_id);

    let tiers = Tiers {
        store: Arc::clone(&store),
        working: working.clone(),
        graph: graph.clone(),
    };

    // 4. Create the central event bus
    let (tx, mut rx) = broadcast::channel(EVENT_BUS_CAPACITY);

    // 5. Start the Unix Socket listener in the background
    let socket_path = config.socket_path.clone();
    let listener_tx = tx.clone();
    let (query_tx, mut query_rx) = tokio::sync::mpsc::channel::<sources::shell::ContextQuery>(32);
    let query_tiers = tiers.clone();
    let snapshot_ollama = ollama.clone();
    tokio::spawn(async move {
        while let Some(req) = query_rx.recv().await {
            // Serve each query on its own task so a slow embedding call does
            // not hold up the next agent asking a question.
            let tiers = query_tiers.clone();
            let ollama = snapshot_ollama.clone();
            tokio::spawn(async move {
                let body = answer_query(&tiers, ollama.as_ref(), req.request).await;
                let _ = req.reply.send(body);
            });
        }
    });

    tokio::spawn(async move {
        if let Err(e) =
            sources::shell::start_shell_listener(socket_path, listener_tx, query_tx).await
        {
            error!("Shell listener crashed: {}", e);
        }
    });

    // 6. Start the process poller in the background
    let proc_tx = tx.clone();
    tokio::spawn(async move {
        sources::proc_poller::start_proc_poller(proc_tx).await;
    });

    // 7. Start the filesystem watcher in the background
    let fs_tx = tx.clone();
    let fs_root = cli::watch_root(&config)?;
    info!("Watching {}", fs_root.display());
    if let Some(git_root) = sources::git::find_git_root(&fs_root) {
        if let Err(e) = sources::git::install_hooks(&git_root, &config.socket_path) {
            error!(
                "Failed to install git hooks in Git root {}: {}",
                git_root.display(),
                e
            );
        }
    } else {
        debug!("No Git repository found from current directory; skipping git hook installation");
    }

    let manifest_rx = tx.subscribe();
    let manifest_tx = tx.clone();
    tokio::spawn(async move {
        sources::manifest::start_manifest_watcher(manifest_rx, manifest_tx).await;
    });

    // Without this the daemon records its own database writes and every
    // briefing becomes a list of contextd modifying contextd.
    let noise_filter = sources::noise::NoiseFilter::for_config(&config);
    tokio::spawn(async move {
        if let Err(e) =
            sources::filesystem::start_filesystem_watcher(fs_root, fs_tx, noise_filter).await
        {
            error!("Filesystem watcher crashed: {}", e);
        }
    });

    let pruner_store = Arc::clone(&store);
    tokio::spawn(async move {
        background::pruner::start_pruning_worker(pruner_store).await;
    });

    // 8. The Main Loop: Read from channel, write to DB
    info!("Daemon is running and listening for events.");

    loop {
        match rx.recv().await {
            Ok(event) => {
                let processed_event =
                    pipeline::heuristics::process_event(event).with_session(&session_id);
                debug!(
                    "Received event from {:?}: {} score={} payload={}",
                    processed_event.raw.source,
                    processed_event.raw.id,
                    processed_event.score,
                    processed_event.raw.payload
                );

                // The only thing on the hot path: write the row. Anything that
                // could block or fail on a model runs later.
                {
                    let conn = store.writer().await;
                    if let Err(e) = store::db::insert_event(&conn, &processed_event) {
                        error!("Failed to write event to database: {}", e);
                        continue;
                    }
                }

                working.record(processed_event.clone());
                graph.observe(&processed_event).await;
                enrichment.offer(&processed_event.raw.id);
            }
            Err(broadcast::error::RecvError::Lagged(missed)) => {
                error!("Database writer is too slow! Missed {} events.", missed);
            }
            Err(broadcast::error::RecvError::Closed) => {
                error!("Event bus closed unexpectedly. Shutting down.");
                break;
            }
        }
    }

    Ok(())
}
