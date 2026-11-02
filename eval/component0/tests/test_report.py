from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from harness import report


def score(arm: str, rep: int, ttf: int | None, repeated: int = 0, forbidden: int = 0,
          solved: bool | None = True) -> dict[str, Any]:  # fmt: skip
    return {"arm": arm, "rep": rep, "gold_variant": "real", "turns_to_frontier": ttf,
            "repeated_work_calls": repeated, "forbidden_count": forbidden, "solved": solved,
            "constraint_violations": [], "failure_tag": "ok", "packet": None,
            "usage": {"input_tokens": 100, "output_tokens": 10}, "model": "m"}  # fmt: skip


def write_run(run_dir: Path, scores: list[dict[str, Any]]) -> None:
    for s in scores:
        trial = run_dir / f"{s['arm']}-r{s['rep']}"
        trial.mkdir(parents=True)
        (trial / "trace.jsonl").write_text(
            json.dumps({"kind": "header", "arm": s["arm"], "rep": s["rep"], "schema": 1}) + "\n"
        )
        (trial / "score.json").write_text(json.dumps(s))
        (trial / "meta.json").write_text(json.dumps({"agent": "stub-oracle"}))


def test_bootstrap_deterministic() -> None:
    values = [1.0, -2.0, 0.0, 3.0, -1.0]
    a = report.bootstrap_ci(values)
    b = report.bootstrap_ci(values)
    assert a == b and a is not None
    lo, hi = a
    assert lo <= sum(values) / len(values) <= hi
    assert report.bootstrap_ci(values, seed=1) != a
    assert report.bootstrap_ci([]) is None
    assert report.bootstrap_ci([2.0, 2.0]) == (2.0, 2.0)


def test_null_ttf_handling() -> None:
    scores = [score("none", 1, None), score("none", 2, 4), score("none", 3, None)]
    assert report.median_ttf(scores) is None
    assert report.median_ttf([score("none", 1, None), score("none", 2, 4),
                              score("none", 3, 2)]) == 4.0  # fmt: skip
    summary = report.arm_summary([{"score": s, "meta": {}} for s in scores])
    assert summary["ttf_reached"] == 1
    assert summary["ttf_mean_reached"] == 4.0
    assert summary["ttf_median"] is None


def test_paired_deltas_skip_unreached_ttf() -> None:
    trials = [{"score": s, "meta": {}} for s in (
        score("none", 1, 5), score("oracle", 1, 1),
        score("none", 2, None), score("oracle", 2, 2),
        score("oracle", 3, 1),
    )]  # fmt: skip
    deltas = report.paired_deltas(trials, "oracle")
    assert deltas["reps"] == [1, 2]
    assert deltas["ttf_both_reached"]["per_rep"] == [-4.0]
    assert deltas["frontier_reached"]["per_rep"] == [0.0, 1.0]
    assert deltas["solved"]["n_pairs"] == 2


