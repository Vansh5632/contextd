//! Keeping Tier 2 alive while the daemon runs.
//!
//! The graph itself is a plain data structure with no opinion about threads.
//! This wraps it in the two things the daemon needs: shared access from the
//! ingest loop and the query handler, and a periodic flush to disk so a crash
//! costs minutes of learning rather than months.

use std::sync::Arc;
use std::time::Duration;

use contextd_core::event::ProcessedEvent;
use store::Store;
use tokio::sync::RwLock;
use tracing::{debug, info, warn};

use crate::graph::{Entity, KnowledgeGraph, Related};
use crate::graph_store;

/// How often the in-memory graph is mirrored to SQLite.
///
/// The graph is cheap to rebuild from a few minutes of events but expensive to
/// rebuild from scratch, so this trades a small, regular write for a bounded
/// worst case.
const FLUSH_INTERVAL: Duration = Duration::from_secs(5 * 60);

/// Shared handle to the live knowledge graph.
#[derive(Clone)]
pub struct SharedGraph {
    inner: Arc<RwLock<KnowledgeGraph>>,
}

impl SharedGraph {
    /// Load the graph from disk, or start empty if it cannot be read.
    ///
    /// A corrupt or missing graph must not stop the daemon: Tier 2 is an
    /// optimisation over Tiers 0 and 1, not a prerequisite for them.
    pub fn load(conn: &rusqlite::Connection) -> Self {
        let graph = match graph_store::load(conn) {
            Ok(graph) => {
                info!(
                    nodes = graph.node_count(),
                    edges = graph.edge_count(),
                    "knowledge graph restored"
                );
                graph
            }
            Err(err) => {
                warn!(error = ?err, "could not restore the knowledge graph; starting empty");
                KnowledgeGraph::new()
            }
        };

        Self {
            inner: Arc::new(RwLock::new(graph)),
        }
    }

    pub fn empty() -> Self {
        Self {
            inner: Arc::new(RwLock::new(KnowledgeGraph::new())),
        }
    }

    pub async fn observe(&self, event: &ProcessedEvent) {
        self.inner.write().await.observe(event);
    }

    pub async fn neighbours(&self, entity: &Entity, limit: usize) -> Vec<Related> {
        self.inner.read().await.neighbours(entity, limit)
    }

