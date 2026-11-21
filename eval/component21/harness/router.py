"""Risk-aware shadow router.

Literal proofs and clear contradictions stay on deterministic checks. Paraphrases,
lexical passes, and anything else ambiguous go to the atomic verifier. Missing
or conflicting evidence abstains. A model failure becomes INSUFFICIENT_EVIDENCE.
Nothing here writes memory or changes admission.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from harness.atomic import decompose, polarity_conflict
from harness.shadow_experiment import EXPERIMENT_SUITES

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_LITERAL_BEFORE = re.compile(
    rf"^(?P<left>{_IDENT}) (?:increases|increments) by (?P<amount>\d+) before (?P<right>{_IDENT})$",
    re.IGNORECASE,
)
_NEGATED = re.compile(rf"\b(?P<left>{_IDENT}) never (?:increases|increments)\b", re.IGNORECASE)


@dataclass(frozen=True)
class RouteDecision:
    route: str
    label: str | None
    reason: str
    impact: str
    evidence_quote: str = ""


def route_claim(claim: str, evidence: str) -> RouteDecision:
    """Choose a route. A deterministic label is final only when it is proved."""
    text = claim.strip()
    if _missing(evidence):
        return RouteDecision("abstain", "INSUFFICIENT_EVIDENCE", "missing_evidence", "high")
    if _conflicting(evidence):
        return RouteDecision("abstain", "INSUFFICIENT_EVIDENCE", "conflicting_evidence", "high")
    atoms = decompose(text)
    labels: list[str | None] = []
    quotes: list[str] = []
    for index, atom in enumerate(atoms):
        label, quote = _atom_decision(atom, evidence, primary=index == 0)
        labels.append(label)
        quotes.append(quote)
    if any(label == "CONTRADICTED" for label in labels):
        quote = next((item for item in quotes if item), "")
        return RouteDecision("deterministic", "CONTRADICTED", "proved_contradiction", "low", quote)
    if any(label == "INSUFFICIENT_EVIDENCE" for label in labels):
        return RouteDecision("deterministic", "INSUFFICIENT_EVIDENCE", "proved_gap", "low")
    if labels and all(label == "SUPPORTED" for label in labels):
        quote = next((item for item in quotes if item), "")
        return RouteDecision("deterministic", "SUPPORTED", "literal_proof", "low", quote)
    return RouteDecision("semantic", None, "needs_model", "low")


def apply_routes(
    rows: list[dict[str, Any]],
    semantic: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Route a batch. Semantic failures abstain and do not propagate."""
    kept = [row for row in rows if row.get("suite") in EXPERIMENT_SUITES]
    decisions = [(row, route_claim(str(row.get("claim_text") or ""), str(row.get("evidence_text") or ""))) for row in kept]
    semantic_rows = [row for row, decision in decisions if decision.route == "semantic"]
    semantic_labels: dict[str, dict[str, Any]] = {}
    semantic_error = ""
    if semantic_rows and semantic is not None:
        try:
            labeled = semantic(semantic_rows)
            semantic_labels = {str(row.get("id")): row for row in labeled}
        except Exception as exc:
            semantic_error = exc.__class__.__name__
    results = []
    for row, decision in decisions:
        if decision.route == "semantic":
            produced = semantic_labels.get(str(row.get("id")))
            if produced is None:
                label, quote, route = "INSUFFICIENT_EVIDENCE", "", "semantic_failed"
            else:
                label = str(produced.get("model_label") or "INSUFFICIENT_EVIDENCE")
                quote = str(produced.get("evidence_quote") or "")
                route = "semantic"
            results.append(
                {
                    **row,
                    "model_label": label,
                    "evidence_quote": quote,
                    "route": route,
                    "route_reason": decision.reason if produced is not None else semantic_error or "model_unavailable",
                    "impact": "high" if semantic_error else decision.impact,
                    "writes_memory": False,
                    "llm_calls": _calls(produced),
                }
            )
            continue
        results.append(
            {
                **row,
                "model_label": decision.label,
                "evidence_quote": decision.evidence_quote,
                "route": decision.route,
                "route_reason": decision.reason,
                "impact": decision.impact,
                "writes_memory": False,
                "llm_calls": 0,
            }
        )
    return results


def _calls(produced: dict[str, Any] | None) -> int:
    if not produced:
        return 0
    if "llm_calls" in produced:
        return int(produced.get("llm_calls") or 0)
    return 1 + int(produced.get("rechecked") or 0)


def _missing(evidence: str) -> bool:
    return not evidence.strip()


