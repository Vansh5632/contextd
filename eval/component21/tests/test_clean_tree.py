"""Official runs record a clean revision."""

from __future__ import annotations

import pytest

from harness.run import require_clean


def test_empty_status_is_accepted() -> None:
    require_clean("")
    require_clean("\n")


def test_dirty_status_is_refused() -> None:
    with pytest.raises(SystemExit) as caught:
        require_clean(" M crates/broker/src/extract.rs\n")
    assert "dirty tree" in str(caught.value)