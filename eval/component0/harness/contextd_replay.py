"""Replay Session A/B inside a hooked contextd daemon and freeze one briefing.

Usage (from eval/component0, after the replay image and Ollama model exist):
    python -m harness.contextd_replay [--generations 3] [--force] [--no-freeze] [--pause-s 3]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from harness.common import COMPONENT_ROOT, Pins, git, load_pins, run
from harness.dtar import extract
from harness.images import MODEL_DIGEST_PATH, OLLAMA_IMAGE, REPLAY_IMAGE, local_image_id

NETWORK = "contextd-c0-replay"
# Default Docker address pools are often already fully subnetted on a shared
# host, and `network create` then fails before it assigns anything. Pin a /24
# that is not one of the pools this benchmark already uses.
NETWORK_SUBNET = "10.231.2.0/24"
OLLAMA_NAME = "contextd-c0-ollama"
OLLAMA_URL = "http://contextd-c0-ollama:11434"
PATCH_DIR = "/patches"
DRAIN_TIMEOUT_S = 120.0
INNER = Path(__file__).with_name("contextd_replay_inner.py")

# Keys written to each generation's provenance.json. Tests assert this set.
PROVENANCE_KEYS = (
    "contextd_head",
    "diff_sha256",
    "dirty_files",
    "binary_sha256",
    "replay_image_id",
    "ollama_image_id",
    "model_digest",
    "config_toml",
    "pause_s",
    "query_wall_s",
    "head_matches_followup",
    "backlog_drain_method",
    "backlog_drain_result",
    "steps_typed",
    "shell_events",
    "fs_events",
    "proc_events",
    "git_events",
    "embeddings",
    "reply_trailing_newline",
)


@dataclass(frozen=True)
class ScriptStep:
    kind: str
    value: str


@dataclass(frozen=True)
class TypedCommand:
    session: str
    kind: str
    command: str


def parse_steps(data: dict[str, Any]) -> list[ScriptStep]:
    steps: list[ScriptStep] = []
    for entry in data["steps"]:
        if not isinstance(entry, dict) or len(entry) != 1:
            raise ValueError(f"each step needs exactly one of note/run/apply: {entry!r}")
        ((kind, value),) = entry.items()
        if kind not in ("note", "run", "apply"):
            raise ValueError(f"unknown step kind {kind!r}")
        # Notes are prose and collapse. Commands keep internal newlines so a
        # heredoc is typed the way the script wrote it.
        text = " ".join(str(value).split()) if kind == "note" else str(value).rstrip("\n")
        steps.append(ScriptStep(kind, text))
    return steps


def load_script(path: Path) -> list[ScriptStep]:
    return parse_steps(yaml.safe_load(path.read_text()))


def followup_commit_command(message: str) -> str:
    return f'git add -A && git commit -q -m "{message}"'


def typed_commands(
    sessions: list[tuple[str, list[ScriptStep]]],
    *,
    followup_message: str,
    patch_dir: str = PATCH_DIR,
) -> list[TypedCommand]:
    """Session A then B. Notes are skipped. The followup commit is appended last."""
    out: list[TypedCommand] = []
    for session, steps in sessions:
        for step in steps:
            if step.kind == "note":
                continue
            if step.kind == "run":
                out.append(TypedCommand(session, "run", step.value))
            elif step.kind == "apply":
                name = Path(step.value).name
                out.append(TypedCommand(session, "apply", f"git apply {patch_dir}/{name}"))
    out.append(TypedCommand("B", "commit", followup_commit_command(followup_message)))
    return out


def missing_provenance_keys(doc: dict[str, Any]) -> list[str]:
    return [key for key in PROVENANCE_KEYS if key not in doc]


def briefing_view(snapshot: dict[str, Any]) -> dict[str, list[Any]]:
    """Item-level fields the generations are compared on."""
    recent = [
        {"source": item.get("source"), "text": item.get("text")}
        for item in snapshot.get("recent_activity") or []
    ]
    history = [
        {"id": item.get("id"), "text": item.get("text")}
        for item in snapshot.get("relevant_history") or []
    ]
    related = [item.get("name") for item in snapshot.get("related") or []]
    return {"recent_activity": recent, "relevant_history": history, "related": related}


def compare_generations(briefings: list[dict[str, Any]]) -> dict[str, Any]:
    views = [briefing_view(snapshot) for snapshot in briefings]
    differences: list[dict[str, Any]] = []
    for index, view in enumerate(views[1:], start=2):
        fields = {
            key: {"generation_1": views[0][key], "generation": view[key]}
            for key in ("recent_activity", "relevant_history", "related")
            if views[0][key] != view[key]
        }
        if fields:
            differences.append({"generation": index, "fields": sorted(fields)})
    identical = not differences
    if identical:
        summary = (
            "all generations match on recent_activity texts/sources, "
            "relevant_history ids/texts, and related names"
        )
    else:
        parts = [
            f"generation {item['generation']} differs from generation 1 in "
            + ", ".join(item["fields"])
            for item in differences
        ]
        summary = "; ".join(parts)
    return {
        "identical": identical,
        "generation_count": len(views),
        "summary": summary,
        "differences": differences,
        "views": views,
    }


def render_config() -> str:
    return (
        'db_path = "/data/events.db"\n'
        'socket_path = "/data/contextd.sock"\n'
        "max_memory_mb = 500\n"
        f'ollama_url = "{OLLAMA_URL}"\n'
        'embedding_model = "nomic-embed-text"\n'
        'watch_root = "/work"\n'
    )


def git_identity_env(pins: Pins) -> dict[str, str]:
    """Same identity and followup timestamp build_workspace uses for that commit."""
    return {
        "GIT_AUTHOR_NAME": pins.author_name,
        "GIT_AUTHOR_EMAIL": pins.author_email,
        "GIT_AUTHOR_DATE": pins.followup_date,
        "GIT_COMMITTER_NAME": pins.author_name,
        "GIT_COMMITTER_EMAIL": pins.author_email,
        "GIT_COMMITTER_DATE": pins.followup_date,
    }


def _source_provenance() -> dict[str, Any]:
    path = COMPONENT_ROOT / "build" / "contextd-src" / "PROVENANCE.json"
    if not path.is_file():
        raise SystemExit(f"{path} missing; run scripts/build_contextd_image.sh first")
    return json.loads(path.read_text())


def _model_digest() -> str:
    if not MODEL_DIGEST_PATH.is_file():
        raise SystemExit(f"{MODEL_DIGEST_PATH} missing; run scripts/fetch_ollama_model.sh first")
    return MODEL_DIGEST_PATH.read_text().strip()


def _binary_sha256(image: str) -> str:
    result = run(
        [
            "docker", "run", "--rm", "--network", "none",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--entrypoint", "python", image, "-c",
            "import hashlib, pathlib; "
            "p = pathlib.Path('/usr/local/bin/contextd'); "
            "print(hashlib.sha256(p.read_bytes()).hexdigest())",
        ]
    )
    return result.stdout.strip()


def ensure_network() -> None:
    inspected = run(["docker", "network", "inspect", NETWORK], check=False)
    if inspected.returncode != 0:
        created = run(
            [
                "docker", "network", "create", "--internal",
                "--subnet", NETWORK_SUBNET, NETWORK,
            ],
            check=False,
        )
        if created.returncode != 0:
            detail = (created.stderr or created.stdout or "").strip()
            raise SystemExit(f"could not create docker network {NETWORK}: {detail}")
        return
    data = json.loads(inspected.stdout)
    if not data or not data[0].get("Internal"):
        raise SystemExit(f"docker network {NETWORK} exists but is not internal")


def _wait_api_tags() -> None:
    deadline = time.monotonic() + 120
    last = ""
    while time.monotonic() < deadline:
        result = run(
            [
                "docker", "run", "--rm", "--network", NETWORK,
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                REPLAY_IMAGE, "python", "-c",
                "import urllib.request; "
                "print(urllib.request.urlopen("
                f"'{OLLAMA_URL}/api/tags', timeout=5).read().decode())",
            ],
            check=False,
        )
        last = (result.stdout or "") + (result.stderr or "")
        if result.returncode == 0 and "nomic-embed-text" in (result.stdout or ""):
            return
        time.sleep(2)
    raise SystemExit(f"ollama /api/tags did not list nomic-embed-text:\n{last[-2000:]}")


def start_ollama() -> str:
    """Start the pinned Ollama container on the internal network. Returns ro or rw."""
    run(["docker", "rm", "-f", OLLAMA_NAME], check=False)
    volume = COMPONENT_ROOT / "build" / "ollama"
    if not volume.is_dir():
        raise SystemExit(f"{volume} missing; run scripts/fetch_ollama_model.sh first")
    last_error = ""
    for mode in ("ro", "rw"):
        suffix = ":ro" if mode == "ro" else ""
        result = run(
            [
                "docker", "run", "-d", "--name", OLLAMA_NAME,
                "--network", NETWORK,
                "--memory", "6g", "--cpus", "2", "--pids-limit", "512",
                "-v", f"{volume}:/root/.ollama{suffix}",
                OLLAMA_IMAGE,
            ],
            check=False,
        )
        if result.returncode != 0:
            last_error = (result.stderr or result.stdout or "").strip()
            run(["docker", "rm", "-f", OLLAMA_NAME], check=False)
            continue
        try:
            _wait_api_tags()
        except SystemExit as exc:
            last_error = str(exc)
            run(["docker", "rm", "-f", OLLAMA_NAME], check=False)
            continue
        return mode
    raise SystemExit(f"could not start {OLLAMA_NAME}: {last_error}")


def prepare_workspace(pins: Pins, gen_dir: Path, import_sha: str, followup_sha: str) -> Path:
    scratch = gen_dir / "scratch"
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True)
    extract(pins.tar_path, scratch)
    work = scratch / pins.name
    head = git(work, "rev-parse", "HEAD").strip()
    if head != followup_sha:
        raise SystemExit(f"workspace tar HEAD {head} != manifest followup {followup_sha}")
    git(work, "checkout", "-q", import_sha)
    git(work, "config", "user.name", pins.author_name)
    git(work, "config", "user.email", pins.author_email)
    git(work, "config", "commit.gpgsign", "false")
    # The frozen tar drops .git/hooks. contextd install refuses a missing dir.
    (work / ".git" / "hooks").mkdir(exist_ok=True)
    return work


def _run_generation(
    pins: Pins,
    gen_dir: Path,
    commands: list[TypedCommand],
    *,
    pause_s: float,
    import_sha: str,
    followup_sha: str,
    patch: Path,
) -> dict[str, Any]:
    work = prepare_workspace(pins, gen_dir, import_sha, followup_sha)
    data = gen_dir / "data"
    data.mkdir(parents=True, exist_ok=True)
    plan = {
        "commands": [cmd.__dict__ for cmd in commands],
        "pause_s": pause_s,
        "followup_sha": followup_sha,
        "config_toml": render_config(),
        "drain_timeout_s": DRAIN_TIMEOUT_S,
    }
    plan_path = gen_dir / "plan.json"
    plan_path.write_text(json.dumps(plan))
    name = f"contextd-c0-replay-{gen_dir.name}"
    run(["docker", "rm", "-f", name], check=False)
    args = [
        "docker", "run", "--name", name,
        "--network", NETWORK,
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--memory", "4g", "--cpus", "2", "--pids-limit", "1024",
        "-e", f"OLLAMA_HOST={OLLAMA_URL}",
        "-e", "HOME=/home/replay",
        "-e", "XDG_CONFIG_HOME=/home/replay/.config",
        "-e", "PYTHONPATH=/work",
        "-e", "PYTHONUNBUFFERED=1",
        "-e", "LC_ALL=C.UTF-8",
        "-e", "TZ=UTC",
        "-e", "SHELL=/bin/bash",
        # git diff on a pty pages through less and never returns to the prompt.
        "-e", "GIT_PAGER=cat",
        "-e", "PAGER=cat",
        "--ulimit", "nofile=65536:65536",
        "-w", "/work",
        "-v", f"{work}:/work",
        "-v", f"{data}:/data",
        "-v", f"{gen_dir}:/out",
        "-v", f"{patch}:{PATCH_DIR}/{patch.name}:ro",
        "-v", f"{INNER}:/opt/replay/inner.py:ro",
        "-v", f"{plan_path}:/opt/replay/plan.json:ro",
    ]
    for key, value in git_identity_env(pins).items():
        args += ["-e", f"{key}={value}"]
    args += [REPLAY_IMAGE, "python", "/opt/replay/inner.py", "/opt/replay/plan.json"]
    result = run(args, check=False)
    log_path = gen_dir / "driver.log"
    log_path.write_text((result.stdout or "") + (result.stderr or ""))
    run(["docker", "rm", "-f", name], check=False)
    result_path = gen_dir / "result.json"
    if not result_path.is_file():
        raise SystemExit(
            f"{gen_dir.name} produced no result.json (exit {result.returncode}):\n"
            f"{log_path.read_text()[-4000:]}"
        )
    payload = json.loads(result_path.read_text())
    payload["driver_exit"] = result.returncode
    return payload


def _provenance(
    source: dict[str, Any],
    payload: dict[str, Any],
    *,
    pause_s: float,
    replay_id: str,
    ollama_id: str,
    model_digest: str,
    binary_sha: str,
) -> dict[str, Any]:
    counts = payload["counts"]
    doc = {
        "contextd_head": source["head"],
        "diff_sha256": source["diff_sha256"],
        "dirty_files": source["dirty_files"],
        "binary_sha256": binary_sha,
        "replay_image_id": replay_id,
        "ollama_image_id": ollama_id,
        "model_digest": model_digest,
        "config_toml": payload["config_toml"],
        "pause_s": pause_s,
        "query_wall_s": payload["query_wall_s"],
        "head_matches_followup": payload["head_matches_followup"],
        "backlog_drain_method": payload["backlog_drain_method"],
        "backlog_drain_result": payload["backlog_drain_result"],
        "steps_typed": counts["steps_typed"],
        "shell_events": counts["shell"],
        "fs_events": counts["file_system"],
        "proc_events": counts["proc"],
        "git_events": counts["git"],
        "embeddings": counts["embeddings"],
        "reply_trailing_newline": payload["reply_trailing_newline"],
        "followup_sha": payload["followup_sha"],
        "head": payload["head"],
        "ollama_volume": payload.get("ollama_volume"),
        "run_apply_steps": counts["run_apply_steps"],
    }
    missing = missing_provenance_keys(doc)
    if missing:
        raise SystemExit(f"provenance missing keys: {missing}")
    return doc


def _load_briefing(gen_dir: Path) -> dict[str, Any]:
    path = gen_dir / "briefing.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text())


def freeze_generation(
    pins: Pins,
    gen_dir: Path,
    provenance: dict[str, Any],
    variation: dict[str, Any],
    *,
    force: bool,
) -> None:
    packets = pins.episode_dir / "packets"
    targets = [
        packets / "contextd.txt",
        packets / "contextd_events.jsonl",
        packets / "contextd_provenance.json",
    ]
    existing = [path for path in targets if path.exists()]
    if existing and not force:
        names = ", ".join(path.name for path in existing)
        raise SystemExit(f"frozen files exist ({names}); pass --force to overwrite")
    reply = (gen_dir / "briefing.reply").read_bytes()
    # Exact reply bytes. A trailing newline is kept only when the daemon sent one.
    targets[0].write_bytes(reply)
    shutil.copyfile(gen_dir / "events.jsonl", targets[1])
    frozen = {"provenance": provenance, "variation": variation}
    targets[2].write_text(json.dumps(frozen, indent=2, sort_keys=True) + "\n")


def _print_counts(
    generation: int, commands: list[TypedCommand], provenance: dict[str, Any]
) -> None:
    run_apply = sum(1 for cmd in commands if cmd.kind in ("run", "apply"))
    shell = provenance["shell_events"]
    print(
        f"gen {generation}: steps_typed={provenance['steps_typed']} "
        f"shell={shell} fs={provenance['fs_events']} proc={provenance['proc_events']} "
        f"git={provenance['git_events']} embeddings={provenance['embeddings']} "
        f"head_matches_followup={provenance['head_matches_followup']}"
    )
    typed = provenance["steps_typed"]
    if shell == typed:
        print(
            f"gen {generation}: shell events match steps typed ({typed}), "
            f"which is run+apply ({run_apply}) plus the appended commit"
        )
    elif shell == run_apply:
        print(f"gen {generation}: shell events match run+apply steps ({run_apply})")
    else:
        print(
            f"gen {generation}: shell events ({shell}) != steps typed ({typed}) "
            f"and != run+apply ({run_apply}). "
            "contextd drops `cd`/`ls` via should_drop before they are stored, "
            "and the bash hook only sends a history line when it changes."
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generations", type=int, default=3)
    parser.add_argument("--pause-s", type=float, default=3.0)
    parser.add_argument("--force", action="store_true", help="overwrite frozen packet files")
    parser.add_argument(
        "--no-freeze",
        action="store_true",
        help="leave generations in build/contextd-replay and skip the packet freeze",
    )
    parser.add_argument("--episode", default="limiter-handoff")
    return parser


def replay(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.generations < 1:
        print("--generations must be >= 1", file=sys.stderr)
        return 1

    pins = load_pins(args.episode)
    manifest = json.loads(pins.manifest_path.read_text())
    import_sha = manifest["commits"]["import"]
    followup_sha = manifest["commits"]["followup"]
    history = pins.episode_dir / "history"
    sessions = [
        ("A", load_script(history / "session_a.script.yaml")),
        ("B", load_script(history / "session_b.script.yaml")),
    ]
    commands = typed_commands(sessions, followup_message=pins.followup_message)
    patch = pins.followup_patch
    if not patch.is_file():
        raise SystemExit(f"followup patch missing: {patch}")

    source = _source_provenance()
    model_digest = _model_digest()
    replay_id = local_image_id(REPLAY_IMAGE)
    ollama_id = local_image_id(OLLAMA_IMAGE)
    if replay_id is None or ollama_id is None:
        raise SystemExit("replay or ollama image is missing; build and fetch first")
    binary_sha = _binary_sha256(REPLAY_IMAGE)

    ensure_network()
    volume_mode = start_ollama()
    root = COMPONENT_ROOT / "build" / "contextd-replay"
    root.mkdir(parents=True, exist_ok=True)
    provenances: list[dict[str, Any]] = []
    briefings: list[dict[str, Any]] = []
    failed = False
    try:
        for number in range(1, args.generations + 1):
            gen_dir = root / f"gen-{number}"
            if gen_dir.exists():
                shutil.rmtree(gen_dir)
            gen_dir.mkdir(parents=True)
            print(f"== generation {number}")
            payload = _run_generation(
                pins, gen_dir, commands,
                pause_s=args.pause_s,
                import_sha=import_sha,
                followup_sha=followup_sha,
                patch=patch,
            )
            if "counts" not in payload:
                raise SystemExit(f"{gen_dir.name} failed: {payload.get('error', payload)}")
            payload["ollama_volume"] = volume_mode
            provenance = _provenance(
                source, payload,
                pause_s=args.pause_s,
                replay_id=replay_id,
                ollama_id=ollama_id,
                model_digest=model_digest,
                binary_sha=binary_sha,
            )
            (gen_dir / "provenance.json").write_text(
                json.dumps(provenance, indent=2, sort_keys=True) + "\n"
            )
            provenances.append(provenance)
            briefings.append(_load_briefing(gen_dir))
            _print_counts(number, commands, provenance)
            if not provenance["head_matches_followup"]:
                failed = True
                print(
                    f"gen {number}: HEAD {payload.get('head')} != {followup_sha}",
                    file=sys.stderr,
                )
    finally:
        run(["docker", "rm", "-f", OLLAMA_NAME], check=False)

    variation = compare_generations(briefings)
    (root / "variation.json").write_text(json.dumps(variation, indent=2, sort_keys=True) + "\n")
    print(variation["summary"])
    if args.no_freeze:
        print(
            "left generations in build/contextd-replay; check with: "
            "python -m harness.regression check --id briefing-selection "
            "--candidate build/contextd-replay"
        )
    else:
        freeze_generation(pins, root / "gen-1", provenances[0], variation, force=args.force)
        print(f"froze {pins.episode_dir / 'packets' / 'contextd.txt'}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    try:
        return replay(argv)
    except SystemExit as exc:
        if exc.code not in (None, 0):
            print(exc, file=sys.stderr)
            return int(exc.code) if isinstance(exc.code, int) else 1
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
