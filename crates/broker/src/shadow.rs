//! Shadow semantic verification.
//!
//! A shadow classifier may label a candidate `supported`, `contradicted`, or
//! `insufficient`. It never receives a database connection, and
//! [`run_shadow`] forces `writes_memory` to false. Call it before taking the
//! SQLite writer, and do not fold the verdict into the projection.
//!
//! The live handoff path does not call this module.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use crate::extract::{DecisionTrace, TraceOutcome, TraceStage};

/// Labels a semantic verifier may return. These strings match the evaluation
/// gold vocabulary.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ShadowLabel {
    Supported,
    Contradicted,
    InsufficientEvidence,
}

impl ShadowLabel {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Supported => "SUPPORTED",
            Self::Contradicted => "CONTRADICTED",
            Self::InsufficientEvidence => "INSUFFICIENT_EVIDENCE",
        }
    }
}

/// One candidate the deterministic checker already judged.
///
/// Built from a handoff trace and the cited tool text. No SQLite handle is
/// available here, so a model call made from [`ShadowClassifier::classify`]
/// cannot run under the writer lock unless the caller takes the lock itself.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ShadowRequest {
    pub claim_text: String,
    pub evidence_text: String,
    pub evidence_ids: Vec<String>,
    pub deterministic_outcome: String,
    pub deterministic_reason: String,
    pub stage: String,
}

/// A shadow label. `writes_memory` is always false after [`run_shadow`].
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ShadowVerdict {
    pub label: ShadowLabel,
    /// A verbatim span of `evidence_text` the classifier cites, or empty when
    /// it abstains.
    pub evidence_quote: String,
    pub writes_memory: bool,
}

pub trait ShadowClassifier {
    fn classify(&self, request: &ShadowRequest) -> ShadowVerdict;
}

/// Default classifier. It abstains on every candidate and calls no model.
#[derive(Debug, Default, Clone, Copy)]
pub struct AbstainingShadow;

impl ShadowClassifier for AbstainingShadow {
    fn classify(&self, _request: &ShadowRequest) -> ShadowVerdict {
        ShadowVerdict {
            label: ShadowLabel::InsufficientEvidence,
            evidence_quote: String::new(),
            writes_memory: false,
        }
    }
}

/// Classify requests outside the memory store.
///
/// A classifier that sets `writes_memory` is overwritten. The returned
/// verdicts are not claims.
pub fn run_shadow<C: ShadowClassifier>(
    classifier: &C,
    requests: &[ShadowRequest],
) -> Vec<ShadowVerdict> {
    requests
        .iter()
        .map(|request| ground(request, classifier.classify(request)))
        .collect()
}

/// A non-abstaining label needs a nonempty quote copied from its own evidence.
fn ground(request: &ShadowRequest, verdict: ShadowVerdict) -> ShadowVerdict {
    let quote = verdict.evidence_quote.trim();
    let grounded = !quote.is_empty() && request.evidence_text.contains(quote);
    let label = if verdict.label == ShadowLabel::InsufficientEvidence || !grounded {
        ShadowLabel::InsufficientEvidence
    } else {
        verdict.label
    };
    ShadowVerdict {
        label,
        evidence_quote: if grounded {
            quote.to_string()
        } else {
            String::new()
        },
        writes_memory: false,
    }
}

/// Candidates a shadow verifier should see: accepted claims and skipped or
/// dropped clauses. An accepted row wins over a duplicate skip of the same text.
///
/// Evidence is the output of `support_event_id` only. A trace with no cited
/// shell event gets empty evidence, even when other tools ran in the session.
pub fn shadow_requests(
    traces: &[DecisionTrace],
    evidence_by_event: &BTreeMap<String, String>,
) -> Vec<ShadowRequest> {
    let mut chosen: Vec<&DecisionTrace> = Vec::new();
    for trace in traces {
        let interesting = matches!(
            (trace.stage, trace.outcome),
            (
                TraceStage::Admit,
                TraceOutcome::Accepted | TraceOutcome::Rejected | TraceOutcome::Skipped
            ) | (
                TraceStage::ApplyExtraction,
                TraceOutcome::Skipped | TraceOutcome::Extracted
            ) | (TraceStage::KeepClause, TraceOutcome::Dropped)
        );
        if !interesting || trace.text.is_empty() {
            continue;
        }
        if let Some(existing) = chosen.iter().position(|row| row.text == trace.text) {
            let incoming = (stage_rank(trace.stage), trace.outcome.rank());
            let current = (
                stage_rank(chosen[existing].stage),
                chosen[existing].outcome.rank(),
            );
            if incoming > current {
                chosen[existing] = trace;
            }
        } else {
            chosen.push(trace);
        }
    }
    chosen
        .into_iter()
        .map(|trace| {
            let (evidence_text, evidence_ids) = match trace.support_event_id.as_deref() {
                Some(id) => (
                    evidence_by_event.get(id).cloned().unwrap_or_default(),
                    vec![id.to_string()],
                ),
                None => (String::new(), Vec::new()),
            };
            ShadowRequest {
                claim_text: trace.text.clone(),
                evidence_text,
                evidence_ids,
                deterministic_outcome: trace.outcome.as_str().to_string(),
                deterministic_reason: trace.reason.as_str().to_string(),
                stage: trace.stage.as_str().to_string(),
            }
        })
        .collect()
}

