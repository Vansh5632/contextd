//! Turning a payload into a sentence.
//!
//! A briefing built from raw payloads reads like a syslog dump. What an agent
//! actually needs is the one line a colleague would say: "ran the tests, three
//! failed", not `{"action":"process_start","command":"/usr/bin/cargo test"}`.
//!
//! Everything here is rule-based and synchronous. A local model can produce
//! better prose, but it cannot be depended on, and a summary that only exists
//! when Ollama is up is a summary the broker cannot rely on.

use contextd_core::event::{EventSource, RawEvent};

/// Longest summary we will produce. Roughly one line in a terminal, and small
/// enough that a hundred of them still fit in a context window.
const MAX_SUMMARY_CHARS: usize = 160;

/// Markers that indicate a line is reporting a failure.
///
/// Ordered by specificity: the first match wins, so `panicked at` beats a bare
/// `error` appearing later in the same output.
const ERROR_MARKERS: &[&str] = &[
    "panicked at",
    "Traceback (most recent call last)",
    "AssertionError",
    "SyntaxError",
    "TypeError",
    "ValueError",
    "NullPointerException",
    "Segmentation fault",
    "error[E",
    "fatal:",
    "FAILED",
    "Exception",
    "error:",
    "Error:",
    "ERROR",
];

/// A one-line description of what happened.
///
/// Returns `None` when the event has no meaningful text form, in which case the
/// caller should fall back to the raw payload.
pub fn summarize(event: &RawEvent) -> Option<String> {
    let summary = match event.source {
        EventSource::Shell => summarize_shell(event),
        EventSource::Proc => summarize_proc(event),
        EventSource::FileSystem => summarize_filesystem(event),
        EventSource::Git => summarize_git(event),
        EventSource::Manifest => summarize_manifest(event),
        EventSource::Editor => None,
    }?;

    let summary = collapse_whitespace(&summary);
    if summary.is_empty() {
        return None;
    }

    Some(truncate(&summary, MAX_SUMMARY_CHARS))
}

/// The most informative error line in a blob of output, if there is one.
///
/// Used both for summaries and, later, for "have I hit this before" recall,
/// which is why it returns the line rather than a boolean.
pub fn extract_error(text: &str) -> Option<String> {
    for marker in ERROR_MARKERS {
        if let Some(line) = text
            .lines()
            .map(str::trim)
            .find(|line| line.contains(marker))
            && !line.is_empty()
        {
            return Some(truncate(&collapse_whitespace(line), MAX_SUMMARY_CHARS));
        }
    }

    None
}

/// True when the payload looks like it is reporting a failure.
pub fn looks_like_failure(event: &RawEvent) -> bool {
    // An explicit non-zero exit code is far more reliable than text matching.
    if let Some(code) = event.payload.get("exit_code").and_then(|v| v.as_i64()) {
        return code != 0;
    }

    ["stderr", "output", "message", "error"]
        .iter()
        .filter_map(|field| event.payload.get(*field).and_then(|v| v.as_str()))
        .any(|text| extract_error(text).is_some())
}

fn summarize_shell(event: &RawEvent) -> Option<String> {
    let command = event.payload.get("command").and_then(|v| v.as_str())?;
    let command = shorten_command(command);

    // A failing command is the single most useful thing in a briefing, so the
    // error travels with it rather than being buried in the payload.
    let error = ["stderr", "output", "error"]
        .iter()
        .filter_map(|field| event.payload.get(*field).and_then(|v| v.as_str()))
        .find_map(extract_error);

    Some(match error {
        Some(error) => format!("ran `{command}` — failed: {error}"),
        None => match event.payload.get("exit_code").and_then(|v| v.as_i64()) {
            Some(code) if code != 0 => format!("ran `{command}` — exited {code}"),
            _ => format!("ran `{command}`"),
        },
    })
}

fn summarize_proc(event: &RawEvent) -> Option<String> {
    let action = event.payload.get("action").and_then(|v| v.as_str())?;

    // An empty command is not a command. Rendering one produced lines like
    // "process_stop: ``", which is worse than saying nothing.
    let command = event
        .payload
        .get("command")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|command| !command.is_empty())
        .map(shorten_command)
        .or_else(|| {
            event
                .payload
                .get("process_name")
                .and_then(|v| v.as_str())
                .map(str::trim)
                .filter(|name| !name.is_empty())
                .map(str::to_string)
        });

    let command = command?;

    Some(match action {
        "process_start" => format!("started `{command}`"),
        "process_end" => match runtime(event) {
            Some(runtime) => format!("finished `{command}` after {runtime}"),
            None => format!("finished `{command}`"),
        },
        action => format!("{action}: `{command}`"),
    })
}

/// How long a process ran, in words, when that is worth saying.
///
/// Sub-second runtimes are noise; the interesting case is "the build took four
/// minutes", which is context a developer would otherwise have to remember.
fn runtime(event: &RawEvent) -> Option<String> {
    let ms = event.payload.get("ran_for_ms").and_then(|v| v.as_u64())?;
    match ms {
        0..=999 => None,
        1_000..=59_999 => Some(format!("{}s", ms / 1_000)),
        _ => Some(format!("{}m{}s", ms / 60_000, (ms % 60_000) / 1_000)),
    }
}

