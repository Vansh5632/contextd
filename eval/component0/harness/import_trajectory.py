"""Turn a saved transcript into the events a daemon log can hold.

This is the only reader of history.jsonl on the Component 2 path.
extract_claims reads the events this writes. It does not open the transcript.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MAX_OUTPUT_LINES = 200
MAX_OUTPUT_CHARS = 8_000
TRUNCATION_MARK = "[output truncated]"


def truncate_output(text: str) -> str:
    lines = text.splitlines()
    shortened = False
    if len(lines) > MAX_OUTPUT_LINES:
        lines = lines[:MAX_OUTPUT_LINES]
        shortened = True
    body = "\n".join(lines)
    if len(body) > MAX_OUTPUT_CHARS:
        body = body[:MAX_OUTPUT_CHARS]
        shortened = True
    if shortened:
        return f"{body}\n{TRUNCATION_MARK}"
    return text


def import_units(units: list[dict[str, Any]], *, cwd: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for index, unit in enumerate(units):
        session = str(unit.get("session") or "")
        unit_no = unit.get("unit", index)
        event_id = f"h_{session}_{unit_no}"
        timestamp_ms = (index + 1) * 1000
        kind = unit.get("kind")
        if kind == "note":
            events.append(
                {
                    "id": event_id,
                    "timestamp_ms": timestamp_ms,
                    "source": "note",
                    "payload": {"session": session, "text": str(unit.get("text") or "")},
                }
            )
        elif kind == "command":
            events.append(
                {
                    "id": event_id,
                    "timestamp_ms": timestamp_ms,
                    "source": "shell",
                    "payload": {
                        "command": str(unit.get("command") or ""),
                        "exit_code": unit.get("exit_code"),
                        "cwd": cwd,
                        "session": session,
                        "output": truncate_output(str(unit.get("output") or "")),
                    },
                }
            )
    return events


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def cwd_from_replay(replay_events: list[dict[str, Any]], command: str) -> str:
    wanted = " ".join(command.split())
    for event in replay_events:
        if event.get("source") != "shell":
            continue
        payload = event.get("payload") or {}
        if " ".join(str(payload.get("command", "")).split()) == wanted:
            return str(payload.get("cwd") or "")
    return ""


def import_episode(episode_dir: Path, replay_events: Path) -> list[dict[str, Any]]:
    history = load_jsonl(episode_dir / "history" / "history.jsonl")
    replay = load_jsonl(replay_events)
    failing = next(
        (
            unit
            for unit in history
            if unit.get("kind") == "command" and unit.get("exit_code") not in (0, None)
        ),
        None,
    )
    cwd = ""
    if failing is not None:
        cwd = cwd_from_replay(replay, str(failing["command"]))
    return import_units(history, cwd=cwd)
