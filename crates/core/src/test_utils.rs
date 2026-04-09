use crate::config::AppConfig;
use crate::event::{EventSource, RawEvent};
use serde_json::{json, Value};
use std::path::PathBuf;

/// Returns an AppConfig tuned for deterministic tests.
pub fn test_config_in_memory() -> AppConfig {
    AppConfig {
        db_path: PathBuf::from(":memory:"),
        socket_path: PathBuf::from("/tmp/contextd/test.sock"),
        max_memory_mb: 64,
    }
}

/// Builds a predictable RawEvent for tests.
pub fn test_event(source: EventSource, payload: Value) -> RawEvent {
    RawEvent {
        id: "evt-test-1".to_string(),
        timestamp_ms: 1_710_000_000_000,
        source,
        payload,
    }
}

/// Builds a shell event with a canonical payload.
pub fn test_shell_event() -> RawEvent {
    test_event(EventSource::Shell, json!({"cmd": "ls", "exit_code": 0}))
}
