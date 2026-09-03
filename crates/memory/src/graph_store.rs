//! Mirroring Tier 2 to disk.
//!
//! The graph is only useful once it has watched you work for a while, so
//! losing it on every restart would mean it is never useful. It is stored as
//! two plain tables and rebuilt into RAM on boot.
//!
//! Persistence is a full rewrite rather than an incremental update. The graph
//! is small — thousands of nodes after months of use — and a rewrite inside one
//! transaction cannot leave half a graph behind if the daemon is killed.

use anyhow::Result;
use rusqlite::Connection;

use crate::graph::{Entity, EntityKind, GraphSnapshot, KnowledgeGraph};

/// Create the Tier 2 tables. Safe to run on every open.
pub fn migrate(conn: &Connection) -> Result<()> {
    conn.execute(
        "CREATE TABLE IF NOT EXISTS graph_nodes (
            key  TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            name TEXT NOT NULL
        )",
        (),
    )?;

    conn.execute(
        "CREATE TABLE IF NOT EXISTS graph_edges (
            from_key TEXT NOT NULL,
            to_key   TEXT NOT NULL,
            weight   REAL NOT NULL,
            PRIMARY KEY (from_key, to_key)
        )",
        (),
    )?;

    Ok(())
}

/// Write the whole graph, replacing whatever was there.
///
/// Takes a detached [`GraphSnapshot`] rather than the graph itself so the
/// caller can release its lock before this touches the disk.
pub fn save(conn: &mut Connection, snapshot: &GraphSnapshot) -> Result<()> {
    let tx = conn.transaction()?;

    tx.execute("DELETE FROM graph_edges", ())?;
    tx.execute("DELETE FROM graph_nodes", ())?;

    {
        let mut insert_node =
            tx.prepare("INSERT INTO graph_nodes (key, kind, name) VALUES (?1, ?2, ?3)")?;
        for entity in &snapshot.entities {
            insert_node.execute(rusqlite::params![
                entity.key(),
                entity.kind.as_str(),
                entity.name
            ])?;
        }

        let mut insert_edge = tx.prepare(
            "INSERT OR REPLACE INTO graph_edges (from_key, to_key, weight) VALUES (?1, ?2, ?3)",
        )?;
        for (from, to, weight) in &snapshot.edges {
            insert_edge.execute(rusqlite::params![from.key(), to.key(), weight])?;
        }
    }

    tx.commit()?;
    Ok(())
}

/// Rebuild the graph from disk.
///
/// Rows that cannot be understood are skipped rather than failing the load: a
/// graph missing one edge is still worth having, and refusing to boot because
/// of a stale row would be a poor trade.
pub fn load(conn: &Connection) -> Result<KnowledgeGraph> {
    let mut graph = KnowledgeGraph::new();

    let mut nodes = std::collections::HashMap::new();
    {
        let mut stmt = conn.prepare("SELECT key, kind, name FROM graph_nodes")?;
        let rows = stmt.query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
            ))
        })?;

        for row in rows {
            let (key, kind, name) = row?;
            let Some(kind) = EntityKind::parse(&kind) else {
                continue;
            };
            let entity = Entity::new(kind, name);
            graph.restore_entity(entity.clone());
            nodes.insert(key, entity);
        }
    }

    let mut stmt = conn.prepare("SELECT from_key, to_key, weight FROM graph_edges")?;
    let rows = stmt.query_map([], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, f32>(2)?,
        ))
    })?;

    for row in rows {
        let (from_key, to_key, weight) = row?;
        let (Some(from), Some(to)) = (nodes.get(&from_key), nodes.get(&to_key)) else {
            continue;
        };
        graph.restore_edge(from.clone(), to.clone(), weight);
    }

    Ok(graph)
}

#[cfg(test)]
mod tests {
    use super::*;
    use contextd_core::event::{EventSource, ProcessedEvent, RawEvent};
    use serde_json::json;

