//! Tier 0: what is happening right now, held in RAM.
//!
//! Tier 1 can answer "what did I do this week", but it has to open a
//! connection, plan a query, and decode rows to do it. The overwhelmingly
//! common question is "what am I doing *right now*", and that answer should
//! not touch the disk at all.
//!
//! Two bounds keep this honest, and both matter:
//!
//! - **Capacity.** A `cargo build` can push hundreds of events through in a
//!   second. Without a cap, working memory becomes a memory leak.
//! - **Age.** Events from three hours ago are history, not working memory,
//!   even if nothing has happened since. Without this, walking away from the
//!   machine over lunch leaves a briefing that confidently describes the
//!   morning as the present.

use std::collections::VecDeque;
use std::sync::{Arc, RwLock};
use std::time::{SystemTime, UNIX_EPOCH};

use contextd_core::event::{Intent, ProcessedEvent};

/// Most events held in RAM at once.
const CAPACITY: usize = 256;

/// How long an event counts as "now".
const HORIZON_MS: u64 = 30 * 60 * 1_000;

/// The current session's live state.
///
/// Cloning is cheap and shares the same underlying state, so every source and
/// the query handler can hold one.
#[derive(Clone)]
pub struct WorkingSet {
    inner: Arc<RwLock<Inner>>,
}

struct Inner {
    events: VecDeque<ProcessedEvent>,
    intent: Option<Intent>,
    session_id: String,
    capacity: usize,
    horizon_ms: u64,
}

impl WorkingSet {
    pub fn new(session_id: impl Into<String>) -> Self {
        Self::with_bounds(session_id, CAPACITY, HORIZON_MS)
    }

    pub fn with_bounds(session_id: impl Into<String>, capacity: usize, horizon_ms: u64) -> Self {
        Self {
            inner: Arc::new(RwLock::new(Inner {
                events: VecDeque::with_capacity(capacity.min(1_024)),
                intent: None,
                session_id: session_id.into(),
                capacity: capacity.max(1),
                horizon_ms,
            })),
        }
    }

    pub fn session_id(&self) -> String {
        self.read().session_id.clone()
    }

    /// Add an event to working memory, evicting the oldest if we are full.
    ///
    /// Called from the ingest loop, so it must never fail or block for long. A
    /// poisoned lock is ignored rather than propagated: losing Tier 0 is a
    /// degraded briefing, but panicking here would stop the daemon recording
    /// anything at all.
    pub fn record(&self, event: ProcessedEvent) {
        let Ok(mut inner) = self.inner.write() else {
            return;
        };

        while inner.events.len() >= inner.capacity {
            inner.events.pop_front();
        }
        inner.events.push_back(event);
    }

    /// The most recent events, newest first, dropping anything past the horizon.
    pub fn recent(&self, limit: usize) -> Vec<ProcessedEvent> {
        let inner = self.read();
        let cutoff = now_ms().saturating_sub(inner.horizon_ms);

        inner
            .events
            .iter()
            .rev()
            .filter(|event| event.raw.timestamp_ms >= cutoff)
            .take(limit)
            .cloned()
            .collect()
    }

    /// What the user last said they were doing.
    pub fn intent(&self) -> Option<Intent> {
        self.read().intent.clone()
    }

    pub fn set_intent(&self, intent: Intent) {
        if let Ok(mut inner) = self.inner.write() {
            inner.intent = Some(intent);
        }
    }

    /// Number of events currently held. Cheap; used for diagnostics.
    pub fn len(&self) -> usize {
        self.read().events.len()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// Read the state, recovering the inner value if a writer panicked.
    ///
    /// Tier 0 is a cache of things already committed to Tier 1, so the worst a
    /// torn update can cost us is one event in a briefing.
    fn read(&self) -> std::sync::RwLockReadGuard<'_, Inner> {
        self.inner.read().unwrap_or_else(|err| err.into_inner())
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
    use contextd_core::event::{EventSource, RawEvent};
    use serde_json::json;

    fn event(id: &str, timestamp_ms: u64) -> ProcessedEvent {
        ProcessedEvent::new(
            RawEvent {
                id: id.to_string(),
                timestamp_ms,
                source: EventSource::Shell,
                payload: json!({ "command": "cargo test" }),
            },
            0.6,
        )
    }

    fn ids(events: &[ProcessedEvent]) -> Vec<&str> {
        events.iter().map(|e| e.raw.id.as_str()).collect()
    }

    #[test]
    fn recent_returns_newest_first() {
        let set = WorkingSet::new("s1");
        for (index, id) in ["a", "b", "c"].iter().enumerate() {
            set.record(event(id, now_ms() + index as u64));
        }

        assert_eq!(ids(&set.recent(10)), vec!["c", "b", "a"]);
    }

    #[test]
    fn recent_respects_the_limit() {
        let set = WorkingSet::new("s1");
        for id in ["a", "b", "c"] {
            set.record(event(id, now_ms()));
        }

        assert_eq!(set.recent(2).len(), 2);
    }

    #[test]
    fn a_burst_of_events_evicts_the_oldest_rather_than_growing() {
        let set = WorkingSet::with_bounds("s1", 3, HORIZON_MS);
        for id in ["a", "b", "c", "d", "e"] {
            set.record(event(id, now_ms()));
        }

        assert_eq!(set.len(), 3, "capacity must be a hard bound");
        assert_eq!(ids(&set.recent(10)), vec!["e", "d", "c"]);
    }

    #[test]
    fn events_past_the_horizon_are_not_the_present() {
        // Walking away for lunch should not leave a briefing that describes
        // the morning in the present tense.
        let set = WorkingSet::new("s1");
        set.record(event("stale", now_ms() - HORIZON_MS - 1));
        set.record(event("fresh", now_ms()));

        assert_eq!(ids(&set.recent(10)), vec!["fresh"]);
    }

    #[test]
    fn an_entirely_stale_set_reports_nothing_rather_than_lying() {
        let set = WorkingSet::new("s1");
        set.record(event("old", now_ms() - HORIZON_MS - 1));

        assert!(set.recent(10).is_empty());
        assert_eq!(set.len(), 1, "stale events are hidden, not dropped");
    }

    #[test]
    fn intent_round_trips() {
        let set = WorkingSet::new("s1");
        assert!(set.intent().is_none());

        set.set_intent(Intent {
            text: "fixing the login bug".to_string(),
            declared_at_ms: 42,
        });

        assert_eq!(set.intent().unwrap().text, "fixing the login bug");
    }

    #[test]
    fn clones_share_one_underlying_set() {
        let set = WorkingSet::new("s1");
        let clone = set.clone();

        clone.record(event("a", now_ms()));

        assert_eq!(set.len(), 1, "a clone must be a handle, not a copy");
        assert_eq!(set.session_id(), "s1");
    }

    #[test]
    fn a_poisoned_lock_degrades_instead_of_panicking() {
        let set = WorkingSet::new("s1");
        let poisoner = set.clone();

        let panicked = std::thread::spawn(move || {
            let _guard = poisoner.inner.write().unwrap();
            panic!("simulated writer panic");
        })
        .join();
        assert!(panicked.is_err());

        // Tier 0 is a cache of rows already committed to Tier 1, so reads must
        // keep working rather than take the daemon down with them.
        assert_eq!(set.len(), 0);
        assert!(set.recent(10).is_empty());
    }
}
