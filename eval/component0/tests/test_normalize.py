from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from harness.common import load_pins
from harness.normalize import extract_inner, normalize, normalize_trace, parse_script
from harness.schema import (
    Access,
    CommandEvent,
    EditEvent,
    Header,
    Trace,
    UsageEvent,
    dumps_trace,
    parse_trace,
)

RAW = load_pins().episode_dir / "fixtures" / "raw_codex"
POOL = "httpcore/_async/connection_pool.py"


def accesses(script: str) -> list[tuple]:
    parsed = parse_script(script)
    return [(a.path, a.mode, a.start, a.end) for a in parsed.accesses]


# --- wrapper extraction -----------------------------------------------------


@pytest.mark.parametrize(
    "command,inner",
    [
        ("/bin/bash -lc 'sed -n 1,5p a.py'", "sed -n 1,5p a.py"),
        ('bash -lc "rg -n foo httpcore"', "rg -n foo httpcore"),
        ("/usr/bin/zsh -c 'ls'", "ls"),
        ("bash -e -c 'cat x'", "cat x"),
        ("ls -la", "ls -la"),
        ("bash script.sh", "bash script.sh"),
        ("/bin/bash -lc \"bash -lc 'cat a.py'\"", "cat a.py"),
    ],
)
def test_extract_inner(command: str, inner: str) -> None:
    assert extract_inner(command) == inner


# --- access parsing ---------------------------------------------------------


@pytest.mark.parametrize(
    "script,expected",
    [
        ("sed -n '300,340p' " + POOL, [(POOL, "read", 300, 340)]),
        ("sed -n 12p a.py", [("a.py", "read", 12, 12)]),
        ("sed -n '1,5p;20,25p' a.py", [("a.py", "read", 1, 5), ("a.py", "read", 20, 25)]),
        ("sed -n -e 1,5p -e '9,$p' a.py", [("a.py", "read", 1, 5), ("a.py", "read", 9, None)]),
        ("sed -n '10,+5p' a.py", [("a.py", "read", 10, 15)]),
        ("sed -i 's/a/b/' a.py", [("a.py", "write", None, None)]),
        ("sed 's/a/b/' a.py", [("a.py", "read", None, None)]),
        ("head -n 40 a.py", [("a.py", "read", 1, 40)]),
        ("head -40 a.py", [("a.py", "read", 1, 40)]),
        ("head a.py", [("a.py", "read", 1, 10)]),
        ("tail -n 20 a.py", [("a.py", "read", -20, None)]),
        ("tail -n +50 a.py", [("a.py", "read", 50, None)]),
        ("cat a.py b.py", [("a.py", "read", None, None), ("b.py", "read", None, None)]),
        ("nl -ba " + POOL + " | sed -n '300,340p'", [(POOL, "read", 300, 340)]),
        ("cat -n a.py | head -n 30", [("a.py", "read", 1, 30)]),
        ("head -n 360 a.py | tail -n 60", [("a.py", "read", 301, 360)]),
        ("sed -n '100,200p' a.py | head -n 10", [("a.py", "read", 100, 109)]),
        ("awk 'NR>=10 && NR<=20' a.py", [("a.py", "read", 10, 20)]),
        ("awk 'NR==5,NR==9 {print NR\": \"$0}' a.py", [("a.py", "read", 5, 9)]),
        ("cat /workspace/httpcore/_models.py", [("httpcore/_models.py", "read", None, None)]),
        ("cd /workspace/httpcore && cat _models.py", [("httpcore/_models.py", "read", None, None)]),
        ("cat /tmp/probe.py", [("/tmp/probe.py", "read", None, None)]),
        ("cat ../outside.txt", [("/outside.txt", "read", None, None)]),
        ("ls", [(".", "meta", None, None)]),
        ("ls -la httpcore/_backends", [("httpcore/_backends", "meta", None, None)]),
        ("wc -l a.py", [("a.py", "meta", None, None)]),
        ("find httpcore -name '*.py'", [("httpcore", "meta", None, None)]),
        ("python scripts/unasync.py", [("scripts/unasync.py", "exec", None, None)]),
        ("python3 -u scripts/unasync.py --check", [("scripts/unasync.py", "exec", None, None)]),
        (
            "python -m pytest -q -p no:cacheprovider tests/test_pool_counts.py::test_pool_counts",
            [("tests/test_pool_counts.py", "exec", None, None)],
        ),
        ("pytest -k cap tests/_async", [("tests/_async", "exec", None, None)]),
        ("python -m pytest -q", [(".", "exec", None, None)]),
        ("git diff -- " + POOL, [(POOL, "meta", None, None)]),
        ("git diff HEAD " + POOL, [(POOL, "meta", None, None)]),
        ("git show HEAD:" + POOL, [(POOL, "read", None, None)]),
        ("git status && git log --oneline -5", []),
        ("echo done > notes.txt", [("notes.txt", "write", None, None)]),
        ("sed -n 1,5p a.py 2>&1", [("a.py", "read", 1, 5)]),
        ("sed -n 1,5p a.py 2>/dev/null", [("a.py", "read", 1, 5)]),
        ("FOO=1 timeout 30 cat a.py", [("a.py", "read", None, None)]),
        ("cp a.py b.py", [("a.py", "read", None, None), ("b.py", "write", None, None)]),
    ],
)
def test_access_rules(script: str, expected: list[tuple]) -> None:
    parsed = parse_script(script)
    assert not parsed.unparsed, script
    assert accesses(script) == expected