    fn conn() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        migrate(&conn).unwrap();
        conn
    }

    fn event(source: EventSource, timestamp_ms: u64, payload: serde_json::Value) -> ProcessedEvent {
        ProcessedEvent::new(
            RawEvent {
                id: format!("evt-{timestamp_ms}"),
                timestamp_ms,
                source,
                payload,
            },
            0.6,
        )
    }

    fn populated() -> KnowledgeGraph {
        let mut graph = KnowledgeGraph::new();
        graph.observe(&event(
            EventSource::FileSystem,
            1_000,
            json!({"path": "/repo/src/main.rs"}),
        ));
        graph.observe(&event(
            EventSource::Shell,
            2_000,
            json!({"command": "cargo test"}),
        ));
        graph
    }

    #[test]
    fn a_graph_survives_a_restart() {
        let mut conn = conn();
        let graph = populated();
        save(&mut conn, &graph.snapshot()).unwrap();

        let restored = load(&conn).unwrap();
        assert_eq!(restored.node_count(), graph.node_count());
        assert_eq!(restored.edge_count(), graph.edge_count());

        let main = Entity::new(EntityKind::File, "/repo/src/main.rs");
        assert_eq!(restored.neighbours(&main, 5), graph.neighbours(&main, 5));
    }

    #[test]
    fn saving_twice_replaces_rather_than_duplicates() {
        let mut conn = conn();
        let graph = populated();

        save(&mut conn, &graph.snapshot()).unwrap();
        save(&mut conn, &graph.snapshot()).unwrap();

        let restored = load(&conn).unwrap();
        assert_eq!(restored.node_count(), graph.node_count());
        assert_eq!(restored.edge_count(), graph.edge_count());
    }

    #[test]
    fn weights_survive_the_round_trip() {
        let mut conn = conn();
        let mut graph = KnowledgeGraph::new();
        for round in 0..4 {
            let base = round * 10_000;
            graph.observe(&event(
                EventSource::FileSystem,
                base,
                json!({"path": "/repo/a.rs"}),
            ));
            graph.observe(&event(
                EventSource::Shell,
                base + 500,
                json!({"command": "cargo test"}),
            ));
        }
        let before = graph.neighbours(&Entity::new(EntityKind::File, "/repo/a.rs"), 5)[0].weight;
        assert!(before > 1.0, "the test needs an edge worth checking");

        save(&mut conn, &graph.snapshot()).unwrap();
        let restored = load(&conn).unwrap();

        let after = restored.neighbours(&Entity::new(EntityKind::File, "/repo/a.rs"), 5)[0].weight;
        assert_eq!(after, before);
    }

    #[test]
    fn loading_an_empty_database_gives_an_empty_graph() {
        let restored = load(&conn()).unwrap();
        assert_eq!(restored.node_count(), 0);
    }

    #[test]
    fn an_unreadable_row_is_skipped_rather_than_failing_the_load() {
        // A node kind written by a future version must not stop the daemon
        // booting; the rest of the graph is still worth having.
        let conn = conn();
        conn.execute(
            "INSERT INTO graph_nodes (key, kind, name) VALUES ('quantum:x', 'quantum', 'x')",
            (),
        )
        .unwrap();
        conn.execute(
            "INSERT INTO graph_nodes (key, kind, name) VALUES ('file:/a.rs', 'file', '/a.rs')",
            (),
        )
        .unwrap();
        conn.execute(
            "INSERT INTO graph_edges (from_key, to_key, weight) VALUES ('quantum:x', 'file:/a.rs', 2.0)",
            (),
        )
        .unwrap();

        let restored = load(&conn).unwrap();
        assert_eq!(restored.node_count(), 1);
        assert_eq!(
            restored.edge_count(),
            0,
            "an edge to a dropped node must be dropped too"
        );
    }

    #[test]
    fn migrating_twice_is_a_no_op() {
        let conn = conn();
        migrate(&conn).unwrap();
        assert_eq!(load(&conn).unwrap().node_count(), 0);
    }
}
