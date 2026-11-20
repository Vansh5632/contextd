"""CLI: python3 -m eval.harness --layer 0"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from .agent import run_arms
from .episode import Episode, iter_episode_paths, load_episode
from .inject import inject_history, run_l0_queries
from .isolate import find_binary, repo_root, start_daemon
from .metrics import score_arm
from .report import write_jsonl, write_markdown
from .repo import RepoPrepareError, prepare_cwd
from .score_l0 import score_l0


def eval_root() -> Path:
    return repo_root() / "eval"


def select_episodes(args: argparse.Namespace) -> list[Episode]:
    root = eval_root()
    episodes = [load_episode(path) for path in iter_episode_paths(root)]
    if args.episode:
        wanted = set(args.episode)
        episodes = [
            episode
            for episode in episodes
            if episode.name in wanted or episode.path.stem in wanted
        ]
        found = {episode.name for episode in episodes} | {episode.path.stem for episode in episodes}
        missing = wanted - found
        if missing:
            raise SystemExit(f"unknown episodes: {sorted(missing)}")
    if args.level:
        episodes = [episode for episode in episodes if episode.level == args.level]
    if args.ci:
        episodes = [
            episode
            for episode in episodes
            if not episode.skip_ci and not episode.requires_embeddings
        ]
    return episodes


def run_episode(episode: Episode, layer: int) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    daemon = start_daemon()
    try:
        inject_history(daemon, episode, now_ms)
        replies = run_l0_queries(daemon, episode)
        l0 = score_l0(episode, replies)
        row: dict[str, Any] = {
            "name": episode.name,
            "level": episode.level,
            "category": episode.category,
            "path": str(episode.path),
            "l0": {
                "passed": l0.passed,
                "invalid": l0.invalid,
                "reason": l0.reason,
                "required_hits": l0.required_hits,
                "forbidden_hits": l0.forbidden_hits,
                "frontier_hits": l0.frontier_hits,
                "decision_hits": l0.decision_hits,
                "precision": l0.precision,
                "relevant": l0.relevant,
                "retrieved": l0.retrieved,
            },
        }
        if layer >= 1 and 1 in episode.layers:
            if not l0.passed or l0.invalid:
                row["l1"] = {"skipped": True, "reason": "L0 did not pass"}
            else:
                snapshot = replies.get("now") or next(iter(replies.values()), {})
                try:
                    cwd = _episode_cwd(episode)
                except RepoPrepareError as err:
                    row["l1"] = {"skipped": True, "reason": str(err)}
                    return row
                arms = run_arms(daemon, episode, snapshot=snapshot, cwd=cwd)
                row["l1"] = [
                    {
                        "arm": metrics.arm,
                        "skipped": metrics.skipped,
                        "resume_clarifications": metrics.resume_clarifications,
                        "rediscovery_commands": metrics.rediscovery_commands,
                        "time_to_useful_s": metrics.time_to_useful_s,
                        "duplicate_mentions": metrics.duplicate_mentions,
                        "decision_violations": metrics.decision_violations,
                        "stale_mentions": metrics.stale_mentions,
                        "novel_work_ratio": metrics.novel_work_ratio,
                        "cheated": metrics.cheated,
                        "asked_user": metrics.asked_user,
                        "mcp_tools_called": metrics.mcp_tools_called,
                    }
                    for metrics in (score_arm(episode, arm) for arm in arms)
                ]
        return row
    finally:
        daemon.close()


def _episode_cwd(episode: Episode) -> Path:
    return prepare_cwd(eval_root(), episode)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the contextd product eval")
    parser.add_argument("--layer", type=int, default=0, choices=(0, 1))
    parser.add_argument("--episode", action="append", default=[])
    parser.add_argument("--level", choices=("l1", "l2", "l3"))
    parser.add_argument("--ci", action="store_true", help="skip embedding-only and skip_ci episodes")
    parser.add_argument("--report-dir", type=Path, default=eval_root() / "reports")
    args = parser.parse_args(argv)

    try:
        find_binary()
    except FileNotFoundError as err:
        print(err, file=sys.stderr)
        return 2

    episodes = select_episodes(args)
    if not episodes:
        print("no episodes selected", file=sys.stderr)
        return 2

    rows = []
    failed = 0
    args.report_dir.mkdir(parents=True, exist_ok=True)
    for episode in episodes:
        print(f"running {episode.name} ({episode.level}/{episode.category})", flush=True)
        row = run_episode(episode, args.layer)
        rows.append(row)
        write_jsonl(args.report_dir / "latest.jsonl", rows)
        write_markdown(args.report_dir / "latest.md", rows)
        l0 = row["l0"]
        if l0["invalid"]:
            print(f"  invalid: {l0['reason']}")
        elif l0["passed"]:
            print("  L0 pass")
        else:
            print(f"  L0 fail: {l0['reason']}")
            failed += 1
        l1 = row.get("l1")
        if isinstance(l1, dict) and l1.get("skipped"):
            print(f"    L1 skipped: {l1.get('reason')}")
        elif isinstance(l1, list):
            for arm in l1:
                if arm.get("skipped"):
                    print(f"    L1 {arm.get('arm')}: skipped ({arm.get('reason')})")
                else:
                    print(
                        f"    L1 {arm.get('arm')}: asked={arm.get('asked_user')} "
                        f"cheat={arm.get('cheated')} "
                        f"resume={arm.get('resume_clarifications')} "
                        f"dup={arm.get('duplicate_mentions')} "
                        f"tools={arm.get('mcp_tools_called')} "
                        f"t={arm.get('time_to_useful_s')}"
                    )

    write_jsonl(args.report_dir / "latest.jsonl", rows)
    write_markdown(args.report_dir / "latest.md", rows)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
