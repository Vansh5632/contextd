"""Offline semantic-verification experiment.

Candidates come from regression and development fixtures. Held-out cases are
ignored. The planner never calls a model. A live OpenAI run is a separate
step that requires ``--i-accept-spend`` and still cannot write memory.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from harness.diagnostics import atom_rows

BAD = frozenset({"CONTRADICTED", "INSUFFICIENT_EVIDENCE"})
EXPERIMENT_SUITES = frozenset({"regression", "development"})

# USD per 1,000,000 tokens. OpenAI list prices for the mini models.
# A live run multiplies these rates by the API's reported token counts.
PRICES_USD_PER_MILLION = {
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
}


class SpendNotAuthorized(RuntimeError):
    """Raised when a model call is attempted without an explicit spend flag."""


def cohort_of(row: dict[str, Any]) -> str | None:
    reason = row.get("reason")
    outcome = row.get("outcome")
    if reason == "lexical_pass" and outcome == "accepted":
        return "lexical_pass_accept"
    if reason in {"residue", "order"} and outcome in {"skipped", "dropped"}:
        return "residue_or_order_skip"
    if row.get("factuality") == "SUPPORTED" and not row.get("established") and not reason:
        parent = row.get("parent_reason")
        parent_outcome = row.get("parent_outcome")
        if parent in {"residue", "order"} and parent_outcome in {"skipped", "dropped"}:
            return "unsplit_supported"
        if parent == "lexical_pass" and parent_outcome == "accepted":
            return "absorbed_supported"
    return None


def assign_owner(row: dict[str, Any]) -> str:
    """Who should own the claim before any model result is trusted.

    ``ai`` means the deterministic reason does not separate true from false.
    ``rule`` means the checker already refused a negative control.
    ``escalate`` means the span never became its own clause, so a model label
    on the fragment would not be what production stored.
    """
    cohort = row.get("cohort")
    if cohort == "lexical_pass_accept":
        return "ai"
    if cohort == "residue_or_order_skip" and row.get("gold") == "SUPPORTED":
        return "ai"
    if cohort == "residue_or_order_skip":
        return "rule"
    if cohort in {"unsplit_supported", "absorbed_supported"}:
        return "escalate"
    return "rule"


def select_candidates(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence = {case.get("id"): str(case.get("evidence_text") or "") for case in cases}
    chosen: list[dict[str, Any]] = []
    for row in atom_rows(cases, suites=set(EXPERIMENT_SUITES)):
        if row.get("expect") != "presented_iff_supported":
            continue
        if row.get("factuality") not in {"SUPPORTED", *BAD}:
            continue
        cohort = cohort_of(row)
        if cohort is None:
            continue
        gold = str(row["factuality"])
        chosen.append(
            {
                "id": f"{row['case_id']}/{row['atom_id']}",
                "case_id": row["case_id"],
                "atom_id": row["atom_id"],
                "suite": _suite_of(cases, row["case_id"]),
                "claim_text": row["text"],
                "evidence_text": evidence.get(row["case_id"], ""),
                "gold": gold,
                "gold_role": "supported" if gold == "SUPPORTED" else "negative_control",
                "cohort": cohort,
                "deterministic_reason": row.get("reason") or row.get("parent_reason") or "",
                "deterministic_outcome": row.get("outcome") or row.get("parent_outcome") or "",
                "stage": row.get("stage") or "",
                "established": bool(row.get("established")),
                "presented": bool(row.get("presented")),
                "writes_memory": False,
            }
        )
    chosen.sort(key=lambda item: (item["suite"], item["case_id"], str(item["atom_id"])))
    return chosen


def _suite_of(cases: list[dict[str, Any]], case_id: Any) -> str:
    for case in cases:
        if case.get("id") == case_id:
            return str(case.get("suite") or "")
    return ""


def build_request_payload(model: str, row: dict[str, Any]) -> dict[str, Any]:
    """Prompt contains the claim and the tool output only."""
    claim = str(row["claim_text"])
    evidence = str(row["evidence_text"])
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
                    "You verify one claim against one tool output. "
                    "SUPPORTED means the output entails the claim. "
                    "CONTRADICTED means the output entails the negation. "
                    "INSUFFICIENT_EVIDENCE means the output does neither. "
                    "evidence_quote must be an exact contiguous substring of the tool output, "
                    "or empty when the label is INSUFFICIENT_EVIDENCE. "
                    "Use only the tool output. reasoning is one or two sentences."
                ),
            },
            {
                "role": "user",
                "content": f"Tool output:\n{evidence}\n\nClaim:\n{claim}",
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "claim_verdict", "strict": True, "schema": schema},
        },
    }


def parse_structured_output(body: dict[str, Any]) -> dict[str, Any]:
    message = body["choices"][0]["message"]
    content = message.get("content") or "{}"
    data = json.loads(content)
    usage = body.get("usage") or {}
    return {
        "label": data["label"],
        "evidence_quote": data.get("evidence_quote") or "",
        "reasoning": data.get("reasoning") or "",
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
    }


def gate_quote(label: str, quote: str, evidence: str) -> tuple[str, str, bool]:
    """Same quote rule as ``run_shadow``: a citation must be copied from the evidence."""
    if quote and quote not in evidence:
        return "INSUFFICIENT_EVIDENCE", "", False
    if not quote and label != "INSUFFICIENT_EVIDENCE":
        return "INSUFFICIENT_EVIDENCE", "", False
    return label, quote, True


def _rate(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": None if denominator == 0 else numerator / denominator,
    }


def score_predictions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    judged = []
    for row in rows:
        label, quote, ok = gate_quote(
            str(row.get("model_label") or "INSUFFICIENT_EVIDENCE"),
            str(row.get("evidence_quote") or ""),
            str(row.get("evidence_text") or ""),
        )
        judged.append({**row, "gated_label": label, "gated_quote": quote, "evidence_ok": ok})
    bad = [row for row in judged if row.get("gold") in BAD]
    supported = [row for row in judged if row.get("gold") == "SUPPORTED"]
    admitted = [row for row in judged if row["gated_label"] == "SUPPORTED"]
    false_admitted = [row for row in bad if row["gated_label"] == "SUPPORTED"]
    recalled = [row for row in supported if row["gated_label"] == "SUPPORTED"]
    precise = [row for row in admitted if row.get("gold") == "SUPPORTED"]
    flips = [
        row
        for row in bad
        if row.get("gold_role") == "negative_control" and row["gated_label"] == "SUPPORTED"
    ]
    return {
        "writes_memory": False,
        "false_claim_admission": _rate(len(false_admitted), len(bad)),
        "supported_recall": _rate(len(recalled), len(supported)),
        "precision": _rate(len(precise), len(admitted)),
        "evidence_valid": _rate(sum(1 for row in judged if row["evidence_ok"]), len(judged)),
        "negative_control_flips": len(flips),
        "judged": len(judged),
    }


def checker_predictions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The deterministic checker admits a claim when the daemon stored it."""
    predicted = []
    for row in rows:
        label = "SUPPORTED" if row.get("established") else "INSUFFICIENT_EVIDENCE"
        predicted.append(
            {
                **row,
                "model_label": label,
                "evidence_quote": "" if label == "INSUFFICIENT_EVIDENCE" else _checker_quote(row),
            }
        )
    return predicted


