//! REST API handlers for the Architect feature (`/api/architect/*`).
//!
//! The editor's "Architect" panel calls these routes to:
//!   1. Forward revision proposals to operator-mcp's `revise_workflow` tool.
//!   2. List a workflow item's revision history.
//!   3. Re-tag an earlier revision as `published` (Kumiho-native revert).
//!
//! All routes require dashboard auth via `require_auth`. The republish handler
//! hardcodes the `published` tag — callers cannot inject arbitrary tag values.

use super::AppState;
use super::api::require_auth;
use super::api_agents::build_kumiho_client;
use super::kumiho_client::KumihoError;
use axum::{
    extract::{Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Json},
};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

// ── Helpers ─────────────────────────────────────────────────────────────

fn kumiho_err(e: KumihoError) -> (StatusCode, Json<serde_json::Value>) {
    match &e {
        KumihoError::Unreachable(_) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(serde_json::json!({ "error": format!("Kumiho service unavailable: {e}") })),
        ),
        KumihoError::Api { status, body } => {
            let code = if *status == 401 || *status == 403 {
                StatusCode::BAD_GATEWAY
            } else {
                StatusCode::from_u16(*status).unwrap_or(StatusCode::BAD_GATEWAY)
            };
            (
                code,
                Json(serde_json::json!({ "error": format!("Kumiho upstream: {body}") })),
            )
        }
        KumihoError::Decode(msg) => (
            StatusCode::BAD_GATEWAY,
            Json(serde_json::json!({ "error": format!("Bad response from Kumiho: {msg}") })),
        ),
    }
}

/// Generic operator-mcp tool dispatch. Modeled on
/// `api_workflows::validate_via_operator` but parameterized by tool name —
/// returns the parsed inner JSON from the MCP `content[0].text` envelope.
async fn call_operator_tool(
    state: &AppState,
    tool: &str,
    args: serde_json::Map<String, serde_json::Value>,
) -> Result<serde_json::Value, String> {
    let tool_name = format!("{}__{}", crate::agent::operator::OPERATOR_SERVER_NAME, tool);

    let registry = state
        .mcp_registry
        .as_ref()
        .ok_or_else(|| "MCP registry not available — operator not connected".to_string())?;

    let fut = registry.call_tool(&tool_name, serde_json::Value::Object(args));
    let result_str = match tokio::time::timeout(std::time::Duration::from_secs(30), fut).await {
        Ok(Ok(s)) => s,
        Ok(Err(e)) => return Err(format!("operator {tool} failed: {e:#}")),
        Err(_) => return Err(format!("operator {tool} timed out (30s)")),
    };

    let outer: serde_json::Value = serde_json::from_str(&result_str)
        .map_err(|e| format!("{tool}: outer JSON parse failed: {e}"))?;

    let inner_text = outer
        .get("content")
        .and_then(|c| c.get(0))
        .and_then(|c0| c0.get("text"))
        .and_then(|t| t.as_str())
        .ok_or_else(|| format!("{tool}: missing content[0].text"))?;

    serde_json::from_str(inner_text).map_err(|e| format!("{tool}: inner JSON parse failed: {e}"))
}

/// Normalize a kref from a URL path — accept either a bare path
/// ("Project/Workflows/foo") or a full kref URI ("kref://Project/Workflows/foo").
fn normalize_kref(raw: &str) -> String {
    let stripped = raw.strip_prefix("kref://").unwrap_or(raw);
    format!("kref://{stripped}")
}

// ── Request / response types ────────────────────────────────────────────

#[derive(Deserialize)]
pub struct ReviseBody {
    pub workflow_kref: String,
    /// Pass-through: forwarded verbatim to `revise_workflow`. Validation of
    /// individual operation shapes is the MCP tool's responsibility.
    pub operations: Vec<serde_json::Value>,
    pub rationale: Option<String>,
}

#[derive(Deserialize)]
pub struct ValidateYamlBody {
    pub yaml: String,
    pub base_yaml: Option<String>,
    pub intent_summary: Option<String>,
}

