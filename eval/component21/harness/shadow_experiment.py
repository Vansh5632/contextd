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


def require_unique_case_ids(cases: list[dict[str, Any]]) -> None:
    """Refuse a file where one case id is used twice.

    Evidence is stored by case id. A repeated id would attach one suite's tool
    output, including a held-out listing, to another suite's claim.
    """
    seen: dict[Any, str] = {}
    for case in cases:
        case_id = case.get("id")
        suite = str(case.get("suite") or "")
        previous = seen.get(case_id)
        if previous is not None:
            raise ValueError(f"case id {case_id!r} is shared by {previous!r} and {suite!r}")
        seen[case_id] = suite


def select_candidates(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    require_unique_case_ids(cases)
    evidence = {
        (str(case.get("suite") or ""), case.get("id")): str(case.get("evidence_text") or "")
        for case in cases
    }
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
        suite = _suite_of(cases, row["case_id"])
        chosen.append(
            {
                "id": f"{suite}/{row['case_id']}/{row['atom_id']}",
                "case_id": row["case_id"],
                "atom_id": row["atom_id"],
                "suite": suite,
                "claim_text": row["text"],
                "evidence_text": evidence.get((suite, row["case_id"]), ""),
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


def _failed_prediction(row: dict[str, Any], reason: str, *, calls: int, elapsed: float = 0.0) -> dict[str, Any]:
    return {
        **row,
        "model_label": "INSUFFICIENT_EVIDENCE",
        "evidence_quote": "",
        "reasoning": reason,
        "latency_s": elapsed,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "llm_calls": calls,
        "writes_memory": False,
        "settled": True,
    }


def run_authorized(
    rows: list[dict[str, Any]],
    *,
    model: str,
    api_key: str,
    resume: dict[str, dict[str, Any]] | None = None,
    on_row: Any = None,
) -> list[dict[str, Any]]:
    """Call OpenAI outside any database lock. One failed claim abstains and the rest continue."""
    import time

    from harness.openai_shadow import OpenAIShadow, UrllibChatTransport
    from harness.shadow import ShadowRequest

    saved = resume or {}
    if not str(api_key or "").strip():
        return [_failed_prediction(row, "missing_api_key", calls=0) for row in rows]
    classifier = OpenAIShadow(UrllibChatTransport(api_key), model, authorized=True)
    predictions: list[dict[str, Any]] = []
    for row in rows:
        prior = saved.get(str(row.get("id")))
        if prior is not None and prior.get("settled"):
            predictions.append(prior)
            if on_row is not None:
                on_row(prior)
            continue
        request = ShadowRequest(
            claim_text=str(row["claim_text"]),
            evidence_text=str(row["evidence_text"]),
            evidence_ids=(),
            deterministic_outcome=str(row.get("deterministic_outcome") or ""),
            deterministic_reason=str(row.get("deterministic_reason") or ""),
            stage=str(row.get("stage") or ""),
        )
        started = time.perf_counter()
        calls = 0
        try:
            calls = 1
            classifier.classify(request)
            elapsed = time.perf_counter() - started
            parsed = classifier.records[-1]
            result = {
                **row,
                "model_label": parsed["label"],
                "evidence_quote": parsed["evidence_quote"],
                "reasoning": parsed["reasoning"],
                "latency_s": elapsed,
                "prompt_tokens": parsed["prompt_tokens"],
                "completion_tokens": parsed["completion_tokens"],
                "llm_calls": calls,
                "writes_memory": False,
                "settled": True,
            }
        except Exception as exc:
            result = _failed_prediction(
                row,
                f"abstained: {exc.__class__.__name__}",
                calls=calls,
                elapsed=time.perf_counter() - started,
            )
        predictions.append(result)
        if on_row is not None:
            on_row(result)
    return predictions


def refuse_same_output(source, output) -> None:
    """Do not replace the experiment input with its own result file."""
    from pathlib import Path

    left = Path(source)
    right = Path(output)
    if left.resolve() == right.resolve():
        raise SystemExit(f"refusing to overwrite input {left} with the experiment output")


def write_json(path, payload: dict[str, Any]) -> None:
    """Write a complete JSON file. A second run waits, and readers never see a partial file."""
    import fcntl
    import os
    from pathlib import Path

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(target.name + ".lock")
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    text = json.dumps(payload, indent=2) + "\n"
    with lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            temporary.write_text(text, encoding="utf-8")
            os.replace(temporary, target)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            if temporary.exists():
                temporary.unlink()


def settled_by_id(path) -> dict[str, dict[str, Any]]:
    """Rows a previous run finished, keyed by id. Missing or unreadable files resume from scratch."""
    from pathlib import Path

    target = Path(path)
    if not target.exists():
        return {}
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    found: dict[str, dict[str, Any]] = {}
    for row in document.get("predictions") or []:
        if isinstance(row, dict) and row.get("settled") and row.get("id") is not None:
            found[str(row["id"])] = row
    semantic = document.get("settled_semantic") or {}
    if isinstance(semantic, dict):
        for key, row in semantic.items():
            if isinstance(row, dict) and row.get("settled"):
                found[str(row.get("id") or key)] = row
    return found


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
    refuse_same_output(args.from_results, args.output)
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
        write_json(args.output, plan)
        estimate = plan["estimate"]
        print(
            f"plan only: candidates={estimate['candidates']} "
            f"estimated_usd={estimate['usd']} authorization=spend_not_authorized "
            f"wrote {args.output}"
        )
        return
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        predictions = [_failed_prediction(row, "missing_api_key", calls=0) for row in plan["candidates"]]
        plan["predictions"] = predictions
        plan["model_called"] = False
        plan["authorization"] = "missing_api_key"
        write_json(args.output, plan)
        print(f"abstained candidates={len(predictions)} reason=missing_api_key wrote {args.output}")
        return

    kept = settled_by_id(args.output)

    def checkpoint(result: dict[str, Any]) -> None:
        kept[str(result.get("id"))] = result
        plan["partial"] = True
        plan["predictions"] = list(kept.values())
        write_json(args.output, plan)

    predictions = run_authorized(
        plan["candidates"],
        model=args.model,
        api_key=api_key,
        resume=kept,
        on_row=checkpoint,
    )
    plan["model_called"] = sum(int(row.get("llm_calls") or 0) for row in predictions) > 0
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
    plan.pop("partial", None)
    write_json(args.output, plan)
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
