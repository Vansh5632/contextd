"""Fail CI when the Component 2.1 harness is untrustworthy.

A daemon contract failure is a measurement. This check does not treat it as a
broken run. It fails when the scorer hides a skip, records a harness crash, or
mixes the sealed suite into the development file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BAD = frozenset({"CONTRADICTED", "INSUFFICIENT_EVIDENCE"})


def problems(document: dict[str, Any], *, heldout_ids: set[str] | None = None) -> list[str]:
    found: list[str] = []
    if document.get("model") is not None:
        found.append("model is not null")
    counts = document.get("counts") or {}
    reliability = ((document.get("metrics") or {}).get("reliability") or {})
    if counts.get("harness_errors") or reliability.get("harness_errors"):
        found.append("harness error recorded")
    banned = set(heldout_ids or ())
    for case in document.get("cases") or []:
        if case.get("id") in banned:
            found.append(f"held-out case {case['id']} is in this file")
        for atom in case.get("atoms") or []:
            uncovered_false_pass = (
                atom.get("expect") == "presented_iff_supported"
                and atom.get("factuality") in BAD
                and atom.get("covered") is False
                and atom.get("status") == "pass"
            )
            if uncovered_false_pass:
                found.append(
                    f"{case.get('id')} {atom.get('id')} uncovered false claim counted as a pass"
                )
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description="Check a Component 2.1 results file")
    parser.add_argument("results", type=Path)
    parser.add_argument(
        "--reject-heldout",
        action="store_true",
        help="Fail if a sealed held-out case id appears in this file",
    )
    args = parser.parse_args()
    document = json.loads(args.results.read_text())
    banned: set[str] = set()
    if args.reject_heldout:
        from datasets.define_heldout import CASES

        banned = {case["id"] for case in CASES}
    found = problems(document, heldout_ids=banned)
    if found:
        for item in found:
            print(item, file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
