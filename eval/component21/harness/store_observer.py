"""Read-only view of the daemon's SQLite projection.

The public briefing hides claim status, subject, and supersession. Those fields
live in the ``claims`` table. This observer never writes.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def _load_json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def read_store(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {"events": [], "claims": [], "tasks": [], "readable": False}
    uri = f"file:{db_path}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        events = [
            {
                "id": row["id"],
                "timestamp_ms": row["timestamp_ms"],
                "source": row["source"],
                "payload": _load_json(row["payload"], {}),
            }
            for row in connection.execute(
                "SELECT id, timestamp_ms, source, payload FROM events ORDER BY timestamp_ms, id"
            )
        ]
        claims = []
        for row in connection.execute(
            """
            SELECT id, task_id, kind, subject, status, confidence, origin, text,
                   outcome, evidence_ids, supersedes, asserted_by_event_id, updated_at_ms
            FROM claims
            ORDER BY updated_at_ms, id
            """
        ):
            claims.append(
                {
                    "id": row["id"],
                    "task_id": row["task_id"],
                    "kind": row["kind"],
                    "subject": row["subject"],
                    "status": row["status"],
                    "confidence": row["confidence"],
                    "origin": row["origin"],
                    "text": row["text"],
                    "outcome": row["outcome"],
                    "evidence_ids": _load_json(row["evidence_ids"], []),
                    "supersedes": row["supersedes"],
                    "asserted_by_event_id": row["asserted_by_event_id"],
                    "updated_at_ms": row["updated_at_ms"],
                }
            )
        tasks = [
            {
                "id": row["id"],
                "status": row["status"],
                "opened_by_event_id": row["opened_by_event_id"],
                "closed_by_event_id": row["closed_by_event_id"],
                "created_at_ms": row["created_at_ms"],
                "updated_at_ms": row["updated_at_ms"],
            }
            for row in connection.execute(
                """
                SELECT id, status, opened_by_event_id, closed_by_event_id,
                       created_at_ms, updated_at_ms
                FROM tasks
                ORDER BY created_at_ms, id
                """
            )
        ]
    finally:
        connection.close()
    return {"events": events, "claims": claims, "tasks": tasks, "readable": True}
