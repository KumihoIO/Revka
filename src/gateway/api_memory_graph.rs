//! Aggregated memory graph endpoint for the Memory Auditor.
//!
//! `GET /api/memory/graph` — returns items + edges + spaces in one payload,
//! ready for the Obsidian-style force-graph visualization.
//!
//! **Primary path**: Operator MCP tool (`memory_graph`) via direct SDK/gRPC.
//! **Fallback path**: HTTP calls to Kumiho FastAPI (used when operator unavailable).

use super::AppState;
use super::api::require_auth;
use super::kumiho_client::ItemResponse;
use super::kumiho_client::build_kumiho_client;
use axum::{
    extract::{Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Json},
};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

// ── Query parameters ────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct MemoryGraphQuery {
    /// Kumiho project name (default: "CognitiveMemory").
    pub project: Option<String>,
    /// Maximum number of items to include (default 100, max 500).
    pub limit: Option<u32>,
    /// Comma-separated kind filter (e.g. "decision,fact,preference").
    pub kinds: Option<String>,
    /// Space path filter — only include items from this space.
    pub space: Option<String>,
    /// Sort mode: "recent" (default), "name".
    pub sort: Option<String>,
    /// Search query — if provided, filters to matching items via fulltext search.
    pub search: Option<String>,
}

// ── Response types ──────────────────────────────────────────────────────

#[derive(Serialize, Deserialize)]
pub struct MemoryGraphResponse {
    pub nodes: Vec<GraphNode>,
    pub edges: Vec<GraphEdge>,
    pub spaces: Vec<String>,
    pub stats: GraphStats,
}

#[derive(Serialize, Deserialize)]
pub struct GraphNode {
    pub id: String,
    pub name: String,
    pub kind: String,
    pub space: String,
    pub created_at: Option<String>,
    pub title: Option<String>,
    pub summary: Option<String>,
    pub revision_kref: Option<String>,
}

#[derive(Serialize, Deserialize)]
pub struct GraphEdge {
    pub source: String,
    pub target: String,
    pub edge_type: String,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub metadata: HashMap<String, String>,
}

#[derive(Serialize, Deserialize)]
pub struct GraphStats {
    pub total_items: usize,
    pub total_edges: usize,
    pub kinds: HashMap<String, usize>,
}

// ── Handler ─────────────────────────────────────────────────────────────

pub async fn handle_memory_graph(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(query): Query<MemoryGraphQuery>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    // Build MCP tool arguments from query params
    let mut mcp_args = serde_json::Map::new();
    if let Some(ref p) = query.project {
        mcp_args.insert("project".into(), serde_json::Value::String(p.clone()));
    }
    if let Some(l) = query.limit {
        mcp_args.insert(
            "limit".into(),
            serde_json::Value::Number(serde_json::Number::from(l)),
        );
    }
    if let Some(ref k) = query.kinds {
        mcp_args.insert("kinds".into(), serde_json::Value::String(k.clone()));
    }
    if let Some(ref s) = query.space {
        mcp_args.insert("space".into(), serde_json::Value::String(s.clone()));
    }
    if let Some(ref s) = query.sort {
        mcp_args.insert("sort".into(), serde_json::Value::String(s.clone()));
    }
    if let Some(ref s) = query.search {
        mcp_args.insert("search".into(), serde_json::Value::String(s.clone()));
    }

    // Try operator MCP tool first (direct SDK, no HTTP hop).
    // Cap at 45s — the memory graph route has its own 60s timeout, not the global 30s.
    if let Some(registry) = state.mcp_registry() {
        let tool_name = format!(
            "{}__memory_graph",
            crate::agent::operator::OPERATOR_SERVER_NAME
        );
        let mcp_future =
            registry.call_tool(&tool_name, serde_json::Value::Object(mcp_args.clone()));

        match tokio::time::timeout(std::time::Duration::from_secs(45), mcp_future).await {
            Ok(Ok(result_str)) => {
                // MCP tools/call returns {"content": [{"type":"text","text":"..."}]}
                // Extract the inner text, then parse as MemoryGraphResponse.
                if let Ok(wrapper) = serde_json::from_str::<serde_json::Value>(&result_str) {
                    let inner_json = wrapper
                        .get("content")
                        .and_then(|c| c.as_array())
                        .and_then(|arr| arr.first())
                        .and_then(|item| item.get("text"))
                        .and_then(|t| t.as_str())
                        .and_then(|text| serde_json::from_str::<serde_json::Value>(text).ok());

                    if let Some(val) = inner_json {
                        if val.get("error").and_then(|e| e.as_str()).is_none() {
                            if let Ok(response) = serde_json::from_value::<MemoryGraphResponse>(val)
                            {
                                tracing::info!(
                                    "memory_graph: operator MCP path succeeded ({} nodes, {} edges)",
                                    response.nodes.len(),
                                    response.edges.len()
                                );
                                return (StatusCode::OK, Json(response)).into_response();
                            }
                        }
                        tracing::warn!(
                            "memory_graph: operator returned error or unparseable inner JSON"
                        );
                    } else {
                        tracing::warn!(
                            "memory_graph: could not extract text from MCP content wrapper"
                        );
                    }
                }
                // Fall through to HTTP fallback
            }
            Ok(Err(e)) => {
                tracing::warn!("memory_graph: operator tool call failed: {e:#}");
            }
            Err(_) => {
                tracing::warn!("memory_graph: operator tool call timed out (45s)");
            }
        }
    }

    // Fallback: HTTP calls to Kumiho FastAPI
    http_fallback_memory_graph(&state, &query).await
}

