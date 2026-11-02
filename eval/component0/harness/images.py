"""Pinned local Docker image IDs used by trials.

Usage (from eval/component0):
    python -m harness.images check   # compare local image IDs with harness/image_pins.toml
    python -m harness.images pin     # (re)write harness/image_pins.toml from local images

Images are built locally (scripts/build_agent_image.sh, gate_g1, the egress compose file,
scripts/build_contextd_image.sh), so the pin is the local image ID, not a registry digest.
The ollama role is the exception that is pulled by digest; its pin is still the local
image ID. The model role's id is the nomic-embed-text digest from `ollama show`.
"""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path

from harness.common import COMPONENT_ROOT, run

PINS_PATH = COMPONENT_ROOT / "harness" / "image_pins.toml"
AGENT_CONFIG = COMPONENT_ROOT / "harness" / "agent_config.toml"
SCORER_IMAGE = "contextd-c0-scorer:v0"
REPLAY_IMAGE = "contextd-c0-replay:v0"
# Registry digest of ollama/ollama. Filled in by the image pin step; the fetch
# script reads this constant so the pull and the pin cannot drift.
OLLAMA_IMAGE = "ollama/ollama@sha256:2a6e883b917fc543389599dae79918f5cac9e1438890506982f44aa4f5625d01"
MODEL_NAME = "nomic-embed-text"
MODEL_DIGEST_PATH = COMPONENT_ROOT / "build" / "ollama" / "nomic-embed-text.digest"
ROLES = ("agent", "scorer", "egress", "replay", "ollama", "model")


class ImagePinError(Exception):
    pass


def image_names() -> dict[str, str]:
    with open(AGENT_CONFIG, "rb") as handle:
        config = tomllib.load(handle)
    return {
        "agent": str(config["image"]["name"]),
        "scorer": SCORER_IMAGE,
        "egress": str(config["egress"]["proxy_image"]),
        "replay": REPLAY_IMAGE,
        "ollama": OLLAMA_IMAGE,
        "model": MODEL_NAME,
    }


def local_image_id(name: str) -> str | None:
    result = run(["docker", "image", "inspect", name, "--format", "{{.Id}}"], check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def local_model_digest() -> str | None:
    if not MODEL_DIGEST_PATH.is_file():
        return None
    text = MODEL_DIGEST_PATH.read_text().strip()
    return text or None


def local_pin_value(role: str, name: str) -> str | None:
    if role == "model":
        return local_model_digest()
    return local_image_id(name)


def load_pins(path: Path = PINS_PATH) -> dict[str, dict[str, str]]:
    if not path.exists():
        raise ImagePinError(f"{path.name} missing; run `python -m harness.images pin`")
    with open(path, "rb") as handle:
        data = tomllib.load(handle)
    return {role: {k: str(v) for k, v in entry.items()} for role, entry in data["images"].items()}


def verify(roles: tuple[str, ...] = ROLES) -> dict[str, str]:
    """role -> image ID or model digest; ImagePinError if any pin is missing or differs."""
    pins = load_pins()
    names = image_names()
    problems = []
    ids: dict[str, str] = {}
    for role in roles:
        pin = pins.get(role)
        if pin is None:
            problems.append(f"{role}: not pinned")
            continue
        expected = pin.get("id")
        if not expected:
            problems.append(f"{role}: pin has no id")
            continue
        actual = local_pin_value(role, names[role])
        label = pin.get("name", names[role])
        if actual is None:
            problems.append(f"{role}: {label} not present")
        elif actual != expected:
            problems.append(f"{role}: {label} is {actual[:19]}, pinned {expected[:19]}")
        else:
            ids[role] = actual
    if problems:
        raise ImagePinError("image pin check failed: " + "; ".join(problems))
    return ids


def pin(path: Path = PINS_PATH) -> dict[str, str]:
    lines = [
        "# Local image IDs pinned for trials. Rewrite: `python -m harness.images pin`.",
        "# images.model id is the ollama model digest, not a Docker image id.",
        "",
    ]
    ids = {}
    for role, name in image_names().items():
        value = local_pin_value(role, name)
        if value is None:
            raise ImagePinError(f"{role}: {name} not present; build or fetch it first")
        ids[role] = value
        lines += [f"[images.{role}]", f'name = "{name}"', f'id = "{value}"', ""]
    path.write_text("\n".join(lines))
    return ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "pin"))
    args = parser.parse_args(argv)
    try:
        ids = pin() if args.command == "pin" else verify()
    except ImagePinError as exc:
        print(exc)
        return 1
    for role, image_id in ids.items():
        print(f"{role:<7} {image_id}")
    print(f"images {args.command} OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
