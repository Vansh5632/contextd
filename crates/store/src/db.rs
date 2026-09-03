use contextd_core::config::AppConfig;
use contextd_core::event::{EventSource, Intent, ProcessedEvent, RawEvent};
use rusqlite::{Connection, Result};
use serde_json::Value;

/// Opens a standalone connection and migrates it.
///
/// Prefer [`crate::store::Store`] in the daemon; this stays for tests and for
/// one-shot tools that only need a single connection.
pub fn init_db(config: &AppConfig) -> Result<Connection> {
    crate::vector::register_vec_extension();
    let conn = Connection::open(&config.db_path)?;
    migrate(&conn)?;
    Ok(conn)
}

/// Brings a connection's schema up to date. Safe to run on every open.
pub fn migrate(conn: &Connection) -> Result<()> {
    conn.execute(
        "CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            timestamp_ms INTEGER NOT NULL,
            source TEXT NOT NULL,
            payload TEXT NOT NULL,
            score REAL NOT NULL DEFAULT 0.0
        )",
        (),
    )?;

    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS vec_events USING vec0(
            event_id TEXT PRIMARY KEY,
            embedding float[768]
        )",
        (),
    )?;

    // What the user says they are working on. Append-only: the newest row wins,
    // and the older rows are a record of how the session drifted.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS intents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            declared_at_ms INTEGER NOT NULL,
            text TEXT NOT NULL
        )",
        (),
    )?;

    crate::archive::migrate(conn)?;

    add_missing_columns(conn)?;
    create_indices(conn)?;

    Ok(())
}

/// Columns added after the first release. Additive only: an older binary
/// reading a newer database still works, which matters because the daemon
/// upgrades underneath a database people care about.
const EVENT_COLUMNS: &[(&str, &str)] = &[
    ("score", "REAL NOT NULL DEFAULT 0.0"),
    // Which burst of work this belongs to. Set at ingest.
    ("session_id", "TEXT"),
    // coding / research / general_productivity. Filled in by enrichment.
    ("use_case", "TEXT"),
    // episodic / semantic / procedural. Decides which tier it belongs in.
    ("memory_type", "TEXT"),
    // Short human-readable form, used when a briefing cannot afford the payload.
    ("summary", "TEXT"),
    // When enrichment last touched this row. NULL means "still needs work",
    // which is what the enrichment backlog query looks for.
    ("enriched_at_ms", "INTEGER"),
];

fn add_missing_columns(conn: &Connection) -> Result<()> {
    let existing: Vec<String> = conn
        .prepare("PRAGMA table_info(events)")?
        .query_map([], |row| row.get::<_, String>(1))?
        .collect::<Result<Vec<_>>>()?;

    for (name, definition) in EVENT_COLUMNS {
        if !existing.iter().any(|column| column == name) {
            conn.execute(
                &format!("ALTER TABLE events ADD COLUMN {name} {definition}"),
                (),
            )?;
        }
    }

    Ok(())
}

/// Without these, every recency query and every prune is a full table scan.
///
/// Kept deliberately small. Ingest is the hot path, and every index is another
/// write per event, so each one here has to earn its place in a query plan.
fn create_indices(conn: &Connection) -> Result<()> {
    let statements = [
        // Serves both `ORDER BY timestamp_ms DESC LIMIT n` (every briefing) and
        // the pruner's `WHERE timestamp_ms < ?` range scan.
        "CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp_ms DESC)",
        // Session-scoped reads for working memory.
        "CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, timestamp_ms DESC)",
        // The enrichment backlog. Indexing timestamp rather than the predicate
        // column is what lets `ORDER BY timestamp_ms ASC` read straight off the
        // index instead of sorting into a temp B-tree.
        "CREATE INDEX IF NOT EXISTS idx_events_backlog
            ON events(timestamp_ms) WHERE enriched_at_ms IS NULL",
        // Superseded by idx_events_timestamp, which the planner prefers anyway.
        "DROP INDEX IF EXISTS idx_events_prune",
        // Earlier name for the backlog index, keyed on the wrong column.
        // `CREATE INDEX IF NOT EXISTS` will not redefine an existing index, so
        // replacing it means dropping the old name outright.
        "DROP INDEX IF EXISTS idx_events_enrichment",
    ];

    for statement in statements {
        conn.execute(statement, ())?;
    }

    Ok(())
}

pub fn insert_event(conn: &Connection, event: &ProcessedEvent) -> Result<()> {
    // Convert the enum and JSON payload to strings for SQLite
    let source_str =
        serde_json::to_string(&event.raw.source).unwrap_or_else(|_| "\"unknown\"".to_string());
    let payload_str =
        serde_json::to_string(&event.raw.payload).unwrap_or_else(|_| "{}".to_string());

    // Writes every field, including the enrichment ones. The hot path leaves
    // those `None` and fills them in later, but a function that silently
    // discarded part of the struct it was handed would be a trap.
    conn.execute(
        "INSERT INTO events
            (id, timestamp_ms, source, payload, score, session_id, use_case, memory_type, summary)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
        rusqlite::params![
            &event.raw.id,
            event.raw.timestamp_ms,
            source_str.trim_matches('"'), // Remove the extra quotes serde adds to strings
            payload_str,
            event.score,
            event.session_id,
            event.use_case,
            event.memory_type,
            event.summary,
        ],
    )?;

    Ok(())
}

