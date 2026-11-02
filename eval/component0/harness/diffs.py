"""Diff parsing shared by the normalizer and the scorer.

Two hunk flavours are handled: unified hunks (`@@ -a,b +c,d @@`) and apply_patch
envelope hunks (`@@` optionally followed by a context hint, no line numbers).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
BEGIN_PATCH = "*** Begin Patch"
END_PATCH = "*** End Patch"


@dataclass
class PatchHunk:
    old_start: int | None
    new_start: int | None
    lines: list[tuple[str, str]] = field(default_factory=list)

    @property
    def old_lines(self) -> list[str]:
        return [text for tag, text in self.lines if tag in (" ", "-")]

    @property
    def new_lines(self) -> list[str]:
        return [text for tag, text in self.lines if tag in (" ", "+")]

    def changed_spans(self, base: int) -> list[tuple[int, int]]:
        """Old-side lines this hunk changes, as inclusive spans, given the old line
        number of its first line. A pure insertion before old line p is (p, p-1)."""
        spans: list[tuple[int, int]] = []
        line = base
        prev = ""
        for tag, _ in self.lines:
            if tag == " ":
                line += 1
            elif tag == "-":
                spans.append((line, line))
                line += 1
            elif tag == "+" and prev not in ("-", "+"):
                spans.append((line, line - 1))
            prev = tag
        return spans


def span_touches(span: tuple[int, int], region: tuple[int, int]) -> bool:
    """A removed span overlaps the region; an insertion before old line p touches
    it when it lands after the region's first line and no later than just past its end."""
    a, b = span
    s, e = region
    if a <= b:
        return a <= e and b >= s
    return s < a <= e + 1


@dataclass
class FileDiff:
    old_path: str | None
    new_path: str | None
    kind: str
    hunks: list[PatchHunk] = field(default_factory=list)
    binary: bool = False

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""

    @property
    def added_text(self) -> str:
        return "\n".join(t for h in self.hunks for tag, t in h.lines if tag == "+")


def _tag(line: str) -> tuple[str, str] | None:
    if line == "":
        return (" ", "")
    if line[0] in " +-":
        return (line[0], line[1:])
    return None


def parse_unified_hunks(text: str) -> list[PatchHunk]:
    hunks: list[PatchHunk] = []
    current: PatchHunk | None = None
    old_left = new_left = 0
    for line in text.splitlines():
        if current is not None and (old_left > 0 or new_left > 0):
            if line.startswith("\\"):
                continue
            tagged = _tag(line)
            if tagged is None:
                current = None
                continue
            current.lines.append(tagged)
            if tagged[0] in (" ", "-"):
                old_left -= 1
            if tagged[0] in (" ", "+"):
                new_left -= 1
            continue
        match = HUNK_HEADER.match(line)
        if match:
            current = PatchHunk(int(match.group(1)), int(match.group(3)))
            old_left = int(match.group(2)) if match.group(2) is not None else 1
            new_left = int(match.group(4)) if match.group(4) is not None else 1
            hunks.append(current)
    return hunks


def parse_envelope_hunks(text: str) -> list[PatchHunk]:
    hunks: list[PatchHunk] = []
    current: PatchHunk | None = None
    for line in text.splitlines():
        if line.startswith("@@"):
            current = PatchHunk(None, None)
            hunks.append(current)
            continue
        if line.startswith("*** "):
            continue
        tagged = _tag(line)
        if tagged is None:
            continue
        if current is None:
            current = PatchHunk(None, None)
            hunks.append(current)
        current.lines.append(tagged)
    return [h for h in hunks if h.lines]


def parse_hunks(text: str, fmt: str | None) -> list[PatchHunk]:
    return parse_envelope_hunks(text) if fmt == "envelope" else parse_unified_hunks(text)


def hunk_header_counts(hunk: PatchHunk) -> tuple[int, int]:
    return len(hunk.old_lines), len(hunk.new_lines)


@dataclass
class EnvelopeFile:
    path: str
    kind: str
    body: str
    move_path: str | None = None


def find_envelope(text: str) -> str | None:
    start = text.find(BEGIN_PATCH)
    if start < 0:
        return None
    end = text.find(END_PATCH, start)
    if end < 0:
        return None
    return text[start : end + len(END_PATCH)]


def parse_envelope(envelope: str) -> list[EnvelopeFile]:
    files: list[EnvelopeFile] = []
    current: EnvelopeFile | None = None
    body: list[str] = []

    def flush() -> None:
        if current is not None:
            current.body = "\n".join(body) + ("\n" if body else "")
            files.append(current)

    for line in envelope.splitlines():
        if line.strip() in (BEGIN_PATCH, END_PATCH):
            continue
        header = re.match(r"^\*\*\* (Add|Delete|Update) File: (.+)$", line)
        if header:
            flush()
            kind = {"Add": "add", "Delete": "delete", "Update": "update"}[header.group(1)]
            current = EnvelopeFile(header.group(2).strip(), kind, "")
            body = []
            continue
        move = re.match(r"^\*\*\* Move to: (.+)$", line)
        if move and current is not None:
            current.move_path = move.group(1).strip()
            continue
        if current is not None:
            body.append(line)
    flush()
    return files


def content_patch(content: str, kind: str) -> str:
    lines = content.splitlines()
    n = len(lines)
    if kind == "add":
        return f"@@ -0,0 +1,{n} @@\n" + "".join(f"+{line}\n" for line in lines)
    return f"@@ -1,{n} +0,0 @@\n" + "".join(f"-{line}\n" for line in lines)


def parse_git_diff(text: str) -> list[FileDiff]:
    """Parse `git diff` output (multiple files, with new/deleted/rename headers)."""
    files: list[FileDiff] = []
    chunks = re.split(r"(?m)^(?=diff --git )", text)
    for chunk in chunks:
        if not chunk.startswith("diff --git "):
            continue
        first = chunk.splitlines()[0]
        match = re.match(r"^diff --git a/(.+?) b/(.+)$", first)
        if not match:
            continue
        old_path, new_path = match.group(1), match.group(2)
        kind = "update"
        binary = False
        for line in chunk.splitlines()[1:]:
            if line.startswith("@@"):
                break
            if line.startswith("new file mode"):
                kind = "add"
            elif line.startswith("deleted file mode"):
                kind = "delete"
            elif line.startswith("rename from "):
                kind = "rename"
                old_path = line[len("rename from ") :]
            elif line.startswith("rename to "):
                new_path = line[len("rename to ") :]
            elif line.startswith("Binary files") or line.startswith("GIT binary patch"):
                binary = True
        files.append(
            FileDiff(
                None if kind == "add" else old_path,
                None if kind == "delete" else new_path,
                kind,
                parse_unified_hunks(chunk),
                binary,
            )
        )
    return files


def locate(lines: list[str], block: list[str], hint: int | None) -> int | None:
    """1-based start of `block` in `lines`, preferring the match nearest `hint`."""
    if not block:
        return hint if hint is not None else None
    n = len(block)
    matches = [i + 1 for i in range(len(lines) - n + 1) if lines[i : i + n] == block]
    if not matches:
        return None
    if hint is None:
        return matches[0] if len(matches) == 1 else None
    return min(matches, key=lambda m: (abs(m - hint), m))


def apply_hunk(lines: list[str], hunk: PatchHunk, start: int) -> list[str]:
    old = hunk.old_lines
    return lines[: start - 1] + hunk.new_lines + lines[start - 1 + len(old) :]
