//! REST API handlers for agent management (`/api/agents`).
//!
//! Proxies to Kumiho FastAPI for persistent agent storage.  Each agent is a
//! Kumiho item of kind `"agent"` in the `Construct/AgentPool` space.  Agent
//! metadata (identity, soul, expertise, etc.) is stored as revision metadata.

use super::AppState;
use super::api::require_auth;
use super::kumiho_client::{ItemResponse, KumihoClient, KumihoError, RevisionResponse, slugify};

/// Normalize a kref from a URL path — strip existing `kref://` prefix to avoid doubling.
fn normalize_kref(raw: &str) -> String {
    let stripped = raw.strip_prefix("kref://").unwrap_or(raw);
    format!("kref://{stripped}")
}
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

// ── Response cache (avoids N+1 Kumiho calls on rapid dashboard polls) ───

struct AgentCache {
    agents: Vec<AgentResponse>,
    include_deprecated: bool,
    fetched_at: Instant,
}

static AGENT_CACHE: OnceLock<Mutex<Option<AgentCache>>> = OnceLock::new();
const CACHE_TTL_SECS: u64 = 3;

fn get_cached_agents(include_deprecated: bool) -> Option<Vec<AgentResponse>> {
    let lock = AGENT_CACHE.get_or_init(|| Mutex::new(None));
    let cache = lock.lock();
    if let Some(ref c) = *cache {
        if c.include_deprecated == include_deprecated
            && c.fetched_at.elapsed().as_secs() < CACHE_TTL_SECS
        {
            return Some(c.agents.clone());
        }
    }
    None
}

fn set_cached_agents(agents: &[AgentResponse], include_deprecated: bool) {
    let lock = AGENT_CACHE.get_or_init(|| Mutex::new(None));
    let mut cache = lock.lock();
    *cache = Some(AgentCache {
        agents: agents.to_vec(),
        include_deprecated,
        fetched_at: Instant::now(),
    });
}

pub fn invalidate_agent_cache() {
    if let Some(lock) = AGENT_CACHE.get() {
        let mut cache = lock.lock();
        *cache = None;
    }
}

/// Space name within the project.
const AGENT_SPACE_NAME: &str = "AgentPool";

/// Kumiho project used for harness items (agents/teams/workflows), from config.
fn agent_project(state: &AppState) -> String {
    state.config.lock().kumiho.harness_project.clone()
}

/// Full space path for agents, e.g. "/Construct/AgentPool".
fn agent_space_path(state: &AppState) -> String {
    format!("/{}/{}", agent_project(state), AGENT_SPACE_NAME)
}

// ── Query / request types ───────────────────────────────────────────────

#[derive(Deserialize)]
pub struct AgentListQuery {
    /// Include deprecated (disabled) agents.
    #[serde(default)]
    pub include_deprecated: bool,
    /// Full-text search query.  When present, uses Kumiho search instead of list.
    pub q: Option<String>,
    /// Page number (1-based). Default: 1.
    pub page: Option<u32>,
    /// Items per page. Default: 9, max: 50.
    pub per_page: Option<u32>,
}

#[derive(Deserialize)]
pub struct CreateAgentBody {
    pub name: String,
    pub identity: String,
    pub soul: String,
    #[serde(default)]
    pub expertise: Vec<String>,
    #[serde(default)]
    pub tone: Option<String>,
    #[serde(default)]
    pub role: Option<String>,
    #[serde(default)]
    pub agent_type: Option<String>,
    #[serde(default)]
    pub model: Option<String>,
    #[serde(default)]
    pub system_hint: Option<String>,
}

#[derive(Deserialize)]
pub struct DeprecateBody {
    pub kref: String,
    pub deprecated: bool,
}

// ── Response types ──────────────────────────────────────────────────────

