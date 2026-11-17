"""Compare evidence checks on a fixed, hand-labeled set.

The labels were assigned by reading each sentence against the tool output.
Gold atoms are not an input. Overlap count is reported so it can be compared
with a check that does not treat a larger overlap as stronger support.

Usage (from eval/component0):
    python -m harness.evidence_probe
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from harness.common import EPISODES_DIR

STOP = frozenset(
    {
        "about", "after", "also", "and", "are", "because", "been", "before", "but",
        "for", "from", "have", "into", "only", "that", "than", "the", "their",
        "them", "then", "this", "with", "would",
    }
)
DENIALS = (
    "not counted", "isn't counted", "is not counted", "never counted",
    "not incremented", "does not increment", "doesn't increment",
    "stays at zero", "stay at zero", "remains at zero", "at zero", "stays at 0",
)
MARKERS = (" so ", " because ", " means ", " and ")


def tokens(text: str) -> list[str]:
    out: list[str] = []
    current: list[str] = []

    def keep(token: str) -> None:
        token = token.strip("_")
        if len(token) >= 4 and token not in STOP and token[:1].isalpha():
            out.append(token)
        for part in token.split("_"):
            if len(part) >= 4 and part not in STOP and part != token and part[:1].isalpha():
                out.append(part)

    for ch in text:
        if ch.isalnum() or ch == "_":
            current.append(ch.lower())
        elif current:
            keep("".join(current))
            current.clear()
    if current:
        keep("".join(current))
    return out


def overlap(claim: str, support: str) -> int:
    support_tokens = set(tokens(support))
    return len({token for token in tokens(claim) if token in support_tokens})


def clauses(sentence: str) -> list[str]:
    lower = sentence.lower()
    start = 0
    found: list[str] = []
    while start < len(sentence):
        remainder = lower[start:]
        hits = [(remainder.find(marker), len(marker)) for marker in MARKERS]
        hits = [(index, length) for index, length in hits if index >= 0]
        if not hits:
            end, length = len(sentence), 0
        else:
            index, length = min(hits)
            end = start + index
        clause = sentence[start:end].strip(" ,;")
        if clause:
            found.append(clause)
        if length == 0:
            break
        start = end + length
    return found


def unanchored(sentence: str, support: str) -> bool:
    support_tokens = set(tokens(support))
    for clause in clauses(sentence):
        clause_tokens = set(tokens(clause))
        if clause_tokens and clause_tokens.isdisjoint(support_tokens):
            return True
    return False


def increments(support: str) -> list[tuple[str, int]]:
    found = []
    data = support.encode()
    index = 0
    while index + 1 < len(data):
        if data[index : index + 2] == b"+=":
            name_end = index
            while name_end > 0 and chr(data[name_end - 1]).isspace():
                name_end -= 1
            name_start = name_end
            while name_start > 0 and (chr(data[name_start - 1]).isalnum() or data[name_start - 1] == ord("_")):
                name_start -= 1
            raw = support[name_start:name_end]
            name = raw.rsplit(".", 1)[-1]
            digit_at = index + 2
            while digit_at < len(data) and chr(data[digit_at]).isspace():
                digit_at += 1
            digit_start = digit_at
            while digit_at < len(data) and chr(data[digit_at]).isdigit():
                digit_at += 1
            if name and digit_at > digit_start:
                found.append((name, int(support[digit_start:digit_at])))
            index = digit_at
            continue
        index += 1
    return found


def denies(sentence: str) -> bool:
    lower = sentence.lower()
    return any(phrase in lower for phrase in DENIALS)


def polarity(sentence: str, support: str) -> bool:
    if not denies(sentence):
        return False
    return any(amount > 0 and re.search(rf"(?i)\b{re.escape(name)}\b", sentence) for name, amount in increments(support))


def idents(text: str) -> list[str]:
    return [token for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text) if len(token) >= 4]


def anchored(text: str, support: str, last: bool) -> str | None:
    support = support.lower()
    names = idents(text)
    order = reversed(names) if last else names
    for name in order:
        if name.lower() in support:
            return name.lower()
    return None


def order_conflict(sentence: str, support: str) -> bool:
    lower = sentence.lower()
    support_l = support.lower()
    for marker, before in ((" before ", True), (" after ", False)):
        start = 0
        while True:
            relative = lower[start:].find(marker)
            if relative < 0:
                break
            index = start + relative
            left = anchored(sentence[:index], support_l, True)
            right = anchored(sentence[index + len(marker) :], support_l, False)
            if left and right:
                left_at, right_at = support_l.find(left), support_l.find(right)
                if left_at >= 0 and right_at >= 0:
                    reversed_order = left_at > right_at if before else left_at < right_at
                    if reversed_order:
                        return True
            start = index + len(marker)
    return False


def structured(sentence: str, support: str) -> str:
    if re.search(r"(?i)\b(might|maybe)\b", sentence) or "could be" in sentence.lower():
        return "hypothesis"
    if not support.strip():
        return "insufficient"
    if polarity(sentence, support) or order_conflict(sentence, support):
        return "contradicted"
    if unanchored(sentence, support) or overlap(sentence, support) < 2:
        return "insufficient"
    return "supported"


def overlap_label(sentence: str, support: str, minimum: int) -> str:
    if re.search(r"(?i)\b(might|maybe)\b", sentence) or "could be" in sentence.lower():
        return "hypothesis"
    return "supported" if overlap(sentence, support) >= minimum else "insufficient"


def cases() -> list[tuple[str, str, str]]:
    history = EPISODES_DIR / "limiter-handoff" / "history" / "history.jsonl"
    idle = ""
    for line in history.read_text().splitlines():
        row = json.loads(line)
        if row.get("unit") == 17:
            idle = row["output"]
    deliver = (
        "def deliver(self, message):\n"
        "    self.retry_count += 1\n"
        "    self.open_socket()\n"
        "    self.socket.send(message)\n"
    )
    pytest_out = "python -m pytest -q tests/test_retry.py\nAssertionError: retry_count 2 != 1\nE   assert 2 == 1"
    timeout = "# deliver has no timeout handling in this function\n"
    return [
        ("supported", "The failure is retry_count 2 where the test expects 1.", pytest_out),
        ("supported", "deliver increments retry_count before open_socket, so the first send is already counted as a retry.", deliver),
        ("supported", "The timeout path is not the cause because deliver has no timeout handling.", timeout),
        ("supported", "The create-new-connection branch only counts connections that are not idle, so idle keepalive connections never count toward max_connections and the idle-eviction branch below it is never reached.", idle),
        ("contradicted", "The first send is not counted, so retry_count stays at zero until a failure.", deliver),
        ("contradicted", "open_socket runs before retry_count is incremented.", deliver),
        ("insufficient", "The retry_count before open_socket means the database pool is exhausted.", deliver),
        ("insufficient", "The database pool is exhausted and the worker queue is blocked.", "class Courier:\n"),
        ("insufficient", "The worker queue is the real cause of the stalled flush.", ""),
        ("hypothesis", "A shared counter in another service might be involved.", deliver),
    ]


def main() -> int:
    rows = cases()
    checks = {
        "overlap>=2": lambda sentence, support: overlap_label(sentence, support, 2),
        "overlap>=3": lambda sentence, support: overlap_label(sentence, support, 3),
        "overlap>=4": lambda sentence, support: overlap_label(sentence, support, 4),
        "structured": structured,
    }
    print(f"{'gold':<14} {'>=2':<14} {'>=3':<14} {'>=4':<14} {'structured'}")
    for gold, sentence, support in rows:
        preds = [check(sentence, support) for check in checks.values()]
        print(f"{gold:<14} {preds[0]:<14} {preds[1]:<14} {preds[2]:<14} {preds[3]}")
    print()
    for name, check in checks.items():
        correct = sum(check(sentence, support) == gold for gold, sentence, support in rows)
        print(f"{name:<12} {correct}/{len(rows)}")
    started = time.perf_counter()
    repeats = 2000
    for _ in range(repeats):
        for _, sentence, support in rows:
            structured(sentence, support)
    elapsed_ms = (time.perf_counter() - started) / repeats * 1000
    print(f"structured {elapsed_ms:.3f} ms for {len(rows)} cases, no model call")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
