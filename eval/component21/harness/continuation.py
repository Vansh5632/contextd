"""Continuation-usefulness metrics for synthetic traces.

These scores describe a scripted agent trace. They do not launch a model.
A solved task after a poor handoff is attributed to the agent. A failure after
a good handoff is attributed to the agent as well. Memory is credited only
when the handoff quality and the outcome move together.
"""

from __future__ import annotations

from typing import Any


def score_trace(case: dict[str, Any]) -> dict[str, Any]:
    trace = case["trace"]
    quality = case["handoff_quality"]
    solved = bool(trace["solved"])
    if quality == "poor" and solved:
        layer = "agent"
        memory_credited = False
        reason = "the task was completed from a poor handoff"
    elif quality == "good" and not solved:
        layer = "agent"
        memory_credited = False
        reason = "the agent failed after a high-quality handoff"
    elif quality == "good" and solved:
        layer = "memory"
        memory_credited = True
        reason = "the scripted trace completes only alongside the good handoff"
    else:
        layer = "memory"
        memory_credited = False
        reason = "the scripted trace fails alongside a poor handoff"
    incorrect = int(trace.get("incorrect_actions_caused_by_memory") or 0)
    if incorrect and quality == "poor":
        layer = "memory"
        memory_credited = False
        reason = "the trace records incorrect actions caused by memory"
    return {
        "task_completed": solved,
        "turns_to_first_useful_action": trace.get("turns_to_first_useful_action"),
        "incorrect_actions_caused_by_memory": incorrect,
        "tokens": trace.get("tokens"),
        "tool_calls": trace.get("tool_calls"),
        "latency_ms": trace.get("latency_ms"),
        "repeated_work": trace.get("repeated_work"),
        "handoff_quality": quality,
        "layer": layer,
        "memory_credited_for_success": memory_credited and solved and incorrect == 0,
        "reason": reason,
    }
