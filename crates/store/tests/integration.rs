use contextd_core::config::AppConfig;
use contextd_core::event::{EventSource, ProcessedEvent};
use contextd_core::test_utils::{test_config_in_memory, test_shell_event};
use std::time::{SystemTime, UNIX_EPOCH};
use store::db::{init_db, insert_event};

fn unique_suffix() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock should be after UNIX epoch")
        .as_nanos()
}

#[test]
fn init_db_allows_event_insert_round_trip() {
    let cfg = test_config_in_memory();
    let conn = init_db(&cfg).expect("db should initialize");
    let event = test_shell_event();

    conn.execute(
        "INSERT INTO events (id, timestamp_ms, source, payload) VALUES (?1, ?2, ?3, ?4)",
        (
            &event.id,
            event.timestamp_ms,
            "shell",
            event.payload.to_string(),
        ),
    )
    .expect("event insert should succeed");

    let mut stmt = conn
        .prepare("SELECT id, timestamp_ms, source, score FROM events WHERE id = ?1")
        .expect("query should prepare");
    let row = stmt
        .query_row([event.id.as_str()], |row| {
            let id: String = row.get(0)?;
            let ts: u64 = row.get(1)?;
            let source: String = row.get(2)?;
            let score: f32 = row.get(3)?;
            Ok((id, ts, source, score))
        })
        .expect("row should exist");

    assert_eq!(row.0, event.id);
    assert_eq!(row.1, event.timestamp_ms);
    assert_eq!(row.2, "shell");
    assert_eq!(row.3, 0.0);
}

#[test]
fn insert_event_persists_processed_score() {
    let cfg = test_config_in_memory();
    let conn = init_db(&cfg).expect("db should initialize");
    let event = ProcessedEvent {
        raw: test_shell_event(),
        score: 0.9,
    };

    insert_event(&conn, &event).expect("processed event insert should succeed");

    let score: f32 = conn
        .query_row(
            "SELECT score FROM events WHERE id = ?1",
            [event.raw.id.as_str()],
            |row| row.get(0),
        )
        .expect("inserted score should be queryable");

    assert_eq!(score, event.score);
}

#[test]
fn init_db_adds_score_column_to_existing_events_table() {
    let db_path = std::env::temp_dir().join(format!(
        "contextd-store-migration-test-{}-{}.db",
        std::process::id(),
        unique_suffix()
    ));

    let cfg = AppConfig {
        db_path: db_path.clone(),
        ..Default::default()
    };

    {
        let conn = rusqlite::Connection::open(&db_path).expect("preexisting db should open");
        conn.execute(
            "CREATE TABLE events (
                id TEXT PRIMARY KEY,
                timestamp_ms INTEGER NOT NULL,
                source TEXT NOT NULL,
                payload TEXT NOT NULL
            )",
            (),
        )
        .expect("old schema should be created");
    }

    let conn = init_db(&cfg).expect("db should migrate");
    let has_score_column = conn
        .prepare("PRAGMA table_info(events)")
        .expect("table info should prepare")
        .query_map([], |row| row.get::<_, String>(1))
        .expect("columns should query")
        .collect::<rusqlite::Result<Vec<_>>>()
        .expect("columns should collect")
        .iter()
        .any(|column| column == "score");

    if db_path.exists() {
        let _ = std::fs::remove_file(&db_path);
    }

    assert!(has_score_column, "score column should be added");
}

#[test]
fn init_db_is_idempotent_for_same_database() {
    let db_path = std::env::temp_dir().join(format!(
        "contextd-store-test-{}-{}.db",
        std::process::id(),
        unique_suffix()
    ));

    let cfg = AppConfig {
        db_path: db_path.clone(),
        ..Default::default()
    };

    let first = init_db(&cfg);
    let second = init_db(&cfg);

    if db_path.exists() {
        let _ = std::fs::remove_file(&db_path);
    }

    assert!(first.is_ok(), "first init should succeed");
    assert!(second.is_ok(), "second init should also succeed");
}

#[test]
fn init_db_fails_with_invalid_path() {
    let invalid_parent = std::env::temp_dir().join(format!(
        "contextd-missing-parent-{}-{}",
        std::process::id(),
        unique_suffix()
    ));
    let cfg = AppConfig {
        db_path: invalid_parent.join("events.db"),
        ..Default::default()
    };

    let result = init_db(&cfg);
    assert!(result.is_err(), "init must fail for invalid parent path");
}

#[test]
fn event_source_model_remains_compatible() {
    let source = EventSource::Shell;
    let encoded = serde_json::to_string(&source).expect("event source should serialize");

    assert_eq!(encoded, "\"shell\"");
}
