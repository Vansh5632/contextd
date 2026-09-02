//! MCP front end for contextd.
//!
//! This crate is the process that agents spawn (`contextd --mcp`). It holds no
//! state: [`server`] defines the tool and resource surface, [`daemon`] forwards
//! each call to the running daemon's Unix socket, and [`stdio`] wires the two
//! to stdin/stdout.

pub mod daemon;
pub mod server;
pub mod stdio;
