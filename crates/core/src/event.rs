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
