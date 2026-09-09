//! What not to remember.
//!
//! A recursive watcher on a working directory sees enormous amounts of churn
//! that says nothing about what the developer is doing: build output, virtual
//! environments, editor swap files, and — most embarrassingly — contextd's own
//! database and log.
//!
//! Filtering here rather than at query time is deliberate. Noise that reaches
//! the database costs an embedding, a row, and a slot in every future briefing.

use std::collections::HashSet;
use std::path::{Path, PathBuf};

/// Directories whose contents are generated, vendored, or otherwise not the
/// developer's work. Matched against any component of the path.
const IGNORED_DIRECTORIES: &[&str] = &[
    // version control and build output
    ".git",
    ".hg",
    ".svn",
    "target",
    "build",
    "dist",
    "out",
    // language ecosystems
    "node_modules",
    "vendor",
    "__pycache__",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".gradle",
    "Pods",
    "DerivedData",
    // framework and tool caches
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".turbo",
    ".parcel-cache",
    ".cache",
    ".terraform",
    "coverage",
    // editors
    ".idea",
    ".vscode",
];

/// Extensions that are always generated or transient.
const IGNORED_EXTENSIONS: &[&str] = &[
    "log", "tmp", "temp", "swp", "swo", "swx", "pyc", "pyo", "class", "o", "obj", "lock~", "bak",
    "orig", "rej",
];

/// Exact filenames worth skipping wherever they appear.
const IGNORED_FILENAMES: &[&str] = &[".DS_Store", "Thumbs.db", "desktop.ini"];

/// Decides whether a filesystem path is worth recording.
///
/// Holds contextd's own paths so the daemon does not observe itself. Without
/// this the database fills with modifications to the database.
#[derive(Debug, Clone, Default)]
pub struct NoiseFilter {
    own_paths: Vec<PathBuf>,
    ignored_directories: HashSet<String>,
}

impl NoiseFilter {
    /// A filter that knows nothing about contextd's own files. Useful in tests;
    /// prefer [`NoiseFilter::for_config`] in the daemon.
    pub fn new() -> Self {
        Self {
            own_paths: Vec::new(),
            ignored_directories: IGNORED_DIRECTORIES
                .iter()
                .map(|dir| (*dir).to_string())
                .collect(),
        }
    }

    /// A filter that also excludes contextd's database, socket, and their
    /// containing directories.
    pub fn for_config(config: &contextd_core::config::AppConfig) -> Self {
        let mut filter = Self::new();

        for path in [&config.db_path, &config.socket_path] {
            filter.own_paths.push(path.clone());
            if let Some(parent) = path.parent() {
                filter.own_paths.push(parent.to_path_buf());
            }
        }

        filter
    }

    /// Also ignore anything under `path`.
    pub fn ignoring(mut self, path: impl Into<PathBuf>) -> Self {
        self.own_paths.push(path.into());
        self
    }

    /// True when this path is worth turning into an event.
    pub fn is_interesting(&self, path: &Path) -> bool {
        !self.is_noise(path)
    }

    /// True when this directory should receive a kernel watch or poll scan.
    ///
    /// File-event noise (`.tmp`, `.o`, `~`, editor scratch names) does not
    /// apply: a source tree named `scratch.tmp` is still a real tree. Only
    /// ignored directory components (`target/`, `node_modules/`, `.git/`) and
    /// paths contextd owns are excluded.
    pub fn is_interesting_directory(&self, path: &Path) -> bool {
        !self.is_ignored_directory(path)
    }

    fn is_noise(&self, path: &Path) -> bool {
        if self.is_ignored_directory(path) {
            return true;
        }

        let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
            return false;
        };

        is_noisy_filename(name)
    }

    fn is_ignored_directory(&self, path: &Path) -> bool {
        if self.is_our_own(path) {
            return true;
        }

        path.components()
            .filter_map(|component| component.as_os_str().to_str())
            .any(|component| self.ignored_directories.contains(component))
    }

    /// Anything at or beneath a path contextd owns.
    fn is_our_own(&self, path: &Path) -> bool {
        self.own_paths.iter().any(|own| path.starts_with(own))
    }
}

