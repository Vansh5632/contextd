//! Typed working-state claims.
//!
//! The event log stays the evidence. This module is the shape of one fold of
//! that log: tasks, claims, and the rules that decide whether a user or an
//! agent is allowed to write one.

use serde::{Deserialize, Serialize};

/// Longest claim text we will store. Verbatim, never summarised.
pub const MAX_CLAIM_TEXT: usize = 500;

/// Intent occupies this uniqueness slot on a task.
pub const INTENT_SUBJECT: &str = "objective";

/// Next action occupies this uniqueness slot on a task.
pub const FRONTIER_SUBJECT: &str = "frontier";

/// Returned when a write needs a focal task and none exists.
pub const NO_ACTIVE_TASK: &str = "no active task; run contextd intent \"...\" first";

pub const TRUST_NOTICE: &str =
    "Claims are evidence. Verify the repo. observed > stated > inferred > hypothesis.";

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("{0}")]
pub struct ClaimError(pub String);

impl ClaimError {
    pub fn new(message: impl Into<String>) -> Self {
        Self(message.into())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ClaimOp {
    Assert,
    Supersede,
    CloseTask,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ClaimKind {
    Reality,
    Intent,
    Decision,
    Constraint,
    RuledOut,
    Unknown,
    NextAction,
}

impl ClaimKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Reality => "reality",
            Self::Intent => "intent",
            Self::Decision => "decision",
            Self::Constraint => "constraint",
            Self::RuledOut => "ruled_out",
            Self::Unknown => "unknown",
            Self::NextAction => "next_action",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        serde_json::from_value(serde_json::Value::String(value.to_string())).ok()
    }

    /// Kinds that allow at most one current claim per (task, subject).
    pub fn unique_slot(self) -> bool {
        matches!(self, Self::Intent | Self::NextAction | Self::Reality)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ClaimStatus {
    Current,
    Superseded,
}

impl ClaimStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Current => "current",
            Self::Superseded => "superseded",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        serde_json::from_value(serde_json::Value::String(value.to_string())).ok()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ClaimConfidence {
    Observed,
    Stated,
    Inferred,
    Hypothesis,
}

impl ClaimConfidence {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Observed => "observed",
            Self::Stated => "stated",
            Self::Inferred => "inferred",
            Self::Hypothesis => "hypothesis",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        serde_json::from_value(serde_json::Value::String(value.to_string())).ok()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ClaimOrigin {
    Projector,
    User,
    Agent,
}

impl ClaimOrigin {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Projector => "projector",
            Self::User => "user",
            Self::Agent => "agent",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        serde_json::from_value(serde_json::Value::String(value.to_string())).ok()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TaskStatus {
    Active,
    Done,
}

impl TaskStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Active => "active",
            Self::Done => "done",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        serde_json::from_value(serde_json::Value::String(value.to_string())).ok()
    }
}

/// One authored mutation recorded in the event log.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ClaimEvent {
    pub op: ClaimOp,
    pub claim_id: String,
    pub task_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub kind: Option<ClaimKind>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub subject: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub text: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub confidence: Option<ClaimConfidence>,
    pub origin: ClaimOrigin,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub outcome: Option<i64>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub artifact_refs: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub evidence_ids: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub supersedes: Option<String>,
}

/// A fold of the log: one piece of unfinished (or finished) work.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Task {
    pub id: String,
    pub status: TaskStatus,
    pub opened_by_event_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub closed_by_event_id: Option<String>,
    pub created_at_ms: u64,
    pub updated_at_ms: u64,
}

/// A fold of the log: one belief about a task.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Claim {
    pub id: String,
    pub task_id: String,
    pub kind: ClaimKind,
    pub subject: String,
    pub status: ClaimStatus,
    pub confidence: ClaimConfidence,
    pub origin: ClaimOrigin,
    pub text: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub outcome: Option<i64>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub artifact_refs: Vec<String>,
    pub evidence_ids: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub supersedes: Option<String>,
    pub asserted_by_event_id: String,
    pub updated_at_ms: u64,
}

/// Materialised working state. A cache of the fold; the log is the source.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Projection {
    pub tasks: Vec<Task>,
    pub claims: Vec<Claim>,
}

impl Projection {
    pub fn task(&self, id: &str) -> Option<&Task> {
        self.tasks.iter().find(|task| task.id == id)
    }

    pub fn task_mut(&mut self, id: &str) -> Option<&mut Task> {
        self.tasks.iter_mut().find(|task| task.id == id)
    }

