"""Normalize a Codex `exec --json` stream (plus optional rollout) into a trace.

Usage (from eval/component0):
    python -m harness.normalize --exec events.jsonl [--rollout rollout.jsonl] --out trace.jsonl
        [--arm ARM] [--rep N] [--model MODEL] [--workspace-root /workspace]

File accesses are parsed from shell commands with a fixed rule set. Commands the
rules do not cover are flagged `unparsed`; accesses are never guessed.
"""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.diffs import (
    content_patch,
    find_envelope,
    hunk_header_counts,
    parse_envelope,
    parse_envelope_hunks,
    parse_unified_hunks,
)
from harness.schema import (
    USAGE_FIELDS,
    Access,
    CommandEvent,
    EditEvent,
    ErrorEvent,
    Event,
    Header,
    Hunk,
    MessageEvent,
    OtherToolEvent,
    ReasoningEvent,
    Trace,
    UsageEvent,
    number_events,
    write_trace,
)

SHELLS = {"bash", "sh", "zsh", "dash"}
SEPARATORS = {"&&", "||", ";", "&", "\n", ";;"}
PIPES = {"|", "|&"}
KEYWORDS = {"for", "while", "until", "if", "then", "else", "elif", "fi", "do", "done", "case",
            "esac", "function", "select"}  # fmt: skip
WRAPPERS_NO_ARG = {"time", "nice", "sudo", "command", "exec", "nohup"}
NOOPS = {"echo", "printf", "true", "false", "pwd", "export", "set", "unset", "which", "type",
         "sleep", "date", "whoami", "clear", "exit", "env", "id", "uname", "hash", "alias",
         "chmod", "ln"}  # fmt: skip
