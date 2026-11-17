"""The held-out suite is checksum-sealed."""

from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "datasets" / "heldout"


def test_heldout_checksums_match() -> None:
    lines = (ROOT / "SHA256SUMS").read_text().splitlines()
    assert len(lines) == 2
    for line in lines:
        digest, name = line.split()
        payload = (ROOT / name).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == digest


def test_heldout_version_is_recorded() -> None:
    assert (ROOT / "VERSION").read_text().strip() == "component21-eval-1"
