//! REST API handlers for workflow management (`/api/workflows`).
//!
//! Each workflow definition is a Kumiho item of kind `"workflow"` in the
//! `Construct/Workflows` space.  The YAML definition and metadata (description,
//! version, tags, steps count) are stored as revision metadata.
//!
//! Provides:
//!   - `GET    /api/workflows`              — list workflow definitions
//!   - `POST   /api/workflows`              — create a new workflow
//!   - `PUT    /api/workflows/{*kref}`      — update an existing workflow
//!   - `DELETE /api/workflows/{*kref}`       — delete a workflow
//!   - `POST   /api/workflows/deprecate`    — toggle deprecation
//!   - `GET    /api/workflows/runs`         — recent workflow runs (from Kumiho)
//!   - `GET    /api/workflows/runs/{id}`    — single run detail
//!   - `GET    /api/workflows/dashboard`    — aggregated stats

use super::AppState;
use super::api::require_auth;
use super::api_agents::build_kumiho_client;
use super::kumiho_client::{ItemResponse, KumihoClient, KumihoError, RevisionResponse, slugify};
use axum::{
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Json},
};
use parking_lot::Mutex;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::OnceLock;
use std::time::Instant;

const WORKFLOW_SPACE_NAME: &str = "Workflows";
const WORKFLOW_RUNS_SPACE_NAME: &str = "WorkflowRuns";
const WORKFLOW_RUN_REQUESTS_SPACE_NAME: &str = "WorkflowRunRequests";

fn workflow_project(state: &AppState) -> String {
    state.config.lock().kumiho.harness_project.clone()
}

fn workflow_space_path(state: &AppState) -> String {
    format!("/{}/{}", workflow_project(state), WORKFLOW_SPACE_NAME)
}

fn workflow_runs_space_path(state: &AppState) -> String {
    format!("/{}/{}", workflow_project(state), WORKFLOW_RUNS_SPACE_NAME)
}

fn workflow_run_requests_space_path(state: &AppState) -> String {
    format!(
        "/{}/{}",
        workflow_project(state),
        WORKFLOW_RUN_REQUESTS_SPACE_NAME
    )
}

// ── Response cache ──────────────────────────────────────────────────────

struct WorkflowCache {
    workflows: Vec<WorkflowResponse>,
    include_deprecated: bool,
    fetched_at: Instant,
}

static WORKFLOW_CACHE: OnceLock<Mutex<Option<WorkflowCache>>> = OnceLock::new();
const CACHE_TTL_SECS: u64 = 3;

fn get_cached(include_deprecated: bool) -> Option<Vec<WorkflowResponse>> {
    let lock = WORKFLOW_CACHE.get_or_init(|| Mutex::new(None));
    let cache = lock.lock();
    if let Some(ref c) = *cache {
        if c.include_deprecated == include_deprecated
            && c.fetched_at.elapsed().as_secs() < CACHE_TTL_SECS
        {
            return Some(c.workflows.clone());
        }
    }
    None
}

fn set_cached(workflows: &[WorkflowResponse], include_deprecated: bool) {
    let lock = WORKFLOW_CACHE.get_or_init(|| Mutex::new(None));
    let mut cache = lock.lock();
    *cache = Some(WorkflowCache {
        workflows: workflows.to_vec(),
        include_deprecated,
        fetched_at: Instant::now(),
    });
}

pub(super) fn invalidate_cache() {
    if let Some(lock) = WORKFLOW_CACHE.get() {
        let mut cache = lock.lock();
        // Mark as expired but keep stale data for fallback on API errors
        if let Some(ref mut c) = *cache {
            c.fetched_at = Instant::now() - std::time::Duration::from_secs(CACHE_TTL_SECS + 1);
        }
    }
}

// ── Query / request types ───────────────────────────────────────────────

#[derive(Deserialize)]
pub struct WorkflowListQuery {
    #[serde(default)]
    pub include_deprecated: bool,
    pub q: Option<String>,
}

#[derive(Deserialize)]
pub struct CreateWorkflowBody {
    pub name: String,
    pub description: String,
    pub definition: String,
    #[serde(default)]
    pub version: Option<String>,
    #[serde(default)]
    pub tags: Option<Vec<String>>,
}

#[derive(Deserialize)]
pub struct DeprecateBody {
    pub kref: String,
    pub deprecated: bool,
}

#[derive(Deserialize)]
pub struct WorkflowRunsQuery {
    #[serde(default = "default_limit")]
    pub limit: usize,
    #[serde(default)]
    pub workflow: Option<String>,
}

fn default_limit() -> usize {
    20
}

#[derive(Deserialize)]
pub struct RunWorkflowBody {
    #[serde(default)]
    pub inputs: serde_json::Value,
    #[serde(default)]
    pub cwd: Option<String>,
}

// ── Response types ──────────────────────────────────────────────────────

#[derive(Serialize, Clone)]
pub struct WorkflowResponse {
    pub kref: String,
    pub name: String,
    pub item_name: String,
    pub deprecated: bool,
    pub created_at: Option<String>,
    pub description: String,
    pub definition: String,
    pub version: String,
    pub tags: Vec<String>,
    pub steps: usize,
    pub revision_number: i32,
    /// `"builtin"` — shipped with Construct, not yet customized.
    /// `"builtin-modified"` — builtin overridden by a Kumiho copy.
    /// `"custom"` — user-created workflow.
    #[serde(default = "default_source")]
    pub source: String,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub triggers: Vec<WorkflowTrigger>,
}

fn default_source() -> String {
    "custom".to_string()
}

#[derive(Serialize, Clone, Debug)]
pub struct WorkflowTrigger {
    pub on_kind: String,
    pub on_tag: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub on_name_pattern: String,
}

#[derive(Serialize, Clone)]
pub struct WorkflowRunSummary {
    pub kref: String,
    pub run_id: String,
    pub workflow_name: String,
    pub status: String,
    pub started_at: String,
    pub completed_at: String,
    pub steps_completed: String,
    pub steps_total: String,
    pub error: String,
    /// Kumiho item kref of the workflow definition this run used.
    /// Empty for built-in / disk-fallback workflows.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub workflow_item_kref: String,
    /// Kumiho revision kref of the exact workflow YAML this run executed.
    /// The dashboard DAG viewer fetches this revision so the rendered graph
    /// always matches what the run actually ran — independent of later retags.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub workflow_revision_kref: String,
}

#[derive(Serialize, Clone)]
pub struct TranscriptEntry {
    pub speaker: String,
    pub content: String,
    pub round: u32,
}

#[derive(Serialize, Clone, Default)]
pub struct ApprovalOutputData {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub awaiting_approval: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub approval_message: Option<String>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub approve_keywords: Vec<String>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub reject_keywords: Vec<String>,
}

#[derive(Serialize, Clone)]
pub struct WorkflowStepDetail {
    pub step_id: String,
    pub status: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub agent_id: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub agent_type: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub role: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub template_name: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub output_preview: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub artifact_path: String,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub skills: Vec<String>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub transcript: Vec<TranscriptEntry>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub output_data: Option<ApprovalOutputData>,
}

#[derive(Serialize, Clone)]
pub struct WorkflowRunDetail {
    #[serde(flatten)]
    pub summary: WorkflowRunSummary,
    pub steps: Vec<WorkflowStepDetail>,
}

#[derive(Serialize)]
pub struct WorkflowDashboard {
    pub definitions_count: usize,
    pub definitions: Vec<WorkflowResponse>,
    pub active_runs: usize,
    pub recent_runs: Vec<WorkflowRunSummary>,
    pub total_runs: usize,
}

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

fn workflow_metadata(body: &CreateWorkflowBody) -> HashMap<String, String> {
    let mut meta = HashMap::new();
    meta.insert("display_name".to_string(), body.name.clone());
    meta.insert("description".to_string(), body.description.clone());
    meta.insert("definition".to_string(), body.definition.clone());
    meta.insert("created_by".to_string(), "construct-dashboard".to_string());
    // Count steps in the YAML
    let steps = count_yaml_steps(&body.definition);
    meta.insert("steps".to_string(), steps.to_string());
    if let Some(ref tags) = body.tags {
        if !tags.is_empty() {
            meta.insert("tags".to_string(), tags.join(","));
        }
    }
    // Full-text search index
    meta.insert(
        "_search_text".to_string(),
        format!("{} {}", body.name, body.description),
    );
    meta
}

fn count_yaml_steps(content: &str) -> usize {
    let mut count = 0;
    let mut in_steps = false;
    for line in content.lines() {
        let trimmed = line.trim();
        if trimmed == "steps:" || trimmed == "tasks:" {
            in_steps = true;
            continue;
        }
        if in_steps {
            if trimmed.starts_with("- id:") {
                count += 1;
            }
            if !trimmed.is_empty()
                && !trimmed.starts_with('-')
                && !trimmed.starts_with(' ')
                && !trimmed.starts_with('#')
                && !line.starts_with(' ')
            {
                break;
            }
        }
    }
    count
}

