use contextd_core::config::AppConfig;
use contextd_core::event::EventSource;
use contextd_core::test_utils::{test_config_in_memory, test_shell_event};
use std::time::{SystemTime, UNIX_EPOCH};
use store::db::init_db;

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
            event.time_stamp_ms,
            "shell",
            event.payload.to_string(),
        ),
    )
    .expect("event insert should succeed");

    let mut stmt = conn
        .prepare("SELECT id, timestamp_ms, source FROM events WHERE id = ?1")
        .expect("query should prepare");
    let row = stmt
        .query_row([event.id.as_str()], |row| {
            let id: String = row.get(0)?;
            let ts: u64 = row.get(1)?;
            let source: String = row.get(2)?;
            Ok((id, ts, source))
        })
        .expect("row should exist");

    assert_eq!(row.0, event.id);
    assert_eq!(row.1, event.time_stamp_ms);
    assert_eq!(row.2, "shell");
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
