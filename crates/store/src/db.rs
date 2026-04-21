use contextd_core::config::AppConfig;
use contextd_core::event::ProcessedEvent;
use rusqlite::{Connection, Result}; // Pulling from shared core models

/// Initializes the database and runs the first migration
pub fn init_db(config: &AppConfig) -> Result<Connection> {
    // Open a connection using the path from our config
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