fn summarize_filesystem(event: &RawEvent) -> Option<String> {
    let path = event.payload.get("path").and_then(|v| v.as_str())?;
    let action = event.payload.get("action").and_then(|v| v.as_str());
    let name = short_path(path);

    // notify's Debug output ("Modify(Data(Any))") is precise and unreadable.
    let verb = match action {
        Some(action) if action.starts_with("Create") => "created",
        Some(action) if action.starts_with("Remove") => "deleted",
        Some(action) if action.starts_with("Modify") => "edited",
        _ => "touched",
    };

    Some(format!("{verb} {name}"))
}

fn summarize_git(event: &RawEvent) -> Option<String> {
    let action = event.payload.get("action").and_then(|v| v.as_str())?;
    let message = event
        .payload
        .get("message")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|message| !message.is_empty());

    Some(match (action, message) {
        ("commit", Some(message)) => {
            // Only the subject line; a commit body is its own document.
            let subject = message.lines().next().unwrap_or(message);
            format!("committed: {subject}")
        }
        ("commit", None) => "committed".to_string(),
        ("checkout", Some(branch)) => format!("switched to {branch}"),
        ("push", _) => "pushed".to_string(),
        (action, Some(detail)) => format!("git {action}: {detail}"),
        (action, None) => format!("git {action}"),
    })
}

fn summarize_manifest(event: &RawEvent) -> Option<String> {
    let file = event
        .payload
        .get("file")
        .and_then(|v| v.as_str())
        .map(short_path)
        .unwrap_or_else(|| "a manifest".to_string());

    Some(format!("changed dependencies in {file}"))
}

/// Strip the absolute path from an executable so `/usr/bin/cargo test` reads as
/// `cargo test`. Keeps arguments, which are the interesting part.
fn shorten_command(command: &str) -> String {
    let command = command.trim();
    let mut parts = command.splitn(2, char::is_whitespace);

    let Some(program) = parts.next() else {
        return command.to_string();
    };
    let program = program.rsplit('/').next().unwrap_or(program);

    match parts.next() {
        Some(args) => format!("{program} {args}"),
        None => program.to_string(),
    }
}

/// The last two path components, which is usually enough to recognise a file
/// without carrying an absolute path into every summary.
fn short_path(path: &str) -> String {
    let components: Vec<&str> = path.rsplit('/').take(2).collect();
    components.into_iter().rev().collect::<Vec<_>>().join("/")
}