def _conflicting(evidence: str) -> bool:
    """Two different updates disagree. An initial assignment before the only increment does not."""
    events: dict[str, list[tuple[int, str]]] = {}

    def record(name: str, index: int, kind: str) -> None:
        events.setdefault(name.lower(), []).append((index, kind))

    for match in re.finditer(rf"({_IDENT})\s*\+=\s*(\d+)", evidence):
        record(match.group(1), match.start(), f"+={match.group(2)}")
    for match in re.finditer(rf"({_IDENT})\s*\+\+", evidence):
        record(match.group(1), match.start(), "+=1")
    for match in re.finditer(rf"({_IDENT})\s*=\s*(\d+)", evidence):
        record(match.group(1), match.start(), f"={match.group(2)}")
    for writes in events.values():
        writes.sort()
        adds = [kind for _, kind in writes if kind.startswith("+=")]
        sets = [kind for _, kind in writes if kind.startswith("=") and not kind.startswith("+=")]
        if len(set(adds)) > 1 or len(set(sets)) > 1:
            return True
        if not adds or not sets:
            continue
        first_add = min(index for index, kind in writes if kind.startswith("+="))
        if any(index > first_add for index, kind in writes if kind.startswith("=") and not kind.startswith("+=")):
            return True
    return False


def _atom_decision(atom: str, evidence: str, *, primary: bool) -> tuple[str | None, str]:
    if polarity_conflict(atom, evidence):
        return "CONTRADICTED", _window_quote(evidence, "not")
    negated = _NEGATED.search(atom)
    if negated and _shown_amount(negated.group("left"), evidence) is not None:
        return "CONTRADICTED", _increment_quote(negated.group("left"), evidence)
    literal = _LITERAL_BEFORE.search(atom.strip())
    if literal:
        shown = _shown_amount(literal.group("left"), evidence)
        claimed = int(literal.group("amount"))
        if shown is not None and shown != claimed:
            return "CONTRADICTED", _increment_quote(literal.group("left"), evidence)
        order = _order(literal.group("left"), literal.group("right"), evidence)
        if order == "reversed":
            return "CONTRADICTED", _call_quote(literal.group("right"), evidence)
        if order == "before" and shown == claimed:
            return "SUPPORTED", _increment_quote(literal.group("left"), evidence)
    if _foreign(atom) and atom not in evidence:
        return "INSUFFICIENT_EVIDENCE", ""
    if not primary and _content_missing(atom, evidence):
        return "INSUFFICIENT_EVIDENCE", ""
    return None, ""


def _shown_amount(ident: str, evidence: str) -> int | None:
    pattern = re.compile(rf"(?i)(?<![A-Za-z0-9_]){re.escape(ident)}\s*(\+=\s*(\d+)|\+\+)")
    match = pattern.search(evidence)
    if match is None:
        return None
    return 1 if match.group(2) is None else int(match.group(2))


_BRANCH = re.compile(r"\b(?:if|else|elif|switch|case|match)\b")
_FUNCTION = re.compile(r"(?m)^[ \t]*(?:async[ \t]+def|def|fn|func|function)\b")


def _order(left: str, right: str, evidence: str) -> str | None:
    increment = re.search(
        rf"(?i)(?<![A-Za-z0-9_]){re.escape(left)}\s*(?:\+=\s*\d+|\+\+)",
        evidence,
    )
    call = re.search(rf"(?i)(?<![A-Za-z0-9_]){re.escape(right)}\s*\(", evidence)
    if increment is None or call is None:
        return None
    if not _shared_straight_line(evidence, increment.start(), call.start()):
        return None
    if call.start() < increment.start():
        return "reversed"
    return "before"


def _shared_straight_line(evidence: str, left: int, right: int) -> bool:
    """Text order is execution order only inside one straight-line block."""
    start, end = sorted((left, right))
    span = evidence[start:end]
    if _FUNCTION.search(span) or _BRANCH.search(span):
        return False
    return _brace_depth(evidence, start) == _brace_depth(evidence, end)


def _brace_depth(text: str, index: int) -> int:
    snippet = text[:index]
    return snippet.count("{") - snippet.count("}")


def _call_quote(ident: str, evidence: str) -> str:
    match = re.search(rf"(?i)(?<![A-Za-z0-9_]){re.escape(ident)}\s*\(", evidence)
    return match.group(0) if match else ""


def _increment_quote(ident: str, evidence: str) -> str:
    match = re.search(
        rf"(?i)(?<![A-Za-z0-9_]){re.escape(ident)}\s*(?:\+=\s*\d+|\+\+)",
        evidence,
    )
    return match.group(0) if match else ""


def _window_quote(evidence: str, needle: str) -> str:
    index = evidence.lower().find(needle.lower())
    if index < 0:
        return ""
    return evidence[index : index + len(needle)]


def _foreign(atom: str) -> bool:
    return any(ord(ch) > 127 for ch in atom)


def _content_missing(atom: str, evidence: str) -> bool:
    words = re.findall(r"[A-Za-z]{4,}", atom.lower())
    if not words:
        return True
    folded = evidence.lower()
    return all(word not in folded for word in words)


def _load_rows(path: str) -> list[dict[str, Any]]:
    import json

    document = json.loads(open(path, encoding="utf-8").read())
    rows = document.get("predictions") or document.get("candidates") or []
    return [
        row
        for row in rows
        if row.get("suite") in EXPERIMENT_SUITES and row.get("claim_text")
    ]


