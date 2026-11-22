//! Hybrid claim verification. This module has no database handle.
//!
//! A straight-line proof can be `SUPPORTED` or `CONTRADICTED` here. Anything
//! ambiguous is semantic: the caller classifies it with a model before taking
//! the SQLite writer, then [`judge`] applies the quote gate. A missing model
//! result is `INSUFFICIENT_EVIDENCE`.

use std::collections::BTreeMap;

use regex::Regex;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum Label {
    Supported,
    Contradicted,
    InsufficientEvidence,
}

impl Label {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Supported => "SUPPORTED",
            Self::Contradicted => "CONTRADICTED",
            Self::InsufficientEvidence => "INSUFFICIENT_EVIDENCE",
        }
    }

    fn parse(value: &str) -> Self {
        match value {
            "SUPPORTED" => Self::Supported,
            "CONTRADICTED" => Self::Contradicted,
            _ => Self::InsufficientEvidence,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RouteDecision {
    pub route: &'static str,
    pub label: Option<Label>,
    pub reason: &'static str,
    pub evidence_quote: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Judgment {
    pub label: Label,
    pub evidence_quote: String,
    pub route: &'static str,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ModelVerdict {
    pub label: Label,
    pub evidence_quote: String,
    pub prompt_tokens: u32,
    pub completion_tokens: u32,
    pub latency_ms: u64,
    pub calls: u32,
}

#[derive(Debug, Clone, Default)]
pub struct Verification {
    pub verdicts: BTreeMap<String, ModelVerdict>,
}

impl Verification {
    pub fn verdict_for(&self, claim: &str) -> Option<&ModelVerdict> {
        self.verdicts.get(claim)
    }

    pub fn usage(&self) -> (u32, u32, u32, u64) {
        let mut calls = 0;
        let mut prompt = 0;
        let mut completion = 0;
        let mut latency = 0;
        for verdict in self.verdicts.values() {
            calls += verdict.calls;
            prompt += verdict.prompt_tokens;
            completion += verdict.completion_tokens;
            latency += verdict.latency_ms;
        }
        (calls, prompt, completion, latency)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SemanticRequest {
    pub claim: String,
    pub evidence: String,
}

pub fn route_claim(claim: &str, evidence: &str) -> RouteDecision {
    let text = claim.trim();
    if evidence.trim().is_empty() {
        return abstain("missing_evidence");
    }
    if evidence.contains("[output truncated]") {
        return abstain("incomplete_evidence");
    }
    let visible = visible_code(evidence);
    if conflicting(&visible) {
        return abstain("conflicting_evidence");
    }
    let atoms = decompose(text);
    let mut labels = Vec::new();
    let mut quotes = Vec::new();
    for (index, atom) in atoms.iter().enumerate() {
        let (label, quote) = atom_decision(atom, evidence, &visible, index == 0);
        labels.push(label);
        quotes.push(quote);
    }
    if labels.contains(&Some(Label::Contradicted)) {
        let quote = quotes
            .into_iter()
            .find(|quote| !quote.is_empty())
            .unwrap_or_default();
        return RouteDecision {
            route: "deterministic",
            label: Some(Label::Contradicted),
            reason: "proved_contradiction",
            evidence_quote: quote,
        };
    }
    if labels.contains(&Some(Label::InsufficientEvidence)) {
        return RouteDecision {
            route: "deterministic",
            label: Some(Label::InsufficientEvidence),
            reason: "proved_gap",
            evidence_quote: String::new(),
        };
    }
    if !labels.is_empty() && labels.iter().all(|label| *label == Some(Label::Supported)) {
        let quote = quotes
            .into_iter()
            .find(|quote| !quote.is_empty())
            .unwrap_or_default();
        return RouteDecision {
            route: "deterministic",
            label: Some(Label::Supported),
            reason: "literal_proof",
            evidence_quote: quote,
        };
    }
    RouteDecision {
        route: "semantic",
        label: None,
        reason: "needs_model",
        evidence_quote: String::new(),
    }
}

pub fn needs_model(claim: &str, evidence: &str) -> bool {
    needs_model_span(claim, evidence, true)
}

/// Whether this span still needs a model. A later atom can be a proved gap
/// even when the same words, judged alone, would be sent to the model.
pub fn needs_model_span(claim: &str, evidence: &str, primary: bool) -> bool {
    span_route(claim, evidence, primary).route == "semantic"
}

pub fn judge_span(
    claim: &str,
    evidence: &str,
    model: Option<&ModelVerdict>,
    primary: bool,
) -> Judgment {
    if primary {
        return judge(claim, evidence, model);
    }
    let routed = span_route(claim, evidence, false);
    if routed.route != "semantic" {
        return Judgment {
            label: routed.label.unwrap_or(Label::InsufficientEvidence),
            evidence_quote: routed.evidence_quote,
            route: routed.route,
        };
    }
    let Some(model) = model else {
        return Judgment {
            label: Label::InsufficientEvidence,
            evidence_quote: String::new(),
            route: "semantic_failed",
        };
    };
    let (label, quote) = gate_quote(model.label, &model.evidence_quote, evidence);
    Judgment {
        label,
        evidence_quote: quote,
        route: "semantic",
    }
}

fn span_route(span: &str, evidence: &str, primary: bool) -> RouteDecision {
    if evidence.trim().is_empty() {
        return abstain("missing_evidence");
    }
    if evidence.contains("[output truncated]") {
        return abstain("incomplete_evidence");
    }
    let visible = visible_code(evidence);
    if conflicting(&visible) {
        return abstain("conflicting_evidence");
    }
    let (label, quote) = atom_decision(span, evidence, &visible, primary);
    match label {
        Some(Label::Contradicted) => RouteDecision {
            route: "deterministic",
            label: Some(Label::Contradicted),
            reason: "proved_contradiction",
            evidence_quote: quote,
        },
        Some(Label::InsufficientEvidence) => RouteDecision {
            route: "deterministic",
            label: Some(Label::InsufficientEvidence),
            reason: "proved_gap",
            evidence_quote: quote,
        },
        Some(Label::Supported) => RouteDecision {
            route: "deterministic",
            label: Some(Label::Supported),
            reason: "literal_proof",
            evidence_quote: quote,
        },
        None => RouteDecision {
            route: "semantic",
            label: None,
            reason: "needs_model",
            evidence_quote: String::new(),
        },
    }
}

/// Combine a deterministic route with an optional model verdict.
///
/// The model quote must be copied from the evidence. A semantic claim with no
/// verdict stays unverified.
pub fn judge(claim: &str, evidence: &str, model: Option<&ModelVerdict>) -> Judgment {
    let routed = route_claim(claim, evidence);
    if routed.route != "semantic" {
        return Judgment {
            label: routed.label.unwrap_or(Label::InsufficientEvidence),
            evidence_quote: routed.evidence_quote,
            route: routed.route,
        };
    }
    let Some(model) = model else {
        return Judgment {
            label: Label::InsufficientEvidence,
            evidence_quote: String::new(),
            route: "semantic_failed",
        };
    };
    let (label, quote) = gate_quote(model.label, &model.evidence_quote, evidence);
    Judgment {
        label,
        evidence_quote: quote,
        route: "semantic",
    }
}

pub fn gate_quote(label: Label, quote: &str, evidence: &str) -> (Label, String) {
    if label == Label::InsufficientEvidence || quote.is_empty() || !evidence.contains(quote) {
        (Label::InsufficientEvidence, String::new())
    } else {
        (label, quote.to_string())
    }
}

pub fn decompose(text: &str) -> Vec<String> {
    let pieces = boundaries(text.trim());
    let mut atoms = Vec::new();
    for piece in pieces {
        for causal in because_parts(&piece) {
            atoms.extend(conditional_parts(&causal));
        }
    }
    let cleaned: Vec<String> = atoms
        .into_iter()
        .map(|atom| atom.trim().to_string())
        .filter(|atom| !atom.is_empty())
        .collect();
    if cleaned.is_empty() {
        vec![text.trim().to_string()]
    } else {
        cleaned
    }
}

fn abstain(reason: &'static str) -> RouteDecision {
    RouteDecision {
        route: "abstain",
        label: Some(Label::InsufficientEvidence),
        reason,
        evidence_quote: String::new(),
    }
}

fn atom_decision(
    atom: &str,
    evidence: &str,
    visible: &str,
    primary: bool,
) -> (Option<Label>, String) {
    if polarity_conflict(atom, visible) {
        return (Some(Label::Contradicted), window_quote(evidence, "not"));
    }
    if let Some(name) = negated_name(atom, true)
        && !amounts(name, visible, true).is_empty()
    {
        return (
            Some(Label::Contradicted),
            change_quote(evidence, visible, name, true),
        );
    }
    if let Some(name) = negated_name(atom, false)
        && !amounts(name, visible, false).is_empty()
    {
        return (
            Some(Label::Contradicted),
            change_quote(evidence, visible, name, false),
        );
    }
    if let Some((left, amount, right)) = literal_before(atom, true)
        && let Some(proved) = literal_change(left, amount, right, evidence, visible, true)
    {
        return proved;
    }
    if let Some((left, amount, right)) = literal_before(atom, false)
        && let Some(proved) = literal_change(left, amount, right, evidence, visible, false)
    {
        return proved;
    }
    if let Some((left, right, increase)) = bare_before(atom)
        && let Some(proved) = bare_order(left, right, evidence, visible, increase)
    {
        return proved;
    }
    if foreign(atom) && !evidence.contains(atom) {
        return (Some(Label::InsufficientEvidence), String::new());
    }
    if !primary && content_missing(atom, visible) {
        return (Some(Label::InsufficientEvidence), String::new());
    }
    (None, String::new())
}

fn literal_change(
    left: &str,
    claimed: i64,
    right: &str,
    evidence: &str,
    visible: &str,
    increase: bool,
) -> Option<(Option<Label>, String)> {
    let found = amounts(left, visible, increase);
    let quote = change_quote(evidence, visible, left, increase);
    if found.len() == 1 && found[0] != claimed {
        return Some((Some(Label::Contradicted), quote));
    }
    match order_of(left, right, visible) {
        Some("reversed") if !found.is_empty() => Some((
            Some(Label::Contradicted),
            call_quote(evidence, visible, right),
        )),
        Some("before") if found == [claimed] => Some((Some(Label::Supported), quote)),
        _ => None,
    }
}

fn amounts(ident: &str, visible: &str, increase: bool) -> Vec<i64> {
    let pattern = change_pattern(ident, increase);
    pattern
        .captures_iter(visible)
        .filter(|caps| ident_boundary_before(visible, caps.get(0).unwrap().start()))
        .map(|caps| match caps.get(1) {
            Some(digits) => digits.as_str().parse().unwrap_or(1),
            None => 1,
        })
        .collect()
}

fn ident_boundary_before(text: &str, index: usize) -> bool {
    index == 0
        || text[..index]
            .chars()
            .next_back()
            .is_some_and(|ch| !ch.is_ascii_alphanumeric() && ch != '_')
}

fn change_pattern(ident: &str, increase: bool) -> Regex {
    let body = if increase {
        r"\+=\s*(\d+)|\+\+"
    } else {
        r"-=\s*(\d+)|--"
    };
    Regex::new(&format!(r"(?i){}\s*(?:{body})", regex::escape(ident))).expect("change pattern")
}

fn change_quote(original: &str, visible: &str, ident: &str, increase: bool) -> String {
    change_pattern(ident, increase)
        .find_iter(visible)
        .find(|found| ident_boundary_before(visible, found.start()))
        .map(|found| original[found.start()..found.end()].to_string())
        .unwrap_or_default()
}

fn call_pattern(ident: &str) -> Regex {
    Regex::new(&format!(r"(?i){}\s*\(", regex::escape(ident))).expect("call pattern")
}

fn call_quote(original: &str, visible: &str, ident: &str) -> String {
    call_pattern(ident)
        .find_iter(visible)
        .find(|found| ident_boundary_before(visible, found.start()))
        .map(|found| original[found.start()..found.end()].to_string())
        .unwrap_or_default()
}

fn literal_before(atom: &str, increase: bool) -> Option<(&str, i64, &str)> {
    let verb = if increase {
        "(?:increases|increments)"
    } else {
        "(?:decreases|decrements)"
    };
    let pattern = Regex::new(&format!(
        r"(?i)^([A-Za-z_][A-Za-z0-9_]*) {verb} by (\d+) before ([A-Za-z_][A-Za-z0-9_]*)$"
    ))
    .expect("literal pattern");
    let caps = pattern.captures(atom.trim())?;
    let amount = caps.get(2)?.as_str().parse().ok()?;
    Some((
        // The captures borrow `atom`. Returning them requires the pattern to
        // live long enough, so copy via indices on `atom` instead.
        atom_span(atom.trim(), caps.get(1)?.range()),
        amount,
        atom_span(atom.trim(), caps.get(3)?.range()),
    ))
}

fn atom_span(atom: &str, range: std::ops::Range<usize>) -> &str {
    &atom[range]
}

fn bare_before(atom: &str) -> Option<(&str, &str, bool)> {
    let pattern = Regex::new(
        r"(?i)^([A-Za-z_][A-Za-z0-9_]*) (increases|increments|decreases|decrements) before ([A-Za-z_][A-Za-z0-9_]*)$",
    )
    .expect("bare order");
    let caps = pattern.captures(atom.trim())?;
    let verb = caps.get(2)?.as_str().to_lowercase();
    let increase = verb.starts_with("inc");
    Some((
        atom_span(atom.trim(), caps.get(1)?.range()),
        atom_span(atom.trim(), caps.get(3)?.range()),
        increase,
    ))
}

fn bare_order(
    left: &str,
    right: &str,
    evidence: &str,
    visible: &str,
    increase: bool,
) -> Option<(Option<Label>, String)> {
    let found = amounts(left, visible, increase);
    if found.is_empty() {
        return None;
    }
    let quote = change_quote(evidence, visible, left, increase);
    match order_of(left, right, visible) {
        Some("reversed") => Some((
            Some(Label::Contradicted),
            call_quote(evidence, visible, right),
        )),
        Some("before") if found.len() == 1 => Some((Some(Label::Supported), quote)),
        _ => None,
    }
}

fn negated_name(atom: &str, increase: bool) -> Option<&str> {
    let verb = if increase {
        "(?:increases|increments)"
    } else {
        "(?:decreases|decrements)"
    };
    let pattern = Regex::new(&format!(r"(?i)\b([A-Za-z_][A-Za-z0-9_]*) never {verb}\b"))
        .expect("negation pattern");
    let caps = pattern.captures(atom)?;
    let range = caps.get(1)?.range();
    Some(&atom[range])
}

fn order_of(left: &str, right: &str, visible: &str) -> Option<&'static str> {
    let update = change_pattern(left, true)
        .find_iter(visible)
        .find(|found| ident_boundary_before(visible, found.start()))
        .or_else(|| {
            change_pattern(left, false)
                .find_iter(visible)
                .find(|found| ident_boundary_before(visible, found.start()))
        })?;
    let call = call_pattern(right)
        .find_iter(visible)
        .find(|found| ident_boundary_before(visible, found.start()))?;
    if !straight_proof(visible, update.start(), call.start()) {
        return None;
    }
    if call.start() < update.start() {
        Some("reversed")
    } else {
        Some("before")
    }
}

fn straight_proof(visible: &str, left: usize, right: usize) -> bool {
    let (start, end) = if left <= right {
        (left, right)
    } else {
        (right, left)
    };
    let span = &visible[start..end];
    let branch =
        Regex::new(r"\b(?:if|else|elif|for|while|switch|case|match|try|except|catch|loop)\b")
            .unwrap();
    let function = Regex::new(r"(?m)^[ \t]*(?:async[ \t]+def|def|fn|func|function)\b").unwrap();
    if function.is_match(span) || branch.is_match(span) {
        return false;
    }
    if brace_depth(visible, start) != brace_depth(visible, end) {
        return false;
    }
    let lines = line_table(visible);
    let earlier = line_index(&lines, start.min(end));
    let later = line_index(&lines, start.max(end));
    let indent = indent_of(&lines[earlier].1);
    if indent_of(&lines[later].1) != indent {
        return false;
    }
    let control =
        Regex::new(r"^[ \t]*(?:if|else|elif|for|while|switch|case|match|try|except|catch|loop)\b")
            .unwrap();
    for (_, line) in lines.iter().take(later + 1).skip(earlier) {
        if line.trim().is_empty() {
            continue;
        }
        if indent_of(line) != indent || control.is_match(line) {
            return false;
        }
    }
    let header = enclosing_header(&lines, earlier, &indent);
    let function_line = Regex::new(r"^[ \t]*(?:async[ \t]+def|def|fn|func|function)\b").unwrap();
    header.is_some_and(|line| function_line.is_match(line))
}

fn brace_depth(text: &str, index: usize) -> i32 {
    let snippet = &text[..index];
    snippet.chars().filter(|ch| *ch == '{').count() as i32
        - snippet.chars().filter(|ch| *ch == '}').count() as i32
}

fn line_table(text: &str) -> Vec<(usize, String)> {
    let mut rows = Vec::new();
    let mut offset = 0;
    if text.is_empty() {
        return vec![(0, String::new())];
    }
    for line in text.split_inclusive('\n') {
        let len = line.len();
        rows.push((offset, line.to_string()));
        offset += len;
    }
    rows
}

fn line_index(lines: &[(usize, String)], index: usize) -> usize {
    lines
        .iter()
        .position(|(offset, line)| *offset <= index && index < offset + line.len().max(1))
        .unwrap_or(lines.len().saturating_sub(1))
}

fn indent_of(line: &str) -> String {
    line.chars()
        .take_while(|ch| *ch == ' ' || *ch == '\t')
        .collect()
}

fn enclosing_header<'a>(
    lines: &'a [(usize, String)],
    index: usize,
    indent: &str,
) -> Option<&'a str> {
    for (_, line) in lines[..index].iter().rev() {
        if line.trim().is_empty() {
            continue;
        }
        if indent_of(line).len() < indent.len() {
            return Some(line.as_str());
        }
    }
    None
}

fn conflicting(visible: &str) -> bool {
    let mut events: BTreeMap<String, Vec<(usize, String)>> = BTreeMap::new();
    let record = |events: &mut BTreeMap<String, Vec<(usize, String)>>,
                  name: &str,
                  index: usize,
                  kind: String| {
        events
            .entry(name.to_lowercase())
            .or_default()
            .push((index, kind));
    };
    for pattern in [
        (r"([A-Za-z_][A-Za-z0-9_]*)\s*\+=\s*(\d+)", "add"),
        (r"([A-Za-z_][A-Za-z0-9_]*)\s*\+\+", "inc"),
        (r"([A-Za-z_][A-Za-z0-9_]*)\s*-=\s*(\d+)", "sub"),
        (r"([A-Za-z_][A-Za-z0-9_]*)\s*--", "dec"),
        (r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(\d+)", "set"),
    ] {
        let regex = Regex::new(pattern.0).unwrap();
        for caps in regex.captures_iter(visible) {
            let name = caps.get(1).unwrap().as_str();
            let kind = match pattern.1 {
                "add" => format!("+={}", caps.get(2).unwrap().as_str()),
                "inc" => "+=1".to_string(),
                "sub" => format!("-={}", caps.get(2).unwrap().as_str()),
                "dec" => "-=1".to_string(),
                _ => format!("={}", caps.get(2).unwrap().as_str()),
            };
            record(&mut events, name, caps.get(0).unwrap().start(), kind);
        }
    }
    events.values().any(|writes| {
        let mut writes = writes.clone();
        writes.sort_by_key(|(index, _)| *index);
        let adds: Vec<_> = writes
            .iter()
            .filter(|(_, kind)| kind.starts_with("+="))
            .map(|(_, kind)| kind.clone())
            .collect();
        let subs: Vec<_> = writes
            .iter()
            .filter(|(_, kind)| kind.starts_with("-="))
            .map(|(_, kind)| kind.clone())
            .collect();
        let sets: Vec<_> = writes
            .iter()
            .filter(|(_, kind)| kind.starts_with('='))
            .map(|(_, kind)| kind.clone())
            .collect();
        if adds.iter().collect::<std::collections::BTreeSet<_>>().len() > 1
            || subs.iter().collect::<std::collections::BTreeSet<_>>().len() > 1
            || sets.iter().collect::<std::collections::BTreeSet<_>>().len() > 1
            || (!adds.is_empty() && !subs.is_empty())
        {
            return true;
        }
        let changes: Vec<_> = writes
            .iter()
            .filter(|(_, kind)| kind.starts_with('+') || kind.starts_with('-'))
            .collect();
        if changes.is_empty() || sets.is_empty() {
            return false;
        }
        let first = changes.iter().map(|(index, _)| *index).min().unwrap_or(0);
        writes
            .iter()
            .any(|(index, kind)| kind.starts_with('=') && *index > first)
    })
}

fn visible_code(evidence: &str) -> String {
    let chars: Vec<char> = evidence.chars().collect();
    let mut out = String::with_capacity(evidence.len());
    let mut index = 0;
    while index < chars.len() {
        let pair: String = chars.iter().skip(index).take(2).collect();
        if pair == "//" || pair == "/*" || chars[index] == '#' {
            if pair == "/*" {
                blank(&mut out, chars[index]);
                blank(&mut out, chars[index + 1]);
                index += 2;
                while index < chars.len() && !(chars[index - 1] == '*' && chars[index] == '/') {
                    if chars[index] == '\n' {
                        out.push('\n');
                    } else {
                        blank(&mut out, chars[index]);
                    }
                    index += 1;
                }
                if index < chars.len() {
                    blank(&mut out, chars[index]);
                    index += 1;
                }
                continue;
            }
            while index < chars.len() && chars[index] != '\n' {
                blank(&mut out, chars[index]);
                index += 1;
            }
            continue;
        }
        if matches!(chars[index], '"' | '\'' | '`') {
            let quote = chars[index];
            blank(&mut out, chars[index]);
            index += 1;
            while index < chars.len() && chars[index] != quote {
                if chars[index] == '\\' && index + 1 < chars.len() {
                    blank(&mut out, chars[index]);
                    blank(&mut out, chars[index + 1]);
                    index += 2;
                    continue;
                }
                if chars[index] == '\n' {
                    out.push('\n');
                } else {
                    blank(&mut out, chars[index]);
                }
                index += 1;
            }
            if index < chars.len() {
                blank(&mut out, chars[index]);
                index += 1;
            }
            continue;
        }
        out.push(chars[index]);
        index += 1;
    }
    out
}

/// Replace one character with spaces of the same UTF-8 width.
///
/// Quotes are sliced from the original evidence with indexes found in this
/// blanked copy. A multibyte character such as `Ω` must not shrink the copy.
fn blank(out: &mut String, ch: char) {
    for _ in 0..ch.len_utf8() {
        out.push(' ');
    }
}

fn window_quote(evidence: &str, needle: &str) -> String {
    evidence
        .to_lowercase()
        .find(&needle.to_lowercase())
        .map(|index| {
            // `not` is ASCII, so the lowercase index matches the original.
            evidence[index..index + needle.len()].to_string()
        })
        .unwrap_or_default()
}

fn foreign(atom: &str) -> bool {
    atom.chars().any(|ch| (ch as u32) > 127)
}

fn content_missing(atom: &str, evidence: &str) -> bool {
    let words: Vec<String> = Regex::new(r"[A-Za-z]{4,}")
        .unwrap()
        .find_iter(atom)
        .map(|item| item.as_str().to_lowercase())
        .collect();
    if words.is_empty() {
        return true;
    }
    let folded = evidence.to_lowercase();
    words.iter().all(|word| !folded.contains(word))
}

fn polarity_conflict(atom: &str, evidence: &str) -> bool {
    let Some((negated, focus)) = condition(atom) else {
        return false;
    };
    let pattern = Regex::new(&format!(
        r"(?i)\bnot\s+(?:[A-Za-z_][A-Za-z0-9_]*\s*\.\s*)?{}\b",
        regex::escape(&focus)
    ))
    .unwrap();
    pattern.is_match(evidence) != negated
}

fn condition(atom: &str) -> Option<(bool, String)> {
    let pattern = Regex::new(r"(?i)^(if|only when|unless)\s+(.*)$").unwrap();
    let caps = pattern.captures(atom.trim())?;
    let marker = caps.get(1)?.as_str().to_lowercase();
    let body = caps.get(2)?.as_str().trim();
    let body_negated = Regex::new(r"(?i)^not\b").unwrap().is_match(body);
    let negated = (marker == "unless") != body_negated;
    let focus_body = Regex::new(r"(?i)^not\s+").unwrap().replace(body, "");
    let words: Vec<&str> = Regex::new(r"[A-Za-z_][A-Za-z0-9_]*")
        .unwrap()
        .find_iter(focus_body.trim())
        .map(|item| item.as_str())
        .collect();
    let focus = words.last()?.to_lowercase();
    Some((negated, focus))
}

fn boundaries(text: &str) -> Vec<String> {
    let chars: Vec<(usize, char)> = text.char_indices().collect();
    let mut parts = Vec::new();
    let mut start = 0;
    let mut index = 0;
    while index < chars.len() {
        let (offset, ch) = chars[index];
        let sentence_end = matches!(ch, '.' | '!' | '?')
            && index > 0
            && chars[index - 1].1.is_ascii_alphanumeric()
            && chars
                .get(index + 1)
                .map(|(_, next)| next.is_whitespace())
                .unwrap_or(true);
        let clause_mark = matches!(ch, '。' | '！' | '？' | '；' | ';');
        if sentence_end || clause_mark {
            let piece = text[start..offset].trim();
            if !piece.is_empty() {
                parts.push(piece.to_string());
            }
            let mut end = index + 1;
            while end < chars.len() && matches!(chars[end].1, '。' | '！' | '？' | '；' | ';') {
                end += 1;
            }
            start = chars
                .get(end)
                .map(|(offset, _)| *offset)
                .unwrap_or(text.len());
            index = end;
            continue;
        }
        index += 1;
    }
    let tail = text[start..].trim();
    if !tail.is_empty() {
        parts.push(tail.to_string());
    }
    if parts.is_empty() && !text.is_empty() {
        vec![text.to_string()]
    } else {
        parts
    }
}

fn because_parts(clause: &str) -> Vec<String> {
    let pattern = Regex::new(r"(?i)\s+because\s+").unwrap();
    let parts: Vec<String> = pattern
        .split(clause)
        .map(|part| part.trim().to_string())
        .filter(|part| !part.is_empty())
        .collect();
    if parts.len() <= 1 {
        vec![clause.to_string()]
    } else {
        let mut all = vec![clause.trim().to_string()];
        all.extend(parts);
        all
    }
}

fn conditional_parts(clause: &str) -> Vec<String> {
    let pattern = Regex::new(r"(?i)(?:^|\s+)(only when|unless|if)\s+").unwrap();
    let Some(found) = pattern.find(clause) else {
        return vec![clause.to_string()];
    };
    let prefix = clause[..found.start()].trim();
    if prefix.is_empty() {
        let caps = pattern.captures(clause).unwrap();
        let marker = caps.get(1).unwrap().as_str();
        let body = clause[found.end()..].trim();
        let condition_body = body.split(',').next().unwrap_or(body).trim();
        let condition = format!("{marker} {condition_body}");
        let mut pieces = vec![clause.trim().to_string()];
        if !condition.eq_ignore_ascii_case(clause.trim()) {
            pieces.push(condition);
        }
        return pieces;
    }
    let tail = clause[found.start()..].trim().to_string();
    vec![clause.trim().to_string(), tail]
}

pub fn aggregate(labels: &[Label]) -> Label {
    if labels.is_empty() {
        return Label::InsufficientEvidence;
    }
    if labels.contains(&Label::Contradicted) {
        return Label::Contradicted;
    }
    if labels.iter().all(|label| *label == Label::Supported) {
        Label::Supported
    } else {
        Label::InsufficientEvidence
    }
}

/// Atoms the first pass left uncertain, excluding polarity contradictions.
pub fn uncertain_atoms(atoms: &[(String, Label)]) -> Vec<String> {
    atoms
        .iter()
        .filter(|(text, label)| {
            *label == Label::InsufficientEvidence && !polarity_conflict(text, "")
        })
        .map(|(text, _)| text.clone())
        .collect()
}

pub fn finish_atoms(atoms: &[(String, Label, String)], evidence: &str) -> (Label, String) {
    let mut gated = Vec::new();
    let mut quotes = Vec::new();
    for (text, label, quote) in atoms {
        let mut label = *label;
        let quote = quote.clone();
        if polarity_conflict(text, evidence) {
            label = Label::Contradicted;
        }
        let (label, quote) = if label == Label::InsufficientEvidence {
            (label, String::new())
        } else {
            gate_quote(label, &quote, evidence)
        };
        quotes.push(quote);
        gated.push(label);
    }
    let sentence = aggregate(&gated);
    let quote = if sentence == Label::Supported {
        quotes
            .into_iter()
            .find(|quote| !quote.is_empty() && evidence.contains(quote))
            .unwrap_or_default()
    } else {
        String::new()
    };
    (sentence, quote)
}

pub fn atom_system_prompt() -> &'static str {
    "Label each atom on its own against the tool output. \
Do not merge atoms and do not mark the whole sentence. \
SUPPORTED means the output entails that atom. \
CONTRADICTED means the output entails its negation. \
INSUFFICIENT_EVIDENCE means the output does neither. \
A condition atom 'if P' is SUPPORTED only when the output's condition has the same polarity. \
A negated atom (never, not, doesn't) is CONTRADICTED when the output shows the positive event. \
A paraphrase of code that is present is SUPPORTED: a counter growing by one, or a name such as release_permit describing that action. \
Text in another language, or an extra clause naming a mechanism the output does not show, is INSUFFICIENT_EVIDENCE. \
evidence_quote is an exact substring of the tool output, or empty for INSUFFICIENT_EVIDENCE. \
Copy each atom's text exactly."
}

pub fn recheck_system_prompt() -> &'static str {
    "The first pass abstained on one atom. Look only at the tool output. \
Return SUPPORTED only when an exact substring entails the atom. \
Return CONTRADICTED only when an exact substring entails its negation. \
Otherwise return INSUFFICIENT_EVIDENCE and an empty evidence_quote. \
Abstaining is a correct answer. Do not choose SUPPORTED in order to avoid abstaining."
}

impl Label {
    pub fn from_model(value: &str) -> Self {
        Self::parse(value)
    }
}

fn atom_payload(model: &str, evidence: &str, atoms: &[String]) -> serde_json::Value {
    let numbered = atoms
        .iter()
        .enumerate()
        .map(|(index, atom)| format!("{}. {atom}", index + 1))
        .collect::<Vec<_>>()
        .join("\n");
    serde_json::json!({
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": atom_system_prompt()},
            {"role": "user", "content": format!("Tool output:\n{evidence}\n\nAtoms:\n{numbered}")}
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "atom_verdicts",
                "strict": true,
                "schema": {
                    "type": "object",
                    "additionalProperties": false,
                    "properties": {
                        "atoms": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": false,
                                "properties": {
                                    "text": {"type": "string"},
                                    "label": {"type": "string", "enum": ["SUPPORTED", "CONTRADICTED", "INSUFFICIENT_EVIDENCE"]},
                                    "evidence_quote": {"type": "string"},
                                    "reasoning": {"type": "string"}
                                },
                                "required": ["text", "label", "evidence_quote", "reasoning"]
                            }
                        }
                    },
                    "required": ["atoms"]
                }
            }
        }
    })
}