fn to_workflow_response(item: &ItemResponse, rev: Option<&RevisionResponse>) -> WorkflowResponse {
    let meta = rev.map(|r| &r.metadata);
    let get = |key: &str| -> String { meta.and_then(|m| m.get(key)).cloned().unwrap_or_default() };
    let tags_str = get("tags");
    let tags: Vec<String> = if tags_str.is_empty() {
        Vec::new()
    } else {
        tags_str.split(',').map(|s| s.trim().to_string()).collect()
    };
    let steps: usize = get("steps").parse().unwrap_or(0);

    let display_name = {
        let n = get("display_name");
        if n.is_empty() {
            item.item_name.clone()
        } else {
            n
        }
    };

    let definition = get("definition");
    let triggers = extract_triggers(&definition);

    WorkflowResponse {
        kref: item.kref.clone(),
        name: display_name,
        item_name: item.item_name.clone(),
        deprecated: item.deprecated,
        created_at: item.created_at.clone(),
        description: get("description"),
        definition,
        version: format!("{}", rev.map(|r| r.number).unwrap_or(0)),
        tags,
        steps,
        revision_number: rev.map(|r| r.number).unwrap_or(0),
        source: "custom".to_string(),
        triggers,
    }
}

/// Prefer the `workflow.yaml` artifact on disk as the canonical definition,
/// falling back to inline `definition` metadata only when no artifact exists.
///
/// The inline `definition` metadata is a legacy gateway-authored field that
/// drifts from the artifact for operator-authored revisions and can also be
/// truncated by Kumiho's batch endpoint for large YAMLs. The artifact file is
/// the source of truth, so we always overwrite metadata with it when present.
async fn prefer_artifact_definitions(
    client: &super::kumiho_client::KumihoClient,
    revs: &mut HashMap<String, RevisionResponse>,
) {
    for rev in revs.values_mut() {
        if let Ok(artifact) = client
            .get_artifact_by_name(&rev.kref, "workflow.yaml")
            .await
        {
            let path = artifact
                .location
                .strip_prefix("file://")
                .unwrap_or(&artifact.location);
            if let Ok(yaml) = tokio::fs::read_to_string(path).await {
                rev.metadata.insert("definition".to_string(), yaml);
            }
        }
    }
}

async fn enrich_items(
    client: &super::kumiho_client::KumihoClient,
    items: Vec<ItemResponse>,
) -> Vec<WorkflowResponse> {
    // Only include items with kind == "workflow" — filter out stray items
    // that agents may have created in the Workflows space.
    let items: Vec<ItemResponse> = items.into_iter().filter(|i| i.kind == "workflow").collect();

    if items.is_empty() {
        return Vec::new();
    }

    let krefs: Vec<String> = items.iter().map(|i| i.kref.clone()).collect();

    if let Ok(mut rev_map) = client.batch_get_revisions(&krefs, "published").await {
        let missing: Vec<String> = krefs
            .iter()
            .filter(|k| !rev_map.contains_key(*k))
            .cloned()
            .collect();
        let mut latest_map = if !missing.is_empty() {
            client
                .batch_get_revisions(&missing, "latest")
                .await
                .unwrap_or_default()
        } else {
            HashMap::new()
        };

        // Artifact-first: the `workflow.yaml` on disk is canonical. The inline
        // `definition` metadata drifts for operator-authored revisions and is
        // truncated by Kumiho's batch endpoint for large YAMLs, so we always
        // prefer the artifact when it exists — same logic the single-revision
        // endpoint uses.
        prefer_artifact_definitions(client, &mut rev_map).await;
        prefer_artifact_definitions(client, &mut latest_map).await;

        return items
            .iter()
            .map(|item| {
                let rev = rev_map
                    .get(&item.kref)
                    .or_else(|| latest_map.get(&item.kref));
                to_workflow_response(item, rev)
            })
            .collect();
    }

    // Fallback: sequential
    let mut workflows = Vec::with_capacity(items.len());
    for item in &items {
        let rev = client.get_published_or_latest(&item.kref).await.ok();
        workflows.push(to_workflow_response(item, rev.as_ref()));
    }
    workflows
}

fn to_run_summary(item: &ItemResponse, rev: Option<&RevisionResponse>) -> WorkflowRunSummary {
    let meta = rev.map(|r| &r.metadata);
    let get = |key: &str| -> String { meta.and_then(|m| m.get(key)).cloned().unwrap_or_default() };

    let run_id_meta = get("run_id");
    WorkflowRunSummary {
        kref: item.kref.clone(),
        run_id: if run_id_meta.is_empty() {
            item.item_name.clone()
        } else {
            run_id_meta
        },
        workflow_name: {
            let wn = get("workflow_name");
            if wn.is_empty() { get("workflow") } else { wn }
        },
        status: get("status"),
        started_at: get("started_at"),
        completed_at: get("completed_at"),
        steps_completed: get("steps_completed"),
        steps_total: get("steps_total"),
        error: get("error"),
        workflow_item_kref: get("workflow_item_kref"),
        workflow_revision_kref: get("workflow_revision_kref"),
    }
}

fn extract_steps_from_metadata(meta: &HashMap<String, String>) -> Vec<WorkflowStepDetail> {
    // Skip known non-step metadata keys that happen to start with "step_"
    const SKIP_KEYS: &[&str] = &["step_count", "steps_completed", "steps_total"];

    let mut steps = Vec::new();
    for (key, value) in meta {
        if SKIP_KEYS.contains(&key.as_str()) {
            continue;
        }
        if let Some(step_id) = key.strip_prefix("step_") {
            // Value should be JSON object: {"status":"completed","output_preview":"...","agent_id":"..."}
            // Legacy runs may have truncated JSON — fall back to regex extraction.
            if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(value) {
                // Only accept JSON objects (skip plain numbers/strings)
                if !parsed.is_object() {
                    continue;
                }
                let skills = parsed
                    .get("skills")
                    .and_then(|v| v.as_array())
                    .map(|arr| {
                        arr.iter()
                            .filter_map(|s| s.as_str().map(|s| s.to_string()))
                            .collect::<Vec<_>>()
                    })
                    .unwrap_or_default();
                // Group chat transcript — stored as JSON string of array
                let transcript = parsed
                    .get("transcript")
                    .and_then(|v| v.as_str())
                    .and_then(|s| serde_json::from_str::<Vec<serde_json::Value>>(s).ok())
                    .map(|arr| {
                        arr.iter()
                            .map(|entry| TranscriptEntry {
                                speaker: entry
                                    .get("speaker")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("?")
                                    .to_string(),
                                content: entry
                                    .get("content")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("")
                                    .to_string(),
                                round: entry.get("round").and_then(|v| v.as_u64()).unwrap_or(0)
                                    as u32,
                            })
                            .collect::<Vec<_>>()
                    })
                    .unwrap_or_default();
                // Decode output_data for approval steps
                let output_data = parsed.get("output_data").and_then(|v| {
                    // output_data may be a JSON string or an embedded object
                    let obj = if let Some(s) = v.as_str() {
                        serde_json::from_str::<serde_json::Value>(s).ok()
                    } else {
                        Some(v.clone())
                    };
                    obj.map(|o| ApprovalOutputData {
                        awaiting_approval: o.get("awaiting_approval").and_then(|v| v.as_bool()),
                        approval_message: o
                            .get("approval_message")
                            .and_then(|v| v.as_str())
                            .map(String::from),
                        approve_keywords: o
                            .get("approve_keywords")
                            .and_then(|v| v.as_array())
                            .map(|arr| {
                                arr.iter()
                                    .filter_map(|s| s.as_str().map(String::from))
                                    .collect()
                            })
                            .unwrap_or_default(),
                        reject_keywords: o
                            .get("reject_keywords")
                            .and_then(|v| v.as_array())
                            .map(|arr| {
                                arr.iter()
                                    .filter_map(|s| s.as_str().map(String::from))
                                    .collect()
                            })
                            .unwrap_or_default(),
                    })
                });
                steps.push(WorkflowStepDetail {
                    step_id: step_id.to_string(),
                    status: parsed
                        .get("status")
                        .and_then(|v| v.as_str())
                        .unwrap_or("unknown")
                        .to_string(),
                    agent_id: parsed
                        .get("agent_id")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string(),
                    agent_type: parsed
                        .get("agent_type")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string(),
                    role: parsed
                        .get("role")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string(),
                    template_name: parsed
                        .get("template_name")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string(),
                    output_preview: parsed
                        .get("output_preview")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string(),
                    artifact_path: parsed
                        .get("artifact_path")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string(),
                    skills,
                    transcript,
                    output_data,
                });
            } else if value.contains(r#""status""#) {
                // Truncated JSON fallback: extract status with simple string search
                let status = if let Some(start) = value.find(r#""status": ""#) {
                    let rest = &value[start + 11..];
                    rest.split('"').next().unwrap_or("unknown")
                } else {
                    "unknown"
                };
                steps.push(WorkflowStepDetail {
                    step_id: step_id.to_string(),
                    status: status.to_string(),
                    agent_id: String::new(),
                    agent_type: String::new(),
                    role: String::new(),
                    template_name: String::new(),
                    output_preview: String::new(),
                    artifact_path: String::new(),
                    skills: Vec::new(),
                    transcript: Vec::new(),
                    output_data: None,
                });
            }
        }
    }
    steps
}

