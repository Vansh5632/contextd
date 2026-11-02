# Event log is the trajectory

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> Do **not** commit unless the user explicitly asks. Each task ends when its test passes.

**Goal:** The quote step reads one daemon event log. That log carries the notes and the command output. `history.jsonl` is only the fixture an importer turns into those events.

**Architecture:** Add a `note` event. A shell event may carry an `output` string. The fold keeps using the exit code and ignores both the note and the output. One importer reads `history.jsonl` and writes daemon-shaped events. `extract_claims.py` reads that event log and no longer opens the transcript. `contextd note` writes a note event on the live socket. The interactive prompt hook stays command, exit code, and folder. Wrapping a typed command to tee its output can hang a terminal, and agent shells never source that hook. Output on a live shell event is a later plan.

**Tech Stack:** Rust (`crates/core` event enum, `crates/memory` fold, `bin/contextd` CLI, `crates/sources` socket ingest). Python (`eval/component0/harness/import_trajectory.py`, `eval/component0/harness/extract_claims.py`). Tests: `cargo test -p contextd-core -p memory -p contextd-sources` and `python -m pytest eval/component0/tests/test_extract_claims.py eval/component0/tests/test_import_trajectory.py`.

**Research:** `docs/component-2-research.md`, `docs/component-2-design.md`. The shell hook contract is `docs/briefing-selection-failure.md` section 7.

---

## What this plan changes

Today the extractor opens `eval/component0/episodes/limiter-handoff/history/history.jsonl` and a separate 31-event replay log. The daemon log has the command and the exit code. It has no note text and no command output. A daemon that only has those 31 events still has required-atom recall 0.

After this plan there is one reader of the transcript: `import_trajectory`. It writes events a live daemon could have stored:

- `source: "note"`, payload `{ "session": "A", "text": "<the sentence>" }`
- `source: "shell"`, payload `{ "command", "exit_code", "cwd", "session", "output" }`

`extract()` walks that list. Evidence ids are the event ids. The speech-act rules stay the ones in `extract_claims.py`: constraint, decision, ruled out, next step, out of scope, and a file-read conclusion that shares two content words with `payload.output`.

## What stays frozen

- `fold` in `crates/memory/src/project.rs` still opens a task only for a shell exit. A note does not become a task. `output` does not change the reality text. The reality text stays `{command} exited {code}`.
- `project_claims` still refuses a candidate that is not a verbatim span, still refuses `observed` on an extracted claim, and still does not read `atoms.yaml` or `oracle.txt`.
- The interactive snippets in `crates/sources/src/install.rs` stay `command`, `exit_code`, and `cwd`.
- The agent matrix is not re-run. The packet on disk changes only if `write_packet` is run after the extractor reads the imported log and the snapshot bytes differ. Task 6 checks the score. It does not start Codex.

## File map

- Modify: `crates/core/src/event.rs` — add `EventSource::Note`.
- Modify: `crates/memory/src/project.rs` — one test that a note and a shell `output` leave the fold's reality text unchanged.
- Modify: `crates/sources/src/shell.rs` — one test that a note line is accepted on the socket and a claim line is still refused.
- Modify: `bin/contextd/src/cli.rs` — `contextd note <TEXT>`.
- Modify: `bin/contextd/src/main.rs` — dispatch the new command.
- Create: `eval/component0/harness/import_trajectory.py` — transcript in, event log out.
- Create: `eval/component0/tests/test_import_trajectory.py`.
- Modify: `eval/component0/harness/extract_claims.py` — `extract(events)`.
- Modify: `eval/component0/tests/test_extract_claims.py` — events, and a ban on `history.jsonl` inside the extractor.
- Modify: `docs/component-2-design.md` — one paragraph: the extractor input is the event log.
- Modify: `docs/connecting-agents.md` — one row for `contextd note`.

---

### Task 1: A note event is stored and is not a task

**Files:**
- Modify: `crates/core/src/event.rs`
- Modify: `crates/memory/src/project.rs` (tests module)
- Test: `cargo test -p contextd-core event_source_uses_snake_case_in_json`
- Test: `cargo test -p memory note_event_does_not_open_a_task`

- [ ] **Step 1: Write the failing fold test**

In `crates/memory/src/project.rs`, inside `mod tests`, add:

```rust
#[test]
fn note_event_does_not_open_a_task() {
    let events = vec![RawEvent {
        id: "n1".into(),
        timestamp_ms: 1,
        source: EventSource::Note,
        payload: json!({
            "session": "A",
            "text": "Decision: idle connections count toward the cap."
        }),
    }];
    let projection = fold(&events);
    assert!(projection.tasks.is_empty());
    assert!(projection.claims.is_empty());
}
```

