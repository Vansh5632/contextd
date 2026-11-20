"""JSONL + markdown report writers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def write_markdown(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# contextd eval report",
        "",
        "Illustrative dashboards in the design canvases are **not** this file.",
        "These numbers were measured.",
        "",
        "| Episode | Level | L0 | Precision | Reason |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        l0 = row.get("l0") or {}
        status = "invalid" if l0.get("invalid") else ("pass" if l0.get("passed") else "fail")
        precision = l0.get("precision")
        precision_s = "" if precision is None else f"{precision:.2f}"
        reason = (l0.get("reason") or "").replace("|", "/")
        lines.append(
            f"| {row.get('name')} | {row.get('level')} | {status} | {precision_s} | {reason} |"
        )
    l1_rows = [row for row in rows if isinstance(row.get("l1"), list)]
    if l1_rows:
        lines.extend(
            [
                "",
                "## Layer 1 (Cursor Agent)",
                "",
                "| Episode | Arm | Skipped | Asked | Cheat | Resume | Dup | Stale | Tools | Seconds |",
                "|---|---|---|---|---|---|---|---|---|---|",
            ]
        )
        for row in l1_rows:
            for arm in row["l1"]:
                if not isinstance(arm, dict):
                    continue
                tools = ",".join(arm.get("mcp_tools_called") or [])
                seconds = arm.get("time_to_useful_s")
                seconds_s = "" if seconds is None else f"{seconds:.1f}"
                lines.append(
                    "| {name} | {arm} | {skipped} | {asked} | {cheat} | {resume} | {dup} | {stale} | {tools} | {seconds} |".format(
                        name=row.get("name"),
                        arm=arm.get("arm"),
                        skipped=arm.get("skipped"),
                        asked=arm.get("asked_user"),
                        cheat=arm.get("cheated"),
                        resume=arm.get("resume_clarifications"),
                        dup=arm.get("duplicate_mentions"),
                        stale=arm.get("stale_mentions"),
                        tools=tools,
                        seconds=seconds_s,
                    )
                )
    lines.append("")
    path.write_text("\n".join(lines))
