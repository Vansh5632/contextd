use reqwest::Client;
use std::time::Duration;
use tracing::warn;

const DEFAULT_OLLAMA_BASE_URL: &str = "http://localhost:11434";

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
    use super::normalize_base_url;

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
}
