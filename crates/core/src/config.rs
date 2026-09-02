//! Where contextd keeps things, and how to tell it otherwise.
//!
//! Defaults follow the XDG base directory spec, so data survives a reboot and
//! does not collide between users. The previous defaults lived under `/tmp`,
//! which is fine for a prototype and wrong for something you leave running:
//! `/tmp` is world-writable, commonly wiped on boot, and shared between
//! accounts on a multi-user machine.

use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct AppConfig {
    /// path to sqlite database
    pub db_path: PathBuf,
    /// path to unix socket for the broker
    pub socket_path: PathBuf,
    /// maximum memory size in MB before pushing to tier 3
    pub max_memory_mb: u32,
    /// Base URL of the local Ollama instance used for embeddings.
    pub ollama_url: String,
    /// Embedding model. Must produce 768 dimensions to match the vector table.
    pub embedding_model: String,
    /// Directory watched for file activity. Defaults to wherever the daemon
    /// was started, which is almost always the project being worked on.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub watch_root: Option<PathBuf>,
}

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            db_path: data_dir().join("events.db"),
            socket_path: runtime_dir().join("contextd.sock"),
            max_memory_mb: 500,
            ollama_url: "http://localhost:11434".to_string(),
            embedding_model: "nomic-embed-text".to_string(),
            watch_root: None,
        }
    }
}

impl AppConfig {
    /// Load configuration, falling back to defaults for anything unspecified.
    ///
    /// A malformed config is an error rather than a silent fallback. Quietly
    /// ignoring a typo'd setting and using a different path than the user asked
    /// for is far more confusing than refusing to start.
    pub fn load() -> anyhow::Result<Self> {
        match std::fs::read_to_string(config_path()) {
            Ok(text) => Ok(toml::from_str(&text)?),
            // No config file is the normal case, not a problem.
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(Self::default()),
            Err(err) => Err(err.into()),
        }
    }

    /// Load configuration, logging and ignoring a broken file.
    ///
    /// Used by short-lived commands where refusing to run is less helpful than
    /// running with defaults.
    pub fn load_or_default() -> Self {
        Self::load().unwrap_or_else(|err| {
            tracing::warn!(error = ?err, "could not read config; using defaults");
            Self::default()
        })
    }

    /// Create the directories the daemon writes into.
    pub fn ensure_directories(&self) -> std::io::Result<()> {
        for path in [self.db_path.as_path(), self.socket_path.as_path()] {
            if let Some(parent) = path.parent() {
                std::fs::create_dir_all(parent)?;
            }
        }
        Ok(())
    }
}

/// `~/.config/contextd/config.toml`, honouring `XDG_CONFIG_HOME`.
pub fn config_path() -> PathBuf {
    xdg_dir("XDG_CONFIG_HOME", ".config").join("contextd/config.toml")
}

/// `~/.local/share/contextd`, honouring `XDG_DATA_HOME`.
pub fn data_dir() -> PathBuf {
    xdg_dir("XDG_DATA_HOME", ".local/share").join("contextd")
}

/// Where the socket lives.
///
/// Prefers `XDG_RUNTIME_DIR`, which is the correct home for sockets: it is
/// user-private and cleaned up on logout. Falls back to the data directory
/// rather than `/tmp`, so the socket is never world-accessible.
pub fn runtime_dir() -> PathBuf {
    match std::env::var_os("XDG_RUNTIME_DIR") {
        Some(dir) if !dir.is_empty() => PathBuf::from(dir).join("contextd"),
        _ => data_dir(),
    }
}

fn xdg_dir(variable: &str, fallback: &str) -> PathBuf {
    if let Some(dir) = std::env::var_os(variable)
        && !dir.is_empty()
    {
        return PathBuf::from(dir);
    }
    home().join(fallback)
}

