"""Gate G2: the decisive history is not recoverable from the built workspace.

Usage (from eval/component0, with the host harness venv):
    .venv/bin/python -m harness.leakcheck [--episode NAME] [--workspace PATH]
    .venv/bin/python -m harness.leakcheck --upstream-baseline
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from harness.common import Pins, clean_env, git, load_pins

ALL_SCOPES = frozenset({"visible_test", "commit_messages", "patch_added_lines"})
LEFTOVER_SUFFIXES = (".orig", ".rej")
FORBIDDEN_NAMES = frozenset({"agents.md", "agents.override.md", "claude.md"})


@dataclass(frozen=True)
class Term:
    pattern: str
    regex: re.Pattern[str]
    scopes: frozenset[str] = ALL_SCOPES


@dataclass(frozen=True)
class Hit:
    source: str
    location: str
    term: str
    excerpt: str

    def __str__(self) -> str:
        return f"{self.source}:{self.location}: /{self.term}/ {self.excerpt}"


@dataclass(frozen=True)
class TermSet:
    tree: tuple[Term, ...]
    change: tuple[Term, ...]
    deletion_only_patches: tuple[str, ...]


def compile_term(pattern: str, scopes: frozenset[str] = ALL_SCOPES) -> Term:
    return Term(pattern, re.compile(pattern, re.IGNORECASE), scopes)


def load_terms(path: Path) -> TermSet:
    data = yaml.safe_load(path.read_text())
    tree = tuple(compile_term(p) for p in data["tree_terms"])
    change = []
    for entry in data["our_change_terms"]:
        scopes = frozenset(entry.get("scopes", ALL_SCOPES))
        unknown = scopes - ALL_SCOPES
        if unknown:
            raise ValueError(f"unknown scopes {sorted(unknown)} for {entry['pattern']!r}")
        change.append(compile_term(entry["pattern"], scopes))
    return TermSet(tree, tuple(change), tuple(data.get("deletion_only_patches", [])))


def scan_text(text: str, terms: tuple[Term, ...] | list[Term], source: str) -> list[Hit]:
    hits = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for term in terms:
            if term.regex.search(line):
                hits.append(Hit(source, str(lineno), term.pattern, line.strip()[:160]))
    return hits


def iter_files(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*") if p.is_file() and ".git" not in p.relative_to(root).parts
    )


def scan_tree(root: Path, terms: tuple[Term, ...]) -> list[Hit]:
    hits = []
    for path in iter_files(root):
        rel = path.relative_to(root).as_posix()
        hits.extend(scan_text(rel, terms, "filename"))
        text = path.read_bytes().decode("utf-8", errors="replace")
        hits.extend(scan_text(text, terms, rel))
    return hits


def git_history(repo: Path) -> str:
    return git(repo, "log", "-p", "--all", "--format=fuller", "--no-color")


def commit_messages(repo: Path) -> list[tuple[str, str]]:
    raw = git(repo, "log", "--all", "--format=%H%x00%B%x1e")
    messages = []
    for record in raw.split("\x1e"):
        if record.strip():
            sha, _, body = record.strip("\n").partition("\x00")
            messages.append((sha, body))
    return messages


def added_lines(patch_text: str) -> str:
    return "\n".join(
        line[1:]
        for line in patch_text.splitlines()
        if line.startswith("+") and not line.startswith("+++ ")
    )


def scoped(terms: tuple[Term, ...], scope: str) -> list[Term]:
    return [t for t in terms if scope in t.scopes]


def structural_hits(workspace: Path, visible_dest: str) -> list[Hit]:
    hits = []
    for path in iter_files(workspace):
        rel = path.relative_to(workspace).as_posix()
        if "pool_counts" in path.name and rel != visible_dest:
            hits.append(Hit("structure", rel, "pool_counts", "extra file naming the visible test"))
        if path.name.endswith(LEFTOVER_SUFFIXES):
            hits.append(Hit("structure", rel, "*.orig|*.rej", "leftover patch artifact"))
        if path.name.lower() in FORBIDDEN_NAMES:
            hits.append(Hit("structure", rel, "agent instructions", "instruction file in tree"))
    return hits


def check_workspace(pins: Pins, workspace: Path, terms: TermSet) -> list[Hit]:
    hits = scan_tree(workspace, terms.tree)
    hits += scan_text(git_history(workspace), terms.tree, "git log -p --all")
    hits += structural_hits(workspace, pins.visible_test_dest)

    visible = workspace / pins.visible_test_dest
    hits += scan_text(
        visible.read_text(), scoped(terms.change, "visible_test"), pins.visible_test_dest
    )

    for sha, body in commit_messages(workspace):
        hits += scan_text(body, scoped(terms.change, "commit_messages"), f"commit {sha[:12]}")

    # fix.patch is gold-only and never enters the workspace, so it is not checked here.
    for patch in (*pins.plant_patches, pins.followup_patch):
        rel = patch.relative_to(pins.episode_dir).as_posix()
        text = added_lines(patch.read_text())
        hits += scan_text(text, scoped(terms.change, "patch_added_lines"), f"{rel} (+)")
        if rel in terms.deletion_only_patches and text:
            hits.append(Hit(rel, "-", "deletion-only", "patch adds lines"))
    return hits


def upstream_baseline(pins: Pins, terms: TermSet) -> list[Hit]:
    with tempfile.TemporaryDirectory() as tmp:
        archive = subprocess.run(
            ["git", "--git-dir", str(pins.cache_dir), "archive", "--format=tar", pins.repo_sha],
            env=clean_env(),
            check=True,
            capture_output=True,
        ).stdout
        tar_path = Path(tmp) / "upstream.tar"
        tar_path.write_bytes(archive)
        with tarfile.open(tar_path) as tar:
            tar.extractall(Path(tmp) / "tree", filter="tar")
        return scan_tree(Path(tmp) / "tree", terms.tree)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode", default="limiter-handoff")
    parser.add_argument("--workspace", type=Path, help="defaults to the built workspace")
    parser.add_argument(
        "--upstream-baseline",
        action="store_true",
        help="scan the pristine pinned upstream tree for tree terms instead",
    )
    args = parser.parse_args(argv)
    pins = load_pins(args.episode)
    terms = load_terms(pins.leak_terms)

    if args.upstream_baseline:
        hits = upstream_baseline(pins, terms)
        label = f"upstream {pins.repo_tag}"
    else:
        workspace = args.workspace or pins.workspace_dir
        hits = check_workspace(pins, workspace, terms)
        label = str(workspace)

    for hit in hits:
        print(hit)
    print(f"\nG2 {'FAIL' if hits else 'PASS'}: {len(hits)} hit(s) in {label}")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
