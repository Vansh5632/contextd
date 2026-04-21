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
                    0.7 // Starting a dev server is a strong intent signal
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
