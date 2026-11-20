"""Episode YAML: the only thing the runner special-cases is the schema."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover - exercised in CI via requirements
    raise SystemExit(
        "PyYAML is required. Install with: pip install -r eval/requirements.txt"
    ) from exc

OFFSET_UNITS_MS = {
    "s": 1_000,
    "m": 60_000,
    "h": 3_600_000,
    "d": 86_400_000,
    "w": 604_800_000,
}

NOTE_KINDS = frozenset(
    {"finding", "ruled_out", "hypothesis", "decision", "next_step"}
)


def parse_offset_ms(spec: str | int | None) -> int:
    """Parse `-26h`, `+2m`, `0`, or an integer millisecond delta."""
    if spec is None or spec == "" or spec == "now":
        return 0
    if isinstance(spec, int):
        return spec
    text = str(spec).strip()
    if text in {"0", "+0", "-0"}:
        return 0
    sign = -1 if text.startswith("-") else 1
    if text[0] in "+-":
        text = text[1:]
    if not text:
        raise ValueError(f"empty offset after sign: {spec!r}")
    unit = text[-1]
    if unit not in OFFSET_UNITS_MS:
        raise ValueError(f"unknown offset unit in {spec!r}; use s/m/h/d/w")
    value = int(text[:-1])
    return sign * value * OFFSET_UNITS_MS[unit]


@dataclass
class L0Query:
    query: str
    text: str | None = None
    limit: int | None = None

    def wire(self) -> dict[str, Any]:
        body: dict[str, Any] = {"query": self.query}
        if self.text:
            body["text"] = self.text
        if self.limit is not None:
            body["limit"] = self.limit
        return body


@dataclass
class Gold:
    required: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    frontier: dict[str, list[str]] = field(default_factory=dict)
    decisions: list[dict[str, str]] = field(default_factory=list)
    expected_tools: list[str] = field(default_factory=list)


@dataclass
class Session:
    name: str
    offset_ms: int
    intent: str | None
    events: list[dict[str, Any]]


@dataclass
class Episode:
    path: Path
    name: str
    level: str
    category: str
    restraint: bool
    requires_embeddings: bool
    repo_kind: str
    repo_path: str | None
    repo_commit: str | None
    history: list[Session]
    prompt: str
    gold: Gold
    l0_queries: list[L0Query]
    layers: list[int]
    arms: list[str]
    cheat_commands: list[str]
    validation_command: str | None
    skip_ci: bool


def load_episode(path: Path) -> Episode:
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"{path} is not a mapping")
    gold_raw = raw.get("gold") or {}
    queries = raw.get("l0_queries")
    if not queries:
        queries = [{"query": "now"}]
    history = []
    for index, session in enumerate(raw.get("history") or []):
        events = list(session.get("events") or [])
        generated = session.get("generate")
        if generated:
            events.extend(expand_generate(generated, index))
        history.append(
            Session(
                name=str(session.get("session", f"session-{index}")),
                offset_ms=parse_offset_ms(session.get("offset")),
                intent=session.get("intent"),
                events=events,
            )
        )
    repo = raw.get("repo") or {}
    validation = raw.get("validation") or {}
    return Episode(
        path=path,
        name=str(raw["name"]),
        level=str(raw.get("level", "l1")),
        category=str(raw.get("category", "uncategorized")),
        restraint=bool(raw.get("restraint", False)),
        requires_embeddings=bool(raw.get("requires_embeddings", False)),
        repo_kind=str(repo.get("kind", "fixture")),
        repo_path=repo.get("path"),
        repo_commit=repo.get("commit"),
        history=history,
        prompt=str(raw.get("prompt", "")),
        gold=Gold(
            required=list(gold_raw.get("required") or []),
            forbidden=list(gold_raw.get("forbidden") or []),
            frontier={
                key: list(values)
                for key, values in (gold_raw.get("frontier") or {}).items()
            },
            decisions=list(gold_raw.get("decisions") or []),
            expected_tools=list(gold_raw.get("expected_tools") or []),
        ),
        l0_queries=[
            L0Query(
                query=item["query"],
                text=item.get("text"),
                limit=item.get("limit"),
            )
            for item in queries
        ],
        layers=[int(layer) for layer in (raw.get("layers") or [0])],
        arms=list(raw.get("arms") or ["none", "dump", "mcp"]),
        cheat_commands=list(validation.get("cheat_commands") or []),
        validation_command=validation.get("command"),
        skip_ci=bool(raw.get("skip_ci", False)),
    )


def expand_generate(spec: dict[str, Any], session_index: int) -> list[dict[str, Any]]:
    count = int(spec.get("count", 0))
    source = spec.get("source", "file_system")
    template = spec.get("payload") or {}
    events = []
    for i in range(count):
        payload = _render_template(template, i=i, session=session_index)
        events.append({"source": source, "payload": payload})
    return events


def _render_template(value: Any, **subs: Any) -> Any:
    if isinstance(value, str):
        rendered = value
        for key, item in subs.items():
            rendered = rendered.replace("{" + key + "}", str(item))
        return rendered
    if isinstance(value, dict):
        return {key: _render_template(item, **subs) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_template(item, **subs) for item in value]
    return value


def iter_episode_paths(root: Path) -> list[Path]:
    episodes = root / "episodes"
    return sorted(episodes.rglob("*.yaml")) if episodes.exists() else []
