//! The tiers of memory that live above SQLite.
//!
//! contextd keeps context in four tiers, distinguished by how fast they answer
//! and how long they remember:
//!
//! | Tier | Lives in | Answers | Horizon |
//! |------|----------|---------|---------|
//! | 0 [`working`] | RAM | "what am I doing right now" | minutes |
//! | 1 `store` | SQLite + sqlite-vec | "what did I do this week" | days |
//! | 2 [`graph`] | RAM, mirrored to SQLite | "what goes with what" | forever |
//! | 3 `store::archive` | zstd blobs in SQLite | "when did this happen" | forever |
//!
//! Tiers 1 and 3 live in the `store` crate because they are tables. Tiers 0 and
//! 2 live here because they are in-memory structures that happen to be
//! persisted, and keeping them out of `store` keeps the storage crate about
//! storage.

pub mod graph;
pub mod graph_store;
pub mod tier2;
pub mod working;

pub use graph::{Entity, EntityKind, KnowledgeGraph, Related};
pub use tier2::SharedGraph;
pub use working::WorkingSet;
