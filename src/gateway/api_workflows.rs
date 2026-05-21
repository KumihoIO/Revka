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
use super::kumiho_client::build_kumiho_client;
use super::kumiho_client::invalidate_proxy_cache;
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
use std::time::{Duration, Instant};

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
    fetched_at: Instant,
}

#[derive(Clone)]
struct WorkflowRunsSummaryCache {
    recent_runs: Vec<WorkflowRunSummary>,
    total_runs: usize,
    fetched_at: Instant,
}

#[derive(Clone)]
struct WorkflowRunsListCache {
    runs: Vec<WorkflowRunSummary>,
    fetched_at: Instant,
}

static WORKFLOW_CACHE: OnceLock<Mutex<HashMap<(bool, bool), WorkflowCache>>> = OnceLock::new();
const CACHE_TTL_SECS: u64 = 30;
const STALE_CACHE_TTL_SECS: u64 = 120;

static WORKFLOW_DASHBOARD_DEFINITIONS_CACHE: OnceLock<Mutex<Option<WorkflowCache>>> =
    OnceLock::new();
static WORKFLOW_RUNS_SUMMARY_CACHE: OnceLock<Mutex<Option<WorkflowRunsSummaryCache>>> =
    OnceLock::new();
static WORKFLOW_RUNS_LIST_CACHE: OnceLock<
    Mutex<HashMap<(usize, Option<String>), WorkflowRunsListCache>>,
> = OnceLock::new();
const RUNS_SUMMARY_CACHE_TTL_SECS: u64 = 5;
const RUNS_SUMMARY_STALE_TTL_SECS: u64 = 120;

static WORKFLOW_REVISION_CACHE: OnceLock<Mutex<HashMap<String, RevisionWorkflowCache>>> =
    OnceLock::new();
const REVISION_CACHE_TTL_SECS: u64 = 300;

struct RevisionWorkflowCache {
    workflow: WorkflowResponse,
    fetched_at: Instant,
}

fn get_cached(include_deprecated: bool, include_definition: bool) -> Option<Vec<WorkflowResponse>> {
    let lock = WORKFLOW_CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    let cache = lock.lock();
    cache
        .get(&(include_deprecated, include_definition))
        .filter(|c| c.fetched_at.elapsed().as_secs() < CACHE_TTL_SECS)
        .map(|c| c.workflows.clone())
}

fn set_cached(workflows: &[WorkflowResponse], include_deprecated: bool, include_definition: bool) {
    let lock = WORKFLOW_CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    let mut cache = lock.lock();
    cache.insert(
        (include_deprecated, include_definition),
        WorkflowCache {
            workflows: workflows.to_vec(),
            fetched_at: Instant::now(),
        },
    );
}

pub(super) fn invalidate_cache() {
    if let Some(lock) = WORKFLOW_CACHE.get() {
        let mut cache = lock.lock();
        // Mark as expired but keep stale data for fallback on API errors
        for c in cache.values_mut() {
            c.fetched_at = Instant::now() - std::time::Duration::from_secs(CACHE_TTL_SECS + 1);
        }
    }
    invalidate_dashboard_definitions_cache();
}

fn get_cached_dashboard_definitions(allow_stale: bool) -> Option<Vec<WorkflowResponse>> {
    let lock = WORKFLOW_DASHBOARD_DEFINITIONS_CACHE.get_or_init(|| Mutex::new(None));
    let cache = lock.lock();
    let entry = cache.as_ref()?;
    let age = entry.fetched_at.elapsed().as_secs();
    if age < CACHE_TTL_SECS || (allow_stale && age < STALE_CACHE_TTL_SECS) {
        return Some(entry.workflows.clone());
    }
    None
}

fn set_cached_dashboard_definitions(workflows: &[WorkflowResponse]) {
    let lock = WORKFLOW_DASHBOARD_DEFINITIONS_CACHE.get_or_init(|| Mutex::new(None));
    *lock.lock() = Some(WorkflowCache {
        workflows: workflows.to_vec(),
        fetched_at: Instant::now(),
    });
}

fn invalidate_dashboard_definitions_cache() {
    if let Some(lock) = WORKFLOW_DASHBOARD_DEFINITIONS_CACHE.get() {
        if let Some(entry) = lock.lock().as_mut() {
            entry.fetched_at = Instant::now() - Duration::from_secs(CACHE_TTL_SECS + 1);
        }
    }
}

fn get_cached_dashboard_runs(allow_stale: bool) -> Option<(Vec<WorkflowRunSummary>, usize)> {
    let lock = WORKFLOW_RUNS_SUMMARY_CACHE.get_or_init(|| Mutex::new(None));
    let cache = lock.lock();
    let entry = cache.as_ref()?;
    let age = entry.fetched_at.elapsed().as_secs();
    if age < RUNS_SUMMARY_CACHE_TTL_SECS || (allow_stale && age < RUNS_SUMMARY_STALE_TTL_SECS) {
        return Some((entry.recent_runs.clone(), entry.total_runs));
    }
    None
}

fn set_cached_dashboard_runs(recent_runs: &[WorkflowRunSummary], total_runs: usize) {
    let lock = WORKFLOW_RUNS_SUMMARY_CACHE.get_or_init(|| Mutex::new(None));
    *lock.lock() = Some(WorkflowRunsSummaryCache {
        recent_runs: recent_runs.to_vec(),
        total_runs,
        fetched_at: Instant::now(),
    });
}

fn get_cached_workflow_runs(
    limit: usize,
    workflow: Option<&str>,
    allow_stale: bool,
) -> Option<Vec<WorkflowRunSummary>> {
    let lock = WORKFLOW_RUNS_LIST_CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    let cache = lock.lock();
    let key = (limit, workflow.map(str::to_string));
    let entry = cache.get(&key)?;
    let age = entry.fetched_at.elapsed().as_secs();
    if age < RUNS_SUMMARY_CACHE_TTL_SECS || (allow_stale && age < RUNS_SUMMARY_STALE_TTL_SECS) {
        return Some(entry.runs.clone());
    }
    None
}

