use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppConfig {
    /// path to sqlite database
    pub db_path: PathBuf,
    /// path to unix socket for the broker
    pub socket_path: PathBuf,
    /// maximum memory size in MB before pushing to tier 3
    pub max_memory_mb: u32,
}

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            db_path: PathBuf::from("/tmp/contextd/events.db"),
            socket_path: PathBuf::from("/tmp/contextd/contextd.sock"),
            max_memory_mb: 500,
        }
    }
}
