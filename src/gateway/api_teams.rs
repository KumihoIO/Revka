//! REST API handlers for team management (`/api/teams`).
//!
//! Proxies to Kumiho FastAPI for persistent team storage.  Each team is a
//! Kumiho bundle in the `Construct/Teams` space.  Team members are agents from
//! `Construct/AgentPool` connected by directed edges (REPORTS_TO, SUPPORTS,
//! DEPENDS_ON) forming a DAG.

use super::AppState;
use super::api::require_auth;
use super::api_agents::{
    AVATAR_ARTIFACT_NAME_KEY, AVATAR_FILENAME_KEY, AVATAR_LOCATION_KEY, AVATAR_MIME_KEY,
    PROFILE_AVATAR_ARTIFACT_NAME, ascii_storage_segment, avatar_url_from_metadata,
    detect_avatar_image, preserve_avatar_metadata, sanitize_upload_filename,
};
use super::kumiho_client::{KumihoClient, KumihoError, build_kumiho_client};

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
use std::collections::{HashMap, HashSet, VecDeque};
use std::path::PathBuf;
use std::sync::OnceLock;
use std::time::Instant;
use tracing::{error, warn};

// ── Response cache (avoids N+1 Kumiho calls on rapid dashboard polls) ───

struct TeamCache {
    teams: Vec<TeamResponse>,
    include_deprecated: bool,
    fetched_at: Instant,
}

static TEAM_CACHE: OnceLock<Mutex<Option<TeamCache>>> = OnceLock::new();
const CACHE_TTL_SECS: u64 = 30;
pub const TEAM_AVATAR_MAX_BODY: usize = 5 * 1024 * 1024;

fn get_cached_teams(include_deprecated: bool) -> Option<Vec<TeamResponse>> {
    let lock = TEAM_CACHE.get_or_init(|| Mutex::new(None));
    let cache = lock.lock();
    if let Some(ref c) = *cache {
        if c.include_deprecated == include_deprecated
            && c.fetched_at.elapsed().as_secs() < CACHE_TTL_SECS
        {
            return Some(c.teams.clone());
        }
    }
    None
}

fn set_cached_teams(teams: &[TeamResponse], include_deprecated: bool) {
    let lock = TEAM_CACHE.get_or_init(|| Mutex::new(None));
    let mut cache = lock.lock();
    *cache = Some(TeamCache {
        teams: teams.to_vec(),
        include_deprecated,
        fetched_at: Instant::now(),
    });
}

pub fn invalidate_team_cache() {
    if let Some(lock) = TEAM_CACHE.get() {
        let mut cache = lock.lock();
        *cache = None;
    }
}

/// Space name within the project.
const TEAM_SPACE_NAME: &str = "Teams";

/// Kumiho project used for harness items (agents/teams/workflows), from config.
fn team_project(state: &AppState) -> String {
    state.config.lock().kumiho.harness_project.clone()
}

/// Full space path for team bundles, e.g. "/Construct/Teams".
fn team_space_path(state: &AppState) -> String {
    format!("/{}/{}", team_project(state), TEAM_SPACE_NAME)
}

// ── Query / request types ───────────────────────────────────────────────

#[derive(Deserialize)]
pub struct TeamListQuery {
    /// Include deprecated (disabled) teams.
    #[serde(default)]
    pub include_deprecated: bool,
    /// Page number (1-based). Default: 1.
    pub page: Option<u32>,
    /// Items per page. Default: 9, max: 50.
    pub per_page: Option<u32>,
}

#[derive(Deserialize)]
pub struct CreateTeamBody {
    pub name: String,
    pub description: Option<String>,
    #[serde(default)]
    pub members: Vec<String>, // agent krefs
    #[serde(default)]
    pub edges: Vec<TeamEdgeBody>,
}

#[derive(Deserialize)]
pub struct TeamEdgeBody {
    pub from_kref: String,
    pub to_kref: String,
    pub edge_type: String, // REPORTS_TO, SUPPORTS, DEPENDS_ON
}

#[derive(Deserialize)]
pub struct DeprecateBody {
    pub kref: String,
    pub deprecated: bool,
}

// ── Response types ──────────────────────────────────────────────────────

#[derive(Debug, Serialize, Clone)]
pub struct TeamResponse {
    pub kref: String,
    pub name: String,
    pub description: String,
    pub deprecated: bool,
    pub created_at: Option<String>,
    pub members: Vec<TeamMemberResponse>,
    pub edges: Vec<TeamEdgeResponse>,
    /// Summary fields from bundle metadata (for list view — avoids enrichment).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub member_count: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub member_names: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub edge_count: Option<u32>,
    pub avatar_url: Option<String>,
    pub avatar_artifact_name: Option<String>,
    pub avatar_filename: Option<String>,
    pub avatar_mime: Option<String>,
}

