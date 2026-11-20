"""Harness unit tests. No daemon, no binary."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from eval.harness.episode import iter_episode_paths, load_episode, parse_offset_ms
from eval.harness.isolate import repo_root
from eval.harness.score_l0 import score_l0


class OffsetTests(unittest.TestCase):
    def test_hours_and_days(self) -> None:
        self.assertEqual(parse_offset_ms("-26h"), -26 * 3_600_000)
        self.assertEqual(parse_offset_ms("-4h"), -4 * 3_600_000)
        self.assertEqual(parse_offset_ms("2m"), 120_000)
        self.assertEqual(parse_offset_ms("-5s"), -5_000)
        self.assertEqual(parse_offset_ms("-3d"), -3 * 86_400_000)
        self.assertEqual(parse_offset_ms("-2w"), -2 * 604_800_000)
        self.assertEqual(parse_offset_ms("0"), 0)

    def test_rejects_unknown_unit(self) -> None:
        with self.assertRaises(ValueError):
            parse_offset_ms("-3x")


class NeedleTests(unittest.TestCase):
    def test_required_and_forbidden(self) -> None:
        episode = _episode(
            """
name: example
level: l1
prompt: why
gold:
  required: [AtomicUsize]
  forbidden: [Stripe]
  frontier:
    hypothesis: [AtomicUsize]
  decisions:
    - text: do not introduce Redis
l0_queries:
  - query: now
history: []
"""
        )
        replies = {
            "now": {
                "intent": {"text": "investigate login"},
                "recent_activity": [
                    {"text": "hypothesis: AtomicUsize decrement race"},
                    {"text": "decision: do not introduce Redis"},
                ],
            }
        }
        result = score_l0(episode, replies)
        self.assertTrue(result.passed, result.reason)
        self.assertFalse(result.forbidden_hits["Stripe"])

    def test_focal_task_claims_count_as_the_briefing(self) -> None:
        episode = _episode(
            """
name: briefing
level: l1
prompt: why
gold:
  required: ["Keep rate limiting local"]
  forbidden: [Redis]
history: []
"""
        )
        replies = {
            "now": {
                "focal_task": {
                    "reality": [{"text": "Keep rate limiting local for v1"}],
                }
            }
        }
        result = score_l0(episode, replies)
        self.assertTrue(result.passed, result.reason)

    def test_forbidden_leak_fails(self) -> None:
        episode = _episode(
            """
name: leak
gold:
  required: [auth]
  forbidden: [Stripe]
l0_queries:
  - query: now
history: []
"""
        )
        replies = {"now": {"recent_activity": [{"text": "auth plus Stripe retry"}]}}
        result = score_l0(episode, replies)
        self.assertFalse(result.passed)
        self.assertIn("Stripe", result.reason or "")

    def test_query_echo_is_not_a_hit(self) -> None:
        episode = _episode(
            """
name: echo
gold:
  required: [AtomicUsize]
l0_queries:
  - query: search
    text: AtomicUsize
history: []
"""
        )
        replies = {
            "search:AtomicUsize": {
                "query": "AtomicUsize",
                "semantic": False,
                "matches": [],
            }
        }
        result = score_l0(episode, replies)
        self.assertFalse(result.passed, "the search request string must not score itself")
        self.assertFalse(result.required_hits["AtomicUsize"])

    def test_missing_embeddings_is_invalid_not_failed(self) -> None:
        episode = _episode(
            """
name: recall
requires_embeddings: true
gold:
  required: [Mutex]
l0_queries:
  - query: recall
    text: login
history: []
"""
        )
        replies = {"recall": {"semantic": False, "matches": []}}
        result = score_l0(episode, replies)
        self.assertTrue(result.invalid)
        self.assertFalse(result.passed)

    def test_precision_counts_relevant_items(self) -> None:
        episode = _episode(
            """
name: prec
gold:
  required: [race]
l0_queries:
  - query: now
history: []
"""
        )
        replies = {
            "now": {
                "recent_activity": [
                    {"text": "race in the counter"},
                    {"text": "edited src/db/schema.sql"},
                    {"text": "ran cargo test"},
                ]
            }
        }
        result = score_l0(episode, replies)
        self.assertEqual(result.retrieved, 3)
        self.assertEqual(result.relevant, 1)
        self.assertAlmostEqual(result.precision or 0, 1 / 3)


class EpisodeLoadTests(unittest.TestCase):
    def test_generate_expands(self) -> None:
        episode = _episode(
            """
name: noise
history:
  - session: interference
    offset: -4h
    generate:
      count: 3
      source: file_system
      payload:
        path: src/db/migrations/{i}.sql
        action: Modify
gold:
  required: []
"""
        )
        self.assertEqual(len(episode.history[0].events), 3)
        self.assertEqual(
            episode.history[0].events[2]["payload"]["path"],
            "src/db/migrations/2.sql",
        )


class CatalogTests(unittest.TestCase):
    def test_every_episode_loads(self) -> None:
        paths = iter_episode_paths(repo_root() / "eval")
        self.assertGreaterEqual(len(paths), 25)
        for path in paths:
            load_episode(path)

    def test_search_needles_are_payload_substrings(self) -> None:
        for path in iter_episode_paths(repo_root() / "eval"):
            episode = load_episode(path)
            if episode.requires_embeddings:
                continue
            blob = json.dumps(
                [event for session in episode.history for event in session.events],
                default=str,
            ).lower()
            intents = " ".join(
                session.intent or "" for session in episode.history
            ).lower()
            hay = blob + "\n" + intents
            for query in episode.l0_queries:
                if query.query not in {"search", "recall"} or not query.text:
                    continue
                self.assertIn(
                    query.text.lower(),
                    hay,
                    f"{episode.name}: {query.query} {query.text!r} is not in history",
                )


def _episode(text: str):
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
        handle.write(text)
        path = Path(handle.name)
    try:
        return load_episode(path)
    finally:
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
