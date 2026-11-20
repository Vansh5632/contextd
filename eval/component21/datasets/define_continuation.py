"""Continuation controls and synthetic traces.

Live model trials are not run. The synthetic traces calibrate the usefulness
metrics: a solved task after a poor handoff is an agent outcome, and a failure
after a good handoff is an agent outcome.
"""

from __future__ import annotations

CONTROLS = ["none", "raw_observations", "oracle_handoff", "current_contextd", "future_candidate"]

CASES = [
    {
        "id": "c01-poor-handoff-solved",
        "suite": "continuation",
        "failure_id": None,
        "title": "Agent completes the task from a poor handoff",
        "initial_class": "control",
        "judgment": "metric",
        "mode": "synthetic",
        "deterministic": True,
        "needs_human_adjudication": False,
        "control": True,
        "handoff_quality": "poor",
        "expected_layer": "agent",
        "trace": {
            "solved": True,
            "turns_to_first_useful_action": 6,
            "incorrect_actions_caused_by_memory": 0,
            "tokens": 18000,
            "tool_calls": 9,
            "latency_ms": 1200,
            "repeated_work": 2,
        },
    },
    {
        "id": "c02-good-handoff-failed",
        "suite": "continuation",
        "failure_id": None,
        "title": "Agent fails after a high-quality handoff",
        "initial_class": "control",
        "judgment": "metric",
        "mode": "synthetic",
        "deterministic": True,
        "needs_human_adjudication": False,
        "control": True,
        "handoff_quality": "good",
        "expected_layer": "agent",
        "trace": {
            "solved": False,
            "turns_to_first_useful_action": None,
            "incorrect_actions_caused_by_memory": 0,
            "tokens": 9000,
            "tool_calls": 4,
            "latency_ms": 800,
            "repeated_work": 0,
        },
    },
    {
        "id": "c03-memory-caused-wrong-edit",
        "suite": "continuation",
        "failure_id": None,
        "title": "A poor handoff causes an incorrect action",
        "initial_class": "control",
        "judgment": "metric",
        "mode": "synthetic",
        "deterministic": True,
        "needs_human_adjudication": False,
        "control": True,
        "handoff_quality": "poor",
        "expected_layer": "memory",
        "trace": {
            "solved": False,
            "turns_to_first_useful_action": 3,
            "incorrect_actions_caused_by_memory": 2,
            "tokens": 14000,
            "tool_calls": 8,
            "latency_ms": 1500,
            "repeated_work": 3,
        },
    },
    {
        "id": "c04-live-matrix",
        "suite": "continuation",
        "failure_id": None,
        "title": "Live agent matrix",
        "initial_class": "open",
        "judgment": "open_contract",
        "mode": "documented",
        "deterministic": False,
        "needs_human_adjudication": False,
        "control": False,
        "blocked_reason": "spend_not_authorized",
        "controls": CONTROLS,
        "notes": (
            "The only experimental variable is the memory condition. "
            "Use one model, one budget, the same tools, the same environment, and the same stopping rule. "
            "Gold facts must not be recoverable by reading the workspace or by rerunning the tests. "
            "Arms: none, raw observations, oracle handoff, current contextd, and a future candidate. "
            "Repeated trials are required before any interval is treated as stable. "
            "This milestone does not launch that matrix."
        ),
        "steps": [],
        "atoms": [],
    },
]
