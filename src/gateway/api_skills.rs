//! REST API handlers for skill management (`/api/skills`).
//!
//! Proxies to Kumiho FastAPI for persistent skill storage.  Each skill is a
//! Kumiho item of kind `"skill"` in the `<memory_project>/Skills` space.
//! Items created before the kind rename use the legacy `"skilldef"` kind
//! and remain readable via the search fallback in
//! [`super::kumiho_client::KumihoClient::search_skill_items_with_legacy`].
//!
//! ## Storage layout
//!
//! - **Revision metadata** — lightweight summary fields: `description`, `domain`,
//!   `tags`, `created_by`.  No full content here to keep `list_items` under
//!   Kumiho's 4 MB gRPC limit.
//! - **Artifact** (`SKILL.md`) — a `file://` reference to the local markdown
//!   file at `~/.construct/workspace/skills/<slug>.md`.  Content is read from
//!   disk on demand for detail views / edits.
//! - **Backward compat** — older artifacts that have `content` in their metadata
//!   are handled transparently; the detail endpoint reads from file first, then
//!   falls back to artifact metadata, then revision metadata.

use super::AppState;
use super::api::require_auth;
use super::api_agents::build_kumiho_client;
use super::kumiho_client::{ItemResponse, KumihoError, RevisionResponse, slugify};
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

struct SkillCache {
    skills: Vec<SkillResponse>,
    include_deprecated: bool,
    fetched_at: Instant,
}

static SKILL_CACHE: OnceLock<Mutex<Option<SkillCache>>> = OnceLock::new();
const CACHE_TTL_SECS: u64 = 30;

fn get_cached_skills(include_deprecated: bool) -> Option<Vec<SkillResponse>> {
    let lock = SKILL_CACHE.get_or_init(|| Mutex::new(None));
    let cache = lock.lock();
    if let Some(ref c) = *cache {
        if c.include_deprecated == include_deprecated
            && c.fetched_at.elapsed().as_secs() < CACHE_TTL_SECS
        {
            return Some(c.skills.clone());
        }
    }
    None
}

fn set_cached_skills(skills: &[SkillResponse], include_deprecated: bool) {
    let lock = SKILL_CACHE.get_or_init(|| Mutex::new(None));
    let mut cache = lock.lock();
    *cache = Some(SkillCache {
        skills: skills.to_vec(),
        include_deprecated,
        fetched_at: Instant::now(),
    });
}

pub fn invalidate_skill_cache() {
    if let Some(lock) = SKILL_CACHE.get() {
        let mut cache = lock.lock();
        *cache = None;
    }
}

/// Space name within the project.
const SKILL_SPACE_NAME: &str = "Skills";
/// Artifact name for skill markdown content.
const SKILL_ARTIFACT_NAME: &str = "SKILL.md";
/// Item kind for skill definitions.  Re-exports the canonical constant
/// from [`crate::skills::registration::SKILL_ITEM_KIND`] so the gateway
/// and `register_skill_with_kumiho` write the same value.
const SKILL_KIND: &str = crate::skills::registration::SKILL_ITEM_KIND;
/// Local directory where skill markdown files are stored.
const SKILLS_DIR: &str = ".construct/workspace/skills";

/// Memory project name from config (skills are behavioral knowledge).
fn skill_project(state: &AppState) -> String {
    state.config.lock().kumiho.memory_project.clone()
}

/// Full space path for skills, e.g. "/CognitiveMemory/Skills".
fn skill_space_path(state: &AppState) -> String {
    format!("/{}/{}", skill_project(state), SKILL_SPACE_NAME)
}

// ── Query / request types ───────────────────────────────────────────────

