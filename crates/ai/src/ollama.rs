use reqwest::Client;
use serde::{Deserialize, Serialize};
use std::time::Duration;
use tracing::warn;

const DEFAULT_OLLAMA_BASE_URL: &str = "http://localhost:11434";

/// The embedding model we assume unless told otherwise.
///
/// `embeddinggemma` scores better on code, but this is the model most people
/// already have pulled, and it is also 768-d so the `vec_events` schema does
/// not change when you switch. Override with `CONTEXTD_EMBEDDING_MODEL`.
pub const DEFAULT_EMBEDDING_MODEL: &str = "nomic-embed-text";

/// The width of the `vec_events` embedding column. A model that disagrees is a
/// configuration error we want to hear about loudly, not a row we silently drop.
pub const EXPECTED_EMBEDDING_DIMENSIONS: usize = 768;

#[derive(Serialize)]
struct EmbeddingRequest<'a> {
    model: &'a str,
    input: &'a str,
}

/// `/api/embed` returns a batch, even for a single input.
#[derive(Deserialize)]
struct EmbeddingResponse {
    #[serde(default)]
    embeddings: Vec<Vec<f32>>,
}

#[derive(Debug, Clone)]
pub struct OllamaClient {
    client: Client,
    base_url: String,
    embedding_model: String,
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

        let embedding_model = std::env::var("CONTEXTD_EMBEDDING_MODEL")
            .ok()
            .filter(|model| !model.trim().is_empty())
            .unwrap_or_else(|| DEFAULT_EMBEDDING_MODEL.to_string());

        Ok(Self {
            client,
            base_url,
            embedding_model,
        })
    }

    /// Build a client from the user's configuration file.
    ///
    /// The environment still wins over the file, because `OLLAMA_HOST` is how
    /// people point at a remote box for one run without editing anything.
    pub fn from_config(config: &contextd_core::config::AppConfig) -> Result<Self, reqwest::Error> {
        let mut client = Self::new(Some(config.ollama_url.clone()))?;

        let overridden = std::env::var("CONTEXTD_EMBEDDING_MODEL").is_ok();
        if !overridden && !config.embedding_model.trim().is_empty() {
            client.embedding_model = config.embedding_model.clone();
        }

        Ok(client)
    }

    /// Which model this client embeds with.
    pub fn embedding_model(&self) -> &str {
        &self.embedding_model
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
    /// Uses the configured model when `model` is `None`. Whatever the model, the
    /// result must be [`EXPECTED_EMBEDDING_DIMENSIONS`] wide, because that is the
    /// width of the column it is going into. A mismatch means someone pointed
    /// contextd at a model with a different geometry, and silently discarding
    /// those rows would look like "semantic search just doesn't work".
    pub async fn get_embedding(&self, text: &str, model: Option<&str>) -> anyhow::Result<Vec<f32>> {
        let model = model.unwrap_or(&self.embedding_model);
        let url = format!("{}/api/embed", self.base_url);
        let req_body = EmbeddingRequest { model, input: text };

        let res = self.client.post(&url).json(&req_body).send().await?;

        if !res.status().is_success() {
            let status = res.status();
            let err_text = res.text().await?;
            return Err(anyhow::anyhow!("Ollama API error {status}: {err_text}"));
        }

        let parsed: EmbeddingResponse = res.json().await?;
        let embedding = parsed
            .embeddings
            .into_iter()
            .next()
            .ok_or_else(|| anyhow::anyhow!("Ollama returned no embedding for model {model}"))?;

        if embedding.len() != EXPECTED_EMBEDDING_DIMENSIONS {
            return Err(anyhow::anyhow!(
                "embedding model {model} returned {} dimensions, but contextd stores {}. \
                 Set CONTEXTD_EMBEDDING_MODEL to a {}-dimension model.",
                embedding.len(),
                EXPECTED_EMBEDDING_DIMENSIONS,
                EXPECTED_EMBEDDING_DIMENSIONS
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

    #[test]
    fn embedding_model_defaults_but_can_be_overridden() {
        // Not using an env override here on purpose: setting process-wide env in
        // a parallel test suite is a race. Assert the default and trust the
        // single `unwrap_or_else` above for the override path.
        let client = OllamaClient::new(Some("http://localhost:11434".to_string())).unwrap();
        let expected = std::env::var("CONTEXTD_EMBEDDING_MODEL")
            .unwrap_or_else(|_| DEFAULT_EMBEDDING_MODEL.to_string());
        assert_eq!(client.embedding_model(), expected);
    }

    #[tokio::test]
    #[ignore = "Requires Ollama running locally with an embedding model pulled"]
    async fn test_generate_embedding() {
        let client = OllamaClient::new(None).expect("Ollama client should initialize");

        // Ensure Ollama is up before testing
        assert!(
            client.check_health().await,
            "Ollama must be running for this test"
        );

        let text_to_embed = "I am debugging a TypeError in my TypeScript React application.";
        let embedding = client.get_embedding(text_to_embed, None).await.unwrap();

        assert_eq!(embedding.len(), EXPECTED_EMBEDDING_DIMENSIONS);

        // Print the first 5 numbers just to see what an embedding actually looks like!
        println!("First 5 dimensions: {:?}", &embedding[0..5]);
    }
}
