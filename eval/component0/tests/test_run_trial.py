from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from harness import run_trial
from harness.common import load_pins
from harness.run_trial import (
    RefusedError,
    TokenMeter,
    TrialOptions,
    codex_argv,
    docker_argv,
    load_agent_config,
    prepare_trial_dir,
    scrub_check,
    stub_argv,
    stub_fix_data,
    validate_final_tests,
)
from harness.score import solved_from

CONFIG = load_agent_config()
FAKE_SECRET = "c0-test-value-not-a-credential-91b2"


def build_argv(tmp_path: Path, command: list[str], stub: bool = False) -> list[str]:
    return docker_argv(CONFIG, container_name="c0-test", workspace=tmp_path / "ws",
                       codex_home=tmp_path / "home", out_dir=tmp_path / "out", command=command,
                       stub_dir=tmp_path / "stub" if stub else None)  # fmt: skip


def test_codex_argv_uses_every_configured_flag() -> None:
    argv = codex_argv(CONFIG)
    trial = CONFIG["trial"]
    assert argv[:3] == ["codex", "exec", "--json"]
    for token in trial["codex_exec"]:
        if "{" not in token:
            assert token in argv
    overrides = trial["config_overrides"]
    start = argv.index(overrides[1]) - 1
    assert argv[start : start + len(overrides)] == overrides
    assert argv[argv.index("-m") + 1] == CONFIG["codex"]["model"]
    assert argv[argv.index("-C") + 1] == CONFIG["workspace"]["path"]
    effort = f'model_reasoning_effort="{CONFIG["codex"]["reasoning_effort"]}"'
    assert effort in argv
    assert "--dangerously-bypass-approvals-and-sandbox" in argv
    assert argv[-1] == "-"
    assert not any(re.search(r"\{[A-Za-z_]+\}", token) for token in argv)


