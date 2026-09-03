//! Everything that notices something happened.
//!
//! Sources sit on the hot path, so they follow one rule above all others: they
//! observe and publish, and never block on anything that could be slow. A
//! source that waits on the database, a model, or a socket is a source that can
//! stall the thing it is watching.

pub mod emit;
pub mod filesystem;
pub mod git;
pub mod install;
pub mod manifest;
pub mod noise;
pub mod proc_poller;
pub mod shell;
