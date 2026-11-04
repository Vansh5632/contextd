//! Quote load-bearing notes and admit them through the Component 1 gate.
//!
//! Speech-act notes ("Decision:", "Ruled out", a grounded `sed`/`grep`
//! conclusion) keep the offline episode's behaviour. A coding agent does not
//! write those prefixes, so a note marked `role: conclusion` is classified
//! from its own sentences and the tool output it cites. Nothing here is marked
//! observed: the projector still owns exit codes.
//!
//! Extraction runs at startup, after a note is stored, and before `context_now`.
//! It does not run on the shell hook.

use std::collections::{BTreeMap, BTreeSet};

use contextd_core::claim::{
    ClaimConfidence, ClaimEvent, ClaimKind, ClaimOp, ClaimOrigin, FRONTIER_SUBJECT, Projection,
    validate, validate_against_projection,
};
use contextd_core::event::{EventSource, ProcessedEvent, RawEvent};
use contextd_core::protocol::ObserveKind;
use memory::project::{fold, shell_subject, task_id_for_subject};
use rusqlite::Connection;
use serde::Deserialize;
use serde_json::{Value, json};

const MIN_OVERLAP: usize = 2;
const MAX_TEXT: usize = 500;
const MAX_OUTPUT_LINES: usize = 200;
const MAX_OUTPUT_CHARS: usize = 8_000;
const TRUNCATION_MARK: &str = "[output truncated]";

const STOP: &[&str] = &[
    "about", "after", "also", "and", "are", "because", "been", "before", "but", "for", "from",
    "have", "into", "only", "that", "than", "the", "their", "them", "then", "this", "with",
    "would",
];

#[derive(Debug, Clone, Deserialize)]
pub struct Candidate {
    pub claim_id: String,
    pub kind: String,
    pub origin: String,
    pub confidence: String,
    pub text: String,
    #[serde(default)]
    pub subject: Option<String>,
    #[serde(default)]
    pub evidence_ids: Vec<String>,
    pub source_text: String,
    #[serde(default)]
    pub support_text: Option<String>,
    pub task_subject: String,
}

#[derive(Debug, Clone)]
struct Draft {
    claim_id: String,
    kind: String,
    origin: String,
    confidence: String,
    text: String,
    subject: Option<String>,
    evidence_ids: Vec<String>,
    source_text: String,
    support_text: Option<String>,
    task_subject: String,
}

impl Draft {
    fn candidate(&self) -> Candidate {
        Candidate {
            claim_id: self.claim_id.clone(),
            kind: self.kind.clone(),
            origin: self.origin.clone(),
            confidence: self.confidence.clone(),
            text: self.text.clone(),
            subject: self.subject.clone(),
            evidence_ids: self.evidence_ids.clone(),
            source_text: self.source_text.clone(),
            support_text: self.support_text.clone(),
            task_subject: self.task_subject.clone(),
        }
    }
}

#[derive(Debug, Default, serde::Serialize)]
pub struct HandoffReport {
    pub accepted: Vec<Value>,
    pub rejected: Vec<Value>,
    pub skipped: Vec<Value>,
}

/// One coding-agent observation, already given an id by the daemon.
pub struct Observation {
    pub kind: ObserveKind,
    pub session: String,
    pub command: Option<String>,
    pub cwd: Option<String>,
    pub exit_code: Option<i64>,
    pub output: Option<String>,
    pub text: Option<String>,
    pub support_event_id: Option<String>,
    pub replaces_text: Option<String>,
    pub id: String,
    pub timestamp_ms: u64,
}

pub fn observation_event(obs: Observation) -> Result<RawEvent, String> {
    let session = obs.session.trim().to_string();
    let payload = match obs.kind {
        ObserveKind::Tool => {
            let command = obs
                .command
                .as_deref()
                .map(str::trim)
                .filter(|command| !command.is_empty())
                .ok_or_else(|| "a tool observation needs a command".to_string())?;
            let exit_code = obs
                .exit_code
                .ok_or_else(|| "a tool observation needs an exit code".to_string())?;
            json!({
                "command": command,
                "exit_code": exit_code,
                "cwd": obs.cwd.unwrap_or_default(),
                "session": session,
                "output": truncate_output(obs.output.as_deref().unwrap_or("")),
            })
        }
        ObserveKind::Conclusion
        | ObserveKind::User
        | ObserveKind::Retract
        | ObserveKind::Update => {
            let text = obs
                .text
                .as_deref()
                .map(str::trim)
                .filter(|text| !text.is_empty())
                .ok_or_else(|| "an observation note needs text".to_string())?;
            let mut payload = json!({
                "text": text,
                "session": session,
            });
            match obs.kind {
                ObserveKind::Conclusion => {
                    payload["role"] = json!("conclusion");
                    payload["speaker"] = json!("agent");
                }
                ObserveKind::User => {
                    payload["speaker"] = json!("user");
                }
                ObserveKind::Retract => {
                    payload["role"] = json!("retract");
                    payload["speaker"] = json!("agent");
                }
                ObserveKind::Update => {
                    let replaces = obs
                        .replaces_text
                        .as_deref()
                        .map(str::trim)
                        .filter(|text| !text.is_empty())
                        .ok_or_else(|| "an update needs the claim text it replaces".to_string())?;
                    payload["role"] = json!("update");
                    payload["speaker"] = json!("agent");
                    payload["replaces_text"] = json!(replaces);
                }
                ObserveKind::Tool => unreachable!(),
            }
            if let Some(support) = obs
                .support_event_id
                .as_deref()
                .map(str::trim)
                .filter(|id| !id.is_empty())
            {
                payload["support_event_id"] = json!(support);
            }
            payload
        }
    };
    let source = if obs.kind == ObserveKind::Tool {
        EventSource::Shell
    } else {
        EventSource::Note
    };
    Ok(RawEvent {
        id: obs.id,
        timestamp_ms: obs.timestamp_ms,
        source,
        payload,
    })
}

