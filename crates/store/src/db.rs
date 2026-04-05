use contextd_core::config::AppConfig;
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
            payload TEXT NOT NULL
        )",
        (), // No parameters needed for this query
    )?;

    Ok(conn)
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
