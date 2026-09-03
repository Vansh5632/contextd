//! Turning raw observations into something worth remembering.
//!
//! Split by when it runs, not by what it does:
//!
//! - [`heuristics`] is **synchronous**, on the ingest path. It must be fast and
//!   infallible, because an event that is not scored is an event not stored.
//! - [`classify`], [`content`], and [`decision`] run **later**, in the
//!   enrichment worker. They are allowed to be slower, and their output is
//!   always optional.
//!
//! Nothing here calls a model. That is what keeps contextd working on a laptop
//! with Ollama switched off.

pub mod classify;
pub mod content;
pub mod decision;
pub mod heuristics;

use contextd_core::event::RawEvent;
use contextd_core::memory::{MemoryType, UseCase};

/// Everything the off-hot-path stages can work out about one event.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Analysis {
    pub use_case: UseCase,
    pub memory_type: MemoryType,
    /// A one-line description, when the payload has one to give.
    pub summary: Option<String>,
}

/// Run every rule-based stage over one event.
///
/// Cheap enough to call on a whole backlog: no allocation-heavy work, no I/O,
/// no model. The expensive part of enrichment is the embedding, not this.
pub fn analyze(event: &RawEvent) -> Analysis {
    Analysis {
        use_case: classify::classify_use_case(event),
        memory_type: classify::classify_memory_type(event),
        summary: content::summarize(event),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use contextd_core::event::EventSource;
    use serde_json::json;

    #[test]
    fn analyze_fills_in_every_stage_for_a_commit() {
        let commit = RawEvent {
            id: "c1".to_string(),
            timestamp_ms: 0,
            source: EventSource::Git,
            payload: json!({"action": "commit", "message": "fix login redirect loop"}),
        };

        let analysis = analyze(&commit);
        assert_eq!(analysis.use_case, UseCase::Coding);
        assert_eq!(analysis.memory_type, MemoryType::Semantic);
        assert_eq!(
            analysis.summary.as_deref(),
            Some("committed: fix login redirect loop")
        );
    }

    #[test]
    fn analyze_still_classifies_when_there_is_no_summary() {
        let bare = RawEvent {
            id: "b1".to_string(),
            timestamp_ms: 0,
            source: EventSource::Shell,
            payload: json!({}),
        };

        let analysis = analyze(&bare);
        assert!(analysis.summary.is_none());
        assert_eq!(analysis.use_case, UseCase::GeneralProductivity);
        assert_eq!(analysis.memory_type, MemoryType::Episodic);
    }
}
