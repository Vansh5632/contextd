"""Normalized trial trace: one JSON object per line.

Line 1 is a header record (`kind: header`). Every later line is an event with a
0-based `i` and a `kind`. Tool calls (command, edit, other_tool) also carry a
1-based `t`; edit events produced by one tool call (same `item_id`) share `t`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Iterable, Union

SCHEMA_VERSION = 1
ACCESS_MODES = ("read", "search", "write", "exec", "meta")
TOOL_KINDS = ("command", "edit", "other_tool")
USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)


@dataclass
class Access:
    """A file touched by a shell command.

    `start`/`end` are 1-based inclusive lines. Both null means the whole file;
    `end` null with `start` set means through EOF; a negative `start` with null
    `end` means the last `-start` lines (tail).
    """

    path: str
    mode: str
    start: int | None = None
    end: int | None = None
    pattern: str | None = None
    fixed: bool = False
    ignore_case: bool = False


@dataclass
class Hunk:
    """Unified-diff hunk header. Starts are null for apply_patch envelope hunks,
    which carry context lines but no line numbers."""

    old_start: int | None = None
    old_len: int = 0
    new_start: int | None = None
    new_len: int = 0


@dataclass
class Header:
    arm: str | None = None
    rep: int | None = None
    model: str | None = None
    thread_id: str | None = None
    schema: int = SCHEMA_VERSION
    kind: str = "header"


@dataclass
class CommandEvent:
    command: str
    inner: str
    exit_code: int | None = None
    output_chars: int = 0
    status: str | None = None
    accesses: list[Access] = field(default_factory=list)
    unparsed: bool = False
    probe: str | None = None
    item_id: str | None = None
    i: int = -1
    t: int | None = None
    kind: str = "command"


@dataclass
class EditEvent:
    path: str
    change_kind: str
    patch: str | None = None
    patch_format: str | None = None
    hunks: list[Hunk] = field(default_factory=list)
    move_path: str | None = None
    status: str | None = None
    source: str = "file_change"
    item_id: str | None = None
    i: int = -1
    t: int | None = None
    kind: str = "edit"


@dataclass
class MessageEvent:
    text: str
    item_id: str | None = None
    i: int = -1
    kind: str = "message"


@dataclass
class ReasoningEvent:
    text: str
    item_id: str | None = None
    i: int = -1
    kind: str = "reasoning"


@dataclass
class OtherToolEvent:
    tool: str
    detail: str = ""
    status: str | None = None
    item_id: str | None = None
    i: int = -1
    t: int | None = None
    kind: str = "other_tool"


@dataclass
class UsageEvent:
    """Cumulative thread token usage at this point in the trial."""

    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    source: str = "exec"
    i: int = -1
    kind: str = "usage"


@dataclass
class ErrorEvent:
    message: str
    source: str = "error"
    i: int = -1
    kind: str = "error"


Event = Union[
    CommandEvent, EditEvent, MessageEvent, ReasoningEvent, OtherToolEvent, UsageEvent, ErrorEvent
]
EVENT_TYPES: dict[str, type] = {
    "command": CommandEvent,
    "edit": EditEvent,
    "message": MessageEvent,
    "reasoning": ReasoningEvent,
    "other_tool": OtherToolEvent,
    "usage": UsageEvent,
    "error": ErrorEvent,
}


@dataclass
class Trace:
    header: Header
    events: list[Event]

    def tool_events(self) -> list[Event]:
        return [e for e in self.events if e.kind in TOOL_KINDS]

    def tool_call_count(self) -> int:
        return len({e.t for e in self.tool_events()})


def number_events(events: list[Event]) -> list[Event]:
    """Assign `i` in order and `t` to tool calls; edits sharing an item_id share `t`."""
    t = 0
    last_item: str | None = None
    for i, event in enumerate(events):
        event.i = i
        if event.kind not in TOOL_KINDS:
            continue
        item_id = getattr(event, "item_id", None)
        if not (event.kind == "edit" and item_id is not None and item_id == last_item):
            t += 1
        event.t = t  # type: ignore[union-attr]
        last_item = item_id if event.kind == "edit" else None
    return events


def _prune(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _prune(v) for k, v in value.items() if v is not None and v is not False}
    if isinstance(value, list):
        return [_prune(v) for v in value]
    return value


def to_record(obj: Header | Event) -> dict[str, Any]:
    record = _prune(asdict(obj))
    if isinstance(obj, (CommandEvent, EditEvent)):
        record.setdefault("accesses" if isinstance(obj, CommandEvent) else "hunks", [])
    if isinstance(obj, CommandEvent):
        record["unparsed"] = obj.unparsed
    kind = record.pop("kind")
    ordered: dict[str, Any] = {}
    if "i" in record:
        ordered["i"] = record.pop("i")
    ordered["kind"] = kind
    if "t" in record:
        ordered["t"] = record.pop("t")
    ordered.update(record)
    return ordered


def _build(cls: type, record: dict[str, Any]) -> Any:
    names = {f.name for f in fields(cls)}
    kwargs = {k: v for k, v in record.items() if k in names}
    if cls is CommandEvent:
        kwargs["accesses"] = [Access(**a) for a in record.get("accesses", [])]
    if cls is EditEvent:
        kwargs["hunks"] = [Hunk(**h) for h in record.get("hunks", [])]
    return cls(**kwargs)


def from_record(record: dict[str, Any]) -> Header | Event:
    kind = record.get("kind")
    if kind == "header":
        return _build(Header, record)
    if kind not in EVENT_TYPES:
        raise ValueError(f"unknown event kind {kind!r}")
    return _build(EVENT_TYPES[kind], record)


def dumps_trace(trace: Trace) -> str:
    lines = [to_record(trace.header)] + [to_record(e) for e in trace.events]
    return "".join(json.dumps(r, sort_keys=False, ensure_ascii=False) + "\n" for r in lines)


def write_trace(trace: Trace, path: Path) -> None:
    path.write_text(dumps_trace(trace))


def parse_trace(lines: Iterable[str]) -> Trace:
    header = Header()
    events: list[Event] = []
    for line in lines:
        if not line.strip():
            continue
        obj = from_record(json.loads(line))
        if isinstance(obj, Header):
            header = obj
        else:
            events.append(obj)
    return Trace(header, number_events(events))


def read_trace(path: Path) -> Trace:
    return parse_trace(path.read_text().splitlines())
