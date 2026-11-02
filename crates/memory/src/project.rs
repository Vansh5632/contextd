//! Fold the event log into the current claim view.
//!
//! The projector is a pure function of shell exit codes and authored claim
//! events. File, git, and proc observations stay in the log as evidence and
//! never become tasks.

use contextd_core::claim::{
    Claim, ClaimConfidence, ClaimEvent, ClaimKind, ClaimOp, ClaimOrigin, ClaimStatus,
    FRONTIER_SUBJECT, INTENT_SUBJECT, Projection, TaskStatus,
};
use contextd_core::event::{EventSource, RawEvent};
use rusqlite::Connection;
use serde_json::Value;

/// Rebuild the materialized view from the log. Safe to call after every
/// fold-relevant insert and on daemon start.
pub fn rebuild(conn: &Connection) -> rusqlite::Result<Projection> {
    let events = store::db::get_fold_events(conn)?;
    let projection = fold(&events);
    store::db::replace_projection(conn, &projection)?;
    Ok(projection)
}

/// Deterministic fold. Input must already be sorted by `(timestamp_ms, id)`.
pub fn fold(events: &[RawEvent]) -> Projection {
    let mut projection = Projection::default();
    for event in events {
        match event.source {
            EventSource::Shell => fold_shell(&mut projection, event),
            EventSource::Claim => fold_claim(&mut projection, event),
            _ => {}
        }
    }
    projection
}

/// Oldest active task with a current non-zero reality; otherwise the active
/// task with the newest user intent; otherwise none.
pub fn focal_task(projection: &Projection) -> Option<&contextd_core::claim::Task> {
    let mut failing: Vec<&contextd_core::claim::Task> = projection
        .tasks
        .iter()
        .filter(|task| task.status == TaskStatus::Active)
        .filter(|task| projection.has_failing_reality(&task.id))
        .collect();
    failing.sort_by(|a, b| {
        a.created_at_ms
            .cmp(&b.created_at_ms)
            .then_with(|| a.id.cmp(&b.id))
    });
    if let Some(task) = failing.first() {
        return Some(*task);
    }

    projection
        .claims
        .iter()
        .filter(|claim| {
            claim.status == ClaimStatus::Current
                && claim.kind == ClaimKind::Intent
                && claim.origin == ClaimOrigin::User
        })
        .filter(|claim| {
            projection
                .task(&claim.task_id)
                .is_some_and(|task| task.status == TaskStatus::Active)
        })
        .max_by(|a, b| {
            a.updated_at_ms
                .cmp(&b.updated_at_ms)
                .then_with(|| a.id.cmp(&b.id))
        })
        .and_then(|claim| projection.task(&claim.task_id))
}

pub fn task_id_for_subject(subject: &str) -> String {
    hashed_id("t_", &[subject])
}

pub fn hashed_id(prefix: &str, parts: &[&str]) -> String {
    let mut hasher = blake3::Hasher::new();
    for (index, part) in parts.iter().enumerate() {
        if index > 0 {
            hasher.update(&[0]);
        }
        hasher.update(part.as_bytes());
    }
    let hash = hasher.finalize();
    let hex = hash.to_hex();
    format!("{prefix}{}", &hex[..32])
}

pub fn shell_subject(cwd: &str, command: &str) -> String {
    format!("{}\n{}", cwd.trim(), collapse_ws(command))
}

fn collapse_ws(value: &str) -> String {
    value.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn fold_shell(projection: &mut Projection, event: &RawEvent) {
    let Some(command) = event
        .payload
        .get("command")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|command| !command.is_empty())
        .map(collapse_ws)
    else {
        return;
    };
    let Some(exit_code) = event.payload.get("exit_code").and_then(Value::as_i64) else {
        return;
    };
    let cwd = event
        .payload
        .get("cwd")
        .and_then(Value::as_str)
        .unwrap_or("");
    let subject = shell_subject(cwd, &command);
    let task_id = task_id_for_subject(&subject);
    projection.open_task(&task_id, &event.id, event.timestamp_ms);

    let previous =
        projection.supersede_slot(&task_id, ClaimKind::Reality, &subject, event.timestamp_ms);
    let claim_id = hashed_id("c_", &[&task_id, &subject, &event.id]);
    projection.push_claim(Claim {
        id: claim_id,
        task_id: task_id.clone(),
        kind: ClaimKind::Reality,
        subject: subject.clone(),
        status: ClaimStatus::Current,
        confidence: ClaimConfidence::Observed,
        origin: ClaimOrigin::Projector,
        text: format!("{command} exited {exit_code}"),
        outcome: Some(exit_code),
        artifact_refs: Vec::new(),
        evidence_ids: vec![event.id.clone()],
        supersedes: previous,
        asserted_by_event_id: event.id.clone(),
        updated_at_ms: event.timestamp_ms,
    });

    if exit_code != 0 {
        ensure_projector_frontier(
            projection,
            &task_id,
            &command,
            &event.id,
            event.timestamp_ms,
        );
    } else {
        supersede_projector_frontier(projection, &task_id, event.timestamp_ms);
    }
}

