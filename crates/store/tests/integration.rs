use contextd_core::config::AppConfig;
use contextd_core::event::{EventSource, ProcessedEvent};
use contextd_core::test_utils::{test_config_in_memory, test_shell_event};
use std::time::{SystemTime, UNIX_EPOCH};
use store::db::{
    delete_events, get_event_by_id, get_prunable_events, get_recent_events, init_db, insert_event,
};
use store::vector::{EMBEDDING_DIMENSIONS, insert_embedding, search_similar_events};

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
    let event = ProcessedEvent::new(test_shell_event(), 0.9);

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

fn processed(id: &str, timestamp_ms: u64, score: f32) -> ProcessedEvent {
    let mut event = test_shell_event();
    event.id = id.to_string();
    event.timestamp_ms = timestamp_ms;
    ProcessedEvent::new(event, score)
}

fn live_ids(conn: &rusqlite::Connection) -> Vec<String> {
    conn.prepare("SELECT id FROM events ORDER BY id")
        .unwrap()
        .query_map([], |row| row.get(0))
        .unwrap()
        .collect::<rusqlite::Result<Vec<_>>>()
        .unwrap()
}

#[test]
fn pruning_selects_stale_low_score_rows_and_spares_recent_or_important_ones() {
    let cfg = test_config_in_memory();
    let conn = init_db(&cfg).expect("db should initialize");

    insert_event(&conn, &processed("old-trivial", 1_000, 0.2)).unwrap();
    insert_event(&conn, &processed("old-important", 1_000, 0.9)).unwrap();
    insert_event(&conn, &processed("fresh-trivial", 9_000, 0.1)).unwrap();

    let prunable = get_prunable_events(&conn, 5_000, 0.5, 100).expect("selection should succeed");
    let ids: Vec<String> = prunable.into_iter().map(|e| e.raw.id).collect();
    assert_eq!(ids, vec!["old-trivial".to_string()]);

    assert_eq!(delete_events(&conn, &ids).unwrap(), 1);
    assert_eq!(
        live_ids(&conn),
        vec!["fresh-trivial".to_string(), "old-important".to_string()]
    );
}

#[test]
fn durable_memories_are_never_selected_for_pruning() {
    // Score is a guess made seconds after the event; memory type is a fact.
    let cfg = test_config_in_memory();
    let conn = init_db(&cfg).expect("db should initialize");

    for (id, memory_type) in [
        ("episodic", "episodic"),
        ("semantic", "semantic"),
        ("procedural", "procedural"),
    ] {
        let mut event = processed(id, 1_000, 0.1);
        event.memory_type = Some(memory_type.to_string());
        insert_event(&conn, &event).unwrap();
    }
    // An un-enriched row has no memory type yet and must stay prunable.
    insert_event(&conn, &processed("unclassified", 1_000, 0.1)).unwrap();

    let mut ids: Vec<String> = get_prunable_events(&conn, 5_000, 0.5, 100)
        .unwrap()
        .into_iter()
        .map(|e| e.raw.id)
        .collect();
    ids.sort();

    assert_eq!(
        ids,
        vec!["episodic".to_string(), "unclassified".to_string()]
    );
}

#[test]
fn pruning_takes_the_oldest_first_when_batching() {
    let cfg = test_config_in_memory();
    let conn = init_db(&cfg).expect("db should initialize");

    insert_event(&conn, &processed("newer", 3_000, 0.1)).unwrap();
    insert_event(&conn, &processed("oldest", 1_000, 0.1)).unwrap();
    insert_event(&conn, &processed("middle", 2_000, 0.1)).unwrap();

    let ids: Vec<String> = get_prunable_events(&conn, 5_000, 0.5, 2)
        .unwrap()
        .into_iter()
        .map(|e| e.raw.id)
        .collect();

    assert_eq!(ids, vec!["oldest".to_string(), "middle".to_string()]);
}

