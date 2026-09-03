//! Teaching a repository to tell contextd what happened.
//!
//! Git already knows the three things that most clearly mark a change of
//! context — you committed, you switched branch, you pushed — and it will tell
//! anyone who asks via hooks. That is far better information than watching
//! `.git/` change on disk and trying to infer what it meant.
//!
//! Installing hooks means writing into someone's repository, so the rules are
//! strict: never destroy an existing hook (move it aside and keep calling it),
//! never fail a git operation because contextd is not running, and always be
//! safe to run twice.

#[cfg(unix)]
use std::fs;
#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
#[cfg(unix)]
use tracing::info;
use tracing::warn;

const CONTEXTD_HOOK_MARKER: &str = "# contextd-managed hook";
/// The marker used before this file handled more than one hook.
const LEGACY_CONTEXTD_HOOK_MARKER: &str = "# contextd git post-commit hook";
/// The marker used when post-commit was the only managed hook.
const LEGACY_MANAGED_MARKER: &str = "# contextd-managed post-commit hook";

/// The hooks contextd installs, and the payload each one sends.
///
/// Held as data rather than three near-identical scripts so the delivery logic
/// — socket discovery, chaining to a preserved hook, never failing git — exists
/// once and cannot drift between them.
#[cfg(unix)]
struct HookSpec {
    name: &'static str,
    /// Shell that sets `PAYLOAD`, or leaves it empty to send nothing.
    body: &'static str,
}

#[cfg(unix)]
const HOOKS: &[HookSpec] = &[
    HookSpec {
        name: "post-commit",
        body: r#"
HASH=$(git rev-parse HEAD 2>/dev/null || echo unknown)
MESSAGE=$(git log -1 --pretty=%B 2>/dev/null | head -n 1 | tr -d '\r\n')
PAYLOAD=$(printf '{"timestamp_ms":%s,"source":"git","payload":{"action":"commit","hash":"%s","message":"%s"}}' \
  "$TIMESTAMP" "$HASH" "$(json_escape "$MESSAGE")")
"#,
    },
    HookSpec {
        name: "post-checkout",
        body: r#"
# $3 is 1 for a branch checkout and 0 for a file checkout. Only the former is
# a change of context; `git checkout -- file` is just an undo.
if [ "$3" != "1" ]; then
  exit 0
fi

BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)
PAYLOAD=$(printf '{"timestamp_ms":%s,"source":"git","payload":{"action":"checkout","message":"%s","from":"%s","to":"%s"}}' \
  "$TIMESTAMP" "$(json_escape "$BRANCH")" "$1" "$2")
"#,
    },
    HookSpec {
        name: "pre-push",
        body: r#"
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)
PAYLOAD=$(printf '{"timestamp_ms":%s,"source":"git","payload":{"action":"push","remote":"%s","message":"%s"}}' \
  "$TIMESTAMP" "$(json_escape "${1:-origin}")" "$(json_escape "$BRANCH")")
"#,
    },
];

/// Build the full script for one hook.
#[cfg(unix)]
fn render_hook(spec: &HookSpec, socket_path: &Path) -> String {
    let backup = backup_name(spec.name);
    let socket = crate::emit::socket_default(socket_path);
    let emit = crate::emit::emit_function();
    let emit_fn = crate::emit::EMIT_FN;

    format!(
        r#"#!/bin/sh
{CONTEXTD_HOOK_MARKER}
#
# Sends one event to the contextd daemon. Written by `contextd install`.
# Every failure path here exits 0: contextd being down, or absent entirely,
# must never be the reason a commit or a push fails.

set +e

# Whatever hook was here before we arrived still runs, first.
PRIOR_HOOK="$(dirname "$0")/{backup}"
if [ -x "$PRIOR_HOOK" ]; then
  "$PRIOR_HOOK" "$@" || exit $?
elif [ -f "$PRIOR_HOOK" ]; then
  sh "$PRIOR_HOOK" "$@" || exit $?
fi

json_escape() {{
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}}

TIMESTAMP=$(date +%s000)
PAYLOAD=
{body}
[ -n "$PAYLOAD" ] || exit 0

{socket}
{emit}

{emit_fn} "$PAYLOAD"

exit 0
"#,
        body = spec.body,
    )
}

