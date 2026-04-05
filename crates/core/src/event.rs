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

    pub time_stamp_ms: u64,

    pub source: EventSource,

    pub payload: Value,
}

#[cfg(test)]
mod tests {
    use super::{EventSource, RawEvent};
    use serde_json::json;

    #[test]
    fn event_source_uses_snake_case_in_json() {
        let s =
            serde_json::to_string(&EventSource::FileSystem).expect("event source should serialize");
        assert_eq!(s, "\"file_system\"");
    }

    #[test]
    fn raw_event_round_trip_preserves_fields() {
        let event = RawEvent {
            id: "evt-1".to_string(),
            time_stamp_ms: 1_710_000_000_000,
            source: EventSource::Shell,
            payload: json!({"cmd": "ls", "exit_code": 0}),
        };

        let json = serde_json::to_string(&event).expect("raw event should serialize");
        let parsed: RawEvent = serde_json::from_str(&json).expect("raw event should deserialize");

        assert_eq!(parsed.id, event.id);
        assert_eq!(parsed.time_stamp_ms, event.time_stamp_ms);
        assert_eq!(parsed.source, event.source);
        assert_eq!(parsed.payload, event.payload);
    }
}