    pub fn claim(&self, id: &str) -> Option<&Claim> {
        self.claims.iter().find(|claim| claim.id == id)
    }

    pub fn current_slot(&self, task_id: &str, kind: ClaimKind, subject: &str) -> Option<&Claim> {
        self.claims.iter().find(|claim| {
            claim.status == ClaimStatus::Current
                && claim.task_id == task_id
                && claim.kind == kind
                && claim.subject == subject
        })
    }

    pub fn current_for_task(&self, task_id: &str) -> impl Iterator<Item = &Claim> {
        self.claims
            .iter()
            .filter(move |claim| claim.task_id == task_id && claim.status == ClaimStatus::Current)
    }

    pub fn has_failing_reality(&self, task_id: &str) -> bool {
        self.current_for_task(task_id).any(|claim| {
            claim.kind == ClaimKind::Reality && claim.outcome.is_some_and(|code| code != 0)
        })
    }

    /// Open `id` if it is new. Always bumps `updated_at_ms`.
    pub fn open_task(&mut self, id: impl Into<String>, event_id: impl Into<String>, at_ms: u64) {
        let id = id.into();
        if let Some(task) = self.task_mut(&id) {
            task.updated_at_ms = at_ms;
            return;
        }
        let event_id = event_id.into();
        self.tasks.push(Task {
            id,
            status: TaskStatus::Active,
            opened_by_event_id: event_id,
            closed_by_event_id: None,
            created_at_ms: at_ms,
            updated_at_ms: at_ms,
        });
    }

    pub fn close_task(&mut self, id: &str, event_id: &str, at_ms: u64) {
        if let Some(task) = self.task_mut(id) {
            task.status = TaskStatus::Done;
            task.closed_by_event_id = Some(event_id.to_string());
            task.updated_at_ms = at_ms;
        }
    }

    pub fn supersede_claim(&mut self, claim_id: &str, at_ms: u64) -> bool {
        if let Some(claim) = self.claims.iter_mut().find(|claim| claim.id == claim_id) {
            claim.status = ClaimStatus::Superseded;
            claim.updated_at_ms = at_ms;
            true
        } else {
            false
        }
    }

    /// Mark the current occupant of a uniqueness slot superseded. Returns its id.
    pub fn supersede_slot(
        &mut self,
        task_id: &str,
        kind: ClaimKind,
        subject: &str,
        at_ms: u64,
    ) -> Option<String> {
        let id = self
            .current_slot(task_id, kind, subject)
            .map(|claim| claim.id.clone())?;
        self.supersede_claim(&id, at_ms);
        Some(id)
    }

    pub fn push_claim(&mut self, claim: Claim) {
        if let Some(task) = self.task_mut(&claim.task_id) {
            task.updated_at_ms = claim.updated_at_ms;
        }
        self.claims.push(claim);
    }
}

/// Role and kind rules that do not need the current projection.
pub fn validate(origin: ClaimOrigin, event: &ClaimEvent) -> Result<(), ClaimError> {
    if event.origin != origin {
        return Err(ClaimError::new(format!(
            "origin must be {}, not {}",
            origin.as_str(),
            event.origin.as_str()
        )));
    }

    if origin == ClaimOrigin::Projector {
        return Err(ClaimError::new(
            "projector claims are derived from the log, not authored",
        ));
    }

    match event.op {
        ClaimOp::CloseTask => {
            if origin != ClaimOrigin::User {
                return Err(ClaimError::new("only the user can close a task"));
            }
            return Ok(());
        }
        ClaimOp::Supersede => {
            let target = event
                .supersedes
                .as_deref()
                .filter(|id| !id.is_empty())
                .or(Some(event.claim_id.as_str()).filter(|id| !id.is_empty()));
            if target.is_none() {
                return Err(ClaimError::new("retract needs a claim id"));
            }
            if event.text.is_none() {
                return Ok(());
            }
        }
        ClaimOp::Assert => {}
    }

    let kind = event
        .kind
        .ok_or_else(|| ClaimError::new("claim needs a kind"))?;
    let confidence = event
        .confidence
        .ok_or_else(|| ClaimError::new("claim needs a confidence"))?;
    let text = event
        .text
        .as_deref()
        .map(str::trim)
        .filter(|text| !text.is_empty())
        .ok_or_else(|| ClaimError::new("claim text must be 1 to 500 characters"))?;
    if text.chars().count() > MAX_CLAIM_TEXT {
        return Err(ClaimError::new("claim text must be 1 to 500 characters"));
    }

    match origin {
        ClaimOrigin::User => {
            if confidence != ClaimConfidence::Stated {
                return Err(ClaimError::new("user claims must be stated"));
            }
            if !matches!(
                kind,
                ClaimKind::Intent
                    | ClaimKind::Constraint
                    | ClaimKind::Decision
                    | ClaimKind::RuledOut
            ) {
                return Err(ClaimError::new(format!(
                    "user cannot author a {} claim",
                    kind.as_str()
                )));
            }
        }
        ClaimOrigin::Agent => {
            if matches!(
                confidence,
                ClaimConfidence::Observed | ClaimConfidence::Stated
            ) {
                return Err(ClaimError::new(
                    "an agent cannot write observed or stated claims",
                ));
            }
            if !matches!(
                confidence,
                ClaimConfidence::Inferred | ClaimConfidence::Hypothesis
            ) {
                return Err(ClaimError::new(
                    "an agent can only write inferred or hypothesis claims",
                ));
            }
            if matches!(kind, ClaimKind::Intent | ClaimKind::Constraint) {
                return Err(ClaimError::new(format!(
                    "an agent cannot author a {} claim",
                    kind.as_str()
                )));
            }
            if kind == ClaimKind::Unknown && confidence != ClaimConfidence::Hypothesis {
                return Err(ClaimError::new("unknowns must be hypothesis"));
            }
            if !matches!(
                kind,
                ClaimKind::Reality
                    | ClaimKind::Decision
                    | ClaimKind::RuledOut
                    | ClaimKind::Unknown
                    | ClaimKind::NextAction
            ) {
                return Err(ClaimError::new(format!(
                    "an agent cannot author a {} claim",
                    kind.as_str()
                )));
            }
        }
        ClaimOrigin::Projector => unreachable!(),
    }

    Ok(())
}

