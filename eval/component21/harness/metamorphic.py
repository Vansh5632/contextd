"""Contrastive and metamorphic development cases.

Each transformation records the expected judgment. This module does not read
the sealed suite.
"""

from __future__ import annotations

PYTHON = "def release(self, permit):\n    self.quota_left += 1\n    self.release_permit()\n"
PYTHON_RENAMED = "def release(self, permit):\n    self.seats_open += 1\n    self.grant_seat()\n"
PYTHON_REORDERED = "def release(self, permit):\n    self.release_permit()\n    self.quota_left += 1\n"
PYTHON_COMMENTS = (
    "# permit bookkeeping\n"
    "def release(self, permit):\n"
    "    # count the release\n"
    "    self.quota_left += 1\n"
    "    self.release_permit()\n"
)
JS = "function release(permit) {\n  quotaLeft += 1;\n  releasePermit();\n}\n"
GO = "func release(permit int) {\n    quotaLeft++\n    releasePermit()\n}\n"
RUST = "fn release(permit: i32) {\n    quota_left += 1;\n    release_permit();\n}\n"
SECOND = "def release(self, permit):\n    self.quota_left = 0\n    self.release_permit()\n"

CLAIM = "quota_left increases by 1 before release_permit"
RENAMED_CLAIM = "seats_open increases by 1 before grant_seat"
PARAPHRASE = "the quota left counter grows by one before the permit is released"
NEGATION = "quota_left never increases before release_permit"
QUANTITY = "quota_left increases by 50 before release_permit"
REVERSED_ORDER = "quota_left increases by 1 before release_permit"
CAUSE = "release_permit failed because quota_left increased"
CONDITIONAL = "quota_left increases by 1 only when the permit is already released"
CONDITIONAL_FLIP = "quota_left increases by 1 only when the permit is not released"
JS_CLAIM = "quotaLeft increases by 1 before releasePermit"
GO_CLAIM = "quotaLeft increases by 1 before releasePermit"
RUST_CLAIM = "quota_left increases by 1 before release_permit"
FULLWIDTH = "quota_left increases by 1 before release_permit。许可已经耗尽。"


def _tool(output, bind="source"):
    return {
        "action": "observe",
        "kind": "tool",
        "command": "sed -n 1,40p release.py",
        "cwd": "/work/pkg",
        "exit_code": 0,
        "output": output,
        "bind": bind,
    }


def _steps(output, text, extra=None):
    steps = [
        {
            "action": "observe",
            "kind": "tool",
            "command": "pytest -q",
            "cwd": "/work/pkg",
            "exit_code": 1,
            "output": "1 failed\n",
            "bind": "pytest",
        },
        _tool(output),
        {
            "action": "observe",
            "kind": "conclusion",
            "text": text,
            "bind": "conclusion",
            "support": "source",
        },
    ]
    if extra:
        steps.extend(extra)
    steps.extend(
        [
            {"action": "handoff"},
            {"action": "now"},
            {"action": "checkpoint", "name": "final"},
        ]
    )
    return steps


def _atom(text, factuality, justification):
    return {
        "id": "claim",
        "text": text,
        "factuality": factuality,
        "expect": "presented_iff_supported",
        "justification": justification,
        "provenance": "agent",
        "checkpoint": "final",
        "source_bind": "conclusion",
    }


def _case(case_id, title, output, text, factuality, justification, transformation, invariant):
    return {
        "id": case_id,
        "suite": "development",
        "failure_id": None,
        "title": title,
        "initial_class": "development",
        "judgment": "contract",
        "mode": "daemon",
        "deterministic": True,
        "needs_human_adjudication": False,
        "control": False,
        "session": case_id,
        "transformation": transformation,
        "invariant": invariant,
        "steps": _steps(output, text),
        "atoms": [_atom(text, factuality, justification)],
    }


