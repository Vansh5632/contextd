//! Tier 3: old context, compressed rather than deleted.
//!
//! The pruner used to hard-delete anything old and low-scoring, which is the
//! right instinct applied too bluntly: "low-scoring" is a guess made seconds
//! after an event happened, with no idea what would matter later. A month on,
//! the cheap answer to "when did this dependency get added" is exactly the
//! event that got thrown away.
//!
//! So instead of deleting, we batch old events into one zstd-compressed JSON
//! blob per window. Events compress extremely well — they are repetitive JSON
//! over a small vocabulary of paths and commands — so keeping everything costs
//! a small fraction of what the live table did.
//!
//! What is deliberately *not* here: an index. Tier 3 is cold storage, read by
//! date range when someone asks a question the live tiers cannot answer. Making
//! it fast to query would mean keeping it uncompressed, which defeats the point.

use anyhow::{Context, Result};
use contextd_core::event::ProcessedEvent;
use rusqlite::Connection;

/// zstd level. 3 is the default and the right trade here: the data is already
/// highly compressible, and higher levels buy little for noticeably more CPU in
/// a background worker we would rather stayed invisible.
const COMPRESSION_LEVEL: i32 = 3;

/// One compressed window of history.
#[derive(Debug, Clone, PartialEq)]
pub struct ArchiveSegment {
    pub from_ms: u64,
    pub to_ms: u64,
    pub event_count: usize,
    /// One line per event, kept uncompressed so a segment can be described
    /// without paying to decompress it.
    pub digest: String,
}

/// Create the Tier 3 table. Safe to run on every open.
pub fn migrate(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute(
        "CREATE TABLE IF NOT EXISTS archive (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            from_ms     INTEGER NOT NULL,
            to_ms       INTEGER NOT NULL,
            event_count INTEGER NOT NULL,
            digest      TEXT NOT NULL,
            blob        BLOB NOT NULL
        )",
        (),
    )?;

    // Every read of Tier 3 is by date range, so this is the only index it needs.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_archive_range ON archive (from_ms, to_ms)",
        (),
    )?;

    Ok(())
}

/// Compress a batch of events into one archive segment.
///
/// Returns `None` for an empty batch rather than writing a segment describing
/// nothing.
pub fn archive_events(conn: &Connection, events: &[ProcessedEvent]) -> Result<Option<i64>> {
    if events.is_empty() {
        return Ok(None);
    }

    let from_ms = events
        .iter()
        .map(|e| e.raw.timestamp_ms)
        .min()
        .unwrap_or_default();
    let to_ms = events
        .iter()
        .map(|e| e.raw.timestamp_ms)
        .max()
        .unwrap_or_default();

    let digest = digest_of(events);
    let json = serde_json::to_vec(events).context("serializing events for the archive")?;
    let blob = zstd::encode_all(json.as_slice(), COMPRESSION_LEVEL)
        .context("compressing the archive segment")?;

    conn.execute(
        "INSERT INTO archive (from_ms, to_ms, event_count, digest, blob)
         VALUES (?1, ?2, ?3, ?4, ?5)",
        rusqlite::params![from_ms, to_ms, events.len() as i64, digest, blob],
    )?;

    Ok(Some(conn.last_insert_rowid()))
}

/// Read one segment back, decompressing it.
pub fn read_segment(conn: &Connection, id: i64) -> Result<Vec<ProcessedEvent>> {
    let blob: Vec<u8> = conn.query_row("SELECT blob FROM archive WHERE id = ?1", [id], |row| {
        row.get(0)
    })?;

    let json = zstd::decode_all(blob.as_slice()).context("decompressing the archive segment")?;
    serde_json::from_slice(&json).context("parsing the archive segment")
}

/// Describe the segments overlapping a time range, without decompressing them.
///
/// This is what a briefing uses: enough to say "there is history here, and it
/// is about these things" for a cost that does not scale with archive size.
pub fn segments_in_range(
    conn: &Connection,
    from_ms: u64,
    to_ms: u64,
) -> Result<Vec<ArchiveSegment>> {
    // SQLite integers are signed, so `u64::MAX` — the natural way to ask for
    // "everything" — would otherwise fail the bind rather than widen the range.
    let from_ms = from_ms.min(i64::MAX as u64) as i64;
    let to_ms = to_ms.min(i64::MAX as u64) as i64;

    let mut stmt = conn.prepare(
        "SELECT from_ms, to_ms, event_count, digest
         FROM archive
         WHERE to_ms >= ?1 AND from_ms <= ?2
         ORDER BY from_ms DESC",
    )?;

    let rows = stmt.query_map(rusqlite::params![from_ms, to_ms], |row| {
        Ok(ArchiveSegment {
            from_ms: row.get(0)?,
            to_ms: row.get(1)?,
            event_count: row.get::<_, i64>(2)? as usize,
            digest: row.get(3)?,
        })
    })?;

    let mut segments = Vec::new();
    for row in rows {
        segments.push(row?);
    }
    Ok(segments)
}

/// How many events are held in Tier 3.
pub fn archived_event_count(conn: &Connection) -> Result<usize> {
    let count: i64 = conn.query_row(
        "SELECT COALESCE(SUM(event_count), 0) FROM archive",
        [],
        |row| row.get(0),
    )?;
    Ok(count as usize)
}