#[derive(Debug, Serialize, Clone)]
pub struct TeamMemberResponse {
    pub kref: String,
    pub name: String,
    pub role: String,
    pub agent_type: String,
    pub identity: String,
    pub expertise: Vec<String>,
    pub avatar_url: Option<String>,
}

#[derive(Debug, Serialize, Clone)]
pub struct TeamEdgeResponse {
    pub from_kref: String,
    pub to_kref: String,
    pub edge_type: String,
}

// ── Helpers ─────────────────────────────────────────────────────────────

/// Convert Kumiho error to an HTTP response (delegates to the centralised
/// helper in `kumiho_client`).
fn kumiho_err(e: KumihoError) -> axum::response::Response {
    super::kumiho_client::kumiho_error_to_response(e)
}

/// Build a full `TeamResponse` for a bundle kref by fetching members, enriching
/// each with agent metadata, and collecting edges between members.
async fn build_team_response(
    state: &AppState,
    client: &KumihoClient,
    bundle_kref: &str,
    name: &str,
    description: &str,
    deprecated: bool,
    created_at: Option<String>,
) -> Result<TeamResponse, KumihoError> {
    let team_rev = client.get_published_or_latest(bundle_kref).await.ok();
    let team_meta = team_rev.as_ref().map(|rev| &rev.metadata);
    let response_description = if description.is_empty() {
        team_meta
            .and_then(|meta| meta.get("description"))
            .cloned()
            .unwrap_or_default()
    } else {
        description.to_string()
    };
    let avatar_url = avatar_url_from_metadata(state, team_meta);
    let avatar_artifact_name = team_meta.and_then(|m| m.get(AVATAR_ARTIFACT_NAME_KEY).cloned());
    let avatar_filename = team_meta.and_then(|m| m.get(AVATAR_FILENAME_KEY).cloned());
    let avatar_mime = team_meta.and_then(|m| m.get(AVATAR_MIME_KEY).cloned());

    // 1. Fetch bundle members (gracefully handle errors — Kumiho may return 500)
    let members_resp = match client.list_bundle_members(bundle_kref).await {
        Ok(resp) => resp,
        Err(_) => {
            // Bundle member listing failed — return team with empty members.
            return Ok(TeamResponse {
                kref: bundle_kref.to_string(),
                name: name.to_string(),
                description: response_description,
                deprecated,
                created_at,
                members: Vec::new(),
                edges: Vec::new(),
                member_count: None,
                member_names: None,
                edge_count: None,
                avatar_url,
                avatar_artifact_name,
                avatar_filename,
                avatar_mime,
            });
        }
    };

    // 2. Enrich each member with agent metadata (batch)
    let member_krefs: Vec<String> = members_resp
        .members
        .iter()
        .map(|m| m.item_kref.clone())
        .collect();

    // Batch fetch published revisions, then latest for any missing
    let rev_map = client
        .batch_get_revisions(&member_krefs, "published")
        .await
        .unwrap_or_default();
    let missing: Vec<String> = member_krefs
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

    let mut member_responses = Vec::with_capacity(member_krefs.len());
    for member_kref in &member_krefs {
        let rev = rev_map
            .get(member_kref)
            .or_else(|| latest_map.get(member_kref));
        let meta = rev.map(|r| &r.metadata);
        let get =
            |key: &str| -> String { meta.and_then(|m| m.get(key)).cloned().unwrap_or_default() };

        let expertise_str = get("expertise");
        let expertise: Vec<String> = if expertise_str.is_empty() {
            Vec::new()
        } else {
            expertise_str
                .split(',')
                .map(|s| s.trim().to_string())
                .collect()
        };

        let item_name = {
            let name_from_kref = member_kref
                .rsplit('/')
                .next()
                .and_then(|s| s.split('.').next())
                .unwrap_or("")
                .to_string();
            if name_from_kref.is_empty() {
                member_kref.clone()
            } else {
                name_from_kref
            }
        };

        member_responses.push(TeamMemberResponse {
            kref: member_kref.clone(),
            name: item_name,
            role: get("role"),
            agent_type: get("agent_type"),
            identity: get("identity"),
            expertise,
            avatar_url: avatar_url_from_metadata(state, meta),
        });
    }

    // 3. Collect edges between team members.
    //    Use concurrent futures (not spawned tasks) with a short per-call timeout
    //    to avoid blowing the gateway request timeout on cloud-hosted Kumiho.
    let member_kref_set: HashSet<String> = member_krefs.iter().cloned().collect();
    let edge_handles: Vec<_> = member_krefs
        .iter()
        .filter_map(|kref| {
            let rev = rev_map.get(kref).or_else(|| latest_map.get(kref));
            rev.map(|r| {
                let client = client.clone();
                let item_kref = kref.clone();
                let rev_kref = r.kref.clone();
                tokio::spawn(async move {
                    let edges = tokio::time::timeout(
                        std::time::Duration::from_secs(8),
                        client.list_edges(&rev_kref, None, Some("outgoing")),
                    )
                    .await
                    .ok()
                    .and_then(|r| r.ok())
                    .unwrap_or_default();
                    (item_kref, edges)
                })
            })
        })
        .collect();

    let mut edge_results = Vec::new();
    for handle in edge_handles {
        if let Ok(result) = handle.await {
            edge_results.push(result);
        }
    }

    let mut edge_responses = Vec::new();
    for (member_kref, edges) in edge_results {
        for edge in edges {
            let target_item_kref = edge
                .target_kref
                .split('?')
                .next()
                .unwrap_or(&edge.target_kref);
            // Skip self-edges (same item, possibly different revisions)
            if target_item_kref == member_kref {
                continue;
            }
            if member_kref_set.contains(target_item_kref)
                || member_krefs
                    .iter()
                    .any(|mk| edge.target_kref.starts_with(mk))
            {
                edge_responses.push(TeamEdgeResponse {
                    from_kref: member_kref.clone(),
                    to_kref: target_item_kref.to_string(),
                    edge_type: edge.edge_type,
                });
            }
        }
    }

    Ok(TeamResponse {
        kref: bundle_kref.to_string(),
        name: name.to_string(),
        description: response_description,
        deprecated,
        created_at,
        member_count: None,
        member_names: None,
        edge_count: None,
        avatar_url,
        avatar_artifact_name,
        avatar_filename,
        avatar_mime,
        members: member_responses,
        edges: edge_responses,
    })
}

