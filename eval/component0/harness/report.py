"""Summarize a scored run: per-arm metrics, paired deltas vs `none`, G6.

Usage (from eval/component0):
    python -m harness.report runs/RUN_ID [--baseline-run runs/OTHER]

Reads score.json (real gold) and meta.json from each trial dir; writes RUN_ID/report.md and
RUN_ID/report.json. Bootstrap CIs resample reps (paired deltas) with a fixed seed. There is
deliberately no composite score. G6 runs only when this run scored oracle, none, and
stale-only. --baseline-run adds that run's arms and a cross-run delta section.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import random
import re
import statistics
from pathlib import Path
from typing import Any, Callable

import yaml

from harness import validate
from harness.common import DEFAULT_EPISODE, EPISODES_DIR
from harness.run_trial import load_agent_config

BOOTSTRAP_SEED = 20260930
BOOTSTRAP_RESAMPLES = 10_000
BASELINE = "none"


def load_trials(run_dir: Path) -> list[dict[str, Any]]:
    trials = []
    for score_path in sorted(run_dir.glob("*/score.json")):
        score = json.loads(score_path.read_text())
        meta_path = score_path.parent / "meta.json"
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        trials.append({"dir": score_path.parent.name, "score": score, "meta": meta})
    return trials


def arm_order(run_dir: Path, trials: list[dict[str, Any]]) -> list[str]:
    schedule = run_dir / "schedule.json"
    if schedule.exists():
        return list(json.loads(schedule.read_text())["arms"])
    return sorted({t["score"]["arm"] for t in trials}, key=lambda a: (a != BASELINE, a))


# ---------------------------------------------------------------------------
# Statistics


def ttf_value(score: dict[str, Any]) -> float:
    value = score.get("turns_to_frontier")
    return math.inf if value is None else float(value)


def median_ttf(scores: list[dict[str, Any]]) -> float | None:
    """Median with not-reached as +inf; None when the median itself is not reached."""
    if not scores:
        return None
    value = statistics.median(ttf_value(s) for s in scores)
    return None if math.isinf(value) else value


def mean_or_none(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def tokens(trial: dict[str, Any]) -> int | None:
    meta_tokens = (trial["meta"].get("tokens") or {}).get("input_plus_output")
    if meta_tokens is not None:
        return int(meta_tokens)
    usage = trial["score"].get("usage")
    return usage["input_tokens"] + usage["output_tokens"] if usage else None


def bootstrap_ci(values: list[float], seed: int = BOOTSTRAP_SEED,
                 resamples: int = BOOTSTRAP_RESAMPLES,
                 alpha: float = 0.05) -> tuple[float, float] | None:  # fmt: skip
    """Percentile bootstrap CI of the mean."""
    if not values:
        return None
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(rng.choices(values, k=n)) / n for _ in range(resamples))
    lo = means[math.floor(alpha / 2 * resamples)]
    hi = means[math.ceil((1 - alpha / 2) * resamples) - 1]
    return lo, hi


# ---------------------------------------------------------------------------
# Tables


def arm_summary(trials: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [t["score"] for t in trials]
    n = len(scores)
    reached = [s["turns_to_frontier"] for s in scores if s.get("turns_to_frontier") is not None]
    packet = next((s["packet"] for s in scores if s.get("packet")), None)
    tags: dict[str, int] = {}
    for s in scores:
        tags[s["failure_tag"]] = tags.get(s["failure_tag"], 0) + 1
    token_values = [v for v in (tokens(t) for t in trials) if v is not None]
    out = {
        "n": n,
        "solved_rate": sum(1 for s in scores if s.get("solved") is True) / n if n else None,
        "unscored_tests": sum(1 for s in scores if s.get("solved") is None),
        "ttf_median": median_ttf(scores),
        "ttf_mean_reached": mean_or_none([float(v) for v in reached]),
        "ttf_reached": len(reached),
        "repeated_work_calls_mean": mean_or_none(
            [float(s["repeated_work_calls"]) for s in scores]
        ),  # fmt: skip
        "forbidden_rate": sum(1 for s in scores if s["forbidden_count"] > 0) / n if n else None,
        "constraint_violation_rate": (
            sum(1 for s in scores if s.get("constraint_violations")) / n if n else None
        ),
        "packet_required_recall": packet.get("required_recall") if packet else None,
        "packet_unit_precision": packet.get("unit_precision") if packet else None,
        "packet_token_precision": packet.get("token_precision") if packet else None,
        "tokens_mean": mean_or_none([float(v) for v in token_values]),
        "failure_tags": dict(sorted(tags.items())),
    }
    for score in scores:
        packet_block = score.get("packet")
        ladder = packet_block.get("ladder") if isinstance(packet_block, dict) else None
        if isinstance(ladder, dict) and "observation_recall" in ladder:
            out["ladder_observation_recall"] = ladder.get("observation_recall")
            out["ladder_selection_recall"] = ladder.get("selection_recall")
            break
    return out


DELTA_METRICS: dict[str, Callable[[dict[str, Any]], float | None]] = {
    "solved": lambda t: float(t["score"].get("solved") is True),
    "frontier_reached": lambda t: float(t["score"].get("turns_to_frontier") is not None),
    "ttf_both_reached": lambda t: (
        None
        if t["score"].get("turns_to_frontier") is None
        else float(t["score"]["turns_to_frontier"])
    ),  # fmt: skip
    "repeated_work_calls": lambda t: float(t["score"]["repeated_work_calls"]),
    "forbidden_any": lambda t: float(t["score"]["forbidden_count"] > 0),
    "constraint_any": lambda t: float(bool(t["score"].get("constraint_violations"))),
    "tokens": lambda t: None if tokens(t) is None else float(tokens(t) or 0),
}


def paired_deltas(trials: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    by_rep: dict[tuple[str, Any], dict[str, Any]] = {}
    for t in trials:
        by_rep[(t["score"]["arm"], t["score"]["rep"])] = t
    reps = sorted({r for a, r in by_rep if a == arm} & {r for a, r in by_rep if a == BASELINE},
                  key=lambda r: (r is None, r))  # fmt: skip
    out: dict[str, Any] = {"reps": reps}
    for name, fn in DELTA_METRICS.items():
        deltas = []
        for rep in reps:
            a, b = fn(by_rep[(arm, rep)]), fn(by_rep[(BASELINE, rep)])
            if a is not None and b is not None:
                deltas.append(a - b)
        ci = bootstrap_ci(deltas)
        out[name] = {"n_pairs": len(deltas), "per_rep": deltas,
                     "mean": mean_or_none(deltas), "ci95": list(ci) if ci else None}  # fmt: skip
    return out


def cross_run_deltas(current: list[dict[str, Any]], baseline: list[dict[str, Any]],
                     arm: str) -> dict[str, Any]:  # fmt: skip
    """Arm in the current run minus `none` in the baseline run, paired by rep."""
    mixed = [t for t in current if t["score"]["arm"] == arm]
    mixed += [t for t in baseline if t["score"]["arm"] == BASELINE]
    return paired_deltas(mixed, arm)


def run_g6(run_dir: Path) -> tuple[str, str, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        result = validate.gate_g6(run_dir)
    return result.status, result.detail, buffer.getvalue()


G6_ARMS = frozenset({"oracle", "none", "stale-only"})


def g6_block(run_dir: Path, trials: list[dict[str, Any]]) -> dict[str, str]:
    present = {t["score"]["arm"] for t in trials}
    if not G6_ARMS <= present:
        return {"status": "not applicable", "detail": "G6: not applicable to this run", "log": ""}
    status, detail, log = run_g6(run_dir)
    return {"status": status, "detail": detail, "log": log}


def frozen_contextd_sha(episode_dir: Path) -> str | None:
    import tomllib

    pins_path = episode_dir / "pins.toml"
    if not pins_path.is_file():
        return None
    with open(pins_path, "rb") as handle:
        freeze = tomllib.load(handle).get("freeze") or {}
    value = freeze.get("contextd_txt")
    return str(value) if value else None


def prediction_status(prereg_path: Path, frozen_sha: str | None,
                      current_arms: dict[str, dict[str, Any]],
                      baseline_arms: dict[str, dict[str, Any]] | None = None,
                      ) -> dict[str, Any] | None:  # fmt: skip
    """Evaluate preregistered predictions when their packet hash matches the freeze.

    Returns None when the file is missing, the hash is null, or it does not match
    the current frozen packet (caller prints `predictions: not registered`).
    """
    if not prereg_path.is_file():
        return None
    data = yaml.safe_load(prereg_path.read_text()) or {}
    recorded = data.get("frozen_packet_sha256")
    if not recorded or frozen_sha is None or str(recorded) != frozen_sha:
        return None
    results = []
    for pred in data.get("predictions") or []:
        results.append(judge_prediction(pred, current_arms, baseline_arms or {}))
    return {"frozen_packet_sha256": str(recorded), "results": results}


def _metric(summary: dict[str, Any] | None, metric: str) -> float | None:
    if summary is None:
        return None
    if summary.get(metric) is None:
        # Same convention as the per-arm table: a scored arm whose median is null never
        # reached the frontier in most trials, so its ttf median is infinite.
        if metric == "ttf_median" and summary.get("n"):
            return math.inf
        return None
    return float(summary[metric])


def judge_prediction(pred: dict[str, Any], current: dict[str, dict[str, Any]],
                     baseline: dict[str, dict[str, Any]]) -> dict[str, Any]:  # fmt: skip
    metric = str(pred.get("metric", ""))
    comparison = str(pred.get("comparison", ""))
    match = re.fullmatch(r"(\S+)\s+vs\s+(\S+)", comparison.strip())
    direction = str(pred.get("direction", ""))
    if match is None:
        return {"id": pred.get("id"), "ok": False, "detail": f"unparsed comparison {comparison!r}"}
    left_name, right_name = match.group(1), match.group(2)
    # Within the current run when both arms were scored there; otherwise each side
    # prefers the current run and falls back to the baseline run.
    if left_name in current and right_name in current:
        where = "within-run"
        left = _metric(current[left_name], metric)
        right = _metric(current[right_name], metric)
    else:
        left = _metric(current.get(left_name, baseline.get(left_name)), metric)
        right = _metric(current.get(right_name, baseline.get(right_name)), metric)
        both_baseline = left_name not in current and right_name not in current
        where = "baseline" if both_baseline else "cross-run"
    if direction == "higher":
        ok = left is not None and right is not None and left > right
    elif direction == "lower":
        ok = left is not None and right is not None and left < right
    else:
        ok = False
    return {
        "id": pred.get("id"),
        "metric": metric,
        "comparison": comparison,
        "direction": direction,
        "where": where,
        "left": "not reached" if left == math.inf else left,
        "right": "not reached" if right == math.inf else right,
        "ok": ok,
    }


# ---------------------------------------------------------------------------
# Rendering


def fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def md_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def _arm_cells(s: dict[str, Any]) -> list[str]:
    ttf_median = "not reached" if s["ttf_median"] is None and s["n"] else fmt(s["ttf_median"])
    return [str(s["n"]), fmt(s["solved_rate"]), ttf_median,
            f"{fmt(s['ttf_mean_reached'])} ({s['ttf_reached']}/{s['n']})",
            fmt(s["repeated_work_calls_mean"]), fmt(s["forbidden_rate"]),
            fmt(s["constraint_violation_rate"]), fmt(s["packet_required_recall"]),
            fmt(s["packet_unit_precision"]), fmt(s["tokens_mean"], 0)]  # fmt: skip


def _ladder_notes(arms: dict[str, dict[str, Any]]) -> list[str]:
    notes = []
    for arm, summary in arms.items():
        if "ladder_observation_recall" not in summary:
            continue
        notes.append(
            f"{arm} ladder: observation recall {fmt(summary['ladder_observation_recall'])}, "
            f"selection recall {fmt(summary['ladder_selection_recall'])}"
        )
    return notes


def _delta_rows(deltas: dict[str, Any], run: str | None = None) -> list[list[str]]:
    rows = []
    for arm, arm_deltas in deltas.items():
        for name in DELTA_METRICS:
            d = arm_deltas[name]
            ci = "-" if d["ci95"] is None else f"[{fmt(d['ci95'][0])}, {fmt(d['ci95'][1])}]"
            per_rep = " ".join(fmt(v, 0 if name == "tokens" else 2) for v in d["per_rep"]) or "-"
            row = [arm, name, str(d["n_pairs"]), fmt(d["mean"]), ci, per_rep]
            if run is not None:
                row.insert(0, run)
            rows.append(row)
    return rows


def _g6_parts(report: dict[str, Any]) -> list[str]:
    g6 = report["g6"]
    return ["", f"## G6: {g6['status']}", "", g6["detail"], "", "```", g6["log"].strip(), "```", ""]


def _prediction_parts(report: dict[str, Any]) -> list[str]:
    preds = report.get("predictions")
    if not preds:
        return []
    rows = []
    for item in preds["results"]:
        rows.append([str(item.get("id")), str(item.get("metric")), str(item.get("comparison")),
                     str(item.get("direction")), item.get("where", ""),
                     "held" if item.get("ok") else "missed"])  # fmt: skip
    lines = ["", "## Predictions", "",
             f"Frozen packet `{preds['frozen_packet_sha256']}`.", ""]  # fmt: skip
    lines.append(md_table(["id", "metric", "comparison", "direction", "where", "result"], rows)
                 if rows else "(no predictions)")
    return lines


def render(report: dict[str, Any]) -> str:
    if report.get("baseline_run"):
        return _render_with_baseline(report)
    parts = [f"# Component 0 run report: {report['run']}", "",
             f"Trials scored: {report['n_trials']}. Agents: {', '.join(report['agents'])}. "
             f"Model: {', '.join(report['models'])}. Codex {report['codex_version']}.", "",
             "## Per arm (real gold)", ""]  # fmt: skip
    rows = [[arm, *_arm_cells(s)] for arm, s in report["arms"].items()]
    parts.append(md_table(["arm", "n", "solved", "ttf median", "ttf mean (reached)",
                           "repeated", "forbidden", "constraint", "pkt recall",
                           "pkt precision", "tokens"], rows))  # fmt: skip
    ladder = _ladder_notes(report["arms"])
    if ladder:
        parts += ["", *ladder]
    parts += ["", "ttf median counts not-reached as infinite; ttf mean is over reached trials "
              "only. Rates are shares of trials. Packet metrics are properties of the arm's "
              "packet.", "", f"## Paired per-rep deltas vs {BASELINE} (arm minus {BASELINE})",
              "", f"Bootstrap 95% CI of the mean over reps: seed {BOOTSTRAP_SEED}, "
              f"{BOOTSTRAP_RESAMPLES} resamples. ttf deltas use reps where both trials reached "
              "the frontier.", ""]  # fmt: skip
    rows = []
    for arm, deltas in report["deltas"].items():
        for name in DELTA_METRICS:
            d = deltas[name]
            ci = "-" if d["ci95"] is None else f"[{fmt(d['ci95'][0])}, {fmt(d['ci95'][1])}]"
            rows.append([arm, name, str(d["n_pairs"]), fmt(d["mean"]), ci,
                         " ".join(fmt(v, 0 if name == "tokens" else 2)
                                  for v in d["per_rep"]) or "-"])  # fmt: skip
    parts.append(md_table(["arm", "metric", "pairs", "mean delta", "95% CI", "per rep"], rows))
    parts += ["", "## Failure tags", ""]
    tags = sorted({tag for s in report["arms"].values() for tag in s["failure_tags"]})
    rows = [[arm] + [str(s["failure_tags"].get(tag, 0)) for tag in tags]
            for arm, s in report["arms"].items()]  # fmt: skip
    parts.append(md_table(["arm", *tags], rows) if tags else "(none)")
    parts += _prediction_parts(report)
    parts += _g6_parts(report)
    return "\n".join(parts)


def _render_with_baseline(report: dict[str, Any]) -> str:
    base = str(report["baseline_run"])
    metric_headers = ["n", "solved", "ttf median", "ttf mean (reached)", "repeated", "forbidden",
                      "constraint", "pkt recall", "pkt precision", "tokens"]  # fmt: skip
    parts = [f"# Component 0 run report: {report['run']}", "",
             f"Trials scored: {report['n_trials']}. Agents: {', '.join(report['agents'])}. "
             f"Model: {', '.join(report['models'])}. Codex {report['codex_version']}.",
             f"Baseline run: {base}.", "",
             "## Per arm (real gold)", ""]  # fmt: skip
    rows = [[report["run"], arm, *_arm_cells(s)] for arm, s in report["arms"].items()]
    rows += [[base, arm, *_arm_cells(s)] for arm, s in report["baseline_arms"].items()]
    parts.append(md_table(["run", "arm", *metric_headers], rows))
    ladder = _ladder_notes(report["arms"]) + _ladder_notes(report["baseline_arms"])
    if ladder:
        parts += ["", *ladder]
    parts += ["", "ttf median counts not-reached as infinite; ttf mean is over reached trials "
              "only. Rates are shares of trials. Packet metrics are properties of the arm's "
              "packet.", "",
              f"## Within-run paired per-rep deltas vs {BASELINE} (arm minus {BASELINE})",
              "", "Each arm is paired with none from the same run. "
              f"Bootstrap 95% CI of the mean over reps: seed {BOOTSTRAP_SEED}, "
              f"{BOOTSTRAP_RESAMPLES} resamples. ttf deltas use reps where both trials reached "
              "the frontier.", ""]  # fmt: skip
    rows = _delta_rows(report["deltas"], report["run"])
    rows += _delta_rows(report["baseline_deltas"], base)
    parts.append(md_table(["run", "arm", "metric", "pairs", "mean delta", "95% CI", "per rep"],
                          rows))  # fmt: skip
    parts += ["", f"## Cross-run paired deltas vs baseline {BASELINE}", "",
              f"Arm in this run ({report['run']}) minus {BASELINE} in the baseline run ({base}), "
              "paired by rep. Within-run pairs above are the preferred comparison.", ""]  # fmt: skip
    parts.append(md_table(["arm", "metric", "pairs", "mean delta", "95% CI", "per rep"],
                          _delta_rows(report["cross_run_deltas"])))  # fmt: skip
    parts += ["", "## Failure tags", ""]
    tag_names = sorted({tag for group in (report["arms"], report["baseline_arms"])
                        for s in group.values() for tag in s["failure_tags"]})  # fmt: skip
    tag_rows = [[report["run"], arm] + [str(s["failure_tags"].get(tag, 0)) for tag in tag_names]
                for arm, s in report["arms"].items()]  # fmt: skip
    tag_rows += [[base, arm] + [str(s["failure_tags"].get(tag, 0)) for tag in tag_names]
                 for arm, s in report["baseline_arms"].items()]  # fmt: skip
    parts.append(md_table(["run", "arm", *tag_names], tag_rows) if tag_names else "(none)")
    parts += _prediction_parts(report)
    parts += _g6_parts(report)
    return "\n".join(parts)


def _summaries(trials: list[dict[str, Any]], arms: list[str]) -> dict[str, dict[str, Any]]:
    return {arm: arm_summary([t for t in trials if t["score"]["arm"] == arm]) for arm in arms}


def build_report(run_dir: Path, baseline_dir: Path | None = None) -> dict[str, Any]:
    trials = load_trials(run_dir)
    arms = arm_order(run_dir, trials)
    report: dict[str, Any] = {
        "run": run_dir.name,
        "n_trials": len(trials),
        "agents": sorted({str(t["meta"].get("agent")) for t in trials}),
        "models": sorted({str(t["score"].get("model")) for t in trials}),
        "codex_version": str(load_agent_config()["codex"]["version"]),
        "arms": _summaries(trials, arms),
        "deltas": {arm: paired_deltas(trials, arm) for arm in arms if arm != BASELINE},
        "bootstrap": {"seed": BOOTSTRAP_SEED, "resamples": BOOTSTRAP_RESAMPLES},
        "g6": g6_block(run_dir, trials),
    }
    if baseline_dir is not None:
        base_trials = load_trials(baseline_dir)
        base_arms = arm_order(baseline_dir, base_trials)
        report["baseline_run"] = baseline_dir.name
        report["baseline_arms"] = _summaries(base_trials, base_arms)
        report["baseline_deltas"] = {
            arm: paired_deltas(base_trials, arm) for arm in base_arms if arm != BASELINE
        }
        report["cross_run_deltas"] = {
            arm: cross_run_deltas(trials, base_trials, arm) for arm in arms if arm != BASELINE
        }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--baseline-run", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.baseline_run is not None and not args.baseline_run.is_dir():
        parser.error(f"baseline run not found: {args.baseline_run}")
    report = build_report(args.run_dir, args.baseline_run)
    prereg = EPISODES_DIR / DEFAULT_EPISODE / "gold" / "prereg_contextd.yaml"
    frozen = frozen_contextd_sha(EPISODES_DIR / DEFAULT_EPISODE)
    predictions = prediction_status(
        prereg, frozen, report["arms"], report.get("baseline_arms")
    )
    if predictions is None:
        print("predictions: not registered")
    else:
        report["predictions"] = predictions
    text = render(report)
    (args.run_dir / "report.md").write_text(text)
    (args.run_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(text)
    if report["g6"]["status"] == "not applicable":
        print("G6: not applicable to this run")
    else:
        print(f"G6 {report['g6']['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
