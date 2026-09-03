//! Assembling the briefing.
//!
//! This is where the four tiers meet. Each contributes what it is good at:
//!
//! - **Tier 0** the last few minutes, with no disk access at all
//! - **Tier 1** older events that match the question semantically
//! - **Tier 2** what usually accompanies the work in progress
//! - **Tier 3** an acknowledgement that older history exists, and roughly what
//!   is in it, without paying to decompress it
//!
//! Everything is scored by [`crate::relevance`], ordered, and then fitted to a
//! token budget. The load-bearing property, carried from the original design:
//! if the model is offline, every semantic signal is simply zero and the result
//! degrades to a recency-and-importance briefing. It never fails.

use std::time::{SystemTime, UNIX_EPOCH};

use contextd_core::event::{Intent, ProcessedEvent};
use memory::{SharedGraph, WorkingSet};
use serde::Serialize;
use store::db::{get_current_intent, get_event_by_id, get_recent_events};
use store::vector::search_similar_events;

use crate::budget::{DEFAULT_TOKEN_BUDGET, TokenBudget, fit_to_tokens};
use crate::item::ContextItem;
use crate::relevance;

pub const RECENT_LIMIT: usize = 10;
pub const RELATED_LIMIT: usize = 5;

/// Longest any single line of a briefing may be.
///
/// Without this, one pathological payload — a stack trace, a minified bundle
/// path — could eat the entire budget and crowd out everything else.
const MAX_ITEM_TOKENS: usize = 120;

/// What an agent gets when it asks "what am I doing right now?"
#[derive(Debug, Clone, Serialize)]
pub struct ContextSnapshot {
    /// What the user said they are trying to do, if they ever said. The only
    /// field they authored; everything else is observed.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub intent: Option<Intent>,

    /// Which run of the daemon this is, so an agent can tell sessions apart.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session_id: Option<String>,

    /// What just happened, most relevant first.
    pub recent_activity: Vec<ContextItem>,

    /// Older work that bears on the question. Empty when Ollama is down.
    pub relevant_history: Vec<ContextItem>,

    /// Things the knowledge graph associates with the work in progress.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub related: Vec<RelatedEntity>,

    /// A pointer to compressed history, when there is any.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub archived: Option<ArchiveHint>,

    /// True when the budget forced something out, so the reader knows the
    /// briefing is a selection rather than everything there was.
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub truncated: bool,

    /// Roughly what this snapshot costs to read.
    pub estimated_tokens: usize,
}

/// Something the graph links to the current work.
#[derive(Debug, Clone, Serialize)]
pub struct RelatedEntity {
    pub kind: String,
    pub name: String,
    /// How strongly associated, 0..1.
    pub strength: f32,
}

/// What is in long-term storage, without decompressing it.
#[derive(Debug, Clone, Serialize)]
pub struct ArchiveHint {
    pub event_count: usize,
    pub oldest_ms: u64,
    /// A sentence or two describing the most recent archived window.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub digest: Option<String>,
}

/// Everything the broker needs to answer a question.
///
/// Grouped into a struct because the alternative is a function taking six
/// positional arguments, half of them optional.
pub struct SnapshotRequest<'a> {
    pub store: &'a store::Store,
    pub ollama: Option<&'a ai::ollama::OllamaClient>,
    /// Tier 0. Absent for callers that have no live daemon state, such as tests
    /// and one-shot tools.
    pub working: Option<&'a WorkingSet>,
    /// Tier 2. Absent for the same reason.
    pub graph: Option<&'a SharedGraph>,
    pub query: &'a str,
    pub token_budget: usize,
    /// Include the per-signal breakdown on each item.
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

