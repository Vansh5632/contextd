"""Layered metrics. These are not collapsed into one quality score.

A skipped false claim is uncovered. It is not a refusal, and it is not in the
false-admission denominator. A run that admits nothing has supported recall 0
and an undefined precision. Held-out atoms are not pooled with regression.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

BAD = frozenset({"CONTRADICTED", "INSUFFICIENT_EVIDENCE"})
EVIDENCE_DECISIONS = frozenset({"evidence_match", "evidence_mismatch", "evidence_missing"})


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _covered(row: dict[str, Any]) -> bool:
    if "covered" in row and row["covered"] is not None:
        return bool(row["covered"])
    if row.get("established"):
        return True
    return row.get("checker") in {"accepted", "rejected"}


def _contract_case(case: dict[str, Any]) -> bool:
    return case.get("mode") == "daemon" and case.get("judgment") == "contract" and bool(case.get("executed"))


def atom_rows(case_results: list[dict[str, Any]], *, suites: set[str] | None = None) -> list[dict[str, Any]]:
    rows = []
    for case in case_results:
        if not _contract_case(case):
            continue
        if suites is not None and case.get("suite") not in suites:
            continue
        for atom in case.get("atoms") or []:
            if atom.get("status") == "blocked":
                continue
            row = dict(atom)
            row["case_id"] = case["id"]
            row["suite"] = case.get("suite")
            rows.append(row)
    return rows


def semantic_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    supported = [
        row
        for row in rows
        if row.get("factuality") == "SUPPORTED" and row.get("expect") == "presented_iff_supported"
    ]
    bad = [
        row
        for row in rows
        if row.get("factuality") in BAD and row.get("expect") == "presented_iff_supported"
    ]
    covered_bad = [row for row in bad if _covered(row)]
    admitted_bad = [row for row in covered_bad if row.get("established")]
    uncovered_bad = [row for row in bad if not _covered(row)]
    recalled = [row for row in supported if row.get("established")]
    presented_established = [
        row
        for row in rows
        if row.get("presented_established") and row.get("expect") == "presented_iff_supported"
    ]
    presented_supported = [row for row in presented_established if row.get("factuality") == "SUPPORTED"]
    hypotheses = [row for row in rows if row.get("expect") == "must_be_hypothesis"]
    hypotheses_ok = [row for row in hypotheses if row.get("status") == "pass"]
    uncertainty_as_fact = [row for row in hypotheses if row.get("established")]
    uncertainty_dropped = [row for row in hypotheses if "uncertainty_dropped" in (row.get("flags") or [])]
    return {
        "supported_claim_recall": {
            "value": _rate(len(recalled), len(supported)),
            "numerator": len(recalled),
            "denominator": len(supported),
            "denominator_means": "submitted supported atoms judged presented_iff_supported, including claims the checker skipped",
        },
        "false_claim_admission": {
            "value": _rate(len(admitted_bad), len(covered_bad)),
            "numerator": len(admitted_bad),
            "denominator": len(covered_bad),
            "uncovered": len(uncovered_bad),
            "denominator_means": "contradicted or insufficient atoms the checker accepted or rejected, or that were stored; skipped claims are excluded",
        },
        "supported_claim_precision": {
            "value": _rate(len(presented_supported), len(presented_established)),
            "numerator": len(presented_supported),
            "denominator": len(presented_established),
            "denominator_means": "atoms presented as established realities under presented_iff_supported; undefined when nothing is admitted",
        },
        "uncovered_false_claims": len(uncovered_bad),
        "unsupported_presented_as_fact": {
            "value": len(admitted_bad),
            "denominator": len(covered_bad),
        },
        "correct_uncertainty_representation": {
            "value": _rate(len(hypotheses_ok), len(hypotheses)),
            "numerator": len(hypotheses_ok),
            "denominator": len(hypotheses),
            "presented_as_fact": len(uncertainty_as_fact),
            "dropped": len(uncertainty_dropped),
            "denominator_means": "must_be_hypothesis atoms; a drop is not a false-claim admission",
        },
    }


def _expect_rate(rows: list[dict[str, Any]], expect: str) -> dict[str, Any]:
    chosen = [row for row in rows if row.get("expect") == expect]
    ok = [row for row in chosen if row.get("status") == "pass"]
    return {
        "value": _rate(len(ok), len(chosen)),
        "numerator": len(ok),
        "denominator": len(chosen),
    }


def evidence_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    decided = [row for row in rows if row.get("evidence") in EVIDENCE_DECISIONS]
    matched = [row for row in decided if row.get("evidence") == "evidence_match"]
    return {
        "evidence_reference_accuracy": {
            "value": _rate(len(matched), len(decided)),
            "numerator": len(matched),
            "denominator": len(decided),
            "denominator_means": "stored claims with a snippet and evidence bind; uncovered claims are excluded",
        },
        "task_association_accuracy": _expect_rate(rows, "task_matches"),
        "author_attribution_accuracy": _expect_rate(rows, "author_is"),
        "verification_status_note": (
            "There is no verification bit distinct from confidence. "
            "Authored routes are labeled admitted_without_gate in the case transcript "
            "and are not counted as verified."
        ),
    }


def _unique_claims(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((str(row.get("case_id")), str(row.get("text"))), []).append(row)
    return list(grouped.values())


def temporal_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    stale = [row for row in rows if row.get("expect") == "must_not_be_current"]
    stale_bad = [row for row in stale if "stale_current" in (row.get("flags") or [])]
    supersession = [
        row
        for row in rows
        if row.get("expect") == "must_be_superseded" and row.get("metric_role") != "restart_state"
    ]
    supersession_ok = [row for row in supersession if row.get("status") == "pass"]
    cross = [row for row in rows if row.get("metric_role") == "cross_task"]
    contaminated = [
        row
        for row in cross
        if row.get("status") == "fail" or "superseded_other_task" in (row.get("flags") or [])
    ]
    unrelated_groups = _unique_claims([row for row in rows if row.get("metric_role") == "unrelated_retention"])
    unrelated_ok = [group for group in unrelated_groups if all(row.get("status") == "pass" for row in group)]
    restart_rows = [row for row in rows if row.get("metric_role") == "restart_state"]
    restart_ok = [row for row in restart_rows if row.get("status") == "pass"]
    return {
        "stale_as_current_rate": {
            "value": _rate(len(stale_bad), len(stale)),
            "numerator": len(stale_bad),
            "denominator": len(stale),
            "denominator_means": "atoms that must not stay current",
        },
        "supersession_correctness": {
            "value": _rate(len(supersession_ok), len(supersession)),
            "numerator": len(supersession_ok),
            "denominator": len(supersession),
            "denominator_means": "must_be_superseded atoms other than post-restart copies",
        },
        "cross_task_contamination": {
            "value": _rate(len(contaminated), len(cross)),
            "numerator": len(contaminated),
            "denominator": len(cross),
            "denominator_means": "atoms tagged cross_task",
        },
        "unrelated_retention": {
            "value": _rate(len(unrelated_ok), len(unrelated_groups)),
            "numerator": len(unrelated_ok),
            "denominator": len(unrelated_groups),
            "denominator_means": "one row per unrelated claim, not per checkpoint",
        },
        "restart_state": {
            "value": _rate(len(restart_ok), len(restart_rows)),
            "numerator": len(restart_ok),
            "denominator": len(restart_rows),
            "denominator_means": "atoms tagged restart_state; process restart is counted separately",
        },
    }


def _blocked_reason(case: dict[str, Any]) -> str | None:
    if case.get("status") != "blocked":
        return None
    return str(case.get("blocked_reason") or "unspecified")


def reliability_metrics(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    requests = 0
    successes = 0
    panics: list[str] = []
    handler_failures = 0
    restarts_ok = 0
    restarts = 0
    transport: list[dict[str, Any]] = []
    harness_errors = 0
    blocked: Counter[str] = Counter()
    incomplete = {"not_admitted": 0, "admitted_as_reality": 0, "situation_not_reached": 0}
    for case in case_results:
        if case.get("harness_error"):
            harness_errors += 1
        reason = _blocked_reason(case)
        if reason:
            blocked[reason] += 1
        if case.get("panic"):
            panics.append(case["id"])
        if case.get("mode") != "daemon" or not case.get("executed"):
            for atom in case.get("atoms") or []:
                _count_incomplete(atom, incomplete)
            continue
        for step in case.get("steps") or []:
            query = step.get("query")
            if query in {"handoff", "now", "observe", "agent_claim", "user_claim", "intent"}:
                requests += 1
                if not step.get("error") and not step.get("transport_error"):
                    successes += 1
            if step.get("transport_error"):
                transport.append(
                    {
                        "case_id": case["id"],
                        "action": step.get("action"),
                        "error": step.get("transport_error"),
                    }
                )
            if step.get("error") and "unavailable" in str(step.get("error")):
                handler_failures += 1
            if step.get("action") == "restart":
                restarts += 1
                if step.get("restart_ok"):
                    restarts_ok += 1
        for atom in case.get("atoms") or []:
            _count_incomplete(atom, incomplete)
    return {
        "request_success_rate": {
            "value": _rate(successes, requests),
            "numerator": successes,
            "denominator": requests,
            "denominator_means": "observe, handoff, now, and authored requests on executed daemon cases",
        },
        "transport_errors": transport,
        "query_handler_unavailable": handler_failures,
        "panics": panics,
        "panic_count": len(panics),
        "process_restart": {
            "value": _rate(restarts_ok, restarts),
            "numerator": restarts_ok,
            "denominator": restarts,
            "denominator_means": "daemon processes that came back after restart; this is not state correctness",
        },
        "harness_errors": harness_errors,
        "blocked_reasons": dict(blocked),
        "incomplete_observations": incomplete,
    }


def _count_incomplete(atom: dict[str, Any], incomplete: dict[str, int]) -> None:
    flags = atom.get("flags") or []
    if atom.get("disposition") == "incomplete" or "incomplete_not_admitted" in flags:
        incomplete["not_admitted"] += 1
    if atom.get("expect") == "evidence_incomplete" and atom.get("established"):
        incomplete["admitted_as_reality"] += 1
    if "situation_not_reached" in flags:
        incomplete["situation_not_reached"] += 1


def checker_renderer_split(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "accepted": sum(1 for row in rows if row.get("checker") == "accepted"),
        "rejected": sum(1 for row in rows if row.get("checker") == "rejected"),
        "skipped": sum(1 for row in rows if row.get("checker") == "skipped"),
        "absent": sum(1 for row in rows if row.get("checker") == "absent"),
        "renderer_excluded": sum(1 for row in rows if row.get("renderer_excluded")),
        "presented_established": sum(1 for row in rows if row.get("presented_established")),
    }


def _population(cases: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"pass": 0, "fail": 0, "blocked": 0, "vacuous": 0, "incomplete": 0, "total": len(cases)}
    for case in cases:
        status = case.get("status")
        if status in counts:
            counts[status] += 1
    return counts


def populations(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    def choose(predicate) -> dict[str, int]:
        return _population([case for case in case_results if predicate(case)])

    return {
        "daemon_contract": choose(
            lambda case: case.get("mode") == "daemon" and case.get("suite") in {"regression", "development"}
        ),
        "heldout": choose(lambda case: case.get("suite") == "heldout"),
        "vacuous_rejection": choose(lambda case: case.get("status") == "vacuous"),
        "incomplete_observation": choose(lambda case: case.get("status") == "incomplete"),
        "grader_self_test": choose(lambda case: case.get("mode") == "grader"),
        "synthetic_continuation": choose(lambda case: case.get("mode") == "synthetic"),
        "documented": choose(lambda case: case.get("mode") == "documented"),
    }


def summarize(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    regression = atom_rows(case_results, suites={"regression"})
    development = atom_rows(case_results, suites={"development"})
    heldout = atom_rows(case_results, suites={"heldout"})
    daemon = regression + development
    return {
        "populations": populations(case_results),
        "semantic": {
            "regression": semantic_metrics(regression),
            "development": semantic_metrics(development),
        },
        "heldout": {
            "semantic": semantic_metrics(heldout),
            "evidence_and_provenance": evidence_metrics(heldout),
            "case_ids": sorted({row["case_id"] for row in heldout}),
        },
        "evidence_and_provenance": evidence_metrics(daemon),
        "temporal": temporal_metrics(daemon),
        "reliability": reliability_metrics(case_results),
        "checker_versus_renderer": checker_renderer_split(daemon),
        "atom_judgments": len(daemon),
        "headline_case_ids": sorted({row["case_id"] for row in daemon}),
    }