fn collapse_whitespace(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn truncate(text: &str, limit: usize) -> String {
    if text.chars().count() <= limit {
        return text.to_string();
    }

    // Cut on a char boundary, not a byte one; payloads contain arbitrary UTF-8.
    let kept: String = text.chars().take(limit.saturating_sub(1)).collect();
    format!("{}…", kept.trim_end())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn event(source: EventSource, payload: serde_json::Value) -> RawEvent {
        RawEvent {
            id: "test".to_string(),
            timestamp_ms: 0,
            source,
            payload,
        }
    }

    #[test]
    fn a_shell_command_reads_like_a_sentence() {
        let summary = summarize(&event(
            EventSource::Shell,
            json!({"command": "/usr/bin/cargo test --workspace"}),
        ));
        assert_eq!(summary.as_deref(), Some("ran `cargo test --workspace`"));
    }

    #[test]
    fn a_failing_command_carries_its_error() {
        let summary = summarize(&event(
            EventSource::Shell,
            json!({
                "command": "cargo build",
                "stderr": "   Compiling contextd\nerror[E0433]: failed to resolve: use of undeclared crate\n",
            }),
        ))
        .unwrap();

        assert!(summary.starts_with("ran `cargo build` — failed:"));
        assert!(
            summary.contains("E0433"),
            "the error code is the useful bit"
        );
    }

    #[test]
    fn a_nonzero_exit_is_reported_even_without_error_text() {
        let summary = summarize(&event(
            EventSource::Shell,
            json!({"command": "make", "exit_code": 2}),
        ));
        assert_eq!(summary.as_deref(), Some("ran `make` — exited 2"));
    }

    #[test]
    fn filesystem_actions_read_as_verbs() {
        let cases = [
            ("Create(File)", "created src/main.rs"),
            ("Modify(Data(Any))", "edited src/main.rs"),
            ("Remove(File)", "deleted src/main.rs"),
        ];

        for (action, expected) in cases {
            let summary = summarize(&event(
                EventSource::FileSystem,
                json!({"action": action, "path": "/home/dev/repo/src/main.rs"}),
            ));
            assert_eq!(summary.as_deref(), Some(expected));
        }
    }

    #[test]
    fn a_commit_keeps_only_its_subject_line() {
        let summary = summarize(&event(
            EventSource::Git,
            json!({
                "action": "commit",
                "message": "fix login redirect loop\n\nThe session cookie was being cleared before the redirect.",
            }),
        ));
        assert_eq!(
            summary.as_deref(),
            Some("committed: fix login redirect loop")
        );
    }

    #[test]
    fn process_events_distinguish_start_from_end() {
        assert_eq!(
            summarize(&event(
                EventSource::Proc,
                json!({"action": "process_start", "command": "/usr/bin/cargo build"}),
            ))
            .as_deref(),
            Some("started `cargo build`")
        );
        assert_eq!(
            summarize(&event(
                EventSource::Proc,
                json!({"action": "process_end", "command": "/usr/bin/cargo build"}),
            ))
            .as_deref(),
            Some("finished `cargo build`")
        );
    }

    #[test]
    fn a_finished_process_says_how_long_it_took() {
        // "the build took four minutes" is context a developer would otherwise
        // have to hold in their head.
        assert_eq!(
            summarize(&event(
                EventSource::Proc,
                json!({
                    "action": "process_end",
                    "command": "cargo build --release",
                    "ran_for_ms": 245_000,
                }),
            ))
            .as_deref(),
            Some("finished `cargo build --release` after 4m5s")
        );
    }

    #[test]
    fn a_brief_runtime_is_not_worth_reporting() {
        assert_eq!(
            summarize(&event(
                EventSource::Proc,
                json!({"action": "process_end", "command": "ls", "ran_for_ms": 12}),
            ))
            .as_deref(),
            Some("finished `ls`")
        );
    }

    #[test]
    fn a_process_with_no_command_falls_back_to_its_name() {
        assert_eq!(
            summarize(&event(
                EventSource::Proc,
                json!({"action": "process_start", "command": "", "process_name": "node"}),
            ))
            .as_deref(),
            Some("started `node`")
        );
    }

    #[test]
    fn a_process_event_with_nothing_to_name_says_nothing() {
        // The old code rendered this as "process_stop: ``", which is noise
        // dressed up as information.
        assert_eq!(
            summarize(&event(
                EventSource::Proc,
                json!({"action": "process_stop", "command": ""}),
            )),
            None
        );
    }

    #[test]
    fn manifest_changes_say_what_changed() {
        assert_eq!(
            summarize(&event(
                EventSource::Manifest,
                json!({"action": "dependencies_updated", "file": "/repo/Cargo.toml"}),
            ))
            .as_deref(),
            Some("changed dependencies in repo/Cargo.toml")
        );
    }

    #[test]
    fn a_payload_with_nothing_to_say_produces_no_summary() {
        assert!(summarize(&event(EventSource::Shell, json!({}))).is_none());
        assert!(summarize(&event(EventSource::FileSystem, json!({"size": 4}))).is_none());
    }

    #[test]
    fn summaries_are_bounded_and_cut_on_char_boundaries() {
        let long = "x".repeat(500);
        let summary = summarize(&event(
            EventSource::Shell,
            json!({ "command": format!("echo {long}") }),
        ))
        .unwrap();

        assert!(summary.chars().count() <= MAX_SUMMARY_CHARS);
        assert!(summary.ends_with('…'));
    }

    #[test]
    fn multibyte_payloads_do_not_panic_when_truncated() {
        let summary = summarize(&event(
            EventSource::Shell,
            json!({ "command": format!("echo {}", "日本語テキスト".repeat(60)) }),
        ))
        .unwrap();

        assert!(summary.chars().count() <= MAX_SUMMARY_CHARS);
    }

    #[test]
    fn error_extraction_prefers_the_most_specific_marker() {
        // Both markers are present; "panicked at" is the one worth surfacing.
        let text = "error: test failed\nthread 'main' panicked at src/lib.rs:12:5";
        let extracted = extract_error(text).unwrap();
        assert!(extracted.contains("panicked at"), "got: {extracted}");
    }

    #[test]
    fn error_extraction_finds_python_tracebacks() {
        let text = "Traceback (most recent call last):\n  File \"x.py\", line 1\nValueError: bad";
        assert!(extract_error(text).is_some());
    }

    #[test]
    fn clean_output_has_no_error() {
        assert!(extract_error("Compiling contextd\nFinished in 3.2s").is_none());
        assert!(extract_error("").is_none());
    }

    #[test]
    fn failure_detection_trusts_the_exit_code_over_the_text() {
        // Output that merely mentions the word "error" did not necessarily fail.
        let succeeded = event(
            EventSource::Shell,
            json!({"command": "grep error log.txt", "exit_code": 0, "output": "error: x"}),
        );
        assert!(!looks_like_failure(&succeeded));

        let failed = event(
            EventSource::Shell,
            json!({"command": "cargo build", "exit_code": 101}),
        );
        assert!(looks_like_failure(&failed));
    }

    #[test]
    fn failure_detection_falls_back_to_text_without_an_exit_code() {
        let failed = event(
            EventSource::Shell,
            json!({"command": "pytest", "stderr": "AssertionError: expected 3"}),
        );
        assert!(looks_like_failure(&failed));
    }

    #[test]
    fn a_bare_command_with_no_arguments_still_shortens() {
        assert_eq!(shorten_command("/usr/local/bin/make"), "make");
        assert_eq!(shorten_command("ls"), "ls");
    }
}
