mod cli;

use contextd_core::claim::{
    ClaimConfidence, ClaimError, ClaimEvent, ClaimKind, ClaimOp, ClaimOrigin, FRONTIER_SUBJECT,
    INTENT_SUBJECT, NO_ACTIVE_TASK, Projection, check_text, validate, validate_against_projection,
};
use contextd_core::config::AppConfig;
use contextd_core::event::{EventSource, ProcessedEvent, RawEvent};
use contextd_core::protocol::{ContextRequest, UserClaimKind};
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
        ContextRequest::Now { text: _ } => broker::snapshot::build(
            broker::snapshot::SnapshotRequest::new(store, "")
                .with_ollama(ollama)
                .with_tiers(Some(&tiers.working), Some(&tiers.graph)),
        )
        .await
        .and_then(|snapshot| Ok(serde_json::to_value(snapshot)?)),
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
        ContextRequest::Intent { text } => record_user_intent(tiers, text).await,
        ContextRequest::UserClaim { kind, text } => {
            record_user_claim(tiers, *kind, text.as_deref()).await
        }
        ContextRequest::AgentClaim {
            kind,
            text,
            confidence,
            replaces,
        } => record_agent_claim(tiers, *kind, text, *confidence, replaces.as_deref()).await,
    };

    match result {
        Ok(value) => value.to_string(),
        Err(error) => serde_json::json!({ "error": error.to_string() }).to_string(),
    }
}

async fn current_projection(tiers: &Tiers) -> anyhow::Result<Projection> {
    let conn = tiers.store.reader()?;
    Ok(store::db::load_projection(&conn)?)
}

fn mint_event_id() -> String {
    Ulid::new().to_string()
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|elapsed| elapsed.as_millis() as u64)
        .unwrap_or_default()
}

fn claim_raw(event_id: String, timestamp_ms: u64, body: ClaimEvent) -> RawEvent {
    RawEvent {
        id: event_id,
        timestamp_ms,
        source: EventSource::Claim,
        payload: serde_json::to_value(body).unwrap_or_else(|_| serde_json::json!({})),
    }
}

async fn append_and_rebuild(
    tiers: &Tiers,
    raw: RawEvent,
) -> anyhow::Result<contextd_core::claim::Projection> {
    let processed = ProcessedEvent::new(raw, 1.0).with_session(tiers.working.session_id());
    let conn = tiers.store.writer().await;
    store::db::insert_claim_event(&conn, &processed)?;
    Ok(memory::project::rebuild(&conn)?)
}

fn write_ok(claim_id: &str, task_id: &str) -> anyhow::Result<serde_json::Value> {
    Ok(serde_json::json!({
        "ok": true,
        "claim_id": claim_id,
        "task_id": task_id
    }))
}

fn map_claim_err(error: ClaimError) -> anyhow::Error {
    anyhow::anyhow!(error.0)
}

async fn record_user_intent(tiers: &Tiers, text: &str) -> anyhow::Result<serde_json::Value> {
    let text = check_text(text).map_err(map_claim_err)?;
    let projection = current_projection(tiers).await?;
    let event_id = mint_event_id();
    let timestamp_ms = now_ms();
    let task_id = memory::project::focal_task(&projection)
        .map(|task| task.id.clone())
        .unwrap_or_else(|| memory::project::hashed_id("t_", &[&event_id]));
    let claim_id = format!("c_{event_id}");
    let body = ClaimEvent {
        op: ClaimOp::Assert,
        claim_id: claim_id.clone(),
        task_id: task_id.clone(),
        kind: Some(ClaimKind::Intent),
        subject: Some(INTENT_SUBJECT.to_string()),
        text: Some(text.clone()),
        confidence: Some(ClaimConfidence::Stated),
        origin: ClaimOrigin::User,
        outcome: None,
        artifact_refs: Vec::new(),
        evidence_ids: Vec::new(),
        supersedes: None,
    };
    validate(ClaimOrigin::User, &body).map_err(map_claim_err)?;
    append_and_rebuild(tiers, claim_raw(event_id, timestamp_ms, body)).await?;
    info!("Intent recorded: {text}");
    write_ok(&claim_id, &task_id)
}