fn set_cached_workflow_runs(limit: usize, workflow: Option<&str>, runs: &[WorkflowRunSummary]) {
    let lock = WORKFLOW_RUNS_LIST_CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    let mut cache = lock.lock();
    cache.insert(
        (limit, workflow.map(str::to_string)),
        WorkflowRunsListCache {
            runs: runs.to_vec(),
            fetched_at: Instant::now(),
        },
    );
}

fn invalidate_dashboard_runs_cache() {
    if let Some(lock) = WORKFLOW_RUNS_SUMMARY_CACHE.get() {
        if let Some(entry) = lock.lock().as_mut() {
            entry.fetched_at =
                Instant::now() - Duration::from_secs(RUNS_SUMMARY_CACHE_TTL_SECS + 1);
        }
    }
    if let Some(lock) = WORKFLOW_RUNS_LIST_CACHE.get() {
        let mut cache = lock.lock();
        for entry in cache.values_mut() {
            entry.fetched_at =
                Instant::now() - Duration::from_secs(RUNS_SUMMARY_CACHE_TTL_SECS + 1);
        }
    }
}

fn upsert_cached_workflow(workflow: &WorkflowResponse) {
    let Some(lock) = WORKFLOW_CACHE.get() else {
        return;
    };

    let mut cache = lock.lock();
    for ((include_deprecated, include_definition), c) in cache.iter_mut() {
        let mut cached = workflow.clone();
        if !*include_definition {
            cached.definition.clear();
            cached.triggers.clear();
        }

        if let Some(existing) = c.workflows.iter_mut().find(|w| w.kref == workflow.kref) {
            if cached.created_at.is_none() {
                cached.created_at.clone_from(&existing.created_at);
            }
            if existing.source != "custom" {
                cached.source.clone_from(&existing.source);
            }
            *existing = cached;
        } else if *include_deprecated || !cached.deprecated {
            c.workflows.push(cached);
        }

        c.fetched_at = Instant::now();
    }
}

fn get_cached_revision_workflow(revision_kref: &str) -> Option<WorkflowResponse> {
    let lock = WORKFLOW_REVISION_CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    let cache = lock.lock();
    cache.get(revision_kref).and_then(|entry| {
        if entry.fetched_at.elapsed().as_secs() < REVISION_CACHE_TTL_SECS {
            Some(entry.workflow.clone())
        } else {
            None
        }
    })
}

fn set_cached_revision_workflow(revision_kref: &str, workflow: &WorkflowResponse) {
    let lock = WORKFLOW_REVISION_CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    let mut cache = lock.lock();
    cache.insert(
        revision_kref.to_string(),
        RevisionWorkflowCache {
            workflow: workflow.clone(),
            fetched_at: Instant::now(),
        },
    );
}

fn to_workflow_dashboard_summary(item: &ItemResponse) -> WorkflowResponse {
    let get = |key: &str| -> String { item.metadata.get(key).cloned().unwrap_or_default() };
    let display_name = {
        let n = get("display_name");
        if n.is_empty() {
            item.item_name.clone()
        } else {
            n
        }
    };
    let tags_str = get("tags");
    let tags = if tags_str.is_empty() {
        Vec::new()
    } else {
        tags_str.split(',').map(|s| s.trim().to_string()).collect()
    };

    WorkflowResponse {
        kref: item.kref.clone(),
        name: display_name,
        item_name: item.item_name.clone(),
        deprecated: item.deprecated,
        created_at: item.created_at.clone(),
        description: get("description"),
        definition: String::new(),
        version: get("version"),
        tags,
        steps: get("steps").parse().unwrap_or(0),
        revision_number: 0,
        source: "custom".to_string(),
        triggers: Vec::new(),
    }
}

async fn fetch_workflow_definitions_for_dashboard(
    client: &KumihoClient,
    space_path: &str,
    include_definition: bool,
) -> Vec<WorkflowResponse> {
    if let Some(cached) = get_cached(false, include_definition) {
        return cached;
    }
    if !include_definition {
        if let Some(cached) = get_cached_dashboard_definitions(false) {
            return cached;
        }
    }

    let started = Instant::now();
    let definitions = match client.list_items(space_path, false).await {
        Ok(items) => {
            let mut workflows = if include_definition {
                let workflows =
                    merge_with_builtins(enrich_items(client, items, include_definition).await);
                set_cached(&workflows, false, include_definition);
                workflows
            } else {
                // Dashboard cards do not need revision metadata or YAML bodies.
                // Avoid the expensive revision batch fanout on the cold path;
                // item metadata plus builtin summaries are enough for counts,
                // names, and source classification.
                merge_with_builtins(
                    items
                        .iter()
                        .filter(|i| i.kind == "workflow")
                        .map(to_workflow_dashboard_summary)
                        .collect(),
                )
            };
            if !include_definition {
                for workflow in &mut workflows {
                    workflow.definition.clear();
                    workflow.triggers.clear();
                }
                set_cached_dashboard_definitions(&workflows);
            }
            workflows
        }
        Err(e) => {
            tracing::warn!(error = %e, "workflow dashboard definitions fetch failed");
            get_cached_dashboard_definitions(true)
                .unwrap_or_else(|| merge_with_builtins(Vec::new()))
        }
    };

    let elapsed = started.elapsed();
    if elapsed >= Duration::from_secs(1) {
        tracing::warn!(
            elapsed_ms = elapsed.as_millis() as u64,
            definitions = definitions.len(),
            include_definition,
            "workflow dashboard definitions fetch was slow"
        );
    }

    definitions
}

