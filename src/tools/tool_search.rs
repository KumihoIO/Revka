//! Built-in `tool_search` tool for on-demand MCP tool schema loading.
//!
//! When `mcp.deferred_loading` is enabled, this tool lets the LLM discover and
//! activate deferred MCP tools. Supports two query modes:
//! - `select:name1,name2` — fetch exact tools by prefixed name.
//! - Free-text keyword search — returns the best-matching stubs.

use std::fmt::Write;
use std::sync::{Arc, Mutex};

use async_trait::async_trait;

use crate::tools::mcp_deferred::{ActivatedToolSet, DeferredMcpToolSet};
use crate::tools::traits::{Tool, ToolResult, ToolSpec};

/// Default maximum number of search results.
const DEFAULT_MAX_RESULTS: usize = 5;

/// Serialize a tool spec into a `<function>{...}</function>` entry.
///
/// Uses `serde_json` so that the free-form `name` and `description` fields are
/// escaped correctly — backslashes (Windows paths, regex hints), newlines,
/// tabs, and control characters all produce valid JSON, which hand-rolled
/// quote-only escaping did not.
fn write_function_entry(output: &mut String, spec: &ToolSpec) {
    let entry = serde_json::json!({
        "name": spec.name,
        "description": spec.description,
        "parameters": spec.parameters,
    });
    let _ = writeln!(
        output,
        "<function>{}</function>",
        serde_json::to_string(&entry).unwrap_or_default()
    );
}

/// Built-in tool that fetches full schemas for deferred MCP tools.
pub struct ToolSearchTool {
    deferred: DeferredMcpToolSet,
    activated: Arc<Mutex<ActivatedToolSet>>,
    /// Optional per-run capability allowlist. When `Some(list)`, only deferred
    /// tools whose prefixed name appears in the list may be searched, selected,
    /// or activated — so a run scoped down via `allowed_tools` cannot reach
    /// arbitrary MCP tools through `tool_search`. When `None`, all deferred
    /// tools are reachable (backward compatible).
    allowed_tools: Option<Vec<String>>,
}

impl ToolSearchTool {
    pub fn new(deferred: DeferredMcpToolSet, activated: Arc<Mutex<ActivatedToolSet>>) -> Self {
        Self {
            deferred,
            activated,
            allowed_tools: None,
        }
    }

    /// Restrict which deferred tools this `tool_search` may activate to the
    /// given capability allowlist. Pass the same `allowed_tools` that was used
    /// to filter the static registry so MCP exposure is bounded by it too.
    #[must_use]
    pub fn with_allowed_tools(mut self, allowed_tools: Option<Vec<String>>) -> Self {
        self.allowed_tools = allowed_tools;
        self
    }

    /// Whether `prefixed_name` is permitted by the capability allowlist.
    /// Returns `true` when no allowlist is configured.
    fn is_allowed(&self, prefixed_name: &str) -> bool {
        match &self.allowed_tools {
            None => true,
            Some(list) => list.iter().any(|name| name == prefixed_name),
        }
    }
}

#[async_trait]
impl Tool for ToolSearchTool {
    fn name(&self) -> &str {
        "tool_search"
    }

    fn description(&self) -> &str {
        "Fetch full schema definitions for deferred MCP tools so they can be called. \
         Use \"select:name1,name2\" for exact match or keywords to search."
    }

    fn parameters_schema(&self) -> serde_json::Value {
        serde_json::json!({
            "type": "object",
            "properties": {
                "query": {
                    "description": "Query to find deferred tools. Use \"select:<tool_name>\" for direct selection, or keywords to search.",
                    "type": "string"
                },
                "max_results": {
                    "description": "Maximum number of results to return (default: 5)",
                    "type": "number",
                    "default": DEFAULT_MAX_RESULTS
                }
            },
            "required": ["query"]
        })
    }