fn to_run_detail(item: &ItemResponse, rev: Option<&RevisionResponse>) -> WorkflowRunDetail {
    let summary = to_run_summary(item, rev);
    let steps = rev
        .map(|r| extract_steps_from_metadata(&r.metadata))
        .unwrap_or_default();
    WorkflowRunDetail { summary, steps }
}

// ── Builtin workflow discovery ──────────────────────────────────────────

/// Default directory containing builtin workflow YAML files.
const BUILTIN_WORKFLOWS_DIR: &str = ".construct/operator_mcp/workflow/builtins";

/// Discover builtin workflow YAML files from `~/BUILTIN_WORKFLOWS_DIR`.
///
/// Returns a vec of `WorkflowResponse` entries with `source = "builtin"`.
fn discover_builtin_workflows() -> Vec<WorkflowResponse> {
    let home = directories::UserDirs::new()
        .map(|u| u.home_dir().to_path_buf())
        .unwrap_or_default();
    let builtins_dir = home.join(BUILTIN_WORKFLOWS_DIR);
    let Ok(entries) = std::fs::read_dir(&builtins_dir) else {
        return Vec::new();
    };

    let mut workflows = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
        if ext != "yaml" && ext != "yml" {
            continue;
        }
        let Ok(content) = std::fs::read_to_string(&path) else {
            continue;
        };
        // Extract name, description, tags from YAML frontmatter (lightweight parse)
        let name = extract_yaml_field(&content, "name").unwrap_or_else(|| {
            path.file_stem()
                .unwrap_or_default()
                .to_string_lossy()
                .into_owned()
        });
        let description = extract_yaml_field(&content, "description").unwrap_or_default();
        let version = extract_yaml_field(&content, "version").unwrap_or_else(|| "1.0".into());
        let tags_str = extract_yaml_field(&content, "tags").unwrap_or_default();
        let tags: Vec<String> = if tags_str.is_empty() {
            Vec::new()
        } else {
            // Parse [tag1, tag2] format
            tags_str
                .trim_start_matches('[')
                .trim_end_matches(']')
                .split(',')
                .map(|s| s.trim().trim_matches('"').trim_matches('\'').to_string())
                .filter(|s| !s.is_empty())
                .collect()
        };
        let steps = count_yaml_steps(&content);
        let item_name = slugify(&name);

        let triggers = extract_triggers(&content);
        workflows.push(WorkflowResponse {
            kref: format!("builtin://{item_name}"),
            name,
            item_name,
            deprecated: false,
            created_at: None,
            description,
            definition: content,
            version,
            tags,
            steps,
            revision_number: 0,
            source: "builtin".to_string(),
            triggers,
        });
    }
    workflows
}

/// Extract a top-level scalar field from YAML content (lightweight, no full parser).
fn extract_yaml_field(content: &str, field: &str) -> Option<String> {
    for line in content.lines() {
        let trimmed = line.trim();
        if let Some(rest) = trimmed.strip_prefix(field) {
            if let Some(value) = rest.strip_prefix(':') {
                let v = value.trim();
                // Strip quotes
                let v = v.trim_matches('"').trim_matches('\'');
                if !v.is_empty() {
                    return Some(v.to_string());
                }
            }
        }
        // Stop at steps/inputs — only look at frontmatter
        if trimmed == "steps:" || trimmed == "inputs:" {
            break;
        }
    }
    None
}

/// Extract trigger definitions from a YAML workflow definition (lightweight, no full parser).
///
/// Expects a `triggers:` top-level key containing a list of mappings with `on_kind`,
/// optional `on_tag` (defaults to `"ready"`), and optional `on_name_pattern`.
fn extract_triggers(content: &str) -> Vec<WorkflowTrigger> {
    let mut triggers = Vec::new();
    let mut in_triggers = false;
    let mut current_kind = String::new();
    let mut current_tag = String::new();
    let mut current_pattern = String::new();

    for line in content.lines() {
        let trimmed = line.trim();
        if trimmed == "triggers:" {
            in_triggers = true;
            continue;
        }
        if !in_triggers {
            continue;
        }
        // A non-indented, non-empty, non-comment line means we left the triggers block
        if !trimmed.is_empty()
            && !trimmed.starts_with('-')
            && !trimmed.starts_with('#')
            && !line.starts_with(' ')
            && !line.starts_with('\t')
        {
            break;
        }
        // New list item — flush previous if any
        if trimmed.starts_with("- ") {
            if !current_kind.is_empty() {
                triggers.push(WorkflowTrigger {
                    on_kind: std::mem::take(&mut current_kind),
                    on_tag: if current_tag.is_empty() {
                        "ready".to_string()
                    } else {
                        std::mem::take(&mut current_tag)
                    },
                    on_name_pattern: std::mem::take(&mut current_pattern),
                });
            }
            // Parse inline key on the `- ` line (e.g. `- on_kind: model`)
            let after_dash = trimmed.strip_prefix("- ").unwrap_or("");
            if let Some((k, v)) = after_dash.split_once(':') {
                let k = k.trim();
                let v = v.trim().trim_matches('"').trim_matches('\'');
                match k {
                    "on_kind" => current_kind = v.to_string(),
                    "on_tag" => current_tag = v.to_string(),
                    "on_name_pattern" => current_pattern = v.to_string(),
                    _ => {}
                }
            }
            continue;
        }
        // Continuation key within a list item
        if let Some((k, v)) = trimmed.split_once(':') {
            let k = k.trim();
            let v = v.trim().trim_matches('"').trim_matches('\'');
            match k {
                "on_kind" => current_kind = v.to_string(),
                "on_tag" => current_tag = v.to_string(),
                "on_name_pattern" => current_pattern = v.to_string(),
                _ => {}
            }
        }
    }
    // Flush last trigger
    if !current_kind.is_empty() {
        triggers.push(WorkflowTrigger {
            on_kind: current_kind,
            on_tag: if current_tag.is_empty() {
                "ready".to_string()
            } else {
                current_tag
            },
            on_name_pattern: current_pattern,
        });
    }
    triggers
}

/// Extract cron trigger expressions from a workflow YAML definition (lightweight, no full parser).
///
/// Expects a `triggers:` top-level key containing list items with a `cron:` field and optional
/// `timezone:` field.  Returns `Vec<(cron_expression, optional_timezone)>`.
fn extract_cron_triggers(content: &str) -> Vec<(String, Option<String>)> {
    let mut results = Vec::new();
    let mut in_triggers = false;
    let mut current_cron = String::new();
    let mut current_tz: Option<String> = None;

    for line in content.lines() {
        let trimmed = line.trim();
        if trimmed == "triggers:" {
            in_triggers = true;
            continue;
        }
        if !in_triggers {
            continue;
        }
        // A non-indented, non-empty, non-comment line means we left the triggers block
        if !trimmed.is_empty()
            && !trimmed.starts_with('-')
            && !trimmed.starts_with('#')
            && !line.starts_with(' ')
            && !line.starts_with('\t')
        {
            break;
        }
        // New list item — flush previous if any
        if trimmed.starts_with("- ") {
            if !current_cron.is_empty() {
                results.push((std::mem::take(&mut current_cron), current_tz.take()));
            }
            // Parse inline key on the `- ` line (e.g. `- cron: "0 9 * * *"`)
            let after_dash = trimmed.strip_prefix("- ").unwrap_or("");
            if let Some((k, v)) = after_dash.split_once(':') {
                let k = k.trim();
                let v = v.trim().trim_matches('"').trim_matches('\'');
                match k {
                    "cron" if !v.is_empty() => current_cron = v.to_string(),
                    "timezone" | "tz" if !v.is_empty() => current_tz = Some(v.to_string()),
                    _ => {}
                }
            }
            continue;
        }
        // Continuation key within a list item
        if let Some((k, v)) = trimmed.split_once(':') {
            let k = k.trim();
            let v = v.trim().trim_matches('"').trim_matches('\'');
            match k {
                "cron" if !v.is_empty() => current_cron = v.to_string(),
                "timezone" | "tz" if !v.is_empty() => current_tz = Some(v.to_string()),
                _ => {}
            }
        }
    }
    // Flush last trigger
    if !current_cron.is_empty() {
        results.push((current_cron, current_tz));
    }
    results
}

/// Sync cron triggers for a single workflow to the cron scheduler.
///
/// Removes any existing cron jobs for this workflow and re-creates them from
/// the triggers found in the current YAML definition.
/// Write the workflow YAML to ~/.construct/workflows/ and register a Kumiho artifact.
async fn persist_workflow_artifact(
    client: &KumihoClient,
    revision_kref: &str,
    revision_number: i32,
    workflow_name: &str,
    definition: &str,
) {
    let home = directories::UserDirs::new()
        .map(|u| u.home_dir().to_path_buf())
        .unwrap_or_default();
    let dir = home.join(".construct/workflows");
    let _ = tokio::fs::create_dir_all(&dir).await;

    let slug = slugify(workflow_name);
    let file_path = dir.join(format!("{slug}.r{revision_number}.yaml"));
    let location = format!("file://{}", file_path.display());

    if let Err(e) = tokio::fs::write(&file_path, definition).await {
        tracing::warn!("Failed to write workflow YAML for {workflow_name}: {e}");
        return;
    }

    if let Err(e) = client
        .create_artifact(revision_kref, "workflow.yaml", &location, HashMap::new())
        .await
    {
        tracing::warn!("Failed to create artifact for workflow {workflow_name}: {e}");
    } else {
        tracing::info!("Persisted workflow artifact: {location}");
    }
}

