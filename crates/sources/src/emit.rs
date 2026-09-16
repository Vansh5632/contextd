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

/// POSIX function that turns a string into JSON string *contents*.
///
/// Called as `json_escape "$VALUE"`. The result is interpolated into a
/// `"%s"` slot in a `printf` JSON template, so it must be legal inside a
/// JSON string: backslash, quote, and every byte below 0x20 (`\b` `\t` `\n`
/// `\f` `\r`, otherwise `\u00XX`). A raw control character here is why
/// `serde_json` used to drop the whole event while the hook still exited 0.
///
/// `awk` splits on newlines, so `printf '%s\n'` adds one extra terminator:
/// a value that already ended in a newline becomes an extra empty record
/// (kept as `\n`), and a value that did not is unchanged. GNU-only `sed`
/// looping is not portable.
pub fn json_escape_function() -> String {
    r###"json_escape() {
  printf '%s\n' "$1" | awk '
    BEGIN {
      ORS=""
      for (i = 1; i < 32; i++) ord[sprintf("%c", i)] = i
    }
    {
      if (NR > 1) printf "\\n"
      n = length($0)
      for (i = 1; i <= n; i++) {
        c = substr($0, i, 1)
        if (c == "\\") printf "\\\\"
        else if (c == "\"") printf "\\\""
        else if (c == "\b") printf "\\b"
        else if (c == "\t") printf "\\t"
        else if (c == "\f") printf "\\f"
        else if (c == "\r") printf "\\r"
        else if (c in ord) printf "\\u00%02x", ord[c]
        else printf "%s", c
      }
    }'
}"###
    .to_string()
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

    /// Run the generated `json_escape` under `sh` and wrap the result as a
    /// JSON string. Returns `None` when this machine has no `sh`.
    fn json_string_via_sh(input: &str) -> Option<String> {
        let script = format!(
            "{}\nprintf '%s' \"$(json_escape \"$CONTEXTD_JSON_ESCAPE_INPUT\")\"\n",
            json_escape_function()
        );
        let output = std::process::Command::new("sh")
            .env("CONTEXTD_JSON_ESCAPE_INPUT", input)
            .arg("-c")
            .arg(script)
            .output()
            .ok()?;
        if !output.status.success() {
            panic!(
                "json_escape failed: {}",
                String::from_utf8_lossy(&output.stderr)
            );
        }
        Some(String::from_utf8_lossy(&output.stdout).into_owned())
    }

    fn decoded_json_string(input: &str) -> Option<String> {
        let escaped = json_string_via_sh(input)?;
        let payload = format!(r#"{{"x":"{escaped}"}}"#);
        let value: serde_json::Value = serde_json::from_str(&payload).unwrap_or_else(|err| {
            panic!("json_escape produced invalid JSON for {input:?}: {err}\n{payload}")
        });
        Some(
            value["x"]
                .as_str()
                .expect("x should be a string")
                .to_string(),
        )
    }

    #[test]
    fn json_escape_round_trips_quotes_and_backslashes() {
        let Some(got) = decoded_json_string(r#"say "hi""#) else {
            return;
        };
        assert_eq!(got, r#"say "hi""#);

        let Some(got) = decoded_json_string(r"a\b") else {
            return;
        };
        assert_eq!(got, r"a\b");

        let Some(got) = decoded_json_string(r#"quote"and\slash"#) else {
            return;
        };
        assert_eq!(got, r#"quote"and\slash"#);
    }

    #[test]
    fn json_escape_encodes_a_tab_so_serde_json_accepts_the_line() {
        // The old `sed 's/\\/\\\\/g; s/"/\\"/g'` left tabs raw. JSON forbids
        // that, so a commit subject with a tab never reached ingest.
        let input = "fix:\tlogin";
        let Some(escaped) = json_string_via_sh(input) else {
            return;
        };
        assert!(
            !escaped.contains('\t'),
            "a raw tab is illegal inside a JSON string: {escaped:?}"
        );
        let Some(got) = decoded_json_string(input) else {
            return;
        };
        assert_eq!(got, input);
    }

    #[test]
    fn json_escape_keeps_a_newline_as_one_socket_line() {
        let input = "first\nsecond";
        let Some(escaped) = json_string_via_sh(input) else {
            return;
        };
        assert!(
            !escaped.contains('\n'),
            "a raw newline would split the socket payload: {escaped:?}"
        );
        let Some(got) = decoded_json_string(input) else {
            return;
        };
        assert_eq!(got, input);
    }

    #[test]
    fn json_escape_preserves_a_trailing_newline() {
        // awk drops the final record separator unless we feed it an extra
        // newline. Command substitution then keeps the encoded `\n`.
        let input = "hello\n";
        let Some(got) = decoded_json_string(input) else {
            return;
        };
        assert_eq!(got, input);
        let Some(got) = decoded_json_string("\n") else {
            return;
        };
        assert_eq!(got, "\n");
    }

    #[test]
    fn json_escape_does_not_invent_a_trailing_newline() {
        let Some(got) = decoded_json_string("hello") else {
            return;
        };
        assert_eq!(got, "hello");
    }

    #[test]
    fn json_escape_encodes_every_json_control_character() {
        // serde_json rejects any raw byte below 0x20, not just tab/CR/LF.
        let input = "a\u{08}b\u{0c}c\u{01}d";
        let Some(escaped) = json_string_via_sh(input) else {
            return;
        };
        assert!(
            !escaped.chars().any(|c| (c as u32) < 0x20),
            "raw C0 in JSON string: {escaped:?}"
        );
        let Some(got) = decoded_json_string(input) else {
            return;
        };
        assert_eq!(got, input);
    }

    #[test]
    fn json_escape_is_valid_posix_shell() {
        let status = std::process::Command::new("sh")
            .arg("-n")
            .arg("-c")
            .arg(json_escape_function())
            .status();
        if let Ok(status) = status {
            assert!(
                status.success(),
                "json_escape does not parse:\n{}",
                json_escape_function()
            );
        }
    }
}