/// Admit already-proposed candidates. This is the offline gate: it does not
/// read notes again, so a hand-built candidate list stays under the caller's
/// control.
pub fn admit_candidates(log: &mut Vec<RawEvent>, candidates: Vec<Candidate>) -> HandoffReport {
    sort_log(log);
    let mut report = HandoffReport::default();
    let mut seen = seed_seen(log);
    let mut projection = fold(log);
    for candidate in candidates {
        accept_or_record(log, &candidate, &mut projection, &mut seen, &mut report);
    }
    report
}

/// Quote the log and admit what the gate allows. Safe to run again: a note
/// that already has a claim is left alone, and an identical quote is not
/// inserted twice.
pub fn apply_extraction(log: &mut Vec<RawEvent>) -> HandoffReport {
    sort_log(log);
    let original_len = log.len();
    let applied = cited_notes(&log[..original_len]);
    let mut seen = seed_seen(&log[..original_len]);
    let mut projection = fold(log);
    let mut report = HandoffReport::default();

    for index in 0..original_len {
        let event = log[index].clone();
        if event.source != EventSource::Note {
            continue;
        }
        if applied.contains(&event.id) {
            continue;
        }
        let text = payload_str(&event, "text").trim().to_string();
        if text.is_empty() {
            skip(&mut report, &event.id, "empty note");
            continue;
        }
        let role = payload_str(&event, "role");
        if role == "retract" || starts_word(&text.to_lowercase(), "retract") {
            retract_note(log, &event, &text, &mut projection, &mut report);
            continue;
        }
        if role == "update" {
            update_note(log, &event, &text, &mut projection, &mut seen, &mut report);
            continue;
        }

        let Some(task_event) = note_task(log, &event) else {
            skip(
                &mut report,
                &event.id,
                "session has no command that is still failing",
            );
            continue;
        };
        let task = task_for(task_event);
        let support = support_event(log, &event, index).cloned();
        let drafts = drafts_for_note(&event, &text, support.as_ref(), &task);
        if drafts.is_empty() {
            skip(
                &mut report,
                &event.id,
                &skip_reason(&text, support.as_ref()),
            );
            continue;
        }
        for draft in drafts {
            if !draft.source_text.contains(&draft.text) || draft.text.is_empty() {
                skip(
                    &mut report,
                    &event.id,
                    "candidate is not a verbatim span of the note",
                );
                continue;
            }
            if !(1..=MAX_TEXT).contains(&draft.text.chars().count()) {
                skip(
                    &mut report,
                    &event.id,
                    "candidate text is empty or longer than 500 characters",
                );
                continue;
            }
            if draft.kind == "reality"
                && content_overlap(&draft.text, draft.support_text.as_deref().unwrap_or(""))
                    < MIN_OVERLAP
            {
                skip(
                    &mut report,
                    &event.id,
                    "inference is not grounded in the preceding tool output",
                );
                continue;
            }
            accept_or_record(
                log,
                &draft.candidate(),
                &mut projection,
                &mut seen,
                &mut report,
            );
        }
    }
    report
}

/// Read the daemon log, admit new claims, and rebuild the projection.
pub fn run_handoff(conn: &Connection) -> rusqlite::Result<HandoffReport> {
    let mut events = store::db::get_handoff_events(conn)?;
    if !events.iter().any(|event| event.source == EventSource::Note) {
        return Ok(HandoffReport::default());
    }
    let before: BTreeSet<String> = events.iter().map(|event| event.id.clone()).collect();
    let report = apply_extraction(&mut events);
    let mut added = false;
    for event in events {
        if before.contains(&event.id) {
            continue;
        }
        let processed = ProcessedEvent::new(event, 1.0);
        store::db::insert_claim_event(conn, &processed)?;
        added = true;
    }
    if added {
        memory::project::rebuild(conn)?;
    }
    Ok(report)
}

fn accept_or_record(
    log: &mut Vec<RawEvent>,
    candidate: &Candidate,
    projection: &mut Projection,
    seen: &mut Vec<(String, String)>,
    report: &mut HandoffReport,
) {
    match admit(candidate, projection, seen, log) {
        Ok(event) => {
            seen.push((
                candidate.kind.clone(),
                collapse(&candidate.text).to_lowercase(),
            ));
            push_event(log, event, format!("c2_{}", candidate.claim_id));
            *projection = fold(log);
            report.accepted.push(json!({
                "claim_id": candidate.claim_id,
                "kind": candidate.kind,
                "confidence": candidate.confidence,
                "origin": candidate.origin,
                "text": candidate.text,
                "evidence_ids": candidate.evidence_ids,
            }));
        }
        Err(reason) if reason.contains("duplicate") => {
            skip(report, &candidate.claim_id, &reason);
        }
        Err(reason) => report.rejected.push(json!({
            "claim_id": candidate.claim_id,
            "reason": reason,
        })),
    }
}

fn retract_note(
    log: &mut Vec<RawEvent>,
    note: &RawEvent,
    text: &str,
    projection: &mut Projection,
    report: &mut HandoffReport,
) {
    let Some(target) = longest_quoted_claim(projection, text) else {
        skip(report, &note.id, "retract does not quote a current claim");
        return;
    };
    if target.origin == ClaimOrigin::Projector || target.confidence == ClaimConfidence::Observed {
        skip(report, &note.id, "cannot retract an observed reality");
        return;
    }
    let origin = if target.origin == ClaimOrigin::User {
        ClaimOrigin::User
    } else {
        ClaimOrigin::Agent
    };
    let event = ClaimEvent {
        op: ClaimOp::Supersede,
        claim_id: format!("retract_{}", note.id),
        task_id: target.task_id.clone(),
        kind: None,
        subject: None,
        text: None,
        confidence: None,
        origin,
        outcome: None,
        artifact_refs: Vec::new(),
        evidence_ids: vec![note.id.clone()],
        supersedes: Some(target.id.clone()),
    };
    if let Err(err) = validate(origin, &event)
        .map_err(|err| err.to_string())
        .and_then(|_| {
            validate_against_projection(origin, &event, projection).map_err(|err| err.to_string())
        })
    {
        skip(report, &note.id, &err);
        return;
    }
    let target_id = target.id.clone();
    push_event(log, event, format!("c2_retract_{}", note.id));
    *projection = fold(log);
    report.accepted.push(json!({
        "claim_id": format!("retract_{}", note.id),
        "kind": "retract",
        "text": target_id,
        "evidence_ids": [note.id],
    }));
}

