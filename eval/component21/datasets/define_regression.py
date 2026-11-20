"""Regression cases for the 20 Component 2.1 failure categories.

The JSON under datasets/regression is generated from CASES. Gold factuality
describes the supplied observation, not the live repository.
"""

from __future__ import annotations

DELIVER = "def deliver(self, message):\n    self.retry_count += 1\n    self.open_socket()\n"
ATTEMPT = "def deliver(self, message):\n    self.attempt_budget += 1\n    self.open_socket()\n"
ORDER = (
    "def bump(self):\n"
    "    self.attempt_budget += 1\n\n"
    "def open_first(self):\n"
    "    self.open_socket()\n"
    "    self.bump()\n"
)
QUANT = (
    "def count_active(connections):\n"
    "    return len([item for item in connections if not item.idle])\n"
)
PARSE = "def parse_quota():\n    return 3\n"
WIDGET = "def flush(self):\n    self.widget_count += 1\n    self.flush_buffer()\n"


def tool(command, cwd, exit_code, output, bind, session=None):
    step = {
        "action": "observe",
        "kind": "tool",
        "command": command,
        "cwd": cwd,
        "exit_code": exit_code,
        "output": output,
        "bind": bind,
    }
    if session is not None:
        step["session"] = session
    return step


def note(kind, text, bind, support=None, session=None, replaces_text=None):
    step = {"action": "observe", "kind": kind, "text": text, "bind": bind}
    if support:
        step["support"] = support
    if session is not None:
        step["session"] = session
    if replaces_text:
        step["replaces_text"] = replaces_text
    return step


def protocol(listing, conclusion, bind="conclusion"):
    return [
        tool("pytest -q", "/work/pkg", 1, "1 failed\n", "pytest"),
        tool("sed -n 1,40p courier.py", "/work/pkg", 0, listing, "source"),
        note("conclusion", conclusion, bind, support="source"),
        {"action": "handoff"},
        {"action": "now"},
        {"action": "checkpoint", "name": "final"},
    ]


def atom(atom_id, text, factuality, expect, justification, **extra):
    body = {
        "id": atom_id,
        "text": text,
        "factuality": factuality,
        "expect": expect,
        "justification": justification,
        "provenance": extra.pop("provenance", "agent"),
        "checkpoint": extra.pop("checkpoint", "final"),
    }
    body.update(extra)
    return body


def case(**kwargs):
    kwargs.setdefault("suite", "regression")
    kwargs.setdefault("judgment", "contract")
    kwargs.setdefault("mode", "daemon")
    kwargs.setdefault("deterministic", kwargs["mode"] == "daemon")
    kwargs.setdefault("needs_human_adjudication", False)
    kwargs.setdefault("control", False)
    kwargs.setdefault("session", kwargs["id"])
    return kwargs


CONTROL = "retry_count increments before open_socket"
NEGATION = "retry_count never increments before open_socket"
NUMERIC = "retry_count increments by 999 before open_socket"
PARAPHRASE = "the retry counter is incremented before the socket opens"
ENGLISH = "retry_count is incremented before open_socket"
MIXED = "retry_count is incremented before open_socket。数据库连接池耗尽。"
PANIC = "Ω so retry_count increments before open_socket"
ATTEMPT_CLAIM = "attempt_budget increments before open_socket"
PREFIX = "attempt_budget increments before open_socket"
SO_CLAIM = "attempt_budget increments before open_socket so the database pool is exhausted"
SEMI_CLAIM = "attempt_budget increments before open_socket; the database pool is exhausted"
CAUSE_AB = "attempt_budget is the cause of open_socket"
CAUSE_BA = "open_socket is the cause of attempt_budget"
ORDER_CLAIM = "attempt_budget increments before open_socket"
IDLE = "counts connections if idle"
NOT_IDLE = "counts connections if not idle"
MULTI = "parse_quota increments before the assertion fails"
CONSTRAINT = "A constraint is to keep the signature unchanged."
USER_REQ = "Keep the public CLI unchanged."
WIDGET_CLAIM = "widget_count increments before flush_buffer"
HYPO = "attempt_budget might increment before open_socket"
PROBABLY = "attempt_budget probably increments before open_socket"

