"""In-container driver for one contextd replay generation.

The host mounts the plan, the workspace, and this file. Nothing here imports
the harness package: the image only has the stdlib plus the scorer's Python
dependencies.
"""

from __future__ import annotations

import json
import os
import pty
import select
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

PROMPT = b"CTXD_READY$ "
WORK = Path("/work")
DATA = Path("/data")
OUT = Path("/out")
HOME = Path("/home/replay")
CONFIG_PATH = HOME / ".config" / "contextd" / "config.toml"
SOCKET_PATH = DATA / "contextd.sock"
DB_PATH = DATA / "events.db"


def _write_bashrc() -> None:
    """History has to be on before PROMPT_COMMAND reads `history 1`."""
    extra = """
# Replay driver: a stable prompt and in-memory history for the shell hook.
set -o history
shopt -s cmdhist lithist
set +H
set +m
HISTCONTROL=
HISTSIZE=100000
HISTFILE=/tmp/replay.bash_history
PS1='CTXD_READY$ '
PS2='CTXD_MORE> '
# set -o history above records the rest of this file. Drop that before the
# first prompt, or the hook reports the last rc line as a typed command.
history -c
"""
    path = HOME / ".bashrc"
    existing = path.read_text() if path.exists() else ""
    if "CTXD_READY$" not in existing:
        path.write_text(existing + extra)


def _install(env: dict[str, str]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(env["CONTEXTD_CONFIG_TEXT"])
    (WORK / ".git" / "hooks").mkdir(exist_ok=True)
    subprocess.run(
        ["contextd", "install"],
        cwd=WORK,
        env=env,
        check=False,
        text=True,
    )
    _write_bashrc()


def _start_daemon(env: dict[str, str], log_path: Path) -> subprocess.Popen[bytes]:
    log = log_path.open("wb")
    proc = subprocess.Popen(
        ["contextd", "run"],
        cwd=WORK,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"contextd exited {proc.returncode}; see {log_path}")
        if SOCKET_PATH.exists():
            return proc
        time.sleep(0.1)
    raise RuntimeError("contextd socket did not appear")


def _stop_daemon(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)


def _read_until(
    master: int, buf: bytearray, token: bytes, *, timeout: float, count_at_least: int
) -> None:
    deadline = time.monotonic() + timeout
    while buf.count(token) < count_at_least:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            tail = bytes(buf[-1500:]).decode("utf-8", "replace")
            raise TimeoutError(f"prompt not seen after {timeout:.0f}s:\n{tail}")
        ready, _, _ = select.select([master], [], [], min(1.0, remaining))
        if not ready:
            continue
        chunk = os.read(master, 65536)
        if not chunk:
            tail = bytes(buf[-1500:]).decode("utf-8", "replace")
            raise EOFError(f"shell closed before prompt:\n{tail}")
        buf.extend(chunk)


def _drive_shell(
    commands: list[dict[str, str]], pause_s: float, env: dict[str, str]
) -> list[dict[str, object]]:
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        ["bash", "-i"],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=WORK,
        env=env,
        start_new_session=True,
        close_fds=True,
    )
    os.close(slave)
    buf = bytearray()
    steps: list[dict[str, object]] = []
    try:
        _read_until(master, buf, PROMPT, timeout=30, count_at_least=1)
        for index, command in enumerate(commands):
            if index:
                time.sleep(pause_s)
            start = time.time()
            payload = command["command"]
            if not payload.endswith("\n"):
                payload += "\n"
            os.write(master, payload.encode())
            before = buf.count(PROMPT)
            # pytest of the http11 suite is the long pole; heredocs are short.
            _read_until(master, buf, PROMPT, timeout=300, count_at_least=before + 1)
            end = time.time()
            steps.append(
                {
                    "index": index,
                    "session": command["session"],
                    "kind": command["kind"],
                    "command": command["command"],
                    "start_unix": start,
                    "end_unix": end,
                }
            )
        time.sleep(pause_s)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
        os.close(master)
    return steps


def _query_now() -> tuple[bytes, float]:
    """One JSON line, then a write-side half-close so the daemon sees EOF."""
    start = time.perf_counter()
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(120)
    client.connect(str(SOCKET_PATH))
    client.sendall(b'{"query":"now"}\n')
    client.shutdown(socket.SHUT_WR)
    chunks: list[bytes] = []
    while True:
        chunk = client.recv(65536)
        if not chunk:
            break
        chunks.append(chunk)
    client.close()
    return b"".join(chunks), time.perf_counter() - start