fn stage_rank(stage: TraceStage) -> u8 {
    match stage {
        TraceStage::Admit => 3,
        TraceStage::ApplyExtraction => 2,
        TraceStage::KeepClause => 1,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::extract::{TraceReason, TraceRelation};

    fn request(outcome: &str, reason: &str) -> ShadowRequest {
        ShadowRequest {
            claim_text: "retry_count never increments before open_socket".into(),
            evidence_text: "self.retry_count += 1\nself.open_socket()".into(),
            evidence_ids: vec!["shell-1".into()],
            deterministic_outcome: outcome.into(),
            deterministic_reason: reason.into(),
            stage: "admit".into(),
        }
    }

    struct Hostile;

    impl ShadowClassifier for Hostile {
        fn classify(&self, request: &ShadowRequest) -> ShadowVerdict {
            ShadowVerdict {
                label: ShadowLabel::Supported,
                evidence_quote: request.evidence_text.clone(),
                writes_memory: true,
            }
        }
    }

    #[test]
    fn abstaining_shadow_labels_without_writing_memory() {
        let verdicts = run_shadow(&AbstainingShadow, &[request("accepted", "lexical_pass")]);
        assert_eq!(verdicts[0].label, ShadowLabel::InsufficientEvidence);
        assert!(verdicts[0].evidence_quote.is_empty());
        assert!(!verdicts[0].writes_memory);
    }

    #[test]
    fn a_classifier_cannot_mark_the_verdict_as_a_write() {
        let verdicts = run_shadow(&Hostile, &[request("skipped", "residue")]);
        assert_eq!(verdicts[0].label, ShadowLabel::Supported);
        assert_eq!(
            verdicts[0].evidence_quote,
            "self.retry_count += 1\nself.open_socket()"
        );
        assert!(!verdicts[0].writes_memory);
    }

    #[test]
    fn a_supported_label_without_a_quote_abstains() {
        struct Empty;
        impl ShadowClassifier for Empty {
            fn classify(&self, _request: &ShadowRequest) -> ShadowVerdict {
                ShadowVerdict {
                    label: ShadowLabel::Supported,
                    evidence_quote: String::new(),
                    writes_memory: false,
                }
            }
        }
        let verdicts = run_shadow(&Empty, &[request("accepted", "lexical_pass")]);
        assert_eq!(verdicts[0].label, ShadowLabel::InsufficientEvidence);
        assert!(verdicts[0].evidence_quote.is_empty());
    }

    fn row(
        stage: TraceStage,
        text: &str,
        outcome: TraceOutcome,
        reason: TraceReason,
        relation: TraceRelation,
        support: Option<&str>,
    ) -> DecisionTrace {
        DecisionTrace {
            stage,
            text: text.into(),
            outcome,
            reason,
            relation,
            support_event_id: support.map(str::to_string),
        }
    }

    #[test]
    fn shadow_requests_keep_accepted_and_skipped_claims() {
        let traces = vec![
            row(
                TraceStage::KeepClause,
                "the retry counter is incremented",
                TraceOutcome::Dropped,
                TraceReason::Residue,
                TraceRelation::Insufficient,
                Some("e1"),
            ),
            row(
                TraceStage::ApplyExtraction,
                "retry_count never increments",
                TraceOutcome::Extracted,
                TraceReason::LexicalPass,
                TraceRelation::Supported,
                Some("e1"),
            ),
            row(
                TraceStage::Admit,
                "retry_count never increments",
                TraceOutcome::Accepted,
                TraceReason::LexicalPass,
                TraceRelation::Supported,
                Some("e1"),
            ),
            row(
                TraceStage::Admit,
                "retry_count never increments",
                TraceOutcome::Skipped,
                TraceReason::Duplicate,
                TraceRelation::NotApplicable,
                None,
            ),
        ];
        let evidence = BTreeMap::from([
            ("e1".to_string(), "listing".to_string()),
            ("e2".to_string(), "unrelated tool output".to_string()),
        ]);
        let requests = shadow_requests(&traces, &evidence);
        assert_eq!(requests.len(), 2);
        let accepted = requests
            .iter()
            .find(|row| row.claim_text == "retry_count never increments")
            .unwrap();
        assert_eq!(accepted.stage, "admit");
        assert_eq!(accepted.deterministic_outcome, "accepted");
        assert_eq!(accepted.evidence_text, "listing");
        assert_eq!(accepted.evidence_ids, vec!["e1".to_string()]);
        assert!(
            requests
                .iter()
                .all(|row| !row.evidence_text.contains("unrelated"))
        );
        assert!(
            requests
                .iter()
                .any(|row| row.deterministic_reason == "residue")
        );
    }
}
