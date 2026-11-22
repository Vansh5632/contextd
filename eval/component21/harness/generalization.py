"""Experiment 6. One frozen pass over the sealed held-out claims.

The adversarial catalog is scored with no model. Held-out gold was reviewed
before this run and is not edited from the scores. Deterministic-only, the
hybrid router, and full atomic verification are the same snapshot.
"""

from __future__ import annotations

import json
from typing import Any

from harness.adversarial import evaluate
from harness.router import apply_routes

# Independent reading of datasets/define_heldout.py. Labels stay as frozen.
GOLD_REVIEW = (
    {
        "id": "h01-rust-supported",
        "decision": "keep",
        "gold": "SUPPORTED",
        "reason": "settle adds 1 to ledger_slots and then calls commit_batch in the same function.",
    },
    {
        "id": "h02-rust-order",
        "decision": "keep",
        "gold": "CONTRADICTED",
        "reason": "commit_batch runs before ledger_slots is incremented.",
    },
    {
        "id": "h03-js-decrement",
        "decision": "keep",
        "gold": "SUPPORTED",
        "reason": "drain subtracts 1 from meshQuota and then calls closeChannel.",
    },
    {
        "id": "h04-js-negation",
        "decision": "keep",
        "gold": "CONTRADICTED",
        "reason": "The listing does decrease meshQuota before closeChannel.",
    },
    {
        "id": "h05-go-guard",
        "decision": "keep",
        "gold": "INSUFFICIENT_EVIDENCE",
        "reason": "depth decreases only inside if q.depth > 0, not on every pop.",
    },
    {
        "id": "h06-mixed-tail",
        "decision": "keep",
        "gold": "INSUFFICIENT_EVIDENCE",
        "reason": "The Spanish sentence is not in the listing. The true prefix does not establish it.",
    },
)

PRODUCT_DEFECTS = (
    {
        "id": "f05-unicode-panic",
        "owner": "product",
        "fact": "The daemon query handler panics on the omega note. The shadow router does not.",
    },
    {
        "id": "f11-truncation",
        "owner": "product",
        "fact": "Handoff can race enrichment and admit a truncated prefix. The router abstains when the text says truncated.",
    },
    {
        "id": "note_claim",
        "owner": "product",
        "fact": "record_agent_claim stores an agent claim with empty evidence_ids and does not call the router.",
    },
)


def heldout_rows() -> list[dict[str, Any]]:
    """One row per sealed claim. Evidence is the cited source listing, not the probe."""
    from datasets.define_heldout import CASES

    rows = []
    for case in CASES:
        source = next(step for step in case["steps"] if step.get("bind") == "source")
        atom = case["atoms"][0]
        gold = atom["factuality"]
        rows.append(
            {
                "id": case["id"],
                "case_id": case["id"],
                "suite": "heldout",
                "claim_text": atom["text"],
                "evidence_text": source["output"],
                "gold": gold,
                "gold_role": "supported" if gold == "SUPPORTED" else "negative_control",
                "justification": atom["justification"],
                "writes_memory": False,
            }
        )
    return rows


def _abstain(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {**row, "model_label": "INSUFFICIENT_EVIDENCE", "evidence_quote": "", "llm_calls": 0, "rechecked": 0}
        for row in batch
    ]


def _usage(rows: list[dict[str, Any]], model: str) -> dict[str, Any]:
    from harness.shadow_experiment import estimate_cost

    prompt = sum(int(row.get("prompt_tokens") or 0) for row in rows)
    completion = sum(int(row.get("completion_tokens") or 0) for row in rows)
    calls = sum(int(row.get("llm_calls") or 0) for row in rows)
    return {"llm_calls": calls, **estimate_cost(prompt, completion, model)}


def main() -> None:
    import argparse
    import os
    import subprocess
    import sys
    from datetime import datetime, timezone
    from pathlib import Path

    from harness.shadow_experiment import refuse_same_output, score_predictions, write_json

    parser = argparse.ArgumentParser(description="Experiment 6 held-out validation")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gpt-4.1-mini")
    parser.add_argument("--i-accept-spend", action="store_true")
    args = parser.parse_args()
    refuse_same_output(Path("datasets/heldout/cases.json"), args.output)
    safety = evaluate()
    failures = [row["id"] for row in safety if not row["matches"]]
    rows = heldout_rows()
    deterministic = apply_routes(rows, _abstain, suites={"heldout"})
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        check=False,
    )
    plan: dict[str, Any] = {
        "experiment": "generalization-6",
        "writes_memory": False,
        "tuned_on_heldout": False,
        "heldout_runs": 1,
        "model_called": False,
        "authorization": "spend_not_authorized",
        "git_revision": (revision.stdout or "").strip(),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gold_review": list(GOLD_REVIEW),
        "product_defects": list(PRODUCT_DEFECTS),
        "adversarial": {"passed": len(safety) - len(failures), "failed": failures, "rows": safety},
        "heldout_claims": len(rows),
        "deterministic_only": score_predictions(deterministic),
        "deterministic_routes": {
            "deterministic": sum(1 for row in deterministic if row["route"] == "deterministic"),
            "semantic": sum(1 for row in deterministic if row["route"] == "semantic"),
            "abstain": sum(1 for row in deterministic if row["route"] == "abstain"),
        },
    }
    if failures:
        write_json(args.output, plan)
        print(f"adversarial failures={failures}", file=sys.stderr)
        raise SystemExit(1)
    if not args.i_accept_spend:
        plan["predictions_deterministic"] = deterministic
        write_json(args.output, plan)
        print(
            f"plan only adversarial={plan['adversarial']['passed']} "
            f"heldout_routes={plan['deterministic_routes']} authorization=spend_not_authorized"
        )
        return
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        plan["authorization"] = "missing_api_key"
        write_json(args.output, plan)
        raise SystemExit("OPENAI_API_KEY is empty")
    from harness.atomic import run_atomic_rows

    def semantic(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return run_atomic_rows(batch, model=args.model, api_key=api_key)

    print(f"hybrid held-out claims={len(rows)}", flush=True)
    hybrid = apply_routes(rows, semantic, suites={"heldout"})
    print(f"atomic held-out claims={len(rows)}", flush=True)
    atomic = run_atomic_rows(rows, model=args.model, api_key=api_key)
    plan.update(
        {
            "model_called": True,
            "authorization": "i-accept-spend",
            "model": args.model,
            "hybrid": score_predictions(hybrid),
            "atomic": score_predictions(atomic),
            "hybrid_usage": _usage(hybrid, args.model),
            "atomic_usage": _usage(atomic, args.model),
            "hybrid_predictions": hybrid,
            "atomic_predictions": atomic,
        }
    )
    write_json(args.output, plan)
    hybrid_metrics = plan["hybrid"]
    print(
        f"hybrid false_admission={hybrid_metrics['false_claim_admission']['numerator']}/"
        f"{hybrid_metrics['false_claim_admission']['denominator']} "
        f"recall={hybrid_metrics['supported_recall']['numerator']}/"
        f"{hybrid_metrics['supported_recall']['denominator']} "
        f"usd={plan['hybrid_usage']['usd']} wrote {args.output}",
        file=sys.stdout,
    )


if __name__ == "__main__":
    main()
