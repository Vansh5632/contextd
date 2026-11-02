//! Admit verbatim claim candidates and render them with the Component 1 projector.
//!
//! Usage: project_claims EVENTS.jsonl CLAIMS.json CLAIMS_OUT.json REPORT.json
//!
//! EVENTS.jsonl is an existing contextd event log. CLAIMS.json is the extractor
//! output (`claims` plus `skipped`). This binary does not read gold atoms. It
//! rejects a candidate that Component 1 would reject, that is not a verbatim
//! span of its source note, or that states an inferred reality the cited tool
//! output does not support. Admitted claims are appended and folded. The packet
//! written to CLAIMS_OUT is `context_now` for that fold.

use std::collections::BTreeSet;
use std::env;
use std::fs;
use std::process::ExitCode;

use broker::snapshot::render;
use contextd_core::claim::{
    validate, validate_against_projection, ClaimConfidence, ClaimEvent, ClaimKind, ClaimOp,
    ClaimOrigin, FRONTIER_SUBJECT,
};
use contextd_core::event::{EventSource, RawEvent};
use memory::project::{fold, task_id_for_subject};
use serde::Deserialize;
use serde_json::{json, Value};

const MIN_OVERLAP: usize = 2;
const TOKEN_BUDGET: usize = 1_500;

const STOP: &[&str] = &[
    "about", "after", "also", "and", "are", "because", "been", "before", "but", "for", "from",
    "have", "into", "only", "that", "than", "the", "their", "them", "then", "this", "with",
    "would",
];

#[derive(Debug, Deserialize)]
struct Candidate {
    claim_id: String,
    kind: String,
    origin: String,
    confidence: String,
    text: String,
    #[serde(default)]
    subject: Option<String>,
    #[serde(default)]
    evidence_ids: Vec<String>,
    source_text: String,
    #[serde(default)]
    support_text: Option<String>,
    task_subject: String,
}

#[derive(Debug, Deserialize)]
struct CandidateFile {
    claims: Vec<Candidate>,
}

fn main() -> ExitCode {
    let mut args = env::args().skip(1);
    let Some(events_path) = args.next() else {
        eprintln!("usage: project_claims EVENTS.jsonl CLAIMS.json PACKET.json REPORT.json");
        return ExitCode::from(2);
    };
    let Some(claims_path) = args.next() else {
        eprintln!("missing claims file");
        return ExitCode::from(2);
    };
    let Some(packet_path) = args.next() else {
        eprintln!("missing packet path");
        return ExitCode::from(2);
    };
    let Some(report_path) = args.next() else {
        eprintln!("missing report path");
        return ExitCode::from(2);
    };

    let events = match load_events(&events_path) {
        Ok(events) => events,
        Err(err) => {
            eprintln!("{err}");
            return ExitCode::from(1);
        }
    };
    let file: CandidateFile = match fs::read_to_string(&claims_path)
        .map_err(|err| err.to_string())
        .and_then(|text| serde_json::from_str(&text).map_err(|err| err.to_string()))
    {
        Ok(file) => file,
        Err(err) => {
            eprintln!("claims file: {err}");
            return ExitCode::from(1);
        }
    };

    let mut log = events;
    log.sort_by(|left, right| {
        left.timestamp_ms
            .cmp(&right.timestamp_ms)
            .then_with(|| left.id.cmp(&right.id))
    });
    let base_ts = log.iter().map(|event| event.timestamp_ms).max().unwrap_or(0);
    let mut projection = fold(&log);
    let mut accepted: Vec<Value> = Vec::new();
    let mut rejected: Vec<Value> = Vec::new();
    let mut seen: Vec<(String, String)> = Vec::new();

    for (index, candidate) in file.claims.into_iter().enumerate() {
        match admit(&candidate, &projection, &seen) {
            Ok(event) => {
                seen.push((
                    candidate.kind.clone(),
                    collapse(&candidate.text).to_lowercase(),
                ));
                let raw = RawEvent {
                    id: format!("c2_{}", candidate.claim_id),
                    timestamp_ms: base_ts.saturating_add((index as u64 + 1) * 1000),
                    source: EventSource::Claim,
                    payload: serde_json::to_value(&event).expect("claim event serializes"),
                };
                log.push(raw);
                projection = fold(&log);
                accepted.push(json!({
                    "claim_id": candidate.claim_id,
                    "kind": candidate.kind,
                    "confidence": candidate.confidence,
                    "origin": candidate.origin,
                    "evidence_ids": candidate.evidence_ids,
                }));
            }
            Err(reason) => rejected.push(json!({
                "claim_id": candidate.claim_id,
                "reason": reason,
            })),
        }
    }

    let snapshot = render(&projection, None, TOKEN_BUDGET);
    let packet = serde_json::to_string_pretty(&snapshot).expect("snapshot serializes");
    if let Err(err) = fs::write(&packet_path, format!("{packet}\n")) {
        eprintln!("writing packet: {err}");
        return ExitCode::from(1);
    }
    let report = json!({
        "accepted": accepted,
        "rejected": rejected,
        "snapshot": snapshot,
    });
    if let Err(err) = fs::write(
        &report_path,
        format!("{}\n", serde_json::to_string_pretty(&report).expect("report serializes")),
    ) {
        eprintln!("writing report: {err}");
        return ExitCode::from(1);
    }
    if !rejected.is_empty() {
        eprintln!("rejected {} claim(s)", rejected.len());
        return ExitCode::from(3);
    }
    ExitCode::SUCCESS
}

fn admit(
    candidate: &Candidate,
    projection: &contextd_core::claim::Projection,
    seen: &[(String, String)],
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
    if text.is_empty() || !candidate.source_text.contains(text) {
        return Err("claim text is not a verbatim span of the source note".into());
    }
    if candidate.evidence_ids.is_empty() {
        return Err("claim has no evidence id".into());
    }
    if candidate.task_subject.trim().is_empty() {
        return Err("claim has no task subject".into());
    }
    let key = (candidate.kind.clone(), collapse(text).to_lowercase());
    if seen.iter().any(|item| item == &key) {
        return Err("duplicate of a claim already admitted".into());
    }
    if kind == ClaimKind::Reality {
        let support = candidate.support_text.as_deref().unwrap_or("");
        if content_overlap(text, support) < MIN_OVERLAP {
            return Err(
                "inferred reality is not supported by the cited tool output".into(),
            );
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

fn load_events(path: &str) -> Result<Vec<RawEvent>, String> {
    let text = fs::read_to_string(path).map_err(|err| format!("events: {err}"))?;
    let mut events = Vec::new();
    for (index, line) in text.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let event = serde_json::from_str(line)
            .map_err(|err| format!("events line {}: {err}", index + 1))?;
        events.push(event);
    }
    Ok(events)
}

fn collapse(value: &str) -> String {
    value.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn content_overlap(claim: &str, support: &str) -> usize {
    let support = support.to_lowercase();
    let mut hits = BTreeSet::new();
    for token in content_tokens(claim) {
        if support.contains(&token) {
            hits.insert(token);
        }
    }
    hits.len()
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
    if token.len() >= 4 && !STOP.contains(&token) {
        tokens.push(token.to_string());
    }
}
