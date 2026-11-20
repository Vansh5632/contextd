"""Trace metrics for layer 1. All counts are derived from the transcript."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .agent import ArmResult
from .episode import Episode


@dataclass
class ArmMetrics:
    arm: str
    skipped: bool
    resume_clarifications: int | None = None
    rediscovery_commands: int | None = None
    time_to_useful_s: float | None = None
    duplicate_mentions: int | None = None
    decision_violations: int | None = None
    stale_mentions: int | None = None
    novel_work_ratio: float | None = None
    cheated: bool = False
    asked_user: bool = False
    mcp_tools_called: list[str] | None = None


def score_arm(episode: Episode, result: ArmResult) -> ArmMetrics:
    if result.skipped:
        return ArmMetrics(arm=result.arm, skipped=True)
    transcript = result.transcript or ""
    history_needles = _history_needles(episode)
    duplicate = sum(1 for needle in history_needles if needle.lower() in transcript.lower())
    forbidden = episode.gold.forbidden
    violations = sum(1 for needle in forbidden if needle.lower() in transcript.lower())
    stale = 0
    if episode.gold.frontier.get("ruled_out"):
        # Asserting a ruled-out hypothesis as current is stale context.
        for needle in episode.gold.frontier["ruled_out"]:
            if re.search(rf"\b{re.escape(needle)}\b.*(cause|because|due to)", transcript, re.I):
                stale += 1
    actions = max(len(re.findall(r"\b(cargo|rg |git |pytest|npm )\b", transcript)), 1)
    novel = max(actions - duplicate, 0)
    return ArmMetrics(
        arm=result.arm,
        skipped=False,
        resume_clarifications=1 if result.asked_user else 0,
        rediscovery_commands=duplicate,
        time_to_useful_s=result.wall_s,
        duplicate_mentions=duplicate,
        decision_violations=violations if not episode.restraint else 0,
        stale_mentions=stale,
        novel_work_ratio=novel / actions,
        cheated=result.cheated,
        asked_user=result.asked_user,
        mcp_tools_called=result.tool_names,
    )


def _history_needles(episode: Episode) -> list[str]:
    needles = []
    for session in episode.history:
        for event in session.events:
            payload = event.get("payload") or {}
            if payload.get("command"):
                needles.append(str(payload["command"]))
            if payload.get("path"):
                needles.append(str(payload["path"]))
            if payload.get("text"):
                needles.append(str(payload["text"]))
    return needles
