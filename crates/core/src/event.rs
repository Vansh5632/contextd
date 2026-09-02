use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EventSource {
    Shell,
    FileSystem,
    Git,
    Editor,
    Proc,
    Manifest,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RawEvent {
    pub id: String,

    #[serde(alias = "time_stamp_ms")]
    pub timestamp_ms: u64,

    pub source: EventSource,

    pub payload: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProcessedEvent {
    pub raw: RawEvent,
    pub score: f32,

    /// Which burst of work this belongs to. `None` on events restored from a
    /// database written before sessions existed.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub session_id: Option<String>,

    /// Everything below is filled in later, off the hot path, and may stay
    /// `None` forever if the local model is not running. Nothing in the product
    /// is allowed to require these.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub use_case: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub memory_type: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub summary: Option<String>,
}

impl ProcessedEvent {
    /// A scored event with no enrichment yet. This is what the hot path produces.
    pub fn new(raw: RawEvent, score: f32) -> Self {
        Self {
            raw,
            score,
            session_id: None,
            use_case: None,
            memory_type: None,
            summary: None,
        }
    }

    pub fn with_session(mut self, session_id: impl Into<String>) -> Self {
        self.session_id = Some(session_id.into());
        self
    }
}

/// What the user says they are trying to do, in their own words.
///
/// Observations tell us what happened; an intent tells us why. The broker puts
/// the two side by side so a briefing stays on track instead of just listing
/// the last ten file saves.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Intent {
    pub text: String,
    pub declared_at_ms: u64,
}

#[cfg(test)]
mod tests {
    use super::{EventSource, RawEvent};
    use serde_json::json;

    #[test]
    fn event_source_uses_snake_case_in_json() {
        let cases = [
            (EventSource::Shell, "\"shell\""),
            (EventSource::FileSystem, "\"file_system\""),
            (EventSource::Git, "\"git\""),
            (EventSource::Editor, "\"editor\""),
            (EventSource::Proc, "\"proc\""),
            (EventSource::Manifest, "\"manifest\""),
        ];

        for (source, expected) in cases {
            let actual = serde_json::to_string(&source).expect("event source should serialize");
            assert_eq!(actual, expected);
        }
    }

    #[test]
    fn event_source_rejects_unknown_values() {
        let err = serde_json::from_str::<EventSource>("\"unknown_source\"")
            .expect_err("unknown source must fail deserialization");

        // Don't depend on the exact error message wording; just assert it's a data error.
        assert!(
            err.is_data(),
            "expected data error for unknown variant, got: {err}"
        );
    }

    #[test]
    fn raw_event_round_trip_preserves_fields() {
        let event = RawEvent {
            id: "evt-1".to_string(),
            timestamp_ms: 1_710_000_000_000,
            source: EventSource::Shell,
            payload: json!({"cmd": "ls", "exit_code": 0}),
        };

        let json = serde_json::to_string(&event).expect("raw event should serialize");
        let parsed: RawEvent = serde_json::from_str(&json).expect("raw event should deserialize");

        assert_eq!(parsed.id, event.id);
        assert_eq!(parsed.timestamp_ms, event.timestamp_ms);
        assert_eq!(parsed.source, event.source);
        assert_eq!(parsed.payload, event.payload);
    }
}
