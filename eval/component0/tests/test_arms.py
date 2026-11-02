from __future__ import annotations

from typing import Any

import pytest

from harness import arms
from harness.common import EPISODES_DIR
from harness.packet_score import HISTORY_DELIMITER, count_tokens, split_units

EPISODE_DIR = EPISODES_DIR / "limiter-handoff"


class WordTokenizer:
    def encode_ordinary(self, text: str) -> list[str]:
        return text.split()


def make_units(sizes: list[int]) -> list[dict[str, Any]]:
    units = []
    for i, size in enumerate(sizes, start=1):
        session = "A" if i <= len(sizes) // 2 else "B"
        units.append({"unit": i, "session": session, "kind": "note",
                      "text": " ".join(f"w{i}_{k}" for k in range(size))})  # fmt: skip
    return units


@pytest.mark.parametrize("budget", [0, 5, 40, 57, 58, 100, 10_000])
def test_truncation_keeps_newest_whole_units_within_budget(budget: int) -> None:
    units = make_units([30, 5, 12, 20, 7, 9])
    rendered = [arms.render_unit(u) for u in units]
    raw = arms.build_raw(units, budget, WordTokenizer())
    assert count_tokens(WordTokenizer(), raw.text) <= budget
    kept = split_units(raw.text, "history")
    # A contiguous suffix of whole units: nothing split, newest kept.
    assert kept == rendered[len(rendered) - len(kept) :]
    assert raw.kept_units == [u["unit"] for u in units[len(units) - len(kept) :]]
    assert raw.dropped_units + raw.kept_units == [u["unit"] for u in units]
    # Maximal: one more (older) unit would not fit.
    if raw.dropped_units:
        bigger = arms.join_units(rendered[len(raw.dropped_units) - 1 :])
        assert count_tokens(WordTokenizer(), bigger) > budget


def test_truncation_is_deterministic() -> None:
    units = make_units([11, 3, 8, 21, 2])
    first = arms.build_raw(units, 30, WordTokenizer())
    second = arms.build_raw(units, 30, WordTokenizer())
    assert first == second


def test_render_unit_formats() -> None:
    note = {"unit": 1, "session": "A", "kind": "note", "text": "hello"}
    command = {"unit": 2, "session": "B", "kind": "command", "command": "ls",
               "output": "a\nb", "exit_code": 0}  # fmt: skip
    silent = {"unit": 3, "session": "B", "kind": "command", "command": "true", "output": "",
              "exit_code": 0}  # fmt: skip
    assert arms.render_unit(note) == "[session A] note: hello"
    assert arms.render_unit(command) == "[session B] $ ls\na\nb"
    assert arms.render_unit(silent) == "[session B] $ true"
    bad = dict(note, text="x" + HISTORY_DELIMITER + "y")
    with pytest.raises(ValueError):
        arms.render_unit(bad)


def test_prompt_none_is_exactly_prompt_txt() -> None:
    prompt = arms.build_prompt("none", EPISODE_DIR).encode()
    assert prompt == b"Continue the interrupted work in this repo.\n"


def test_prompt_oracle_exact_bytes() -> None:
    oracle = (EPISODE_DIR / "packets" / "oracle.txt").read_bytes()
    expected = (b"Notes from earlier sessions on this repo:\n" + oracle.rstrip(b"\n")
                + b"\n\nContinue the interrupted work in this repo.\n")  # fmt: skip
    prompt = arms.build_prompt("oracle", EPISODE_DIR).encode()
    assert prompt == expected
    assert prompt.startswith(b"Notes from earlier sessions on this repo:\nFinding: ")
    assert b"rerun the visible test.\n\nContinue the interrupted work" in prompt


def test_frozen_raw_packet_fits_budget_and_keeps_newest() -> None:
    config = arms.load_config(EPISODE_DIR)
    tokenizer = arms.get_tokenizer(config.tokenizer)
    text = (EPISODE_DIR / "packets" / "raw.txt").read_text()
    assert count_tokens(tokenizer, text) <= config.raw_budget_tokens
    history = arms.load_history(EPISODE_DIR / "history" / "history.jsonl")
    kept = split_units(text, "history")
    assert kept == [arms.render_unit(u) for u in history[len(history) - len(kept) :]]
    assert 0 < len(kept) < len(history)


def test_arms_check_passes() -> None:
    assert arms.check(EPISODE_DIR) == []
