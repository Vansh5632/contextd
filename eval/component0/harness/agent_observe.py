"""Turn one coding-agent trace into the events a daemon log can hold.

Interactive shell hooks do not see a coding agent's tool calls. A harness that
already observed the tool result and the agent's own message calls
`adapt_trace` once, after the trajectory, and posts those events. Extraction
stays off the per-command path: nothing here admits a claim.

This module does not open a saved benchmark transcript.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from harness.common import COMPONENT_ROOT

REPO_ROOT = COMPONENT_ROOT.parents[1]


def adapt_trace(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map tool results and agent messages onto shell and note events.

    `support_event_id` points at the latest tool call in that session, which
    is the evidence a conclusion has to overlap. User messages are not given
    a conclusion role. Retractions stay retractions.
    """
    events: list[dict[str, Any]] = []
    last_tool: dict[str, str] = {}
    for index, record in enumerate(records, start=1):
        session = str(record.get("session") or "")
        event_id = f"obs_{index}"
        kind = record.get("type")
        if kind == "tool":
            events.append(
                {
                    "id": event_id,
                    "timestamp_ms": index * 1000,
                    "source": "shell",
                    "payload": {
                        "command": str(record.get("command") or ""),
                        "exit_code": record.get("exit_code"),
                        "cwd": str(record.get("cwd") or ""),
                        "session": session,
                        "output": str(record.get("output") or ""),
                    },
                }
            )
            last_tool[session] = event_id
            continue
        if kind not in ("message", "retract", "update"):
            continue
        payload: dict[str, Any] = {
            "text": str(record.get("text") or ""),
            "session": session,
        }
        support = last_tool.get(session)
        if support:
            payload["support_event_id"] = support
        if kind == "retract":
            payload["role"] = "retract"
            payload["speaker"] = "agent"
        elif kind == "update":
            payload["role"] = "update"
            payload["speaker"] = "agent"
            payload["replaces_text"] = str(record.get("replaces_text") or "")
        else:
            speaker = str(record.get("speaker") or "agent")
            payload["speaker"] = speaker
            if speaker != "user":
                payload["role"] = "conclusion"
        events.append(
            {
                "id": event_id,
                "timestamp_ms": index * 1000,
                "source": "note",
                "payload": payload,
            }
        )
    return events


def project_events(
    events: list[dict[str, Any]],
    *,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Admit `events` through the Rust extractor and return its report."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        log_path = root / "events.jsonl"
        packet_path = root / "packet.json"
        report_path = root / "report.json"
        log_path.write_text(
            "".join(json.dumps(event, separators=(",", ":")) + "\n" for event in events)
        )
        result = subprocess.run(
            [
                "cargo",
                "run",
                "-q",
                "-p",
                "broker",
                "--bin",
                "project_claims",
                "--",
                "--extract",
                str(log_path),
                str(packet_path),
                str(report_path),
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            shell=False,
        )
        if result.returncode != 0:
            raise SystemExit(
                "project_claims --extract failed\n" + result.stderr + result.stdout
            )
        return json.loads(report_path.read_text())
