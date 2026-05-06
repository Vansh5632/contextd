use contextd_core::event::{EventSource, ProcessedEvent, RawEvent};

/// Takes a raw event, analyzes its contents purely via rules, and assigns an importance score.
pub fn process_event(raw: RawEvent) -> ProcessedEvent {
    let score = match raw.source {
        EventSource::Shell => {
            // Let's look inside the JSON payload
            if let Some(cmd) = raw.payload.get("command").and_then(|v| v.as_str()) {
                // If it's a build command or an error, it's highly important context
                if cmd.contains("cargo build") || cmd.contains("npm run") || cmd.contains("error") {
                    0.9
                } else if cmd.starts_with("cd ") || cmd.starts_with("ls") {
                    0.1 // Navigation is low importance
                } else {
                    0.6 // Standard commands
                }
            } else {
                0.5
            }
        }
        EventSource::FileSystem => {
            if let Some(path) = raw.payload.get("path").and_then(|v| v.as_str()) {
                if path.contains("Cargo.toml") || path.contains("package.json") {
                    0.8 // Dependency changes are huge context shifts
                } else {
                    0.3 // Standard file saves are noisy, keep score low
                }
            } else {
                0.3
            }
        }
        EventSource::Proc => {
            if let Some(action) = raw.payload.get("action").and_then(|v| v.as_str()) {
                if action == "process_start" {
                    let command = raw
                        .payload
                        .get("command")
                        .and_then(|v| v.as_str())
                        .unwrap_or_default();

                    if command.contains("cargo build") || command.contains("npm run") {
                        0.9
                    } else {
                        0.7 // Starting a dev server is a strong intent signal
                    }
                } else {
                    0.2
                }
            } else {
                0.4
            }
        }
        // Fallback for Git, Editor, Manifest until we build them
        _ => 0.5,
    };

    ProcessedEvent { raw, score }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn raw(source: EventSource, payload: serde_json::Value) -> RawEvent {
        RawEvent {
            id: "test-event".to_string(),
            timestamp_ms: 1_000,
            source,
            payload,
        }
    }

    #[test]
    fn cargo_build_process_start_scores_like_build_intent() {
        let event = raw(
            EventSource::Proc,
            json!({
                "action": "process_start",
                "command": "/usr/bin/cargo build",
                "process_name": "cargo"
            }),
        );

        assert_eq!(process_event(event).score, 0.9);
    }

    #[test]
    fn rustc_error_format_does_not_look_like_user_error_intent() {
        let event = raw(
            EventSource::Proc,
            json!({
                "action": "process_start",
                "command": "/usr/bin/rustc --error-format=json",
                "process_name": "rustc"
            }),
        );

        assert_eq!(process_event(event).score, 0.7);
    }

    #[test]
    fn cargo_toml_filesystem_event_scores_like_manifest_change() {
        let event = raw(
            EventSource::FileSystem,
            json!({"path": "/repo/Cargo.toml", "action": "Modify(Metadata(Any))"}),
        );

        assert_eq!(process_event(event).score, 0.8);
    }
}
