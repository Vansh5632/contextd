use crate::noise::NoiseFilter;
use anyhow::{Context, Result};
use contextd_core::event::{EventSource, RawEvent};
use notify::{Config, Event, EventKind, PollWatcher, RecommendedWatcher, RecursiveMode, Watcher};
use serde_json::json;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio::sync::{broadcast, mpsc};
use tracing::{info, warn};
use ulid::Ulid;

/// How often to scan interesting directories when inotify cannot be created
/// or cannot register watches.
///
/// Linux has two ceilings: `max_user_instances` (`inotify_init`, hit in
/// `RecommendedWatcher::new`) and `max_user_watches` (`inotify_add_watch`,
/// hit later in `watch()`, reported as `ENOSPC` / `MaxFilesWatch`). Polling
/// is slower but still records saves. It must scan only the directories
/// `NoiseFilter` cares about — a recursive poll of `target/` would be the
/// loaded-machine path this fallback exists to survive.
const POLL_INTERVAL: Duration = Duration::from_secs(2);

type BoxedWatcher = Box<dyn Watcher + Send>;

pub async fn start_filesystem_watcher(
    root: PathBuf,
    tx: broadcast::Sender<RawEvent>,
    filter: NoiseFilter,
) -> Result<()> {
    let (event_tx, mut event_rx) = mpsc::channel(100);

    // The watcher must stay alive for as long as we read `event_rx`.
    let mut watcher = bind_and_watch(&root, event_tx, &filter)?;

    info!("Filesystem watcher bound at {}", root.display());

    while let Some(result) = event_rx.recv().await {
        match result {
            Ok(event) => {
                watch_created_directories(&mut *watcher, &event, &filter);
                publish_event(event, &tx, &filter);
            }
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
    filter: &NoiseFilter,
) -> Result<BoxedWatcher> {
    let native = RecommendedWatcher::new(event_forwarder(event_tx.clone()), Config::default())
        .map(|watcher| Box::new(watcher) as BoxedWatcher);

    watch_or_poll(root, filter, native, move || {
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
/// it fails (`max_user_instances`) or if `watch()` hits the OS watch limit
/// (`max_user_watches` / `MaxFilesWatch`).
///
/// Other watch errors still fail: a missing `watch_root` is a config problem,
/// and `PollWatcher::watch` would hide it by always returning `Ok`.
fn watch_or_poll(
    root: &Path,
    filter: &NoiseFilter,
    native: notify::Result<BoxedWatcher>,
    poll: impl FnOnce() -> Result<BoxedWatcher>,
) -> Result<BoxedWatcher> {
    if !root.is_dir() {
        anyhow::bail!("failed to watch {}: not a directory", root.display());
    }

    let dirs = interesting_watch_dirs(root, filter);

    match native {
        Ok(mut watcher) => match attach_watches(&mut *watcher, &dirs) {
            Ok(()) => Ok(watcher),
            Err(err) if matches!(err.kind, notify::ErrorKind::MaxFilesWatch) => {
                warn!(
                    error = %err,
                    "inotify watch limit reached; polling the filesystem instead"
                );
                // Drop first so any watches already registered are released
                // before polling starts (watch can fail mid-list).
                drop(watcher);
                start_poll(&dirs, poll)
            }
            Err(err) => Err(err).with_context(|| format!("failed to watch {}", root.display())),
        },
        Err(err) => {
            warn!(
                error = %err,
                "inotify unavailable; polling the filesystem instead"
            );
            start_poll(&dirs, poll)
        }
    }
}

fn start_poll(
    dirs: &[PathBuf],
    poll: impl FnOnce() -> Result<BoxedWatcher>,
) -> Result<BoxedWatcher> {
    let mut poller = poll()?;
    attach_watches(&mut *poller, dirs)
        .context("failed to watch with polling filesystem watcher")?;
    Ok(poller)
}

/// Directories that should have a kernel watch or a poll scan.
///
/// `notify`'s recursive mode walks `target/`, `node_modules/`, and `.git/`
/// itself. Filtering only in `publish_event` still pays for those trees.
fn interesting_watch_dirs(root: &Path, filter: &NoiseFilter) -> Vec<PathBuf> {
    let mut dirs = Vec::new();
    collect_interesting_dirs(root, filter, &mut dirs);
    dirs
}

fn collect_interesting_dirs(dir: &Path, filter: &NoiseFilter, out: &mut Vec<PathBuf>) {
    out.push(dir.to_path_buf());
    let Ok(entries) = fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let Ok(file_type) = entry.file_type() else {
            continue;
        };
        if file_type.is_symlink() || !file_type.is_dir() {
            continue;
        }
        let path = entry.path();
        if filter.is_interesting(&path) {
            collect_interesting_dirs(&path, filter, out);
        }
    }
}

fn attach_watches(watcher: &mut dyn Watcher, dirs: &[PathBuf]) -> notify::Result<()> {
    for dir in dirs {
        watcher.watch(dir, RecursiveMode::NonRecursive)?;
    }
    Ok(())
}

fn watch_created_directories(watcher: &mut dyn Watcher, event: &Event, filter: &NoiseFilter) {
    if !matches!(event.kind, EventKind::Create(_)) {
        return;
    }
    for path in &event.paths {
        if !filter.is_interesting(path) || !path.is_dir() {
            continue;
        }
        if let Err(err) = watcher.watch(path, RecursiveMode::NonRecursive) {
            warn!(
                path = %path.display(),
                error = %err,
                "failed to watch newly created directory"
            );
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
    use std::path::Path;
    use std::sync::Arc;
    use std::sync::Mutex;
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
    fn noise_directories_are_not_selected_for_watching() {
        let root = scratch_dir();
        std::fs::create_dir_all(root.join("src/nested")).unwrap();
        std::fs::create_dir_all(root.join("target/debug")).unwrap();
        std::fs::create_dir_all(root.join("node_modules/pkg")).unwrap();
        std::fs::create_dir_all(root.join(".git/objects")).unwrap();
        std::fs::write(root.join("src/main.rs"), "fn main() {}").unwrap();
        std::fs::write(root.join("target/debug/app"), "bin").unwrap();

        let dirs = interesting_watch_dirs(&root, &NoiseFilter::new());
        let _ = std::fs::remove_dir_all(&root);

        assert!(
            dirs.contains(&root),
            "the project root itself must still be watched"
        );
        assert!(
            dirs.contains(&root.join("src")),
            "source directories must be watched"
        );
        assert!(
            dirs.contains(&root.join("src/nested")),
            "nested source directories must be watched"
        );
        for noise in ["target", "node_modules", ".git"] {
            assert!(
                dirs.iter().all(|dir| !dir
                    .components()
                    .any(|component| component.as_os_str() == noise)),
                "{noise}/ must not be selected for watching"
            );
        }
    }

    #[test]
    fn watches_are_non_recursive_and_skip_noise() {
        let root = scratch_dir();
        std::fs::create_dir_all(root.join("src")).unwrap();
        std::fs::create_dir_all(root.join("target/debug")).unwrap();

        let recorded = Arc::new(Mutex::new(Vec::new()));
        let mut watcher = RecordingWatcher {
            watched: Arc::clone(&recorded),
        };
        attach_watches(
            &mut watcher,
            &interesting_watch_dirs(&root, &NoiseFilter::new()),
        )
        .expect("attaching watches to a recording backend should succeed");

        let watched = recorded.lock().unwrap().clone();
        let _ = std::fs::remove_dir_all(&root);

        assert!(
            watched
                .iter()
                .all(|(_, mode)| *mode == RecursiveMode::NonRecursive),
            "each interesting directory is watched non-recursively so notify cannot walk target/"
        );
        assert!(watched.iter().any(|(path, _)| path == &root));
        assert!(watched.iter().any(|(path, _)| path == &root.join("src")));
        assert!(
            watched.iter().all(|(path, _)| !path
                .components()
                .any(|component| component.as_os_str() == "target")),
            "target/ must never be registered"
        );
    }

    #[test]
    fn polling_is_used_when_inotify_cannot_be_created() {
        let root = scratch_dir();
        std::fs::create_dir_all(root.join("src")).unwrap();
        std::fs::create_dir_all(root.join("target/debug")).unwrap();
        let used_poll = Arc::new(AtomicBool::new(false));
        let recorded = Arc::new(Mutex::new(Vec::new()));

        let result = watch_or_poll(
            &root,
            &NoiseFilter::new(),
            Err(notify::Error::generic("inotify_init failed")),
            poll_backend(Arc::clone(&used_poll), Arc::clone(&recorded)),
        );

        let watched = recorded.lock().unwrap().clone();
        let _ = std::fs::remove_dir_all(&root);
        result.expect("creating the native watcher failing should still poll");
        assert!(
            used_poll.load(Ordering::SeqCst),
            "PollWatcher should be created when inotify_init fails"
        );
        assert!(
            watched.iter().any(|(path, _)| path == &root.join("src")),
            "the fallback must still watch source directories"
        );
        assert!(
            watched.iter().all(|(path, _)| !path
                .components()
                .any(|component| component.as_os_str() == "target")),
            "the fallback must not poll target/"
        );
    }

    #[test]
    fn polling_is_used_when_watch_hits_the_os_limit() {
        let root = scratch_dir();
        std::fs::create_dir_all(root.join("src")).unwrap();
        let used_poll = Arc::new(AtomicBool::new(false));
        let recorded = Arc::new(Mutex::new(Vec::new()));

        let result = watch_or_poll(
            &root,
            &NoiseFilter::new(),
            Ok(Box::new(WatchLimitExceeded) as BoxedWatcher),
            poll_backend(Arc::clone(&used_poll), Arc::clone(&recorded)),
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
        let root = scratch_dir().join("does-not-exist");
        let used_poll = Arc::new(AtomicBool::new(false));
        let recorded = Arc::new(Mutex::new(Vec::new()));

        let result = watch_or_poll(
            &root,
            &NoiseFilter::new(),
            Ok(Box::new(WatchPathMissing) as BoxedWatcher),
            poll_backend(Arc::clone(&used_poll), Arc::clone(&recorded)),
        );

        assert!(
            result.is_err(),
            "a missing tree should not be hidden by polling"
        );
        assert!(
            !used_poll.load(Ordering::SeqCst),
            "PollWatcher is for inotify limits, not for a bad watch_root"
        );
    }

    #[test]
    fn newly_created_source_directories_get_a_watch() {
        let root = scratch_dir();
        let nested = root.join("src/newmod");
        std::fs::create_dir_all(&nested).unwrap();

        let recorded = Arc::new(Mutex::new(Vec::new()));
        let mut watcher = RecordingWatcher {
            watched: Arc::clone(&recorded),
        };
        watch_created_directories(&mut watcher, &create_folder(&nested), &NoiseFilter::new());

        let watched = recorded.lock().unwrap().clone();
        let _ = std::fs::remove_dir_all(&root);

        assert_eq!(watched, vec![(nested, RecursiveMode::NonRecursive)]);
    }

    #[test]
    fn newly_created_noise_directories_do_not_get_a_watch() {
        let root = scratch_dir();
        let target = root.join("target/debug");
        std::fs::create_dir_all(&target).unwrap();

        let recorded = Arc::new(Mutex::new(Vec::new()));
        let mut watcher = RecordingWatcher {
            watched: Arc::clone(&recorded),
        };
        watch_created_directories(&mut watcher, &create_folder(&target), &NoiseFilter::new());

        let watched = recorded.lock().unwrap().clone();
        let _ = std::fs::remove_dir_all(&root);

        assert!(
            watched.is_empty(),
            "creating target/ must not register a new watch"
        );
    }

    #[test]
    fn a_small_pruned_tree_can_be_watched() {
        let root = scratch_dir();
        std::fs::create_dir_all(root.join("src")).unwrap();
        std::fs::create_dir_all(root.join("target/debug")).unwrap();
        let (tx, _rx) = mpsc::channel(1);
        let watcher =
            bind_and_watch(&root, tx, &NoiseFilter::new()).expect("a small tree should bind");
        drop(watcher);
        let _ = std::fs::remove_dir_all(root);
    }

    fn create_folder(path: &Path) -> Event {
        Event {
            kind: EventKind::Create(notify::event::CreateKind::Folder),
            paths: vec![path.to_path_buf()],
            attrs: Default::default(),
        }
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
        used: Arc<AtomicBool>,
        recorded: Arc<Mutex<Vec<(PathBuf, RecursiveMode)>>>,
    ) -> impl FnOnce() -> Result<BoxedWatcher> {
        move || {
            used.store(true, Ordering::SeqCst);
            Ok(Box::new(RecordingWatcher { watched: recorded }) as BoxedWatcher)
        }
    }

    struct RecordingWatcher {
        watched: Arc<Mutex<Vec<(PathBuf, RecursiveMode)>>>,
    }

    impl Watcher for RecordingWatcher {
        fn new<F: notify::EventHandler>(_: F, _: Config) -> notify::Result<Self> {
            Ok(Self {
                watched: Arc::new(Mutex::new(Vec::new())),
            })
        }

        fn watch(&mut self, path: &Path, mode: RecursiveMode) -> notify::Result<()> {
            self.watched
                .lock()
                .unwrap()
                .push((path.to_path_buf(), mode));
            Ok(())
        }

        fn unwatch(&mut self, _: &Path) -> notify::Result<()> {
            Ok(())
        }

        fn kind() -> notify::WatcherKind {
            notify::WatcherKind::PollWatcher
        }
    }

    /// Native backend that reproduces `inotify_add_watch` returning ENOSPC:
    /// the watcher object exists, `watch()` is what fails.
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