CASES = [
    case(
        id="f00-supported-control",
        failure_id=None,
        title="Supported control for the deliver listing",
        initial_class="control",
        control=True,
        notes="Public-protocol control recorded on 10 November 2026. Caller-supplied tool output.",
        steps=protocol(DELIVER, CONTROL),
        atoms=[
            atom(
                "supported",
                CONTROL,
                "SUPPORTED",
                "presented_iff_supported",
                "The listing executes retry_count += 1 and then calls open_socket().",
                source_bind="conclusion",
                evidence_bind="source",
                snippet="self.retry_count += 1",
            )
        ],
    ),
    case(
        id="f01-negation",
        failure_id=1,
        title="Negated increment admitted or rejected",
        initial_class="reproduced",
        predicted_violation="presented_as_reality",
        notes="Public-protocol case negation_false_admit.",
        steps=protocol(DELIVER, NEGATION),
        atoms=[
            atom(
                "negation",
                NEGATION,
                "CONTRADICTED",
                "presented_iff_supported",
                "The listing increments retry_count. The conclusion says it never increments.",
                source_bind="conclusion",
                evidence_bind="source",
                snippet="self.retry_count += 1",
            )
        ],
    ),
    case(
        id="f02-numeric",
        failure_id=2,
        title="Wrong increment magnitude",
        initial_class="reproduced",
        predicted_violation="presented_as_reality",
        notes="Public-protocol case numeric_false_admit.",
        steps=protocol(DELIVER, NUMERIC),
        atoms=[
            atom(
                "magnitude",
                NUMERIC,
                "CONTRADICTED",
                "presented_iff_supported",
                "The listing adds 1. The conclusion says the increment is 999.",
                source_bind="conclusion",
                evidence_bind="source",
                snippet="self.retry_count += 1",
            )
        ],
    ),
    case(
        id="f03-paraphrase",
        failure_id=3,
        title="Paraphrase of a supported increment",
        initial_class="reproduced",
        predicted_violation="not_presented",
        notes="Public-protocol case paraphrase_false_reject.",
        steps=protocol(DELIVER, PARAPHRASE),
        atoms=[
            atom(
                "paraphrase",
                PARAPHRASE,
                "SUPPORTED",
                "presented_iff_supported",
                "Counter and socket are the same objects as retry_count and open_socket in this listing.",
                source_bind="conclusion",
                evidence_bind="source",
                snippet="self.retry_count += 1",
            )
        ],
    ),
    case(
        id="f04-unicode-tail",
        failure_id=4,
        title="Non-ASCII tail must not inherit support",
        initial_class="reproduced",
        predicted_violations=["presented_as_reality", "not_presented"],
        notes="Public-protocol case unicode_hidden_mechanism. The Chinese sentence says the database pool is exhausted.",
        steps=protocol(DELIVER, MIXED),
        atoms=[
            atom(
                "english",
                ENGLISH,
                "SUPPORTED",
                "presented_iff_supported",
                "The English clause matches the listing and can stand on its own.",
                source_bind="conclusion",
                evidence_bind="source",
                snippet="self.retry_count += 1",
            ),
            atom(
                "whole",
                MIXED,
                "INSUFFICIENT_EVIDENCE",
                "presented_iff_supported",
                "The listing does not show a database connection pool. The combined text must not be one established reality.",
                source_bind="conclusion",
                evidence_bind="source",
                snippet="self.retry_count += 1",
            ),
        ],
    ),
    case(
        id="f05-unicode-panic",
        failure_id=5,
        title="Unicode casefold must not drop the query handler",
        initial_class="reproduced",
        predicted_violation="handler_unavailable",
        notes="Public-protocol case unicode_casefold. Omega lowercases to a longer UTF-8 sequence.",
        steps=protocol(DELIVER, PANIC),
        atoms=[
            atom(
                "handler",
                PANIC,
                "SUPPORTED",
                "handler_available",
                "The request handler must accept, reject, or skip this note without panicking.",
            )
        ],
    ),
    case(
        id="f06-execution-order",
        failure_id=6,
        title="Text order is not execution order",
        initial_class="source_analysis",
        predicted_violation="presented_as_reality",
        notes="open_first calls open_socket before bump. The increment appears earlier only as a function definition.",
        steps=protocol(ORDER, ORDER_CLAIM),
        atoms=[
            atom(
                "order",
                ORDER_CLAIM,
                "CONTRADICTED",
                "presented_iff_supported",
                "In open_first the socket call is executed before bump, which is where attempt_budget increases.",
                source_bind="conclusion",
                evidence_bind="source",
                snippet="self.open_socket()",
            )
        ],
    ),
    case(
        id="f07-quantifier",
        failure_id=7,
        title="Idle quantifier reversal",
        initial_class="source_analysis",
        predicted_violations=["presented_as_reality", "not_presented"],
        notes="The function counts items that are not idle.",
        steps=[
            tool("pytest -q", "/work/pkg", 1, "1 failed\n", "pytest"),
            tool("sed -n 1,40p pool.py", "/work/pkg", 0, QUANT, "source"),
            note("conclusion", IDLE, "idle", support="source"),
            note("conclusion", NOT_IDLE, "active", support="source"),
            {"action": "handoff"},
            {"action": "now"},
            {"action": "checkpoint", "name": "final"},
        ],
        atoms=[
            atom(
                "idle",
                IDLE,
                "CONTRADICTED",
                "presented_iff_supported",
                "The filter keeps connections whose idle flag is false.",
                source_bind="idle",
                evidence_bind="source",
                snippet="not item.idle",
            ),
            atom(
                "not-idle",
                NOT_IDLE,
                "SUPPORTED",
                "presented_iff_supported",
                "The comprehension counts items for which idle is not true.",
                source_bind="active",
                evidence_bind="source",
                snippet="not item.idle",
            ),
        ],
    ),
    case(
        id="f08-causality",
        failure_id=8,
        title="Shared names do not establish cause",
        initial_class="source_analysis",
        predicted_violation="presented_as_reality",
        notes="The listing shows an increment and a call. It does not say either one causes the other.",
        steps=[
            tool("pytest -q", "/work/pkg", 1, "1 failed\n", "pytest"),
            tool("sed -n 1,40p courier.py", "/work/pkg", 0, ATTEMPT, "source"),
            note("conclusion", CAUSE_AB, "forward", support="source"),
            note("conclusion", CAUSE_BA, "reverse", support="source"),
            {"action": "handoff"},
            {"action": "now"},
            {"action": "checkpoint", "name": "final"},
        ],
        atoms=[
            atom(
                "forward",
                CAUSE_AB,
                "INSUFFICIENT_EVIDENCE",
                "presented_iff_supported",
                "An increment followed by a call does not establish that the increment causes the call.",
                source_bind="forward",
                evidence_bind="source",
                snippet="self.attempt_budget += 1",
            ),
            atom(
                "reverse",
                CAUSE_BA,
                "INSUFFICIENT_EVIDENCE",
                "presented_iff_supported",
                "The same listing does not establish the opposite cause either.",
                source_bind="reverse",
                evidence_bind="source",
                snippet="self.attempt_budget += 1",
            ),
        ],
    ),
    case(
        id="f09-semicolon",
        failure_id=9,
        title="Semicolon does not license an unsupported tail",
        initial_class="source_analysis",
        predicted_violations=["presented_as_reality", "not_presented"],
        steps=protocol(ATTEMPT, SEMI_CLAIM),
        atoms=[
            atom(
                "prefix",
                PREFIX,
                "SUPPORTED",
                "presented_iff_supported",
                "The increment-before-call clause is supported by the listing.",
                source_bind="conclusion",
                evidence_bind="source",
                snippet="self.attempt_budget += 1",
            ),
            atom(
                "whole",
                SEMI_CLAIM,
                "INSUFFICIENT_EVIDENCE",
                "presented_iff_supported",
                "Nothing in the listing says the database pool is exhausted.",
                source_bind="conclusion",
                evidence_bind="source",
                snippet="self.attempt_budget += 1",
            ),
        ],
    ),
    case(
        id="f09-so-split",
        failure_id=9,
        title="Clause marker so separates an unsupported tail",
        initial_class="source_analysis",
        predicted_violation="presented_as_reality",
        notes="A correct split keeps the prefix and drops the unsupported tail. Admitting the whole sentence violates the contract.",
        steps=protocol(ATTEMPT, SO_CLAIM),
        atoms=[
            atom(
                "prefix",
                PREFIX,
                "SUPPORTED",
                "presented_iff_supported",
                "The prefix restates the listing.",
                source_bind="conclusion",
                evidence_bind="source",
                snippet="self.attempt_budget += 1",
            ),
            atom(
                "whole",
                SO_CLAIM,
                "INSUFFICIENT_EVIDENCE",
                "presented_iff_supported",
                "The tail about the database pool is not in the listing.",
                source_bind="conclusion",
                evidence_bind="source",
                snippet="self.attempt_budget += 1",
            ),
        ],
    ),
    case(
        id="f10-compaction",
        failure_id=10,
        title="Compaction must not leave missing output looking verified",
        initial_class="source_analysis",
        predicted_violation="evidence_missing_but_presented",
        notes=(
            "The long tool output is eligible for summarization. "
            "This run asks only that missing output not be presented as established support. "
            "It does not claim an already-admitted claim was observed surviving compaction; "
            "enrichment can remove the output before the first handoff."
        ),
        steps=[
            tool("pytest -q", "/work/pkg", 1, "1 failed\n", "pytest"),
            tool(
                "sed -n 1,80p courier.py",
                "/work/pkg",
                0,
                ATTEMPT + "\n" + ("pad " * 200),
                "source",
            ),
            note("conclusion", ATTEMPT_CLAIM, "conclusion", support="source"),
            {"action": "handoff"},
            {"action": "now"},
            {"action": "wait_compaction", "bind": "source", "timeout_ms": 15000},
            {"action": "handoff"},
            {"action": "now"},
            {"action": "checkpoint", "name": "final"},
        ],
        atoms=[
            atom(
                "after",
                ATTEMPT_CLAIM,
                "INSUFFICIENT_EVIDENCE",
                "evidence_incomplete",
                "Once the output field is gone, the increment claim must not stay an established reality.",
                evidence_bind="source",
                when_presented="evidence_missing_but_presented",
                source_bind="conclusion",
            )
        ],
    ),
    case(
        id="f11-truncation",
        failure_id=11,
        title="A truncated prefix is incomplete evidence",
        initial_class="source_analysis",
        predicted_violation="truncated_prefix_admitted",
        notes="The retained prefix shows an increment of 1. The omitted tail sets the counter back to zero. The prefix alone must not verify the increment.",
        steps=protocol(
            "self.retry_count += 1\n" + ("context line\n" * 220) + "self.retry_count = 0\n",
            "retry_count increments",
        ),
        atoms=[
            atom(
                "prefix-only",
                "retry_count increments",
                "INSUFFICIENT_EVIDENCE",
                "evidence_incomplete",
                "Tool output keeps at most 200 lines. The later reset is past that cut, so the prefix is not the whole program.",
                evidence_bind="source",
                when_presented="truncated_prefix_admitted",
                source_bind="conclusion",
            )
        ],
    ),
    case(
        id="f12-multi-evidence",
        failure_id=12,
        title="One cited output cannot verify a two-source claim",
        initial_class="contract_limitation",
        predicted_violation="presented_as_reality",
        notes="observe accepts one support_event_id. The test result is stored and is not the cited output.",
        steps=[
            tool("pytest -q", "/work/pkg", 1, "AssertionError: quota drift\n", "test"),
            tool("sed -n 1,20p quota.py", "/work/pkg", 0, PARSE, "source"),
            note("conclusion", MULTI, "conclusion", support="source"),
            {"action": "handoff"},
            {"action": "now"},
            {"action": "checkpoint", "name": "final"},
        ],
        atoms=[
            atom(
                "combined",
                MULTI,
                "INSUFFICIENT_EVIDENCE",
                "presented_iff_supported",
                "The cited listing returns 3 and does not mention an assertion. The assertion text is a different event.",
                source_bind="conclusion",
                evidence_bind="source",
                snippet="return 3",
            )
        ],
    ),
    case(
        id="f13-wrong-task",
        failure_id=13,
        title="Same command in two directories stays two tasks",
        initial_class="source_analysis",
        predicted_violation="wrong_task",
        steps=[
            tool("pytest -q", "/work/alpha", 1, "alpha_budget += 1\n", "alpha"),
            tool("pytest -q", "/work/beta", 1, "beta_budget += 1\n", "beta"),
            note("conclusion", "beta_budget increments", "conclusion", support="beta"),
            {"action": "handoff"},
            {"action": "now"},
            {"action": "checkpoint", "name": "final"},
        ],
        atoms=[
            atom(
                "task",
                "beta_budget increments",
                "SUPPORTED",
                "task_matches",
                "The cited output is the command that ran in /work/beta. The claim belongs on that task.",
                task_bind="beta",
                source_bind="conclusion",
                evidence_bind="beta",
                snippet="beta_budget += 1",
            )
        ],
    ),
    case(
        id="f13-empty-session",
        failure_id=13,
        title="Missing session ids must not merge directories",
        initial_class="source_analysis",
        predicted_violation="wrong_task",
        notes="Both observations omit the session id.",
        session="",
        steps=[
            tool("pytest -q", "/work/left", 1, "left_budget += 1\n", "left", session=""),
            tool("pytest -q", "/work/right", 1, "right_budget += 1\n", "right", session=""),
            note("conclusion", "right_budget increments", "conclusion", support="right", session=""),
            {"action": "handoff"},
            {"action": "now"},
            {"action": "checkpoint", "name": "final"},
        ],
        atoms=[
            atom(
                "task",
                "right_budget increments",
                "SUPPORTED",
                "task_matches",
                "The cited command ran in /work/right. An empty session id does not make it the /work/left task.",
                task_bind="right",
                source_bind="conclusion",
                evidence_bind="right",
                snippet="right_budget += 1",
            )
        ],
    ),
    case(
        id="f14-author-constraint",
        failure_id=14,
        title="The word constraint does not make an agent note a user requirement",
        initial_class="source_analysis",
        predicted_violation="origin_user",
        steps=[
            tool("pytest -q", "/work/pkg", 1, "1 failed\n", "pytest"),
            note("conclusion", CONSTRAINT, "conclusion"),
            {"action": "handoff"},
            {"action": "now"},
            {"action": "checkpoint", "name": "final"},
        ],
        atoms=[
            atom(
                "author",
                CONSTRAINT,
                "INSUFFICIENT_EVIDENCE",
                "author_is",
                "The note was submitted as an agent conclusion. The speaker metadata, not the word constraint, decides authorship.",
                provenance="agent",
                source_bind="conclusion",
            )
        ],
    ),
    case(
        id="f14-user-requirement",
        failure_id=14,
        title="A user requirement outside the small normative list",
        initial_class="source_analysis",
        predicted_violation="missing_claim",
        steps=[
            tool("pytest -q", "/work/pkg", 1, "1 failed\n", "pytest"),
            note("user", USER_REQ, "conclusion"),
            {"action": "handoff"},
            {"action": "now"},
            {"action": "checkpoint", "name": "final"},
        ],
        atoms=[
            atom(
                "user-requirement",
                USER_REQ,
                "SUPPORTED",
                "author_is",
                "The note was submitted with user speaker metadata. It is a requirement even though it does not mention a signature.",
                provenance="user",
                source_bind="conclusion",
            )
        ],
    ),
    case(
        id="f15-stale-after-change",
        failure_id=15,
        title="A passing command does not keep an old finding current",
        initial_class="source_analysis",
        predicted_violation="stale_current",
        notes="The unrelated widget finding stays current. A pass does not erase every earlier finding; it does retire this one because the cited source changed and the command passed.",
        steps=[
            tool("go test", "/work/other", 1, WIDGET, "other_tool", session="other"),
            note("conclusion", WIDGET_CLAIM, "other_claim", support="other_tool", session="other"),
            tool("pytest -q", "/work/app", 1, ATTEMPT, "fail_a", session="app"),
            note("conclusion", HYPO, "hypo", support="fail_a", session="app"),
            note("conclusion", ATTEMPT_CLAIM, "finding", support="fail_a", session="app"),
            {"action": "handoff"},
            {"action": "now"},
            {"action": "checkpoint", "name": "after_rev_a"},
            tool("pytest -q", "/work/app", 0, "1 passed\n", "pass_b", session="app"),
            tool(
                "sed -n 1,40p courier.py",
                "/work/app",
                0,
                "def deliver(self, message):\n    self.open_socket()\n",
                "source_b",
                session="app",
            ),
            {"action": "handoff"},
            {"action": "now"},
            {"action": "checkpoint", "name": "after_pass"},
            note("retract", ATTEMPT_CLAIM, "retract", session="app"),
            {"action": "handoff"},
            {"action": "now"},
            {"action": "checkpoint", "name": "after_correction"},
            {"action": "restart"},
            {"action": "now"},
            {"action": "checkpoint", "name": "final"},
        ],
        atoms=[
            atom(
                "hypothesis",
                HYPO,
                "INSUFFICIENT_EVIDENCE",
                "must_be_hypothesis",
                "The note says the budget might increment. That is a hypothesis, not an established cause.",
                checkpoint="after_rev_a",
                source_bind="hypo",
            ),
            atom(
                "finding",
                ATTEMPT_CLAIM,
                "SUPPORTED",
                "presented_iff_supported",
                "Revision A of the cited output increments attempt_budget before open_socket.",
                checkpoint="after_rev_a",
                source_bind="finding",
                evidence_bind="fail_a",
                snippet="self.attempt_budget += 1",
            ),
            atom(
                "stale",
                ATTEMPT_CLAIM,
                "INSUFFICIENT_EVIDENCE",
                "must_not_be_current",
                "The command now passes and the new source listing no longer increments attempt_budget. The old finding needs revalidation.",
                checkpoint="after_pass",
            ),
            atom(
                "unrelated",
                WIDGET_CLAIM,
                "SUPPORTED",
                "must_stay_current",
                "The widget task was not changed by the courier command.",
                checkpoint="after_pass",
                metric_role="unrelated_retention",
            ),
            atom(
                "corrected",
                ATTEMPT_CLAIM,
                "INSUFFICIENT_EVIDENCE",
                "must_be_superseded",
                "A later note retracts the old finding. Client submission order is the ordering oracle.",
                checkpoint="after_correction",
                metric_role="supersession",
            ),
            atom(
                "restart",
                ATTEMPT_CLAIM,
                "INSUFFICIENT_EVIDENCE",
                "must_be_superseded",
                "The retraction must still hold after the daemon replays its log.",
                checkpoint="final",
                metric_role="restart_state",
            ),
            atom(
                "unrelated-restart",
                WIDGET_CLAIM,
                "SUPPORTED",
                "must_stay_current",
                "Restart must not drop the unrelated widget finding.",
                checkpoint="final",
                metric_role="unrelated_retention",
            ),
        ],
    ),
    case(
        id="f15-done-reopen",
        failure_id=15,
        title="A later failure after a pass, task status left open",
        initial_class="open",
        judgment="open_contract",
        notes="Whether a Done task should become Active again is an unresolved product contract. The run records task status and does not score it.",
        steps=[
            tool("pytest -q", "/work/pkg", 1, "1 failed\n", "fail"),
            tool("pytest -q", "/work/pkg", 0, "1 passed\n", "ok"),
            tool("pytest -q", "/work/pkg", 1, "1 failed\n", "again"),
            {"action": "now"},
            {"action": "checkpoint", "name": "final"},
        ],
        atoms=[],
    ),
    case(
        id="f16-cross-task-retract",
        failure_id=16,
        title="A retraction in one task must not retire another task",
        initial_class="source_analysis",
        predicted_violation="superseded_other_task",
        steps=[
            tool("pytest -q", "/work/alpha", 1, "alpha_budget += 1\n", "alpha_tool", session="alpha"),
            note("conclusion", "alpha_budget increments", "alpha_claim", support="alpha_tool", session="alpha"),
            tool("pytest -q", "/work/beta", 1, "beta_budget += 1\n", "beta_tool", session="beta"),
            note("conclusion", "beta_budget increments", "beta_claim", support="beta_tool", session="beta"),
            {"action": "handoff"},
            note("retract", "alpha_budget increments", "retract", session="beta"),
            {"action": "handoff"},
            {"action": "now"},
            {"action": "checkpoint", "name": "final"},
        ],
        atoms=[
            atom(
                "alpha",
                "alpha_budget increments",
                "SUPPORTED",
                "must_stay_current",
                "The retract note was submitted in the beta session. It must not supersede the alpha claim.",
                metric_role="cross_task",
                evidence_bind="alpha_tool",
                snippet="alpha_budget += 1",
            ),
            atom(
                "beta",
                "beta_budget increments",
                "SUPPORTED",
                "must_stay_current",
                "The beta claim was not retracted. It stays on the beta task.",
                metric_role="unrelated_retention",
                evidence_bind="beta_tool",
                snippet="beta_budget += 1",
            ),
        ],
    ),
    case(
        id="f16-restatement",
        failure_id=16,
        title="A retracted claim can be stated again",
        initial_class="source_analysis",
        predicted_violation="duplicate_blocks_restatement",
        steps=[
            tool("pytest -q", "/work/pkg", 1, "1 failed\n", "pytest"),
            tool("sed -n 1,40p courier.py", "/work/pkg", 0, ATTEMPT, "source"),
            note("conclusion", ATTEMPT_CLAIM, "first", support="source"),
            {"action": "handoff"},
            note("retract", ATTEMPT_CLAIM, "retract"),
            {"action": "handoff"},
            note("conclusion", ATTEMPT_CLAIM, "again", support="source"),
            {"action": "handoff"},
            {"action": "now"},
            {"action": "checkpoint", "name": "final"},
        ],
        atoms=[
            atom(
                "restated",
                ATTEMPT_CLAIM,
                "SUPPORTED",
                "presented_iff_supported",
                "The listing still supports the increment, and the earlier copy was retracted before this restatement.",
                source_bind="again",
                evidence_bind="source",
                snippet="self.attempt_budget += 1",
            )
        ],
    ),
    case(
        id="f17-timestamp-order",
        failure_id=17,
        title="A wall-clock correction must survive replay",
        initial_class="source_analysis",
        predicted_violation="correction_lost",
        notes="Handoff stamps extracted claims at max(timestamp)+1000ms. The user retract uses wall-clock time. Replay order is not the client order.",
        steps=[
            tool("pytest -q", "/work/pkg", 1, "1 failed\n", "pytest"),
            tool("sed -n 1,40p courier.py", "/work/pkg", 0, ATTEMPT, "source"),
            note("conclusion", ATTEMPT_CLAIM, "conclusion", support="source"),
            {"action": "handoff"},
            {"action": "user_claim", "kind": "retract", "text_bind": "conclusion"},
            {"action": "restart"},
            {"action": "now"},
            {"action": "checkpoint", "name": "final"},
        ],
        atoms=[
            atom(
                "replay",
                ATTEMPT_CLAIM,
                "INSUFFICIENT_EVIDENCE",
                "must_be_superseded",
                "The client retracted the claim after it was admitted. Replay must not apply that retract before the claim exists.",
                metric_role="restart_state",
                evidence_bind="source",
                snippet="self.attempt_budget += 1",
            )
        ],
    ),
    case(
        id="f18-hedge-might",
        failure_id=18,
        title="Might remains a hypothesis",
        initial_class="source_analysis",
        predicted_violations=["presented_as_reality", "uncertainty_dropped"],
        steps=protocol(ATTEMPT, HYPO),
        atoms=[
            atom(
                "might",
                HYPO,
                "INSUFFICIENT_EVIDENCE",
                "must_be_hypothesis",
                "The note says the budget might increment. The listing does not turn that into a settled fact by itself, and the uncertainty should be kept.",
                source_bind="conclusion",
            )
        ],
    ),
    case(
        id="f18-hedge-probably",
        failure_id=18,
        title="Probably is also uncertainty",
        initial_class="source_analysis",
        predicted_violations=["presented_as_reality", "uncertainty_dropped"],
        steps=protocol(ATTEMPT, PROBABLY),
        atoms=[
            atom(
                "probably",
                PROBABLY,
                "INSUFFICIENT_EVIDENCE",
                "must_be_hypothesis",
                "Probably is weaker than an established fact. Dropping it or storing it as reality both miss the uncertainty.",
                source_bind="conclusion",
            )
        ],
    ),
    case(
        id="f18-trim-budget",
        failure_id=18,
        title="Renderer trim order is not exposed",
        initial_class="source_analysis",
        mode="documented",
        deterministic=False,
        blocked_reason="unobservable",
        notes="context_now has no token-budget parameter, so trim order cannot be forced through the public protocol.",
        steps=[],
        atoms=[],
    ),
    case(
        id="f19-note-claim",
        failure_id=19,
        title="Direct agent claim bypasses the evidence gate",
        initial_class="contract_limitation",
        judgment="open_contract",
        notes="agent_claim validates origin and kind and does not run evidence_relation. Admission is not verification. The product contract for this route is unresolved.",
        steps=[
            tool("pytest -q", "/work/pkg", 1, "1 failed\n", "pytest"),
            {"action": "agent_claim", "kind": "reality", "text": "the pool is exhausted", "confidence": "inferred"},
            {"action": "now"},
            {"action": "checkpoint", "name": "final"},
        ],
        atoms=[
            atom(
                "bypass",
                "the pool is exhausted",
                "INSUFFICIENT_EVIDENCE",
                "presented_iff_supported",
                "No tool output mentions a pool. Recording the route is required; calling the claim verified is not.",
            )
        ],
    ),
    case(
        id="f19-shell-hook",
        failure_id=19,
        title="Shell hooks do not capture stdout",
        initial_class="contract_limitation",
        mode="documented",
        deterministic=False,
        blocked_reason="open_contract",
        notes="The installed shell hook sends command, cwd, and exit code. It cannot supply the output a semantic checker would need. This is a capture limit, not a verifier defect.",
        steps=[],
        atoms=[],
    ),
    case(
        id="f20-regex-gap",
        failure_id=20,
        title="Regex atom recall is not factuality",
        initial_class="source_analysis",
        mode="grader",
        deterministic=True,
        notes="A required-atom style regex matches a packet that also states a contradiction. The atomic judge flags the contradiction.",
        steps=[],
        atoms=[],
    ),
]