async fn fetch_recent_runs_for_dashboard(
    client: &KumihoClient,
    runs_space: &str,
) -> (Vec<WorkflowRunSummary>, usize) {
    if let Some(cached) = get_cached_dashboard_runs(false) {
        return cached;
    }

    let started = Instant::now();
    let result = match client.list_items(runs_space, false).await {
        Ok(mut items) => {
            items.retain(|i| i.kind == "workflow_run");

            let total = items.len();
            items.sort_by(|a, b| {
                let a_time = a.created_at.as_deref().unwrap_or("");
                let b_time = b.created_at.as_deref().unwrap_or("");
                b_time.cmp(a_time)
            });
            items.truncate(5);

            let runs: Vec<WorkflowRunSummary> = items.iter().map(to_run_summary_fast).collect();

            (runs, total)
        }
        Err(e) => {
            tracing::warn!(error = %e, "workflow dashboard runs fetch failed");
            get_cached_dashboard_runs(true).unwrap_or_else(|| (Vec::new(), 0))
        }
    };
    if !result.0.is_empty() || result.1 > 0 {
        set_cached_dashboard_runs(&result.0, result.1);
    }

    let elapsed = started.elapsed();
    if elapsed >= Duration::from_secs(1) {
        tracing::warn!(
            elapsed_ms = elapsed.as_millis() as u64,
            recent_runs = result.0.len(),
            total_runs = result.1,
            "workflow dashboard runs fetch was slow"
        );
    }

    result
}

// ── Query / request types ───────────────────────────────────────────────

#[derive(Deserialize)]
pub struct WorkflowListQuery {
    #[serde(default)]
    pub include_deprecated: bool,
    #[serde(default = "default_include_definition")]
    pub include_definition: bool,
    pub q: Option<String>,
}

#[derive(Deserialize)]
pub struct WorkflowDashboardQuery {
    #[serde(default = "default_include_definition")]
    pub include_definition: bool,
}

fn default_include_definition() -> bool {
    true
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
    /// Optional "run to here" target step id. When present, the operator
    /// runs only the transitive ancestor closure of this step (plus the
    /// step itself) and stops. Unknown ids are surfaced as a classified
    /// validation error from the operator-mcp tool call.
    #[serde(default)]
    pub target_step_id: Option<String>,
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
    #[serde(skip_serializing_if = "String::is_empty")]
    pub on_space: String,
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
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub expanded_steps_completed: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub current_loop: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub current_iteration: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub current_loop_total: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub current_step_instance: String,
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
    pub error: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub artifact_path: String,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub skills: Vec<String>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub transcript: Vec<TranscriptEntry>,
    // Generic input/output blobs the run-view UI renders for any step type.
    // Stored as raw JSON Values so each step type can shape them differently
    // — a `shell` step has `command`/`exit_code`, a `resolve` step has
    // `kind`/`tag`/`matched_kref`, etc. The frontend type-switches on the
    // step's type to decide which keys to show.
    //
    // The existing approval-shape UI continues to work because the JSON
    // wire format is identical: a `human_approval` step's output_data still
    // carries `awaiting_approval` / `approve_keywords` / `reject_keywords`
    // at the top level — they just used to be filtered through the
    // ApprovalOutputData struct (now removed in favour of raw Value).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub input_data: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub output_data: Option<serde_json::Value>,
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

fn kumiho_err(e: KumihoError) -> axum::response::Response {
    super::kumiho_client::kumiho_error_to_response(e)
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

fn to_workflow_response(
    item: &ItemResponse,
    rev: Option<&RevisionResponse>,
    include_definition: bool,
) -> WorkflowResponse {
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

    let definition = if include_definition {
        get("definition")
    } else {
        String::new()
    };
    let triggers = if include_definition {
        extract_triggers(&definition)
    } else {
        Vec::new()
    };

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

fn workflow_item_name_from_kref(kref: &str, fallback: &str) -> String {
    kref.split('/')
        .next_back()
        .and_then(|segment| {
            let name = segment
                .rsplit_once('.')
                .map(|(name, _kind)| name)
                .unwrap_or(segment);
            (!name.is_empty()).then(|| name.to_string())
        })
        .unwrap_or_else(|| slugify(fallback))
}

fn workflow_item_from_body(kref: &str, body: &CreateWorkflowBody) -> ItemResponse {
    let item_name = workflow_item_name_from_kref(kref, &body.name);
    ItemResponse {
        kref: kref.to_string(),
        name: item_name.clone(),
        item_name,
        kind: "workflow".to_string(),
        deprecated: false,
        created_at: None,
        author: None,
        username: None,
        author_display: None,
        metadata: HashMap::new(),
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
    const ARTIFACT_LOOKUP_TIMEOUT: Duration = Duration::from_secs(2);

    let lookups = revs
        .iter()
        .map(|(item_kref, rev)| (item_kref.clone(), rev.kref.clone()))
        .map(|(item_kref, rev_kref)| async move {
            let artifact = match tokio::time::timeout(
                ARTIFACT_LOOKUP_TIMEOUT,
                client.get_artifact_by_name(&rev_kref, "workflow.yaml"),
            )
            .await
            {
                Ok(Ok(artifact)) => artifact,
                Ok(Err(e)) => {
                    tracing::debug!(
                        revision_kref = %rev_kref,
                        "workflow artifact lookup skipped: {e}"
                    );
                    return None;
                }
                Err(_) => {
                    tracing::debug!(
                        revision_kref = %rev_kref,
                        timeout_ms = ARTIFACT_LOOKUP_TIMEOUT.as_millis(),
                        "workflow artifact lookup timed out"
                    );
                    return None;
                }
            };

            let path = artifact
                .location
                .strip_prefix("file://")
                .unwrap_or(&artifact.location);
            match tokio::fs::read_to_string(path).await {
                Ok(yaml) => Some((item_kref, yaml)),
                Err(e) => {
                    tracing::debug!(
                        revision_kref = %rev_kref,
                        artifact_path = %path,
                        "workflow artifact read skipped: {e}"
                    );
                    None
                }
            }
        });

    for (item_kref, yaml) in futures_util::future::join_all(lookups)
        .await
        .into_iter()
        .flatten()
    {
        if let Some(rev) = revs.get_mut(&item_kref) {
            rev.metadata.insert("definition".to_string(), yaml);
        }
    }
}

async fn enrich_items(
    client: &super::kumiho_client::KumihoClient,
    items: Vec<ItemResponse>,
    include_definition: bool,
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

        if include_definition {
            // Artifact-first: the `workflow.yaml` on disk is canonical. The inline
            // `definition` metadata drifts for operator-authored revisions and is
            // truncated by Kumiho's batch endpoint for large YAMLs, so we always
            // prefer the artifact when it exists — same logic the single-revision
            // endpoint uses.
            prefer_artifact_definitions(client, &mut rev_map).await;
            prefer_artifact_definitions(client, &mut latest_map).await;
        }

        return items
            .iter()
            .map(|item| {
                let rev = rev_map
                    .get(&item.kref)
                    .or_else(|| latest_map.get(&item.kref));
                to_workflow_response(item, rev, include_definition)
            })
            .collect();
    }

    // Fallback: sequential
    let mut workflows = Vec::with_capacity(items.len());
    for item in &items {
        let rev = client.get_published_or_latest(&item.kref).await.ok();
        workflows.push(to_workflow_response(item, rev.as_ref(), include_definition));
    }
    workflows
}

fn to_run_summary(item: &ItemResponse, rev: Option<&RevisionResponse>) -> WorkflowRunSummary {
    let meta = rev.map(|r| &r.metadata).unwrap_or(&item.metadata);
    let get = |key: &str| -> String { meta.get(key).cloned().unwrap_or_default() };

    let run_id_meta = get("run_id");
    let completed_at = get("completed_at");
    let status = normalize_run_status(
        &get("status"),
        &get("steps_completed"),
        &get("steps_total"),
        &completed_at,
    );
    let started_at = {
        let value = get("started_at");
        if value.is_empty() {
            item.created_at.clone().unwrap_or_default()
        } else {
            value
        }
    };
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
        status,
        started_at,
        completed_at,
        steps_completed: get("steps_completed"),
        steps_total: get("steps_total"),
        expanded_steps_completed: get("expanded_steps_completed"),
        current_loop: get("current_loop"),
        current_iteration: get("current_iteration"),
        current_loop_total: get("current_loop_total"),
        current_step_instance: get("current_step_instance"),
        error: get("error"),
        workflow_item_kref: get("workflow_item_kref"),
        workflow_revision_kref: get("workflow_revision_kref"),
    }
}

fn to_run_summary_fast(item: &ItemResponse) -> WorkflowRunSummary {
    let mut summary = to_run_summary(item, None);
    apply_local_checkpoint_progress(&mut summary);
    summary
}

fn is_safe_checkpoint_run_id(run_id: &str) -> bool {
    !run_id.is_empty()
        && run_id.len() <= 128
        && run_id
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || ch == '-' || ch == '_')
}

fn workflow_checkpoint_path(run_id: &str) -> Option<std::path::PathBuf> {
    if !is_safe_checkpoint_run_id(run_id) {
        return None;
    }
    directories::UserDirs::new().map(|dirs| {
        dirs.home_dir()
            .join(".construct")
            .join("workflow_checkpoints")
            .join(format!("{run_id}.json"))
    })
}

fn parse_usize_json(value: Option<&serde_json::Value>) -> Option<usize> {
    match value? {
        serde_json::Value::Number(n) => n.as_u64().map(|v| v as usize),
        serde_json::Value::String(s) => s.parse::<usize>().ok(),
        _ => None,
    }
}

fn json_value_to_summary_string(value: Option<&serde_json::Value>) -> String {
    match value {
        Some(serde_json::Value::String(s)) => s.clone(),
        Some(serde_json::Value::Number(n)) => n.to_string(),
        Some(serde_json::Value::Bool(v)) => v.to_string(),
        _ => String::new(),
    }
}

fn nonempty_json_string<'a>(value: &'a serde_json::Value, key: &str) -> Option<&'a str> {
    value
        .get(key)
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
}

