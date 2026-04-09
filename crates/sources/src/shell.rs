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
