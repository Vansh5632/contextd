use contextd_core::config::AppConfig;
use contextd_core::event::ProcessedEvent;
use rusqlite::{Connection, Result}; // Pulling from shared core models

/// Initializes the database and runs the first migration
pub fn init_db(config: &AppConfig) -> Result<Connection> {
    crate::vector::register_vec_extension();
    let conn = Connection::open(&config.db_path)?;
    // Create our base table
    conn.execute(
        "CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            timestamp_ms INTEGER NOT NULL,
            source TEXT NOT NULL,
            payload TEXT NOT NULL,
            score REAL NOT NULL DEFAULT 0.0
        )",
        (), // No parameters needed for this query
    )?;

    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS vec_events USING vec0(
            event_id TEXT PRIMARY KEY,
            embedding float[768]
        )",
        (),
    )?;
    let has_score_column = conn
        .prepare("PRAGMA table_info(events)")?
        .query_map([], |row| row.get::<_, String>(1))?
        .collect::<Result<Vec<_>>>()?
        .iter()
        .any(|column| column == "score");

    if !has_score_column {
        conn.execute(
            "ALTER TABLE events ADD COLUMN score REAL NOT NULL DEFAULT 0.0",
            (),
        )?;
    }

    Ok(conn)
}

pub fn insert_event(conn: &Connection, event: &ProcessedEvent) -> Result<()> {
    // Convert the enum and JSON payload to strings for SQLite
    let source_str =
        serde_json::to_string(&event.raw.source).unwrap_or_else(|_| "\"unknown\"".to_string());
    let payload_str =
        serde_json::to_string(&event.raw.payload).unwrap_or_else(|_| "{}".to_string());

    conn.execute(
        "INSERT INTO events (id, timestamp_ms, source, payload, score) VALUES (?1, ?2, ?3, ?4, ?5)",
        (
            &event.raw.id,
            event.raw.timestamp_ms,
            source_str.trim_matches('"'), // Remove the extra quotes serde adds to strings
            payload_str,
            event.score,
        ),
    )?;

    Ok(())
}

pub fn get_recent_events(conn: &Connection, limit: usize) -> Result<Vec<ProcessedEvent>> {
    let mut stmt = conn.prepare(
        "SELECT id, timestamp_ms, source, payload, score 
         FROM events 
         ORDER BY timestamp_ms DESC 
         LIMIT ?1"
    )?;
    
    let rows = stmt.query_map([limit], |row| {
        let source_str: String = row.get(2)?;
        let payload_str: String = row.get(3)?;
        
        Ok(ProcessedEvent {
            raw: contextd_core::event::RawEvent {
                id: row.get(0)?,
                timestamp_ms: row.get(1)?,
                // We add quotes back because serde expects enum JSON strings to be quoted
                source: serde_json::from_str(&format!("\"{}\"", source_str))
                    .unwrap_or(contextd_core::event::EventSource::Shell),
                payload: serde_json::from_str(&payload_str).unwrap_or_default(),
            },
            score: row.get(4)?,
        })
    })?;

    let mut events = Vec::new();
    for row in rows {
        events.push(row?);
    }
    Ok(events)
}

pub fn get_event_by_id(conn: &Connection, id: &str) -> Result<Option<ProcessedEvent>> {
    let mut stmt = conn.prepare(
        "SELECT timestamp_ms, source, payload, score 
         FROM events WHERE id = ?1"
    )?;
    
    let mut rows = stmt.query([id])?;
    
    if let Some(row) = rows.next()? {
        let source_str: String = row.get(1)?;
        let payload_str: String = row.get(2)?;
        
        Ok(Some(ProcessedEvent {
            raw: contextd_core::event::RawEvent {
                id: id.to_string(),
                timestamp_ms: row.get(0)?,
                source: serde_json::from_str(&format!("\"{}\"", source_str))
                    .unwrap_or(contextd_core::event::EventSource::Shell),
                payload: serde_json::from_str(&payload_str).unwrap_or_default(),
            },
            score: row.get(3)?,
        }))
    } else {
        Ok(None)
    }
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
