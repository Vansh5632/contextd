//! Looking backwards on purpose.
//!
//! [`snapshot`](crate::snapshot) answers "what am I doing"; this module answers
//! "when did I last deal with this". Both are fail-open: if the local model is
//! offline we fall back to substring matching rather than returning nothing.

use contextd_core::event::ProcessedEvent;
use serde::Serialize;
use store::db::{get_event_by_id, search_events_by_text};
use store::vector::search_similar_events;

/// Why a particular event came back, so an agent can weigh it appropriately.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MatchKind {
    /// Nearest neighbour in embedding space.
    Semantic,
    /// The payload literally contains the query string.
    Text,
    /// Both methods found it. The strongest signal we have.
    Both,
}

#[derive(Debug, Clone, Serialize)]
pub struct SearchHit {
    #[serde(flatten)]
    pub event: ProcessedEvent,
    pub matched_by: MatchKind,
    /// Cosine distance for semantic hits. Lower is closer. Absent for text-only hits.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub distance: Option<f32>,
}

#[derive(Debug, Clone, Serialize)]
pub struct SearchResults {
    pub query: String,
    /// True when the semantic half actually ran. Lets a caller tell "nothing
    /// matched" apart from "the model was down so this is substring-only".
    pub semantic: bool,
    pub matches: Vec<SearchHit>,
}

/// Hybrid search: semantic neighbours merged with literal substring matches.
///
/// An agent asking "have I seen this error" wants both — the embedding finds
/// the paraphrase, the substring finds the exact stack frame.
pub async fn search(
    store: &store::Store,
    ai_client: Option<&ai::ollama::OllamaClient>,
    query: &str,
    limit: usize,
) -> anyhow::Result<SearchResults> {
    let embedding = crate::embed_query(ai_client, query).await;
    let conn = store.reader()?;

    let semantic = collect_semantic(&conn, embedding.as_deref(), limit);
    let textual = search_events_by_text(&conn, query, limit).unwrap_or_else(|err| {
        tracing::warn!(error = ?err, "text search failed; continuing with semantic matches only");
        Vec::new()
    });

    Ok(SearchResults {
        query: query.to_string(),
        semantic: embedding.is_some(),
        matches: merge(semantic, textual, limit),
    })
}

/// Semantic-only recall, with substring as the fallback when there is no model.
///
/// Unlike [`search`], this never mixes in literal matches when embeddings are
/// available: the caller is explicitly asking "what does this remind you of".
pub async fn recall(
    store: &store::Store,
    ai_client: Option<&ai::ollama::OllamaClient>,
    query: &str,
    limit: usize,
) -> anyhow::Result<SearchResults> {
    let embedding = crate::embed_query(ai_client, query).await;
    let conn = store.reader()?;

    let matches = match embedding.as_deref() {
        Some(embedding) => collect_semantic(&conn, Some(embedding), limit),
        None => search_events_by_text(&conn, query, limit)
            .unwrap_or_default()
            .into_iter()
            .map(|event| SearchHit {
                event,
                matched_by: MatchKind::Text,
                distance: None,
            })
            .collect(),
    };

    Ok(SearchResults {
        query: query.to_string(),
        semantic: embedding.is_some(),
        matches,
    })
}

fn collect_semantic(
    conn: &rusqlite::Connection,
    embedding: Option<&[f32]>,
    limit: usize,
) -> Vec<SearchHit> {
    let Some(embedding) = embedding else {
        return Vec::new();
    };

    let neighbours = match search_similar_events(conn, embedding, limit) {
        Ok(neighbours) => neighbours,
        Err(err) => {
            tracing::warn!(error = ?err, "semantic search failed; continuing without it");
            return Vec::new();
        }
    };

    neighbours
        .into_iter()
        .filter_map(|(id, distance)| {
            get_event_by_id(conn, &id)
                .ok()
                .flatten()
                .map(|event| SearchHit {
                    event,
                    matched_by: MatchKind::Semantic,
                    distance: Some(distance),
                })
        })
        .collect()
}

