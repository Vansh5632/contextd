"""Turn one observable trajectory into claim candidates.

The input is daemon events: notes (what was said) and shell events (what was
run, and the output that came back). Gold atoms and the oracle packet are
not inputs. A candidate is a verbatim span of one note. The projector gate
in `project_claims` is what admits it to the event log.

This module does not run during a command. A caller invokes it once, at a
handoff, on a finished trajectory. It does not open the transcript.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.common import COMPONENT_ROOT, EPISODES_DIR
from harness.import_trajectory import import_episode

REPO_ROOT = COMPONENT_ROOT.parents[1]
EVENTS_REL = Path(
    "regressions/briefing-selection/baseline/replay/gen-1/events.jsonl"
)
MIN_OVERLAP = 2
MAX_TEXT = 500
FRONTIER_SUBJECT = "frontier"

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_SOURCE_READ = re.compile(r"^(sed|grep)\b")
_STOP = frozenset(
    {
        "about",
        "after",
        "also",
        "and",
        "are",
        "because",
        "been",
        "before",
        "but",
        "for",
        "from",
        "have",
        "into",
        "only",
        "that",
        "than",
        "the",
        "their",
        "them",
        "then",
        "this",
        "with",
        "would",
    }
)


@dataclass
class Draft:
    claim_id: str
    kind: str
    origin: str
    confidence: str
    text: str
    subject: str | None
    evidence_ids: list[str]
    source_text: str
    support_text: str | None
    task_subject: str

    def as_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "claim_id": self.claim_id,
            "kind": self.kind,
            "origin": self.origin,
            "confidence": self.confidence,
            "text": self.text,
            "evidence_ids": list(self.evidence_ids),
            "source_text": self.source_text,
            "support_text": self.support_text,
            "task_subject": self.task_subject,
        }
        if self.subject is not None:
            body["subject"] = self.subject
        return body


@dataclass
class Extraction:
    claims: list[Draft] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "claims": [claim.as_dict() for claim in self.claims],
            "skipped": list(self.skipped),
        }


def collapse(value: str) -> str:
    return " ".join(value.split())


def content_tokens(text: str) -> set[str]:
    return {match.group(0).lower() for match in _TOKEN.finditer(text)} - _STOP


def overlap(claim: str, support: str) -> int:
    folded = support.lower()
    return sum(1 for token in content_tokens(claim) if token in folded)


def _shell_state(
    event: dict[str, Any],
) -> tuple[tuple[str, str], int | None] | None:
    if event.get("source") != "shell":
        return None
    payload = event.get("payload") or {}
    key = (
        str(payload.get("session") or ""),
        collapse(str(payload.get("command") or "")),
    )
    return key, payload.get("exit_code")


def open_sessions(events: list[dict[str, Any]]) -> list[str]:
    """Sessions whose latest run of some command is still non-zero."""
    latest: dict[tuple[str, str], int | None] = {}
    for event in events:
        state = _shell_state(event)
        if state is None:
            continue
        key, code = state
        latest[key] = code
    sessions: list[str] = []
    for (session, _command), code in latest.items():
        if code not in (0, None) and session not in sessions:
            sessions.append(session)
    return sessions


def focal_command(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Oldest shell event whose command is still failing."""
    latest: dict[tuple[str, str], int | None] = {}
    first: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        state = _shell_state(event)
        if state is None:
            continue
        key, code = state
        latest[key] = code
        first.setdefault(key, event)
    for event in events:
        state = _shell_state(event)
        if state is None:
            continue
        key, _code = state
        if latest[key] not in (0, None) and first[key] is event:
            return event
    return None


def task_subject(cwd: str, command: str) -> str:
    return f"{cwd.strip()}\n{collapse(command)}"


def _evidence(event: dict[str, Any], *extra: dict[str, Any]) -> list[str]:
    ids = [str(event["id"])]
    for item in extra:
        ref = str(item["id"])
        if ref not in ids:
            ids.append(ref)
    return ids