Add `EventSource` to the test module's imports if it is not already imported there. The file already imports `EventSource` at the top of the crate. The test module uses `super::*` or its own imports; match the existing test module.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cargo test -p memory note_event_does_not_open_a_task`

Expected: FAIL to compile. `EventSource` has no `Note` variant.

- [ ] **Step 3: Add the variant**

In `crates/core/src/event.rs`, add `Note` to `EventSource` after `Claim`:

```rust
pub enum EventSource {
    Shell,
    FileSystem,
    Git,
    Editor,
    Proc,
    Manifest,
    Claim,
    Note,
}
```

In the snake_case test in the same file, add `(EventSource::Note, "\"note\"")` to the `cases` array.

`fold` already ignores every source other than `Shell` and `Claim`, so a `Note` row produces an empty projection once it compiles.

- [ ] **Step 4: Run the tests**

Run: `cargo test -p contextd-core event_source_uses_snake_case_in_json && cargo test -p memory note_event_does_not_open_a_task`

Expected: both PASS.

---

### Task 2: Shell output is kept on the event and ignored by the fold

**Files:**
- Modify: `crates/memory/src/project.rs` (tests module)
- Test: `cargo test -p memory shell_output_does_not_change_the_reality_text`

The payload field is `output`. `crates/pipeline/src/content.rs` already reads `output` and `stderr` when it summarizes a failure. This task does not call that summarizer. The projector reality stays the exit line.

- [ ] **Step 1: Write the failing test**

```rust
#[test]
fn shell_output_does_not_change_the_reality_text() {
    let command = "python -m pytest tests/test_pool_counts.py";
    let events = vec![RawEvent {
        id: "s1".into(),
        timestamp_ms: 1,
        source: EventSource::Shell,
        payload: json!({
            "command": command,
            "exit_code": 1,
            "cwd": "/work",
            "session": "A",
            "output": "E   AssertionError: count 3 diverged from 2"
        }),
    }];
    let projection = fold(&events);
    let reality = projection
        .claims
        .iter()
        .find(|claim| claim.kind == ClaimKind::Reality)
        .expect("exit becomes a reality");
    assert_eq!(
        reality.text,
        format!("{command} exited 1")
    );
    assert_eq!(reality.confidence, ClaimConfidence::Observed);
    assert!(!reality.text.contains("diverged"));
}
```

Use the `ClaimKind` and `ClaimConfidence` imports already present in that test module.

- [ ] **Step 2: Run the test**

Run: `cargo test -p memory shell_output_does_not_change_the_reality_text`

Expected: PASS with no production change. `fold_shell` never reads `output`. If the test fails because the reality text includes the assertion, stop. That means the fold was changed and this plan must not ship that change.

- [ ] **Step 3: Leave `fold_shell` unchanged**

No edit when the test passes. The test is the lock.

---

### Task 3: Import the transcript into daemon events

**Files:**
- Create: `eval/component0/harness/import_trajectory.py`
- Create: `eval/component0/tests/test_import_trajectory.py`
- Test: `python -m pytest eval/component0/tests/test_import_trajectory.py -v`

The importer is the only new reader of `history.jsonl`. Each history unit becomes one event. Event ids are `h_<session>_<unit>` so a test can find the source unit without a second file.

Output is truncated to 200 lines and 8_000 characters, which is the bound `docs/component-2-research.md` already records for the transcript. A truncated output ends with the line `\n[output truncated]`.

`cwd` is an argument. The episode importer uses the cwd already stored on the matching command in `regressions/briefing-selection/baseline/replay/gen-1/events.jsonl`. Tests pass `cwd="/work"`.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `eval/component0`: `python -m pytest tests/test_import_trajectory.py -v`

Expected: FAIL with `ModuleNotFoundError: harness.import_trajectory`.

- [ ] **Step 3: Write the importer**

