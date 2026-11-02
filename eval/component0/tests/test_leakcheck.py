from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from harness.common import Pins, clean_env, load_pins, run
from harness.leakcheck import (
    added_lines,
    check_workspace,
    compile_term,
    load_terms,
    scan_text,
)

PINS = load_pins()
TERMS = load_terms(PINS.leak_terms)


def tree_term(pattern: str):
    return next(t for t in TERMS.tree if t.pattern == pattern)


def change_term(pattern: str):
    return next(t for t in TERMS.change if t.pattern == pattern)


@pytest.mark.parametrize(
    "text", ["Redistribution and use", "* Redistributions of source", "predis", "redistribute"]
)
def test_redis_term_respects_word_boundaries(text: str) -> None:
    assert not scan_text(text, [tree_term(r"\bredis\b")], "t")


@pytest.mark.parametrize("text", ["we use redis here", "REDIS", "Redis-backed", "(redis)"])
def test_redis_term_is_case_insensitive(text: str) -> None:
    assert scan_text(text, [tree_term(r"\bredis\b")], "t")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Idle connections don't count toward the cap", True),
        ("idle connections do not count", True),
        ("the idle connection count", True),
        ("IDLE connections should not be kept", False),
    ],
)
def test_idle_count_term(text: str, expected: bool) -> None:
    term = tree_term(r"\bidle connections? (do not |don't )?count")
    assert bool(scan_text(text, [term], "t")) is expected


def test_is_idle_call_does_not_match_idle_word() -> None:
    term = change_term(r"\bidle\b")
    assert not scan_text("if not c.is_idle()", [term], "t")
    assert scan_text("skip Idle ones", [term], "t")


def test_scan_text_reports_line_numbers() -> None:
    hits = scan_text("clean\nhypothesis: X\n", [compile_term(r"\bhypothesis\b")], "f.py")
    assert [(h.source, h.location) for h in hits] == [("f.py", "2")]


def test_added_lines_skip_headers_and_removed_lines() -> None:
    patch = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old lock\n+new line\n context\n"
    assert added_lines(patch) == "new line"


def test_max_connections_only_checked_in_commit_messages() -> None:
    term = change_term(r"\bmax_connections\b")
    assert term.scopes == frozenset({"commit_messages"})


def _commit(repo: Path, message: str) -> None:
    env = clean_env(
        {
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
        }
    )
    run(["git", "-C", str(repo), "add", "-A"], env=env)
    run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", "commit", "-q", "-m", message],
        env=env,
    )


def _fake_episode(tmp_path: Path, *, leak: bool) -> tuple[Pins, Path]:
    episode = tmp_path / "episode"
    (episode / "patches").mkdir(parents=True)
    plant = episode / "patches" / "plant.patch"
    removal = episode / "patches" / "remove_covering_test.patch"
    followup = episode / "patches" / "followup.patch"
    plant.write_text("+++ b/m.py\n+value = compute()\n")
    removal.write_text("--- a/t.py\n+++ b/t.py\n-def test_gone():\n-    pass\n")
    followup.write_text("+++ b/n.py\n+port = 80\n")
    if leak:
        followup.write_text("+++ b/n.py\n+# keep the lock held\n")
        removal.write_text(removal.read_text() + "+added = True\n")
    pins = dataclasses.replace(
        PINS,
        episode_dir=episode,
        plant_patches=(plant, removal),
        followup_patch=followup,
    )

    repo = tmp_path / "ws"
    (repo / "tests").mkdir(parents=True)
    run(["git", "init", "-q", "-b", "main", str(repo)], env=clean_env())
    (repo / "tests" / "test_pool_counts.py").write_text(
        "def test_pool_counts():\n    assert True\n"
    )
    (repo / "README.md").write_text("Redistribution is allowed.\n")
    _commit(repo, "import")
    if leak:
        (repo / "NOTES.txt").write_text("We ruled out Redis early.\n")
        (repo / "tests" / "copy_pool_counts.py").write_text("\n")
        (repo / "m.py.rej").write_text("\n")
        _commit(repo, "tune max_connections for idle pool")
        (repo / "NOTES.txt").unlink()
        _commit(repo, "cleanup")
    return pins, repo


def test_clean_workspace_has_no_hits(tmp_path: Path) -> None:
    pins, repo = _fake_episode(tmp_path, leak=False)
    assert check_workspace(pins, repo, TERMS) == []


def test_planted_leaks_are_detected(tmp_path: Path) -> None:
    pins, repo = _fake_episode(tmp_path, leak=True)
    hits = check_workspace(pins, repo, TERMS)
    sources = {h.source for h in hits}
    terms = {h.term for h in hits}

    assert "git log -p --all" in sources
    assert r"\bruled out\b" in terms and r"\bredis\b" in terms
    assert any(h.source.startswith("commit ") and h.term == r"\bmax_connections\b" for h in hits)
    assert any(h.source.startswith("commit ") and h.term == r"\bidle\b" for h in hits)
    assert any(h.source == "patches/followup.patch (+)" for h in hits)
    assert any(h.term == "deletion-only" for h in hits)
    assert any(h.term == "pool_counts" for h in hits)
    assert any(h.term == "*.orig|*.rej" for h in hits)
