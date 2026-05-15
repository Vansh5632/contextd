use contextd_core::event::{EventSource, RawEvent};
use serde_json::json;
use std::{
    ffi::OsStr,
    path::Path,
    time::{SystemTime, UNIX_EPOCH},
};
use tokio::sync::broadcast;
use tracing::{info, warn};
use ulid::Ulid;

/// Listens to internal FileSystem events. If a package manager file changes,
/// it emits a high-value Manifest event.
pub async fn start_manifest_watcher(
    mut rx: broadcast::Receiver<RawEvent>,
    tx: broadcast::Sender<RawEvent>,
) {
    info!("Starting manifest watcher (listening to internal FS events)");

    loop {
        match rx.recv().await {
            Ok(event) => {
                // We only care about FileSystem events
                if event.source == EventSource::FileSystem {
                    if let Some(path_str) = event.payload.get("path").and_then(|v| v.as_str()) {
                        if is_project_manifest_path(Path::new(path_str)) {
                            info!("Manifest change detected: {}", path_str);

                            // In a full implementation, you would use `std::fs::read_to_string` here,
                            // parse the TOML/JSON, and diff the dependencies.
                            // For now, we emit a structural event indicating the context shifted.
                            let derived_event = RawEvent {
                                id: Ulid::new().to_string(),
                                timestamp_ms: current_timestamp_ms(),
                                source: EventSource::Manifest,
                                payload: json!({
                                    "action": "dependencies_updated",
                                    "file": path_str
                                }),
                            };

                            // Inject the new event back into the pipeline
                            if let Err(e) = tx.send(derived_event) {
                                warn!("Failed to broadcast manifest event: {}", e);
                            }
                        }
                    }
                }
            }
            Err(broadcast::error::RecvError::Lagged(missed)) => {
                warn!(
                    "Manifest watcher lagged behind and missed {} events",
                    missed
                );
                continue;
            }
            Err(broadcast::error::RecvError::Closed) => {
                break; // Channel closed, daemon is shutting down
            }
        }
    }
}

fn is_project_manifest_path(path: &Path) -> bool {
    let Some(file_name) = path.file_name() else {
        return false;
    };

    is_manifest_file_name(file_name) && !has_component(path, OsStr::new("node_modules"))
}

fn is_manifest_file_name(file_name: &OsStr) -> bool {
    file_name == OsStr::new("Cargo.toml") || file_name == OsStr::new("package.json")
}

fn has_component(path: &Path, component: &OsStr) -> bool {
    path.components()
        .any(|path_component| path_component.as_os_str() == component)
}

fn current_timestamp_ms() -> u64 {
    match SystemTime::now().duration_since(UNIX_EPOCH) {
        Ok(duration) => duration.as_millis() as u64,
        Err(err) => {
            warn!("system clock is before Unix epoch; using 0 for manifest event timestamp: {err}");
            0
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::time::Duration;

    fn filesystem_event(path: &str) -> RawEvent {
        RawEvent {
            id: "fs-event".to_string(),
            timestamp_ms: 1_000,
            source: EventSource::FileSystem,
            payload: json!({
                "action": "Modify(Metadata(Any))",
                "path": path
            }),
        }
    }

    #[test]
    fn project_manifest_matching_requires_exact_file_name_and_skips_dependency_dirs() {
        assert!(is_project_manifest_path(Path::new("/repo/Cargo.toml")));
        assert!(is_project_manifest_path(Path::new(
            "/repo/packages/web/package.json"
        )));

        assert!(!is_project_manifest_path(Path::new("/repo/myCargo.toml")));
        assert!(!is_project_manifest_path(Path::new(
            "/repo/foo-package.json"
        )));
        assert!(!is_project_manifest_path(Path::new(
            "/repo/node_modules/pkg/package.json"
        )));
    }

    #[tokio::test]
    async fn emits_manifest_event_for_manifest_filesystem_event() {
        let (tx, mut rx) = broadcast::channel(10);
        let watcher_rx = tx.subscribe();
        let watcher = tokio::spawn(start_manifest_watcher(watcher_rx, tx.clone()));

        tx.send(filesystem_event("/repo/Cargo.toml"))
            .expect("filesystem event should publish");

        let manifest_event = tokio::time::timeout(Duration::from_secs(1), async {
            loop {
                let event = rx.recv().await.expect("event bus should remain open");
                if event.source == EventSource::Manifest {
                    break event;
                }
            }
        })
        .await
        .expect("manifest watcher should emit a derived event");

        assert_eq!(manifest_event.source, EventSource::Manifest);
        assert_eq!(
            manifest_event.payload,
            json!({
                "action": "dependencies_updated",
                "file": "/repo/Cargo.toml"
            })
        );

        watcher.abort();
    }
}