def main() -> None:
    import argparse
    import os
    import sys
    from datetime import datetime, timezone
    from pathlib import Path

    from harness.shadow_experiment import refuse_same_output, settled_by_id, write_json

    parser = argparse.ArgumentParser(description="Risk-aware shadow router")
    parser.add_argument("--from-experiment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gpt-4.1-mini")
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--i-accept-spend", action="store_true")
    args = parser.parse_args()
    refuse_same_output(args.from_experiment, args.output)
    rows = _load_rows(str(args.from_experiment))
    from harness.shadow_experiment import score_predictions

    def abstain(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{**row, "model_label": "INSUFFICIENT_EVIDENCE", "evidence_quote": "", "rechecked": 0} for row in batch]

    deterministic = apply_routes(rows, abstain)
    plan = {
        "experiment": "router-5",
        "writes_memory": False,
        "heldout_excluded": True,
        "claims": len(rows),
        "routes": {
            "deterministic": sum(1 for row in deterministic if row["route"] == "deterministic"),
            "semantic": sum(1 for row in deterministic if row["route"] == "semantic"),
            "abstain": sum(1 for row in deterministic if row["route"] == "abstain"),
        },
        "deterministic_only": score_predictions(deterministic),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model_called": False,
        "authorization": "spend_not_authorized",
    }
    if not args.i_accept_spend:
        plan["predictions"] = deterministic
        write_json(args.output, plan)
        print(f"plan only routes={plan['routes']} authorization=spend_not_authorized")
        return
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        plan["predictions"] = [
            row
            if row["route"] != "semantic"
            else {**row, "model_label": "INSUFFICIENT_EVIDENCE", "evidence_quote": "", "route": "semantic_failed", "route_reason": "missing_api_key", "llm_calls": 0}
            for row in deterministic
        ]
        plan["model_called"] = False
        plan["authorization"] = "missing_api_key"
        write_json(args.output, plan)
        print(f"abstained reason=missing_api_key wrote {args.output}")
        return
    from harness.atomic import run_atomic_rows
    from harness.shadow_experiment import estimate_cost

    semantic_rows = [row for row in deterministic if row["route"] == "semantic"]
    # The deterministic pass stored no model label worth keeping for these rows.
    source = {str(row.get("id")): row for row in rows}
    batch = [source[str(row.get("id"))] for row in semantic_rows]
    resume = settled_by_id(args.output)
    plan["settled_semantic"] = dict(resume)
    passes = []
    for index in range(max(1, args.repeat)):
        print(f"semantic pass {index + 1}/{args.repeat} claims={len(batch)}", flush=True)

        def checkpoint(result: dict[str, Any]) -> None:
            plan["settled_semantic"][str(result.get("id"))] = result
            plan["partial"] = True
            write_json(args.output, plan)

        labeled = (
            run_atomic_rows(
                batch,
                model=args.model,
                api_key=api_key,
                resume=resume if index == 0 else None,
                on_row=checkpoint if index == 0 else None,
            )
            if batch
            else []
        )
        by_id = {str(row.get("id")): row for row in labeled}
        merged = []
        for row in deterministic:
            if row["route"] != "semantic":
                merged.append(row)
                continue
            produced = by_id.get(str(row.get("id")))
            if produced is None:
                merged.append({**row, "model_label": "INSUFFICIENT_EVIDENCE", "route": "semantic_failed"})
            else:
                merged.append(
                    {
                        **row,
                        "model_label": produced["model_label"],
                        "evidence_quote": produced.get("evidence_quote") or "",
                        "atoms": produced.get("atoms"),
                        "rechecked": produced.get("rechecked") or 0,
                        "llm_calls": int(produced.get("llm_calls") or 0),
                        "settled": bool(produced.get("settled")),
                        "prompt_tokens": produced.get("prompt_tokens") or 0,
                        "completion_tokens": produced.get("completion_tokens") or 0,
                        "latency_s": produced.get("latency_s") or 0,
                        "route": "semantic",
                        "writes_memory": False,
                    }
                )
        passes.append(merged)
    tokens_in = sum(int(row.get("prompt_tokens") or 0) for run in passes for row in run)
    tokens_out = sum(int(row.get("completion_tokens") or 0) for run in passes for row in run)
    calls = sum(int(row.get("llm_calls") or 0) for run in passes for row in run)
    plan.update(
        {
            "model_called": calls > 0,
            "authorization": "i-accept-spend",
            "model": args.model,
            "metrics": score_predictions(passes[0]),
            "repeat_metrics": [score_predictions(run) for run in passes],
            "llm_calls": calls,
            "usage": estimate_cost(tokens_in, tokens_out, args.model),
            "predictions": passes[0],
            "repeat_labels": [
                [{"id": row.get("id"), "model_label": row["model_label"], "route": row["route"]} for row in run]
                for run in passes
            ],
        }
    )
    plan.pop("partial", None)
    plan.pop("settled_semantic", None)
    write_json(args.output, plan)
    metrics = plan["metrics"]
    print(
        f"false_admission={metrics['false_claim_admission']['numerator']}/{metrics['false_claim_admission']['denominator']} "
        f"recall={metrics['supported_recall']['numerator']}/{metrics['supported_recall']['denominator']} "
        f"llm_calls={calls} usd={plan['usage']['usd']} wrote {args.output}",
        file=sys.stdout,
    )


if __name__ == "__main__":
    main()