def test_rg_search_accesses() -> None:
    parsed = parse_script('rg -n -g "*.py" "_assign_requests" httpcore tests')
    assert [(a.path, a.mode, a.pattern) for a in parsed.accesses] == [
        ("httpcore", "search", "_assign_requests"),
        ("tests", "search", "_assign_requests"),
    ]
    default = parse_script("rg -n create_connection").accesses
    assert [(a.path, a.pattern) for a in default] == [(".", "create_connection")]
    flagged = parse_script("rg -F -i -e 'is_idle()' " + POOL).accesses[0]
    assert (flagged.pattern, flagged.fixed, flagged.ignore_case) == ("is_idle()", True, True)
    listing = parse_script("rg --files httpcore").accesses
    assert [(a.path, a.mode) for a in listing] == [("httpcore", "meta")]


def test_grep_search_accesses() -> None:
    parsed = parse_script("grep -rn 'max_connections' httpcore/ | head -20")
    assert [(a.path, a.mode, a.pattern) for a in parsed.accesses] == [
        ("httpcore", "search", "max_connections")
    ]
    assert parse_script("grep -n foo").accesses == []
    piped = parse_script("cat " + POOL + " | grep -n is_idle").accesses
    assert [(a.path, a.mode, a.pattern) for a in piped] == [(POOL, "search", "is_idle")]
    git_grep = parse_script("git grep -n _max_keepalive_connections").accesses
    assert [(a.path, a.pattern) for a in git_grep] == [(".", "_max_keepalive_connections")]


def test_rg_after_pipe_reads_stdin() -> None:
    parsed = parse_script("git diff | rg is_idle")
    assert parsed.accesses == [] and not parsed.unparsed


# --- heredocs and unparsed --------------------------------------------------


def test_python_heredoc_is_unparsed_probe() -> None:
    parsed = parse_script("python - <<'EOF'\nimport threading\nprint(1)\nEOF")
    assert parsed.unparsed
    assert parsed.accesses == []
    assert parsed.probe == "import threading\nprint(1)\n"


def test_python_dash_c_is_unparsed_probe() -> None:
    parsed = parse_script('python -c "import concurrent.futures as f; print(f)"')
    assert parsed.unparsed and parsed.probe == "import concurrent.futures as f; print(f)"


def test_cat_heredoc_writes_file_and_probe_when_python_runs() -> None:
    parsed = parse_script("cat > /tmp/p.py <<EOF\nimport threading\nEOF\npython /tmp/p.py")
    assert not parsed.unparsed
    assert [(a.path, a.mode) for a in parsed.accesses] == [
        ("/tmp/p.py", "write"),
        ("/tmp/p.py", "exec"),
    ]
    assert parsed.probe == "import threading\n"


