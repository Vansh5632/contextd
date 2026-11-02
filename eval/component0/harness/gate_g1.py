"""Gate G1: the built workspace behaves as designed inside the offline scorer image.

Usage (from eval/component0, after harness.build_workspace):
    python -m harness.gate_g1 [--episode NAME] [--rebuild-image]

The workspace is never pip-installed: each container run sets PYTHONPATH=/work so the
checked-out httpcore package and the `tests` package import from the workspace copy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from harness.common import COMPONENT_ROOT, Pins, load_pins, run
from harness.dtar import extract

IMAGE = "contextd-c0-scorer:v0"
DOCKERFILE = COMPONENT_ROOT / "docker" / "scorer.Dockerfile"
VISIBLE_MESSAGE = "count 3 diverged from 2"
FAIL_ON_BUG = frozenset(
    {
        "test_cap_sync_max_three_four_origins",
        "test_cap_async_max_three_four_origins",
        "test_connection_pool_concurrency",
    }
)
PYTEST = ["python", "-m", "pytest", "-p", "no:cacheprovider", "-q"]


@dataclass(frozen=True)
class Check:
    stage: str
    name: str
    expected: str
    actual: str
    ok: bool


@dataclass(frozen=True)
class Outcome:
    code: int
    output: str
    cases: dict[str, str]


def ensure_image(rebuild: bool) -> None:
    exists = run(["docker", "image", "inspect", IMAGE], check=False).returncode == 0
    if rebuild or not exists:
        run(["docker", "build", "-q", "-f", str(DOCKERFILE), "-t", IMAGE, str(DOCKERFILE.parent)])


def parse_junit(path: Path) -> dict[str, str]:
    cases: dict[str, str] = {}
    if not path.exists():
        return cases
    for case in ET.parse(path).getroot().iter("testcase"):
        key = f"{case.get('classname')}::{case.get('name')}"
        if case.find("failure") is not None or case.find("error") is not None:
            cases[key] = "failed"
        elif case.find("skipped") is not None:
            cases[key] = "skipped"
        else:
            cases[key] = "passed"
    return cases


class Sandbox:
    """A fresh extracted copy of the workspace plus a scratch dir for reports."""

    def __init__(self, pins: Pins, root: Path, label: str) -> None:
        self.pins = pins
        self.base = root / label
        self.out = self.base / "out"
        self.out.mkdir(parents=True)
        extract(pins.tar_path, self.base)
        self.work = self.base / pins.name
        self._runs = 0

    def docker(self, command: list[str]) -> tuple[int, str]:
        args = [
            "docker", "run", "--rm", "--network", "none",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "-e", "HOME=/tmp",
            "-e", "PYTHONPATH=/work",
            "-v", f"{self.work}:/work",
            "-v", f"{self.out}:/out",
            "-v", f"{self.pins.episode_dir}:/episode:ro",
            "-w", "/work",
            IMAGE, *command,
        ]  # fmt: skip
        result = run(args, check=False)
        return result.returncode, result.stdout + result.stderr

    def pytest(self, *targets: str) -> Outcome:
        self._runs += 1
        report = f"junit-{self._runs}.xml"
        code, output = self.docker([*PYTEST, f"--junitxml=/out/{report}", *targets])
        return Outcome(code, output, parse_junit(self.out / report))

    def unasync_check(self) -> tuple[int, str]:
        return self.docker(["python", "scripts/unasync.py", "--check"])

    def add_hidden_tests(self) -> list[str]:
        names = []
        for src in sorted(self.pins.hidden_tests.glob("test_*.py")):
            shutil.copyfile(src, self.work / "tests" / src.name)
            names.append(f"tests/{src.name}")
        return names


def summary(outcome: Outcome) -> str:
    counts: dict[str, int] = {}
    for state in outcome.cases.values():
        counts[state] = counts.get(state, 0) + 1
    detail = ", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "no cases"
    return f"exit {outcome.code}; {detail}"


def base_name(case_key: str) -> str:
    return case_key.rsplit("::", 1)[-1].split("[", 1)[0]


def stage_bug(pins: Pins, root: Path) -> list[Check]:
    box = Sandbox(pins, root, "a-bug")
    checks = []
    suite = box.pytest("tests", f"--ignore={pins.visible_test_dest}")
    ok = suite.code == 0 and bool(suite.cases)
    checks.append(Check("a", "workspace suite (minus visible)", "pass", summary(suite), ok))

    visible = box.pytest(pins.visible_test_dest)
    ok = visible.code == 1 and VISIBLE_MESSAGE in visible.output
    found = "message found" if VISIBLE_MESSAGE in visible.output else "message missing"
    checks.append(
        Check(
            "a",
            "visible test",
            f"fail with '{VISIBLE_MESSAGE}'",
            f"{summary(visible)}; {found}",
            ok,
        )
    )

    code, _ = box.unasync_check()
    checks.append(Check("a", "unasync --check", "exit 0", f"exit {code}", code == 0))
    return checks


def stage_fix(pins: Pins, root: Path) -> list[Check]:
    box = Sandbox(pins, root, "b-fix")
    checks = []
    touched = [
        line.split(" b/", 1)[1]
        for line in pins.fix_patch.read_text().splitlines()
        if line.startswith("diff --git ")
    ]
    ok = pins.visible_test_dest not in touched
    checks.append(Check("b", "fix.patch leaves visible test", "untouched", ", ".join(touched), ok))

    code, output = box.docker(
        ["git", "apply", f"/episode/{pins.fix_patch.relative_to(pins.episode_dir)}"]
    )
    checks.append(
        Check(
            "b", "git apply fix.patch", "exit 0", f"exit {code} {output.strip()}".strip(), code == 0
        )
    )

    hidden = box.add_hidden_tests()
    suite = box.pytest("tests")
    hidden_modules = {Path(h).stem for h in hidden}
    hidden_cases = [k for k in suite.cases if k.split("::")[0].rsplit(".", 1)[-1] in hidden_modules]
    visible_cases = [k for k in suite.cases if "test_pool_counts" in k]
    ok = (
        suite.code == 0
        and len(hidden_cases) > 0
        and len(visible_cases) == 1
        and all(suite.cases[k] == "passed" for k in hidden_cases + visible_cases)
    )
    detail = f"{summary(suite)}; hidden {len(hidden_cases)}, visible {len(visible_cases)}"
    checks.append(Check("b", "suite + visible + hidden", "all pass", detail, ok))

    code, _ = box.unasync_check()
    checks.append(Check("b", "unasync --check", "exit 0", f"exit {code}", code == 0))
    return checks


def stage_bug_hidden(pins: Pins, root: Path) -> list[Check]:
    box = Sandbox(pins, root, "c-bug-hidden")
    hidden = box.add_hidden_tests()
    outcome = box.pytest(*hidden)
    checks = []
    if not outcome.cases:
        return [Check("c", "hidden tests collected", "cases", summary(outcome), False)]
    for key in sorted(outcome.cases):
        expected = "failed" if base_name(key) in FAIL_ON_BUG else "passed"
        actual = outcome.cases[key]
        label = key.replace("tests.", "", 1)
        checks.append(Check("c", label, expected, actual, actual == expected))
    missing = FAIL_ON_BUG - {base_name(k) for k in outcome.cases}
    if missing:
        checks.append(Check("c", "cap tests present", "all", f"missing {sorted(missing)}", False))
    return checks


def print_table(checks: list[Check]) -> None:
    headers = ("stage", "check", "expected", "actual", "result")
    rows = [(c.stage, c.name, c.expected, c.actual, "PASS" if c.ok else "FAIL") for c in checks]
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    print(line)
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(v.ljust(w) for v, w in zip(row, widths)))


def verify_tar(pins: Pins) -> None:
    manifest = json.loads(pins.manifest_path.read_text())
    digest = hashlib.sha256(pins.tar_path.read_bytes()).hexdigest()
    if digest != manifest["tar_sha256"]:
        raise SystemExit(f"tar hash {digest} does not match manifest; rebuild the workspace")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode", default="limiter-handoff")
    parser.add_argument("--rebuild-image", action="store_true")
    parser.add_argument("--keep", action="store_true", help="keep sandbox copies for inspection")
    args = parser.parse_args(argv)

    pins = load_pins(args.episode)
    verify_tar(pins)
    ensure_image(args.rebuild_image)

    root = Path(tempfile.mkdtemp(prefix="g1-", dir=COMPONENT_ROOT / "build"))
    try:
        checks = stage_bug(pins, root) + stage_fix(pins, root) + stage_bug_hidden(pins, root)
    finally:
        if args.keep:
            print(f"sandboxes kept at {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)

    print_table(checks)
    passed = all(c.ok for c in checks)
    print(f"\nG1 {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
