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
_LITERAL_DECREASE = re.compile(
    rf"^(?P<left>{_IDENT}) (?:decreases|decrements) by (?P<amount>\d+) before (?P<right>{_IDENT})$",
    re.IGNORECASE,
)
_NEGATED = re.compile(rf"\b(?P<left>{_IDENT}) never (?:increases|increments)\b", re.IGNORECASE)
_NEGATED_DECREASE = re.compile(
    rf"\b(?P<left>{_IDENT}) never (?:decreases|decrements)\b",
    re.IGNORECASE,
)
_INCOMPLETE = re.compile(r"\btruncated\b|\boutput removed\b", re.IGNORECASE)


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
    if _incomplete(evidence):
        return RouteDecision("abstain", "INSUFFICIENT_EVIDENCE", "incomplete_evidence", "high")
    if _conflicting(evidence):
        return RouteDecision("abstain", "INSUFFICIENT_EVIDENCE", "conflicting_evidence", "high")
    visible = _visible_code(evidence)
    atoms = decompose(text)
    labels: list[str | None] = []
    quotes: list[str] = []
    for index, atom in enumerate(atoms):
        label, quote = _atom_decision(atom, evidence, visible, primary=index == 0)
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
    *,
    suites: frozenset[str] | set[str] | None = None,
) -> list[dict[str, Any]]:
    """Route a batch. Semantic failures abstain and do not propagate.

    The default suites are regression and development. Held-out rows stay out
    unless the caller passes that suite explicitly for a frozen one-shot run.
    """
    allowed = EXPERIMENT_SUITES if suites is None else frozenset(suites)
    kept = [row for row in rows if row.get("suite") in allowed]
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
                    "prompt_tokens": int(produced.get("prompt_tokens") or 0) if produced else 0,
                    "completion_tokens": int(produced.get("completion_tokens") or 0) if produced else 0,
                    "latency_s": float(produced.get("latency_s") or 0) if produced else 0.0,
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


def _incomplete(evidence: str) -> bool:
    return _INCOMPLETE.search(evidence) is not None


def _conflicting(evidence: str) -> bool:
    """Two different updates disagree. An initial assignment before the only change does not."""
    visible = _visible_code(evidence)
    events: dict[str, list[tuple[int, str]]] = {}

    def record(name: str, index: int, kind: str) -> None:
        events.setdefault(name.lower(), []).append((index, kind))

    for match in re.finditer(rf"({_IDENT})\s*\+=\s*(\d+)", visible):
        record(match.group(1), match.start(), f"+={match.group(2)}")
    for match in re.finditer(rf"({_IDENT})\s*\+\+", visible):
        record(match.group(1), match.start(), "+=1")
    for match in re.finditer(rf"({_IDENT})\s*-=\s*(\d+)", visible):
        record(match.group(1), match.start(), f"-={match.group(2)}")
    for match in re.finditer(rf"({_IDENT})\s*--", visible):
        record(match.group(1), match.start(), "-=1")
    for match in re.finditer(rf"({_IDENT})\s*=\s*(\d+)", visible):
        record(match.group(1), match.start(), f"={match.group(2)}")
    for writes in events.values():
        writes.sort()
        adds = [kind for _, kind in writes if kind.startswith("+=")]
        subs = [kind for _, kind in writes if kind.startswith("-=")]
        sets = [kind for _, kind in writes if kind[:1] == "="]
        if len(set(adds)) > 1 or len(set(subs)) > 1 or len(set(sets)) > 1:
            return True
        if adds and subs:
            return True
        changes = [(index, kind) for index, kind in writes if kind[:1] in {"+", "-"}]
        if not changes or not sets:
            continue
        first_change = min(index for index, _ in changes)
        if any(index > first_change for index, kind in writes if kind[:1] == "="):
            return True
    return False


def _atom_decision(atom: str, evidence: str, visible: str, *, primary: bool) -> tuple[str | None, str]:
    if polarity_conflict(atom, visible):
        return "CONTRADICTED", _window_quote(evidence, "not")
    negated = _NEGATED.search(atom)
    if negated and _amounts(negated.group("left"), visible, "increase"):
        return "CONTRADICTED", _change_quote(evidence, visible, negated.group("left"), "increase")
    negated_drop = _NEGATED_DECREASE.search(atom)
    if negated_drop and _amounts(negated_drop.group("left"), visible, "decrease"):
        return "CONTRADICTED", _change_quote(evidence, visible, negated_drop.group("left"), "decrease")
    literal = _LITERAL_BEFORE.search(atom.strip())
    proved = _literal_change(literal, evidence, visible, "increase") if literal else None
    if proved is not None:
        return proved
    dropped = _LITERAL_DECREASE.search(atom.strip())
    proved = _literal_change(dropped, evidence, visible, "decrease") if dropped else None
    if proved is not None:
        return proved
    if _foreign(atom) and atom not in evidence:
        return "INSUFFICIENT_EVIDENCE", ""
    if not primary and _content_missing(atom, visible):
        return "INSUFFICIENT_EVIDENCE", ""
    return None, ""


