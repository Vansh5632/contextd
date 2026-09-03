//! One line of a briefing.
//!
//! The old snapshot handed back whole `ProcessedEvent` structs, raw JSON
//! payload and all. That is the wrong shape for the consumer: an agent reading
//! `{"action":"Modify(Data(Any))","path":"/home/me/p/src/main.rs"}` has to
//! reverse-engineer what happened, and pays tokens for the privilege.
//!
//! A `ContextItem` is the rendered form — a sentence where enrichment produced
//! one, a compact rendering of the payload where it did not — plus the numbers
//! explaining why it is in the briefing at all.

use contextd_core::event::{EventSource, ProcessedEvent};
use serde::Serialize;

use crate::relevance::Signals;

/// A single observation, ready to be read.
#[derive(Debug, Clone, Serialize)]
pub struct ContextItem {
    pub id: String,
    pub timestamp_ms: u64,
    pub source: EventSource,
    /// What happened, in words.
    pub text: String,
    /// Combined relevance, 0..1. What the briefing is ordered by.
    pub relevance: f32,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub use_case: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub memory_type: Option<String>,
    /// The individual signals, so a surprising briefing can be explained.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub why: Option<SignalBreakdown>,
}

/// The four inputs to an item's relevance, rounded for readability.
#[derive(Debug, Clone, Copy, Serialize)]
pub struct SignalBreakdown {
    pub recency: f32,
    pub importance: f32,
    pub similarity: f32,
    pub proximity: f32,
}

impl From<Signals> for SignalBreakdown {
    fn from(signals: Signals) -> Self {
        let round = |value: f32| (value * 100.0).round() / 100.0;
        Self {
            recency: round(signals.recency),
            importance: round(signals.importance),
            similarity: round(signals.similarity),
            proximity: round(signals.proximity),
        }
    }
}

impl ContextItem {
    /// Render an event for a briefing.
    pub fn render(event: &ProcessedEvent, signals: Signals, explain: bool) -> Self {
        Self {
            id: event.raw.id.clone(),
            timestamp_ms: event.raw.timestamp_ms,
            source: event.raw.source.clone(),
            text: describe(event),
            relevance: (signals.combined() * 1000.0).round() / 1000.0,
            use_case: event.use_case.clone(),
            memory_type: event.memory_type.clone(),
            why: explain.then(|| signals.into()),
        }
    }

    /// Roughly what this item costs to include.
    pub fn cost_text(&self) -> &str {
        &self.text
    }
}

/// The best available description of an event.
///
/// Prefers the summary written during enrichment. Falls back to summarising on
/// the spot, and only then to the payload — so a briefing is readable even for
/// events that arrived seconds ago and have not been enriched yet.
fn describe(event: &ProcessedEvent) -> String {
    if let Some(summary) = event.summary.as_deref().map(str::trim)
        && !summary.is_empty()
    {
        return summary.to_string();
    }

    if let Some(summary) = pipeline::content::summarize(&event.raw) {
        return summary;
    }

    compact_payload(&event.raw.payload)
}

/// Render a JSON payload as `key=value` pairs rather than as JSON.
///
/// Braces, quotes, and colons are pure token cost for a reader that is not
/// going to parse them.
fn compact_payload(payload: &serde_json::Value) -> String {
    let Some(object) = payload.as_object() else {
        return payload.to_string();
    };

    let mut parts: Vec<String> = object
        .iter()
        .map(|(key, value)| match value.as_str() {
            Some(text) => format!("{key}={text}"),
            None => format!("{key}={value}"),
        })
        .collect();
    parts.sort();
    parts.join(" ")
}

#[cfg(test)]
mod tests {
    use super::*;
    use contextd_core::event::RawEvent;
    use serde_json::json;

    fn event(payload: serde_json::Value) -> ProcessedEvent {
        ProcessedEvent::new(
            RawEvent {
                id: "e1".to_string(),
                timestamp_ms: 1_000,
                source: EventSource::Shell,
                payload,
            },
            0.5,
        )
    }

    #[test]
    fn an_enriched_summary_is_used_verbatim() {
        let mut e = event(json!({"command": "cargo build"}));
        e.summary = Some("ran the build, three warnings".to_string());

        assert_eq!(describe(&e), "ran the build, three warnings");
    }

    #[test]
    fn an_unenriched_event_is_summarised_on_the_spot() {
        // A briefing must read well for events that arrived a second ago and
        // have not been through the enrichment queue yet.
        let e = event(json!({"command": "cargo build"}));
        assert_eq!(describe(&e), "ran `cargo build`");
    }

    #[test]
    fn an_empty_summary_does_not_produce_an_empty_line() {
        let mut e = event(json!({"command": "cargo build"}));
        e.summary = Some("   ".to_string());

        assert_eq!(describe(&e), "ran `cargo build`");
    }

    #[test]
    fn an_undescribable_payload_falls_back_to_readable_pairs() {
        let e = event(json!({"zebra": "last", "apple": 1}));
        assert_eq!(describe(&e), "apple=1 zebra=last");
    }

    #[test]
    fn the_fallback_rendering_is_cheaper_than_the_json() {
        let payload = json!({"path": "/repo/src/main.rs", "action": "Modify(Data(Any))"});
        let rendered = compact_payload(&payload);

        assert!(
            rendered.len() < payload.to_string().len(),
            "the point of the fallback is to cost fewer tokens than raw JSON"
        );
        assert!(!rendered.contains('{'));
        assert!(!rendered.contains('"'));
    }

    #[test]
    fn a_non_object_payload_still_renders() {
        let e = event(json!("just a string"));
        assert_eq!(describe(&e), "\"just a string\"");
    }

    #[test]
    fn the_breakdown_is_only_present_when_asked_for() {
        let e = event(json!({"command": "ls"}));
        let signals = Signals::default();

        assert!(ContextItem::render(&e, signals, false).why.is_none());
        assert!(ContextItem::render(&e, signals, true).why.is_some());
    }

    #[test]
    fn rendering_carries_the_enrichment_through() {
        let mut e = event(json!({"command": "ls"}));
        e.use_case = Some("coding".to_string());
        e.memory_type = Some("episodic".to_string());

        let item = ContextItem::render(&e, Signals::default(), false);
        assert_eq!(item.use_case.as_deref(), Some("coding"));
        assert_eq!(item.memory_type.as_deref(), Some("episodic"));
        assert_eq!(item.id, "e1");
    }
}