def test_heredoc_body_does_not_produce_accesses() -> None:
    parsed = parse_script("cat <<EOF\ncat httpcore/_backends/mock.py\nEOF")
    assert parsed.accesses == [] and not parsed.unparsed


@pytest.mark.parametrize(
    "script",
    [
        "python - <<EOF\nprint(1)\n",
        "cat $(git ls-files | grep pool)",
        "for f in a.py b.py; do cat $f; done",
        "vim a.py",
        "sed -n '/def x/,/def y/p' a.py",
        "awk '/idle/' a.py",
        "cat 'unterminated",
        "find . -name '*.py' -exec grep -n x {} +",
        "git apply fix.patch",
    ],
)
def test_unparsed_forms(script: str) -> None:
    parsed = parse_script(script)
    assert parsed.unparsed, script


def test_unparsed_segment_keeps_known_accesses() -> None:
    parsed = parse_script("make lint && sed -n 1,5p a.py")
    assert parsed.unparsed
    assert accesses("make lint && sed -n 1,5p a.py") == [("a.py", "read", 1, 5)]


# --- exec + rollout ---------------------------------------------------------


@pytest.fixture(scope="module")
def raw_trace() -> Trace:
    return normalize_trace(RAW / "exec.jsonl", RAW / "rollout.jsonl", "/workspace", Header())


def test_rollout_diff_merged_into_edit(raw_trace: Trace) -> None:
    edits = [e for e in raw_trace.events if isinstance(e, EditEvent)]
    assert len(edits) == 1
    edit = edits[0]
    assert edit.path == POOL and edit.change_kind == "update"
    assert edit.patch_format == "unified"
    fixed_line = "+            elif len(self._connections) < self._max_connections:"
    assert edit.patch and fixed_line in edit.patch
    assert [(h.old_start, h.old_len, h.new_start, h.new_len) for h in edit.hunks] == [
        (321, 10, 321, 7)
    ]


def test_edit_without_rollout_has_no_patch() -> None:
    events = normalize(RAW / "exec.jsonl", None)
    edit = next(e for e in events if isinstance(e, EditEvent))
    assert edit.patch is None and edit.hunks == []
    assert [e.source for e in events if isinstance(e, UsageEvent)] == ["exec"]


def test_usage_is_cumulative_and_ordered(raw_trace: Trace) -> None:
    usages = [e for e in raw_trace.events if isinstance(e, UsageEvent)]
    inputs = [u.input_tokens for u in usages]
    assert inputs == sorted(inputs)
    assert usages[0].i == 0 and usages[0].source == "rollout"
    assert usages[-1].source == "exec" and usages[-1].input_tokens == 48210
    assert usages[-2].input_tokens == usages[-1].input_tokens
    first_tool = raw_trace.tool_events()[0]
    edit = next(e for e in raw_trace.events if isinstance(e, EditEvent))
    before_edit = [u for u in usages if u.i < edit.i][-1]
    assert before_edit.input_tokens == 31220
    assert first_tool.i > usages[0].i


def test_tool_indices_and_header(raw_trace: Trace) -> None:
    assert raw_trace.header.thread_id == "0199b6f2-3c4d-7e10-9a2b-5c6d7e8f9a01"
    tools = raw_trace.tool_events()
    assert [e.t for e in tools] == [1, 2, 3, 4, 5, 6]
    assert [e.kind for e in tools] == ["command", "command", "command", "edit", "command",
                                       "other_tool"]  # fmt: skip
    commands = [e for e in tools if isinstance(e, CommandEvent)]
    assert commands[1].inner == "nl -ba httpcore/_async/connection_pool.py | sed -n '300,345p'"
    assert commands[1].accesses == [Access(POOL, "read", 300, 345)]
    assert commands[0].exit_code == 1 and commands[0].output_chars > 0


