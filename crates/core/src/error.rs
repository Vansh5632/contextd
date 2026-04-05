use thiserror::Error;

#[derive(Error, Debug)]

pub enum ContextdError {
    #[error("Serialization error: {0}")]
    Serialization(#[from] serde_json::Error),

    #[error("Database error: {0}")]
    Database(String),

    #[error("Unknown internal error")]
    Unknown,
}

#[cfg(test)]
mod tests {
    use super::ContextdError;

    #[test]
    fn converts_from_serde_json_error() {
        let parse_err = serde_json::from_str::<serde_json::Value>("{not-json}")
            .expect_err("invalid JSON should fail");
        let err: ContextdError = parse_err.into();

        match err {
            ContextdError::Serialization(_) => {}
            _ => panic!("expected serialization variant"),
        }
    }

    #[test]
    fn database_error_has_expected_display() {
        let err = ContextdError::Database("db offline".to_string());
        assert_eq!(err.to_string(), "Database error: db offline");
    }
}