fn apply_checkpoint_value_to_summary(
    summary: &mut WorkflowRunSummary,
    checkpoint: &serde_json::Value,
) {
    if let Some(checkpoint_run_id) = nonempty_json_string(checkpoint, "run_id") {
        if checkpoint_run_id != summary.run_id {
            return;
        }
    }

    if let Some(status) = nonempty_json_string(checkpoint, "status") {
        summary.status = status.to_string();
    }
    if let Some(started_at) = nonempty_json_string(checkpoint, "started_at") {
        summary.started_at = started_at.to_string();
    }
    if let Some(completed_at) = nonempty_json_string(checkpoint, "completed_at") {
        summary.completed_at = completed_at.to_string();
    }
    if let Some(error) = nonempty_json_string(checkpoint, "error") {
        summary.error = error.to_string();
    }
    if let Some(workflow_item_kref) = nonempty_json_string(checkpoint, "workflow_item_kref") {
        summary.workflow_item_kref = workflow_item_kref.to_string();
    }
    if let Some(workflow_revision_kref) = nonempty_json_string(checkpoint, "workflow_revision_kref")
    {
        summary.workflow_revision_kref = workflow_revision_kref.to_string();
    }

    let step_results = checkpoint
        .get("step_results")
        .and_then(|value| value.as_object());
    if let Some(steps) = step_results {
        let is_progress_terminal = |step: &serde_json::Value| -> bool {
            matches!(
                step.get("status")
                    .and_then(|value| value.as_str())
                    .unwrap_or_default(),
                "completed" | "skipped"
            )
        };
        let completed = steps
            .iter()
            .filter(|(step_id, step)| !step_id.contains("__iter_") && is_progress_terminal(step))
            .count();
        let expanded_completed = steps
            .values()
            .filter(|step| is_progress_terminal(step))
            .count();
        let explicit_total = parse_usize_json(checkpoint.get("steps_total"));
        let capped_completed = explicit_total
            .map(|total| completed.min(total))
            .unwrap_or(completed);
        summary.steps_completed = capped_completed.to_string();
        summary.expanded_steps_completed = expanded_completed.to_string();
    }

    let for_each_ctx = checkpoint
        .get("inputs")
        .and_then(|inputs| inputs.get("__for_each__"));
    if let Some(ctx) = for_each_ctx {
        let current_loop = json_value_to_summary_string(
            ctx.get("loop_id")
                .or_else(|| ctx.get("loop"))
                .or_else(|| ctx.get("step_id")),
        );
        if !current_loop.is_empty() {
            summary.current_loop = current_loop;
        }
        summary.current_iteration = json_value_to_summary_string(ctx.get("iteration"));
        summary.current_loop_total = json_value_to_summary_string(ctx.get("total"));
        if !summary.current_iteration.is_empty() {
            if let Some(current_step) = nonempty_json_string(checkpoint, "current_step") {
                summary.current_step_instance =
                    format!("{current_step}__iter_{}", summary.current_iteration);
            }
        }
    }

    let explicit_total = parse_usize_json(checkpoint.get("steps_total"));
    let inferred_completed_total = step_results.and_then(|steps| {
        if summary.status == "completed" {
            Some(
                steps
                    .iter()
                    .filter(|(step_id, step)| {
                        !step_id.contains("__iter_")
                            && matches!(
                                step.get("status")
                                    .and_then(|value| value.as_str())
                                    .unwrap_or_default(),
                                "completed" | "skipped"
                            )
                    })
                    .count(),
            )
        } else {
            None
        }
    });
    if let Some(total) = explicit_total {
        summary.steps_total = total.to_string();
    } else if summary.steps_total.is_empty() || summary.steps_total == "0" {
        if let Some(total) = inferred_completed_total {
            summary.steps_total = total.to_string();
        }
    }

    summary.status = normalize_run_status(
        &summary.status,
        &summary.steps_completed,
        &summary.steps_total,
        &summary.completed_at,
    );
}

