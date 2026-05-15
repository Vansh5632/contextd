use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::Path;
use tracing::{info, warn};

// The bash script we will inject into the .git/hooks directory
const POST_COMMIT_HOOK: &str = r#"#!/bin/bash
# contextd git post-commit hook

# Get commit details
TIMESTAMP=$(date +%s000)
COMMIT_HASH=$(git rev-parse HEAD)
# Get just the first line of the commit message safely
COMMIT_MSG=$(git log -1 --pretty=%B | head -n 1 | tr -d '"' | tr -d '\n')
EVENT_ID="contextd-git-$TIMESTAMP-$COMMIT_HASH"

# Construct the JSON exactly matching our RawEvent struct
PAYLOAD=$(cat <<EOF
{
  "id": "$EVENT_ID",
  "timestamp_ms": $TIMESTAMP,
  "source": "git",
  "payload": {
    "action": "commit",
    "hash": "$COMMIT_HASH",
    "message": "$COMMIT_MSG"
  }
}
EOF
)

# Fire and forget to the daemon socket using netcat
echo "$PAYLOAD" | nc -U /tmp/contextd/contextd.sock || true
"#;

/// Installs the git hooks into the target repository
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

    // Write the script to the file
    fs::write(&post_commit_path, POST_COMMIT_HOOK)?;

    // Make it executable (chmod 755)
    let mut perms = fs::metadata(&post_commit_path)?.permissions();
    perms.set_mode(0o755);
    fs::set_permissions(&post_commit_path, perms)?;

    info!(
        "Successfully installed Git post-commit hook in {:?}",
        hooks_dir
    );

    Ok(())
}