fn sync_cron_for_workflow(state: &AppState, workflow_name: &str, definition: &str) {
    let cron_triggers = extract_cron_triggers(definition);
    let config = state.config.lock();

    // Remove existing cron jobs for this workflow first
    if let Err(e) = crate::cron::remove_workflow_cron_jobs(&config, workflow_name) {
        tracing::warn!("Failed to remove old cron jobs for workflow {workflow_name}: {e}");
    }

    if cron_triggers.is_empty() {
        return;
    }

    let wf_crons: Vec<(String, String, Option<String>)> = cron_triggers
        .into_iter()
        .map(|(expr, tz)| (workflow_name.to_string(), expr, tz))
        .collect();

    if let Err(e) = crate::cron::sync_workflow_cron_jobs(&config, &wf_crons) {
        tracing::warn!("Failed to sync cron triggers for workflow {workflow_name}: {e}");
    }
}

/// Merge builtin workflows with Kumiho workflows.
///
/// - Builtins whose `item_name` matches a Kumiho item are marked `"builtin-modified"`.
/// - Unmatched builtins are included as `"builtin"`.
/// - Kumiho-only workflows remain `"custom"`.
fn merge_with_builtins(mut kumiho_workflows: Vec<WorkflowResponse>) -> Vec<WorkflowResponse> {
    let builtins = discover_builtin_workflows();
    if builtins.is_empty() {
        return kumiho_workflows;
    }

    let builtin_names: std::collections::HashSet<String> =
        builtins.iter().map(|b| b.item_name.clone()).collect();

    // Tag Kumiho workflows that override a builtin
    for wf in &mut kumiho_workflows {
        if builtin_names.contains(&wf.item_name) {
            wf.source = "builtin-modified".to_string();
        }
    }

    // Add builtins that have no Kumiho override
    let kumiho_names: std::collections::HashSet<String> = kumiho_workflows
        .iter()
        .map(|w| w.item_name.clone())
        .collect();
    for builtin in builtins {
        if !kumiho_names.contains(&builtin.item_name) {
            kumiho_workflows.push(builtin);
        }
    }

    kumiho_workflows
}

// ── Definition Handlers ─────────────────────────────────────────────────

/// GET /api/workflows
pub async fn handle_list_workflows(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(query): Query<WorkflowListQuery>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let client = build_kumiho_client(&state);
    let project = workflow_project(&state);
    let space_path = workflow_space_path(&state);

    // Return cached result if available (before making API call)
    if query.q.is_none() {
        if let Some(cached) = get_cached(query.include_deprecated) {
            return Json(serde_json::json!({ "workflows": cached })).into_response();
        }
    }

    let items_result = if let Some(ref q) = query.q {
        client
            .search_items(q, &project, "workflow", query.include_deprecated)
            .await
            .map(|results| results.into_iter().map(|sr| sr.item).collect::<Vec<_>>())
    } else {
        client
            .list_items(&space_path, query.include_deprecated)
            .await
    };

    match items_result {
        Ok(items) => {
            let workflows = merge_with_builtins(enrich_items(&client, items).await);
            if query.q.is_none() {
                set_cached(&workflows, query.include_deprecated);
            }
            Json(serde_json::json!({ "workflows": workflows })).into_response()
        }
        Err(ref e) if matches!(e, KumihoError::Api { status: 404, .. }) => {
            let _ = client.ensure_project(&project).await;
            let _ = client.ensure_space(&project, WORKFLOW_SPACE_NAME).await;
            let workflows = merge_with_builtins(Vec::new());
            Json(serde_json::json!({ "workflows": workflows })).into_response()
        }
        Err(e) => {
            // On API error, try to return stale cache rather than an error
            if query.q.is_none() {
                let lock = WORKFLOW_CACHE.get_or_init(|| Mutex::new(None));
                let cache = lock.lock();
                if let Some(ref c) = *cache {
                    tracing::warn!("Workflows list failed, returning stale cache: {e}");
                    return Json(serde_json::json!({ "workflows": c.workflows })).into_response();
                }
            }
            kumiho_err(e).into_response()
        }
    }
}

/// Result of calling the operator's `validate_workflow` MCP tool.
///
/// Mirrors the Python-side `ValidationResult.to_dict()` shape:
/// `{ valid: bool, errors: [...], warnings: [...] }`. Unwraps the MCP
/// `content[0].text` envelope.
#[derive(Debug)]
struct ValidationOutcome {
    valid: bool,
    errors: Vec<serde_json::Value>,
    warnings: Vec<serde_json::Value>,
}

/// Call the operator's `validate_workflow` tool via MCP. Returns a structured
/// outcome. Any transport/parse failure is returned as `Err(String)` — callers
/// should fail-open (allow the operation) rather than block on infra errors.
async fn validate_via_operator(
    state: &AppState,
    args: serde_json::Map<String, serde_json::Value>,
) -> Result<ValidationOutcome, String> {
    let tool_name = format!(
        "{}__validate_workflow",
        crate::agent::operator::OPERATOR_SERVER_NAME
    );

    let registry = state
        .mcp_registry
        .as_ref()
        .ok_or_else(|| "MCP registry not available — operator not connected".to_string())?;

    let fut = registry.call_tool(&tool_name, serde_json::Value::Object(args));
    let result_str = match tokio::time::timeout(std::time::Duration::from_secs(15), fut).await {
        Ok(Ok(s)) => s,
        Ok(Err(e)) => return Err(format!("operator validate_workflow failed: {e:#}")),
        Err(_) => return Err("operator validate_workflow timed out (15s)".to_string()),
    };

    // Outer MCP envelope: { "content": [{"type":"text","text":"<json>"}], ... }
    let outer: serde_json::Value = serde_json::from_str(&result_str)
        .map_err(|e| format!("validate_workflow: outer JSON parse failed: {e}"))?;

    let inner_text = outer
        .get("content")
        .and_then(|c| c.get(0))
        .and_then(|c0| c0.get("text"))
        .and_then(|t| t.as_str())
        .ok_or_else(|| "validate_workflow: missing content[0].text".to_string())?;

    let inner: serde_json::Value = serde_json::from_str(inner_text)
        .map_err(|e| format!("validate_workflow: inner JSON parse failed: {e}"))?;

    let valid = inner
        .get("valid")
        .and_then(|v| v.as_bool())
        .ok_or_else(|| "validate_workflow: missing `valid` field".to_string())?;
    let errors = inner
        .get("errors")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    let warnings = inner
        .get("warnings")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    Ok(ValidationOutcome {
        valid,
        errors,
        warnings,
    })
}

/// Build the 400 response body for a failed validation.
fn validation_error_response(
    outcome: &ValidationOutcome,
    context: &str,
) -> (StatusCode, Json<serde_json::Value>) {
    (
        StatusCode::BAD_REQUEST,
        Json(serde_json::json!({
            "error": format!("Workflow validation failed: {context}"),
            "valid": false,
            "errors": outcome.errors,
            "warnings": outcome.warnings,
        })),
    )
}

/// Broadcast a `workflow.revision.published` event to all SSE subscribers.
///
/// Echoes the optional `X-Construct-Session` request header back as
/// `originating_session` so the editor can suppress events it itself caused.
/// Failures on the broadcast channel are non-fatal (subscriber lag).
fn broadcast_revision_published(
    state: &AppState,
    headers: &HeaderMap,
    workflow_kref: &str,
    rev: &RevisionResponse,
    name: &str,
) {
    let originating_session = headers
        .get("x-construct-session")
        .and_then(|v| v.to_str().ok())
        .map(|s| s.to_string());

    let published_at = rev
        .created_at
        .clone()
        .unwrap_or_else(|| chrono::Utc::now().to_rfc3339());

    let payload = serde_json::json!({
        "type": "workflow.revision.published",
        "workflow_kref": workflow_kref,
        "revision_kref": rev.kref,
        "revision_number": rev.number,
        "name": name,
        "published_at": published_at,
        "originating_session": originating_session,
    });

    if let Err(err) = state.event_tx.send(payload) {
        tracing::debug!("workflow.revision.published broadcast skipped: {err}");
    }
}

