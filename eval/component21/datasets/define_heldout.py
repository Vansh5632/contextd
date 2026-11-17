"""Sealed held-out cases, version component21-eval-1.

These identifiers do not appear in the regression or development suites.
Do not retune the grader against this file.
"""

from __future__ import annotations

LEDGER = "fn settle(batch: Batch) {\n    ledger_slots += 1;\n    commit_batch();\n}\n"
LEDGER_BACK = "fn settle(batch: Batch) {\n    commit_batch();\n    ledger_slots += 1;\n}\n"
MESH = "function drain(node) {\n  meshQuota -= 1;\n  closeChannel();\n}\n"
QUEUE = "func pop(q *Queue) {\n    if q.depth > 0 {\n        q.depth--\n    }\n}\n"


def _case(case_id, title, output, text, factuality, justification, command):
    return {
        "id": case_id,
        "suite": "heldout",
        "failure_id": None,
        "title": title,
        "initial_class": "heldout",
        "judgment": "contract",
        "mode": "daemon",
        "deterministic": True,
        "needs_human_adjudication": True,
        "control": False,
        "session": case_id,
        "review": {
            "adjudicator": "harness author",
            "independent_second_review": False,
            "rule": "The justification cites the fixture text. A second reviewer has not signed this seal.",
        },
        "steps": [
            {
                "action": "observe",
                "kind": "tool",
                "command": "probe",
                "cwd": "/work/held",
                "exit_code": 1,
                "output": "probe failed\n",
                "bind": "probe",
            },
            {
                "action": "observe",
                "kind": "tool",
                "command": command,
                "cwd": "/work/held",
                "exit_code": 0,
                "output": output,
                "bind": "source",
            },
            {
                "action": "observe",
                "kind": "conclusion",
                "text": text,
                "bind": "conclusion",
                "support": "source",
            },
            {"action": "handoff"},
            {"action": "now"},
            {"action": "checkpoint", "name": "final"},
        ],
        "atoms": [
            {
                "id": "claim",
                "text": text,
                "factuality": factuality,
                "expect": "presented_iff_supported",
                "justification": justification,
                "provenance": "agent",
                "checkpoint": "final",
                "source_bind": "conclusion",
            }
        ],
    }


CASES = [
    _case(
        "h01-rust-supported",
        "Held-out Rust increment",
        LEDGER,
        "ledger_slots increases by 1 before commit_batch",
        "SUPPORTED",
        "settle adds 1 to ledger_slots and then calls commit_batch.",
        "sed -n 1,20p settle.rs",
    ),
    _case(
        "h02-rust-order",
        "Held-out Rust reversed calls",
        LEDGER_BACK,
        "ledger_slots increases by 1 before commit_batch",
        "CONTRADICTED",
        "settle calls commit_batch before it increments ledger_slots.",
        "sed -n 1,20p settle.rs",
    ),
    _case(
        "h03-js-decrement",
        "Held-out JavaScript decrement",
        MESH,
        "meshQuota decreases by 1 before closeChannel",
        "SUPPORTED",
        "drain subtracts 1 from meshQuota and then calls closeChannel.",
        "sed -n 1,20p drain.js",
    ),
    _case(
        "h04-js-negation",
        "Held-out negation of a decrement",
        MESH,
        "meshQuota never decreases before closeChannel",
        "CONTRADICTED",
        "drain does decrease meshQuota before closeChannel.",
        "sed -n 1,20p drain.js",
    ),
    _case(
        "h05-go-guard",
        "Held-out guarded decrement",
        QUEUE,
        "depth decreases whenever pop is called",
        "INSUFFICIENT_EVIDENCE",
        "pop decreases depth only when depth is already greater than zero.",
        "sed -n 1,20p queue.go",
    ),
    _case(
        "h06-mixed-tail",
        "Held-out unsupported Spanish tail",
        LEDGER,
        "ledger_slots increases by 1 before commit_batch. El lote fue rechazado.",
        "INSUFFICIENT_EVIDENCE",
        "The listing does not say the batch was rejected. The Spanish sentence is not established.",
        "sed -n 1,20p settle.rs",
    ),
]
