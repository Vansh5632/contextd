from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from harness import arms, gate_g3
from harness.common import EPISODES_DIR
from harness.contextd_ladder import ladder
from harness.packet_score import PACKET_KINDS, load_atoms, score_packet, split_units

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "contextd"
EPISODE_DIR = EPISODES_DIR / "limiter-handoff"
ATOMS = load_atoms(FIXTURES / "atoms.yaml")


class WordTokenizer:
    def encode_ordinary(self, text: str) -> list[str]:
        return text.split()


def test_packet_kinds_are_explicit() -> None:
    assert PACKET_KINDS["raw"] == "history"
    assert PACKET_KINDS["summary"] == "paragraphs"
    assert PACKET_KINDS["oracle"] == "paragraphs"
    assert PACKET_KINDS["stale-only"] == "paragraphs"
    assert PACKET_KINDS["contextd"] == "contextd"
    assert arms.packet_kind("contextd") == "contextd"
    assert arms.PACKET_FILES["contextd"] == "contextd.txt"


def test_split_units_contextd_fixture() -> None:
    text = (FIXTURES / "briefing.json").read_text()
    assert split_units(text, "contextd") == [
        "[intent] finish the pool cap",
        "[recent shell] idle connections count toward the cap",
        "[history note] earlier note about the pool",
        "[related symbol] ConnectionPool",
        "[archived] old sessions digested",
    ]


def test_split_units_standing_fields() -> None:
    text = json.dumps(
        {
            "constraints": [{"text": "do not change the public signature"}],
            "passed_checks": [
                {"text": "pytest now passes", "evidence_ids": ["pass-1"]}
            ],
            "open_work": [
                {
                    "decisions": [
                        {"text": "Idle connections should count toward the pool cap"}
                    ],
                    "ruled_out": [
                        {"text": "mocking the backend does not reproduce the cap"}
                    ],
                }
            ],
            "other_active_tasks": [
                {"summary": "python -m pytest tests/test_export.py exited 1"}
            ],
        }
    )
    assert split_units(text, "contextd") == [
        "[constraint] do not change the public signature",
        "[passed] pytest now passes",
        "[decision] Idle connections should count toward the pool cap",
        "[ruled_out] mocking the backend does not reproduce the cap",
        "[other] python -m pytest tests/test_export.py exited 1",
    ]


def test_split_units_claim_view() -> None:
    text = json.dumps(
        {
            "focal_task": {
                "intent": {"text": "fix the pool cap"},
                "reality": [{"text": "pytest exited 1", "evidence_ids": ["e1"]}],
                "constraints": [{"text": "keep the public signatures unchanged"}],
                "next_action": {"text": "Re-run and inspect: pytest tests/test_pool_counts.py"},
            },
            "other_active_tasks": [{"summary": "git commit exited 0"}],
        }
    )
    assert split_units(text, "contextd") == [
        "[intent] fix the pool cap",
        "[constraint] keep the public signatures unchanged",
        "[reality] pytest exited 1",
        "[next] Re-run and inspect: pytest tests/test_pool_counts.py",
        "[other] git commit exited 0",
    ]


def test_split_units_contextd_rejects_invalid_json() -> None:
    with pytest.raises(ValueError, match="invalid contextd JSON"):
        split_units("{not json", "contextd")
    with pytest.raises(ValueError, match="JSON object"):
        split_units("[]", "contextd")


def test_packet_tokens_count_raw_text_not_renderings() -> None:
    raw = (FIXTURES / "briefing.json").read_text()
    units = split_units(raw, "contextd")
    result = score_packet(units, ATOMS, WordTokenizer(), raw_text=raw)
    assert result["packet_tokens"] == len(raw.split())
    assert result["packet_tokens"] != sum(len(unit.split()) for unit in units)
    unchanged = score_packet(units, ATOMS, WordTokenizer())
    assert unchanged["packet_tokens"] == sum(len(unit.split()) for unit in units)
    assert result["unit_precision"] == unchanged["unit_precision"]


def test_ladder_observation_and_selection() -> None:
    briefing = (FIXTURES / "briefing.json").read_text()
    result = ladder(ATOMS, FIXTURES / "events.jsonl", briefing, WordTokenizer())
    by_id = {row["id"]: row for row in result["atoms"]}
    assert by_id["idle_not_counted"]["stored"] is True
    assert by_id["idle_not_counted"]["selected"] is False
    assert by_id["no_deps"]["stored"] is True
    assert by_id["idle_counts"]["selected"] is True
    assert by_id["idle_counts"]["stored"] is False
    assert result["observation_recall"] == pytest.approx(2 / 3)
    assert result["selection_recall"] == pytest.approx(1 / 3)
    assert result["events_by_source"] == {"editor": 1, "shell": 1}
    assert result["stored_events"] == 2
    assert result["stored_events_in_briefing"] == 1
    assert result["briefing_tokens"] == len(briefing.split())


