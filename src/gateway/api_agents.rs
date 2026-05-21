//! REST API handlers for agent management (`/api/agents`).
//!
//! Proxies to Kumiho FastAPI for persistent agent storage.  Each agent is a
//! Kumiho item of kind `"agent"` in the `Construct/AgentPool` space.  Agent
//! metadata (identity, soul, expertise, etc.) is stored as revision metadata.

use super::AppState;
use super::api::require_auth;
use super::kumiho_client::{
    ItemResponse, KumihoClient, KumihoError, RevisionResponse, build_kumiho_client, slugify,
};
use super::workspace_assets;

/// Normalize a kref from a URL path — strip existing `kref://` prefix to avoid doubling.
fn normalize_kref(raw: &str) -> String {
    let stripped = raw.strip_prefix("kref://").unwrap_or(raw);
    format!("kref://{stripped}")
}
use axum::{
    extract::{Multipart, Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Json},
};
use parking_lot::Mutex;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::{Component, Path as FsPath, PathBuf};
use std::sync::OnceLock;
use std::time::Instant;
use tracing::{error, warn};

// ── Response cache (avoids N+1 Kumiho calls on rapid dashboard polls) ───

struct AgentCache {
    agents: Vec<AgentResponse>,
    include_deprecated: bool,
    fetched_at: Instant,
}

static AGENT_CACHE: OnceLock<Mutex<Option<AgentCache>>> = OnceLock::new();
const CACHE_TTL_SECS: u64 = 3;
pub const AGENT_AVATAR_MAX_BODY: usize = 5 * 1024 * 1024;
const AGENT_AVATAR_TTL_SECS: u64 = 24 * 60 * 60;
pub(super) const PROFILE_AVATAR_ARTIFACT_NAME: &str = "profile-avatar";
pub(super) const AVATAR_LOCATION_KEY: &str = "avatar_location";
pub(super) const AVATAR_FILENAME_KEY: &str = "avatar_filename";
pub(super) const AVATAR_MIME_KEY: &str = "avatar_mime";
pub(super) const AVATAR_ARTIFACT_NAME_KEY: &str = "avatar_artifact_name";
pub(super) const AVATAR_METADATA_KEYS: &[&str] = &[
    AVATAR_LOCATION_KEY,
    AVATAR_FILENAME_KEY,
    AVATAR_MIME_KEY,
    AVATAR_ARTIFACT_NAME_KEY,
];

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
    pub revision_number: Option<i32>,
    pub avatar_url: Option<String>,
    pub avatar_artifact_name: Option<String>,
    pub avatar_filename: Option<String>,
    pub avatar_mime: Option<String>,
}

// ── Helpers ─────────────────────────────────────────────────────────────

