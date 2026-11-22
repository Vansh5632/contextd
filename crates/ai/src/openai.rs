//! Chat completions for claim verification. This module has no database handle.

use std::time::Duration;

use serde::Deserialize;
use serde_json::Value;

#[derive(Debug, Clone)]
pub struct ChatUsage {
    pub content: String,
    pub prompt_tokens: u32,
    pub completion_tokens: u32,
}

#[derive(Deserialize)]
struct ChatResponse {
    choices: Vec<Choice>,
    #[serde(default)]
    usage: Usage,
}

#[derive(Deserialize)]
struct Choice {
    message: Message,
}

#[derive(Deserialize)]
struct Message {
    content: Option<String>,
}

#[derive(Deserialize, Default)]
struct Usage {
    #[serde(default)]
    prompt_tokens: u32,
    #[serde(default)]
    completion_tokens: u32,
}

/// One structured chat completion. The caller must not hold a database lock.
pub async fn chat(api_key: &str, payload: Value) -> Result<ChatUsage, String> {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(45))
        .build()
        .map_err(|err| err.to_string())?;
    let response = client
        .post("https://api.openai.com/v1/chat/completions")
        .bearer_auth(api_key)
        .json(&payload)
        .send()
        .await
        .map_err(|err| err.to_string())?;
    if !response.status().is_success() {
        let status = response.status();
        let body = response.text().await.unwrap_or_default();
        return Err(format!("openai status {status}: {body}"));
    }
    let parsed: ChatResponse = response.json().await.map_err(|err| err.to_string())?;
    let content = parsed
        .choices
        .into_iter()
        .next()
        .and_then(|choice| choice.message.content)
        .unwrap_or_else(|| "{}".to_string());
    Ok(ChatUsage {
        content,
        prompt_tokens: parsed.usage.prompt_tokens,
        completion_tokens: parsed.usage.completion_tokens,
    })
}
