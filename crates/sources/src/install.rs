//! Getting contextd wired into a machine.
//!
//! Two integrations that cannot be done from inside the daemon, because they
//! involve files the daemon does not own: the shell hook that reports typed
//! commands, and the systemd unit that starts the daemon at login.
//!
//! Both follow the same rule as the git hooks: never destroy what is already
//! there, always be safe to run twice, and never break the thing being hooked
//! into. A shell snippet that can hang a terminal is worse than no snippet.

use std::path::{Path, PathBuf};

/// Marks the block we manage inside a user's shell profile.
const BEGIN_MARKER: &str = "# >>> contextd >>>";
const END_MARKER: &str = "# <<< contextd <<<";

/// What an install attempt did, so the CLI can say something truthful.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Outcome {
    Installed,
    Updated,
    AlreadyCurrent,
}

/// The shell snippet that reports typed commands.
///
/// Written for POSIX shells with bash and zsh specialisations. Delivery is
/// shared with the git hooks via [`crate::emit`], which is where the reasons
/// for its shape are written down; what is specific here is capturing the
/// command without disturbing the shell it is capturing from.
pub fn shell_snippet(socket_path: &Path) -> String {
    let socket = crate::emit::socket_default(socket_path);
    let emit = crate::emit::emit_function();
    let emit_fn = crate::emit::EMIT_FN;

    format!(
        r#"{BEGIN_MARKER}
# Reports commands you run to the contextd daemon. Managed by `contextd install`.
# Remove this block to uninstall.
export {socket}

{emit}

__contextd_send() {{
  __contextd_cmd=$(printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g' | tr -d '\r\n')
  [ -n "$__contextd_cmd" ] || return 0

  {emit_fn} "$(printf '{{"source":"shell","payload":{{"command":"%s","exit_code":%s,"cwd":"%s"}}}}' \
    "$__contextd_cmd" "${{2:-0}}" "$PWD")"
  return 0
}}

if [ -n "$ZSH_VERSION" ]; then
  __contextd_preexec() {{ __contextd_last="$1"; }}
  __contextd_precmd() {{
    __contextd_status=$?
    [ -n "$__contextd_last" ] && __contextd_send "$__contextd_last" "$__contextd_status"
    __contextd_last=
    return $__contextd_status
  }}
  autoload -Uz add-zsh-hook 2>/dev/null && {{
    add-zsh-hook preexec __contextd_preexec
    add-zsh-hook precmd __contextd_precmd
  }}
elif [ -n "$BASH_VERSION" ]; then
  __contextd_prompt() {{
    __contextd_status=$?
    __contextd_line=$(HISTTIMEFORMAT= history 1 | sed 's/^ *[0-9]* *//')
    if [ "$__contextd_line" != "$__contextd_last" ]; then
      __contextd_last="$__contextd_line"
      __contextd_send "$__contextd_line" "$__contextd_status"
    fi
    return $__contextd_status
  }}
  case "$PROMPT_COMMAND" in
    *__contextd_prompt*) ;;
    "") PROMPT_COMMAND="__contextd_prompt" ;;
    *) PROMPT_COMMAND="__contextd_prompt;$PROMPT_COMMAND" ;;
  esac
fi
{END_MARKER}
"#
    )
}

/// Add or refresh the contextd block in a shell profile.
///
/// The block is delimited by markers so an update rewrites exactly our lines
/// and leaves everything the user wrote untouched.
pub fn install_shell_hook(profile: &Path, socket_path: &Path) -> std::io::Result<Outcome> {
    let snippet = shell_snippet(socket_path);
    let existing = std::fs::read_to_string(profile).unwrap_or_default();

    let Some(block) = find_block(&existing) else {
        if let Some(parent) = profile.parent() {
            std::fs::create_dir_all(parent)?;
        }
        // Keep a blank line between whatever was there and our block.
        let separator = if existing.is_empty() || existing.ends_with("\n\n") {
            ""
        } else if existing.ends_with('\n') {
            "\n"
        } else {
            "\n\n"
        };
        std::fs::write(profile, format!("{existing}{separator}{snippet}"))?;
        return Ok(Outcome::Installed);
    };

    if existing[block.clone()] == snippet {
        return Ok(Outcome::AlreadyCurrent);
    }

    let mut updated = String::with_capacity(existing.len());
    updated.push_str(&existing[..block.start]);
    updated.push_str(&snippet);
    updated.push_str(&existing[block.end..]);
    std::fs::write(profile, updated)?;

    Ok(Outcome::Updated)
}

/// Remove the contextd block from a shell profile.
pub fn uninstall_shell_hook(profile: &Path) -> std::io::Result<bool> {
    let existing = std::fs::read_to_string(profile).unwrap_or_default();
    let Some(block) = find_block(&existing) else {
        return Ok(false);
    };

    let mut updated = String::with_capacity(existing.len());
    updated.push_str(existing[..block.start].trim_end());
    updated.push('\n');
    updated.push_str(existing[block.end..].trim_start_matches('\n'));
    std::fs::write(profile, updated)?;

    Ok(true)
}