```python
"""Turn a saved transcript into the events a daemon log can hold.

This is the only reader of history.jsonl on the Component 2 path.
extract_claims reads the events this writes. It does not open the transcript.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MAX_OUTPUT_LINES = 200
MAX_OUTPUT_CHARS = 8_000
TRUNCATION_MARK = "[output truncated]"


def truncate_output(text: str) -> str:
    lines = text.splitlines()
    shortened = False
    if len(lines) > MAX_OUTPUT_LINES:
        lines = lines[:MAX_OUTPUT_LINES]
        shortened = True
    body = "\n".join(lines)
    if len(body) > MAX_OUTPUT_CHARS:
        body = body[:MAX_OUTPUT_CHARS]
        shortened = True
    if shortened:
        return f"{body}\n{TRUNCATION_MARK}"
    return text


def import_units(units: list[dict[str, Any]], *, cwd: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for index, unit in enumerate(units):
        session = str(unit.get("session") or "")
        unit_no = unit.get("unit", index)
        event_id = f"h_{session}_{unit_no}"
        timestamp_ms = (index + 1) * 1000
        kind = unit.get("kind")
        if kind == "note":
            events.append(
                {
                    "id": event_id,
                    "timestamp_ms": timestamp_ms,
                    "source": "note",
                    "payload": {"session": session, "text": str(unit.get("text") or "")},
                }
            )
        elif kind == "command":
            events.append(
                {
                    "id": event_id,
                    "timestamp_ms": timestamp_ms,
                    "source": "shell",
                    "payload": {
                        "command": str(unit.get("command") or ""),
                        "exit_code": unit.get("exit_code"),
                        "cwd": cwd,
                        "session": session,
                        "output": truncate_output(str(unit.get("output") or "")),
                    },
                }
            )
    return events


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def cwd_from_replay(replay_events: list[dict[str, Any]], command: str) -> str:
    wanted = " ".join(command.split())
    for event in replay_events:
        if event.get("source") != "shell":
            continue
        payload = event.get("payload") or {}
        if " ".join(str(payload.get("command", "")).split()) == wanted:
            return str(payload.get("cwd") or "")
    return ""


def import_episode(episode_dir: Path, replay_events: Path) -> list[dict[str, Any]]:
    history = load_jsonl(episode_dir / "history" / "history.jsonl")
    replay = load_jsonl(replay_events)
    failing = next(
        (
            unit
            for unit in history
            if unit.get("kind") == "command" and unit.get("exit_code") not in (0, None)
        ),
        None,
    )
    cwd = ""
    if failing is not None:
        cwd = cwd_from_replay(replay, str(failing["command"]))
    return import_units(history, cwd=cwd)
```

`import_episode` picks the first non-zero command only to copy that command's cwd onto every imported shell row. The episode has one cwd. Do not use it to choose the focal task. `focal_command` in the extractor still does that.

- [ ] **Step 4: Run the test**

Run from `eval/component0`: `python -m pytest tests/test_import_trajectory.py -v`

Expected: PASS.

---

### Task 4: The extractor reads events

**Files:**
- Modify: `eval/component0/harness/extract_claims.py`
- Modify: `eval/component0/tests/test_extract_claims.py`
- Test: `python -m pytest eval/component0/tests/test_extract_claims.py -v`

`extract(events)` replaces `extract(units, cwd=...)`. The task subject is `payload.cwd` plus the command, the same string `shell_subject` hashes. Evidence ids are event ids. A reality's support text is the preceding shell event's `payload.output`.

Session filter: a session is open when some shell event in that session has a latest exit code other than 0 or missing. Notes whose `payload.session` is not in that set are skipped with `session has no command that is still failing`.

Speech-act classification (`_drafts_for_note`, `_normative`, overlap, the 500-character cap, duplicate drop) stays. Change its input from a history unit to an event.

- [ ] **Step 1: Rewrite the test helpers and add the ban**

Replace `_note` and `_cmd` in `eval/component0/tests/test_extract_claims.py`:

```python
def _note(unit: int, text: str, session: str = "A") -> dict:
    return {
        "id": f"h_{session}_{unit}",
        "timestamp_ms": unit * 1000,
        "source": "note",
        "payload": {"session": session, "text": text},
    }


def _cmd(
    unit: int,
    command: str,
    output: str = "",
    code: int = 0,
    session: str = "A",
    cwd: str = "/work",
) -> dict:
    return {
        "id": f"h_{session}_{unit}",
        "timestamp_ms": unit * 1000,
        "source": "shell",
        "payload": {
            "command": command,
            "exit_code": code,
            "cwd": cwd,
            "session": session,
            "output": output,
        },
    }
```

Add this test:

```python
def test_extractor_does_not_open_the_transcript() -> None:
    text = EXTRACTOR.read_text()
    assert "history.jsonl" not in text
    assert "atoms.yaml" not in text
    assert "oracle.txt" not in text
    assert "gold/" not in text
```

Update every `extract(units, cwd="/work")` call to `extract(units)`.

