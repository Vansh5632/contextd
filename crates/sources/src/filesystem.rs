use anyhow::{Context, Result};
use contextd_core::event::{EventSource, RawEvent};
use notify::{Config, Event, EventKind, RecommendedWatcher, RecursiveMode, Watcher};
use serde_json::json;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};
use tokio::sync::{broadcast, mpsc};
use tracing::{info, warn};
use ulid::Ulid;

pub async fn start_filesystem_watcher(
    root: PathBuf,
    tx: broadcast::Sender<RawEvent>,
) -> Result<()> {
    let (event_tx, mut event_rx) = mpsc::channel(100);

    let mut watcher = RecommendedWatcher::new(
        move |result| {
            if event_tx.blocking_send(result).is_err() {
                warn!("filesystem watcher receiver dropped");
            }
        },
        Config::default(),
    )
    .context("failed to create filesystem watcher")?;

    watcher
        .watch(&root, RecursiveMode::Recursive)
        .with_context(|| format!("failed to watch {}", root.display()))?;

    info!("Filesystem watcher bound at {}", root.display());

    while let Some(result) = event_rx.recv().await {
        match result {
            Ok(event) => publish_event(event, &tx),
            Err(err) => warn!("filesystem watcher error: {err}"),
        }
    }

    Ok(())
}

fn publish_event(event: Event, tx: &broadcast::Sender<RawEvent>) {
    if !is_interesting_kind(&event.kind) {
        return;
    }

    for path in event.paths.into_iter().filter(|path| should_publish(path)) {
        let timestamp_ms = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis() as u64;

        let raw = RawEvent {
            id: Ulid::new().to_string(),
            timestamp_ms,
            source: EventSource::FileSystem,
            payload: json!({
                "action": format!("{:?}", event.kind),
                "path": path.to_string_lossy()
            }),
        };

        if let Err(err) = tx.send(raw) {
            warn!("failed to broadcast filesystem event: {err}");
        }
    }
}

fn is_interesting_kind(kind: &EventKind) -> bool {
    matches!(
        kind,
        EventKind::Create(_) | EventKind::Modify(_) | EventKind::Remove(_)
    )
}

fn should_publish(path: &Path) -> bool {
    !path
        .components()
        .any(|component| matches!(component.as_os_str().to_str(), Some("target" | ".git")))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn should_publish_ignores_noisy_directories() {
        assert!(!should_publish(Path::new("/repo/target/debug/contextd")));
        assert!(!should_publish(Path::new("/repo/.git/index")));
        assert!(should_publish(Path::new("/repo/Cargo.toml")));
    }
}
