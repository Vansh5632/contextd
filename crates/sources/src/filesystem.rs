use crate::noise::NoiseFilter;
use anyhow::{Context, Result};
use contextd_core::event::{EventSource, RawEvent};
use notify::{Config, Event, EventKind, RecommendedWatcher, RecursiveMode, Watcher};
use serde_json::json;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};
use tokio::sync::{broadcast, mpsc};
use tracing::{info, warn};
use ulid::Ulid;

pub async fn start_filesystem_watcher(
    root: PathBuf,
    tx: broadcast::Sender<RawEvent>,
    filter: NoiseFilter,
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
            Ok(event) => publish_event(event, &tx, &filter),
            Err(err) => warn!("filesystem watcher error: {err}"),
        }
    }

    Ok(())
}

fn publish_event(event: Event, tx: &broadcast::Sender<RawEvent>, filter: &NoiseFilter) {
    if !is_interesting_kind(&event.kind) {
        return;
    }

    for path in event
        .paths
        .into_iter()
        .filter(|path| filter.is_interesting(path))
    {
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

#[cfg(test)]
mod tests {
    use super::*;
    use notify::event::{DataChange, ModifyKind};

    fn modify(paths: &[&str]) -> Event {
        Event {
            kind: EventKind::Modify(ModifyKind::Data(DataChange::Any)),
            paths: paths.iter().map(PathBuf::from).collect(),
            attrs: Default::default(),
        }
    }

    #[test]
    fn noisy_paths_never_reach_the_bus() {
        let (tx, mut rx) = broadcast::channel(8);
        publish_event(
            modify(&["/repo/target/debug/contextd", "/repo/src/main.rs"]),
            &tx,
            &NoiseFilter::new(),
        );

        let event = rx.try_recv().expect("the source file should be published");
        assert_eq!(event.payload["path"], "/repo/src/main.rs");
        assert!(rx.try_recv().is_err(), "only one event should be published");
    }

    #[test]
    fn access_events_are_not_interesting() {
        assert!(!is_interesting_kind(&EventKind::Access(
            notify::event::AccessKind::Read
        )));
        assert!(is_interesting_kind(&EventKind::Modify(ModifyKind::Data(
            DataChange::Any
        ))));
    }
}