fn apply_local_checkpoint_progress(summary: &mut WorkflowRunSummary) {
    let Some(path) = workflow_checkpoint_path(&summary.run_id) else {
        return;
    };
    let Ok(content) = std::fs::read_to_string(path) else {
        return;
    };
    let Ok(checkpoint) = serde_json::from_str::<serde_json::Value>(&content) else {
        return;
    };
    apply_checkpoint_value_to_summary(summary, &checkpoint);
}

fn normalize_run_status(
    status: &str,
    steps_completed: &str,
    steps_total: &str,
    completed_at: &str,
) -> String {
    let status = status.to_string();
    if status != "running" {
        return status;
    }
    if completed_at.is_empty() {
        return status;
    }

    let completed = steps_completed.parse::<usize>().ok();
    let count = steps_total.parse::<usize>().ok();
    match (completed, count) {
        (Some(completed), Some(count)) if count > 0 && completed >= count => "completed".into(),
        _ => status,
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
                // Generic input/output blobs — exposed as raw JSON for any
                // step type. Persistence may write these as either embedded
                // objects (current) or JSON strings (legacy / size-capped),
                // so we accept both.
                let decode_blob = |key: &str| -> Option<serde_json::Value> {
                    parsed.get(key).and_then(|v| {
                        if let Some(s) = v.as_str() {
                            serde_json::from_str::<serde_json::Value>(s).ok()
                        } else if v.is_object() || v.is_array() {
                            Some(v.clone())
                        } else {
                            None
                        }
                    })
                };
                let input_data_raw = decode_blob("input_data");
                let output_data_raw = decode_blob("output_data");
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
                    error: parsed
                        .get("error")
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
                    input_data: input_data_raw,
                    output_data: output_data_raw,
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
                    error: String::new(),
                    artifact_path: String::new(),
                    skills: Vec::new(),
                    transcript: Vec::new(),
                    input_data: None,
                    output_data: None,
                });
            }
        }
    }
    steps
}

fn to_run_detail(item: &ItemResponse, rev: Option<&RevisionResponse>) -> WorkflowRunDetail {
    let mut summary = to_run_summary(item, rev);
    apply_local_checkpoint_progress(&mut summary);
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
/// optional `on_tag` (defaults to `"ready"`), optional `on_name_pattern`, and
/// optional `on_space`.
fn extract_triggers(content: &str) -> Vec<WorkflowTrigger> {
    let mut triggers = Vec::new();
    let mut in_triggers = false;
    let mut current_kind = String::new();
    let mut current_tag = String::new();
    let mut current_pattern = String::new();
    let mut current_space = String::new();

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
                    on_space: std::mem::take(&mut current_space),
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
                    "on_space" => current_space = v.to_string(),
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
                "on_space" => current_space = v.to_string(),
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
            on_space: current_space,
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
        if let Some(cached) = get_cached(query.include_deprecated, query.include_definition) {
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
            let workflows =
                merge_with_builtins(enrich_items(&client, items, query.include_definition).await);
            if query.q.is_none() {
                set_cached(
                    &workflows,
                    query.include_deprecated,
                    query.include_definition,
                );
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
                let lock = WORKFLOW_CACHE.get_or_init(|| Mutex::new(HashMap::new()));
                let cache = lock.lock();
                if let Some(c) = cache.get(&(query.include_deprecated, query.include_definition)) {
                    tracing::warn!("Workflows list failed, returning stale cache: {e}");
                    return Json(serde_json::json!({ "workflows": c.workflows.clone() }))
                        .into_response();
                }
            }
            kumiho_err(e).into_response()
        }
    }
}

