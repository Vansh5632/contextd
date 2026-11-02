"""Score one trial directory against the gold action predicates.

Usage (from eval/component0):
    python -m harness.score TRIAL_DIR [--gold real|counterfactual] [--out score.json]

Trial directory inputs: trace.jsonl (normalized), final.diff (git diff vs HEAD incl.
untracked files), optional final_tests.json, optional packet_arm.txt.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness.common import EPISODES_DIR, clean_env
from harness.diffs import (
    FileDiff,
    PatchHunk,
    apply_hunk,
    locate,
    parse_git_diff,
    parse_hunks,
    span_touches,
)
from harness.gold import (
    Gold,
    PinnedTree,
    Range,
    Rule,
    Target,
    declared_packages,
    install_targets,
    load_gold,
    path_matches,
    path_words,
    pinned_tree,
    region_ranges,
    signature,
    signature_diff,
    undeclared_targets,
)
from harness.schema import (
    CommandEvent,
    EditEvent,
    Event,
    Trace,
    UsageEvent,
    read_trace,
)

EPISODE = "limiter-handoff"


# ---------------------------------------------------------------------------
# File content tracking across trace edits


@dataclass
class LocatedHunk:
    hunk: PatchHunk
    start: int
    in_current: bool


class ContentTracker:
    """File contents as the trace evolves: pinned HEAD plus trace edits applied in order.

    Shell commands that rewrite files (unasync, sed -i) are not replayed; hunks that no
    longer match the tracked content are located in the pinned file instead.
    """

    def __init__(self, pinned: PinnedTree) -> None:
        self.pinned = pinned
        self.current: dict[str, str | None] = {}

    def text(self, path: str) -> str | None:
        if path in self.current:
            return self.current[path]
        return self.pinned.read(path)

    def regions(self, target: Target, use_pinned: bool = False) -> list[Range]:
        source = self.pinned.read(target.path) if use_pinned else self.text(target.path)
        ranges = region_ranges(source, target.symbol, target.region) if source else None
        if ranges is None and not use_pinned:
            pinned = self.pinned.read(target.path)
            ranges = region_ranges(pinned, target.symbol, target.region) if pinned else None
        return ranges or []

    def locate_hunks(self, edit: EditEvent) -> list[LocatedHunk]:
        hunks = parse_hunks(edit.patch or "", edit.patch_format)
        current = (self.text(edit.path) or "").splitlines()
        pinned = (self.pinned.read(edit.path) or "").splitlines()
        located = []
        for hunk in hunks:
            block = hunk.old_lines
            if not block:
                base = 1 if not hunk.old_start else hunk.old_start + 1
                located.append(LocatedHunk(hunk, base, True))
                continue
            hint = hunk.old_start
            start = locate(current, block, hint)
            if start is not None:
                located.append(LocatedHunk(hunk, start, True))
                continue
            start = locate(pinned, block, hint)
            if start is not None:
                located.append(LocatedHunk(hunk, start, False))
            elif hint is not None:
                located.append(LocatedHunk(hunk, hint, False))
        return located

    def apply(self, edit: EditEvent, located: list[LocatedHunk]) -> None:
        if edit.change_kind == "delete":
            self.current[edit.path] = None
            return
        if edit.change_kind == "add":
            lines = [t for h in located for t in h.hunk.new_lines]
            self.current[edit.path] = "\n".join(lines) + ("\n" if lines else "")
            return
        if not edit.patch:
            self.current.pop(edit.path, None)
            return
        lines = (self.text(edit.path) or "").splitlines()
        for item in sorted((h for h in located if h.in_current), key=lambda h: -h.start):
            lines = apply_hunk(lines, item.hunk, item.start)
        text = "\n".join(lines) + "\n"
        if edit.move_path:
            self.current[edit.path] = None
            self.current[edit.move_path] = text
        else:
            self.current[edit.path] = text


def edit_touches(tracker: ContentTracker, edit: EditEvent, target: Target) -> bool:
    if edit.path != target.path:
        return False
    if edit.change_kind == "delete":
        return bool(tracker.regions(target))
    located = tracker.locate_hunks(edit)
    for item in located:
        regions = tracker.regions(target, use_pinned=not item.in_current)
        for span in item.hunk.changed_spans(item.start):
            if any(span_touches(span, region) for region in regions):
                return True
    return False


def diff_touches(pinned: PinnedTree, fdiff: FileDiff, target: Target) -> bool:
    if target.path not in (fdiff.old_path, fdiff.new_path):
        return False
    source = pinned.read(target.path)
    regions = region_ranges(source, target.symbol, target.region) if source else None
    if not regions:
        return False
    if fdiff.kind == "delete":
        return True
    for hunk in fdiff.hunks:
        base = hunk.old_start if hunk.old_lines else (hunk.old_start or 0) + 1
        for span in hunk.changed_spans(base or 1):
            if any(span_touches(span, region) for region in regions):
                return True
    return False


# ---------------------------------------------------------------------------
# Frontier


def pattern_hits_symbol(access_pattern: str, fixed: bool, ignore_case: bool, symbol: str,
                        prefix_chars: int, min_chars: int) -> bool:  # fmt: skip
    haystack = f"def {symbol}("
    lo, hi = 4, 4 + len(symbol)
    flags = re.IGNORECASE if ignore_case else 0
    try:
        regex = re.compile(re.escape(access_pattern) if fixed else access_pattern, flags)
    except re.error:
        regex = re.compile(re.escape(access_pattern), flags)
    for match in regex.finditer(haystack):
        overlap = min(match.end(), hi) - max(match.start(), lo)
        if overlap <= 0:
            continue
        if match.start() <= lo and match.end() >= lo + min(prefix_chars, len(symbol)):
            return True
        if overlap >= min(min_chars, len(symbol)):
            return True
    return False


def covers(access_path: str, target_path: str) -> bool:
    if access_path in (".", "", target_path):
        return True
    if target_path.startswith(access_path.rstrip("/") + "/"):
        return True
    return path_matches(target_path, [access_path])


def read_overlaps(start: int | None, end: int | None, text: str | None,
                  regions: list[Range]) -> bool:  # fmt: skip
    if start is None and end is None:
        return True
    total = len(text.splitlines()) if text is not None else None
    if start is not None and start < 0:
        if total is None:
            return False
        start, end = max(1, total + start + 1), total
    lo = start if start is not None else 1
    hi = end if end is not None else (total if total is not None else 10**9)
    return any(lo <= e and hi >= s for s, e in regions)


def frontier_hit(event: Event, tracker: ContentTracker, gold: Gold) -> tuple[Target, str] | None:
    for target in gold.frontier:
        if isinstance(event, EditEvent) and "edit" in target.via:
            if edit_touches(tracker, event, target):
                return target, f"edit {event.path}"
        if not isinstance(event, CommandEvent):
            continue
        for access in event.accesses:
            if access.mode == "read" and "read" in target.via and access.path == target.path:
                regions = tracker.regions(target)
                text = tracker.text(target.path)
                if read_overlaps(access.start, access.end, text, regions):
                    span = (
                        "whole"
                        if access.start is None and access.end is None
                        else (f"{access.start}-{access.end if access.end is not None else 'EOF'}")
                    )
                    return target, f"read {access.path} {span}"
            if (
                access.mode == "search"
                and "search" in target.via
                and access.pattern
                and covers(access.path, target.path)
                and pattern_hits_symbol(access.pattern, access.fixed, access.ignore_case,
                                        target.short_symbol, gold.prefix_chars, gold.min_chars)
            ):  # fmt: skip
                return target, f"search {access.pattern!r} in {access.path}"
    return None


# ---------------------------------------------------------------------------
# Forbidden actions and repeated work


def forbidden_in_trace(trace: Trace, gold: Gold, pinned: PinnedTree) -> tuple[
    list[dict[str, Any]], dict[int, tuple[Target, str]], list[dict[str, Any]]
]:  # fmt: skip
    """Walk the trace once: forbidden hits, the frontier hit per event index, and install
    commands that only target packages the pinned repo already declares (env setup)."""
    tracker = ContentTracker(pinned)
    hits: list[dict[str, Any]] = []
    frontier: dict[int, tuple[Target, str]] = {}
    env_setup: list[dict[str, Any]] = []
    for event in trace.events:
        if isinstance(event, (CommandEvent, EditEvent)):
            hit = frontier_hit(event, tracker, gold)
            if hit is not None:
                frontier[event.i] = hit
        if isinstance(event, CommandEvent):
            for rule in gold.commands:
                if rule.allow_declared_from and rule.regex:
                    install = install_hit(event, rule, pinned)
                    if install is not None:
                        if install["undeclared"]:
                            hits.append(_hit(event.t, rule.id, "installs undeclared "
                                             + ", ".join(install["undeclared"]), None))  # fmt: skip
                        else:
                            env_setup.append(install)
                    continue
                match = rule.regex.search(event.inner) if rule.regex else None
                if match:
                    hits.append(_hit(event.t, rule.id, f"command matches {match.group(0)!r}",
                                     None))  # fmt: skip
            for access in event.accesses:
                if access.mode != "write":
                    continue
                for rule in gold.path_edits:
                    if path_matches(access.path, rule.globs):
                        hits.append(_hit(event.t, rule.id, f"shell write {access.path}",
                                         access.path))  # fmt: skip
        elif isinstance(event, EditEvent):
            paths = [event.path] + ([event.move_path] if event.move_path else [])
            for rule in gold.path_edits:
                for path in paths:
                    if path_matches(path, rule.globs):
                        hits.append(_hit(event.t, rule.id, f"edit {path}", path))
            for rule in gold.region_edits:
                for target in rule.targets:
                    if edit_touches(tracker, event, target):
                        hits.append(_hit(event.t, rule.id,
                                         f"edit {event.path} touches {target.region}",
                                         event.path))  # fmt: skip
            if event.change_kind == "add":
                added = "\n".join(
                    t for h in parse_hunks(event.patch or "", event.patch_format)
                    for t in h.new_lines
                )  # fmt: skip
                for rule in gold.created_files:
                    if rule.regex and (
                        rule.regex.search(path_words(event.path)) or rule.regex.search(added)
                    ):
                        hits.append(_hit(event.t, rule.id, f"created {event.path}", event.path))
            tracker.apply(event, tracker.locate_hunks(event))
    return hits, frontier, env_setup


def install_hit(event: CommandEvent, rule: Rule, pinned: PinnedTree) -> dict[str, Any] | None:
    """All install commands in one shell script; None if the rule regex does not match."""
    assert rule.regex is not None
    matches = list(rule.regex.finditer(event.inner))
    if not matches:
        return None
    declared = declared_packages(pinned, rule.allow_declared_from)
    targets: list[str] = []
    undeclared: list[str] = []
    for match in matches:
        found = install_targets(event.inner, match.end())
        targets += found
        undeclared += undeclared_targets(found, declared, rule.allow_declared_from)
    return {"t": event.t, "rule": rule.id, "targets": targets, "undeclared": undeclared}


def _hit(t: int | None, rule: str, detail: str, path: str | None) -> dict[str, Any]:
    return {"t": t, "rule": rule, "detail": detail, "path": path}


def forbidden_in_final(
    diffs: list[FileDiff], gold: Gold, pinned: PinnedTree, seen: set[tuple[str, str | None]]
) -> list[dict[str, Any]]:
    hits = []

    def add(rule: str, detail: str, path: str) -> None:
        if (rule, path) not in seen:
            seen.add((rule, path))
            hits.append(_hit(None, rule, f"final.diff: {detail}", path))

    for fdiff in diffs:
        for rule in gold.path_edits:
            if path_matches(fdiff.path, rule.globs):
                add(rule.id, f"changes {fdiff.path}", fdiff.path)
        for rule in gold.region_edits:
            for target in rule.targets:
                if diff_touches(pinned, fdiff, target):
                    add(rule.id, f"{fdiff.path} touches {target.region}", fdiff.path)
        if fdiff.kind == "add":
            for rule in gold.created_files:
                if rule.regex and (
                    rule.regex.search(path_words(fdiff.path)) or rule.regex.search(fdiff.added_text)
                ):
                    add(rule.id, f"creates {fdiff.path}", fdiff.path)
    return hits


def repeated_work(trace: Trace, gold: Gold) -> list[dict[str, Any]]:
    hits = []
    for event in trace.tool_events():
        if isinstance(event, CommandEvent):
            for access in event.accesses:
                if path_matches(access.path, gold.repeated_paths):
                    hits.append({"t": event.t, "rule": "ruled_out_path",
                                 "detail": f"{access.mode} {access.path}"})  # fmt: skip
            for rule in gold.probe_rules:
                match = rule.regex.search(event.probe) if (rule.regex and event.probe) else None
                if match:
                    hits.append({"t": event.t, "rule": rule.id,
                                 "detail": f"probe uses {match.group(0)!r}"})  # fmt: skip
        elif isinstance(event, EditEvent) and path_matches(event.path, gold.repeated_paths):
            hits.append({"t": event.t, "rule": "ruled_out_path", "detail": f"edit {event.path}"})
    return hits


# ---------------------------------------------------------------------------
# Constraints on the final tree


def reconstruct_final(
    diff_text: str, diffs: list[FileDiff], pinned: PinnedTree
) -> tuple[dict[str, str | None], str | None]:
    """Final contents of every file touched by final.diff (None = deleted)."""
    if not diffs:
        return {}, None
    with tempfile.TemporaryDirectory(prefix="c0-final-") as tmp:
        root = Path(tmp) / "tree"
        root.mkdir()
        for fdiff in diffs:
            if fdiff.old_path:
                content = pinned.read(fdiff.old_path)
                if content is not None:
                    dest = root / fdiff.old_path
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(content)
        patch = Path(tmp) / "final.diff"
        patch.write_text(diff_text)
        env = clean_env({"GIT_CEILING_DIRECTORIES": tmp})
        result = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", str(patch)],
            cwd=root, env=env, capture_output=True, text=True, check=False,
        )  # fmt: skip
        if result.returncode != 0:
            return {}, (result.stderr or result.stdout).strip()[:500]
        out: dict[str, str | None] = {}
        for fdiff in diffs:
            if fdiff.old_path and fdiff.kind in ("delete", "rename"):
                out[fdiff.old_path] = None
            if fdiff.new_path:
                target = root / fdiff.new_path
                out[fdiff.new_path] = target.read_text() if target.exists() else None
        return out, None


def constraint_violations(
    diff_text: str, diffs: list[FileDiff], gold: Gold, pinned: PinnedTree
) -> tuple[list[dict[str, Any]], str | None]:
    violations: list[dict[str, Any]] = []
    for fdiff in diffs:
        for path in {p for p in (fdiff.old_path, fdiff.new_path) if p}:
            if path_matches(path, gold.dependency_files):
                violations.append({"rule": "dependency_file_changed", "path": path,
                                   "detail": f"{fdiff.kind} {path}"})  # fmt: skip
    final, error = reconstruct_final(diff_text, diffs, pinned)
    if error is not None:
        return violations, error
    for target in gold.signatures:
        before_src = pinned.read(target.path)
        if before_src is None:
            continue
        after_src = final.get(target.path, before_src) if target.path in final else before_src
        before = signature(before_src, target.symbol)
        after = signature(after_src, target.symbol) if after_src is not None else None
        for detail in signature_diff(before, after):
            violations.append({"rule": "signature_changed", "path": target.path,
                               "symbol": target.symbol, "detail": detail})  # fmt: skip
    return violations, None


# ---------------------------------------------------------------------------
# Packet metrics (Phase 2 module, imported lazily)


def _contextd_ladder(episode_dir: Path, atoms: Any, briefing: str, tokenizer: Any) -> Any:
    """Ladder summary for the contextd packet block. Missing frozen files are not an error."""
    events_path = episode_dir / "packets" / "contextd_events.jsonl"
    if not events_path.is_file() or not briefing:
        return None
    ladder_mod = importlib.import_module("harness.contextd_ladder")
    return ladder_mod.ladder(atoms, events_path, briefing, tokenizer)


def packet_metrics(arm: str | None, episode_dir: Path) -> tuple[dict[str, Any] | None, str]:
    if arm is None:
        return None, "unknown arm"
    try:
        packet_score = importlib.import_module("harness.packet_score")
    except ModuleNotFoundError as exc:
        return None, f"unavailable: {exc}"
    atoms_path = episode_dir / "gold" / "atoms.yaml"
    if not atoms_path.exists():
        return None, "unavailable: gold/atoms.yaml missing"
    if arm == "none":
        text, kind = "", packet_score.PACKET_KINDS["none"]
    else:
        name = packet_score.PACKET_FILES.get(arm, f"{arm.replace('-', '_')}.txt")
        packet_path = episode_dir / "packets" / name
        if not packet_path.exists():
            return None, f"unavailable: packets/{name} missing"
        text = packet_path.read_text()
        kind = packet_score.PACKET_KINDS.get(arm, "paragraphs")
    try:
        import tiktoken

        tokenizer = tiktoken.get_encoding(_tokenizer_name(episode_dir))
        atoms = packet_score.load_atoms(atoms_path)
        units = packet_score.split_units(text, kind) if text else []
        raw_text = text if kind == "contextd" else None
        scored = packet_score.score_packet(units, atoms, tokenizer, raw_text=raw_text)
        if arm == "contextd":
            scored["ladder"] = _contextd_ladder(episode_dir, atoms, text, tokenizer)
        return scored, "scored"
    except Exception as exc:  # noqa: BLE001 - reported, never fatal to trajectory scoring
        return None, f"error: {type(exc).__name__}: {exc}"


def _tokenizer_name(episode_dir: Path) -> str:
    import tomllib

    with open(episode_dir / "pins.toml", "rb") as handle:
        pins = tomllib.load(handle)
    return str((pins.get("arms") or {}).get("tokenizer", "o200k_base"))


# ---------------------------------------------------------------------------
# Assembly


def load_final_tests(trial_dir: Path) -> dict[str, Any] | None:
    path = trial_dir / "final_tests.json"
    return json.loads(path.read_text()) if path.exists() else None


def solved_from(tests: dict[str, Any] | None) -> bool | None:
    if tests is None:
        return None
    hidden = tests.get("hidden") or {}
    return (
        tests.get("visible") == "pass"
        and tests.get("suite") == "pass"
        and bool(hidden)
        and all(v == "pass" for v in hidden.values())
    )


def usage_summary(events: list[Event]) -> dict[str, int] | None:
    usages = [e for e in events if isinstance(e, UsageEvent)]
    if not usages:
        return None
    last = usages[-1]
    return {
        "input_tokens": last.input_tokens,
        "cached_input_tokens": last.cached_input_tokens,
        "cache_write_input_tokens": last.cache_write_input_tokens,
        "output_tokens": last.output_tokens,
        "reasoning_output_tokens": last.reasoning_output_tokens,
    }


def failure_tag(
    arm: str | None,
    packet: dict[str, Any] | None,
    forbidden_count: int,
    violations: list[dict[str, Any]] | None,
    repeated_calls: int,
    solved: bool | None,
) -> tuple[str, list[str]]:
    """Earliest failing layer: placement, then behaviour, then outcome; else ok.

    placement: an arm that places a packet (not `none`) whose packet score lists missing
               required atoms. Skipped when packet metrics are unavailable.
    behaviour: any forbidden action, any constraint violation, or repeated work > 0.
    outcome:   final tests present and not solved. Missing tests never fail this layer.
    """
    reasons = []
    if arm not in (None, "none") and packet is not None and packet.get("missing_atoms"):
        reasons.append("placement")
    if forbidden_count > 0 or (violations or []) or repeated_calls > 0:
        reasons.append("behaviour")
    if solved is False:
        reasons.append("outcome")
    return (reasons[0] if reasons else "ok"), reasons


def score_trial(
    trial_dir: Path | str, gold_variant: str = "real", episode: str = EPISODE
) -> dict[str, Any]:
    trial_dir = Path(trial_dir)
    episode_dir = EPISODES_DIR / episode
    gold = load_gold(episode_dir, gold_variant)
    pinned = pinned_tree(episode)
    trace = read_trace(trial_dir / "trace.jsonl")

    arm = trace.header.arm
    arm_file = trial_dir / "packet_arm.txt"
    if arm is None and arm_file.exists():
        arm = arm_file.read_text().strip() or None

    forbidden, frontier, env_setup = forbidden_in_trace(trace, gold, pinned)
    seen = {(h["rule"], h["path"]) for h in forbidden}

    diff_path = trial_dir / "final.diff"
    diff_text = diff_path.read_text() if diff_path.exists() else None
    diffs = parse_git_diff(diff_text) if diff_text else []
    violations: list[dict[str, Any]] | None = None
    diff_error: str | None = None
    if diff_text is not None:
        forbidden += forbidden_in_final(diffs, gold, pinned, seen)
        violations, diff_error = constraint_violations(diff_text, diffs, gold, pinned)

    tool_events = trace.tool_events()
    total_calls = trace.tool_call_count()
    first = min(frontier) if frontier else None
    frontier_event = next(e for e in trace.events if e.i == first) if first is not None else None
    tokens_to_frontier = None
    if frontier_event is not None:
        prior = [e for e in trace.events if isinstance(e, UsageEvent) and e.i < frontier_event.i]
        if prior:
            tokens_to_frontier = prior[-1].input_tokens + prior[-1].output_tokens

    rw = repeated_work(trace, gold)
    rw_calls = len({h["t"] for h in rw})
    tests = load_final_tests(trial_dir)
    solved = solved_from(tests)
    packet, packet_status = packet_metrics(arm, episode_dir)
    tag, reasons = failure_tag(arm, packet, len(forbidden), violations, rw_calls, solved)

    by_kind: dict[str, int] = {}
    for kind in ("command", "edit", "other_tool"):
        by_kind[kind] = len({e.t for e in tool_events if e.kind == kind})
    return {
        "trial": trial_dir.name,
        "gold_variant": gold_variant,
        "arm": arm,
        "rep": trace.header.rep,
        "model": trace.header.model,
        "thread_id": trace.header.thread_id,
        "packet": packet,
        "packet_status": packet_status,
        "turns_to_frontier": frontier_event.t if frontier_event is not None else None,
        "tokens_to_frontier": tokens_to_frontier,
        "frontier_event": None
        if frontier_event is None
        else {
            "t": frontier_event.t,
            "i": frontier_event.i,
            "target": frontier[frontier_event.i][0].id,
            "reason": frontier[frontier_event.i][1],
        },
        "forbidden_actions": forbidden,
        "forbidden_count": len(forbidden),
        "env_setup_installs": env_setup,
        "repeated_work": rw,
        "repeated_work_calls": rw_calls,
        "repeated_work_share": (rw_calls / total_calls) if total_calls else 0.0,
        "constraint_violations": violations,
        "final_diff_error": diff_error,
        "final_tests": tests,
        "solved": solved,
        "usage": usage_summary(trace.events),
        "tool_calls": {"total": total_calls, **by_kind},
        "unparsed_commands": sum(
            1 for e in trace.events if isinstance(e, CommandEvent) and e.unparsed
        ),
        "failure_tag": tag,
        "failure_layers": reasons,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trial_dir", type=Path)
    parser.add_argument("--gold", choices=("real", "counterfactual"), default="real")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--episode", default=EPISODE)
    args = parser.parse_args(argv)
    result = score_trial(args.trial_dir, args.gold, args.episode)
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(text)
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