// ── Team graph validation ────────────────────────────────────────────────

#[derive(Debug, Serialize)]
struct TeamValidationError {
    code: String,
    message: String,
}

/// Validate team edges for cycles, reciprocal dependencies, and self-edges.
/// Returns a list of validation errors (empty = valid).
fn validate_team_edges(members: &[String], edges: &[TeamEdgeBody]) -> Vec<TeamValidationError> {
    let mut errors = Vec::new();
    let member_set: HashSet<&str> = members.iter().map(|s| s.as_str()).collect();

    // Check self-edges
    for edge in edges {
        if edge.from_kref == edge.to_kref {
            errors.push(TeamValidationError {
                code: "self_edge".into(),
                message: format!(
                    "Self-referencing edge: {} ({}).",
                    &edge.from_kref, edge.edge_type
                ),
            });
        }
    }

    // Check dangling references
    for edge in edges {
        if !member_set.contains(edge.from_kref.as_str()) {
            errors.push(TeamValidationError {
                code: "dangling_ref".into(),
                message: format!("Edge from_kref not a team member: {}", &edge.from_kref),
            });
        }
        if !member_set.contains(edge.to_kref.as_str()) {
            errors.push(TeamValidationError {
                code: "dangling_ref".into(),
                message: format!("Edge to_kref not a team member: {}", &edge.to_kref),
            });
        }
    }

    // Check reciprocal DEPENDS_ON
    let mut depends_pairs: HashSet<(&str, &str)> = HashSet::new();
    for edge in edges {
        let et = edge.edge_type.to_uppercase();
        if et == "DEPENDS_ON" {
            let pair = (edge.from_kref.as_str(), edge.to_kref.as_str());
            let reverse = (pair.1, pair.0);
            if depends_pairs.contains(&reverse) {
                errors.push(TeamValidationError {
                    code: "reciprocal_depends".into(),
                    message: format!(
                        "Reciprocal DEPENDS_ON between {} and {}. Pick one direction.",
                        &edge.from_kref, &edge.to_kref,
                    ),
                });
            }
            depends_pairs.insert(pair);
        }
    }

    // Cycle detection via Kahn's algorithm on execution edges
    let execution_types: HashSet<&str> = ["DEPENDS_ON", "SUPPORTS", "FEEDS_INTO"]
        .into_iter()
        .collect();

    // Build adjacency for execution ordering
    let mut in_degree: HashMap<&str, usize> = HashMap::new();
    let mut adj: HashMap<&str, Vec<&str>> = HashMap::new();
    for m in members {
        in_degree.insert(m.as_str(), 0);
        adj.insert(m.as_str(), Vec::new());
    }

    for edge in edges {
        let et = edge.edge_type.to_uppercase();
        if !execution_types.contains(et.as_str()) {
            continue;
        }
        let from = edge.from_kref.as_str();
        let to = edge.to_kref.as_str();
        if !member_set.contains(from) || !member_set.contains(to) {
            continue;
        }

        if et == "DEPENDS_ON" {
            // from depends on to → to runs first → directed edge to → from
            adj.entry(to).or_default().push(from);
            *in_degree.entry(from).or_default() += 1;
        } else {
            // SUPPORTS/FEEDS_INTO: from runs first → from → to
            adj.entry(from).or_default().push(to);
            *in_degree.entry(to).or_default() += 1;
        }
    }

    let mut queue: VecDeque<&str> = in_degree
        .iter()
        .filter(|&(_, deg)| *deg == 0)
        .map(|(&k, _)| k)
        .collect();

    let mut visited = 0usize;
    while let Some(node) = queue.pop_front() {
        visited += 1;
        for &dep in adj.get(node).unwrap_or(&Vec::new()) {
            if let Some(deg) = in_degree.get_mut(dep) {
                *deg -= 1;
                if *deg == 0 {
                    queue.push_back(dep);
                }
            }
        }
    }

    if visited < members.len() {
        let cycle_members: Vec<&str> = in_degree
            .iter()
            .filter(|&(_, deg)| *deg > 0)
            .map(|(&k, _)| k)
            .collect();
        errors.push(TeamValidationError {
            code: "cycle".into(),
            message: format!(
                "Dependency cycle detected among {} member(s). Break the cycle by removing or reversing an edge.",
                cycle_members.len(),
            ),
        });
    }

    errors
}