#[test]
fn deleting_events_removes_their_embeddings_too() {
    let cfg = test_config_in_memory();
    let conn = init_db(&cfg).expect("db should initialize");

    insert_event(&conn, &processed("keep-me", 9_000, 0.9)).unwrap();
    insert_event(&conn, &processed("drop-me", 1_000, 0.1)).unwrap();

    let mut keep_vec = vec![0.0f32; EMBEDDING_DIMENSIONS];
    keep_vec[0] = 1.0;
    let mut drop_vec = vec![0.0f32; EMBEDDING_DIMENSIONS];
    drop_vec[1] = 1.0;

    insert_embedding(&conn, "keep-me", &keep_vec).unwrap();
    insert_embedding(&conn, "drop-me", &drop_vec).unwrap();

    assert_eq!(delete_events(&conn, &["drop-me".to_string()]).unwrap(), 1);

    // An orphaned vector would keep surfacing a deleted event in search.
    let results = search_similar_events(&conn, &keep_vec, 10).unwrap();
    assert_eq!(results.len(), 1);
    assert_eq!(results[0].0, "keep-me");
}

#[test]
fn get_recent_events_returns_newest_first_up_to_limit() {
    let cfg = test_config_in_memory();
    let conn = init_db(&cfg).expect("db should initialize");

    insert_event(&conn, &processed("oldest", 1_000, 0.2)).unwrap();
    insert_event(&conn, &processed("middle", 2_000, 0.4)).unwrap();
    insert_event(&conn, &processed("newest", 3_000, 0.9)).unwrap();

    let recent = get_recent_events(&conn, 2).expect("recent events should load");
    let ids: Vec<&str> = recent.iter().map(|event| event.raw.id.as_str()).collect();

    assert_eq!(ids, vec!["newest", "middle"]);
    assert_eq!(recent[0].score, 0.9);
    assert_eq!(recent[0].raw.source, EventSource::Shell);
}

#[test]
fn get_event_by_id_round_trips_payload_and_missing_rows() {
    let cfg = test_config_in_memory();
    let conn = init_db(&cfg).expect("db should initialize");
    let event = processed("evt-login", 4_000, 0.8);
    insert_event(&conn, &event).unwrap();

    let found = get_event_by_id(&conn, "evt-login")
        .expect("lookup should succeed")
        .expect("row should exist");
    assert_eq!(found.raw.id, event.raw.id);
    assert_eq!(found.raw.payload, event.raw.payload);
    assert_eq!(found.score, event.score);

    let missing = get_event_by_id(&conn, "no-such-event").expect("lookup should succeed");
    assert!(missing.is_none());
}

/// The plan for a query, as SQLite describes it.
fn query_plan(conn: &rusqlite::Connection, sql: &str) -> String {
    conn.prepare(&format!("EXPLAIN QUERY PLAN {sql}"))
        .expect("query should be preparable")
        .query_map([], |row| row.get::<_, String>(3))
        .expect("plan should be readable")
        .collect::<rusqlite::Result<Vec<_>>>()
        .expect("plan rows should decode")
        .join(" | ")
}

/// These three queries run constantly. A full scan in any of them is a silent
/// performance regression that only shows up once someone has months of history,
/// so pin the plans rather than trusting that the indices still apply.
#[test]
fn the_hot_queries_all_use_an_index() {
    let cfg = test_config_in_memory();
    let conn = init_db(&cfg).expect("db should initialize");

    let cases = [
        (
            "recency briefing",
            "SELECT id FROM events ORDER BY timestamp_ms DESC LIMIT 10",
            "idx_events_timestamp",
        ),
        (
            "pruner",
            "SELECT id FROM events WHERE timestamp_ms < 1 AND score < 0.5",
            "idx_events_timestamp",
        ),
        (
            "session scope",
            "SELECT id FROM events WHERE session_id = 'x' ORDER BY timestamp_ms DESC LIMIT 10",
            "idx_events_session",
        ),
        (
            "analysis backlog",
            "SELECT id FROM events WHERE enriched_at_ms IS NULL AND use_case IS NULL ORDER BY timestamp_ms ASC LIMIT 64",
            "idx_events_analysis_backlog",
        ),
        (
            "embedding backlog",
            "SELECT id FROM events WHERE enriched_at_ms IS NULL AND use_case IS NOT NULL ORDER BY timestamp_ms ASC LIMIT 64",
            "idx_events_backlog",
        ),
    ];

    for (name, sql, expected_index) in cases {
        let plan = query_plan(&conn, sql);
        assert!(
            plan.contains(expected_index),
            "{name} should use {expected_index}, but the plan was: {plan}"
        );
        assert!(
            !plan.contains("TEMP B-TREE"),
            "{name} should not need to sort in memory, but the plan was: {plan}"
        );
    }
}

