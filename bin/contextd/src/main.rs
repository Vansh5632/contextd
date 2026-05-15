use contextd_core::config::AppConfig;
use std::sync::Arc;
use tokio::sync::{broadcast, Mutex};
use tracing::{debug, error, info, warn};
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // 1. Initialize logging (defaults to INFO level)
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env().add_directive(tracing::Level::INFO.into()))
        .init();

    info!("Starting contextd daemon...");

    // 2. Load config
    let config = AppConfig::default();

    // Ensure the directories exist
    if let Some(parent) = config.db_path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    if let Some(parent) = config.socket_path.parent() {
        std::fs::create_dir_all(parent)?;
    }

    // 3. Initialize the database
    // We wrap it in Arc<Mutex<>> so it can be safely shared across async tasks
    let db_conn = store::db::init_db(&config)?;
    let db = Arc::new(Mutex::new(db_conn));
    info!("Database initialized at {:?}", config.db_path);

    // AI features are optional: the daemon keeps running when Ollama is offline.
    let ai_enabled = match ai::ollama::OllamaClient::new(None) {
        Ok(client) => client.check_health().await,
        Err(e) => {
            warn!("Failed to initialize Ollama client: {}", e);
            false
        }
    };
    if ai_enabled {
        info!("Ollama is online and reachable. AI Pipeline features are ENABLED.");
    } else {
        warn!("AI Pipeline features are DISABLED. Running in heuristics-only mode.");
    }

    // 4. Create the central event bus (capacity of 100 events)
    let (tx, mut rx) = broadcast::channel(100);

    // 5. Start the Unix Socket listener in the background
    let socket_path = config.socket_path.clone();
    let listener_tx = tx.clone();

    tokio::spawn(async move {
        if let Err(e) = sources::shell::start_shell_listener(socket_path, listener_tx).await {
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
    let fs_root = std::env::current_dir()?;
    if let Some(git_root) = sources::git::find_git_root(&fs_root) {
        if let Err(e) = sources::git::install_hooks(&git_root) {
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

    tokio::spawn(async move {
        if let Err(e) = sources::filesystem::start_filesystem_watcher(fs_root, fs_tx).await {
            error!("Filesystem watcher crashed: {}", e);
        }
    });

    // 8. The Main Loop: Read from channel, write to DB
    info!("Daemon is running and listening for events.");

    loop {
        match rx.recv().await {
            Ok(event) => {
                let processed_event = pipeline::heuristics::process_event(event);
                info!(
                    "Received event from {:?}: {} score={} payload={}",
                    processed_event.raw.source,
                    processed_event.raw.id,
                    processed_event.score,
                    processed_event.raw.payload
                );

                // Lock the database, insert, and unlock
                let conn = db.lock().await;
                if let Err(e) = store::db::insert_event(&conn, &processed_event) {
                    error!("Failed to write event to database: {}", e);
                }
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