/// A short human-readable description of what a segment contains.
///
/// Built from summaries where enrichment produced them, so the digest reads
/// like a list of things that happened rather than a list of row IDs.
fn digest_of(events: &[ProcessedEvent]) -> String {
    const MAX_LINES: usize = 12;

    let mut lines: Vec<&str> = events
        .iter()
        .filter_map(|event| event.summary.as_deref())
        .collect();

    lines.dedup();
    lines.truncate(MAX_LINES);

    if lines.is_empty() {
        return format!("{} events", events.len());
    }
    lines.join("; ")
}

#[cfg(test)]
mod tests {
    use super::*;
    use contextd_core::event::{EventSource, RawEvent};
    use serde_json::json;

    fn conn() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        migrate(&conn).unwrap();
        conn
    }

    fn event(id: &str, timestamp_ms: u64, summary: Option<&str>) -> ProcessedEvent {
        let mut event = ProcessedEvent::new(
            RawEvent {
                id: id.to_string(),
                timestamp_ms,
                source: EventSource::Shell,
                payload: json!({ "command": "cargo build --release" }),
            },
            0.2,
        );
        event.summary = summary.map(str::to_string);
        event
    }

    #[test]
    fn events_round_trip_through_compression() {
        let conn = conn();
        let events = vec![
            event("a", 1_000, Some("ran `cargo build`")),
            event("b", 2_000, Some("edited main.rs")),
        ];

        let id = archive_events(&conn, &events).unwrap().unwrap();
        let restored = read_segment(&conn, id).unwrap();

        assert_eq!(restored.len(), 2);
        assert_eq!(restored[0].raw.id, "a");
        assert_eq!(restored[1].summary.as_deref(), Some("edited main.rs"));
        assert_eq!(restored[0].raw.payload, events[0].raw.payload);
    }

    #[test]
    fn archiving_nothing_writes_nothing() {
        let conn = conn();
        assert!(archive_events(&conn, &[]).unwrap().is_none());
        assert_eq!(archived_event_count(&conn).unwrap(), 0);
    }

    #[test]
    fn a_segment_records_the_span_it_covers() {
        let conn = conn();
        let events = vec![
            event("a", 5_000, None),
            event("b", 1_000, None),
            event("c", 9_000, None),
        ];
        archive_events(&conn, &events).unwrap();

        let segments = segments_in_range(&conn, 0, 100_000).unwrap();
        assert_eq!(segments.len(), 1);
        assert_eq!(segments[0].from_ms, 1_000);
        assert_eq!(segments[0].to_ms, 9_000);
        assert_eq!(segments[0].event_count, 3);
    }

    #[test]
    fn range_queries_only_return_overlapping_segments() {
        let conn = conn();
        archive_events(&conn, &[event("old", 1_000, None)]).unwrap();
        archive_events(&conn, &[event("new", 900_000, None)]).unwrap();

        let old = segments_in_range(&conn, 0, 10_000).unwrap();
        assert_eq!(old.len(), 1);
        assert_eq!(old[0].from_ms, 1_000);

        assert!(segments_in_range(&conn, 20_000, 30_000).unwrap().is_empty());
        assert_eq!(segments_in_range(&conn, 0, 1_000_000).unwrap().len(), 2);
    }

    #[test]
    fn the_digest_describes_the_contents_without_decompressing() {
        let conn = conn();
        archive_events(
            &conn,
            &[
                event("a", 1_000, Some("ran `cargo build`")),
                event("b", 2_000, Some("committed: fix login")),
            ],
        )
        .unwrap();

        let digest = &segments_in_range(&conn, 0, 10_000).unwrap()[0].digest;
        assert!(digest.contains("cargo build"));
        assert!(digest.contains("fix login"));
    }

    #[test]
    fn an_unenriched_batch_still_gets_a_usable_digest() {
        let conn = conn();
        archive_events(&conn, &[event("a", 1_000, None), event("b", 2_000, None)]).unwrap();

        assert_eq!(
            segments_in_range(&conn, 0, 10_000).unwrap()[0].digest,
            "2 events"
        );
    }

    #[test]
    fn compression_actually_pays_for_itself() {
        // The premise of Tier 3 is that keeping everything is cheap. If events
        // ever stop compressing well, that premise is wrong and we should know.
        let events: Vec<ProcessedEvent> = (0..500)
            .map(|i| {
                event(
                    &format!("e{i}"),
                    i as u64 * 1_000,
                    Some("ran `cargo build`"),
                )
            })
            .collect();

        let json = serde_json::to_vec(&events).unwrap();
        let compressed = zstd::encode_all(json.as_slice(), COMPRESSION_LEVEL).unwrap();

        assert!(
            compressed.len() * 10 < json.len(),
            "expected better than 10x on repetitive events, got {} -> {}",
            json.len(),
            compressed.len()
        );
    }

    #[test]
    fn the_archived_count_spans_every_segment() {
        let conn = conn();
        archive_events(&conn, &[event("a", 1_000, None)]).unwrap();
        archive_events(&conn, &[event("b", 2_000, None), event("c", 3_000, None)]).unwrap();

        assert_eq!(archived_event_count(&conn).unwrap(), 3);
    }

    #[test]
    fn reading_a_missing_segment_is_an_error_not_a_panic() {
        assert!(read_segment(&conn(), 404).is_err());
    }

    #[test]
    fn asking_for_everything_does_not_overflow_the_bind() {
        // `u64::MAX` is the obvious way to say "all of history", and SQLite
        // integers are signed, so this has to be handled rather than rejected.
        let conn = conn();
        archive_events(&conn, &[event("a", 1_000, None)]).unwrap();

        assert_eq!(segments_in_range(&conn, 0, u64::MAX).unwrap().len(), 1);
    }
}
