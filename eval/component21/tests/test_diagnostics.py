"""Reason codes separate false accepts from skipped supported claims."""

from __future__ import annotations

from harness.diagnostics import summarize_diagnostics
from harness.judge import judge_atom
from harness.shadow import AbstainingShadow, ShadowRequest, ShadowVerdict, run_shadow, shadow_requests

LISTING = "def deliver(self, message):\n    self.retry_count += 1\n    self.open_socket()\n"


def _atom(text, factuality, *, checker, established, presented=False):
    return {
        "id": text[:12],
        "text": text,
        "factuality": factuality,
        "expect": "presented_iff_supported",
        "checker": checker,
        "established": established,
        "presented_established": presented,
        "status": "fail",
    }


def test_false_accepts_are_separate_from_correct_skips() -> None:
    false_text = "retry_count never increments before open_socket"
    missed_text = "the retry counter is incremented before the socket opens"
    cases = [
        {
            "id": "f01-negation",
            "suite": "regression",
            "mode": "daemon",
            "judgment": "contract",
            "evidence_text": LISTING,
            "steps": [{"action": "observe", "event_id": "shell-1"}],
            "verifier_diagnostics": [
                {
                    "stage": "keep_clause",
                    "text": false_text,
                    "outcome": "kept",
                    "reason": "lexical_pass",
                    "relation": "supported",
                },
                {
                    "stage": "admit",
                    "text": false_text,
                    "outcome": "accepted",
                    "reason": "lexical_pass",
                    "relation": "supported",
                },
            ],
            "atoms": [_atom(false_text, "CONTRADICTED", checker="accepted", established=True, presented=True)],
        },
        {
            "id": "f03-paraphrase",
            "suite": "regression",
            "mode": "daemon",
            "judgment": "contract",
            "evidence_text": LISTING,
            "steps": [],
            "verifier_diagnostics": [
                {
                    "stage": "keep_clause",
                    "text": missed_text,
                    "outcome": "dropped",
                    "reason": "residue",
                    "relation": "insufficient",
                },
                {
                    "stage": "apply_extraction",
                    "text": missed_text,
                    "outcome": "skipped",
                    "reason": "residue",
                    "relation": "insufficient",
                },
            ],
            "atoms": [_atom(missed_text, "SUPPORTED", checker="skipped", established=False)],
        },
        {
            "id": "h01-rust-supported",
            "suite": "heldout",
            "mode": "daemon",
            "judgment": "contract",
            "evidence_text": "sealed",
            "verifier_diagnostics": [
                {
                    "stage": "admit",
                    "text": "do not use this label",
                    "outcome": "accepted",
                    "reason": "lexical_pass",
                    "relation": "supported",
                }
            ],
            "atoms": [
                _atom(
                    "do not use this label",
                    "SUPPORTED",
                    checker="accepted",
                    established=True,
                    presented=True,
                )
            ],
        },
    ]
    report = summarize_diagnostics(cases)
    assert report["heldout_excluded"] is True
    assert report["heldout_cases_ignored"] == ["h01-rust-supported"]
    accepted = report["regression"]["false_claims_accepted"]
    skipped = report["regression"]["correct_claims_skipped"]
    assert [row["case_id"] for row in accepted] == ["f01-negation"]
    assert accepted[0]["reason"] == "lexical_pass"
    assert accepted[0]["presented"] is True
    assert accepted[0]["outcome"] == "accepted"
    assert [row["case_id"] for row in skipped] == ["f03-paraphrase"]
    assert skipped[0]["reason"] == "residue"
    assert skipped[0]["outcome"] == "skipped"
    assert "h01-rust-supported" not in report["regression"]["reason_counts"]
    shadow = report["shadow"]
    assert shadow["model"] is None
    assert shadow["writes_memory"] is False
    assert {item["shadow_label"] for item in shadow["labels"]} == {"INSUFFICIENT_EVIDENCE"}
    assert all(item["writes_memory"] is False for item in shadow["labels"])
    assert any(item["deterministic_outcome"] == "accepted" for item in shadow["labels"])
    assert any(item["deterministic_reason"] == "residue" for item in shadow["labels"])


