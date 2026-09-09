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
use std::io;
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

/// The git directory for a `.git` path (directory or gitfile).
///
/// Matches Git's `resolve_gitdir`: a directory is used as-is; a gitfile's
/// first line must be `gitdir: <path>`. That path is absolute as written, or
/// resolved against the directory that contains the gitfile — never against
/// process CWD. A later `gitdir:` line, or `gitdir:` without the space, is
/// not a gitfile.
fn resolve_git_dir(git: &Path) -> io::Result<PathBuf> {
    if git.is_dir() {
        return Ok(git.to_path_buf());
    }
    if git.is_file() {
        let text = std::fs::read_to_string(git)?;
        let pointed = text
            .lines()
            .next()
            .and_then(|line| line.strip_prefix("gitdir: "))
            .map(str::trim)
            .filter(|line| !line.is_empty())
            .ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::InvalidData,
                    format!("{} has no gitdir: line", git.display()),
                )
            })?;
        let pointed = Path::new(pointed);
        let gitdir = if pointed.is_absolute() {
            pointed.to_path_buf()
        } else {
            git.parent()
                .ok_or_else(|| {
                    io::Error::new(
                        io::ErrorKind::NotFound,
                        format!("{} has no parent directory", git.display()),
                    )
                })?
                .join(pointed)
        };
        if !gitdir.exists() {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                format!(
                    "gitdir {} does not exist (from {})",
                    gitdir.display(),
                    git.display()
                ),
            ));
        }
        return Ok(gitdir);
    }
    Err(io::Error::new(
        io::ErrorKind::NotFound,
        format!("no .git directory or file at {}", git.display()),
    ))
}

/// Git's common dir: `$GIT_DIR` unless `$GIT_DIR/commondir` says otherwise.
/// Relative commondir values are relative to `$GIT_DIR`.
fn git_common_dir(git_dir: &Path) -> PathBuf {
    match std::fs::read_to_string(git_dir.join("commondir")) {
        Ok(contents) => git_dir.join(contents.trim()),
        Err(_) => git_dir.to_path_buf(),
    }
}

/// Local `core.hooksPath`, if set. Empty values are treated as unset.
///
/// Only `[core]` (no subsection) counts. Keys are matched case-insensitively.
/// Quoted values are unquoted; an unquoted `#` or `;` starts an inline comment.
fn core_hooks_path_from_config(text: &str) -> Option<String> {
    let mut in_core = false;
    let mut found: Option<String> = None;
    for raw in text.lines() {
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') || line.starts_with(';') {
            continue;
        }
        if let Some(inner) = section_header(line) {
            in_core = is_plain_core_section(inner);
            continue;
        }
        if !in_core {
            continue;
        }
        if let Some(value) = config_assignment(line, "hookspath") {
            found = if value.is_empty() { None } else { Some(value) };
        }
    }
    found
}

fn section_header(line: &str) -> Option<&str> {
    line.strip_prefix('[')
        .and_then(|rest| rest.strip_suffix(']'))
        .map(str::trim)
}

fn is_plain_core_section(inner: &str) -> bool {
    !inner.contains('"') && inner.eq_ignore_ascii_case("core")
}

fn config_assignment(line: &str, key: &str) -> Option<String> {
    let eq = line.find('=')?;
    let name = line[..eq].trim();
    if !name.eq_ignore_ascii_case(key) {
        return None;
    }
    Some(parse_config_value(line[eq + 1..].trim_start()))
}

fn parse_config_value(rest: &str) -> String {
    let rest = rest.trim_start();
    if let Some(inner) = rest.strip_prefix('"') {
        return inner
            .split_once('"')
            .map(|(value, _)| value.to_string())
            .unwrap_or_else(|| inner.trim().to_string());
    }
    let comment = rest
        .find('#')
        .into_iter()
        .chain(rest.find(';'))
        .min()
        .unwrap_or(rest.len());
    rest[..comment].trim().to_string()
}