// ── Handlers ────────────────────────────────────────────────────────────

/// GET /api/teams
///
/// Returns team summaries from bundle metadata (no per-team enrichment).
/// Full member/edge details are loaded on demand via `GET /api/teams/{kref}`.
pub async fn handle_list_teams(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(query): Query<TeamListQuery>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let client = build_kumiho_client(&state);
    let project_name = team_project(&state);
    let space_path = team_space_path(&state);

    let items = match client
        .list_teams_in(&space_path, query.include_deprecated)
        .await
    {
        Ok(items) => items,
        Err(ref e) if matches!(e, KumihoError::Api { status: 404, .. }) => {
            let _ = client.ensure_project(&project_name).await;
            let _ = client.ensure_space(&project_name, TEAM_SPACE_NAME).await;
            return Json(
                serde_json::json!({ "teams": [], "total_count": 0, "page": 1, "per_page": 9 }),
            )
            .into_response();
        }
        // 500 from Kumiho previously returned a degraded 200/empty here. That
        // diverged from the rest of the gateway's contract (non-retried 5xx →
        // 503 + `kumiho_upstream_unavailable`). Route through the central
        // mapper so the dashboard sees a single consistent shape.
        Err(e) => return kumiho_err(e).into_response(),
    };

    let total_count = items.len() as u32;
    let per_page = query.per_page.unwrap_or(9).min(50).max(1);
    let page = query.page.unwrap_or(1).max(1);
    let skip = ((page - 1) * per_page) as usize;

    let page_items: Vec<_> = items.iter().skip(skip).take(per_page as usize).collect();
    let page_krefs: Vec<String> = page_items.iter().map(|item| item.kref.clone()).collect();
    let rev_map = client
        .batch_get_revisions(&page_krefs, "published")
        .await
        .unwrap_or_default();
    let missing: Vec<String> = page_krefs
        .iter()
        .filter(|kref| !rev_map.contains_key(*kref))
        .cloned()
        .collect();
    let latest_map = if missing.is_empty() {
        HashMap::new()
    } else {
        client
            .batch_get_revisions(&missing, "latest")
            .await
            .unwrap_or_default()
    };

    let teams: Vec<TeamResponse> = page_items
        .iter()
        .map(|item| {
            let rev = rev_map
                .get(&item.kref)
                .or_else(|| latest_map.get(&item.kref));
            let meta = rev.map(|rev| &rev.metadata).unwrap_or(&item.metadata);
            let member_count = meta.get("member_count").and_then(|v| v.parse::<u32>().ok());
            let member_names = meta.get("member_names").map(|v| {
                v.split(',')
                    .map(|s| s.trim().to_string())
                    .filter(|s| !s.is_empty())
                    .collect::<Vec<_>>()
            });
            let edge_count = meta.get("edge_count").and_then(|v| v.parse::<u32>().ok());

            TeamResponse {
                kref: item.kref.clone(),
                name: meta
                    .get("name")
                    .cloned()
                    .unwrap_or_else(|| item.item_name.clone()),
                description: meta.get("description").cloned().unwrap_or_default(),
                deprecated: item.deprecated,
                created_at: item.created_at.clone(),
                members: Vec::new(),
                edges: Vec::new(),
                member_count,
                member_names,
                edge_count,
                avatar_url: avatar_url_from_metadata(&state, Some(meta)),
                avatar_artifact_name: meta.get(AVATAR_ARTIFACT_NAME_KEY).cloned(),
                avatar_filename: meta.get(AVATAR_FILENAME_KEY).cloned(),
                avatar_mime: meta.get(AVATAR_MIME_KEY).cloned(),
            }
        })
        .collect();

    Json(serde_json::json!({
        "teams": teams,
        "total_count": total_count,
        "page": page,
        "per_page": per_page,
    }))
    .into_response()
}

