"""Shadow semantic verifier.

The classifier sees claims the deterministic checker accepted and claims it
skipped. A verdict never writes memory. This module does not call a model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

LABELS = frozenset({"SUPPORTED", "CONTRADICTED", "INSUFFICIENT_EVIDENCE"})
STAGE_RANK = {"admit": 3, "apply_extraction": 2, "keep_clause": 1}
OUTCOME_RANK = {
    "accepted": 2,
    "extracted": 2,
    "kept": 2,
    "rejected": 1,
    "skipped": 0,
    "dropped": 0,
}


@dataclass(frozen=True)
class ShadowRequest:
    claim_text: str
    evidence_text: str
    evidence_ids: tuple[str, ...]
    deterministic_outcome: str
    deterministic_reason: str
    stage: str


@dataclass(frozen=True)
class ShadowVerdict:
    label: str
    evidence_quote: str
    writes_memory: bool = False

    def __post_init__(self) -> None:
        if self.label not in LABELS:
            raise ValueError(f"unknown shadow label {self.label}")
        if self.writes_memory:
            raise ValueError("a shadow verdict cannot write memory")


class ShadowClassifier(Protocol):
    def classify(self, request: ShadowRequest) -> ShadowVerdict: ...


class AbstainingShadow:
    """Abstain on every candidate. No model call and no memory write."""

    def classify(self, request: ShadowRequest) -> ShadowVerdict:
        del request
        return ShadowVerdict("INSUFFICIENT_EVIDENCE", "")


def _rank(trace: dict) -> tuple[int, int]:
    return (
        STAGE_RANK.get(str(trace.get("stage") or ""), 0),
        OUTCOME_RANK.get(str(trace.get("outcome") or ""), 0),
    )


def shadow_requests(
    traces: list[dict],
    evidence_by_event: dict[str, str],
) -> list[ShadowRequest]:
    """Accepted claims and skipped or dropped clauses.

    An accepted row outranks a later duplicate skip of the same text. Evidence
    is the output of that row's ``support_event_id``. A row with no cited shell
    event gets empty evidence.
    """
    chosen: list[dict] = []
    for trace in traces:
        stage = str(trace.get("stage") or "")
        outcome = str(trace.get("outcome") or "")
        text = str(trace.get("text") or "")
        interesting = (
            stage == "admit"
            and outcome in {"accepted", "rejected", "skipped"}
            or stage == "apply_extraction"
            and outcome in {"skipped", "extracted"}
            or stage == "keep_clause"
            and outcome == "dropped"
        )
        if not interesting or not text:
            continue
        for index, existing in enumerate(chosen):
            if existing.get("text") == text:
                if _rank(trace) > _rank(existing):
                    chosen[index] = trace
                break
        else:
            chosen.append(trace)
    requests: list[ShadowRequest] = []
    for trace in chosen:
        support_id = trace.get("support_event_id")
        if support_id:
            evidence = evidence_by_event.get(str(support_id), "")
            ids = (str(support_id),)
        else:
            evidence = ""
            ids = ()
        requests.append(
            ShadowRequest(
                claim_text=str(trace.get("text") or ""),
                evidence_text=evidence,
                evidence_ids=ids,
                deterministic_outcome=str(trace.get("outcome") or ""),
                deterministic_reason=str(trace.get("reason") or ""),
                stage=str(trace.get("stage") or ""),
            )
        )
    return requests


def run_shadow(classifier: ShadowClassifier, requests: list[ShadowRequest]) -> list[ShadowVerdict]:
    """Classify outside the daemon. Quotes must be copied from the evidence."""
    verdicts: list[ShadowVerdict] = []
    for request in requests:
        verdict = classifier.classify(request)
        quote = verdict.evidence_quote
        grounded = bool(quote) and quote in request.evidence_text
        label = verdict.label
        if label != "INSUFFICIENT_EVIDENCE" and not grounded:
            label = "INSUFFICIENT_EVIDENCE"
        verdicts.append(ShadowVerdict(label, quote if grounded else "", False))
    return verdicts