fn is_noisy_filename(name: &str) -> bool {
    if IGNORED_FILENAMES.contains(&name) {
        return true;
    }

    // Emacs writes `.#file` locks and `#file#` autosaves; both fire constantly.
    if (name.starts_with(".#")) || (name.starts_with('#') && name.ends_with('#')) {
        return true;
    }

    // Vim and many editors leave `file~` backups.
    if name.ends_with('~') {
        return true;
    }

    // SQLite sidecars. These change on every write to any database, including
    // ones the developer does care about, but the sidecar itself never carries
    // meaning.
    if name.ends_with("-journal") || name.ends_with("-wal") || name.ends_with("-shm") {
        return true;
    }

    match name.rsplit_once('.') {
        Some((_, extension)) => IGNORED_EXTENSIONS.contains(&extension),
        None => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use contextd_core::config::AppConfig;

    #[test]
    fn build_output_and_vcs_are_ignored() {
        let filter = NoiseFilter::new();
        for path in [
            "/repo/target/debug/contextd",
            "/repo/.git/index",
            "/repo/node_modules/react/index.js",
            "/repo/.venv/lib/python3.12/site-packages/x.py",
            "/repo/__pycache__/mod.cpython-312.pyc",
            "/repo/.next/server/app.js",
        ] {
            assert!(
                !filter.is_interesting(Path::new(path)),
                "{path} should be filtered"
            );
        }
    }

    #[test]
    fn real_source_files_survive() {
        let filter = NoiseFilter::new();
        for path in [
            "/repo/Cargo.toml",
            "/repo/src/main.rs",
            "/repo/README.md",
            "/repo/app/components/Login.tsx",
            "/repo/Cargo.lock",
        ] {
            assert!(
                filter.is_interesting(Path::new(path)),
                "{path} should be kept"
            );
        }
    }

    #[test]
    fn editor_scratch_files_are_ignored() {
        let filter = NoiseFilter::new();
        for path in [
            "/repo/src/.#main.rs",
            "/repo/src/#main.rs#",
            "/repo/src/main.rs~",
            "/repo/src/.main.rs.swp",
            "/repo/.DS_Store",
        ] {
            assert!(
                !filter.is_interesting(Path::new(path)),
                "{path} should be filtered"
            );
        }
    }

    #[test]
    fn sqlite_sidecars_are_ignored_but_the_database_itself_is_not() {
        let filter = NoiseFilter::new();
        assert!(!filter.is_interesting(Path::new("/repo/app.db-journal")));
        assert!(!filter.is_interesting(Path::new("/repo/app.db-wal")));
        assert!(!filter.is_interesting(Path::new("/repo/app.db-shm")));
        assert!(
            filter.is_interesting(Path::new("/repo/app.db")),
            "a developer's own database is real work"
        );
    }

    #[test]
    fn contextd_never_observes_itself() {
        // The bug this exists to prevent: running the daemon in a directory
        // where it also writes its log produced briefings made entirely of
        // "daemon.log modified".
        let config = AppConfig {
            db_path: PathBuf::from("/tmp/contextd/events.db"),
            socket_path: PathBuf::from("/tmp/contextd/contextd.sock"),
            ..Default::default()
        };
        let filter = NoiseFilter::for_config(&config);

        assert!(!filter.is_interesting(Path::new("/tmp/contextd/events.db")));
        assert!(!filter.is_interesting(Path::new("/tmp/contextd/events.db-journal")));
        assert!(!filter.is_interesting(Path::new("/tmp/contextd/contextd.sock")));
        assert!(
            !filter.is_interesting(Path::new("/tmp/contextd/anything-else")),
            "the whole data directory is ours"
        );
        assert!(filter.is_interesting(Path::new("/home/dev/project/src/main.rs")));
    }

    #[test]
    fn log_files_are_ignored_wherever_they_live() {
        let filter = NoiseFilter::new();
        assert!(!filter.is_interesting(Path::new("/repo/daemon.log")));
        assert!(!filter.is_interesting(Path::new("/repo/logs/app.log")));
    }

    #[test]
    fn extra_ignored_paths_can_be_added() {
        let filter = NoiseFilter::new().ignoring("/repo/generated");
        assert!(!filter.is_interesting(Path::new("/repo/generated/schema.rs")));
        assert!(filter.is_interesting(Path::new("/repo/src/schema.rs")));
    }

    #[test]
    fn a_path_with_no_filename_does_not_panic() {
        assert!(NoiseFilter::new().is_interesting(Path::new("/")));
    }

    #[test]
    fn directories_named_like_noisy_files_are_still_watchable() {
        let filter = NoiseFilter::new();
        for path in [
            "/repo/scratch.tmp",
            "/repo/cache.o",
            "/repo/backup~",
            "/repo/notes.bak",
        ] {
            assert!(
                filter.is_interesting_directory(Path::new(path)),
                "{path} is a directory name, not a file to skip"
            );
        }
        assert!(
            !filter.is_interesting(Path::new("/repo/scratch.tmp")),
            "a file with a noisy name is still not an event"
        );
    }

    #[test]
    fn watchable_directories_still_exclude_build_trees_and_owned_paths() {
        let filter = NoiseFilter::new().ignoring("/repo/generated");
        assert!(!filter.is_interesting_directory(Path::new("/repo/target")));
        assert!(!filter.is_interesting_directory(Path::new("/repo/node_modules/pkg")));
        assert!(!filter.is_interesting_directory(Path::new("/repo/.git")));
        assert!(!filter.is_interesting_directory(Path::new("/repo/generated")));
        assert!(filter.is_interesting_directory(Path::new("/repo/src")));
    }
}