def test_g6_pass_on_synthetic_scores(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    write_run(run_dir, [
        score("none", 1, 6, repeated=3), score("oracle", 1, 1), score("stale-only", 1, 4,
                                                                      forbidden=2),
        score("none", 2, None, repeated=1), score("oracle", 2, 2), score("stale-only", 2, 3),
    ])  # fmt: skip
    built = report.build_report(run_dir)
    assert built["g6"]["status"] == "PASS"
    assert built["n_trials"] == 6
    assert built["arms"]["stale-only"]["forbidden_rate"] == 0.5


def test_g6_fail_when_oracle_not_faster(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    write_run(run_dir, [score("none", 1, 2), score("oracle", 1, 3),
                        score("stale-only", 1, 2, forbidden=1)])  # fmt: skip
    built = report.build_report(run_dir)
    assert built["g6"]["status"] == "FAIL"
    assert "median ttf oracle < none" in built["g6"]["log"]


def test_render_writes_no_composite(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    write_run(run_dir, [score("none", 1, 2), score("oracle", 1, 1)])
    assert report.main([str(run_dir)]) == 0
    text = (run_dir / "report.md").read_text()
    assert "composite" not in text.lower()
    data = json.loads((run_dir / "report.json").read_text())
    assert set(data["arms"]) == {"none", "oracle"}
    assert data["g6"]["status"] == "not applicable"


def test_g6_not_applicable_without_required_arms(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(report, "EPISODES_DIR", tmp_path / "no-episodes")
    run_dir = tmp_path / "run"
    write_run(run_dir, [score("contextd", 1, 2)])
    assert report.main([str(run_dir)]) == 0
    captured = capsys.readouterr().out
    assert "G6: not applicable to this run" in captured
    assert "predictions: not registered" in captured
    data = json.loads((run_dir / "report.json").read_text())
    assert data["g6"]["status"] == "not applicable"
    assert "predictions" not in data


def test_unreached_ttf_median_counts_as_infinite() -> None:
    pred = {"id": "p", "metric": "ttf_median", "comparison": "contextd vs oracle",
            "direction": "higher"}  # fmt: skip
    current = {"contextd": {"n": 5, "ttf_median": None}}
    baseline = {"oracle": {"n": 5, "ttf_median": 2.0}}
    result = report.judge_prediction(pred, current, baseline)
    assert result["ok"] is True and result["left"] == "not reached"
    unscored = report.judge_prediction(pred, {"contextd": {"n": 0, "ttf_median": None}}, baseline)
    assert unscored["ok"] is False and unscored["left"] is None


def test_predictions_match_frozen_sha(tmp_path: Path) -> None:
    prereg = tmp_path / "prereg_contextd.yaml"
    prereg.write_text(
        "frozen_packet_sha256: abc\n"
        "predictions:\n"
        "  - {id: p1, metric: solved_rate, comparison: contextd vs none,"
        " direction: higher, rationale: stored the decision}\n"
    )
    current = {"contextd": {"solved_rate": 0.8}, "none": {"solved_rate": 0.2}}
    status = report.prediction_status(prereg, "abc", current)
    assert status is not None and status["results"][0]["ok"] is True
    assert status["results"][0]["where"] == "within-run"
    assert report.prediction_status(prereg, None, current) is None
    assert report.prediction_status(prereg, "other", current) is None
    cross = report.judge_prediction(
        {"id": "p2", "metric": "solved_rate", "comparison": "contextd vs none",
         "direction": "higher"},
        {"contextd": {"solved_rate": 0.1}},
        {"none": {"solved_rate": 0.9}},
    )  # fmt: skip
    assert cross["where"] == "cross-run" and cross["ok"] is False


def test_baseline_merge_and_cross_run_deltas(tmp_path: Path) -> None:
    current = tmp_path / "cur"
    baseline = tmp_path / "base"
    contextd = score("contextd", 1, 2, solved=True)
    contextd["packet"] = {
        "required_recall": 0.5,
        "unit_precision": 0.5,
        "token_precision": 0.5,
        "ladder": {"observation_recall": 0.25, "selection_recall": 0.5},
    }
    write_run(current, [contextd, score("none", 1, 5, solved=False)])
    write_run(baseline, [score("none", 1, 9, solved=True), score("summary", 1, 4, solved=True)])
    built = report.build_report(current, baseline)
    assert set(built["arms"]) == {"contextd", "none"}
    assert set(built["baseline_arms"]) == {"none", "summary"}
    assert built["arms"]["contextd"]["ladder_observation_recall"] == 0.25
    assert built["arms"]["contextd"]["ladder_selection_recall"] == 0.5
    within = built["deltas"]["contextd"]["solved"]["per_rep"]
    cross = built["cross_run_deltas"]["contextd"]["solved"]["per_rep"]
    assert within == [1.0]
    assert cross == [0.0]
    text = report.render(built)
    assert "| run | arm |" in text
    assert "Cross-run paired deltas vs baseline none" in text
    assert "Within-run paired per-rep deltas" in text
    assert "contextd ladder: observation recall 0.25, selection recall 0.50" in text
    assert built["g6"]["status"] == "not applicable"