/// `core.hooksPath` from `$GIT_COMMON_DIR/config`, overridden by
/// `$GIT_DIR/config.worktree` when that file sets the key.
fn read_core_hooks_path(common_dir: &Path, git_dir: &Path) -> Option<String> {
    let mut path = std::fs::read_to_string(common_dir.join("config"))
        .ok()
        .and_then(|text| core_hooks_path_from_config(&text));
    if let Ok(text) = std::fs::read_to_string(git_dir.join("config.worktree"))
        && let Some(overridden) = core_hooks_path_from_config(&text)
    {
        path = Some(overridden);
    }
    path
}

/// Where git actually keeps hooks for this working tree.
///
/// Honour `core.hooksPath` from local config when set: a relative value is
/// joined to the working tree (the directory where Git runs hooks), never to
/// `$GIT_DIR` or process CWD. Otherwise a normal repo has `.git/hooks`; a
/// linked worktree or submodule follows `gitdir:` / `commondir` into the
/// common or module hooks directory.
pub fn git_hooks_dir(repo_path: impl AsRef<Path>) -> io::Result<PathBuf> {
    let repo_path = repo_path.as_ref();
    let git_dir = resolve_git_dir(&repo_path.join(".git"))?;
    let common = git_common_dir(&git_dir);
    if let Some(configured) = read_core_hooks_path(&common, &git_dir) {
        let path = Path::new(&configured);
        if path.is_absolute() {
            Ok(path.to_path_buf())
        } else {
            Ok(repo_path.join(path))
        }
    } else {
        Ok(common.join("hooks"))
    }
}

/// Install every contextd hook into a repository.
///
/// Safe to run repeatedly, and safe to run on a repository with its own hooks.
/// Returns an error if the hooks directory cannot be resolved or is not a
/// directory: callers must not treat a skip as success.
#[cfg(unix)]
pub fn install_hooks(repo_path: impl AsRef<Path>, socket_path: &Path) -> io::Result<()> {
    let hooks_dir = git_hooks_dir(repo_path.as_ref())?;

    if !hooks_dir.is_dir() {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            format!(
                "git hooks directory does not exist at {} (looked up from {})",
                hooks_dir.display(),
                repo_path.as_ref().display()
            ),
        ));
    }

    for spec in HOOKS {
        install_one(&hooks_dir, spec, socket_path)?;
    }

    info!("Git hooks installed in {:?}", hooks_dir);
    Ok(())
}