def build_cases() -> list[dict]:
    listing = "The release function adds 1 to quota_left and then calls release_permit."
    return [
        _case(
            "d01-baseline",
            "Python baseline increment",
            PYTHON,
            CLAIM,
            "SUPPORTED",
            listing,
            "identity",
            "the judgment stays supported",
        ),
        _case(
            "d02-rename",
            "Consistent identifier rename",
            PYTHON_RENAMED,
            RENAMED_CLAIM,
            "SUPPORTED",
            "Both the claim and the listing use seats_open and grant_seat.",
            "consistent_rename",
            "a rename applied to the claim and the evidence together stays supported",
        ),
        _case(
            "d03-paraphrase",
            "Equivalent paraphrase",
            PYTHON,
            PARAPHRASE,
            "SUPPORTED",
            "Grows by one is the same change as += 1, and the permit release is release_permit.",
            "paraphrase",
            "the judgment stays supported",
        ),
        _case(
            "d04-comments",
            "Comments reordered around the same statements",
            PYTHON_COMMENTS,
            CLAIM,
            "SUPPORTED",
            "The executable statements are still an increment followed by release_permit.",
            "comment_reorder",
            "comments do not change the supported judgment",
        ),
        _case(
            "d05-whitespace",
            "Whitespace around the same statements",
            PYTHON.replace("\n", "\n\n"),
            CLAIM,
            "SUPPORTED",
            "Blank lines do not change the increment or the call.",
            "whitespace",
            "the judgment stays supported",
        ),
        _case(
            "d06-negation",
            "Negated increment",
            PYTHON,
            NEGATION,
            "CONTRADICTED",
            "The listing increments quota_left.",
            "negation",
            "the judgment changes from supported to contradicted",
        ),
        _case(
            "d07-magnitude",
            "Different numeric magnitude",
            PYTHON,
            QUANTITY,
            "CONTRADICTED",
            "The listing adds 1, not 50.",
            "numeric_magnitude",
            "the judgment changes to contradicted",
        ),
        _case(
            "d08-operation-order",
            "Reversed executable order",
            PYTHON_REORDERED,
            REVERSED_ORDER,
            "CONTRADICTED",
            "release_permit runs before the increment in this listing.",
            "reversed_operation_order",
            "reordering executable statements changes the judgment",
        ),
        _case(
            "d09-cause",
            "Unsupported causal sentence",
            PYTHON,
            CAUSE,
            "INSUFFICIENT_EVIDENCE",
            "The listing does not show a failure, and it does not say the increment caused one.",
            "reversed_cause",
            "adding a cause the listing does not show makes the evidence insufficient",
        ),
        _case(
            "d10-conditional",
            "Conditional the listing does not state",
            PYTHON,
            CONDITIONAL,
            "INSUFFICIENT_EVIDENCE",
            "The increment is unconditional in this function. The permit is not tested first.",
            "conditional_reversal",
            "a condition the listing does not contain is insufficient",
        ),
        _case(
            "d11-conditional-flip",
            "Opposite conditional",
            PYTHON,
            CONDITIONAL_FLIP,
            "INSUFFICIENT_EVIDENCE",
            "The listing has no check of whether the permit was released.",
            "conditional_reversal",
            "the opposite condition is also insufficient",
        ),
        _case(
            "d12-javascript",
            "JavaScript increment",
            JS,
            JS_CLAIM,
            "SUPPORTED",
            "quotaLeft is increased by 1 before releasePermit().",
            "language_javascript",
            "the same increment shape stays supported in JavaScript",
        ),
        _case(
            "d13-go",
            "Go increment",
            GO,
            GO_CLAIM,
            "SUPPORTED",
            "quotaLeft is incremented before releasePermit().",
            "language_go",
            "the same increment shape stays supported in Go",
        ),
        _case(
            "d14-rust",
            "Rust increment",
            RUST,
            RUST_CLAIM,
            "SUPPORTED",
            "quota_left is increased by 1 before release_permit().",
            "language_rust",
            "the same increment shape stays supported in Rust",
        ),
        _case(
            "d15-fullwidth",
            "Fullwidth punctuation and an unsupported tail",
            PYTHON,
            FULLWIDTH,
            "INSUFFICIENT_EVIDENCE",
            "The Chinese sentence says the permit is exhausted. The listing does not say that.",
            "fullwidth_punctuation",
            "the combined sentence is insufficient even though the English prefix matches",
        ),
        {
            "id": "d16-removed-evidence",
            "suite": "development",
            "failure_id": None,
            "title": "Claim whose cited output does not contain the code",
            "initial_class": "development",
            "judgment": "contract",
            "mode": "daemon",
            "deterministic": True,
            "needs_human_adjudication": False,
            "control": False,
            "session": "d16-removed-evidence",
            "transformation": "removed_evidence",
            "invariant": "without the listing, the increment claim is insufficient",
            "steps": _steps("1 failed\n", CLAIM),
            "atoms": [
                _atom(
                    CLAIM,
                    "INSUFFICIENT_EVIDENCE",
                    "The cited output is only the test summary. It does not show quota_left or release_permit.",
                )
            ],
        },
        {
            "id": "d17-conflict",
            "suite": "development",
            "failure_id": None,
            "title": "A second listing conflicts with the cited one",
            "initial_class": "development",
            "judgment": "contract",
            "mode": "daemon",
            "deterministic": True,
            "needs_human_adjudication": False,
            "control": False,
            "session": "d17-conflict",
            "transformation": "conflicting_evidence",
            "invariant": "conflicting listings make the standing increment claim insufficient",
            "steps": [
                {
                    "action": "observe",
                    "kind": "tool",
                    "command": "pytest -q",
                    "cwd": "/work/pkg",
                    "exit_code": 1,
                    "output": "1 failed\n",
                    "bind": "pytest",
                },
                _tool(PYTHON, "source"),
                _tool(SECOND, "other"),
                {
                    "action": "observe",
                    "kind": "conclusion",
                    "text": CLAIM,
                    "bind": "conclusion",
                    "support": "source",
                },
                {"action": "handoff"},
                {"action": "now"},
                {"action": "checkpoint", "name": "final"},
            ],
            "atoms": [
                _atom(
                    CLAIM,
                    "INSUFFICIENT_EVIDENCE",
                    "One listing increments quota_left and a second sets it to 0. Together they do not establish a standing increment of 1.",
                )
            ],
        },
    ]
