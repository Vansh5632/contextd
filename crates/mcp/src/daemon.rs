//! The thin client half: talk to the running daemon over its Unix socket.
//!
//! `contextd --mcp` is a short-lived child process that agents spawn. It owns no
//! state and no database — every request here is forwarded to the long-running
//! daemon and the reply is passed straight back.

use anyhow::{Context, Result};
use contextd_core::protocol::ContextRequest;
use std::path::Path;
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    net::UnixStream,
};

/// Send one request to the daemon and read its single-line reply.
pub async fn ask(socket_path: &Path, request: &ContextRequest) -> Result<String> {
    let stream = UnixStream::connect(socket_path)
        .await
        .with_context(|| format!("failed to connect to contextd at {}", socket_path.display()))?;
    write_request_and_read_reply(stream, request).await
}

pub async fn write_request_and_read_reply(
    mut stream: UnixStream,
    request: &ContextRequest,
) -> Result<String> {
    let mut line = serde_json::to_string(request)?;
    line.push('\n');
    stream.write_all(line.as_bytes()).await?;
    stream.flush().await?;
    // Half-close so the daemon's line reader sees EOF and does not wait for more.
    stream.shutdown().await?;

    let mut reply = String::new();
    BufReader::new(stream)
        .read_line(&mut reply)
        .await
        .context("failed reading reply from daemon")?;
    if reply.trim().is_empty() {
        anyhow::bail!("daemon closed the socket without replying");
    }
    Ok(reply.trim_end().to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Runs a fake daemon that asserts on the request and replies once.
    fn fake_daemon(
        server: UnixStream,
        expect: &'static str,
        reply: &'static str,
    ) -> tokio::task::JoinHandle<()> {
        tokio::spawn(async move {
            let (reader, mut writer) = server.into_split();
            let mut request = String::new();
            BufReader::new(reader)
                .read_line(&mut request)
                .await
                .expect("daemon should read a request line");
            assert!(
                request.contains(expect),
                "expected request to contain {expect}, got {request}"
            );
            writer.write_all(reply.as_bytes()).await.unwrap();
            writer.write_all(b"\n").await.unwrap();
            writer.flush().await.unwrap();
        })
    }

    #[tokio::test]
    async fn now_without_text_omits_the_field() {
        let (client, server) = UnixStream::pair().unwrap();
        fake_daemon(server, r#"{"query":"now"}"#, r#"{"recent_activity":[]}"#);

        let reply = write_request_and_read_reply(client, &ContextRequest::Now { text: None })
            .await
            .unwrap();
        assert!(reply.contains("recent_activity"));
    }

    #[tokio::test]
    async fn now_with_text_includes_it() {
        let (client, server) = UnixStream::pair().unwrap();
        fake_daemon(server, r#""text":"login bug""#, "{}");

        let reply = write_request_and_read_reply(
            client,
            &ContextRequest::Now {
                text: Some("login bug".to_string()),
            },
        )
        .await
        .unwrap();
        assert_eq!(reply, "{}");
    }

    #[tokio::test]
    async fn search_sends_its_limit() {
        let (client, server) = UnixStream::pair().unwrap();
        fake_daemon(server, r#""query":"search""#, r#"{"matches":[]}"#);

        let reply = write_request_and_read_reply(
            client,
            &ContextRequest::Search {
                text: "oom".to_string(),
                limit: Some(3),
            },
        )
        .await
        .unwrap();
        assert!(reply.contains("matches"));
    }

    #[tokio::test]
    async fn a_silent_daemon_is_an_error_not_an_empty_briefing() {
        let (client, server) = UnixStream::pair().unwrap();

        // Accept the request, then hang up without answering. An empty read has
        // to surface as an error, or the agent gets a blank briefing and
        // concludes the developer has been doing nothing.
        tokio::spawn(async move {
            let (reader, writer) = server.into_split();
            let mut request = String::new();
            BufReader::new(reader).read_line(&mut request).await.ok();
            drop(writer);
        });

        let err = write_request_and_read_reply(client, &ContextRequest::Now { text: None })
            .await
            .unwrap_err();
        assert!(
            err.to_string().contains("without replying"),
            "unexpected error: {err}"
        );
    }
}
