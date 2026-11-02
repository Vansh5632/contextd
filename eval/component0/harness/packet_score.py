"""Score a context packet against the episode's gold atoms.

Interface used by harness.score (lazily imported) and harness.gate_g3:
    load_atoms(path) -> AtomSet
    split_units(packet_text, kind) -> list[str]
        kind: "paragraphs" | "history" | "contextd"
    score_packet(units, atoms, tokenizer, raw_text=None) -> dict

PACKET_FILES and PACKET_KINDS are the shared arm -> filename and arm -> unit-kind
maps. `contextd` units are readable renderings; pass the original packet as
raw_text so packet_tokens counts that text rather than the renderings.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PACKET_FILES = {
    "raw": "raw.txt",
    "summary": "summary.txt",
    "oracle": "oracle.txt",
    "stale-only": "stale_only.txt",
    "contextd": "contextd.txt",
    "contextd-new": "contextd_new.txt",
}
PACKET_KINDS = {
    "none": "paragraphs",
    "raw": "history",
    "summary": "paragraphs",
    "oracle": "paragraphs",
    "stale-only": "paragraphs",
    "contextd": "contextd",
    "contextd-new": "contextd",
}

HISTORY_DELIMITER = "\n\n----\n\n"
ROLES = frozenset(
    {"required", "constraint", "ruled_out", "stale", "unresolved", "next_step", "optional"}
)
RECALL_ROLES = frozenset({"required", "constraint", "ruled_out", "next_step"})
USEFUL_ROLES = RECALL_ROLES | {"unresolved", "optional"}


@dataclass(frozen=True)
class Atom:
    id: str
    role: str
    claim: str
    patterns: tuple[re.Pattern[str], ...]

    def matches(self, text: str) -> bool:
        return any(p.search(text) for p in self.patterns)


@dataclass(frozen=True)
class AtomSet:
    version: int
    superseded: re.Pattern[str]
    atoms: tuple[Atom, ...]

    def by_role(self, *roles: str) -> list[Atom]:
        return [a for a in self.atoms if a.role in roles]

    def get(self, atom_id: str) -> Atom:
        for atom in self.atoms:
            if atom.id == atom_id:
                return atom
        raise KeyError(atom_id)


def load_atoms(path: Path | str) -> AtomSet:
    data = yaml.safe_load(Path(path).read_text())
    atoms = []
    seen: set[str] = set()
    for entry in data["atoms"]:
        atom_id, role = str(entry["id"]), str(entry["role"])
        if role not in ROLES:
            raise ValueError(f"atom {atom_id}: unknown role {role!r}")
        if atom_id in seen:
            raise ValueError(f"duplicate atom id {atom_id!r}")
        seen.add(atom_id)
        match = entry["match"]
        patterns = tuple(re.compile(p, re.IGNORECASE) for p in
                         ([match] if isinstance(match, str) else match))  # fmt: skip
        if not patterns:
            raise ValueError(f"atom {atom_id}: empty match list")
        atoms.append(Atom(atom_id, role, str(entry["claim"]), patterns))
    return AtomSet(
        version=int(data["version"]),
        superseded=re.compile(data["superseded_marker"], re.IGNORECASE),
        atoms=tuple(atoms),
    )


def _contextd_units(packet_text: str) -> list[str]:
    try:
        data = json.loads(packet_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid contextd JSON: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise ValueError("contextd packet must be a JSON object")
    units: list[str] = []
    intent = data.get("intent")
    if intent is not None:
        if not isinstance(intent, dict) or "text" not in intent:
            raise ValueError("contextd intent must be an object with text")
        units.append(f"[intent] {intent['text']}")
    for key, label in (("recent_activity", "recent"), ("relevant_history", "history")):
        items = data.get(key)
        if items is None:
            continue
        if not isinstance(items, list):
            raise ValueError(f"contextd {key} must be a list")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"contextd {key} items must be objects")
            units.append(f"[{label} {item.get('source', '')}] {item.get('text', '')}")
    related = data.get("related")
    if related is not None:
        if not isinstance(related, list):
            raise ValueError("contextd related must be a list")
        for item in related:
            if not isinstance(item, dict):
                raise ValueError("contextd related items must be objects")
            units.append(f"[related {item.get('kind', '')}] {item.get('name', '')}")
    archived = data.get("archived")
    if archived is not None and not isinstance(archived, dict):
        raise ValueError("contextd archived must be an object")
    if isinstance(archived, dict) and archived.get("digest"):
        units.append(f"[archived] {archived['digest']}")
    units.extend(_claim_view_units(data))
    return units


def _claim_text(claim: Any, label: str) -> str:
    if not isinstance(claim, dict) or "text" not in claim:
        raise ValueError(f"contextd {label} must be an object with text")
    return f"[{label}] {claim['text']}"


def _claim_list(value: Any, label: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"contextd {label} must be a list")
    return value


def _claim_view_units(data: dict[str, Any]) -> list[str]:
    """Units for the claim briefing. Absent on the older event-feed packet."""
    units: list[str] = []
    focal = data.get("focal_task")
    if focal is None:
        focal = {}
    elif not isinstance(focal, dict):
        raise ValueError("contextd focal_task must be an object")
    intent = focal.get("intent")
    if intent is not None:
        units.append(_claim_text(intent, "intent"))
    for key, label in (
        ("constraints", "constraint"),
        ("reality", "reality"),
        ("decisions", "decision"),
        ("ruled_out", "ruled_out"),
        ("unknowns", "unknown"),
    ):
        for item in _claim_list(focal.get(key), f"focal_task.{key}"):
            units.append(_claim_text(item, label))
    nxt = focal.get("next_action")
    if nxt is not None:
        units.append(_claim_text(nxt, "next"))
    for item in _claim_list(data.get("constraints"), "constraints"):
        units.append(_claim_text(item, "constraint"))
    for item in _claim_list(data.get("passed_checks"), "passed_checks"):
        units.append(_claim_text(item, "passed"))
    open_work = data.get("open_work")
    if open_work is not None:
        if not isinstance(open_work, list):
            raise ValueError("contextd open_work must be a list")
        for work in open_work:
            if not isinstance(work, dict):
                raise ValueError("contextd open_work items must be objects")
            for key, label in (
                ("decisions", "decision"),
                ("ruled_out", "ruled_out"),
                ("unknowns", "unknown"),
                ("reality", "reality"),
            ):
                for item in _claim_list(work.get(key), f"open_work.{key}"):
                    units.append(_claim_text(item, label))
            intent = work.get("intent")
            if intent is not None:
                units.append(_claim_text(intent, "intent"))
            nxt = work.get("next_action")
            if nxt is not None:
                units.append(_claim_text(nxt, "next"))
    others = data.get("other_active_tasks")
    if others is None:
        return units
    if not isinstance(others, list):
        raise ValueError("contextd other_active_tasks must be a list")
    for item in others:
        if not isinstance(item, dict):
            raise ValueError("contextd other_active_tasks items must be objects")
        units.append(f"[other] {item.get('summary', '')}")
    return units


def split_units(packet_text: str, kind: str) -> list[str]:
    if kind == "paragraphs":
        parts = re.split(r"\n[ \t]*\n", packet_text)
    elif kind == "history":
        parts = packet_text.split(HISTORY_DELIMITER)
    elif kind == "contextd":
        return _contextd_units(packet_text)
    else:
        raise ValueError(f"unknown unit kind {kind!r}")
    return [p.strip() for p in parts if p.strip()]


def count_tokens(tokenizer: Any, text: str) -> int:
    # encode_ordinary never raises on special-token text such as "<|endoftext|>".
    encode = getattr(tokenizer, "encode_ordinary", None) or tokenizer.encode
    return len(encode(text))


@dataclass(frozen=True)
class UnitMatch:
    atoms: tuple[str, ...]
    stale_as_current: bool
    stale_marked: bool
    relevant: bool


def classify_unit(unit: str, atoms: AtomSet) -> UnitMatch:
    matched = [a for a in atoms.atoms if a.matches(unit)]
    stale = any(a.role == "stale" for a in matched)
    marked = bool(atoms.superseded.search(unit))
    stale_as_current = stale and not marked
    stale_marked = stale and marked
    # A stale claim explicitly marked superseded is load-bearing: it stops the takeover
    # agent from reviving it, so it counts as relevant for precision.
    useful = any(a.role in USEFUL_ROLES for a in matched) or stale_marked
    return UnitMatch(tuple(a.id for a in matched), stale_as_current, stale_marked,
                     useful and not stale_as_current)  # fmt: skip


def score_packet(units: list[str], atoms: AtomSet, tokenizer: Any,
                 raw_text: str | None = None) -> dict[str, Any]:  # fmt: skip
    recall_atoms = atoms.by_role(*RECALL_ROLES)
    classified = [classify_unit(u, atoms) for u in units]
    hit_ids = {atom_id for c in classified for atom_id in c.atoms}
    tokens = [count_tokens(tokenizer, u) for u in units]
    unit_tokens = sum(tokens)
    # Unit weights stay on the renderings. raw_text, when given, is the original
    # packet: contextd renderings are not what the model is shown.
    packet_tokens = count_tokens(tokenizer, raw_text) if raw_text is not None else unit_tokens
    matched = [a.id for a in atoms.atoms if a.id in hit_ids]
    missing = [a.id for a in recall_atoms if a.id not in hit_ids]
    recall = (len(recall_atoms) - len(missing)) / len(recall_atoms) if recall_atoms else 0.0
    if not units:
        recall = 0.0
    relevant = [c.relevant for c in classified]
    return {
        "units": len(units),
        "required_recall": recall,
        "matched_atoms": matched,
        "missing_atoms": missing,
        "unit_precision": (sum(relevant) / len(units)) if units else None,
        "token_precision": (
            sum(t for t, r in zip(tokens, relevant) if r) / unit_tokens if unit_tokens else None
        ),
        "stale_as_current": sum(c.stale_as_current for c in classified),
        "stale_marked": sum(c.stale_marked for c in classified),
        "unresolved_present": any(a.id in hit_ids for a in atoms.by_role("unresolved")),
        "packet_tokens": packet_tokens,
        "unit_atoms": [list(c.atoms) for c in classified],
    }
