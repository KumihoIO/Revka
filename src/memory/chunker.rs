//! Markdown chunker — stub (removed in Revka).
//!
//! The chunker was used by the Qdrant embedding flow and RAG ingestion.
//! Persistent memory is now handled via Kumiho MCP.
//! A minimal `chunk_markdown` is retained for callers in `rag/mod.rs`.

/// A single chunk of text with metadata.
#[derive(Debug, Clone)]
pub struct Chunk {
    pub index: usize,
    pub content: String,
    pub heading: Option<std::rc::Rc<str>>,
}

/// Split markdown text into chunks — simplified stub.
///
/// Returns the entire text as a single chunk (no splitting).
/// For real chunking, use Kumiho MCP's server-side processing.
pub fn chunk_markdown(text: &str, _max_tokens: usize) -> Vec<Chunk> {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return Vec::new();
    }

    vec![Chunk {
        index: 0,
        content: trimmed.to_string(),
        heading: None,
    }]
}
