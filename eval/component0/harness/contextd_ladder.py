"""Ladder diagnostics for a frozen contextd packet.

observation recall (persistence into the store) is the share of recall-role gold
atoms matched by any stored event. selection recall is the share matched by any
briefing unit. Event text is `source`, compact payload JSON, and summary.

Usage (from eval/component0), once packets/contextd.txt and contextd_events.jsonl exist:
    python -m harness.contextd_ladder [--episode NAME]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from harness.common import DEFAULT_EPISODE, EPISODES_DIR
from harness.packet_score import (
    RECALL_ROLES,
    AtomSet,
    classify_unit,
    count_tokens,
    load_atoms,
    split_units,
)


def render_event(event: dict[str, Any]) -> str:
    source = str(event.get("source", ""))
    payload = event.get("payload", {})
    compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    summary = event.get("summary") or ""
    return f"{source} {compact} {summary}".strip()


def load_events(path: Path | str) -> list[dict[str, Any]]:
    events = []
    for line_no, line in enumerate(Path(path).read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: invalid JSON") from exc
        if not isinstance(item, dict):
            raise ValueError(f"{path}:{line_no}: event is not an object")
        events.append(item)
    return events


def briefing_ids(briefing_text: str) -> set[str]:
    data = json.loads(briefing_text)
    if not isinstance(data, dict):
        raise ValueError("contextd briefing must be a JSON object")
    found: set[str] = set()
    for key in ("recent_activity", "relevant_history"):
        for item in data.get(key) or []:
            if isinstance(item, dict) and item.get("id") is not None:
                found.add(str(item["id"]))

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            evidence = node.get("evidence_ids")
            if isinstance(evidence, list):
                for event_id in evidence:
                    if event_id is not None:
                        found.add(str(event_id))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data.get("focal_task"))
    walk(data.get("other_active_tasks"))
    walk(data.get("constraints"))
    walk(data.get("passed_checks"))
    walk(data.get("open_work"))
    return found


def _recall(atom_ids: list[str], hits: set[str]) -> float:
    if not atom_ids:
        return 0.0
    return sum(1 for atom_id in atom_ids if atom_id in hits) / len(atom_ids)


def ladder(atoms: AtomSet, events_jsonl_path: Path | str, briefing_text: str,
           tokenizer: Any) -> dict[str, Any]:  # fmt: skip
    events = load_events(events_jsonl_path)
    rendered = [render_event(event) for event in events]
    stored: set[str] = set()
    for text in rendered:
        stored.update(classify_unit(text, atoms).atoms)
    selected: set[str] = set()
    for unit in split_units(briefing_text, "contextd"):
        selected.update(classify_unit(unit, atoms).atoms)
    recall_ids = [atom.id for atom in atoms.by_role(*RECALL_ROLES)]
    by_source = Counter(str(event.get("source", "")) for event in events)
    ids = briefing_ids(briefing_text)
    in_briefing = sum(
        1 for event in events if event.get("id") is not None and str(event["id"]) in ids
    )
    return {
        "observation_recall": _recall(recall_ids, stored),
        "selection_recall": _recall(recall_ids, selected),
        "atoms": [
            {
                "id": atom.id,
                "role": atom.role,
                "stored": atom.id in stored,
                "selected": atom.id in selected,
            }
            for atom in atoms.atoms
        ],
        "events_by_source": dict(sorted(by_source.items())),
        "stored_events": len(events),
        "stored_events_in_briefing": in_briefing,
        "briefing_tokens": count_tokens(tokenizer, briefing_text),
        "event_tokens": sum(count_tokens(tokenizer, text) for text in rendered),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode", default=DEFAULT_EPISODE)
    args = parser.parse_args(argv)
    episode_dir = EPISODES_DIR / args.episode
    packet = episode_dir / "packets" / "contextd.txt"
    events = episode_dir / "packets" / "contextd_events.jsonl"
    missing = [path.name for path in (packet, events) if not path.is_file()]
    if missing:
        print(f"contextd ladder: frozen files missing ({', '.join(missing)})")
        return 1
    from harness.arms import get_tokenizer, load_config

    config = load_config(episode_dir)
    result = ladder(
        load_atoms(episode_dir / "gold" / "atoms.yaml"),
        events,
        packet.read_text(),
        get_tokenizer(config.tokenizer),
    )
    out = episode_dir / "packets" / "contextd_ladder.json"
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"wrote {out.relative_to(episode_dir)}")
    print(f"observation_recall {result['observation_recall']:.3f}")
    print(f"selection_recall {result['selection_recall']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