    async fn execute(&self, args: serde_json::Value) -> anyhow::Result<ToolResult> {
        let query = args
            .get("query")
            .and_then(|v| v.as_str())
            .unwrap_or_default()
            .trim();

        let max_results = args
            .get("max_results")
            .and_then(|v| v.as_u64())
            .map(|v| usize::try_from(v).unwrap_or(DEFAULT_MAX_RESULTS))
            .unwrap_or(DEFAULT_MAX_RESULTS);

        if query.is_empty() {
            return Ok(ToolResult {
                success: false,
                output: String::new(),
                error: Some("query parameter is required".into()),
            });
        }

        // Parse query mode
        if let Some(names_str) = query.strip_prefix("select:") {
            // Exact selection mode
            let names: Vec<&str> = names_str.split(',').map(str::trim).collect();
            return self.select_tools(&names);
        }

        // Keyword search mode
        let results: Vec<_> = self
            .deferred
            .search(query, max_results)
            .into_iter()
            .filter(|stub| self.is_allowed(&stub.prefixed_name))
            .collect();
        if results.is_empty() {
            return Ok(ToolResult {
                success: true,
                output: "No matching deferred tools found.".into(),
                error: None,
            });
        }

        // Activate and return full specs
        let mut output = String::from("<functions>\n");
        let mut activated_count = 0;
        // Recover from a poisoned lock so a prior panic under the guard does not
        // permanently break deferred-tool activation.
        let mut guard = self
            .activated
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);

        for stub in &results {
            if let Some(spec) = self.deferred.tool_spec(&stub.prefixed_name) {
                if !guard.is_activated(&stub.prefixed_name) {
                    if let Some(tool) = self.deferred.activate(&stub.prefixed_name) {
                        guard.activate(stub.prefixed_name.clone(), Arc::from(tool));
                        activated_count += 1;
                    }
                }
                write_function_entry(&mut output, &spec);
            }
        }

        output.push_str("</functions>\n");
        drop(guard);

        tracing::debug!(
            "tool_search: query={query:?}, matched={}, activated={activated_count}",
            results.len()
        );

        Ok(ToolResult {
            success: true,
            output,
            error: None,
        })
    }
}

impl ToolSearchTool {
    fn select_tools(&self, names: &[&str]) -> anyhow::Result<ToolResult> {
        let mut output = String::from("<functions>\n");
        let mut not_found = Vec::new();
        let mut activated_count = 0;
        // Recover from a poisoned lock so a prior panic under the guard does not
        // permanently break deferred-tool activation.
        let mut guard = self
            .activated
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);

        for name in names {
            if name.is_empty() {
                continue;
            }
            // get_by_name handles both exact and suffix-resolved lookups.
            match self.deferred.get_by_name(name) {
                Some(stub) if !self.is_allowed(&stub.prefixed_name) => {
                    // Outside the capability allowlist — treat as unavailable.
                    not_found.push(*name);
                }
                Some(stub) => {
                    let full_name = &stub.prefixed_name;
                    if let Some(spec) = self.deferred.tool_spec(full_name) {
                        if !guard.is_activated(full_name) {
                            if let Some(tool) = self.deferred.activate(full_name) {
                                guard.activate(full_name.clone(), Arc::from(tool));
                                activated_count += 1;
                            }
                        }
                        write_function_entry(&mut output, &spec);
                    }
                }
                None => {
                    not_found.push(*name);
                }
            }
        }

        output.push_str("</functions>\n");
        drop(guard);

        if !not_found.is_empty() {
            let _ = write!(output, "\nNot found: {}", not_found.join(", "));
        }

        tracing::debug!(
            "tool_search select: requested={}, activated={activated_count}, not_found={}",
            names.len(),
            not_found.len()
        );

