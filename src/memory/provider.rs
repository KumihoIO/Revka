//! Turn-level cognitive memory contract.
//!
//! This is intentionally separate from [`super::traits::Memory`], which is a
//! low-level key/value-style storage abstraction.  `MemoryProvider` models the
//! four high-level Kumiho memory reflexes used around an agent turn.  The first
//! implementation delegates all four operations to the Python
//! `kumiho-memory` MCP server so introducing the boundary does not change
//! runtime semantics.

use std::collections::BTreeMap;
use std::sync::Arc;

use anyhow::{Context, Result, anyhow, bail};
use async_trait::async_trait;
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use serde_json::Value;

const DEFAULT_KUMIHO_MCP_SERVER: &str = "kumiho-memory";
const ENGAGE_TOOL: &str = "kumiho_memory_engage";
const REFLECT_TOOL: &str = "kumiho_memory_reflect";
const RECALL_TOOL: &str = "kumiho_memory_recall";
const CONSOLIDATE_TOOL: &str = "kumiho_memory_consolidate";

/// Transport boundary used by [`PythonMemoryProvider`].
///
/// Revka's MCP registry implements this trait in `tools::mcp_client`, keeping
/// the memory contract independent from the tool subsystem and making the
/// provider deterministic to test.
#[async_trait]
pub trait MemoryProviderTransport: Send + Sync {
    async fn call_memory_tool(&self, tool_name: &str, arguments: Value) -> Result<String>;
}

/// Turn-level cognitive memory operations.
///
/// `reflect` and `consolidate` deliberately remain part of the contract even
/// though their reference implementation stays in Python.  A later hybrid
/// provider can replace deterministic reads while delegating cognitive writes.
#[async_trait]
pub trait MemoryProvider: Send + Sync {
    async fn engage(&self, request: EngageRequest) -> Result<MemoryContext>;
    async fn reflect(&self, request: ReflectRequest) -> Result<ReflectOutcome>;
    async fn recall(&self, request: RecallRequest) -> Result<RecallOutcome>;
    async fn consolidate(&self, request: ConsolidateRequest) -> Result<ConsolidateOutcome>;
}

/// Context rendering mode accepted by kumiho-memory 0.20.x.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RecallMode {
    Full,
    Summarized,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct EngageRequest {
    pub query: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub limit: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub min_score: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub space_paths: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub memory_types: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub graph_augmented: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub recall_mode: Option<RecallMode>,
}

