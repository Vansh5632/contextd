"""Prepare the fixture or git checkout an agent arm runs against."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .agent import agent_enabled
from .episode import Episode


class RepoPrepareError(RuntimeError):
    """Clone or checkout of a git-pinned episode failed."""


def prepare_cwd(eval_root: Path, episode: Episode) -> Path:
    if episode.repo_kind == "git" and episode.repo_path:
        if not agent_enabled():
            return eval_root
        cache = eval_root / ".cache" / "repos" / episode.name
        try:
            if not cache.exists():
                cache.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["git", "clone", "--", episode.repo_path, str(cache)],
                    check=True,
                    env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                )
            if episode.repo_commit:
                subprocess.run(
                    ["git", "checkout", "--detach", episode.repo_commit],
                    cwd=str(cache),
                    check=True,
                )
        except (OSError, subprocess.CalledProcessError) as err:
            raise RepoPrepareError(f"could not prepare git repo for {episode.name}: {err}") from err
        return cache
    if episode.repo_path:
        path = eval_root / episode.repo_path
        if path.exists():
            return path
    return eval_root
