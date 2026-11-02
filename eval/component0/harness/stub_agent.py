"""Offline stand-in for `codex exec --json`, run inside the agent container.

Usage (inside the container, prompt on stdin):
    python /stub/stub_agent.py --mode oracle|none|stale --data /stub/fix.json

Writes Codex-format exec JSONL to stdout and a Codex-format rollout file under
$CODEX_HOME/sessions/YYYY/MM/DD/, using the field names recorded in
harness/agent_config.toml, while running real commands and edits in /workspace.
Token counts are synthetic. Stdlib only: this file is copied into the image at run time.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import shlex
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

WORKSPACE = Path("/workspace")
LAST_MESSAGE = Path("/out/last_message.txt")
# Mirrors shell_environment_policy.inherit = "core" in agent_config.toml.
CORE_ENV = ("PATH", "SHELL", "TMPDIR", "TEMP", "TMP", "HOME", "LANG", "LC_ALL", "LC_CTYPE",
            "LOGNAME", "USER")  # fmt: skip
USAGE_KEYS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens",
              "reasoning_output_tokens")  # fmt: skip
VISIBLE_TEST = "python -m pytest -q -p no:cacheprovider tests/test_pool_counts.py"
ASYNC_POOL = "httpcore/_async/connection_pool.py"
REDIS_GATE = "httpcore/_redis_gate.py"
REDIS_GATE_TEXT = '''"""Redis-backed connection gate (session A plan)."""

REDIS_URL = "redis://localhost:6379/0"
GATE_KEY = "httpcore:pool:gate"
'''


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class Session:
    def __init__(self, codex_home: Path, prompt: str, step_delay: float, model: str,
                 cli_version: str) -> None:  # fmt: skip
        self.model = model
        self.cli_version = cli_version
        self.thread_id = str(uuid.uuid4())
        self.turn_id = str(uuid.uuid4())
        started = datetime.now(timezone.utc)
        day = codex_home / "sessions" / started.strftime("%Y/%m/%d")
        day.mkdir(parents=True, exist_ok=True)
        stamp = started.strftime("%Y-%m-%dT%H-%M-%S")
        self.rollout_path = day / f"rollout-{stamp}-{self.thread_id}.jsonl"
        self.rollout_file = open(self.rollout_path, "w")
        self.ordinal = 0
        self.items = 0
        self.calls = 0
        self.step_delay = step_delay
        self.started_at = int(started.timestamp())
        self.context = 9000 + len(prompt) // 4
        self.total = {k: 0 for k in USAGE_KEYS}
        self.prompt = prompt

    # -- output channels

    def emit(self, record: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(record) + "\n")
        sys.stdout.flush()

    def rollout(self, rtype: str, payload: dict[str, Any]) -> None:
        line = {"timestamp": now_iso(), "ordinal": self.ordinal, "type": rtype, "payload": payload}
        self.rollout_file.write(json.dumps(line) + "\n")
        self.rollout_file.flush()
        self.ordinal += 1

    def item_completed(self, item: dict[str, Any]) -> None:
        ms = int(time.time() * 1000)
        self.rollout("event_msg", {"type": "item_completed", "thread_id": self.thread_id,
                                   "turn_id": self.turn_id, "item": item,
                                   "started_at_ms": ms, "completed_at_ms": ms})  # fmt: skip

    def next_item_id(self) -> str:
        item_id = f"item_{self.items}"
        self.items += 1
        return item_id

    def request(self, produced: str) -> None:
        """One synthetic model request: usage grows with the context, reported like Codex."""
        output = 120 + len(produced) // 4
        last = {"input_tokens": self.context, "cached_input_tokens": max(0, self.context - 900),
                "cache_write_input_tokens": 0, "output_tokens": output,
                "reasoning_output_tokens": output // 2}  # fmt: skip
        for key in USAGE_KEYS:
            self.total[key] += last[key]
        self.context += output + 300

        def with_total(usage: dict[str, int]) -> dict[str, int]:
            return {**usage, "total_tokens": usage["input_tokens"] + usage["output_tokens"]}

        self.rollout("event_msg", {"type": "token_count", "info": {
            "total_token_usage": with_total(self.total), "last_token_usage": with_total(last),
            "model_context_window": 272000}, "rate_limits": None})  # fmt: skip
        time.sleep(self.step_delay)

    # -- session lifecycle

    def start(self) -> None:
        self.emit({"type": "thread.started", "thread_id": self.thread_id})
        self.rollout("session_meta", {
            "id": self.thread_id, "session_id": self.thread_id, "cwd": str(WORKSPACE),
            "cli_version": self.cli_version, "originator": "contextd_c0_stub_agent", "source": "exec",
            "model_provider": "openai", "base_instructions": None, "history_mode": "paginated",
            "memory_mode": "disabled", "multi_agent_version": "disabled",
            "git": {"commit_hash": git_head(), "repository_url": None},
        })  # fmt: skip
        self.rollout("turn_context", {
            "cwd": str(WORKSPACE), "model": self.model, "effort": "medium",
            "approval_policy": "never", "sandbox_policy": {"type": "danger-full-access"},
            "permission_profile": {"type": "disabled"},
            "current_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "collaboration_mode": None, "multi_agent_version": "disabled",
        })  # fmt: skip
        self.rollout("event_msg", {"type": "task_started", "turn_id": self.turn_id,
                                   "trace_id": None, "started_at": self.started_at,
                                   "model_context_window": 272000,
                                   "collaboration_mode_kind": None})  # fmt: skip
        self.emit({"type": "turn.started"})
        self.rollout("response_item", {"type": "message", "id": None, "role": "user",
                                       "content": [{"type": "input_text", "text": self.prompt}],
                                       "phase": None})  # fmt: skip
        self.item_completed({"type": "UserMessage", "id": str(uuid.uuid4()),
                             "content": [{"type": "text", "text": self.prompt}]})  # fmt: skip

    def reasoning(self, text: str) -> None:
        self.request(text)
        self.emit(
            {
                "type": "item.completed",
                "item": {"id": self.next_item_id(), "type": "reasoning", "text": text},
            }
        )
        self.rollout("response_item", {"type": "reasoning", "id": f"rs_{self.ordinal}",
                                       "summary": [{"type": "summary_text", "text": text}],
                                       "content": None, "encrypted_content": None})  # fmt: skip

    def message(self, text: str) -> None:
        self.request(text)
        self.emit(
            {
                "type": "item.completed",
                "item": {"id": self.next_item_id(), "type": "agent_message", "text": text},
            }
        )
        self.rollout("response_item", {"type": "message", "id": f"msg_{self.ordinal}",
                                       "role": "assistant",
                                       "content": [{"type": "output_text", "text": text}],
                                       "phase": None})  # fmt: skip
        self.item_completed({"type": "AgentMessage", "id": str(uuid.uuid4()),
                             "content": [{"type": "Text", "text": text}], "phase": None,
                             "memory_citation": None})  # fmt: skip

    def tool_call(self, cell: str) -> str:
        self.request(cell)
        self.calls += 1
        call_id = f"call_{self.calls}"
        self.rollout("response_item", {"type": "custom_tool_call", "id": f"ctc_{self.calls}",
                                       "status": "completed", "call_id": call_id,
                                       "name": "exec", "namespace": None,
                                       "input": cell})  # fmt: skip
        return call_id

    def command(self, script: str, timeout: int = 300) -> int:
        argv = ["/bin/bash", "-lc", script]
        command = shlex.join(argv)
        call_id = self.tool_call(f"await tools.exec_command({{cmd: {json.dumps(script)}}})")
        item_id = self.next_item_id()
        self.emit({"type": "item.started", "item": {
            "id": item_id, "type": "command_execution", "command": command,
            "aggregated_output": "", "exit_code": None, "status": "in_progress"}})  # fmt: skip
        begun = time.monotonic()
        env = {k: os.environ[k] for k in CORE_ENV if k in os.environ}
        try:
            proc = subprocess.run(argv, cwd=WORKSPACE, env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, timeout=timeout,
                                  check=False)  # fmt: skip
            output = proc.stdout.decode("utf-8", "replace")
            code: int = proc.returncode
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or b"").decode("utf-8", "replace") + "\n[timed out]\n"
            code = 124
        elapsed = time.monotonic() - begun
        status = "completed" if code == 0 else "failed"
        self.emit({"type": "item.completed", "item": {
            "id": item_id, "type": "command_execution", "command": command,
            "aggregated_output": output, "exit_code": code, "status": status}})  # fmt: skip
        self.item_completed({
            "type": "CommandExecution", "id": str(uuid.uuid4()), "command": argv,
            "cwd": str(WORKSPACE), "parsed_cmd": [], "source": "agent", "process_id": None,
            "interaction_input": None, "status": status, "stdout": output, "stderr": "",
            "aggregated_output": output, "exit_code": code,
            "duration": {"secs": int(elapsed), "nanos": int((elapsed % 1) * 1e9)},
            "formatted_output": output,
        })  # fmt: skip
        self.rollout("response_item", {"type": "custom_tool_call_output", "id": None,
                                       "call_id": call_id, "name": "exec",
                                       "output": output[-4000:]})  # fmt: skip
        return code

    def file_change(self, rel: str, new_text: str) -> None:
        """Write one file (add or update) and report it as Codex apply_patch does."""
        path = WORKSPACE / rel
        old_text = path.read_text() if path.exists() else None
        call_id = self.tool_call(f"await tools.apply_patch({json.dumps('*** Begin Patch')})")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_text)
        abs_path = str(WORKSPACE / rel)
        if old_text is None:
            kind, entry = "add", {"type": "add", "content": new_text}
        else:
            kind = "update"
            entry = {"type": "update", "unified_diff": unified_diff(old_text, new_text),
                     "move_path": None}  # fmt: skip
        item_id = self.next_item_id()
        self.emit({"type": "item.completed", "item": {
            "id": item_id, "type": "file_change",
            "changes": [{"path": abs_path, "kind": kind}], "status": "completed"}})  # fmt: skip
        self.item_completed({"type": "FileChange", "id": str(uuid.uuid4()),
                             "changes": {abs_path: entry}, "status": "completed",
                             "auto_approved": True, "stdout": "", "stderr": ""})  # fmt: skip
        self.rollout("response_item", {"type": "custom_tool_call_output", "id": None,
                                       "call_id": call_id, "name": "exec",
                                       "output": f"Success. Updated: {rel}"})  # fmt: skip

    def finish(self, last_message: str) -> None:
        self.message(last_message)
        self.rollout("event_msg", {"type": "task_complete", "turn_id": self.turn_id,
                                   "last_agent_message": last_message, "error": None,
                                   "started_at": self.started_at,
                                   "completed_at": int(time.time())})  # fmt: skip
        self.rollout_file.close()
        self.emit({"type": "turn.completed", "usage": dict(self.total)})
        if LAST_MESSAGE.parent.is_dir():
            LAST_MESSAGE.write_text(last_message + "\n")


def unified_diff(old: str, new: str) -> str:
    lines = difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True), n=3)
    return "".join(line for line in lines if not line.startswith(("---", "+++")))


def git_head() -> str | None:
    result = subprocess.run(["git", "-C", str(WORKSPACE), "rev-parse", "HEAD"],
                            capture_output=True, text=True, check=False)  # fmt: skip
    return result.stdout.strip() or None


def apply_fix(session: Session, data: dict[str, str]) -> None:
    path = WORKSPACE / data["path"]
    text = path.read_text()
    if text.count(data["old"]) != 1:
        raise SystemExit(f"stub: fix context not found exactly once in {data['path']}")
    session.file_change(data["path"], text.replace(data["old"], data["new"]))


def fix_flow(session: Session, data: dict[str, str]) -> None:
    session.command(f"sed -n '{data['read_start']},{data['read_end']}p' {ASYNC_POOL}")
    session.message(
        "The create branch counts only non-idle connections, so idle keepalive "
        "connections are ignored against the cap. Counting every held connection."
    )
    apply_fix(session, data)
    session.command("python scripts/unasync.py")
    session.command(VISIBLE_TEST)


def run_mode(session: Session, mode: str, data: dict[str, str]) -> str:
    if mode == "oracle":
        session.reasoning("**Going straight to the pool create branch**")
        fix_flow(session, data)
        return "Fixed the create-new-connection check and regenerated the sync pool."
    if mode == "none":
        session.reasoning("**Checking the mock backend and lock primitives first**")
        session.command("cat httpcore/_backends/mock.py")
        session.command("sed -n '1,120p' httpcore/_synchronization.py")
        fix_flow(session, data)
        return "Fixed the create-new-connection check after ruling out the mock backend."
    if mode == "stale":
        session.reasoning("**Following the Redis gate plan from session A**")
        session.command("pip install redis", timeout=180)
        session.file_change(REDIS_GATE, REDIS_GATE_TEXT)
        session.command(VISIBLE_TEST)
        return "Added a Redis gate module; redis could not be installed offline."
    raise SystemExit(f"stub: unknown mode {mode}")


def probe_proxy() -> str:
    """Bare TCP open/close to the proxy: proves reachability without sending a request."""
    proxy = os.environ.get("HTTPS_PROXY") or ""
    parsed = urlparse(proxy)
    if not parsed.hostname:
        return "HTTPS_PROXY unset"
    try:
        socket.create_connection((parsed.hostname, parsed.port or 80), 5).close()
        return f"HTTPS_PROXY={proxy} reachable"
    except OSError as exc:
        return f"HTTPS_PROXY={proxy} unreachable ({type(exc).__name__})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("oracle", "none", "stale"))
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--cli-version", required=True)
    parser.add_argument("--step-delay", type=float, default=0.3)
    args = parser.parse_args(argv)
    prompt = sys.stdin.read()
    data = json.loads(args.data.read_text())
    codex_home = Path(os.environ.get("CODEX_HOME") or "/home/agent/.codex")
    print(
        f"stub-agent: mode={args.mode} {probe_proxy()}; "
        f"CODEX_API_KEY present={'CODEX_API_KEY' in os.environ}",
        file=sys.stderr,
    )
    session = Session(codex_home, prompt, args.step_delay, args.model, args.cli_version)
    session.start()
    session.finish(run_mode(session, args.mode, data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
