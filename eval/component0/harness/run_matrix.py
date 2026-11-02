"""Run the arm x rep trial matrix for one run id (resumable).

Usage (from eval/component0):
    python -m harness.run_matrix --run-id ID [--reps 5] [--seed 20260930]
        [--arms a,b] [--agent codex|stub-oracle|stub-none|stub-stale] [--stub]
        [--i-accept-spend] [--token-cap N] [--matrix-token-cap N] [--timeout-s S]
        [--retry-failed]

Order: within each rep every arm runs once, in an order shuffled with the fixed seed
(paired by rep). runs/ID/schedule.json is written first and must match on resume.
Stub agents require --stub; the codex agent requires CODEX_API_KEY and --i-accept-spend.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness import images
from harness.common import COMPONENT_ROOT, clean_env, load_pins, run
from harness.run_trial import (
    AGENTS,
    DEFAULT_TIMEOUT_S,
    DEFAULT_TOKEN_CAP,
    SECRET_ENV,
    RefusedError,
    TrialError,
    TrialOptions,
    arms_check,
    ensure_egress,
    load_agent_config,
    run_trial,
    verify_tar,
    write_json,
)

RUNS_DIR = COMPONENT_ROOT / "runs"
DEFAULT_SEED = 20260930
DEFAULT_REPS = 5
DEFAULT_MATRIX_TOKEN_CAP = 40_000_000
PREFLIGHT_GATES = ("G3", "G4", "G5")


# ---------------------------------------------------------------------------
# Schedule


def select_arms(available: list[str], requested: str | None) -> list[str]:
    """Subset of pins [arms].names. None means every listed arm, in pins order.

    The returned order is the pins order, so the seed shuffles a stable list.
    """
    if requested is None:
        return list(available)
    wanted = [part.strip() for part in requested.split(",")]
    if not wanted or any(not part for part in wanted):
        raise ValueError("--arms must be a comma-separated list of arm names")
    unknown = [part for part in wanted if part not in available]
    if unknown:
        raise ValueError(
            f"unknown arm(s): {', '.join(unknown)}; available: {', '.join(available)}"
        )
    chosen = set(wanted)
    return [arm for arm in available if arm in chosen]


def build_schedule(arms: list[str], reps: int, seed: int, agent: str) -> dict[str, Any]:
    rng = random.Random(seed)
    trials = []
    for rep in range(1, reps + 1):
        order = list(arms)
        rng.shuffle(order)
        for arm in order:
            trials.append({"index": len(trials), "rep": rep, "arm": arm, "dir": f"{arm}-r{rep}"})
    return {"schema": 1, "seed": seed, "reps": reps, "arms": list(arms), "agent": agent,
            "trials": trials}  # fmt: skip


def load_or_create_schedule(run_dir: Path, schedule: dict[str, Any]) -> dict[str, Any]:
    path = run_dir / "schedule.json"
    if path.exists():
        existing = json.loads(path.read_text())
        if existing != schedule:
            raise SystemExit(f"{path} differs from the requested schedule (seed, reps, arms or "
                             "agent changed); use a new --run-id")  # fmt: skip
        return existing
    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(path, schedule)
    return schedule


def trial_tokens(trial_dir: Path) -> int:
    meta_path = trial_dir / "meta.json"
    if not meta_path.exists():
        return 0
    tokens = json.loads(meta_path.read_text()).get("tokens") or {}
    return int(tokens.get("input_plus_output") or 0)


def cumulative_tokens(run_dir: Path, schedule: dict[str, Any]) -> int:
    return sum(trial_tokens(run_dir / t["dir"]) for t in schedule["trials"])


def pending_trials(run_dir: Path, schedule: dict[str, Any], recorded: dict[str, Any],
                   retry_failed: bool) -> list[dict[str, Any]]:  # fmt: skip
    """Trials still to run: no score.json, and not a recorded failure unless retrying."""
    pending = []
    for trial in schedule["trials"]:
        if (run_dir / trial["dir"] / "score.json").exists():
            continue
        prior = recorded.get(trial["dir"])
        if prior is not None and prior.get("status") != "complete" and not retry_failed:
            continue
        pending.append(trial)
    return pending


# ---------------------------------------------------------------------------
# Preflight


def refusal(agent: str, stub: bool, accept_spend: bool) -> str | None:
    if agent not in AGENTS:
        return f"unknown agent {agent}"
    if agent.startswith("stub-") and not stub:
        return f"--agent {agent} is a stub; pass --stub explicitly"
    if agent == "codex" and stub:
        return "--stub given with --agent codex"
    if agent == "codex" and not os.environ.get(SECRET_ENV):
        return f"{SECRET_ENV} is not set; refusing to run the codex matrix"
    if agent == "codex" and not accept_spend:
        return "the codex matrix spends API tokens; pass --i-accept-spend"
    return None


def gate_statuses(output: str) -> dict[str, str]:
    statuses: dict[str, str] = {}
    in_summary = False
    for line in output.splitlines():
        if line.startswith("== summary"):
            in_summary = True
            continue
        match = re.match(r"^(G\d)\s+(PASS|FAIL|SKIPPED)\b", line)
        if in_summary and match:
            statuses[match.group(1)] = match.group(2)
    return statuses


def preflight(run_dir: Path, config: dict[str, Any]) -> list[tuple[str, str, bool]]:
    checks: list[tuple[str, str, bool]] = []

    def attempt(name: str, fn: Any) -> None:
        try:
            detail = fn()
            checks.append((name, str(detail), True))
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - a crashing check fails
            checks.append((name, str(exc), False))

    pins = load_pins()

    def arms() -> str:
        arms_check()
        return "PASS"

    attempt("arms check", arms)
    attempt("workspace tar", lambda: verify_tar(pins)["tar_sha256"][:16])

    def validate() -> str:
        result = run([sys.executable, "-m", "harness.validate", "--skip-docker"],
                     cwd=COMPONENT_ROOT, env=clean_env(), check=False)  # fmt: skip
        (run_dir / "preflight_validate.log").write_text(result.stdout + result.stderr)
        statuses = gate_statuses(result.stdout)
        bad = [g for g in PREFLIGHT_GATES if statuses.get(g) != "PASS"]
        if bad:
            raise TrialError(f"validate --skip-docker: {', '.join(bad)} not PASS ({statuses})")
        return ", ".join(f"{g} {statuses[g]}" for g in PREFLIGHT_GATES)

    attempt("validate G3-G5", validate)

    def egress() -> str:
        script = COMPONENT_ROOT / config["egress"]["check_script"]
        result = run(["bash", str(script)], cwd=COMPONENT_ROOT, check=False)
        (run_dir / "preflight_egress.log").write_text(result.stdout + result.stderr)
        if result.returncode != 0 or "EGRESS OK" not in result.stdout:
            raise TrialError("check_egress.sh failed; see preflight_egress.log")
        info = ensure_egress(config, images.load_pins()["egress"]["id"])
        return f"{info['container']} up, pinned image; EGRESS OK"

    attempt("egress proxy", egress)

    # After the egress check: a rebuilt proxy image must fail here, not at trial 0.
    def check_images() -> str:
        ids = images.verify()
        return ", ".join(f"{k}={v[7:19]}" for k, v in ids.items())

    attempt("image IDs", check_images)
    return checks


# ---------------------------------------------------------------------------
# Main loop


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--reps", type=int, default=DEFAULT_REPS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--arms", default=None,
                        help="comma-separated subset of pins [arms].names (default: all)")  # fmt: skip
    parser.add_argument("--agent", choices=AGENTS, default="codex")
    parser.add_argument("--stub", action="store_true", help="required for stub agents")
    parser.add_argument("--i-accept-spend", action="store_true", dest="accept_spend")
    parser.add_argument("--token-cap", type=int, default=DEFAULT_TOKEN_CAP,
                        help="per-trial input+output token cap")  # fmt: skip
    parser.add_argument("--matrix-token-cap", type=int, default=DEFAULT_MATRIX_TOKEN_CAP)
    parser.add_argument("--timeout-s", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--retry-failed", action="store_true",
                        help="rerun trials recorded as failed, replacing their dirs")  # fmt: skip
    args = parser.parse_args(argv)

    reason = refusal(args.agent, args.stub, args.accept_spend)
    if reason:
        print(f"refused: {reason}", file=sys.stderr)
        return 2
    if not re.fullmatch(r"[A-Za-z0-9._-]+", args.run_id):
        parser.error("--run-id may only contain letters, digits, '.', '_' and '-'")

    config = load_agent_config()
    pins = load_pins()
    with open(pins.episode_dir / "pins.toml", "rb") as handle:
        available = list(tomllib.load(handle)["arms"]["names"])
    try:
        arms = select_arms(available, args.arms)
    except ValueError as exc:
        parser.error(str(exc))
    run_dir = RUNS_DIR / args.run_id
    schedule = load_or_create_schedule(
        run_dir, build_schedule(arms, args.reps, args.seed, args.agent)
    )

    checks = preflight(run_dir, config)
    print("== preflight")
    for name, detail, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<16} {detail}")
    if not all(ok for _, _, ok in checks):
        print("preflight FAIL; nothing run", file=sys.stderr)
        return 1

    matrix_path = run_dir / "matrix.json"
    matrix: dict[str, Any] = json.loads(matrix_path.read_text()) if matrix_path.exists() else {}
    recorded: dict[str, Any] = matrix.get("trials", {})
    pending = pending_trials(run_dir, schedule, recorded, args.retry_failed)
    print(f"\n== plan: run {args.run_id}, agent {args.agent}, "
          f"model {config['codex']['model']}, codex {config['codex']['version']}")  # fmt: skip
    print(f"trials {len(schedule['trials'])} ({len(arms)} arms x {args.reps} reps, seed "
          f"{args.seed}); pending {len(pending)}")  # fmt: skip
    print(f"per-trial token cap {args.token_cap}; matrix token cap {args.matrix_token_cap}; "
          f"timeout {args.timeout_s:g}s")  # fmt: skip
    for trial in schedule["trials"]:
        state = "pending" if trial in pending else (recorded.get(trial["dir"], {}).get("status")
                                                    or "complete")  # fmt: skip
        print(f"  {trial['index']:>2}  rep {trial['rep']}  {trial['arm']:<11} {state}")

    matrix.update({"run_id": args.run_id, "agent": args.agent,
                   "model": config["codex"]["model"],
                   "codex_version": config["codex"]["version"],
                   "token_cap": args.token_cap,
                   "matrix_token_cap": args.matrix_token_cap, "stopped": None,
                   "trials": recorded})  # fmt: skip
    matrix.setdefault("started_utc", utc_now())

    def save() -> None:
        matrix["cumulative_tokens"] = cumulative_tokens(run_dir, schedule)
        matrix["updated_utc"] = utc_now()
        write_json(matrix_path, matrix)

    save()
    for trial in pending:
        used = cumulative_tokens(run_dir, schedule)
        remaining = args.matrix_token_cap - used
        if remaining <= 0:
            matrix["stopped"] = f"matrix token cap reached ({used} >= {args.matrix_token_cap})"
            print(matrix["stopped"])
            break
        cap = min(args.token_cap, remaining)
        print(f"\n-- trial {trial['index']}: {trial['dir']} (token cap {cap})", flush=True)
        opts = TrialOptions(arm=trial["arm"], rep=trial["rep"], out=run_dir / trial["dir"],
                            agent=args.agent, timeout_s=args.timeout_s, token_cap=cap,
                            force=args.retry_failed)  # fmt: skip
        try:
            meta = run_trial(opts)
            entry = {"status": meta["status"], "errors": meta["errors"],
                     "terminated": meta.get("terminated"), "exit_code": meta.get("exit_code"),
                     "tokens": (meta.get("tokens") or {}).get("input_plus_output"),
                     "score": meta.get("score")}  # fmt: skip
        except RefusedError as exc:
            entry = {"status": "refused", "errors": [str(exc)]}
        entry["finished_utc"] = utc_now()
        recorded[trial["dir"]] = entry
        save()
        print(
            f"   {entry['status']}: {json.dumps(entry.get('score'))} "
            f"tokens={entry.get('tokens')} errors={entry['errors']}"
        )
    save()
    done = sum(1 for t in schedule["trials"] if (run_dir / t["dir"] / "score.json").exists())
    print(f"\n== matrix {args.run_id}: {done}/{len(schedule['trials'])} scored, cumulative "
          f"tokens {matrix['cumulative_tokens']}" + (f"; {matrix['stopped']}"
                                                     if matrix["stopped"] else ""))  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