#[derive(Deserialize)]
pub struct SkillListQuery {
    /// Include deprecated (disabled) skills.
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
pub struct CreateSkillBody {
    pub name: String,
    pub description: String,
    pub content: String,
    pub domain: String,
    #[serde(default)]
    pub tags: Option<Vec<String>>,
}

#[derive(Deserialize)]
pub struct DeprecateBody {
    pub kref: String,
    pub deprecated: bool,
}

// ── Response types ──────────────────────────────────────────────────────

#[derive(Serialize, Clone)]
pub struct SkillResponse {
    pub kref: String,
    pub name: String,
    pub item_name: String,
    pub deprecated: bool,
    pub created_at: Option<String>,
    pub description: String,
    pub content: String,
    pub domain: String,
    pub tags: Vec<String>,
    pub revision_number: i32,
}

// ── Helpers ─────────────────────────────────────────────────────────────

/// Convert Kumiho error to an HTTP response.
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

/// Build lightweight revision metadata (no content — that goes into the artifact).
fn skill_revision_metadata(body: &CreateSkillBody) -> HashMap<String, String> {
    let mut meta = HashMap::new();
    meta.insert("description".to_string(), body.description.clone());
    meta.insert("domain".to_string(), body.domain.clone());
    meta.insert("created_by".to_string(), "construct-dashboard".to_string());
    if let Some(ref tags) = body.tags {
        if !tags.is_empty() {
            meta.insert("tags".to_string(), tags.join(","));
        }
    }
    meta
}

/// Build a `SkillResponse` from an item + its latest revision metadata.
///
/// For list views, `content` will be empty (artifact not fetched).
/// For detail views, pass `artifact_content` with the full markdown.
fn to_skill_response(
    item: &ItemResponse,
    rev: Option<&RevisionResponse>,
    artifact_content: Option<&str>,
) -> SkillResponse {
    let meta = rev.map(|r| &r.metadata);
    let get = |key: &str| -> String { meta.and_then(|m| m.get(key)).cloned().unwrap_or_default() };
    let tags_str = get("tags");
    let tags: Vec<String> = if tags_str.is_empty() {
        Vec::new()
    } else {
        tags_str.split(',').map(|s| s.trim().to_string()).collect()
    };

    // Content priority: explicit artifact_content > revision metadata (backward compat)
    let content = match artifact_content {
        Some(c) => c.to_string(),
        None => get("content"),
    };

    SkillResponse {
        kref: item.kref.clone(),
        name: item.item_name.clone(),
        item_name: item.item_name.clone(),
        deprecated: item.deprecated,
        created_at: item.created_at.clone(),
        description: get("description"),
        content,
        domain: get("domain"),
        tags,
        revision_number: rev.map(|r| r.number).unwrap_or(0),
    }
}

/// Fetch latest revision for each item and build list responses.
///
/// Content is NOT fetched here (list view) — only lightweight metadata.
/// Batches revision fetches in parallel chunks to stay under Kumiho's gRPC limit.
const BATCH_CHUNK_SIZE: usize = 20;

async fn enrich_items(
    client: &super::kumiho_client::KumihoClient,
    items: Vec<ItemResponse>,
) -> Vec<SkillResponse> {
    if items.is_empty() {
        return Vec::new();
    }

    let krefs: Vec<String> = items.iter().map(|i| i.kref.clone()).collect();

    // Fetch revisions in parallel chunks to avoid exceeding gRPC message
    // size limits while keeping total latency low.
    let mut set = tokio::task::JoinSet::new();
    for chunk in krefs.chunks(BATCH_CHUNK_SIZE) {
        let chunk_vec: Vec<String> = chunk.to_vec();
        let c = client.clone();
        set.spawn(async move { c.batch_get_revisions(&chunk_vec, "published").await });
    }

    let mut rev_map: std::collections::HashMap<String, RevisionResponse> =
        std::collections::HashMap::new();
    while let Some(res) = set.join_next().await {
        if let Ok(Ok(batch)) = res {
            rev_map.extend(batch);
        }
    }

    // Fetch latest for any items missing a published revision — use batch
    // calls instead of individual per-item requests to avoid Cloudflare
    // rate-limiting that causes the 30s gateway timeout to fire.
    let missing: Vec<String> = krefs
        .iter()
        .filter(|k| !rev_map.contains_key(*k))
        .cloned()
        .collect();
    if !missing.is_empty() {
        if let Ok(latest_map) = client.batch_get_revisions(&missing, "latest").await {
            rev_map.extend(latest_map);
        }
    }

    items
        .iter()
        .map(|item| {
            let rev = rev_map.get(&item.kref);
            // List view: no artifact content, but include revision metadata content
            // as a truncated preview for backward-compat with older skills.
            let mut skill = to_skill_response(item, rev, None);
            if skill.content.len() > 200 {
                skill.content = format!("{}...", &skill.content[..200]);
            }
            skill
        })
        .collect()
}

/// Resolve the local skills directory.
fn skills_dir() -> std::path::PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".to_string());
    std::path::PathBuf::from(home).join(SKILLS_DIR)
}