#[derive(Serialize, Clone)]
pub struct AgentResponse {
    pub kref: String,
    pub name: String,
    /// Kumiho slug (e.g. "senior-rust-engineer") — the value workflow YAML's
    /// `assign:` expects. Distinct from `name`, which is the human-readable
    /// `display_name` (falling back to the slug when unset).
    pub item_name: String,
    pub kind: String,
    pub deprecated: bool,
    pub created_at: Option<String>,
    // Metadata fields from latest revision
    pub identity: String,
    pub soul: String,
    pub expertise: Vec<String>,
    pub tone: String,
    pub role: String,
    pub agent_type: String,
    pub model: String,
    pub system_hint: String,
    pub revision: Option<i32>,
}

// ── Helpers ─────────────────────────────────────────────────────────────

/// Build a `KumihoClient` from the current config + env.
/// Shared Kumiho client — reuses TCP connections and TLS sessions across requests.
static KUMIHO_CLIENT: std::sync::OnceLock<KumihoClient> = std::sync::OnceLock::new();

pub(super) fn build_kumiho_client(state: &AppState) -> KumihoClient {
    KUMIHO_CLIENT
        .get_or_init(|| {
            let config = state.config.lock();
            let base_url = config.kumiho.api_url.clone();
            drop(config);
            let service_token = std::env::var("KUMIHO_SERVICE_TOKEN").unwrap_or_default();
            KumihoClient::new(base_url, service_token)
        })
        .clone()
}