/// Convert Kumiho error to an HTTP response.
///
/// Delegates to the centralised [`super::kumiho_client::kumiho_error_to_response`]
/// so every gateway route returns the same shape and upstream HTML never leaks
/// to the dashboard.
fn kumiho_err(e: KumihoError) -> axum::response::Response {
    super::kumiho_client::kumiho_error_to_response(e)
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

pub(super) fn preserve_avatar_metadata(
    metadata: &mut HashMap<String, String>,
    rev: Option<&RevisionResponse>,
) {
    let Some(rev) = rev else {
        return;
    };
    for key in AVATAR_METADATA_KEYS {
        if let Some(value) = rev.metadata.get(*key) {
            metadata.insert((*key).to_string(), value.clone());
        }
    }
}

pub(super) fn sanitize_upload_filename(name: &str) -> String {
    let cleaned = name
        .chars()
        .map(|c| match c {
            '/' | '\\' | ':' | '*' | '?' | '"' | '<' | '>' | '|' => '_',
            c if c.is_control() => '_',
            c => c,
        })
        .collect::<String>()
        .trim()
        .trim_matches('.')
        .to_string();
    if cleaned.is_empty() {
        "avatar".to_string()
    } else {
        cleaned
    }
}

pub(super) fn ascii_storage_segment(value: &str, fallback: &str) -> String {
    let mut out = String::new();
    let mut prev_dash = false;
    for c in value.trim().to_lowercase().chars() {
        let next = if c.is_ascii_alphanumeric() {
            Some(c)
        } else if c == '-' || c == '_' {
            Some('-')
        } else {
            None
        };
        match next {
            Some('-') => {
                if !prev_dash && !out.is_empty() {
                    out.push('-');
                    prev_dash = true;
                }
            }
            Some(c) => {
                out.push(c);
                prev_dash = false;
            }
            None => {
                if !prev_dash && !out.is_empty() {
                    out.push('-');
                    prev_dash = true;
                }
            }
        }
    }
    let trimmed = out.trim_matches('-');
    if trimmed.is_empty() {
        fallback.to_string()
    } else {
        trimmed.to_string()
    }
}

#[derive(Clone, Copy)]
pub(super) struct AvatarImageKind {
    pub(super) ext: &'static str,
    pub(super) mime: &'static str,
}

struct AvatarUpload {
    filename: String,
    mime: String,
    bytes: Vec<u8>,
}

pub(super) fn detect_avatar_image(
    bytes: &[u8],
    declared_mime: &str,
) -> Result<AvatarImageKind, &'static str> {
    let mime = declared_mime
        .split(';')
        .next()
        .unwrap_or_default()
        .trim()
        .to_ascii_lowercase();
    if mime == "image/svg+xml" {
        return Err("svg avatars are not supported");
    }

    let kind = if bytes.starts_with(&[0x89, b'P', b'N', b'G', 0x0d, 0x0a, 0x1a, 0x0a]) {
        AvatarImageKind {
            ext: "png",
            mime: "image/png",
        }
    } else if bytes.starts_with(&[0xff, 0xd8, 0xff]) {
        AvatarImageKind {
            ext: "jpg",
            mime: "image/jpeg",
        }
    } else if bytes.len() >= 12 && &bytes[0..4] == b"RIFF" && &bytes[8..12] == b"WEBP" {
        AvatarImageKind {
            ext: "webp",
            mime: "image/webp",
        }
    } else {
        return Err("avatar must be a png, jpeg, or webp image");
    };

    if !mime.is_empty() && mime != "application/octet-stream" && !mime.starts_with("image/") {
        return Err("avatar upload content type must be an image");
    }
    Ok(kind)
}

fn path_to_workspace_rel(workspace_dir: &FsPath, location: &str) -> Option<String> {
    let path = PathBuf::from(location);
    let rel = if path.is_absolute() {
        match path.strip_prefix(workspace_dir) {
            Ok(stripped) => stripped.to_path_buf(),
            Err(_) => {
                let root = workspace_dir.canonicalize().ok()?;
                let canonical = path.canonicalize().ok()?;
                canonical.strip_prefix(root).ok()?.to_path_buf()
            }
        }
    } else {
        path
    };

    let mut parts = Vec::new();
    for component in rel.components() {
        match component {
            Component::Normal(part) => parts.push(part.to_string_lossy().to_string()),
            Component::CurDir => {}
            _ => return None,
        }
    }
    if parts.is_empty() {
        None
    } else {
        Some(parts.join("/"))
    }
}

pub(super) fn avatar_url_from_metadata(
    state: &AppState,
    meta: Option<&HashMap<String, String>>,
) -> Option<String> {
    let location = meta?.get(AVATAR_LOCATION_KEY)?;
    let workspace_dir = state.config.lock().workspace_dir.clone();
    let rel_path = path_to_workspace_rel(&workspace_dir, location)?;
    Some(workspace_assets::sign_url(
        &rel_path,
        AGENT_AVATAR_TTL_SECS,
        state.service_token.as_bytes(),
    ))
}

