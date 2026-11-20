"""Dataset schema and coverage of the 20 failure categories."""

from __future__ import annotations

import json
from pathlib import Path

from datasets.define_continuation import CASES as CONTINUATION
from datasets.define_heldout import CASES as HELDOUT
from datasets.define_regression import CASES as REGRESSION
from harness import DATASET_VERSION
from harness.metamorphic import build_cases

ROOT = Path(__file__).resolve().parents[1]


def _load(suite: str) -> dict:
    return json.loads((ROOT / "datasets" / suite / "cases.json").read_text())


def test_frozen_suites_match_their_sources() -> None:
    assert _load("regression")["cases"] == REGRESSION
    assert _load("development")["cases"] == build_cases()
    assert _load("heldout")["cases"] == HELDOUT
    assert _load("continuation")["cases"] == CONTINUATION
    for suite in ("regression", "development", "heldout", "continuation"):
        document = _load(suite)
        assert document["version"] == DATASET_VERSION
        assert document["suite"] == suite


def test_regression_covers_every_failure_category() -> None:
    ids = {case["failure_id"] for case in REGRESSION if case["failure_id"] is not None}
    assert ids == set(range(1, 21))
    assert any(case["control"] for case in REGRESSION)


def test_every_daemon_case_has_a_justified_atom_or_an_explicit_block() -> None:
    for case in REGRESSION + build_cases() + HELDOUT + CONTINUATION:
        assert case["id"]
        assert "mode" in case
        if case["mode"] == "documented":
            assert case["blocked_reason"]
            continue
        if case["mode"] == "grader":
            continue
        if case["mode"] == "synthetic":
            assert case["expected_layer"]
            continue
        assert case["steps"]
        for atom in case["atoms"]:
            assert atom["factuality"] in {"SUPPORTED", "CONTRADICTED", "INSUFFICIENT_EVIDENCE"}
            assert atom["justification"]
            assert atom["expect"]