/// Fetch full skill content from local file (via artifact location), falling
/// back to artifact metadata, then revision metadata.
async fn fetch_skill_content(
    client: &super::kumiho_client::KumihoClient,
    rev: &RevisionResponse,
) -> String {
    // Try artifact first (new storage)
    if let Ok(artifacts) = client.get_artifacts(&rev.kref).await {
        for art in &artifacts {
            if art.name == SKILL_ARTIFACT_NAME {
                // Priority 1: read from local file via artifact location
                let location = &art.location;
                let file_path = if let Some(path) = location.strip_prefix("file://") {
                    Some(std::path::PathBuf::from(path))
                } else if location.starts_with('/') {
                    Some(std::path::PathBuf::from(location))
                } else {
                    None
                };
                if let Some(ref path) = file_path {
                    if let Ok(content) = tokio::fs::read_to_string(path).await {
                        return content;
                    }
                }
                // Priority 2: artifact metadata (legacy/clawhub installs)
                if let Some(content) = art.metadata.get("content") {
                    return content.clone();
                }
            }
        }
    }
    // Priority 3: revision metadata (very old skills)
    rev.metadata.get("content").cloned().unwrap_or_default()
}

/// Store skill content as a local file and create a `SKILL.md` artifact
/// pointing to it.  The file is written to `~/.construct/workspace/skills/<slug>.md`.
async fn store_skill_artifact(
    client: &super::kumiho_client::KumihoClient,
    revision_kref: &str,
    _item_kref: &str,
    slug: &str,
    content: &str,
) -> std::result::Result<(), KumihoError> {
    let dir = skills_dir();
    let _ = tokio::fs::create_dir_all(&dir).await;
    let file_path = dir.join(format!("{slug}.md"));
    let location = format!("file://{}", file_path.display());

    // Write content to local file
    tokio::fs::write(&file_path, content)
        .await
        .map_err(|e| KumihoError::Decode(format!("Failed to write skill file: {e}")))?;

    // Create artifact referencing the local file
    let metadata = HashMap::new();
    client
        .create_artifact(revision_kref, SKILL_ARTIFACT_NAME, &location, metadata)
        .await?;
    Ok(())
}

// ── Handlers ────────────────────────────────────────────────────────────

