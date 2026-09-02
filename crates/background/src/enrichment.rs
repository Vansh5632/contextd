//! Everything expensive that happens *after* an event is safely stored.
//!
//! The rule this module exists to enforce: watching must never depend on a model
//! being online. Ingest writes the row and moves on; enrichment happens here,
//! later, and is allowed to fail, lag, or be skipped entirely.
//!
//! Two things feed the worker:
//!
//! - the live queue, bounded, which drops work when it is full
//! - the database backlog (`enriched_at_ms IS NULL`), swept at startup
//!
//! Dropping from the queue is safe precisely because the backlog exists: the row
//! is still marked unenriched, so the next sweep picks it up.

use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use ai::ollama::OllamaClient;
use pipeline::decision::Decision;
use store::Store;
use store::db::{
    Enrichment, delete_event, get_enrichment_backlog, get_event_by_id, mark_enriched,
    record_analysis,
};
use tokio::sync::mpsc;
use tracing::{debug, info, warn};

/// How many events can be waiting for enrichment before we start dropping.
///
/// Sized for a burst of activity (a `cargo build` touching many files), not for
/// an outage. If Ollama is wedged, we would rather shed load than grow forever.
const QUEUE_CAPACITY: usize = 256;

/// How many backlog rows to reclaim per sweep.
const BACKLOG_BATCH: usize = 64;

/// How often to look for rows the live queue missed.
const BACKLOG_INTERVAL: Duration = Duration::from_secs(60);

/// Hands work to the enrichment worker without ever blocking the caller.
#[derive(Clone)]
pub struct EnrichmentQueue {
    tx: mpsc::Sender<String>,
}

impl EnrichmentQueue {
    /// Offer an event for enrichment. Returns `false` if the queue was full.
    ///
    /// Deliberately non-blocking and infallible from the caller's point of view:
    /// this is called from the ingest loop, which must not stall.
    pub fn offer(&self, event_id: &str) -> bool {
        match self.tx.try_send(event_id.to_string()) {
            Ok(()) => true,
            Err(mpsc::error::TrySendError::Full(_)) => {
                debug!(
                    event_id,
                    "enrichment queue full; leaving row for the backlog sweep"
                );
                false
            }
            Err(mpsc::error::TrySendError::Closed(_)) => {
                warn!("enrichment worker is gone; enrichment is now backlog-only");
                false
            }
        }
    }
}

/// Start the enrichment worker. Returns the handle used to feed it.
pub fn start(store: Arc<Store>, ollama: Option<OllamaClient>) -> EnrichmentQueue {
    let (tx, rx) = mpsc::channel(QUEUE_CAPACITY);

    tokio::spawn(run_worker(Arc::clone(&store), ollama, rx));
    tokio::spawn(run_backlog_sweeper(
        store,
        EnrichmentQueue { tx: tx.clone() },
    ));

    EnrichmentQueue { tx }
}

async fn run_worker(
    store: Arc<Store>,
    ollama: Option<OllamaClient>,
    mut rx: mpsc::Receiver<String>,
) {
    info!("Enrichment worker started");

    while let Some(event_id) = rx.recv().await {
        enrich_one(&store, ollama.as_ref(), &event_id).await;
    }

    info!("Enrichment worker stopped");
}

/// Periodically reclaim anything the live queue dropped, or that was written by
/// a previous run of the daemon before it exited.
async fn run_backlog_sweeper(store: Arc<Store>, queue: EnrichmentQueue) {
    let mut ticker = tokio::time::interval(BACKLOG_INTERVAL);
    // The first tick fires immediately, which is what we want on startup.

    loop {
        ticker.tick().await;

        let backlog = {
            let Ok(reader) = store.reader() else {
                warn!("could not open a reader for the enrichment backlog");
                continue;
            };
            match get_enrichment_backlog(&reader, BACKLOG_BATCH) {
                Ok(events) => events,
                Err(err) => {
                    warn!(error = ?err, "failed to read enrichment backlog");
                    continue;
                }
            }
        };

        if backlog.is_empty() {
            continue;
        }

        debug!(count = backlog.len(), "reclaiming unenriched events");
        for event in backlog {
            // Go through the queue rather than enriching inline, so the backlog
            // sweep cannot itself become an unbounded burst of model calls.
            if !queue.offer(&event.raw.id) {
                break;
            }
        }
    }
}

