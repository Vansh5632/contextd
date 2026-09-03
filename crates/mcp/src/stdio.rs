use crate::server::ContextdServer;
use anyhow::Result;
use rmcp::{ServiceExt, transport::stdio};
use std::path::PathBuf;

/// Speak MCP on stdin/stdout until the client hangs up.
///
/// Note that stdout belongs to the protocol. Anything that wants to talk to a
/// human has to go to stderr, or the JSON-RPC stream is corrupted.
pub async fn run(socket_path: PathBuf) -> Result<()> {
    let service = ContextdServer::new(socket_path).serve(stdio()).await?;
    service.waiting().await?;
    Ok(())
}