#[test]
fn migrating_twice_is_a_no_op() {
    let cfg = test_config_in_memory();
    let conn = init_db(&cfg).expect("db should initialize");
    insert_event(&conn, &processed("survivor", 1_000, 0.7)).unwrap();

    store::db::migrate(&conn).expect("re-migrating an existing database should succeed");

    assert_eq!(get_recent_events(&conn, 10).unwrap().len(), 1);
}

#[test]
fn migration_adds_the_enrichment_columns_to_an_old_table() {
    let cfg = test_config_in_memory();
    let conn = rusqlite::Connection::open(&cfg.db_path).expect("connection should open");

    // The original v1 schema, before scoring or enrichment existed.
    conn.execute(
        "CREATE TABLE events (
            id TEXT PRIMARY KEY,
            timestamp_ms INTEGER NOT NULL,
            source TEXT NOT NULL,
            payload TEXT NOT NULL
        )",
        (),
    )
    .unwrap();
    conn.execute(
        "INSERT INTO events (id, timestamp_ms, source, payload)
         VALUES ('legacy', 1, 'shell', '{\"command\":\"ls\"}')",
        (),
    )
    .unwrap();

    store::vector::register_vec_extension();
    store::db::migrate(&conn).expect("migration should upgrade an old database in place");

    let columns: Vec<String> = conn
        .prepare("PRAGMA table_info(events)")
        .unwrap()
        .query_map([], |row| row.get(1))
        .unwrap()
        .collect::<rusqlite::Result<Vec<_>>>()
        .unwrap();

    for expected in [
        "score",
        "session_id",
        "use_case",
        "memory_type",
        "summary",
        "enriched_at_ms",
    ] {
        assert!(
            columns.contains(&expected.to_string()),
            "missing {expected}"
        );
    }

    // The pre-existing row must survive, and read back with empty enrichment.
    let legacy = get_event_by_id(&conn, "legacy").unwrap().unwrap();
    assert_eq!(legacy.score, 0.0);
    assert!(legacy.session_id.is_none());
    assert!(legacy.summary.is_none());
}

#[test]
fn enrichment_round_trips_through_the_row() {
    use store::db::{
        Enrichment, get_analysis_backlog, get_embedding_backlog, mark_enriched, record_analysis,
    };

    let cfg = test_config_in_memory();
    let conn = init_db(&cfg).expect("db should initialize");
    insert_event(&conn, &processed("evt", 1_000, 0.7)).unwrap();

    assert_eq!(get_analysis_backlog(&conn, 10).unwrap().len(), 1);
    assert!(
        get_embedding_backlog(&conn, 10).unwrap().is_empty(),
        "a fresh row still needs analysis, not a vector"
    );

    record_analysis(
        &conn,
        "evt",
        &Enrichment {
            use_case: Some("coding".to_string()),
            memory_type: Some("episodic".to_string()),
            summary: Some("ran the test suite".to_string()),
        },
    )
    .unwrap();

    let event = get_event_by_id(&conn, "evt").unwrap().unwrap();
    assert_eq!(event.use_case.as_deref(), Some("coding"));
    assert_eq!(event.memory_type.as_deref(), Some("episodic"));
    assert_eq!(event.summary.as_deref(), Some("ran the test suite"));

    assert!(
        get_analysis_backlog(&conn, 10).unwrap().is_empty(),
        "analysis moves the row off the analysis backlog"
    );
    assert_eq!(
        get_embedding_backlog(&conn, 10).unwrap().len(),
        1,
        "analysis alone does not finish an event; it still needs a vector"
    );

    mark_enriched(&conn, "evt", 9_999).unwrap();
    assert!(
        get_embedding_backlog(&conn, 10).unwrap().is_empty(),
        "an enriched row must leave the embedding backlog"
    );
}

