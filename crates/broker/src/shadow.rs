//! Shadow semantic verification.
//!
//! A shadow classifier may label a candidate `supported`, `contradicted`, or
//! `insufficient`. It never receives a database connection, and
//! [`run_shadow`] forces `writes_memory` to false. Call it before taking the
//! SQLite writer, and do not fold the verdict into the projection.
//!
//! The live handoff path does not call this module.

use serde::{Deserialize, Serialize};

use crate::extract::DecisionTrace;

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
        .map(|request| {
            let verdict = classifier.classify(request);
            ShadowVerdict {
                label: verdict.label,
                evidence_quote: verdict.evidence_quote,
                writes_memory: false,
            }
        })
        .collect()
}

/// Candidates a shadow verifier should see: accepted claims and skipped or
/// dropped clauses. An `admit` row wins over an earlier stage with the same text.
pub fn shadow_requests(
    traces: &[DecisionTrace],
    evidence_text: &str,
    evidence_ids: &[String],
) -> Vec<ShadowRequest> {
    let mut chosen: Vec<&DecisionTrace> = Vec::new();
    for trace in traces {
        let interesting = matches!(
            (trace.stage, trace.outcome),
            ("admit", "accepted" | "rejected" | "skipped")
                | ("apply_extraction", "skipped" | "extracted")
                | ("keep_clause", "dropped")
        );
        if !interesting || trace.text.is_empty() {
            continue;
        }
        if let Some(existing) = chosen.iter().position(|row| row.text == trace.text) {
            if stage_rank(trace.stage) >= stage_rank(chosen[existing].stage) {
                chosen[existing] = trace;
            }
        } else {
            chosen.push(trace);
        }
    }
    chosen
        .into_iter()
        .map(|trace| ShadowRequest {
            claim_text: trace.text.clone(),
            evidence_text: evidence_text.to_string(),
            evidence_ids: evidence_ids.to_vec(),
            deterministic_outcome: trace.outcome.to_string(),
            deterministic_reason: trace.reason.to_string(),
            stage: trace.stage.to_string(),
        })
        .collect()
}

fn stage_rank(stage: &str) -> u8 {
    match stage {
        "admit" => 3,
        "apply_extraction" => 2,
        "keep_clause" => 1,
        _ => 0,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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
    fn shadow_requests_keep_accepted_and_skipped_claims() {
        let traces = vec![
            DecisionTrace {
                stage: "keep_clause",
                text: "the retry counter is incremented".into(),
                outcome: "dropped",
                reason: "residue",
                relation: "insufficient",
            },
            DecisionTrace {
                stage: "apply_extraction",
                text: "retry_count never increments".into(),
                outcome: "extracted",
                reason: "lexical_pass",
                relation: "supported",
            },
            DecisionTrace {
                stage: "admit",
                text: "retry_count never increments".into(),
                outcome: "accepted",
                reason: "lexical_pass",
                relation: "supported",
            },
        ];
        let requests = shadow_requests(&traces, "listing", &["e1".into()]);
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
                .any(|row| row.deterministic_reason == "residue")
        );
    }
}
