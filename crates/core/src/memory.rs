//! The vocabulary the enrichment pipeline produces.
//!
//! These are stored as plain text in SQLite rather than integers, because the
//! database outlives any particular build of contextd and a human reading it
//! with `sqlite3` should be able to tell what a row means.

use serde::{Deserialize, Serialize};
use std::fmt;
use std::str::FromStr;

/// What kind of work an event belongs to.
///
/// This is what lets a briefing say "you have been reading docs for an hour"
/// rather than listing forty individual observations.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum UseCase {
    /// Writing, building, testing, or debugging software.
    Coding,
    /// Reading, searching, comparing. Gathering rather than producing.
    Research,
    /// Everything else the machine is used for.
    GeneralProductivity,
}

impl UseCase {
    pub fn as_str(self) -> &'static str {
        match self {
            UseCase::Coding => "coding",
            UseCase::Research => "research",
            UseCase::GeneralProductivity => "general_productivity",
        }
    }
}

impl fmt::Display for UseCase {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

impl FromStr for UseCase {
    type Err = ();

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "coding" => Ok(UseCase::Coding),
            "research" => Ok(UseCase::Research),
            "general_productivity" => Ok(UseCase::GeneralProductivity),
            _ => Err(()),
        }
    }
}

/// How long a memory stays useful, which decides where it lives.
///
/// Borrowed from the standard split in memory research, because the three kinds
/// genuinely want different retention: what happened once, what is true, and
/// how something is done.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MemoryType {
    /// A specific thing that happened at a specific time. "I ran the tests and
    /// three failed." Valuable now, worthless in a month.
    Episodic,
    /// A durable fact about the project. "This repo uses pnpm, not npm."
    Semantic,
    /// A repeatable way of doing something. "Deploying means running make ship."
    Procedural,
}

impl MemoryType {
    pub fn as_str(self) -> &'static str {
        match self {
            MemoryType::Episodic => "episodic",
            MemoryType::Semantic => "semantic",
            MemoryType::Procedural => "procedural",
        }
    }

    /// Whether this kind of memory is worth keeping past the retention window.
    ///
    /// Episodic memory ages out; the other two are the reason an archive exists.
    pub fn survives_pruning(self) -> bool {
        !matches!(self, MemoryType::Episodic)
    }
}

impl fmt::Display for MemoryType {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

impl FromStr for MemoryType {
    type Err = ();

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "episodic" => Ok(MemoryType::Episodic),
            "semantic" => Ok(MemoryType::Semantic),
            "procedural" => Ok(MemoryType::Procedural),
            _ => Err(()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn use_case_round_trips_through_its_string_form() {
        for case in [
            UseCase::Coding,
            UseCase::Research,
            UseCase::GeneralProductivity,
        ] {
            assert_eq!(UseCase::from_str(case.as_str()), Ok(case));
        }
    }

    #[test]
    fn memory_type_round_trips_through_its_string_form() {
        for kind in [
            MemoryType::Episodic,
            MemoryType::Semantic,
            MemoryType::Procedural,
        ] {
            assert_eq!(MemoryType::from_str(kind.as_str()), Ok(kind));
        }
    }

    #[test]
    fn stored_text_matches_the_json_form() {
        // The column and the wire format must agree, or a snapshot and a
        // `sqlite3` query will disagree about the same row.
        assert_eq!(
            serde_json::to_string(&UseCase::GeneralProductivity).unwrap(),
            "\"general_productivity\""
        );
        assert_eq!(
            serde_json::to_string(&MemoryType::Procedural).unwrap(),
            "\"procedural\""
        );
    }

    #[test]
    fn unknown_values_are_rejected_rather_than_guessed() {
        assert!(UseCase::from_str("vibes").is_err());
        assert!(MemoryType::from_str("muscle").is_err());
    }

    #[test]
    fn only_episodic_memory_ages_out() {
        assert!(!MemoryType::Episodic.survives_pruning());
        assert!(MemoryType::Semantic.survives_pruning());
        assert!(MemoryType::Procedural.survives_pruning());
    }
}
