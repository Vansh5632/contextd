use anyhow::{Context, Result};
use contextd_core::event::RawEvent;
use contextd_core::protocol::ContextRequest;
use serde_json::Value;
use std::path::PathBuf;
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    net::{UnixListener, UnixStream},
    sync::{broadcast, mpsc, oneshot},
};
use tracing::{info, warn};

/// A read request from an agent. The daemon answers on `reply` with a JSON body.
pub struct ContextQuery {
    pub request: ContextRequest,
    pub reply: oneshot::Sender<String>,
}

#[derive(Debug)]
pub enum InboundLine {
    Empty,
    Event(RawEvent),
    Query(ContextRequest),
}

/// Starts a Unix domain socket listener.
/// Event JSON lines are ingested. `{"query":"now"}` lines get a JSON reply.
pub async fn start_shell_listener(
    socket_path: PathBuf,
    tx: broadcast::Sender<RawEvent>,
    query_tx: mpsc::Sender<ContextQuery>,
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
        let query_tx = query_tx.clone();

        tokio::spawn(async move {
            if let Err(err) = handle_client(stream, tx, query_tx).await {
                warn!("shell client connection failed: {err}");
            }
        });
    }
}

async fn handle_client(
    stream: UnixStream,
    tx: broadcast::Sender<RawEvent>,
    query_tx: mpsc::Sender<ContextQuery>,
) -> Result<()> {
    let (reader, mut writer) = stream.into_split();
    let mut lines = BufReader::new(reader).lines();

    while let Some(line) = lines
        .next_line()
        .await
        .context("failed reading client payload")?
    {
        match parse_inbound_line(&line)? {
            InboundLine::Empty => {}
            InboundLine::Event(event) => {
                tx.send(event)
                    .map_err(|err| anyhow::anyhow!("failed to publish raw event: {err}"))?;
            }
            InboundLine::Query(request) => {
                let (reply_tx, reply_rx) = oneshot::channel();
                query_tx
                    .send(ContextQuery {
                        request,
                        reply: reply_tx,
                    })
                    .await
                    .map_err(|err| anyhow::anyhow!("failed to dispatch context query: {err}"))?;
                let body = reply_rx
                    .await
                    .unwrap_or_else(|_| r#"{"error":"query handler unavailable"}"#.to_string());
                writer.write_all(body.as_bytes()).await?;
                if !body.ends_with('\n') {
                    writer.write_all(b"\n").await?;
                }
                writer.flush().await?;
            }
        }
    }

    Ok(())
}

/// An event as a *client* writes it.
///
/// `id` and `timestamp_ms` are optional here even though [`RawEvent`] requires
/// them. The main producer is a shell hook — a few lines in someone's
/// `.bashrc` — and requiring it to mint a ULID and a millisecond timestamp in
/// POSIX shell would be a real barrier for two values the daemon can supply
/// perfectly well itself.
#[derive(serde::Deserialize)]
struct IncomingEvent {
    #[serde(default)]
    id: Option<String>,
    #[serde(default, alias = "time_stamp_ms")]
    timestamp_ms: Option<u64>,
    source: contextd_core::event::EventSource,
    #[serde(default)]
    payload: Value,
}

impl From<IncomingEvent> for RawEvent {
    fn from(incoming: IncomingEvent) -> Self {
        RawEvent {
            id: incoming.id.unwrap_or_else(|| ulid::Ulid::new().to_string()),
            timestamp_ms: incoming.timestamp_ms.unwrap_or_else(now_ms),
            source: incoming.source,
            payload: incoming.payload,
        }
    }
}

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|elapsed| elapsed.as_millis() as u64)
        .unwrap_or_default()
}

pub fn parse_inbound_line(line: &str) -> Result<InboundLine> {
    let trimmed = line.trim();
    if trimmed.is_empty() {
        return Ok(InboundLine::Empty);
    }

    let value: Value = serde_json::from_str(trimmed).context("failed to parse socket JSON")?;

    // A `query` key means the caller wants an answer; anything else is ingest.
    // Deciding on the key rather than on parse success keeps a malformed query
    // from being silently misread as an event.
    if value.get("query").is_some() {
        let request: ContextRequest =
            serde_json::from_value(value).context("failed to parse context request")?;
        return Ok(InboundLine::Query(request));
    }

    let incoming: IncomingEvent =
        serde_json::from_value(value).context("failed to deserialize raw event")?;
    Ok(InboundLine::Event(incoming.into()))
}