fn update_note(
    log: &mut Vec<RawEvent>,
    note: &RawEvent,
    text: &str,
    projection: &mut Projection,
    seen: &mut Vec<(String, String)>,
    report: &mut HandoffReport,
) {
    let replaces = payload_str(note, "replaces_text");
    let Some(target) = current_with_text(projection, &replaces) else {
        skip(report, &note.id, "update does not name a current claim");
        return;
    };
    if target.origin == ClaimOrigin::Projector || target.confidence == ClaimConfidence::Observed {
        skip(report, &note.id, "cannot retract an observed reality");
        return;
    }
    let Some(task_event) = note_task(log, note) else {
        skip(
            report,
            &note.id,
            "session has no command that is still failing",
        );
        return;
    };
    let task = task_for(task_event);
    let support = support_by_id(log, note);
    let mut forced = note.clone();
    if let Some(object) = forced.payload.as_object_mut() {
        object.insert("role".into(), json!("conclusion"));
    }
    let drafts = natural_drafts(&forced, text, support, &task);
    if drafts.is_empty() {
        skip(report, &note.id, "update is not a grounded claim");
        return;
    }
    let mut admitted = Vec::new();
    let mut scratch_seen = seen.clone();
    for draft in &drafts {
        match admit(&draft.candidate(), projection, &scratch_seen, log) {
            Ok(_) => {
                scratch_seen.push((draft.kind.clone(), collapse(&draft.text).to_lowercase()));
                admitted.push(draft.clone());
            }
            Err(reason) => {
                skip(report, &note.id, &reason);
                return;
            }
        }
    }
    let origin = if target.origin == ClaimOrigin::User {
        ClaimOrigin::User
    } else {
        ClaimOrigin::Agent
    };
    let retract = ClaimEvent {
        op: ClaimOp::Supersede,
        claim_id: format!("retract_{}", note.id),
        task_id: target.task_id.clone(),
        kind: None,
        subject: None,
        text: None,
        confidence: None,
        origin,
        outcome: None,
        artifact_refs: Vec::new(),
        evidence_ids: vec![note.id.clone()],
        supersedes: Some(target.id.clone()),
    };
    if let Err(err) = validate(origin, &retract)
        .map_err(|err| err.to_string())
        .and_then(|_| {
            validate_against_projection(origin, &retract, projection).map_err(|err| err.to_string())
        })
    {
        skip(report, &note.id, &err);
        return;
    }
    push_event(log, retract, format!("c2_retract_{}", note.id));
    *projection = fold(log);
    for draft in admitted {
        accept_or_record(log, &draft.candidate(), projection, seen, report);
    }
}

fn admit(
    candidate: &Candidate,
    projection: &Projection,
    seen: &[(String, String)],
    events: &[RawEvent],
) -> Result<ClaimEvent, String> {
    let origin = ClaimOrigin::parse(&candidate.origin)
        .ok_or_else(|| format!("unknown origin {}", candidate.origin))?;
    if origin == ClaimOrigin::Projector {
        return Err("projector claims are derived from the log, not extracted".into());
    }
    let kind = ClaimKind::parse(&candidate.kind)
        .ok_or_else(|| format!("unknown kind {}", candidate.kind))?;
    let confidence = ClaimConfidence::parse(&candidate.confidence)
        .ok_or_else(|| format!("unknown confidence {}", candidate.confidence))?;
    let text = candidate.text.trim();
    if candidate.evidence_ids.is_empty() {
        return Err("claim has no evidence id".into());
    }
    for id in &candidate.evidence_ids {
        if event_by_id(events, id).is_none() {
            return Err(format!("evidence id {id} is not in the event log"));
        }
    }
    let note = event_by_id(events, &candidate.evidence_ids[0])
        .filter(|event| event.source == EventSource::Note)
        .ok_or_else(|| "first evidence id is not a note".to_string())?;
    let note_text = payload_str(note, "text");
    if text.is_empty() || !note_text.contains(text) || candidate.source_text != note_text {
        return Err("claim text is not a verbatim span of the source note".into());
    }
    if candidate.task_subject.trim().is_empty() {
        return Err("claim has no task subject".into());
    }
    if !failing_subjects(events).contains(candidate.task_subject.trim()) {
        return Err("claim is not attached to a command that is still failing".into());
    }
    let key = (candidate.kind.clone(), collapse(text).to_lowercase());
    if seen.iter().any(|item| item == &key) {
        return Err("duplicate of a claim already admitted".into());
    }
    if kind == ClaimKind::Reality {
        let support_event = candidate.evidence_ids.iter().find_map(|id| {
            event_by_id(events, id).filter(|event| event.source == EventSource::Shell)
        });
        let Some(support_event) = support_event else {
            return Err("inferred reality does not cite a shell event".into());
        };
        let output = payload_str(support_event, "output");
        if candidate
            .support_text
            .as_deref()
            .is_some_and(|support| support != output)
        {
            return Err("support text does not match the cited shell output".into());
        }
        if content_overlap(text, &output) < MIN_OVERLAP {
            return Err("inferred reality is not supported by the cited tool output".into());
        }
    }
    if confidence == ClaimConfidence::Observed {
        return Err("an extracted claim cannot be marked observed".into());
    }
    let subject = if kind == ClaimKind::NextAction {
        if candidate.subject.as_deref() != Some(FRONTIER_SUBJECT) {
            return Err("a next action must use the frontier subject".into());
        }
        FRONTIER_SUBJECT.to_string()
    } else {
        candidate
            .subject
            .clone()
            .filter(|subject| !subject.is_empty())
            .unwrap_or_else(|| candidate.claim_id.clone())
    };
    let event = ClaimEvent {
        op: ClaimOp::Assert,
        claim_id: candidate.claim_id.clone(),
        task_id: task_id_for_subject(&candidate.task_subject),
        kind: Some(kind),
        subject: Some(subject),
        text: Some(text.to_string()),
        confidence: Some(confidence),
        origin,
        outcome: None,
        artifact_refs: Vec::new(),
        evidence_ids: candidate.evidence_ids.clone(),
        supersedes: None,
    };
    validate(origin, &event).map_err(|err| err.to_string())?;
    validate_against_projection(origin, &event, projection).map_err(|err| err.to_string())?;
    Ok(event)
}