#[cfg(unix)]
fn install_one(hooks_dir: &Path, spec: &HookSpec, socket_path: &Path) -> io::Result<()> {
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
pub fn install_hooks(repo_path: impl AsRef<Path>, _socket_path: &Path) -> io::Result<()> {
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

    fn temp_dir(name: &str) -> PathBuf {
        let id = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!("contextd-{name}-{id}"));
        std::fs::create_dir_all(&root).unwrap();
        root
    }

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
    fn installing_outside_a_repository_explains_why() {
        let bare = std::env::temp_dir().join(format!(
            "contextd-not-a-repo-{}",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&bare).unwrap();

        let err = install_hooks(&bare, &socket()).expect_err("a skip is not success");
        assert!(
            err.to_string().contains("no .git directory or file"),
            "unhelpful error: {err}"
        );
    }

    #[cfg(unix)]
    #[test]
    fn installing_when_the_hooks_directory_is_missing_explains_why() {
        let repo = temp_repo("missing-hooks");
        std::fs::remove_dir_all(repo.join(".git/hooks")).unwrap();

        let err = install_hooks(&repo, &socket()).expect_err("a skip is not success");
        assert!(
            err.to_string()
                .contains("git hooks directory does not exist"),
            "unhelpful error: {err}"
        );
        assert!(
            err.to_string().contains(&repo.display().to_string()),
            "error should name the repo that was looked up: {err}"
        );
    }

    #[cfg(unix)]
    #[test]
    fn a_worktree_gitfile_without_gitdir_explains_why() {
        let root = std::env::temp_dir().join(format!(
            "contextd-bad-gitfile-{}",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&root).unwrap();
        std::fs::write(root.join(".git"), "this is not a gitdir file\n").unwrap();

        let err = install_hooks(&root, &socket()).expect_err("a skip is not success");
        assert!(
            err.to_string().contains("has no gitdir: line"),
            "unhelpful error: {err}"
        );
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

    #[test]
    fn git_hooks_dir_for_a_plain_repo_is_dot_git_hooks() {
        let repo = temp_repo("plain-hooks");
        assert_eq!(git_hooks_dir(&repo).unwrap(), repo.join(".git/hooks"));
    }

    #[test]
    fn git_hooks_dir_errors_for_an_empty_gitfile() {
        let root = temp_dir("empty-gitfile");
        std::fs::write(root.join(".git"), "gitdir:   \n").unwrap();
        let err = git_hooks_dir(&root).expect_err("empty gitdir: is not a repo");
        assert!(
            err.to_string().contains("has no gitdir: line"),
            "unhelpful error: {err}"
        );
    }

    #[test]
    fn git_hooks_dir_rejects_gitdir_without_the_required_space() {
        // Git's read_gitfile_gently requires starts_with("gitdir: ").
        // `gitdir:/path` is not a gitfile; treating it as one would install
        // hooks wherever the concatenated path happens to point.
        let (main, worktree) = linked_worktree("wt-nospace");
        std::fs::write(
            worktree.join(".git"),
            format!("gitdir:{}/.git/worktrees/observe\n", main.display()),
        )
        .unwrap();

        let err = git_hooks_dir(&worktree).expect_err("gitdir:/path is not a gitfile");
        assert!(
            err.to_string().contains("has no gitdir: line"),
            "unhelpful error: {err}"
        );
    }

    #[test]
    fn git_hooks_dir_rejects_gitdir_on_a_later_line() {
        let (main, worktree) = linked_worktree("wt-later");
        std::fs::write(
            worktree.join(".git"),
            format!(
                "not a gitfile\ngitdir: {}/.git/worktrees/observe\n",
                main.display()
            ),
        )
        .unwrap();

        let err = git_hooks_dir(&worktree).expect_err("only the first line is a gitfile");
        assert!(
            err.to_string().contains("has no gitdir: line"),
            "unhelpful error: {err}"
        );
    }

    fn linked_worktree(name: &str) -> (PathBuf, PathBuf) {
        let id = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let main = std::env::temp_dir().join(format!("contextd-{name}-main-{id}"));
        let worktree = std::env::temp_dir().join(format!("contextd-{name}-linked-{id}"));
        std::fs::create_dir_all(main.join(".git/hooks")).unwrap();
        std::fs::create_dir_all(main.join(".git/worktrees/observe")).unwrap();
        std::fs::write(main.join(".git/worktrees/observe/commondir"), "../..\n").unwrap();
        std::fs::create_dir_all(&worktree).unwrap();
        (main, worktree)
    }

    #[test]
    fn git_hooks_dir_follows_a_worktree_git_file() {
        let (main, worktree) = linked_worktree("wt-abs");
        std::fs::write(
            worktree.join(".git"),
            format!("gitdir: {}/.git/worktrees/observe\n", main.display()),
        )
        .unwrap();

        let hooks = git_hooks_dir(&worktree)
            .expect("worktree should resolve hooks")
            .canonicalize()
            .unwrap();
        assert_eq!(hooks, main.join(".git/hooks").canonicalize().unwrap());
        assert_eq!(git_hooks_dir(&main).unwrap(), main.join(".git/hooks"));
    }

    #[test]
    fn git_hooks_dir_resolves_a_relative_gitdir_against_the_worktree() {
        // Git writes `gitdir: ../main/.git/worktrees/<name>` for relative
        // worktrees. Relative `gitdir:` is resolved against the gitfile's
        // parent, not process CWD.
        let (main, worktree) = linked_worktree("wt-rel");
        let relative = format!(
            "../{}/.git/worktrees/observe",
            main.file_name().unwrap().to_string_lossy()
        );
        std::fs::write(worktree.join(".git"), format!("gitdir: {relative}\n")).unwrap();

        let hooks = git_hooks_dir(&worktree)
            .expect("relative gitdir should resolve")
            .canonicalize()
            .unwrap();
        assert_eq!(hooks, main.join(".git/hooks").canonicalize().unwrap());
    }

    #[test]
    fn git_hooks_dir_errors_when_a_relative_gitdir_cannot_be_found() {
        let root = std::env::temp_dir().join(format!(
            "contextd-missing-gitdir-{}",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&root).unwrap();
        std::fs::write(
            root.join(".git"),
            "gitdir: ../does-not-exist/.git/worktrees/x\n",
        )
        .unwrap();

        let err = git_hooks_dir(&root).expect_err("a miss is not success");
        assert!(
            err.to_string().contains("does not exist"),
            "unhelpful error: {err}"
        );
        assert!(
            err.to_string().contains("does-not-exist"),
            "error should name the gitdir that was missing: {err}"
        );
    }

    #[cfg(unix)]
    #[test]
    fn install_hooks_follows_a_relative_gitdir_into_the_common_hooks_dir() {
        let (main, worktree) = linked_worktree("wt-install");
        let relative = format!(
            "../{}/.git/worktrees/observe",
            main.file_name().unwrap().to_string_lossy()
        );
        std::fs::write(worktree.join(".git"), format!("gitdir: {relative}\n")).unwrap();

        install_hooks(&worktree, &socket()).expect("relative gitdir should install");

        assert!(
            main.join(".git/hooks/post-commit").exists(),
            "hooks belong in the common directory, not the worktree"
        );
        assert!(
            !worktree.join("hooks/post-commit").exists(),
            "must not invent a hooks directory relative to cwd"
        );
    }

    #[test]
    fn git_hooks_dir_resolves_relative_gitdir_against_the_git_file() {
        // Submodules always write `gitdir: ../.git/modules/<name>` (relative to
        // the directory that contains the .git *file*, not to process CWD).
        let super_repo = temp_dir("sub-super");
        let child = super_repo.join("child");
        let module_git = super_repo.join(".git/modules/child");
        std::fs::create_dir_all(module_git.join("hooks")).unwrap();
        std::fs::create_dir_all(&child).unwrap();
        std::fs::write(child.join(".git"), "gitdir: ../.git/modules/child\n").unwrap();

        let hooks = git_hooks_dir(&child)
            .expect("relative gitdir: must resolve")
            .canonicalize()
            .unwrap();
        assert_eq!(hooks, module_git.join("hooks").canonicalize().unwrap());
    }

    #[cfg(unix)]
    #[test]
    fn install_hooks_follows_a_relative_gitdir_pointer() {
        let super_repo = temp_dir("install-sub");
        let child = super_repo.join("child");
        let module_hooks = super_repo.join(".git/modules/child/hooks");
        std::fs::create_dir_all(&module_hooks).unwrap();
        std::fs::create_dir_all(&child).unwrap();
        std::fs::write(child.join(".git"), "gitdir: ../.git/modules/child\n").unwrap();

        install_hooks(&child, &socket()).expect("install should succeed");

        let hook = module_hooks.join("post-commit");
        assert!(
            hook.exists(),
            "hooks must land in the module git dir, not be silently skipped"
        );
        assert!(
            std::fs::read_to_string(&hook)
                .unwrap()
                .contains(CONTEXTD_HOOK_MARKER)
        );
        assert!(!child.join(".git/hooks/post-commit").exists());
    }

    fn write_core_hooks_path(repo: &Path, value: &str) {
        std::fs::write(
            repo.join(".git/config"),
            format!("[core]\n\thooksPath = {value}\n"),
        )
        .unwrap();
    }

    #[test]
    fn git_hooks_dir_follows_relative_core_hooks_path() {
        let repo = temp_repo("husky-hooks");
        std::fs::create_dir_all(repo.join(".husky/_")).unwrap();
        write_core_hooks_path(&repo, ".husky/_");

        assert_eq!(git_hooks_dir(&repo).unwrap(), repo.join(".husky/_"));
    }

    #[test]
    fn git_hooks_dir_unquotes_core_hooks_path() {
        let repo = temp_repo("quoted-hooks");
        std::fs::create_dir_all(repo.join(".lefthook")).unwrap();
        write_core_hooks_path(&repo, "\".lefthook\"");

        assert_eq!(git_hooks_dir(&repo).unwrap(), repo.join(".lefthook"));
    }

    #[test]
    fn git_hooks_dir_uses_absolute_core_hooks_path() {
        let repo = temp_repo("abs-hooks");
        let hooks = temp_dir("abs-hooks-dir");
        write_core_hooks_path(&repo, &hooks.display().to_string());

        assert_eq!(git_hooks_dir(&repo).unwrap(), hooks);
    }

    #[test]
    fn git_hooks_dir_ignores_core_subsection_hooks_path() {
        let repo = temp_repo("core-sub");
        std::fs::create_dir_all(repo.join(".wrong")).unwrap();
        std::fs::write(
            repo.join(".git/config"),
            "[core \"foo\"]\n\thooksPath = .wrong\n[user]\n\tname = x\n",
        )
        .unwrap();

        assert_eq!(git_hooks_dir(&repo).unwrap(), repo.join(".git/hooks"));
    }

    #[test]
    fn git_hooks_dir_last_hooks_path_wins() {
        let repo = temp_repo("dup-hooks");
        std::fs::create_dir_all(repo.join(".first")).unwrap();
        std::fs::create_dir_all(repo.join(".second")).unwrap();
        std::fs::write(
            repo.join(".git/config"),
            "[core]\n\thooksPath = .first\n\thooksPath = .second\n",
        )
        .unwrap();

        assert_eq!(git_hooks_dir(&repo).unwrap(), repo.join(".second"));
    }

    #[test]
    fn git_hooks_dir_empty_hooks_path_falls_back_to_dot_git_hooks() {
        let repo = temp_repo("empty-hooks-path");
        write_core_hooks_path(&repo, "");

        assert_eq!(git_hooks_dir(&repo).unwrap(), repo.join(".git/hooks"));
    }

    #[test]
    fn git_hooks_dir_hooks_path_key_is_case_insensitive() {
        let repo = temp_repo("case-hooks");
        std::fs::create_dir_all(repo.join(".husky/_")).unwrap();
        std::fs::write(repo.join(".git/config"), "[core]\n\thookspath = .husky/_\n").unwrap();

        assert_eq!(git_hooks_dir(&repo).unwrap(), repo.join(".husky/_"));
    }

    #[test]
    fn git_hooks_dir_strips_inline_comment_from_hooks_path() {
        let repo = temp_repo("comment-hooks");
        std::fs::create_dir_all(repo.join(".husky/_")).unwrap();
        std::fs::write(
            repo.join(".git/config"),
            "[core]\n\thooksPath = .husky/_ # managed by husky\n",
        )
        .unwrap();

        assert_eq!(git_hooks_dir(&repo).unwrap(), repo.join(".husky/_"));
    }

    #[test]
    fn git_hooks_dir_resolves_relative_hooks_path_against_this_worktree() {
        let (main, worktree) = linked_worktree("wt-hooks-path");
        std::fs::write(
            worktree.join(".git"),
            format!("gitdir: {}/.git/worktrees/observe\n", main.display()),
        )
        .unwrap();
        std::fs::write(main.join(".git/config"), "[core]\n\thooksPath = .husky/_\n").unwrap();
        std::fs::create_dir_all(worktree.join(".husky/_")).unwrap();
        std::fs::create_dir_all(main.join(".husky/_")).unwrap();

        assert_eq!(git_hooks_dir(&worktree).unwrap(), worktree.join(".husky/_"));
        assert_eq!(git_hooks_dir(&main).unwrap(), main.join(".husky/_"));
    }

    #[test]
    fn git_hooks_dir_prefers_worktree_config_hooks_path() {
        let (main, worktree) = linked_worktree("wt-config");
        std::fs::write(
            worktree.join(".git"),
            format!("gitdir: {}/.git/worktrees/observe\n", main.display()),
        )
        .unwrap();
        std::fs::write(main.join(".git/config"), "[core]\n\thooksPath = .husky/_\n").unwrap();
        std::fs::write(
            main.join(".git/worktrees/observe/config.worktree"),
            "[core]\n\thooksPath = .lefthook\n",
        )
        .unwrap();
        std::fs::create_dir_all(worktree.join(".lefthook")).unwrap();
        std::fs::create_dir_all(worktree.join(".husky/_")).unwrap();

        assert_eq!(
            git_hooks_dir(&worktree).unwrap(),
            worktree.join(".lefthook")
        );
    }

    #[cfg(unix)]
    #[test]
    fn install_hooks_writes_into_core_hooks_path_and_preserves_the_existing_hook() {
        let repo = temp_repo("install-husky");
        let hooks_dir = repo.join(".husky/_");
        std::fs::create_dir_all(&hooks_dir).unwrap();
        std::fs::write(hooks_dir.join("post-commit"), "#!/bin/sh\necho husky\n").unwrap();
        write_core_hooks_path(&repo, ".husky/_");

        install_hooks(&repo, &socket()).expect("hook install should succeed");

        let hook = std::fs::read_to_string(hooks_dir.join("post-commit")).unwrap();
        assert!(hook.contains(CONTEXTD_HOOK_MARKER));
        assert!(
            hook.contains("post-commit.contextd-backup"),
            "the preserved hook must still be called"
        );
        assert_eq!(
            std::fs::read_to_string(hooks_dir.join("post-commit.contextd-backup")).unwrap(),
            "#!/bin/sh\necho husky\n"
        );
        assert!(
            !repo.join(".git/hooks/post-commit").exists(),
            "Git never runs .git/hooks when core.hooksPath is set"
        );
    }

    #[cfg(unix)]
    #[test]
    fn install_hooks_errors_when_core_hooks_path_is_missing_and_does_not_fall_back() {
        let repo = temp_repo("missing-hooks-path");
        write_core_hooks_path(&repo, ".husky/_");

        let err = install_hooks(&repo, &socket()).expect_err("a skip is not success");
        assert!(
            err.to_string()
                .contains("git hooks directory does not exist"),
            "unhelpful error: {err}"
        );
        assert!(
            err.to_string().contains(".husky/_"),
            "error should name the configured hooks path: {err}"
        );
        assert!(
            !repo.join(".git/hooks/post-commit").exists(),
            "must not fall back to .git/hooks"
        );
    }

    #[cfg(unix)]
    #[test]
    fn install_hooks_errors_when_core_hooks_path_is_not_a_directory() {
        let repo = temp_repo("hooks-path-file");
        std::fs::write(repo.join(".not-a-dir"), "").unwrap();
        write_core_hooks_path(&repo, ".not-a-dir");

        let err = install_hooks(&repo, &socket()).expect_err("a file is not a hooks directory");
        assert!(
            err.to_string()
                .contains("git hooks directory does not exist"),
            "unhelpful error: {err}"
        );
        assert!(!repo.join(".git/hooks/post-commit").exists());
    }
}
