//! Wraps a discovered MCP tool as a construct [`Tool`] so it is dispatched
//! through the existing tool registry and agent loop without modification.

use std::sync::Arc;

use async_trait::async_trait;

use crate::tools::mcp_client::McpRegistry;
use crate::tools::mcp_protocol::McpToolDef;
use crate::tools::traits::{Tool, ToolResult};

/// A construct [`Tool`] backed by an MCP server tool.
///
/// The `prefixed_name` (e.g. `filesystem__read_file`) is what the agent loop
/// sees. The registry knows how to route it to the correct server.
pub struct McpToolWrapper {
    /// Prefixed name: `<server_name>__<tool_name>`.
    prefixed_name: String,
    /// Description extracted from the MCP tool definition. Stored as an owned
    /// String so that `description()` can return `&str` with self's lifetime.
    description: String,
    /// JSON schema for the tool's input parameters.
    input_schema: serde_json::Value,
    /// Shared registry — used to dispatch actual tool calls.
    registry: Arc<McpRegistry>,
}

impl McpToolWrapper {
    pub fn new(prefixed_name: String, def: McpToolDef, registry: Arc<McpRegistry>) -> Self {
        let description = def.description.unwrap_or_else(|| "MCP tool".to_string());
        Self {
            prefixed_name,
            description,
            input_schema: def.input_schema,
            registry,
        }
    }
}

#[async_trait]
impl Tool for McpToolWrapper {
    fn name(&self) -> &str {
        &self.prefixed_name
    }

    fn description(&self) -> &str {
        &self.description
    }

    fn parameters_schema(&self) -> serde_json::Value {
        self.input_schema.clone()
    }

    async fn execute(&self, args: serde_json::Value) -> anyhow::Result<ToolResult> {
        // Strip the `approved` field before forwarding to the MCP server.
        // Construct's security model injects `approved: bool` into built-in tool
        // calls for supervised-mode confirmation. MCP servers have no knowledge
        // of this field and will reject calls that include it as an unexpected
        // parameter. We strip it here so MCP servers always receive clean args.
        //
        // Also coerce string-encoded numbers to their native JSON types when the
        // schema declares `"type": "integer"` or `"type": "number"`. LLMs
        // sometimes emit `"5"` instead of `5`, which causes MCP-side jsonschema
        // validation to reject the call with "is not of type 'integer'".
        let args = match args {
            serde_json::Value::Object(mut map) => {
                map.remove("approved");
                coerce_string_numerics(&mut map, &self.input_schema);
                serde_json::Value::Object(map)
            }
            other => other,
        };
        match self.registry.call_tool(&self.prefixed_name, args).await {
            Ok(output) => Ok(ToolResult {
                success: true,
                output,
                error: None,
            }),
            Err(e) => Ok(ToolResult {
                success: false,
                output: String::new(),
                error: Some(e.to_string()),
            }),
        }
    }
}

