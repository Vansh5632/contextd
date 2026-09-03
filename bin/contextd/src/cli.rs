//! The commands a person types.
//!
//! Deliberately hand-parsed rather than pulled in behind a derive macro. There
//! are five subcommands with almost no options between them, and the daemon's
//! dependency list is one of the few things standing between "cargo install
//! contextd" and a two-minute build.

use std::path::{Path, PathBuf};

use contextd_core::config::AppConfig;
use contextd_core::protocol::ContextRequest;

pub const USAGE: &str = "\
contextd — a local context engine for AI coding agents

USAGE:
    contextd [COMMAND]

COMMANDS:
    run                 Run the daemon in the foreground (default)
    mcp                 Speak MCP over stdio. This is what an agent launches.
    intent <TEXT>       Record what you are working on
    status              Show whether the daemon is running and what it holds
    install             Set up shell hooks, git hooks, and a systemd unit
    uninstall           Undo what `install` did
    help                Show this message

Configuration is read from the file printed by `contextd status`.
";

#[derive(Debug, PartialEq, Eq)]
pub enum Command {
    Run,
    Mcp,
    Intent(String),
    Status,
    Install,
    Uninstall,
    Help,
}

/// Work out what the user asked for.
///
/// Bare `contextd` runs the daemon, which is what someone typing the name of a
/// daemon means. `--mcp` is kept as a spelling of `mcp` because it is already
/// written into people's editor configuration files.
pub fn parse(args: &[String]) -> Result<Command, String> {
    let Some(first) = args.first().map(String::as_str) else {
        return Ok(Command::Run);
    };

    Ok(match first {
        "run" => Command::Run,
        "mcp" | "--mcp" => Command::Mcp,
        "status" => Command::Status,
        "install" => Command::Install,
        "uninstall" => Command::Uninstall,
        "help" | "--help" | "-h" => Command::Help,
        "intent" => {
            let text = args[1..].join(" ");
            if text.trim().is_empty() {
                return Err("intent needs something to record, e.g. \
                            `contextd intent \"fixing the login bug\"`"
                    .to_string());
            }
            Command::Intent(text)
        }
        other => return Err(format!("unknown command `{other}`. Try `contextd help`.")),
    })
}

/// Tell the running daemon what the user is working on.
pub async fn run_intent(config: &AppConfig, text: &str) -> anyhow::Result<()> {
    let request = ContextRequest::Intent {
        text: text.to_string(),
    };

    match mcp::daemon::ask(&config.socket_path, &request).await {
        Ok(_) => {
            println!("Noted: {text}");
            Ok(())
        }
        Err(err) => Err(anyhow::anyhow!(
            "could not reach the daemon at {}: {err}\nIs contextd running?",
            config.socket_path.display()
        )),
    }
}

/// Report whether things are set up and working.
///
/// Written to be useful when something is wrong, which is the only time anyone
/// runs it: every line is a fact someone would otherwise have to go and check.
pub async fn run_status(config: &AppConfig) -> anyhow::Result<()> {
    println!(
        "config     {}",
        contextd_core::config::config_path().display()
    );
    println!("database   {}", config.db_path.display());
    println!("socket     {}", config.socket_path.display());

    let running = config.socket_path.exists();
    let request = ContextRequest::Now { text: None };
    let reply = if running {
        mcp::daemon::ask(&config.socket_path, &request).await.ok()
    } else {
        None
    };

    match reply {
        Some(body) => {
            println!("daemon     running");
            match serde_json::from_str::<serde_json::Value>(&body) {
                Ok(snapshot) => describe(&snapshot),
                Err(_) => println!("           (the daemon replied with something unreadable)"),
            }
        }
        None if running => {
            println!("daemon     not responding (stale socket file?)");
        }
        None => {
            println!("daemon     not running");
            println!();
            println!("Start it with `contextd run`, or enable the service:");
            println!("    systemctl --user enable --now contextd");
        }
    }

    Ok(())
}

fn describe(snapshot: &serde_json::Value) {
    let count = |key: &str| {
        snapshot
            .get(key)
            .and_then(|value| value.as_array())
            .map_or(0, Vec::len)
    };

    if let Some(session) = snapshot.get("session_id").and_then(|v| v.as_str()) {
        println!("session    {session}");
    }
    if let Some(intent) = snapshot
        .get("intent")
        .and_then(|intent| intent.get("text"))
        .and_then(|text| text.as_str())
    {
        println!("intent     {intent}");
    }
    println!("watching   {} recent events", count("recent_activity"));

    if let Some(archived) = snapshot
        .get("archived")
        .and_then(|archive| archive.get("event_count"))
        .and_then(serde_json::Value::as_u64)
    {
        println!("archived   {archived} events in long-term memory");
    }
}

/// Wire contextd into the shell, git, and systemd.
///
/// Each step reports what it did and none of them is fatal: a machine without
/// systemd should still get the shell hook.
pub fn run_install(config: &AppConfig) -> anyhow::Result<()> {
    use sources::install::{self, Outcome};

    let executable = std::env::current_exe().unwrap_or_else(|_| PathBuf::from("contextd"));

    config.ensure_directories()?;
    write_default_config(config)?;

    let profile = install::default_profile();
    match install::install_shell_hook(&profile, &config.socket_path) {
        Ok(Outcome::AlreadyCurrent) => {
            println!("shell      already set up in {}", profile.display())
        }
        Ok(_) => println!("shell      hook written to {}", profile.display()),
        Err(err) => println!("shell      could not update {}: {err}", profile.display()),
    }

    let unit_path = install::systemd_unit_path();
    match install::install_systemd_unit(&unit_path, &executable) {
        Ok(Outcome::AlreadyCurrent) => println!("systemd    already set up"),
        Ok(_) => println!("systemd    unit written to {}", unit_path.display()),
        Err(err) => println!("systemd    could not write the unit: {err}"),
    }

    match sources::git::find_git_root(std::env::current_dir()?) {
        Some(repo) => match sources::git::install_hooks(&repo, &config.socket_path) {
            Ok(()) => println!("git        hooks installed in {}", repo.display()),
            Err(err) => println!("git        could not install hooks: {err}"),
        },
        None => println!("git        not in a repository, skipped"),
    }

    println!();
    println!("Done. Two things left:");
    println!(
        "    1. Open a new terminal, or run: source {}",
        profile.display()
    );
    println!("    2. Start the daemon:            systemctl --user enable --now contextd");

    Ok(())
}

