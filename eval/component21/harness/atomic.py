"""Split a sentence into atoms and aggregate their labels in code.

The model may label atoms. It does not decide the sentence. A sentence is
SUPPORTED only when every atom is SUPPORTED. This module does not touch a
database.
"""

from __future__ import annotations

import json
import re
from typing import Any

_BOUNDARY = re.compile(r"[。！？；;]+|(?<=[A-Za-z0-9])[.!?](?=\s|$)")
_CONDITIONAL = re.compile(r"(?:^|\s+)(only when|unless|if)\s+", re.IGNORECASE)
_BECAUSE = re.compile(r"\s+because\s+", re.IGNORECASE)
_FOCUS = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def decompose(text: str) -> list[str]:
    """Split punctuation, causal clauses, then a conditional tail.

    ``before`` stays inside one atom. It is one order assertion, not two facts
    that can be approved separately.
    """
    pieces = _boundaries(text.strip())
    atoms: list[str] = []
    for piece in pieces:
        for causal in _because(piece):
            atoms.extend(_conditional(causal))
    cleaned = [atom.strip() for atom in atoms if atom.strip()]
    return cleaned or [text.strip()]


def coverage_report(text: str) -> dict[str, Any]:
    """Which independent assertions were extracted from one sentence."""
    atoms = decompose(text)
    lower = f" {text.lower()} "
    markers = []
    if re.search(r"[。；;]", text):
        markers.append("clause_boundary")
    if re.search(r"\b(if|only when|unless)\b", text, re.IGNORECASE):
        markers.append("condition")
    if re.search(r"\b(never|not|doesn't|does not)\b", text, re.IGNORECASE):
        markers.append("negation")
    if " because " in lower:
        markers.append("because")
    if re.search(r"\bbefore\b", text, re.IGNORECASE):
        markers.append("before")
    checks = {
        "clause_boundary": "clause_boundary" not in markers or len(atoms) >= 2,
        "condition": "condition" not in markers
        or any(re.match(r"(?i)(if|only when|unless)\b", atom) for atom in atoms),
        "negation": "negation" not in markers
        or any(re.search(r"(?i)\b(never|not|doesn't|does not)\b", atom) for atom in atoms),
        "because": "because" not in markers or len(_because(text)) >= 2,
        "before": "before" not in markers or any(re.search(r"(?i)\bbefore\b", atom) for atom in atoms),
    }
    return {
        "atoms": atoms,
        "atom_count": len(atoms),
        "markers": markers,
        "checks": checks,
        "covered": all(checks.values()),
    }


def aggregate(labels: list[str]) -> str:
    if not labels:
        return "INSUFFICIENT_EVIDENCE"
    if any(label == "CONTRADICTED" for label in labels):
        return "CONTRADICTED"
    if all(label == "SUPPORTED" for label in labels):
        return "SUPPORTED"
    return "INSUFFICIENT_EVIDENCE"


def polarity_conflict(atom: str, evidence: str) -> bool:
    """True when a condition atom's negation disagrees with the evidence.

    ``if idle`` conflicts with ``if not item.idle``. ``unless idle`` does not:
    ``unless`` already means the negated condition. Only a ``not`` that
    modifies the focus word counts.
    """
    parsed = _condition(atom)
    if parsed is None:
        return False
    negated, focus = parsed
    return _focus_negated(evidence, focus) != negated


def verify_atoms(atoms: list[dict[str, Any]], evidence: str) -> dict[str, Any]:
    """Apply the quote gate and the polarity veto, then aggregate."""
    judged: list[dict[str, Any]] = []
    for atom in atoms:
        label = str(atom.get("label") or "INSUFFICIENT_EVIDENCE")
        quote = str(atom.get("evidence_quote") or "")
        if quote and quote not in evidence:
            label, quote = "INSUFFICIENT_EVIDENCE", ""
        if not quote and label != "INSUFFICIENT_EVIDENCE":
            label, quote = "INSUFFICIENT_EVIDENCE", ""
        if polarity_conflict(str(atom.get("text") or ""), evidence):
            label = "CONTRADICTED"
        judged.append(
            {
                "text": atom.get("text") or "",
                "label": label,
                "evidence_quote": quote,
                "reasoning": atom.get("reasoning") or "",
            }
        )
    sentence = aggregate([item["label"] for item in judged])
    quote = _sentence_quote(sentence, judged, evidence)
    return {
        "label": sentence,
        "evidence_quote": quote,
        "atoms": judged,
        "writes_memory": False,
    }