/// Build a briefing.
pub async fn build(request: SnapshotRequest<'_>) -> anyhow::Result<ContextSnapshot> {
    let now_ms = now_ms();

    // Tier 0 first: it needs no I/O, and it tells us what the question is about
    // when the caller did not say.
    let working_events = request
        .working
        .map(|working| working.recent(RECENT_LIMIT))
        .unwrap_or_default();

    let recent_events = if working_events.is_empty() {
        let conn = request.store.reader()?;
        get_recent_events(&conn, RECENT_LIMIT)?
    } else {
        working_events
    };

    let query_text = if request.query.trim().is_empty() {
        // With no question, the question is "what is going on", and the best
        // available statement of that is the most recent thing that happened.
        recent_events
            .first()
            .map(|event| {
                event
                    .summary
                    .clone()
                    .unwrap_or_else(|| event.raw.payload.to_string())
            })
            .unwrap_or_default()
    } else {
        request.query.to_string()
    };

    // Embed before taking a connection: the model call is the slow part and
    // must not hold a reader while it runs.
    let query_embedding = crate::embed_query(request.ollama, &query_text).await;

    // Tier 2, also lock-only, before touching the database.
    let related = match (request.graph, recent_events.first()) {
        (Some(graph), Some(anchor)) => graph
            .related_to_event(anchor, RELATED_LIMIT)
            .await
            .into_iter()
            .map(|related| RelatedEntity {
                kind: related.entity.kind.as_str().to_string(),
                name: related.entity.name,
                strength: (relevance::proximity(related.weight) * 100.0).round() / 100.0,
            })
            .collect(),
        _ => Vec::new(),
    };

    let conn = request.store.reader()?;

    // Tier 0 knows the intent declared during this run without a query. The
    // database is the fallback, and covers intents declared before a restart.
    let intent = request
        .working
        .and_then(|working| working.intent())
        .or_else(|| {
            get_current_intent(&conn).unwrap_or_else(|err| {
                tracing::warn!(error = ?err, "failed to read current intent; continuing without it");
                None
            })
        });

    let history = load_relevant_history(&conn, &recent_events, query_embedding.as_deref());
    let archived = load_archive_hint(&conn);

    // Score everything, then let the budget decide where the line falls.
    let mut budget = TokenBudget::new(request.token_budget);

    let recent_activity = fit_items(
        rank(recent_events.iter().map(|event| (event, None)), now_ms),
        &mut budget,
        request.explain,
    );
    let relevant_history = fit_items(
        rank(
            history
                .iter()
                .map(|(event, distance)| (event, Some(*distance))),
            now_ms,
        ),
        &mut budget,
        request.explain,
    );

    Ok(ContextSnapshot {
        intent,
        session_id: request.working.map(|working| working.session_id()),
        recent_activity,
        relevant_history,
        related,
        archived,
        truncated: budget.truncated(),
        estimated_tokens: budget.spent(),
    })
}

/// Score a set of events and sort them by relevance, best first.
fn rank<'a>(
    events: impl Iterator<Item = (&'a ProcessedEvent, Option<f32>)>,
    now_ms: u64,
) -> Vec<(&'a ProcessedEvent, relevance::Signals)> {
    let mut scored: Vec<(&ProcessedEvent, relevance::Signals)> = events
        .map(|(event, distance)| (event, relevance::score(event, now_ms, distance, None)))
        .collect();

    scored.sort_by(|a, b| {
        b.1.combined()
            .partial_cmp(&a.1.combined())
            .unwrap_or(std::cmp::Ordering::Equal)
            // Ties broken by recency, then id, so output is stable.
            .then_with(|| b.0.raw.timestamp_ms.cmp(&a.0.raw.timestamp_ms))
            .then_with(|| a.0.raw.id.cmp(&b.0.raw.id))
    });
    scored
}

/// Render as many ranked events as the budget allows, best first.
fn fit_items(
    ranked: Vec<(&ProcessedEvent, relevance::Signals)>,
    budget: &mut TokenBudget,
    explain: bool,
) -> Vec<ContextItem> {
    let mut items = Vec::new();

    for (event, signals) in ranked {
        let mut item = ContextItem::render(event, signals, explain);
        // Cap any single line before it is offered, so one pathological payload
        // cannot crowd out everything ranked below it.
        item.text = fit_to_tokens(&item.text, MAX_ITEM_TOKENS);

        if budget.try_spend(item.cost_text()) {
            items.push(item);
        }
    }

    items
}

/// Tier 1 semantic matches, excluding anything already in the recent window.
///
/// Returns `(event, distance)` so the caller can fold similarity into the
/// ranking rather than throwing the distance away.
fn load_relevant_history(
    conn: &rusqlite::Connection,
    recent: &[ProcessedEvent],
    query_embedding: Option<&[f32]>,
) -> Vec<(ProcessedEvent, f32)> {
    let Some(embedding) = query_embedding else {
        return Vec::new();
    };

    let matches = match search_similar_events(conn, embedding, RELATED_LIMIT * 2) {
        Ok(matches) => matches,
        Err(err) => {
            tracing::warn!(
                error = ?err,
                "semantic search failed when building context snapshot; continuing without semantic matches"
            );
            return Vec::new();
        }
    };

    let mut history = Vec::new();
    for (id, distance) in matches {
        if history.len() >= RELATED_LIMIT {
            break;
        }
        if recent.iter().any(|event| event.raw.id == id) {
            continue;
        }
        if let Ok(Some(event)) = get_event_by_id(conn, &id) {
            history.push((event, distance));
        }
    }

    history
}

