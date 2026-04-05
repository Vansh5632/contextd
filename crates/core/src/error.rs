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
