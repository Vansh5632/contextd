from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.run_matrix import (
    build_schedule,
    cumulative_tokens,
    gate_statuses,
    load_or_create_schedule,
    pending_trials,
    refusal,
    select_arms,
)

ARMS = ["none", "raw", "summary", "oracle", "stale-only"]


def test_schedule_deterministic_for_seed() -> None:
    a = build_schedule(ARMS, 5, 20260930, "codex")
    b = build_schedule(ARMS, 5, 20260930, "codex")
    c = build_schedule(ARMS, 5, 1, "codex")
    assert a == b
    assert [t["arm"] for t in a["trials"]] != [t["arm"] for t in c["trials"]]
    assert len(a["trials"]) == 25
    assert [t["index"] for t in a["trials"]] == list(range(25))


def test_schedule_paired_by_rep() -> None:
    schedule = build_schedule(ARMS, 5, 20260930, "codex")
    trials = schedule["trials"]
    for rep in range(1, 6):
        block = trials[(rep - 1) * 5 : rep * 5]
        assert {t["rep"] for t in block} == {rep}
        assert sorted(t["arm"] for t in block) == sorted(ARMS)
    orders = {tuple(t["arm"] for t in trials[i : i + 5]) for i in range(0, 25, 5)}
    assert len(orders) > 1
    assert all(t["dir"] == f"{t['arm']}-r{t['rep']}" for t in trials)


def test_schedule_resume_reuses_and_rejects_changes(tmp_path: Path) -> None:
    schedule = build_schedule(ARMS, 2, 20260930, "stub-oracle")
    load_or_create_schedule(tmp_path, schedule)
    written = (tmp_path / "schedule.json").read_text()
    assert (
        load_or_create_schedule(tmp_path, build_schedule(ARMS, 2, 20260930, "stub-oracle"))
        == schedule
    )
    assert (tmp_path / "schedule.json").read_text() == written
    for changed in (build_schedule(ARMS, 2, 7, "stub-oracle"),
                    build_schedule(ARMS, 3, 20260930, "stub-oracle"),
                    build_schedule(ARMS, 2, 20260930, "codex")):  # fmt: skip
        with pytest.raises(SystemExit):
            load_or_create_schedule(tmp_path, changed)


def make_trial(run_dir: Path, name: str, tokens: int, scored: bool) -> None:
    trial = run_dir / name
    trial.mkdir(parents=True)
    (trial / "meta.json").write_text(json.dumps({"tokens": {"input_plus_output": tokens}}))
    if scored:
        (trial / "score.json").write_text("{}")


def test_resume_skips_scored_and_recorded_failures(tmp_path: Path) -> None:
    schedule = build_schedule(ARMS, 1, 20260930, "stub-oracle")
    dirs = [t["dir"] for t in schedule["trials"]]
    make_trial(tmp_path, dirs[0], 1000, scored=True)
    make_trial(tmp_path, dirs[1], 250, scored=False)
    recorded = {dirs[1]: {"status": "error"}}
    pending = [t["dir"] for t in pending_trials(tmp_path, schedule, recorded, False)]
    assert pending == dirs[2:]
    retry = [t["dir"] for t in pending_trials(tmp_path, schedule, recorded, True)]
    assert retry == dirs[1:]
    assert cumulative_tokens(tmp_path, schedule) == 1250


def test_refusals() -> None:
    assert "--stub" in (refusal("stub-oracle", False, False) or "")
    assert refusal("stub-oracle", True, False) is None
    assert refusal("codex", True, True) is not None


def test_codex_refusals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    assert "CODEX_API_KEY" in (refusal("codex", False, True) or "")
    monkeypatch.setenv("CODEX_API_KEY", "c0-test-value-not-a-credential")
    assert "--i-accept-spend" in (refusal("codex", False, False) or "")
    assert refusal("codex", False, True) is None


def test_arms_filter_records_subset_and_resume_rejects_mismatch(tmp_path: Path) -> None:
    assert select_arms(ARMS, None) == ARMS
    selected = select_arms(ARMS, "oracle,none")
    assert selected == ["none", "oracle"]
    assert select_arms(ARMS, "none,oracle") == selected
    schedule = build_schedule(selected, 2, 20260930, "stub-oracle")
    assert schedule["arms"] == ["none", "oracle"]
    assert build_schedule(selected, 2, 20260930, "stub-oracle") == schedule
    assert [t["arm"] for t in schedule["trials"]] != [
        t["arm"] for t in build_schedule(selected, 2, 1, "stub-oracle")["trials"]
    ]
    for rep in (1, 2):
        block = [t for t in schedule["trials"] if t["rep"] == rep]
        assert sorted(t["arm"] for t in block) == ["none", "oracle"]
    load_or_create_schedule(tmp_path, schedule)
    with pytest.raises(SystemExit):
        load_or_create_schedule(tmp_path, build_schedule(ARMS, 2, 20260930, "stub-oracle"))
    with pytest.raises(ValueError, match="unknown arm"):
        select_arms(ARMS, "contextd")


def test_gate_statuses_reads_summary_only() -> None:
    output = ("== G4: fixtures\nG4 stuff\n\n== summary\ngate  status   detail\n"
              "----  -------  ------\nG1    SKIPPED  --skip-docker\nG3    PASS     ok\n"
              "G4    PASS     8/8\nG5    FAIL     3/8\n")  # fmt: skip
    assert gate_statuses(output) == {"G1": "SKIPPED", "G3": "PASS", "G4": "PASS", "G5": "FAIL"}