// ── HTTP Fallback ───────────────────────────────────────────────────────

/// Strip `kref://` prefix if present.
fn strip_kref_scheme(kref: &str) -> &str {
    kref.strip_prefix("kref://").unwrap_or(kref)
}

/// Extract the item-level ID from a revision kref.
fn revision_kref_to_item_id(rev_kref: &str) -> String {
    let stripped = strip_kref_scheme(rev_kref);
    stripped.split('?').next().unwrap_or(stripped).to_string()
}

/// Extract `space_path` from an item kref.
fn item_kref_to_space(kref: &str) -> String {
    let stripped = strip_kref_scheme(kref);
    match stripped.rfind('/') {
        Some(pos) => stripped[..pos].to_string(),
        None => String::new(),
    }
}

fn item_to_node(
    item: &ItemResponse,
    rev_title: Option<&str>,
    rev_summary: Option<&str>,
    rev_kref: Option<&str>,
) -> GraphNode {
    let id = strip_kref_scheme(&item.kref).to_string();
    let space = item_kref_to_space(&item.kref);
    GraphNode {
        id,
        name: item.item_name.clone(),
        kind: item.kind.clone(),
        space,
        created_at: item.created_at.clone(),
        title: rev_title.map(|s| s.to_string()),
        summary: rev_summary.map(|s| s.to_string()),
        revision_kref: rev_kref.map(|s| s.to_string()),
    }
}

