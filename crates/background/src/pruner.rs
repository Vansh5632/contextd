use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};
use tokio::sync::Mutex;
use tokio::time::{interval, Duration};
use tracing::{error, info};
use rusqlite::Connection;

const PRUNE_INTERVAL_SECS: u64 = 60 * 60;
const RETENTION_MS: u64 = 7 * 24 * 60 * 60 * 1000;
const SCORE_THRESHOLD: f32 = 0.5;

/// Runs forever in the background, waking up periodically to prune old memory.
pub async fn start_pruning_worker(db: Arc<Mutex<Connection>>) {
    let mut ticker = interval(Duration::from_secs(PRUNE_INTERVAL_SECS));

    info!(
        "Background pruning worker started (runs every {} hour(s), retention {} days)",
        PRUNE_INTERVAL_SECS / 3600,
        RETENTION_MS / (24 * 60 * 60 * 1000)
    );

    loop {
        ticker.tick().await;

        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis() as u64;

        let cutoff = now.saturating_sub(RETENTION_MS);

        let conn = db.lock().await;

        match store::db::prune_old_events(&conn, cutoff, SCORE_THRESHOLD) {
            Ok(deleted_count) => {
                if deleted_count > 0 {
                    info!(
                        "Pruning complete. Removed {} stale events from memory.",
                        deleted_count
                    );
                }
            }
            Err(e) => {
                error!("Failed to run pruning job: {}", e);
            }
        }
    }
}
