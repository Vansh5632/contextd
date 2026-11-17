"""Freeze dataset JSON from the Python case sources and seal the held-out suite."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from datasets.define_continuation import CASES as CONTINUATION
from datasets.define_heldout import CASES as HELDOUT
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


def _seal_heldout() -> None:
    version_path = DATASETS / "heldout" / "VERSION"
    version_path.write_text(DATASET_VERSION + "\n")
    lines = []
    for name in ("VERSION", "cases.json"):
        path = DATASETS / "heldout" / name
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {name}")
    (DATASETS / "heldout" / "SHA256SUMS").write_text("\n".join(lines) + "\n")


def main() -> None:
    _write("regression", REGRESSION)
    _write("development", build_cases())
    _write("heldout", HELDOUT)
    _write("continuation", CONTINUATION)
    _seal_heldout()


if __name__ == "__main__":
    main()
