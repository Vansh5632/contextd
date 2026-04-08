use contextd_core::event::RawEvent;
use std::path::Path;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::net::UnixListener;
use tokio::sync::broadcast;
use tracing::{error, info, warn};

/// Starts a Unix domain socket listener that accepts connections from shell hooks,
/// parses incoming JSON lines into RawEvents, and broadcasts them.
pub async fn start_shell_listener(
    socket_path: impl AsRef<Path>,
    tx: broadcast::Sender<RawEvent>,
) -> std::io::Result<()> {
    let path = socket_path.as_ref();

    // Clean up the old socket file if the daemon crashed previously
      if path.exists() {
        std::fs::remove_file(path)?;

    let listener = UnixListener::bind(path)?;
    info!("Shell listener bound to {:?}", path);

    // Infinite loop accepting new connections
    loop {
        let (mut stream, _addr) = match listener.accept().await {
            Ok(res) => res,
            Err(e) => {
                error!("Failed to accept socket connection: {}", e);
                continue;
            }
        };

        let tx = tx.clone();

        // Spawn a background task for each connection so we can immediately accept the next one
        tokio::spawn(async move {
            let (reader, _) = stream.split();
            let mut lines = BufReader::new(reader).lines();

            loop {
                match lines.next_line().await {
                    Ok(Some(line)) => {
                        match serde_json::from_str::<RawEvent>(&line) {
                            Ok(event) => {
                                // Send it to the rest of the application
                                if let Err(e) = tx.send(event) {
                                    warn!("Failed to broadcast event (no receivers?): {}", e);
                                }
                            }
                            Err(e) => {
                                error!(
                                    "Failed to parse incoming shell event: {}. Payload: {}",
                                    e, line
                                );
                            }
                        }
                    }
                    Ok(None) => {
                        debug!("Shell event stream closed by peer");
                        break;
                    }
                    Err(e) => {
                        warn!("I/O error while reading from shell event stream: {}", e);
                        break;
                    }
                }
            }
        });
    }
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
        let listener = tokio::spawn(async move { start_shell_listener(listener_path, tx).await });

        tokio::time::sleep(std::time::Duration::from_millis(10)).await;

        let mut stream = UnixStream::connect(&socket_path)
            .await
            .expect("client should connect to shell socket");

        let test_event = RawEvent {
            id: "test-id-123".to_string(),
            time_stamp_ms: 1_000,
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
        assert_eq!(received_event.time_stamp_ms, 1_000);
        assert_eq!(received_event.source, EventSource::Shell);
        assert_eq!(received_event.payload, json!({"command": "cargo test"}));

        listener.abort();
        let _ = listener.await;
    }
}
