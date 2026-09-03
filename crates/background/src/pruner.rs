//! Ageing events out of the live table and into Tier 3.
//!
//! This worker used to hard-delete anything old and low-scoring. That is the
//! right instinct applied too bluntly: the score was a guess made seconds after
//! the event happened, with no idea what would turn out to matter. Now the same
//! events are compressed into the archive instead, so "when did this get added"
//! still has an answer a month later.
//!
//! The live table stays small, which is the part that affects query speed. The
//! archive grows, but events compress to a small fraction of their size, so the
//! trade is heavily in favour of keeping them.

use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};
use store::Store;
use tokio::time::{Duration, interval};
use tracing::{error, info, warn};

const PRUNE_INTERVAL_SECS: u64 = 60 * 60;
const RETENTION_MS: u64 = 7 * 24 * 60 * 60 * 1000;
const SCORE_THRESHOLD: f32 = 0.5;

/// Events moved per pass.
///
/// Bounded so one sweep after a long outage cannot hold the write connection
/// for an unbounded stretch. Whatever is left is picked up on the next tick.
const BATCH: usize = 1_000;

/// Runs forever in the background, waking up periodically to age out memory.
pub async fn start_pruning_worker(store: Arc<Store>) {
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

        match archive_pass(&store, now.saturating_sub(RETENTION_MS)).await {
            Ok(0) => {}
            Ok(count) => info!("Archived {count} stale events into long-term memory."),
            Err(e) => error!("Failed to run pruning job: {}", e),
        }
    }
}

/// Move one batch of aged-out events into the archive. Returns how many moved.
async fn archive_pass(store: &Store, cutoff_ms: u64) -> anyhow::Result<usize> {
    let conn = store.writer().await;

    let events = store::db::get_prunable_events(&conn, cutoff_ms, SCORE_THRESHOLD, BATCH)?;
    if events.is_empty() {
        return Ok(0);
    }

    // Compress first, delete second. If the daemon dies between the two the
    // worst case is a duplicated segment, which is recoverable; the other order
    // loses the events outright.
    if store::archive::archive_events(&conn, &events)?.is_none() {
        warn!("archive declined a non-empty batch; leaving events in place");
        return Ok(0);
    }

    let ids: Vec<String> = events.into_iter().map(|event| event.raw.id).collect();
    Ok(store::db::delete_events(&conn, &ids)?)
}

#[cfg(test)]
mod tests {
    use super::*;
    use contextd_core::event::ProcessedEvent;
    use contextd_core::test_utils::{test_config_in_memory, test_shell_event};
    use store::db::insert_event;

    fn processed(id: &str, timestamp_ms: u64, score: f32) -> ProcessedEvent {
        let mut event = test_shell_event();
        event.id = id.to_string();
        event.timestamp_ms = timestamp_ms;
        ProcessedEvent::new(event, score)
    }

    fn durable(id: &str, timestamp_ms: u64, score: f32, memory_type: &str) -> ProcessedEvent {
        let mut event = processed(id, timestamp_ms, score);
        event.memory_type = Some(memory_type.to_string());
        event
    }

    fn now_ms() -> u64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system clock should be after UNIX epoch")
            .as_millis() as u64
    }

    async fn live_ids(store: &Store) -> Vec<String> {
        let conn = store.reader().unwrap();
        conn.prepare("SELECT id FROM events ORDER BY id")
            .unwrap()
            .query_map([], |row| row.get(0))
            .unwrap()
            .collect::<rusqlite::Result<Vec<_>>>()
            .unwrap()
    }

    #[tokio::test]
    async fn worker_clears_stale_low_score_events_on_first_tick() {
        let store = Arc::new(Store::open(&test_config_in_memory()).expect("db should initialize"));
        let now = now_ms();
        let stale = now.saturating_sub(RETENTION_MS + 1_000);

        {
            let conn = store.writer().await;
            insert_event(&conn, &processed("old-trivial", stale, 0.2)).unwrap();
            insert_event(&conn, &processed("old-important", stale, 0.9)).unwrap();
            insert_event(&conn, &processed("fresh-trivial", now, 0.1)).unwrap();
        }

        let worker_store = Arc::clone(&store);
        let worker = tokio::spawn(async move {
            start_pruning_worker(worker_store).await;
        });

        tokio::time::sleep(Duration::from_millis(200)).await;
        worker.abort();
        let _ = worker.await;

        assert_eq!(
            live_ids(&store).await,
            vec!["fresh-trivial".to_string(), "old-important".to_string()]
        );
    }

    #[tokio::test]
    async fn pruned_events_are_archived_rather_than_destroyed() {
        // The whole point of Tier 3: ageing out of the live table is not the
        // same as being forgotten.
        let store = Store::open(&test_config_in_memory()).unwrap();
        let stale = now_ms().saturating_sub(RETENTION_MS + 1_000);

        {
            let conn = store.writer().await;
            insert_event(&conn, &processed("gone", stale, 0.2)).unwrap();
        }

        let moved = archive_pass(&store, now_ms() - RETENTION_MS).await.unwrap();
        assert_eq!(moved, 1);

        let conn = store.reader().unwrap();
        assert_eq!(store::archive::archived_event_count(&conn).unwrap(), 1);

        let segments = store::archive::segments_in_range(&conn, 0, u64::MAX).unwrap();
        assert_eq!(segments.len(), 1);
        assert_eq!(segments[0].event_count, 1);
    }

    #[tokio::test]
    async fn durable_memories_stay_live_however_quietly_they_arrived() {
        // A commit scores no higher than a file save, but only one of them is
        // worth having in the live table next month.
        let store = Store::open(&test_config_in_memory()).unwrap();
        let stale = now_ms().saturating_sub(RETENTION_MS + 1_000);

        {
            let conn = store.writer().await;
            insert_event(&conn, &durable("semantic", stale, 0.1, "semantic")).unwrap();
            insert_event(&conn, &durable("procedural", stale, 0.1, "procedural")).unwrap();
            insert_event(&conn, &durable("episodic", stale, 0.1, "episodic")).unwrap();
        }

        archive_pass(&store, now_ms() - RETENTION_MS).await.unwrap();

        assert_eq!(
            live_ids(&store).await,
            vec!["procedural".to_string(), "semantic".to_string()]
        );
    }

    #[tokio::test]
    async fn an_empty_pass_writes_no_segment() {
        let store = Store::open(&test_config_in_memory()).unwrap();

        assert_eq!(archive_pass(&store, now_ms()).await.unwrap(), 0);

        let conn = store.reader().unwrap();
        assert!(
            store::archive::segments_in_range(&conn, 0, u64::MAX)
                .unwrap()
                .is_empty(),
            "an empty pass must not leave a segment describing nothing"
        );
    }

    #[tokio::test]
    async fn archived_events_can_be_read_back_intact() {
        let store = Store::open(&test_config_in_memory()).unwrap();
        let stale = now_ms().saturating_sub(RETENTION_MS + 1_000);

        {
            let conn = store.writer().await;
            insert_event(&conn, &processed("recoverable", stale, 0.2)).unwrap();
        }
        archive_pass(&store, now_ms() - RETENTION_MS).await.unwrap();

        let conn = store.reader().unwrap();
        let id: i64 = conn
            .query_row("SELECT id FROM archive LIMIT 1", [], |row| row.get(0))
            .unwrap();
        let restored = store::archive::read_segment(&conn, id).unwrap();

        assert_eq!(restored.len(), 1);
        assert_eq!(restored[0].raw.id, "recoverable");
    }
}