fn recheck_payload(model: &str, evidence: &str, atom: &str) -> serde_json::Value {
    serde_json::json!({
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": recheck_system_prompt()},
            {"role": "user", "content": format!("Tool output:\n{evidence}\n\nAtom:\n{atom}")}
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "atom_recheck",
                "strict": true,
                "schema": {
                    "type": "object",
                    "additionalProperties": false,
                    "properties": {
                        "label": {"type": "string", "enum": ["SUPPORTED", "CONTRADICTED", "INSUFFICIENT_EVIDENCE"]},
                        "evidence_quote": {"type": "string"},
                        "reasoning": {"type": "string"}
                    },
                    "required": ["label", "evidence_quote", "reasoning"]
                }
            }
        }
    })
}

/// Classify semantic claims. Does not take a database connection.
pub async fn classify_semantic(requests: &[SemanticRequest]) -> Verification {
    let key = std::env::var("OPENAI_API_KEY").unwrap_or_default();
    let model = std::env::var("OPENAI_MODEL").unwrap_or_else(|_| "gpt-4.1-mini".to_string());
    let mut verification = Verification::default();
    if key.trim().is_empty() {
        tracing::warn!("OPENAI_API_KEY is empty; semantic claims stay unverified");
        return verification;
    }
    for request in requests {
        let started = std::time::Instant::now();
        let verdict = match classify_one(&key, &model, request).await {
            Ok(mut verdict) => {
                verdict.latency_ms = started.elapsed().as_millis() as u64;
                verdict
            }
            Err(err) => {
                tracing::warn!(error = %err, "semantic verification failed closed");
                ModelVerdict {
                    label: Label::InsufficientEvidence,
                    evidence_quote: String::new(),
                    prompt_tokens: 0,
                    completion_tokens: 0,
                    latency_ms: started.elapsed().as_millis() as u64,
                    calls: 0,
                }
            }
        };
        verification.verdicts.insert(request.claim.clone(), verdict);
    }
    verification
}

