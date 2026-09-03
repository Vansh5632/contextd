//! Connection management.
//!
//! The daemon used to share one `Mutex<Connection>` between the ingest writer,
//! the pruner, and every agent query. That serialises everything: a snapshot
//! request would block behind a prune, and both would block ingest.
//!
//! SQLite in WAL mode allows one writer concurrent with many readers, so that
//! is the shape here: a single writer behind a mutex, and a small pool of read
//! connections handed out by RAII guard.

use std::ops::Deref;
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};

use contextd_core::config::AppConfig;
use rusqlite::{Connection, OpenFlags, Result};

/// Read connections kept alive between requests. Opening one is not free, and
/// we never expect more than a handful of concurrent agent queries.
const MAX_IDLE_READERS: usize = 4;

/// How long a connection waits on a lock before giving up. Generous, because
/// the alternative is dropping an event.
const BUSY_TIMEOUT_MS: u32 = 5_000;

static MEMORY_DB_COUNTER: AtomicU64 = AtomicU64::new(0);

/// How to open further connections to the same database.
#[derive(Debug, Clone)]
struct OpenSpec {
    uri: String,
    flags: OpenFlags,
}

impl OpenSpec {
    fn new(db_path: &Path) -> Self {
        // `:memory:` is per-connection, which would give each reader its own
        // empty database. Naming it and sharing the cache makes an in-memory
        // store behave like a file one, so tests exercise the real code path.
        if db_path.as_os_str() == ":memory:" {
            let id = MEMORY_DB_COUNTER.fetch_add(1, Ordering::Relaxed);
            return Self {
                uri: format!("file:contextd-mem-{id}?mode=memory&cache=shared"),
                flags: OpenFlags::default() | OpenFlags::SQLITE_OPEN_URI,
            };
        }

        Self {
            uri: db_path.to_string_lossy().into_owned(),
            flags: OpenFlags::default(),
        }
    }

    fn open(&self) -> Result<Connection> {
        let conn = Connection::open_with_flags(&self.uri, self.flags)?;
        conn.busy_timeout(std::time::Duration::from_millis(BUSY_TIMEOUT_MS as u64))?;
        // WAL is what lets readers run while the writer is busy. It is a
        // property of the database file, not the connection, but setting it is
        // idempotent and harmless. It is also a no-op for in-memory databases.
        conn.pragma_update(None, "journal_mode", "WAL")?;
        // NORMAL trades a tiny crash window for far fewer fsyncs. For a stream
        // of ambient observations that is the right trade; losing the last few
        // events on a power cut is survivable, blocking the writer is not.
        conn.pragma_update(None, "synchronous", "NORMAL")?;
        conn.pragma_update(None, "foreign_keys", "ON")?;
        Ok(conn)
    }
}

/// Owns every connection to the events database.
pub struct Store {
    spec: OpenSpec,
    writer: tokio::sync::Mutex<Connection>,
    idle_readers: std::sync::Mutex<Vec<Connection>>,
}

impl Store {
    /// Open the database, apply migrations, and prepare the connection pool.
    pub fn open(config: &AppConfig) -> Result<Self> {
        crate::vector::register_vec_extension();

        let spec = OpenSpec::new(&config.db_path);
        let writer = spec.open()?;
        crate::db::migrate(&writer)?;

        Ok(Self {
            spec,
            writer: tokio::sync::Mutex::new(writer),
            idle_readers: std::sync::Mutex::new(Vec::new()),
        })
    }

    /// The one connection allowed to write. Holding this guard blocks other
    /// writers but not readers.
    pub async fn writer(&self) -> tokio::sync::MutexGuard<'_, Connection> {
        self.writer.lock().await
    }

    /// Borrow a read connection. Returned to the pool when the guard drops.
    pub fn reader(&self) -> Result<ReadGuard<'_>> {
        let pooled = self
            .idle_readers
            .lock()
            .expect("reader pool mutex poisoned")
            .pop();

        let conn = match pooled {
            Some(conn) => conn,
            None => self.spec.open()?,
        };

        Ok(ReadGuard {
            store: self,
            conn: Some(conn),
        })
    }

    fn recycle(&self, conn: Connection) {
        let mut idle = self
            .idle_readers
            .lock()
            .expect("reader pool mutex poisoned");
        if idle.len() < MAX_IDLE_READERS {
            idle.push(conn);
        }
        // Over the cap, just let it close.
    }
}

