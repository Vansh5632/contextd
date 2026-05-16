use reqwest::Client;
use serde::{Deserialize, Serialize};
use std::time::Duration;
use tracing::warn;

const DEFAULT_OLLAMA_BASE_URL: &str = "http://localhost:11434";
const DEFAULT_EMBEDDING_MODEL: &str = "nomic-embed-text";
const NOMIC_EMBED_TEXT_DIMENSIONS: usize = 768;

#[derive(Serialize)]
struct EmbeddingRequest<'a> {
    model: &'a str,
    prompt: &'a str,
}

#[derive(Deserialize)]
struct EmbeddingResponse {
    embedding: Vec<f32>,
}

#[derive(Debug, Clone)]
pub struct OllamaClient {
    client: Client,
    base_url: String,
}

impl OllamaClient {
    /// Creates a new Ollama client with sensible timeouts for local inference
    pub fn new(base_url: Option<String>) -> Result<Self, reqwest::Error> {
        let client = Client::builder()
            .timeout(Duration::from_secs(120)) // Local LLMs can take a bit to load the model into VRAM
            .build()?;
        let base_url = base_url
            .or_else(|| std::env::var("OLLAMA_HOST").ok())
            .filter(|url| !url.trim().is_empty())
            .map(normalize_base_url)
            .unwrap_or_else(|| DEFAULT_OLLAMA_BASE_URL.to_string());

        Ok(Self { client, base_url })
    }

    /// Pings the Ollama server to see if it is running
    pub async fn check_health(&self) -> bool {
        let url = format!("{}/api/tags", self.base_url);
        match self
            .client
            .get(&url)
            .timeout(Duration::from_secs(2))
            .send()
            .await
        {
            Ok(response) => {
                if response.status().is_success() {
                    true
                } else {
                    warn!("Ollama responded with an error: {}", response.status());
                    false
                }
            }
            Err(e) => {
                warn!("Ollama is offline or unreachable: {}", e);
                false
            }
        }
    }

    /// Generates a vector embedding for the given text.
    ///
    /// Uses [`DEFAULT_EMBEDDING_MODEL`] (`nomic-embed-text`) when `model` is `None`.
    /// For the default model, returns exactly 768 dimensions or an error on mismatch.
    /// Other models return the server vector without a fixed-size guarantee.
    pub async fn get_embedding(&self, text: &str, model: Option<&str>) -> anyhow::Result<Vec<f32>> {
        let model = model.unwrap_or(DEFAULT_EMBEDDING_MODEL);
        let url = format!("{}/api/embeddings", self.base_url);
        let req_body = EmbeddingRequest {
            model,
            prompt: text,
        };

        let res = self.client.post(&url).json(&req_body).send().await?;

        if !res.status().is_success() {
            let status = res.status();
            let err_text = res.text().await?;
            return Err(anyhow::anyhow!("Ollama API error {}: {}", status, err_text));
        }

        let parsed: EmbeddingResponse = res.json().await?;
        let embedding = parsed.embedding;

        if model == DEFAULT_EMBEDDING_MODEL && embedding.len() != NOMIC_EMBED_TEXT_DIMENSIONS {
            return Err(anyhow::anyhow!(
                "Ollama embedding dimension mismatch: expected {}, got {}",
                NOMIC_EMBED_TEXT_DIMENSIONS,
                embedding.len()
            ));
        }

        Ok(embedding)
    }
}

fn normalize_base_url(base_url: String) -> String {
    let trimmed = base_url.trim().trim_end_matches('/');
    if trimmed.starts_with("http://") || trimmed.starts_with("https://") {
        trimmed.to_string()
    } else {
        format!("http://{trimmed}")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_base_url_adds_http_scheme() {
        assert_eq!(
            normalize_base_url("127.0.0.1:11434".to_string()),
            "http://127.0.0.1:11434"
        );
    }

    #[test]
    fn normalize_base_url_preserves_explicit_scheme_and_removes_trailing_slash() {
        assert_eq!(
            normalize_base_url("http://localhost:11434/".to_string()),
            "http://localhost:11434"
        );
    }

    #[tokio::test]
    #[ignore = "Requires Ollama running locally with nomic-embed-text"]
    async fn test_generate_embedding() {
        let client = OllamaClient::new(None).expect("Ollama client should initialize");

        // Ensure Ollama is up before testing
        assert!(
            client.check_health().await,
            "Ollama must be running for this test"
        );

        let text_to_embed = "I am debugging a TypeError in my TypeScript React application.";
        let embedding = client.get_embedding(text_to_embed, None).await.unwrap();

        assert_eq!(embedding.len(), NOMIC_EMBED_TEXT_DIMENSIONS);

        // Print the first 5 numbers just to see what an embedding actually looks like!
        println!("First 5 dimensions: {:?}", &embedding[0..5]);
    }
}
