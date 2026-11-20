"""Optional Cursor Agent none/dump/MCP runner. Off by default; CI never requires it."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .episode import Episode
from .inject import write_mcp_config
from .isolate import IsolatedDaemon


@dataclass
class ArmResult:
    arm: str
    skipped: bool
    reason: str | None = None
    exit_code: int | None = None
    transcript: str = ""
    parsed: Any = None
    wall_s: float | None = None
    tool_names: list[str] = field(default_factory=list)
    cheated: bool = False
    asked_user: bool = False


def agent_enabled() -> bool:
    return os.environ.get("EVAL_AGENT", "") in {"1", "true", "yes"}


def cursor_agent_bin() -> str | None:
    override = os.environ.get("CURSOR_AGENT_BIN")
    if override:
        return override if Path(override).is_file() or shutil.which(override) else None
    which = shutil.which("agent")
    if which:
        return which
    return None


def run_arms(
    daemon: IsolatedDaemon,
    episode: Episode,
    *,
    snapshot: dict[str, Any],
    cwd: Path,
) -> list[ArmResult]:
    if not agent_enabled():
        return [
            ArmResult(arm=arm, skipped=True, reason="EVAL_AGENT is not set")
            for arm in episode.arms
        ]
    if not cursor_agent_bin():
        return [
            ArmResult(arm=arm, skipped=True, reason="Cursor `agent` CLI not on PATH")
            for arm in episode.arms
        ]

    results = []
    for arm in episode.arms:
        print(f"    L1 arm {arm}…", flush=True)
        results.append(_run_one_arm(daemon, episode, arm, snapshot, cwd))
    return results


def _run_one_arm(
    daemon: IsolatedDaemon,
    episode: Episode,
    arm: str,
    snapshot: dict[str, Any],
    cwd: Path,
) -> ArmResult:
    workspace = daemon.root / "workspaces" / arm
    if workspace.exists():
        shutil.rmtree(workspace)
    shutil.copytree(cwd, workspace, dirs_exist_ok=True)

    prompt = episode.prompt
    if arm == "dump":
        prompt = (
            "Prior session dump (raw contextd snapshot). Use it; do not ask what happened.\n\n"
            + json.dumps(snapshot)
            + "\n\n"
            + episode.prompt
        )
    if arm == "mcp":
        mcp_path = workspace / ".cursor" / "mcp.json"
        mcp_path.parent.mkdir(parents=True, exist_ok=True)
        write_mcp_config(daemon, mcp_path)
        prompt = (
            "Call the contextd MCP tool context_now before asking the user "
            "what they were doing. You may also use search_context and "
            "recall_similar.\n\n"
            + episode.prompt
        )

    cmd = [
        cursor_agent_bin() or "agent",
        "-p",
        "--output-format",
        "json",
        "--trust",
        "--force",
        "--sandbox",
        "disabled",
        "--workspace",
        str(workspace),
    ]
    model = os.environ.get("EVAL_AGENT_MODEL", "auto")
    if model:
        cmd.extend(["--model", model])
    if arm == "mcp":
        cmd.append("--approve-mcps")
    cmd.append(prompt)

    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(workspace),
            env=_agent_env(daemon),
            capture_output=True,
            text=True,
            timeout=int(os.environ.get("EVAL_AGENT_TIMEOUT", "300")),
            check=False,
        )
    except subprocess.TimeoutExpired as err:
        transcript = (err.stdout or "") + "\n" + (err.stderr or "")
        return ArmResult(
            arm=arm,
            skipped=False,
            reason="timeout",
            exit_code=124,
            transcript=transcript,
            wall_s=time.time() - started,
            cheated=_cheated(episode, transcript),
            asked_user=_asked_user(transcript),
        )

    wall_s = time.time() - started
    transcript = "\n".join(part for part in (proc.stdout, proc.stderr) if part)
    parsed = _try_json(proc.stdout)
    tool_names = _tool_names(parsed, transcript)
    cheated = _cheated(episode, transcript)
    asked = _asked_user(transcript)
    return ArmResult(
        arm=arm,
        skipped=False,
        exit_code=proc.returncode,
        transcript=transcript,
        parsed=parsed,
        wall_s=wall_s,
        tool_names=tool_names,
        cheated=cheated,
        asked_user=asked,
    )


def _agent_env(daemon: IsolatedDaemon) -> dict[str, str]:
    """Keep the caller's Cursor login. Isolated XDG is only for the MCP child."""
    env = os.environ.copy()
    bin_dir = str(daemon.binary.parent)
    env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
    return env


def _try_json(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
        return None


def _tool_names(parsed: Any, transcript: str) -> list[str]:
    names: list[str] = []
    blob = transcript.lower()
    for tool in ("context_now", "search_context", "recall_similar", "set_intent"):
        if tool in blob:
            names.append(tool)
    dumped = json.dumps(parsed).lower() if parsed is not None else ""
    for tool in ("context_now", "search_context", "recall_similar", "set_intent"):
        if tool in dumped and tool not in names:
            names.append(tool)
    return names


def _cheated(episode: Episode, transcript: str) -> bool:
    lowered = transcript.lower()
    return any(cmd.lower() in lowered for cmd in episode.cheat_commands)


def _asked_user(transcript: str) -> bool:
    lowered = transcript.lower()
    markers = (
        "what were you working on",
        "which file",
        "can you paste",
        "could you paste",
        "what failed",
    )
    return any(marker in lowered for marker in markers)
