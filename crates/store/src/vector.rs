use rusqlite::{params, Connection, Result};

/// Safely converts an array of f32s into a raw byte vector for SQLite BLOB storage.
fn f32_to_bytes(vec: &[f32]) -> Vec<u8> {
    vec.iter().flat_map(|&f| f.to_ne_bytes()).collect()
}

/// Saves an embedding tied to a specific event ID.
pub fn insert_embedding(conn: &Connection, event_id: &str, embedding: &[f32]) -> Result<()> {
    let blob = f32_to_bytes(embedding);
    
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
    limit: usize
) -> Result<Vec<(String, f32)>> {
    let blob = f32_to_bytes(query_embedding);
    
    // sqlite-vec uses the MATCH syntax for K-Nearest Neighbors (KNN) search
    let mut stmt = conn.prepare(
        "SELECT event_id, distance
         FROM vec_events
         WHERE embedding MATCH ?1
         ORDER BY distance
         LIMIT ?2"
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


// ==========================================
// TESTS
// ==========================================
#[cfg(test)]
mod tests {
    use super::*;
    use contextd_core::test_utils::test_config_in_memory;

    #[test]
    fn test_vector_insertion_and_search() {
        let conn = crate::db::init_db(&test_config_in_memory()).unwrap();

        // Create some dummy 768-dimensional embeddings
        let mut embed_1 = vec![0.0f32; 768];
        embed_1[0] = 1.0; // Make this one point strongly in dimension 0

        let mut embed_2 = vec![0.0f32; 768];
        embed_2[0] = 0.9; // Very similar to embed_1
        embed_2[1] = 0.1;

        let mut embed_3 = vec![0.0f32; 768];
        embed_3[500] = 1.0; // Completely different

        // 2. Act: Insert them
        insert_embedding(&conn, "event-1", &embed_1).unwrap();
        insert_embedding(&conn, "event-2", &embed_2).unwrap();
        insert_embedding(&conn, "event-3", &embed_3).unwrap();

        // 3. Search: Find matches for embed_1
        let results = search_similar_events(&conn, &embed_1, 2).unwrap();

        // 4. Assert
        assert_eq!(results.len(), 2);
        
        // The closest match should be event-1 (distance 0.0)
        assert_eq!(results[0].0, "event-1");
        
        // The second closest should be event-2
        assert_eq!(results[1].0, "event-2");
        
        // It should NOT return event-3 because we limited to 2 results, 
        // and event-3 was mathematically far away.
    }
}