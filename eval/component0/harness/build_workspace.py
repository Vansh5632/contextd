"""Build the frozen agent workspace for an episode.

Usage (from eval/component0):
    python -m harness.build_workspace [--episode NAME] [--verify-reproducible]
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from harness.common import Pins, clean_env, git, load_pins, run
from harness.dtar import write_deterministic_tar

GIT_CONFIG = (
    "[core]\n"
    "\trepositoryformatversion = 0\n"
    "\tfilemode = true\n"
    "\tbare = false\n"
    "\tlogallrefupdates = true\n"
)
FORBIDDEN_NAMES = {"agents.md", "agents.override.md", "claude.md"}


def ensure_mirror(pins: Pins) -> None:
    cache = pins.cache_dir
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--mirror", "--quiet", pins.repo_url, str(cache)])
    has_sha = run(
        ["git", "--git-dir", str(cache), "cat-file", "-e", f"{pins.repo_sha}^{{commit}}"],
        check=False,
    )
    if has_sha.returncode != 0:
        run(["git", "--git-dir", str(cache), "fetch", "--quiet", "origin"])
    tag_sha = run(
        ["git", "--git-dir", str(cache), "rev-parse", f"{pins.repo_tag}^{{commit}}"]
    ).stdout.strip()
    if tag_sha != pins.repo_sha:
        raise SystemExit(f"tag {pins.repo_tag} resolves to {tag_sha}, pinned {pins.repo_sha}")


def export_tree(pins: Pins, dest: Path) -> None:
    """Export the pinned tree without upstream history."""
    archive = subprocess.run(
        ["git", "--git-dir", str(pins.cache_dir), "archive", "--format=tar", pins.repo_sha],
        env=clean_env(),
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest, filter="tar")
    expected = set(
        run(
            ["git", "--git-dir", str(pins.cache_dir), "ls-tree", "-r", "--name-only", pins.repo_sha]
        ).stdout.splitlines()
    )
    exported = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if not p.is_dir()}
    if exported != expected:
        raise SystemExit(f"export mismatch: {sorted(exported ^ expected)[:10]}")


def commit_env(pins: Pins, date: str) -> dict[str, str]:
    return clean_env(
        {
            "GIT_AUTHOR_NAME": pins.author_name,
            "GIT_AUTHOR_EMAIL": pins.author_email,
            "GIT_AUTHOR_DATE": date,
            "GIT_COMMITTER_NAME": pins.author_name,
            "GIT_COMMITTER_EMAIL": pins.author_email,
            "GIT_COMMITTER_DATE": date,
        }
    )


def apply_patch(repo: Path, patch: Path) -> None:
    git(repo, "apply", "--check", str(patch))
    git(repo, "apply", str(patch))


def commit_all(repo: Path, message: str, env: dict[str, str]) -> str:
    git(repo, "add", "-A", "-f", env=env)
    git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", message, env=env)
    return git(repo, "rev-parse", "HEAD").strip()


def assert_no_agent_instructions(root: Path) -> None:
    for path in root.rglob("*"):
        if ".git" in path.relative_to(root).parts:
            continue
        if path.name.lower() in FORBIDDEN_NAMES:
            raise SystemExit(f"forbidden instruction file in tree: {path}")


def normalize_git_dir(repo: Path) -> None:
    """Drop host- and time-dependent state so the .git directory is reproducible."""
    dot_git = repo / ".git"
    for name in ("logs", "COMMIT_EDITMSG", "index", "description", "hooks", "info"):
        target = dot_git / name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    (dot_git / "config").write_text(GIT_CONFIG)
    # read-tree writes an index with zeroed stat data; git refreshes it on first status.
    git(repo, "read-tree", "HEAD")


def build(pins: Pins) -> dict[str, object]:
    ensure_mirror(pins)
    repo = pins.workspace_dir
    if repo.exists():
        shutil.rmtree(repo)
    repo.mkdir(parents=True)
    export_tree(pins, repo)

    with tempfile.TemporaryDirectory() as empty_template:
        git(repo, "init", "-q", f"--template={empty_template}", "-b", pins.default_branch)
    (repo / ".git" / "config").write_text(GIT_CONFIG)

    for patch in pins.plant_patches:
        apply_patch(repo, patch)
    dest = repo / pins.visible_test_dest
    if dest.exists():
        raise SystemExit(f"visible test destination already exists: {dest}")
    shutil.copyfile(pins.visible_test, dest)
    assert_no_agent_instructions(repo)
    import_sha = commit_all(repo, pins.import_message, commit_env(pins, pins.import_date))

    apply_patch(repo, pins.followup_patch)
    assert_no_agent_instructions(repo)
    followup_sha = commit_all(repo, pins.followup_message, commit_env(pins, pins.followup_date))

    status = git(repo, "status", "--porcelain", "--untracked-files=all")
    if status.strip():
        raise SystemExit(f"workspace not clean after build:\n{status}")
    # Must run after the status check: status rewrites the index with host stat data.
    normalize_git_dir(repo)

    tar_sha = write_deterministic_tar(
        repo, pins.tar_path, arc_prefix=pins.name, mtime=pins.fixed_mtime
    )
    manifest: dict[str, object] = {
        "episode": pins.name,
        "upstream": {"url": pins.repo_url, "sha": pins.repo_sha, "tag": pins.repo_tag},
        "commits": {"import": import_sha, "followup": followup_sha},
        "tar": pins.tar_path.name,
        "tar_sha256": tar_sha,
    }
    pins.manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode", default="limiter-handoff")
    parser.add_argument(
        "--verify-reproducible", action="store_true", help="build twice and compare outputs"
    )
    args = parser.parse_args(argv)
    pins = load_pins(args.episode)

    first = build(pins)
    commits = first["commits"]
    assert isinstance(commits, dict)
    print(f"import   {commits['import']}")
    print(f"followup {commits['followup']}")
    print(f"tar      {first['tar_sha256']}  {pins.tar_path}")
    if args.verify_reproducible:
        second = build(pins)
        if second != first:
            print(f"NOT reproducible: {second}", file=sys.stderr)
            return 1
        print("reproducible: identical commits and tar hash across two builds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