/// GET /api/skills
pub async fn handle_list_skills(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(query): Query<SkillListQuery>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    // Check cache first for non-search list requests
    if query.q.is_none() {
        if let Some(cached) = get_cached_skills(query.include_deprecated) {
            let total_count = cached.len() as u32;
            let per_page = query.per_page.unwrap_or(9).min(50).max(1);
            let page = query.page.unwrap_or(1).max(1);
            let skip = ((page - 1) * per_page) as usize;
            let skills: Vec<_> = cached
                .into_iter()
                .skip(skip)
                .take(per_page as usize)
                .collect();
            return Json(serde_json::json!({
                "skills": skills,
                "total_count": total_count,
                "page": page,
                "per_page": per_page,
            }))
            .into_response();
        }
    }

    let client = build_kumiho_client(&state);

    let project_name = skill_project(&state);
    let space_path = skill_space_path(&state);

    // Search mode — use Kumiho fulltext search.  Goes through the
    // legacy-aware helper so items written before the `skilldef` →
    // `skill` rename remain discoverable via a single user-facing query.
    if let Some(ref q) = query.q {
        let items_result = client
            .search_items_with_legacy(
                q,
                &project_name,
                SKILL_KIND,
                crate::skills::registration::LEGACY_SKILL_ITEM_KIND,
                query.include_deprecated,
            )
            .await
            .map(|results| results.into_iter().map(|sr| sr.item).collect::<Vec<_>>());

        return match items_result {
            Ok(items) => {
                let skills = enrich_items(&client, items).await;
                let total_count = skills.len() as u32;
                let per_page = query.per_page.unwrap_or(9).min(50).max(1);
                let page = query.page.unwrap_or(1).max(1);
                let skip = ((page - 1) * per_page) as usize;
                let skills: Vec<_> = skills
                    .into_iter()
                    .skip(skip)
                    .take(per_page as usize)
                    .collect();
                Json(serde_json::json!({
                    "skills": skills,
                    "total_count": total_count,
                    "page": page,
                    "per_page": per_page,
                }))
                .into_response()
            }
            Err(ref e) if matches!(e, KumihoError::Api { status: 404, .. }) => Json(
                serde_json::json!({ "skills": [], "total_count": 0, "page": 1, "per_page": 9 }),
            )
            .into_response(),
            Err(e) => kumiho_err(e).into_response(),
        };
    }

    // List mode — with lightweight revision metadata, direct list_items
    // should stay well under the 4 MB gRPC limit.  Fall back to name_filter
    // queries if the direct list fails (e.g., due to stale data).
    let include_deprecated = query.include_deprecated;
    let items: Vec<ItemResponse> = match client.list_items(&space_path, include_deprecated).await {
        Ok(items) => items,
        Err(_) => {
            // Fallback: name_filter queries covering all skill items
            // (canonical `"skill"` kind plus any legacy `"skilldef"`
            // items still in the space — `list_items` doesn't filter by
            // kind so both surface naturally).
            let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
            let mut fallback_items: Vec<ItemResponse> = Vec::new();
            for filter in &["a", "d"] {
                if let Ok(batch) = client
                    .list_items_filtered(&space_path, filter, include_deprecated)
                    .await
                {
                    for item in batch {
                        if seen.insert(item.kref.clone()) {
                            fallback_items.push(item);
                        }
                    }
                }
            }
            fallback_items
        }
    };

    if items.is_empty() {
        let _ = client.ensure_project(&project_name).await;
        let _ = client.ensure_space(&project_name, SKILL_SPACE_NAME).await;
    }

    let skills = enrich_items(&client, items).await;
    set_cached_skills(&skills, query.include_deprecated);

    // Pagination
    let total_count = skills.len() as u32;
    let per_page = query.per_page.unwrap_or(9).min(50).max(1);
    let page = query.page.unwrap_or(1).max(1);
    let skip = ((page - 1) * per_page) as usize;
    let skills: Vec<_> = skills
        .into_iter()
        .skip(skip)
        .take(per_page as usize)
        .collect();

    Json(serde_json::json!({
        "skills": skills,
        "total_count": total_count,
        "page": page,
        "per_page": per_page,
    }))
    .into_response()
}

/// GET /api/skills/:kref — fetch a single skill with full content.
pub async fn handle_get_skill(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(kref): Path<String>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let kref = format!("kref://{kref}");
    let client = build_kumiho_client(&state);

    // Fetch the published revision
    let rev = match client.get_published_or_latest(&kref).await {
        Ok(rev) => rev,
        Err(e) => return kumiho_err(e).into_response(),
    };

    // Fetch full content from artifact (or fallback to revision metadata)
    let content = fetch_skill_content(&client, &rev).await;

    // Build a minimal item from what we know
    let item_name = kref
        .rsplit('/')
        .next()
        .unwrap_or_default()
        .trim_end_matches(".skilldef")
        .trim_end_matches(".skill")
        .to_string();
    let item = ItemResponse {
        kref: kref.clone(),
        name: item_name.clone(),
        item_name,
        kind: SKILL_KIND.to_string(),
        deprecated: false,
        created_at: None,
        metadata: HashMap::new(),
    };

    let skill = to_skill_response(&item, Some(&rev), Some(&content));
    Json(serde_json::json!({ "skill": skill })).into_response()
}

