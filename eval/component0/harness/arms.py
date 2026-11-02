"""Render, verify and place the context-packet arms of an episode.

Usage (from eval/component0):
    python -m harness.arms render  [--episode NAME]   # packets/raw.txt + packets/manifest.json
    python -m harness.arms check   [--episode NAME]   # recompute; compare with pins.toml [freeze]
    python -m harness.arms freeze  [--episode NAME]   # (re)write the pins.toml [freeze] section
    python -m harness.arms prompt --arm ARM [--episode NAME]
    python -m harness.arms enable-arm contextd [--episode NAME]

Arms: none (prompt only), raw (newest history units within the token budget), summary,
oracle, stale-only, and contextd once listed in pins.toml [arms].names. An arm is
available only when it is listed there. Budget and tokenizer come from pins.toml [arms].
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness.common import DEFAULT_EPISODE, EPISODES_DIR
from harness.packet_score import HISTORY_DELIMITER, PACKET_FILES, PACKET_KINDS, count_tokens

CONTEXTD_ARM = "contextd"
CONTEXTD_FREEZE_KEY = "contextd_txt"
CONTEXTD_REL = "packets/contextd.txt"
_NAMES_RE = re.compile(
    r"^(?P<indent>[ \t]*)names[ \t]*=[ \t]*\[(?P<body>[^\]]*)\][ \t]*$", re.M
)

FREEZE_FILES = {
    "history_jsonl": "history/history.jsonl",
    "raw_txt": "packets/raw.txt",
    "summary_txt": "packets/summary.txt",
    "oracle_txt": "packets/oracle.txt",
    "stale_only_txt": "packets/stale_only.txt",
    "atoms_yaml": "gold/atoms.yaml",
    "prompt_txt": "prompt.txt",
    "wrapper_txt": "wrapper.txt",
}


@dataclass(frozen=True)
class ArmConfig:
    names: tuple[str, ...]
    raw_budget_tokens: int
    tokenizer: str
    freeze: dict[str, str]


def load_config(episode_dir: Path) -> ArmConfig:
    with open(episode_dir / "pins.toml", "rb") as handle:
        pins = tomllib.load(handle)
    arms = pins["arms"]
    return ArmConfig(
        names=tuple(arms["names"]),
        raw_budget_tokens=int(arms["raw_budget_tokens"]),
        tokenizer=str(arms["tokenizer"]),
        freeze={str(k): str(v) for k, v in (pins.get("freeze") or {}).items()},
    )


def get_tokenizer(name: str) -> Any:
    import tiktoken

    return tiktoken.get_encoding(name)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Raw history


def load_history(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def render_unit(unit: dict[str, Any]) -> str:
    prefix = f"[session {unit['session']}]"
    if unit["kind"] == "note":
        text = f"{prefix} note: {unit['text']}"
    else:
        output = unit.get("output") or ""
        text = f"{prefix} $ {unit['command']}" + (f"\n{output}" if output else "")
    if HISTORY_DELIMITER in text:
        raise ValueError(f"unit {unit['unit']} contains the history delimiter")
    return text


def join_units(rendered: list[str]) -> str:
    return HISTORY_DELIMITER.join(rendered) + "\n" if rendered else ""


def truncate_oldest(rendered: list[str], budget: int, tokenizer: Any) -> int:
    """Index of the first kept unit: oldest complete units are dropped until the joined
    text fits the budget. Units are never split."""
    for start in range(len(rendered) + 1):
        if count_tokens(tokenizer, join_units(rendered[start:])) <= budget:
            return start
    return len(rendered)


@dataclass(frozen=True)
class RawPacket:
    text: str
    kept_units: list[int]
    dropped_units: list[int]
    session_tokens: dict[str, int]


def build_raw(units: list[dict[str, Any]], budget: int, tokenizer: Any) -> RawPacket:
    rendered = [render_unit(u) for u in units]
    start = truncate_oldest(rendered, budget, tokenizer)
    session_tokens: dict[str, int] = {}
    for unit, text in zip(units, rendered):
        key = str(unit["session"])
        session_tokens[key] = session_tokens.get(key, 0) + count_tokens(tokenizer, text)
    return RawPacket(
        text=join_units(rendered[start:]),
        kept_units=[int(u["unit"]) for u in units[start:]],
        dropped_units=[int(u["unit"]) for u in units[:start]],
        session_tokens=session_tokens,
    )


# ---------------------------------------------------------------------------
# Prompts


def packet_kind(arm: str) -> str:
    return PACKET_KINDS[arm]


def packet_files_for(names: tuple[str, ...]) -> dict[str, str]:
    """Packet files this episode materializes. contextd is omitted until enabled,
    because packets/contextd.txt does not exist yet."""
    return {
        arm: filename
        for arm, filename in PACKET_FILES.items()
        if arm != CONTEXTD_ARM or arm in names
    }


def packet_text(arm: str, episode_dir: Path) -> str:
    if arm == "none":
        return ""
    return (episode_dir / "packets" / PACKET_FILES[arm]).read_text()


def assemble_prompt(episode_dir: Path, packet: str | None) -> str:
    """none: exactly prompt.txt; otherwise wrapper + packet + one blank line + prompt."""
    prompt = (episode_dir / "prompt.txt").read_text()
    if packet is None:
        return prompt
    wrapper = (episode_dir / "wrapper.txt").read_text()
    return wrapper + packet.rstrip("\n") + "\n\n" + prompt


def build_prompt(arm: str, episode_dir: Path) -> str:
    if arm != "none" and arm not in PACKET_FILES:
        raise ValueError(f"unknown arm {arm!r}")
    return assemble_prompt(episode_dir, None if arm == "none" else packet_text(arm, episode_dir))


# ---------------------------------------------------------------------------
# Render / check / freeze


def build_manifest(episode_dir: Path, config: ArmConfig, raw: RawPacket,
                   history: bytes, units: int, tokenizer: Any) -> dict[str, Any]:  # fmt: skip
    packets: dict[str, Any] = {"none": {"file": None, "sha256": sha256(b""), "tokens": 0}}
    for arm, name in packet_files_for(config.names).items():
        if arm != "raw" and not (episode_dir / "packets" / name).is_file():
            continue
        text = raw.text if arm == "raw" else packet_text(arm, episode_dir)
        entry: dict[str, Any] = {"file": f"packets/{name}", "sha256": sha256(text.encode()),
                                 "tokens": count_tokens(tokenizer, text)}  # fmt: skip
        if arm == "raw":
            entry["kept_units"] = raw.kept_units
            entry["dropped_units"] = raw.dropped_units
        packets[arm] = entry
    prompts = {}
    for arm in config.names:
        if arm not in ("none", "raw") and arm not in packets:
            continue
        text = raw.text if arm == "raw" else packet_text(arm, episode_dir)
        prompt = assemble_prompt(episode_dir, None if arm == "none" else text)
        prompts[arm] = {
            "sha256": sha256(prompt.encode()),
            "tokens": count_tokens(tokenizer, prompt),
        }
    return {
        "episode": episode_dir.name,
        "tokenizer": config.tokenizer,
        "raw_budget_tokens": config.raw_budget_tokens,
        "history": {
            "file": "history/history.jsonl",
            "sha256": sha256(history),
            "units": units,
            "session_tokens": raw.session_tokens,
        },  # fmt: skip
        "packets": packets,
        "prompts": prompts,
    }


def compute(episode_dir: Path) -> tuple[RawPacket, dict[str, Any]]:
    config = load_config(episode_dir)
    tokenizer = get_tokenizer(config.tokenizer)
    history_path = episode_dir / FREEZE_FILES["history_jsonl"]
    units = load_history(history_path)
    raw = build_raw(units, config.raw_budget_tokens, tokenizer)
    if count_tokens(tokenizer, raw.text) > config.raw_budget_tokens:
        raise AssertionError("raw packet exceeds the budget")
    manifest = build_manifest(episode_dir, config, raw, history_path.read_bytes(), len(units),
                              tokenizer)  # fmt: skip
    return raw, manifest


def manifest_text(manifest: dict[str, Any]) -> str:
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


def render(episode_dir: Path) -> dict[str, Any]:
    raw, manifest = compute(episode_dir)
    packets = episode_dir / "packets"
    packets.mkdir(exist_ok=True)
    (packets / "raw.txt").write_text(raw.text)
    (packets / "manifest.json").write_text(manifest_text(manifest))
    return manifest


def freeze_files(episode_dir: Path, names: tuple[str, ...]) -> dict[str, str]:
    """Freeze map. contextd_txt is included only once the arm is listed and the packet exists."""
    files = dict(FREEZE_FILES)
    if CONTEXTD_ARM in names and (episode_dir / CONTEXTD_REL).is_file():
        files[CONTEXTD_FREEZE_KEY] = CONTEXTD_REL
    return files


def file_hashes(episode_dir: Path) -> dict[str, str]:
    config = load_config(episode_dir)
    files = freeze_files(episode_dir, config.names)
    return {key: sha256((episode_dir / rel).read_bytes()) for key, rel in files.items()}


def check(episode_dir: Path) -> list[str]:
    """Problems found; empty means every artifact matches its frozen hash."""
    problems = []
    config = load_config(episode_dir)
    raw, manifest = compute(episode_dir)
    raw_path = episode_dir / "packets" / "raw.txt"
    if not raw_path.exists() or raw_path.read_text() != raw.text:
        problems.append("packets/raw.txt differs from a fresh render of history.jsonl")
    manifest_path = episode_dir / "packets" / "manifest.json"
    if not manifest_path.exists() or manifest_path.read_text() != manifest_text(manifest):
        problems.append("packets/manifest.json differs from a fresh render")
    if not config.freeze:
        problems.append("pins.toml has no [freeze] section")
    if CONTEXTD_ARM in config.names and not (episode_dir / CONTEXTD_REL).is_file():
        problems.append(f"{CONTEXTD_REL} missing for enabled arm {CONTEXTD_ARM}")
    hashed = file_hashes(episode_dir)
    rels = freeze_files(episode_dir, config.names)
    for key, digest in hashed.items():
        frozen = config.freeze.get(key)
        rel = rels[key]
        if frozen is not None and frozen != digest:
            problems.append(f"{rel}: sha256 {digest[:12]} != frozen {frozen[:12]}")
        elif frozen is None and config.freeze:
            problems.append(f"{rel}: not frozen in pins.toml")
    return problems


def freeze(episode_dir: Path) -> dict[str, str]:
    """Rewrite only the trailing [freeze] section of pins.toml."""
    hashes = file_hashes(episode_dir)
    pins_path = episode_dir / "pins.toml"
    text = pins_path.read_text()
    marker = "\n[freeze]\n"
    if marker in text:
        head, _, tail = text.partition(marker)
        if "\n[" in tail:
            raise SystemExit("pins.toml: [freeze] must be the last section")
        text = head + "\n"
    block = "[freeze]\n" + "".join(f'{k} = "{v}"\n' for k, v in hashes.items())
    pins_path.write_text(text.rstrip("\n") + "\n\n" + block)
    return hashes


def set_arm_names(text: str, names: list[str]) -> str:
    match = _NAMES_RE.search(text)
    if match is None:
        raise SystemExit("pins.toml: could not find [arms] names = [...]")
    rendered = "names = [" + ", ".join(json.dumps(name) for name in names) + "]"
    return text[: match.start()] + match.group("indent") + rendered + text[match.end() :]


def enable_arm(episode_dir: Path, arm: str) -> dict[str, str]:
    """Append `arm` to [arms].names, refresh the manifest, and refreeze.

    Refuses when packets/contextd.txt is missing. The manifest is re-rendered so
    `arms check` sees the new prompt hash; raw.txt is unchanged when history is.
    """
    if arm != CONTEXTD_ARM:
        raise SystemExit(f"enable-arm supports {CONTEXTD_ARM}, not {arm!r}")
    packet = episode_dir / CONTEXTD_REL
    if not packet.is_file():
        raise SystemExit(f"refusing: {CONTEXTD_REL} is missing")
    config = load_config(episode_dir)
    if arm not in config.names:
        pins_path = episode_dir / "pins.toml"
        pins_path.write_text(set_arm_names(pins_path.read_text(), [*config.names, arm]))
    render(episode_dir)
    return freeze(episode_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "command", choices=("render", "check", "freeze", "prompt", "enable-arm")
    )
    parser.add_argument("arm_name", nargs="?")
    parser.add_argument("--episode", default=DEFAULT_EPISODE)
    parser.add_argument("--arm")
    args = parser.parse_args(argv)
    episode_dir = EPISODES_DIR / args.episode

    if args.command == "enable-arm":
        if not args.arm_name:
            parser.error("enable-arm requires an arm name")
        for key, digest in enable_arm(episode_dir, args.arm_name).items():
            print(f"{key:<15} {digest}")
        return 0
    if args.command == "prompt":
        names = load_config(episode_dir).names
        if args.arm not in names:
            parser.error(f"--arm must be one of {', '.join(names)}")
        sys.stdout.write(build_prompt(args.arm, episode_dir))
        return 0
    if args.command == "render":
        manifest = render(episode_dir)
        for arm, entry in manifest["packets"].items():
            print(f"{arm:<11} {entry['tokens']:>6} tokens  {entry['sha256'][:12]}")
        print(f"raw dropped units: {manifest['packets']['raw']['dropped_units']}")
        print(f"session tokens: {manifest['history']['session_tokens']}")
        return 0
    if args.command == "freeze":
        for key, digest in freeze(episode_dir).items():
            print(f"{key:<15} {digest}")
        return 0
    problems = check(episode_dir)
    for problem in problems:
        print(f"FAIL {problem}")
    print(f"arms check {'FAIL' if problems else 'PASS'}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