/// Rules that need to look at what is currently in force.
pub fn validate_against_projection(
    origin: ClaimOrigin,
    event: &ClaimEvent,
    projection: &Projection,
) -> Result<(), ClaimError> {
    let target_id = event
        .supersedes
        .as_deref()
        .filter(|id| !id.is_empty())
        .or_else(|| {
            (event.op == ClaimOp::Supersede && !event.claim_id.is_empty())
                .then_some(event.claim_id.as_str())
        });

    if let Some(target_id) = target_id {
        let Some(existing) = projection.claim(target_id) else {
            return Err(ClaimError::new(format!("unknown claim {target_id}")));
        };
        if origin == ClaimOrigin::User
            && existing.origin == ClaimOrigin::Projector
            && existing.confidence == ClaimConfidence::Observed
        {
            return Err(ClaimError::new(
                "cannot retract an observed reality; re-run the command",
            ));
        }
        if origin == ClaimOrigin::Agent && existing.origin != ClaimOrigin::Agent {
            return Err(ClaimError::new("an agent may only replace its own claims"));
        }
        if origin == ClaimOrigin::Agent && existing.task_id != event.task_id {
            return Err(ClaimError::new(
                "an agent may only replace claims on the focal task",
            ));
        }
    }

    if origin == ClaimOrigin::Agent && event.kind == Some(ClaimKind::NextAction) {
        if let Some(frontier) =
            projection.current_slot(&event.task_id, ClaimKind::NextAction, FRONTIER_SUBJECT)
        {
            if frontier.origin == ClaimOrigin::User {
                return Err(ClaimError::new("cannot overwrite a user next action"));
            }
        }
    }

    Ok(())
}