/// A borrowed read connection. Derefs to [`Connection`] so callers can use the
/// plain `db::` functions without knowing about pooling.
pub struct ReadGuard<'a> {
    store: &'a Store,
    conn: Option<Connection>,
}

impl Deref for ReadGuard<'_> {
    type Target = Connection;

    fn deref(&self) -> &Connection {
        self.conn.as_ref().expect("read guard used after drop")
    }
}

impl Drop for ReadGuard<'_> {
    fn drop(&mut self) {
        if let Some(conn) = self.conn.take() {
            self.store.recycle(conn);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use contextd_core::event::{EventSource, ProcessedEvent, RawEvent};
    use contextd_core::test_utils::test_config_in_memory;
    use serde_json::json;

    fn processed(id: &str, timestamp_ms: u64) -> ProcessedEvent {
        ProcessedEvent::new(
            RawEvent {
                id: id.to_string(),
                timestamp_ms,
                source: EventSource::Shell,
                payload: json!({ "command": "cargo test" }),
            },
            0.5,
        )
    }

    #[tokio::test]
    async fn readers_see_what_the_writer_wrote() {
        let store = Store::open(&test_config_in_memory()).unwrap();

        {
            let conn = store.writer().await;
            crate::db::insert_event(&conn, &processed("a", 1)).unwrap();
        }

        let reader = store.reader().unwrap();
        let events = crate::db::get_recent_events(&reader, 10).unwrap();
        assert_eq!(events.len(), 1, "a named in-memory db must be shared");
        assert_eq!(events[0].raw.id, "a");
    }

    #[tokio::test]
    async fn two_readers_can_be_live_at_once() {
        let store = Store::open(&test_config_in_memory()).unwrap();
        {
            let conn = store.writer().await;
            crate::db::insert_event(&conn, &processed("a", 1)).unwrap();
        }

        let first = store.reader().unwrap();
        let second = store.reader().unwrap();

        assert_eq!(crate::db::get_recent_events(&first, 10).unwrap().len(), 1);
        assert_eq!(crate::db::get_recent_events(&second, 10).unwrap().len(), 1);
    }

    #[tokio::test]
    async fn dropped_readers_go_back_to_the_pool() {
        let store = Store::open(&test_config_in_memory()).unwrap();

        drop(store.reader().unwrap());
        assert_eq!(store.idle_readers.lock().unwrap().len(), 1);

        // Borrowing again reuses it rather than opening a second connection.
        let guard = store.reader().unwrap();
        assert_eq!(store.idle_readers.lock().unwrap().len(), 0);
        drop(guard);
        assert_eq!(store.idle_readers.lock().unwrap().len(), 1);
    }

    #[tokio::test]
    async fn the_idle_pool_is_capped() {
        let store = Store::open(&test_config_in_memory()).unwrap();

        let guards: Vec<_> = (0..MAX_IDLE_READERS + 3)
            .map(|_| store.reader().unwrap())
            .collect();
        drop(guards);

        assert_eq!(
            store.idle_readers.lock().unwrap().len(),
            MAX_IDLE_READERS,
            "surplus connections should be closed, not hoarded"
        );
    }

    #[tokio::test]
    async fn separate_stores_do_not_share_memory_databases() {
        let first = Store::open(&test_config_in_memory()).unwrap();
        let second = Store::open(&test_config_in_memory()).unwrap();

        {
            let conn = first.writer().await;
            crate::db::insert_event(&conn, &processed("only-in-first", 1)).unwrap();
        }

        let reader = second.reader().unwrap();
        assert!(
            crate::db::get_recent_events(&reader, 10)
                .unwrap()
                .is_empty(),
            "each in-memory store needs its own name"
        );
    }

    #[tokio::test]
    async fn a_file_backed_store_round_trips() {
        let dir = std::env::temp_dir().join(format!("contextd-store-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let db_path = dir.join("events.db");
        let _ = std::fs::remove_file(&db_path);

        let config = AppConfig {
            db_path: db_path.clone(),
            ..Default::default()
        };

        {
            let store = Store::open(&config).unwrap();
            let conn = store.writer().await;
            crate::db::insert_event(&conn, &processed("persisted", 1)).unwrap();
        }

        let reopened = Store::open(&config).unwrap();
        let reader = reopened.reader().unwrap();
        assert_eq!(
            crate::db::get_recent_events(&reader, 10).unwrap()[0].raw.id,
            "persisted"
        );

        let _ = std::fs::remove_dir_all(&dir);
    }
}