fn drafts_for_note(
    note: &RawEvent,
    text: &str,
    support: Option<&RawEvent>,
    task: &str,
) -> Vec<Draft> {
    if has_word(text, "constraint") {
        let mut drafts = Vec::new();
        for (index, sentence) in sentences(text).into_iter().enumerate() {
            if normative(sentence) {
                drafts.push(speech_draft(
                    note,
                    sentence,
                    task,
                    "constraint",
                    "user",
                    "stated",
                    &suffix_for(index),
                    None,
                    None,
                ));
            }
        }
        return drafts;
    }
    if starts_word(&text.to_lowercase(), "decision") {
        return vec![speech_draft(
            note, text, task, "decision", "agent", "inferred", "", None, None,
        )];
    }
    if contains_phrase(&text.to_lowercase(), "ruled out")
        || has_word(&text.to_lowercase(), "rejected")
    {
        return vec![speech_draft(
            note,
            text,
            task,
            "ruled_out",
            "agent",
            "inferred",
            "",
            None,
            None,
        )];
    }
    if starts_phrase(&text.to_lowercase(), "next step") {
        return vec![speech_draft(
            note,
            text,
            task,
            "next_action",
            "agent",
            "inferred",
            "",
            Some(FRONTIER_SUBJECT.to_string()),
            None,
        )];
    }
    if contains_phrase(&text.to_lowercase(), "out of scope") {
        return vec![speech_draft(
            note,
            text,
            task,
            "unknown",
            "agent",
            "hypothesis",
            "",
            None,
            None,
        )];
    }
    let lower = text.to_lowercase();
    if starts_phrase(&lower, "first suspect")
        || starts_phrase(&lower, "next suspect")
        || starts_word(&lower, "idea")
    {
        return Vec::new();
    }
    if payload_str(note, "speaker") == "user" {
        let drafts = user_constraints(note, text, task);
        if !drafts.is_empty() {
            return drafts;
        }
    }
    if payload_str(note, "role") == "conclusion" {
        return natural_drafts(note, text, support, task);
    }
    if is_source_read(support)
        && let Some(output) = support.map(|event| payload_str(event, "output"))
        && content_overlap(text, &output) >= MIN_OVERLAP
    {
        return vec![speech_draft(
            note, text, task, "reality", "agent", "inferred", "", None, support,
        )];
    }
    Vec::new()
}

fn user_constraints(note: &RawEvent, text: &str, task: &str) -> Vec<Draft> {
    let mut drafts = Vec::new();
    for (index, sentence) in sentences(text).into_iter().enumerate() {
        if normative(sentence) {
            drafts.push(speech_draft(
                note,
                sentence,
                task,
                "constraint",
                "user",
                "stated",
                &suffix_for(index),
                None,
                None,
            ));
        }
    }
    drafts
}

fn natural_drafts(
    note: &RawEvent,
    text: &str,
    support: Option<&RawEvent>,
    task: &str,
) -> Vec<Draft> {
    let output = support
        .filter(|event| event.source == EventSource::Shell)
        .map(|event| payload_str(event, "output"))
        .unwrap_or_default();
    let mut drafts = Vec::new();
    for (index, sentence) in sentences(text).into_iter().enumerate() {
        let Some(kind) = classify_natural(sentence, &output) else {
            continue;
        };
        let suffix = suffix_for(index);
        let draft = match kind {
            Natural::Hypothesis => speech_draft(
                note,
                sentence,
                task,
                "unknown",
                "agent",
                "hypothesis",
                &suffix,
                None,
                None,
            ),
            Natural::NextAction => speech_draft(
                note,
                sentence,
                task,
                "next_action",
                "agent",
                "inferred",
                &suffix,
                Some(FRONTIER_SUBJECT.to_string()),
                None,
            ),
            Natural::RuledOut => speech_draft(
                note,
                sentence,
                task,
                "ruled_out",
                "agent",
                "inferred",
                &suffix,
                None,
                support.filter(|event| event.source == EventSource::Shell),
            ),
            Natural::Reality => speech_draft(
                note,
                sentence,
                task,
                "reality",
                "agent",
                "inferred",
                &suffix,
                None,
                support.filter(|event| event.source == EventSource::Shell),
            ),
        };
        drafts.push(draft);
    }
    drafts
}

enum Natural {
    Hypothesis,
    NextAction,
    RuledOut,
    Reality,
}

fn classify_natural(sentence: &str, support: &str) -> Option<Natural> {
    if sentence.trim().ends_with('?') {
        return None;
    }
    let lower = sentence.to_lowercase();
    if has_word(&lower, "might")
        || has_word(&lower, "maybe")
        || lower.contains("not sure")
        || lower.contains("could be")
    {
        return Some(Natural::Hypothesis);
    }
    if lower.starts_with("i'll") || lower.starts_with("i will") || lower.starts_with("next i'll") {
        return Some(Natural::NextAction);
    }
    if content_overlap(sentence, support) < MIN_OVERLAP {
        return None;
    }
    if lower.contains("not the cause")
        || lower.contains("isn't the cause")
        || lower.contains("is not the cause")
        || lower.contains("not involved")
        || lower.contains("not the problem")
    {
        return Some(Natural::RuledOut);
    }
    Some(Natural::Reality)
}