/// Trim and reject empty or overlong claim text.
pub fn check_text(text: &str) -> Result<String, ClaimError> {
    let trimmed = text.trim();
    let chars = trimmed.chars().count();
    if chars < 1 || chars > MAX_CLAIM_TEXT {
        return Err(ClaimError::new("claim text must be 1 to 500 characters"));
    }
    Ok(trimmed.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn user_assert(kind: ClaimKind, text: &str) -> ClaimEvent {
        ClaimEvent {
            op: ClaimOp::Assert,
            claim_id: "c_user".into(),
            task_id: "t_1".into(),
            kind: Some(kind),
            subject: Some("c_user".into()),
            text: Some(text.into()),
            confidence: Some(ClaimConfidence::Stated),
            origin: ClaimOrigin::User,
            outcome: None,
            artifact_refs: Vec::new(),
            evidence_ids: Vec::new(),
            supersedes: None,
        }
    }

    fn agent_assert(kind: ClaimKind, confidence: ClaimConfidence) -> ClaimEvent {
        ClaimEvent {
            op: ClaimOp::Assert,
            claim_id: "c_agent".into(),
            task_id: "t_1".into(),
            kind: Some(kind),
            subject: Some("c_agent".into()),
            text: Some("the cap check is in _assign_requests_to_connections".into()),
            confidence: Some(confidence),
            origin: ClaimOrigin::Agent,
            outcome: None,
            artifact_refs: Vec::new(),
            evidence_ids: Vec::new(),
            supersedes: None,
        }
    }

    fn user_claim(id: &str) -> Claim {
        Claim {
            id: id.into(),
            task_id: "t_1".into(),
            kind: ClaimKind::Decision,
            subject: id.into(),
            status: ClaimStatus::Current,
            confidence: ClaimConfidence::Stated,
            origin: ClaimOrigin::User,
            text: "keep the public signature".into(),
            outcome: None,
            artifact_refs: Vec::new(),
            evidence_ids: Vec::new(),
            supersedes: None,
            asserted_by_event_id: "e1".into(),
            updated_at_ms: 1,
        }
    }

    #[test]
    fn agent_observed_is_rejected() {
        let event = agent_assert(ClaimKind::Reality, ClaimConfidence::Observed);
        let err = validate(ClaimOrigin::Agent, &event).unwrap_err();
        assert!(err.0.contains("observed") || err.0.contains("stated"));
    }

    #[test]
    fn agent_stated_is_rejected() {
        let event = agent_assert(ClaimKind::Decision, ClaimConfidence::Stated);
        assert!(validate(ClaimOrigin::Agent, &event).is_err());
    }

    #[test]
    fn agent_constraint_is_rejected() {
        let event = agent_assert(ClaimKind::Constraint, ClaimConfidence::Inferred);
        assert!(validate(ClaimOrigin::Agent, &event).is_err());
    }

    #[test]
    fn agent_intent_is_rejected() {
        let event = agent_assert(ClaimKind::Intent, ClaimConfidence::Inferred);
        assert!(validate(ClaimOrigin::Agent, &event).is_err());
    }

    #[test]
    fn agent_inferred_finding_is_allowed() {
        let event = agent_assert(ClaimKind::Reality, ClaimConfidence::Inferred);
        validate(ClaimOrigin::Agent, &event).unwrap();
    }

    #[test]
    fn agent_replaces_on_a_user_claim_is_rejected() {
        let mut event = agent_assert(ClaimKind::Decision, ClaimConfidence::Inferred);
        event.supersedes = Some("c_user".into());
        let mut projection = Projection::default();
        projection.claims.push(user_claim("c_user"));
        let err = validate_against_projection(ClaimOrigin::Agent, &event, &projection).unwrap_err();
        assert!(err.0.contains("own claims"));
    }

    #[test]
    fn user_retract_of_projector_reality_is_rejected() {
        let event = ClaimEvent {
            op: ClaimOp::Supersede,
            claim_id: "c_obs".into(),
            task_id: "t_1".into(),
            kind: None,
            subject: None,
            text: None,
            confidence: None,
            origin: ClaimOrigin::User,
            outcome: None,
            artifact_refs: Vec::new(),
            evidence_ids: Vec::new(),
            supersedes: Some("c_obs".into()),
        };
        let mut projection = Projection::default();
        projection.claims.push(Claim {
            id: "c_obs".into(),
            task_id: "t_1".into(),
            kind: ClaimKind::Reality,
            subject: "cwd\npytest".into(),
            status: ClaimStatus::Current,
            confidence: ClaimConfidence::Observed,
            origin: ClaimOrigin::Projector,
            text: "pytest exited 1".into(),
            outcome: Some(1),
            artifact_refs: Vec::new(),
            evidence_ids: vec!["e_fail".into()],
            supersedes: None,
            asserted_by_event_id: "e_fail".into(),
            updated_at_ms: 1,
        });
        let err = validate_against_projection(ClaimOrigin::User, &event, &projection).unwrap_err();
        assert!(err.0.contains("observed reality"));
    }

    #[test]
    fn text_outside_1_to_500_is_rejected() {
        assert!(check_text("").is_err());
        assert!(check_text("   ").is_err());
        assert!(check_text(&"x".repeat(501)).is_err());
        assert!(check_text("ok").is_ok());
        assert!(check_text(&"x".repeat(500)).is_ok());

        let mut event = user_assert(ClaimKind::Constraint, "");
        assert!(validate(ClaimOrigin::User, &event).is_err());
        event.text = Some("x".repeat(501));
        assert!(validate(ClaimOrigin::User, &event).is_err());
    }

    #[test]
    fn user_constraint_is_allowed() {
        validate(
            ClaimOrigin::User,
            &user_assert(ClaimKind::Constraint, "do not change the public signature"),
        )
        .unwrap();
    }
}