/// POST /api/teams
pub async fn handle_create_team(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<CreateTeamBody>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let client = build_kumiho_client(&state);
    let project_name = team_project(&state);
    let space_path = team_space_path(&state);

    // 1. Ensure project + space exist (parallel, idempotent)
    let (proj_res, space_res) = tokio::join!(
        client.ensure_project(&project_name),
        client.ensure_space(&project_name, TEAM_SPACE_NAME),
    );
    if let Err(e) = proj_res {
        return kumiho_err(e).into_response();
    }
    if let Err(e) = space_res {
        return kumiho_err(e).into_response();
    }

    // 2. Validate team graph before persisting (always — even with empty edges)
    let validation_errors = validate_team_edges(&body.members, &body.edges);
    if !validation_errors.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "error": "Team graph is invalid",
                "validation_errors": validation_errors,
            })),
        )
            .into_response();
    }

    // 3. Build metadata for the bundle
    let mut metadata = HashMap::new();
    if let Some(ref desc) = body.description {
        metadata.insert("description".to_string(), desc.clone());
    }

    // 4. Create the bundle
    let bundle = match client
        .create_bundle(&space_path, &body.name, metadata)
        .await
    {
        Ok(b) => b,
        Err(e) => return kumiho_err(e).into_response(),
    };

    // 4. Add members to the bundle (parallel)
    let member_handles: Vec<_> = body
        .members
        .iter()
        .map(|member_kref| {
            let client = client.clone();
            let bundle_kref = bundle.kref.clone();
            let member_kref = member_kref.clone();
            tokio::spawn(async move {
                client
                    .add_bundle_member(&bundle_kref, &member_kref, HashMap::new())
                    .await
            })
        })
        .collect();
    for handle in member_handles {
        match handle.await {
            Ok(Ok(_)) => {}
            Ok(Err(e)) => return kumiho_err(e).into_response(),
            Err(_) => return kumiho_err(KumihoError::Decode("task failed".into())).into_response(),
        }
    }

    // 5. Create edges between members (batch-resolve revisions, then parallel edge creation)
    if !body.edges.is_empty() {
        // Collect all unique krefs needed for edges
        let edge_krefs: Vec<String> = body
            .edges
            .iter()
            .flat_map(|e| [e.from_kref.clone(), e.to_kref.clone()])
            .collect::<std::collections::HashSet<_>>()
            .into_iter()
            .collect();

        // Batch-resolve revisions for all edge endpoints
        let rev_map = client
            .batch_get_revisions(&edge_krefs, "published")
            .await
            .unwrap_or_default();
        let missing: Vec<String> = edge_krefs
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
            HashMap::new()
        };

        // Create edges in parallel (skip self-edges)
        let edge_handles: Vec<_> = body
            .edges
            .iter()
            .filter(|edge| edge.from_kref != edge.to_kref)
            .filter_map(|edge| {
                let source = rev_map
                    .get(&edge.from_kref)
                    .or_else(|| latest_map.get(&edge.from_kref));
                let target = rev_map
                    .get(&edge.to_kref)
                    .or_else(|| latest_map.get(&edge.to_kref));
                match (source, target) {
                    (Some(s), Some(t)) => {
                        let client = client.clone();
                        let src_kref = s.kref.clone();
                        let tgt_kref = t.kref.clone();
                        let edge_type = edge.edge_type.clone();
                        Some(tokio::spawn(async move {
                            client
                                .create_edge(&src_kref, &tgt_kref, &edge_type, HashMap::new())
                                .await
                        }))
                    }
                    _ => None,
                }
            })
            .collect();
        for handle in edge_handles {
            if let Ok(Err(e)) = handle.await {
                return kumiho_err(e).into_response();
            }
        }
    }

    // 6. Store summary metadata on the bundle for fast list retrieval.
    let member_names: Vec<String> = body
        .members
        .iter()
        .map(|k| {
            k.rsplit('/')
                .next()
                .and_then(|s| s.split('.').next())
                .unwrap_or("")
                .to_string()
        })
        .collect();
    let mut summary_meta = HashMap::new();
    summary_meta.insert("member_count".to_string(), body.members.len().to_string());
    summary_meta.insert("member_names".to_string(), member_names.join(","));
    summary_meta.insert("edge_count".to_string(), body.edges.len().to_string());
    if let Some(ref desc) = body.description {
        summary_meta.insert("description".to_string(), desc.clone());
    }
    if let Ok(rev) = client.create_revision(&bundle.kref, summary_meta).await {
        let _ = client.tag_revision(&rev.kref, "published").await;
    }

    // 7. Build and return the full team response
    let description = body.description.as_deref().unwrap_or("");
    match build_team_response(
        &state,
        &client,
        &bundle.kref,
        &bundle.item_name,
        description,
        bundle.deprecated,
        bundle.created_at.clone(),
    )
    .await
    {
        Ok(team) => {
            invalidate_team_cache();
            (
                StatusCode::CREATED,
                Json(serde_json::json!({ "team": team })),
            )
                .into_response()
        }
        Err(e) => kumiho_err(e).into_response(),
    }
}

