"""The grader must detect planted false memories without a daemon."""

from __future__ import annotations

from harness.continuation import score_trace
from harness.judge import judge_atom
from harness.metrics import semantic_metrics
from harness.validity_demo import planted_reality, regex_gap


def test_supported_control_is_credited() -> None:
    text = "retry_count increments before open_socket"
    judgment = judge_atom(
        {
            "id": "ok",
            "text": text,
            "factuality": "SUPPORTED",
            "expect": "presented_iff_supported",
            "justification": "The planted listing increments retry_count before the call.",
        },
        planted_reality(text),
    )
    assert judgment["status"] == "pass"
    assert judgment["established"] is True


def test_contradicted_claim_is_not_rewarded() -> None:
    text = "retry_count never increments before open_socket"
    judgment = judge_atom(
        {
            "id": "neg",
            "text": text,
            "factuality": "CONTRADICTED",
            "expect": "presented_iff_supported",
            "justification": "The planted listing does increment retry_count.",
        },
        planted_reality(text),
    )
    assert judgment["status"] == "fail"
    assert "presented_as_reality" in judgment["flags"]


def test_missing_supported_claims_score_zero_recall() -> None:
    empty = planted_reality("unused")
    empty["accepted"] = []
    empty["rendered"] = []
    empty["store_claims"] = []
    judgment = judge_atom(
        {
            "id": "missing",
            "text": "retry_count increments before open_socket",
            "factuality": "SUPPORTED",
            "expect": "presented_iff_supported",
            "justification": "The useful claim is absent.",
        },
        empty,
    )
    assert judgment["status"] == "fail"
    assert "not_presented" in judgment["flags"]


def test_correct_text_on_the_wrong_evidence_fails() -> None:
    text = "retry_count increments before open_socket"
    checkpoint = planted_reality(text, evidence_ids=["evt-other"])
    judgment = judge_atom(
        {
            "id": "evidence",
            "text": text,
            "factuality": "SUPPORTED",
            "expect": "evidence_resolves",
            "evidence_bind": "source",
            "snippet": "retry_count += 1",
            "justification": "The claim must cite the observation that contains the increment.",
        },
        checkpoint,
    )
    assert judgment["status"] == "fail"
    assert "evidence_mismatch" in judgment["flags"]


def test_stale_claim_labeled_current_fails() -> None:
    text = "attempt_budget increments before open_socket"
    judgment = judge_atom(
        {
            "id": "stale",
            "text": text,
            "factuality": "INSUFFICIENT_EVIDENCE",
            "expect": "must_not_be_current",
            "justification": "The source changed and this finding is no longer current.",
        },
        planted_reality(text),
    )
    assert judgment["status"] == "fail"
    assert "stale_current" in judgment["flags"]


def test_wrong_author_fails() -> None:
    text = "A constraint is to keep the signature unchanged."
    judgment = judge_atom(
        {
            "id": "author",
            "text": text,
            "factuality": "INSUFFICIENT_EVIDENCE",
            "expect": "author_is",
            "provenance": "agent",
            "justification": "The note speaker is the agent.",
        },
        planted_reality(text, origin="user"),
    )
    assert judgment["status"] == "fail"
    assert "origin_user" in judgment["flags"]


def test_wrong_task_fails() -> None:
    text = "beta_budget increments"
    checkpoint = planted_reality(text)
    checkpoint["binds"]["beta"] = "evt-beta"
    checkpoint["store_claims"].append(
        {
            "id": "projector",
            "task_id": "task-beta",
            "kind": "reality",
            "status": "current",
            "confidence": "observed",
            "origin": "projector",
            "text": "pytest -q exited 1",
            "evidence_ids": ["evt-beta"],
        }
    )
    judgment = judge_atom(
        {
            "id": "task",
            "text": text,
            "factuality": "SUPPORTED",
            "expect": "task_matches",
            "task_bind": "beta",
            "justification": "The cited command is the beta event.",
        },
        checkpoint,
    )
    assert judgment["status"] == "fail"
    assert "wrong_task" in judgment["flags"]


def test_query_error_is_not_an_empty_success() -> None:
    failed = planted_reality("unused")
    failed["handoff_error"] = "query handler unavailable"
    failed["panic"] = True
    failed["rendered"] = []
    failed["accepted"] = []
    down = judge_atom(
        {
            "id": "handler",
            "text": "unused",
            "factuality": "SUPPORTED",
            "expect": "handler_available",
            "justification": "The handler must answer.",
        },
        failed,
    )
    healthy = judge_atom(
        {
            "id": "handler",
            "text": "unused",
            "factuality": "SUPPORTED",
            "expect": "handler_available",
            "justification": "An empty successful briefing is a different outcome.",
        },
        planted_reality("unused"),
    )
    assert down["status"] == "fail"
    assert "handler_unavailable" in down["flags"]
    assert healthy["status"] == "pass"


def test_regex_match_does_not_clear_a_false_reality() -> None:
    demo = regex_gap()
    assert demo["regex_matches"] is True
    assert demo["grader_flags_false_reality"] is True


def test_reject_all_does_not_get_perfect_precision() -> None:
    rows = [
        {
            "factuality": "SUPPORTED",
            "expect": "presented_iff_supported",
            "established": False,
            "presented_established": False,
            "status": "fail",
        },
        {
            "factuality": "CONTRADICTED",
            "expect": "presented_iff_supported",
            "established": False,
            "presented_established": False,
            "status": "pass",
        },
    ]
    metrics = semantic_metrics(rows)
    assert metrics["supported_claim_recall"]["value"] == 0
    assert metrics["false_reality_admission_rate"]["value"] == 0
    assert metrics["supported_claim_precision"]["value"] is None


def test_continuation_layers() -> None:
    poor_solved = score_trace(
        {
            "handoff_quality": "poor",
            "expected_layer": "agent",
            "trace": {"solved": True, "incorrect_actions_caused_by_memory": 0},
        }
    )
    good_failed = score_trace(
        {
            "handoff_quality": "good",
            "expected_layer": "agent",
            "trace": {"solved": False, "incorrect_actions_caused_by_memory": 0},
        }
    )
    memory_harm = score_trace(
        {
            "handoff_quality": "poor",
            "expected_layer": "memory",
            "trace": {"solved": False, "incorrect_actions_caused_by_memory": 2},
        }
    )
    assert poor_solved["layer"] == "agent"
    assert poor_solved["memory_credited_for_success"] is False
    assert good_failed["layer"] == "agent"
    assert memory_harm["layer"] == "memory"
