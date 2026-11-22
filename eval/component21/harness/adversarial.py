"""Adversarial claims for the shadow router.

These listings are not the sealed held-out suite. A deterministic SUPPORTED
label has to survive comments, strings, branches, loops, aliases, and
truncated output. Anything else stays semantic or abstains.
"""

from __future__ import annotations

from typing import Any

from harness.router import _visible_code, route_claim

_STRAIGHT = (
    "def release(self, permit):\n"
    "    self.quota_left += 1\n"
    "    self.release_permit()\n"
)
_DECREASE = (
    "def drain(self, ticket):\n"
    "    self.ticket_count -= 1\n"
    "    self.issue_pass()\n"
)
_COMMENT_REVERSED = (
    "def release(self, permit):\n"
    "    # quota_left += 1\n"
    "    self.release_permit()\n"
    "    self.quota_left += 1\n"
)
_BLOCK_COMMENT = (
    "fn release(permit: i32) {\n"
    "    /* quota_left += 1 */\n"
    "    release_permit();\n"
    "    quota_left += 1;\n"
    "}\n"
)
_STRING_ONLY = (
    "def release(self, permit):\n"
    '    note = "quota_left += 1"\n'
    "    self.release_permit()\n"
)
_LOOP_THEN_CALL = (
    "def release(self, items):\n"
    "    for item in items:\n"
    "        self.quota_left += 1\n"
    "    self.release_permit()\n"
)
_INSIDE_LOOP = (
    "def release(self, items):\n"
    "    for item in items:\n"
    "        self.quota_left += 1\n"
    "        self.release_permit()\n"
)
_GUARDED = (
    "def pop(self):\n"
    "    if self.depth > 0:\n"
    "        self.depth -= 1\n"
    "    self.log_pop()\n"
)
_ALIAS = (
    "def release(self, permit):\n"
    "    slots = self.quota_left\n"
    "    slots += 1\n"
    "    self.release_permit()\n"
)
_TWICE = (
    "def release(self, permit):\n"
    "    self.quota_left += 1\n"
    "    self.quota_left += 1\n"
    "    self.release_permit()\n"
)
_TRUNCATED = _STRAIGHT + "[output truncated]\n"
_COMPOUND = "quota_left increases by 1 before release_permit. El cupo se agoto."


def cases() -> list[dict[str, Any]]:
    claim = "quota_left increases by 1 before release_permit"
    return [
        {"id": "straight-increment", "topic": "straight_line", "claim": claim, "evidence": _STRAIGHT, "route": "deterministic", "label": "SUPPORTED"},
        {"id": "straight-decrement", "topic": "straight_line", "claim": "ticket_count decreases by 1 before issue_pass", "evidence": _DECREASE, "route": "deterministic", "label": "SUPPORTED"},
        {"id": "comment-reversed", "topic": "comment", "claim": claim, "evidence": _COMMENT_REVERSED, "route": "deterministic", "label": "CONTRADICTED"},
        {"id": "block-comment-reversed", "topic": "comment", "claim": claim, "evidence": _BLOCK_COMMENT, "route": "deterministic", "label": "CONTRADICTED"},
        {"id": "string-literal", "topic": "string", "claim": claim, "evidence": _STRING_ONLY, "route": "semantic", "label": None},
        {"id": "loop-then-call", "topic": "loop", "claim": claim, "evidence": _LOOP_THEN_CALL, "route": "semantic", "label": None},
        {"id": "inside-loop", "topic": "loop", "claim": claim, "evidence": _INSIDE_LOOP, "route": "semantic", "label": None},
        {"id": "guarded-decrement", "topic": "branch", "claim": "depth decreases by 1 before log_pop", "evidence": _GUARDED, "route": "semantic", "label": None},
        {"id": "alias", "topic": "alias", "claim": claim, "evidence": _ALIAS, "route": "semantic", "label": None},
        {"id": "two-increments", "topic": "compound", "claim": claim, "evidence": _TWICE, "route": "semantic", "label": None},
        {"id": "later-assignment", "topic": "contradiction", "claim": claim, "evidence": _STRAIGHT + "def other(self):\n    self.quota_left = 0\n", "route": "abstain", "label": "INSUFFICIENT_EVIDENCE"},
        {"id": "truncated", "topic": "incomplete", "claim": claim, "evidence": _TRUNCATED, "route": "abstain", "label": "INSUFFICIENT_EVIDENCE"},
        {"id": "never-decreases", "topic": "negation", "claim": "ticket_count never decreases before issue_pass", "evidence": _DECREASE, "route": "deterministic", "label": "CONTRADICTED"},
        {"id": "spanish-tail", "topic": "multilingual", "claim": _COMPOUND, "evidence": _STRAIGHT, "route": "deterministic", "label": "INSUFFICIENT_EVIDENCE"},
        {"id": "empty", "topic": "missing", "claim": claim, "evidence": "   ", "route": "abstain", "label": "INSUFFICIENT_EVIDENCE"},
    ]


def evaluate() -> list[dict[str, Any]]:
    """Route every adversarial claim. SUPPORTED quotes must be executable text."""
    report = []
    for case in cases():
        decision = route_claim(case["claim"], case["evidence"])
        quote_ok = True
        if decision.label == "SUPPORTED":
            visible = _visible_code(case["evidence"])
            quote_ok = bool(decision.evidence_quote) and decision.evidence_quote in visible
        matches = decision.route == case["route"] and decision.label == case["label"]
        report.append(
            {
                "id": case["id"],
                "topic": case["topic"],
                "route": decision.route,
                "label": decision.label,
                "reason": decision.reason,
                "evidence_quote": decision.evidence_quote,
                "expected_route": case["route"],
                "expected_label": case["label"],
                "matches": matches and quote_ok,
                "writes_memory": False,
            }
        )
    return report
