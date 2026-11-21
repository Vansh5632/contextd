"""The shadow experiment never writes memory and never spends without a flag."""

from __future__ import annotations

import json

import pytest

from harness.shadow_experiment import (
    SpendNotAuthorized,
    assign_owner,
    build_request_payload,
    estimate_cost,
    plan_experiment,
    refuse_same_output,
    score_predictions,
    select_candidates,
    write_json,
)
from harness.openai_shadow import OpenAIShadow

LISTING = "def deliver(self, message):\n    self.retry_count += 1\n    self.open_socket()\n"
FALSE = "retry_count never increments before open_socket"
PARAPHRASE = "the retry counter is incremented before the socket opens"
POOL = "attempt_budget increments before open_socket; the database pool is exhausted"


def _case(case_id, suite, text, factuality, *, reason, outcome, established, parent_reason=None, parent_outcome=None):
    return {
        "id": case_id,
        "suite": suite,
        "mode": "daemon",
        "judgment": "contract",
        "evidence_text": LISTING,
        "verifier_diagnostics": [
            {
                "stage": "admit" if outcome == "accepted" else "apply_extraction",
                "text": text,
                "outcome": outcome,
                "reason": reason,
                "relation": "supported" if reason == "lexical_pass" else "insufficient",
            }
        ]
        if reason
        else [
            {
                "stage": "apply_extraction",
                "text": text + " tail",
                "outcome": parent_outcome,
                "reason": parent_reason,
                "relation": "insufficient",
            }
        ],
        "atoms": [
            {
                "id": "atom",
                "text": text,
                "factuality": factuality,
                "expect": "presented_iff_supported",
                "checker": "accepted" if established else "skipped",
                "established": established,
                "presented_established": established,
                "status": "fail",
            }
        ],
    }


def _population():
    return [
        _case("f00-supported-control", "regression", "retry_count increments before open_socket", "SUPPORTED", reason="lexical_pass", outcome="accepted", established=True),
        _case("f01-negation", "regression", FALSE, "CONTRADICTED", reason="lexical_pass", outcome="accepted", established=True),
        _case("f03-paraphrase", "regression", PARAPHRASE, "SUPPORTED", reason="residue", outcome="skipped", established=False),
        _case("d03-paraphrase", "development", "the quota grows before release", "SUPPORTED", reason="order", outcome="skipped", established=False),
        _case("f09-semicolon", "regression", "attempt_budget increments before open_socket", "SUPPORTED", reason=None, outcome=None, established=False, parent_reason="residue", parent_outcome="skipped"),
        _case("f09-pool", "regression", POOL, "INSUFFICIENT_EVIDENCE", reason="residue", outcome="skipped", established=False),
        _case("h01-rust-supported", "heldout", "sealed claim", "SUPPORTED", reason="lexical_pass", outcome="accepted", established=True),
        _case("f16-restatement", "regression", "same text again", "SUPPORTED", reason="duplicate", outcome="skipped", established=False),
    ]


def test_selection_keeps_heldout_sealed_and_covers_both_cohorts() -> None:
    rows = select_candidates(_population())
    ids = {row["case_id"] for row in rows}
    assert "h01-rust-supported" not in ids
    assert "f16-restatement" not in ids
    assert "f00-supported-control" in ids
    assert "f01-negation" in ids
    assert "f03-paraphrase" in ids
    assert "d03-paraphrase" in ids
    assert "f09-semicolon" in ids
    assert "f09-pool" in ids
    by_id = {row["case_id"]: row for row in rows}
    assert by_id["f01-negation"]["cohort"] == "lexical_pass_accept"
    assert by_id["f01-negation"]["gold_role"] == "negative_control"
    assert by_id["f00-supported-control"]["gold_role"] == "supported"
    assert by_id["f03-paraphrase"]["cohort"] == "residue_or_order_skip"
    assert by_id["f09-pool"]["gold_role"] == "negative_control"
    assert by_id["f09-semicolon"]["cohort"] == "unsplit_supported"
    assert all(row["writes_memory"] is False for row in rows)


def test_prompt_hides_gold_and_the_checker_reason() -> None:
    row = next(item for item in select_candidates(_population()) if item["case_id"] == "f01-negation")
    payload = build_request_payload("gpt-4.1-mini", row)
    user = payload["messages"][1]["content"]
    blob = json.dumps(payload)
    assert row["gold"] not in user
    assert "lexical_pass" not in blob
    assert "negative_control" not in blob
    assert row["case_id"] not in user
    assert row["claim_text"] in user
    assert LISTING in user
    schema = payload["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["label"]["enum"] == [
        "SUPPORTED",
        "CONTRADICTED",
        "INSUFFICIENT_EVIDENCE",
    ]
    assert schema["additionalProperties"] is False


def test_unauthorized_classifier_does_not_call_the_transport() -> None:
    called = {"n": 0}

    class Transport:
        def complete(self, payload):
            called["n"] += 1
            return payload

    with pytest.raises(SpendNotAuthorized):
        OpenAIShadow(Transport(), "gpt-4.1-mini", authorized=False)
    assert called["n"] == 0


def test_scoring_counts_a_negative_control_the_model_accepts() -> None:
    rows = select_candidates(_population())
    predictions = []
    for row in rows:
        quote = "self.retry_count += 1" if row["case_id"] != "f09-pool" else "not in the listing"
        predictions.append({**row, "model_label": "SUPPORTED", "evidence_quote": quote, "reasoning": "because"})
    metrics = score_predictions(predictions)
    assert metrics["false_claim_admission"]["numerator"] >= 1
    assert metrics["negative_control_flips"] >= 1
    assert metrics["evidence_valid"]["numerator"] < metrics["evidence_valid"]["denominator"]
    assert metrics["writes_memory"] is False
    owners = {row["case_id"]: assign_owner(row) for row in rows}
    assert owners["f01-negation"] == "ai"
    assert owners["f03-paraphrase"] == "ai"
    assert owners["f09-pool"] == "rule"
    assert owners["f09-semicolon"] == "escalate"


def test_plan_estimates_cost_and_records_no_model_call() -> None:
    plan = plan_experiment(_population(), model="gpt-4.1-mini")
    assert plan["model_called"] is False
    assert plan["authorization"] == "spend_not_authorized"
    assert plan["writes_memory"] is False
    assert plan["heldout_excluded"] is True
    assert plan["estimate"]["candidates"] == len(select_candidates(_population()))
    assert plan["estimate"]["usd"] == estimate_cost(
        plan["estimate"]["prompt_tokens"],
        plan["estimate"]["completion_tokens"],
        "gpt-4.1-mini",
    )["usd"]
    assert estimate_cost(1000, 100, "gpt-4.1-mini")["usd"] == (1000 * 0.40 + 100 * 1.60) / 1_000_000
    checker = plan["checker"]
    assert checker["false_claim_admission"]["denominator"] > 0


def test_a_repeated_case_id_is_refused() -> None:
    cases = _population()
    cases.append({**cases[0], "suite": "heldout"})
    with pytest.raises(ValueError, match="shared"):
        select_candidates(cases)


def test_output_cannot_replace_the_input_and_missing_parents_are_created(tmp_path) -> None:
    source = tmp_path / "in.json"
    source.write_text("{}\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        refuse_same_output(source, source)
    target = tmp_path / "nested" / "plan.json"
    write_json(target, {"model_called": False})
    assert json.loads(target.read_text(encoding="utf-8"))["model_called"] is False
    assert not list(target.parent.glob(".plan.json.*.tmp"))