#[derive(Deserialize)]
pub struct RepublishBody {
    pub revision_kref: String,
}

#[derive(Deserialize)]
pub struct RevisionsQuery {
    pub workflow_kref: String,
}

#[derive(Serialize)]
pub struct RevisionSummary {
    /// Includes `?r=N` suffix.
    pub kref: String,
    pub number: i32,
    pub created_at: Option<String>,
    pub tags: Vec<String>,
    pub metadata: HashMap<String, String>,
}

// ── Handlers ────────────────────────────────────────────────────────────

/// `POST /api/architect/revise`
///
/// Forwards `{workflow_kref, operations[], rationale?}` to operator-mcp's
/// `revise_workflow` tool and returns the structured response verbatim.
pub async fn handle_architect_revise(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<ReviseBody>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    if body.workflow_kref.trim().is_empty() {
        return (StatusCode::BAD_REQUEST, "workflow_kref required").into_response();
    }
    if body.operations.is_empty() {
        return (StatusCode::BAD_REQUEST, "operations must be non-empty").into_response();
    }

    let mut args = serde_json::Map::new();
    args.insert(
        "workflow_kref".to_string(),
        serde_json::Value::String(body.workflow_kref),
    );
    args.insert(
        "operations".to_string(),
        serde_json::Value::Array(body.operations),
    );
    if let Some(r) = body.rationale {
        args.insert("rationale".to_string(), serde_json::Value::String(r));
    }

    match call_operator_tool(&state, "revise_workflow", args).await {
        Ok(result) => Json(result).into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("revise_workflow failed: {e}"),
        )
            .into_response(),
    }
}

/// `POST /api/architect/validate_yaml`
///
/// Validates a workflow YAML proposal by routing through the same
/// `propose_workflow_yaml` Operator tool the LLM uses. Used by the editor's
/// chat-fallback path: when Architect dumps YAML in chat instead of calling
/// the tool, the client posts the extracted YAML here so it goes through
/// schema + workflow-level validation (matching the orphan-parallel guard
/// added in PR #163) before reaching the canvas.
///
/// Returns the tool result body verbatim — same shape as
/// `propose_workflow_yaml`'s tool result: `{ valid, errors, warnings, yaml,
/// summary, added_step_ids, modified_step_ids, removed_step_ids }`. No
/// persistence — `propose_workflow_yaml` is in-memory only by design.
pub async fn handle_architect_validate_yaml(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<ValidateYamlBody>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    if body.yaml.trim().is_empty() {
        return (StatusCode::BAD_REQUEST, "yaml required").into_response();
    }

    let mut args = serde_json::Map::new();
    args.insert(
        "proposed_yaml".to_string(),
        serde_json::Value::String(body.yaml),
    );
    args.insert(
        "intent_summary".to_string(),
        serde_json::Value::String(body.intent_summary.unwrap_or_default()),
    );
    if let Some(b) = body.base_yaml {
        args.insert("base_yaml".to_string(), serde_json::Value::String(b));
    }

    match call_operator_tool(&state, "propose_workflow_yaml", args).await {
        Ok(result) => Json(result).into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("propose_workflow_yaml failed: {e}"),
        )
            .into_response(),
    }
}

/// `GET /api/architect/revisions?workflow_kref=...`
///
/// Lists Kumiho revisions for a workflow item. Returns a thin summary
/// (kref, number, created_at, tags, metadata).
///
/// Query-param shape (rather than path-embedded kref) because axum 0.8 routes
/// can't have wildcard captures followed by literal segments — and the
/// existing `/api/workflows/{*kref}` route already claims that prefix.
pub async fn handle_list_workflow_revisions(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(q): Query<RevisionsQuery>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    if q.workflow_kref.trim().is_empty() {
        return (StatusCode::BAD_REQUEST, "workflow_kref required").into_response();
    }

    let kref = normalize_kref(&q.workflow_kref);
    let client = build_kumiho_client(&state);

    match client.list_item_revisions(&kref).await {
        Ok(revs) => {
            let summary: Vec<RevisionSummary> = revs
                .iter()
                .map(|r| RevisionSummary {
                    kref: r.kref.clone(),
                    number: r.number,
                    created_at: r.created_at.clone(),
                    tags: r.tags.clone(),
                    metadata: r.metadata.clone(),
                })
                .collect();
            Json(serde_json::json!({ "revisions": summary })).into_response()
        }
        Err(e) => kumiho_err(e).into_response(),
    }
}