#[cfg(unix)]
fn backup_name(hook: &str) -> String {
    format!("{hook}.contextd-backup")
}

/// Finds the nearest Git repository root by walking up from `start`.
pub fn find_git_root(start: impl AsRef<Path>) -> Option<PathBuf> {
    start
        .as_ref()
        .ancestors()
        .find(|path| path.join(".git").exists())
        .map(Path::to_path_buf)
}

/// Install every contextd hook into a repository.
///
/// Safe to run repeatedly, and safe to run on a repository with its own hooks.
#[cfg(unix)]
pub fn install_hooks(repo_path: impl AsRef<Path>, socket_path: &Path) -> std::io::Result<()> {
    let hooks_dir = repo_path.as_ref().join(".git/hooks");

    if !hooks_dir.exists() {
        warn!(
            "Not a git repository (or no hooks dir): {:?}",
            repo_path.as_ref()
        );
        return Ok(()); // Fail gracefully if they run the daemon outside a repo
    }

    for spec in HOOKS {
        install_one(&hooks_dir, spec, socket_path)?;
    }

    info!("Git hooks installed in {:?}", hooks_dir);
    Ok(())
}

#[cfg(unix)]
fn install_one(hooks_dir: &Path, spec: &HookSpec, socket_path: &Path) -> std::io::Result<()> {
    let hook_path = hooks_dir.join(spec.name);
    let backup_path = hooks_dir.join(backup_name(spec.name));

    if hook_path.exists() {
        let existing = fs::read_to_string(&hook_path).unwrap_or_default();
        if !is_contextd_hook(&existing) {
            if backup_path.exists() {
                // Two different foreign hooks would mean silently discarding
                // one of them. Leave the repository alone instead.
                warn!(
                    "Existing non-contextd {} hook found and a backup already exists at {:?}; leaving it unchanged",
                    spec.name, backup_path
                );
                return Ok(());
            }

            fs::rename(&hook_path, &backup_path)?;
            info!(
                "Preserved existing Git {} hook at {:?}",
                spec.name, backup_path
            );
        }
    }

    fs::write(&hook_path, render_hook(spec, socket_path))?;

    let mut perms = fs::metadata(&hook_path)?.permissions();
    perms.set_mode(0o755);
    fs::set_permissions(&hook_path, perms)?;

    Ok(())
}

#[cfg(unix)]
fn is_contextd_hook(contents: &str) -> bool {
    contents.contains(CONTEXTD_HOOK_MARKER)
        || contents.contains(LEGACY_MANAGED_MARKER)
        || contents.contains(LEGACY_CONTEXTD_HOOK_MARKER)
}

