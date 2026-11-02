from __future__ import annotations

import yaml

from harness.common import load_pins
from harness.contextd_replay import (
    PROVENANCE_KEYS,
    ScriptStep,
    briefing_view,
    compare_generations,
    followup_commit_command,
    load_script,
    missing_provenance_keys,
    parse_steps,
    typed_commands,
)

FOLLOWUP = "models: fix default port check in host header"

SESSION_A = """
session: A
steps:
  - note: >-
      Picking up the pool count failure.
  - run: echo hello
  - run: |
      python - <<'EOF'
      import httpcore
      print(stream.read(64), stream.read(64))
      EOF
"""

SESSION_B = """
session: B
steps:
  - note: Different problem today.
  - apply: patches/session_b_fix.patch
  - run: git diff
"""


def test_notes_skipped_apply_translated_commit_last_and_heredoc_intact() -> None:
    sessions = [
        ("A", parse_steps(yaml.safe_load(SESSION_A))),
        ("B", parse_steps(yaml.safe_load(SESSION_B))),
    ]
    commands = typed_commands(sessions, followup_message=FOLLOWUP)

    assert [cmd.kind for cmd in commands] == ["run", "run", "apply", "run", "commit"]
    assert all("Picking up" not in cmd.command for cmd in commands)
    assert commands[2].command == "git apply /patches/session_b_fix.patch"
    heredoc = commands[1].command
    assert "python - <<'EOF'" in heredoc
    assert "import httpcore" in heredoc
    assert heredoc.endswith("EOF")
    assert "\n" in heredoc
    assert commands[-1].session == "B"
    assert commands[-1].command == followup_commit_command(FOLLOWUP)
    assert commands[-1].command == (
        'git add -A && git commit -q -m "models: fix default port check in host header"'
    )


def test_real_session_scripts_keep_heredocs_and_append_the_commit() -> None:
    pins = load_pins()
    history = pins.episode_dir / "history"
    sessions = [
        ("A", load_script(history / "session_a.script.yaml")),
        ("B", load_script(history / "session_b.script.yaml")),
    ]
    commands = typed_commands(sessions, followup_message=pins.followup_message)
    notes = sum(1 for _session, steps in sessions for step in steps if step.kind == "note")
    assert notes > 0
    assert not any(cmd.kind == "note" for cmd in commands)
    heredocs = [cmd.command for cmd in commands if "<<'EOF'" in cmd.command]
    assert len(heredocs) == 3
    for command in heredocs:
        assert "import httpcore" in command
        assert command.rstrip("\n").endswith("EOF")
        assert "\n" in command
    assert commands[-1].command == followup_commit_command(pins.followup_message)
    assert sum(1 for cmd in commands if cmd.kind == "apply") == 1


def test_provenance_schema_keys() -> None:
    doc = {key: None for key in PROVENANCE_KEYS}
    assert missing_provenance_keys(doc) == []
    del doc["model_digest"]
    del doc["head_matches_followup"]
    assert missing_provenance_keys(doc) == ["model_digest", "head_matches_followup"]


def test_generation_comparison_is_item_level() -> None:
    base = {
        "recent_activity": [
            {"source": "shell", "text": "ran `pytest`"},
            {"source": "git", "text": "committed"},
        ],
        "relevant_history": [{"id": "01", "text": "ran `sed`"}],
        "related": [{"name": "ConnectionPool"}],
    }
    same = compare_generations([base, base])
    assert same["identical"]
    assert same["differences"] == []

    reordered = {
        "recent_activity": list(reversed(base["recent_activity"])),
        "relevant_history": [{"id": "01", "text": "ran `sed`"}],
        "related": [{"name": "ConnectionPool"}],
    }
    moved = compare_generations([base, reordered])
    assert not moved["identical"]
    assert moved["differences"][0]["fields"] == ["recent_activity"]

    other_id = {
        "recent_activity": base["recent_activity"],
        "relevant_history": [{"id": "99", "text": "ran `sed`"}],
        "related": [{"name": "URL"}],
    }
    changed = compare_generations([base, other_id])
    assert changed["differences"][0]["fields"] == ["related", "relevant_history"]
    assert briefing_view(base)["related"] == ["ConnectionPool"]


def test_script_step_round_trip_does_not_collapse_command_whitespace() -> None:
    steps = parse_steps(yaml.safe_load(SESSION_A))
    assert steps[0] == ScriptStep("note", "Picking up the pool count failure.")
    assert "\n" in steps[2].value
