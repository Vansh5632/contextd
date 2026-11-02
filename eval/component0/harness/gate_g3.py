"""Gate G3: packet scoring separates the arms as designed.

Usage (from eval/component0, after harness.arms render):
    python -m harness.gate_g3 [--episode NAME]

Every arm is scored through harness.score.packet_metrics, the same path trial scoring
uses, and `harness.arms check` must pass against the pins.toml [freeze] hashes.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness import arms, packet_score
from harness.common import DEFAULT_EPISODE, EPISODES_DIR
from harness.score import packet_metrics

DECISION = "decision_idle_counts"
LOCK_RULED_OUT = "ruled_out_lock"


@dataclass(frozen=True)
class Check:
    name: str
    expected: str
    actual: str
    ok: bool


def print_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h)
              for i, h in enumerate(headers)]  # fmt: skip
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(v.ljust(w) for v, w in zip(row, widths)))


def fmt(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def score_arms(episode_dir: Path, names: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    scores = {}
    for arm in names:
        metrics, status = packet_metrics(arm, episode_dir)
        if metrics is None:
            raise SystemExit(f"arm {arm}: packet metrics {status}")
        scores[arm] = metrics
    return scores


def raw_dropped_atoms(episode_dir: Path, atoms: packet_score.AtomSet,
                      raw: dict[str, Any]) -> list[str]:  # fmt: skip
    """Atoms present somewhere in the full history but absent from the truncated raw arm."""
    history = arms.load_history(episode_dir / arms.FREEZE_FILES["history_jsonl"])
    in_history = {
        atom_id
        for u in history
        for atom_id in packet_score.classify_unit(arms.render_unit(u), atoms).atoms
    }
    return [a.id for a in atoms.atoms if a.id in in_history and a.id not in raw["matched_atoms"]]


def contextd_checks(episode_dir: Path, freeze: dict[str, str]) -> list[Check]:
    """Structural checks for an enabled contextd arm. Recall is reported, not gated."""
    checks: list[Check] = []
    path = episode_dir / arms.CONTEXTD_REL
    digest: str | None = None
    parsed: Any = None
    if not path.is_file():
        checks.append(Check("contextd.txt", "present", "missing", False))
    else:
        raw = path.read_bytes()
        digest = arms.sha256(raw)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            checks.append(Check("contextd.txt JSON", "object", "invalid JSON", False))
        if isinstance(parsed, dict):
            recent = parsed.get("recent_activity")
            checks.append(Check("contextd recent_activity", "list", type(recent).__name__,
                                isinstance(recent, list)))  # fmt: skip
        elif parsed is not None:
            checks.append(Check("contextd.txt JSON", "object", type(parsed).__name__, False))
    frozen = freeze.get(arms.CONTEXTD_FREEZE_KEY)
    checks.append(Check(
        "contextd sha256",
        frozen or f"pins [freeze].{arms.CONTEXTD_FREEZE_KEY}",
        digest or "missing",
        digest is not None and frozen is not None and digest == frozen,
    ))
    prov_path = episode_dir / "packets" / "contextd_provenance.json"
    if not prov_path.is_file():
        checks.append(Check("contextd provenance", "head_matches_followup true", "missing", False))
    else:
        try:
            prov = json.loads(prov_path.read_text())
        except json.JSONDecodeError:
            prov = None
        if isinstance(prov, dict) and isinstance(prov.get("provenance"), dict):
            prov = prov["provenance"]
        ok = isinstance(prov, dict) and prov.get("head_matches_followup") is True
        actual = "invalid JSON" if prov is None else str(
            prov.get("head_matches_followup") if isinstance(prov, dict) else prov
        )  # fmt: skip
        checks.append(Check("contextd provenance", "head_matches_followup true", actual, ok))
    return checks


def checks_for(scores: dict[str, dict[str, Any]], atoms: packet_score.AtomSet,
               dropped: list[str], arms_problems: list[str]) -> list[Check]:  # fmt: skip
    oracle, stale, summary = scores["oracle"], scores["stale-only"], scores["summary"]
    raw, none = scores["raw"], scores["none"]
    constraints = [a.id for a in atoms.by_role("constraint")]
    ruled_out = [a.id for a in atoms.by_role("ruled_out")]
    unresolved = [a.id for a in atoms.by_role("unresolved")]

    def missing(name: str, score: dict[str, Any], ids: list[str]) -> Check:
        absent = [i for i in ids if i not in score["matched_atoms"]]
        return Check(name, "missing " + ",".join(ids), "missing " + (",".join(absent) or "-"),
                     absent == ids)  # fmt: skip

    return [
        Check("oracle required_recall", "1.0", fmt(oracle["required_recall"]),
              oracle["required_recall"] == 1.0),
        Check("oracle unit_precision", "1.0", fmt(oracle["unit_precision"]),
              oracle["unit_precision"] == 1.0),
        Check("oracle stale_as_current", "0", fmt(oracle["stale_as_current"]),
              oracle["stale_as_current"] == 0),
        Check("oracle stale_marked", ">= 1", fmt(oracle["stale_marked"]),
              oracle["stale_marked"] >= 1),
        Check("stale-only stale_as_current", ">= 1", fmt(stale["stale_as_current"]),
              stale["stale_as_current"] >= 1),
        missing("stale-only decision + ruled_out", stale, [DECISION, *ruled_out]),
        missing("summary constraints", summary, constraints),
        missing("summary lock ruled_out", summary, [LOCK_RULED_OUT]),
        missing("summary unresolved", summary, unresolved),
        Check("summary stale_as_current", "0", fmt(summary["stale_as_current"]),
              summary["stale_as_current"] == 0),
        Check("raw required_recall", "< 1.0",
              f"{fmt(raw['required_recall'])} (dropped: {','.join(dropped) or '-'})",
              raw["required_recall"] < 1.0),
        Check("raw unit_precision", f"< oracle {fmt(oracle['unit_precision'])}",
              fmt(raw["unit_precision"]),
              raw["unit_precision"] is not None
              and raw["unit_precision"] < oracle["unit_precision"]),
        Check("none required_recall", "0", fmt(none["required_recall"]),
              none["required_recall"] == 0),
        Check("arms check", "pass", "; ".join(arms_problems) or "pass", not arms_problems),
    ]  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode", default=DEFAULT_EPISODE)
    # harness.validate calls main() in-process; never parse its sys.argv.
    args = parser.parse_args(argv if argv is not None else [])
    episode_dir = EPISODES_DIR / args.episode

    config = arms.load_config(episode_dir)
    atoms = packet_score.load_atoms(episode_dir / "gold" / "atoms.yaml")
    score_names = tuple(name for name in config.names if name != arms.CONTEXTD_ARM)
    scores = score_arms(episode_dir, score_names)
    extra: list[Check] = []
    if arms.CONTEXTD_ARM not in config.names:
        print("contextd: not enabled")
    else:
        extra = contextd_checks(episode_dir, config.freeze)
        metrics, status = packet_metrics(arms.CONTEXTD_ARM, episode_dir)
        if metrics is None:
            extra.append(Check("contextd metrics", "scored", status, False))
        else:
            scores[arms.CONTEXTD_ARM] = metrics
    dropped = raw_dropped_atoms(episode_dir, atoms, scores["raw"])
    arms_problems = arms.check(episode_dir)

    print("-- packet metrics")
    rows = [
        (arm, fmt(s["packet_tokens"]), fmt(s["units"]), fmt(s["required_recall"]),
         fmt(s["unit_precision"]), fmt(s["token_precision"]), fmt(s["stale_as_current"]),
         fmt(s["stale_marked"]), fmt(s["unresolved_present"]),
         ",".join(s["missing_atoms"]) or "-")
        for arm, s in scores.items()
    ]  # fmt: skip
    print_table(("arm", "tokens", "units", "recall", "unit_prec", "token_prec", "stale_cur",
                 "stale_marked", "unresolved", "missing"), rows)  # fmt: skip

    checks = checks_for(scores, atoms, dropped, arms_problems) + extra
    print("\n-- G3 checks")
    print_table(
        ("check", "expected", "actual", "result"),
        [(c.name, c.expected, c.actual, "PASS" if c.ok else "FAIL") for c in checks],
    )
    passed = all(c.ok for c in checks)
    print(f"\nG3 {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