/// Byte range of the managed block, markers included.
fn find_block(text: &str) -> Option<std::ops::Range<usize>> {
    let start = text.find(BEGIN_MARKER)?;
    let end = text[start..].find(END_MARKER)? + start + END_MARKER.len();
    // Take the trailing newline too, so removal does not leave a blank line.
    let end = if text[end..].starts_with('\n') {
        end + 1
    } else {
        end
    };
    Some(start..end)
}

/// The profile file for the shell the user is actually running.
pub fn default_profile() -> PathBuf {
    let home = std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));

    match std::env::var("SHELL").unwrap_or_default() {
        shell if shell.ends_with("zsh") => home.join(".zshrc"),
        shell if shell.ends_with("fish") => home.join(".config/fish/config.fish"),
        _ => home.join(".bashrc"),
    }
}

/// A systemd user unit that keeps the daemon running.
pub fn systemd_unit(executable: &Path) -> String {
    format!(
        "[Unit]\n\
         Description=contextd — local context engine for AI coding agents\n\
         Documentation=https://github.com/vansh5632/contextd\n\
         After=default.target\n\
         \n\
         [Service]\n\
         Type=simple\n\
         ExecStart={exe} run\n\
         Restart=on-failure\n\
         RestartSec=5\n\
         # The daemon is deliberately low priority: it must never compete with\n\
         # the compiler or the editor it is watching.\n\
         Nice=10\n\
         IOSchedulingClass=idle\n\
         Environment=RUST_LOG=info\n\
         \n\
         [Install]\n\
         WantedBy=default.target\n",
        exe = executable.display(),
    )
}

/// `~/.config/systemd/user/contextd.service`
pub fn systemd_unit_path() -> PathBuf {
    contextd_core::config::config_path()
        .parent()
        .and_then(Path::parent)
        .unwrap_or(Path::new("."))
        .join("systemd/user/contextd.service")
}

