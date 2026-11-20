"""The grader must detect planted false memories without a daemon."""

from __future__ import annotations

from harness.ci_check import problems
from harness.continuation import score_trace
from harness.judge import judge_atom
from harness.metrics import semantic_metrics, summarize
from harness.runner import _status_from_atoms, incidents_from_steps
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
    assert judgment["evidence"] == "evidence_mismatch"


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
    assert metrics["false_claim_admission"]["value"] is None
    assert metrics["false_claim_admission"]["denominator"] == 0
    assert metrics["uncovered_false_claims"] == 1
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


def _empty(text: str) -> dict:
    checkpoint = planted_reality(text)
    checkpoint["accepted"] = []
    checkpoint["rendered"] = []
    checkpoint["store_claims"] = []
    return checkpoint


def test_skipped_negation_is_not_a_semantic_pass() -> None:
    text = "retry_count never increments before open_socket"
    checkpoint = _empty(text)
    checkpoint["skipped"] = [{"unit": "evt-note", "claim_id": "evt-note", "reason": "unrecognized", "text": text}]
    judgment = judge_atom(
        {
            "id": "neg",
            "text": text,
            "factuality": "CONTRADICTED",
            "expect": "presented_iff_supported",
            "source_bind": "conclusion",
            "justification": "The listing increments retry_count.",
        },
        checkpoint,
    )
    assert judgment["status"] == "vacuous"
    assert judgment["passed"] is None
    assert judgment["covered"] is False
    metrics = semantic_metrics([judgment])
    assert metrics["false_claim_admission"]["value"] is None
    assert metrics["false_claim_admission"]["denominator"] == 0
    assert metrics["uncovered_false_claims"] == 1


def test_explicit_rejection_is_a_semantic_refusal() -> None:
    text = "retry_count never increments before open_socket"
    checkpoint = _empty(text)
    checkpoint["rejected"] = [{"text": text}]
    judgment = judge_atom(
        {
            "id": "neg",
            "text": text,
            "factuality": "CONTRADICTED",
            "expect": "presented_iff_supported",
            "justification": "The checker rejected the negation.",
        },
        checkpoint,
    )
    assert judgment["status"] == "pass"
    assert judgment["covered"] is True
    assert judgment["disposition"] == "semantic"


def test_admitted_negation_has_full_false_admission() -> None:
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
    metrics = semantic_metrics([judgment])
    assert metrics["false_claim_admission"]["value"] == 1
    assert metrics["false_claim_admission"]["numerator"] == 1
    assert metrics["false_claim_admission"]["denominator"] == 1


def test_matching_snippet_passes_evidence_check() -> None:
    text = "retry_count increments before open_socket"
    judgment = judge_atom(
        {
            "id": "evidence",
            "text": text,
            "factuality": "SUPPORTED",
            "expect": "evidence_resolves",
            "evidence_bind": "source",
            "snippet": "retry_count += 1",
            "justification": "The planted output contains the increment.",
        },
        planted_reality(text),
    )
    assert judgment["status"] == "pass"
    assert judgment["evidence"] == "evidence_match"


def test_snippet_missing_from_cited_payload_fails() -> None:
    text = "retry_count increments before open_socket"
    judgment = judge_atom(
        {
            "id": "evidence",
            "text": text,
            "factuality": "SUPPORTED",
            "expect": "evidence_resolves",
            "evidence_bind": "source",
            "snippet": "retry_count += 999",
            "justification": "The cited output does not contain this magnitude.",
        },
        planted_reality(text),
    )
    assert judgment["status"] == "fail"
    assert judgment["evidence"] == "evidence_mismatch"
    assert "evidence_mismatch" in judgment["flags"]


def test_missing_evidence_event_fails() -> None:
    text = "retry_count increments before open_socket"
    checkpoint = planted_reality(text)
    checkpoint["binds"]["source"] = "evt-missing"
    judgment = judge_atom(
        {
            "id": "evidence",
            "text": text,
            "factuality": "SUPPORTED",
            "expect": "evidence_resolves",
            "evidence_bind": "source",
            "snippet": "retry_count += 1",
            "justification": "The bind does not name a stored event.",
        },
        checkpoint,
    )
    assert judgment["status"] == "fail"
    assert judgment["evidence"] == "evidence_missing"
    assert "evidence_missing" in judgment["flags"]