/// POST /api/workflows
pub async fn handle_create_workflow(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<CreateWorkflowBody>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    // Validate YAML before persisting — reject syntactically or schematically broken
    // definitions so they never reach storage (and thus never silently fail at dispatch).
    let mut v_args = serde_json::Map::new();
    v_args.insert(
        "workflow_yaml".to_string(),
        serde_json::Value::String(body.definition.clone()),
    );
    match validate_via_operator(&state, v_args).await {
        Ok(outcome) if !outcome.valid => {
            return validation_error_response(&outcome, "cannot save invalid workflow")
                .into_response();
        }
        Ok(_) => {}
        Err(e) => {
            tracing::warn!("create_workflow: validation skipped (infra error): {e}");
        }
    }

    let client = build_kumiho_client(&state);
    let project = workflow_project(&state);
    let space_path = workflow_space_path(&state);

    if let Err(e) = client.ensure_project(&project).await {
        return kumiho_err(e).into_response();
    }
    if let Err(e) = client.ensure_space(&project, WORKFLOW_SPACE_NAME).await {
        return kumiho_err(e).into_response();
    }

    let slug = slugify(&body.name);
    let item = match client
        .create_item(&space_path, &slug, "workflow", HashMap::new())
        .await
    {
        Ok(item) => item,
        Err(e) => return kumiho_err(e).into_response(),
    };

    let metadata = workflow_metadata(&body);
    let rev = match client.create_revision(&item.kref, metadata).await {
        Ok(rev) => rev,
        Err(e) => return kumiho_err(e).into_response(),
    };

    // Persist YAML to disk and register artifact BEFORE publishing (published revisions are immutable)
    persist_workflow_artifact(&client, &rev.kref, rev.number, &body.name, &body.definition).await;
    let _ = client.tag_revision(&rev.kref, "published").await;

    invalidate_cache();
    sync_cron_for_workflow(&state, &body.name, &body.definition);

    broadcast_revision_published(&state, &headers, &item.kref, &rev, &body.name);

    let workflow = to_workflow_response(&item, Some(&rev));
    (
        StatusCode::CREATED,
        Json(serde_json::json!({ "workflow": workflow })),
    )
        .into_response()
}

/// PUT /api/workflows/{*kref}
pub async fn handle_update_workflow(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(kref): Path<String>,
    Json(body): Json<CreateWorkflowBody>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    // Validate YAML before persisting the new revision.
    let mut v_args = serde_json::Map::new();
    v_args.insert(
        "workflow_yaml".to_string(),
        serde_json::Value::String(body.definition.clone()),
    );
    match validate_via_operator(&state, v_args).await {
        Ok(outcome) if !outcome.valid => {
            return validation_error_response(&outcome, "cannot save invalid workflow")
                .into_response();
        }
        Ok(_) => {}
        Err(e) => {
            tracing::warn!("update_workflow: validation skipped (infra error): {e}");
        }
    }

    let kref = format!("kref://{kref}");
    let client = build_kumiho_client(&state);

    let metadata = workflow_metadata(&body);
    let rev = match client.create_revision(&kref, metadata).await {
        Ok(rev) => rev,
        Err(e) => return kumiho_err(e).into_response(),
    };

    // Persist YAML to disk and register artifact BEFORE publishing (published revisions are immutable)
    persist_workflow_artifact(&client, &rev.kref, rev.number, &body.name, &body.definition).await;
    let _ = client.tag_revision(&rev.kref, "published").await;

    let items = match client.list_items(&workflow_space_path(&state), true).await {
        Ok(items) => items,
        Err(e) => return kumiho_err(e).into_response(),
    };

    invalidate_cache();
    sync_cron_for_workflow(&state, &body.name, &body.definition);

    broadcast_revision_published(&state, &headers, &kref, &rev, &body.name);

    let item = items.iter().find(|i| i.kref == kref);
    match item {
        Some(item) => {
            let workflow = to_workflow_response(item, Some(&rev));
            Json(serde_json::json!({ "workflow": workflow })).into_response()
        }
        None => {
            let fallback = ItemResponse {
                kref: kref.clone(),
                name: body.name.clone(),
                item_name: body.name.clone(),
                kind: "workflow".to_string(),
                deprecated: false,
                created_at: None,
                metadata: HashMap::new(),
            };
            let workflow = to_workflow_response(&fallback, Some(&rev));
            Json(serde_json::json!({ "workflow": workflow })).into_response()
        }
    }
}

/// POST /api/workflows/deprecate
pub async fn handle_deprecate_workflow(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<DeprecateBody>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let kref = body.kref.clone();
    let client = build_kumiho_client(&state);

    match client.deprecate_item(&kref, body.deprecated).await {
        Ok(item) => {
            invalidate_cache();
            let rev = client.get_published_or_latest(&kref).await.ok();

            // Sync cron triggers: remove when deprecating, re-add when restoring.
            if body.deprecated {
                // Remove cron jobs for this workflow
                if let Some(item_segment) = kref.split('/').last() {
                    let workflow_name = item_segment
                        .rsplit_once('.')
                        .map(|(name, _kind)| name)
                        .unwrap_or(item_segment);
                    let config = state.config.lock();
                    if let Err(e) = crate::cron::remove_workflow_cron_jobs(&config, workflow_name) {
                        tracing::warn!("Failed to remove cron jobs for deprecated workflow: {e}");
                    }
                }
            } else if let Some(ref rev) = rev {
                // Restoring — re-sync cron triggers from the definition
                if let Some(definition) = rev.metadata.get("definition") {
                    sync_cron_for_workflow(&state, &item.item_name, definition);
                }
            }

            let workflow = to_workflow_response(&item, rev.as_ref());
            Json(serde_json::json!({ "workflow": workflow })).into_response()
        }
        Err(e) => kumiho_err(e).into_response(),
    }
}

/// DELETE /api/workflows/{*kref}
pub async fn handle_delete_workflow(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(kref): Path<String>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let kref = format!("kref://{kref}");
    let client = build_kumiho_client(&state);

    match client.delete_item(&kref).await {
        Ok(()) => {
            invalidate_cache();

            // Remove associated cron jobs.  Extract the item_name from the kref
            // (the last path segment minus the `.workflow` kind suffix) and use
            // it as the workflow name for cron cleanup.
            if let Some(item_segment) = kref.split('/').last() {
                let workflow_name = item_segment
                    .rsplit_once('.')
                    .map(|(name, _kind)| name)
                    .unwrap_or(item_segment);
                let config = state.config.lock();
                if let Err(e) = crate::cron::remove_workflow_cron_jobs(&config, workflow_name) {
                    tracing::warn!("Failed to remove cron jobs for deleted workflow: {e}");
                }
            }

            StatusCode::NO_CONTENT.into_response()
        }
        Err(e) => kumiho_err(e).into_response(),
    }
}

