"""Replay the retry-budget trajectory through the daemon, then score the packet.

The gold file is read only after a packet exists. The live path posts
`observe` requests and runs `handoff`. It does not insert claim rows by hand.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from harness.agent_observe import adapt_trace, project_events
from harness.common import COMPONENT_ROOT, EPISODES_DIR
from harness.packet_score import load_atoms, score_packet, split_units

EPISODE = EPISODES_DIR / "retry-budget"
TRAJECTORY = EPISODE / "trajectory.jsonl"
REPO = COMPONENT_ROOT.parents[1]
ARTIFACTS = COMPONENT_ROOT / "runs" / "component2-retry-budget-2026-11-05"
MARKER = "kq7-retry-budget-marker-4e1c"


def _records() -> list[dict[str, Any]]:
    return [json.loads(line) for line in TRAJECTORY.read_text().splitlines() if line.strip()]


def _ask(sock_path: Path, request: dict[str, Any]) -> dict[str, Any]:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(30)
    client.connect(str(sock_path))
    client.sendall((json.dumps(request, separators=(",", ":")) + "\n").encode())
    client.shutdown(socket.SHUT_WR)
    chunks: list[bytes] = []
    while True:
        piece = client.recv(65536)
        if not piece:
            break
        chunks.append(piece)
    client.close()
    body = b"".join(chunks).decode()
    parsed = json.loads(body)
    if isinstance(parsed, dict) and parsed.get("error"):
        raise AssertionError(parsed["error"])
    if not isinstance(parsed, dict):
        raise AssertionError(f"daemon reply was not an object: {body[:200]}")
    return parsed


def _wait_for_socket(path: Path, proc: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"daemon exited {proc.returncode} before the socket appeared")
        if path.exists():
            try:
                _ask(path, {"query": "now"})
            except (
                ConnectionRefusedError,
                FileNotFoundError,
                socket.timeout,
                json.JSONDecodeError,
            ):
                time.sleep(0.05)
                continue
            return
        time.sleep(0.05)
    raise AssertionError("daemon socket did not answer")


def _stop(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _start_daemon(binary: Path, root: Path) -> tuple[subprocess.Popen[str], Path]:
    config_dir = root / "config" / "contextd"
    data_dir = root / "data"
    runtime_dir = root / "runtime"
    watch = root / "watch"
    for path in (config_dir, data_dir, runtime_dir, watch):
        path.mkdir(parents=True, exist_ok=True)
    socket_path = runtime_dir / "contextd.sock"
    if socket_path.exists():
        socket_path.unlink()
    (config_dir / "config.toml").write_text(
        "\n".join(
            [
                f'db_path = "{data_dir / "events.db"}"',
                f'socket_path = "{socket_path}"',
                f'watch_root = "{watch}"',
                'ollama_url = "http://127.0.0.1:9"',
                'embedding_model = "nomic-embed-text"',
                "max_memory_mb = 50",
                "",
            ]
        )
    )
    env = os.environ.copy()
    env["XDG_CONFIG_HOME"] = str(root / "config")
    env["XDG_DATA_HOME"] = str(data_dir)
    env["XDG_RUNTIME_DIR"] = str(runtime_dir)
    env["RUST_LOG"] = "error"
    proc = subprocess.Popen(
        [str(binary), "run"],
        cwd=watch,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        _wait_for_socket(socket_path, proc)
    except Exception:
        _stop(proc)
        stderr = ""
        if proc.stderr is not None:
            stderr = proc.stderr.read()
        raise AssertionError(stderr[-2000:]) from None
    return proc, socket_path


def _observe(sock: Path, record: dict[str, Any], support: str | None) -> str:
    kind = record["type"]
    if kind == "tool":
        body: dict[str, Any] = {
            "query": "observe",
            "kind": "tool",
            "session": record["session"],
            "command": record["command"],
            "cwd": record["cwd"],
            "exit_code": record["exit_code"],
            "output": record["output"],
        }
    else:
        observed = {"message": "conclusion", "retract": "retract"}[kind]
        if record.get("speaker") == "user":
            observed = "user"
        body = {
            "query": "observe",
            "kind": observed,
            "session": record["session"],
            "text": record["text"],
        }
        if support and observed == "conclusion":
            body["support_event_id"] = support
    reply = _ask(sock, body)
    assert reply.get("stored") is True
    return str(reply["event_id"])


def _claim_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    focal = snapshot.get("focal_task") or {}
    rows: list[dict[str, Any]] = []
    for key in ("reality", "decisions", "ruled_out", "unknowns", "constraints"):
        for item in focal.get(key) or []:
            rows.append({"slot": key, **item})
    if focal.get("next_action"):
        rows.append({"slot": "next_action", **focal["next_action"]})
    for item in snapshot.get("constraints") or []:
        rows.append({"slot": "standing_constraint", **item})
    return rows


def _texts(rows: list[dict[str, Any]], slot: str) -> list[str]:
    return [row["text"] for row in rows if row["slot"] == slot]


class _Tokens:
    def __init__(self) -> None:
        import tiktoken

        self._encoding = tiktoken.get_encoding("o200k_base")
        self.name = "o200k_base"

    def encode_ordinary(self, text: str) -> list[int]:
        return self._encoding.encode_ordinary(text)


def _score(packet: str) -> dict[str, Any]:
    tokens = _Tokens()
    scored = score_packet(
        split_units(packet, "contextd") if packet.strip() else [],
        load_atoms(EPISODE / "gold" / "atoms.yaml"),
        tokens,
        raw_text=packet,
    )
    scored["tokenizer"] = tokens.name
    return scored


def _build_binary() -> Path:
    binary = REPO / "target" / "debug" / "contextd"
    result = subprocess.run(
        ["cargo", "build", "-q", "-p", "contextd", "--bin", "contextd"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr[-4000:] + result.stdout[-1000:])
    return binary


def test_retry_budget_live_handoff_survives_restart_without_the_gold_file() -> None:
    marker = (EPISODE / "gold" / "leak_marker.txt").read_text().strip()
    assert marker == MARKER
    offline = project_events(adapt_trace(_records()))
    offline_texts = {
        item["text"] for item in offline["accepted"] if item.get("kind") != "retract"
    }

    binary = _build_binary()
    # Outside the repo: the daemon walks ancestors looking for .git and installs hooks.
    root = Path(tempfile.mkdtemp(prefix="ctxrb-"))
    assert not any((parent / ".git").exists() for parent in root.parents)
    proc, sock = _start_daemon(binary, root)
    posted: dict[str, dict[str, Any]] = {}
    try:
        last_tool: dict[str, str] = {}
        for record in _records():
            support = last_tool.get(str(record.get("session")))
            event_id = _observe(sock, record, support)
            posted[event_id] = record
            if record["type"] == "tool":
                last_tool[str(record["session"])] = event_id
        started = time.perf_counter()
        handoff = _ask(sock, {"query": "handoff"})
        handoff_s = time.perf_counter() - started
        before = _ask(sock, {"query": "now"})
    finally:
        _stop(proc)

    accepted = [item["text"] for item in handoff["accepted"] if item.get("kind") != "retract"]
    assert handoff["rejected"] == []
    assert set(accepted) == offline_texts
    assert all("not counted" not in text for text in accepted)
    skipped = " ".join(str(item.get("reason") or "") for item in handoff["skipped"])
    assert "contradicts" in skipped
    assert "does not quote" in skipped

    blob = json.dumps(before)
    assert marker not in blob
    assert "atoms.yaml" not in blob
    assert "port check" not in blob
    assert "stalled flush" not in blob
    assert "database pool is exhausted" not in blob
    assert "not counted" not in blob

    rows = _claim_rows(before)
    realities = _texts(rows, "reality")
    assert any(text.endswith("exited 1") for text in realities)
    assert any("retry_count 2" in text for text in realities)
    assert any("open_socket" in text for text in realities)
    assert _texts(rows, "decisions") == []
    constraints = "\n".join(_texts(rows, "standing_constraint") + _texts(rows, "constraints"))
    assert "signature unchanged" in constraints
    assert "Do not add a dependency." in constraints
    assert any("not the cause" in text for text in _texts(rows, "ruled_out"))
    assert any("might be involved" in text for text in _texts(rows, "unknowns"))
    assert any(text.startswith("I'll move") for text in _texts(rows, "next_action"))
    for row in rows:
        assert row["evidence_ids"]
        if row["origin"] == "projector":
            assert row["confidence"] == "observed"
        else:
            assert row["confidence"] != "observed"
        if row["slot"] == "reality" and row["origin"] == "agent":
            assert len(row["evidence_ids"]) >= 2
            cited = [item for item in row["evidence_ids"] if item in posted]
            assert any(posted[item]["type"] == "tool" for item in cited)

    proc, sock = _start_daemon(binary, root)
    try:
        restarted = _ask(sock, {"query": "now"})
        again = _ask(sock, {"query": "handoff"})
    finally:
        _stop(proc)

    assert again["accepted"] == []
    assert again["rejected"] == []
    assert _claim_rows(restarted) == rows
    assert json.dumps(restarted).count(marker) == 0

    packet = json.dumps(before)
    shell_events = [event for event in adapt_trace(_records()) if event["source"] == "shell"]
    component1 = project_events(shell_events)
    scores = {
        "none": _score(""),
        "contextd-component1": _score(json.dumps(component1["snapshot"])),
        "contextd-component2": _score(packet),
    }
    assert scores["contextd-component2"]["required_recall"] == 1
    assert scores["contextd-component2"]["missing_atoms"] == []
    assert scores["contextd-component2"]["stale_as_current"] == 0
    assert scores["contextd-component2"]["unresolved_present"] is True
    assert scores["contextd-component1"]["required_recall"] == 0
    assert scores["none"]["required_recall"] == 0

    missing = [
        name
        for name in (
            "pins.toml",
            "prompt.txt",
            "wrapper.txt",
            "gold/actions.yaml",
            "gold/hidden_tests",
            "packets/manifest.json",
            "packets/oracle.txt",
            "packets/summary.txt",
            "packets/contextd_new.txt",
        )
        if not (EPISODE / name).exists()
    ]
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=False
    )
    report = {
        "commit": commit.stdout.strip(),
        "handoff_seconds": round(handoff_s, 4),
        "accepted": handoff["accepted"],
        "skipped": handoff["skipped"],
        "scores": scores,
        "missing_component0_files": missing,
        "codex_key_present": bool(os.environ.get("CODEX_API_KEY")),
        "decisions_in_snapshot": _texts(rows, "decisions"),
    }
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / "live_report.json").write_text(json.dumps(report, indent=2) + "\n")
    (ARTIFACTS / "context_now.json").write_text(json.dumps(before, indent=2) + "\n")
    (ARTIFACTS / "context_now_after_restart.json").write_text(
        json.dumps(restarted, indent=2) + "\n"
    )
    shutil.rmtree(root, ignore_errors=True)