def _status_reports_backlog(env: dict[str, str]) -> bool:
    result = subprocess.run(
        ["contextd", "status"],
        cwd=WORK,
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )
    text = (result.stdout or "") + (result.stderr or "")
    return "backlog" in text.lower()


def _embedding_gap() -> tuple[int, int]:
    # enriched_at_ms is set only after insert_embedding succeeds. Python's
    # sqlite3 cannot load the sqlite-vec virtual table, so this is the row flag.
    uri = f"file:{DB_PATH}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute("SELECT enriched_at_ms FROM events").fetchall()
    finally:
        conn.close()
    missing = sum(1 for (enriched,) in rows if enriched is None)
    return len(rows), missing


def _drain(env: dict[str, str], timeout_s: float) -> dict[str, object]:
    if _status_reports_backlog(env):
        method = "contextd_status"
    else:
        method = "db_poll"
    deadline = time.monotonic() + timeout_s
    last_total, last_missing = 0, 0
    while time.monotonic() < deadline:
        last_total, last_missing = _embedding_gap()
        if last_total and last_missing == 0:
            return {
                "method": method,
                "status": "drained",
                "events": last_total,
                "missing_embeddings": 0,
            }
        time.sleep(2)
    return {
        "method": method,
        "status": "timeout",
        "events": last_total,
        "missing_embeddings": last_missing,
        "timeout_s": timeout_s,
    }


def _dump_events() -> dict[str, int]:
    uri = f"file:{DB_PATH}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, timestamp_ms, source, payload, score, summary, use_case, enriched_at_ms
            FROM events
            ORDER BY timestamp_ms ASC, id ASC
            """
        ).fetchall()
    finally:
        conn.close()
    counts = {"shell": 0, "file_system": 0, "proc": 0, "git": 0, "embeddings": 0, "events": 0}
    lines: list[str] = []
    for row in rows:
        payload: object
        try:
            payload = json.loads(row["payload"])
        except json.JSONDecodeError:
            payload = row["payload"]
        has_embedding = row["enriched_at_ms"] is not None
        record = {
            "id": row["id"],
            "timestamp_ms": row["timestamp_ms"],
            "source": row["source"],
            "payload": payload,
            "score": row["score"],
            "summary": row["summary"],
            "use_case": row["use_case"],
            "has_embedding": has_embedding,
        }
        lines.append(json.dumps(record, ensure_ascii=False))
        counts["events"] += 1
        source = row["source"]
        if source in counts:
            counts[source] += 1
        if has_embedding:
            counts["embeddings"] += 1
    (OUT / "events.jsonl").write_text("\n".join(lines) + ("\n" if lines else ""))
    return counts


def _head() -> str:
    result = subprocess.run(
        ["git", "-C", str(WORK), "rev-parse", "HEAD"],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def main() -> int:
    plan = json.loads(Path(sys.argv[1]).read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["CONTEXTD_CONFIG_TEXT"] = plan["config_toml"]
    env["HOME"] = str(HOME)
    env["XDG_CONFIG_HOME"] = str(HOME / ".config")
    env["SHELL"] = "/bin/bash"
    result: dict[str, object] = {
        "config_toml": plan["config_toml"],
        "followup_sha": plan["followup_sha"],
    }
    daemon: subprocess.Popen[bytes] | None = None
    exit_code = 0
    try:
        _install(env)
        daemon = _start_daemon(env, OUT / "daemon.log")
        steps = _drive_shell(plan["commands"], float(plan["pause_s"]), env)
        (OUT / "steps.jsonl").write_text("".join(json.dumps(step) + "\n" for step in steps))
        head = _head()
        result["head"] = head
        result["head_matches_followup"] = head == plan["followup_sha"]
        drain = _drain(env, float(plan["drain_timeout_s"]))
        result["backlog_drain_method"] = drain["method"]
        result["backlog_drain_result"] = drain
        reply, elapsed = _query_now()
        result["query_wall_s"] = elapsed
        result["reply_trailing_newline"] = reply.endswith(b"\n")
        (OUT / "briefing.reply").write_bytes(reply)
        text = reply.decode()
        parsed = json.loads(text)
        (OUT / "briefing.json").write_text(json.dumps(parsed, indent=2) + "\n")
        counts = _dump_events()
        run_apply = sum(1 for step in steps if step["kind"] in ("run", "apply"))
        counts["steps_typed"] = len(steps)
        counts["run_apply_steps"] = run_apply
        result["counts"] = counts
        if not result["head_matches_followup"]:
            exit_code = 1
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        exit_code = 1
        print(result["error"], file=sys.stderr)
    finally:
        if daemon is not None:
            _stop_daemon(daemon)
        (OUT / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
