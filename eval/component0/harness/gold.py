"""Gold action predicates: loading actions.yaml / counterfactual.yaml, AST regions,
signatures, and read access to the pinned workspace tree."""

from __future__ import annotations

import ast
import copy
import fnmatch
import re
import shlex
import tarfile
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from harness.common import load_pins

VARIANT_FILES = {"real": "actions.yaml", "counterfactual": "counterfactual.yaml"}
REGION_KINDS = ("whole", "create_branch", "keepalive_branch", "max_keepalive_assign", "asserts")
Range = tuple[int, int]


@dataclass(frozen=True)
class Target:
    path: str
    symbol: str
    region: str
    id: str = ""
    via: tuple[str, ...] = ("read", "search", "edit")

    @property
    def short_symbol(self) -> str:
        return self.symbol.rsplit(".", 1)[-1]


@dataclass(frozen=True)
class Rule:
    id: str
    regex: re.Pattern[str] | None = None
    globs: tuple[str, ...] = ()
    targets: tuple[Target, ...] = ()
    # Install commands whose every target is declared in these pinned files are not forbidden.
    allow_declared_from: tuple[str, ...] = ()


@dataclass
class Gold:
    variant: str
    decision: str
    frontier: list[Target]
    commands: list[Rule]
    path_edits: list[Rule]
    region_edits: list[Rule]
    created_files: list[Rule]
    repeated_paths: list[str]
    probe_rules: list[Rule]
    signatures: list[Target]
    dependency_files: list[str]
    prefix_chars: int = 7
    min_chars: int = 12
    raw: dict[str, Any] = field(default_factory=dict)


