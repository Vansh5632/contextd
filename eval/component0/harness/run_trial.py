"""Run one trial: agent container, trace, final diff, offline test scoring, scores.

Usage (from eval/component0):
    python -m harness.run_trial --arm ARM --rep N --out runs/RUN_ID/ARM-rN
        [--agent codex|stub-oracle|stub-none|stub-stale] [--timeout-s 1800] [--token-cap N]
        [--force]

The Codex invocation and the docker run template come from harness/agent_config.toml.
CODEX_API_KEY is forwarded by name only (`-e CODEX_API_KEY`); its value never appears in
argv, files or logs, and every trial file is scanned for it afterwards.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from harness import images
from harness.arms import GENERATED_ARMS
from harness.common import COMPONENT_ROOT, Pins, clean_env, load_pins, run
from harness.diffs import parse_git_diff
from harness.dtar import extract
from harness.gate_g1 import PYTEST, parse_junit
from harness.normalize import normalize_trace
from harness.schema import USAGE_FIELDS, Header, write_trace
from harness.score import score_trial

AGENT_CONFIG_PATH = COMPONENT_ROOT / "harness" / "agent_config.toml"
STUB_AGENT_PATH = COMPONENT_ROOT / "harness" / "stub_agent.py"
AGENTS = ("codex", "stub-oracle", "stub-none", "stub-stale")
STUB_MODES = {"stub-oracle": "oracle", "stub-none": "none", "stub-stale": "stale"}
SECRET_ENV = "CODEX_API_KEY"
DEFAULT_TIMEOUT_S = 1800
DEFAULT_TOKEN_CAP = 2_000_000
SCORER_TIMEOUT_S = 900
CPUS = str(min(4, os.cpu_count() or 1))
AGENT_LIMITS = ("--cpus", CPUS, "--memory", "8g", "--memory-swap", "8g", "--pids-limit", "1024")
SCORER_LIMITS = ("--cpus", CPUS, "--memory", "4g", "--memory-swap", "4g", "--pids-limit", "512")
# Directly under the scorer copy's root: the hidden tests resolve the repo as parents[1].
HIDDEN_DIR = ".c0_hidden"
FINAL_TEST_KEYS = ("visible", "hidden", "suite", "unasync_check", "apply")
STUB_MOUNT = "/stub"
ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class TrialError(Exception):
    pass


class RefusedError(TrialError):
    pass


# ---------------------------------------------------------------------------
# Configuration and argv


def load_agent_config(path: Path = AGENT_CONFIG_PATH) -> dict[str, Any]:
    with open(path, "rb") as handle:
        return tomllib.load(handle)


def codex_argv(config: dict[str, Any]) -> list[str]:
    trial = config["trial"]
    subs = {
        "model": config["codex"]["model"],
        "reasoning_effort": config["codex"]["reasoning_effort"],
        "last_message_path": trial["last_message_path"],
    }
    argv: list[str] = []
    for token in trial["codex_exec"]:
        if token == "{config_overrides}":
            argv.extend(trial["config_overrides"])
        else:
            argv.append(token.format(**subs))
    return argv


def stub_argv(agent: str, config: dict[str, Any] | None = None) -> list[str]:
    config = load_agent_config() if config is None else config
    return ["python", f"{STUB_MOUNT}/stub_agent.py", "--mode", STUB_MODES[agent],
            "--data", f"{STUB_MOUNT}/fix.json",
            "--model", str(config["codex"]["model"]),
            "--cli-version", str(config["codex"]["version"])]  # fmt: skip


def docker_argv(
    config: dict[str, Any],
    *,
    container_name: str,
    workspace: Path,
    codex_home: Path,
    out_dir: Path,
    command: list[str],
    stub_dir: Path | None = None,
) -> list[str]:
    image = config["image"]["name"]
    subs = {
        "container_name": container_name,
        "workspace_dir": str(workspace),
        "codex_home_dir": str(codex_home),
        "out_dir": str(out_dir),
    }
    args = [token.format(**subs) for token in config["trial"]["docker_run"]]
    if args[-1] != image:
        raise TrialError(f"docker_run template must end with the image {image}")
    extra = ["--user", config["image"]["user"], *AGENT_LIMITS]
    if stub_dir is not None:
        extra += ["-v", f"{stub_dir}:{STUB_MOUNT}:ro"]
    return args[:-1] + extra + [image] + command


def assert_no_secret_in_argv(argv: list[str]) -> None:
    for token in argv:
        if token.startswith(f"{SECRET_ENV}="):
            raise TrialError(f"{SECRET_ENV} value would appear in argv")
    secret = os.environ.get(SECRET_ENV)
    if secret and any(secret in token for token in argv):
        raise TrialError(f"{SECRET_ENV} value would appear in argv")


# ---------------------------------------------------------------------------
# Token cap


@dataclass
class TokenMeter:
    """Thread token totals seen so far.

    Both `turn.completed.usage` (exec stream) and rollout `token_count.info.total_token_usage`
    are thread totals, not deltas, so the meter keeps the largest observation instead of
    summing. The cap applies to input_tokens + output_tokens (cached input is part of input).
    """

    cap: int | None
    usage: dict[str, int] = field(default_factory=lambda: {k: 0 for k in USAGE_FIELDS})
    source: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def total(self) -> int:
        return self.usage["input_tokens"] + self.usage["output_tokens"]

    @property
    def exceeded(self) -> bool:
        return self.cap is not None and self.total > self.cap

    def observe(self, usage: dict[str, Any], source: str) -> bool:
        values = {k: int(usage.get(k) or 0) for k in USAGE_FIELDS}
        with self._lock:
            if values["input_tokens"] + values["output_tokens"] > self.total:
                self.usage = values
                self.source = source
            return self.exceeded

    def observe_exec_line(self, line: str) -> bool:
        record = _json_line(line)
        if record and record.get("type") == "turn.completed":
            return self.observe(record.get("usage") or {}, "exec")
        return self.exceeded

    def observe_rollout_line(self, line: str) -> bool:
        record = _json_line(line)
        payload = (record or {}).get("payload") or {}
        if record and record.get("type") == "event_msg" and payload.get("type") == "token_count":
            total = (payload.get("info") or {}).get("total_token_usage")
            if total:
                return self.observe(total, "rollout")
        return self.exceeded

    def summary(self) -> dict[str, Any]:
        return {**self.usage, "input_plus_output": self.total, "source": self.source,
                "cap": self.cap}  # fmt: skip


def _json_line(line: str) -> dict[str, Any] | None:
    try:
        record = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    return record if isinstance(record, dict) else None


class RolloutTail(threading.Thread):
    """Polls the trial CODEX_HOME for rollout lines so the cap also fires mid-turn."""

    def __init__(self, codex_home: Path, pattern: str, meter: TokenMeter,
                 on_exceeded: Callable[[], None], interval: float = 1.0) -> None:  # fmt: skip
        super().__init__(daemon=True)
        self.codex_home = codex_home
        self.pattern = pattern
        self.meter = meter
        self.on_exceeded = on_exceeded
        self.interval = interval
        self.offsets: dict[Path, int] = {}
        self.stop_event = threading.Event()

    def poll(self) -> None:
        for path in sorted(self.codex_home.glob(self.pattern)):
            offset = self.offsets.get(path, 0)
            try:
                with open(path, "rb") as handle:
                    handle.seek(offset)
                    chunk = handle.read()
            except OSError:
                continue
            complete = chunk.rfind(b"\n") + 1
            self.offsets[path] = offset + complete
            for line in chunk[:complete].decode("utf-8", "replace").splitlines():
                if self.meter.observe_rollout_line(line):
                    self.on_exceeded()

    def run(self) -> None:
        while not self.stop_event.wait(self.interval):
            self.poll()

    def stop(self) -> None:
        self.stop_event.set()
        self.join(timeout=5)
        self.poll()


# ---------------------------------------------------------------------------
# Container run


@dataclass
class RunResult:
    exit_code: int | None
    terminated: str | None
    start_utc: str
    end_utc: str
    wall_seconds: float


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(ts: datetime) -> str:
    return ts.strftime(ISO_FORMAT)


def docker_kill(name: str) -> None:
    # Retried because the container may not be registered yet right after `docker run`.
    for _ in range(10):
        if run(["docker", "kill", name], check=False).returncode == 0:
            return
        time.sleep(0.5)


def run_container(argv: list[str], prompt: bytes, *, name: str, exec_path: Path,
                  stderr_path: Path, timeout_s: float, meter: TokenMeter, codex_home: Path,
                  rollout_glob: str) -> RunResult:  # fmt: skip
    terminated: list[str] = []
    lock = threading.Lock()

    def kill(reason: str) -> None:
        with lock:
            if terminated:
                return
            terminated.append(reason)
        docker_kill(name)

    start = utc_now()
    began = time.monotonic()
    with open(stderr_path, "wb") as stderr_file, open(exec_path, "wb") as exec_file:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=stderr_file, env=clean_env())  # fmt: skip
        assert proc.stdin is not None and proc.stdout is not None

        def feed() -> None:
            assert proc.stdin is not None
            try:
                proc.stdin.write(prompt)
                proc.stdin.close()
            except BrokenPipeError:
                pass

        feeder = threading.Thread(target=feed, daemon=True)
        feeder.start()
        timer = threading.Timer(timeout_s, kill, args=("timeout",))
        timer.daemon = True
        timer.start()
        tail = RolloutTail(codex_home, rollout_glob, meter, lambda: kill("token_cap"))
        tail.start()
        try:
            for raw in proc.stdout:
                exec_file.write(raw)
                exec_file.flush()
                if meter.observe_exec_line(raw.decode("utf-8", "replace")):
                    kill("token_cap")
            code = proc.wait()
        except BaseException:
            kill("interrupted")
            proc.wait()
            raise
        finally:
            timer.cancel()
            tail.stop()
            feeder.join(timeout=5)
    end = utc_now()
    return RunResult(code, terminated[0] if terminated else None, iso(start), iso(end),
                     round(time.monotonic() - began, 3))  # fmt: skip


# ---------------------------------------------------------------------------
# Egress proxy


def egress_container_name(config: dict[str, Any]) -> str:
    compose = yaml.safe_load((COMPONENT_ROOT / config["egress"]["compose_file"]).read_text())
    return str(compose["services"]["proxy"]["container_name"])


def egress_running(name: str) -> bool:
    result = run(["docker", "inspect", "-f", "{{.State.Running}}", name], check=False)
    return result.returncode == 0 and result.stdout.strip() == "true"


def ensure_egress(config: dict[str, Any], pinned_id: str) -> dict[str, Any]:
    name = egress_container_name(config)
    if not egress_running(name):
        compose = str(COMPONENT_ROOT / config["egress"]["compose_file"])
        result = run(["docker", "compose", "-f", compose, "up", "-d", "--wait"], check=False)
        if result.returncode != 0:
            raise TrialError(f"egress proxy failed to start: {result.stderr.strip()[-500:]}")
    if not egress_running(name):
        raise TrialError(f"egress proxy {name} is not running")
    image_id = run(["docker", "inspect", "-f", "{{.Image}}", name]).stdout.strip()
    if image_id != pinned_id:
        raise TrialError(f"egress container runs {image_id[:19]}, pinned {pinned_id[:19]}")
    network = config["egress"]["internal_network"]
    internal = run(["docker", "network", "inspect", "-f", "{{.Internal}}", network], check=False)
    if internal.returncode != 0 or internal.stdout.strip() != "true":
        raise TrialError(f"network {network} missing or not internal")
    return {"container": name, "image_id": image_id, "internal_network": network}


def proxy_log(name: str, start_utc: str, end_utc: str) -> str:
    end = datetime.strptime(end_utc, ISO_FORMAT).replace(tzinfo=timezone.utc)
    until = iso(end + timedelta(seconds=2))
    result = run(["docker", "logs", "--since", start_utc, "--until", until, name], check=False)
    return result.stdout + result.stderr


# ---------------------------------------------------------------------------
# Inputs: tar, prompt, stub data


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def verify_tar(pins: Pins) -> dict[str, Any]:
    manifest = json.loads(pins.manifest_path.read_text())
    digest = sha256_file(pins.tar_path)
    if digest != manifest["tar_sha256"]:
        raise TrialError(f"tar sha256 {digest} != manifest {manifest['tar_sha256']}")
    return manifest


def host_python(args: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run([sys.executable, "-m", *args], cwd=COMPONENT_ROOT, env=clean_env(),
                          capture_output=True, check=False)  # fmt: skip


def arms_check() -> None:
    result = host_python(["harness.arms", "check"])
    if result.returncode != 0:
        raise TrialError("arms check failed:\n" + result.stdout.decode(errors="replace"))


def build_prompt(pins: Pins, arm: str) -> bytes:
    arms_check()
    result = host_python(["harness.arms", "prompt", "--arm", arm, "--episode", pins.name])
    if result.returncode != 0:
        raise TrialError("arms prompt failed: " + result.stderr.decode(errors="replace"))
    manifest = json.loads((pins.episode_dir / "packets" / "manifest.json").read_text())
    expected = (manifest.get("prompts") or {}).get(arm)
    # Generated packets are gitignored, so they stay out of the frozen manifest.
    # A frozen arm must still match its manifest hash byte for byte.
    if expected is None:
        if arm not in GENERATED_ARMS:
            raise TrialError(f"prompt for {arm} is not in packets/manifest.json")
        return result.stdout
    if sha256_bytes(result.stdout) != expected["sha256"]:
        raise TrialError(f"prompt for {arm} does not match packets/manifest.json")
    return result.stdout


def stub_fix_data(pins: Pins) -> dict[str, Any]:
    """The async-file hunk of gold/fix.patch as an exact text replacement."""
    diffs = [d for d in parse_git_diff(pins.fix_patch.read_text()) if "/_async/" in d.path]
    if len(diffs) != 1 or len(diffs[0].hunks) != 1:
        raise TrialError("gold fix.patch: expected one async file with one hunk")
    hunk = diffs[0].hunks[0]
    start = hunk.old_start or 1
    return {
        "path": diffs[0].path,
        "old": "\n".join(hunk.old_lines) + "\n",
        "new": "\n".join(hunk.new_lines) + "\n",
        "read_start": max(1, start - 20),
        "read_end": start + len(hunk.old_lines) + 5,
    }


# ---------------------------------------------------------------------------
# Outputs: rollout, diff, scrub


def collect_rollout(codex_home: Path, pattern: str, dest: Path) -> list[str]:
    files = sorted(codex_home.glob(pattern))
    if files:
        with open(dest, "wb") as out:
            for path in files:
                out.write(path.read_bytes())
    return [p.relative_to(codex_home).as_posix() for p in files]


def compute_final_diff(workspace: Path, base_commit: str, dest: Path) -> dict[str, Any]:
    """Working tree vs the pinned base commit, untracked files included, via a scratch index
    so the workspace index is left untouched."""
    env_base = clean_env()
    head = run(["git", "-C", str(workspace), "rev-parse", "HEAD"], env=env_base,
               check=False).stdout.strip()  # fmt: skip
    with tempfile.TemporaryDirectory(prefix="c0-index-") as tmp:
        env = clean_env({"GIT_INDEX_FILE": str(Path(tmp) / "index")})
        run(["git", "-C", str(workspace), "read-tree", base_commit], env=env)
        run(["git", "-C", str(workspace), "add", "-A", "-N"], env=env)
        diff = subprocess.run(["git", "-C", str(workspace), "diff", "--binary", base_commit],
                              env=env, capture_output=True, check=True).stdout  # fmt: skip
    dest.write_bytes(diff)
    return {"bytes": len(diff), "base_commit": base_commit, "head": head,
            "head_moved": head != base_commit}  # fmt: skip


def scrub_check(trial_dir: Path, secret: str | None) -> dict[str, Any]:
    if not secret:
        return {"status": "skipped", "reason": f"{SECRET_ENV} unset", "files_scanned": 0}
    needle = secret.encode()
    hits: list[str] = []
    scanned = 0
    for dirpath, _, filenames in os.walk(trial_dir):
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink() or not path.is_file():
                continue
            scanned += 1
            if needle in path.read_bytes():
                hits.append(path.relative_to(trial_dir).as_posix())
    return {"status": "LEAK" if hits else "clean", "files_scanned": scanned, "hits": hits}


# ---------------------------------------------------------------------------
# Test scoring in the scorer container


class ScorerBox:
    """A fresh pristine workspace copy scored inside the offline scorer image."""

    def __init__(self, pins: Pins, root: Path, logs: Path) -> None:
        self.pins = pins
        self.out = root / "out"
        self.out.mkdir(parents=True)
        extract(pins.tar_path, root)
        self.work = root / pins.name
        self.logs = logs
        self.logs.mkdir(parents=True, exist_ok=True)
        self.steps = 0

    def docker(self, label: str, command: list[str]) -> tuple[int, str]:
        self.steps += 1
        args = [
            "docker", "run", "--rm", "--network", "none",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            *SCORER_LIMITS,
            "--user", f"{os.getuid()}:{os.getgid()}",
            "-e", "HOME=/tmp",
            "-e", "PYTHONPATH=/work",
            "-v", f"{self.work}:/work",
            "-v", f"{self.out}:/out",
            "-w", "/work",
            images.SCORER_IMAGE,
            "timeout", "-k", "10", str(SCORER_TIMEOUT_S), *command,
        ]  # fmt: skip
        try:
            result = subprocess.run(args, env=clean_env(), capture_output=True, text=True,
                                    timeout=SCORER_TIMEOUT_S + 60, check=False)  # fmt: skip
            code, output = result.returncode, result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            code, output = 124, "host timeout"
        log = self.logs / f"{self.steps:02d}-{label}.log"
        log.write_text(f"$ {' '.join(command)}\nexit {code}\n\n{output}")
        return code, output

    def pytest(self, label: str, *targets: str) -> tuple[int, dict[str, str]]:
        report = f"junit-{self.steps + 1:02d}.xml"
        code, _ = self.docker(label, [*PYTEST, f"--junitxml=/out/{report}", *targets])
        cases = parse_junit(self.out / report)
        if (self.out / report).exists():
            shutil.copyfile(self.out / report, self.logs / report)
        return code, cases


def failed_final_tests(pins: Pins) -> dict[str, Any]:
    hidden = {p.name: "fail" for p in sorted(pins.hidden_tests.glob("test_*.py"))}
    return {"visible": "fail", "hidden": hidden, "suite": "fail", "unasync_check": "fail",
            "apply": "fail"}  # fmt: skip


def validate_final_tests(data: dict[str, Any]) -> None:
    if tuple(sorted(data)) != tuple(sorted(FINAL_TEST_KEYS)):
        raise ValueError(f"final_tests keys {sorted(data)} != {sorted(FINAL_TEST_KEYS)}")
    for key in ("visible", "suite", "unasync_check", "apply"):
        if data[key] not in ("pass", "fail"):
            raise ValueError(f"final_tests.{key} = {data[key]!r}")
    if not isinstance(data["hidden"], dict) or not data["hidden"]:
        raise ValueError("final_tests.hidden must be a non-empty mapping")
    if any(v not in ("pass", "fail") for v in data["hidden"].values()):
        raise ValueError("final_tests.hidden values must be pass|fail")


def score_tests(pins: Pins, diff_path: Path, logs: Path) -> dict[str, Any]:
    root = Path(tempfile.mkdtemp(prefix="score-", dir=COMPONENT_ROOT / "build"))
    try:
        box = ScorerBox(pins, root, logs)
        if diff_path.read_bytes().strip():
            shutil.copyfile(diff_path, box.out / "final.diff")
            code, _ = box.docker("apply", ["git", "apply", "--whitespace=nowarn",
                                           "/out/final.diff"])  # fmt: skip
            if code != 0:
                result = failed_final_tests(pins)
                validate_final_tests(result)
                return result
        visible_dest = box.work / pins.visible_test_dest
        visible_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pins.visible_test, visible_dest)

        suite_code, _ = box.pytest("suite", "tests", f"--ignore={pins.visible_test_dest}")
        visible_code, _ = box.pytest("visible", pins.visible_test_dest)

        hidden_root = box.work / HIDDEN_DIR
        if hidden_root.exists():
            shutil.rmtree(hidden_root)
        hidden_root.mkdir()
        hidden: dict[str, str] = {}
        for src in sorted(pins.hidden_tests.glob("test_*.py")):
            shutil.copyfile(src, hidden_root / src.name)
            _, cases = box.pytest(f"hidden-{src.stem}", f"{HIDDEN_DIR}/{src.name}")
            if not cases:
                hidden[src.name] = "fail"
            for key, state in sorted(cases.items()):
                hidden[f"{src.name}::{key.rsplit('::', 1)[-1]}"] = (
                    "pass" if state == "passed" else "fail"
                )
        unasync_code, _ = box.docker("unasync-check", ["python", "scripts/unasync.py", "--check"])
        result = {
            "visible": "pass" if visible_code == 0 else "fail",
            "hidden": hidden,
            "suite": "pass" if suite_code == 0 else "fail",
            "unasync_check": "pass" if unasync_code == 0 else "fail",
            "apply": "pass",
        }
        validate_final_tests(result)
        return result
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Trial


@dataclass
class TrialOptions:
    arm: str
    rep: int
    out: Path
    agent: str = "codex"
    timeout_s: float = DEFAULT_TIMEOUT_S
    token_cap: int | None = DEFAULT_TOKEN_CAP
    force: bool = False
    episode: str = "limiter-handoff"
    stub_step_delay: float | None = None


def prepare_trial_dir(out: Path, force: bool) -> None:
    if (out / "score.json").exists() and not force:
        raise RefusedError(f"{out} is a completed trial (score.json exists); use --force")
    if out.exists() and any(out.iterdir()):
        if not force:
            raise RefusedError(f"{out} exists and is not empty (incomplete trial); use --force")
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)


def pins_snapshot(pins: Pins, manifest: dict[str, Any]) -> dict[str, Any]:
    with open(pins.episode_dir / "pins.toml", "rb") as handle:
        freeze = dict(tomllib.load(handle).get("freeze") or {})
    return {
        "freeze": freeze,
        "repo_sha": pins.repo_sha,
        "commits": manifest["commits"],
        "tar_sha256": manifest["tar_sha256"],
        "pins_toml_sha256": sha256_file(pins.episode_dir / "pins.toml"),
        "agent_config_sha256": sha256_file(AGENT_CONFIG_PATH),
        "image_pins_sha256": sha256_file(images.PINS_PATH),
        "actions_yaml_sha256": sha256_file(pins.episode_dir / "gold" / "actions.yaml"),
        "counterfactual_yaml_sha256": sha256_file(pins.episode_dir / "gold" /
                                                  "counterfactual.yaml"),  # fmt: skip
        "hidden_tests_sha256": {p.name: sha256_file(p)
                                for p in sorted(pins.hidden_tests.glob("test_*.py"))},
        "visible_test_sha256": sha256_file(pins.visible_test),
    }  # fmt: skip


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def run_trial(opts: TrialOptions) -> dict[str, Any]:
    """Run and score one trial; returns meta. Raises RefusedError before any side effect
    when the trial must not run."""
    if opts.agent not in AGENTS:
        raise RefusedError(f"unknown agent {opts.agent}")
    secret = os.environ.get(SECRET_ENV)
    if opts.agent == "codex" and not secret:
        raise RefusedError(f"{SECRET_ENV} is not set; refusing to run --agent codex")
    config = load_agent_config()
    pins = load_pins(opts.episode)
    with open(pins.episode_dir / "pins.toml", "rb") as handle:
        arm_names = tomllib.load(handle)["arms"]["names"]
    if opts.arm not in arm_names:
        raise RefusedError(f"--arm must be one of {', '.join(arm_names)}")
    out = opts.out.resolve()
    prepare_trial_dir(out, opts.force)

    meta: dict[str, Any] = {
        "status": "running", "errors": [], "arm": opts.arm, "rep": opts.rep,
        "agent": opts.agent, "model": config["codex"]["model"],
        "reasoning_effort": config["codex"]["reasoning_effort"],
        "timeout_s": opts.timeout_s, "token_cap": opts.token_cap,
    }  # fmt: skip
    meta_path = out / "meta.json"
    write_json(meta_path, meta)
    finished = False
    try:
        manifest = verify_tar(pins)
        try:
            image_ids = images.verify()
        except images.ImagePinError as exc:
            raise TrialError(str(exc)) from exc
        meta["images"] = image_ids
        meta["tar_sha256"] = manifest["tar_sha256"]
        meta["pins"] = pins_snapshot(pins, manifest)
        image = config["image"]["name"]
        version = run(["docker", "run", "--rm", "--network", "none", image, "codex",
                       "--version"], check=False).stdout.strip()  # fmt: skip
        meta["codex_version"] = {"config": config["codex"]["version"], "image": version}
        if not version.endswith(config["codex"]["version"]):
            raise TrialError(f"image codex version {version!r} != {config['codex']['version']}")

        prompt = build_prompt(pins, opts.arm)
        (out / "prompt.txt").write_bytes(prompt)
        meta["prompt_sha256"] = sha256_bytes(prompt)
        (out / "prompt.sha256").write_text(f"{meta['prompt_sha256']}  prompt.txt\n")
        meta["egress"] = ensure_egress(config, image_ids["egress"])

        workspace = out / "workspace"
        with tempfile.TemporaryDirectory(prefix="extract-", dir=out) as tmp:
            extract(pins.tar_path, Path(tmp))
            shutil.move(str(Path(tmp) / pins.name), workspace)
        codex_home = out / "codex_home"
        codex_home.mkdir()
        container_out = out / "out"
        container_out.mkdir()

        stub_dir = None
        if opts.agent == "codex":
            command = codex_argv(config)
        else:
            stub_dir = out / "stub"
            stub_dir.mkdir()
            shutil.copyfile(STUB_AGENT_PATH, stub_dir / "stub_agent.py")
            write_json(stub_dir / "fix.json", stub_fix_data(pins))
            command = stub_argv(opts.agent, config)
            if opts.stub_step_delay is not None:
                command += ["--step-delay", str(opts.stub_step_delay)]
            meta["pins"]["stub_agent_sha256"] = sha256_file(STUB_AGENT_PATH)

        name = f"c0-trial-{opts.arm}-r{opts.rep}-{uuid.uuid4().hex[:8]}"
        argv = docker_argv(config, container_name=name, workspace=workspace,
                           codex_home=codex_home, out_dir=container_out, command=command,
                           stub_dir=stub_dir)  # fmt: skip
        assert_no_secret_in_argv(argv)
        meta["container"] = {"name": name, "argv": argv, "limits": list(AGENT_LIMITS)}
        write_json(meta_path, meta)

        meter = TokenMeter(opts.token_cap)
        rollout_glob = config["codex_home"]["rollout_glob"]
        result = run_container(argv, prompt, name=name, exec_path=out / "exec.jsonl",
                               stderr_path=out / "stderr.log", timeout_s=opts.timeout_s,
                               meter=meter, codex_home=codex_home,
                               rollout_glob=rollout_glob)  # fmt: skip
        meta.update({"start_utc": result.start_utc, "end_utc": result.end_utc,
                     "wall_seconds": result.wall_seconds, "exit_code": result.exit_code,
                     "terminated": result.terminated, "tokens": meter.summary()})  # fmt: skip
        if not (out / "exec.jsonl").read_bytes().strip():
            meta["errors"].append(f"agent produced no exec events (exit {result.exit_code})")
        (out / "proxy.log").write_text(
            proxy_log(meta["egress"]["container"], result.start_utc, result.end_utc)
        )

        rollout_path = out / "rollout.jsonl"
        meta["rollout_files"] = collect_rollout(codex_home, rollout_glob, rollout_path)
        if not meta["rollout_files"]:
            meta["errors"].append("no rollout file found in codex_home")

        meta["diff"] = compute_final_diff(workspace, manifest["commits"]["followup"],
                                          out / "final.diff")  # fmt: skip

        header = Header(arm=opts.arm, rep=opts.rep, model=config["codex"]["model"])
        trace = normalize_trace(out / "exec.jsonl",
                                rollout_path if rollout_path.exists() else None,
                                config["workspace"]["path"], header)  # fmt: skip
        write_trace(trace, out / "trace.jsonl")
        meta["thread_id"] = trace.header.thread_id

        final_tests = score_tests(pins, out / "final.diff", out / "scorer")
        write_json(out / "final_tests.json", final_tests)
        real = score_trial(out, "real", opts.episode)
        write_json(out / "score_cf.json", score_trial(out, "counterfactual", opts.episode))
        meta["score"] = {k: real.get(k) for k in ("solved", "turns_to_frontier",
                                                  "forbidden_count", "repeated_work_calls",
                                                  "failure_tag")}  # fmt: skip
        write_json(out / "score.json", real)
        finished = True
    except TrialError as exc:
        meta["errors"].append(str(exc))
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - recorded; the matrix keeps going
        meta["errors"].append(f"{type(exc).__name__}: {exc}")
    finally:
        if not finished and not meta["errors"]:
            meta["errors"].append("interrupted")
        meta["scrub"] = {"status": "pending"}
        write_json(meta_path, meta)
        # Scans every trial file, meta.json included; only the scrub result is added after.
        meta["scrub"] = scrub_check(out, secret)
        if meta["scrub"]["status"] == "LEAK":
            meta["errors"].append("secret value found in trial files: " +
                                  ", ".join(meta["scrub"]["hits"]))  # fmt: skip
        meta["status"] = "error" if meta["errors"] else "complete"
        write_json(meta_path, meta)
    return meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--arm", required=True)
    parser.add_argument("--rep", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--agent", choices=AGENTS, default="codex")
    parser.add_argument("--timeout-s", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--token-cap", type=int, default=DEFAULT_TOKEN_CAP)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--episode", default="limiter-handoff")
    parser.add_argument("--stub-step-delay", type=float, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    opts = TrialOptions(arm=args.arm, rep=args.rep, out=args.out, agent=args.agent,
                        timeout_s=args.timeout_s, token_cap=args.token_cap, force=args.force,
                        episode=args.episode, stub_step_delay=args.stub_step_delay)  # fmt: skip
    try:
        meta = run_trial(opts)
    except RefusedError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    score = meta.get("score") or {}
    print(
        f"{meta['status']}: arm={meta['arm']} rep={meta['rep']} agent={meta['agent']} "
        f"exit={meta.get('exit_code')} terminated={meta.get('terminated')} "
        f"tokens={(meta.get('tokens') or {}).get('input_plus_output')} "
        f"solved={score.get('solved')} ttf={score.get('turns_to_frontier')} "
        f"forbidden={score.get('forbidden_count')} "
        f"repeated={score.get('repeated_work_calls')} scrub={meta['scrub']['status']}"
    )
    for error in meta["errors"]:
        print(f"error: {error}", file=sys.stderr)
    return 0 if meta["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