#[cfg(test)]
fn publish_event_line(line: &str, tx: &broadcast::Sender<RawEvent>) -> Result<()> {
    match parse_inbound_line(line)? {
        InboundLine::Empty | InboundLine::Query(_) => Ok(()),
        InboundLine::Event(event) => {
            tx.send(event)
                .map_err(|err| anyhow::anyhow!("failed to publish raw event: {err}"))?;
            Ok(())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use contextd_core::event::{EventSource, RawEvent};
    use serde_json::json;
    use tokio::io::AsyncBufReadExt;

    fn sample_event() -> RawEvent {
        RawEvent {
            id: "test-id-123".to_string(),
            timestamp_ms: 1_000,
            source: EventSource::Shell,
            payload: json!({"command": "cargo test"}),
        }
    }

    #[tokio::test]
    async fn publish_event_line_broadcasts_events() {
        let (tx, mut rx) = broadcast::channel(10);
        let payload =
            serde_json::to_string(&sample_event()).expect("shell event should serialize to JSON");
        publish_event_line(&payload, &tx).expect("publishing a valid line should succeed");

        let received_event = tokio::time::timeout(std::time::Duration::from_secs(1), rx.recv())
            .await
            .expect("broadcast receive should complete before timeout")
            .expect("broadcast should deliver the event");

        assert_eq!(received_event.id, "test-id-123");
        assert_eq!(received_event.timestamp_ms, 1_000);
        assert_eq!(received_event.source, EventSource::Shell);
        assert_eq!(received_event.payload, json!({"command": "cargo test"}));
    }

    #[test]
    fn parse_inbound_line_reads_context_now_query() {
        match parse_inbound_line(r#"{"query":"now"}"#).unwrap() {
            InboundLine::Query(ContextRequest::Now { text }) => assert!(text.is_none()),
            other => panic!("expected query, got {other:?}"),
        }
    }

    #[test]
    fn parse_inbound_line_reads_optional_query_text() {
        match parse_inbound_line(r#"{"query":"now","text":"login bug"}"#).unwrap() {
            InboundLine::Query(ContextRequest::Now { text }) => {
                assert_eq!(text.as_deref(), Some("login bug"))
            }
            other => panic!("expected query, got {other:?}"),
        }
    }

    #[test]
    fn parse_inbound_line_reads_the_other_verbs() {
        let cases = [
            (
                r#"{"query":"search","text":"oom","limit":3}"#,
                ContextRequest::Search {
                    text: "oom".to_string(),
                    limit: Some(3),
                },
            ),
            (
                r#"{"query":"recall","text":"oom"}"#,
                ContextRequest::Recall {
                    text: "oom".to_string(),
                    limit: None,
                },
            ),
            (
                r#"{"query":"intent","text":"fix login"}"#,
                ContextRequest::Intent {
                    text: "fix login".to_string(),
                },
            ),
        ];

        for (line, expected) in cases {
            match parse_inbound_line(line).unwrap() {
                InboundLine::Query(request) => assert_eq!(request, expected),
                other => panic!("expected query for {line}, got {other:?}"),
            }
        }
    }

    #[test]
    fn a_malformed_query_is_an_error_not_a_misread_event() {
        // Before the `query` key gated this, a bad query fell through to the
        // event parser and produced a confusing "not a RawEvent" error.
        let err = parse_inbound_line(r#"{"query":"search"}"#).unwrap_err();
        assert!(
            err.to_string().contains("context request"),
            "unhelpful error: {err}"
        );
    }

    #[test]
    fn parse_inbound_line_still_reads_raw_events() {
        let payload = serde_json::to_string(&sample_event()).unwrap();
        match parse_inbound_line(&payload).unwrap() {
            InboundLine::Event(event) => assert_eq!(event.id, "test-id-123"),
            other => panic!("expected event, got {other:?}"),
        }
    }

    #[test]
    fn a_client_may_omit_the_id_and_timestamp() {
        // This is the shape a shell hook can produce without help. Requiring a
        // ULID and a millisecond clock from `.bashrc` would be a real barrier.
        match parse_inbound_line(r#"{"source":"shell","payload":{"command":"ls"}}"#).unwrap() {
            InboundLine::Event(event) => {
                assert!(!event.id.is_empty(), "the daemon should mint an id");
                assert!(event.timestamp_ms > 0, "the daemon should stamp the time");
                assert_eq!(event.source, EventSource::Shell);
                assert_eq!(event.payload, json!({"command": "ls"}));
            }
            other => panic!("expected event, got {other:?}"),
        }
    }

    #[test]
    fn a_supplied_id_and_timestamp_are_respected() {
        // Git hooks know when the commit happened; the daemon must not
        // overwrite that with the time the message arrived.
        let line = r#"{"id":"mine","timestamp_ms":42,"source":"git","payload":{}}"#;
        match parse_inbound_line(line).unwrap() {
            InboundLine::Event(event) => {
                assert_eq!(event.id, "mine");
                assert_eq!(event.timestamp_ms, 42);
            }
            other => panic!("expected event, got {other:?}"),
        }
    }

    #[test]
    fn an_event_without_a_source_is_still_an_error() {
        // Everything else can be inferred; what kind of event it is cannot.
        assert!(parse_inbound_line(r#"{"payload":{"command":"ls"}}"#).is_err());
    }

    #[tokio::test]
    async fn query_line_writes_json_reply() {
        let (client, server) = UnixStream::pair().unwrap();
        let (event_tx, _) = broadcast::channel(8);
        let (query_tx, mut query_rx) = mpsc::channel::<ContextQuery>(8);

        tokio::spawn(async move {
            let req = query_rx.recv().await.expect("query should arrive");
            assert_eq!(req.request, ContextRequest::Now { text: None });
            let _ = req.reply.send(r#"{"recent_activity":[]}"#.to_string());
        });
        tokio::spawn(async move {
            handle_client(server, event_tx, query_tx)
                .await
                .expect("server should handle query");
        });

        let (reader, mut writer) = client.into_split();
        writer.write_all(b"{\"query\":\"now\"}\n").await.unwrap();
        writer.flush().await.unwrap();

        let mut reply = String::new();
        BufReader::new(reader)
            .read_line(&mut reply)
            .await
            .expect("client should receive a reply line");
        assert!(reply.contains("recent_activity"));
    }
}
