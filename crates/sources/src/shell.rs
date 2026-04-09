use anyhow::{Context, Result};
use contextd_core::event::RawEvent;
use std::path::PathBuf;
use tokio::{
    io::{AsyncBufReadExt, BufReader},
    net::{UnixListener, UnixStream},
    sync::broadcast,
};
use tracing::{info, warn};

pub async fn start_shell_listener(
    socket_path: PathBuf,
    tx: broadcast::Sender<RawEvent>,
) -> Result<()> {
    if socket_path.exists() {
        std::fs::remove_file(&socket_path).with_context(|| {
            format!(
                "failed to remove existing socket at {}",
                socket_path.display()
            )
        })?;
    }

    let listener = UnixListener::bind(&socket_path)
        .with_context(|| format!("failed to bind unix socket at {}", socket_path.display()))?;

    info!("Shell listener bound at {}", socket_path.display());

    loop {
        let (stream, _) = listener
            .accept()
            .await
            .context("failed to accept socket client")?;
        let tx = tx.clone();

        tokio::spawn(async move {
            if let Err(err) = handle_client(stream, tx).await {
                warn!("shell client connection failed: {err}");
            }
        });
    }
}

async fn handle_client(stream: UnixStream, tx: broadcast::Sender<RawEvent>) -> Result<()> {
    let reader = BufReader::new(stream);
    let mut lines = reader.lines();

    while let Some(line) = lines
        .next_line()
        .await
        .context("failed reading client payload")?
    {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }

        let event: RawEvent =
            serde_json::from_str(trimmed).context("failed to deserialize raw event")?;
        tx.send(event)
            .map_err(|err| anyhow::anyhow!("failed to publish raw event: {err}"))?;
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use contextd_core::event::{EventSource, RawEvent};
    use serde_json::json;
    use tempfile::tempdir;
    use tokio::io::AsyncWriteExt;
    use tokio::net::UnixStream;

    #[tokio::test]
    async fn shell_listener_receives_and_broadcasts_events() {
        let dir = tempdir().expect("tempdir should be created");
        let socket_path = dir.path().join("test.sock");
        let (tx, mut rx) = broadcast::channel(10);

        let listener_path = socket_path.clone();
        let listener_tx = tx.clone();
        let listener =
            tokio::spawn(async move { start_shell_listener(listener_path, listener_tx).await });

        // Give the listener a moment to bind before connecting
        tokio::time::sleep(std::time::Duration::from_millis(10)).await;

        let mut stream = UnixStream::connect(&socket_path)
            .await
            .expect("client should connect to shell socket");

        let test_event = RawEvent {
            id: "test-id-123".to_string(),
            timestamp_ms: 1_000,
            source: EventSource::Shell,
            payload: json!({"command": "cargo test"}),
        };

        let mut payload =
            serde_json::to_string(&test_event).expect("shell event should serialize to JSON");
        payload.push('\n');

        stream
            .write_all(payload.as_bytes())
            .await
            .expect("client should write event payload");
        stream.flush().await.expect("client should flush payload");

        let received_event = tokio::time::timeout(std::time::Duration::from_secs(1), rx.recv())
            .await
            .expect("broadcast receive should complete before timeout")
            .expect("broadcast should deliver the event");

        assert_eq!(received_event.id, "test-id-123");
        assert_eq!(received_event.timestamp_ms, 1_000);
        assert_eq!(received_event.source, EventSource::Shell);
        assert_eq!(received_event.payload, json!({"command": "cargo test"}));

        listener.abort();
        let _ = listener.await;
    }
}