fn ensure_projector_frontier(
    projection: &mut Projection,
    task_id: &str,
    command: &str,
    event_id: &str,
    at_ms: u64,
) {
    if let Some(frontier) =
        projection.current_slot(task_id, ClaimKind::NextAction, FRONTIER_SUBJECT)
        && frontier.origin != ClaimOrigin::Projector
    {
        return;
    }
    let previous =
        projection.supersede_slot(task_id, ClaimKind::NextAction, FRONTIER_SUBJECT, at_ms);
    let claim_id = hashed_id("c_", &[task_id, FRONTIER_SUBJECT, event_id]);
    projection.push_claim(Claim {
        id: claim_id,
        task_id: task_id.to_string(),
        kind: ClaimKind::NextAction,
        subject: FRONTIER_SUBJECT.to_string(),
        status: ClaimStatus::Current,
        confidence: ClaimConfidence::Inferred,
        origin: ClaimOrigin::Projector,
        text: format!("Re-run and inspect: {command}"),
        outcome: None,
        artifact_refs: Vec::new(),
        evidence_ids: vec![event_id.to_string()],
        supersedes: previous,
        asserted_by_event_id: event_id.to_string(),
        updated_at_ms: at_ms,
    });
}

fn supersede_projector_frontier(projection: &mut Projection, task_id: &str, at_ms: u64) {
    let Some(frontier) = projection.current_slot(task_id, ClaimKind::NextAction, FRONTIER_SUBJECT)
    else {
        return;
    };
    if frontier.origin != ClaimOrigin::Projector {
        return;
    }
    let id = frontier.id.clone();
    projection.supersede_claim(&id, at_ms);
}

fn fold_claim(projection: &mut Projection, event: &RawEvent) {
    let Ok(claim_event) = serde_json::from_value::<ClaimEvent>(event.payload.clone()) else {
        return;
    };
    if claim_event.task_id.is_empty() {
        return;
    }
    projection.open_task(&claim_event.task_id, &event.id, event.timestamp_ms);

    match claim_event.op {
        ClaimOp::CloseTask => {
            projection.close_task(&claim_event.task_id, &event.id, event.timestamp_ms);
        }
        ClaimOp::Supersede => {
            let target = claim_event
                .supersedes
                .as_deref()
                .filter(|id| !id.is_empty())
                .unwrap_or(claim_event.claim_id.as_str());
            projection.supersede_claim(target, event.timestamp_ms);
            if claim_event.text.is_some() && claim_event.kind.is_some() {
                assert_claim(projection, &claim_event, event);
            }
        }
        ClaimOp::Assert => assert_claim(projection, &claim_event, event),
    }
}