/// Result of calling the operator's `validate_workflow` MCP tool.
///
/// Mirrors the Python-side `ValidationResult.to_dict()` shape:
/// `{ valid: bool, errors: [...], warnings: [...], all_step_ids: [...] }`.
/// `all_step_ids` is the superset of every step id (including parallel /
/// for_each body steps) — used to validate caller-supplied
/// ``target_step_id`` for run-to-step.
#[derive(Debug)]
struct ValidationOutcome {
    valid: bool,
    errors: Vec<serde_json::Value>,
    warnings: Vec<serde_json::Value>,
    all_step_ids: Vec<String>,
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
        .mcp_registry()
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
    let all_step_ids = inner
        .get("all_step_ids")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str().map(|s| s.to_string()))
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();

    Ok(ValidationOutcome {
        valid,
        errors,
        warnings,
        all_step_ids,
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
    invalidate_proxy_cache();
    sync_cron_for_workflow(&state, &body.name, &body.definition);

    broadcast_revision_published(&state, &headers, &item.kref, &rev, &body.name);

    let workflow = to_workflow_response(&item, Some(&rev), true);
    upsert_cached_workflow(&workflow);
    set_cached_revision_workflow(&rev.kref, &workflow);
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

    let item = workflow_item_from_body(&kref, &body);
    let workflow = to_workflow_response(&item, Some(&rev), true);
    upsert_cached_workflow(&workflow);
    set_cached_revision_workflow(&rev.kref, &workflow);
    invalidate_proxy_cache();
    sync_cron_for_workflow(&state, &body.name, &body.definition);

    broadcast_revision_published(&state, &headers, &kref, &rev, &body.name);

    Json(serde_json::json!({ "workflow": workflow })).into_response()
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
            invalidate_proxy_cache();
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

            let workflow = to_workflow_response(&item, rev.as_ref(), true);
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
            invalidate_proxy_cache();

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
    let target_step_id = body
        .as_ref()
        .and_then(|b| b.target_step_id.clone())
        .filter(|s| !s.is_empty());

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
    // Validation also returns the workflow's ``all_step_ids`` (superset
    // including parallel/for_each body steps). Use that to check
    // ``target_step_id`` BEFORE we touch Kumiho — without this preflight, a
    // typo'd id would create a pending run-request item that the listener
    // later picks up and (per the executor's older behaviour) silently
    // executes the entire workflow.
    let all_step_ids: Vec<String> = match validate_via_operator(&state, v_args).await {
        Ok(outcome) if !outcome.valid => {
            return validation_error_response(&outcome, "cannot dispatch invalid workflow")
                .into_response();
        }
        Ok(outcome) => outcome.all_step_ids,
        Err(e) => {
            tracing::warn!("run_workflow: validation skipped (infra error): {e}");
            Vec::new()
        }
    };

    if let Some(ref tsid) = target_step_id {
        // Only check when validation actually returned step ids — otherwise
        // we'd 400 every call when validate_via_operator hits an infra
        // error. Operator-side hard validation in execute_workflow is the
        // backstop.
        if !all_step_ids.is_empty() && !all_step_ids.iter().any(|s| s == tsid) {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({
                    "error": format!("unknown_target_step: '{tsid}'"),
                    "error_code": "unknown_target_step",
                    "valid": false,
                    "errors": [{
                        "message": format!(
                            "target_step_id '{tsid}' is not a step in workflow '{name}'"
                        ),
                        "severity": "error",
                    }],
                })),
            )
                .into_response();
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
    if let Some(ref tsid) = target_step_id {
        metadata.insert("target_step_id".to_string(), tsid.clone());
    }

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
            if let Some(registry) = state.mcp_registry() {
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
                        body.as_ref()
                            .and_then(|b| b.cwd.clone())
                            .unwrap_or_default(),
                    ),
                );
                tool_args.insert(
                    "run_id".to_string(),
                    serde_json::Value::String(run_id.clone()),
                );
                if let Some(ref tsid) = target_step_id {
                    tool_args.insert(
                        "target_step_id".to_string(),
                        serde_json::Value::String(tsid.clone()),
                    );
                }
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
                                        .and_then(|s| {
                                            serde_json::from_str::<serde_json::Value>(s).ok()
                                        })
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

            invalidate_dashboard_runs_cache();
            invalidate_proxy_cache();
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

    if let Some(workflow) = get_cached_revision_workflow(&revision_kref) {
        return Json(serde_json::json!({ "workflow": workflow })).into_response();
    }

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
        author: None,
        username: None,
        author_display: None,
        metadata: HashMap::new(),
    };

    let workflow = to_workflow_response(&item, Some(&rev), true);
    set_cached_revision_workflow(&revision_kref, &workflow);
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
    let workflow_filter = query.workflow.as_deref();

    if let Some(cached) = get_cached_workflow_runs(query.limit, workflow_filter, false) {
        return Json(serde_json::json!({ "runs": cached, "count": cached.len() })).into_response();
    }

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

            let runs: Vec<WorkflowRunSummary> = items.iter().map(to_run_summary_fast).collect();

            set_cached_workflow_runs(query.limit, workflow_filter, &runs);
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
            if let Some(cached) = get_cached_workflow_runs(query.limit, workflow_filter, true) {
                return Json(serde_json::json!({ "runs": cached, "count": cached.len() }))
                    .into_response();
            }
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
            invalidate_dashboard_runs_cache();
            invalidate_proxy_cache();
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

    let mcp_result = if let Some(registry) = state.mcp_registry() {
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
            invalidate_dashboard_runs_cache();
            invalidate_proxy_cache();
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

    let mcp_result = if let Some(registry) = state.mcp_registry() {
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
            invalidate_dashboard_runs_cache();
            invalidate_proxy_cache();
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

/// POST /api/workflows/runs/{run_id}/cancel
///
/// Body: empty.
///
/// Cancels a running workflow. Sets the executor's `cancel_requested` flag
/// via the `cancel_workflow` MCP tool — the executor reads this at the next
/// step boundary, kills any in-flight subprocesses (shell/python steps),
/// and transitions the run to `cancelled` cleanly.
///
/// Returns:
///   - 200 with `{cancelled: true, run_id, status, ...}` for active runs.
///   - 404 with `{cancelled: false, reason: "not_found_or_already_finished"}`
///     when the run isn't in the active registry.
///   - 409 when the run is already in a terminal state.
pub async fn handle_cancel_workflow_run(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(run_id): Path<String>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let tool_name = format!(
        "{}__cancel_workflow",
        crate::agent::operator::OPERATOR_SERVER_NAME
    );
    let mut tool_args = serde_json::Map::new();
    tool_args.insert(
        "run_id".to_string(),
        serde_json::Value::String(run_id.clone()),
    );

    let mcp_result = if let Some(registry) = state.mcp_registry() {
        let mcp_future = registry.call_tool(&tool_name, serde_json::Value::Object(tool_args));
        match tokio::time::timeout(std::time::Duration::from_secs(10), mcp_future).await {
            Ok(Ok(result_str)) => Ok(result_str),
            Ok(Err(e)) => Err(format!("operator tool call failed: {e:#}")),
            Err(_) => Err("operator tool call timed out (10s)".to_string()),
        }
    } else {
        Err("MCP registry not available — operator not connected".to_string())
    };

    match mcp_result {
        Ok(result_str) => {
            let payload = serde_json::from_str::<serde_json::Value>(&result_str)
                .unwrap_or_else(|_| serde_json::json!({"raw": result_str}));

            let status_code = cancel_status_for(&payload);
            if status_code == StatusCode::OK {
                invalidate_dashboard_runs_cache();
                invalidate_proxy_cache();
                let _ = state.event_tx.send(serde_json::json!({
                    "type": "workflow_cancel",
                    "run_id": run_id,
                    "timestamp": chrono::Utc::now().to_rfc3339(),
                }));
            }
            (status_code, Json(payload)).into_response()
        }
        Err(e) => {
            tracing::warn!("cancel_workflow_run: failed for run_id={run_id}: {e}");
            (
                StatusCode::BAD_GATEWAY,
                Json(serde_json::json!({ "error": format!("Failed to cancel workflow: {e}") })),
            )
                .into_response()
        }
    }
}

/// Map a `cancel_workflow` MCP-tool result to the gateway's HTTP status code.
///
///   - `cancelled=true`                                  → 200 OK
///   - `reason=not_found_or_already_finished`            → 404
///   - `reason=already_terminal`                         → 409
///   - anything else (e.g. classified_error)             → 400
fn cancel_status_for(payload: &serde_json::Value) -> StatusCode {
    let cancelled = payload
        .get("cancelled")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    if cancelled {
        return StatusCode::OK;
    }
    let reason = payload.get("reason").and_then(|v| v.as_str()).unwrap_or("");
    match reason {
        "not_found_or_already_finished" => StatusCode::NOT_FOUND,
        "already_terminal" => StatusCode::CONFLICT,
        _ => StatusCode::BAD_REQUEST,
    }
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
    Query(query): Query<WorkflowDashboardQuery>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let client = build_kumiho_client(&state);
    let space_path = workflow_space_path(&state);
    let runs_space = workflow_runs_space_path(&state);

    let definitions_future =
        fetch_workflow_definitions_for_dashboard(&client, &space_path, query.include_definition);
    let recent_runs_future = fetch_recent_runs_for_dashboard(&client, &runs_space);

    let (definitions, (recent_runs, total_runs)) =
        tokio::join!(definitions_future, recent_runs_future);
    let definitions_count = definitions.len();

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

#[cfg(test)]
mod workflow_save_tests {
    use super::*;

    #[test]
    fn workflow_item_name_prefers_kref_slug() {
        assert_eq!(
            workflow_item_name_from_kref("kref://Construct/Workflows/my-flow.workflow", "My Flow"),
            "my-flow"
        );
    }

    #[test]
    fn workflow_item_name_falls_back_to_slugified_body_name() {
        assert_eq!(workflow_item_name_from_kref("", "My Flow"), "my-flow");
    }
}

#[cfg(test)]
mod cancel_tests {
    //! Tests for the `POST /api/workflows/runs/{run_id}/cancel` response-shape
    //! mapping. We don't exercise the full handler (it requires a real
    //! `AppState` with an MCP registry) — instead we test the pure mapping
    //! function `cancel_status_for`, which is what determines the 200/404/409
    //! semantics. The MCP tool's behavior is tested in the operator-mcp
    //! Python suite (`tests/test_workflow_cancel.py`).
    use super::cancel_status_for;
    use axum::http::StatusCode;
    use serde_json::json;

    #[test]
    fn cancelled_true_returns_200_for_active_run() {
        let payload = json!({
            "cancelled": true,
            "run_id": "abc123",
            "status": "running",
            "steps_completed": 1,
        });
        assert_eq!(cancel_status_for(&payload), StatusCode::OK);
    }

    #[test]
    fn unknown_run_returns_404() {
        let payload = json!({
            "cancelled": false,
            "run_id": "nope",
            "reason": "not_found_or_already_finished",
        });
        assert_eq!(cancel_status_for(&payload), StatusCode::NOT_FOUND);
    }

    #[test]
    fn terminal_state_returns_409() {
        let payload = json!({
            "cancelled": false,
            "run_id": "done123",
            "status": "completed",
            "reason": "already_terminal",
        });
        assert_eq!(cancel_status_for(&payload), StatusCode::CONFLICT);
    }

    #[test]
    fn unrecognized_payload_returns_400() {
        // Operator's classified_error path (missing_run_id etc.) — generic
        // bad-request fallback, never silently 200.
        let payload = json!({"error": "missing run_id", "code": "missing_run_id"});
        assert_eq!(cancel_status_for(&payload), StatusCode::BAD_REQUEST);
    }
}

#[cfg(test)]
mod workflow_run_status_tests {
    use super::{
        ItemResponse, RevisionResponse, WorkflowRunSummary, apply_checkpoint_value_to_summary,
        normalize_run_status, to_run_summary,
    };
    use serde_json::json;
    use std::collections::HashMap;

    fn summary(run_id: &str, status: &str) -> WorkflowRunSummary {
        WorkflowRunSummary {
            kref: "kref://Construct/WorkflowRuns/test.workflow_run".to_string(),
            run_id: run_id.to_string(),
            workflow_name: "test".to_string(),
            status: status.to_string(),
            started_at: String::new(),
            completed_at: String::new(),
            steps_completed: String::new(),
            steps_total: String::new(),
            expanded_steps_completed: String::new(),
            current_loop: String::new(),
            current_iteration: String::new(),
            current_loop_total: String::new(),
            current_step_instance: String::new(),
            error: String::new(),
            workflow_item_kref: String::new(),
            workflow_revision_kref: String::new(),
        }
    }

    #[test]
    fn completed_steps_with_completion_time_override_stale_running_status() {
        assert_eq!(
            normalize_run_status("running", "46", "46", "2026-05-20T00:00:00Z"),
            "completed"
        );
        assert_eq!(
            normalize_run_status("running", "47", "46", "2026-05-20T00:00:00Z"),
            "completed"
        );
    }

    #[test]
    fn incomplete_running_status_stays_running() {
        assert_eq!(
            normalize_run_status("running", "45", "46", "2026-05-20T00:00:00Z"),
            "running"
        );
        assert_eq!(
            normalize_run_status("running", "", "46", "2026-05-20T00:00:00Z"),
            "running"
        );
        assert_eq!(
            normalize_run_status("running", "46", "", "2026-05-20T00:00:00Z"),
            "running"
        );
    }

    #[test]
    fn completed_steps_without_completion_time_stay_running() {
        assert_eq!(normalize_run_status("running", "46", "46", ""), "running");
        assert_eq!(normalize_run_status("running", "47", "46", ""), "running");
    }

    #[test]
    fn run_summary_uses_total_steps_not_observed_step_count() {
        let item = ItemResponse {
            kref: "kref://Construct/WorkflowRuns/test.workflow_run".to_string(),
            name: "test".to_string(),
            item_name: "test".to_string(),
            kind: "workflow_run".to_string(),
            deprecated: false,
            created_at: None,
            author: None,
            username: None,
            author_display: None,
            metadata: HashMap::new(),
        };
        let rev = RevisionResponse {
            kref: "kref://Construct/WorkflowRuns/test.workflow_run?rev=1".to_string(),
            item_kref: item.kref.clone(),
            number: 1,
            latest: true,
            tags: Vec::new(),
            metadata: HashMap::from([
                ("run_id".to_string(), "run-1".to_string()),
                ("workflow_name".to_string(), "test".to_string()),
                ("status".to_string(), "running".to_string()),
                ("step_count".to_string(), "3".to_string()),
                ("steps_completed".to_string(), "3".to_string()),
                ("steps_total".to_string(), "11".to_string()),
            ]),
            deprecated: false,
            created_at: None,
            author: None,
            username: None,
            author_display: None,
        };

        let run = to_run_summary(&item, Some(&rev));

        assert_eq!(run.status, "running");
        assert_eq!(run.steps_completed, "3");
        assert_eq!(run.steps_total, "11");
    }

    #[test]
    fn checkpoint_progress_fills_completed_run_total_from_observed_steps() {
        let checkpoint = json!({
            "run_id": "run-1",
            "status": "completed",
            "step_results": {
                "one": { "status": "completed" },
                "two": { "status": "skipped" }
            }
        });
        let mut run = summary("run-1", "running");

        apply_checkpoint_value_to_summary(&mut run, &checkpoint);

        assert_eq!(run.status, "completed");
        assert_eq!(run.steps_completed, "2");
        assert_eq!(run.steps_total, "2");
    }

    #[test]
    fn checkpoint_progress_preserves_unknown_total_for_running_run() {
        let checkpoint = json!({
            "run_id": "run-1",
            "status": "running",
            "step_results": {
                "one": { "status": "completed" },
                "two": { "status": "running" }
            }
        });
        let mut run = summary("run-1", "running");

        apply_checkpoint_value_to_summary(&mut run, &checkpoint);

        assert_eq!(run.status, "running");
        assert_eq!(run.steps_completed, "1");
        assert_eq!(run.steps_total, "");
    }

    #[test]
    fn checkpoint_progress_uses_explicit_total() {
        let checkpoint = json!({
            "run_id": "run-1",
            "status": "running",
            "steps_total": 5,
            "step_results": {
                "one": { "status": "completed" },
                "two": { "status": "completed" }
            }
        });
        let mut run = summary("run-1", "running");

        apply_checkpoint_value_to_summary(&mut run, &checkpoint);

        assert_eq!(run.status, "running");
        assert_eq!(run.steps_completed, "2");
        assert_eq!(run.steps_total, "5");
    }

    #[test]
    fn checkpoint_progress_keeps_workflow_total_when_for_each_expands_results() {
        let checkpoint = json!({
            "run_id": "run-1",
            "status": "running",
            "steps_total": 2,
            "current_step": "child",
            "inputs": {
                "__for_each__": {
                    "loop_id": "for_each",
                    "iteration": 2,
                    "total": 3
                }
            },
            "step_results": {
                "for_each": { "status": "completed" },
                "child": { "status": "completed" },
                "child__iter_1": { "status": "completed" },
                "child__iter_2": { "status": "completed" }
            }
        });
        let mut run = summary("run-1", "running");

        apply_checkpoint_value_to_summary(&mut run, &checkpoint);

        assert_eq!(run.status, "running");
        assert_eq!(run.steps_completed, "2");
        assert_eq!(run.steps_total, "2");
        assert_eq!(run.expanded_steps_completed, "4");
        assert_eq!(run.current_loop, "for_each");
        assert_eq!(run.current_iteration, "2");
        assert_eq!(run.current_loop_total, "3");
        assert_eq!(run.current_step_instance, "child__iter_2");
    }

    #[test]
    fn checkpoint_progress_ignores_mismatched_run_id() {
        let checkpoint = json!({
            "run_id": "other-run",
            "status": "completed",
            "steps_total": 1,
            "step_results": {
                "one": { "status": "completed" }
            }
        });
        let mut run = summary("run-1", "running");

        apply_checkpoint_value_to_summary(&mut run, &checkpoint);

        assert_eq!(run.status, "running");
        assert_eq!(run.steps_completed, "");
        assert_eq!(run.steps_total, "");
    }
}
