//! Admit verbatim claim candidates and render them with the Component 1 projector.
//!
//! Usage:
//!   project_claims EVENTS.jsonl CLAIMS.json PACKET.json REPORT.json
//!   project_claims --extract EVENTS.jsonl PACKET.json REPORT.json
//!
//! The first form admits a candidate file. The second quotes the event log
//! itself. Neither reads gold atoms. Admitted claims are appended to the log.
//! The packet is `context_now` for that fold.

use std::env;
use std::fs;
use std::process::ExitCode;

use broker::extract::{self, Candidate};
use broker::snapshot::render;
use contextd_core::event::RawEvent;
use memory::project::fold;
use serde::Deserialize;

const TOKEN_BUDGET: usize = 1_500;

#[derive(Debug, Deserialize)]
struct CandidateFile {
    claims: Vec<Candidate>,
}

fn main() -> ExitCode {
    let mut args = env::args().skip(1);
    let Some(first) = args.next() else {
        eprintln!(
            "usage: project_claims EVENTS.jsonl CLAIMS.json PACKET.json REPORT.json\n       \
             project_claims --extract EVENTS.jsonl PACKET.json REPORT.json"
        );
        return ExitCode::from(2);
    };
    if first == "--extract" {
        let Some(events_path) = args.next() else {
            eprintln!("missing events file");
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
        return finish(&events_path, &packet_path, &report_path, true, None);
    }
    let events_path = first;
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
    finish(
        &events_path,
        &packet_path,
        &report_path,
        false,
        Some(claims_path),
    )
}

fn finish(
    events_path: &str,
    packet_path: &str,
    report_path: &str,
    extract_first: bool,
    claims_path: Option<String>,
) -> ExitCode {
    let mut log = match load_events(events_path) {
        Ok(events) => events,
        Err(err) => {
            eprintln!("{err}");
            return ExitCode::from(1);
        }
    };
    let report = if extract_first {
        extract::apply_extraction(&mut log)
    } else {
        let Some(claims_path) = claims_path else {
            eprintln!("missing claims file");
            return ExitCode::from(2);
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
        extract::admit_candidates(&mut log, file.claims)
    };
    let snapshot = render(&fold(&log), None, TOKEN_BUDGET);
    if let Err(err) = write_log(events_path, &log) {
        eprintln!("writing event log: {err}");
        return ExitCode::from(1);
    }
    let packet = serde_json::to_string_pretty(&snapshot).expect("snapshot serializes");
    if let Err(err) = fs::write(packet_path, format!("{packet}\n")) {
        eprintln!("writing packet: {err}");
        return ExitCode::from(1);
    }
    let body = serde_json::json!({
        "accepted": report.accepted,
        "rejected": report.rejected,
        "skipped": report.skipped,
        "snapshot": snapshot,
    });
    if let Err(err) = fs::write(
        report_path,
        format!(
            "{}\n",
            serde_json::to_string_pretty(&body).expect("report serializes")
        ),
    ) {
        eprintln!("writing report: {err}");
        return ExitCode::from(1);
    }
    if !report.rejected.is_empty() {
        eprintln!("rejected {} claim(s)", report.rejected.len());
        return ExitCode::from(3);
    }
    ExitCode::SUCCESS
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

fn write_log(path: &str, events: &[RawEvent]) -> Result<(), String> {
    let mut body = String::new();
    for event in events {
        let line = serde_json::to_string(event).map_err(|err| err.to_string())?;
        body.push_str(&line);
        body.push('\n');
    }
    fs::write(path, body).map_err(|err| err.to_string())
}