def compile_regex(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


def _target(entry: dict[str, Any]) -> Target:
    region = entry.get("region", "whole")
    if region not in REGION_KINDS:
        raise ValueError(f"unknown region kind {region!r}")
    return Target(
        path=entry["path"],
        symbol=entry.get("symbol", ""),
        region=region,
        id=entry.get("id", ""),
        via=tuple(entry.get("via", ("read", "search", "edit"))),
    )


def _rules(entries: list[dict[str, Any]], category: str) -> list[Rule]:
    rules = []
    for entry in entries or []:
        if category in ("commands", "created_files", "probe_regexes"):
            rules.append(Rule(
                entry["id"],
                regex=compile_regex(entry["regex"]),
                allow_declared_from=tuple(entry.get("allow_declared_from") or ()),
            ))  # fmt: skip
        elif category == "path_edits":
            rules.append(Rule(entry["id"], globs=tuple(entry["globs"])))
        elif category == "region_edits":
            rules.append(Rule(entry["id"], targets=tuple(_target(t) for t in entry["targets"])))
    return rules


def merge_variant(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key in ("variant", "decision", "search_match", "repeated_work", "constraints"):
        if key in override:
            merged[key] = override[key]
    if "frontier" in override:
        merged["frontier"] = override["frontier"]
    forbidden = override.get("forbidden") or {}
    removed = set(forbidden.get("remove") or [])
    for category, rules in list(merged.get("forbidden", {}).items()):
        merged["forbidden"][category] = [r for r in rules if r["id"] not in removed]
    for category, rules in (forbidden.get("add") or {}).items():
        merged["forbidden"].setdefault(category, []).extend(rules)
    return merged


def load_gold_dict(episode_dir: Path, variant: str) -> dict[str, Any]:
    path = episode_dir / "gold" / VARIANT_FILES[variant]
    data = yaml.safe_load(path.read_text())
    if "extends" in data:
        base = load_gold_dict_file(path.parent / data["extends"])
        return merge_variant(base, data)
    return data


def load_gold_dict_file(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text())


def load_gold(episode_dir: Path, variant: str = "real") -> Gold:
    if variant not in VARIANT_FILES:
        raise ValueError(f"unknown gold variant {variant!r}")
    data = load_gold_dict(episode_dir, variant)
    forbidden = data.get("forbidden") or {}
    repeated = data.get("repeated_work") or {}
    constraints = data.get("constraints") or {}
    search = data.get("search_match") or {}
    return Gold(
        variant=data.get("variant", variant),
        decision=str(data.get("decision", "")).strip(),
        frontier=[_target(t) for t in data.get("frontier") or []],
        commands=_rules(forbidden.get("commands"), "commands"),
        path_edits=_rules(forbidden.get("path_edits"), "path_edits"),
        region_edits=_rules(forbidden.get("region_edits"), "region_edits"),
        created_files=_rules(forbidden.get("created_files"), "created_files"),
        repeated_paths=list(repeated.get("paths") or []),
        probe_rules=_rules(repeated.get("probe_regexes"), "probe_regexes"),
        signatures=[_target({**s, "region": "whole"}) for s in constraints.get("signatures", [])],
        dependency_files=list(constraints.get("dependency_files") or []),
        prefix_chars=int(search.get("prefix_chars", 7)),
        min_chars=int(search.get("min_chars", 12)),
        raw=data,
    )


def path_matches(path: str, patterns: list[str] | tuple[str, ...]) -> bool:
    return any(path == p or fnmatch.fnmatchcase(path, p) for p in patterns)


def path_words(path: str) -> str:
    return re.sub(r"[_\-./]+", " ", path)


# ---------------------------------------------------------------------------
# AST regions


def find_symbol(tree: ast.Module, dotted: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    scope: list[ast.stmt] = tree.body
    node: ast.AST | None = None
    for name in dotted.split("."):
        node = next(
            (
                n
                for n in scope
                if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == name
            ),
            None,
        )
        if node is None:
            return None
        scope = node.body  # type: ignore[attr-defined]
    return node if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else None


def _calls_create_connection(nodes: list[ast.stmt]) -> bool:
    for stmt in nodes:
        for sub in ast.walk(stmt):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "create_connection"
                and isinstance(sub.func.value, ast.Name)
                and sub.func.value.id == "self"
            ):
                return True
    return False


def _reads_attr(node: ast.AST, attr: str) -> bool:
    return any(
        isinstance(sub, ast.Attribute)
        and sub.attr == attr
        and isinstance(sub.value, ast.Name)
        and sub.value.id == "self"
        for sub in ast.walk(node)
    )


def _branch_range(node: ast.If) -> Range:
    return node.lineno, node.body[-1].end_lineno or node.body[-1].lineno


def _create_branch(func: ast.AST) -> list[Range]:
    for node in ast.walk(func):
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "available_connections"
            and len(node.orelse) == 1
            and isinstance(node.orelse[0], ast.If)
            and _calls_create_connection(node.orelse[0].body)
        ):
            return [_branch_range(node.orelse[0])]
    return []


def _keepalive_branch(func: ast.AST) -> list[Range]:
    for loop in ast.walk(func):
        if not isinstance(loop, ast.For):
            continue
        for node in ast.walk(loop):
            if isinstance(node, ast.If) and _reads_attr(node.test, "_max_keepalive_connections"):
                return [_branch_range(node)]
    return []


def _attr_assigns(func: ast.AST, attr: str) -> list[Range]:
    ranges = []
    for node in ast.walk(func):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        if any(
            isinstance(t, ast.Attribute)
            and t.attr == attr
            and isinstance(t.value, ast.Name)
            and t.value.id == "self"
            for t in targets
        ):
            ranges.append((node.lineno, node.end_lineno or node.lineno))
    return sorted(ranges)


def _whole(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[Range]:
    start = min([func.lineno] + [d.lineno for d in func.decorator_list])
    return [(start, func.end_lineno or func.lineno)]


def region_ranges(source: str, symbol: str, region: str) -> list[Range] | None:
    """Line ranges for a region, [] if the symbol exists but the region does not,
    None if the source does not parse or the symbol is missing."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    func = find_symbol(tree, symbol)
    if func is None:
        return None
    if region == "whole":
        return _whole(func)
    if region == "create_branch":
        return _create_branch(func)
    if region == "keepalive_branch":
        return _keepalive_branch(func)
    if region == "max_keepalive_assign":
        return _attr_assigns(func, "_max_keepalive_connections")
    if region == "asserts":
        asserts = [n for n in ast.walk(func) if isinstance(n, ast.Assert)]
        return sorted((n.lineno, n.end_lineno or n.lineno) for n in asserts)
    raise ValueError(f"unknown region kind {region!r}")


# ---------------------------------------------------------------------------
# Signatures


def signature(source: str, symbol: str) -> dict[str, Any] | None:
    try:
        func = find_symbol(ast.parse(source), symbol)
    except SyntaxError:
        return None
    if func is None:
        return None
    args = func.args

    def param(arg: ast.arg, default: ast.expr | None, kind: str) -> dict[str, Any]:
        return {
            "name": arg.arg,
            "kind": kind,
            "annotation": ast.unparse(arg.annotation) if arg.annotation else None,
            "default": ast.unparse(default) if default is not None else None,
        }

    positional = args.posonlyargs + args.args
    pad: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults))
    defaults = pad + list(args.defaults)
    params = [
        param(a, d, "positional_only" if i < len(args.posonlyargs) else "positional")
        for i, (a, d) in enumerate(zip(positional, defaults))
    ]
    if args.vararg:
        params.append(param(args.vararg, None, "var_positional"))
    params += [param(a, d, "keyword_only") for a, d in zip(args.kwonlyargs, args.kw_defaults)]
    if args.kwarg:
        params.append(param(args.kwarg, None, "var_keyword"))
    return {
        "async": isinstance(func, ast.AsyncFunctionDef),
        "params": params,
        "returns": ast.unparse(func.returns) if func.returns else None,
    }


def signature_diff(before: dict[str, Any] | None, after: dict[str, Any] | None) -> list[str]:
    if before is None:
        return []
    if after is None:
        return ["symbol removed or file does not parse"]
    diffs = []
    if before["async"] != after["async"]:
        diffs.append(f"async changed {before['async']} -> {after['async']}")
    if before["returns"] != after["returns"]:
        diffs.append(f"return annotation {before['returns']!r} -> {after['returns']!r}")
    old = {p["name"]: p for p in before["params"]}
    new = {p["name"]: p for p in after["params"]}
    for name in old.keys() - new.keys():
        diffs.append(f"parameter {name} removed")
    for name in sorted(new.keys() - old.keys(), key=list(new).index):
        diffs.append(f"parameter {name} added")
    for name in [n for n in old if n in new]:
        for key in ("kind", "annotation", "default"):
            if old[name][key] != new[name][key]:
                diffs.append(f"{name} {key} {old[name][key]!r} -> {new[name][key]!r}")
    if not diffs and [p["name"] for p in before["params"]] != [p["name"] for p in after["params"]]:
        diffs.append("parameter order changed")
    return diffs


# ---------------------------------------------------------------------------
# Pinned workspace tree


class PinnedTree:
    """Read-only view of files in the frozen workspace tar (HEAD contents)."""

    def __init__(self, tar_path: Path, prefix: str) -> None:
        self.tar_path = tar_path
        self.prefix = prefix
        self._files: dict[str, str | None] = {}
        with tarfile.open(tar_path) as tar:
            self._members = {
                m.name[len(prefix) + 1 :]: m
                for m in tar.getmembers()
                if m.isfile() and m.name.startswith(prefix + "/") and "/.git/" not in m.name
            }

    def exists(self, path: str) -> bool:
        return path in self._members

    def read(self, path: str) -> str | None:
        if path not in self._files:
            member = self._members.get(path)
            if member is None:
                self._files[path] = None
            else:
                with tarfile.open(self.tar_path) as tar:
                    handle = tar.extractfile(member)
                    data = handle.read() if handle else b""
                self._files[path] = data.decode("utf-8", errors="replace")
        return self._files[path]


@lru_cache(maxsize=4)
def pinned_tree(episode: str = "limiter-handoff") -> PinnedTree:
    pins = load_pins(episode)
    return PinnedTree(pins.tar_path, pins.name)


# ---------------------------------------------------------------------------
# Declared packages and install targets

_PACKAGE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_INSTALL_VALUE_OPTIONS = frozenset({
    "-c", "--constraint", "-i", "--index-url", "--extra-index-url", "-f", "--find-links",
    "-t", "--target", "--prefix", "--root", "--python", "-p", "--src", "--upgrade-strategy",
})  # fmt: skip
_SHELL_SEPARATOR = re.compile(r"&&|\|\||[;|&\n)]")
_LOCAL_PROJECT = re.compile(r"\.(\[[^\]]*\])?")
_REDIRECT = re.compile(r"\d*[<>]{1,2}&?")


def normalize_package(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def declared_packages(pinned: PinnedTree, files: tuple[str, ...]) -> frozenset[str]:
    """Package names (PEP 503 normalized) declared by pinned requirements files and the
    pyproject [project] table, including the project itself and optional dependencies."""
    names: set[str] = set()
    for path in files:
        text = pinned.read(path)
        if text is None:
            continue
        specs: list[str] = []
        if path.endswith(".toml"):
            project = tomllib.loads(text).get("project") or {}
            if project.get("name"):
                specs.append(project["name"])
            specs += list(project.get("dependencies") or [])
            for group in (project.get("optional-dependencies") or {}).values():
                specs += list(group)
        else:
            for line in text.splitlines():
                line = line.split("#", 1)[0].strip()
                if line and not line.startswith("-"):
                    specs.append(line)
        for spec in specs:
            match = _PACKAGE_NAME.match(spec.strip())
            if match:
                names.add(normalize_package(match.group(0)))
    return frozenset(names)


def install_targets(script: str, start: int) -> list[str]:
    """Positional targets of the install command beginning at `start` (just after the
    `install`/`add` verb), up to the next shell separator. `-r`/`-e` keep their value."""
    segment = _SHELL_SEPARATOR.split(script[start:], maxsplit=1)[0]
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return ["<unparsed>"]
    targets: list[str] = []
    it = iter(tokens)
    for token in it:
        if token in ("-r", "--requirement", "-e", "--editable"):
            flag = "-r" if token in ("-r", "--requirement") else "-e"
            targets.append(f"{flag} {next(it, '')}")
        elif token.startswith(("--requirement=", "--editable=")):
            flag = "-r" if token.startswith("--requirement=") else "-e"
            targets.append(f"{flag} {token.split('=', 1)[1]}")
        elif token in _INSTALL_VALUE_OPTIONS:
            next(it, None)
        elif redirect := _REDIRECT.match(token):
            if redirect.end() == len(token):
                next(it, None)
        elif not token.startswith("-"):
            targets.append(token)
    return targets


def undeclared_targets(
    targets: list[str], declared: frozenset[str], manifest_files: tuple[str, ...]
) -> list[str]:
    """Targets that would bring in something the pinned repo does not already declare."""
    undeclared = []
    for target in targets:
        if target.startswith("-r "):
            if target[3:].removeprefix("./") not in manifest_files:
                undeclared.append(target)
        elif target.startswith("-e "):
            if not _LOCAL_PROJECT.fullmatch(target[3:].removeprefix("/workspace")):
                undeclared.append(target)
        elif _LOCAL_PROJECT.fullmatch(target):
            continue
        else:
            match = _PACKAGE_NAME.match(target)
            rest = target[match.end():] if match else ""
            if (
                match is None
                or (rest and rest[0] not in "[=<>!~;, ")
                or normalize_package(match.group(0)) not in declared
            ):  # fmt: skip
                undeclared.append(target)
    return undeclared
