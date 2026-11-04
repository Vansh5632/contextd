"""Extraction admits only verbatim, grounded spans. Gold files are not inputs."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from harness.common import EPISODES_DIR
from harness.extract_claims import (
    collapse,
    extract,
    focal_command,
    overlap,
    write_packet,
)
from harness.import_trajectory import import_episode
from harness.packet_score import load_atoms, score_packet, split_units

EPISODE = EPISODES_DIR / "limiter-handoff"
REPLAY = (
    EPISODE
    / "regressions"
    / "briefing-selection"
    / "baseline"
    / "replay"
    / "gen-1"
    / "events.jsonl"
)
EXTRACTOR = Path(__file__).resolve().parents[1] / "harness" / "extract_claims.py"
REPO = Path(__file__).resolve().parents[3]
GATE = REPO / "crates" / "broker" / "src" / "extract.rs"
ADMIT = REPO / "crates" / "broker" / "src" / "bin" / "project_claims.rs"
ADAPTER = REPO / "eval" / "component0" / "harness" / "agent_observe.py"


def episode_events() -> list[dict]:
    return import_episode(EPISODE, REPLAY)


def _note(unit: int, text: str, session: str = "A") -> dict:
    return {
        "id": f"h_{session}_{unit}",
        "timestamp_ms": unit * 1000,
        "source": "note",
        "payload": {"session": session, "text": text},
    }


def _cmd(
    unit: int,
    command: str,
    output: str = "",
    code: int = 0,
    session: str = "A",
    cwd: str = "/work",
) -> dict:
    return {
        "id": f"h_{session}_{unit}",
        "timestamp_ms": unit * 1000,
        "source": "shell",
        "payload": {
            "command": command,
            "exit_code": code,
            "cwd": cwd,
            "session": session,
            "output": output,
        },
    }


def test_extractor_and_gate_do_not_name_the_gold_files() -> None:
    for path in (EXTRACTOR, GATE, ADMIT, ADAPTER):
        text = path.read_text()
        assert "atoms.yaml" not in text
        assert "oracle.txt" not in text
        assert "gold/" not in text


def test_overlap_requires_whole_words() -> None:
    assert overlap("idle count", "the counter is idle today") == 1
    assert overlap("idle count", "idleness is countless") == 0


def test_a_note_stays_on_its_own_failing_command() -> None:
    events = [
        _cmd(1, "python -m pytest tests/test_pool_counts.py", code=1, session="A"),
        _cmd(2, "python -m pytest tests/test_host.py", code=1, session="B", cwd="/other"),
        _note(3, "Decision: idle connections count toward the cap.", session="B"),
    ]
    result = extract(events)
    assert len(result.claims) == 1
    assert result.claims[0].task_subject.startswith("/other\n")
    assert "test_host.py" in result.claims[0].task_subject


def test_extractor_does_not_open_the_transcript() -> None:
    text = EXTRACTOR.read_text()
    assert "history.jsonl" not in text
    assert "atoms.yaml" not in text
    assert "oracle.txt" not in text
    assert "gold/" not in text


def test_open_hypothesis_and_ungrounded_note_are_not_claims() -> None:
    units = [
        _cmd(1, "python -m pytest tests/test_pool_counts.py", "count 3 diverged from 2", 1),
        _note(2, "First suspect: the mock network backend."),
        _note(3, "Idea: a shared admission counter in Redis."),
        _cmd(4, "sed -n 1p pool.py", "return 1"),
        _note(5, "The database cluster is the real cause of this failure mode."),
        _note(6, "Picking up the pool count failure."),
    ]
    result = extract(units)
    assert result.claims == []
    reasons = {item["reason"] for item in result.skipped}
    assert any("hypothesis" in reason for reason in reasons)
    assert any("not grounded" in reason or "speech act" in reason for reason in reasons)


def test_decision_is_inferred_and_constraint_is_stated() -> None:
    units = [
        _cmd(1, "python -m pytest tests/test_pool_counts.py", code=1),
        _note(
            2,
            "Constraint for the fix: keep the public signature unchanged. No new dependencies.",
        ),
        _note(3, "Decision: idle connections count toward the cap."),
        _note(4, "Next step, not started: edit the create branch, then rerun the test."),
    ]
    result = extract(units)
    by_kind = {claim.kind: claim for claim in result.claims}
    assert by_kind["constraint"].origin == "user"
    assert by_kind["constraint"].confidence == "stated"
    assert by_kind["decision"].origin == "agent"
    assert by_kind["decision"].confidence == "inferred"
    assert by_kind["decision"].confidence != "observed"
    assert by_kind["next_action"].subject == "frontier"
    assert all(claim.text in claim.source_text for claim in result.claims)
    assert all(claim.confidence != "observed" for claim in result.claims)


def test_reality_needs_the_preceding_tool_output() -> None:
    units = [
        _cmd(1, "python -m pytest tests/test_pool_counts.py", code=1),
        _cmd(
            2,
            "sed -n 10p pool.py",
            "len([c for c in self._connections if not c.is_idle()]) < self._max_connections",
        ),
        _note(
            3,
            "The create branch only counts connections that are not idle toward max_connections.",
        ),
    ]
    result = extract(units)
    assert len(result.claims) == 1
    claim = result.claims[0]
    assert claim.kind == "reality"
    assert claim.confidence == "inferred"
    assert claim.evidence_ids == ["h_A_3", "h_A_2"]
    assert overlap(claim.text, claim.support_text or "") >= 2


def test_a_closed_session_is_not_copied_onto_the_open_task() -> None:
    units = [
        _cmd(1, "python -m pytest tests/test_pool_counts.py", code=1),
        _note(2, "Decision: idle connections count toward the cap."),
        _cmd(3, "python -m pytest tests/test_models.py", code=0, session="B"),
        _note(4, "Decision: the port check should be equality.", session="B"),
    ]
    result = extract(units)
    texts = [claim.text for claim in result.claims]
    assert any("idle connections" in text for text in texts)
    assert all("port check" not in text for text in texts)


def test_duplicate_note_is_kept_once() -> None:
    text = "Decision: idle connections count toward the cap."
    units = [
        _cmd(1, "python -m pytest tests/test_pool_counts.py", code=1),
        _note(2, text),
        _note(3, text),
    ]
    result = extract(units)
    assert len(result.claims) == 1
    assert any(item["reason"].startswith("duplicate") for item in result.skipped)


def test_commands_alone_extract_nothing() -> None:
    commands = [event for event in episode_events() if event["source"] == "shell"]
    assert extract(commands).claims == []


def test_episode_claims_are_verbatim_spans_of_session_a() -> None:
    events = episode_events()
    by_id = {event["id"]: event for event in events}
    notes = {
        event["payload"]["text"]
        for event in events
        if event["source"] == "note" and event["payload"].get("session") == "A"
    }
    result = extract(events)
    assert result.claims
    for claim in result.claims:
        assert claim.text in claim.source_text
        assert claim.confidence != "observed"
        assert claim.source_text in notes
        assert not claim.text.casefold().startswith("first suspect")
        assert not claim.text.casefold().startswith("idea:")
        for ref in claim.evidence_ids:
            source = by_id[ref]
            if source["source"] == "note":
                assert claim.text in source["payload"]["text"]
            else:
                assert claim.kind == "reality"
                assert overlap(claim.text, source["payload"]["output"]) >= 2
    kinds = {claim.kind for claim in result.claims}
    assert {"constraint", "decision", "ruled_out", "reality", "unknown", "next_action"} <= kinds


def test_component1_projector_admits_the_episode_and_hides_nothing_required() -> None:
    report = write_packet(EPISODE)
    assert report["rejected"] == []
    snapshot = report["snapshot"]
    focal = snapshot["focal_task"]
    assert focal["id"] == "t_a58f76e3ca4078e8a849ed9682f450cc"
    assert focal["reality"][0]["text"].endswith("exited 1")
    assert focal["reality"][0]["confidence"] == "observed"
    assert focal["next_action"]["text"].startswith("Next step")
    assert focal["next_action"]["confidence"] == "inferred"
    assert focal["decisions"][0]["confidence"] == "inferred"
    inferred = [
        claim
        for claim in focal["reality"]
        if claim["origin"] == "agent"
    ]
    assert inferred
    assert all(claim["confidence"] == "inferred" for claim in inferred)
    assert snapshot["constraints"]
    assert all(item["confidence"] == "stated" for item in snapshot["constraints"])
    packet = json.dumps(snapshot)
    atoms = load_atoms(EPISODE / "gold" / "atoms.yaml")
    scored = score_packet(split_units(packet, "contextd"), atoms, _Tokens(), raw_text=packet)
    assert scored["missing_atoms"] == []
    assert scored["required_recall"] == 1
    assert scored["stale_as_current"] == 0
    assert scored["stale_marked"] >= 1
    assert scored["unresolved_present"] is True
    on_disk = json.loads((EPISODE / "packets" / "contextd_component2.txt").read_text())
    assert on_disk == snapshot


def test_rust_extractor_matches_python_on_the_episode(tmp_path: Path) -> None:
    events = episode_events()
    python = {(claim.kind, claim.text, claim.confidence) for claim in extract(events).claims}
    log_path = tmp_path / "events.jsonl"
    packet_path = tmp_path / "packet.json"
    report_path = tmp_path / "report.json"
    log_path.write_text("".join(json.dumps(event) + "\n" for event in events))
    result = subprocess.run(
        [
            "cargo",
            "run",
            "-q",
            "-p",
            "broker",
            "--bin",
            "project_claims",
            "--",
            "--extract",
            str(log_path),
            str(packet_path),
            str(report_path),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    report = json.loads(report_path.read_text())
    rust = {
        (item["kind"], item["text"], item["confidence"])
        for item in report["accepted"]
        if item["kind"] != "retract"
    }
    assert rust == python
    assert report["snapshot"]["focal_task"]["id"] == "t_a58f76e3ca4078e8a849ed9682f450cc"


def test_focal_command_is_the_unsuperseded_pytest() -> None:
    failing = focal_command(episode_events())
    assert failing is not None
    command = failing["payload"]["command"]
    assert collapse(command).startswith("python -m pytest")
    assert "test_pool_counts.py" in command


class _Tokens:
    def encode_ordinary(self, text: str) -> list[str]:
        return text.split()


@pytest.mark.skipif(not GATE.is_file(), reason="projector gate missing")
def test_gate_file_is_the_one_under_test() -> None:
    assert "an extracted claim cannot be marked observed" in GATE.read_text()