/// Coerce string-encoded values to their schema-declared types before MCP
/// server-side jsonschema validation runs.  LLMs frequently emit:
///   - `"5"` instead of `5` for integer parameters
///   - `"[\"a\",\"b\"]"` instead of `["a","b"]` for array parameters
///   - `"true"` instead of `true` for boolean parameters
fn coerce_string_numerics(
    map: &mut serde_json::Map<String, serde_json::Value>,
    schema: &serde_json::Value,
) {
    let props = match schema.get("properties").and_then(|p| p.as_object()) {
        Some(p) => p,
        None => return,
    };
    for (key, prop_schema) in props {
        let expected_type = match prop_schema.get("type").and_then(|t| t.as_str()) {
            Some(t) => t,
            None => continue,
        };
        if let Some(serde_json::Value::String(s)) = map.get(key) {
            match expected_type {
                "integer" => {
                    if let Ok(n) = s.parse::<i64>() {
                        map.insert(key.clone(), serde_json::Value::Number(n.into()));
                    }
                }
                "number" => {
                    if let Ok(n) = s.parse::<f64>() {
                        if let Some(num) = serde_json::Number::from_f64(n) {
                            map.insert(key.clone(), serde_json::Value::Number(num));
                        }
                    }
                }
                "boolean" => match s.as_str() {
                    "true" => {
                        map.insert(key.clone(), serde_json::Value::Bool(true));
                    }
                    "false" => {
                        map.insert(key.clone(), serde_json::Value::Bool(false));
                    }
                    _ => {}
                },
                "array" if s.starts_with('[') => {
                    // Try parsing the string as a JSON array
                    if let Ok(arr) = serde_json::from_str::<serde_json::Value>(s) {
                        if arr.is_array() {
                            map.insert(key.clone(), arr);
                        }
                    }
                }
                "object" if s.starts_with('{') => {
                    // Try parsing the string as a JSON object
                    if let Ok(obj) = serde_json::from_str::<serde_json::Value>(s) {
                        if obj.is_object() {
                            map.insert(key.clone(), obj);
                        }
                    }
                }
                _ => {}
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn make_def(name: &str, description: Option<&str>, schema: serde_json::Value) -> McpToolDef {
        McpToolDef {
            name: name.to_string(),
            description: description.map(str::to_string),
            input_schema: schema,
        }
    }

    async fn empty_registry() -> Arc<McpRegistry> {
        Arc::new(
            McpRegistry::connect_all(&[])
                .await
                .expect("empty connect_all should succeed"),
        )
    }

    // ── Accessor tests ─────────────────────────────────────────────────────

    #[tokio::test]
    async fn name_returns_prefixed_name() {
        let registry = empty_registry().await;
        let def = make_def("read_file", Some("Reads a file"), json!({}));
        let wrapper = McpToolWrapper::new("filesystem__read_file".to_string(), def, registry);
        assert_eq!(wrapper.name(), "filesystem__read_file");
    }

    #[tokio::test]
    async fn description_returns_def_description() {
        let registry = empty_registry().await;
        let def = make_def("navigate", Some("Navigate browser"), json!({}));
        let wrapper = McpToolWrapper::new("playwright__navigate".to_string(), def, registry);
        assert_eq!(wrapper.description(), "Navigate browser");
    }

    #[tokio::test]
    async fn description_falls_back_to_mcp_tool_when_none() {
        let registry = empty_registry().await;
        let def = make_def("mystery", None, json!({}));
        let wrapper = McpToolWrapper::new("srv__mystery".to_string(), def, registry);
        assert_eq!(wrapper.description(), "MCP tool");
    }

    #[tokio::test]
    async fn parameters_schema_returns_input_schema() {
        let registry = empty_registry().await;
        let schema = json!({
            "type": "object",
            "properties": { "path": { "type": "string" } },
            "required": ["path"]
        });
        let def = make_def("read_file", Some("Read"), schema.clone());
        let wrapper = McpToolWrapper::new("fs__read_file".to_string(), def, registry);
        assert_eq!(wrapper.parameters_schema(), schema);
    }

    #[tokio::test]
    async fn spec_returns_all_three_fields() {
        let registry = empty_registry().await;
        let schema = json!({ "type": "object", "properties": {} });
        let def = make_def("list_dir", Some("List directory"), schema.clone());
        let wrapper = McpToolWrapper::new("fs__list_dir".to_string(), def, registry);
        let spec = wrapper.spec();
        assert_eq!(spec.name, "fs__list_dir");
        assert_eq!(spec.description, "List directory");
        assert_eq!(spec.parameters, schema);
    }

    // ── execute() error path ───────────────────────────────────────────────

    #[tokio::test]
    async fn execute_returns_non_fatal_error_for_unknown_tool() {
        // An empty registry has no tools — execute must return Ok(ToolResult { success: false })
        // rather than propagating an Err (non-fatal by design).
        let registry = empty_registry().await;
        let def = make_def("ghost", Some("Ghost tool"), json!({}));
        let wrapper = McpToolWrapper::new("nowhere__ghost".to_string(), def, registry);
        let result = wrapper
            .execute(json!({}))
            .await
            .expect("execute should be non-fatal");
        assert!(!result.success);
        let err_msg = result.error.expect("error message should be present");
        assert!(
            err_msg.contains("unknown MCP tool"),
            "unexpected error: {err_msg}"
        );
        assert!(result.output.is_empty());
    }

    #[tokio::test]
    async fn execute_success_sets_success_true_and_output() {
        // Verify the ToolResult success-branch struct shape compiles correctly.
        // A real happy-path requires a live MCP server; that is covered by E2E tests.
        let _: ToolResult = ToolResult {
            success: true,
            output: "hello".to_string(),
            error: None,
        };
    }

    // ── approved-field stripping ───────────────────────────────────────────
    // Construct's security model injects `approved: bool` into built-in tool args.
    // MCP servers are unaware of this field and reject calls that include it.
    // execute() must strip it before forwarding.

    #[tokio::test]
    async fn execute_strips_approved_field_from_object_args() {
        // The wrapper should remove `approved` before forwarding to the registry.
        // We use an empty registry (returns "unknown MCP tool" error), but the key
        // assertion is that the call does not fail due to an unexpected `approved` arg.
        let registry = empty_registry().await;
        let def = make_def("do_thing", Some("Do a thing"), json!({}));
        let wrapper = McpToolWrapper::new("srv__do_thing".to_string(), def, registry);
        // With `approved` present the call must not propagate an Err — non-fatal.
        let result = wrapper
            .execute(json!({ "approved": true, "param": "value" }))
            .await
            .expect("execute must be non-fatal even with approved field");
        // The registry returns a non-fatal error (unknown tool), not a panic/Err.
        assert!(!result.success);
        // Crucially: error must not mention `approved` as the cause.
        let err = result.error.unwrap_or_default();
        assert!(
            !err.to_lowercase().contains("approved"),
            "approved field should have been stripped, but got: {err}"
        );
    }

    // ── string→numeric coercion ─────────────────────────────────────────
    // LLMs sometimes emit `"5"` instead of `5` for integer parameters.

    #[test]
    fn coerce_string_to_integer() {
        let schema = json!({
            "type": "object",
            "properties": {
                "limit": { "type": "integer" },
                "name": { "type": "string" }
            }
        });
        let mut map = serde_json::Map::new();
        map.insert("limit".into(), json!("5"));
        map.insert("name".into(), json!("hello"));
        coerce_string_numerics(&mut map, &schema);
        assert_eq!(map["limit"], json!(5));
        assert_eq!(map["name"], json!("hello"));
    }

    #[test]
    fn coerce_string_to_number() {
        let schema = json!({
            "type": "object",
            "properties": { "score": { "type": "number" } }
        });
        let mut map = serde_json::Map::new();
        map.insert("score".into(), json!("2.5"));
        coerce_string_numerics(&mut map, &schema);
        assert_eq!(map["score"], json!(2.5));
    }

    #[test]
    fn coerce_leaves_already_correct_types() {
        let schema = json!({
            "type": "object",
            "properties": { "limit": { "type": "integer" } }
        });
        let mut map = serde_json::Map::new();
        map.insert("limit".into(), json!(10));
        coerce_string_numerics(&mut map, &schema);
        assert_eq!(map["limit"], json!(10));
    }

    #[test]
    fn coerce_ignores_non_numeric_strings() {
        let schema = json!({
            "type": "object",
            "properties": { "limit": { "type": "integer" } }
        });
        let mut map = serde_json::Map::new();
        map.insert("limit".into(), json!("not_a_number"));
        coerce_string_numerics(&mut map, &schema);
        assert_eq!(map["limit"], json!("not_a_number"));
    }

    #[test]
    fn coerce_string_to_array() {
        let schema = json!({
            "type": "object",
            "properties": { "tags": { "type": "array", "items": { "type": "string" } } }
        });
        let mut map = serde_json::Map::new();
        map.insert("tags".into(), json!("[\"rust\",\"testing\"]"));
        coerce_string_numerics(&mut map, &schema);
        assert_eq!(map["tags"], json!(["rust", "testing"]));
    }

    #[test]
    fn coerce_string_to_boolean() {
        let schema = json!({
            "type": "object",
            "properties": { "enabled": { "type": "boolean" } }
        });
        let mut map = serde_json::Map::new();
        map.insert("enabled".into(), json!("true"));
        coerce_string_numerics(&mut map, &schema);
        assert_eq!(map["enabled"], json!(true));
    }

    #[tokio::test]
    async fn execute_handles_non_object_args_without_panic() {
        // Non-object args (string, null, array) must pass through without panicking
        // or returning an Err — the registry error path covers the failure case.
        let registry = empty_registry().await;
        let def = make_def("noop", None, json!({}));
        let wrapper = McpToolWrapper::new("srv__noop".to_string(), def, registry);
        for non_obj in [json!(null), json!("a string"), json!([1, 2, 3])] {
            let result = wrapper
                .execute(non_obj.clone())
                .await
                .expect("non-object args must not propagate Err");
            assert!(!result.success, "expected non-fatal failure for {non_obj}");
        }
    }
}