Update `test_episode_claims_are_verbatim_spans_of_session_a` and `test_commands_alone_extract_nothing` and `test_focal_command_is_the_unsuperseded_pytest` to import the episode first:

```python
from harness.import_trajectory import import_episode

REPLAY = (
    EPISODE
    / "regressions"
    / "briefing-selection"
    / "baseline"
    / "replay"
    / "gen-1"
    / "events.jsonl"
)


def episode_events() -> list[dict]:
    return import_episode(EPISODE, REPLAY)
```

The episode test then checks evidence ids against the event list:

```python
def test_episode_claims_are_verbatim_spans_of_session_a() -> None:
    events = episode_events()
    by_id = {event["id"]: event for event in events}
    result = extract(events)
    assert result.claims
    for claim in result.claims:
        assert claim.text in claim.source_text
        assert claim.confidence != "observed"
        for ref in claim.evidence_ids:
            source = by_id[ref]
            if source["source"] == "note":
                assert claim.text in source["payload"]["text"]
            else:
                assert claim.kind == "reality"
                assert overlap(claim.text, source["payload"]["output"]) >= 2
```

`test_commands_alone_extract_nothing` filters `event["source"] == "shell"` and asserts `extract(commands).claims == []`.

`test_focal_command_is_the_unsuperseded_pytest` calls `focal_command(episode_events())` and reads `failing["payload"]["command"]`.

- [ ] **Step 2: Run the tests to verify they fail**

Run from `eval/component0`: `python -m pytest tests/test_extract_claims.py -v`

Expected: FAIL. `extract` still requires `cwd`, and the module still contains `history.jsonl`.

- [ ] **Step 3: Point `extract` at events**

In `eval/component0/harness/extract_claims.py`:

- Delete `load_jsonl`, `cwd_for_command`, and the `cwd` parameter of `extract`.
- `open_sessions(events)` walks events with `source == "shell"`. The key is `(payload["session"], collapse(payload["command"]))`. The latest `exit_code` wins.
- `focal_command(events)` returns the oldest shell event whose latest exit for that command is not 0 and not missing. Oldest means the first event in the list with that key.
- `task_subject` reads `payload["cwd"]` and `payload["command"]`.
- `_evidence` returns the event id, plus the support event id for a grounded reality.
- `_drafts_for_note` receives the note event, its text, and the previous event. `_is_source_read` is true when the previous event is `source == "shell"` and its command starts with `sed` or `grep`. Support text is `payload["output"]`.
- `extract(events)` skips any event whose source is not `note`.
- `write_packet` calls `import_episode` to build the candidate list, and passes the existing replay `events.jsonl` path to `project_claims` as the fold input. The fold input stays the 31 shell events. The candidates are built from the imported log, which has the same commands plus notes and output. `project_claims` attaches candidates to `task_subject`. That subject must use the cwd from the replay shell event, which `import_episode` copies.

Do not pass `history.jsonl` anywhere in this file. The string `history.jsonl` must not appear.

Keep the skip reasons stable: `open hypothesis; a later note has to close it before it is current`, `note follows a file read but is not grounded in that output`, `no load-bearing speech act`, `session has no command that is still failing`.

- [ ] **Step 4: Run the tests**

Run from `eval/component0`: `python -m pytest tests/test_extract_claims.py tests/test_import_trajectory.py -v`

Expected: PASS, including `test_component1_projector_admits_the_episode_and_hides_nothing_required` with `required_recall == 1` and rejected claims empty.

If that projector test fails because the task id changed, the imported `cwd` does not match the replay event. Fix `cwd_from_replay` so the subject string is the replay cwd plus the collapsed command. Do not edit `task_id_for_subject`.

---

### Task 5: A live session can record a note

**Files:**
- Modify: `bin/contextd/src/cli.rs`
- Modify: `bin/contextd/src/main.rs`
- Modify: `crates/sources/src/shell.rs` (test only)
- Test: `cargo test -p contextd-sources note_line_is_ingested`
- Test: the CLI parse test module in `bin/contextd/src/cli.rs`

`contextd note <TEXT>` writes one JSON line to the daemon socket. The line is an event, not a `ContextRequest`. The socket already accepts any `EventSource` except `Claim`.

```json
{"source":"note","payload":{"session":"live","text":"<TEXT>"}}
```

The daemon fills `id` and `timestamp_ms`. The note is not a claim. The extractor, run later on the exported log, is what may quote it.

- [ ] **Step 1: Write the failing ingest test**

In `crates/sources/src/shell.rs` tests:

