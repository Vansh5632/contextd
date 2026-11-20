"""Layer 0 scoring: gold needles in retrieved JSON, not an LLM."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .episode import Episode, Gold


@dataclass
class L0Result:
    passed: bool
    invalid: bool
    reason: str | None
    required_hits: dict[str, bool]
    forbidden_hits: dict[str, bool]
    frontier_hits: dict[str, dict[str, bool]]
    decision_hits: dict[str, bool]
    precision: float | None
    relevant: int
    retrieved: int
    replies: dict[str, Any] = field(default_factory=dict)


def _claim_texts(node: Any, texts: list[str]) -> None:
    """Read the current briefing. `now` is a focal task, not an event feed."""
    if isinstance(node, dict):
        text = node.get("text")
        if isinstance(text, str) and text.strip():
            texts.append(text)
        summary = node.get("summary")
        if isinstance(summary, str) and summary.strip():
            texts.append(summary)
        for key in (
            "focal_task",
            "intent",
            "next_action",
            "reality",
            "decisions",
            "ruled_out",
            "unknowns",
            "constraints",
            "open_work",
            "other_active_tasks",
            "passed_checks",
        ):
            if key in node:
                _claim_texts(node[key], texts)
    elif isinstance(node, list):
        for item in node:
            _claim_texts(item, texts)


def collect_item_texts(replies: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for body in replies.values():
        if not isinstance(body, dict):
            continue
        _claim_texts(body, texts)
        for key in ("recent_activity", "relevant_history"):
            for item in body.get(key) or []:
                if isinstance(item, dict) and item.get("text"):
                    texts.append(str(item["text"]))
        for match in body.get("matches") or []:
            if not isinstance(match, dict):
                continue
            event = match.get("event") or match
            summary = None
            if isinstance(event, dict):
                summary = event.get("summary")
                payload = event.get("raw", {}).get("payload") if isinstance(event.get("raw"), dict) else event.get("payload")
                if summary:
                    texts.append(str(summary))
                elif payload is not None:
                    texts.append(json.dumps(payload))
        intent = body.get("intent")
        if isinstance(intent, dict) and intent.get("text"):
            texts.append(str(intent["text"]))
        for related in body.get("related") or []:
            if isinstance(related, dict) and related.get("name"):
                texts.append(str(related["name"]))
    return texts


def _without_query_echo(value: Any) -> Any:
    """Drop request `query` fields so the search string cannot score itself."""
    if isinstance(value, dict):
        return {
            key: _without_query_echo(item)
            for key, item in value.items()
            if key != "query"
        }
    if isinstance(value, list):
        return [_without_query_echo(item) for item in value]
    return value


def haystack(replies: dict[str, Any]) -> str:
    parts = collect_item_texts(replies)
    for body in replies.values():
        if not isinstance(body, dict):
            continue
        for match in body.get("matches") or []:
            parts.append(json.dumps(_without_query_echo(match), default=str))
    return "\n".join(parts).lower()


def _precision_replies(replies: dict[str, Any]) -> dict[str, Any]:
    """Precision is a dump-vs-MCP story: score the `now` briefing when present."""
    now = {
        key: body
        for key, body in replies.items()
        if key == "now" or str(key).startswith("now:")
    }
    return now if now else replies


def contains(needle: str, blob: str) -> bool:
    return needle.lower() in blob


def score_l0(episode: Episode, replies: dict[str, Any]) -> L0Result:
    gold: Gold = episode.gold
    blob = haystack(replies)
    texts = collect_item_texts(_precision_replies(replies))

    if episode.requires_embeddings:
        semantic_flags = [
            body.get("semantic")
            for body in replies.values()
            if isinstance(body, dict) and "semantic" in body
        ]
        if semantic_flags and not any(semantic_flags):
            return L0Result(
                passed=False,
                invalid=True,
                reason="requires embeddings but every search/recall reply had semantic=false",
                required_hits={},
                forbidden_hits={},
                frontier_hits={},
                decision_hits={},
                precision=None,
                relevant=0,
                retrieved=0,
                replies=replies,
            )

    required_hits = {needle: contains(needle, blob) for needle in gold.required}
    forbidden_hits = {needle: contains(needle, blob) for needle in gold.forbidden}
    frontier_hits = {
        field: {needle: contains(needle, blob) for needle in needles}
        for field, needles in gold.frontier.items()
    }
    decision_hits = {}
    for decision in gold.decisions:
        text = decision.get("text") or ""
        if text:
            decision_hits[text] = contains(text, blob)

    gold_needles = list(gold.required)
    for needles in gold.frontier.values():
        gold_needles.extend(needles)
    gold_needles.extend(decision_hits.keys())

    relevant = 0
    for text in texts:
        lowered = text.lower()
        if any(needle.lower() in lowered for needle in gold_needles if needle):
            relevant += 1
    retrieved = len(texts)
    precision = (relevant / retrieved) if retrieved else None

    missing_required = [needle for needle, hit in required_hits.items() if not hit]
    leaked_forbidden = [needle for needle, hit in forbidden_hits.items() if hit]
    missing_frontier = [
        f"{field}:{needle}"
        for field, hits in frontier_hits.items()
        for needle, hit in hits.items()
        if not hit
    ]
    missing_decisions = [text for text, hit in decision_hits.items() if not hit]

    reasons = []
    if missing_required:
        reasons.append("missing required: " + ", ".join(missing_required))
    if leaked_forbidden:
        reasons.append("forbidden leaked: " + ", ".join(leaked_forbidden))
    if missing_frontier:
        reasons.append("missing frontier: " + ", ".join(missing_frontier))
    if missing_decisions:
        reasons.append("missing decisions: " + ", ".join(missing_decisions))

    if episode.restraint and gold.forbidden:
        # Negative control: prior-task needles must not appear.
        pass

    passed = not reasons
    return L0Result(
        passed=passed,
        invalid=False,
        reason="; ".join(reasons) if reasons else None,
        required_hits=required_hits,
        forbidden_hits=forbidden_hits,
        frontier_hits=frontier_hits,
        decision_hits=decision_hits,
        precision=precision,
        relevant=relevant,
        retrieved=retrieved,
        replies=replies,
    )