def uncertain_atoms(atoms: list[dict[str, Any]], evidence: str) -> list[dict[str, Any]]:
    """Atoms the first pass left insufficient, excluding polarity contradictions."""
    chosen = []
    for atom in atoms:
        if atom.get("label") != "INSUFFICIENT_EVIDENCE":
            continue
        if polarity_conflict(str(atom.get("text") or ""), evidence):
            continue
        chosen.append(atom)
    return chosen


def apply_second_pass(
    atoms: list[dict[str, Any]],
    second: dict[str, dict[str, Any]],
    evidence: str,
) -> list[dict[str, Any]]:
    """Replace an abstention only when the new label cites the evidence.

    A missing quote, a quote that is not in the evidence, or another abstention
    leaves the atom insufficient. This never invents SUPPORTED.
    """
    revised = []
    for atom in atoms:
        text = str(atom.get("text") or "")
        if atom.get("label") != "INSUFFICIENT_EVIDENCE" or polarity_conflict(text, evidence):
            label = "CONTRADICTED" if polarity_conflict(text, evidence) else atom.get("label")
            revised.append({**atom, "label": label, "reverified": False})
            continue
        proposal = second.get(text)
        if not proposal:
            revised.append({**atom, "reverified": True, "second_label": None})
            continue
        label = str(proposal.get("label") or "INSUFFICIENT_EVIDENCE")
        quote = str(proposal.get("evidence_quote") or "")
        if label != "INSUFFICIENT_EVIDENCE" and (not quote or quote not in evidence):
            label, quote = "INSUFFICIENT_EVIDENCE", ""
        if polarity_conflict(text, evidence):
            label = "CONTRADICTED"
        revised.append(
            {
                **atom,
                "label": label,
                "evidence_quote": quote if label != "INSUFFICIENT_EVIDENCE" else "",
                "reasoning": proposal.get("reasoning") or atom.get("reasoning") or "",
                "reverified": True,
                "first_label": "INSUFFICIENT_EVIDENCE",
                "second_label": proposal.get("label"),
            }
        )
    return revised


def build_reverify_payload(model: str, atom: str, evidence: str) -> dict[str, Any]:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "label": {
                "type": "string",
                "enum": ["SUPPORTED", "CONTRADICTED", "INSUFFICIENT_EVIDENCE"],
            },
            "evidence_quote": {"type": "string"},
            "reasoning": {"type": "string"},
        },
        "required": ["label", "evidence_quote", "reasoning"],
    }
    return {
        "model": model,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": (
                    "The first pass abstained on one atom. Look only at the tool output. "
                    "Return SUPPORTED only when an exact substring entails the atom. "
                    "Return CONTRADICTED only when an exact substring entails its negation. "
                    "Otherwise return INSUFFICIENT_EVIDENCE and an empty evidence_quote. "
                    "Abstaining is a correct answer. Do not choose SUPPORTED in order to avoid abstaining."
                ),
            },
            {
                "role": "user",
                "content": f"Tool output:\n{evidence}\n\nAtom:\n{atom}",
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "atom_recheck", "strict": True, "schema": schema},
        },
    }


