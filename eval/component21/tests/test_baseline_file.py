"""The committed baseline is a measurement, not a pass certificate."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "baseline" / "results.json"


def test_baseline_file_separates_pass_fail_and_blocked() -> None:
    document = json.loads(BASELINE.read_text())
    assert document["dataset_version"] == "component21-eval-1"
    assert document["git"]["revision"]
    counts = {"pass": 0, "fail": 0, "blocked": 0}
    failure_ids = set()
    for case in document["cases"]:
        assert case["status"] in counts
        counts[case["status"]] += 1
        if case["status"] == "pass":
            assert case["contract_holds"] is True
        elif case["status"] == "fail":
            assert case["contract_holds"] is False
            assert case["executed"] is True
        else:
            assert case["contract_holds"] is None
            assert case["status"] != "pass"
        if case.get("suite") == "regression" and case.get("failure_id") is not None:
            failure_ids.add(case["failure_id"])
    assert failure_ids == set(range(1, 21))
    assert counts == document["counts"] or set(counts) <= set(document["counts"])
    assert document["counts"]["pass"] == counts["pass"]
    assert document["counts"]["fail"] == counts["fail"]
    assert document["counts"]["blocked"] == counts["blocked"]
    assert document["historical_regex"]["factuality_oracle"] is False
