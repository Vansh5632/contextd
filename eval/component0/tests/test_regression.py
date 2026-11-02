from __future__ import annotations

import json
import shutil
from pathlib import Path

from harness.contextd_ladder import load_events
from harness.contextd_replay import build_parser
from harness.regression import (
    evaluate,
    generation_dirs,
    load_regression,
    load_regressions,
    main,
    matching_events,
    verify_files,
)
from harness.validate import gate_r1

PASS_TEXT = "ran `python -m pytest ... tests/test_pool_counts.py` — exited 1"
UNMARKED_TEXT = "ran `python -m pytest ... tests/test_pool_counts.py`"


def _reg():
    found = [reg for reg in load_regressions() if reg.data["id"] == "briefing-selection"]
    assert len(found) == 1
    return found[0]


def _write_candidate(dest: Path, *, text: str | None, drop_target: bool) -> None:
    reg = _reg()
    src = reg.dir / "baseline" / "replay" / "gen-1"
    dest.mkdir(parents=True)
    events = load_events(src / "events.jsonl")
    matches = matching_events(events, reg.data["target"])
    assert matches
    if drop_target:
        drop = {event.get("id") for event in matches}
        kept = [event for event in events if event.get("id") not in drop]
        body = "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in kept)
        (dest / "events.jsonl").write_text(body)
    else:
        shutil.copyfile(src / "events.jsonl", dest / "events.jsonl")
    briefing = json.loads((src / "briefing.reply").read_text())
    if text is not None:
        event = matches[0]
        briefing["recent_activity"].insert(
            0,
            {
                "id": event["id"],
                "source": "shell",
                "text": text,
                "timestamp_ms": event["timestamp_ms"],
                "relevance": 0.9,
            },
        )
    (dest / "briefing.reply").write_text(json.dumps(briefing))


def test_real_baseline_verifies_and_reproduces() -> None:
    reg = _reg()
    assert verify_files(reg) == []
    expect = reg.data["baseline_expect"]
    gens = generation_dirs(reg.dir / "baseline" / "replay")
    assert len(gens) == 3
    for gen in gens:
        result = evaluate(
            (gen / "briefing.reply").read_text(),
            load_events(gen / "events.jsonl"),
            reg.data["target"],
        )
        assert result["checks"] == expect
    assert main(["check", "--id", "briefing-selection"]) == 0


def test_recorded_contextd_head_is_contextd_not_the_workspace() -> None:
    reg = _reg()
    provenance = json.loads(
        (reg.dir / "baseline" / "replay" / "gen-1" / "provenance.json").read_text()
    )
    assert reg.data["contextd"]["head"] == provenance["contextd_head"]
    assert reg.data["contextd"]["head"] != provenance["head"]


def test_gen1_evaluate_matches_diagnosis() -> None:
    reg = _reg()
    gen = reg.dir / "baseline" / "replay" / "gen-1"
    result = evaluate(
        (gen / "briefing.reply").read_text(),
        load_events(gen / "events.jsonl"),
        reg.data["target"],
    )
    assert result["checks"]["stored"] is True
    assert result["checks"]["selected"] is False
    assert result["info"]["failure_rank"] is None
    assert result["info"]["fs_same_timestamp_items"] >= 4
    assert result["info"]["commit_items"] >= 2


def test_candidate_with_failure_selected_passes(tmp_path: Path) -> None:
    dest = tmp_path / "candidate"
    _write_candidate(dest, text=PASS_TEXT, drop_target=False)
    assert main(["check", "--id", "briefing-selection", "--candidate", str(dest)]) == 0


def test_selected_without_failure_mark_exits_1(tmp_path: Path) -> None:
    dest = tmp_path / "candidate"
    _write_candidate(dest, text=UNMARKED_TEXT, drop_target=False)
    reg = _reg()
    result = evaluate(
        (dest / "briefing.reply").read_text(),
        load_events(dest / "events.jsonl"),
        reg.data["target"],
    )
    assert result["checks"]["selected"] is True
    assert result["checks"]["marked_failed"] is False
    assert main(["check", "--id", "briefing-selection", "--candidate", str(dest)]) == 1


def test_removed_target_event_is_observation_regressed(tmp_path: Path, capsys) -> None:
    dest = tmp_path / "candidate"
    _write_candidate(dest, text=None, drop_target=True)
    reg = _reg()
    result = evaluate(
        (dest / "briefing.reply").read_text(),
        load_events(dest / "events.jsonl"),
        reg.data["target"],
    )
    assert result["checks"]["stored"] is False
    code = main(["check", "--id", "briefing-selection", "--candidate", str(dest)])
    captured = capsys.readouterr()
    assert code == 1
    assert "observation regressed" in captured.out


def test_tampered_baseline_file_is_reported(tmp_path: Path) -> None:
    dest = tmp_path / "briefing-selection"
    shutil.copytree(_reg().dir, dest)
    target = dest / "baseline" / "replay" / "gen-1" / "events.jsonl"
    blob = bytearray(target.read_bytes())
    blob[0] ^= 0xFF
    target.write_bytes(blob)
    problems = verify_files(load_regression(dest / "regression.yaml"))
    assert any("events.jsonl" in problem for problem in problems)


def test_replay_parser_accepts_no_freeze() -> None:
    assert build_parser().parse_args(["--no-freeze"]).no_freeze is True


def test_validate_r1_passes() -> None:
    assert gate_r1().status == "PASS"