/// Convert Kumiho error to an HTTP response.
fn kumiho_err(e: KumihoError) -> (StatusCode, Json<serde_json::Value>) {
    match &e {
        KumihoError::Unreachable(_) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(serde_json::json!({ "error": format!("Kumiho service unavailable: {e}") })),
        ),
        KumihoError::Api { status, body } => {
            // Never forward 401/403 from Kumiho — the browser would confuse them
            // with Construct pairing auth failures and force a re-pair.
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

/// Build metadata `HashMap` from the create/update body.
fn agent_metadata(body: &CreateAgentBody) -> HashMap<String, String> {
    let mut meta = HashMap::new();
    meta.insert("display_name".to_string(), body.name.clone());
    meta.insert("identity".to_string(), body.identity.clone());
    meta.insert("soul".to_string(), body.soul.clone());
    if !body.expertise.is_empty() {
        meta.insert("expertise".to_string(), body.expertise.join(","));
    }
    if let Some(ref tone) = body.tone {
        meta.insert("tone".to_string(), tone.clone());
    }
    if let Some(ref role) = body.role {
        meta.insert("role".to_string(), role.clone());
    }
    if let Some(ref agent_type) = body.agent_type {
        meta.insert("agent_type".to_string(), agent_type.clone());
    }
    if let Some(ref model) = body.model {
        meta.insert("model".to_string(), model.clone());
    }
    if let Some(ref hint) = body.system_hint {
        meta.insert("system_hint".to_string(), hint.clone());
    }
    meta
}

/// Build an `AgentResponse` from an item + its latest revision metadata.
fn to_agent_response(item: &ItemResponse, rev: Option<&RevisionResponse>) -> AgentResponse {
    let meta = rev.map(|r| &r.metadata);
    let get = |key: &str| -> String { meta.and_then(|m| m.get(key)).cloned().unwrap_or_default() };
    let expertise_str = get("expertise");
    let expertise: Vec<String> = if expertise_str.is_empty() {
        Vec::new()
    } else {
        expertise_str
            .split(',')
            .map(|s| s.trim().to_string())
            .collect()
    };

    let display_name = {
        let n = get("display_name");
        if n.is_empty() {
            item.item_name.clone()
        } else {
            n
        }
    };

    AgentResponse {
        kref: item.kref.clone(),
        name: display_name,
        item_name: item.item_name.clone(),
        kind: item.kind.clone(),
        deprecated: item.deprecated,
        created_at: item.created_at.clone(),
        identity: get("identity"),
        soul: get("soul"),
        expertise,
        tone: get("tone"),
        role: get("role"),
        agent_type: get("agent_type"),
        model: get("model"),
        system_hint: get("system_hint"),
        revision: rev.map(|r| r.number),
    }
}

/// Fetch published (or latest) revision for each item and build responses.
///
/// Uses batch API for a single HTTP call instead of N parallel requests.
/// Falls back to parallel individual calls if the batch endpoint is unavailable.
async fn enrich_items(client: &KumihoClient, items: Vec<ItemResponse>) -> Vec<AgentResponse> {
    if items.is_empty() {
        return Vec::new();
    }

    let krefs: Vec<String> = items.iter().map(|i| i.kref.clone()).collect();

    // Try batch fetch (published tag first, then latest as fallback)
    if let Ok(rev_map) = client.batch_get_revisions(&krefs, "published").await {
        // Find items missing a published revision and fetch latest for those
        let missing: Vec<String> = krefs
            .iter()
            .filter(|k| !rev_map.contains_key(*k))
            .cloned()
            .collect();
        let latest_map = if !missing.is_empty() {
            client
                .batch_get_revisions(&missing, "latest")
                .await
                .unwrap_or_default()
        } else {
            std::collections::HashMap::new()
        };

        return items
            .iter()
            .map(|item| {
                let rev = rev_map
                    .get(&item.kref)
                    .or_else(|| latest_map.get(&item.kref));
                to_agent_response(item, rev)
            })
            .collect();
    }

    // Fallback: parallel individual calls
    let handles: Vec<_> = items
        .iter()
        .map(|item| {
            let kref = item.kref.clone();
            let client = client.clone();
            tokio::spawn(async move { client.get_published_or_latest(&kref).await.ok() })
        })
        .collect();
    let mut agents = Vec::with_capacity(items.len());
    for (item, handle) in items.iter().zip(handles) {
        let rev = handle.await.ok().flatten();
        agents.push(to_agent_response(item, rev.as_ref()));
    }
    agents
}

// ── Handlers ────────────────────────────────────────────────────────────

/// GET /api/agents
pub async fn handle_list_agents(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(query): Query<AgentListQuery>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let client = build_kumiho_client(&state);

    let project_name = agent_project(&state);
    let space_path = agent_space_path(&state);

    // Search or list
    let items_result = if let Some(ref q) = query.q {
        client
            .search_items(q, &project_name, "agent", query.include_deprecated)
            .await
            .map(|results| results.into_iter().map(|sr| sr.item).collect::<Vec<_>>())
    } else {
        client
            .list_items(&space_path, query.include_deprecated)
            .await
    };

    // Pagination parameters
    let per_page = query.per_page.unwrap_or(9).min(50).max(1);
    let page = query.page.unwrap_or(1).max(1);

    // Check cache for non-search list requests
    if query.q.is_none() {
        if let Some(cached) = get_cached_agents(query.include_deprecated) {
            let total_count = cached.len() as u32;
            let skip = ((page - 1) * per_page) as usize;
            let agents: Vec<_> = cached
                .into_iter()
                .skip(skip)
                .take(per_page as usize)
                .collect();
            return Json(serde_json::json!({
                "agents": agents,
                "total_count": total_count,
                "page": page,
                "per_page": per_page,
            }))
            .into_response();
        }
    }

    match items_result {
        Ok(items) => {
            let agents = enrich_items(&client, items).await;
            // Cache non-search results
            if query.q.is_none() {
                set_cached_agents(&agents, query.include_deprecated);
            }
            let total_count = agents.len() as u32;
            let skip = ((page - 1) * per_page) as usize;
            let agents: Vec<_> = agents
                .into_iter()
                .skip(skip)
                .take(per_page as usize)
                .collect();
            Json(serde_json::json!({
                "agents": agents,
                "total_count": total_count,
                "page": page,
                "per_page": per_page,
            }))
            .into_response()
        }
        Err(ref e) if matches!(e, KumihoError::Api { status: 404, .. }) => {
            // Project or space doesn't exist yet — create them and return empty list.
            let _ = client.ensure_project(&project_name).await;
            let _ = client.ensure_space(&project_name, AGENT_SPACE_NAME).await;
            Json(serde_json::json!({
                "agents": [],
                "total_count": 0,
                "page": page,
                "per_page": per_page,
            }))
            .into_response()
        }
        Err(e) => kumiho_err(e).into_response(),
    }
}

/// POST /api/agents
pub async fn handle_create_agent(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<CreateAgentBody>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let client = build_kumiho_client(&state);
    let project_name = agent_project(&state);
    let space_path = agent_space_path(&state);

    // 1. Ensure project + space exist (idempotent)
    if let Err(e) = client.ensure_project(&project_name).await {
        return kumiho_err(e).into_response();
    }
    if let Err(e) = client.ensure_space(&project_name, AGENT_SPACE_NAME).await {
        return kumiho_err(e).into_response();
    }

    // 2. Create item (slugify name for kref-safe identifier)
    let slug = slugify(&body.name);
    let item = match client
        .create_item(&space_path, &slug, "agent", HashMap::new())
        .await
    {
        Ok(item) => item,
        Err(e) => return kumiho_err(e).into_response(),
    };

    // 3. Create revision with metadata
    let metadata = agent_metadata(&body);
    let rev = match client.create_revision(&item.kref, metadata).await {
        Ok(rev) => rev,
        Err(e) => return kumiho_err(e).into_response(),
    };

    // 4. Tag revision as published
    let _ = client.tag_revision(&rev.kref, "published").await;

    invalidate_agent_cache();
    let agent = to_agent_response(&item, Some(&rev));
    (
        StatusCode::CREATED,
        Json(serde_json::json!({ "agent": agent })),
    )
        .into_response()
}

/// PUT /api/agents/:kref
///
/// The kref is passed as `*kref` to capture the full `kref://...` path.
pub async fn handle_update_agent(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(kref): Path<String>,
    Json(body): Json<CreateAgentBody>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let kref = normalize_kref(&kref);
    let client = build_kumiho_client(&state);
    let space_path = agent_space_path(&state);

    // Create new revision on existing item with updated metadata
    let metadata = agent_metadata(&body);
    let rev = match client.create_revision(&kref, metadata).await {
        Ok(rev) => rev,
        Err(e) => return kumiho_err(e).into_response(),
    };

    // Tag revision as published
    let _ = client.tag_revision(&rev.kref, "published").await;

    // Fetch item details for the full response
    let items = match client.list_items(&space_path, true).await {
        Ok(items) => items,
        Err(e) => return kumiho_err(e).into_response(),
    };

    invalidate_agent_cache();
    let item = items.iter().find(|i| i.kref == kref);
    match item {
        Some(item) => {
            let agent = to_agent_response(item, Some(&rev));
            Json(serde_json::json!({ "agent": agent })).into_response()
        }
        None => {
            // Item was found (revision succeeded) but not in list — build a minimal response.
            // `item_name` is the slug (kref-safe identifier), not the human display
            // name; mirror the create handler's slugify(body.name) here so this rare
            // fallback path can't drop a "Pretty Name" string into a slug field.
            let fallback = ItemResponse {
                kref: kref.clone(),
                name: body.name.clone(),
                item_name: slugify(&body.name),
                kind: "agent".to_string(),
                deprecated: false,
                created_at: None,
                metadata: HashMap::new(),
            };
            let agent = to_agent_response(&fallback, Some(&rev));
            Json(serde_json::json!({ "agent": agent })).into_response()
        }
    }
}

/// POST /api/agents/deprecate
pub async fn handle_deprecate_agent(
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
            invalidate_agent_cache();
            let rev = client.get_published_or_latest(&kref).await.ok();
            let agent = to_agent_response(&item, rev.as_ref());
            Json(serde_json::json!({ "agent": agent })).into_response()
        }
        Err(e) => kumiho_err(e).into_response(),
    }
}

/// DELETE /api/agents/:kref
pub async fn handle_delete_agent(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(kref): Path<String>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let kref = normalize_kref(&kref);
    let client = build_kumiho_client(&state);

    match client.delete_item(&kref).await {
        Ok(()) => {
            invalidate_agent_cache();
            StatusCode::NO_CONTENT.into_response()
        }
        Err(e) => kumiho_err(e).into_response(),
    }
}