/// Build an `AgentResponse` from an item + its latest revision metadata.
fn to_agent_response(
    state: &AppState,
    item: &ItemResponse,
    rev: Option<&RevisionResponse>,
) -> AgentResponse {
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
        revision_number: rev.map(|r| r.number),
        avatar_url: avatar_url_from_metadata(state, meta),
        avatar_artifact_name: meta.and_then(|m| m.get(AVATAR_ARTIFACT_NAME_KEY).cloned()),
        avatar_filename: meta.and_then(|m| m.get(AVATAR_FILENAME_KEY).cloned()),
        avatar_mime: meta.and_then(|m| m.get(AVATAR_MIME_KEY).cloned()),
    }
}

/// Fetch published (or latest) revision for each item and build responses.
///
/// Uses batch API for a single HTTP call instead of N parallel requests.
/// Falls back to parallel individual calls if the batch endpoint is unavailable.
async fn enrich_items(
    state: &AppState,
    client: &KumihoClient,
    items: Vec<ItemResponse>,
) -> Vec<AgentResponse> {
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
                to_agent_response(state, item, rev)
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
        agents.push(to_agent_response(state, item, rev.as_ref()));
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
            let agents = enrich_items(&state, &client, items).await;
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
    let agent = to_agent_response(&state, &item, Some(&rev));
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
    let current_rev = client.get_published_or_latest(&kref).await.ok();

    // Create new revision on existing item with updated metadata
    let mut metadata = agent_metadata(&body);
    preserve_avatar_metadata(&mut metadata, current_rev.as_ref());
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
            let agent = to_agent_response(&state, item, Some(&rev));
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
                author: None,
                username: None,
                author_display: None,
                metadata: HashMap::new(),
            };
            let agent = to_agent_response(&state, &fallback, Some(&rev));
            Json(serde_json::json!({ "agent": agent })).into_response()
        }
    }
}

