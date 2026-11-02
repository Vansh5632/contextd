//! Render the current claim view as the briefing.
//!
//! `context_now` is a picture of what is in force, not a ranked event feed.
//! Search and recall stay on their own tools.

use contextd_core::claim::{
    Claim, ClaimConfidence, ClaimKind, ClaimOrigin, ClaimStatus, Projection, TRUST_NOTICE, Task,
    TaskStatus,
};
use memory::project::focal_task;
use memory::{SharedGraph, WorkingSet};
use serde::Serialize;
use store::db::load_projection;

use crate::budget::{DEFAULT_TOKEN_BUDGET, estimate_tokens};

/// What an agent gets when it asks "what am I doing right now?"
#[derive(Debug, Clone, Serialize)]
pub struct ContextSnapshot {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session_id: Option<String>,
    pub notice: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub constraints: Vec<RenderedClaim>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub passed_checks: Vec<PassedCheck>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub open_work: Vec<FocalTaskView>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub focal_task: Option<FocalTaskView>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub other_active_tasks: Vec<OtherTaskLine>,
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub truncated: bool,
    pub estimated_tokens: usize,
}

#[derive(Debug, Clone, Serialize)]
pub struct FocalTaskView {
    pub id: String,
    pub status: TaskStatus,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub intent: Option<RenderedClaim>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub constraints: Vec<RenderedClaim>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub reality: Vec<RenderedClaim>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub decisions: Vec<RenderedClaim>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub ruled_out: Vec<RenderedClaim>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub unknowns: Vec<RenderedClaim>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub next_action: Option<RenderedClaim>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub artifacts: Vec<String>,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct RenderedClaim {
    pub id: String,
    pub text: String,
    pub confidence: ClaimConfidence,
    pub origin: ClaimOrigin,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub evidence_ids: Vec<String>,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct OtherTaskLine {
    pub id: String,
    pub status: TaskStatus,
    pub summary: String,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct PassedCheck {
    pub task_id: String,
    pub text: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub evidence_ids: Vec<String>,
}

pub struct SnapshotRequest<'a> {
    pub store: &'a store::Store,
    pub ollama: Option<&'a ai::ollama::OllamaClient>,
    pub working: Option<&'a WorkingSet>,
    pub graph: Option<&'a SharedGraph>,
    pub query: &'a str,
    pub token_budget: usize,
    pub explain: bool,
}

impl<'a> SnapshotRequest<'a> {
    pub fn new(store: &'a store::Store, query: &'a str) -> Self {
        Self {
            store,
            ollama: None,
            working: None,
            graph: None,
            query,
            token_budget: DEFAULT_TOKEN_BUDGET,
            explain: false,
        }
    }

    pub fn with_ollama(mut self, ollama: Option<&'a ai::ollama::OllamaClient>) -> Self {
        self.ollama = ollama;
        self
    }

    pub fn with_tiers(
        mut self,
        working: Option<&'a WorkingSet>,
        graph: Option<&'a SharedGraph>,
    ) -> Self {
        self.working = working;
        self.graph = graph;
        self
    }

    pub fn with_budget(mut self, tokens: usize) -> Self {
        self.token_budget = tokens;
        self
    }
}

pub async fn build(request: SnapshotRequest<'_>) -> anyhow::Result<ContextSnapshot> {
    let conn = request.store.reader()?;
    let projection = load_projection(&conn)?;
    Ok(render(
        &projection,
        request.working.map(|working| working.session_id()),
        request.token_budget,
    ))
}

pub async fn snapshot_now(
    store: &store::Store,
    _ai_client: Option<&ai::ollama::OllamaClient>,
    query: &str,
) -> anyhow::Result<ContextSnapshot> {
    build(SnapshotRequest::new(store, query)).await
}

pub fn render(
    projection: &Projection,
    session_id: Option<String>,
    token_budget: usize,
) -> ContextSnapshot {
    let focal = focal_task(projection);
    let focal_id = focal.as_ref().map(|task| task.id.clone());
    let mut snapshot = ContextSnapshot {
        session_id,
        notice: TRUST_NOTICE.to_string(),
        constraints: standing_constraints(projection),
        passed_checks: passed_checks(projection),
        open_work: open_work(projection, focal_id.as_deref()),
        focal_task: focal.map(|task| focal_view(projection, task)),
        other_active_tasks: focal_id
            .as_deref()
            .map(|id| other_tasks(projection, id))
            .unwrap_or_default(),
        truncated: false,
        estimated_tokens: 0,
    };
    if snapshot.constraints.is_empty()
        && snapshot.focal_task.is_none()
        && snapshot.passed_checks.is_empty()
        && snapshot.open_work.is_empty()
    {
        snapshot.other_active_tasks.clear();
        snapshot.estimated_tokens = estimate_tokens(TRUST_NOTICE);
        return snapshot;
    }
    trim_to_budget(&mut snapshot, token_budget);
    snapshot
}

fn passed_checks(projection: &Projection) -> Vec<PassedCheck> {
    let mut checks: Vec<PassedCheck> = projection
        .claims
        .iter()
        .filter(|claim| {
            claim.status == ClaimStatus::Current
                && claim.kind == ClaimKind::Reality
                && claim.outcome == Some(0)
                && previously_failed(projection, claim)
                && projection
                    .task(&claim.task_id)
                    .is_some_and(|task| task.status == TaskStatus::Active)
        })
        .map(|claim| PassedCheck {
            task_id: claim.task_id.clone(),
            text: pass_text(&claim.text),
            evidence_ids: claim.evidence_ids.clone(),
        })
        .collect();
    checks.sort_by(|a, b| a.task_id.cmp(&b.task_id).then_with(|| a.text.cmp(&b.text)));
    checks
}

fn previously_failed(projection: &Projection, current: &Claim) -> bool {
    projection.claims.iter().any(|claim| {
        claim.id != current.id
            && claim.task_id == current.task_id
            && claim.kind == ClaimKind::Reality
            && claim.subject == current.subject
            && claim.status == ClaimStatus::Superseded
            && claim.outcome.is_some_and(|code| code != 0)
    })
}

fn pass_text(reality_text: &str) -> String {
    let command = reality_text
        .strip_suffix(" exited 0")
        .unwrap_or(reality_text);
    format!("{command} now passes")
}

fn standing_constraints(projection: &Projection) -> Vec<RenderedClaim> {
    let mut claims: Vec<&Claim> = projection
        .claims
        .iter()
        .filter(|claim| claim.status == ClaimStatus::Current && claim.kind == ClaimKind::Constraint)
        .collect();
    claims.sort_by(|a, b| {
        a.updated_at_ms
            .cmp(&b.updated_at_ms)
            .then_with(|| a.id.cmp(&b.id))
    });
    claims.into_iter().map(render_claim).collect()
}

fn focal_view(projection: &Projection, task: &Task) -> FocalTaskView {
    let current: Vec<&Claim> = projection.current_for_task(&task.id).collect();
    let pick = |kind: ClaimKind| -> Vec<RenderedClaim> {
        let mut claims: Vec<&Claim> = current
            .iter()
            .copied()
            .filter(|claim| claim.kind == kind)
            .filter(|claim| {
                if kind == ClaimKind::Reality {
                    !matches!(claim.outcome, Some(0))
                } else {
                    true
                }
            })
            .collect();
        claims.sort_by(|a, b| {
            confidence_rank(a.confidence)
                .cmp(&confidence_rank(b.confidence))
                .then_with(|| a.updated_at_ms.cmp(&b.updated_at_ms))
                .then_with(|| a.id.cmp(&b.id))
        });
        claims.into_iter().map(render_claim).collect()
    };

    let mut artifacts = Vec::new();
    for claim in &current {
        for artifact in &claim.artifact_refs {
            if !artifacts.contains(artifact) {
                artifacts.push(artifact.clone());
            }
        }
    }

    FocalTaskView {
        id: task.id.clone(),
        status: task.status,
        intent: pick(ClaimKind::Intent).into_iter().next(),
        constraints: Vec::new(),
        reality: pick(ClaimKind::Reality),
        decisions: pick(ClaimKind::Decision),
        ruled_out: pick(ClaimKind::RuledOut),
        unknowns: pick(ClaimKind::Unknown),
        next_action: pick(ClaimKind::NextAction).into_iter().next(),
        artifacts,
    }
}

fn open_work(projection: &Projection, focal_id: Option<&str>) -> Vec<FocalTaskView> {
    let mut tasks: Vec<&Task> = projection
        .tasks
        .iter()
        .filter(|task| task.status == TaskStatus::Active)
        .filter(|task| focal_id != Some(task.id.as_str()))
        .filter(|task| has_authored_continuation(projection, &task.id))
        .collect();
    tasks.sort_by(|a, b| {
        a.created_at_ms
            .cmp(&b.created_at_ms)
            .then_with(|| a.id.cmp(&b.id))
    });
    tasks
        .into_iter()
        .map(|task| focal_view(projection, task))
        .collect()
}

fn has_authored_continuation(projection: &Projection, task_id: &str) -> bool {
    projection
        .current_for_task(task_id)
        .any(|claim| match claim.kind {
            ClaimKind::Decision | ClaimKind::RuledOut | ClaimKind::Unknown => true,
            ClaimKind::Intent => true,
            ClaimKind::NextAction => claim.origin != ClaimOrigin::Projector,
            ClaimKind::Reality => claim.origin != ClaimOrigin::Projector,
            ClaimKind::Constraint => false,
        })
}

fn other_tasks(projection: &Projection, focal_id: &str) -> Vec<OtherTaskLine> {
    let mut lines: Vec<OtherTaskLine> = projection
        .tasks
        .iter()
        .filter(|task| task.status == TaskStatus::Active && task.id != focal_id)
        .filter(|task| projection.has_failing_reality(&task.id))
        .map(|task| OtherTaskLine {
            id: task.id.clone(),
            status: task.status,
            summary: task_summary(projection, task),
        })
        .collect();
    lines.sort_by(|a, b| a.id.cmp(&b.id));
    lines
}

fn task_summary(projection: &Projection, task: &Task) -> String {
    if let Some(intent) = projection.current_slot(&task.id, ClaimKind::Intent, "objective") {
        return intent.text.clone();
    }
    if let Some(failing) = projection.current_for_task(&task.id).find(|claim| {
        claim.kind == ClaimKind::Reality && claim.outcome.is_some_and(|code| code != 0)
    }) {
        return failing.text.clone();
    }
    projection
        .current_for_task(&task.id)
        .next()
        .map(|claim| claim.text.clone())
        .unwrap_or_else(|| task.id.clone())
}

fn render_claim(claim: &Claim) -> RenderedClaim {
    RenderedClaim {
        id: claim.id.clone(),
        text: claim.text.clone(),
        confidence: claim.confidence,
        origin: claim.origin,
        evidence_ids: claim.evidence_ids.clone(),
    }
}

fn confidence_rank(confidence: ClaimConfidence) -> u8 {
    match confidence {
        ClaimConfidence::Observed => 0,
        ClaimConfidence::Stated => 1,
        ClaimConfidence::Inferred => 2,
        ClaimConfidence::Hypothesis => 3,
    }
}

fn trim_to_budget(snapshot: &mut ContextSnapshot, budget: usize) {
    loop {
        let cost = packet_cost(snapshot);
        if cost <= budget {
            snapshot.estimated_tokens = cost;
            return;
        }
        snapshot.truncated = true;
        if trim_task(snapshot.focal_task.as_mut()) {
            continue;
        }
        if trim_open_work(snapshot) {
            continue;
        }
        if !snapshot.other_active_tasks.is_empty() {
            snapshot.other_active_tasks.pop();
            continue;
        }
        snapshot.estimated_tokens = packet_cost(snapshot);
        return;
    }
}

fn trim_open_work(snapshot: &mut ContextSnapshot) -> bool {
    for work in snapshot.open_work.iter_mut().rev() {
        if trim_task(Some(work)) {
            return true;
        }
    }
    false
}

fn trim_task(task: Option<&mut FocalTaskView>) -> bool {
    let Some(task) = task else {
        return false;
    };
    if !task.artifacts.is_empty() {
        task.artifacts.pop();
        return true;
    }
    if !task.unknowns.is_empty() {
        task.unknowns.pop();
        return true;
    }
    if task.decisions.len() > 1 {
        task.decisions.pop();
        return true;
    }
    false
}

fn packet_cost(snapshot: &ContextSnapshot) -> usize {
    estimate_tokens(&serde_json::to_string(snapshot).unwrap_or_default())
}

#[cfg(test)]
mod tests {
    use super::*;
    use contextd_core::claim::{
        Claim, ClaimConfidence, ClaimKind, ClaimOrigin, ClaimStatus, FRONTIER_SUBJECT, Projection,
        Task, TaskStatus,
    };
    use contextd_core::event::{EventSource, ProcessedEvent, RawEvent};
    use contextd_core::test_utils::test_config_in_memory;
    use memory::project::fold;
    use serde_json::json;
    use store::db::{insert_event, replace_projection};

    fn task(id: &str, opened: &str, at: u64) -> Task {
        Task {
            id: id.into(),
            status: TaskStatus::Active,
            opened_by_event_id: opened.into(),
            closed_by_event_id: None,
            created_at_ms: at,
            updated_at_ms: at,
        }
    }

    fn failing_reality(task_id: &str) -> Claim {
        Claim {
            id: "c_fail".into(),
            task_id: task_id.into(),
            kind: ClaimKind::Reality,
            subject: "cwd\npytest".into(),
            status: ClaimStatus::Current,
            confidence: ClaimConfidence::Observed,
            origin: ClaimOrigin::Projector,
            text: "python -m pytest tests/test_pool_counts.py exited 1".into(),
            outcome: Some(1),
            artifact_refs: Vec::new(),
            evidence_ids: vec!["pytest-fail".into()],
            supersedes: None,
            asserted_by_event_id: "pytest-fail".into(),
            updated_at_ms: 1,
        }
    }

    fn next_action(task_id: &str) -> Claim {
        Claim {
            id: "c_next".into(),
            task_id: task_id.into(),
            kind: ClaimKind::NextAction,
            subject: FRONTIER_SUBJECT.into(),
            status: ClaimStatus::Current,
            confidence: ClaimConfidence::Inferred,
            origin: ClaimOrigin::Projector,
            text: "Re-run and inspect: python -m pytest tests/test_pool_counts.py".into(),
            outcome: None,
            artifact_refs: Vec::new(),
            evidence_ids: vec!["pytest-fail".into()],
            supersedes: None,
            asserted_by_event_id: "pytest-fail".into(),
            updated_at_ms: 1,
        }
    }

    #[test]
    fn removed_event_feed_keys_are_absent() {
        let mut projection = Projection::default();
        projection.tasks.push(task("t_1", "pytest-fail", 1));
        projection.claims.push(failing_reality("t_1"));
        let snapshot = render(&projection, Some("s".into()), 1_500);
        let json = serde_json::to_value(&snapshot).unwrap();
        for key in [
            "recent_activity",
            "relevant_history",
            "related",
            "archived",
            "intent",
        ] {
            assert!(json.get(key).is_none(), "{key} should be absent");
        }
        assert!(json.get("focal_task").is_some());
        assert!(json.get("notice").is_some());
    }

    #[test]
    fn empty_packet_when_there_is_no_focal_task() {
        let snapshot = render(&Projection::default(), None, 1_500);
        assert!(snapshot.focal_task.is_none());
        assert!(snapshot.other_active_tasks.is_empty());
        assert!(!snapshot.truncated);
    }

    fn note(id: &str, task_id: &str, kind: ClaimKind, text: &str, at: u64) -> Claim {
        Claim {
            id: id.into(),
            task_id: task_id.into(),
            kind,
            subject: id.into(),
            status: ClaimStatus::Current,
            confidence: ClaimConfidence::Stated,
            origin: ClaimOrigin::User,
            text: text.into(),
            outcome: None,
            artifact_refs: Vec::new(),
            evidence_ids: Vec::new(),
            supersedes: None,
            asserted_by_event_id: id.into(),
            updated_at_ms: at,
        }
    }

    fn reality(
        id: &str,
        task_id: &str,
        subject: &str,
        text: &str,
        outcome: i64,
        status: ClaimStatus,
        at: u64,
    ) -> Claim {
        Claim {
            id: id.into(),
            task_id: task_id.into(),
            kind: ClaimKind::Reality,
            subject: subject.into(),
            status,
            confidence: ClaimConfidence::Observed,
            origin: ClaimOrigin::Projector,
            text: text.into(),
            outcome: Some(outcome),
            artifact_refs: Vec::new(),
            evidence_ids: vec![id.into()],
            supersedes: None,
            asserted_by_event_id: id.into(),
            updated_at_ms: at,
        }
    }

    #[test]
    fn green_rerun_keeps_the_constraint() {
        let mut projection = Projection::default();
        projection.tasks.push(task("t_pool", "fail", 1));
        projection.claims.push(reality(
            "c_fail",
            "t_pool",
            "repo\npytest",
            "pytest exited 1",
            1,
            ClaimStatus::Superseded,
            1,
        ));
        projection.claims.push(reality(
            "c_pass",
            "t_pool",
            "repo\npytest",
            "pytest exited 0",
            0,
            ClaimStatus::Current,
            3,
        ));
        projection.claims.push(note(
            "c_rule",
            "t_pool",
            ClaimKind::Constraint,
            "do not change the public signature",
            2,
        ));

        let snapshot = render(&projection, None, 1_500);
        assert!(snapshot.focal_task.is_none());
        assert_eq!(
            snapshot.constraints.len(),
            1,
            "a current constraint keeps the page open"
        );
        assert_eq!(
            snapshot.constraints[0].text,
            "do not change the public signature"
        );
        let json = serde_json::to_string(&snapshot).unwrap();
        assert!(!json.contains("exited 1"));
    }

    #[test]
    fn former_failure_renders_as_now_passes() {
        let mut projection = Projection::default();
        projection.tasks.push(task("t_pool", "fail", 1));
        projection.claims.push(reality(
            "c_fail",
            "t_pool",
            "repo\npytest",
            "python -m pytest tests/test_pool_counts.py exited 1",
            1,
            ClaimStatus::Superseded,
            1,
        ));
        projection.claims.push(reality(
            "c_pass",
            "t_pool",
            "repo\npytest",
            "python -m pytest tests/test_pool_counts.py exited 0",
            0,
            ClaimStatus::Current,
            3,
        ));

        let snapshot = render(&projection, None, 1_500);
        assert!(snapshot.focal_task.is_none());
        assert_eq!(snapshot.passed_checks.len(), 1);
        assert_eq!(
            snapshot.passed_checks[0].text,
            "python -m pytest tests/test_pool_counts.py now passes"
        );
        assert_eq!(
            snapshot.passed_checks[0].evidence_ids,
            vec!["c_pass".to_string()]
        );
        let json = serde_json::to_string(&snapshot).unwrap();
        assert!(!json.contains("exited 1"));
        assert!(!json.contains("Re-run and inspect"));
    }

    #[test]
    fn never_failed_command_has_no_pass_line() {
        let mut projection = Projection::default();
        projection.tasks.push(task("t_sed", "sed", 1));
        projection.claims.push(reality(
            "c_sed",
            "t_sed",
            "repo\nsed",
            "sed -n 1p Cargo.toml exited 0",
            0,
            ClaimStatus::Current,
            1,
        ));

        let snapshot = render(&projection, None, 1_500);
        assert!(snapshot.passed_checks.is_empty());
        assert!(snapshot.focal_task.is_none());
        assert!(snapshot.other_active_tasks.is_empty());
    }

    #[test]
    fn pass_line_leaves_when_the_task_is_done() {
        let mut projection = Projection::default();
        let mut done = task("t_pool", "fail", 1);
        done.status = TaskStatus::Done;
        projection.tasks.push(done);
        projection.claims.push(reality(
            "c_fail",
            "t_pool",
            "repo\npytest",
            "pytest exited 1",
            1,
            ClaimStatus::Superseded,
            1,
        ));
        projection.claims.push(reality(
            "c_pass",
            "t_pool",
            "repo\npytest",
            "pytest exited 0",
            0,
            ClaimStatus::Current,
            3,
        ));

        let snapshot = render(&projection, None, 1_500);
        assert!(snapshot.passed_checks.is_empty());
    }

    #[test]
    fn superseded_constraint_stays_off_the_page() {
        let mut projection = Projection::default();
        projection.tasks.push(task("t_pool", "fail", 1));
        let mut lifted = note(
            "c_rule",
            "t_pool",
            ClaimKind::Constraint,
            "do not change the public signature",
            2,
        );
        lifted.status = ClaimStatus::Superseded;
        projection.claims.push(lifted);

        let snapshot = render(&projection, None, 1_500);
        assert!(snapshot.constraints.is_empty());
        assert!(snapshot.focal_task.is_none());
    }

    #[test]
    fn constraint_on_a_done_task_stays() {
        let mut projection = Projection::default();
        let mut done = task("t_pool", "fail", 1);
        done.status = TaskStatus::Done;
        done.closed_by_event_id = Some("close".into());
        projection.tasks.push(done);
        projection.claims.push(note(
            "c_rule",
            "t_pool",
            ClaimKind::Constraint,
            "do not change the public signature",
            2,
        ));

        let snapshot = render(&projection, None, 1_500);
        assert_eq!(snapshot.constraints.len(), 1);
        assert!(snapshot.focal_task.is_none());
    }

    #[test]
    fn budget_keeps_the_rule_and_the_pass_line() {
        let mut projection = Projection::default();
        projection.tasks.push(task("t_pool", "fail", 1));
        projection.claims.push(reality(
            "c_fail",
            "t_pool",
            "repo\npytest",
            "pytest exited 1",
            1,
            ClaimStatus::Superseded,
            1,
        ));
        projection.claims.push(reality(
            "c_pass",
            "t_pool",
            "repo\npytest",
            "pytest exited 0",
            0,
            ClaimStatus::Current,
            2,
        ));
        projection.claims.push(note(
            "c_rule",
            "t_pool",
            ClaimKind::Constraint,
            "do not change the public signature",
            2,
        ));
        projection.claims.push(note(
            "c_decision",
            "t_pool",
            ClaimKind::Decision,
            "Idle connections should count toward the pool cap",
            2,
        ));
        for i in 0..20 {
            projection.claims.push(Claim {
                id: format!("c_unk{i}"),
                task_id: "t_pool".into(),
                kind: ClaimKind::Unknown,
                subject: format!("c_unk{i}"),
                status: ClaimStatus::Current,
                confidence: ClaimConfidence::Hypothesis,
                origin: ClaimOrigin::Agent,
                text: format!("unresolved question number {i} with extra padding words"),
                outcome: None,
                artifact_refs: Vec::new(),
                evidence_ids: Vec::new(),
                supersedes: None,
                asserted_by_event_id: format!("e{i}"),
                updated_at_ms: 3 + i,
            });
        }

        let snapshot = render(&projection, None, 80);
        assert!(snapshot.truncated);
        assert_eq!(
            snapshot.constraints[0].text,
            "do not change the public signature"
        );
        assert_eq!(snapshot.passed_checks.len(), 1);
        assert_eq!(snapshot.open_work[0].decisions.len(), 1);
        assert!(snapshot.open_work[0].unknowns.len() < 20);
    }

    #[test]
    fn constraint_survives_budget() {
        let mut projection = Projection::default();
        projection.tasks.push(task("t_1", "pytest-fail", 1));
        projection.claims.push(failing_reality("t_1"));
        projection.claims.push(next_action("t_1"));
        projection.claims.push(Claim {
            id: "c_con".into(),
            task_id: "t_1".into(),
            kind: ClaimKind::Constraint,
            subject: "c_con".into(),
            status: ClaimStatus::Current,
            confidence: ClaimConfidence::Stated,
            origin: ClaimOrigin::User,
            text: "do not change the public signature".into(),
            outcome: None,
            artifact_refs: Vec::new(),
            evidence_ids: Vec::new(),
            supersedes: None,
            asserted_by_event_id: "e-con".into(),
            updated_at_ms: 2,
        });
        for i in 0..20 {
            projection.claims.push(Claim {
                id: format!("c_unk{i}"),
                task_id: "t_1".into(),
                kind: ClaimKind::Unknown,
                subject: format!("c_unk{i}"),
                status: ClaimStatus::Current,
                confidence: ClaimConfidence::Hypothesis,
                origin: ClaimOrigin::Agent,
                text: format!("unresolved question number {i} with extra padding words"),
                outcome: None,
                artifact_refs: Vec::new(),
                evidence_ids: Vec::new(),
                supersedes: None,
                asserted_by_event_id: format!("e{i}"),
                updated_at_ms: 3 + i,
            });
        }
        let snapshot = render(&projection, None, 80);
        let focal = snapshot.focal_task.unwrap();
        assert_eq!(
            snapshot.constraints[0].text,
            "do not change the public signature"
        );
        assert!(focal.constraints.is_empty());
        assert!(focal.reality[0].text.contains("exited 1"));
        assert!(focal.next_action.unwrap().text.contains("pytest"));
        assert!(snapshot.truncated);
        assert!(focal.unknowns.len() < 20);
    }

    #[tokio::test]
    async fn build_reads_the_materialized_view() {
        let store = store::Store::open(&test_config_in_memory()).unwrap();
        let event = ProcessedEvent::new(
            RawEvent {
                id: "pytest-fail".into(),
                timestamp_ms: 1_000,
                source: EventSource::FileSystem,
                payload: json!({"path": "/repo/x"}),
            },
            0.3,
        );
        {
            let conn = store.writer().await;
            insert_event(&conn, &event).unwrap();
            let mut projection = Projection::default();
            projection.tasks.push(task("t_1", "pytest-fail", 1));
            projection.claims.push(failing_reality("t_1"));
            projection.claims.push(next_action("t_1"));
            replace_projection(&conn, &projection).unwrap();
        }
        let snapshot = snapshot_now(&store, None, "ignored query").await.unwrap();
        let json = serde_json::to_value(&snapshot).unwrap();
        let focal = snapshot.focal_task.expect("view should load");
        assert!(focal.reality[0].text.contains("exited 1"));
        assert!(json.get("recent_activity").is_none());
    }

    #[test]
    fn other_tasks_keep_a_second_failure_and_drop_sed() {
        let mut projection = Projection::default();
        projection.tasks.push(task("t_a_pool", "fail", 1));
        projection.claims.push(failing_reality("t_a_pool"));
        projection.claims.push(next_action("t_a_pool"));

        projection.tasks.push(task("t_z_export", "export-fail", 2));
        projection.claims.push(Claim {
            id: "c_export".into(),
            task_id: "t_z_export".into(),
            kind: ClaimKind::Reality,
            subject: "repo\nexport".into(),
            status: ClaimStatus::Current,
            confidence: ClaimConfidence::Observed,
            origin: ClaimOrigin::Projector,
            text: "python -m pytest tests/test_export.py exited 1".into(),
            outcome: Some(1),
            artifact_refs: Vec::new(),
            evidence_ids: vec!["export-fail".into()],
            supersedes: None,
            asserted_by_event_id: "export-fail".into(),
            updated_at_ms: 2,
        });

        projection.tasks.push(task("t_m_sed", "sed", 3));
        projection.claims.push(reality(
            "c_sed",
            "t_m_sed",
            "repo\nsed",
            "sed -n 1p Cargo.toml exited 0",
            0,
            ClaimStatus::Current,
            3,
        ));

        let snapshot = render(&projection, None, 1_500);
        let focal = snapshot
            .focal_task
            .as_ref()
            .expect("oldest failure stays focal");
        assert_eq!(focal.id, "t_a_pool");
        assert_eq!(snapshot.other_active_tasks.len(), 1);
        assert_eq!(snapshot.other_active_tasks[0].id, "t_z_export");
        let json = serde_json::to_string(&snapshot).unwrap();
        assert!(!json.contains("sed -n"));
    }

    #[test]
    fn fold_fixture_does_not_surface_the_commit() {
        let pytest = "python -m pytest tests/test_pool_counts.py";
        let events = vec![
            RawEvent {
                id: "pytest-fail".into(),
                timestamp_ms: 1,
                source: EventSource::Shell,
                payload: json!({"command": pytest, "exit_code": 1, "cwd": "/repo"}),
            },
            RawEvent {
                id: "commit".into(),
                timestamp_ms: 2,
                source: EventSource::Git,
                payload: json!({"action": "commit", "subject": "fix csv export"}),
            },
        ];
        let projection = fold(&events);
        let snapshot = render(&projection, None, 1_500);
        let json = serde_json::to_string(&snapshot).unwrap();
        assert!(json.contains(pytest));
        assert!(!json.contains("csv export"));
    }

    #[test]
    fn decision_on_a_green_task_stays_visible() {
        let mut projection = Projection::default();
        projection.tasks.push(task("t_pool", "fail", 1));
        projection.claims.push(reality(
            "c_fail",
            "t_pool",
            "repo\npytest",
            "pytest exited 1",
            1,
            ClaimStatus::Superseded,
            1,
        ));
        projection.claims.push(reality(
            "c_pass",
            "t_pool",
            "repo\npytest",
            "pytest exited 0",
            0,
            ClaimStatus::Current,
            3,
        ));
        projection.claims.push(note(
            "c_decision",
            "t_pool",
            ClaimKind::Decision,
            "Idle connections should count toward the pool cap",
            2,
        ));
        projection.claims.push(note(
            "c_ruled",
            "t_pool",
            ClaimKind::RuledOut,
            "mocking the backend does not reproduce the cap",
            2,
        ));

        let snapshot = render(&projection, None, 1_500);
        assert!(snapshot.focal_task.is_none());
        assert_eq!(snapshot.open_work.len(), 1);
        assert_eq!(snapshot.open_work[0].id, "t_pool");
        assert_eq!(
            snapshot.open_work[0].decisions[0].text,
            "Idle connections should count toward the pool cap"
        );
        assert_eq!(
            snapshot.open_work[0].ruled_out[0].text,
            "mocking the backend does not reproduce the cap"
        );
        assert!(snapshot.open_work[0].reality.is_empty());
        assert_eq!(snapshot.passed_checks.len(), 1);
    }

    #[test]
    fn decision_leaves_when_the_work_is_done() {
        let mut projection = Projection::default();
        let mut done = task("t_pool", "fail", 1);
        done.status = TaskStatus::Done;
        projection.tasks.push(done);
        projection.claims.push(note(
            "c_decision",
            "t_pool",
            ClaimKind::Decision,
            "Idle connections should count toward the pool cap",
            2,
        ));
        projection.claims.push(note(
            "c_rule",
            "t_pool",
            ClaimKind::Constraint,
            "do not change the public signature",
            2,
        ));

        let snapshot = render(&projection, None, 1_500);
        assert!(snapshot.open_work.is_empty());
        assert_eq!(snapshot.constraints.len(), 1);
    }
}