impl EngageRequest {
    pub fn new(query: impl Into<String>) -> Self {
        Self {
            query: query.into(),
            limit: None,
            min_score: None,
            space_paths: None,
            memory_types: None,
            graph_augmented: None,
            recall_mode: None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct RecallRequest {
    pub query: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub limit: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub min_score: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub space_paths: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub memory_types: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub graph_augmented: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub recall_mode: Option<RecallMode>,
}

impl RecallRequest {
    pub fn new(query: impl Into<String>) -> Self {
        Self {
            query: query.into(),
            limit: None,
            min_score: None,
            space_paths: None,
            memory_types: None,
            graph_augmented: None,
            recall_mode: None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct MemoryCapture {
    #[serde(rename = "type")]
    pub memory_type: String,
    pub title: String,
    pub content: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tags: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub space_hint: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub event_date: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ReflectRequest {
    pub session_id: String,
    pub response: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub captures: Option<Vec<MemoryCapture>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub source_krefs: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub space_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub discover_edges: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub idempotency_prefix: Option<String>,
}

impl ReflectRequest {
    pub fn new(session_id: impl Into<String>, response: impl Into<String>) -> Self {
        Self {
            session_id: session_id.into(),
            response: response.into(),
            captures: None,
            source_krefs: None,
            space_path: None,
            discover_edges: None,
            idempotency_prefix: None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ConsolidateRequest {
    pub session_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub evidence_level: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub source: Option<String>,
}

impl ConsolidateRequest {
    pub fn new(session_id: impl Into<String>) -> Self {
        Self {
            session_id: session_id.into(),
            evidence_level: None,
            source: None,
        }
    }
}

/// A recalled memory row.  Stable, commonly consumed fields are typed while
/// additive kumiho-memory fields are retained in `extra` for version drift.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct MemoryRecord {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub kref: Option<String>,
    #[serde(
        default,
        rename = "_item_kref",
        skip_serializing_if = "Option::is_none"
    )]
    pub item_kref: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub summary: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub content: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub score: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub created_at: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub event_date: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub memory_type: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sibling_revisions: Option<Vec<Value>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub metadata: Option<BTreeMap<String, Value>>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct MemoryContext {
    #[serde(default)]
    pub context: String,
    #[serde(default)]
    pub results: Vec<MemoryRecord>,
    #[serde(default)]
    pub source_krefs: Vec<String>,
    #[serde(default)]
    pub count: usize,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub recall_mode: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub approx_tokens: Option<usize>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub backend_error: Option<String>,
    #[serde(default)]
    pub deduplicated: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct RecallOutcome {
    #[serde(default)]
    pub results: Vec<MemoryRecord>,
    #[serde(default)]
    pub count: usize,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub recall_mode: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub backend_error: Option<String>,
    #[serde(default)]
    pub deduplicated: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct ReflectOutcome {
    #[serde(default)]
    pub buffered: bool,
    #[serde(default)]
    pub captures_stored: usize,
    #[serde(default)]
    pub edges_discovered: usize,
    #[serde(default)]
    pub stored_krefs: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub capture_results: Option<Vec<Value>>,
    #[serde(default)]
    pub dropped_event_dates: Vec<DroppedEventDate>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct DroppedEventDate {
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub event_date: String,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct ConsolidateOutcome {
    #[serde(default)]
    pub success: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub summary: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub store_result: Option<Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

/// Python-backed reference implementation of [`MemoryProvider`].
#[derive(Clone)]
pub struct PythonMemoryProvider {
    transport: Arc<dyn MemoryProviderTransport>,
    server_name: String,
}

impl PythonMemoryProvider {
    pub fn new(transport: Arc<dyn MemoryProviderTransport>) -> Self {
        Self::with_server_name(transport, DEFAULT_KUMIHO_MCP_SERVER)
    }

    pub fn with_server_name(
        transport: Arc<dyn MemoryProviderTransport>,
        server_name: impl Into<String>,
    ) -> Self {
        Self {
            transport,
            server_name: server_name.into(),
        }
    }

    async fn invoke<T: DeserializeOwned, R: Serialize>(
        &self,
        tool: &str,
        request: &R,
        reject_payload_error: bool,
    ) -> Result<T> {
        let arguments = serde_json::to_value(request)
            .with_context(|| format!("failed to encode `{tool}` request"))?;
        let prefixed_tool = format!("{}__{tool}", self.server_name);
        let raw = self
            .transport
            .call_memory_tool(&prefixed_tool, arguments)
            .await
            .with_context(|| format!("Python memory provider call `{tool}` failed"))?;
        let payload = decode_mcp_payload(&raw, tool)?;
        if reject_payload_error {
            reject_payload_error_field(&payload, tool)?;
        }
        serde_json::from_value(payload)
            .with_context(|| format!("failed to decode `{tool}` response payload"))
    }
}

#[async_trait]
impl MemoryProvider for PythonMemoryProvider {
    async fn engage(&self, request: EngageRequest) -> Result<MemoryContext> {
        self.invoke(ENGAGE_TOOL, &request, true).await
    }

    async fn reflect(&self, request: ReflectRequest) -> Result<ReflectOutcome> {
        self.invoke(REFLECT_TOOL, &request, true).await
    }

    async fn recall(&self, request: RecallRequest) -> Result<RecallOutcome> {
        self.invoke(RECALL_TOOL, &request, true).await
    }

    async fn consolidate(&self, request: ConsolidateRequest) -> Result<ConsolidateOutcome> {
        // `success: false, error: ...` is a normal consolidation outcome (for
        // example, an empty session), not a transport/protocol failure.
        self.invoke(CONSOLIDATE_TOOL, &request, false).await
    }
}

fn decode_mcp_payload(raw: &str, tool: &str) -> Result<Value> {
    let outer: Value = serde_json::from_str(raw)
        .with_context(|| format!("`{tool}` returned malformed outer JSON"))?;

    if outer.get("isError").and_then(Value::as_bool) == Some(true) {
        let detail = first_text_block(&outer).unwrap_or("MCP server reported an error");
        bail!("`{tool}` failed: {detail}");
    }

    if let Some(structured) = outer
        .get("structuredContent")
        .filter(|value| !value.is_null())
    {
        return Ok(structured.clone());
    }

    if outer.get("content").is_some() {
        let text = first_text_block(&outer)
            .ok_or_else(|| anyhow!("`{tool}` MCP envelope has no text content block"))?;
        return serde_json::from_str(text)
            .with_context(|| format!("`{tool}` returned malformed JSON in MCP text content"));
    }

    // Tests and non-MCP transports may return the payload directly.
    Ok(outer)
}

fn first_text_block(value: &Value) -> Option<&str> {
    value
        .get("content")?
        .as_array()?
        .iter()
        .find_map(|block| block.get("text").and_then(Value::as_str))
}

fn reject_payload_error_field(payload: &Value, tool: &str) -> Result<()> {
    let Some(error) = payload.get("error").filter(|value| !value.is_null()) else {
        return Ok(());
    };
    let detail = error
        .as_str()
        .map(ToOwned::to_owned)
        .unwrap_or_else(|| error.to_string());
    bail!("`{tool}` returned an error payload: {detail}")
}

#[cfg(test)]
mod tests {
    use std::sync::Mutex;

    use super::*;
    use serde_json::json;

    #[derive(Default)]
    struct RecordingTransport {
        responses: BTreeMap<String, String>,
        calls: Mutex<Vec<(String, Value)>>,
    }

    impl RecordingTransport {
        fn with_responses(responses: impl IntoIterator<Item = (&'static str, Value)>) -> Self {
            Self {
                responses: responses
                    .into_iter()
                    .map(|(tool, value)| (tool.to_owned(), value.to_string()))
                    .collect(),
                calls: Mutex::new(Vec::new()),
            }
        }

        fn calls(&self) -> Vec<(String, Value)> {
            self.calls.lock().expect("calls lock poisoned").clone()
        }
    }

    #[async_trait]
    impl MemoryProviderTransport for RecordingTransport {
        async fn call_memory_tool(&self, tool_name: &str, arguments: Value) -> Result<String> {
            self.calls
                .lock()
                .expect("calls lock poisoned")
                .push((tool_name.to_owned(), arguments));
            self.responses
                .get(tool_name)
                .cloned()
                .ok_or_else(|| anyhow!("no response configured for {tool_name}"))
        }
    }

    #[tokio::test]
    async fn engage_routes_exact_request_and_decodes_text_envelope() {
        let transport = Arc::new(RecordingTransport::with_responses([(
            "kumiho-memory__kumiho_memory_engage",
            json!({
                "content": [{
                    "type": "text",
                    "text": json!({
                        "context": "remember Rust",
                        "results": [{
                            "kref": "kref://CognitiveMemory/Facts/rust?r=1",
                            "_item_kref": "kref://CognitiveMemory/Facts/rust",
                            "title": "Language preference",
                            "score": 0.97,
                            "future_badge": "retained"
                        }],
                        "source_krefs": ["kref://CognitiveMemory/Facts/rust?r=1"],
                        "count": 1,
                        "recall_mode": "summarized",
                        "approx_tokens": 4,
                        "future_top_level": true
                    })
                    .to_string()
                }],
                "isError": false
            }),
        )]));
        let provider = PythonMemoryProvider::new(transport.clone());
        let request = EngageRequest {
            query: "preferred language".into(),
            limit: Some(3),
            min_score: Some(0.25),
            space_paths: Some(vec!["CognitiveMemory".into()]),
            memory_types: Some(vec!["preference".into()]),
            graph_augmented: Some(false),
            recall_mode: Some(RecallMode::Summarized),
        };

        let outcome = provider.engage(request).await.unwrap();

        assert_eq!(outcome.context, "remember Rust");
        assert_eq!(
            outcome.results[0].item_kref.as_deref(),
            Some("kref://CognitiveMemory/Facts/rust")
        );
        assert_eq!(outcome.results[0].extra["future_badge"], "retained");
        assert_eq!(outcome.extra["future_top_level"], true);
        assert_eq!(
            transport.calls(),
            vec![(
                "kumiho-memory__kumiho_memory_engage".into(),
                json!({
                    "query": "preferred language",
                    "limit": 3,
                    "min_score": 0.25,
                    "space_paths": ["CognitiveMemory"],
                    "memory_types": ["preference"],
                    "graph_augmented": false,
                    "recall_mode": "summarized"
                })
            )]
        );
    }

    #[tokio::test]
    async fn recall_accepts_direct_payload_and_omits_unspecified_defaults() {
        let transport = Arc::new(RecordingTransport::with_responses([(
            "custom__kumiho_memory_recall",
            json!({"results": [], "count": 0, "new_field": [1, 2, 3]}),
        )]));
        let provider = PythonMemoryProvider::with_server_name(transport.clone(), "custom");

        let outcome = provider.recall(RecallRequest::new("query")).await.unwrap();

        assert_eq!(outcome.count, 0);
        assert_eq!(outcome.extra["new_field"], json!([1, 2, 3]));
        assert_eq!(
            transport.calls(),
            vec![(
                "custom__kumiho_memory_recall".into(),
                json!({"query": "query"})
            )]
        );
    }

    #[tokio::test]
    async fn reflect_routes_python_write_and_decodes_structured_content() {
        let transport = Arc::new(RecordingTransport::with_responses([(
            "kumiho-memory__kumiho_memory_reflect",
            json!({
                "structuredContent": {
                    "buffered": true,
                    "captures_stored": 1,
                    "edges_discovered": 2,
                    "stored_krefs": ["kref://CognitiveMemory/Decisions/sdk?r=1"],
                    "capture_results": [{"revision_kref": "kref://CognitiveMemory/Decisions/sdk?r=1"}]
                }
            }),
        )]));
        let provider = PythonMemoryProvider::new(transport.clone());
        let mut request = ReflectRequest::new("session-1", "Use the Rust SDK first.");
        request.captures = Some(vec![MemoryCapture {
            memory_type: "decision".into(),
            title: "Use Rust SDK on Aug 31".into(),
            content: "Connect Revka to the Rust SDK before porting memory.".into(),
            tags: Some(vec!["architecture".into()]),
            space_hint: Some("CognitiveMemory/Decisions".into()),
            event_date: Some("2026-08-31".into()),
        }]);
        request.source_krefs = Some(vec!["kref://CognitiveMemory/Plans/revka?r=2".into()]);
        request.discover_edges = Some(true);
        request.idempotency_prefix = Some("turn-42".into());

        let outcome = provider.reflect(request).await.unwrap();

        assert!(outcome.buffered);
        assert_eq!(outcome.captures_stored, 1);
        assert_eq!(outcome.edges_discovered, 2);
        assert_eq!(
            transport.calls(),
            vec![(
                "kumiho-memory__kumiho_memory_reflect".into(),
                json!({
                    "session_id": "session-1",
                    "response": "Use the Rust SDK first.",
                    "captures": [{
                        "type": "decision",
                        "title": "Use Rust SDK on Aug 31",
                        "content": "Connect Revka to the Rust SDK before porting memory.",
                        "tags": ["architecture"],
                        "space_hint": "CognitiveMemory/Decisions",
                        "event_date": "2026-08-31"
                    }],
                    "source_krefs": ["kref://CognitiveMemory/Plans/revka?r=2"],
                    "discover_edges": true,
                    "idempotency_prefix": "turn-42"
                })
            )]
        );
    }

    #[tokio::test]
    async fn consolidate_keeps_python_failure_as_typed_outcome() {
        let transport = Arc::new(RecordingTransport::with_responses([(
            "kumiho-memory__kumiho_memory_consolidate",
            json!({
                "content": [{
                    "type": "text",
                    "text": "{\"success\":false,\"error\":\"No messages to consolidate\"}"
                }]
            }),
        )]));
        let provider = PythonMemoryProvider::new(transport.clone());
        let mut request = ConsolidateRequest::new("session-empty");
        request.evidence_level = Some("official".into());
        request.source = Some("chat:user".into());

        let outcome = provider.consolidate(request).await.unwrap();

        assert!(!outcome.success);
        assert_eq!(outcome.error.as_deref(), Some("No messages to consolidate"));
        assert_eq!(
            transport.calls(),
            vec![(
                "kumiho-memory__kumiho_memory_consolidate".into(),
                json!({
                    "session_id": "session-empty",
                    "evidence_level": "official",
                    "source": "chat:user"
                })
            )]
        );
    }

    #[tokio::test]
    async fn mcp_error_envelope_is_rejected() {
        let transport = Arc::new(RecordingTransport::with_responses([(
            "kumiho-memory__kumiho_memory_engage",
            json!({
                "content": [{"type": "text", "text": "backend unavailable"}],
                "isError": true
            }),
        )]));
        let provider = PythonMemoryProvider::new(transport);

        let error = provider
            .engage(EngageRequest::new("query"))
            .await
            .unwrap_err();

        assert!(error.to_string().contains("backend unavailable"));
    }

    #[tokio::test]
    async fn read_error_payload_and_malformed_inner_json_are_rejected() {
        let error_transport = Arc::new(RecordingTransport::with_responses([(
            "kumiho-memory__kumiho_memory_recall",
            json!({"results": [], "error": "search failed"}),
        )]));
        let error_provider = PythonMemoryProvider::new(error_transport);
        let error = error_provider
            .recall(RecallRequest::new("query"))
            .await
            .unwrap_err();
        assert!(error.to_string().contains("search failed"));

        let malformed_transport = Arc::new(RecordingTransport::with_responses([(
            "kumiho-memory__kumiho_memory_engage",
            json!({"content": [{"type": "text", "text": "not-json"}]}),
        )]));
        let malformed_provider = PythonMemoryProvider::new(malformed_transport);
        let error = malformed_provider
            .engage(EngageRequest::new("query"))
            .await
            .unwrap_err();
        assert!(error.to_string().contains("malformed JSON"));
    }
}