/// POST /api/workflows/run/{name}
///
/// Triggers a workflow run request.  Creates a `workflow-run-request` item in
/// Kumiho so the scheduler or operator can pick it up.
pub async fn handle_run_workflow(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(name): Path<String>,
    body: Option<Json<RunWorkflowBody>>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let inputs = body
        .as_ref()
        .map(|b| b.inputs.clone())
        .unwrap_or(serde_json::Value::Object(Default::default()));
    let cwd = body.as_ref().and_then(|b| b.cwd.clone());

    // Pre-dispatch validation: resolve the named workflow (from builtins/Kumiho)
    // and run the schema validator. Blocks silent failures where a malformed
    // definition gets enqueued but the async runner can't parse it.
    let mut v_args = serde_json::Map::new();
    v_args.insert(
        "workflow".to_string(),
        serde_json::Value::String(name.clone()),
    );
    if let Some(ref c) = cwd {
        v_args.insert("cwd".to_string(), serde_json::Value::String(c.clone()));
    }
    match validate_via_operator(&state, v_args).await {
        Ok(outcome) if !outcome.valid => {
            return validation_error_response(&outcome, "cannot dispatch invalid workflow")
                .into_response();
        }
        Ok(_) => {}
        Err(e) => {
            tracing::warn!("run_workflow: validation skipped (infra error): {e}");
        }
    }

    let run_id = uuid::Uuid::new_v4().to_string();
    let now = chrono::Utc::now().to_rfc3339();

    let client = build_kumiho_client(&state);
    let project = workflow_project(&state);
    let requests_space_path = workflow_run_requests_space_path(&state);

    // Ensure the WorkflowRunRequests space exists
    let _ = client.ensure_project(&project).await;
    let _ = client
        .ensure_space(&project, WORKFLOW_RUN_REQUESTS_SPACE_NAME)
        .await;

    let mut metadata = HashMap::new();
    metadata.insert("workflow_name".to_string(), name.clone());
    metadata.insert("run_id".to_string(), run_id.clone());
    metadata.insert("inputs".to_string(), inputs.to_string());
    metadata.insert("cwd".to_string(), cwd.unwrap_or_default());
    metadata.insert("trigger_source".to_string(), "api".to_string());
    metadata.insert("requested_at".to_string(), now);

    let item_name = format!("run-{}", &run_id[..run_id.len().min(12)]);

    match client
        .create_item(
            &requests_space_path,
            &item_name,
            "workflow-run-request",
            metadata.clone(),
        )
        .await
    {
        Ok(item) => {
            if let Ok(rev) = client.create_revision(&item.kref, metadata).await {
                let _ = client.tag_revision(&rev.kref, "pending").await;
            }

            // Direct invocation: kick the operator's run_workflow tool now so
            // the workflow starts within seconds instead of waiting up to 30s
            // for the run-request poller to pick the item up.  The Kumiho
            // `pending` item above remains as the durable record — if this
            // direct call fails (e.g. operator-mcp transport dropped), the
            // event listener / poller will still find it.
            //
            // tool_run_workflow itself detaches execution into a background
            // asyncio task and returns immediately, so the call should be
            // fast on success.  We still tokio::spawn it as fire-and-forget
            // because we don't want to hold the HTTP handler open at all.
            if let Some(registry) = state.mcp_registry.clone() {
                let tool_name = format!(
                    "{}__run_workflow",
                    crate::agent::operator::OPERATOR_SERVER_NAME
                );
                let mut tool_args = serde_json::Map::new();
                tool_args.insert(
                    "workflow".to_string(),
                    serde_json::Value::String(name.clone()),
                );
                tool_args.insert("inputs".to_string(), inputs.clone());
                tool_args.insert(
                    "cwd".to_string(),
                    serde_json::Value::String(
                        body.as_ref().and_then(|b| b.cwd.clone()).unwrap_or_default(),
                    ),
                );
                tool_args.insert(
                    "run_id".to_string(),
                    serde_json::Value::String(run_id.clone()),
                );
                let tool_args_val = serde_json::Value::Object(tool_args);
                let run_id_for_log = run_id.clone();
                let workflow_name_for_log = name.clone();
                tokio::spawn(async move {
                    let fut = registry.call_tool(&tool_name, tool_args_val);
                    match tokio::time::timeout(std::time::Duration::from_secs(30), fut).await {
                        Ok(Ok(payload)) => {
                            // Distinguish a real "started" from a classified-error payload.
                            // tool_run_workflow returns Ok at the MCP transport level even on
                            // validation failures (missing_cwd, not_found, etc.) — the inner
                            // result dict carries the diagnosis. Without this check we'd log
                            // "direct dispatch ok" while the workflow never started.
                            let inner = serde_json::from_str::<serde_json::Value>(&payload)
                                .ok()
                                .and_then(|outer| {
                                    outer
                                        .get("content")
                                        .and_then(|c| c.get(0))
                                        .and_then(|c0| c0.get("text"))
                                        .and_then(|t| t.as_str())
                                        .and_then(|s| serde_json::from_str::<serde_json::Value>(s).ok())
                                });
                            let inner_status = inner
                                .as_ref()
                                .and_then(|i| i.get("status"))
                                .and_then(|s| s.as_str())
                                .unwrap_or("");
                            let inner_error = inner
                                .as_ref()
                                .and_then(|i| i.get("error"))
                                .and_then(|s| s.as_str());
                            if inner_status == "started" {
                                tracing::info!(
                                    "run_workflow direct dispatch started: workflow={} run_id={}",
                                    workflow_name_for_log,
                                    run_id_for_log
                                );
                            } else if let Some(err) = inner_error {
                                tracing::warn!(
                                    "run_workflow direct dispatch returned error (Kumiho pending item will be picked up by listener/poller): workflow={} run_id={} err={err}",
                                    workflow_name_for_log,
                                    run_id_for_log
                                );
                            } else {
                                tracing::debug!(
                                    "run_workflow direct dispatch returned unexpected payload (listener/poller will handle): workflow={} run_id={}",
                                    workflow_name_for_log,
                                    run_id_for_log
                                );
                            }
                        }
                        Ok(Err(e)) => {
                            tracing::warn!(
                                "run_workflow direct dispatch failed (Kumiho pending item will be picked up by listener/poller): workflow={} run_id={} err={e:#}",
                                workflow_name_for_log,
                                run_id_for_log
                            );
                        }
                        Err(_) => {
                            tracing::warn!(
                                "run_workflow direct dispatch timed out after 30s (Kumiho pending item will be picked up by listener/poller): workflow={} run_id={}",
                                workflow_name_for_log,
                                run_id_for_log
                            );
                        }
                    }
                });
            } else {
                tracing::debug!(
                    "run_workflow: MCP registry not available — relying on event listener / poller for run_id={run_id}"
                );
            }

            (
                StatusCode::OK,
                Json(serde_json::json!({
                    "run_id": run_id,
                    "workflow": name,
                    "status": "pending",
                })),
            )
                .into_response()
        }
        Err(e) => {
            tracing::warn!("Failed to create workflow run request: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({
                    "error": format!("Failed to create run request: {e}")
                })),
            )
                .into_response()
        }
    }
}

/// GET /api/workflows/revisions/{*kref}
///
/// Fetches a workflow definition pinned to a specific Kumiho revision kref
/// (e.g. `kref://Construct/Workflows/my-wf.workflow?r=3`). Used by the dashboard
/// DAG viewer to render the exact YAML a run executed, independent of whatever
/// is currently tagged `published` on the workflow item.
pub async fn handle_get_workflow_by_revision(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(kref): Path<String>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let revision_kref = if kref.starts_with("kref://") {
        kref.clone()
    } else {
        format!("kref://{kref}")
    };

    let client = build_kumiho_client(&state);

    let mut rev = match client.get_revision(&revision_kref).await {
        Ok(r) => r,
        Err(e) => return kumiho_err(e).into_response(),
    };

    // Canonical source for a pinned revision's YAML is the `workflow.yaml`
    // artifact on disk. The inline `definition` metadata key is a legacy
    // gateway-authored field and drifts from the artifact for operator-
    // authored revisions, so we always prefer the artifact and only fall
    // back to metadata when no artifact exists.
    if let Ok(artifact) = client
        .get_artifact_by_name(&rev.kref, "workflow.yaml")
        .await
    {
        let path = artifact
            .location
            .strip_prefix("file://")
            .unwrap_or(&artifact.location);
        if let Ok(yaml) = tokio::fs::read_to_string(path).await {
            rev.metadata.insert("definition".to_string(), yaml);
        }
    }

    // Derive a minimal item from the revision's item_kref. The DAG viewer only
    // consumes `definition` (YAML) and `revision_number` from the response.
    let item_name = rev
        .item_kref
        .rsplit('/')
        .next()
        .map(|seg| {
            seg.rsplit_once('.')
                .map(|(n, _)| n)
                .unwrap_or(seg)
                .to_string()
        })
        .unwrap_or_default();

    let item = ItemResponse {
        kref: rev.item_kref.clone(),
        name: item_name.clone(),
        item_name,
        kind: "workflow".to_string(),
        deprecated: false,
        created_at: rev.created_at.clone(),
        metadata: HashMap::new(),
    };

    let workflow = to_workflow_response(&item, Some(&rev));
    Json(serde_json::json!({ "workflow": workflow })).into_response()
}

// ── Run Handlers ────────────────────────────────────────────────────────

/// GET /api/workflows/runs
pub async fn handle_list_workflow_runs(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(query): Query<WorkflowRunsQuery>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let client = build_kumiho_client(&state);
    let project = workflow_project(&state);
    let runs_space = workflow_runs_space_path(&state);

    match client.list_items(&runs_space, false).await {
        Ok(mut items) => {
            // Only include workflow_run kind items
            items.retain(|i| i.kind == "workflow_run");

            if let Some(ref wf_name) = query.workflow {
                items.retain(|item| {
                    item.metadata
                        .get("workflow_name")
                        .or_else(|| item.metadata.get("workflow"))
                        .map(|n| n == wf_name)
                        .unwrap_or(false)
                });
            }

            items.sort_by(|a, b| {
                let a_time = a.created_at.as_deref().unwrap_or("");
                let b_time = b.created_at.as_deref().unwrap_or("");
                b_time.cmp(a_time)
            });
            items.truncate(query.limit);

            let krefs: Vec<String> = items.iter().map(|i| i.kref.clone()).collect();
            let rev_map = client
                .batch_get_revisions(&krefs, "latest")
                .await
                .unwrap_or_default();

            let runs: Vec<WorkflowRunSummary> = items
                .iter()
                .map(|item| to_run_summary(item, rev_map.get(&item.kref)))
                .collect();

            Json(serde_json::json!({ "runs": runs, "count": runs.len() })).into_response()
        }
        Err(ref e) if matches!(e, KumihoError::Api { status: 404, .. }) => {
            let _ = client.ensure_project(&project).await;
            let _ = client
                .ensure_space(&project, WORKFLOW_RUNS_SPACE_NAME)
                .await;
            Json(serde_json::json!({ "runs": [], "count": 0 })).into_response()
        }
        Err(e) => {
            let msg = format!("Failed to fetch workflow runs: {e}");
            (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(serde_json::json!({ "error": msg })),
            )
                .into_response()
        }
    }
}