READERS = {"cat", "nl", "less", "more", "bat", "batcat"}
META = {"ls", "tree", "stat", "wc", "file", "du"}
DEV_PATHS = {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty", "-"}
PYTHONS = re.compile(r"^python(3(\.\d+)?)?$")
HEREDOC_TOKEN = re.compile(r"^__HEREDOC_(\d+)__$")
GIT_READONLY = {"status", "log", "branch", "rev-parse", "remote", "describe", "shortlog",
                "config", "reflog", "cat-file", "ls-files", "ls-tree", "rev-list", "version",
                "help", "whatchanged"}  # fmt: skip


@dataclass
class ParsedCommand:
    accesses: list[Access] = field(default_factory=list)
    unparsed: bool = False
    probe: str | None = None


# ---------------------------------------------------------------------------
# Shell text handling


def extract_inner(command: str) -> str:
    """The script inside `bash -lc '...'` / `/bin/bash -c ...` wrappers (repeatedly)."""
    current = command
    for _ in range(3):
        try:
            argv = shlex.split(current)
        except ValueError:
            return current
        if len(argv) < 3 or posixpath.basename(argv[0]) not in SHELLS:
            return current
        idx = 1
        while idx < len(argv) and argv[idx].startswith("-") and "c" not in argv[idx][1:]:
            idx += 1
        if idx >= len(argv) - 1 or not re.fullmatch(r"-[a-z]*c[a-z]*", argv[idx]):
            return current
        current = argv[idx + 1]
    return current


@dataclass
class Preprocessed:
    script: str
    heredocs: list[str]
    ok: bool
    substitution: bool


def preprocess(script: str) -> Preprocessed:
    """Quote-aware pass: pull heredoc bodies out (replaced by placeholder words), drop
    file-descriptor digits in front of redirections, and flag command substitution."""
    out: list[str] = []
    heredocs: list[str] = []
    pending: list[tuple[int, str, bool]] = []
    substitution = False
    ok = True
    i, n = 0, len(script)
    quote: str | None = None
    at_word_start = True
    while i < n:
        ch = script[i]
        if quote == "'":
            out.append(ch)
            if ch == "'":
                quote = None
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            out.append(script[i : i + 2])
            i += 2
            at_word_start = False
            continue
        if quote == '"':
            if ch == '"':
                quote = None
            elif ch == "`" or script.startswith("$(", i):
                substitution = True
            out.append(ch)
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            i += 1
            at_word_start = False
            continue
        if ch == "`" or any(script.startswith(op, i) for op in ("$(", "<(", ">(")):
            substitution = True
        if script.startswith("<<", i) and not script.startswith("<<<", i):
            match = re.match(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2", script[i:])
            if match:
                index = len(heredocs) + len(pending)
                pending.append((index, match.group(3), match.group(1) == "-"))
                out.append(f" __HEREDOC_{index}__ ")
                i += match.end()
                at_word_start = True
                continue
        if ch == "\n" and pending:
            out.append("\n")
            i += 1
            for _, delim, strip_tabs in pending:
                body: list[str] = []
                found = False
                while i < n:
                    end = script.find("\n", i)
                    line = script[i:] if end < 0 else script[i:end]
                    i = n if end < 0 else end + 1
                    check = line.lstrip("\t") if strip_tabs else line
                    if check == delim:
                        found = True
                        break
                    body.append(check)
                if not found:
                    ok = False
                heredocs.append("\n".join(body) + ("\n" if body else ""))
            pending = []
            at_word_start = True
            continue
        if at_word_start and ch.isdigit():
            j = i
            while j < n and script[j].isdigit():
                j += 1
            if j < n and script[j] in "<>":
                i = j
                continue
        at_word_start = ch in " \t\n;&|()"
        out.append(ch)
        i += 1
    if pending:
        ok = False
    if quote is not None:
        ok = False
    return Preprocessed("".join(out), heredocs, ok, substitution)


def tokenize(script: str) -> list[str]:
    lexer = shlex.shlex(script, posix=True, punctuation_chars=";&|<>()\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    tokens = []
    for token in lexer:
        if token.startswith("#") and (not tokens or tokens[-1] in SEPARATORS):
            continue
        tokens.append(token)
    return _merge_newlines(tokens)


def _merge_newlines(tokens: list[str]) -> list[str]:
    merged: list[str] = []
    for token in tokens:
        if set(token) == {"\n"}:
            token = "\n"
        elif "\n" in token and set(token) <= set(";&|\n"):
            parts = [p for p in re.split(r"(\n)", token) if p]
            merged.extend(parts)
            continue
        merged.append(token)
    return merged


def is_redirect(token: str) -> bool:
    return bool(token) and set(token) <= set("<>&") and ("<" in token or ">" in token)


def split_pipelines(tokens: list[str]) -> list[list[list[str]]]:
    pipelines: list[list[list[str]]] = []
    pipeline: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in SEPARATORS:
            if current:
                pipeline.append(current)
            if pipeline:
                pipelines.append(pipeline)
            pipeline, current = [], []
        elif token in PIPES:
            if current:
                pipeline.append(current)
            current = []
        elif token in ("(", ")", "{", "}"):
            continue
        else:
            current.append(token)
    if current:
        pipeline.append(current)
    if pipeline:
        pipelines.append(pipeline)
    return pipelines


# ---------------------------------------------------------------------------
# Paths


@dataclass
class PathContext:
    root: str = "/workspace"
    cwd: str | None = "."

    def resolve(self, path: str) -> str | None:
        if not path or path in DEV_PATHS:
            return None
        if path.startswith("~") or "$" in path:
            return None
        root = self.root.rstrip("/")
        if path.startswith("/"):
            norm = posixpath.normpath(path)
        else:
            if self.cwd is None:
                return None
            base = self.cwd if self.cwd.startswith("/") else posixpath.join(root, self.cwd)
            norm = posixpath.normpath(posixpath.join(base, path))
        if norm == root:
            return "."
        if norm.startswith(root + "/"):
            return norm[len(root) + 1 :]
        return norm

    def chdir(self, path: str | None) -> None:
        if path is None:
            self.cwd = None
            return
        resolved = self.resolve(path)
        self.cwd = resolved


# ---------------------------------------------------------------------------
# Option parsing


def split_options(
    args: list[str], short_values: str = "", long_values: frozenset[str] = frozenset()
) -> tuple[list[tuple[str, str | None]], list[str]]:
    """getopt-style split into (options, operands); options keep their value."""
    options: list[tuple[str, str | None]] = []
    operands: list[str] = []
    idx = 0
    while idx < len(args):
        arg = args[idx]
        if arg == "--":
            operands.extend(args[idx + 1 :])
            break
        if arg.startswith("--"):
            name, eq, value = arg.partition("=")
            if eq:
                options.append((name, value))
            elif name in long_values and idx + 1 < len(args):
                options.append((name, args[idx + 1]))
                idx += 1
            else:
                options.append((name, None))
        elif arg.startswith("-") and len(arg) > 1 and not re.fullmatch(r"-\d+", arg):
            chars = arg[1:]
            for pos, ch in enumerate(chars):
                if ch in short_values:
                    rest = chars[pos + 1 :]
                    if rest:
                        options.append((f"-{ch}", rest))
                    elif idx + 1 < len(args):
                        options.append((f"-{ch}", args[idx + 1]))
                        idx += 1
                    else:
                        options.append((f"-{ch}", None))
                    break
                options.append((f"-{ch}", None))
        elif re.fullmatch(r"-\d+", arg):
            options.append(("-n", arg[1:]))
        else:
            operands.append(arg)
        idx += 1
    return options, operands


def has(options: list[tuple[str, str | None]], *names: str) -> bool:
    return any(name in names for name, _ in options)


def values(options: list[tuple[str, str | None]], *names: str) -> list[str]:
    return [v for name, v in options if name in names and v is not None]


# ---------------------------------------------------------------------------
# Line windows (sed/awk/head/tail)

SED_RANGE = re.compile(r"^(\d+|\$)(?:\s*,\s*(\d+|\$|\+\d+))?\s*p$")


def sed_ranges(script: str) -> list[tuple[int, int | None]] | None:
    ranges: list[tuple[int, int | None]] = []
    for part in re.split(r"[;\n]", script):
        part = part.strip()
        if not part:
            continue
        match = SED_RANGE.match(part)
        if not match:
            return None
        first, second = match.group(1), match.group(2)
        if first == "$":
            if second is not None:
                return None
            ranges.append((-1, None))
            continue
        start = int(first)
        if second is None:
            ranges.append((start, start))
        elif second == "$":
            ranges.append((start, None))
        elif second.startswith("+"):
            ranges.append((start, start + int(second[1:])))
        else:
            ranges.append((start, max(start, int(second))))
    return ranges or None


AWK_PATTERNS = (
    re.compile(r"^NR(>=|>)(\d+)&&NR(<=|<)(\d+)(\{.*\})?;?$"),
    re.compile(r"^NR==(\d+),NR==(\d+)(\{.*\})?;?$"),
    re.compile(r"^NR==(\d+)(\{.*\})?;?$"),
)


def awk_range(program: str) -> tuple[int, int] | None:
    compact = re.sub(r"\s+", "", program)
    match = AWK_PATTERNS[0].match(compact)
    if match:
        low = int(match.group(2)) + (1 if match.group(1) == ">" else 0)
        high = int(match.group(4)) - (1 if match.group(3) == "<" else 0)
        return (low, high) if low <= high else None
    match = AWK_PATTERNS[1].match(compact)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = AWK_PATTERNS[2].match(compact)
    if match:
        return int(match.group(1)), int(match.group(1))
    return None


def head_count(options: list[tuple[str, str | None]]) -> int | None:
    raw = values(options, "-n", "--lines")
    if not raw:
        return 10
    value = raw[-1]
    return int(value) if value.isdigit() else None


@dataclass
class Filter:
    kind: str
    count: int | None = None
    from_start: int | None = None
    ranges: list[tuple[int, int | None]] | None = None
    pattern: str | None = None
    fixed: bool = False
    ignore_case: bool = False


def narrow(access: Access, flt: Filter) -> list[Access] | None:
    """Apply a downstream line filter to a single-file read. None means unchanged."""
    s, e = access.start, access.end
    whole = s is None and e is None
    known = s is not None and s > 0
    if flt.kind == "identity":
        return None
    if flt.kind == "meta":
        return [Access(access.path, "meta")]
    if flt.kind == "search":
        return [
            Access(access.path, "search", pattern=flt.pattern, fixed=flt.fixed,
                   ignore_case=flt.ignore_case)
        ]  # fmt: skip
    if flt.kind == "head" and flt.count is not None:
        if whole:
            return [Access(access.path, "read", 1, flt.count)]
        if known:
            end = s + flt.count - 1
            return [Access(access.path, "read", s, end if e is None else min(e, end))]
    if flt.kind == "tail" and flt.count is not None:
        if whole:
            return [Access(access.path, "read", -flt.count, None)]
        if known and e is not None:
            return [Access(access.path, "read", max(s, e - flt.count + 1), e)]
    if flt.kind == "tail_from" and flt.from_start is not None:
        if whole:
            return [Access(access.path, "read", flt.from_start, None)]
        if known:
            return [Access(access.path, "read", s + flt.from_start - 1, e)]
    if flt.kind == "ranges" and flt.ranges:
        if whole:
            return [Access(access.path, "read", a, b) for a, b in flt.ranges]
        if known and all(a > 0 for a, _ in flt.ranges):
            out = []
            for a, b in flt.ranges:
                start = s + a - 1
                end = None if b is None else s + b - 1
                if e is not None:
                    end = e if end is None else min(end, e)
                if end is None or start <= end:
                    out.append(Access(access.path, "read", start, end))
            return out
    return None


# ---------------------------------------------------------------------------
# Per-command rules

GREP_SHORT = "efABCmdD"
GREP_LONG = frozenset({"--regexp", "--file", "--after-context", "--before-context", "--context",
                       "--max-count", "--include", "--exclude", "--exclude-dir", "--label",
                       "--devices", "--directories", "--binary-files"})  # fmt: skip
RG_SHORT = "efgtTABCmMjrEd"
RG_LONG = frozenset({"--regexp", "--file", "--glob", "--iglob", "--type", "--type-not",
                     "--max-count", "--max-columns", "--context", "--after-context",
                     "--before-context", "--replace", "--threads", "--sort", "--sortr",
                     "--encoding", "--max-depth", "--type-add", "--type-clear", "--color",
                     "--colors", "--max-filesize", "--pre", "--pre-glob", "--context-separator",
                     "--field-match-separator", "--path-separator", "--engine", "--ignore-file",
                     "--dfa-size-limit", "--regex-size-limit"})  # fmt: skip
PYTEST_SHORT = "kmpocWnr"
PYTEST_LONG = frozenset({"--junitxml", "--rootdir", "--ignore", "--deselect", "--tb", "--maxfail",
                         "--durations", "--override-ini", "--basetemp", "--log-level",
                         "--timeout", "--confcutdir", "--ignore-glob", "--cov",
                         "--import-mode"})  # fmt: skip


@dataclass
class CommandResult:
    accesses: list[Access] = field(default_factory=list)
    recognized: bool = True
    filter: Filter | None = None
    probe: str | None = None


def search_accesses(
    ctx: PathContext,
    options: list[tuple[str, str | None]],
    operands: list[str],
    default_path: str | None,
    tool: str,
) -> CommandResult:
    patterns = values(options, "-e", "--regexp")
    if not patterns:
        if not operands:
            return CommandResult(recognized=False)
        patterns, operands = [operands[0]], operands[1:]
    pattern = patterns[0] if len(patterns) == 1 else "|".join(f"(?:{p})" for p in patterns)
    fixed = has(options, "-F", "--fixed-strings")
    ignore_case = has(options, "-i", "--ignore-case")
    if tool == "rg" and has(options, "-S", "--smart-case") and pattern == pattern.lower():
        ignore_case = True
    paths = operands or ([default_path] if default_path else [])
    if not paths:
        return CommandResult(filter=Filter("search", pattern=pattern, fixed=fixed,
                                           ignore_case=ignore_case))  # fmt: skip
    accesses = []
    for raw in paths:
        path = ctx.resolve(raw)
        if path is not None:
            accesses.append(Access(path, "search", pattern=pattern, fixed=fixed,
                                   ignore_case=ignore_case))  # fmt: skip
    return CommandResult(accesses)


def rule_grep(argv: list[str], ctx: PathContext, piped: bool) -> CommandResult:
    options, operands = split_options(argv[1:], GREP_SHORT, GREP_LONG)
    recursive = has(options, "-r", "-R", "--recursive", "--dereference-recursive")
    default = "." if recursive and not piped else None
    return search_accesses(ctx, options, operands, default, "grep")


def rule_rg(argv: list[str], ctx: PathContext, piped: bool) -> CommandResult:
    options, operands = split_options(argv[1:], RG_SHORT, RG_LONG)
    if has(options, "--files"):
        paths = operands or ["."]
        return CommandResult([Access(p, "meta") for p in map(ctx.resolve, paths) if p])
    return search_accesses(ctx, options, operands, None if piped else ".", "rg")


def rule_reader(argv: list[str], ctx: PathContext) -> CommandResult:
    short = "bnwsvil" if argv[0] == "nl" else ""
    options, operands = split_options(argv[1:], short)
    if not operands:
        return CommandResult(filter=Filter("identity"))
    return CommandResult([Access(p, "read") for p in map(ctx.resolve, operands) if p])


def rule_head_tail(argv: list[str], ctx: PathContext) -> CommandResult:
    options, operands = split_options(argv[1:], "nc", frozenset({"--lines", "--bytes"}))
    tail = argv[0] == "tail"
    by_bytes = has(options, "-c", "--bytes")
    raw = values(options, "-n", "--lines")
    flt: Filter
    if by_bytes:
        flt = Filter("identity")
    elif tail and raw and raw[-1].startswith("+") and raw[-1][1:].isdigit():
        flt = Filter("tail_from", from_start=int(raw[-1][1:]))
    else:
        count = head_count(options)
        if count is None:
            return CommandResult(recognized=False)
        flt = Filter("tail" if tail else "head", count=count)
    if not operands:
        return CommandResult(filter=flt)
    accesses = []
    for path in map(ctx.resolve, operands):
        if path is None:
            continue
        narrowed = narrow(Access(path, "read"), flt)
        accesses.extend(narrowed or [Access(path, "read")])
    return CommandResult(accesses)


def rule_sed(argv: list[str], ctx: PathContext) -> CommandResult:
    args = argv[1:]
    in_place = any(a == "-i" or (a.startswith("-i") and not a.startswith("--")) or
                   a.startswith("--in-place") for a in args)  # fmt: skip
    args = [a for a in args if not (a.startswith("-i") or a.startswith("--in-place"))]
    options, operands = split_options(args, "efl", frozenset({"--expression", "--file"}))
    scripts = values(options, "-e", "--expression")
    if has(options, "-f", "--file"):
        return CommandResult(recognized=False)
    if not scripts:
        if not operands:
            return CommandResult(recognized=False)
        scripts, operands = [operands[0]], operands[1:]
    paths = [p for p in map(ctx.resolve, operands) if p]
    if in_place:
        return CommandResult([Access(p, "write") for p in paths], recognized=bool(operands))
    quiet = has(options, "-n", "--quiet", "--silent")
    if quiet:
        ranges = sed_ranges(";".join(scripts))
        if ranges is None:
            return CommandResult(recognized=False)
        flt = Filter("ranges", ranges=ranges)
    else:
        match = re.fullmatch(r"(\d+)q", ";".join(scripts).strip())
        flt = Filter("head", count=int(match.group(1))) if match else Filter("identity")
    if not operands:
        return CommandResult(filter=flt)
    accesses = []
    for path in paths:
        accesses.extend(narrow(Access(path, "read"), flt) or [Access(path, "read")])
    return CommandResult(accesses)


def rule_awk(argv: list[str], ctx: PathContext) -> CommandResult:
    options, operands = split_options(argv[1:], "Fvf")
    if has(options, "-f") or not operands:
        return CommandResult(recognized=False)
    window = awk_range(operands[0])
    if window is None:
        return CommandResult(recognized=False)
    flt = Filter("ranges", ranges=[window])
    files = operands[1:]
    if not files:
        return CommandResult(filter=flt)
    return CommandResult([Access(p, "read", *window) for p in map(ctx.resolve, files) if p])


def rule_meta(argv: list[str], ctx: PathContext) -> CommandResult:
    options, operands = split_options(argv[1:], "IL" if argv[0] == "tree" else "")
    if argv[0] == "wc" and not operands:
        return CommandResult(filter=Filter("meta"))
    paths = operands or (["."] if argv[0] in ("ls", "tree", "du") else [])
    return CommandResult([Access(p, "meta") for p in map(ctx.resolve, paths) if p])


def rule_find(argv: list[str], ctx: PathContext) -> CommandResult:
    args = argv[1:]
    if any(a in ("-exec", "-execdir", "-delete", "-ok", "-fprint") for a in args):
        return CommandResult(recognized=False)
    paths = []
    for arg in args:
        if arg.startswith("-") or arg in ("(", "!", ")"):
            break
        paths.append(arg)
    return CommandResult([Access(p, "meta") for p in map(ctx.resolve, paths or ["."]) if p])


def pytest_accesses(args: list[str], ctx: PathContext) -> CommandResult:
    _, operands = split_options(args, PYTEST_SHORT, PYTEST_LONG)
    accesses = []
    for operand in operands:
        path = ctx.resolve(re.split(r"::|\[", operand, maxsplit=1)[0])
        if path is not None:
            accesses.append(Access(path, "exec"))
    return CommandResult(accesses or [Access(".", "exec")])


def rule_python(argv: list[str], ctx: PathContext, heredoc: str | None) -> CommandResult:
    idx = 1
    while idx < len(argv):
        arg = argv[idx]
        if arg == "-m" and idx + 1 < len(argv):
            module, rest = argv[idx + 1], argv[idx + 2 :]
            if module == "pytest":
                return pytest_accesses(rest, ctx)
            return CommandResult()
        if arg.startswith("-m") and len(arg) > 2:
            if arg[2:] == "pytest":
                return pytest_accesses(argv[idx + 1 :], ctx)
            return CommandResult()
        if arg == "-c" and idx + 1 < len(argv):
            return CommandResult(recognized=False, probe=argv[idx + 1])
        if arg in ("-W", "-X") and idx + 1 < len(argv):
            idx += 2
            continue
        if arg == "-":
            return CommandResult(recognized=False, probe=heredoc)
        if arg.startswith("-"):
            idx += 1
            continue
        path = ctx.resolve(arg)
        return CommandResult([Access(path, "exec")] if path else [], recognized=path is not None)
    return CommandResult(recognized=False, probe=heredoc)


def looks_like_path(arg: str) -> bool:
    if any(c in arg for c in ":^~") or ".." in arg:
        return False
    return "/" in arg or bool(re.search(r"\.\w+$", arg))


def rule_git(argv: list[str], ctx: PathContext) -> CommandResult:
    args = argv[1:]
    while args and args[0] in ("-C", "-c") and len(args) > 1:
        if args[0] == "-C":
            return CommandResult(recognized=False)
        args = args[2:]
    while args and args[0] in ("--no-pager", "-P", "--paginate", "-p"):
        args = args[1:]
    if not args:
        return CommandResult()
    sub, rest = args[0], args[1:]
    if sub in GIT_READONLY:
        if "--" in rest:
            paths = rest[rest.index("--") + 1 :]
            return CommandResult([Access(p, "meta") for p in map(ctx.resolve, paths) if p])
        return CommandResult()
    if sub == "grep":
        options, operands = split_options(rest, GREP_SHORT, GREP_LONG)
        return search_accesses(ctx, options, operands, ".", "grep")
    if sub in ("diff", "show", "blame"):
        if "--" in rest:
            before, paths = rest[: rest.index("--")], rest[rest.index("--") + 1 :]
        else:
            before, paths = rest, []
        accesses = []
        for arg in before:
            if arg.startswith("-"):
                continue
            if sub == "show" and ":" in arg:
                path = ctx.resolve(arg.split(":", 1)[1]) if arg.split(":", 1)[1] else None
                if path:
                    accesses.append(Access(path, "read"))
            elif looks_like_path(arg):
                paths.append(arg)
        mode = "read" if sub == "blame" else "meta"
        accesses.extend(Access(p, mode) for p in map(ctx.resolve, paths) if p)
        return CommandResult(accesses)
    if sub in ("checkout", "restore") and "--" in rest:
        paths = rest[rest.index("--") + 1 :]
        return CommandResult([Access(p, "write") for p in map(ctx.resolve, paths) if p])
    return CommandResult(recognized=False)


def rule_files(argv: list[str], ctx: PathContext) -> CommandResult:
    tool = argv[0]
    _, operands = split_options(argv[1:], "tS" if tool in ("cp", "mv") else "")
    paths = [p for p in map(ctx.resolve, operands) if p]
    if tool == "cp" and len(paths) >= 2:
        return CommandResult([Access(p, "read") for p in paths[:-1]] + [Access(paths[-1], "write")])
    return CommandResult([Access(p, "write") for p in paths])


def strip_wrappers(argv: list[str]) -> list[str]:
    while argv:
        head = argv[0]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", head):
            argv = argv[1:]
        elif (
            head in WRAPPERS_NO_ARG
            and len(argv) > 1
            and not (head == "command" and argv[1] == "-v")
        ):
            argv = argv[1:]
        elif head == "timeout" and len(argv) > 2:
            _, rest = split_options(argv[1:], "sk")
            argv = rest[1:]
        elif head == "env" and len(argv) > 1:
            argv = argv[1:]
        elif head == "stdbuf" and len(argv) > 1:
            idx = 1
            while idx < len(argv) and argv[idx].startswith("-"):
                idx += 1
            argv = argv[idx:]
        else:
            break
    return argv


def parse_simple(
    tokens: list[str], ctx: PathContext, heredocs: list[str], piped: bool = False
) -> CommandResult:
    argv: list[str] = []
    redirects: list[Access] = []
    heredoc: str | None = None
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        placeholder = HEREDOC_TOKEN.match(token)
        if placeholder:
            heredoc = heredocs[int(placeholder.group(1))]
        elif is_redirect(token):
            target = tokens[idx + 1] if idx + 1 < len(tokens) else None
            idx += 1
            if target is None:
                return CommandResult(recognized=False)
            if token == "<<<" or token.endswith("&") and re.fullmatch(r"\d+|-", target):
                pass
            elif ">" in token:
                path = ctx.resolve(target)
                if path:
                    redirects.append(Access(path, "write"))
            elif token == "<":
                path = ctx.resolve(target)
                if path:
                    redirects.append(Access(path, "read"))
        else:
            argv.append(token)
        idx += 1
    argv = strip_wrappers(argv)
    if not argv:
        return CommandResult(redirects)
    result = dispatch(argv, ctx, heredoc, piped)
    result.accesses = result.accesses + redirects
    return result


def dispatch(
    argv: list[str], ctx: PathContext, heredoc: str | None, piped: bool = False
) -> CommandResult:
    name = posixpath.basename(argv[0])
    argv = [name, *argv[1:]]
    if name in KEYWORDS:
        return CommandResult(recognized=False)
    if name == "cd":
        ctx.chdir(argv[1] if len(argv) > 1 else None)
        return CommandResult()
    if name in NOOPS:
        return CommandResult()
    if name in READERS:
        if heredoc is not None and len(argv) == 1:
            return CommandResult()
        return rule_reader(argv, ctx)
    if name in ("head", "tail"):
        return rule_head_tail(argv, ctx)
    if name == "sed":
        return rule_sed(argv, ctx)
    if name == "awk":
        return rule_awk(argv, ctx)
    if name in ("grep", "egrep", "fgrep"):
        return rule_grep(argv, ctx, piped)
    if name == "rg":
        return rule_rg(argv, ctx, piped)
    if name in META:
        return rule_meta(argv, ctx)
    if name == "find":
        return rule_find(argv, ctx)
    if PYTHONS.match(name):
        return rule_python(argv, ctx, heredoc)
    if name in ("pytest", "py.test"):
        return pytest_accesses(argv[1:], ctx)
    if name == "git":
        return rule_git(argv, ctx)
    if name in ("cp", "mv", "rm", "touch", "mkdir", "tee"):
        return rule_files(argv, ctx)
    if name in ("sort", "uniq", "cut", "tr", "column", "fold"):
        return CommandResult(filter=Filter("identity"))
    if name in ("source", ".") and len(argv) > 1:
        path = ctx.resolve(argv[1])
        return CommandResult([Access(path, "exec")] if path else [])
    return CommandResult(recognized=False)


def _dedupe(accesses: list[Access]) -> list[Access]:
    seen: set[tuple[Any, ...]] = set()
    out = []
    for access in accesses:
        key = (access.path, access.mode, access.start, access.end, access.pattern)
        if key not in seen:
            seen.add(key)
            out.append(access)
    return out


def parse_script(inner: str, workspace_root: str = "/workspace") -> ParsedCommand:
    pre = preprocess(inner)
    probes = []
    if not pre.ok or pre.substitution:
        return ParsedCommand([], True, _heredoc_probe(inner, pre))
    try:
        tokens = tokenize(pre.script)
    except ValueError:
        return ParsedCommand([], True, None)
    ctx = PathContext(workspace_root, ".")
    accesses: list[Access] = []
    unparsed = False
    for pipeline in split_pipelines(tokens):
        results = [
            parse_simple(cmd, ctx, pre.heredocs, piped=pos > 0) for pos, cmd in enumerate(pipeline)
        ]
        for result in results:
            if result.probe:
                probes.append(result.probe)
            if not result.recognized:
                unparsed = True
        first = results[0]
        reads = [a for a in first.accesses if a.mode == "read"]
        if len(reads) == 1 and len(first.accesses) == 1 and first.recognized:
            current = [reads[0]]
            for result in results[1:]:
                if result.filter is None or len(current) != 1:
                    break
                narrowed = narrow(current[0], result.filter)
                if narrowed is not None:
                    current = narrowed
            accesses.extend(current)
        else:
            accesses.extend(first.accesses)
        for result in results[1:]:
            accesses.extend(result.accesses)
    if re.search(r"\bpython(3(\.\d+)?)?\b", pre.script):
        probes.extend(body for body in pre.heredocs if body not in probes)
    probe = "\n".join(probes) if probes else None
    return ParsedCommand(_dedupe(accesses), unparsed, probe)


def _heredoc_probe(inner: str, pre: Preprocessed) -> str | None:
    if pre.heredocs and re.search(r"\bpython3?\b", inner):
        return "\n".join(pre.heredocs)
    match = re.search(r"\bpython3?\s+-c\s+(['\"])(.*?)\1", inner, re.S)
    return match.group(2) if match else None


# ---------------------------------------------------------------------------
# Edits


def rel_path(path: str, workspace_root: str) -> str:
    resolved = PathContext(workspace_root, ".").resolve(path)
    return resolved if resolved is not None else path


def hunks_for(patch: str | None, fmt: str | None) -> list[Hunk]:
    if not patch:
        return []
    if fmt == "envelope":
        out = []
        for hunk in parse_envelope_hunks(patch):
            old_len, new_len = hunk_header_counts(hunk)
            out.append(Hunk(None, old_len, None, new_len))
        return out
    return [
        Hunk(h.old_start, len(h.old_lines), h.new_start, len(h.new_lines))
        for h in parse_unified_hunks(patch)
    ]


def envelope_edits(
    envelope: str, workspace_root: str, item_id: str | None, status: str | None
) -> list[EditEvent]:
    edits = []
    for entry in parse_envelope(envelope):
        path = rel_path(entry.path, workspace_root)
        if entry.kind == "add":
            content = "".join(line[1:] + "\n" for line in entry.body.splitlines()
                              if line.startswith("+"))  # fmt: skip
            patch, fmt = content_patch(content, "add"), "unified"
        elif entry.kind == "delete":
            patch, fmt = None, None
        else:
            patch, fmt = entry.body, "envelope"
        edits.append(
            EditEvent(
                path=path,
                change_kind=entry.kind,
                patch=patch,
                patch_format=fmt,
                hunks=hunks_for(patch, fmt),
                move_path=rel_path(entry.move_path, workspace_root) if entry.move_path else None,
                status=status,
                source="apply_patch_command",
                item_id=item_id,
            )
        )
    return edits


def is_apply_patch(inner: str) -> str | None:
    stripped = inner.lstrip()
    if not re.match(r"(cd\s+\S+\s*&&\s*)?(apply_patch|applypatch)\b", stripped):
        return None
    return find_envelope(inner)


# ---------------------------------------------------------------------------
# Rollout


@dataclass
class RolloutInfo:
    file_changes: list[tuple[str | None, dict[str, Any]]] = field(default_factory=list)
    usage_by_anchor: list[tuple[int, dict[str, int]]] = field(default_factory=list)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def read_rollout(path: Path) -> RolloutInfo:
    info = RolloutInfo()
    tools_done = 0
    last_total: dict[str, int] | None = None
    for record in read_jsonl(path):
        if record.get("type") != "event_msg":
            continue
        payload = record.get("payload") or {}
        ptype = payload.get("type")
        if ptype == "item_completed":
            item = payload.get("item") or {}
            itype = item.get("type")
            if itype == "FileChange":
                info.file_changes.append((item.get("id"), item.get("changes") or {}))
                tools_done += 1
            elif itype == "CommandExecution":
                tools_done += 1
        elif ptype == "token_count":
            total = ((payload.get("info") or {}).get("total_token_usage")) or None
            if not total:
                continue
            usage = {k: int(total.get(k) or 0) for k in USAGE_FIELDS}
            if usage != last_total:
                info.usage_by_anchor.append((tools_done, usage))
                last_total = usage
    return info


def rollout_patch(entry: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    kind = entry.get("type")
    if kind == "update":
        return entry.get("unified_diff"), "unified", entry.get("move_path")
    if kind in ("add", "delete") and entry.get("content") is not None:
        return content_patch(entry["content"], kind), "unified", None
    return None, None, None


# ---------------------------------------------------------------------------
# Exec stream


def command_events(item: dict[str, Any], workspace_root: str) -> list[Event]:
    command = item.get("command") or ""
    if isinstance(command, list):
        command = shlex.join(command)
    inner = extract_inner(command)
    status = item.get("status")
    exit_code = item.get("exit_code")
    envelope = is_apply_patch(inner)
    if envelope:
        edit_status = "completed" if exit_code in (0, None) and status != "failed" else "failed"
        edits = envelope_edits(envelope, workspace_root, item.get("id") or command, edit_status)
        if edits:
            return list(edits)
    parsed = parse_script(inner, workspace_root)
    return [
        CommandEvent(
            command=command,
            inner=inner,
            exit_code=exit_code,
            output_chars=len(item.get("aggregated_output") or ""),
            status=status,
            accesses=parsed.accesses,
            unparsed=parsed.unparsed,
            probe=parsed.probe,
            item_id=item.get("id"),
        )
    ]


def file_change_events(
    item: dict[str, Any],
    rollout_changes: dict[str, Any] | None,
    workspace_root: str,
) -> list[Event]:
    by_path: dict[str, dict[str, Any]] = {}
    for raw_path, entry in (rollout_changes or {}).items():
        by_path[rel_path(raw_path, workspace_root)] = entry or {}
    events: list[Event] = []
    for change in item.get("changes") or []:
        path = rel_path(change.get("path") or "", workspace_root)
        patch, fmt, move = rollout_patch(by_path.get(path, {}))
        events.append(
            EditEvent(
                path=path,
                change_kind=change.get("kind") or "update",
                patch=patch,
                patch_format=fmt,
                hunks=hunks_for(patch, fmt),
                move_path=rel_path(move, workspace_root) if move else None,
                status=item.get("status"),
                item_id=item.get("id"),
            )
        )
    return events


def usage_event(usage: dict[str, Any], source: str) -> UsageEvent:
    return UsageEvent(**{k: int(usage.get(k) or 0) for k in USAGE_FIELDS}, source=source)


def normalize_trace(
    exec_path: Path,
    rollout_path: Path | None = None,
    workspace_root: str = "/workspace",
    header: Header | None = None,
) -> Trace:
    header = header or Header()
    rollout = read_rollout(rollout_path) if rollout_path else RolloutInfo()
    records = read_jsonl(exec_path)
    exec_fc_ids = [
        (r.get("item") or {}).get("id")
        for r in records
        if r.get("type") == "item.completed" and (r.get("item") or {}).get("type") == "file_change"
    ]
    rollout_ids = {fid for fid, _ in rollout.file_changes if fid is not None}
    match_by_id = bool(rollout_ids & {i for i in exec_fc_ids if i is not None})
    rollout_by_id = {fid: changes for fid, changes in rollout.file_changes}

    events: list[Event] = []
    pending_usage = list(rollout.usage_by_anchor)
    tools_done = 0
    fc_seen = 0
    started: dict[str, dict[str, Any]] = {}
    completed: set[str] = set()

    def flush_usage() -> None:
        while pending_usage and pending_usage[0][0] <= tools_done:
            events.append(usage_event(pending_usage.pop(0)[1], "rollout"))

    flush_usage()
    for record in records:
        rtype = record.get("type")
        if rtype == "thread.started":
            header.thread_id = header.thread_id or record.get("thread_id")
        elif rtype == "turn.completed":
            events.append(usage_event(record.get("usage") or {}, "exec"))
        elif rtype == "turn.failed":
            message = ((record.get("error") or {}).get("message")) or ""
            events.append(ErrorEvent(message, "turn.failed"))
        elif rtype == "error":
            events.append(ErrorEvent(record.get("message") or "", "error"))
        elif rtype == "item.started":
            item = record.get("item") or {}
            if item.get("id"):
                started[item["id"]] = item
        elif rtype == "item.completed":
            item = record.get("item") or {}
            if item.get("id"):
                completed.add(item["id"])
            itype = item.get("type")
            if itype == "command_execution":
                events.extend(command_events(item, workspace_root))
                tools_done += 1
            elif itype == "file_change":
                if match_by_id:
                    changes = rollout_by_id.get(item.get("id"))
                elif fc_seen < len(rollout.file_changes):
                    changes = rollout.file_changes[fc_seen][1]
                else:
                    changes = None
                fc_seen += 1
                events.extend(file_change_events(item, changes, workspace_root))
                tools_done += 1
            elif itype == "agent_message":
                events.append(MessageEvent(item.get("text") or "", item.get("id")))
            elif itype == "reasoning":
                events.append(ReasoningEvent(item.get("text") or "", item.get("id")))
            elif itype in ("mcp_tool_call", "collab_tool_call", "web_search", "todo_list"):
                events.append(other_tool_event(item))
            elif itype == "error":
                events.append(ErrorEvent(item.get("message") or "", "item"))
            flush_usage()
    for item_id, item in started.items():
        if item_id not in completed and item.get("type") == "command_execution":
            events.extend(command_events(item, workspace_root))
    for _, usage in pending_usage:
        events.append(usage_event(usage, "rollout"))
    return Trace(header, number_events(events))


def other_tool_event(item: dict[str, Any]) -> OtherToolEvent:
    itype = item.get("type") or "tool"
    if itype == "mcp_tool_call":
        tool, detail = f"mcp:{item.get('server')}/{item.get('tool')}", ""
    elif itype == "collab_tool_call":
        tool, detail = f"collab:{item.get('tool')}", ""
    elif itype == "web_search":
        tool, detail = "web_search", str(item.get("query") or "")
    else:
        tool, detail = itype, ""
    return OtherToolEvent(tool, detail, item.get("status"), item.get("id"))


def normalize(
    exec_jsonl_path: Path | str,
    rollout_path: Path | str | None = None,
    workspace_root: str = "/workspace",
) -> list[Event]:
    trace = normalize_trace(
        Path(exec_jsonl_path), Path(rollout_path) if rollout_path else None, workspace_root
    )
    return trace.events


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exec", dest="exec_path", type=Path, required=True)
    parser.add_argument("--rollout", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--arm")
    parser.add_argument("--rep", type=int)
    parser.add_argument("--model")
    parser.add_argument("--workspace-root", default="/workspace")
    args = parser.parse_args(argv)
    header = Header(arm=args.arm, rep=args.rep, model=args.model)
    trace = normalize_trace(args.exec_path, args.rollout, args.workspace_root, header)
    write_trace(trace, args.out)
    unparsed = sum(1 for e in trace.events if isinstance(e, CommandEvent) and e.unparsed)
    print(f"{len(trace.events)} events, {trace.tool_call_count()} tool calls, "
          f"{unparsed} unparsed commands -> {args.out}")  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
