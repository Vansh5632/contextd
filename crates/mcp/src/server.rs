//! The MCP surface contextd presents to Cursor, Claude Code, and Continue.
//!
//! Everything here is a thin translation layer. The tools do no thinking: they
//! turn an MCP call into a [`ContextRequest`], hand it to the daemon over the
//! Unix socket, and wrap whatever comes back as text content.
//!
//! Failure policy follows the MCP distinction. A daemon that is not running is a
//! *tool* error (`isError: true`) because the caller can act on it — start the
//! daemon. Only a genuinely unroutable request becomes a protocol error.

use std::path::PathBuf;

use contextd_core::protocol::ContextRequest;
use rmcp::{
    ErrorData, ServerHandler,
    handler::server::wrapper::Parameters,
    model::{
        CacheScope, CallToolResult, ContentBlock, Implementation, InitializeResult,
        ListResourcesResult, PaginatedRequestParams, ReadResourceRequestParams,
        ReadResourceResponse, ReadResourceResult, Resource, ResourceContents, ServerCapabilities,
    },
    service::RequestContext,
    tool, tool_handler, tool_router,
};
use schemars::JsonSchema;
use serde::Deserialize;

/// The one resource we expose: the current briefing, attachable without a tool call.
pub const SNAPSHOT_URI: &str = "context://snapshot/current";

const INSTRUCTIONS: &str = "\
contextd watches this machine locally and keeps one picture of what the developer is doing.

Call `context_now` before asking the user what they are working on or which file they are in -- \
the answer is usually already there. Use `search_context` for a specific past moment, \
`recall_similar` when the current problem feels familiar, and `set_intent` when the user states \
a goal so later briefings stay on track.";

#[derive(Debug, Deserialize, JsonSchema)]
pub struct NowRequest {
    /// Optional focus for the related-history half, e.g. "login bug".
    /// Omit it to use whatever just happened.
    #[serde(default)]
    pub text: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct SearchRequest {
    /// What to look for across recorded activity.
    pub text: String,
    /// How many results to return. Defaults to 10, capped at 50.
    #[serde(default)]
    pub limit: Option<usize>,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct IntentRequest {
    /// What the developer is trying to do, in their own words.
    pub text: String,
}

/// Talks MCP on stdin/stdout, forwards everything to the daemon's socket.
#[derive(Clone)]
pub struct ContextdServer {
    socket_path: PathBuf,
}

#[tool_router]
impl ContextdServer {
    pub fn new(socket_path: PathBuf) -> Self {
        Self { socket_path }
    }

    #[tool(
        description = "What the developer is doing right now: recent activity, their declared \
                       intent, and related history from earlier work. Call this before asking \
                       the user for context."
    )]
    pub async fn context_now(
        &self,
        Parameters(NowRequest { text }): Parameters<NowRequest>,
    ) -> Result<CallToolResult, ErrorData> {
        self.forward(ContextRequest::Now { text }).await
    }

    #[tool(
        description = "Search everything contextd has recorded on this machine -- commands, file \
                       changes, commits, builds. Combines meaning-based and literal matching."
    )]
    pub async fn search_context(
        &self,
        Parameters(SearchRequest { text, limit }): Parameters<SearchRequest>,
    ) -> Result<CallToolResult, ErrorData> {
        self.forward(ContextRequest::Search { text, limit }).await
    }

    #[tool(
        description = "Find earlier moments that resemble the current one. Use this when a problem \
                       feels familiar and you want to know how it went last time."
    )]
    pub async fn recall_similar(
        &self,
        Parameters(SearchRequest { text, limit }): Parameters<SearchRequest>,
    ) -> Result<CallToolResult, ErrorData> {
        self.forward(ContextRequest::Recall { text, limit }).await
    }

    #[tool(
        description = "Record what the developer is trying to accomplish so future briefings stay \
                       on track. Call this when they state a goal, e.g. \"I am fixing the login bug\"."
    )]
    pub async fn set_intent(
        &self,
        Parameters(IntentRequest { text }): Parameters<IntentRequest>,
    ) -> Result<CallToolResult, ErrorData> {
        self.forward(ContextRequest::Intent { text }).await
    }

    /// One request, one reply, one piece of text content.
    async fn forward(&self, request: ContextRequest) -> Result<CallToolResult, ErrorData> {
        match crate::daemon::ask(&self.socket_path, &request).await {
            Ok(body) => Ok(CallToolResult::success(vec![ContentBlock::text(body)])),
            Err(error) => Ok(CallToolResult::error(vec![ContentBlock::text(format!(
                "{error}. Is the contextd daemon running?"
            ))])),
        }
    }
}

