//! The request language spoken over the Unix socket.
//!
//! Both halves of the product depend on this module: the daemon parses these
//! requests in `sources::shell`, and the `--mcp` child builds them in
//! `mcp::daemon`. Keeping the type here is what stops the two from drifting.
//!
//! The wire format is one JSON object per line, discriminated by `query`:
//!
//! ```text
//! {"query":"now"}
//! {"query":"now","text":"login bug"}
//! {"query":"search","text":"connection refused","limit":10}
//! {"query":"recall","text":"flaky test","limit":5}
//! {"query":"intent","text":"fixing the login bug"}
//! ```
//!
//! Anything without a `query` key is treated as an ingested [`RawEvent`].

use serde::{Deserialize, Serialize};

/// How many results a search-style request returns when the caller does not say.
pub const DEFAULT_SEARCH_LIMIT: usize = 10;

/// The most results we will return for one request, whatever the caller asks for.
/// An agent pasting an unbounded result set into its context window helps nobody.
pub const MAX_SEARCH_LIMIT: usize = 50;

/// A request from an agent (or a human with `nc`) to the running daemon.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "query", rename_all = "snake_case")]
pub enum ContextRequest {
    /// "What am I doing right now?" — the briefing.
    Now {
        /// Optional focus for the relevance half. When absent, the daemon uses
        /// whatever just happened.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        text: Option<String>,
    },
    /// Free-text search across stored history.
    Search {
        text: String,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        limit: Option<usize>,
    },
    /// "When did I last deal with this?" — semantic neighbours only, no recency.
    Recall {
        text: String,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        limit: Option<usize>,
    },
    /// Declare what the user is trying to do, so it can sit beside observations.
    Intent { text: String },
}

impl ContextRequest {
    /// Clamp a caller-supplied limit into something we are willing to serve.
    pub fn resolve_limit(limit: Option<usize>) -> usize {
        limit
            .unwrap_or(DEFAULT_SEARCH_LIMIT)
            .clamp(1, MAX_SEARCH_LIMIT)
    }

    /// The free-text this request carries, if any. Empty strings become `None`
    /// so callers do not have to keep re-checking for whitespace.
    pub fn text(&self) -> Option<&str> {
        let text = match self {
            ContextRequest::Now { text } => text.as_deref()?,
            ContextRequest::Search { text, .. }
            | ContextRequest::Recall { text, .. }
            | ContextRequest::Intent { text } => text.as_str(),
        };
        let trimmed = text.trim();
        (!trimmed.is_empty()).then_some(trimmed)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn now_parses_with_and_without_text() {
        let bare: ContextRequest = serde_json::from_str(r#"{"query":"now"}"#).unwrap();
        assert_eq!(bare, ContextRequest::Now { text: None });

        let focused: ContextRequest =
            serde_json::from_str(r#"{"query":"now","text":"login bug"}"#).unwrap();
        assert_eq!(focused.text(), Some("login bug"));
    }

    #[test]
    fn search_and_recall_carry_limits() {
        let search: ContextRequest =
            serde_json::from_str(r#"{"query":"search","text":"oom","limit":3}"#).unwrap();
        assert_eq!(
            search,
            ContextRequest::Search {
                text: "oom".to_string(),
                limit: Some(3)
            }
        );

        let recall: ContextRequest =
            serde_json::from_str(r#"{"query":"recall","text":"oom"}"#).unwrap();
        assert_eq!(
            recall,
            ContextRequest::Recall {
                text: "oom".to_string(),
                limit: None
            }
        );
    }

    #[test]
    fn intent_round_trips() {
        let intent = ContextRequest::Intent {
            text: "fixing the login bug".to_string(),
        };
        let encoded = serde_json::to_string(&intent).unwrap();
        assert_eq!(
            encoded,
            r#"{"query":"intent","text":"fixing the login bug"}"#
        );
        assert_eq!(
            serde_json::from_str::<ContextRequest>(&encoded).unwrap(),
            intent
        );
    }

    #[test]
    fn unknown_query_verbs_are_rejected() {
        assert!(serde_json::from_str::<ContextRequest>(r#"{"query":"launch_missiles"}"#).is_err());
    }

    #[test]
    fn blank_text_reads_as_absent() {
        let padded = ContextRequest::Now {
            text: Some("   ".to_string()),
        };
        assert_eq!(padded.text(), None);
    }

    #[test]
    fn limits_are_clamped_into_range() {
        assert_eq!(ContextRequest::resolve_limit(None), DEFAULT_SEARCH_LIMIT);
        assert_eq!(ContextRequest::resolve_limit(Some(0)), 1);
        assert_eq!(ContextRequest::resolve_limit(Some(9_999)), MAX_SEARCH_LIMIT);
        assert_eq!(ContextRequest::resolve_limit(Some(7)), 7);
    }
}
