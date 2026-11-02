"""Replay the scripted session histories in the offline scorer image.

Usage (from eval/component0, after harness.build_workspace):
    python -m harness.record_history [--episode NAME] [--force] [--twice]

Each session replays against a fresh copy of the workspace checked out at the import
commit. Commands run with `--network none` and the copy mounted at /work. Output is
merged stdout+stderr, ANSI-stripped, noise-normalized and truncated deterministically.
Writes episodes/NAME/history/history.jsonl.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from harness.common import COMPONENT_ROOT, Pins, git, load_pins, run
from harness.dtar import extract

IMAGE = "contextd-c0-scorer:v0"
SESSIONS = ("A", "B")
MAX_OUTPUT_LINES = 200
PATCH_MOUNT_DIR = "/tmp"

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
NOISE: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bin \d+(\.\d+)?s\b"), "in 0.00s"),
    (re.compile(r"\b\d+\.\d+s (call|setup|teardown)\b"), r"0.00s \1"),
    (re.compile(r"/tmp/pytest-of-[^/\s]+/pytest-\d+"), "/tmp/pytest-of-user/pytest-0"),
    (re.compile(r"/tmp/tmp[A-Za-z0-9_]+"), "/tmp/tmpXXXXXX"),
    (re.compile(r"\b0x[0-9a-fA-F]{4,}\b"), "0x0"),
)


@dataclass(frozen=True)
class Step:
    kind: str  # note | run | apply
    value: str


def load_script(path: Path) -> list[Step]:
    data = yaml.safe_load(path.read_text())
    steps = []
    for entry in data["steps"]:
        if not isinstance(entry, dict) or len(entry) != 1:
            raise ValueError(f"{path}: each step needs exactly one of note/run/apply: {entry!r}")
        ((kind, value),) = entry.items()
        if kind not in ("note", "run", "apply"):
            raise ValueError(f"{path}: unknown step kind {kind!r}")
        text = " ".join(str(value).split()) if kind == "note" else str(value).rstrip("\n")
        steps.append(Step(kind, text))
    return steps


def normalize_output(raw: str) -> str:
    text = ANSI.sub("", raw.replace("\r\n", "\n"))
    for pattern, repl in NOISE:
        text = pattern.sub(repl, text)
    lines = [line.rstrip() for line in text.rstrip("\n").split("\n")] if text.strip() else []
    if len(lines) > MAX_OUTPUT_LINES:
        dropped = len(lines) - MAX_OUTPUT_LINES
        lines = lines[:MAX_OUTPUT_LINES] + [f"[... {dropped} more lines truncated ...]"]
    return "\n".join(lines)


class Sandbox:
    def __init__(self, pins: Pins, root: Path, label: str, import_sha: str) -> None:
        self.pins = pins
        base = root / label
        base.mkdir(parents=True)
        extract(pins.tar_path, base)
        self.work = base / pins.name
        git(self.work, "checkout", "-q", import_sha)

    def docker(self, command: str, mounts: tuple[tuple[Path, str], ...] = ()) -> tuple[int, str]:
        args = [
            "docker", "run", "--rm", "--network", "none",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "-e", "HOME=/tmp", "-e", "PYTHONPATH=/work", "-e", "PYTHONUNBUFFERED=1",
            "-e", "COLUMNS=80", "-e", "LC_ALL=C.UTF-8", "-e", "TZ=UTC",
            "-v", f"{self.work}:/work",
        ]  # fmt: skip
        for host, container in mounts:
            args += ["-v", f"{host}:{container}:ro"]
        # Redirect inside the container so stdout/stderr interleave deterministically.
        args += ["-w", "/work", IMAGE, "sh", "-c", "exec 2>&1\n" + command]
        result = subprocess.run(args, capture_output=True, check=False)
        return result.returncode, result.stdout.decode("utf-8", errors="replace")


def replay(pins: Pins, root: Path, session: str, steps: list[Step], import_sha: str,
           first_unit: int) -> list[dict[str, Any]]:  # fmt: skip
    box = Sandbox(pins, root, f"session-{session.lower()}", import_sha)
    units: list[dict[str, Any]] = []
    for step in steps:
        unit: dict[str, Any] = {"unit": first_unit + len(units), "session": session}
        if step.kind == "note":
            unit.update(kind="note", text=step.value)
        else:
            mounts: tuple[tuple[Path, str], ...] = ()
            command = step.value
            if step.kind == "apply":
                patch = (pins.episode_dir / step.value).resolve()
                if not patch.is_file():
                    raise SystemExit(f"apply step: {patch} missing")
                target = f"{PATCH_MOUNT_DIR}/{patch.name}"
                mounts = ((patch, target),)
                command = f"git apply --verbose {target}"
            code, output = box.docker(command, mounts)
            unit.update(kind="command", command=command, output=normalize_output(output),
                        exit_code=code)  # fmt: skip
        units.append(unit)
    return units


def record(pins: Pins) -> str:
    manifest = json.loads(pins.manifest_path.read_text())
    import_sha = manifest["commits"]["import"]
    history_dir = pins.episode_dir / "history"
    root = Path(tempfile.mkdtemp(prefix="history-", dir=COMPONENT_ROOT / "build"))
    try:
        units: list[dict[str, Any]] = []
        for session in SESSIONS:
            steps = load_script(history_dir / f"session_{session.lower()}.script.yaml")
            units += replay(pins, root, session, steps, import_sha, len(units) + 1)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return "".join(json.dumps(u, ensure_ascii=False) + "\n" for u in units)


def ensure_image() -> None:
    if run(["docker", "image", "inspect", IMAGE], check=False).returncode != 0:
        raise SystemExit(f"docker image {IMAGE} missing; run harness.gate_g1 first")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode", default="limiter-handoff")
    parser.add_argument("--force", action="store_true", help="overwrite history.jsonl")
    parser.add_argument("--twice", action="store_true",
                        help="record twice and require byte-identical output")  # fmt: skip
    args = parser.parse_args(argv)
    pins = load_pins(args.episode)
    out = pins.episode_dir / "history" / "history.jsonl"
    if out.exists() and not args.force:
        print(f"{out} exists; pass --force to overwrite", file=sys.stderr)
        return 1
    ensure_image()
    text = record(pins)
    if args.twice:
        again = record(pins)
        if again != text:
            print("NOT deterministic: two recordings differ", file=sys.stderr)
            return 1
        print("deterministic: two recordings are byte-identical")
    out.write_text(text)
    units = [json.loads(line) for line in text.splitlines()]
    failed = [u["unit"] for u in units if u.get("exit_code") not in (None, 0)]
    print(f"wrote {out}: {len(units)} units; nonzero exit in units {failed or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
