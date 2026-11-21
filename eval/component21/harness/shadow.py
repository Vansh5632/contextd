"""Shadow semantic verifier.

The classifier sees claims the deterministic checker accepted and claims it
skipped. A verdict never writes memory. This module does not call a model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

LABELS = frozenset({"SUPPORTED", "CONTRADICTED", "INSUFFICIENT_EVIDENCE"})
STAGE_RANK = {"admit": 3, "apply_extraction": 2, "keep_clause": 1}


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


def shadow_requests(
    traces: list[dict],
    evidence_text: str,
    evidence_ids: list[str] | tuple[str, ...],
) -> list[ShadowRequest]:
    """Accepted claims and skipped or dropped clauses. Admit wins on equal text."""
    chosen: list[dict] = []
    ids = tuple(evidence_ids)
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
                if STAGE_RANK.get(stage, 0) >= STAGE_RANK.get(str(existing.get("stage")), 0):
                    chosen[index] = trace
                break
        else:
            chosen.append(trace)
    return [
        ShadowRequest(
            claim_text=str(trace.get("text") or ""),
            evidence_text=evidence_text,
            evidence_ids=ids,
            deterministic_outcome=str(trace.get("outcome") or ""),
            deterministic_reason=str(trace.get("reason") or ""),
            stage=str(trace.get("stage") or ""),
        )
        for trace in chosen
    ]


def run_shadow(classifier: ShadowClassifier, requests: list[ShadowRequest]) -> list[ShadowVerdict]:
    """Classify outside the daemon. Quotes must be copied from the evidence."""
    verdicts: list[ShadowVerdict] = []
    for request in requests:
        verdict = classifier.classify(request)
        quote = verdict.evidence_quote
        if quote and quote not in request.evidence_text:
            verdict = ShadowVerdict("INSUFFICIENT_EVIDENCE", "")
        verdicts.append(ShadowVerdict(verdict.label, verdict.evidence_quote, False))
    return verdicts
