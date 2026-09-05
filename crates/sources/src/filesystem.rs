use crate::noise::NoiseFilter;
use anyhow::{Context, Result};
use contextd_core::event::{EventSource, RawEvent};
use notify::{Config, Event, EventKind, PollWatcher, RecommendedWatcher, RecursiveMode, Watcher};
use serde_json::json;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio::sync::{broadcast, mpsc};
use tracing::{info, warn};
use ulid::Ulid;

/// How often to scan the tree when inotify cannot be created or cannot
/// register watches.
///
/// Linux has two separate ceilings: `max_user_instances` (`inotify_init`, hit
/// in `RecommendedWatcher::new`) and `max_user_watches` (`inotify_add_watch`,
/// hit later in recursive `watch()`, reported as `ENOSPC` / `MaxFilesWatch`).
/// Polling is slower but still records saves, which is the point of this sensor.
const POLL_INTERVAL: Duration = Duration::from_secs(2);

type BoxedWatcher = Box<dyn Watcher + Send>;

pub async fn start_filesystem_watcher(
    root: PathBuf,
    tx: broadcast::Sender<RawEvent>,
    filter: NoiseFilter,
) -> Result<()> {
    let (event_tx, mut event_rx) = mpsc::channel(100);

    // The watcher must stay alive for as long as we read `event_rx`.
    let _keep_watching = bind_and_watch(&root, event_tx)?;

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

fn bind_and_watch(
    root: &Path,
    event_tx: mpsc::Sender<notify::Result<Event>>,
) -> Result<BoxedWatcher> {
    let native = RecommendedWatcher::new(event_forwarder(event_tx.clone()), Config::default())
        .map(|watcher| Box::new(watcher) as BoxedWatcher);

    watch_or_poll(root, native, move || {
        PollWatcher::new(
            event_forwarder(event_tx),
            Config::default().with_poll_interval(POLL_INTERVAL),
        )
        .map(|watcher| Box::new(watcher) as BoxedWatcher)
        .context("failed to create polling filesystem watcher")
    })
}

fn event_forwarder(
    event_tx: mpsc::Sender<notify::Result<Event>>,
) -> impl FnMut(notify::Result<Event>) + Send + 'static {
    move |result| {
        if event_tx.blocking_send(result).is_err() {
            warn!("filesystem watcher receiver dropped");
        }
    }
}

/// Try the native watcher (inotify on Linux). Fall back to polling if creating
/// it fails (`max_user_instances`) or if recursive `watch()` hits the OS watch
/// limit (`max_user_watches` / `MaxFilesWatch`).
///
/// Other watch errors still fail: a missing `watch_root` is a config problem,
/// and `PollWatcher::watch` would hide it by always returning `Ok`.
fn watch_or_poll(
    root: &Path,
    native: notify::Result<BoxedWatcher>,
    poll: impl FnOnce() -> Result<BoxedWatcher>,
) -> Result<BoxedWatcher> {
    match native {
        Ok(mut watcher) => match watcher.watch(root, RecursiveMode::Recursive) {
            Ok(()) => Ok(watcher),
            Err(err) if matches!(err.kind, notify::ErrorKind::MaxFilesWatch) => {
                warn!(
                    error = %err,
                    "inotify watch limit reached; polling the filesystem instead"
                );
                // Drop first so any watches already registered are released
                // before polling starts (recursive watch can fail mid-walk).
                drop(watcher);
                start_poll(root, poll)
            }
            Err(err) => Err(err).with_context(|| format!("failed to watch {}", root.display())),
        },
        Err(err) => {
            warn!(
                error = %err,
                "inotify unavailable; polling the filesystem instead"
            );
            start_poll(root, poll)
        }
    }
}