        Ok(ToolResult {
            success: true,
            output,
            error: None,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tools::mcp_client::McpRegistry;
    use crate::tools::mcp_deferred::DeferredMcpToolStub;
    use crate::tools::mcp_protocol::McpToolDef;

    async fn make_deferred_set(stubs: Vec<DeferredMcpToolStub>) -> DeferredMcpToolSet {
        let registry = Arc::new(McpRegistry::connect_all(&[]).await.unwrap());
        DeferredMcpToolSet { stubs, registry }
    }

    fn make_stub(name: &str, desc: &str) -> DeferredMcpToolStub {
        let def = McpToolDef {
            name: name.to_string(),
            description: Some(desc.to_string()),
            input_schema: serde_json::json!({"type": "object", "properties": {}}),
        };
        DeferredMcpToolStub::new(name.to_string(), def)
    }

    #[tokio::test]
    async fn tool_metadata() {
        let tool = ToolSearchTool::new(
            make_deferred_set(vec![]).await,
            Arc::new(Mutex::new(ActivatedToolSet::new())),
        );
        assert_eq!(tool.name(), "tool_search");
        assert!(!tool.description().is_empty());
        assert!(tool.parameters_schema()["properties"]["query"].is_object());
    }

    #[tokio::test]
    async fn empty_query_returns_error() {
        let tool = ToolSearchTool::new(
            make_deferred_set(vec![]).await,
            Arc::new(Mutex::new(ActivatedToolSet::new())),
        );
        let result = tool
            .execute(serde_json::json!({"query": ""}))
            .await
            .unwrap();
        assert!(!result.success);
    }

    #[tokio::test]
    async fn select_nonexistent_tool_reports_not_found() {
        let tool = ToolSearchTool::new(
            make_deferred_set(vec![]).await,
            Arc::new(Mutex::new(ActivatedToolSet::new())),
        );
        let result = tool
            .execute(serde_json::json!({"query": "select:nonexistent"}))
            .await
            .unwrap();
        assert!(result.success);
        assert!(result.output.contains("Not found"));
    }

    #[tokio::test]
    async fn keyword_search_no_matches() {
        let tool = ToolSearchTool::new(
            make_deferred_set(vec![make_stub("fs__read", "Read file")]).await,
            Arc::new(Mutex::new(ActivatedToolSet::new())),
        );
        let result = tool
            .execute(serde_json::json!({"query": "zzzzz_nonexistent"}))
            .await
            .unwrap();
        assert!(result.success);
        assert!(result.output.contains("No matching"));
    }

    #[tokio::test]
    async fn keyword_search_finds_match() {
        let activated = Arc::new(Mutex::new(ActivatedToolSet::new()));
        let tool = ToolSearchTool::new(
            make_deferred_set(vec![make_stub("fs__read", "Read a file from disk")]).await,
            Arc::clone(&activated),
        );
        let result = tool
            .execute(serde_json::json!({"query": "read file"}))
            .await
            .unwrap();
        assert!(result.success);
        assert!(result.output.contains("<function>"));
        assert!(result.output.contains("fs__read"));
        // Tool should now be activated
        assert!(activated.lock().unwrap().is_activated("fs__read"));
    }

    /// Verify tool_search works with stubs from multiple MCP servers,
    /// simulating a daemon-mode setup where several servers are deferred.
    #[tokio::test]
    async fn multiple_servers_stubs_all_searchable() {
        let activated = Arc::new(Mutex::new(ActivatedToolSet::new()));
        let stubs = vec![
            make_stub("server_a__list_files", "List files on server A"),
            make_stub("server_a__read_file", "Read file on server A"),
            make_stub("server_b__query_db", "Query database on server B"),
            make_stub("server_b__insert_row", "Insert row on server B"),
        ];
        let tool = ToolSearchTool::new(make_deferred_set(stubs).await, Arc::clone(&activated));

        // Search should find tools across both servers
        let result = tool
            .execute(serde_json::json!({"query": "file"}))
            .await
            .unwrap();
        assert!(result.success);
        assert!(result.output.contains("server_a__list_files"));
        assert!(result.output.contains("server_a__read_file"));

        // Server B tools should also be searchable
        let result = tool
            .execute(serde_json::json!({"query": "database query"}))
            .await
            .unwrap();
        assert!(result.success);
        assert!(result.output.contains("server_b__query_db"));
    }

    /// Verify select mode activates tools and they stay activated across calls,
    /// matching the daemon-mode pattern where a single ActivatedToolSet persists.
    #[tokio::test]
    async fn select_activates_and_persists_across_calls() {
        let activated = Arc::new(Mutex::new(ActivatedToolSet::new()));
        let stubs = vec![
            make_stub("srv__tool_a", "Tool A"),
            make_stub("srv__tool_b", "Tool B"),
        ];
        let tool = ToolSearchTool::new(make_deferred_set(stubs).await, Arc::clone(&activated));

        // Activate tool_a
        let result = tool
            .execute(serde_json::json!({"query": "select:srv__tool_a"}))
            .await
            .unwrap();
        assert!(result.success);
        assert!(activated.lock().unwrap().is_activated("srv__tool_a"));
        assert!(!activated.lock().unwrap().is_activated("srv__tool_b"));

        // Activate tool_b in a separate call
        let result = tool
            .execute(serde_json::json!({"query": "select:srv__tool_b"}))
            .await
            .unwrap();
        assert!(result.success);

        // Both should remain activated
        let guard = activated.lock().unwrap();
        assert!(guard.is_activated("srv__tool_a"));
        assert!(guard.is_activated("srv__tool_b"));
        assert_eq!(guard.tool_specs().len(), 2);
    }

    /// A description containing a backslash, newline, and tab must still produce
    /// a `<function>` entry whose JSON payload parses back cleanly.
    #[tokio::test]
    async fn description_with_control_chars_emits_valid_json() {
        let desc = "Reads a file at C:\\Users\\me.\nMatches \\d+ digits.\tDone.";
        let tool = ToolSearchTool::new(
            make_deferred_set(vec![make_stub("fs__read", desc)]).await,
            Arc::new(Mutex::new(ActivatedToolSet::new())),
        );
        let result = tool
            .execute(serde_json::json!({"query": "select:fs__read"}))
            .await
            .unwrap();
        assert!(result.success);

        // Extract the JSON between the <function> tags and parse it.
        let payload = result
            .output
            .lines()
            .find(|l| l.starts_with("<function>"))
            .and_then(|l| l.strip_prefix("<function>"))
            .and_then(|l| l.strip_suffix("</function>"))
            .expect("expected a <function> entry");
        let parsed: serde_json::Value =
            serde_json::from_str(payload).expect("emitted entry must be valid JSON");
        assert_eq!(parsed["name"], "fs__read");
        assert_eq!(parsed["description"], desc);
    }

    /// Verify re-activating an already-activated tool does not duplicate it.
    #[tokio::test]
    async fn reactivation_is_idempotent() {
        let activated = Arc::new(Mutex::new(ActivatedToolSet::new()));
        let tool = ToolSearchTool::new(
            make_deferred_set(vec![make_stub("srv__tool", "A tool")]).await,
            Arc::clone(&activated),
        );

        tool.execute(serde_json::json!({"query": "select:srv__tool"}))
            .await
            .unwrap();
        tool.execute(serde_json::json!({"query": "select:srv__tool"}))
            .await
            .unwrap();

        assert_eq!(activated.lock().unwrap().tool_specs().len(), 1);
    }

    /// With an allowlist configured, keyword search must not surface or activate
    /// tools outside the allowed set, while allowed tools still work.
    #[tokio::test]
    async fn allowlist_filters_keyword_search() {
        let activated = Arc::new(Mutex::new(ActivatedToolSet::new()));
        let stubs = vec![
            make_stub("fs__read", "Read a file from disk"),
            make_stub("net__fetch", "Fetch a file from the network"),
        ];
        let tool = ToolSearchTool::new(make_deferred_set(stubs).await, Arc::clone(&activated))
            .with_allowed_tools(Some(vec!["fs__read".to_string()]));

        let result = tool
            .execute(serde_json::json!({"query": "file"}))
            .await
            .unwrap();
        assert!(result.success);
        // Allowed tool appears and is activated.
        assert!(result.output.contains("fs__read"));
        assert!(activated.lock().unwrap().is_activated("fs__read"));
        // Disallowed tool is neither surfaced nor activated.
        assert!(!result.output.contains("net__fetch"));
        assert!(!activated.lock().unwrap().is_activated("net__fetch"));
    }

    /// With an allowlist configured, `select:` must refuse to activate a tool
    /// outside the allowed set and report it as not found.
    #[tokio::test]
    async fn allowlist_blocks_select_of_disallowed_tool() {
        let activated = Arc::new(Mutex::new(ActivatedToolSet::new()));
        let stubs = vec![
            make_stub("fs__read", "Read a file"),
            make_stub("net__fetch", "Fetch from the network"),
        ];
        let tool = ToolSearchTool::new(make_deferred_set(stubs).await, Arc::clone(&activated))
            .with_allowed_tools(Some(vec!["fs__read".to_string()]));

        // Disallowed tool: reported as not found, never activated.
        let result = tool
            .execute(serde_json::json!({"query": "select:net__fetch"}))
            .await
            .unwrap();
        assert!(result.success);
        assert!(result.output.contains("Not found"));
        assert!(!activated.lock().unwrap().is_activated("net__fetch"));

        // Allowed tool: activates normally.
        let result = tool
            .execute(serde_json::json!({"query": "select:fs__read"}))
            .await
            .unwrap();
        assert!(result.success);
        assert!(result.output.contains("fs__read"));
        assert!(activated.lock().unwrap().is_activated("fs__read"));
    }

    /// No allowlist (`None`) keeps every deferred tool reachable — the
    /// pre-existing default behavior.
    #[tokio::test]
    async fn no_allowlist_allows_all_tools() {
        let activated = Arc::new(Mutex::new(ActivatedToolSet::new()));
        let stubs = vec![make_stub("net__fetch", "Fetch from the network")];
        let tool = ToolSearchTool::new(make_deferred_set(stubs).await, Arc::clone(&activated))
            .with_allowed_tools(None);

        let result = tool
            .execute(serde_json::json!({"query": "select:net__fetch"}))
            .await
            .unwrap();
        assert!(result.success);
        assert!(result.output.contains("net__fetch"));
        assert!(activated.lock().unwrap().is_activated("net__fetch"));
    }
}
