//! What to actually do with an event once we understand it.
//!
//! Score says how loud an event is. Use case and memory type say what it is.
//! This module combines them into one verdict, so the policy lives in a single
//! readable place instead of being spread across the pruner, the archiver, and
//! the broker.

use contextd_core::event::ProcessedEvent;
use contextd_core::memory::{MemoryType, UseCase};

/// Below this, an event is noise unless something else redeems it.
const NOISE_SCORE: f32 = 0.25;

/// At or above this, an event is worth keeping regardless of what kind it is.
const HIGH_VALUE_SCORE: f32 = 0.8;

/// The verdict on one event.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Decision {
    /// Not worth the space. Eligible for the pruner as soon as it ages out.
    Drop,
    /// Keep as-is.
    Keep,
    /// Keep, but the payload is long or repetitive — store the summary instead.
    Summarize,
    /// Durable enough to survive pruning and belong in long-term memory.
    Promote,
}

impl Decision {
    /// Whether the event should still be readable after the retention window.
    pub fn is_durable(self) -> bool {
        matches!(self, Decision::Promote)
    }
}

/// Payloads longer than this are worth replacing with their summary.
const LONG_PAYLOAD_CHARS: usize = 512;

/// Decide the fate of one enriched event.
pub fn decide(event: &ProcessedEvent, use_case: UseCase, memory_type: MemoryType) -> Decision {
    // Anything the user might want months from now is promoted regardless of
    // how quiet it was. A one-line commit scores no higher than a file save,
    // but only one of them is worth keeping.
    if memory_type.survives_pruning() && event.score >= NOISE_SCORE {
        return Decision::Promote;
    }

    if event.score >= HIGH_VALUE_SCORE {
        return Decision::Promote;
    }

    // Failures are the highest-value thing we record and the lowest-scoring: a
    // process exiting scores 0.2 whether it succeeded or blew up. Scoring alone
    // would throw away precisely the events "have I hit this before" needs.
    if crate::content::looks_like_failure(&event.raw) {
        return Decision::Promote;
    }

    if event.score < NOISE_SCORE {
        // Research is mostly low-scoring by construction — reading and grepping
        // never looks urgent — so dropping on score alone would erase the entire
        // "what was I looking into" half of a briefing.
        if use_case == UseCase::Research {
            return Decision::Keep;
        }
        return Decision::Drop;
    }

    if payload_len(event) > LONG_PAYLOAD_CHARS {
        return Decision::Summarize;
    }

    Decision::Keep
}

fn payload_len(event: &ProcessedEvent) -> usize {
    event.raw.payload.to_string().chars().count()
}

#[cfg(test)]
mod tests {
    use super::*;
    use contextd_core::event::{EventSource, RawEvent};
    use serde_json::json;

    fn scored(score: f32, payload: serde_json::Value) -> ProcessedEvent {
        ProcessedEvent::new(
            RawEvent {
                id: "test".to_string(),
                timestamp_ms: 0,
                source: EventSource::Shell,
                payload,
            },
            score,
        )
    }

    #[test]
    fn durable_memory_is_promoted_even_when_quiet() {
        let quiet = scored(0.3, json!({"action": "commit"}));
        assert_eq!(
            decide(&quiet, UseCase::Coding, MemoryType::Semantic),
            Decision::Promote
        );
        assert_eq!(
            decide(&quiet, UseCase::Coding, MemoryType::Procedural),
            Decision::Promote
        );
    }

    #[test]
    fn loud_episodic_events_are_still_promoted() {
        let loud = scored(0.95, json!({"command": "cargo build"}));
        assert_eq!(
            decide(&loud, UseCase::Coding, MemoryType::Episodic),
            Decision::Promote
        );
    }

    #[test]
    fn quiet_episodic_noise_is_dropped() {
        let noise = scored(0.1, json!({"path": "/repo/x"}));
        assert_eq!(
            decide(&noise, UseCase::Coding, MemoryType::Episodic),
            Decision::Drop
        );
    }

    #[test]
    fn quiet_research_is_kept_rather_than_dropped() {
        // Reading is always low-scoring; dropping on score would delete the
        // entire record of what someone spent the morning investigating.
        let reading = scored(0.1, json!({"command": "man tar"}));
        assert_eq!(
            decide(&reading, UseCase::Research, MemoryType::Episodic),
            Decision::Keep
        );
    }

    #[test]
    fn long_payloads_are_summarized_rather_than_stored_whole() {
        let verbose = scored(0.5, json!({"output": "x".repeat(1_000)}));
        assert_eq!(
            decide(&verbose, UseCase::Coding, MemoryType::Episodic),
            Decision::Summarize
        );
    }

    #[test]
    fn a_quiet_failure_is_promoted_not_dropped() {
        // A process exiting scores 0.2 whether it succeeded or crashed. Dropping
        // on score would erase every build failure we ever saw.
        let crashed = scored(0.2, json!({"action": "process_end", "exit_code": 101}));
        assert_eq!(
            decide(&crashed, UseCase::Coding, MemoryType::Episodic),
            Decision::Promote
        );

        let panicked = scored(
            0.2,
            json!({"stderr": "thread 'main' panicked at src/main.rs:4:1"}),
        );
        assert_eq!(
            decide(&panicked, UseCase::Coding, MemoryType::Episodic),
            Decision::Promote
        );
    }

    #[test]
    fn a_clean_exit_is_not_mistaken_for_a_failure() {
        let clean = scored(0.2, json!({"action": "process_end", "exit_code": 0}));
        assert_eq!(
            decide(&clean, UseCase::Coding, MemoryType::Episodic),
            Decision::Drop
        );
    }

    #[test]
    fn an_ordinary_file_save_survives_the_noise_floor() {
        // Guards the interaction with the scorer: standard saves score 0.3, and
        // if that ever drops below the floor we would silently delete real work.
        let save = scored(0.3, json!({"path": "/repo/src/main.rs"}));
        assert_ne!(
            decide(&save, UseCase::Coding, MemoryType::Episodic),
            Decision::Drop
        );
    }

    #[test]
    fn ordinary_events_are_simply_kept() {
        let ordinary = scored(0.5, json!({"command": "cargo test"}));
        assert_eq!(
            decide(&ordinary, UseCase::Coding, MemoryType::Episodic),
            Decision::Keep
        );
    }

    #[test]
    fn a_dropped_event_is_not_durable_but_a_promoted_one_is() {
        assert!(Decision::Promote.is_durable());
        for decision in [Decision::Drop, Decision::Keep, Decision::Summarize] {
            assert!(!decision.is_durable());
        }
    }

    #[test]
    fn the_noise_floor_is_exclusive_at_the_boundary() {
        let at_floor = scored(NOISE_SCORE, json!({"command": "x"}));
        assert_eq!(
            decide(&at_floor, UseCase::Coding, MemoryType::Episodic),
            Decision::Keep,
            "an event exactly at the floor is not noise"
        );
    }
}
