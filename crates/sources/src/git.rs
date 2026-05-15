#[cfg(unix)]
use std::fs;
#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use tracing::{info, warn};

const CONTEXTD_HOOK_MARKER: &str = "# contextd-managed post-commit hook";
const LEGACY_CONTEXTD_HOOK_MARKER: &str = "# contextd git post-commit hook";
const BACKUP_HOOK_FILE_NAME: &str = "post-commit.contextd-backup";

#[cfg(unix)]
const POST_COMMIT_HOOK: &str = r#"#!/bin/sh
# contextd-managed post-commit hook

set +e

BACKUP_HOOK="$(dirname "$0")/post-commit.contextd-backup"
if [ -x "$BACKUP_HOOK" ]; then
  "$BACKUP_HOOK" "$@"
elif [ -f "$BACKUP_HOOK" ]; then
  sh "$BACKUP_HOOK" "$@"
fi

# Get commit details
TIMESTAMP=$(date +%s000)
COMMIT_HASH=$(git rev-parse HEAD 2>/dev/null || echo unknown)
# Get just the first line of the commit message safely
COMMIT_MSG=$(git log -1 --pretty=%B 2>/dev/null | head -n 1 | tr -d '\r\n')
COMMIT_MSG_ESCAPED=$(printf '%s' "$COMMIT_MSG" | sed 's/\\/\\\\/g; s/"/\\"/g')
EVENT_ID="contextd-git-$TIMESTAMP-$COMMIT_HASH"

# Construct one newline-delimited JSON object matching our RawEvent struct.
PAYLOAD=$(printf '{"id":"%s","timestamp_ms":%s,"source":"git","payload":{"action":"commit","hash":"%s","message":"%s"}}' \
  "$EVENT_ID" \
  "$TIMESTAMP" \
  "$COMMIT_HASH" \
  "$COMMIT_MSG_ESCAPED"
)

SOCKET_PATH="${CONTEXTD_SOCKET:-/tmp/contextd/contextd.sock}"

if ! command -v nc >/dev/null 2>&1; then
  echo "contextd post-commit hook: 'nc' not found; cannot send event to contextd (socket: $SOCKET_PATH)" >&2
  exit 0
fi

if ! printf '%s\n' "$PAYLOAD" | nc -U "$SOCKET_PATH"; then
  echo "contextd post-commit hook: failed to send event to contextd via socket '$SOCKET_PATH'" >&2
fi

exit 0
"#;

/// Finds the nearest Git repository root by walking up from `start`.
pub fn find_git_root(start: impl AsRef<Path>) -> Option<PathBuf> {
    start
        .as_ref()
        .ancestors()
        .find(|path| path.join(".git").exists())
        .map(Path::to_path_buf)
}

/// Installs the git hooks into the target repository
#[cfg(unix)]
pub fn install_hooks(repo_path: impl AsRef<Path>) -> std::io::Result<()> {
    let hooks_dir = repo_path.as_ref().join(".git/hooks");

    if !hooks_dir.exists() {
        warn!(
            "Not a git repository (or no hooks dir): {:?}",
            repo_path.as_ref()
        );
        return Ok(()); // Fail gracefully if they run the daemon outside a repo
    }

    let post_commit_path = hooks_dir.join("post-commit");
    let backup_path = hooks_dir.join(BACKUP_HOOK_FILE_NAME);

    if post_commit_path.exists() {
        let existing = fs::read_to_string(&post_commit_path).unwrap_or_default();
        if !is_contextd_hook(&existing) {
            if backup_path.exists() {
                warn!(
                    "Existing non-contextd post-commit hook found and backup already exists at {:?}; leaving hook unchanged",
                    backup_path
                );
                return Ok(());
            }

            fs::rename(&post_commit_path, &backup_path)?;
            info!(
                "Preserved existing Git post-commit hook at {:?}",
                backup_path
            );
        }
    }

    fs::write(&post_commit_path, POST_COMMIT_HOOK)?;

    let mut perms = fs::metadata(&post_commit_path)?.permissions();
    perms.set_mode(0o755);
    fs::set_permissions(&post_commit_path, perms)?;

    info!(
        "Successfully installed Git post-commit hook in {:?}",
        hooks_dir
    );

    Ok(())
}

#[cfg(unix)]
fn is_contextd_hook(contents: &str) -> bool {
    contents.contains(CONTEXTD_HOOK_MARKER) || contents.contains(LEGACY_CONTEXTD_HOOK_MARKER)
}

/// Non-Unix platforms do not support the Unix-domain socket hook path yet.
#[cfg(not(unix))]
pub fn install_hooks(repo_path: impl AsRef<Path>) -> std::io::Result<()> {
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
    fn mode(path: &Path) -> u32 {
        std::fs::metadata(path).unwrap().permissions().mode() & 0o777
    }

    #[cfg(unix)]
    #[test]
    fn install_hooks_creates_managed_post_commit_hook() {
        let repo = temp_repo("creates-hook");

        install_hooks(&repo).expect("hook install should succeed");

        let hook = repo.join(".git/hooks/post-commit");
        let contents = std::fs::read_to_string(&hook).unwrap();

        assert!(contents.contains(CONTEXTD_HOOK_MARKER));
        assert!(contents.contains("printf '%s\\n' \"$PAYLOAD\""));
        assert!(contents.contains("CONTEXTD_SOCKET"));
        assert!(contents.contains("command -v nc"));
        assert_eq!(mode(&hook), 0o755);
    }

    #[cfg(unix)]
    #[test]
    fn install_hooks_updates_managed_hook_idempotently() {
        let repo = temp_repo("idempotent-hook");

        install_hooks(&repo).expect("first install should succeed");
        install_hooks(&repo).expect("second install should succeed");

        let hooks_dir = repo.join(".git/hooks");
        let hook = std::fs::read_to_string(hooks_dir.join("post-commit")).unwrap();

        assert!(hook.contains(CONTEXTD_HOOK_MARKER));
        assert!(!hooks_dir.join(BACKUP_HOOK_FILE_NAME).exists());
    }

    #[cfg(unix)]
    #[test]
    fn install_hooks_preserves_existing_custom_hook_as_backup() {
        let repo = temp_repo("preserve-custom-hook");
        let hook_path = repo.join(".git/hooks/post-commit");
        std::fs::write(&hook_path, "#!/bin/sh\necho custom-hook\n").unwrap();

        install_hooks(&repo).expect("hook install should succeed");

        let hooks_dir = repo.join(".git/hooks");
        let hook = std::fs::read_to_string(hooks_dir.join("post-commit")).unwrap();
        let backup = std::fs::read_to_string(hooks_dir.join(BACKUP_HOOK_FILE_NAME)).unwrap();

        assert!(hook.contains(CONTEXTD_HOOK_MARKER));
        assert!(hook.contains(BACKUP_HOOK_FILE_NAME));
        assert_eq!(backup, "#!/bin/sh\necho custom-hook\n");
    }

    #[cfg(unix)]
    #[test]
    fn install_hooks_updates_legacy_contextd_hook_without_backup() {
        let repo = temp_repo("legacy-contextd-hook");
        let hooks_dir = repo.join(".git/hooks");
        std::fs::write(
            hooks_dir.join("post-commit"),
            "#!/bin/bash\n# contextd git post-commit hook\n",
        )
        .unwrap();

        install_hooks(&repo).expect("hook install should succeed");

        let hook = std::fs::read_to_string(hooks_dir.join("post-commit")).unwrap();

        assert!(hook.contains(CONTEXTD_HOOK_MARKER));
        assert!(!hooks_dir.join(BACKUP_HOOK_FILE_NAME).exists());
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
