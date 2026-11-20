"""Run Component 2.1 evaluation suites and write machine-readable results."""

from __future__ import annotations

import argparse
import json
import platform
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness import DATASET_VERSION, SCORER_VERSION
from harness.daemon import REPO_ROOT, ensure_binary
from harness.historical import historical_regex
from harness.metrics import summarize
from harness.runner import execute_case

ROOT = Path(__file__).resolve().parents[1]
DATASETS = ROOT / "datasets"
SUITES = ("regression", "development", "heldout", "continuation")


def load_suite(name: str) -> list[dict[str, Any]]:
    path = DATASETS / name / "cases.json"
    document = json.loads(path.read_text())
    if document.get("version") != DATASET_VERSION:
        raise SystemExit(f"{path} version {document.get('version')} != {DATASET_VERSION}")
    cases = document["cases"]
    return sorted(cases, key=lambda case: case["id"])


def git_snapshot() -> dict[str, str]:
    def run(args: list[str]) -> str:
        result = subprocess.run(args, cwd=REPO_ROOT, capture_output=True, text=True, check=False)
        return (result.stdout or result.stderr).strip()

    return {
        "revision": run(["git", "rev-parse", "HEAD"]),
        "status": run(["git", "status", "--short"]),
    }


def require_clean(status: str) -> None:
    """Official numbers describe one revision. A dirty status does not."""
    if status.strip():
        raise SystemExit(
            "refusing to record an official Component 2.1 run from a dirty tree:\n" + status
        )


def run_suites(names: list[str], with_historical: bool, binary: Path, *, official: bool) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for name in names:
        for case in load_suite(name):
            print(f"run {case['id']}", flush=True)
            result = execute_case(case, binary)
            print(f"  {result['status']}", flush=True)
            cases.append(result)
    document = {
        "dataset_version": DATASET_VERSION,
        "scorer": SCORER_VERSION,
        "official": official,
        "suites": names,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git": git_snapshot(),
        "binary": {
            "path": str(binary),
            "package_version": "0.1.0",
            "profile": "debug",
        },
        "environment": {
            "python": platform.python_version(),
            "system": platform.platform(),
        },
        "model": None,
        "random_seed": None,
        "command": "python -m harness.run" + ("" if len(sys.argv) == 1 else " " + shlex.join(sys.argv[1:])),
        "cases": cases,
        "metrics": summarize(cases),
        "historical_regex": historical_regex() if with_historical else {
            "status": "not_requested",
            "factuality_oracle": False,
        },
        "counts": _counts(cases),
    }
    return document


def _counts(cases: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"pass": 0, "fail": 0, "blocked": 0, "vacuous": 0, "incomplete": 0}
    for case in cases:
        status = case.get("status")
        if status in counts:
            counts[status] += 1
    counts["executed"] = sum(1 for case in cases if case.get("executed"))
    counts["harness_errors"] = sum(1 for case in cases if case.get("harness_error"))
    counts["total"] = len(cases)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Component 2.1 evaluation harness")
    parser.add_argument("--suite", action="append", choices=SUITES)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--with-historical", action="store_true")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Record a run from a dirty tree. Never use this for baseline/results.json.",
    )
    parser.add_argument(
        "--reuse-binary",
        action="store_true",
        help="Score the existing target/debug/contextd. Official runs rebuild.",
    )
    args = parser.parse_args()
    snapshot = git_snapshot()
    if not args.allow_dirty:
        require_clean(snapshot["status"])
    binary = ensure_binary(rebuild=not args.reuse_binary)
    names = args.suite or list(SUITES)
    document = run_suites(names, args.with_historical, binary, official=not args.allow_dirty)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2) + "\n")
    counts = document["counts"]
    print(
        f"wrote {args.output} pass={counts['pass']} fail={counts['fail']} "
        f"vacuous={counts['vacuous']} incomplete={counts['incomplete']} "
        f"blocked={counts['blocked']} harness_errors={counts['harness_errors']} "
        f"revision={document['git']['revision']}"
    )
    if counts["harness_errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
