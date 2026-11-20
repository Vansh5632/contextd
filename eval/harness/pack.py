#!/usr/bin/env python3
"""Turn a captured event JSONL + gold YAML snippet into an episode stub."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    import yaml
except ImportError as exc:
    raise SystemExit("pip install -r eval/requirements.txt") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("events_jsonl", type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    events = []
    for line in args.events_jsonl.read_text().splitlines():
        if line.strip():
            events.append(json.loads(line))
    gold = yaml.safe_load(args.gold.read_text())
    episode = {
        "name": args.name,
        "level": "l2",
        "category": "debugging-frontier",
        "history": [{"session": "captured", "offset": "0", "events": events}],
        "prompt": "Continue.",
        "gold": gold,
        "layers": [0],
        "l0_queries": [{"query": "now"}],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(yaml.safe_dump(episode, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