#[tool_handler]
impl ServerHandler for ContextdServer {
    fn get_info(&self) -> InitializeResult {
        InitializeResult::new(
            ServerCapabilities::builder()
                .enable_tools()
                .enable_resources()
                .build(),
        )
        .with_server_info(Implementation::new("contextd", env!("CARGO_PKG_VERSION")))
        .with_instructions(INSTRUCTIONS)
    }

    async fn list_resources(
        &self,
        _request: Option<PaginatedRequestParams>,
        _context: RequestContext<rmcp::RoleServer>,
    ) -> Result<ListResourcesResult, ErrorData> {
        let snapshot = Resource::new(SNAPSHOT_URI, "Current context snapshot")
            .with_description(
                "Live briefing of what the developer is doing on this machine right now.",
            )
            .with_mime_type("application/json");

        // The list of resources is stable; only the contents move. Private,
        // because this describes one person's machine.
        Ok(ListResourcesResult::with_all_items(vec![snapshot])
            .with_ttl_ms(3_600_000)
            .with_cache_scope(CacheScope::Private))
    }

    async fn read_resource(
        &self,
        request: ReadResourceRequestParams,
        _context: RequestContext<rmcp::RoleServer>,
    ) -> Result<ReadResourceResponse, ErrorData> {
        if request.uri != SNAPSHOT_URI {
            return Err(ErrorData::resource_not_found(
                format!("unknown resource: {}", request.uri),
                None,
            ));
        }

        let body = crate::daemon::ask(&self.socket_path, &ContextRequest::Now { text: None })
            .await
            .map_err(|error| {
                ErrorData::internal_error(format!("{error}. Is the contextd daemon running?"), None)
            })?;

        // ttl 0: a briefing is stale the moment it is read. Caching one would
        // defeat the entire point of the daemon.
        Ok(ReadResourceResult::new(vec![
            ResourceContents::text(body, SNAPSHOT_URI).with_mime_type("application/json"),
        ])
        .with_ttl_ms(0)
        .with_cache_scope(CacheScope::Private)
        .into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn server() -> ContextdServer {
        ContextdServer::new(PathBuf::from("/tmp/contextd-does-not-exist.sock"))
    }

    #[test]
    fn advertises_tools_and_resources() {
        let info = server().get_info();
        assert!(info.capabilities.tools.is_some());
        assert!(info.capabilities.resources.is_some());
        assert_eq!(info.server_info.name, "contextd");
        assert!(info.instructions.is_some());
    }

    #[test]
    fn exposes_all_four_tools() {
        let names: Vec<String> = ContextdServer::tool_router()
            .list_all()
            .into_iter()
            .map(|tool| tool.name.to_string())
            .collect();

        for expected in [
            "context_now",
            "search_context",
            "recall_similar",
            "set_intent",
        ] {
            assert!(names.contains(&expected.to_string()), "missing {expected}");
        }
    }

    #[test]
    fn every_tool_schema_is_an_object() {
        // MCP requires the root of inputSchema to be `type: "object"`; clients
        // reject anything else, and a bad schema is invisible until runtime.
        for tool in ContextdServer::tool_router().list_all() {
            assert_eq!(
                tool.input_schema.get("type").and_then(|t| t.as_str()),
                Some("object"),
                "tool {} has a non-object input schema",
                tool.name
            );
        }
    }

    #[tokio::test]
    async fn an_unreachable_daemon_is_a_tool_error_not_a_protocol_error() {
        let result = server()
            .forward(ContextRequest::Now { text: None })
            .await
            .expect("an unreachable daemon must not fail the protocol");

        assert_eq!(result.is_error, Some(true));
        let text = match &result.content[0] {
            rmcp::model::ContentBlock::Text(text) => text.text.clone(),
            other => panic!("expected text content, got {other:?}"),
        };
        assert!(
            text.contains("daemon running") && text.contains("contextd-does-not-exist.sock"),
            "message should name the problem and the path: {text}"
        );
    }
}