def test_docker_argv_isolation_and_no_secret(tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    monkeypatch.setenv("CODEX_API_KEY", FAKE_SECRET)
    argv = build_argv(tmp_path, codex_argv(CONFIG))
    run_trial.assert_no_secret_in_argv(argv)
    assert not any(FAKE_SECRET in t for t in argv)
    assert argv[argv.index("CODEX_API_KEY") - 1] == "-e"
    assert not any(t.startswith("CODEX_API_KEY=") for t in argv)
    image = CONFIG["image"]["name"]
    docker_part = argv[: argv.index(image)]
    assert docker_part[:3] == ["docker", "run", "--rm"]
    assert docker_part[docker_part.index("--network") + 1] == CONFIG["egress"]["internal_network"]
    assert docker_part.count("--network") == 1
    assert docker_part[docker_part.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges:true" in docker_part
    assert docker_part[docker_part.index("--user") + 1] == CONFIG["image"]["user"]
    for flag in ("--cpus", "--memory", "--pids-limit"):
        assert flag in docker_part
    assert "HTTPS_PROXY=http://egress:8888" in docker_part
    mounts = [docker_part[i + 1] for i, t in enumerate(docker_part) if t == "-v"]
    assert mounts == [f"{tmp_path / 'ws'}:/workspace", f"{tmp_path / 'home'}:/home/agent/.codex",
                      f"{tmp_path / 'out'}:/out"]  # fmt: skip
    assert argv[argv.index(image) + 1 :] == codex_argv(CONFIG)


def test_stub_mount_only_in_stub_mode(tmp_path: Path) -> None:
    codex = build_argv(tmp_path, codex_argv(CONFIG))
    assert not any(":/stub" in t for t in codex)
    stub = build_argv(tmp_path, stub_argv("stub-oracle"), stub=True)
    assert f"{tmp_path / 'stub'}:/stub:ro" in stub


def test_assert_no_secret_rejects_inline_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_API_KEY", FAKE_SECRET)
    with pytest.raises(run_trial.TrialError):
        run_trial.assert_no_secret_in_argv(["docker", "-e", f"CODEX_API_KEY={FAKE_SECRET}"])


def usage_line(inp: int, out: int) -> str:
    usage = {"input_tokens": inp, "cached_input_tokens": 0, "cache_write_input_tokens": 0,
             "output_tokens": out, "reasoning_output_tokens": 0}  # fmt: skip
    return json.dumps({"type": "turn.completed", "usage": usage})


def token_count_line(inp: int, out: int) -> str:
    total = {
        "input_tokens": inp,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": out,
        "reasoning_output_tokens": 0,
        "total_tokens": inp + out,
    }
    return json.dumps({"timestamp": "t", "ordinal": 1, "type": "event_msg", "payload": {
        "type": "token_count", "info": {"total_token_usage": total}}})  # fmt: skip


def test_token_meter_thread_totals_not_summed() -> None:
    meter = TokenMeter(cap=1000)
    assert not meter.observe_rollout_line(token_count_line(300, 50))
    assert not meter.observe_rollout_line(token_count_line(600, 100))
    assert meter.total == 700
    assert not meter.observe_exec_line(usage_line(600, 100))
    assert meter.total == 700
    assert meter.observe_exec_line(usage_line(950, 60))
    assert meter.summary()["input_plus_output"] == 1010
    assert meter.source == "exec"


def test_token_meter_ignores_other_lines_and_no_cap() -> None:
    meter = TokenMeter(cap=None)
    for line in ("not json", json.dumps({"type": "item.completed", "item": {}}),
                 json.dumps({"type": "event_msg", "payload": {"type": "token_count",
                                                              "info": None}})):  # fmt: skip
        assert not meter.observe_exec_line(line)
        assert not meter.observe_rollout_line(line)
    assert not meter.observe_exec_line(usage_line(10**9, 10**9))
    assert meter.total == 2 * 10**9


def test_final_tests_schema_and_solved() -> None:
    good = {
        "visible": "pass",
        "suite": "pass",
        "unasync_check": "pass",
        "apply": "pass",
        "hidden": {"test_pool_cap_hidden.py::test_cap_sync_max_three_four_origins": "pass"},
    }
    validate_final_tests(good)
    assert solved_from(good) is True
    failed = run_trial.failed_final_tests(load_pins())
    validate_final_tests(failed)
    assert solved_from(failed) is False
    assert set(failed["hidden"]) == {p.name for p in load_pins().hidden_tests.glob("test_*.py")}
    for bad in (
        {**good, "extra": "x"},
        {**good, "visible": "passed"},
        {**good, "hidden": {}},
        {k: v for k, v in good.items() if k != "apply"},
    ):
        with pytest.raises(ValueError):
            validate_final_tests(bad)


def test_refuse_overwrite(tmp_path: Path) -> None:
    trial = tmp_path / "oracle-r1"
    trial.mkdir()
    (trial / "score.json").write_text("{}")
    with pytest.raises(RefusedError, match="completed trial"):
        prepare_trial_dir(trial, force=False)
    assert (trial / "score.json").exists()
    prepare_trial_dir(trial, force=True)
    assert trial.exists() and not any(trial.iterdir())


def test_refuse_incomplete_dir(tmp_path: Path) -> None:
    trial = tmp_path / "none-r1"
    trial.mkdir()
    (trial / "meta.json").write_text("{}")
    with pytest.raises(RefusedError, match="incomplete"):
        prepare_trial_dir(trial, force=False)


def test_codex_refused_without_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    out = tmp_path / "none-r1"
    with pytest.raises(RefusedError, match="CODEX_API_KEY"):
        run_trial.run_trial(TrialOptions(arm="none", rep=1, out=out, agent="codex"))
    assert not out.exists()


def test_scrub_check(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("nothing here")
    assert scrub_check(tmp_path, None)["status"] == "skipped"
    assert scrub_check(tmp_path, FAKE_SECRET)["status"] == "clean"
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "leak.bin").write_bytes(b"\x00" + FAKE_SECRET.encode() + b"\x00")
    result = scrub_check(tmp_path, FAKE_SECRET)
    assert result == {"status": "LEAK", "files_scanned": 2, "hits": ["sub/leak.bin"]}


def test_stub_fix_data_matches_pinned_file() -> None:
    from harness.gold import pinned_tree

    data = stub_fix_data(load_pins())
    text = pinned_tree().read(data["path"])
    assert text is not None and text.count(data["old"]) == 1
    assert "len(self._connections) < self._max_connections" in data["new"]
    assert data["read_start"] < 324 < data["read_end"]