/// Enrich a single event. Every failure is logged and swallowed.
async fn enrich_one(store: &Store, ollama: Option<&OllamaClient>, event_id: &str) {
    let event = {
        let Ok(reader) = store.reader() else {
            warn!(event_id, "could not open a reader to enrich event");
            return;
        };
        match get_event_by_id(&reader, event_id) {
            Ok(Some(event)) => event,
            // Pruned between being queued and being picked up. Normal.
            Ok(None) => return,
            Err(err) => {
                warn!(event_id, error = ?err, "failed to load event for enrichment");
                return;
            }
        }
    };

    // Rule-based analysis first: it cannot fail and needs no model, so even a
    // machine with Ollama switched off ends up with classified, summarised
    // memory rather than a pile of raw JSON.
    let analysis = pipeline::analyze(&event.raw);
    let decision = pipeline::decision::decide(&event, analysis.use_case, analysis.memory_type);

    if decision == Decision::Drop {
        // Noise. Reclaim the row now rather than carrying it for a week and
        // letting the pruner rediscover it.
        let conn = store.writer().await;
        match delete_event(&conn, event_id) {
            Ok(()) => debug!(event_id, "dropped as noise"),
            Err(err) => warn!(event_id, error = ?err, "failed to drop noise event"),
        }
        return;
    }

    let enrichment = Enrichment {
        use_case: Some(analysis.use_case.to_string()),
        memory_type: Some(analysis.memory_type.to_string()),
        summary: analysis.summary.clone(),
    };

    {
        let conn = store.writer().await;
        if let Err(err) = record_analysis(&conn, event_id, &enrichment) {
            warn!(event_id, error = ?err, "failed to record analysis");
        }
    }

    // Embed the summary rather than the raw JSON where we have one: it makes
    // semantic search match on meaning instead of on key names and punctuation.
    let text = analysis
        .summary
        .unwrap_or_else(|| event.raw.payload.to_string());

    let Some(client) = ollama else { return };

    let embedding = match client.get_embedding(&text, None).await {
        Ok(embedding) => embedding,
        Err(err) => {
            // Ollama being down is the expected case, not an exception. The row
            // keeps its analysis and stays in the backlog for a later vector.
            debug!(event_id, error = ?err, "embedding skipped");
            return;
        }
    };

    let conn = store.writer().await;
    if let Err(err) = store::vector::insert_embedding(&conn, event_id, &embedding) {
        warn!(event_id, error = ?err, "failed to store embedding");
        return;
    }

    // Only now is the event genuinely finished, so only now does it leave the
    // backlog. Analysis alone is not enough: a row with no vector is invisible
    // to semantic search, and we want it retried when the model comes back.
    if let Err(err) = mark_enriched(&conn, event_id, now_ms()) {
        warn!(event_id, error = ?err, "failed to mark event enriched");
    }
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|elapsed| elapsed.as_millis() as u64)
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    use contextd_core::event::{EventSource, ProcessedEvent, RawEvent};
    use contextd_core::test_utils::test_config_in_memory;
    use serde_json::json;
    use store::db::insert_event;

    fn processed(id: &str) -> ProcessedEvent {
        ProcessedEvent::new(
            RawEvent {
                id: id.to_string(),
                timestamp_ms: 1_000,
                source: EventSource::Shell,
                payload: json!({ "command": "cargo build" }),
            },
            0.9,
        )
    }

    /// Shell navigation: the scorer gives it 0.1, and it is worth nothing later.
    fn noise(id: &str) -> ProcessedEvent {
        ProcessedEvent::new(
            RawEvent {
                id: id.to_string(),
                timestamp_ms: 1_000,
                source: EventSource::Shell,
                payload: json!({ "command": "cd /repo" }),
            },
            0.1,
        )
    }

    #[tokio::test]
    async fn offering_to_a_full_queue_reports_the_drop_instead_of_blocking() {
        let (tx, _rx) = mpsc::channel(1);
        let queue = EnrichmentQueue { tx };

        assert!(queue.offer("first"), "first offer fits in the buffer");
        assert!(
            !queue.offer("second"),
            "a full queue must refuse rather than wait"
        );
    }

    #[tokio::test]
    async fn offering_to_a_closed_queue_is_not_fatal() {
        let (tx, rx) = mpsc::channel(4);
        drop(rx);

        assert!(!EnrichmentQueue { tx }.offer("orphan"));
    }

    #[tokio::test]
    async fn without_ollama_rows_stay_in_the_backlog() {
        let store = Arc::new(Store::open(&test_config_in_memory()).unwrap());
        {
            let conn = store.writer().await;
            insert_event(&conn, &processed("a")).unwrap();
        }

        enrich_one(&store, None, "a").await;

        let reader = store.reader().unwrap();
        let backlog = get_enrichment_backlog(&reader, 10).unwrap();
        assert_eq!(
            backlog.len(),
            1,
            "an event with no embedding must remain claimable"
        );
    }

    #[tokio::test]
    async fn analysis_lands_even_though_the_model_is_offline() {
        // The whole point of splitting analysis from embedding: a laptop with
        // Ollama switched off still gets classified, summarised memory.
        let store = Arc::new(Store::open(&test_config_in_memory()).unwrap());
        {
            let conn = store.writer().await;
            insert_event(&conn, &processed("a")).unwrap();
        }

        enrich_one(&store, None, "a").await;

        let reader = store.reader().unwrap();
        let stored = get_event_by_id(&reader, "a").unwrap().unwrap();
        assert_eq!(stored.use_case.as_deref(), Some("coding"));
        assert_eq!(stored.memory_type.as_deref(), Some("episodic"));
        assert_eq!(stored.summary.as_deref(), Some("ran `cargo build`"));
    }

    #[tokio::test]
    async fn noise_is_deleted_rather_than_enriched() {
        let store = Arc::new(Store::open(&test_config_in_memory()).unwrap());
        {
            let conn = store.writer().await;
            insert_event(&conn, &noise("junk")).unwrap();
            insert_event(&conn, &processed("keep")).unwrap();
        }

        enrich_one(&store, None, "junk").await;
        enrich_one(&store, None, "keep").await;

        let reader = store.reader().unwrap();
        assert!(
            get_event_by_id(&reader, "junk").unwrap().is_none(),
            "a dropped event should not survive enrichment"
        );
        assert!(
            get_event_by_id(&reader, "keep").unwrap().is_some(),
            "dropping noise must not touch its neighbours"
        );
    }

    #[tokio::test]
    async fn enriching_a_pruned_event_is_a_no_op() {
        let store = Arc::new(Store::open(&test_config_in_memory()).unwrap());

        // Must not panic: the row was deleted between queueing and pickup.
        enrich_one(&store, None, "never-existed").await;
    }

    #[tokio::test]
    async fn backlog_only_returns_unenriched_rows() {
        let store = Arc::new(Store::open(&test_config_in_memory()).unwrap());
        {
            let conn = store.writer().await;
            insert_event(&conn, &processed("done")).unwrap();
            insert_event(&conn, &processed("pending")).unwrap();
            mark_enriched(&conn, "done", 42).unwrap();
        }

        let reader = store.reader().unwrap();
        let backlog = get_enrichment_backlog(&reader, 10).unwrap();
        let ids: Vec<&str> = backlog.iter().map(|e| e.raw.id.as_str()).collect();
        assert_eq!(ids, vec!["pending"]);
    }
}