def gold_audit() -> list[dict[str, str]]:
    """Independent reading of the two development cases Experiment 3 missed.

    Labels are not changed. d03 stays SUPPORTED because it describes the same
    listing as the supported identity claim: ``quota_left += 1`` followed by
    ``release_permit()``. The callee body is not shown; the call is the event,
    which is the convention already used for that listing. d14 stays SUPPORTED
    because the Rust listing is the same increment before the same call.
    """
    return [
        {
            "case_id": "d03-paraphrase",
            "gold": "SUPPORTED",
            "decision": "keep",
            "evidence": "def release(self, permit):\n    self.quota_left += 1\n    self.release_permit()\n",
            "finding": (
                "quota_left += 1 is the counter growing by one. "
                "The next statement is the release_permit call. "
                "The function body is not in the listing. "
                "The same two statements make the identity claim supported, so the paraphrase stays supported."
            ),
        },
        {
            "case_id": "d14-rust",
            "gold": "SUPPORTED",
            "decision": "keep",
            "evidence": "fn release(permit: i32) {\n    quota_left += 1;\n    release_permit();\n}\n",
            "finding": (
                "quota_left += 1 is written before release_permit(). "
                "The gold label is supported. A pass that abstains is model variance, not a bad label."
            ),
        },
    ]