    /// Entities related to anything this event touches, strongest first.
    ///
    /// This is the form the broker wants: it has an event in hand and needs to
    /// know what usually surrounds it.
    pub async fn related_to_event(&self, event: &ProcessedEvent, limit: usize) -> Vec<Related> {
        let graph = self.inner.read().await;

        let mut all: Vec<Related> = crate::graph::entities_of(event)
            .iter()
            .flat_map(|entity| graph.neighbours(entity, limit))
            .collect();

        all.sort_by(|a, b| {
            b.weight
                .partial_cmp(&a.weight)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        all.dedup_by(|a, b| a.entity == b.entity);
        all.truncate(limit);
        all
    }

    pub async fn size(&self) -> (usize, usize) {
        let graph = self.inner.read().await;
        (graph.node_count(), graph.edge_count())
    }

    async fn flush(&self, store: &Store) {
        // Copy the graph out and release the lock before touching the disk.
        // Holding it across the transaction would stall ingest for the length
        // of a write.
        let snapshot = self.inner.read().await.snapshot();

        // A fresh boot flushes before anything has been observed. Writing then
        // would erase the graph the previous run spent a week building.
        if snapshot.is_empty() {
            return;
        }

        let mut conn = store.writer().await;
        match graph_store::save(&mut conn, &snapshot) {
            Ok(()) => debug!(
                nodes = snapshot.entities.len(),
                edges = snapshot.edges.len(),
                "knowledge graph flushed"
            ),
            Err(err) => warn!(error = ?err, "failed to flush the knowledge graph"),
        }
    }
}

/// Mirror the graph to disk on a timer, forever.
pub fn start_flush_worker(store: Arc<Store>, graph: SharedGraph) {
    tokio::spawn(async move {
        let mut ticker = tokio::time::interval(FLUSH_INTERVAL);
        // The immediate first tick would write an empty graph; skip it.
        ticker.tick().await;

        loop {
            ticker.tick().await;
            graph.flush(&store).await;
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::EntityKind;
    use contextd_core::event::{EventSource, RawEvent};
    use contextd_core::test_utils::test_config_in_memory;
    use serde_json::json;

    fn event(source: EventSource, timestamp_ms: u64, payload: serde_json::Value) -> ProcessedEvent {
        ProcessedEvent::new(
            RawEvent {
                id: format!("evt-{timestamp_ms}"),
                timestamp_ms,
                source,
                payload,
            },
            0.6,
        )
    }

    async fn worked_for_a_while(graph: &SharedGraph) {
        for round in 0..3 {
            let base = round * 10_000;
            graph
                .observe(&event(
                    EventSource::FileSystem,
                    base,
                    json!({"path": "/repo/src/auth.rs"}),
                ))
                .await;
            graph
                .observe(&event(
                    EventSource::Shell,
                    base + 500,
                    json!({"command": "cargo test"}),
                ))
                .await;
        }
    }

    #[tokio::test]
    async fn what_the_graph_learns_survives_a_restart() {
        let store = Store::open(&test_config_in_memory()).unwrap();
        {
            let conn = store.writer().await;
            graph_store::migrate(&conn).unwrap();
        }

        let graph = SharedGraph::empty();
        worked_for_a_while(&graph).await;
        graph.flush(&store).await;

        let conn = store.reader().unwrap();
        let restored = SharedGraph::load(&conn);

        assert_eq!(restored.size().await, graph.size().await);
        let auth = Entity::new(EntityKind::File, "/repo/src/auth.rs");
        assert_eq!(
            restored.neighbours(&auth, 5).await,
            graph.neighbours(&auth, 5).await
        );
    }

    #[tokio::test]
    async fn the_broker_can_ask_what_surrounds_an_event() {
        let graph = SharedGraph::empty();
        worked_for_a_while(&graph).await;

        let related = graph
            .related_to_event(
                &event(
                    EventSource::FileSystem,
                    99_000,
                    json!({"path": "/repo/src/auth.rs"}),
                ),
                5,
            )
            .await;

        assert_eq!(related[0].entity.name, "cargo test");
    }

    #[tokio::test]
    async fn an_unseen_event_has_no_neighbours_rather_than_failing() {
        let graph = SharedGraph::empty();

        let related = graph
            .related_to_event(
                &event(EventSource::FileSystem, 1, json!({"path": "/nowhere.rs"})),
                5,
            )
            .await;

        assert!(related.is_empty());
    }

    #[tokio::test]
    async fn flushing_an_empty_graph_does_not_wipe_a_stored_one() {
        // The flush worker runs on a timer, so it will fire on a fresh boot
        // before anything has been observed. That must not erase the graph the
        // previous run spent a week building.
        let store = Store::open(&test_config_in_memory()).unwrap();
        {
            let conn = store.writer().await;
            graph_store::migrate(&conn).unwrap();
        }

        let learned = SharedGraph::empty();
        worked_for_a_while(&learned).await;
        learned.flush(&store).await;

        SharedGraph::empty().flush(&store).await;

        let conn = store.reader().unwrap();
        let (nodes, _) = SharedGraph::load(&conn).size().await;
        assert!(nodes > 0, "an empty flush must be a no-op, not a wipe");
    }

    #[tokio::test]
    async fn loading_from_a_database_without_the_tables_starts_empty() {
        let store = Store::open(&test_config_in_memory()).unwrap();
        let conn = store.reader().unwrap();

        assert_eq!(SharedGraph::load(&conn).size().await, (0, 0));
    }
}