/// Store what the rule-based stages worked out about an event.
///
/// Deliberately separate from [`mark_enriched`]. Analysis always succeeds and is
/// useful immediately; embedding may be impossible right now. Writing them
/// together would either discard good analysis or claim an event has a vector
/// when it does not.
pub fn record_analysis(conn: &Connection, event_id: &str, enrichment: &Enrichment) -> Result<()> {
    conn.execute(
        "UPDATE events SET use_case = ?2, memory_type = ?3, summary = ?4 WHERE id = ?1",
        rusqlite::params![
            event_id,
            enrichment.use_case,
            enrichment.memory_type,
            enrichment.summary,
        ],
    )?;
    Ok(())
}

/// Declare an event fully processed, removing it from the enrichment backlog.
pub fn mark_enriched(conn: &Connection, event_id: &str, enriched_at_ms: u64) -> Result<()> {
    conn.execute(
        "UPDATE events SET enriched_at_ms = ?2 WHERE id = ?1",
        rusqlite::params![event_id, enriched_at_ms],
    )?;
    Ok(())
}

/// Remove one event and its embedding. Used by the decision engine for noise.
pub fn delete_event(conn: &Connection, event_id: &str) -> Result<()> {
    conn.execute("DELETE FROM events WHERE id = ?1", [event_id])?;
    conn.execute("DELETE FROM vec_events WHERE event_id = ?1", [event_id])?;
    Ok(())
}

/// What enrichment produced for one event. Every field is optional because
/// every stage is allowed to fail without taking the others down.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Enrichment {
    pub use_case: Option<String>,
    pub memory_type: Option<String>,
    pub summary: Option<String>,
}

/// Events that have never been enriched, oldest first.
///
/// This is what makes enrichment survive a restart: the in-memory queue is
/// lossy by design, but the backlog is in the database.
pub fn get_enrichment_backlog(conn: &Connection, limit: usize) -> Result<Vec<ProcessedEvent>> {
    let mut stmt = conn.prepare(
        "SELECT id, timestamp_ms, source, payload, score, session_id, use_case, memory_type, summary
         FROM events
         WHERE enriched_at_ms IS NULL
         ORDER BY timestamp_ms ASC
         LIMIT ?1",
    )?;

    let rows = stmt.query_map([limit as i64], processed_from_row)?;

    let mut events = Vec::new();
    for row in rows {
        events.push(row?);
    }
    Ok(events)
}

/// Events eligible for archiving: old, and not durable enough to stay live.
///
/// The memory type is what keeps this honest. Score is a guess made seconds
/// after an event happened; "this is a commit" is a fact. Procedural and
/// semantic memories stay in the live table however quietly they arrived.
pub fn get_prunable_events(
    conn: &Connection,
    cutoff_timestamp_ms: u64,
    score_threshold: f32,
    limit: usize,
) -> Result<Vec<ProcessedEvent>> {
    let sql = format!(
        "{EVENT_SELECT}
         WHERE timestamp_ms < ?1
           AND score < ?2
           AND (memory_type IS NULL OR memory_type = 'episodic')
         ORDER BY timestamp_ms ASC
         LIMIT ?3"
    );

    let mut stmt = conn.prepare(&sql)?;
    let rows = stmt.query_map(
        rusqlite::params![cutoff_timestamp_ms, score_threshold, limit as i64],
        processed_from_row,
    )?;

    let mut events = Vec::new();
    for row in rows {
        events.push(row?);
    }
    Ok(events)
}

/// Remove events from the live table once they are safely in Tier 3.
pub fn delete_events(conn: &Connection, ids: &[String]) -> Result<usize> {
    let mut deleted = 0;
    for id in ids {
        deleted += conn.execute("DELETE FROM events WHERE id = ?1", [id])?;
        conn.execute("DELETE FROM vec_events WHERE event_id = ?1", [id])?;
    }
    Ok(deleted)
}

/// The column list every read shares, so the row decoder stays in one place.
const EVENT_SELECT: &str =
    "SELECT id, timestamp_ms, source, payload, score, session_id, use_case, memory_type, summary
     FROM events";

