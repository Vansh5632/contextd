use rusqlite::Connection;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};
use tokio::sync::Mutex;
use tokio::time::{interval, Duration};
use tracing::{error, info};

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

#[cfg(test)]
mod tests {
    use super::*;
    use contextd_core::event::ProcessedEvent;
    use contextd_core::test_utils::{test_config_in_memory, test_shell_event};
    use store::db::{init_db, insert_event};

    fn processed(id: &str, timestamp_ms: u64, score: f32) -> ProcessedEvent {
        let mut event = test_shell_event();
        event.id = id.to_string();
        event.timestamp_ms = timestamp_ms;
        ProcessedEvent { raw: event, score }
    }

    fn now_ms() -> u64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system clock should be after UNIX epoch")
            .as_millis() as u64
    }

    #[tokio::test]
    async fn worker_deletes_stale_low_score_events_on_first_tick() {
        let conn = init_db(&test_config_in_memory()).expect("db should initialize");
        let now = now_ms();
        let stale = now.saturating_sub(RETENTION_MS + 1_000);

        insert_event(&conn, &processed("old-trivial", stale, 0.2)).unwrap();
        insert_event(&conn, &processed("old-important", stale, 0.9)).unwrap();
        insert_event(&conn, &processed("fresh-trivial", now, 0.1)).unwrap();

        let db = Arc::new(Mutex::new(conn));
        let worker_db = Arc::clone(&db);
        let worker = tokio::spawn(async move {
            start_pruning_worker(worker_db).await;
        });

        tokio::time::sleep(Duration::from_millis(200)).await;
        worker.abort();
        let _ = worker.await;

        let conn = db.lock().await;
        let remaining: Vec<String> = conn
            .prepare("SELECT id FROM events ORDER BY id")
            .unwrap()
            .query_map([], |row| row.get(0))
            .unwrap()
            .collect::<rusqlite::Result<Vec<_>>>()
            .unwrap();

        assert_eq!(
            remaining,
            vec!["fresh-trivial".to_string(), "old-important".to_string()]
        );
    }
}