def test_hypothesis_kept_dropped_and_stored_as_fact() -> None:
    text = "attempt_budget might increment before open_socket"
    kept = planted_reality(text)
    kept["accepted"] = []
    kept["rendered"][0]["slot"] = "unknowns"
    kept["rendered"][0]["confidence"] = "hypothesis"
    kept["store_claims"][0]["kind"] = "unknown"
    kept["store_claims"][0]["confidence"] = "hypothesis"
    kept_judgment = judge_atom(
        {
            "id": "kept",
            "text": text,
            "factuality": "INSUFFICIENT_EVIDENCE",
            "expect": "must_be_hypothesis",
            "justification": "Might is a hypothesis.",
        },
        kept,
    )
    dropped = judge_atom(
        {
            "id": "dropped",
            "text": text,
            "factuality": "INSUFFICIENT_EVIDENCE",
            "expect": "must_be_hypothesis",
            "justification": "The hedge was discarded.",
        },
        _empty(text),
    )
    as_fact = judge_atom(
        {
            "id": "fact",
            "text": text,
            "factuality": "INSUFFICIENT_EVIDENCE",
            "expect": "must_be_hypothesis",
            "justification": "The hedge was stored as reality.",
        },
        planted_reality(text),
    )
    assert kept_judgment["status"] == "pass"
    assert dropped["status"] == "fail"
    assert "uncertainty_dropped" in dropped["flags"]
    assert as_fact["status"] == "fail"
    assert "presented_as_reality" in as_fact["flags"]
    metrics = semantic_metrics([kept_judgment, dropped, as_fact])
    assert metrics["correct_uncertainty_representation"]["numerator"] == 1
    assert metrics["correct_uncertainty_representation"]["denominator"] == 3
    assert metrics["correct_uncertainty_representation"]["presented_as_fact"] == 1
    assert metrics["false_claim_admission"]["denominator"] == 0


def test_supersession_and_cross_task_failures() -> None:
    text = "alpha_budget increments"
    superseded = planted_reality(text)
    superseded["rendered"] = []
    superseded["store_claims"][0]["status"] = "superseded"
    cross = judge_atom(
        {
            "id": "alpha",
            "text": text,
            "factuality": "SUPPORTED",
            "expect": "must_stay_current",
            "metric_role": "cross_task",
            "justification": "Another task's retract must not retire this claim.",
        },
        superseded,
    )
    current = planted_reality(text)
    lost = judge_atom(
        {
            "id": "replay",
            "text": text,
            "factuality": "INSUFFICIENT_EVIDENCE",
            "expect": "must_be_superseded",
            "metric_role": "restart_state",
            "justification": "The retract did not stick.",
        },
        current,
    )
    assert cross["status"] == "fail"
    assert "superseded_other_task" in cross["flags"]
    assert lost["status"] == "fail"
    assert "correction_lost" in lost["flags"]


def test_restart_state_is_not_process_restart() -> None:
    case = {
        "id": "f17-timestamp-order",
        "suite": "regression",
        "mode": "daemon",
        "judgment": "contract",
        "executed": True,
        "status": "fail",
        "harness_error": False,
        "steps": [{"action": "restart", "restart_ok": True}],
        "atoms": [
            {
                "id": "replay",
                "text": "attempt_budget increments before open_socket",
                "factuality": "INSUFFICIENT_EVIDENCE",
                "expect": "must_be_superseded",
                "metric_role": "restart_state",
                "status": "fail",
                "flags": ["correction_lost"],
                "established": True,
                "covered": True,
            }
        ],
    }
    summary = summarize([case])
    assert summary["reliability"]["process_restart"]["value"] == 1
    assert summary["temporal"]["restart_state"]["value"] == 0
    assert summary["temporal"]["supersession_correctness"]["denominator"] == 0


def test_unrelated_retention_counts_a_claim_once() -> None:
    def row(atom_id: str, role: str, status: str) -> dict:
        return {
            "id": atom_id,
            "text": "widget_count increments before flush_buffer",
            "expect": "must_stay_current",
            "metric_role": role,
            "status": status,
            "flags": ["superseded_other_task"] if status == "fail" else [],
            "established": status == "pass",
        }

    case = {
        "id": "f15-stale-after-change",
        "suite": "regression",
        "mode": "daemon",
        "judgment": "contract",
        "executed": True,
        "status": "fail",
        "atoms": [
            row("unrelated", "unrelated_retention", "pass"),
            row("unrelated-restart", "unrelated_retention", "pass"),
            {
                "id": "alpha",
                "text": "alpha_budget increments",
                "expect": "must_stay_current",
                "metric_role": "cross_task",
                "status": "fail",
                "flags": ["superseded_other_task"],
            },
        ],
    }
    temporal = summarize([case])["temporal"]
    assert temporal["unrelated_retention"]["denominator"] == 1
    assert temporal["unrelated_retention"]["numerator"] == 1
    assert temporal["cross_task_contamination"]["numerator"] == 1
    assert temporal["cross_task_contamination"]["denominator"] == 1