/// Non-Unix platforms do not support the Unix-domain socket hook path yet.
#[cfg(not(unix))]
pub fn install_hooks(repo_path: impl AsRef<Path>, _socket_path: &Path) -> std::io::Result<()> {
    warn!(
        "Git hook installation is not supported on this platform: {:?}",
        repo_path.as_ref()
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_repo(name: &str) -> PathBuf {
        let id = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!("contextd-{name}-{id}"));
        std::fs::create_dir_all(root.join(".git/hooks")).unwrap();
        root
    }

    #[cfg(unix)]
    fn socket() -> PathBuf {
        PathBuf::from("/run/user/1000/contextd/contextd.sock")
    }

    #[cfg(unix)]
    fn mode(path: &Path) -> u32 {
        std::fs::metadata(path).unwrap().permissions().mode() & 0o777
    }

    #[cfg(unix)]
    fn read_hook(repo: &Path, name: &str) -> String {
        std::fs::read_to_string(repo.join(".git/hooks").join(name)).unwrap()
    }

    #[cfg(unix)]
    #[test]
    fn all_three_context_changing_hooks_are_installed() {
        let repo = temp_repo("all-hooks");

        install_hooks(&repo, &socket()).expect("hook install should succeed");

        for name in ["post-commit", "post-checkout", "pre-push"] {
            let hook = repo.join(".git/hooks").join(name);
            assert!(hook.exists(), "{name} should be installed");
            assert!(read_hook(&repo, name).contains(CONTEXTD_HOOK_MARKER));
            assert_eq!(mode(&hook), 0o755, "{name} must be executable");
        }
    }

    #[cfg(unix)]
    #[test]
    fn hooks_are_told_where_the_socket_is() {
        let repo = temp_repo("socket-path");
        install_hooks(&repo, &socket()).unwrap();

        let hook = read_hook(&repo, "post-commit");
        assert!(hook.contains("/run/user/1000/contextd/contextd.sock"));
        assert!(
            hook.contains("CONTEXTD_SOCKET"),
            "the environment must still be able to override it"
        );
    }

    #[cfg(unix)]
    #[test]
    fn a_hook_never_fails_a_git_operation() {
        // If a hook can fail the commit, contextd has made the user's life
        // worse than not installing it. This is the most important property.
        let repo = temp_repo("never-fails");
        install_hooks(&repo, &socket()).unwrap();

        for name in ["post-commit", "post-checkout", "pre-push"] {
            let hook = read_hook(&repo, name);
            assert!(hook.contains("exit 0"), "{name} must end successfully");
            assert!(
                hook.contains(r#"[ -S "$CONTEXTD_SOCKET" ] || return 0"#),
                "{name} must give up quietly when the daemon is not running"
            );
        }
    }

    #[cfg(unix)]
    #[test]
    fn a_hook_never_makes_git_wait() {
        // The delivery deadlock this guards against is easy to reintroduce and
        // invisible until someone's commit hangs: `nc -U` does not half-close
        // the socket when stdin ends, so it waits for the daemon while the
        // daemon waits for another line. Run the real hook with no daemon
        // listening and with a socket that accepts but never answers.
        let repo = temp_repo("never-waits");
        install_hooks(&repo, Path::new("/nonexistent/contextd.sock")).unwrap();

        let started = std::time::Instant::now();
        let status = std::process::Command::new("sh")
            .arg(repo.join(".git/hooks/post-commit"))
            .current_dir(&repo)
            .status()
            .expect("the hook should run");

        assert!(status.success(), "the hook must not fail the commit");
        assert!(
            started.elapsed() < std::time::Duration::from_secs(3),
            "the hook took {:?}; git would have waited that long",
            started.elapsed()
        );
    }

    #[cfg(unix)]
    #[test]
    fn a_hook_does_not_wait_on_a_daemon_that_never_answers() {
        use std::os::unix::net::UnixListener;

        let repo = temp_repo("silent-daemon");
        let socket_path = repo.join("silent.sock");
        // Bound but never accepted: the worst case for a client that expects a
        // reply, and the exact shape of a wedged daemon.
        let _listener = UnixListener::bind(&socket_path).expect("should bind");

        install_hooks(&repo, &socket_path).unwrap();

        let started = std::time::Instant::now();
        let status = std::process::Command::new("sh")
            .arg(repo.join(".git/hooks/post-commit"))
            .current_dir(&repo)
            .status()
            .expect("the hook should run");

        assert!(status.success());
        assert!(
            started.elapsed() < std::time::Duration::from_secs(3),
            "a silent daemon held the hook for {:?}",
            started.elapsed()
        );
    }

    #[cfg(unix)]
    #[test]
    fn every_hook_is_valid_shell() {
        let repo = temp_repo("parses");
        install_hooks(&repo, &socket()).unwrap();

        for name in ["post-commit", "post-checkout", "pre-push"] {
            let status = std::process::Command::new("sh")
                .arg("-n")
                .arg(repo.join(".git/hooks").join(name))
                .status()
                .expect("sh should run");
            assert!(status.success(), "{name} is not valid shell");
        }
    }

    #[cfg(unix)]
    #[test]
    fn installing_twice_changes_nothing() {
        let repo = temp_repo("idempotent");

        install_hooks(&repo, &socket()).expect("first install should succeed");
        let first = read_hook(&repo, "post-commit");
        install_hooks(&repo, &socket()).expect("second install should succeed");

        assert_eq!(read_hook(&repo, "post-commit"), first);
        assert!(!repo.join(".git/hooks/post-commit.contextd-backup").exists());
    }

    #[cfg(unix)]
    #[test]
    fn an_existing_hook_is_preserved_and_still_runs() {
        let repo = temp_repo("preserve-custom");
        let hooks_dir = repo.join(".git/hooks");
        std::fs::write(hooks_dir.join("pre-push"), "#!/bin/sh\necho custom\n").unwrap();

        install_hooks(&repo, &socket()).expect("hook install should succeed");

        let hook = read_hook(&repo, "pre-push");
        let backup = std::fs::read_to_string(hooks_dir.join("pre-push.contextd-backup")).unwrap();

        assert!(hook.contains(CONTEXTD_HOOK_MARKER));
        assert!(
            hook.contains("pre-push.contextd-backup"),
            "the preserved hook must still be called"
        );
        assert_eq!(backup, "#!/bin/sh\necho custom\n");
    }

    #[cfg(unix)]
    #[test]
    fn a_preserved_hook_that_fails_still_blocks_the_push() {
        // pre-push is a veto hook. If someone's own pre-push rejects a push,
        // wrapping it must not silently turn that into an approval.
        let repo = temp_repo("veto");
        std::fs::write(repo.join(".git/hooks/pre-push"), "#!/bin/sh\nexit 1\n").unwrap();

        install_hooks(&repo, &socket()).unwrap();

        assert!(read_hook(&repo, "pre-push").contains(r#"|| exit $?"#));
    }

    #[cfg(unix)]
    #[test]
    fn a_second_foreign_hook_is_left_alone_rather_than_discarded() {
        let repo = temp_repo("two-foreign");
        let hooks_dir = repo.join(".git/hooks");
        std::fs::write(hooks_dir.join("post-commit"), "#!/bin/sh\necho first\n").unwrap();
        install_hooks(&repo, &socket()).unwrap();

        // Someone replaces the managed hook with their own.
        std::fs::write(hooks_dir.join("post-commit"), "#!/bin/sh\necho second\n").unwrap();
        install_hooks(&repo, &socket()).unwrap();

        assert_eq!(
            std::fs::read_to_string(hooks_dir.join("post-commit")).unwrap(),
            "#!/bin/sh\necho second\n",
            "we must not overwrite a hook whose predecessor we already saved"
        );
        assert_eq!(
            std::fs::read_to_string(hooks_dir.join("post-commit.contextd-backup")).unwrap(),
            "#!/bin/sh\necho first\n"
        );
    }

    #[cfg(unix)]
    #[test]
    fn a_hook_from_an_older_contextd_is_upgraded_in_place() {
        let repo = temp_repo("legacy");
        let hooks_dir = repo.join(".git/hooks");
        std::fs::write(
            hooks_dir.join("post-commit"),
            "#!/bin/bash\n# contextd git post-commit hook\n",
        )
        .unwrap();

        install_hooks(&repo, &socket()).expect("hook install should succeed");

        assert!(read_hook(&repo, "post-commit").contains(CONTEXTD_HOOK_MARKER));
        assert!(
            !hooks_dir.join("post-commit.contextd-backup").exists(),
            "our own old hook is not worth preserving"
        );
    }

    #[cfg(unix)]
    #[test]
    fn a_file_checkout_is_not_treated_as_a_change_of_context() {
        // `git checkout -- file` is an undo, not a context switch.
        let repo = temp_repo("file-checkout");
        install_hooks(&repo, &socket()).unwrap();

        assert!(read_hook(&repo, "post-checkout").contains(r#"if [ "$3" != "1" ]"#));
    }

    #[cfg(unix)]
    #[test]
    fn installing_outside_a_repository_is_not_an_error() {
        let bare = std::env::temp_dir().join(format!(
            "contextd-not-a-repo-{}",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&bare).unwrap();

        assert!(install_hooks(&bare, &socket()).is_ok());
    }

    #[test]
    fn find_git_root_finds_root_from_repo_and_subdirectory() {
        let repo = temp_repo("find-root");
        let nested = repo.join("crates/sources/src");
        std::fs::create_dir_all(&nested).unwrap();

        assert_eq!(find_git_root(&repo), Some(repo.clone()));
        assert_eq!(find_git_root(&nested), Some(repo));
    }

    #[test]
    fn find_git_root_returns_none_outside_repo() {
        let root = std::env::temp_dir().join(format!(
            "contextd-no-git-{}",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&root).unwrap();

        assert_eq!(find_git_root(&root), None);
    }
}