/// POST /api/skills
pub async fn handle_create_skill(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<CreateSkillBody>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let client = build_kumiho_client(&state);
    let project_name = skill_project(&state);
    let space_path = skill_space_path(&state);

    // 1. Ensure project + space exist (idempotent)
    if let Err(e) = client.ensure_project(&project_name).await {
        return kumiho_err(e).into_response();
    }
    if let Err(e) = client.ensure_space(&project_name, SKILL_SPACE_NAME).await {
        return kumiho_err(e).into_response();
    }

    // 2. Create item (slugify name for kref-safe identifier)
    let slug = slugify(&body.name);
    let item = match client
        .create_item(&space_path, &slug, SKILL_KIND, HashMap::new())
        .await
    {
        Ok(item) => item,
        Err(e) => return kumiho_err(e).into_response(),
    };

    // 3. Create revision with lightweight metadata (no content)
    let metadata = skill_revision_metadata(&body);
    let rev = match client.create_revision(&item.kref, metadata).await {
        Ok(rev) => rev,
        Err(e) => return kumiho_err(e).into_response(),
    };

    // 4. Store full content as SKILL.md artifact (BEFORE publishing)
    if let Err(e) = store_skill_artifact(&client, &rev.kref, &item.kref, &slug, &body.content).await
    {
        tracing::warn!("Failed to create SKILL.md artifact for {}: {e}", item.kref);
    }

    // 5. Tag as published (after artifact is attached)
    let _ = client.tag_revision(&rev.kref, "published").await;

    invalidate_skill_cache();
    let skill = to_skill_response(&item, Some(&rev), Some(&body.content));
    (
        StatusCode::CREATED,
        Json(serde_json::json!({ "skill": skill })),
    )
        .into_response()
}

/// PUT /api/skills/:kref
///
/// The kref is passed as `*kref` to capture the full `kref://...` path.
pub async fn handle_update_skill(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(kref): Path<String>,
    Json(body): Json<CreateSkillBody>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let kref = format!("kref://{kref}");
    let client = build_kumiho_client(&state);

    // Derive slug from kref for file naming
    let slug = kref
        .rsplit('/')
        .next()
        .unwrap_or_default()
        .trim_end_matches(".skilldef")
        .trim_end_matches(".skill")
        .to_string();

    // Create new revision with lightweight metadata (no content)
    let metadata = skill_revision_metadata(&body);
    let rev = match client.create_revision(&kref, metadata).await {
        Ok(rev) => rev,
        Err(e) => return kumiho_err(e).into_response(),
    };

    // Store full content as SKILL.md artifact (BEFORE publishing)
    if let Err(e) = store_skill_artifact(&client, &rev.kref, &kref, &slug, &body.content).await {
        tracing::warn!("Failed to create SKILL.md artifact for {kref}: {e}");
    }

    // Tag as published (after artifact is attached)
    let _ = client.tag_revision(&rev.kref, "published").await;

    invalidate_skill_cache();

    let item = ItemResponse {
        kref: kref.clone(),
        name: body.name.clone(),
        item_name: body.name.clone(),
        kind: SKILL_KIND.to_string(),
        deprecated: false,
        created_at: None,
        metadata: HashMap::new(),
    };
    let skill = to_skill_response(&item, Some(&rev), Some(&body.content));
    Json(serde_json::json!({ "skill": skill })).into_response()
}

/// POST /api/skills/deprecate
pub async fn handle_deprecate_skill(
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
            invalidate_skill_cache();
            let rev = client.get_published_or_latest(&kref).await.ok();
            let skill = to_skill_response(&item, rev.as_ref(), None);
            Json(serde_json::json!({ "skill": skill })).into_response()
        }
        Err(e) => kumiho_err(e).into_response(),
    }
}

/// DELETE /api/skills/:kref
pub async fn handle_delete_skill(
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
            invalidate_skill_cache();
            StatusCode::NO_CONTENT.into_response()
        }
        Err(e) => kumiho_err(e).into_response(),
    }
}
