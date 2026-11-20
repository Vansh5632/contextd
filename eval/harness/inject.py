"""Inject a session history, query L0, optionally run agent arms."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .episode import Episode, L0Query, Session, load_episode
from .isolate import IsolatedDaemon
from .unix import ask, inject_event


def inject_history(daemon: IsolatedDaemon, episode: Episode, now_ms: int) -> None:
    expected = sum(len(session.events) for session in episode.history)
    for session in episode.history:
        _inject_session(daemon, session, now_ms)
    # Ingest is fire-and-forget; wait until the writer has the rows.
    _wait_for_ingest(daemon, episode, expected, timeout_s=20.0)


def _inject_session(daemon: IsolatedDaemon, session: Session, now_ms: int) -> None:
    base = now_ms + session.offset_ms
    if session.intent:
        ask(daemon.socket_path, {"query": "intent", "text": session.intent})
    for index, event in enumerate(session.events):
        payload = dict(event)
        payload.setdefault("timestamp_ms", base + index)
        inject_event(daemon.socket_path, payload)


def _event_count(db_path: Path) -> int:
    if not db_path.exists():
        return 0
    try:
        conn = sqlite3.connect(str(db_path), timeout=1.0)
        try:
            row = conn.execute("SELECT COUNT(*) FROM events").fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return 0
    return int(row[0]) if row else 0


def _sentinel_needle(episode: Episode) -> str | None:
    for session in reversed(episode.history):
        for event in reversed(session.events):
            payload = event.get("payload") or {}
            for key in ("text", "path", "command", "stderr", "message"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip().splitlines()[0][:80]
    return None


def _wait_for_ingest(
    daemon: IsolatedDaemon,
    episode: Episode,
    expected: int,
    timeout_s: float,
) -> None:
    deadline = time.time() + timeout_s
    last: dict[str, Any] | None = None
    sentinel = _sentinel_needle(episode)
    while time.time() < deadline:
        count = _event_count(daemon.db_path)
        try:
            last = ask(daemon.socket_path, {"query": "now"})
        except Exception:
            time.sleep(0.05)
            continue
        if last.get("error"):
            time.sleep(0.05)
            continue
        if expected and count < expected:
            time.sleep(0.05)
            continue
        if sentinel:
            try:
                found = ask(
                    daemon.socket_path,
                    {"query": "search", "text": sentinel, "limit": 5},
                )
            except Exception:
                time.sleep(0.05)
                continue
            if not (found.get("matches") or []):
                time.sleep(0.05)
                continue
        return
    raise TimeoutError(
        f"ingest did not settle (events={_event_count(daemon.db_path)} "
        f"expected>={expected}): {last}"
    )


def gold_search_queries(episode: Episode) -> list[L0Query]:
    """Literal LIKE needles for every gold fact, so L0 does not depend on query phrasing."""
    seen = {(item.query, item.text) for item in episode.l0_queries}
    extra: list[L0Query] = []
    needles: list[str] = list(episode.gold.required)
    for values in episode.gold.frontier.values():
        needles.extend(values)
    needles.extend(
        str(decision.get("text"))
        for decision in episode.gold.decisions
        if decision.get("text")
    )
    for needle in needles:
        text = needle.strip()
        if not text or ("search", text) in seen:
            continue
        extra.append(L0Query(query="search", text=text, limit=10))
        seen.add(("search", text))
    return extra


def run_l0_queries(daemon: IsolatedDaemon, episode: Episode) -> dict[str, Any]:
    replies: dict[str, Any] = {}
    for item in [*episode.l0_queries, *gold_search_queries(episode)]:
        key = item.query if item.text is None else f"{item.query}:{item.text[:40]}"
        if key in replies:
            continue
        replies[key] = ask(daemon.socket_path, item.wire())
    return replies


def write_mcp_config(daemon: IsolatedDaemon, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "contextd": {
                        "type": "stdio",
                        "command": str(daemon.binary),
                        "args": ["mcp"],
                        "env": {
                            "XDG_CONFIG_HOME": daemon.env["XDG_CONFIG_HOME"],
                            "XDG_DATA_HOME": daemon.env["XDG_DATA_HOME"],
                            "XDG_RUNTIME_DIR": daemon.env["XDG_RUNTIME_DIR"],
                        },
                    }
                }
            },
            indent=2,
        )
    )
    return dest


def load_named(eval_root: Path, name: str) -> Episode:
    for path in (eval_root / "episodes").rglob("*.yaml"):
        episode = load_episode(path)
        if episode.name == name:
            return episode
    raise FileNotFoundError(f"no episode named {name!r}")