/// GET /api/teams/{*kref}
pub async fn handle_get_team(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(kref): Path<String>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let kref = normalize_kref(&kref);
    let client = build_kumiho_client(&state);

    let bundle = match client.get_bundle(&kref).await {
        Ok(b) => b,
        Err(e) => return kumiho_err(e).into_response(),
    };

    let description = bundle
        .metadata
        .get("description")
        .cloned()
        .unwrap_or_default();
    match build_team_response(
        &state,
        &client,
        &bundle.kref,
        &bundle.item_name,
        &description,
        bundle.deprecated,
        bundle.created_at.clone(),
    )
    .await
    {
        Ok(team) => Json(serde_json::json!({ "team": team })).into_response(),
        Err(e) => kumiho_err(e).into_response(),
    }
}

/// PUT /api/teams/{*kref}
pub async fn handle_update_team(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(kref): Path<String>,
    Json(body): Json<CreateTeamBody>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let kref = normalize_kref(&kref);
    let client = build_kumiho_client(&state);

    // 1. Verify bundle exists
    let bundle = match client.get_bundle(&kref).await {
        Ok(b) => b,
        Err(e) => return kumiho_err(e).into_response(),
    };
    let current_rev = client.get_published_or_latest(&kref).await.ok();

    // 2. Validate team graph before updating (always — even with empty edges)
    let validation_errors = validate_team_edges(&body.members, &body.edges);
    if !validation_errors.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "error": "Team graph is invalid",
                "validation_errors": validation_errors,
            })),
        )
            .into_response();
    }

    // 3. Update bundle metadata via a new revision (includes summary for list view)
    let member_names: Vec<String> = body
        .members
        .iter()
        .map(|k| {
            k.rsplit('/')
                .next()
                .and_then(|s| s.split('.').next())
                .unwrap_or("")
                .to_string()
        })
        .collect();
    let mut metadata = HashMap::new();
    metadata.insert("name".to_string(), body.name.clone());
    if let Some(ref desc) = body.description {
        metadata.insert("description".to_string(), desc.clone());
    }
    metadata.insert("member_count".to_string(), body.members.len().to_string());
    metadata.insert("member_names".to_string(), member_names.join(","));
    metadata.insert("edge_count".to_string(), body.edges.len().to_string());
    preserve_avatar_metadata(&mut metadata, current_rev.as_ref());
    if let Ok(rev) = client.create_revision(&kref, metadata).await {
        let _ = client.tag_revision(&rev.kref, "published").await;
    }

    // 3. Sync members: fetch current, add missing, remove extra
    let current_members = match client.list_bundle_members(&kref).await {
        Ok(m) => m,
        Err(e) => return kumiho_err(e).into_response(),
    };

    let current_krefs: Vec<String> = current_members
        .members
        .iter()
        .map(|m| m.item_kref.clone())
        .collect();
    let desired_krefs: Vec<String> = body.members.clone();

    // Add missing members and remove extra members (parallel)
    let to_add: Vec<_> = desired_krefs
        .iter()
        .filter(|k| !current_krefs.contains(k))
        .cloned()
        .collect();
    let to_remove: Vec<_> = current_krefs
        .iter()
        .filter(|k| !desired_krefs.contains(k))
        .cloned()
        .collect();

    let add_handles: Vec<_> = to_add
        .iter()
        .map(|member_kref| {
            let client = client.clone();
            let bundle_kref = kref.clone();
            let member_kref = member_kref.clone();
            tokio::spawn(async move {
                client
                    .add_bundle_member(&bundle_kref, &member_kref, HashMap::new())
                    .await
            })
        })
        .collect();
    let remove_handles: Vec<_> = to_remove
        .iter()
        .map(|member_kref| {
            let client = client.clone();
            let bundle_kref = kref.clone();
            let member_kref = member_kref.clone();
            tokio::spawn(async move {
                client
                    .remove_bundle_member(&bundle_kref, &member_kref)
                    .await
            })
        })
        .collect();

    for handle in add_handles {
        if let Ok(Err(e)) = handle.await {
            return kumiho_err(e).into_response();
        }
    }
    for handle in remove_handles {
        if let Ok(Err(e)) = handle.await {
            return kumiho_err(e).into_response();
        }
    }

    // 4. Sync edges: batch-resolve revisions, delete existing, create desired (parallel)
    let all_member_krefs: Vec<String> = current_krefs
        .iter()
        .chain(desired_krefs.iter())
        .cloned()
        .collect::<std::collections::HashSet<_>>()
        .into_iter()
        .collect();

    let rev_map = client
        .batch_get_revisions(&all_member_krefs, "published")
        .await
        .unwrap_or_default();
    let missing: Vec<String> = all_member_krefs
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
        HashMap::new()
    };

    // Delete existing edges (parallel)
    let delete_handles: Vec<_> = all_member_krefs
        .iter()
        .filter_map(|mk| {
            let rev = rev_map.get(mk).or_else(|| latest_map.get(mk));
            rev.map(|r| {
                let client = client.clone();
                let rev_kref = r.kref.clone();
                tokio::spawn(async move {
                    if let Ok(edges) = client.list_edges(&rev_kref, None, Some("outgoing")).await {
                        for edge in edges {
                            let _ = client
                                .delete_edge(&edge.source_kref, &edge.target_kref, &edge.edge_type)
                                .await;
                        }
                    }
                })
            })
        })
        .collect();
    for handle in delete_handles {
        let _ = handle.await;
    }

    // Create desired edges (parallel, skip self-edges)
    if !body.edges.is_empty() {
        let edge_handles: Vec<_> = body
            .edges
            .iter()
            .filter(|edge| edge.from_kref != edge.to_kref)
            .filter_map(|edge| {
                let source = rev_map
                    .get(&edge.from_kref)
                    .or_else(|| latest_map.get(&edge.from_kref));
                let target = rev_map
                    .get(&edge.to_kref)
                    .or_else(|| latest_map.get(&edge.to_kref));
                match (source, target) {
                    (Some(s), Some(t)) => {
                        let client = client.clone();
                        let src_kref = s.kref.clone();
                        let tgt_kref = t.kref.clone();
                        let edge_type = edge.edge_type.clone();
                        Some(tokio::spawn(async move {
                            client
                                .create_edge(&src_kref, &tgt_kref, &edge_type, HashMap::new())
                                .await
                        }))
                    }
                    _ => None,
                }
            })
            .collect();
        for handle in edge_handles {
            if let Ok(Err(e)) = handle.await {
                return kumiho_err(e).into_response();
            }
        }
    }

    // 5. Build and return the full team response
    let description = body.description.as_deref().unwrap_or("");
    match build_team_response(
        &state,
        &client,
        &kref,
        &body.name,
        description,
        bundle.deprecated,
        bundle.created_at.clone(),
    )
    .await
    {
        Ok(team) => {
            invalidate_team_cache();
            Json(serde_json::json!({ "team": team })).into_response()
        }
        Err(e) => kumiho_err(e).into_response(),
    }
}