/// Tell the agent that older history exists, cheaply.
fn load_archive_hint(conn: &rusqlite::Connection) -> Option<ArchiveHint> {
    let event_count = store::archive::archived_event_count(conn).ok()?;
    if event_count == 0 {
        return None;
    }

    let segments = store::archive::segments_in_range(conn, 0, u64::MAX).ok()?;
    let oldest_ms = segments.iter().map(|s| s.from_ms).min().unwrap_or_default();

    Some(ArchiveHint {
        event_count,
        oldest_ms,
        // Segments come back newest first, so this describes the most recent
        // window to age out — the part most likely to still be relevant.
        digest: segments
            .first()
            .map(|segment| fit_to_tokens(&segment.digest, MAX_ITEM_TOKENS)),
    })
}

/// Ask for a snapshot with default settings. Fail-open: recency always returns.
pub async fn snapshot_now(
    store: &store::Store,
    ai_client: Option<&ai::ollama::OllamaClient>,
    query: &str,
) -> anyhow::Result<ContextSnapshot> {
    build(SnapshotRequest::new(store, query).with_ollama(ai_client)).await
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|elapsed| elapsed.as_millis() as u64)
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    use contextd_core::event::{EventSource, ProcessedEvent, RawEvent};
    use contextd_core::test_utils::test_config_in_memory;
    use serde_json::json;
    use store::db::insert_event;
    use store::vector::{EMBEDDING_DIMENSIONS, insert_embedding};

    fn processed(id: &str, timestamp_ms: u64, payload: serde_json::Value) -> ProcessedEvent {
        ProcessedEvent::new(
            RawEvent {
                id: id.to_string(),
                timestamp_ms,
                source: EventSource::Shell,
                payload,
            },
            0.5,
        )
    }

    fn unit_embedding(index: usize) -> Vec<f32> {
        let mut embedding = vec![0.0f32; EMBEDDING_DIMENSIONS];
        embedding[index] = 1.0;
        embedding
    }

    fn ids(items: &[ContextItem]) -> Vec<&str> {
        items.iter().map(|item| item.id.as_str()).collect()
    }

    async fn store_with(events: &[ProcessedEvent]) -> store::Store {
        let store = store::Store::open(&test_config_in_memory()).unwrap();
        {
            let conn = store.writer().await;
            for event in events {
                insert_event(&conn, event).unwrap();
            }
        }
        store
    }

    #[tokio::test]
    async fn without_ollama_the_briefing_is_recency_and_importance() {
        let now = now_ms();
        let store = store_with(&[
            processed("old", now - 3_600_000, json!({"command": "ls"})),
            processed("new", now, json!({"command": "cargo build"})),
        ])
        .await;

        let snapshot = snapshot_now(&store, None, "").await.unwrap();

        assert_eq!(ids(&snapshot.recent_activity), vec!["new", "old"]);
        assert!(snapshot.relevant_history.is_empty());
    }

    #[tokio::test]
    async fn semantic_matches_do_not_repeat_the_recent_window() {
        let now = now_ms();
        let store = store_with(&[
            processed("login", now - 1_000, json!({"command": "fix login"})),
            processed("now", now, json!({"command": "cargo test"})),
        ])
        .await;

        let login_vec = unit_embedding(0);
        {
            let conn = store.writer().await;
            insert_embedding(&conn, "login", &login_vec).unwrap();
        }

        let snapshot = build(SnapshotRequest::new(&store, "")).await.unwrap();
        assert!(
            snapshot.relevant_history.is_empty(),
            "login is already in the recent window, so it must not be repeated"
        );
    }

    #[tokio::test]
    async fn the_briefing_carries_the_declared_intent() {
        let store = store_with(&[processed("x", now_ms(), json!({"command": "ls"}))]).await;
        {
            let conn = store.writer().await;
            store::db::set_intent(&conn, "fixing the login bug", 5_000).unwrap();
        }

        let intent = snapshot_now(&store, None, "")
            .await
            .unwrap()
            .intent
            .expect("declared intent should be returned");

        assert_eq!(intent.text, "fixing the login bug");
        assert_eq!(intent.declared_at_ms, 5_000);
    }

    #[tokio::test]
    async fn an_intent_declared_this_session_is_read_from_tier_zero() {
        let store = store_with(&[]).await;
        let working = WorkingSet::new("s");
        working.set_intent(Intent {
            text: "fixing the login bug".to_string(),
            declared_at_ms: 9_000,
        });

        let snapshot = build(SnapshotRequest::new(&store, "").with_tiers(Some(&working), None))
            .await
            .unwrap();

        assert_eq!(snapshot.intent.unwrap().text, "fixing the login bug");
    }

    #[tokio::test]
    async fn an_intent_from_before_a_restart_still_surfaces() {
        // Tier 0 is empty on a fresh daemon, but what the user declared
        // yesterday is still the best statement of what they are doing.
        let store = store_with(&[]).await;
        {
            let conn = store.writer().await;
            store::db::set_intent(&conn, "declared last week", 1_000).unwrap();
        }
        let working = WorkingSet::new("s");

        let snapshot = build(SnapshotRequest::new(&store, "").with_tiers(Some(&working), None))
            .await
            .unwrap();

        assert_eq!(snapshot.intent.unwrap().text, "declared last week");
    }

    #[tokio::test]
    async fn the_newest_intent_wins() {
        let store = store_with(&[]).await;
        {
            let conn = store.writer().await;
            store::db::set_intent(&conn, "first thing", 1_000).unwrap();
            store::db::set_intent(&conn, "second thing", 2_000).unwrap();
        }

        let snapshot = snapshot_now(&store, None, "").await.unwrap();
        assert_eq!(snapshot.intent.unwrap().text, "second thing");
    }

    #[tokio::test]
    async fn items_are_summaries_rather_than_raw_payloads() {
        let now = now_ms();
        let mut event = processed("e", now, json!({"command": "cargo build --release"}));
        event.summary = Some("ran the release build".to_string());
        let store = store_with(&[event]).await;

        let snapshot = snapshot_now(&store, None, "").await.unwrap();
        assert_eq!(snapshot.recent_activity[0].text, "ran the release build");
    }

    #[tokio::test]
    async fn a_tight_budget_truncates_and_says_so() {
        let now = now_ms();
        let events: Vec<ProcessedEvent> = (0..RECENT_LIMIT)
            .map(|i| {
                processed(
                    &format!("e{i}"),
                    now - i as u64,
                    json!({"command": format!("cargo build --feature verbose-thing-{i}")}),
                )
            })
            .collect();
        let store = store_with(&events).await;

        let snapshot = build(SnapshotRequest::new(&store, "").with_budget(12))
            .await
            .unwrap();

        assert!(snapshot.recent_activity.len() < RECENT_LIMIT);
        assert!(snapshot.truncated, "a trimmed briefing must admit it");
        assert!(snapshot.estimated_tokens <= 12);
    }

    #[tokio::test]
    async fn a_generous_budget_truncates_nothing() {
        let now = now_ms();
        let store = store_with(&[processed("e", now, json!({"command": "ls"}))]).await;

        let snapshot = snapshot_now(&store, None, "").await.unwrap();

        assert!(!snapshot.truncated);
        assert_eq!(snapshot.recent_activity.len(), 1);
        assert!(snapshot.estimated_tokens > 0);
    }

    #[tokio::test]
    async fn the_budget_keeps_the_most_relevant_when_it_has_to_choose() {
        // Truncation must drop the least useful line, not an arbitrary one.
        let now = now_ms();
        let store = store_with(&[
            processed("ancient", now - 10 * 3_600_000, json!({"command": "ls"})),
            processed("current", now, json!({"command": "ls"})),
        ])
        .await;

        let snapshot = build(SnapshotRequest::new(&store, "").with_budget(3))
            .await
            .unwrap();

        assert_eq!(ids(&snapshot.recent_activity), vec!["current"]);
    }

    #[tokio::test]
    async fn tier_zero_answers_without_touching_the_database() {
        // The working set is authoritative for "right now": these events were
        // never written to SQLite, and must still appear.
        let store = store_with(&[]).await;
        let working = WorkingSet::new("session-1");
        working.record(processed(
            "live",
            now_ms(),
            json!({"command": "cargo test"}),
        ));

        let snapshot = build(SnapshotRequest::new(&store, "").with_tiers(Some(&working), None))
            .await
            .unwrap();

        assert_eq!(ids(&snapshot.recent_activity), vec!["live"]);
        assert_eq!(snapshot.session_id.as_deref(), Some("session-1"));
    }

    #[tokio::test]
    async fn an_empty_working_set_falls_back_to_the_database() {
        // On a fresh daemon Tier 0 is empty but Tier 1 still remembers
        // yesterday, and a briefing of nothing would be a regression.
        let store = store_with(&[processed("stored", now_ms(), json!({"command": "ls"}))]).await;
        let working = WorkingSet::new("session-1");

        let snapshot = build(SnapshotRequest::new(&store, "").with_tiers(Some(&working), None))
            .await
            .unwrap();

        assert_eq!(ids(&snapshot.recent_activity), vec!["stored"]);
    }

    #[tokio::test]
    async fn the_graph_contributes_what_usually_accompanies_the_work() {
        let store = store_with(&[]).await;
        let working = WorkingSet::new("s");
        let graph = SharedGraph::empty();

        for round in 0..3u64 {
            let base = round * 10_000;
            graph
                .observe(&ProcessedEvent::new(
                    RawEvent {
                        id: format!("f{round}"),
                        timestamp_ms: base,
                        source: EventSource::FileSystem,
                        payload: json!({"path": "/repo/src/auth.rs"}),
                    },
                    0.5,
                ))
                .await;
            graph
                .observe(&processed(
                    &format!("c{round}"),
                    base + 500,
                    json!({"command": "cargo test"}),
                ))
                .await;
        }

        working.record(ProcessedEvent::new(
            RawEvent {
                id: "now".to_string(),
                timestamp_ms: now_ms(),
                source: EventSource::FileSystem,
                payload: json!({"path": "/repo/src/auth.rs"}),
            },
            0.5,
        ));

        let snapshot =
            build(SnapshotRequest::new(&store, "").with_tiers(Some(&working), Some(&graph)))
                .await
                .unwrap();

        assert_eq!(snapshot.related[0].name, "cargo test");
        assert_eq!(snapshot.related[0].kind, "command");
        assert!(snapshot.related[0].strength > 0.0);
    }

    #[tokio::test]
    async fn the_briefing_mentions_the_archive_without_decompressing_it() {
        let store = store_with(&[processed("live", now_ms(), json!({"command": "ls"}))]).await;
        {
            let conn = store.writer().await;
            let mut old = processed("archived", 1_000, json!({"command": "cargo build"}));
            old.summary = Some("ran the build".to_string());
            store::archive::archive_events(&conn, &[old]).unwrap();
        }

        let hint = snapshot_now(&store, None, "")
            .await
            .unwrap()
            .archived
            .expect("an archive with events in it should be mentioned");

        assert_eq!(hint.event_count, 1);
        assert_eq!(hint.oldest_ms, 1_000);
        assert_eq!(hint.digest.as_deref(), Some("ran the build"));
    }

    #[tokio::test]
    async fn an_empty_archive_is_not_mentioned_at_all() {
        let store = store_with(&[processed("live", now_ms(), json!({"command": "ls"}))]).await;

        assert!(
            snapshot_now(&store, None, "")
                .await
                .unwrap()
                .archived
                .is_none()
        );
    }

    #[tokio::test]
    async fn a_briefing_with_nothing_to_report_is_still_valid() {
        let store = store_with(&[]).await;
        let snapshot = snapshot_now(&store, None, "").await.unwrap();

        assert!(snapshot.recent_activity.is_empty());
        assert!(snapshot.relevant_history.is_empty());
        assert!(!snapshot.truncated);
        assert!(serde_json::to_string(&snapshot).is_ok());
    }

    #[tokio::test]
    async fn explaining_a_briefing_shows_why_each_line_is_there() {
        let store = store_with(&[processed("e", now_ms(), json!({"command": "ls"}))]).await;

        let mut request = SnapshotRequest::new(&store, "");
        request.explain = true;
        let snapshot = build(request).await.unwrap();

        let why = snapshot.recent_activity[0]
            .why
            .expect("explain mode should include the breakdown");
        assert!(why.recency > 0.0);
        assert!(why.importance > 0.0);
    }

    #[tokio::test]
    async fn one_enormous_payload_cannot_crowd_out_the_rest() {
        let now = now_ms();
        let store = store_with(&[
            processed("huge", now, json!({"command": "x".repeat(20_000)})),
            processed("small", now - 1_000, json!({"command": "ls"})),
        ])
        .await;

        let snapshot = snapshot_now(&store, None, "").await.unwrap();

        assert_eq!(snapshot.recent_activity.len(), 2, "both should survive");
        assert!(snapshot.recent_activity.iter().any(|i| i.id == "small"));
    }
}
