//! The shell code that actually hands an event to the daemon.
//!
//! Both the git hooks and the shell profile hook need to do the same thing:
//! push one JSON line into a Unix socket without ever making the caller wait.
//! Getting that wrong is unusually costly — a hook that blocks is a commit that
//! hangs — so the logic lives here once rather than being written twice.

use std::path::Path;

/// Name of the POSIX function [`emit_function`] defines.
pub const EMIT_FN: &str = "__contextd_emit";

/// A POSIX shell function that sends one line to the daemon, or gives up.
///
/// Called as `__contextd_emit "$PAYLOAD"`, reading the socket from
/// `$CONTEXTD_SOCKET`. It returns immediately and always succeeds.
///
/// Three things have to be true and none of them is automatic:
///
/// - **It cannot block.** The send is backgrounded in a subshell whose output
///   is redirected, which matters because git waits for EOF on the hook's
///   stdout, not merely for the hook to exit. A child still holding that pipe
///   would keep git waiting even after the hook returned.
/// - **`nc` has to be told to hang up.** Most netcats do not half-close the
///   socket when stdin ends, so `nc` waits for the daemon while the daemon
///   waits for another line — a deadlock that outlives the hook. OpenBSD netcat
///   spells the half-close `-N`, GNU spells it `-q0`, and `timeout` is the
///   backstop for the ones that have neither. The event still arrives in that
///   last case: the daemon acts on the line as soon as it reads it, so bounding
///   the wait costs nothing.
/// - **It cannot return non-zero.** In a git hook that would fail the commit;
///   in a bash `PROMPT_COMMAND` it would corrupt `$?`.
pub fn emit_function() -> String {
    format!(
        r#"{EMIT_FN}() {{
  [ -S "$CONTEXTD_SOCKET" ] || return 0

  # Backgrounded, silenced, and time-bounded on purpose: contextd being slow or
  # absent must never be something the caller notices. See crates/sources/src/emit.rs.
  (
    if command -v nc >/dev/null 2>&1; then
      # -N (openbsd) and -q0 (gnu) both half-close after stdin ends. Without one
      # of them nc waits for a daemon that is waiting for nc.
      printf '%s\n' "$1" | nc -U -N "$CONTEXTD_SOCKET" \
        || printf '%s\n' "$1" | nc -U -q0 "$CONTEXTD_SOCKET" \
        || printf '%s\n' "$1" | {{
             if command -v timeout >/dev/null 2>&1; then
               timeout 2 nc -U "$CONTEXTD_SOCKET"
             else
               nc -U "$CONTEXTD_SOCKET"
             fi
           }}
    elif command -v socat >/dev/null 2>&1; then
      printf '%s\n' "$1" | socat -t0 - "UNIX-CONNECT:$CONTEXTD_SOCKET"
    fi
  ) >/dev/null 2>&1 &

  return 0
}}"#
    )
}

/// Shell that sets `CONTEXTD_SOCKET`, leaving an existing value alone.
///
/// The environment wins so that a daemon on a non-default socket can be
/// reached without rewriting every hook in every repository.
pub fn socket_default(socket_path: &Path) -> String {
    format!(
        r#"CONTEXTD_SOCKET="${{CONTEXTD_SOCKET:-{}}}""#,
        socket_path.display()
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_send_is_backgrounded_with_its_output_redirected() {
        // Both halves matter: git waits for EOF on the hook's stdout, so a
        // background child still holding that pipe keeps git waiting.
        let function = emit_function();
        assert!(
            function.contains(") >/dev/null 2>&1 &"),
            "the subshell must be both redirected and backgrounded"
        );
    }

    #[test]
    fn every_netcat_dialect_is_asked_to_hang_up() {
        let function = emit_function();
        assert!(function.contains("nc -U -N"), "openbsd half-close");
        assert!(function.contains("nc -U -q0"), "gnu half-close");
        assert!(
            function.contains("timeout 2 nc -U"),
            "backstop for the rest"
        );
    }

    #[test]
    fn there_is_a_path_for_machines_without_netcat() {
        assert!(emit_function().contains("socat"));
    }

    #[test]
    fn emitting_always_succeeds() {
        // A non-zero return fails the commit in a git hook and corrupts $? in a
        // bash prompt.
        let function = emit_function();
        assert!(function.contains("return 0"));
        assert!(
            function.contains(r#"[ -S "$CONTEXTD_SOCKET" ] || return 0"#),
            "a missing socket is not an error"
        );
    }

    #[test]
    fn the_environment_overrides_the_built_in_socket_path() {
        let line = socket_default(Path::new("/run/user/1000/contextd/contextd.sock"));
        assert_eq!(
            line,
            r#"CONTEXTD_SOCKET="${CONTEXTD_SOCKET:-/run/user/1000/contextd/contextd.sock}""#
        );
    }

    #[test]
    fn the_function_is_valid_posix_shell() {
        // Catches quoting mistakes in the template, which are otherwise only
        // found by someone's commit hanging.
        let script = format!(
            "{}\n{}\n",
            socket_default(Path::new("/nonexistent.sock")),
            emit_function()
        );

        let status = std::process::Command::new("sh")
            .arg("-n")
            .arg("-c")
            .arg(&script)
            .status();

        if let Ok(status) = status {
            assert!(
                status.success(),
                "generated shell does not parse:\n{script}"
            );
        }
    }

    #[test]
    fn emitting_to_a_socket_that_is_not_there_returns_immediately() {
        // The real property: no daemon, no delay, no error.
        let script = format!(
            "{}\n{}\n{EMIT_FN} '{{\"source\":\"test\"}}'\necho done\n",
            socket_default(Path::new("/nonexistent/contextd.sock")),
            emit_function(),
        );

        let started = std::time::Instant::now();
        let Ok(output) = std::process::Command::new("sh")
            .arg("-c")
            .arg(&script)
            .output()
        else {
            return;
        };

        assert!(output.status.success());
        assert_eq!(String::from_utf8_lossy(&output.stdout).trim(), "done");
        assert!(
            started.elapsed() < std::time::Duration::from_secs(2),
            "a missing daemon should cost nothing"
        );
    }
}
