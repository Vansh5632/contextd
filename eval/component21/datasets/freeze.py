"""Freeze dataset JSON from the Python case sources.

The sealed held-out suite is not rewritten here.
"""

from __future__ import annotations

import json
from pathlib import Path

from datasets.define_regression import CASES as REGRESSION
from harness import DATASET_VERSION
from harness.metamorphic import build_cases

ROOT = Path(__file__).resolve().parents[1]
DATASETS = ROOT / "datasets"


def _write(suite: str, cases: list[dict]) -> None:
    path = DATASETS / suite / "cases.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"version": DATASET_VERSION, "suite": suite, "cases": cases}
    path.write_text(json.dumps(document, indent=2) + "\n")


def main() -> None:
    """Rewrite the tuning suites. The sealed held-out file is not regenerated."""
    _write("regression", REGRESSION)
    _write("development", build_cases())


if __name__ == "__main__":
    main()