struct TeamAvatarUpload {
    filename: String,
    mime: String,
    bytes: Vec<u8>,
}

/// POST /api/teams/avatar
///
/// Multipart fields:
/// - `kref`: team bundle kref
/// - `file`: png/jpeg/webp image bytes
pub async fn handle_upload_team_avatar(
    State(state): State<AppState>,
    headers: HeaderMap,
    mut multipart: Multipart,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let mut kref: Option<String> = None;
    let mut upload: Option<TeamAvatarUpload> = None;
    while let Ok(Some(field)) = multipart.next_field().await {
        let name = field.name().map(str::to_string);
        match name.as_deref() {
            Some("kref") => match field.text().await {
                Ok(value) => kref = Some(normalize_kref(value.trim())),
                Err(err) => {
                    warn!(err = %err, "failed to read team avatar kref field");
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
                        warn!(err = %err, "failed to read team avatar upload bytes");
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
                if bytes.len() > TEAM_AVATAR_MAX_BODY {
                    return (
                        StatusCode::PAYLOAD_TOO_LARGE,
                        Json(serde_json::json!({
                            "error": format!(
                                "team avatar exceeds {} byte limit (received {} bytes)",
                                TEAM_AVATAR_MAX_BODY,
                                bytes.len(),
                            ),
                        })),
                    )
                        .into_response();
                }
                upload = Some(TeamAvatarUpload {
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
            Json(serde_json::json!({ "error": "missing team kref" })),
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
    let bundle = match client.get_bundle(&kref).await {
        Ok(bundle) => bundle,
        Err(e) => return kumiho_err(e).into_response(),
    };
    let current_rev = match client.get_published_or_latest(&kref).await {
        Ok(rev) => rev,
        Err(e) => return kumiho_err(e).into_response(),
    };

    let project_segment = ascii_storage_segment(&team_project(&state), "construct");
    let team_segment = ascii_storage_segment(&bundle.item_name, "team");
    let filename = format!("{}.{}", uuid::Uuid::new_v4(), kind.ext);
    let rel_path = PathBuf::from("artifacts")
        .join(project_segment)
        .join(TEAM_SPACE_NAME)
        .join(team_segment)
        .join("avatars")
        .join(filename);
    let workspace_dir = state.config.lock().workspace_dir.clone();
    let absolute_path = workspace_dir.join(&rel_path);
    if let Some(parent) = absolute_path.parent() {
        if let Err(err) = tokio::fs::create_dir_all(parent).await {
            error!(err = %err, dir = %parent.display(), "failed to create team avatar directory");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({ "error": "failed to create team avatar storage" })),
            )
                .into_response();
        }
    }
    if let Err(err) = tokio::fs::write(&absolute_path, &upload.bytes).await {
        error!(err = %err, path = %absolute_path.display(), "failed to persist team avatar");
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "error": "failed to persist team avatar" })),
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
    artifact_metadata.insert("kind".to_string(), "team_avatar".to_string());
    artifact_metadata.insert("mime".to_string(), kind.mime.to_string());
    artifact_metadata.insert("filename".to_string(), upload.filename);
    artifact_metadata.insert("team_bundle_kref".to_string(), kref.clone());

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

    invalidate_team_cache();
    let description = rev
        .metadata
        .get("description")
        .cloned()
        .or_else(|| bundle.metadata.get("description").cloned())
        .unwrap_or_default();
    match build_team_response(
        &state,
        &client,
        &bundle.kref,
        &bundle.item_name,
        &description,
        bundle.deprecated,
        bundle.created_at.clone(),
    )
    .await
    {
        Ok(team) => Json(serde_json::json!({ "team": team })).into_response(),
        Err(e) => kumiho_err(e).into_response(),
    }
}

/// DELETE /api/teams/{*kref}
pub async fn handle_delete_team(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(kref): Path<String>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let kref = normalize_kref(&kref);
    let client = build_kumiho_client(&state);

    // 1. Delete all edges between team members
    if let Ok(members_resp) = client.list_bundle_members(&kref).await {
        for member in &members_resp.members {
            if let Ok(rev) = client.get_published_or_latest(&member.item_kref).await {
                if let Ok(edges) = client.list_edges(&rev.kref, None, Some("outgoing")).await {
                    for edge in edges {
                        let _ = client
                            .delete_edge(&edge.source_kref, &edge.target_kref, &edge.edge_type)
                            .await;
                    }
                }
            }
        }

        // 2. Remove all members from the bundle
        for member in &members_resp.members {
            let _ = client.remove_bundle_member(&kref, &member.item_kref).await;
        }
    }

    // 3. Delete the bundle itself
    match client.delete_bundle(&kref).await {
        Ok(()) => {
            invalidate_team_cache();
            StatusCode::NO_CONTENT.into_response()
        }
        Err(e) => kumiho_err(e).into_response(),
    }
}

/// POST /api/teams/deprecate
pub async fn handle_deprecate_team(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<DeprecateBody>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let kref = body.kref.clone();
    let client = build_kumiho_client(&state);

    match client.deprecate_team(&kref, body.deprecated).await {
        Ok(()) => {
            invalidate_team_cache();
            Json(serde_json::json!({ "success": true })).into_response()
        }
        Err(e) => kumiho_err(e).into_response(),
    }
}