def test_incomplete_evidence_not_shown_is_not_a_pass() -> None:
    text = "retry_count increments"
    checkpoint = _empty(text)
    checkpoint["store_events"] = [
        {"id": "evt-support", "timestamp_ms": 1, "source": "shell", "payload": {"command": "sed"}}
    ]
    hidden = judge_atom(
        {
            "id": "after",
            "text": text,
            "factuality": "INSUFFICIENT_EVIDENCE",
            "expect": "evidence_incomplete",
            "evidence_bind": "source",
            "justification": "The output field is gone.",
        },
        checkpoint,
    )
    shown = planted_reality(text)
    shown["store_events"][0]["payload"] = {"output": "retry_count += 1\n[output truncated]"}
    admitted = judge_atom(
        {
            "id": "after",
            "text": text,
            "factuality": "INSUFFICIENT_EVIDENCE",
            "expect": "evidence_incomplete",
            "evidence_bind": "source",
            "justification": "The truncated prefix was stored as reality.",
        },
        shown,
    )
    assert hidden["status"] == "incomplete"
    assert hidden["passed"] is None
    assert "incomplete_not_admitted" in hidden["flags"]
    assert admitted["status"] == "fail"
    assert _status_from_atoms([hidden])[:3] == ("incomplete", None, None)
    assert _status_from_atoms([admitted])[:3] == ("fail", None, False)


def test_accepted_then_unrendered_sets_renderer_excluded() -> None:
    text = "retry_count increments before open_socket"
    checkpoint = planted_reality(text)
    checkpoint["rendered"] = []
    checkpoint["store_claims"] = []
    judgment = judge_atom(
        {
            "id": "supported",
            "text": text,
            "factuality": "SUPPORTED",
            "expect": "presented_iff_supported",
            "justification": "The checker accepted the claim and the brief omitted it.",
        },
        checkpoint,
    )
    assert judgment["status"] == "fail"
    assert judgment["renderer_excluded"] is True
    assert "renderer_excluded" in judgment["flags"]


def test_unknown_expectation_is_blocked() -> None:
    judgment = judge_atom(
        {
            "id": "odd",
            "text": "unused",
            "factuality": "SUPPORTED",
            "expect": "not_a_real_expect",
            "justification": "The harness must not invent a score for an unknown rule.",
        },
        planted_reality("unused"),
    )
    assert judgment["status"] == "blocked"
    assert judgment["disposition"] == "blocked"


def test_dead_handler_without_a_panic_string_fails() -> None:
    checkpoint = planted_reality("unused")
    checkpoint["process_alive"] = False
    checkpoint["panic"] = False
    checkpoint["handoff_error"] = None
    checkpoint["now_error"] = None
    judgment = judge_atom(
        {
            "id": "handler",
            "text": "unused",
            "factuality": "SUPPORTED",
            "expect": "handler_available",
            "justification": "A dead process is not an empty briefing.",
        },
        checkpoint,
    )
    assert judgment["status"] == "fail"
    assert "handler_unavailable" in judgment["flags"]
    assert "panic" not in judgment["flags"]


def test_duplicate_skip_without_a_bind_is_not_this_atom() -> None:
    text = "retry_count increments before open_socket"
    checkpoint = _empty(text)
    checkpoint["skipped"] = [{"unit": "someone-else", "reason": "duplicate of a prior claim"}]
    judgment = judge_atom(
        {
            "id": "supported",
            "text": text,
            "factuality": "SUPPORTED",
            "expect": "presented_iff_supported",
            "justification": "This atom has no source bind.",
        },
        checkpoint,
    )
    assert "duplicate_blocks_restatement" not in judgment["flags"]


def test_transport_error_is_preserved_as_an_incident() -> None:
    incidents = incidents_from_steps(
        [
            {
                "client_seq": 2,
                "action": "handoff",
                "query": "handoff",
                "error": "query handler unavailable",
                "transport_error": None,
            },
            {
                "client_seq": 3,
                "action": "now",
                "query": "now",
                "error": "timed out",
                "transport_error": "timed out",
            },
        ],
        panic=True,
    )
    assert incidents[0]["error"] == "query handler unavailable"
    assert incidents[1]["transport_error"] == "timed out"
    assert incidents[-1]["kind"] == "panic"


