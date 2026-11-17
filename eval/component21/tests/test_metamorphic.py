"""The development generator must not read the sealed suite."""

from __future__ import annotations

from pathlib import Path

from harness.metamorphic import build_cases

SOURCE = Path(__file__).resolve().parents[1] / "harness" / "metamorphic.py"


def test_generator_does_not_name_the_sealed_suite() -> None:
    text = SOURCE.read_text()
    assert "heldout" not in text
    assert "define_heldout" not in text


def test_transformations_declare_invariants() -> None:
    cases = build_cases()
    kinds = {case["transformation"] for case in cases}
    assert "consistent_rename" in kinds
    assert "negation" in kinds
    assert "reversed_operation_order" in kinds
    assert "language_javascript" in kinds
    assert "language_go" in kinds
    assert "language_rust" in kinds
    for case in cases:
        assert case["invariant"]
        assert case["atoms"][0]["factuality"] in {
            "SUPPORTED",
            "CONTRADICTED",
            "INSUFFICIENT_EVIDENCE",
        }
