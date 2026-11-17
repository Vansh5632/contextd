"""Small planted examples used to show what the grader detects.

These checkpoints are constructed by the harness. They are not daemon output
and they are not gold labels produced by a model.
"""

from __future__ import annotations

import re
from typing import Any

from harness.judge import judge_atom


def planted_reality(text: str, *, origin: str = "agent", evidence_ids: list[str] | None = None) -> dict[str, Any]:
    evidence = evidence_ids or ["evt-support"]
    return {
        "binds": {"source": "evt-support", "conclusion": "evt-note"},
        "accepted": [
            {
                "text": text,
                "kind": "reality",
                "origin": origin,
                "confidence": "inferred",
                "evidence_ids": evidence,
            }
        ],
        "rejected": [],
        "skipped": [],
        "rendered": [
            {
                "slot": "reality",
                "task_id": "task-1",
                "id": "claim-1",
                "text": text,
                "confidence": "inferred",
                "origin": origin,
                "evidence_ids": evidence,
            }
        ],
        "store_claims": [
            {
                "id": "claim-1",
                "task_id": "task-1",
                "kind": "reality",
                "status": "current",
                "confidence": "inferred",
                "origin": origin,
                "text": text,
                "evidence_ids": evidence,
            }
        ],
        "store_events": [
            {
                "id": "evt-support",
                "timestamp_ms": 10,
                "source": "shell",
                "payload": {"command": "sed", "output": "self.retry_count += 1\nself.open_socket()\n", "cwd": "/work/pkg"},
            }
        ],
        "handoff_error": None,
        "now_error": None,
        "process_alive": True,
        "panic": False,
    }


def regex_gap() -> dict[str, Any]:
    """A required-atom regex hits a packet that also states a contradiction."""
    packet = (
        "retry_count before open_socket. "
        "retry_count never increments before open_socket."
    )
    regex_matches = re.search(r"retry_count before open_socket", packet) is not None
    false_text = "retry_count never increments before open_socket"
    judgment = judge_atom(
        {
            "id": "negated",
            "text": false_text,
            "factuality": "CONTRADICTED",
            "expect": "presented_iff_supported",
            "justification": "The planted briefing states a negation of the increment in the supplied listing.",
        },
        planted_reality(false_text),
    )
    return {
        "packet": packet,
        "regex_matches": regex_matches,
        "regex_note": "A Component 0 style atom regex matches the packet even though a negated sentence is present.",
        "grader_flags_false_reality": "presented_as_reality" in judgment["flags"],
        "judgment": [judgment],
    }