def _normative(sentence: str) -> bool:
    if re.search(
        r"\bno\s+(new|extra|additional)\s+(?:\w+\s+){0,3}dependenc",
        sentence,
        re.IGNORECASE,
    ):
        return True
    has_target = re.search(
        r"\b(signature|constructor|handle_request|handle_async_request)\b",
        sentence,
        re.IGNORECASE,
    )
    has_rule = re.search(
        r"\b(keep|unchanged|stay|stays|preserve)\b", sentence, re.IGNORECASE
    )
    return bool(has_target and has_rule)


def _draft(
    event: dict[str, Any],
    *,
    suffix: str,
    kind: str,
    origin: str,
    confidence: str,
    text: str,
    source_text: str,
    task: str,
    support: str | None = None,
    extra_evidence: tuple[dict[str, Any], ...] = (),
    subject: str | None = None,
) -> Draft:
    return Draft(
        claim_id=f"{event['id']}{suffix}",
        kind=kind,
        origin=origin,
        confidence=confidence,
        text=text.strip(),
        subject=subject,
        evidence_ids=_evidence(event, *extra_evidence),
        source_text=source_text,
        support_text=support,
        task_subject=task,
    )


def extract(events: list[dict[str, Any]]) -> Extraction:
    """Quote load-bearing notes from sessions that still have a failing command."""
    result = Extraction()
    failing = focal_command(events)
    if failing is None:
        result.skipped.append(
            {"unit": None, "reason": "no command is still failing"}
        )
        return result
    payload = failing.get("payload") or {}
    task = task_subject(str(payload.get("cwd") or ""), str(payload.get("command") or ""))
    sessions = set(open_sessions(events))
    seen: set[tuple[str, str]] = set()

    for index, event in enumerate(events):
        if event.get("source") != "note":
            continue
        note = event.get("payload") or {}
        if str(note.get("session") or "") not in sessions:
            result.skipped.append(
                {
                    "unit": event["id"],
                    "reason": "session has no command that is still failing",
                }
            )
            continue
        text = str(note.get("text") or "").strip()
        if not text:
            result.skipped.append({"unit": event["id"], "reason": "empty note"})
            continue
        previous = events[index - 1] if index else None
        drafts = _drafts_for_note(event, text, previous, task)
        if not drafts:
            result.skipped.append(
                {"unit": event["id"], "reason": _skip_reason(text, previous)}
            )
            continue
        for draft in drafts:
            if draft.text not in draft.source_text:
                result.skipped.append(
                    {
                        "unit": event["id"],
                        "reason": "candidate is not a verbatim span of the note",
                    }
                )
                continue
            if not (1 <= len(draft.text) <= MAX_TEXT):
                result.skipped.append(
                    {
                        "unit": event["id"],
                        "reason": "candidate text is empty or longer than 500 characters",
                    }
                )
                continue
            if draft.kind == "reality":
                support = draft.support_text or ""
                if overlap(draft.text, support) < MIN_OVERLAP:
                    result.skipped.append(
                        {
                            "unit": event["id"],
                            "reason": "inference is not grounded in the preceding tool output",
                        }
                    )
                    continue
            key = (draft.kind, collapse(draft.text).casefold())
            if key in seen:
                result.skipped.append(
                    {
                        "unit": event["id"],
                        "reason": "duplicate of a claim already extracted",
                    }
                )
                continue
            seen.add(key)
            result.claims.append(draft)
    return result


def _skip_reason(text: str, previous: dict[str, Any] | None) -> str:
    if re.match(r"(?i)^(first suspect|next suspect|idea)\b", text):
        return "open hypothesis; a later note has to close it before it is current"
    if _is_source_read(previous):
        return "note follows a file read but is not grounded in that output"
    return "no load-bearing speech act"


def _is_source_read(event: dict[str, Any] | None) -> bool:
    if not event or event.get("source") != "shell":
        return False
    payload = event.get("payload") or {}
    return bool(_SOURCE_READ.match(collapse(str(payload.get("command") or ""))))