def test_rollout_matched_by_id_when_ids_align(tmp_path: Path) -> None:
    exec_lines = [
        {"type": "thread.started", "thread_id": "t1"},
        {"type": "item.completed", "item": {"id": "fc_b", "type": "file_change",
         "changes": [{"path": "/workspace/b.py", "kind": "update"}], "status": "completed"}},
        {"type": "item.completed", "item": {"id": "fc_a", "type": "file_change",
         "changes": [{"path": "/workspace/a.py", "kind": "add"}], "status": "completed"}},
    ]  # fmt: skip
    rollout_lines = [
        {"type": "event_msg", "payload": {"type": "item_completed", "item": {
            "type": "FileChange", "id": "fc_a",
            "changes": {"/workspace/a.py": {"type": "add", "content": "x = 1\n"}}}}},
        {"type": "event_msg", "payload": {"type": "item_completed", "item": {
            "type": "FileChange", "id": "fc_b",
            "changes": {"/workspace/b.py": {"type": "update",
                                            "unified_diff": "@@ -1 +1 @@\n-a\n+b\n"}}}}},
    ]  # fmt: skip
    (tmp_path / "e.jsonl").write_text("".join(json.dumps(r) + "\n" for r in exec_lines))
    (tmp_path / "r.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rollout_lines))
    edits = [e for e in normalize(tmp_path / "e.jsonl", tmp_path / "r.jsonl")
             if isinstance(e, EditEvent)]  # fmt: skip
    assert [(e.path, e.change_kind) for e in edits] == [("b.py", "update"), ("a.py", "add")]
    assert edits[0].patch == "@@ -1 +1 @@\n-a\n+b\n"
    assert edits[1].patch == "@@ -0,0 +1,1 @@\n+x = 1\n"


def test_apply_patch_shell_command_becomes_edits(tmp_path: Path) -> None:
    patch = (
        "*** Begin Patch\n"
        "*** Update File: httpcore/_async/connection_pool.py\n"
        "@@ def _assign_requests_to_connections\n"
        "                 pool_request.assign_to_connection(connection)\n"
        "-            elif (\n"
        "+            elif len(self._connections) < self._max_connections:\n"
        "*** Add File: notes/plan.txt\n"
        "+line one\n"
        "*** End Patch\n"
    )
    command = "bash -lc " + shlex.quote("apply_patch <<'PATCH'\n" + patch + "PATCH")
    exec_lines = [
        {"type": "item.completed", "item": {"id": "item_1", "type": "command_execution",
         "command": command, "aggregated_output": "Done!", "exit_code": 0,
         "status": "completed"}},
        {"type": "item.completed", "item": {"id": "item_2", "type": "command_execution",
         "command": "bash -lc 'ls'", "aggregated_output": "", "exit_code": 0,
         "status": "completed"}},
    ]  # fmt: skip
    (tmp_path / "e.jsonl").write_text("".join(json.dumps(r) + "\n" for r in exec_lines))
    trace = normalize_trace(tmp_path / "e.jsonl")
    edits = [e for e in trace.events if isinstance(e, EditEvent)]
    assert [(e.path, e.change_kind, e.patch_format, e.source) for e in edits] == [
        (POOL, "update", "envelope", "apply_patch_command"),
        ("notes/plan.txt", "add", "unified", "apply_patch_command"),
    ]
    assert edits[0].hunks[0].old_start is None and edits[0].hunks[0].old_len == 2
    assert [e.t for e in trace.tool_events()] == [1, 1, 2]


def test_incomplete_command_is_kept(tmp_path: Path) -> None:
    lines = [
        {"type": "item.started", "item": {"id": "item_1", "type": "command_execution",
         "command": "bash -lc 'cat a.py'", "aggregated_output": "", "exit_code": None,
         "status": "in_progress"}},
        {"type": "turn.failed", "error": {"message": "stream disconnected"}},
    ]  # fmt: skip
    (tmp_path / "e.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lines))
    events = normalize(tmp_path / "e.jsonl")
    assert [e.kind for e in events] == ["error", "command"]
    assert events[1].status == "in_progress"  # type: ignore[union-attr]


def test_schema_roundtrip(raw_trace: Trace) -> None:
    text = dumps_trace(raw_trace)
    again = parse_trace(text.splitlines())
    assert dumps_trace(again) == text
    first = json.loads(text.splitlines()[1])
    assert list(first)[:2] == ["i", "kind"]