def test_prompt_contextd_bytes(tmp_path: Path) -> None:
    episode = tmp_path / "ep"
    (episode / "packets").mkdir(parents=True)
    packet = (FIXTURES / "briefing.json").read_bytes()
    if not packet.endswith(b"\n"):
        packet += b"\n"
    (episode / "packets" / "contextd.txt").write_bytes(packet)
    (episode / "wrapper.txt").write_text("Notes from earlier sessions on this repo:\n")
    (episode / "prompt.txt").write_text("Continue the interrupted work in this repo.\n")
    expected = (
        b"Notes from earlier sessions on this repo:\n"
        + packet.rstrip(b"\n")
        + b"\n\nContinue the interrupted work in this repo.\n"
    )
    assert arms.build_prompt("contextd", episode).encode() == expected


def test_prompt_cli_rejects_unlisted_contextd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _copy_episode(tmp_path / "limiter-handoff")
    monkeypatch.setattr(arms, "EPISODES_DIR", tmp_path)
    with pytest.raises(SystemExit):
        arms.main(["prompt", "--arm", "contextd", "--episode", "limiter-handoff"])


def test_enable_arm_refuses_when_packet_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    episode = tmp_path / "ep"
    episode.mkdir()
    (episode / "pins.toml").write_text(
        '[arms]\nnames = ["none"]\nraw_budget_tokens = 8\ntokenizer = "o200k_base"\n'
    )
    monkeypatch.setattr(arms, "EPISODES_DIR", tmp_path)
    with pytest.raises(SystemExit, match="missing"):
        arms.main(["enable-arm", "contextd", "--episode", "ep"])
    assert "contextd" not in (episode / "pins.toml").read_text()


def _copy_episode(dst: Path) -> None:
    for rel in (
        "pins.toml",
        "prompt.txt",
        "wrapper.txt",
        "history/history.jsonl",
        "gold/atoms.yaml",
        "packets/raw.txt",
        "packets/summary.txt",
        "packets/oracle.txt",
        "packets/stale_only.txt",
    ):
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(EPISODE_DIR / rel, target)
    pins = dst / "pins.toml"
    # Drop the contextd arm without touching a later contextd-new name.
    pins.write_text(pins.read_text().replace(', "contextd",', ",").replace(', "contextd"]', "]"))


def test_enable_arm_appends_and_freezes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    episode = tmp_path / "limiter-handoff"
    _copy_episode(episode)
    packet = (FIXTURES / "briefing.json").read_text()
    (episode / "packets" / "contextd.txt").write_text(packet)
    (episode / "packets" / "contextd_provenance.json").write_text(
        json.dumps({"head_matches_followup": True}) + "\n"
    )
    monkeypatch.setattr(arms, "EPISODES_DIR", tmp_path)
    assert arms.main(["enable-arm", "contextd", "--episode", "limiter-handoff"]) == 0
    config = arms.load_config(episode)
    assert config.names[-1] == "contextd"
    digest = hashlib.sha256((episode / "packets" / "contextd.txt").read_bytes()).hexdigest()
    assert config.freeze["contextd_txt"] == digest
    assert arms.check(episode) == []
    rendered = arms.main(["prompt", "--arm", "contextd", "--episode", "limiter-handoff"])
    assert rendered == 0


def test_contextd_gate_checks_sha_and_provenance(tmp_path: Path) -> None:
    episode = tmp_path / "ep"
    (episode / "packets").mkdir(parents=True)
    packet = b'{"recent_activity": []}\n'
    (episode / "packets" / "contextd.txt").write_bytes(packet)
    (episode / "packets" / "contextd_provenance.json").write_text(
        '{"head_matches_followup": true}\n'
    )
    digest = hashlib.sha256(packet).hexdigest()
    checks = gate_g3.contextd_checks(episode, {"contextd_txt": digest})
    assert all(c.ok for c in checks)
    bad = gate_g3.contextd_checks(episode, {"contextd_txt": "0" * 64})
    assert any(c.name == "contextd sha256" and not c.ok for c in bad)