def _drafts_for_note(
    note: dict[str, Any],
    text: str,
    previous: dict[str, Any] | None,
    task: str,
) -> list[Draft]:
    if re.search(r"(?i)\bconstraint\b", text):
        drafts = []
        for index, sentence in enumerate(_SENTENCE.split(text)):
            sentence = sentence.strip()
            if sentence and _normative(sentence):
                drafts.append(
                    _draft(
                        note,
                        suffix="" if index == 0 else chr(ord("b") + index - 1),
                        kind="constraint",
                        origin="user",
                        confidence="stated",
                        text=sentence,
                        source_text=text,
                        task=task,
                    )
                )
        return drafts

    if re.match(r"(?i)^decision\b", text):
        return [
            _draft(
                note,
                suffix="",
                kind="decision",
                origin="agent",
                confidence="inferred",
                text=text,
                source_text=text,
                task=task,
            )
        ]

    if re.search(r"(?i)\b(ruled out|rejected)\b", text):
        return [
            _draft(
                note,
                suffix="",
                kind="ruled_out",
                origin="agent",
                confidence="inferred",
                text=text,
                source_text=text,
                task=task,
            )
        ]

    if re.match(r"(?i)^next step\b", text):
        return [
            _draft(
                note,
                suffix="",
                kind="next_action",
                origin="agent",
                confidence="inferred",
                text=text,
                source_text=text,
                task=task,
                subject=FRONTIER_SUBJECT,
            )
        ]

    if re.search(r"(?i)\bout of scope\b", text):
        return [
            _draft(
                note,
                suffix="",
                kind="unknown",
                origin="agent",
                confidence="hypothesis",
                text=text,
                source_text=text,
                task=task,
            )
        ]

    if re.match(r"(?i)^(first suspect|next suspect|idea)\b", text):
        return []

    if _is_source_read(previous):
        support = str((previous.get("payload") or {}).get("output") or "")
        if overlap(text, support) >= MIN_OVERLAP:
            return [
                _draft(
                    note,
                    suffix="",
                    kind="reality",
                    origin="agent",
                    confidence="inferred",
                    text=text,
                    source_text=text,
                    task=task,
                    support=support,
                    extra_evidence=(previous,),
                )
            ]
    return []


def write_packet(
    episode_dir: Path,
    *,
    repo_root: Path = REPO_ROOT,
    cargo_bin: list[str] | None = None,
) -> dict[str, Any]:
    """Extract, admit through the Component 1 projector, and return the report."""
    episode_dir = episode_dir.resolve()
    events_path = episode_dir / EVENTS_REL
    events = import_episode(episode_dir, events_path)
    if focal_command(events) is None:
        raise SystemExit("no failing command in the history")
    extraction = extract(events)
    claims_path = episode_dir / "packets" / "contextd_component2_claims.json"
    report_path = episode_dir / "packets" / "contextd_component2_report.json"
    packet_path = episode_dir / "packets" / "contextd_component2.txt"
    claims_path.parent.mkdir(exist_ok=True)
    claims_path.write_text(json.dumps(extraction.as_dict(), indent=2) + "\n")
    command = cargo_bin or [
        "cargo",
        "run",
        "-q",
        "-p",
        "broker",
        "--bin",
        "project_claims",
        "--",
    ]
    result = subprocess.run(
        [*command, str(events_path), str(claims_path), str(packet_path), str(report_path)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            "project_claims failed\n" + result.stderr + result.stdout
        )
    return json.loads(report_path.read_text())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode", default="limiter-handoff")
    args = parser.parse_args(argv)
    report = write_packet(EPISODES_DIR / args.episode)
    accepted = len(report["accepted"])
    rejected = len(report["rejected"])
    print(
        f"accepted {accepted} claims, rejected {rejected}, "
        f"packet tokens (daemon estimate) {report['snapshot']['estimated_tokens']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