fn start_poll(root: &Path, poll: impl FnOnce() -> Result<BoxedWatcher>) -> Result<BoxedWatcher> {
    let mut poller = poll()?;
    poller
        .watch(root, RecursiveMode::Recursive)
        .with_context(|| format!("failed to watch {}", root.display()))?;
    Ok(poller)
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
    use std::sync::Arc;
    use std::sync::atomic::{AtomicBool, Ordering};

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

    #[test]
    fn a_small_tree_can_be_watched() {
        let root = scratch_dir();
        let (tx, _rx) = mpsc::channel(1);
        let watcher = bind_and_watch(&root, tx).expect("a small tree should bind");
        drop(watcher);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn polling_is_used_when_inotify_cannot_be_created() {
        let root = scratch_dir();
        let (tx, _rx) = mpsc::channel(1);
        let used_poll = Arc::new(AtomicBool::new(false));

        let result = watch_or_poll(
            &root,
            Err(notify::Error::generic("inotify_init failed")),
            poll_backend(tx, Arc::clone(&used_poll)),
        );

        let _ = std::fs::remove_dir_all(&root);
        result.expect("creating the native watcher failing should still poll");
        assert!(
            used_poll.load(Ordering::SeqCst),
            "PollWatcher should be created when inotify_init fails"
        );
    }

    #[test]
    fn polling_is_used_when_recursive_watch_hits_the_os_limit() {
        let root = scratch_dir();
        let (tx, _rx) = mpsc::channel(1);
        let used_poll = Arc::new(AtomicBool::new(false));

        let result = watch_or_poll(
            &root,
            Ok(Box::new(WatchLimitExceeded) as BoxedWatcher),
            poll_backend(tx, Arc::clone(&used_poll)),
        );

        let _ = std::fs::remove_dir_all(&root);
        result.expect("should fall back to polling when inotify cannot watch the tree");
        assert!(
            used_poll.load(Ordering::SeqCst),
            "PollWatcher should be created after MaxFilesWatch"
        );
    }

    #[test]
    fn a_missing_path_is_still_an_error() {
        let root = scratch_dir();
        let (tx, _rx) = mpsc::channel(1);
        let used_poll = Arc::new(AtomicBool::new(false));

        let result = watch_or_poll(
            &root,
            Ok(Box::new(WatchPathMissing) as BoxedWatcher),
            poll_backend(tx, Arc::clone(&used_poll)),
        );

        let _ = std::fs::remove_dir_all(&root);
        assert!(
            result.is_err(),
            "a missing tree should not be hidden by polling"
        );
        assert!(
            !used_poll.load(Ordering::SeqCst),
            "PollWatcher is for inotify limits, not for a bad watch_root"
        );
    }

    fn scratch_dir() -> PathBuf {
        let id = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("contextd-fs-watch-{id}"));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn poll_backend(
        event_tx: mpsc::Sender<notify::Result<Event>>,
        used: Arc<AtomicBool>,
    ) -> impl FnOnce() -> Result<BoxedWatcher> {
        move || {
            used.store(true, Ordering::SeqCst);
            PollWatcher::new(
                move |result| {
                    let _ = event_tx.blocking_send(result);
                },
                Config::default().with_poll_interval(POLL_INTERVAL),
            )
            .map(|watcher| Box::new(watcher) as BoxedWatcher)
            .context("failed to create polling filesystem watcher")
        }
    }

    /// Native backend that reproduces `inotify_add_watch` returning ENOSPC:
    /// the watcher object exists, recursive `watch()` is what fails.
    struct WatchLimitExceeded;

    impl Watcher for WatchLimitExceeded {
        fn new<F: notify::EventHandler>(_: F, _: Config) -> notify::Result<Self> {
            Ok(Self)
        }

        fn watch(&mut self, _: &Path, _: RecursiveMode) -> notify::Result<()> {
            Err(notify::Error::new(notify::ErrorKind::MaxFilesWatch))
        }

        fn unwatch(&mut self, _: &Path) -> notify::Result<()> {
            Ok(())
        }

        fn kind() -> notify::WatcherKind {
            notify::WatcherKind::Inotify
        }
    }

    struct WatchPathMissing;

    impl Watcher for WatchPathMissing {
        fn new<F: notify::EventHandler>(_: F, _: Config) -> notify::Result<Self> {
            Ok(Self)
        }

        fn watch(&mut self, _: &Path, _: RecursiveMode) -> notify::Result<()> {
            Err(notify::Error::path_not_found())
        }

        fn unwatch(&mut self, _: &Path) -> notify::Result<()> {
            Ok(())
        }

        fn kind() -> notify::WatcherKind {
            notify::WatcherKind::Inotify
        }
    }
}