fn processed_from_row(row: &rusqlite::Row<'_>) -> Result<ProcessedEvent> {
    let source_str: String = row.get(2)?;
    let payload_str: String = row.get(3)?;

    // A row we cannot decode is still worth returning: a briefing with a
    // slightly wrong source beats a briefing that errored out entirely.
    let source = serde_json::from_str(&format!("\"{source_str}\"")).unwrap_or(EventSource::Shell);
    let payload: Value = serde_json::from_str(&payload_str).unwrap_or_default();

    Ok(ProcessedEvent {
        raw: RawEvent {
            id: row.get(0)?,
            timestamp_ms: row.get(1)?,
            source,
            payload,
        },
        score: row.get(4)?,
        session_id: row.get(5)?,
        use_case: row.get(6)?,
        memory_type: row.get(7)?,
        summary: row.get(8)?,
    })
}

/// Newest events first. This is the "what just happened" half of a briefing.
pub fn get_recent_events(conn: &Connection, limit: usize) -> Result<Vec<ProcessedEvent>> {
    let mut stmt = conn.prepare(&format!(
        "{EVENT_SELECT} ORDER BY timestamp_ms DESC LIMIT ?1"
    ))?;

    let rows = stmt.query_map([limit as i64], processed_from_row)?;

    let mut events = Vec::new();
    for row in rows {
        events.push(row?);
    }
    Ok(events)
}

/// Newest events first within one session.
pub fn get_session_events(
    conn: &Connection,
    session_id: &str,
    limit: usize,
) -> Result<Vec<ProcessedEvent>> {
    let mut stmt = conn.prepare(&format!(
        "{EVENT_SELECT} WHERE session_id = ?1 ORDER BY timestamp_ms DESC LIMIT ?2"
    ))?;

    let rows = stmt.query_map(
        rusqlite::params![session_id, limit as i64],
        processed_from_row,
    )?;

    let mut events = Vec::new();
    for row in rows {
        events.push(row?);
    }
    Ok(events)
}

/// Substring search over stored payloads, newest first.
///
/// This is the fallback that makes search work with no model running: it will
/// never find "the auth thing" when you typed "login", but it always answers.
/// Semantic search lives in `vector::search_similar_events`.
pub fn search_events_by_text(
    conn: &Connection,
    needle: &str,
    limit: usize,
) -> Result<Vec<ProcessedEvent>> {
    let mut stmt = conn.prepare(&format!(
        "{EVENT_SELECT}
         WHERE payload LIKE ?1 ESCAPE '\\'
         ORDER BY timestamp_ms DESC
         LIMIT ?2"
    ))?;

    let pattern = format!("%{}%", escape_like(needle));
    let rows = stmt.query_map(rusqlite::params![pattern, limit as i64], processed_from_row)?;

    let mut events = Vec::new();
    for row in rows {
        events.push(row?);
    }
    Ok(events)
}

/// LIKE treats `%` and `_` as wildcards, so a user searching for "100%" or
/// "get_user" would otherwise get nonsense.
fn escape_like(input: &str) -> String {
    let mut escaped = String::with_capacity(input.len());
    for ch in input.chars() {
        if matches!(ch, '%' | '_' | '\\') {
            escaped.push('\\');
        }
        escaped.push(ch);
    }
    escaped
}

/// Records what the user says they are doing. Newest declaration wins.
pub fn set_intent(conn: &Connection, text: &str, declared_at_ms: u64) -> Result<()> {
    conn.execute(
        "INSERT INTO intents (declared_at_ms, text) VALUES (?1, ?2)",
        rusqlite::params![declared_at_ms, text],
    )?;
    Ok(())
}

/// The intent in force right now, if the user ever declared one.
pub fn get_current_intent(conn: &Connection) -> Result<Option<Intent>> {
    let mut stmt =
        conn.prepare("SELECT text, declared_at_ms FROM intents ORDER BY id DESC LIMIT 1")?;
    let mut rows = stmt.query([])?;
    let Some(row) = rows.next()? else {
        return Ok(None);
    };

    Ok(Some(Intent {
        text: row.get(0)?,
        declared_at_ms: row.get(1)?,
    }))
}

pub fn get_event_by_id(conn: &Connection, id: &str) -> Result<Option<ProcessedEvent>> {
    let mut stmt = conn.prepare(&format!("{EVENT_SELECT} WHERE id = ?1"))?;

    let mut rows = stmt.query([id])?;
    let Some(row) = rows.next()? else {
        return Ok(None);
    };

    Ok(Some(processed_from_row(row)?))
}
// ==========================================
// TESTS
// ==========================================
#[cfg(test)]
mod tests {
    use super::*; // Import everything from the parent module
    use std::path::PathBuf;

    #[test]
    fn test_db_initialization() {
        // 1. Arrange: Create a config that points to RAM, not the disk
        let test_config = AppConfig {
            db_path: PathBuf::from(":memory:"),
            ..Default::default() // Fill the rest with defaults
        };

        // 2. Act: Run our function
        let conn = init_db(&test_config).expect("Failed to initialize database");

        // 3. Assert: Query SQLite's internal schema table to prove our table was created
        let mut stmt = conn
            .prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='events'")
            .unwrap();

        let table_exists = stmt.exists([]).unwrap();

        assert!(
            table_exists,
            "The events table should have been created in the database!"
        );
    }
}
