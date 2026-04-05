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

#[cfg(test)]
mod tests {
    use super::AppConfig;

    #[test]
    fn default_config_values_are_stable() {
        let cfg = AppConfig::default();

        assert_eq!(cfg.db_path.to_string_lossy(), "/tmp/contextd/events.db");
        assert_eq!(
            cfg.socket_path.to_string_lossy(),
            "/tmp/contextd/contextd.sock"
        );
        assert_eq!(cfg.max_memory_mb, 500);
    }

    #[test]
    fn config_serializes_and_deserializes() {
        let cfg = AppConfig::default();
        let json = serde_json::to_string(&cfg).expect("config should serialize");
        let parsed: AppConfig =
            serde_json::from_str(&json).expect("serialized config should deserialize");

        assert_eq!(parsed.db_path, cfg.db_path);
        assert_eq!(parsed.socket_path, cfg.socket_path);
        assert_eq!(parsed.max_memory_mb, cfg.max_memory_mb);
    }
}