#[test]
fn analysis_backlog_advances_past_already_analyzed_rows() {
    use store::db::{Enrichment, get_analysis_backlog, get_embedding_backlog, record_analysis};

    let cfg = test_config_in_memory();
    let conn = init_db(&cfg).expect("db should initialize");

    for i in 0..70 {
        insert_event(&conn, &processed(&format!("evt-{i:02}"), 1_000 + i, 0.7)).unwrap();
    }

    let first = get_analysis_backlog(&conn, 64).unwrap();
    let first_ids: Vec<String> = first.iter().map(|e| e.raw.id.clone()).collect();
    assert_eq!(first_ids.len(), 64);
    assert_eq!(first_ids.first().map(String::as_str), Some("evt-00"));
    assert_eq!(first_ids.last().map(String::as_str), Some("evt-63"));

    let sample = Enrichment {
        use_case: Some("coding".to_string()),
        memory_type: Some("episodic".to_string()),
        summary: Some("classified".to_string()),
    };
    for id in &first_ids {
        record_analysis(&conn, id, &sample).unwrap();
    }

    let second = get_analysis_backlog(&conn, 64).unwrap();
    let second_ids: Vec<&str> = second.iter().map(|e| e.raw.id.as_str()).collect();
    assert_eq!(
        second_ids,
        vec!["evt-64", "evt-65", "evt-66", "evt-67", "evt-68", "evt-69"],
        "analyzing the oldest 64 must uncover the remaining rows, not re-select them"
    );
    assert_eq!(get_embedding_backlog(&conn, 100).unwrap().len(), 64);
}

#[test]
fn deleting_an_event_takes_its_embedding_with_it() {
    use store::vector::{EMBEDDING_DIMENSIONS, insert_embedding, search_similar_events};

    let cfg = test_config_in_memory();
    let conn = init_db(&cfg).expect("db should initialize");
    insert_event(&conn, &processed("doomed", 1_000, 0.7)).unwrap();

    let embedding = vec![0.1_f32; EMBEDDING_DIMENSIONS];
    insert_embedding(&conn, "doomed", &embedding).unwrap();
    // Re-embedding the same event replaces rather than conflicts.
    insert_embedding(&conn, "doomed", &embedding).unwrap();

    store::db::delete_event(&conn, "doomed").unwrap();

    assert!(get_event_by_id(&conn, "doomed").unwrap().is_none());
    assert!(
        search_similar_events(&conn, &embedding, 5)
            .unwrap()
            .is_empty(),
        "an orphaned vector would keep surfacing a deleted event in search"
    );
}

#[test]
fn session_scoping_separates_two_runs_of_the_daemon() {
    use store::db::get_session_events;

    let cfg = test_config_in_memory();
    let conn = init_db(&cfg).expect("db should initialize");

    insert_event(&conn, &processed("a", 1, 0.5).with_session("morning")).unwrap();
    insert_event(&conn, &processed("b", 2, 0.5).with_session("morning")).unwrap();
    insert_event(&conn, &processed("c", 3, 0.5).with_session("afternoon")).unwrap();

    let morning = get_session_events(&conn, "morning", 10).unwrap();
    let ids: Vec<&str> = morning.iter().map(|e| e.raw.id.as_str()).collect();
    assert_eq!(ids, vec!["b", "a"], "newest first, scoped to one session");

    assert_eq!(get_session_events(&conn, "afternoon", 10).unwrap().len(), 1);
    assert!(
        get_session_events(&conn, "never-happened", 10)
            .unwrap()
            .is_empty()
    );
}