#[allow(clippy::too_many_arguments)]
fn speech_draft(
    note: &RawEvent,
    text: &str,
    task: &str,
    kind: &str,
    origin: &str,
    confidence: &str,
    suffix: &str,
    subject: Option<String>,
    support: Option<&RawEvent>,
) -> Draft {
    let mut evidence_ids = vec![note.id.clone()];
    let mut support_text = None;
    if let Some(support) = support {
        if support.id != note.id {
            evidence_ids.push(support.id.clone());
        }
        support_text = Some(payload_str(support, "output"));
    }
    Draft {
        claim_id: format!("{}{suffix}", note.id),
        kind: kind.to_string(),
        origin: origin.to_string(),
        confidence: confidence.to_string(),
        text: text.trim().to_string(),
        subject,
        evidence_ids,
        source_text: payload_str(note, "text"),
        support_text,
        task_subject: task.to_string(),
    }
}

fn note_task<'a>(events: &'a [RawEvent], note: &RawEvent) -> Option<&'a RawEvent> {
    let session = payload_str(note, "session");
    if !open_sessions(events).contains(&session) {
        return None;
    }
    failing_command(events, Some(&session))
}

fn skip_reason(text: &str, support: Option<&RawEvent>) -> String {
    let lower = text.to_lowercase();
    if starts_phrase(&lower, "first suspect")
        || starts_phrase(&lower, "next suspect")
        || starts_word(&lower, "idea")
    {
        return "open hypothesis; a later note has to close it before it is current".into();
    }
    if is_source_read(support) {
        return "note follows a file read but is not grounded in that output".into();
    }
    "no load-bearing speech act".into()
}

fn is_source_read(event: Option<&RawEvent>) -> bool {
    let Some(event) = event else {
        return false;
    };
    if event.source != EventSource::Shell {
        return false;
    }
    let command = collapse(&payload_str(event, "command")).to_lowercase();
    starts_word(&command, "sed") || starts_word(&command, "grep")
}

fn normative(sentence: &str) -> bool {
    if no_new_dependency(sentence) || do_not_add_dependency(sentence) {
        return true;
    }
    let target = has_word(sentence, "signature")
        || has_word(sentence, "constructor")
        || has_word(sentence, "handle_request")
        || has_word(sentence, "handle_async_request");
    let rule = has_word(sentence, "keep")
        || has_word(sentence, "unchanged")
        || has_word(sentence, "stay")
        || has_word(sentence, "stays")
        || has_word(sentence, "preserve");
    target && rule
}

fn no_new_dependency(sentence: &str) -> bool {
    let words = words(sentence);
    for (index, word) in words.iter().enumerate() {
        if word != "no" {
            continue;
        }
        let Some(next) = words.get(index + 1) else {
            continue;
        };
        if !matches!(next.as_str(), "new" | "extra" | "additional") {
            continue;
        }
        let end = (index + 6).min(words.len());
        if words[index + 2..end]
            .iter()
            .any(|word| word.starts_with("dependenc"))
        {
            return true;
        }
    }
    false
}

fn do_not_add_dependency(sentence: &str) -> bool {
    let lower = sentence.to_lowercase();
    [
        "do not add a dependency",
        "do not add a new dependency",
        "don't add a dependency",
        "don't add a new dependency",
    ]
    .iter()
    .any(|phrase| lower.contains(phrase))
}

fn sentences(text: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let chars: Vec<(usize, char)> = text.char_indices().collect();
    let mut start = 0;
    let mut index = 0;
    while index < chars.len() {
        let (byte, ch) = chars[index];
        if matches!(ch, '.' | '!' | '?') {
            let next = index + 1;
            if next < chars.len() && chars[next].1.is_whitespace() {
                let end = byte + ch.len_utf8();
                let sentence = text[start..end].trim();
                if !sentence.is_empty() {
                    out.push(sentence);
                }
                let mut cursor = next;
                while cursor < chars.len() && chars[cursor].1.is_whitespace() {
                    cursor += 1;
                }
                start = chars
                    .get(cursor)
                    .map(|(byte, _)| *byte)
                    .unwrap_or(text.len());
                index = cursor;
                continue;
            }
        }
        index += 1;
    }
    let tail = text[start..].trim();
    if !tail.is_empty() {
        out.push(tail);
    }
    out
}

fn suffix_for(index: usize) -> String {
    if index == 0 {
        String::new()
    } else {
        char::from(b'b' + (index as u8 - 1).min(24)).to_string()
    }
}

fn shell_state(event: &RawEvent) -> Option<((String, String), Option<i64>)> {
    if event.source != EventSource::Shell {
        return None;
    }
    let session = payload_str(event, "session");
    let command = collapse(&payload_str(event, "command"));
    let code = event.payload.get("exit_code").and_then(Value::as_i64);
    Some(((session, command), code))
}

fn open_sessions(events: &[RawEvent]) -> BTreeSet<String> {
    let mut latest: BTreeMap<(String, String), Option<i64>> = BTreeMap::new();
    for event in events {
        if let Some((key, code)) = shell_state(event) {
            latest.insert(key, code);
        }
    }
    latest
        .into_iter()
        .filter_map(|((session, _), code)| code.is_some_and(|code| code != 0).then_some(session))
        .collect()
}

fn failing_command<'a>(events: &'a [RawEvent], session: Option<&str>) -> Option<&'a RawEvent> {
    let mut latest: BTreeMap<(String, String), Option<i64>> = BTreeMap::new();
    let mut first: BTreeMap<(String, String), usize> = BTreeMap::new();
    for (index, event) in events.iter().enumerate() {
        let Some((key, code)) = shell_state(event) else {
            continue;
        };
        if session.is_some_and(|wanted| key.0 != wanted) {
            continue;
        }
        latest.insert(key.clone(), code);
        first.entry(key).or_insert(index);
    }
    for (index, event) in events.iter().enumerate() {
        let Some((key, _)) = shell_state(event) else {
            continue;
        };
        if session.is_some_and(|wanted| key.0 != wanted) {
            continue;
        }
        let still_failing = latest
            .get(&key)
            .and_then(|code| *code)
            .is_some_and(|code| code != 0);
        if still_failing && first.get(&key) == Some(&index) {
            return Some(event);
        }
    }
    None
}

