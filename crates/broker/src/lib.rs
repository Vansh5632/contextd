//! Turns stored events into something an agent can read.
//!
//! The broker is the only part of contextd that decides what an agent sees. It
//! reads from all four tiers, scores everything with [`relevance`], renders it
//! through [`item`], and fits the result to a [`budget`].

pub mod budget;
pub mod item;
pub mod relevance;
pub mod search;
pub mod snapshot;

/// Embed a query string, or return `None` for any reason at all.
///
/// Every caller in this crate treats a missing embedding as "do the non-semantic
/// thing" rather than as an error, so the failure modes (no client, empty text,
/// Ollama unreachable) all collapse to the same `None` here. This is the single
/// place where "the model is optional" is actually enforced.
pub async fn embed_query(
    ai_client: Option<&ai::ollama::OllamaClient>,
    text: &str,
) -> Option<Vec<f32>> {
    let client = ai_client?;
    if text.trim().is_empty() {
        return None;
    }

    match client.get_embedding(text, None).await {
        Ok(embedding) => Some(embedding),
        Err(err) => {
            tracing::warn!(
                error = ?err,
                "failed to generate query embedding; continuing without semantic matches"
            );
            None
        }
    }
}