/// `POST /api/architect/republish`
///
/// Re-tags the specified revision as `published`. The body shape is used
/// (instead of a path-embedded kref) because axum 0.8 disallows wildcard
/// captures followed by literal segments.
///
/// The `published` tag is hardcoded — callers cannot use this route to set
/// arbitrary tag values (defense against confused-deputy).
pub async fn handle_republish_revision(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<RepublishBody>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    if body.revision_kref.trim().is_empty() {
        return (StatusCode::BAD_REQUEST, "revision_kref required").into_response();
    }

    let revision_kref = normalize_kref(&body.revision_kref);
    let client = build_kumiho_client(&state);

    match client.tag_revision(&revision_kref, "published").await {
        Ok(_) => {
            // Invalidate the workflows list cache so the next /api/workflows
            // poll reflects the newly-published revision.
            super::api_workflows::invalidate_cache();

            // Emit the canonical `workflow.revision.published` event so the
            // editor's `useWorkflowEvents` hook picks the revert up and
            // refreshes the canvas/YAML. Strip's `useRevisionEvents` matches
            // this event too, so the history strip still updates.
            //
            // Payload shape mirrors `api_workflows::broadcast_revision_published`:
            //   workflow_kref       — item kref (no `?r=N`)
            //   revision_kref       — full revision kref
            //   revision_number     — parsed from the `?r=N` segment
            //   name                — derived from the kref's last segment
            //   published_at        — current UTC time (re-tagging is "now")
            //   originating_session — null (republish is system-initiated)
            let workflow_kref = revision_kref
                .split_once("?r=")
                .map(|(base, _)| base.to_string())
                .unwrap_or_else(|| revision_kref.clone());

            let revision_number: i32 = revision_kref
                .split_once("?r=")
                .and_then(|(_, n)| n.split('&').next())
                .and_then(|n| n.parse::<i32>().ok())
                .unwrap_or(0);

            // Derive the name from the kref's last segment
            // (item kref pattern: `kref://Project/Workflows/<name>`).
            // Mirrors the same derivation in api_workflows.rs ~L1547.
            // RevisionResponse has no `name` field and there's no client.get_item
            // method, so the kref's last segment is the canonical source.
            let name = workflow_kref
                .rsplit('/')
                .next()
                .map(|seg| {
                    seg.rsplit_once('.')
                        .map(|(n, _)| n)
                        .unwrap_or(seg)
                        .to_string()
                })
                .unwrap_or_default();

            let published_at = chrono::Utc::now().to_rfc3339();

            let payload = serde_json::json!({
                "type": "workflow.revision.published",
                "workflow_kref": workflow_kref,
                "revision_kref": revision_kref.clone(),
                "revision_number": revision_number,
                "name": name,
                "published_at": published_at,
                "originating_session": serde_json::Value::Null,
            });
            if let Err(err) = state.event_tx.send(payload) {
                tracing::debug!("workflow.revision.published broadcast skipped: {err}");
            }

            Json(serde_json::json!({
                "ok": true,
                "revision_kref": revision_kref,
            }))
            .into_response()
        }
        Err(e) => kumiho_err(e).into_response(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_kref_adds_prefix() {
        assert_eq!(
            normalize_kref("Project/Workflows/foo"),
            "kref://Project/Workflows/foo"
        );
    }

    #[test]
    fn normalize_kref_idempotent() {
        assert_eq!(
            normalize_kref("kref://Project/Workflows/foo"),
            "kref://Project/Workflows/foo"
        );
    }
}