fn task_for(event: &RawEvent) -> String {
    shell_subject(&payload_str(event, "cwd"), &payload_str(event, "command"))
}

fn support_event<'a>(
    events: &'a [RawEvent],
    note: &RawEvent,
    index: usize,
) -> Option<&'a RawEvent> {
    let support_id = payload_str(note, "support_event_id");
    if !support_id.is_empty() {
        return events.iter().find(|event| event.id == support_id);
    }
    if index == 0 {
        None
    } else {
        Some(&events[index - 1])
    }
}

fn support_by_id<'a>(events: &'a [RawEvent], note: &RawEvent) -> Option<&'a RawEvent> {
    let support_id = payload_str(note, "support_event_id");
    if support_id.is_empty() {
        None
    } else {
        events.iter().find(|event| event.id == support_id)
    }
}

fn failing_subjects(events: &[RawEvent]) -> BTreeSet<String> {
    let mut latest: BTreeMap<String, i64> = BTreeMap::new();
    for event in events {
        if event.source != EventSource::Shell {
            continue;
        }
        let command = payload_str(event, "command");
        if command.is_empty() {
            continue;
        }
        let Some(code) = event.payload.get("exit_code").and_then(Value::as_i64) else {
            continue;
        };
        latest.insert(shell_subject(&payload_str(event, "cwd"), &command), code);
    }
    latest
        .into_iter()
        .filter(|(_, code)| *code != 0)
        .map(|(subject, _)| subject)
        .collect()
}

fn longest_quoted_claim<'a>(
    projection: &'a Projection,
    text: &str,
) -> Option<&'a contextd_core::claim::Claim> {
    projection
        .claims
        .iter()
        .filter(|claim| {
            claim.status == contextd_core::claim::ClaimStatus::Current && text.contains(&claim.text)
        })
        .max_by_key(|claim| claim.text.len())
}

fn current_with_text<'a>(
    projection: &'a Projection,
    text: &str,
) -> Option<&'a contextd_core::claim::Claim> {
    let wanted = collapse(text);
    if wanted.is_empty() {
        return None;
    }
    projection.claims.iter().find(|claim| {
        claim.status == contextd_core::claim::ClaimStatus::Current
            && collapse(&claim.text) == wanted
    })
}

fn seed_seen(events: &[RawEvent]) -> Vec<(String, String)> {
    let mut seen = Vec::new();
    for event in events {
        if event.source != EventSource::Claim {
            continue;
        }
        let Ok(claim) = serde_json::from_value::<ClaimEvent>(event.payload.clone()) else {
            continue;
        };
        if claim.op != ClaimOp::Assert {
            continue;
        }
        let (Some(kind), Some(text)) = (claim.kind, claim.text.as_deref()) else {
            continue;
        };
        seen.push((kind.as_str().to_string(), collapse(text).to_lowercase()));
    }
    seen
}

fn cited_notes(events: &[RawEvent]) -> BTreeSet<String> {
    let mut notes = BTreeSet::new();
    for event in events {
        if event.source != EventSource::Claim {
            continue;
        }
        let Ok(claim) = serde_json::from_value::<ClaimEvent>(event.payload.clone()) else {
            continue;
        };
        notes.extend(claim.evidence_ids);
    }
    notes
}

fn push_event(log: &mut Vec<RawEvent>, event: ClaimEvent, id: String) {
    let timestamp_ms = log
        .iter()
        .map(|event| event.timestamp_ms)
        .max()
        .unwrap_or(0)
        .saturating_add(1000);
    log.push(RawEvent {
        id,
        timestamp_ms,
        source: EventSource::Claim,
        payload: serde_json::to_value(&event).unwrap_or_else(|_| json!({})),
    });
}

fn sort_log(log: &mut [RawEvent]) {
    log.sort_by(|left, right| {
        left.timestamp_ms
            .cmp(&right.timestamp_ms)
            .then_with(|| left.id.cmp(&right.id))
    });
}

fn skip(report: &mut HandoffReport, unit: &str, reason: &str) {
    report.skipped.push(json!({
        "unit": unit,
        "reason": reason,
    }));
}

fn event_by_id<'a>(events: &'a [RawEvent], id: &str) -> Option<&'a RawEvent> {
    events.iter().find(|event| event.id == id)
}

fn payload_str(event: &RawEvent, key: &str) -> String {
    event
        .payload
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string()
}