def _literal_change(
    literal: re.Match[str],
    evidence: str,
    visible: str,
    direction: str,
) -> tuple[str, str] | None:
    """Prove one straight-line update, or refuse it. Ambiguous text is not supported."""
    amounts = _amounts(literal.group("left"), visible, direction)
    claimed = int(literal.group("amount"))
    quote = _change_quote(evidence, visible, literal.group("left"), direction)
    if len(amounts) == 1 and amounts[0] != claimed:
        return "CONTRADICTED", quote
    order = _order(literal.group("left"), literal.group("right"), visible)
    if order == "reversed" and amounts:
        return "CONTRADICTED", _call_quote(evidence, visible, literal.group("right"))
    if order == "before" and amounts == [claimed]:
        return "SUPPORTED", quote
    return None


_BRANCH = re.compile(r"\b(?:if|else|elif|for|while|switch|case|match|try|except|catch|loop)\b")
_FUNCTION = re.compile(r"(?m)^[ \t]*(?:async[ \t]+def|def|fn|func|function)\b")
_CONTROL_LINE = re.compile(
    r"^[ \t]*(?:if|else|elif|for|while|switch|case|match|try|except|catch|loop)\b",
    re.IGNORECASE,
)
_FUNCTION_LINE = re.compile(r"^[ \t]*(?:async[ \t]+def|def|fn|func|function)\b")


def _amounts(ident: str, visible: str, direction: str) -> list[int]:
    amounts = []
    for match in _change_pattern(ident, direction).finditer(visible):
        amounts.append(1 if match.group(1) is None else int(match.group(1)))
    return amounts


def _change_pattern(ident: str, direction: str) -> re.Pattern[str]:
    if direction == "increase":
        body = r"\+=\s*(\d+)|\+\+"
    else:
        body = r"-=\s*(\d+)|--"
    return re.compile(rf"(?i)(?<![A-Za-z0-9_]){re.escape(ident)}\s*(?:{body})")


def _change_quote(original: str, visible: str, ident: str, direction: str) -> str:
    match = _change_pattern(ident, direction).search(visible)
    if match is None:
        return ""
    return original[match.start() : match.end()]


def _order(left: str, right: str, visible: str) -> str | None:
    increment = _change_pattern(left, "increase").search(visible) or _change_pattern(left, "decrease").search(visible)
    call = re.search(rf"(?i)(?<![A-Za-z0-9_]){re.escape(right)}\s*\(", visible)
    if increment is None or call is None:
        return None
    if not _straight_proof(visible, increment.start(), call.start()):
        return None
    if call.start() < increment.start():
        return "reversed"
    return "before"


def _straight_proof(visible: str, left: int, right: int) -> bool:
    """True only when both sites are siblings directly under one function.

    A loop, a branch, a comment, or another function is not a proof. Those
    claims stay on the semantic route.
    """
    if not _shared_straight_line(visible, left, right):
        return False
    lines = _line_table(visible)
    earlier = _line_index(lines, min(left, right))
    later = _line_index(lines, max(left, right))
    indent = _indent(lines[earlier][1])
    if _indent(lines[later][1]) != indent:
        return False
    for offset, line in lines[earlier : later + 1]:
        del offset
        if not line.strip():
            continue
        if _indent(line) != indent or _CONTROL_LINE.match(line):
            return False
    header = _enclosing_header(lines, earlier, indent)
    return header is not None and _FUNCTION_LINE.match(header) is not None


def _line_table(text: str) -> list[tuple[int, str]]:
    rows = []
    offset = 0
    for line in text.splitlines(keepends=True):
        rows.append((offset, line))
        offset += len(line)
    return rows or [(0, text)]


def _line_index(lines: list[tuple[int, str]], index: int) -> int:
    for position, (offset, line) in enumerate(lines):
        if offset <= index < offset + max(len(line), 1):
            return position
    return max(0, len(lines) - 1)


def _indent(line: str) -> str:
    stripped = line.lstrip(" \t")
    return line[: len(line) - len(stripped)]


def _enclosing_header(lines: list[tuple[int, str]], index: int, indent: str) -> str | None:
    for _, line in reversed(lines[:index]):
        if not line.strip():
            continue
        if len(_indent(line)) < len(indent):
            return line
    return None


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


def _call_quote(original: str, visible: str, ident: str) -> str:
    match = re.search(rf"(?i)(?<![A-Za-z0-9_]){re.escape(ident)}\s*\(", visible)
    if match is None:
        return ""
    return original[match.start() : match.end()]


def _visible_code(evidence: str) -> str:
    """Blank comments and string contents without moving the remaining columns."""
    chars = list(evidence)
    index = 0
    limit = len(evidence)
    while index < limit:
        pair = evidence[index : index + 2]
        if pair in {"//", "/*"} or evidence[index] == "#":
            if pair == "/*":
                chars[index] = chars[index + 1] = " "
                index += 2
                while index < limit and evidence[index - 1 : index + 1] != "*/":
                    if evidence[index] != "\n":
                        chars[index] = " "
                    index += 1
                if index < limit:
                    chars[index] = " "
                    index += 1
                continue
            while index < limit and evidence[index] != "\n":
                chars[index] = " "
                index += 1
            continue
        if evidence[index] in {'"', "'", "`"}:
            quote = evidence[index]
            chars[index] = " "
            index += 1
            while index < limit and evidence[index] != quote:
                if evidence[index] == "\\" and index + 1 < limit:
                    chars[index] = " "
                    chars[index + 1] = " "
                    index += 2
                    continue
                if evidence[index] != "\n":
                    chars[index] = " "
                index += 1
            if index < limit:
                chars[index] = " "
                index += 1
            continue
        index += 1
    return "".join(chars)


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