def test_grader_and_synthetic_cases_stay_out_of_daemon_rates() -> None:
    daemon = {
        "id": "f01-negation",
        "suite": "regression",
        "mode": "daemon",
        "judgment": "contract",
        "executed": True,
        "status": "fail",
        "atoms": [
            {
                "factuality": "CONTRADICTED",
                "expect": "presented_iff_supported",
                "established": True,
                "covered": True,
                "presented_established": True,
                "status": "fail",
                "checker": "accepted",
            }
        ],
    }
    grader = {
        "id": "f20-regex-gap",
        "suite": "regression",
        "mode": "grader",
        "judgment": "contract",
        "executed": True,
        "status": "pass",
        "atoms": [
            {
                "factuality": "CONTRADICTED",
                "expect": "presented_iff_supported",
                "established": False,
                "covered": False,
                "presented_established": False,
                "status": "pass",
                "checker": "skipped",
            }
        ],
    }
    synthetic = {
        "id": "c01-poor-handoff-solved",
        "suite": "continuation",
        "mode": "synthetic",
        "judgment": "metric",
        "executed": True,
        "status": "pass",
        "atoms": [],
    }
    summary = summarize([daemon, grader, synthetic])
    admission = summary["semantic"]["regression"]["false_claim_admission"]
    assert admission["denominator"] == 1
    assert admission["numerator"] == 1
    assert summary["populations"]["grader_self_test"]["pass"] == 1
    assert summary["populations"]["synthetic_continuation"]["pass"] == 1
    assert summary["populations"]["daemon_contract"]["fail"] == 1
    assert "f20-regex-gap" not in summary["headline_case_ids"]


def test_heldout_atom_does_not_change_regression_admission() -> None:
    regression = {
        "id": "f01-negation",
        "suite": "regression",
        "mode": "daemon",
        "judgment": "contract",
        "executed": True,
        "status": "fail",
        "atoms": [
            {
                "factuality": "CONTRADICTED",
                "expect": "presented_iff_supported",
                "established": True,
                "covered": True,
                "presented_established": True,
                "status": "fail",
            }
        ],
    }
    heldout = {
        "id": "h02-rust-order",
        "suite": "heldout",
        "mode": "daemon",
        "judgment": "contract",
        "executed": True,
        "status": "vacuous",
        "atoms": [
            {
                "factuality": "CONTRADICTED",
                "expect": "presented_iff_supported",
                "established": False,
                "covered": False,
                "presented_established": False,
                "status": "vacuous",
                "checker": "skipped",
            }
        ],
    }
    summary = summarize([regression, heldout])
    assert summary["semantic"]["regression"]["false_claim_admission"]["denominator"] == 1
    assert summary["semantic"]["regression"]["false_claim_admission"]["numerator"] == 1
    assert "h02-rust-order" not in summary["headline_case_ids"]
    assert summary["heldout"]["semantic"]["uncovered_false_claims"] == 1


def test_ci_check_rejects_a_hidden_skip_and_a_model() -> None:
    leaked = {
        "model": "gpt",
        "counts": {"harness_errors": 0},
        "metrics": {"reliability": {"harness_errors": 0}},
        "cases": [
            {
                "id": "h04-js-negation",
                "atoms": [
                    {
                        "id": "claim",
                        "expect": "presented_iff_supported",
                        "factuality": "CONTRADICTED",
                        "covered": False,
                        "status": "pass",
                    }
                ],
            }
        ],
    }
    found = problems(leaked, heldout_ids={"h04-js-negation"})
    assert any("model" in item for item in found)
    assert any("held-out" in item for item in found)
    assert any("uncovered false claim" in item for item in found)
    honest = {
        "model": None,
        "counts": {"harness_errors": 0},
        "metrics": {"reliability": {"harness_errors": 0}},
        "cases": [
            {
                "id": "d06-negation",
                "atoms": [
                    {
                        "id": "claim",
                        "expect": "presented_iff_supported",
                        "factuality": "CONTRADICTED",
                        "covered": False,
                        "status": "vacuous",
                    }
                ],
            }
        ],
    }
    assert problems(honest, heldout_ids={"h04-js-negation"}) == []