/// Write the systemd unit, creating directories as needed.
pub fn install_systemd_unit(path: &Path, executable: &Path) -> std::io::Result<Outcome> {
    let unit = systemd_unit(executable);

    if std::fs::read_to_string(path).unwrap_or_default() == unit {
        return Ok(Outcome::AlreadyCurrent);
    }

    let existed = path.exists();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(path, unit)?;

    Ok(if existed {
        Outcome::Updated
    } else {
        Outcome::Installed
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_file(name: &str) -> PathBuf {
        let id = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("contextd-install-{name}-{id}"));
        std::fs::create_dir_all(&dir).unwrap();
        dir.join(name)
    }

    fn socket() -> PathBuf {
        PathBuf::from("/run/user/1000/contextd/contextd.sock")
    }

    #[test]
    fn the_snippet_never_makes_the_prompt_wait() {
        // A prompt that blocks on a socket is a terminal that hangs when the
        // daemon is wedged. This is the property that matters most.
        let snippet = shell_snippet(&socket());

        assert!(
            snippet.contains(") >/dev/null 2>&1 &"),
            "the send must background"
        );
        assert!(snippet.contains(r#"[ -S "$CONTEXTD_SOCKET" ] || return 0"#));
    }

    #[test]
    fn the_snippet_parses_in_every_shell_it_targets() {
        // A syntax error here is only discovered when someone opens a terminal
        // and every prompt starts printing errors at them.
        for shell in ["sh", "bash", "zsh"] {
            let Ok(status) = std::process::Command::new(shell)
                .arg("-n")
                .arg("-c")
                .arg(shell_snippet(&socket()))
                .status()
            else {
                continue; // Not installed on this machine.
            };
            assert!(status.success(), "the snippet does not parse under {shell}");
        }
    }

    #[test]
    fn the_snippet_preserves_the_exit_status() {
        // In PROMPT_COMMAND, clobbering $? breaks every prompt that shows the
        // last command's exit code.
        let snippet = shell_snippet(&socket());

        assert!(snippet.contains("__contextd_status=$?"));
        assert!(snippet.contains("return $__contextd_status"));
    }

    #[test]
    fn the_snippet_handles_both_common_shells() {
        let snippet = shell_snippet(&socket());
        assert!(snippet.contains("ZSH_VERSION"));
        assert!(snippet.contains("BASH_VERSION"));
        assert!(snippet.contains("add-zsh-hook preexec"));
    }

    #[test]
    fn installing_into_an_empty_profile_writes_the_block() {
        let profile = temp_file(".bashrc");

        assert_eq!(
            install_shell_hook(&profile, &socket()).unwrap(),
            Outcome::Installed
        );

        let contents = std::fs::read_to_string(&profile).unwrap();
        assert!(contents.contains(BEGIN_MARKER));
        assert!(contents.contains(END_MARKER));
    }

    #[test]
    fn installing_preserves_what_the_user_already_wrote() {
        let profile = temp_file(".bashrc");
        std::fs::write(&profile, "export EDITOR=vim\nalias g=git\n").unwrap();

        install_shell_hook(&profile, &socket()).unwrap();

        let contents = std::fs::read_to_string(&profile).unwrap();
        assert!(contents.starts_with("export EDITOR=vim\nalias g=git\n"));
        assert!(contents.contains(BEGIN_MARKER));
    }

    #[test]
    fn installing_twice_reports_no_change_and_makes_none() {
        let profile = temp_file(".bashrc");
        install_shell_hook(&profile, &socket()).unwrap();
        let first = std::fs::read_to_string(&profile).unwrap();

        assert_eq!(
            install_shell_hook(&profile, &socket()).unwrap(),
            Outcome::AlreadyCurrent
        );
        assert_eq!(std::fs::read_to_string(&profile).unwrap(), first);
    }

    #[test]
    fn a_changed_socket_path_rewrites_only_our_block() {
        let profile = temp_file(".bashrc");
        std::fs::write(&profile, "before\n").unwrap();
        install_shell_hook(&profile, &socket()).unwrap();
        std::fs::write(
            &profile,
            format!("{}\nafter\n", std::fs::read_to_string(&profile).unwrap()),
        )
        .unwrap();

        assert_eq!(
            install_shell_hook(&profile, Path::new("/new/path.sock")).unwrap(),
            Outcome::Updated
        );

        let contents = std::fs::read_to_string(&profile).unwrap();
        assert!(contents.starts_with("before\n"));
        assert!(contents.trim_end().ends_with("after"));
        assert!(contents.contains("/new/path.sock"));
        assert!(!contents.contains("/run/user/1000"));
        assert_eq!(contents.matches(BEGIN_MARKER).count(), 1);
    }

    #[test]
    fn uninstalling_removes_our_block_and_nothing_else() {
        let profile = temp_file(".bashrc");
        std::fs::write(&profile, "export EDITOR=vim\n").unwrap();
        install_shell_hook(&profile, &socket()).unwrap();

        assert!(uninstall_shell_hook(&profile).unwrap());

        let contents = std::fs::read_to_string(&profile).unwrap();
        assert!(!contents.contains("contextd"));
        assert!(contents.contains("export EDITOR=vim"));
    }

    #[test]
    fn uninstalling_when_not_installed_is_not_an_error() {
        let profile = temp_file(".bashrc");
        std::fs::write(&profile, "export EDITOR=vim\n").unwrap();

        assert!(!uninstall_shell_hook(&profile).unwrap());
        assert_eq!(
            std::fs::read_to_string(&profile).unwrap(),
            "export EDITOR=vim\n"
        );
    }

    #[test]
    fn install_and_uninstall_round_trip_to_the_original() {
        let profile = temp_file(".bashrc");
        let original = "export EDITOR=vim\nalias g=git\n";
        std::fs::write(&profile, original).unwrap();

        install_shell_hook(&profile, &socket()).unwrap();
        uninstall_shell_hook(&profile).unwrap();

        assert_eq!(std::fs::read_to_string(&profile).unwrap(), original);
    }

    #[test]
    fn the_bash_hook_is_not_added_to_the_prompt_twice() {
        let snippet = shell_snippet(&socket());
        assert!(
            snippet.contains("*__contextd_prompt*) ;;"),
            "re-sourcing .bashrc must not stack the hook"
        );
    }

    #[test]
    fn the_profile_matches_the_running_shell() {
        // Writing a zsh hook into .bashrc would silently do nothing.
        unsafe { std::env::set_var("HOME", "/home/tester") };

        unsafe { std::env::set_var("SHELL", "/usr/bin/zsh") };
        assert!(default_profile().ends_with(".zshrc"));

        unsafe { std::env::set_var("SHELL", "/bin/bash") };
        assert!(default_profile().ends_with(".bashrc"));
    }

    #[test]
    fn the_systemd_unit_restarts_the_daemon_but_stays_out_of_the_way() {
        let unit = systemd_unit(Path::new("/usr/local/bin/contextd"));

        // Naming the subcommand rather than relying on the bare-invocation
        // default keeps the unit readable and survives a change to that default.
        assert!(unit.contains("ExecStart=/usr/local/bin/contextd run"));
        assert!(unit.contains("Restart=on-failure"));
        assert!(unit.contains("WantedBy=default.target"));
        // A background watcher must never compete with the compiler it watches.
        assert!(unit.contains("Nice=10"));
        assert!(unit.contains("IOSchedulingClass=idle"));
    }

    #[test]
    fn writing_the_unit_creates_its_directory() {
        let path = temp_file("unit").with_file_name("systemd/user/contextd.service");

        assert_eq!(
            install_systemd_unit(&path, Path::new("/usr/local/bin/contextd")).unwrap(),
            Outcome::Installed
        );
        assert!(path.exists());
    }

    #[test]
    fn rewriting_an_identical_unit_reports_no_change() {
        let path = temp_file("unit2").with_file_name("systemd/user/contextd.service");
        let exe = Path::new("/usr/local/bin/contextd");

        install_systemd_unit(&path, exe).unwrap();
        assert_eq!(
            install_systemd_unit(&path, exe).unwrap(),
            Outcome::AlreadyCurrent
        );

        assert_eq!(
            install_systemd_unit(&path, Path::new("/opt/contextd")).unwrap(),
            Outcome::Updated
        );
    }
}
