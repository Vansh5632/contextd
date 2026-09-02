use rusqlite::{Connection, Result, params};

pub const EMBEDDING_DIMENSIONS: usize = 768;

static REGISTER_SQLITE_VEC: std::sync::Once = std::sync::Once::new();

/// Registers sqlite-vec with rusqlite's bundled SQLite before opening connections.
#[allow(clippy::missing_transmute_annotations)]
pub fn register_vec_extension() {
    REGISTER_SQLITE_VEC.call_once(|| unsafe {
        rusqlite::ffi::sqlite3_auto_extension(Some(std::mem::transmute(
            sqlite_vec::sqlite3_vec_init as *const (),
        )));
    });
}

/// Converts f32 embeddings to little-endian bytes for portable SQLite BLOB storage.
fn f32_to_bytes(vec: &[f32]) -> Vec<u8> {
    vec.iter().flat_map(|f| f.to_le_bytes()).collect()
}

fn validate_embedding_dimensions(embedding: &[f32]) -> Result<()> {
    if embedding.len() != EMBEDDING_DIMENSIONS {
        return Err(rusqlite::Error::InvalidParameterCount(
            EMBEDDING_DIMENSIONS,
            embedding.len(),
        ));
    }
    Ok(())
}

/// Saves an embedding tied to a specific event ID.
///
/// Idempotent: re-embedding an event replaces its vector. The enrichment
/// backlog can legitimately hand us the same event twice — a crash between
/// writing the vector and marking the row done leaves it queued — and that
/// should not be an error.
pub fn insert_embedding(conn: &Connection, event_id: &str, embedding: &[f32]) -> Result<()> {
    validate_embedding_dimensions(embedding)?;
    let blob = f32_to_bytes(embedding);

    conn.execute(
        "DELETE FROM vec_events WHERE event_id = ?1",
        params![event_id],
    )?;
    conn.execute(
        "INSERT INTO vec_events (event_id, embedding) VALUES (?1, ?2)",
        params![event_id, blob],
    )?;

    Ok(())
}

/// Performs a semantic search using cosine distance.
/// Returns a list of (event_id, distance). Lower distance = higher similarity.
pub fn search_similar_events(
    conn: &Connection,
    query_embedding: &[f32],
    limit: usize,
) -> Result<Vec<(String, f32)>> {
    validate_embedding_dimensions(query_embedding)?;
    let blob = f32_to_bytes(query_embedding);

    let mut stmt = conn.prepare(
        "SELECT event_id, distance
         FROM vec_events
         WHERE embedding MATCH ?1
         ORDER BY distance
         LIMIT ?2",
    )?;

    let rows = stmt.query_map(params![blob, limit], |row| {
        let id: String = row.get(0)?;
        let distance: f32 = row.get(1)?;
        Ok((id, distance))
    })?;

    let mut results = Vec::new();
    for row in rows {
        results.push(row?);
    }

    Ok(results)
}

#[cfg(test)]
mod tests {
    use super::*;
    use contextd_core::test_utils::test_config_in_memory;

    #[test]
    fn test_vector_insertion_and_search() {
        let conn = crate::db::init_db(&test_config_in_memory()).unwrap();

        let mut embed_1 = vec![0.0f32; EMBEDDING_DIMENSIONS];
        embed_1[0] = 1.0;

        let mut embed_2 = vec![0.0f32; EMBEDDING_DIMENSIONS];
        embed_2[0] = 0.9;
        embed_2[1] = 0.1;

        let mut embed_3 = vec![0.0f32; EMBEDDING_DIMENSIONS];
        embed_3[500] = 1.0;

        insert_embedding(&conn, "event-1", &embed_1).unwrap();
        insert_embedding(&conn, "event-2", &embed_2).unwrap();
        insert_embedding(&conn, "event-3", &embed_3).unwrap();

        let results = search_similar_events(&conn, &embed_1, 2).unwrap();

        assert_eq!(results.len(), 2);
        assert_eq!(results[0].0, "event-1");
        assert_eq!(results[1].0, "event-2");
    }

    #[test]
    fn rejects_wrong_embedding_dimensions() {
        let conn = crate::db::init_db(&test_config_in_memory()).unwrap();
        let short = vec![0.1_f32; 4];

        let err = insert_embedding(&conn, "event-bad", &short).unwrap_err();
        assert!(matches!(
            err,
            rusqlite::Error::InvalidParameterCount(768, 4)
        ));

        let search_err = search_similar_events(&conn, &short, 1).unwrap_err();
        assert!(matches!(
            search_err,
            rusqlite::Error::InvalidParameterCount(768, 4)
        ));
    }
}