/// Undo what `install` did, leaving the data alone.
pub fn run_uninstall() -> anyhow::Result<()> {
    let profile = sources::install::default_profile();
    match sources::install::uninstall_shell_hook(&profile) {
        Ok(true) => println!("shell      hook removed from {}", profile.display()),
        Ok(false) => println!("shell      no hook found in {}", profile.display()),
        Err(err) => println!("shell      could not update {}: {err}", profile.display()),
    }

    let unit = sources::install::systemd_unit_path();
    match std::fs::remove_file(&unit) {
        Ok(()) => println!("systemd    unit removed from {}", unit.display()),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
            println!("systemd    no unit found")
        }
        Err(err) => println!("systemd    could not remove the unit: {err}"),
    }

    println!();
    println!("Your recorded context was left alone. To remove it as well:");
    println!("    rm -rf {}", contextd_core::config::data_dir().display());

    Ok(())
}

/// Write a documented config file, if there is not one already.
///
/// Never overwrites: the point is to give someone something to edit, not to
/// discard what they have already edited.
fn write_default_config(config: &AppConfig) -> anyhow::Result<()> {
    let path = contextd_core::config::config_path();
    if path.exists() {
        println!("config     already at {}", path.display());
        return Ok(());
    }

    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(&path, contextd_core::config::example_config(config))?;
    println!("config     written to {}", path.display());

    Ok(())
}

/// Where the daemon should watch, given the config and where it was started.
pub fn watch_root(config: &AppConfig) -> std::io::Result<PathBuf> {
    match config.watch_root.as_deref().map(Path::to_path_buf) {
        Some(root) => Ok(root),
        None => std::env::current_dir(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(items: &[&str]) -> Vec<String> {
        items.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn no_arguments_runs_the_daemon() {
        assert_eq!(parse(&[]).unwrap(), Command::Run);
    }

    #[test]
    fn every_command_is_reachable() {
        let cases = [
            (vec!["run"], Command::Run),
            (vec!["mcp"], Command::Mcp),
            (vec!["status"], Command::Status),
            (vec!["install"], Command::Install),
            (vec!["uninstall"], Command::Uninstall),
            (vec!["help"], Command::Help),
        ];

        for (input, expected) in cases {
            assert_eq!(parse(&args(&input)).unwrap(), expected, "{input:?}");
        }
    }

    #[test]
    fn the_old_mcp_flag_still_works() {
        // It is already written into people's editor configuration; breaking it
        // would silently disconnect every agent they have set up.
        assert_eq!(parse(&args(&["--mcp"])).unwrap(), Command::Mcp);
    }

    #[test]
    fn intent_takes_the_rest_of_the_line() {
        assert_eq!(
            parse(&args(&["intent", "fixing", "the", "login", "bug"])).unwrap(),
            Command::Intent("fixing the login bug".to_string())
        );
    }

    #[test]
    fn intent_accepts_a_single_quoted_argument() {
        assert_eq!(
            parse(&args(&["intent", "fixing the login bug"])).unwrap(),
            Command::Intent("fixing the login bug".to_string())
        );
    }

    #[test]
    fn an_empty_intent_is_refused_with_an_example() {
        let err = parse(&args(&["intent"])).unwrap_err();
        assert!(err.contains("contextd intent"), "unhelpful error: {err}");

        assert!(parse(&args(&["intent", "   "])).is_err());
    }

    #[test]
    fn an_unknown_command_points_at_help() {
        let err = parse(&args(&["frobnicate"])).unwrap_err();
        assert!(err.contains("frobnicate"));
        assert!(err.contains("contextd help"));
    }

    #[test]
    fn help_lists_every_command() {
        for command in ["run", "mcp", "intent", "status", "install", "uninstall"] {
            assert!(USAGE.contains(command), "help omits `{command}`");
        }
    }

    #[test]
    fn the_watch_root_prefers_the_configured_directory() {
        let config = AppConfig {
            watch_root: Some(PathBuf::from("/src/thing")),
            ..AppConfig::default()
        };

        assert_eq!(watch_root(&config).unwrap(), PathBuf::from("/src/thing"));
    }

    #[test]
    fn without_configuration_the_watch_root_is_where_you_are() {
        let config = AppConfig::default();
        assert_eq!(
            watch_root(&config).unwrap(),
            std::env::current_dir().unwrap()
        );
    }

    #[test]
    fn status_describes_a_snapshot_without_panicking_on_a_sparse_one() {
        // The daemon may reply before anything has been recorded.
        describe(&serde_json::json!({}));
        describe(&serde_json::json!({"recent_activity": []}));
        describe(&serde_json::json!({
            "session_id": "abc",
            "intent": {"text": "fixing login"},
            "recent_activity": [1, 2, 3],
            "archived": {"event_count": 40},
        }));
    }
}