/// Merge the two result sets, promoting anything both halves agreed on.
///
/// Semantic order is preserved because it is the more useful ranking; text-only
/// hits are appended after. Events found by both are marked and stay in their
/// semantic position.
fn merge(semantic: Vec<SearchHit>, textual: Vec<ProcessedEvent>, limit: usize) -> Vec<SearchHit> {
    let mut merged = semantic;

    for event in textual {
        match merged
            .iter_mut()
            .find(|hit| hit.event.raw.id == event.raw.id)
        {
            Some(existing) => existing.matched_by = MatchKind::Both,
            None => merged.push(SearchHit {
                event,
                matched_by: MatchKind::Text,
                distance: None,
            }),
        }
    }

    merged.truncate(limit);
    merged
}

#[cfg(test)]
mod tests {
    use super::*;
    use contextd_core::event::{EventSource, RawEvent};
    use contextd_core::test_utils::test_config_in_memory;
    use serde_json::json;
    use store::db::{init_db, insert_event};
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

    #[tokio::test]
    async fn search_without_ollama_falls_back_to_substring() {
        let store = store::Store::open(&test_config_in_memory()).unwrap();
        {
            let conn = store.writer().await;
            insert_event(
                &conn,
                &processed("a", 1, json!({"command": "cargo test broker"})),
            )
            .unwrap();
            insert_event(&conn, &processed("b", 2, json!({"command": "ls"}))).unwrap();
        }

        let results = search(&store, None, "broker", 10).await.unwrap();

        assert!(!results.semantic, "no model means no semantic half");
        assert_eq!(results.matches.len(), 1);
        assert_eq!(results.matches[0].event.raw.id, "a");
        assert_eq!(results.matches[0].matched_by, MatchKind::Text);
    }

    #[tokio::test]
    async fn recall_without_ollama_still_answers() {
        let store = store::Store::open(&test_config_in_memory()).unwrap();
        {
            let conn = store.writer().await;
            insert_event(
                &conn,
                &processed("boom", 1, json!({"stderr": "connection refused"})),
            )
            .unwrap();
        }

        let results = recall(&store, None, "connection refused", 5).await.unwrap();
        assert_eq!(results.matches.len(), 1);
        assert_eq!(results.matches[0].matched_by, MatchKind::Text);
    }

    #[test]
    fn merge_marks_events_found_by_both_halves() {
        let shared = processed("shared", 1, json!({"command": "cargo build"}));
        let semantic = vec![SearchHit {
            event: shared.clone(),
            matched_by: MatchKind::Semantic,
            distance: Some(0.1),
        }];
        let textual = vec![
            shared,
            processed("text-only", 2, json!({"command": "cargo fmt"})),
        ];

        let merged = merge(semantic, textual, 10);

        assert_eq!(merged.len(), 2);
        assert_eq!(merged[0].event.raw.id, "shared");
        assert_eq!(merged[0].matched_by, MatchKind::Both);
        assert_eq!(merged[0].distance, Some(0.1));
        assert_eq!(merged[1].matched_by, MatchKind::Text);
    }

    #[test]
    fn merge_respects_the_limit() {
        let semantic: Vec<SearchHit> = (0..5)
            .map(|i| SearchHit {
                event: processed(&format!("s{i}"), i, json!({})),
                matched_by: MatchKind::Semantic,
                distance: Some(0.1),
            })
            .collect();
        let textual: Vec<ProcessedEvent> = (0..5)
            .map(|i| processed(&format!("t{i}"), i, json!({})))
            .collect();

        assert_eq!(merge(semantic, textual, 3).len(), 3);
    }

    #[test]
    fn text_search_treats_wildcards_literally() {
        let conn = init_db(&test_config_in_memory()).unwrap();
        insert_event(&conn, &processed("pct", 1, json!({"note": "100% done"}))).unwrap();
        insert_event(&conn, &processed("other", 2, json!({"note": "1000 done"}))).unwrap();

        // Without LIKE escaping, "100%" would match "1000 done" too.
        let hits = search_events_by_text(&conn, "100%", 10).unwrap();
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].raw.id, "pct");
    }

    #[tokio::test]
    async fn semantic_hits_are_returned_with_distance() {
        let conn = init_db(&test_config_in_memory()).unwrap();
        insert_event(
            &conn,
            &processed("login", 1, json!({"command": "fix login bug"})),
        )
        .unwrap();
        let vector = unit_embedding(3);
        insert_embedding(&conn, "login", &vector).unwrap();

        let hits = collect_semantic(&conn, Some(&vector), 5);
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].event.raw.id, "login");
        assert_eq!(hits[0].matched_by, MatchKind::Semantic);
        assert!(hits[0].distance.is_some());
    }
}