async fn classify_one(
    key: &str,
    model: &str,
    request: &SemanticRequest,
) -> Result<ModelVerdict, String> {
    let atoms = decompose(&request.claim);
    let mut calls = 0;
    let mut prompt = 0;
    let mut completion = 0;
    calls += 1;
    let first = ai::openai::chat(key, atom_payload(model, &request.evidence, &atoms))
        .await
        .map_err(|err| err.to_string())?;
    prompt += first.prompt_tokens;
    completion += first.completion_tokens;
    let parsed: serde_json::Value =
        serde_json::from_str(&first.content).unwrap_or_else(|_| serde_json::json!({}));
    let returned = parsed
        .get("atoms")
        .and_then(|value| value.as_array())
        .cloned()
        .unwrap_or_default();
    let mut labeled = Vec::new();
    for atom in &atoms {
        let found = returned
            .iter()
            .find(|item| item.get("text").and_then(|value| value.as_str()) == Some(atom.as_str()));
        let label = found
            .and_then(|item| item.get("label").and_then(|value| value.as_str()))
            .map(Label::from_model)
            .unwrap_or(Label::InsufficientEvidence);
        let quote = found
            .and_then(|item| item.get("evidence_quote").and_then(|value| value.as_str()))
            .unwrap_or("")
            .to_string();
        labeled.push((atom.clone(), label, quote));
    }
    for (text, label, _) in labeled.clone() {
        if label != Label::InsufficientEvidence || polarity_conflict(&text, &request.evidence) {
            continue;
        }
        calls += 1;
        match ai::openai::chat(key, recheck_payload(model, &request.evidence, &text)).await {
            Ok(second) => {
                prompt += second.prompt_tokens;
                completion += second.completion_tokens;
                let body: serde_json::Value =
                    serde_json::from_str(&second.content).unwrap_or_else(|_| serde_json::json!({}));
                let new_label = body
                    .get("label")
                    .and_then(|value| value.as_str())
                    .map(Label::from_model)
                    .unwrap_or(Label::InsufficientEvidence);
                let new_quote = body
                    .get("evidence_quote")
                    .and_then(|value| value.as_str())
                    .unwrap_or("")
                    .to_string();
                let (gated, quote) = gate_quote(new_label, &new_quote, &request.evidence);
                if let Some(slot) = labeled.iter_mut().find(|(atom, _, _)| atom == &text) {
                    slot.1 = gated;
                    slot.2 = quote;
                }
            }
            Err(err) => {
                tracing::warn!(error = %err, "atom recheck failed closed");
            }
        }
    }
    let (label, quote) = finish_atoms(&labeled, &request.evidence);
    Ok(ModelVerdict {
        label,
        evidence_quote: quote,
        prompt_tokens: prompt,
        completion_tokens: completion,
        latency_ms: 0,
        calls,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const LISTING: &str =
        "def release(self, permit):\n    self.quota_left += 1\n    self.release_permit()\n";
    const REVERSED: &str =
        "def release(self, permit):\n    self.release_permit()\n    self.quota_left += 1\n";

    #[test]
    fn a_straight_increment_is_supported_without_a_model() {
        let decision = route_claim("quota_left increases by 1 before release_permit", LISTING);
        assert_eq!(decision.label, Some(Label::Supported));
        assert!(!decision.evidence_quote.is_empty());
        assert!(LISTING.contains(&decision.evidence_quote));
    }

    #[test]
    fn reversed_order_is_a_contradiction() {
        let decision = route_claim("quota_left increases by 1 before release_permit", REVERSED);
        assert_eq!(decision.label, Some(Label::Contradicted));
        assert!(!decision.evidence_quote.is_empty());
        assert!(REVERSED.contains(&decision.evidence_quote));
    }

    #[test]
    fn unicode_inside_a_comment_does_not_move_the_proof() {
        let evidence = "def release(self, permit):\n    # Ω ignored\n    self.quota_left += 1\n    self.release_permit()\n";
        let decision = route_claim("quota_left increases by 1 before release_permit", evidence);
        assert_eq!(decision.label, Some(Label::Supported));
        assert!(evidence.contains(&decision.evidence_quote));
        assert_eq!(visible_code(evidence).len(), evidence.len());
    }

    #[test]
    fn a_commented_increment_is_not_proof() {
        let evidence = "def release(self, permit):\n    # quota_left += 1\n    self.release_permit()\n    self.quota_left += 1\n";
        let decision = route_claim("quota_left increases by 1 before release_permit", evidence);
        assert_eq!(decision.label, Some(Label::Contradicted));
    }

    #[test]
    fn a_loop_is_not_a_deterministic_proof() {
        let evidence = "def release(self, items):\n    for item in items:\n        self.quota_left += 1\n    self.release_permit()\n";
        let decision = route_claim("quota_left increases by 1 before release_permit", evidence);
        assert_eq!(decision.route, "semantic");
    }

    #[test]
    fn truncated_evidence_abstains() {
        let decision = route_claim(
            "quota_left increases by 1 before release_permit",
            &format!("{LISTING}[output truncated]\n"),
        );
        assert_eq!(decision.route, "abstain");
        assert_eq!(decision.label, Some(Label::InsufficientEvidence));
    }

    #[test]
    fn a_semantic_claim_without_a_verdict_stays_unverified() {
        let judgment = judge("the counter grows before the call", LISTING, None);
        assert_eq!(judgment.label, Label::InsufficientEvidence);
        assert_eq!(judgment.route, "semantic_failed");
    }

    #[test]
    fn a_model_quote_must_come_from_the_evidence() {
        let invented = ModelVerdict {
            label: Label::Supported,
            evidence_quote: "not in the listing".into(),
            prompt_tokens: 1,
            completion_tokens: 1,
            latency_ms: 1,
            calls: 1,
        };
        let judgment = judge(
            "the counter grows before the call",
            LISTING,
            Some(&invented),
        );
        assert_eq!(judgment.label, Label::InsufficientEvidence);
        let cited = ModelVerdict {
            evidence_quote: "self.quota_left += 1".into(),
            ..invented
        };
        let judgment = judge("the counter grows before the call", LISTING, Some(&cited));
        assert_eq!(judgment.label, Label::Supported);
    }

    #[test]
    fn every_atom_must_be_supported() {
        assert_eq!(
            aggregate(&[Label::Supported, Label::InsufficientEvidence]),
            Label::InsufficientEvidence
        );
        assert_eq!(
            aggregate(&[Label::Supported, Label::Supported]),
            Label::Supported
        );
    }
}