/// POST /api/agents/avatar
///
/// Multipart fields:
/// - `kref`: agent item kref
/// - `file`: png/jpeg/webp image bytes
pub async fn handle_upload_agent_avatar(
    State(state): State<AppState>,
    headers: HeaderMap,
    mut multipart: Multipart,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let mut kref: Option<String> = None;
    let mut upload: Option<AvatarUpload> = None;

    while let Ok(Some(field)) = multipart.next_field().await {
        let name = field.name().map(str::to_string);
        match name.as_deref() {
            Some("kref") => match field.text().await {
                Ok(value) => kref = Some(normalize_kref(value.trim())),
                Err(err) => {
                    warn!(err = %err, "failed to read agent avatar kref field");
                    return (
                        StatusCode::BAD_REQUEST,
                        Json(serde_json::json!({ "error": "failed to read kref field" })),
                    )
                        .into_response();
                }
            },
            Some("file") => {
                let filename = field
                    .file_name()
                    .map(sanitize_upload_filename)
                    .unwrap_or_else(|| "avatar".to_string());
                let mime = field
                    .content_type()
                    .map(str::to_string)
                    .filter(|value| !value.trim().is_empty())
                    .unwrap_or_else(|| "application/octet-stream".to_string());
                let bytes = match field.bytes().await {
                    Ok(bytes) => bytes,
                    Err(err) => {
                        warn!(err = %err, "failed to read agent avatar upload bytes");
                        return (
                            StatusCode::BAD_REQUEST,
                            Json(serde_json::json!({ "error": "failed to read upload body" })),
                        )
                            .into_response();
                    }
                };
                if bytes.is_empty() {
                    return (
                        StatusCode::BAD_REQUEST,
                        Json(serde_json::json!({ "error": "empty avatar file" })),
                    )
                        .into_response();
                }
                if bytes.len() > AGENT_AVATAR_MAX_BODY {
                    return (
                        StatusCode::PAYLOAD_TOO_LARGE,
                        Json(serde_json::json!({
                            "error": format!(
                                "avatar exceeds {} byte limit (received {} bytes)",
                                AGENT_AVATAR_MAX_BODY,
                                bytes.len(),
                            ),
                        })),
                    )
                        .into_response();
                }
                upload = Some(AvatarUpload {
                    filename,
                    mime,
                    bytes: bytes.to_vec(),
                });
            }
            _ => {}
        }
    }

    let Some(kref) = kref else {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({ "error": "missing agent kref" })),
        )
            .into_response();
    };
    let Some(upload) = upload else {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({ "error": "missing avatar file" })),
        )
            .into_response();
    };

    let kind = match detect_avatar_image(&upload.bytes, &upload.mime) {
        Ok(kind) => kind,
        Err(message) => {
            return (
                StatusCode::UNSUPPORTED_MEDIA_TYPE,
                Json(serde_json::json!({ "error": message })),
            )
                .into_response();
        }
    };

    let client = build_kumiho_client(&state);
    let space_path = agent_space_path(&state);
    let item = match client.list_items(&space_path, true).await {
        Ok(items) => match items.into_iter().find(|item| item.kref == kref) {
            Some(item) => item,
            None => {
                return (
                    StatusCode::NOT_FOUND,
                    Json(serde_json::json!({ "error": "agent not found" })),
                )
                    .into_response();
            }
        },
        Err(e) => return kumiho_err(e).into_response(),
    };

    let current_rev = match client.get_published_or_latest(&kref).await {
        Ok(rev) => rev,
        Err(e) => return kumiho_err(e).into_response(),
    };

    let project_segment = ascii_storage_segment(&agent_project(&state), "construct");
    let item_segment = ascii_storage_segment(&item.item_name, "agent");
    let filename = format!("{}.{}", uuid::Uuid::new_v4(), kind.ext);
    let rel_path = PathBuf::from("artifacts")
        .join(project_segment)
        .join(AGENT_SPACE_NAME)
        .join(item_segment)
        .join("avatars")
        .join(filename);
    let workspace_dir = state.config.lock().workspace_dir.clone();
    let absolute_path = workspace_dir.join(&rel_path);
    if let Some(parent) = absolute_path.parent() {
        if let Err(err) = tokio::fs::create_dir_all(parent).await {
            error!(err = %err, dir = %parent.display(), "failed to create agent avatar directory");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({ "error": "failed to create avatar storage" })),
            )
                .into_response();
        }
    }
    if let Err(err) = tokio::fs::write(&absolute_path, &upload.bytes).await {
        error!(err = %err, path = %absolute_path.display(), "failed to persist agent avatar");
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "error": "failed to persist avatar" })),
        )
            .into_response();
    }

    let location = absolute_path.to_string_lossy().to_string();
    let mut metadata = current_rev.metadata.clone();
    metadata.insert(AVATAR_LOCATION_KEY.to_string(), location.clone());
    metadata.insert(AVATAR_FILENAME_KEY.to_string(), upload.filename.clone());
    metadata.insert(AVATAR_MIME_KEY.to_string(), kind.mime.to_string());
    metadata.insert(
        AVATAR_ARTIFACT_NAME_KEY.to_string(),
        PROFILE_AVATAR_ARTIFACT_NAME.to_string(),
    );

    let rev = match client.create_revision(&kref, metadata).await {
        Ok(rev) => rev,
        Err(e) => {
            let _ = tokio::fs::remove_file(&absolute_path).await;
            return kumiho_err(e).into_response();
        }
    };

    let mut artifact_metadata = HashMap::new();
    artifact_metadata.insert("kind".to_string(), "agent_avatar".to_string());
    artifact_metadata.insert("mime".to_string(), kind.mime.to_string());
    artifact_metadata.insert("filename".to_string(), upload.filename);
    artifact_metadata.insert("agent_item_kref".to_string(), kref.clone());

    if let Err(e) = client
        .create_artifact(
            &rev.kref,
            PROFILE_AVATAR_ARTIFACT_NAME,
            &location,
            artifact_metadata,
        )
        .await
    {
        let _ = tokio::fs::remove_file(&absolute_path).await;
        return kumiho_err(e).into_response();
    }

    let _ = client.tag_revision(&rev.kref, "published").await;

    invalidate_agent_cache();
    let agent = to_agent_response(&state, &item, Some(&rev));
    Json(serde_json::json!({ "agent": agent })).into_response()
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
            let agent = to_agent_response(&state, &item, rev.as_ref());
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