def _checker_quote(row: dict[str, Any]) -> str:
    evidence = str(row.get("evidence_text") or "")
    if not evidence:
        return ""
    line = evidence.splitlines()[0] if evidence.splitlines() else evidence
    return line if line in evidence else ""


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def estimate_cost(prompt_tokens: int, completion_tokens: int, model: str) -> dict[str, Any]:
    prices = PRICES_USD_PER_MILLION.get(model)
    if prices is None:
        return {"model": model, "usd": None, "priced": False, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
    usd = (prompt_tokens * prices["input"] + completion_tokens * prices["output"]) / 1_000_000
    return {
        "model": model,
        "priced": True,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "usd": usd,
        "prices_usd_per_million": prices,
    }


def _estimate_row(model: str, row: dict[str, Any]) -> tuple[int, int]:
    payload = build_request_payload(model, row)
    prompt = json.dumps(payload["messages"])
    return estimate_tokens(prompt), 80


def plan_experiment(cases: list[dict[str, Any]], *, model: str = "gpt-4.1-mini") -> dict[str, Any]:
    rows = select_candidates(cases)
    prompt_tokens = 0
    completion_tokens = 0
    for row in rows:
        prompt, completion = _estimate_row(model, row)
        prompt_tokens += prompt
        completion_tokens += completion
    owners: dict[str, int] = {}
    for row in rows:
        owner = assign_owner(row)
        row["proposed_owner"] = owner
        owners[owner] = owners.get(owner, 0) + 1
    digest = hashlib.sha256(
        json.dumps(
            [{"id": row["id"], "claim_text": row["claim_text"], "gold": row["gold"]} for row in rows],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return {
        "model_called": False,
        "authorization": "spend_not_authorized",
        "writes_memory": False,
        "heldout_excluded": True,
        "model": None,
        "planned_model": model,
        "candidate_sha256": digest,
        "candidates": rows,
        "estimate": {
            "candidates": len(rows),
            **estimate_cost(prompt_tokens, completion_tokens, model),
            "token_estimate": "four characters per token; completion budget is 80 tokens",
        },
        "checker": score_predictions(checker_predictions(rows)),
        "owners": owners,
        "suites": sorted(EXPERIMENT_SUITES),
    }


def run_authorized(rows: list[dict[str, Any]], *, model: str, api_key: str) -> list[dict[str, Any]]:
    """Call OpenAI outside any database lock. The caller must already have authorized spend."""
    import time

    from harness.openai_shadow import OpenAIShadow, UrllibChatTransport
    from harness.shadow import ShadowRequest

    classifier = OpenAIShadow(UrllibChatTransport(api_key), model, authorized=True)
    predictions: list[dict[str, Any]] = []
    for row in rows:
        request = ShadowRequest(
            claim_text=str(row["claim_text"]),
            evidence_text=str(row["evidence_text"]),
            evidence_ids=(),
            deterministic_outcome=str(row.get("deterministic_outcome") or ""),
            deterministic_reason=str(row.get("deterministic_reason") or ""),
            stage=str(row.get("stage") or ""),
        )
        started = time.perf_counter()
        classifier.classify(request)
        elapsed = time.perf_counter() - started
        parsed = classifier.records[-1]
        predictions.append(
            {
                **row,
                "model_label": parsed["label"],
                "evidence_quote": parsed["evidence_quote"],
                "reasoning": parsed["reasoning"],
                "latency_s": elapsed,
                "prompt_tokens": parsed["prompt_tokens"],
                "completion_tokens": parsed["completion_tokens"],
                "writes_memory": False,
            }
        )
    return predictions


def main() -> None:
    import argparse
    import os
    import subprocess
    import sys
    from datetime import datetime, timezone
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Shadow semantic verification experiment")
    parser.add_argument("--from-results", type=Path, required=True, help="Daemon harness JSON. Held-out cases in it are ignored.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gpt-4.1-mini")
    parser.add_argument(
        "--i-accept-spend",
        action="store_true",
        help="Authorize paid OpenAI calls. Without this flag the command only writes the plan.",
    )
    args = parser.parse_args()
    document = json.loads(args.from_results.read_text())
    cases = document.get("cases") or []
    plan = plan_experiment(cases, model=args.model)
    plan["source_results"] = str(args.from_results)
    plan["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        check=False,
    )
    plan["git_revision"] = (revision.stdout or "").strip()
    if not args.i_accept_spend:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(plan, indent=2) + "\n")
        estimate = plan["estimate"]
        print(
            f"plan only: candidates={estimate['candidates']} "
            f"estimated_usd={estimate['usd']} authorization=spend_not_authorized "
            f"wrote {args.output}"
        )
        return
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is empty")
    predictions = run_authorized(plan["candidates"], model=args.model, api_key=api_key)
    plan["model_called"] = True
    plan["authorization"] = "i-accept-spend"
    plan["model"] = args.model
    plan["predictions"] = predictions
    plan["model_metrics"] = score_predictions(predictions)
    tokens_in = sum(int(row.get("prompt_tokens") or 0) for row in predictions)
    tokens_out = sum(int(row.get("completion_tokens") or 0) for row in predictions)
    plan["usage"] = estimate_cost(tokens_in, tokens_out, args.model)
    plan["latency_s"] = {
        "sum": sum(float(row.get("latency_s") or 0) for row in predictions),
        "n": len(predictions),
    }
    args.output.write_text(json.dumps(plan, indent=2) + "\n")
    metrics = plan["model_metrics"]
    print(
        f"model={args.model} false_admission={metrics['false_claim_admission']['numerator']}/"
        f"{metrics['false_claim_admission']['denominator']} "
        f"recall={metrics['supported_recall']['numerator']}/{metrics['supported_recall']['denominator']} "
        f"usd={plan['usage']['usd']} wrote {args.output}",
        file=sys.stdout,
    )


if __name__ == "__main__":
    main()
