"""Pinned briefing regressions: record a baseline, and check it still reproduces.

Usage (from eval/component0):
    python -m harness.regression check [--id ID] [--episode NAME]
    python -m harness.regression check --id ID --candidate PATH
    python -m harness.regression record --id ID --episode NAME \\
        --replay-root DIR --live-run DIR [--packet-dir DIR] [--force]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from harness.common import COMPONENT_ROOT, EPISODES_DIR
from harness.contextd_ladder import load_events

REGRESSIONS_GLOB = "episodes/*/regressions/*/regression.yaml"

REPLAY_FILES = ("briefing.reply", "events.jsonl", "provenance.json")
PACKET_FILES = ("contextd_provenance.json", "contextd_ladder.json")
LIVE_FILES = ("report.md", "report.json", "matrix.json", "schedule.json")
TRIAL_FILES = (
    "score.json",
    "score_cf.json",
    "final_tests.json",
    "final.diff",
    "trace.jsonl",
    "exec.jsonl",
)
TRIAL_NAMES = tuple(f"contextd-r{n}" for n in range(1, 6)) + tuple(
    f"none-r{n}" for n in range(1, 6)
)
# regression.yaml `contextd` key -> provenance.json key. Provenance `head` is the
# replay workspace (httpcore) HEAD, not contextd's commit.
CONTEXTD_FIELDS = {
    "head": "contextd_head",
    "diff_sha256": "diff_sha256",
    "binary_sha256": "binary_sha256",
    "replay_image_id": "replay_image_id",
    "ollama_image_id": "ollama_image_id",
    "model_digest": "model_digest",
}
ARM_FIELDS = ("n", "solved_rate", "ttf_reached", "ttf_median", "tokens_mean")
REQUIRED_FIELDS = ("id", "target", "checks", "baseline_expect")
BRIEFING_LISTS = ("recent_activity", "relevant_history")


@dataclass
class Regression:
    dir: Path
    data: dict[str, Any]


class _Dumper(yaml.SafeDumper):
    pass


def _represent_str(dumper: yaml.SafeDumper, value: str) -> yaml.Node:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style='"')
    return yaml.representer.SafeRepresenter.represent_str(dumper, value)


_Dumper.add_representer(str, _represent_str)


def load_regression(path: Path | str) -> Regression:
    yaml_path = Path(path)
    data = yaml.safe_load(yaml_path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{yaml_path}: regression.yaml must be a mapping")
    return Regression(dir=yaml_path.parent, data=data)


def load_regressions(root: Path | None = None) -> list[Regression]:
    base = COMPONENT_ROOT if root is None else root
    return [load_regression(path) for path in sorted(base.glob(REGRESSIONS_GLOB))]


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    # Replay writes an object. Still accept a JSON string from older dumps.
    payload = event.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return {}
    if isinstance(payload, dict):
        return payload
    return {}


def matching_events(events: list[dict[str, Any]], target: dict[str, Any]) -> list[dict[str, Any]]:
    source = target["source"]
    pattern = re.compile(target["command_regex"])
    require_nonzero = bool(target.get("exit_code_nonzero"))
    matched = []
    for event in events:
        if event.get("source") != source:
            continue
        payload = _payload(event)
        command = payload.get("command")
        if not isinstance(command, str) or pattern.search(command) is None:
            continue
        if require_nonzero:
            # bool is a subclass of int; stored exit codes are real ints.
            code = payload.get("exit_code")
            if type(code) is not int or code == 0:
                continue
        matched.append(event)
    return matched


def _briefing_items(briefing_text: str) -> list[dict[str, Any]]:
    data = json.loads(briefing_text)
    if not isinstance(data, dict):
        raise ValueError("briefing must be a JSON object")
    items: list[dict[str, Any]] = []
    for key in BRIEFING_LISTS:
        for item in data.get(key) or []:
            if isinstance(item, dict):
                items.append(item)
    items.extend(_claim_items(data.get("focal_task")))
    items.extend(_claim_items(data.get("constraints")))
    items.extend(_claim_items(data.get("passed_checks")))
    items.extend(_claim_items(data.get("open_work")))
    return items


def _claim_items(node: Any) -> list[dict[str, Any]]:
    """Flatten the claim-view packet into id+text rows the matcher already understands.

    Event-feed briefings identify a line by the event id. Claim briefings identify
    a line by the claim id and point at events through `evidence_ids`. Treat each
    evidence id as a selectable row so `selected` / `marked_failed` still work.
    """
    items: list[dict[str, Any]] = []
    if isinstance(node, list):
        for child in node:
            items.extend(_claim_items(child))
        return items
    if not isinstance(node, dict):
        return items
    text = node.get("text")
    evidence = node.get("evidence_ids")
    if isinstance(text, str) and isinstance(evidence, list) and evidence:
        for event_id in evidence:
            items.append({"id": event_id, "text": text})
    elif isinstance(text, str) and node.get("id") is not None:
        items.append({"id": node.get("id"), "text": text})
    for child in node.values():
        if isinstance(child, (dict, list)):
            items.extend(_claim_items(child))
    return items


def evaluate(briefing_text: str, events: list[dict[str, Any]],
             target: dict[str, Any]) -> dict[str, Any]:  # fmt: skip
    matches = matching_events(events, target)
    target_ids = [str(event["id"]) for event in matches if event.get("id") is not None]
    target_set = set(target_ids)
    items = _briefing_items(briefing_text)
    failure_rank: int | None = None
    marked_failed = False
    selected = False
    for rank, item in enumerate(items, start=1):
        item_id = item.get("id")
        if item_id is None or str(item_id) not in target_set:
            continue
        selected = True
        if failure_rank is None:
            failure_rank = rank
        text = str(item.get("text") or "")
        if "exited" in text or "failed" in text:
            marked_failed = True
    fs_counts: dict[Any, int] = {}
    commit_items = 0
    for item in items:
        if item.get("source") == "file_system":
            stamp = item.get("timestamp_ms")
            fs_counts[stamp] = fs_counts.get(stamp, 0) + 1
        text = str(item.get("text") or "")
        if text.startswith("committed:") or "git commit" in text:
            commit_items += 1
    return {
        "checks": {
            "stored": bool(matches),
            "selected": selected,
            "marked_failed": marked_failed,
        },
        "info": {
            "target_ids": target_ids,
            "failure_rank": failure_rank,
            "briefing_items": len(items),
            "fs_same_timestamp_items": max(fs_counts.values()) if fs_counts else 0,
            "commit_items": commit_items,
        },
    }


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _baseline_files(reg_dir: Path) -> list[Path]:
    baseline = reg_dir / "baseline"
    if not baseline.is_dir():
        return []
    return sorted(path for path in baseline.rglob("*") if path.is_file())


def verify_files(reg: Regression) -> list[str]:
    problems: list[str] = []
    listed = reg.data.get("files") or {}
    if not isinstance(listed, dict):
        return ["files must be a mapping of relative path to sha256"]
    for rel, expected in listed.items():
        path = reg.dir / rel
        if not path.is_file():
            problems.append(f"missing {rel}")
            continue
        actual = sha256_file(path)
        if actual != expected:
            problems.append(f"sha mismatch {rel}")
    known = set(listed)
    for path in _baseline_files(reg.dir):
        rel = path.relative_to(reg.dir).as_posix()
        if rel not in known:
            problems.append(f"unlisted {rel}")
    packet = reg.data.get("packet") or {}
    same_rel = packet.get("same_as")
    expected_sha = packet.get("sha256")
    if not same_rel or not expected_sha:
        problems.append("packet.sha256/same_as missing")
    else:
        same_path = reg.dir / str(same_rel)
        if not same_path.is_file():
            problems.append(f"missing {same_rel}")
        elif sha256_file(same_path) != expected_sha:
            problems.append(f"packet.sha256 != sha256({same_rel})")
    return problems


def generation_dirs(path: Path | str) -> list[Path]:
    """One generation if `path` itself has both files; otherwise its gen-* children."""
    root = Path(path)
    if (root / "briefing.reply").is_file() and (root / "events.jsonl").is_file():
        return [root]
    if not root.is_dir():
        return []
    found = []
    for child in sorted(root.glob("gen-*")):
        if (child / "briefing.reply").is_file() and (child / "events.jsonl").is_file():
            found.append(child)
    return found


def _fmt_bool(value: bool) -> str:
    return "true" if value else "false"


def _fmt_rank(value: int | None) -> str:
    return "null" if value is None else str(value)


def print_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
    widths = [max(len(header), *(len(row[i]) for row in rows)) if rows else len(header)
              for i, header in enumerate(headers)]  # fmt: skip
    print("  ".join(header.ljust(width) for header, width in zip(headers, widths)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(width) for value, width in zip(row, widths)))


def _select(regs: list[Regression], reg_id: str | None, episode: str | None) -> list[Regression]:
    if reg_id is not None:
        regs = [reg for reg in regs if reg.data.get("id") == reg_id]
    if episode is not None:
        regs = [reg for reg in regs if reg.data.get("episode") == episode]
    return regs


def _eval_generation(gen: Path, target: dict[str, Any]) -> dict[str, Any]:
    return evaluate(
        (gen / "briefing.reply").read_text(),
        load_events(gen / "events.jsonl"),
        target,
    )


def _detail(n_reg: int, n_gen: int) -> str:
    reg_word = "regression" if n_reg == 1 else "regressions"
    gen_word = "generation" if n_gen == 1 else "generations"
    return f"{n_reg} {reg_word}, {n_gen} {gen_word}"


def run_baseline(regs: list[Regression]) -> tuple[bool, str]:
    rows: list[tuple[str, ...]] = []
    problems: list[str] = []
    generations = 0
    for reg in regs:
        name = str(reg.data.get("id"))
        problems.extend(verify_files(reg))
        gens = generation_dirs(reg.dir / "baseline" / "replay")
        generations += len(gens)
        if not gens:
            problems.append(f"{name}: no baseline generations")
            continue
        expect = reg.data["baseline_expect"]
        for gen in gens:
            try:
                result = _eval_generation(gen, reg.data["target"])
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                problems.append(f"{name} {gen.name}: {exc}")
                continue
            checks = result["checks"]
            if checks != expect:
                problems.append(f"{name} {gen.name}: {checks} != {expect}")
            rows.append((
                name, gen.name, _fmt_bool(checks["stored"]), _fmt_bool(checks["selected"]),
                _fmt_bool(checks["marked_failed"]), _fmt_rank(result["info"]["failure_rank"]),
            ))  # fmt: skip
    print_table(
        ("regression", "generation", "stored", "selected", "marked_failed", "rank"), rows
    )
    for problem in problems:
        print(problem)
    detail = _detail(len(regs), generations)
    if problems:
        detail = f"{detail}; {problems[0]}"
    return not problems, detail


def _info_row(name: str, label: str, result: dict[str, Any]) -> tuple[str, ...]:
    checks = result["checks"]
    info = result["info"]
    return (
        name,
        label,
        _fmt_bool(checks["stored"]),
        _fmt_bool(checks["selected"]),
        _fmt_bool(checks["marked_failed"]),
        _fmt_rank(info["failure_rank"]),
        str(info["briefing_items"]),
        str(info["fs_same_timestamp_items"]),
        str(info["commit_items"]),
        ",".join(info["target_ids"]),
    )


def run_candidate(reg: Regression, path: Path) -> int:
    gens = generation_dirs(path)
    if not gens:
        print(f"no generations under {path}")
        return 1
    name = str(reg.data.get("id"))
    target = reg.data["target"]
    wanted = list(reg.data["checks"])
    rows: list[tuple[str, ...]] = []
    baseline = reg.dir / "baseline" / "replay" / "gen-1"
    if (baseline / "briefing.reply").is_file() and (baseline / "events.jsonl").is_file():
        rows.append(_info_row(name, "baseline/gen-1", _eval_generation(baseline, target)))
    failed = False
    regressed = False
    for gen in gens:
        try:
            result = _eval_generation(gen, target)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"{gen}: {exc}")
            failed = True
            continue
        label = f"candidate/{gen.name}"
        rows.append(_info_row(name, label, result))
        checks = result["checks"]
        if not checks["stored"]:
            regressed = True
        if any(not checks[check] for check in wanted):
            failed = True
    print_table(
        ("regression", "generation", "stored", "selected", "marked_failed", "failure_rank",
         "briefing_items", "fs_same_timestamp_items", "commit_items", "target_ids"),
        rows,
    )  # fmt: skip
    if regressed:
        print("observation regressed")
    return 1 if failed else 0


def _resolve(path: Path) -> Path:
    path = Path(path)
    if path.is_absolute():
        return path
    return (Path.cwd() / path).resolve()


def _copy_file(src: Path, dest: Path) -> None:
    if not src.is_file():
        raise FileNotFoundError(src)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        yaml.dump(data, Dumper=_Dumper, sort_keys=False, width=4096, allow_unicode=True)
    )


def _skeleton(reg_id: str, episode: str) -> dict[str, Any]:
    return {
        "id": reg_id,
        "episode": episode,
        "recorded": date.today().isoformat(),
        "status": "open",
    }


def _fill_computed(reg_dir: Path, data: dict[str, Any], packet_dir: Path) -> None:
    provenance_path = reg_dir / "baseline" / "replay" / "gen-1" / "provenance.json"
    provenance = json.loads(provenance_path.read_text())
    missing = [key for key in CONTEXTD_FIELDS.values() if key not in provenance]
    if missing:
        raise ValueError(f"provenance.json missing {missing}")
    data["contextd"] = {key: provenance[source] for key, source in CONTEXTD_FIELDS.items()}

    packet = dict(data.get("packet") or {})
    packet.setdefault("file", f"episodes/{data['episode']}/packets/contextd.txt")
    packet.setdefault("same_as", "baseline/replay/gen-1/briefing.reply")
    reply = (reg_dir / str(packet["same_as"])).read_bytes()
    packet["sha256"] = hashlib.sha256(reply).hexdigest()
    frozen = COMPONENT_ROOT / str(packet["file"])
    if frozen.is_file() and frozen.read_bytes() != reply:
        raise ValueError(f"{packet['file']} is not byte-identical to {packet['same_as']}")
    data["packet"] = packet

    report = json.loads((reg_dir / "baseline" / "live" / "report.json").read_text())
    arms_in = report.get("arms")
    if not isinstance(arms_in, dict):
        raise ValueError("report.json has no arms object")
    arms: dict[str, Any] = {}
    for arm_name in ("contextd", "none"):
        if arm_name not in arms_in:
            raise ValueError(f"report.json arms missing {arm_name}")
        src = arms_in[arm_name]
        absent = [key for key in ARM_FIELDS if key not in src]
        if absent:
            raise ValueError(f"report.json arms.{arm_name} missing {absent}")
        arms[arm_name] = {key: src[key] for key in ARM_FIELDS}
    models = report.get("models")
    if isinstance(models, list):
        if len(models) != 1:
            raise ValueError(f"expected one model in report.json, got {models!r}")
        model = models[0]
    else:
        model = models
    live = dict(data.get("live") or {})
    live["run"] = report.get("run", live.get("run"))
    live["model"] = model
    live["arms"] = arms
    data["live"] = live
    data["files"] = {
        path.relative_to(reg_dir).as_posix(): sha256_file(path) for path in _baseline_files(reg_dir)
    }


def record_baseline(
    *,
    reg_id: str,
    episode: str,
    replay_root: Path,
    live_run: Path,
    packet_dir: Path | None = None,
    force: bool = False,
) -> Path:
    """Copy replay, packet, and live-run bytes into baseline/ and fill computed fields."""
    reg_dir = EPISODES_DIR / episode / "regressions" / reg_id
    yaml_path = reg_dir / "regression.yaml"
    reg_dir.mkdir(parents=True, exist_ok=True)
    if not yaml_path.is_file():
        _write_yaml(yaml_path, _skeleton(reg_id, episode))
        raise ValueError(
            f"wrote skeleton {yaml_path}; add {', '.join(REQUIRED_FIELDS)} and re-run"
        )
    reg = load_regression(yaml_path)
    missing = [key for key in REQUIRED_FIELDS if key not in reg.data]
    if missing:
        raise ValueError(f"{yaml_path} missing {missing}")
    if reg.data.get("id") != reg_id or reg.data.get("episode") != episode:
        raise ValueError(
            f"{yaml_path} id/episode is {reg.data.get('id')!r}/{reg.data.get('episode')!r}, "
            f"not {reg_id!r}/{episode!r}"
        )

    replay_root = _resolve(replay_root)
    live_run = _resolve(live_run)
    if packet_dir is None:
        packet_dir = EPISODES_DIR / episode / "packets"
    else:
        packet_dir = _resolve(packet_dir)
    baseline = reg_dir / "baseline"
    if baseline.exists():
        if not force:
            raise ValueError(f"{baseline} exists; pass --force to overwrite")
        shutil.rmtree(baseline)
    gens = sorted(path for path in replay_root.glob("gen-*") if path.is_dir())
    if not gens:
        raise FileNotFoundError(f"no gen-* directories under {replay_root}")
    for gen in gens:
        for name in REPLAY_FILES:
            _copy_file(gen / name, baseline / "replay" / gen.name / name)
    _copy_file(replay_root / "variation.json", baseline / "replay" / "variation.json")
    for name in PACKET_FILES:
        _copy_file(packet_dir / name, baseline / "packet" / name)
    for name in LIVE_FILES:
        _copy_file(live_run / name, baseline / "live" / name)
    for trial in TRIAL_NAMES:
        src_dir = live_run / trial
        if not src_dir.is_dir():
            continue
        for name in TRIAL_FILES:
            src = src_dir / name
            if src.is_file():
                _copy_file(src, baseline / "live" / trial / name)
    _fill_computed(reg_dir, reg.data, packet_dir)
    _write_yaml(yaml_path, reg.data)
    return yaml_path


def _cmd_check(args: argparse.Namespace) -> int:
    regs = _select(load_regressions(), args.id, args.episode)
    if not regs:
        print("no regressions matched")
        return 1
    if args.candidate is not None:
        if len(regs) != 1:
            print("check --candidate requires exactly one regression (--id or a single file)")
            return 1
        return run_candidate(regs[0], Path(args.candidate))
    ok, _detail_text = run_baseline(regs)
    return 0 if ok else 1


def _cmd_record(args: argparse.Namespace) -> int:
    try:
        path = record_baseline(
            reg_id=args.id,
            episode=args.episode,
            replay_root=args.replay_root,
            live_run=args.live_run,
            packet_dir=args.packet_dir,
            force=args.force,
        )
    except (OSError, ValueError, json.JSONDecodeError, KeyError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"recorded {path.relative_to(COMPONENT_ROOT)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")

    check = sub.add_parser("check", help="verify baselines still reproduce (default)")
    check.add_argument("--id", help="regression id")
    check.add_argument("--episode", help="episode name")
    check.add_argument(
        "--candidate", type=Path, help="replay dir or one generation to test as a fix"
    )
    check.set_defaults(func=_cmd_check)

    record = sub.add_parser("record", help="copy a replay and live run into a pinned baseline")
    record.add_argument("--id", required=True)
    record.add_argument("--episode", required=True)
    record.add_argument("--replay-root", type=Path, required=True)
    record.add_argument("--live-run", type=Path, required=True)
    record.add_argument("--packet-dir", type=Path)
    record.add_argument("--force", action="store_true", help="overwrite an existing baseline/")
    record.set_defaults(func=_cmd_record)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    if argv is None:
        argv = sys.argv[1:]
    # `check` is the default so a bare invocation still verifies baselines.
    if not argv or argv[0].startswith("-"):
        argv = ["check", *argv]
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