/// GET /api/workflows/runs/{run_id}
pub async fn handle_get_workflow_run(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(run_id): Path<String>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let client = build_kumiho_client(&state);
    let project = workflow_project(&state);
    let runs_space = workflow_runs_space_path(&state);

    // The item name format is "{workflow_name}-{run_id[:12]}", so the first
    // 12 characters of the run_id are always present in the item name.
    let run_id_prefix = &run_id[..run_id.len().min(12)];

    // Strategy 1 (most reliable): filter items by name containing the run_id
    // prefix.  This avoids fulltext-search indexing lag and does not depend
    // on item metadata being returned in the list response.
    if let Ok(items) = client
        .list_items_filtered(&runs_space, run_id_prefix, false)
        .await
    {
        // Narrow to workflow_run kind items whose name actually contains the prefix
        let run_id_lower = run_id.to_lowercase();
        let prefix_lower = run_id_lower[..run_id_lower.len().min(12)].to_string();
        if let Some(item) = items.iter().find(|i| {
            i.kind == "workflow_run" && i.item_name.to_lowercase().contains(&prefix_lower)
        }) {
            let rev = client.get_latest_revision(&item.kref).await.ok();
            let detail = to_run_detail(item, rev.as_ref());
            return Json(serde_json::json!({ "run": detail })).into_response();
        }
    }

    // Strategy 2: full-text search by run_id (may find it if indexed in item
    // metadata or if the run_id appears in the item name).
    if let Ok(results) = client
        .search_items(&run_id, &project, "workflow_run", false)
        .await
    {
        if let Some(sr) = results.first() {
            let rev = client.get_latest_revision(&sr.item.kref).await.ok();
            let detail = to_run_detail(&sr.item, rev.as_ref());
            return Json(serde_json::json!({ "run": detail })).into_response();
        }
    }

    // Strategy 3: broad list + metadata/name match as last resort
    match client.list_items(&runs_space, false).await {
        Ok(items) => {
            let run_id_lower = run_id.to_lowercase();
            let found = items.iter().find(|item| {
                if item.kind != "workflow_run" {
                    return false;
                }
                // Match by metadata run_id (if metadata is returned)
                if let Some(meta_run_id) = item.metadata.get("run_id") {
                    if meta_run_id == &run_id {
                        return true;
                    }
                }
                // Match by item_name containing the run_id prefix (first 12 chars)
                let prefix = &run_id_lower[..run_id_lower.len().min(12)];
                item.item_name.to_lowercase().contains(prefix)
            });

            match found {
                Some(item) => {
                    let rev = client.get_latest_revision(&item.kref).await.ok();
                    let detail = to_run_detail(item, rev.as_ref());
                    Json(serde_json::json!({ "run": detail })).into_response()
                }
                None => (
                    StatusCode::NOT_FOUND,
                    Json(serde_json::json!({ "error": format!("Run '{run_id}' not found") })),
                )
                    .into_response(),
            }
        }
        Err(e) => {
            let msg = format!("Kumiho error looking up run '{run_id}': {e}");
            tracing::warn!("{msg}");
            (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(serde_json::json!({ "error": msg })),
            )
                .into_response()
        }
    }
}

/// DELETE /api/workflows/runs/{run_id}
///
/// Deletes a workflow run from the WorkflowRuns space.  Finds the item by
/// run_id prefix matching (same strategy as the GET handler) then calls
/// `delete_item` on the resolved kref.
pub async fn handle_delete_workflow_run(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(run_id): Path<String>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let client = build_kumiho_client(&state);
    let runs_space = workflow_runs_space_path(&state);

    // Resolve the run item — reuse the same prefix-matching logic as the GET
    let run_id_prefix = &run_id[..run_id.len().min(12)];

    let kref = if let Ok(items) = client
        .list_items_filtered(&runs_space, run_id_prefix, false)
        .await
    {
        let run_id_lower = run_id.to_lowercase();
        let prefix_lower = run_id_lower[..run_id_lower.len().min(12)].to_string();
        items
            .iter()
            .find(|i| {
                i.kind == "workflow_run" && i.item_name.to_lowercase().contains(&prefix_lower)
            })
            .map(|i| i.kref.clone())
    } else {
        None
    };

    let kref = match kref {
        Some(k) => k,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({ "error": format!("Run '{run_id}' not found") })),
            )
                .into_response();
        }
    };

    match client.delete_item(&kref).await {
        Ok(()) => {
            cleanup_local_run_files(&run_id).await;
            StatusCode::NO_CONTENT.into_response()
        }
        Err(e) => {
            let msg = format!("Failed to delete run '{run_id}': {e}");
            tracing::warn!("{msg}");
            kumiho_err(e).into_response()
        }
    }
}

/// Best-effort cleanup of on-disk run state after a successful Kumiho hard delete.
/// Removes the checkpoint at `~/.construct/workflow_checkpoints/{run_id}.json` and
/// any artifacts directory at `~/.construct/artifacts/<workflow>/{run_id}/`. Since
/// the workflow name isn't carried into this handler, we scan the artifacts root
/// for any subdirectory containing a matching run_id directory. Failures are logged
/// but do not affect the API response — the authoritative delete already succeeded.
async fn cleanup_local_run_files(run_id: &str) {
    let Some(user_dirs) = directories::UserDirs::new() else {
        return;
    };
    let home = user_dirs.home_dir().to_path_buf();

    let checkpoint = home.join(format!(".construct/workflow_checkpoints/{run_id}.json"));
    if let Err(e) = tokio::fs::remove_file(&checkpoint).await {
        if e.kind() != std::io::ErrorKind::NotFound {
            tracing::warn!("Failed to remove checkpoint {}: {e}", checkpoint.display());
        }
    }

    let artifacts_root = home.join(".construct/artifacts");
    let mut entries = match tokio::fs::read_dir(&artifacts_root).await {
        Ok(e) => e,
        Err(_) => return,
    };
    while let Ok(Some(entry)) = entries.next_entry().await {
        let candidate = entry.path().join(run_id);
        if tokio::fs::metadata(&candidate).await.is_ok() {
            if let Err(e) = tokio::fs::remove_dir_all(&candidate).await {
                tracing::warn!(
                    "Failed to remove artifacts dir {}: {e}",
                    candidate.display()
                );
            }
        }
    }
}

/// POST /api/workflows/runs/{run_id}/approve
///
/// Body: { "approved": bool, "feedback": string (optional) }
///
/// Approves or rejects a paused workflow step. Atomically claims the approval
/// from the registry to prevent race conditions with Discord.
pub async fn handle_approve_workflow_run(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(run_id): Path<String>,
    Json(body): Json<ApproveWorkflowBody>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let approved = body.approved;
    let feedback = body.feedback.unwrap_or_default();

    // Atomically claim the approval from the registry to prevent races with Discord.
    // If None returned, the registry may have been lost (gateway restart) — fall through
    // and call resume_workflow directly; the operator validates paused state itself.
    let claimed = state.approval_registry.try_claim(&run_id);
    let cwd = claimed
        .as_ref()
        .map(|a| a.cwd.clone())
        .unwrap_or_else(|| "/tmp".to_string());

    if claimed.is_none() {
        tracing::info!(
            "approve_workflow_run: no registry entry for run_id={run_id} (gateway restart?), \
             calling resume_workflow directly"
        );
    }

    // Call the operator MCP tool `resume_workflow`
    let tool_name = format!(
        "{}__resume_workflow",
        crate::agent::operator::OPERATOR_SERVER_NAME
    );
    let mut tool_args = serde_json::Map::new();
    tool_args.insert(
        "run_id".to_string(),
        serde_json::Value::String(run_id.clone()),
    );
    tool_args.insert("approved".to_string(), serde_json::Value::Bool(approved));
    tool_args.insert(
        "response".to_string(),
        serde_json::Value::String(feedback.clone()),
    );
    tool_args.insert("cwd".to_string(), serde_json::Value::String(cwd));

    let mcp_result = if let Some(ref registry) = state.mcp_registry {
        let mcp_future = registry.call_tool(&tool_name, serde_json::Value::Object(tool_args));
        match tokio::time::timeout(std::time::Duration::from_secs(30), mcp_future).await {
            Ok(Ok(result_str)) => Ok(result_str),
            Ok(Err(e)) => Err(format!("operator tool call failed: {e:#}")),
            Err(_) => Err("operator tool call timed out (30s)".to_string()),
        }
    } else {
        Err("MCP registry not available — operator not connected".to_string())
    };

    match mcp_result {
        Ok(_) => {
            // Broadcast a human_approval_resolved SSE event so connected dashboards
            // can update their UI immediately without waiting for the next REST poll.
            let _ = state.event_tx.send(serde_json::json!({
                "type": "human_approval_resolved",
                "run_id": run_id,
                "approved": approved,
                "timestamp": chrono::Utc::now().to_rfc3339(),
            }));

            (
                StatusCode::OK,
                Json(serde_json::json!({
                    "status": "ok",
                    "message": if approved { "Workflow approved" } else { "Workflow rejected" },
                    "run_id": run_id,
                    "approved": approved,
                })),
            )
                .into_response()
        }
        Err(e) => {
            tracing::warn!("approve_workflow_run: failed for run_id={run_id}: {e}");
            (
                StatusCode::BAD_GATEWAY,
                Json(serde_json::json!({
                    "error": format!("Failed to resume workflow: {e}")
                })),
            )
                .into_response()
        }
    }
}

#[derive(Deserialize)]
pub struct ApproveWorkflowBody {
    pub approved: bool,
    pub feedback: Option<String>,
}