fn assert_claim(projection: &mut Projection, claim_event: &ClaimEvent, event: &RawEvent) {
    let Some(kind) = claim_event.kind else {
        return;
    };
    let Some(text) = claim_event
        .text
        .as_deref()
        .map(str::trim)
        .filter(|text| !text.is_empty())
    else {
        return;
    };
    let subject = claim_event.subject.clone().unwrap_or_else(|| match kind {
        ClaimKind::Intent => INTENT_SUBJECT.to_string(),
        ClaimKind::NextAction => FRONTIER_SUBJECT.to_string(),
        _ => claim_event.claim_id.clone(),
    });
    let mut superseded = None;
    if kind.unique_slot() {
        superseded =
            projection.supersede_slot(&claim_event.task_id, kind, &subject, event.timestamp_ms);
    }
    if let Some(explicit) = claim_event.supersedes.as_deref() {
        projection.supersede_claim(explicit, event.timestamp_ms);
        if superseded.is_none() {
            superseded = Some(explicit.to_string());
        }
    }
    let confidence = claim_event.confidence.unwrap_or(match claim_event.origin {
        ClaimOrigin::User => ClaimConfidence::Stated,
        ClaimOrigin::Agent => ClaimConfidence::Inferred,
        ClaimOrigin::Projector => ClaimConfidence::Observed,
    });
    projection.push_claim(Claim {
        id: claim_event.claim_id.clone(),
        task_id: claim_event.task_id.clone(),
        kind,
        subject,
        status: ClaimStatus::Current,
        confidence,
        origin: claim_event.origin,
        text: text.to_string(),
        outcome: claim_event.outcome,
        artifact_refs: claim_event.artifact_refs.clone(),
        evidence_ids: claim_event.evidence_ids.clone(),
        supersedes: superseded.or(claim_event.supersedes.clone()),
        asserted_by_event_id: event.id.clone(),
        updated_at_ms: event.timestamp_ms,
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use contextd_core::claim::{ClaimEvent, ClaimKind, ClaimOp, ClaimOrigin};
    use contextd_core::event::{EventSource, ProcessedEvent};
    use contextd_core::test_utils::test_config_in_memory;
    use serde_json::json;
    use store::Store;
    use store::db::{insert_claim_event, insert_event};

    const PYTEST: &str = "python -m pytest -p no:cacheprovider tests/test_pool_counts.py";
    const CWD: &str = "/repo";

    fn shell(id: &str, ts: u64, command: &str, exit: i64) -> RawEvent {
        RawEvent {
            id: id.to_string(),
            timestamp_ms: ts,
            source: EventSource::Shell,
            payload: json!({"command": command, "exit_code": exit, "cwd": CWD}),
        }
    }

    fn git_commit(id: &str, ts: u64) -> RawEvent {
        RawEvent {
            id: id.to_string(),
            timestamp_ms: ts,
            source: EventSource::Git,
            payload: json!({"action": "commit", "subject": "fix csv export off-by-one"}),
        }
    }

    fn file_touch(id: &str, ts: u64, path: &str) -> RawEvent {
        RawEvent {
            id: id.to_string(),
            timestamp_ms: ts,
            source: EventSource::FileSystem,
            payload: json!({"path": path}),
        }
    }

    fn authored(id: &str, ts: u64, body: ClaimEvent) -> RawEvent {
        RawEvent {
            id: id.to_string(),
            timestamp_ms: ts,
            source: EventSource::Claim,
            payload: serde_json::to_value(body).unwrap(),
        }
    }

    fn briefing_selection_fixture() -> Vec<RawEvent> {
        let mut events = Vec::new();
        events.push(shell("pytest-fail", 1_000, PYTEST, 1));
        for i in 1..30 {
            if i % 2 == 0 {
                events.push(file_touch(
                    &format!("file-{i}"),
                    1_000 + i,
                    "/repo/src/export.rs",
                ));
            } else {
                events.push(shell(
                    &format!("noise-{i}"),
                    1_000 + i,
                    &format!("sed -i s/x/y/ file{i}.py"),
                    0,
                ));
            }
        }
        events.push(git_commit("session-b-commit", 1_031));
        events
    }

    fn current(projection: &Projection, kind: ClaimKind) -> Vec<&Claim> {
        projection
            .claims
            .iter()
            .filter(|claim| claim.status == ClaimStatus::Current && claim.kind == kind)
            .collect()
    }

    #[test]
    fn oldest_failure_is_focal() {
        let events = briefing_selection_fixture();
        let projection = fold(&events);
        let focal = focal_task(&projection).expect("pytest should be focal");
        let reality = current(&projection, ClaimKind::Reality)
            .into_iter()
            .find(|claim| claim.task_id == focal.id && claim.outcome == Some(1))
            .expect("failing reality");
        assert_eq!(reality.outcome, Some(1));
        assert!(reality.text.contains(PYTEST), "{}", reality.text);
        assert_eq!(reality.evidence_ids, vec!["pytest-fail".to_string()]);
        let next = current(&projection, ClaimKind::NextAction)
            .into_iter()
            .find(|claim| claim.task_id == focal.id)
            .expect("next action");
        assert!(next.text.contains(PYTEST), "{}", next.text);
        assert!(
            projection
                .claims
                .iter()
                .all(|claim| !claim.text.contains("csv export")),
            "commit must not become a claim"
        );
    }

    #[test]
    fn commit_does_not_close() {
        let events = briefing_selection_fixture();
        let projection = fold(&events);
        let focal = focal_task(&projection).unwrap();
        assert_eq!(focal.status, TaskStatus::Active);
        assert!(
            projection
                .tasks
                .iter()
                .all(|task| task.opened_by_event_id != "session-b-commit")
        );
    }

    #[test]
    fn exit_zero_supersedes() {
        let events = vec![shell("fail", 1, PYTEST, 1), shell("pass", 2, PYTEST, 0)];
        let projection = fold(&events);
        let failing = projection
            .claims
            .iter()
            .find(|claim| claim.asserted_by_event_id == "fail")
            .unwrap();
        assert_eq!(failing.status, ClaimStatus::Superseded);
        assert!(!failing.text.contains("exited 1") || failing.status == ClaimStatus::Superseded);
        let current_fail = current(&projection, ClaimKind::Reality)
            .into_iter()
            .find(|claim| claim.outcome == Some(1));
        assert!(current_fail.is_none());
        assert_eq!(projection.tasks[0].status, TaskStatus::Active);
        let pass = current(&projection, ClaimKind::Reality)
            .into_iter()
            .find(|claim| claim.outcome == Some(0));
        assert!(pass.is_some());
        assert!(current(&projection, ClaimKind::NextAction).is_empty());
    }

    #[test]
    fn two_failures_oldest_wins() {
        let events = vec![
            shell("old", 1, PYTEST, 1),
            shell("new", 2, "python -m pytest tests/test_export.py", 1),
        ];
        let projection = fold(&events);
        let focal = focal_task(&projection).unwrap();
        let reality = current(&projection, ClaimKind::Reality)
            .into_iter()
            .find(|claim| claim.task_id == focal.id)
            .unwrap();
        assert!(reality.text.contains("test_pool_counts"));
        assert_eq!(projection.tasks.len(), 2);
        let other = projection
            .tasks
            .iter()
            .find(|task| task.id != focal.id)
            .unwrap();
        assert!(projection.has_failing_reality(&other.id));
    }

    #[test]
    fn command_key_is_exact() {
        let events = vec![
            shell("a", 1, PYTEST, 1),
            shell(
                "b",
                2,
                "python -m pytest -p no:cacheprovider tests/test_other.py",
                0,
            ),
        ];
        let projection = fold(&events);
        let still_failing = current(&projection, ClaimKind::Reality)
            .into_iter()
            .any(|claim| claim.outcome == Some(1) && claim.text.contains("test_pool_counts"));
        assert!(still_failing);
    }

    #[test]
    fn grep_error_is_not_a_failure() {
        let events = vec![shell("g", 1, "grep error log.txt", 0)];
        let projection = fold(&events);
        assert!(
            current(&projection, ClaimKind::Reality)
                .into_iter()
                .all(|claim| claim.outcome != Some(1))
        );
        assert!(focal_task(&projection).is_none());
    }

    #[test]
    fn intent_slot_supersedes() {
        let fail = shell("fail", 1, PYTEST, 1);
        let task_id = task_id_for_subject(&shell_subject(CWD, PYTEST));
        let events = vec![
            fail,
            authored(
                "i1",
                2,
                ClaimEvent {
                    op: ClaimOp::Assert,
                    claim_id: "c_i1".into(),
                    task_id: task_id.clone(),
                    kind: Some(ClaimKind::Intent),
                    subject: Some(INTENT_SUBJECT.into()),
                    text: Some("first goal".into()),
                    confidence: Some(ClaimConfidence::Stated),
                    origin: ClaimOrigin::User,
                    outcome: None,
                    artifact_refs: Vec::new(),
                    evidence_ids: Vec::new(),
                    supersedes: None,
                },
            ),
            authored(
                "i2",
                3,
                ClaimEvent {
                    op: ClaimOp::Assert,
                    claim_id: "c_i2".into(),
                    task_id: task_id.clone(),
                    kind: Some(ClaimKind::Intent),
                    subject: Some(INTENT_SUBJECT.into()),
                    text: Some("second goal".into()),
                    confidence: Some(ClaimConfidence::Stated),
                    origin: ClaimOrigin::User,
                    outcome: None,
                    artifact_refs: Vec::new(),
                    evidence_ids: Vec::new(),
                    supersedes: None,
                },
            ),
        ];
        let projection = fold(&events);
        let intents: Vec<_> = current(&projection, ClaimKind::Intent)
            .into_iter()
            .map(|claim| claim.text.as_str())
            .collect();
        assert_eq!(intents, vec!["second goal"]);
        assert!(
            projection
                .claims
                .iter()
                .any(|claim| claim.id == "c_i1" && claim.status == ClaimStatus::Superseded)
        );
    }

    #[test]
    fn decisions_do_not_merge() {
        let fail = shell("fail", 1, PYTEST, 1);
        let task_id = task_id_for_subject(&shell_subject(CWD, PYTEST));
        let mk = |id: &str, ts: u64, text: &str| {
            authored(
                id,
                ts,
                ClaimEvent {
                    op: ClaimOp::Assert,
                    claim_id: id.into(),
                    task_id: task_id.clone(),
                    kind: Some(ClaimKind::Decision),
                    subject: Some(id.into()),
                    text: Some(text.into()),
                    confidence: Some(ClaimConfidence::Stated),
                    origin: ClaimOrigin::User,
                    outcome: None,
                    artifact_refs: Vec::new(),
                    evidence_ids: Vec::new(),
                    supersedes: None,
                },
            )
        };
        let projection = fold(&[
            fail,
            mk("d1", 2, "keep the signature"),
            mk("d2", 3, "no redis"),
        ]);
        assert_eq!(current(&projection, ClaimKind::Decision).len(), 2);
    }

    #[test]
    fn claim_with_unknown_task_opens_it() {
        let events = vec![authored(
            "e1",
            1,
            ClaimEvent {
                op: ClaimOp::Assert,
                claim_id: "c_new".into(),
                task_id: "t_brand_new".into(),
                kind: Some(ClaimKind::Intent),
                subject: Some(INTENT_SUBJECT.into()),
                text: Some("fix the limiter".into()),
                confidence: Some(ClaimConfidence::Stated),
                origin: ClaimOrigin::User,
                outcome: None,
                artifact_refs: Vec::new(),
                evidence_ids: Vec::new(),
                supersedes: None,
            },
        )];
        let projection = fold(&events);
        assert!(projection.task("t_brand_new").is_some());
        assert_eq!(focal_task(&projection).unwrap().id, "t_brand_new");
    }

    #[tokio::test]
    async fn authored_claims_survive_rebuild() {
        let store = Store::open(&test_config_in_memory()).unwrap();
        let fail = ProcessedEvent::new(shell("pytest-fail", 1_000, PYTEST, 1), 0.6);
        let task_id = task_id_for_subject(&shell_subject(CWD, PYTEST));
        let finding = ClaimEvent {
            op: ClaimOp::Assert,
            claim_id: "c_find".into(),
            task_id: task_id.clone(),
            kind: Some(ClaimKind::Reality),
            subject: Some("finding".into()),
            text: Some("the cap check is in _assign_requests_to_connections".into()),
            confidence: Some(ClaimConfidence::Inferred),
            origin: ClaimOrigin::Agent,
            outcome: None,
            artifact_refs: Vec::new(),
            evidence_ids: vec!["pytest-fail".into()],
            supersedes: None,
        };
        let constraint = ClaimEvent {
            op: ClaimOp::Assert,
            claim_id: "c_con".into(),
            task_id,
            kind: Some(ClaimKind::Constraint),
            subject: Some("c_con".into()),
            text: Some("do not change the public signature".into()),
            confidence: Some(ClaimConfidence::Stated),
            origin: ClaimOrigin::User,
            outcome: None,
            artifact_refs: Vec::new(),
            evidence_ids: Vec::new(),
            supersedes: None,
        };
        let finding_event = ProcessedEvent::new(authored("e-find", 1_001, finding), 1.0);
        let constraint_event = ProcessedEvent::new(authored("e-con", 1_002, constraint), 1.0);
        let original_payloads = [
            fail.raw.payload.clone(),
            finding_event.raw.payload.clone(),
            constraint_event.raw.payload.clone(),
        ];
        let conn = store.writer().await;
        insert_event(&conn, &fail).unwrap();
        insert_claim_event(&conn, &finding_event).unwrap();
        insert_claim_event(&conn, &constraint_event).unwrap();
        rebuild(&conn).unwrap();
        rebuild(&conn).unwrap();
        let loaded = store::db::load_projection(&conn).unwrap();
        assert!(
            loaded
                .claims
                .iter()
                .any(|claim| claim.id == "c_find" && claim.status == ClaimStatus::Current)
        );
        assert!(
            loaded
                .claims
                .iter()
                .any(|claim| claim.id == "c_con" && claim.status == ClaimStatus::Current)
        );
        let events = store::db::get_fold_events(&conn).unwrap();
        assert_eq!(events[0].payload, original_payloads[0]);
        assert_eq!(events[1].payload, original_payloads[1]);
        assert_eq!(events[2].payload, original_payloads[2]);
    }
}
