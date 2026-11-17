"""Optional historical regex scores.

Component 0 packet recall stays available for comparison. It is not a
factuality oracle and is not read by the atomic judge.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPONENT0 = REPO_ROOT / "eval" / "component0"


def historical_regex() -> dict[str, Any]:
    """Score checked-in episode packets when the Component 0 dependencies import."""
    episodes = COMPONENT0 / "episodes"
    packets = []
    if not COMPONENT0.exists():
        return {"status": "blocked", "blocked_reason": "component0 harness is absent", "packets": []}
    try:
        spec = importlib.util.spec_from_file_location(
            "component0_packet_score",
            COMPONENT0 / "harness" / "packet_score.py",
        )
        if spec is None or spec.loader is None:
            raise ImportError("packet_score.py has no loader")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        load_atoms = module.load_atoms
        score_packet = module.score_packet
        split_units = module.split_units
    except Exception as exc:  # missing yaml is a missing optional dependency
        return {
            "status": "blocked",
            "blocked_reason": f"historical scorer could not be imported: {exc}",
            "packets": [],
        }

    class _Tokens:
        def encode_ordinary(self, text: str) -> list[int]:
            return text.split()

    tokens = _Tokens()
    for episode in ("limiter-handoff", "retry-budget"):
        atom_path = episodes / episode / "gold" / "atoms.yaml"
        oracle = episodes / episode / "packets" / "oracle.txt"
        if not atom_path.exists() or not oracle.exists():
            packets.append(
                {
                    "episode": episode,
                    "status": "blocked",
                    "blocked_reason": "atom file or oracle packet is absent",
                }
            )
            continue
        atoms = load_atoms(atom_path)
        text = oracle.read_text()
        scored = score_packet(split_units(text, "paragraphs"), atoms, tokens, raw_text=text)
        packets.append(
            {
                "episode": episode,
                "arm": "oracle",
                "status": "scored",
                "role": "historical_regex_side_channel",
                "required_recall": scored.get("required_recall"),
                "unit_precision": scored.get("unit_precision"),
                "stale_as_current": scored.get("stale_as_current"),
                "note": "Regex atom recall does not establish semantic correctness.",
            }
        )
    return {"status": "scored", "packets": packets, "factuality_oracle": False}