async fn record_user_claim(
    tiers: &Tiers,
    kind: UserClaimKind,
    text: Option<&str>,
) -> anyhow::Result<serde_json::Value> {
    let projection = current_projection(tiers).await?;
    let focal = memory::project::focal_task(&projection);
    let event_id = mint_event_id();
    let timestamp_ms = now_ms();

    if kind == UserClaimKind::Done {
        let task = focal.ok_or_else(|| anyhow::anyhow!(NO_ACTIVE_TASK))?;
        let body = ClaimEvent {
            op: ClaimOp::CloseTask,
            claim_id: format!("c_{event_id}"),
            task_id: task.id.clone(),
            kind: None,
            subject: None,
            text: None,
            confidence: None,
            origin: ClaimOrigin::User,
            outcome: None,
            artifact_refs: Vec::new(),
            evidence_ids: Vec::new(),
            supersedes: None,
        };
        validate(ClaimOrigin::User, &body).map_err(map_claim_err)?;
        append_and_rebuild(tiers, claim_raw(event_id, timestamp_ms, body)).await?;
        return write_ok("", &task.id);
    }

    if kind == UserClaimKind::Retract {
        let claim_id = check_text(text.unwrap_or("")).map_err(map_claim_err)?;
        let body = ClaimEvent {
            op: ClaimOp::Supersede,
            claim_id: claim_id.clone(),
            task_id: projection
                .claim(&claim_id)
                .map(|claim| claim.task_id.clone())
                .unwrap_or_default(),
            kind: None,
            subject: None,
            text: None,
            confidence: None,
            origin: ClaimOrigin::User,
            outcome: None,
            artifact_refs: Vec::new(),
            evidence_ids: Vec::new(),
            supersedes: Some(claim_id.clone()),
        };
        validate(ClaimOrigin::User, &body).map_err(map_claim_err)?;
        validate_against_projection(ClaimOrigin::User, &body, &projection)
            .map_err(map_claim_err)?;
        let task_id = body.task_id.clone();
        append_and_rebuild(tiers, claim_raw(event_id, timestamp_ms, body)).await?;
        return write_ok(&claim_id, &task_id);
    }

    let task = focal.ok_or_else(|| anyhow::anyhow!(NO_ACTIVE_TASK))?;
    let text = check_text(text.unwrap_or("")).map_err(map_claim_err)?;
    let claim_kind = match kind {
        UserClaimKind::Constraint => ClaimKind::Constraint,
        UserClaimKind::Decision => ClaimKind::Decision,
        UserClaimKind::RuledOut => ClaimKind::RuledOut,
        UserClaimKind::Retract | UserClaimKind::Done => unreachable!(),
    };
    let claim_id = format!("c_{event_id}");
    let body = ClaimEvent {
        op: ClaimOp::Assert,
        claim_id: claim_id.clone(),
        task_id: task.id.clone(),
        kind: Some(claim_kind),
        subject: Some(claim_id.clone()),
        text: Some(text),
        confidence: Some(ClaimConfidence::Stated),
        origin: ClaimOrigin::User,
        outcome: None,
        artifact_refs: Vec::new(),
        evidence_ids: Vec::new(),
        supersedes: None,
    };
    validate(ClaimOrigin::User, &body).map_err(map_claim_err)?;
    append_and_rebuild(tiers, claim_raw(event_id, timestamp_ms, body)).await?;
    write_ok(&claim_id, &task.id)
}

async fn record_agent_claim(
    tiers: &Tiers,
    kind: ClaimKind,
    text: &str,
    confidence: Option<ClaimConfidence>,
    replaces: Option<&str>,
) -> anyhow::Result<serde_json::Value> {
    let projection = current_projection(tiers).await?;
    let task =
        memory::project::focal_task(&projection).ok_or_else(|| anyhow::anyhow!(NO_ACTIVE_TASK))?;
    let text = check_text(text).map_err(map_claim_err)?;
    let mut confidence = confidence.unwrap_or(ClaimConfidence::Inferred);
    if kind == ClaimKind::Unknown {
        confidence = ClaimConfidence::Hypothesis;
    }
    let event_id = mint_event_id();
    let timestamp_ms = now_ms();
    let claim_id = format!("c_{event_id}");
    let subject = if kind == ClaimKind::NextAction {
        FRONTIER_SUBJECT.to_string()
    } else {
        claim_id.clone()
    };
    let mut supersedes = replaces.map(str::to_string);
    if kind == ClaimKind::NextAction
        && let Some(frontier) =
            projection.current_slot(&task.id, ClaimKind::NextAction, FRONTIER_SUBJECT)
        && frontier.origin != ClaimOrigin::User
    {
        supersedes = Some(frontier.id.clone());
    }
    let body = ClaimEvent {
        op: ClaimOp::Assert,
        claim_id: claim_id.clone(),
        task_id: task.id.clone(),
        kind: Some(kind),
        subject: Some(subject),
        text: Some(text),
        confidence: Some(confidence),
        origin: ClaimOrigin::Agent,
        outcome: None,
        artifact_refs: Vec::new(),
        evidence_ids: Vec::new(),
        supersedes,
    };
    validate(ClaimOrigin::Agent, &body).map_err(map_claim_err)?;
    validate_against_projection(ClaimOrigin::Agent, &body, &projection).map_err(map_claim_err)?;
    append_and_rebuild(tiers, claim_raw(event_id, timestamp_ms, body)).await?;
    write_ok(&claim_id, &task.id)
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
        cli::Command::Note(text) => cli::run_note(&config, &text),
        cli::Command::Claim(claim) => cli::run_claim(&config, claim).await,
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
    {
        let conn = store.writer().await;
        if let Err(error) = memory::project::rebuild(&conn) {
            error!("Failed to rebuild working-state projection: {error}");
        }
    }

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

    // One daemon run is one session.
    let session_id = Ulid::new().to_string();
    info!("Session {session_id}");

    // Tier 0. Everything here is also in Tier 1; this exists so the common
    // question — "what am I doing right now" — never touches the disk.
    let working = memory::WorkingSet::new(&session_id);

    // Everything expensive happens here, off the ingest path.
    let enrichment =
        background::enrichment::start(Arc::clone(&store), ollama.clone(), working.clone());

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

                if pipeline::decision::should_drop(&processed_event) {
                    debug!(
                        event_id = processed_event.raw.id.as_str(),
                        "skipped as noise before storage"
                    );
                    continue;
                }

                // No model I/O on the hot path. Noise is refused here because
                // working memory and the graph cannot retract a Drop.
                {
                    let conn = store.writer().await;
                    if let Err(e) = store::db::insert_event(&conn, &processed_event) {
                        error!("Failed to write event to database: {}", e);
                        continue;
                    }
                    let has_exit = processed_event.raw.source
                        == contextd_core::event::EventSource::Shell
                        && processed_event
                            .raw
                            .payload
                            .get("exit_code")
                            .and_then(|value| value.as_i64())
                            .is_some();
                    if has_exit && let Err(e) = memory::project::rebuild(&conn) {
                        error!("Failed to rebuild working-state projection: {e}");
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
