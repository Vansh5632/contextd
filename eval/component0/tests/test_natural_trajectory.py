"""A second episode whose notes are not preformatted speech acts.

The gold file is read only by the scorer, after the packet exists. The
extractor and the adapter are checked for the leak marker and for gold paths.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from harness.agent_observe import adapt_trace, project_events
from harness.common import EPISODES_DIR
from harness.packet_score import load_atoms, score_packet, split_units

EPISODE = EPISODES_DIR / "retry-budget"
TRAJECTORY = EPISODE / "trajectory.jsonl"
REPO = Path(__file__).resolve().parents[3]
ADAPTER = REPO / "eval" / "component0" / "harness" / "agent_observe.py"
EXTRACT_RS = REPO / "crates" / "broker" / "src" / "extract.rs"
EXTRACT_PY = REPO / "eval" / "component0" / "harness" / "extract_claims.py"
GATE = REPO / "crates" / "broker" / "src" / "bin" / "project_claims.rs"
PREFIX = re.compile(r"(?i)^(decision|ruled out|rejected|next step)\b")


class _Tokens:
    def encode_ordinary(self, text: str) -> list[str]:
        return text.split()


def _records() -> list[dict]:
    return [json.loads(line) for line in TRAJECTORY.read_text().splitlines() if line.strip()]


def test_the_trajectory_is_not_preformatted_and_does_not_carry_the_marker() -> None:
    marker = (EPISODE / "gold" / "leak_marker.txt").read_text().strip()
    assert marker
    scanned = [TRAJECTORY, ADAPTER, EXTRACT_RS, EXTRACT_PY, GATE, Path(__file__)]
    for path in scanned:
        assert marker not in path.read_text(), path
    for path in (ADAPTER, EXTRACT_RS, EXTRACT_PY, GATE):
        text = path.read_text()
        assert "atoms.yaml" not in text
        assert "oracle.txt" not in text
        assert "gold/" not in text
    for record in _records():
        assert PREFIX.match(str(record.get("text") or "")) is None


def test_natural_trajectory_admits_only_grounded_claims() -> None:
    events = adapt_trace(_records())
    report = project_events(events)
    assert report["rejected"] == []
    admitted = [item for item in report["accepted"] if item["kind"] != "retract"]
    texts = [item["text"] for item in admitted]
    assert any("retry_count 2" in text for text in texts)
    assert any("open_socket" in text for text in texts)
    assert any("not the cause" in text for text in texts)
    assert any("might be involved" in text for text in texts)
    assert any("signature unchanged" in text for text in texts)
    assert any("Do not add a dependency." in text for text in texts)
    assert any(text.startswith("I'll move") for text in texts)
    assert any("not counted" in text for text in texts)
    joined = "\n".join(texts)
    assert "port check" not in joined
    assert "stalled flush" not in joined
    assert "database pool is exhausted" not in joined
    assert all(item["confidence"] != "observed" for item in admitted)
    assert all(item["evidence_ids"] for item in admitted)
    realities = [item for item in admitted if item["kind"] == "reality"]
    assert realities
    assert all(len(item["evidence_ids"]) >= 2 for item in realities)
    reasons = " ".join(item["reason"] for item in report["skipped"])
    assert "still failing" in reasons
    assert "not grounded" in reasons or "speech act" in reasons

    snapshot = report["snapshot"]
    focal = snapshot["focal_task"]
    current = [item["text"] for item in focal["reality"]]
    assert any(text.endswith("exited 1") for text in current)
    assert all(item["confidence"] == "observed" for item in focal["reality"] if item["origin"] == "projector")
    assert all("not counted" not in text for text in current)
    packet = json.dumps(snapshot)
    scored = score_packet(
        split_units(packet, "contextd"),
        load_atoms(EPISODE / "gold" / "atoms.yaml"),
        _Tokens(),
        raw_text=packet,
    )
    assert scored["missing_atoms"] == []
    assert scored["required_recall"] == 1
    assert scored["stale_as_current"] == 0
    assert scored["unresolved_present"] is True