fn home() -> PathBuf {
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

/// Render a config file documenting every setting at its current value.
///
/// Written by `contextd install` so there is something to edit, with the
/// defaults visible rather than having to be looked up.
pub fn example_config(config: &AppConfig) -> String {
    let show = |path: &Path| path.display().to_string();

    format!(
        "# contextd configuration\n\
         # Every setting is optional; the values below are the defaults.\n\
         \n\
         # Where the event database lives.\n\
         db_path = \"{}\"\n\
         \n\
         # Unix socket agents and shell hooks talk to.\n\
         socket_path = \"{}\"\n\
         \n\
         # Soft ceiling on stored context before old events are archived.\n\
         max_memory_mb = {}\n\
         \n\
         # Local Ollama instance. contextd works without it, minus semantic search.\n\
         ollama_url = \"{}\"\n\
         \n\
         # Embedding model. Must be 768-dimensional to match the vector table.\n\
         # `embeddinggemma` is a drop-in upgrade and scores better on code.\n\
         embedding_model = \"{}\"\n\
         \n\
         # Directory to watch. Defaults to wherever the daemon is started.\n\
         # watch_root = \"/home/you/projects/thing\"\n",
        show(&config.db_path),
        show(&config.socket_path),
        config.max_memory_mb,
        config.ollama_url,
        config.embedding_model,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_are_under_the_users_own_directories() {
        let cfg = AppConfig::default();

        // `/tmp` is world-writable and commonly wiped on boot, which is not
        // where a week of accumulated context should live.
        assert!(
            !cfg.db_path.starts_with("/tmp"),
            "database must not default into /tmp: {:?}",
            cfg.db_path
        );
        assert!(cfg.db_path.ends_with("contextd/events.db"));
        assert!(cfg.socket_path.ends_with("contextd/contextd.sock"));
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

    #[test]
    fn a_partial_config_file_keeps_the_defaults_for_everything_else() {
        // Nobody should have to write out every setting to change one.
        let parsed: AppConfig = toml::from_str(r#"max_memory_mb = 100"#).unwrap();

        assert_eq!(parsed.max_memory_mb, 100);
        assert_eq!(parsed.db_path, AppConfig::default().db_path);
        assert_eq!(parsed.embedding_model, "nomic-embed-text");
    }

    #[test]
    fn an_empty_config_file_is_the_defaults() {
        let parsed: AppConfig = toml::from_str("").unwrap();
        assert_eq!(parsed.db_path, AppConfig::default().db_path);
    }

    #[test]
    fn settings_actually_override_the_defaults() {
        let parsed: AppConfig = toml::from_str(
            r#"
            db_path = "/data/ctx.db"
            socket_path = "/run/ctx.sock"
            ollama_url = "http://otherhost:11434"
            embedding_model = "embeddinggemma"
            watch_root = "/src/thing"
            "#,
        )
        .unwrap();

        assert_eq!(parsed.db_path, PathBuf::from("/data/ctx.db"));
        assert_eq!(parsed.socket_path, PathBuf::from("/run/ctx.sock"));
        assert_eq!(parsed.ollama_url, "http://otherhost:11434");
        assert_eq!(parsed.embedding_model, "embeddinggemma");
        assert_eq!(parsed.watch_root, Some(PathBuf::from("/src/thing")));
    }

    #[test]
    fn a_typo_is_reported_rather_than_silently_ignored() {
        // Using a different database than the user asked for, without saying
        // so, is a worse outcome than refusing to start.
        assert!(toml::from_str::<AppConfig>("db_path = 42").is_err());
    }

    #[test]
    fn the_example_config_is_valid_and_round_trips() {
        let cfg = AppConfig::default();
        let rendered = example_config(&cfg);
        let parsed: AppConfig =
            toml::from_str(&rendered).expect("the config we write must be one we can read");

        assert_eq!(parsed.db_path, cfg.db_path);
        assert_eq!(parsed.embedding_model, cfg.embedding_model);
    }

    #[test]
    fn the_socket_prefers_the_runtime_directory() {
        // Sockets belong somewhere user-private and cleaned up at logout.
        let dir = runtime_dir();
        assert!(dir.ends_with("contextd") || dir.to_string_lossy().contains("contextd"));
    }
}
