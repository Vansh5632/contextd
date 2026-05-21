use ai::ollama::OllamaClient;
use contextd_core::event::ProcessedEvent;
use serde::Serialize;
use store::db::{get_event_by_id, get_recent_events};
use store::vector::search_similar_events;
use tracing::warn;

/// The final payload that will be delivered to external LLMs (like Cursor or Claude)
#[derive(Debug, Serialize)]
pub struct ContextSnapshot {
    /// What the developer just did (last 10 events)
    pub recent_activity: Vec<ProcessedEvent>,
    /// What the developer did in the past related to their current query
    pub relevant_history: Vec<ProcessedEvent>,
}

/// Generates a rich context snapshot by combining recent chronological activity
/// with semantically relevant historical activity.
pub async fn generate_snapshot(
    db_conn: &rusqlite::Connection,
    ai_client: &OllamaClient,
    query: &str,
) -> anyhow::Result<ContextSnapshot> {
    // 1. Get chronological context (Tier 0)
    let recent_activity = get_recent_events(db_conn, 10)?;

    // 2. Get semantic context (Tier 1)
    let mut relevant_history = Vec::new();

    match ai_client.get_embedding(query, None).await {
        Ok(query_embedding) => match search_similar_events(db_conn, &query_embedding, 5) {
            Ok(matches) => {
                for (id, _distance) in matches {
                    if let Ok(Some(event)) = get_event_by_id(db_conn, &id) {
                        if !recent_activity.iter().any(|e| e.raw.id == id) {
                            relevant_history.push(event);
                        }
                    }
                }
            }
            Err(err) => {
                warn!(
                    error = ?err,
                    "semantic search failed when building context snapshot; continuing without semantic matches"
                );
            }
        },
        Err(err) => {
            warn!(
                error = ?err,
                "failed to generate query embedding for semantic context; continuing without semantic matches"
            );
        }
    }

    Ok(ContextSnapshot {
        recent_activity,
        relevant_history,
    })
}

// ==========================================
// TESTS
// ==========================================
#[cfg(test)]
mod tests {
    use super::*;
    use contextd_core::config::AppConfig;
    use rusqlite::Connection;

    async fn seed_embeddings_if_empty(conn: &Connection, ai_client: &OllamaClient) {
        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM vec_events", [], |row| row.get(0))
            .unwrap_or(0);
        if count > 0 {
            return;
        }

        let mut stmt = conn
            .prepare("SELECT id, payload FROM events ORDER BY timestamp_ms DESC LIMIT 20")
            .expect("events query should prepare");
        let rows: Vec<(String, String)> = stmt
            .query_map([], |row| Ok((row.get(0)?, row.get(1)?)))
            .expect("events should query")
            .filter_map(Result::ok)
            .collect();

        for (id, payload) in rows {
            if let Ok(embedding) = ai_client.get_embedding(&payload, None).await {
                let _ = store::vector::insert_embedding(conn, &id, &embedding);
            }
        }
    }

    #[tokio::test]
    #[ignore = "Requires a populated database and Ollama running"]
    async fn test_live_snapshot() {
        // Connect to your ACTUAL local development database
        let config = AppConfig::default();
        store::vector::register_vec_extension();
        let conn = Connection::open(&config.db_path).unwrap();

        let ai_client = OllamaClient::new(None).expect("failed to create Ollama client");
        assert!(ai_client.check_health().await, "Ollama must be running");

        // The daemon stores events but does not yet embed them; seed for live RAG demo.
        seed_embeddings_if_empty(&conn, &ai_client).await;

        // Ask the broker a question based on what you were doing earlier!
        let query = "dependency changes in Cargo.toml";

        let snapshot = generate_snapshot(&conn, &ai_client, query).await.unwrap();

        println!("--- RECENT ACTIVITY (Top 3) ---");
        for event in snapshot.recent_activity.iter().take(3) {
            println!("[{:?}] ID: {}", event.raw.source, event.raw.id);
        }

        println!("\n--- SEMANTIC MATCHES for '{}' ---", query);
        for event in snapshot.relevant_history {
            println!(
                "[{:?}] Score: {:.2} | Payload: {}",
                event.raw.source,
                event.score,
                serde_json::to_string(&event.raw.payload).unwrap()
            );
        }
    }
}