fn collapse(value: &str) -> String {
    value.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn words(text: &str) -> Vec<String> {
    text.split(|ch: char| !ch.is_ascii_alphanumeric())
        .filter(|word| !word.is_empty())
        .map(|word| word.to_lowercase())
        .collect()
}

fn has_word(text: &str, word: &str) -> bool {
    let lower = text.to_lowercase();
    let word = word.to_lowercase();
    lower.match_indices(&word).any(|(index, _)| {
        let before_ok = index == 0
            || !lower.as_bytes()[index - 1].is_ascii_alphanumeric()
                && lower.as_bytes()[index - 1] != b'_';
        let after = index + word.len();
        let after_ok = after >= lower.len()
            || !lower.as_bytes()[after].is_ascii_alphanumeric() && lower.as_bytes()[after] != b'_';
        before_ok && after_ok
    })
}

fn contains_phrase(lower: &str, phrase: &str) -> bool {
    lower.match_indices(phrase).any(|(index, _)| {
        let before_ok = index == 0 || !lower.as_bytes()[index - 1].is_ascii_alphanumeric();
        let after = index + phrase.len();
        let after_ok = after >= lower.len() || !lower.as_bytes()[after].is_ascii_alphanumeric();
        before_ok && after_ok
    })
}

fn starts_word(lower: &str, word: &str) -> bool {
    let lower = lower.trim_start();
    lower.starts_with(word)
        && (lower.len() == word.len() || !lower.as_bytes()[word.len()].is_ascii_alphanumeric())
}

fn starts_phrase(lower: &str, phrase: &str) -> bool {
    starts_word(lower, phrase)
}

fn content_overlap(claim: &str, support: &str) -> usize {
    let support_tokens: BTreeSet<String> = content_tokens(support).collect();
    content_tokens(claim)
        .filter(|token| support_tokens.contains(token))
        .collect::<BTreeSet<_>>()
        .len()
}

fn content_tokens(text: &str) -> impl Iterator<Item = String> {
    let mut tokens = Vec::new();
    let mut current = String::new();
    for ch in text.chars() {
        if ch.is_ascii_alphanumeric() || ch == '_' {
            current.push(ch.to_ascii_lowercase());
        } else if !current.is_empty() {
            keep_token(&mut tokens, &current);
            current.clear();
        }
    }
    if !current.is_empty() {
        keep_token(&mut tokens, &current);
    }
    tokens.into_iter()
}

fn keep_token(tokens: &mut Vec<String>, token: &str) {
    let token = token.trim_matches('_');
    if token.len() >= 4 && !STOP.contains(&token) && starts_like_word(token) {
        tokens.push(token.to_string());
    }
    for part in token.split('_') {
        if part.len() >= 4 && !STOP.contains(&part) && part != token && starts_like_word(part) {
            tokens.push(part.to_string());
        }
    }
}

fn starts_like_word(token: &str) -> bool {
    token
        .chars()
        .next()
        .is_some_and(|ch| ch.is_ascii_alphabetic() || ch == '_')
}

fn truncate_output(text: &str) -> String {
    let mut lines: Vec<&str> = text.split_terminator('\n').collect();
    let mut shortened = false;
    if lines.len() > MAX_OUTPUT_LINES {
        lines.truncate(MAX_OUTPUT_LINES);
        shortened = true;
    }
    let mut body = lines.join("\n");
    if body.chars().count() > MAX_OUTPUT_CHARS {
        body = body.chars().take(MAX_OUTPUT_CHARS).collect();
        shortened = true;
    }
    if shortened {
        format!("{body}\n{TRUNCATION_MARK}")
    } else {
        text.to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use contextd_core::claim::{ClaimConfidence, ClaimKind, ClaimOrigin, ClaimStatus};
    use contextd_core::config::AppConfig;
    use std::path::PathBuf;

    fn shell(id: &str, at: u64, command: &str, output: &str, code: i64) -> RawEvent {
        RawEvent {
            id: id.into(),
            timestamp_ms: at,
            source: EventSource::Shell,
            payload: json!({
                "command": command,
                "exit_code": code,
                "cwd": "/work",
                "session": "A",
                "output": output,
            }),
        }
    }

    fn note(id: &str, at: u64, text: &str) -> RawEvent {
        RawEvent {
            id: id.into(),
            timestamp_ms: at,
            source: EventSource::Note,
            payload: json!({"text": text, "session": "A"}),
        }
    }

    fn conclusion(id: &str, at: u64, text: &str, support: &str) -> RawEvent {
        RawEvent {
            id: id.into(),
            timestamp_ms: at,
            source: EventSource::Note,
            payload: json!({
                "text": text,
                "session": "A",
                "role": "conclusion",
                "speaker": "agent",
                "support_event_id": support,
            }),
        }
    }

    fn current_texts(projection: &Projection, kind: ClaimKind) -> Vec<String> {
        projection
            .claims
            .iter()
            .filter(|claim| claim.status == ClaimStatus::Current && claim.kind == kind)
            .map(|claim| claim.text.clone())
            .collect()
    }

    #[test]
    fn a_decision_note_is_inferred_and_commands_alone_extract_nothing() {
        let mut commands = vec![shell(
            "s",
            1,
            "python -m pytest tests/test_pool_counts.py",
            "",
            1,
        )];
        assert!(apply_extraction(&mut commands).accepted.is_empty());

        let mut events = vec![
            shell("s", 1, "python -m pytest tests/test_pool_counts.py", "", 1),
            note("n", 2, "Decision: idle connections count toward the cap."),
        ];
        let report = apply_extraction(&mut events);
        assert!(report.rejected.is_empty(), "{:?}", report.rejected);
        assert_eq!(report.accepted.len(), 1);
        assert_eq!(report.accepted[0]["kind"], "decision");
        assert_eq!(report.accepted[0]["confidence"], "inferred");
        assert_ne!(report.accepted[0]["confidence"], "observed");
    }

    #[test]
    fn an_ungrounded_file_read_and_an_open_hypothesis_are_skipped() {
        let mut events = vec![
            shell(
                "s",
                1,
                "python -m pytest tests/test_pool_counts.py",
                "count 3",
                1,
            ),
            note("n1", 2, "First suspect: the mock network backend."),
            shell("g", 3, "sed -n 1p pool.py", "return 1", 0),
            note(
                "n2",
                4,
                "The database cluster is the real cause of this failure mode.",
            ),
        ];
        let report = apply_extraction(&mut events);
        assert!(report.accepted.is_empty(), "{:?}", report.accepted);
        assert!(report.rejected.is_empty(), "{:?}", report.rejected);
    }

    #[test]
    fn a_grounded_sed_note_cites_the_tool_output() {
        let output =
            "len([c for c in self._connections if not c.is_idle()]) < self._max_connections";
        let mut events = vec![
            shell("s", 1, "python -m pytest tests/test_pool_counts.py", "", 1),
            shell("g", 2, "sed -n 10p pool.py", output, 0),
            note(
                "n",
                3,
                "The create branch only counts connections that are not idle toward max_connections.",
            ),
        ];
        let report = apply_extraction(&mut events);
        assert_eq!(report.accepted.len(), 1, "{:?}", report.skipped);
        assert_eq!(report.accepted[0]["kind"], "reality");
        assert_eq!(report.accepted[0]["confidence"], "inferred");
        let evidence = report.accepted[0]["evidence_ids"].as_array().unwrap();
        assert_eq!(evidence[0], "n");
        assert_eq!(evidence[1], "g");
    }

    #[test]
    fn contradictory_conclusions_both_stay_until_an_explicit_retract() {
        let output =
            "def deliver(self, message):\n    self.retry_count += 1\n    self.open_socket()\n";
        let first = "deliver increments retry_count before open_socket, so the first send is already counted as a retry.";
        let second = "The first send is not counted, so retry_count stays at zero until a failure.";
        let base = vec![
            shell(
                "s",
                1,
                "python -m pytest -q tests/test_retry.py",
                "retry_count 2 != 1",
                1,
            ),
            shell("g", 2, "sed -n 12,18p courier.py", output, 0),
            conclusion("c1", 3, first, "g"),
            conclusion("c2", 4, second, "g"),
        ];
        let mut open = base.clone();
        let report = apply_extraction(&mut open);
        assert!(report.rejected.is_empty(), "{:?}", report.rejected);
        let projection = fold(&open);
        let realities = current_texts(&projection, ClaimKind::Reality);
        assert!(realities.iter().any(|text| text == first));
        assert!(realities.iter().any(|text| text == second));

        let mut retracted = base;
        retracted.push(note("r", 5, &format!("Retract: {second}")));
        let report = apply_extraction(&mut retracted);
        assert!(report.rejected.is_empty(), "{:?}", report.rejected);
        let projection = fold(&retracted);
        let realities = current_texts(&projection, ClaimKind::Reality);
        assert!(realities.iter().any(|text| text == first));
        assert!(!realities.iter().any(|text| text == second));
        assert!(
            projection
                .claims
                .iter()
                .filter(|claim| claim.text == second)
                .all(|claim| claim.status == ClaimStatus::Superseded)
        );
    }

    #[test]
    fn an_ungrounded_update_does_not_supersede() {
        let text = "Decision: idle connections count toward the cap.";
        let mut events = vec![
            shell("s", 1, "python -m pytest tests/test_pool_counts.py", "", 1),
            note("n", 2, text),
            RawEvent {
                id: "u".into(),
                timestamp_ms: 3,
                source: EventSource::Note,
                payload: json!({
                    "text": "The database pool is exhausted and the worker queue is blocked.",
                    "session": "A",
                    "role": "update",
                    "replaces_text": text,
                    "support_event_id": "s",
                }),
            },
        ];
        let report = apply_extraction(&mut events);
        assert!(report.rejected.is_empty(), "{:?}", report.rejected);
        let projection = fold(&events);
        assert!(
            current_texts(&projection, ClaimKind::Decision)
                .iter()
                .any(|item| item == text)
        );
    }

    #[test]
    fn a_restart_rebuilds_the_same_snapshot() {
        let dir = std::env::temp_dir().join(format!(
            "contextd-handoff-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&dir).unwrap();
        let config = AppConfig {
            db_path: dir.join("events.db"),
            socket_path: PathBuf::from("/tmp/contextd-handoff-test.sock"),
            ..AppConfig::default()
        };
        let first = "The failure is retry_count 2 where the test expects 1.";
        let store = store::Store::open(&config).unwrap();
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        runtime.block_on(async {
            let conn = store.writer().await;
            for event in [
                shell(
                    "s",
                    1,
                    "python -m pytest -q tests/test_retry.py",
                    "AssertionError: retry_count 2 != 1",
                    1,
                ),
                conclusion("c", 2, first, "s"),
                note(
                    "u",
                    3,
                    "Keep deliver's signature unchanged. Do not add a dependency.",
                ),
            ] {
                let user = event.id == "u";
                let mut event = event;
                if user {
                    event.payload["speaker"] = json!("user");
                }
                store::db::insert_event(&conn, &ProcessedEvent::new(event, 1.0)).unwrap();
            }
            let report = run_handoff(&conn).unwrap();
            assert!(report.rejected.is_empty(), "{:?}", report.rejected);
            assert!(report.accepted.len() >= 3, "{:?}", report.accepted);
        });
        drop(store);

        let reopened = store::Store::open(&config).unwrap();
        runtime.block_on(async {
            let conn = reopened.writer().await;
            memory::project::rebuild(&conn).unwrap();
            let projection = store::db::load_projection(&conn).unwrap();
            let before_snap = crate::snapshot::render(&projection, None, 1_500);
            let before = serde_json::to_value(&before_snap).unwrap();
            let again = run_handoff(&conn).unwrap();
            assert!(again.rejected.is_empty(), "{:?}", again.rejected);
            assert!(
                again.accepted.is_empty(),
                "second handoff inserted claims: {:?}",
                again.accepted
            );
            let projection = store::db::load_projection(&conn).unwrap();
            let after_snap = crate::snapshot::render(&projection, None, 1_500);
            let after = serde_json::to_value(&after_snap).unwrap();
            assert_eq!(before, after);
            let focal = after_snap.focal_task.expect("focal task");
            assert!(focal.reality.iter().any(|claim| claim.text == first));
            assert!(focal.reality.iter().any(|claim| {
                claim.confidence == ClaimConfidence::Observed
                    && claim.origin == ClaimOrigin::Projector
            }));
            assert!(
                focal
                    .reality
                    .iter()
                    .filter(|claim| claim.origin == ClaimOrigin::Agent)
                    .all(|claim| {
                        claim.confidence == ClaimConfidence::Inferred
                            && !claim.evidence_ids.is_empty()
                    })
            );
            assert!(after_snap.constraints.len() >= 2);
            assert!(after_snap.constraints.iter().all(|claim| {
                claim.confidence == ClaimConfidence::Stated && claim.origin == ClaimOrigin::User
            }));
            let fold_sources_ok = store::db::get_fold_events(&conn)
                .unwrap()
                .iter()
                .all(|event| event.source != EventSource::Note);
            assert!(fold_sources_ok);
        });
        let _ = std::fs::remove_dir_all(&dir);
    }
}