def align_model_atoms(expected: list[str], returned: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One verdict per decomposed atom. A missing atom is insufficient."""
    by_text = {str(item.get("text") or "").strip(): item for item in returned}
    aligned = []
    for atom in expected:
        found = by_text.get(atom)
        if found is None:
            aligned.append(
                {
                    "text": atom,
                    "label": "INSUFFICIENT_EVIDENCE",
                    "evidence_quote": "",
                    "reasoning": "the model did not label this atom",
                }
            )
        else:
            aligned.append(found)
    return aligned


def build_atomic_payload(model: str, claim: str, evidence: str, atoms: list[str]) -> dict[str, Any]:
    numbered = "\n".join(f"{index}. {atom}" for index, atom in enumerate(atoms, start=1))
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "atoms": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "text": {"type": "string"},
                        "label": {
                            "type": "string",
                            "enum": ["SUPPORTED", "CONTRADICTED", "INSUFFICIENT_EVIDENCE"],
                        },
                        "evidence_quote": {"type": "string"},
                        "reasoning": {"type": "string"},
                    },
                    "required": ["text", "label", "evidence_quote", "reasoning"],
                },
            }
        },
        "required": ["atoms"],
    }
    return {
        "model": model,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Label each atom on its own against the tool output. "
                    "Do not merge atoms and do not mark the whole sentence. "
                    "SUPPORTED means the output entails that atom. "
                    "CONTRADICTED means the output entails its negation. "
                    "INSUFFICIENT_EVIDENCE means the output does neither. "
                    "A condition atom 'if P' is SUPPORTED only when the output's condition "
                    "has the same polarity. 'if P' is CONTRADICTED by 'if not P' or 'not <expr>.P'. "
                    "'if not P' is CONTRADICTED by a positive 'if P'. "
                    "A negated atom (never, not, doesn't) is CONTRADICTED when the output shows the positive event. "
                    "A paraphrase of code that is present is SUPPORTED: a counter growing by one, or a name "
                    "such as release_permit describing that action. "
                    "Text in another language, or an extra clause naming a mechanism the output does not show, "
                    "is INSUFFICIENT_EVIDENCE. "
                    "evidence_quote is an exact substring of the tool output, or empty for INSUFFICIENT_EVIDENCE. "
                    "Copy each atom's text exactly."
                ),
            },
            {
                "role": "user",
                "content": f"Tool output:\n{evidence}\n\nAtoms:\n{numbered}",
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "atom_verdicts", "strict": True, "schema": schema},
        },
    }


def parse_atomic_output(body: dict[str, Any]) -> dict[str, Any]:
    message = body["choices"][0]["message"]
    data = json.loads(message.get("content") or "{}")
    usage = body.get("usage") or {}
    return {
        "atoms": data.get("atoms") or [],
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
    }


def _boundaries(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    for match in _BOUNDARY.finditer(text):
        piece = text[start : match.start()].strip()
        if piece:
            parts.append(piece)
        start = match.end()
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts or ([text] if text else [])


def _because(clause: str) -> list[str]:
    parts = [part.strip() for part in _BECAUSE.split(clause) if part.strip()]
    if len(parts) <= 1:
        return [clause]
    # The causal sentence is its own atom. True clauses do not prove the link.
    return [clause.strip(), *parts]


def _conditional(clause: str) -> list[str]:
    match = _CONDITIONAL.search(clause)
    if match is None:
        return [clause]
    prefix = clause[: match.start()].strip()
    if not prefix:
        body = clause[match.end() :].strip()
        condition_body = body.split(",", 1)[0].strip()
        condition = f"{match.group(1)} {condition_body}".strip()
        pieces = [clause.strip()]
        if condition and condition.lower() != clause.strip().lower():
            pieces.append(condition)
        return pieces
    tail = clause[match.start() :].strip()
    # Keep the full clause. The condition is an extra atom that must also pass,
    # so one true clause cannot carry a reversed or unsupported condition.
    return [piece for piece in (clause.strip(), tail) if piece]


def _condition(atom: str) -> tuple[bool, str] | None:
    match = re.match(r"(?i)(if|only when|unless)\s+(.*)$", atom.strip())
    if match is None:
        return None
    marker = match.group(1).lower()
    body = match.group(2).strip()
    body_negated = re.match(r"(?i)not\b", body) is not None
    # ``unless P`` is the negated condition. ``unless not P`` is not.
    negated = (marker == "unless") != body_negated
    focus_body = re.sub(r"(?i)^not\s+", "", body).strip()
    words = _FOCUS.findall(focus_body)
    if not words:
        return None
    return negated, words[-1].lower()


def _focus_negated(evidence: str, focus: str) -> bool:
    """True when ``not`` applies to this focus, as in ``not idle`` or ``not item.idle``."""
    pattern = re.compile(rf"(?i)\bnot\s+(?:[A-Za-z_][A-Za-z0-9_]*\s*\.\s*)?{re.escape(focus)}\b")
    return pattern.search(evidence) is not None


def label_statement(model_atoms: list[dict[str, Any]], evidence: str, expected: list[str]) -> dict[str, Any]:
    """Align the model's atom labels, then aggregate in code."""
    return verify_atoms(align_model_atoms(expected, model_atoms), evidence)


def _abstained(row: dict[str, Any], reason: str, *, calls: int, elapsed: float = 0.0) -> dict[str, Any]:
    return {
        **row,
        "model_label": "INSUFFICIENT_EVIDENCE",
        "evidence_quote": "",
        "atoms": [],
        "atom_count": 0,
        "decomposed": decompose(str(row.get("claim_text") or "")),
        "rechecked": 0,
        "reasoning": reason,
        "latency_s": elapsed,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "llm_calls": calls,
        "writes_memory": False,
        "settled": True,
    }


def run_atomic_rows(
    rows: list[dict[str, Any]],
    *,
    model: str,
    api_key: str,
    transport: Any = None,
    resume: dict[str, dict[str, Any]] | None = None,
    on_row: Any = None,
) -> list[dict[str, Any]]:
    """One pass. A failed call abstains that claim and the batch continues.

    ``resume`` skips row ids that a previous run already settled, so a retry
    does not pay for them again. The transport never receives a database.
    """
    import time

    from harness.openai_shadow import UrllibChatTransport

    saved = resume or {}
    if transport is None:
        if not str(api_key or "").strip():
            return [_abstained(row, "missing_api_key", calls=0) for row in rows]
        transport = UrllibChatTransport(api_key)
    labeled = []
    for row in rows:
        prior = saved.get(str(row.get("id")))
        if prior is not None and prior.get("settled"):
            labeled.append(prior)
            if on_row is not None:
                on_row(prior)
            continue
        claim = str(row["claim_text"])
        evidence = str(row.get("evidence_text") or "")
        atoms = decompose(claim)
        print(f"  {row.get('case_id')} atoms={len(atoms)}", flush=True)
        calls = 0
        started = time.perf_counter()
        try:
            payload = build_atomic_payload(model, claim, evidence, atoms)
            calls += 1
            parsed = parse_atomic_output(transport.complete(payload))
        except Exception as exc:
            result = _abstained(
                row,
                f"abstained: {exc.__class__.__name__}",
                calls=calls,
                elapsed=time.perf_counter() - started,
            )
            labeled.append(result)
            if on_row is not None:
                on_row(result)
            continue
        elapsed = time.perf_counter() - started
        prompt_tokens = parsed["prompt_tokens"]
        completion_tokens = parsed["completion_tokens"]
        verdict = label_statement(parsed["atoms"], evidence, atoms)
        pending = uncertain_atoms(verdict["atoms"], evidence)
        second: dict[str, dict[str, Any]] = {}
        for atom in pending:
            print(f"    recheck {atom.get('text')}", flush=True)
            recheck = build_reverify_payload(model, str(atom.get("text") or ""), evidence)
            started_recheck = time.perf_counter()
            try:
                calls += 1
                body = transport.complete(recheck)
                message = body["choices"][0]["message"]
                data = json.loads(message.get("content") or "{}")
                usage = body.get("usage") or {}
                second[str(atom.get("text") or "")] = data
                prompt_tokens += int(usage.get("prompt_tokens") or 0)
                completion_tokens += int(usage.get("completion_tokens") or 0)
            except Exception as exc:
                second[str(atom.get("text") or "")] = {
                    "label": "INSUFFICIENT_EVIDENCE",
                    "evidence_quote": "",
                    "reasoning": f"recheck abstained: {exc}",
                }
            elapsed += time.perf_counter() - started_recheck
        revised = apply_second_pass(verdict["atoms"], second, evidence)
        sentence = aggregate([str(item["label"]) for item in revised])
        result = {
            **row,
            "model_label": sentence,
            "evidence_quote": _sentence_quote(sentence, revised, evidence),
            "atoms": revised,
            "atom_count": len(atoms),
            "decomposed": atoms,
            "rechecked": len(pending),
            "reasoning": " | ".join(f"{item['text']}: {item['label']}" for item in revised),
            "latency_s": elapsed,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "llm_calls": calls,
            "writes_memory": False,
            "settled": True,
        }
        labeled.append(result)
        if on_row is not None:
            on_row(result)
    return labeled


def _sentence_quote(sentence: str, atoms: list[dict[str, Any]], evidence: str) -> str:
    if sentence == "SUPPORTED":
        for atom in atoms:
            quote = str(atom.get("evidence_quote") or "")
            if quote and quote in evidence:
                return quote
        return ""
    for atom in atoms:
        if atom.get("label") != "SUPPORTED":
            quote = str(atom.get("evidence_quote") or "")
            if quote and quote in evidence:
                return quote
    return ""


def _load_rows(path: str) -> list[dict[str, Any]]:
    from harness.shadow_experiment import EXPERIMENT_SUITES

    document = json.loads(open(path, encoding="utf-8").read())
    rows = document.get("predictions") or document.get("candidates") or []
    kept = []
    for row in rows:
        if row.get("suite") not in EXPERIMENT_SUITES:
            continue
        if row.get("claim_text") and row.get("gold"):
            kept.append(row)
    return kept


def _agreement(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> dict[str, Any]:
    pairs = zip(left, right)
    same = 0
    total = 0
    changed = []
    for first, second in pairs:
        total += 1
        if first["model_label"] == second["model_label"]:
            same += 1
        else:
            changed.append(
                {
                    "id": first.get("id") or first.get("case_id"),
                    "first": first["model_label"],
                    "second": second["model_label"],
                }
            )
    return {
        "same": same,
        "total": total,
        "value": None if total == 0 else same / total,
        "changed": changed,
    }


def main() -> None:
    import argparse
    import os
    import sys
    from datetime import datetime, timezone
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Atomic shadow verification")
    parser.add_argument("--from-experiment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gpt-4.1-mini")
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--i-accept-spend", action="store_true")
    from harness.shadow_experiment import refuse_same_output, settled_by_id, write_json

    args = parser.parse_args()
    refuse_same_output(args.from_experiment, args.output)
    rows = _load_rows(str(args.from_experiment))
    plan = {
        "experiment": "atomic-3",
        "model_called": False,
        "authorization": "spend_not_authorized",
        "writes_memory": False,
        "heldout_excluded": True,
        "claims": len(rows),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "decomposed": [
            {"id": row.get("id"), "claim_text": row["claim_text"], "atoms": decompose(str(row["claim_text"]))}
            for row in rows
        ],
    }
    if not args.i_accept_spend:
        write_json(args.output, plan)
        print(f"plan only: claims={len(rows)} authorization=spend_not_authorized")
        return
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        plan["predictions"] = [_abstained(row, "missing_api_key", calls=0) for row in rows]
        plan["model_called"] = False
        plan["authorization"] = "missing_api_key"
        write_json(args.output, plan)
        print(f"abstained claims={len(rows)} reason=missing_api_key wrote {args.output}")
        return
    from harness.shadow_experiment import estimate_cost, score_predictions

    resume = settled_by_id(args.output)
    passes = []
    kept = dict(resume)

    def checkpoint(result: dict[str, Any]) -> None:
        kept[str(result.get("id"))] = result
        plan["partial"] = True
        plan["predictions"] = list(kept.values())
        write_json(args.output, plan)

    for index in range(max(1, args.repeat)):
        print(f"pass {index + 1}/{args.repeat}", flush=True)
        passes.append(
            run_atomic_rows(
                rows,
                model=args.model,
                api_key=api_key,
                resume=resume if index == 0 else None,
                on_row=checkpoint if index == 0 else None,
            )
        )
        plan["predictions"] = passes[-1]
    primary = passes[0]
    tokens_in = sum(int(row.get("prompt_tokens") or 0) for run in passes for row in run)
    tokens_out = sum(int(row.get("completion_tokens") or 0) for run in passes for row in run)
    calls = sum(int(row.get("llm_calls") or 0) for run in passes for row in run)
    latency = [float(row.get("latency_s") or 0) for run in passes for row in run]
    split = sum(1 for row in primary if row["atom_count"] > 1)
    plan.update(
        {
            "model_called": calls > 0,
            "authorization": "i-accept-spend",
            "model": args.model,
            "metrics": score_predictions(primary),
            "repeat_metrics": [score_predictions(run) for run in passes],
            "consistency": _agreement(passes[0], passes[1]) if len(passes) > 1 else None,
            "atomic_coverage": {
                "claims": len(primary),
                "split_claims": split,
                "atoms": sum(row["atom_count"] for row in primary),
            },
            "usage": estimate_cost(tokens_in, tokens_out, args.model),
            "llm_calls": calls,
            "latency_s": {
                "sum": sum(latency),
                "n": len(latency),
                "mean": 0 if not latency else sum(latency) / len(latency),
            },
            "predictions": primary,
            "repeat_labels": [
                [{"id": row.get("id"), "model_label": row["model_label"]} for row in run] for run in passes
            ],
        }
    )
    plan.pop("partial", None)
    write_json(args.output, plan)
    metrics = plan["metrics"]
    print(
        f"false_admission={metrics['false_claim_admission']['numerator']}/{metrics['false_claim_admission']['denominator']} "
        f"recall={metrics['supported_recall']['numerator']}/{metrics['supported_recall']['denominator']} "
        f"precision={metrics['precision']['numerator']}/{metrics['precision']['denominator']} "
        f"usd={plan['usage']['usd']} wrote {args.output}",
        file=sys.stdout,
    )


if __name__ == "__main__":
    main()
