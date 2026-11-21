"""Join deterministic reason codes to frozen gold labels.

False claims the checker accepted and correct claims it skipped are separate
lists. Held-out cases are left out of both. Shadow labels are attached beside
those lists and are not admission decisions.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from harness.shadow import AbstainingShadow, ShadowClassifier, run_shadow, shadow_requests

BAD = frozenset({"CONTRADICTED", "INSUFFICIENT_EVIDENCE"})
STAGE_RANK = {"admit": 3, "apply_extraction": 2, "keep_clause": 1}
HEADLINE_SUITES = frozenset({"regression"})


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def case_traces(case: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [dict(row) for row in case.get("verifier_diagnostics") or []]
    for step in case.get("steps") or []:
        for row in step.get("diagnostics") or []:
            rows.append(dict(row))
    return rows


def match_trace(atom_text: str, traces: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Exact text only. A longer accepted sentence must not explain a shorter atom."""
    target = _norm(atom_text)
    if not target:
        return None
    exact = [
        trace
        for trace in traces
        if _norm(str(trace.get("text") or "")) == target
    ]
    if not exact:
        return None
    exact.sort(key=lambda trace: STAGE_RANK.get(str(trace.get("stage")), 0))
    return exact[-1]


def parent_trace(atom_text: str, traces: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Enclosing candidate, used only when the atom itself has no exact trace."""
    target = _norm(atom_text)
    if not target:
        return None
    parents = []
    for trace in traces:
        text = _norm(str(trace.get("text") or ""))
        if text and text != target and target in text:
            parents.append(trace)
    if not parents:
        return None
    parents.sort(
        key=lambda trace: (
            len(_norm(str(trace.get("text") or ""))),
            -STAGE_RANK.get(str(trace.get("stage")), 0),
        )
    )
    return parents[0]


def bucket(atom: dict[str, Any]) -> str:
    if atom.get("expect") != "presented_iff_supported":
        return "out_of_scope"
    factuality = atom.get("factuality")
    established = bool(atom.get("established"))
    checker = atom.get("checker")
    if factuality in BAD and (checker == "accepted" or established):
        return "false_claim_accepted"
    if factuality == "SUPPORTED" and not established and checker in {"skipped", "absent", "rejected"}:
        return "correct_claim_skipped"
    if factuality == "SUPPORTED" and established:
        return "correct_claim_accepted"
    if factuality in BAD and not established:
        return "false_claim_uncovered"
    return "other"


def _evidence_ids(case: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for step in case.get("steps") or []:
        event_id = step.get("event_id")
        if step.get("action") == "observe" and event_id and event_id not in found:
            found.append(str(event_id))
    return found


def atom_rows(cases: list[dict[str, Any]], *, suites: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        if case.get("suite") not in suites:
            continue
        if case.get("mode") != "daemon" or case.get("judgment") != "contract":
            continue
        traces = case_traces(case)
        for atom in case.get("atoms") or []:
            if atom.get("status") == "blocked":
                continue
            text = str(atom.get("text") or "")
            trace = match_trace(text, traces)
            parent = None if trace is not None else parent_trace(text, traces)
            row = {
                "case_id": case.get("id"),
                "atom_id": atom.get("id"),
                "text": atom.get("text"),
                "factuality": atom.get("factuality"),
                "expect": atom.get("expect"),
                "checker": atom.get("checker"),
                "established": bool(atom.get("established")),
                "presented": bool(atom.get("presented_established")),
                "bucket": bucket(atom),
                "stage": None if trace is None else trace.get("stage"),
                "outcome": None if trace is None else trace.get("outcome"),
                "reason": None if trace is None else trace.get("reason"),
                "relation": None if trace is None else trace.get("relation"),
                "parent_reason": None if parent is None else parent.get("reason"),
                "parent_outcome": None if parent is None else parent.get("outcome"),
            }
            rows.append(row)
    return rows


def _reason_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        reason = row.get("reason") or "missing"
        counts[f"{row['bucket']}:{reason}"] += 1
    return dict(sorted(counts.items()))


def _shadow_block(
    cases: list[dict[str, Any]],
    *,
    suites: set[str],
    classifier: ShadowClassifier,
) -> dict[str, Any]:
    labels: list[dict[str, Any]] = []
    for case in cases:
        if case.get("suite") not in suites or case.get("mode") != "daemon":
            continue
        requests = shadow_requests(
            case_traces(case),
            str(case.get("evidence_text") or ""),
            _evidence_ids(case),
        )
        for request, verdict in zip(requests, run_shadow(classifier, requests), strict=True):
            if verdict.writes_memory:
                raise RuntimeError("shadow verdict attempted to write memory")
            labels.append(
                {
                    "case_id": case.get("id"),
                    "claim_text": request.claim_text,
                    "deterministic_outcome": request.deterministic_outcome,
                    "deterministic_reason": request.deterministic_reason,
                    "stage": request.stage,
                    "shadow_label": verdict.label,
                    "evidence_quote": verdict.evidence_quote,
                    "writes_memory": False,
                }
            )
    return {
        "model": None,
        "writes_memory": False,
        "classifier": type(classifier).__name__,
        "candidates": len(labels),
        "labels": labels,
    }


def summarize_diagnostics(
    cases: list[dict[str, Any]],
    classifier: ShadowClassifier | None = None,
) -> dict[str, Any]:
    """Regression gold versus deterministic reasons, plus an inert shadow pass.

    Development is reported separately. Held-out cases are not read and are
    not included in either list.
    """
    chosen = classifier or AbstainingShadow()
    regression = atom_rows(cases, suites=set(HEADLINE_SUITES))
    development = atom_rows(cases, suites={"development"})
    heldout_present = [case.get("id") for case in cases if case.get("suite") == "heldout"]

    def pack(rows: list[dict[str, Any]]) -> dict[str, Any]:
        accepted_false = [row for row in rows if row["bucket"] == "false_claim_accepted"]
        skipped_true = [row for row in rows if row["bucket"] == "correct_claim_skipped"]
        return {
            "atoms": len(rows),
            "with_reason": sum(1 for row in rows if row.get("reason")),
            "missing_reason": [row["case_id"] for row in rows if not row.get("reason")],
            "false_claims_accepted": accepted_false,
            "correct_claims_skipped": skipped_true,
            "reason_counts": _reason_counts(rows),
        }

    return {
        "heldout_excluded": True,
        "heldout_cases_ignored": heldout_present,
        "regression": pack(regression),
        "development": pack(development),
        "shadow": _shadow_block(cases, suites=set(HEADLINE_SUITES), classifier=chosen),
    }