async fn http_fallback_memory_graph(
    state: &AppState,
    query: &MemoryGraphQuery,
) -> axum::response::Response {
    let client = build_kumiho_client(state);
    let default_project = {
        let config = state.config.lock();
        config.kumiho.memory_project.clone()
    };
    let project = query.project.as_deref().unwrap_or(&default_project);
    let limit = query.limit.unwrap_or(100).min(500) as usize;
    let kind_filter: Vec<String> = query
        .kinds
        .as_deref()
        .unwrap_or("")
        .split(',')
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect();
    let space_filter = query.space.as_deref().unwrap_or("");
    let sort_mode = query.sort.as_deref().unwrap_or("recent");
    let search_query = query.search.as_deref().unwrap_or("");

    // 1. List all spaces recursively
    let root_path = format!("/{project}");
    let spaces_result = client.list_spaces(&root_path, true).await;
    let space_paths: Vec<String> = match spaces_result {
        Ok(spaces) => {
            let mut paths = vec![root_path.clone()];
            paths.extend(spaces.into_iter().map(|s| s.path));
            paths
        }
        Err(e) => {
            return (
                StatusCode::BAD_GATEWAY,
                Json(serde_json::json!({ "error": format!("Failed to list spaces: {e}") })),
            )
                .into_response();
        }
    };

    let target_spaces: Vec<&str> = if space_filter.is_empty() {
        space_paths.iter().map(|s| s.as_str()).collect()
    } else {
        space_paths
            .iter()
            .filter(|s| s.starts_with(space_filter) || *s == space_filter)
            .map(|s| s.as_str())
            .collect()
    };

    // 2. Fetch items
    let mut all_items: Vec<ItemResponse> = Vec::new();

    if !search_query.is_empty() {
        match client.search_items(search_query, project, "", false).await {
            Ok(results) => {
                all_items = results.into_iter().map(|r| r.item).collect();
            }
            Err(e) => {
                return (
                    StatusCode::BAD_GATEWAY,
                    Json(serde_json::json!({ "error": format!("Search failed: {e}") })),
                )
                    .into_response();
            }
        }
    } else {
        for chunk in target_spaces.chunks(10) {
            let futs: Vec<_> = chunk
                .iter()
                .map(|sp| {
                    let c = client.clone();
                    let sp = sp.to_string();
                    async move {
                        c.list_items_paged(&sp, false, 200, 0)
                            .await
                            .unwrap_or_default()
                    }
                })
                .collect();
            let results = futures_util::future::join_all(futs).await;
            for items in results {
                all_items.extend(items);
            }
            if all_items.len() > limit * 2 {
                break;
            }
        }
    }

    // 3. Apply kind filter
    if !kind_filter.is_empty() {
        all_items.retain(|item| kind_filter.contains(&item.kind));
    }

    // 4. Sort
    match sort_mode {
        "name" => all_items.sort_by(|a, b| a.item_name.cmp(&b.item_name)),
        _ => {
            all_items.sort_by(|a, b| {
                let a_date = a.created_at.as_deref().unwrap_or("");
                let b_date = b.created_at.as_deref().unwrap_or("");
                b_date.cmp(a_date)
            });
        }
    }

    let mut kind_counts: HashMap<String, usize> = HashMap::new();
    for item in &all_items {
        *kind_counts.entry(item.kind.clone()).or_insert(0) += 1;
    }
    let total_items_count = all_items.len();

    // 5. Truncate
    all_items.truncate(limit);

    // 6. Batch-fetch revisions
    let item_krefs: Vec<String> = all_items.iter().map(|i| i.kref.clone()).collect();
    let rev_map = client
        .batch_get_revisions(&item_krefs, "latest")
        .await
        .unwrap_or_default();

    // 7. Build nodes
    let mut nodes: Vec<GraphNode> = Vec::with_capacity(all_items.len());
    let mut item_id_set: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut rev_krefs: Vec<String> = Vec::new();

    for item in &all_items {
        let rev = rev_map.get(&item.kref);
        let title = rev.and_then(|r| r.metadata.get("title").map(|s| s.as_str()));
        let summary = rev.and_then(|r| r.metadata.get("summary").map(|s| s.as_str()));
        let rev_kref = rev.map(|r| r.kref.as_str());
        nodes.push(item_to_node(item, title, summary, rev_kref));
        item_id_set.insert(strip_kref_scheme(&item.kref).to_string());
        if let Some(r) = rev {
            rev_krefs.push(r.kref.clone());
        }
    }

    // 8. Fetch edges
    let mut edge_results = Vec::new();
    for chunk in rev_krefs.chunks(10) {
        let futs: Vec<_> = chunk
            .iter()
            .map(|rk| {
                let c = client.clone();
                let rk = rk.clone();
                async move {
                    c.list_edges(&rk, None, Some("both"))
                        .await
                        .unwrap_or_default()
                }
            })
            .collect();
        edge_results.extend(futures_util::future::join_all(futs).await);
    }

    // 9. Deduplicate edges
    let mut seen_edges: std::collections::HashSet<(String, String, String)> =
        std::collections::HashSet::new();
    let mut edges: Vec<GraphEdge> = Vec::new();

    for edge_list in edge_results {
        for edge in edge_list {
            let source_id = revision_kref_to_item_id(&edge.source_kref);
            let target_id = revision_kref_to_item_id(&edge.target_kref);
            if source_id == target_id {
                continue;
            }
            if !item_id_set.contains(&source_id) || !item_id_set.contains(&target_id) {
                continue;
            }
            let key = (source_id.clone(), target_id.clone(), edge.edge_type.clone());
            if seen_edges.contains(&key) {
                continue;
            }
            seen_edges.insert(key);
            edges.push(GraphEdge {
                source: source_id,
                target: target_id,
                edge_type: edge.edge_type,
                metadata: edge.metadata.unwrap_or_default(),
            });
        }
    }

    let total_edges = edges.len();

    let response = MemoryGraphResponse {
        nodes,
        edges,
        spaces: space_paths
            .into_iter()
            .map(|s| s.trim_start_matches('/').to_string())
            .collect(),
        stats: GraphStats {
            total_items: total_items_count,
            total_edges,
            kinds: kind_counts,
        },
    };

    (StatusCode::OK, Json(response)).into_response()
}
