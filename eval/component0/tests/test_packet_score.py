from __future__ import annotations

from pathlib import Path

import pytest

from harness import arms
from harness.common import EPISODES_DIR
from harness.packet_score import (
    HISTORY_DELIMITER,
    classify_unit,
    load_atoms,
    score_packet,
    split_units,
)

EPISODE_DIR = EPISODES_DIR / "limiter-handoff"
ATOMS = load_atoms(EPISODE_DIR / "gold" / "atoms.yaml")


class WordTokenizer:
    def encode_ordinary(self, text: str) -> list[str]:
        return text.split()


def matched(text: str) -> set[str]:
    return set(classify_unit(text, ATOMS).atoms)


@pytest.mark.parametrize(
    ("atom_id", "text"),
    [
        ("finding_idle_not_counted", "Idle keepalive connections are not counted toward the cap."),
        ("finding_idle_not_counted", "The create branch only counts non-idle connections."),
        ("finding_idle_not_counted",
         "A third origin gets a new connection while the other two sit idle."),
        ("decision_idle_counts", "Idle connections count toward max_connections from now on."),
        ("decision_idle_counts",
         "All connections, idle ones included, count against the limit."),
        ("decision_idle_counts", "At the cap, evict an idle connection instead of going over."),
        ("constraint_signatures", "Keep the constructor signature and defaults as they are."),
        ("constraint_signatures", "handle_async_request must not change."),
        ("constraint_no_new_deps", "No extra dependencies."),
        ("constraint_no_new_deps", "Do not add any new packages."),
        ("ruled_out_mock_backend", "The mock backend seems fine."),
        ("ruled_out_mock_backend", "Ruled out: MockBackend network backend."),
        ("ruled_out_lock", "Not the cause: the pool lock."),
        ("ruled_out_lock", "Locking is not involved; it reproduces single-threaded."),
        ("stale_redis_counter", "Use a global admission counter shared by processes."),
        ("unresolved_keepalive_comprehension",
         "The keepalive cleanup comprehension is odd but out of scope."),
        ("next_step_create_branch",
         "Next: fix the create-new-connection branch in _assign_requests_to_connections, "
         "then run scripts/unasync.py."),
        ("optional_host_header_fix", "Host header fix done."),
        ("optional_exact_counts", "It reports 3 held with max_connections=2."),
    ],
)  # fmt: skip
def test_atom_matches_paraphrase(atom_id: str, text: str) -> None:
    assert atom_id in matched(text)


@pytest.mark.parametrize(
    ("atom_id", "text"),
    [
        ("decision_idle_counts", "Idle keepalive connections never count toward the cap."),
        ("decision_idle_counts", "Idle connections do not count towards max_connections."),
        ("ruled_out_mock_backend", "First suspect: the mock network backend."),
        ("ruled_out_lock", "Next suspect: the pool lock losing a wakeup."),
        ("ruled_out_lock", "with self._state_lock: the event was ruled out"),
        ("stale_redis_counter", "Redistribution and use in source and binary forms"),
        ("next_step_create_branch", "Look at how the pool assigns requests to connections."),
    ],
)  # fmt: skip
def test_atom_rejects_near_miss(atom_id: str, text: str) -> None:
    assert atom_id not in matched(text)


def test_session_b_history_matches_only_the_optional_host_fix() -> None:
    history = arms.load_history(EPISODE_DIR / "history" / "history.jsonl")
    session_b = [u for u in history if u["session"] == "B"]
    assert session_b
    for unit in session_b:
        hits = matched(arms.render_unit(unit))
        assert hits <= {"optional_host_header_fix"}, (unit["unit"], hits)


def test_superseded_marker_separates_stale_from_marked() -> None:
    current = classify_unit("Plan: a shared admission counter in Redis.", ATOMS)
    marked = classify_unit("Rejected: a shared admission counter in Redis.", ATOMS)
    assert current.stale_as_current and not current.stale_marked and not current.relevant
    assert marked.stale_marked and not marked.stale_as_current and marked.relevant


def test_stale_as_current_unit_is_imprecise_even_with_a_required_atom() -> None:
    unit = ("Next step: call the Redis counter from the create-new-connection branch in "
            "_assign_requests_to_connections, then run unasync.")  # fmt: skip
    result = score_packet([unit], ATOMS, WordTokenizer())
    assert "next_step_create_branch" in result["matched_atoms"]
    assert result["stale_as_current"] == 1
    assert result["unit_precision"] == 0.0


def test_empty_packet() -> None:
    result = score_packet([], ATOMS, WordTokenizer())
    assert result["required_recall"] == 0
    assert result["unit_precision"] is None
    assert result["token_precision"] is None
    assert result["packet_tokens"] == 0
    assert result["missing_atoms"] == [
        a.id for a in ATOMS.atoms if a.role in ("required", "constraint", "ruled_out", "next_step")
    ]


def test_token_precision_is_token_weighted() -> None:
    relevant = "Constraint: no new dependencies."  # 4 words
    noise = "one two three four five six seven eight nine ten eleven twelve"  # 12 words
    result = score_packet([relevant, noise], ATOMS, WordTokenizer())
    assert result["unit_precision"] == 0.5
    assert result["token_precision"] == pytest.approx(4 / 16)
    assert result["packet_tokens"] == 16


def test_recall_counts_only_recall_roles() -> None:
    result = score_packet(["Redis counter.", "count 3 diverged from 2"], ATOMS, WordTokenizer())
    assert result["required_recall"] == 0
    assert result["matched_atoms"] == ["stale_redis_counter", "optional_exact_counts"]


def test_split_units() -> None:
    assert split_units("a\n\n b \n \nc\n", "paragraphs") == ["a", "b", "c"]
    text = HISTORY_DELIMITER.join(["[session A] $ ls\nx\n\ny", "[session B] note: z"]) + "\n"
    assert split_units(text, "history") == ["[session A] $ ls\nx\n\ny", "[session B] note: z"]
    with pytest.raises(ValueError):
        split_units("a", "lines")


def test_load_atoms_rejects_unknown_role(tmp_path: Path) -> None:
    path = tmp_path / "atoms.yaml"
    path.write_text("version: 1\nsuperseded_marker: 'x'\natoms:\n"
                    "  - {id: a, role: vital, claim: c, match: ['a']}\n")  # fmt: skip
    with pytest.raises(ValueError):
        load_atoms(path)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("oracle.txt", {"required_recall": 1.0, "unit_precision": 1.0, "stale_as_current": 0}),
        ("stale_only.txt", {"stale_marked": 0}),
        ("summary.txt", {"stale_as_current": 0, "unresolved_present": False}),
    ],
)
def test_frozen_packets(name: str, expected: dict[str, object]) -> None:
    text = (EPISODE_DIR / "packets" / name).read_text()
    result = score_packet(split_units(text, "paragraphs"), ATOMS, WordTokenizer())
    for key, value in expected.items():
        assert result[key] == value, key