def test_a_longer_accepted_sentence_does_not_explain_the_shorter_atom() -> None:
    short = "retry_count is incremented before open_socket"
    longer = short + "。数据库连接池耗尽。"
    cases = [
        {
            "id": "f04-unicode-tail",
            "suite": "regression",
            "mode": "daemon",
            "judgment": "contract",
            "evidence_text": LISTING,
            "steps": [],
            "verifier_diagnostics": [
                {
                    "stage": "admit",
                    "text": longer,
                    "outcome": "accepted",
                    "reason": "lexical_pass",
                    "relation": "supported",
                }
            ],
            "atoms": [
                _atom(short, "SUPPORTED", checker="absent", established=False),
                _atom(longer, "INSUFFICIENT_EVIDENCE", checker="accepted", established=True, presented=True),
            ],
        }
    ]
    report = summarize_diagnostics(cases)
    skipped = report["regression"]["correct_claims_skipped"]
    accepted = report["regression"]["false_claims_accepted"]
    assert skipped[0]["text"] == short
    assert skipped[0]["reason"] is None
    assert skipped[0]["parent_reason"] == "lexical_pass"
    assert skipped[0]["parent_outcome"] == "accepted"
    assert accepted[0]["text"] == longer
    assert accepted[0]["reason"] == "lexical_pass"


def test_shadow_quote_must_come_from_the_evidence() -> None:
    class Invents:
        def classify(self, request: ShadowRequest) -> ShadowVerdict:
            del request
            return ShadowVerdict("SUPPORTED", "a sentence the tool never printed")

    request = ShadowRequest(
        claim_text="retry_count increments before open_socket",
        evidence_text=LISTING,
        evidence_ids=("shell-1",),
        deterministic_outcome="skipped",
        deterministic_reason="residue",
        stage="keep_clause",
    )
    verdict = run_shadow(Invents(), [request])[0]
    assert verdict.label == "INSUFFICIENT_EVIDENCE"
    assert verdict.evidence_quote == ""
    assert verdict.writes_memory is False


def test_cited_shadow_label_does_not_become_admission() -> None:
    class CitesListing:
        def classify(self, request: ShadowRequest) -> ShadowVerdict:
            return ShadowVerdict("CONTRADICTED", "self.retry_count += 1")

    traces = [
        {
            "stage": "admit",
            "text": "retry_count never increments before open_socket",
            "outcome": "accepted",
            "reason": "lexical_pass",
            "relation": "supported",
        }
    ]
    requests = shadow_requests(traces, LISTING, ["shell-1"])
    verdict = run_shadow(CitesListing(), requests)[0]
    assert verdict.label == "CONTRADICTED"
    assert verdict.evidence_quote == "self.retry_count += 1"
    assert verdict.writes_memory is False
    abstained = run_shadow(AbstainingShadow(), requests)[0]
    assert abstained.label == "INSUFFICIENT_EVIDENCE"


def test_diagnostics_do_not_change_a_contract_judgment() -> None:
    text = "retry_count never increments before open_socket"
    checkpoint = {
        "accepted": [{"text": text, "kind": "reality"}],
        "rejected": [],
        "skipped": [],
        "rendered": [{"text": text, "kind": "reality", "confidence": "inferred", "origin": "agent"}],
        "store_claims": [
            {
                "text": text,
                "status": "current",
                "kind": "reality",
                "confidence": "inferred",
                "origin": "agent",
            }
        ],
        "store_events": [],
        "binds": {},
        "diagnostics": [
            {
                "stage": "admit",
                "text": text,
                "outcome": "accepted",
                "reason": "lexical_pass",
                "relation": "supported",
            }
        ],
    }
    atom = {
        "id": "negation",
        "text": text,
        "factuality": "CONTRADICTED",
        "expect": "presented_iff_supported",
        "justification": "The listing increments the counter.",
    }
    judgment = judge_atom(atom, checkpoint)
    assert judgment["status"] == "fail"
    assert judgment["checker"] == "accepted"
    assert judgment["established"] is True
