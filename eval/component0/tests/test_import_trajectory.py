"""history.jsonl becomes daemon events. The extractor does not do this."""

from harness.import_trajectory import import_units


def test_note_and_command_become_daemon_events() -> None:
    units = [
        {
            "unit": 17,
            "session": "A",
            "kind": "command",
            "command": "sed -n 40,80p pool.py",
            "exit_code": 0,
            "output": "if not c.is_idle():\n    branch create\n",
        },
        {
            "unit": 18,
            "session": "A",
            "kind": "note",
            "text": "The create branch counts only non-idle connections.",
        },
    ]
    events = import_units(units, cwd="/work")
    assert events[0]["id"] == "h_A_17"
    assert events[0]["source"] == "shell"
    assert events[0]["payload"]["command"] == "sed -n 40,80p pool.py"
    assert events[0]["payload"]["exit_code"] == 0
    assert events[0]["payload"]["cwd"] == "/work"
    assert events[0]["payload"]["session"] == "A"
    assert "is_idle" in events[0]["payload"]["output"]
    assert events[1]["id"] == "h_A_18"
    assert events[1]["source"] == "note"
    assert events[1]["payload"]["text"].startswith("The create branch")
    assert events[1]["payload"]["session"] == "A"
    assert "output" not in events[1]["payload"]


def test_output_is_truncated_at_200_lines() -> None:
    units = [
        {
            "unit": 2,
            "session": "A",
            "kind": "command",
            "command": "python -m pytest -q",
            "exit_code": 1,
            "output": "\n".join(f"line {i}" for i in range(250)),
        }
    ]
    events = import_units(units, cwd="/work")
    output = events[0]["payload"]["output"]
    assert output.endswith("[output truncated]")
    assert len(output.splitlines()) == 201