/// POST /api/workflows/runs/{run_id}/retry
///
/// Body: { "cwd": string (optional) }
///
/// Retries a failed workflow run from the first failed step. Successful step
/// outputs are preserved so only the failed step + downstream steps re-execute.
pub async fn handle_retry_workflow_run(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(run_id): Path<String>,
    body: Option<Json<RetryWorkflowBody>>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let cwd = body
        .and_then(|Json(b)| b.cwd)
        .unwrap_or_else(|| "/tmp".to_string());

    let tool_name = format!(
        "{}__retry_workflow",
        crate::agent::operator::OPERATOR_SERVER_NAME
    );
    let mut tool_args = serde_json::Map::new();
    tool_args.insert(
        "run_id".to_string(),
        serde_json::Value::String(run_id.clone()),
    );
    tool_args.insert("cwd".to_string(), serde_json::Value::String(cwd));

    let mcp_result = if let Some(ref registry) = state.mcp_registry {
        let mcp_future = registry.call_tool(&tool_name, serde_json::Value::Object(tool_args));
        match tokio::time::timeout(std::time::Duration::from_secs(30), mcp_future).await {
            Ok(Ok(result_str)) => Ok(result_str),
            Ok(Err(e)) => Err(format!("operator tool call failed: {e:#}")),
            Err(_) => Err("operator tool call timed out (30s)".to_string()),
        }
    } else {
        Err("MCP registry not available — operator not connected".to_string())
    };

    match mcp_result {
        Ok(result_str) => {
            let _ = state.event_tx.send(serde_json::json!({
                "type": "workflow_retry",
                "run_id": run_id,
                "timestamp": chrono::Utc::now().to_rfc3339(),
            }));
            let payload = serde_json::from_str::<serde_json::Value>(&result_str)
                .unwrap_or_else(|_| serde_json::json!({"raw": result_str}));
            (StatusCode::OK, Json(payload)).into_response()
        }
        Err(e) => {
            tracing::warn!("retry_workflow_run: failed for run_id={run_id}: {e}");
            (
                StatusCode::BAD_GATEWAY,
                Json(serde_json::json!({ "error": format!("Failed to retry workflow: {e}") })),
            )
                .into_response()
        }
    }
}

#[derive(Deserialize)]
pub struct RetryWorkflowBody {
    pub cwd: Option<String>,
}

/// GET /api/workflows/agent-activity/{agent_id}
///
/// Reads the RunLog JSONL file for an agent and returns structured activity data.
/// Used by the Live Execution View for on-demand drill-down into agent tool calls,
/// messages, and results.
pub async fn handle_agent_activity(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(agent_id): Path<String>,
    Query(query): Query<AgentActivityQuery>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let runlogs_dir =
        std::path::PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| "/tmp".into()))
            .join(".construct/operator_mcp/runlogs");
    let path = runlogs_dir.join(format!("{agent_id}.jsonl"));

    if !path.exists() {
        return (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({ "error": "No run log found for this agent" })),
        )
            .into_response();
    }

    let content = match std::fs::read_to_string(&path) {
        Ok(c) => c,
        Err(e) => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({ "error": format!("Failed to read log: {e}") })),
            )
                .into_response();
        }
    };

    let view = query.view.as_deref().unwrap_or("summary");
    let limit = query.limit.unwrap_or(100).min(500) as usize;

    let entries: Vec<serde_json::Value> = content
        .lines()
        .filter_map(|line| serde_json::from_str(line).ok())
        .collect();

    match view {
        "tool_calls" => {
            // Return tool calls with full args and results
            let tools: Vec<&serde_json::Value> = entries
                .iter()
                .filter(|e| e.get("kind").and_then(|v| v.as_str()) == Some("tool_call"))
                .collect();
            let total = tools.len();
            let slice: Vec<_> = tools.into_iter().rev().take(limit).collect();
            Json(serde_json::json!({
                "agent_id": agent_id,
                "view": "tool_calls",
                "total": total,
                "entries": slice,
            }))
            .into_response()
        }
        "messages" => {
            // Return assistant messages
            let msgs: Vec<&serde_json::Value> = entries
                .iter()
                .filter(|e| {
                    let kind = e.get("kind").and_then(|v| v.as_str()).unwrap_or("");
                    kind == "message" || kind == "user_message"
                })
                .collect();
            let total = msgs.len();
            let slice: Vec<_> = msgs.into_iter().rev().take(limit).collect();
            Json(serde_json::json!({
                "agent_id": agent_id,
                "view": "messages",
                "total": total,
                "entries": slice,
            }))
            .into_response()
        }
        "errors" => {
            let errs: Vec<&serde_json::Value> = entries
                .iter()
                .filter(|e| {
                    let kind = e.get("kind").and_then(|v| v.as_str()).unwrap_or("");
                    kind == "error"
                        || kind == "turn_failed"
                        || e.get("status").and_then(|v| v.as_str()) == Some("failed")
                })
                .collect();
            Json(serde_json::json!({
                "agent_id": agent_id,
                "view": "errors",
                "total": errs.len(),
                "entries": errs,
            }))
            .into_response()
        }
        "full" => {
            // Last N entries (most recent)
            let total = entries.len();
            let slice: Vec<_> = entries.into_iter().rev().take(limit).collect();
            Json(serde_json::json!({
                "agent_id": agent_id,
                "view": "full",
                "total": total,
                "entries": slice,
            }))
            .into_response()
        }
        _ => {
            // Summary view: header + stats + last message + recent tool calls
            let header = entries.first().cloned().unwrap_or_default();
            let tool_count = entries
                .iter()
                .filter(|e| e.get("kind").and_then(|v| v.as_str()) == Some("tool_call"))
                .count();
            let error_count = entries
                .iter()
                .filter(|e| {
                    let kind = e.get("kind").and_then(|v| v.as_str()).unwrap_or("");
                    kind == "error" || kind == "turn_failed"
                })
                .count();
            let last_message = entries
                .iter()
                .rev()
                .find(|e| e.get("kind").and_then(|v| v.as_str()) == Some("message"))
                .and_then(|e| e.get("text").and_then(|v| v.as_str()))
                .unwrap_or("");
            // Truncate to reasonable size for summary
            let last_msg_truncated = if last_message.len() > 5000 {
                &last_message[..5000]
            } else {
                last_message
            };
            // Recent tool calls (last 20)
            let recent_tools: Vec<_> = entries
                .iter()
                .filter(|e| e.get("kind").and_then(|v| v.as_str()) == Some("tool_call"))
                .rev()
                .take(20)
                .cloned()
                .collect();
            // Usage stats from turn_completed entries
            let mut input_tokens: u64 = 0;
            let mut output_tokens: u64 = 0;
            let mut total_cost: f64 = 0.0;
            for e in &entries {
                if e.get("kind").and_then(|v| v.as_str()) == Some("turn_completed") {
                    if let Some(usage) = e.get("usage") {
                        input_tokens += usage
                            .get("inputTokens")
                            .and_then(|v| v.as_u64())
                            .unwrap_or(0);
                        output_tokens += usage
                            .get("outputTokens")
                            .and_then(|v| v.as_u64())
                            .unwrap_or(0);
                        total_cost += usage
                            .get("totalCostUsd")
                            .and_then(|v| v.as_f64())
                            .unwrap_or(0.0);
                    }
                }
            }
            Json(serde_json::json!({
                "agent_id": agent_id,
                "view": "summary",
                "title": header.get("title").and_then(|v| v.as_str()).unwrap_or(""),
                "agent_type": header.get("agent_type").and_then(|v| v.as_str()).unwrap_or(""),
                "total_events": entries.len(),
                "tool_call_count": tool_count,
                "error_count": error_count,
                "last_message": last_msg_truncated,
                "recent_tools": recent_tools,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_cost_usd": total_cost,
                },
            }))
            .into_response()
        }
    }
}

#[derive(Deserialize)]
pub struct AgentActivityQuery {
    view: Option<String>,
    limit: Option<u32>,
}

/// GET /api/workflows/dashboard
pub async fn handle_workflow_dashboard(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let client = build_kumiho_client(&state);
    let space_path = workflow_space_path(&state);
    let runs_space = workflow_runs_space_path(&state);

    // Fetch definitions from Kumiho + merge builtins
    let definitions = match client.list_items(&space_path, false).await {
        Ok(items) => merge_with_builtins(enrich_items(&client, items).await),
        Err(_) => merge_with_builtins(Vec::new()),
    };
    let definitions_count = definitions.len();

    // Fetch recent runs from Kumiho
    let (recent_runs, total_runs) = match client.list_items(&runs_space, false).await {
        Ok(mut items) => {
            let total = items.len();
            items.sort_by(|a, b| {
                let a_time = a.created_at.as_deref().unwrap_or("");
                let b_time = b.created_at.as_deref().unwrap_or("");
                b_time.cmp(a_time)
            });
            items.truncate(5);

            let krefs: Vec<String> = items.iter().map(|i| i.kref.clone()).collect();
            let rev_map = client
                .batch_get_revisions(&krefs, "latest")
                .await
                .unwrap_or_default();

            let runs: Vec<WorkflowRunSummary> = items
                .iter()
                .map(|item| to_run_summary(item, rev_map.get(&item.kref)))
                .collect();

            (runs, total)
        }
        Err(_) => (Vec::new(), 0),
    };

    let active_runs = recent_runs
        .iter()
        .filter(|r| r.status == "running" || r.status == "paused")
        .count();

    let dashboard = WorkflowDashboard {
        definitions_count,
        definitions,
        active_runs,
        recent_runs,
        total_runs,
    };

    Json(serde_json::json!({ "dashboard": dashboard })).into_response()
}
