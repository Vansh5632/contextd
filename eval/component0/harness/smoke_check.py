"""Check one live trial dir against the Phase 0 unknowns and print a checklist.

Usage (from eval/component0):
    python -m harness.smoke_check runs/RUN_ID/ARM-rN

Checks: exec stream shape (thread.started, turn.completed usage, command_execution and
file_change items), rollout present with a patch for every file change, no web_search or
MCP items, reasoning items (reported, not required), exec/rollout field names against
agent_config.toml, secret absent from every file, proxy log CONNECTs only to the allowlist.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness.run_trial import SECRET_ENV, load_agent_config, scrub_check

CONNECT_LINE = re.compile(r"Request \(file descriptor \d+\): CONNECT (\S+) HTTP/")


@dataclass
class Item:
    name: str
    status: str
    detail: str


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    if not path.exists():
        return records
    for line in path.read_text().splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def top_fields(spec: list[str]) -> set[str]:
    return {name.split(".", 1)[0].split("[", 1)[0] for name in spec}


def check(trial: Path) -> list[Item]:
    config = load_agent_config()
    items: list[Item] = []

    def add(name: str, ok: bool | None, detail: str) -> None:
        items.append(Item(name, "INFO" if ok is None else ("PASS" if ok else "FAIL"), detail))

    exec_records = read_jsonl(trial / "exec.jsonl")
    types = [r.get("type") for r in exec_records]
    completed = [r.get("item") or {} for r in exec_records if r.get("type") == "item.completed"]
    item_types = [i.get("type") for i in completed]

    started = [r for r in exec_records if r.get("type") == "thread.started"]
    add("exec thread.started", bool(started) and bool(started[0].get("thread_id")),
        f"{len(started)} event(s)")  # fmt: skip
    usage_fields = config["events"]["jsonl"]["usage_fields"]["fields"]
    turns = [r for r in exec_records if r.get("type") == "turn.completed"]
    usage_ok = bool(turns) and all(set(usage_fields) <= set(t.get("usage") or {}) for t in turns)
    add(
        "exec turn.completed usage",
        usage_ok,
        f"{len(turns)} turn(s); last {json.dumps(turns[-1].get('usage')) if turns else '-'}",
    )
    commands = item_types.count("command_execution")
    file_changes = item_types.count("file_change")
    add("exec command_execution items", commands > 0, str(commands))
    add("exec file_change items", file_changes > 0, str(file_changes))
    other_types = sorted(set(types) - set(config["events"]["jsonl"]["event_fields"]))
    add("exec event types known", not other_types, ", ".join(other_types) or "all known")

    item_spec = config["events"]["jsonl"]["item_fields"]
    unknown: set[str] = set()
    for item in completed:
        allowed = top_fields(item_spec.get(item.get("type"), [])) | {"type"}
        unknown |= {f"{item.get('type')}.{k}" for k in set(item) - allowed}
    add("exec item fields match config", not unknown, ", ".join(sorted(unknown)) or "ok")

    rollout = read_jsonl(trial / "rollout.jsonl")
    add("rollout found", bool(rollout), f"{len(rollout)} lines")
    line_fields = set(config["rollout"]["line_fields"])
    optional_fields = set(config["rollout"].get("optional_line_fields", []))
    bad_lines = sum(1 for r in rollout if not line_fields <= set(r) <= line_fields | optional_fields)
    add("rollout line fields match config", bool(rollout) and not bad_lines,
        f"{bad_lines} line(s) differ from {sorted(line_fields)}")  # fmt: skip
    payloads = [r.get("payload") or {} for r in rollout]
    rollout_changes = [
        (p.get("item") or {}) for p in payloads
        if p.get("type") == "item_completed" and (p.get("item") or {}).get("type") == "FileChange"
    ]  # fmt: skip
    missing_patch = []
    for change_item in rollout_changes:
        for path, entry in (change_item.get("changes") or {}).items():
            kind = (entry or {}).get("type")
            has = entry.get("unified_diff") if kind == "update" else entry.get("content")
            if has is None:
                missing_patch.append(f"{kind} {path}")
    add("rollout patch for every file_change",
        bool(rollout) and len(rollout_changes) == file_changes and not missing_patch,
        f"{len(rollout_changes)} rollout FileChange vs {file_changes} exec file_change; "
        f"missing: {', '.join(missing_patch) or 'none'}")  # fmt: skip

    rollout_items = [str((p.get("item") or {}).get("type", "")) for p in payloads
                     if p.get("type") == "item_completed"]  # fmt: skip
    web = (
        item_types.count("web_search")
        + sum(1 for p in payloads if str(p.get("type", "")).startswith("web_search"))
        + sum(1 for t in rollout_items if t.startswith("WebSearch"))
    )
    mcp = item_types.count("mcp_tool_call") + sum(1 for t in rollout_items if "Mcp" in t)
    add("no web_search / mcp items", web == 0 and mcp == 0, f"web_search {web}, mcp {mcp}")
    reasoning_exec = item_types.count("reasoning")
    reasoning_rollout = sum(1 for p in payloads if p.get("type") == "reasoning")
    add("reasoning items", None, f"exec {reasoning_exec}, rollout {reasoning_rollout}")

    meta_path = trial / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    session = next((p for r, p in zip(rollout, payloads) if r.get("type") == "session_meta"), {})
    context = next((p for r, p in zip(rollout, payloads) if r.get("type") == "turn_context"), {})
    add(
        "session cli_version",
        session.get("cli_version") == config["codex"]["version"],
        f"{session.get('cli_version')} (originator {session.get('originator')})",
    )
    add("turn_context model", context.get("model") == config["codex"]["model"],
        f"{context.get('model')}, effort {context.get('effort')}, "
        f"sandbox {(context.get('sandbox_policy') or {}).get('type')}")  # fmt: skip

    secret = os.environ.get(SECRET_ENV)
    if secret:
        scrub = scrub_check(trial, secret)
        add(
            "secret absent from trial files",
            scrub["status"] == "clean",
            f"scanned {scrub['files_scanned']} files now; hits {scrub.get('hits')}",
        )
    else:
        recorded = meta.get("scrub") or {}
        ok = {"clean": True, "LEAK": False}.get(recorded.get("status"))
        add("secret absent from trial files", ok,
            f"{SECRET_ENV} unset now; trial-time scrub: {recorded.get('status')} "
            f"({recorded.get('files_scanned', 0)} files)")  # fmt: skip

    proxy = trial / "proxy.log"
    if not proxy.exists():
        add("proxy CONNECTs only to allowlist", None, "proxy.log not available")
    else:
        targets = CONNECT_LINE.findall(proxy.read_text())
        allowed = set(config["egress"]["allowed_connect"])
        others = sorted(set(targets) - allowed)
        detail = (f"{len(targets)} CONNECT(s): " + (", ".join(sorted(set(targets))) or "none")
                  + (f"; not allowed: {', '.join(others)}" if others else ""))  # fmt: skip
        # A live Codex trial must have tunnelled to the API; stubs make no requests.
        live = meta.get("agent") == "codex"
        add(
            "proxy CONNECTs only to allowlist",
            not others and (bool(targets) or not live),
            detail + ("" if targets or not live else "; live trial made no CONNECT"),
        )
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trial_dir", type=Path)
    args = parser.parse_args(argv)
    items = check(args.trial_dir)
    width = max(len(i.name) for i in items)
    print(f"smoke check: {args.trial_dir}")
    for item in items:
        print(f"[{item.status:<4}] {item.name:<{width}}  {item.detail}")
    failed = [i.name for i in items if i.status == "FAIL"]
    print(f"\nsmoke check {'FAIL: ' + ', '.join(failed) if failed else 'PASS'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
