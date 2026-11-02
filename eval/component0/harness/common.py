from __future__ import annotations

import os
import subprocess
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

COMPONENT_ROOT = Path(__file__).resolve().parents[1]
EPISODES_DIR = COMPONENT_ROOT / "episodes"
DEFAULT_EPISODE = "limiter-handoff"


@dataclass(frozen=True)
class Pins:
    episode_dir: Path
    name: str
    repo_url: str
    repo_sha: str
    repo_tag: str
    author_name: str
    author_email: str
    default_branch: str
    import_message: str
    import_date: str
    followup_message: str
    followup_date: str
    cache_dir: Path
    workspace_root: Path
    plant_patches: tuple[Path, ...]
    followup_patch: Path
    visible_test: Path
    visible_test_dest: str
    fix_patch: Path
    hidden_tests: Path
    leak_terms: Path

    @property
    def workspace_dir(self) -> Path:
        return self.workspace_root / self.name

    @property
    def tar_path(self) -> Path:
        return self.workspace_root / f"{self.name}.tar"

    @property
    def manifest_path(self) -> Path:
        return self.workspace_root / f"{self.name}.manifest.json"

    @property
    def fixed_mtime(self) -> int:
        return int(datetime.fromisoformat(self.followup_date).timestamp())


def load_pins(episode: str = DEFAULT_EPISODE) -> Pins:
    episode_dir = EPISODES_DIR / episode
    with open(episode_dir / "pins.toml", "rb") as handle:
        data = tomllib.load(handle)
    repo, git, paths, ep = data["repo"], data["git"], data["paths"], data["episode"]
    return Pins(
        episode_dir=episode_dir,
        name=ep["name"],
        repo_url=repo["url"],
        repo_sha=repo["sha"],
        repo_tag=repo["tag"],
        author_name=git["author_name"],
        author_email=git["author_email"],
        default_branch=git["default_branch"],
        import_message=git["import_message"],
        import_date=git["import_date"],
        followup_message=git["followup_message"],
        followup_date=git["followup_date"],
        cache_dir=COMPONENT_ROOT / paths["cache"],
        workspace_root=COMPONENT_ROOT / paths["workspace_root"],
        plant_patches=tuple(episode_dir / p for p in ep["plant_patches"]),
        followup_patch=episode_dir / ep["followup_patch"],
        visible_test=episode_dir / ep["visible_test"],
        visible_test_dest=ep["visible_test_dest"],
        fix_patch=episode_dir / ep["fix_patch"],
        hidden_tests=episode_dir / ep["hidden_tests"],
        leak_terms=episode_dir / ep["leak_terms"],
    )


def clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Host environment for subprocesses.

    PYTHONPATH is dropped because host ROS entries break pytest plugin loading.
    Global and system git config are ignored so host hooks, signing and
    templates cannot leak into the scratch repos.
    """
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
            "TZ": "UTC",
        }
    )
    if extra:
        env.update(extra)
    return env


def run(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        env=env if env is not None else clean_env(),
        check=check,
        capture_output=True,
        text=True,
    )


def git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return run(["git", "-C", str(repo), *args], env=env).stdout