```rust
#[test]
fn note_line_is_ingested() {
    let line = r#"{"source":"note","payload":{"session":"live","text":"Decision: idle connections count toward the cap."}}"#;
    match parse_inbound_line(line).unwrap() {
        InboundLine::Event(event) => {
            assert_eq!(event.source, contextd_core::event::EventSource::Note);
            assert_eq!(
                event.payload["text"],
                "Decision: idle connections count toward the cap."
            );
        }
        other => panic!("expected an event, got {other:?}"),
    }
}
```

- [ ] **Step 2: Run it**

Run: `cargo test -p contextd-sources note_line_is_ingested`

Expected: PASS once Task 1 has added `Note`. `parse_inbound_line` already deserializes `EventSource`. No change to the refuse-claim branch.

- [ ] **Step 3: Add the CLI command**

In `bin/contextd/src/cli.rs`:

- Add `note <TEXT>` to `USAGE` under `intent`. The help line is `note <TEXT>          Record a sentence for a later handoff. This does not file a claim.`
- Add `Note(String)` to `Command`.
- In `parse`, handle `"note"` the way `"intent"` is handled. Empty text returns `note needs a sentence, e.g. contextd note "Decision: keep the idle count"`.
- Add `run_note`. It connects to `config.socket_path` with `std::os::unix::net::UnixStream`, writes the JSON line plus `\n`, and shuts down the write half. On connection failure, print `contextd note: is the daemon running?` to stderr and return a non-zero exit. Match the error style of `run_intent`.

In `bin/contextd/src/main.rs`, dispatch `Command::Note(text)` to `cli::run_note`.

Add a parse test beside the existing CLI tests:

```rust
#[test]
fn note_command_keeps_the_sentence() {
    let args = ["note".into(), "Decision: idle connections count toward the cap.".into()];
    assert_eq!(
        parse(&args).unwrap(),
        Command::Note("Decision: idle connections count toward the cap.".into())
    );
}
```

- [ ] **Step 4: Run the tests**

Run: `cargo test -p contextd-sources note_line_is_ingested && cargo test -p contextd --bin contextd note_command_keeps_the_sentence`

Expected: PASS.

The binary package name is the one in `bin/contextd/Cargo.toml`. If `-p contextd` does not select it, use the package name printed by `cargo metadata --no-deps --format-version 1` for that manifest.

---

### Task 6: Docs match the new input

**Files:**
- Modify: `docs/component-2-design.md`
- Modify: `docs/connecting-agents.md`

- [ ] **Step 1: Replace the pipeline paragraph in `docs/component-2-design.md`**

The decision section currently says the handoff reads `history.jsonl`. Replace that sentence with:

> At a handoff, read the daemon event log. Notes are `source: note`. Command output is `payload.output` on the shell event. Quote a note only when it is a load-bearing speech act, or when it is a conclusion grounded in the immediately preceding shell output. Admit the quote through Component 1's existing claim validator and fold. Render with the existing `context_now` projector.

In the pipeline diagram, the first box is `event log (shell events with output, note events)`. Delete the box that names `history.jsonl` as an extractor input. State that `import_trajectory` is how this episode's saved transcript becomes that log, and that `extract_claims.py` does not open it.

In "Out of scope", leave the interactive shell hook where it is. Add one sentence: a later plan can attach `output` at the moment a command runs. This plan records output when the event is written by the importer or by a sender that already has the text.

- [ ] **Step 2: Add the command to `docs/connecting-agents.md`**

Add a row next to `contextd intent`:

| Command | What it stores |
|---|---|
| `contextd note <TEXT>` | A raw sentence on the event log (`source: note`). It is not a claim. A later handoff may quote it. |

Keep the existing `note_claim` row. That tool files a structured claim. `contextd note` does not.

---

## Plan self-review

- Notes reach the log as `EventSource::Note` (Task 1) and through `contextd note` (Task 5).
- Command output reaches the log as `payload.output` (Task 3). The fold does not absorb it into the observed reality (Task 2).
- The extractor's only input is the event list (Task 4). The string `history.jsonl` is banned in that file.
- The transcript still exists. One importer reads it. That is the fixture adapter, not the product path.
- Gold files stay out of the extractor and the gate.
- Interactive stdout capture is named as later work, because the current hook must not wrap the typed command.
- Required recall on the episode stays a Task 4 assertion (`required_recall == 1`), using the imported log.

## Execution note

Task 4 is the behavior change. Tasks 1–3 can land first and still leave the old extractor reading the transcript. Do not stop after Task 3 and call the gap closed.
