"""Layered metrics. These are not collapsed into one quality score.

A run that admits nothing has supported recall 0 and an undefined precision.
It does not receive credit for refusing every claim.
"""

from __future__ import annotations

from typing import Any

BAD = frozenset({"CONTRADICTED", "INSUFFICIENT_EVIDENCE"})


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def atom_rows(case_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for case in case_results:
        if case.get("mode") != "daemon" or case.get("judgment") != "contract":
            continue
        if not case.get("executed"):
            continue
        for atom in case.get("atoms") or []:
            if atom.get("status") == "blocked":
                continue
            rows.append(atom)
    return rows


def semantic_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    supported = [row for row in rows if row.get("factuality") == "SUPPORTED" and row.get("expect") == "presented_iff_supported"]
    contradicted = [row for row in rows if row.get("factuality") == "CONTRADICTED" and row.get("expect") == "presented_iff_supported"]
    insufficient = [
        row
        for row in rows
        if row.get("factuality") == "INSUFFICIENT_EVIDENCE" and row.get("expect") == "presented_iff_supported"
    ]
    bad = contradicted + insufficient
    admitted_bad = [row for row in bad if row.get("established")]
    admitted_contradicted = [row for row in contradicted if row.get("established")]
    recalled = [row for row in supported if row.get("established")]
    presented_established = [row for row in rows if row.get("presented_established") and row.get("expect") == "presented_iff_supported"]
    presented_supported = [row for row in presented_established if row.get("factuality") == "SUPPORTED"]
    hypotheses = [row for row in rows if row.get("expect") == "must_be_hypothesis"]
    hypotheses_ok = [row for row in hypotheses if row.get("status") == "pass"]
    uncertainty_as_fact = [row for row in hypotheses if row.get("established")]
    return {
        "false_reality_admission_rate": {
            "value": _rate(len(admitted_bad), len(bad)),
            "numerator": len(admitted_bad),
            "denominator": len(bad),
            "denominator_means": "submitted contradicted or insufficient atoms judged presented_iff_supported",
        },
        "contradicted_claim_admission_rate": {
            "value": _rate(len(admitted_contradicted), len(contradicted)),
            "numerator": len(admitted_contradicted),
            "denominator": len(contradicted),
            "denominator_means": "submitted contradicted atoms judged presented_iff_supported",
        },
        "supported_claim_recall": {
            "value": _rate(len(recalled), len(supported)),
            "numerator": len(recalled),
            "denominator": len(supported),
            "denominator_means": "submitted supported atoms judged presented_iff_supported",
        },
        "supported_claim_precision": {
            "value": _rate(len(presented_supported), len(presented_established)),
            "numerator": len(presented_supported),
            "denominator": len(presented_established),
            "denominator_means": "atoms presented as established realities under presented_iff_supported; undefined when nothing is admitted",
        },
        "unsupported_presented_as_fact": {
            "value": len(admitted_bad),
            "denominator": len(bad),
        },
        "correct_uncertainty_representation": {
            "value": _rate(len(hypotheses_ok), len(hypotheses)),
            "numerator": len(hypotheses_ok),
            "denominator": len(hypotheses),
            "presented_as_fact": len(uncertainty_as_fact),
        },
    }


def evidence_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def count(expect: str) -> dict[str, Any]:
        chosen = [row for row in rows if row.get("expect") == expect]
        ok = [row for row in chosen if row.get("status") == "pass"]
        return {
            "value": _rate(len(ok), len(chosen)),
            "numerator": len(ok),
            "denominator": len(chosen),
        }

    return {
        "evidence_reference_accuracy": count("evidence_resolves"),
        "evidence_completeness": count("evidence_incomplete"),
        "task_association_accuracy": count("task_matches"),
        "author_attribution_accuracy": count("author_is"),
        "verification_status_note": (
            "There is no verification bit distinct from confidence. "
            "Authored routes are labeled admitted_without_gate in the case transcript "
            "and are not counted as verified."
        ),
    }


def temporal_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    current_checks = [row for row in rows if row.get("expect") in {"must_stay_current", "must_be_superseded", "must_not_be_current"}]
    current_ok = [row for row in current_checks if row.get("status") == "pass"]
    stale = [row for row in rows if row.get("expect") == "must_not_be_current"]
    stale_bad = [row for row in stale if "stale_current" in (row.get("flags") or [])]
    corrections = [row for row in rows if row.get("expect") == "must_be_superseded"]
    corrections_ok = [row for row in corrections if row.get("status") == "pass"]
    unrelated = [row for row in rows if row.get("expect") == "must_stay_current"]
    contaminated = [row for row in unrelated if "superseded_other_task" in (row.get("flags") or [])]
    return {
        "current_state_accuracy": {
            "value": _rate(len(current_ok), len(current_checks)),
            "numerator": len(current_ok),
            "denominator": len(current_checks),
            "denominator_means": "atoms whose expectation is current, superseded, or not current",
        },
        "stale_as_current_rate": {
            "value": _rate(len(stale_bad), len(stale)),
            "numerator": len(stale_bad),
            "denominator": len(stale),
        },
        "correction_success": {
            "value": _rate(len(corrections_ok), len(corrections)),
            "numerator": len(corrections_ok),
            "denominator": len(corrections),
        },
        "supersession_correctness": {
            "value": _rate(len(corrections_ok), len(corrections)),
            "numerator": len(corrections_ok),
            "denominator": len(corrections),
        },
        "cross_task_contamination": {
            "value": _rate(len(contaminated), len(unrelated)),
            "numerator": len(contaminated),
            "denominator": len(unrelated),
            "denominator_means": "claims that were required to stay current on an unrelated task",
        },
    }


def reliability_metrics(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    requests = 0
    successes = 0
    panics = 0
    handler_failures = 0
    restarts_ok = 0
    restarts = 0
    for case in case_results:
        if case.get("mode") != "daemon" or not case.get("executed"):
            continue
        for step in case.get("steps") or []:
            query = step.get("query")
            if query in {"handoff", "now"}:
                requests += 1
                if not step.get("error"):
                    successes += 1
                if step.get("error") and "unavailable" in str(step.get("error")):
                    handler_failures += 1
            if step.get("action") == "restart":
                restarts += 1
                if step.get("restart_ok"):
                    restarts_ok += 1
        if case.get("panic"):
            panics += 1
    return {
        "request_success_rate": {
            "value": _rate(successes, requests),
            "numerator": successes,
            "denominator": requests,
            "denominator_means": "handoff and now requests on executed daemon cases",
        },
        "query_handler_unavailable": handler_failures,
        "panic_count": panics,
        "restart_success": {
            "value": _rate(restarts_ok, restarts),
            "numerator": restarts_ok,
            "denominator": restarts,
        },
    }


def checker_renderer_split(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "checker_rejected": sum(1 for row in rows if row.get("checker") in {"skipped", "rejected"}),
        "checker_accepted": sum(1 for row in rows if row.get("checker") == "accepted"),
        "renderer_excluded": sum(1 for row in rows if row.get("renderer_excluded")),
        "presented_established": sum(1 for row in rows if row.get("presented_established")),
    }


def summarize(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    rows = atom_rows(case_results)
    return {
        "semantic": semantic_metrics(rows),
        "evidence_and_provenance": evidence_metrics(rows),
        "temporal": temporal_metrics(rows),
        "reliability": reliability_metrics(case_results),
        "checker_versus_renderer": checker_renderer_split(rows),
        "atom_judgments": len(rows),
    }
