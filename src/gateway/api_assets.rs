//! Mutating Asset Browser API routes.
//!
//! The generic `/api/kumiho/*` proxy is GET-only by design. These routes keep
//! Asset Browser writes typed and idempotency-aware while still delegating the
//! persistent model to Kumiho.

use super::AppState;
use super::api::require_auth;
use super::api_agents::{ascii_storage_segment, sanitize_upload_filename};
use super::kumiho_client::build_kumiho_client;
use super::kumiho_client::invalidate_proxy_cache;
use super::kumiho_client::{
    ArtifactResponse, BundleMemberInfo, EdgeResponse, ItemResponse, KumihoClient, KumihoError,
    RevisionResponse, kumiho_error_to_response,
};
use crate::security::SecurityPolicy;
use axum::{
    Json,
    extract::{Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};
use std::ffi::OsString;
use std::io;
use std::path::{Path, PathBuf};

const MAX_EDIT_BYTES: usize = 1024 * 1024;
const MAX_GRAPH_DEPTH: u8 = 3;
const DEFAULT_GRAPH_NODE_LIMIT: usize = 100;
const MAX_GRAPH_NODE_LIMIT: usize = 200;
const PROTECTED_BUNDLE_SUFFIXES: &[&str] = &[
    "main-canon",
    "current-character-states",
    "active-storylines",
    "active-foreshadow",
];

#[derive(Deserialize)]
pub struct DeprecateItemBody {
    pub kref: String,
    pub deprecated: bool,
}

#[derive(Deserialize)]
pub struct DeprecateRevisionBody {
    pub kref: String,
    pub deprecated: bool,
}

#[derive(Deserialize)]
pub struct DeprecateArtifactBody {
    pub kref: String,
    pub deprecated: bool,
}

#[derive(Deserialize)]
pub struct PublishRevisionBody {
    pub kref: String,
}

#[derive(Deserialize)]
pub struct UpdateArtifactContentBody {
    pub artifact_kref: String,
    pub revision_kref: String,
    pub content: String,
}

#[derive(Deserialize)]
pub struct CreateProjectBody {
    pub name: String,
    pub description: Option<String>,
}

#[derive(Deserialize)]
pub struct CreateSpaceBody {
    pub parent_path: String,
    pub name: String,
}

#[derive(Deserialize)]
pub struct CreateItemBody {
    pub space_path: String,
    pub item_name: String,
    pub kind: String,
    #[serde(default)]
    pub metadata: HashMap<String, String>,
}

#[derive(Deserialize)]
pub struct CreateRevisionBody {
    pub item_kref: String,
    #[serde(default)]
    pub metadata: HashMap<String, String>,
}

#[derive(Deserialize)]
pub struct CreateBundleBody {
    pub space_path: String,
    pub bundle_name: String,
    #[serde(default)]
    pub metadata: HashMap<String, String>,
}

#[derive(Deserialize)]
pub struct CreateArtifactBody {
    pub revision_kref: String,
    pub name: String,
    pub location: String,
    #[serde(default)]
    pub metadata: HashMap<String, String>,
    #[serde(default)]
    pub content: Option<String>,
    #[serde(default)]
    pub write_file: bool,
    #[serde(default)]
    pub overwrite: bool,
    #[serde(default)]
    pub validate_exists: bool,
}

#[derive(Deserialize)]
pub struct CreateEdgeBody {
    pub source_kref: String,
    pub target_kref: String,
    pub edge_type: String,
    #[serde(default)]
    pub metadata: HashMap<String, String>,
}

#[derive(Deserialize)]
pub struct RevisionTagBody {
    pub kref: String,
    pub tag: String,
}

#[derive(Deserialize)]
pub struct BundleMemberBody {
    pub bundle_kref: String,
    pub item_kref: String,
    #[serde(default)]
    pub metadata: HashMap<String, String>,
    #[serde(default)]
    pub allow_protected: bool,
}

#[derive(Deserialize)]
pub struct AssetBundlesQuery {
    pub project: String,
    pub space_path: Option<String>,
}

#[derive(Deserialize)]
pub struct BundleMembersQuery {
    pub bundle_kref: String,
}

#[derive(Deserialize)]
pub struct DependencyGraphQuery {
    pub revision_kref: String,
    pub direction: Option<String>,
    pub depth: Option<u8>,
    pub edge_type: Option<String>,
    pub node_limit: Option<usize>,
}

#[derive(Serialize)]
pub struct UpdateArtifactContentResponse {
    pub revision: RevisionResponse,
    pub artifact: ArtifactResponse,
    pub created_revision: bool,
    pub copied_artifacts: usize,
}

#[derive(Serialize)]
pub struct BundleMemberDetail {
    pub membership: BundleMemberInfo,
    pub item: Option<ItemResponse>,
    pub latest_revision: Option<RevisionResponse>,
    pub current_revision: Option<RevisionResponse>,
    pub error: Option<String>,
}

#[derive(Serialize)]
pub struct BundleMembersDetailResponse {
    pub members: Vec<BundleMemberDetail>,
    pub total_count: Option<i32>,
}

#[derive(Debug, Clone, Serialize)]
pub struct AssetGraphNode {
    pub kref: String,
    pub item_kref: Option<String>,
    pub item_name: Option<String>,
    pub kind: Option<String>,
    pub revision_number: Option<i32>,
    pub tags: Vec<String>,
    pub metadata: HashMap<String, String>,
    pub artifacts: Vec<ArtifactResponse>,
    pub incoming_edges: Vec<EdgeResponse>,
    pub outgoing_edges: Vec<EdgeResponse>,
    pub created_at: Option<String>,
    pub missing: bool,
}

#[derive(Serialize)]
pub struct AssetDependencyGraphResponse {
    pub center_kref: String,
    pub direction: String,
    pub depth: u8,
    pub edge_type: Option<String>,
    pub node_limit: usize,
    pub truncated: bool,
    pub nodes: Vec<AssetGraphNode>,
    pub edges: Vec<EdgeResponse>,
}

fn kumiho_err(e: KumihoError) -> Response {
    kumiho_error_to_response(e)
}

fn json_error(status: StatusCode, msg: impl Into<String>) -> Response {
    (
        status,
        Json(serde_json::json!({
            "error": msg.into(),
        })),
    )
        .into_response()
}

fn trimmed_required(value: &str, label: &str) -> Result<String, Response> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return Err(json_error(
            StatusCode::BAD_REQUEST,
            format!("{label} is required"),
        ));
    }
    Ok(trimmed.to_string())
}

fn is_published(revision: &RevisionResponse) -> bool {
    revision.tags.iter().any(|tag| tag == "published")
}

fn kref_without_selector(kref: &str) -> &str {
    kref.split_once('?').map_or(kref, |(base, _)| base)
}

fn kref_leaf_name(kref: &str) -> &str {
    kref_without_selector(kref)
        .rsplit('/')
        .next()
        .unwrap_or(kref)
        .split_once('.')
        .map_or_else(|| kref_without_selector(kref), |(name, _)| name)
}

fn bundle_name_matches_suffix(name: &str, suffix: &str) -> bool {
    if name == suffix {
        return true;
    }
    match name.strip_suffix(suffix) {
        Some(prefix) => prefix.ends_with('-'),
        None => false,
    }
}

fn is_protected_bundle(kref: &str) -> bool {
    let name = kref_leaf_name(kref);
    PROTECTED_BUNDLE_SUFFIXES
        .iter()
        .any(|suffix| bundle_name_matches_suffix(name, suffix))
}

fn normalize_graph_direction(input: Option<&str>) -> String {
    match input.unwrap_or("both").trim().to_lowercase().as_str() {
        "upstream" | "dependencies" | "out" | "outgoing" => "outgoing".to_string(),
        "downstream" | "dependents" | "in" | "incoming" => "incoming".to_string(),
        _ => "both".to_string(),
    }
}

fn edge_neighbors(edge: &EdgeResponse, center: &str, direction: &str) -> Vec<String> {
    match direction {
        "outgoing" => {
            if edge.source_kref == center {
                vec![edge.target_kref.clone()]
            } else {
                Vec::new()
            }
        }
        "incoming" => {
            if edge.target_kref == center {
                vec![edge.source_kref.clone()]
            } else {
                Vec::new()
            }
        }
        _ => {
            let mut out = Vec::new();
            if edge.source_kref == center {
                out.push(edge.target_kref.clone());
            }
            if edge.target_kref == center {
                out.push(edge.source_kref.clone());
            }
            out
        }
    }
}

fn edge_key(edge: &EdgeResponse) -> String {
    format!(
        "{}\n{}\n{}",
        edge.source_kref, edge.edge_type, edge.target_kref
    )
}

fn is_remote_uri(raw: &str) -> bool {
    let trimmed = raw.trim();
    let Some((scheme, _)) = trimmed.split_once("://") else {
        return false;
    };
    !scheme.eq_ignore_ascii_case("file")
        && scheme
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '+' | '-' | '.'))
}

fn normalize_artifact_location(raw: &str) -> Result<String, String> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return Err("artifact location is empty".to_string());
    }
    if is_remote_uri(trimmed) {
        return Ok(trimmed.to_string());
    }
    let path = resolve_location(trimmed)?;
    Ok(location_from_path(&path, location_uses_file_uri(trimmed)))
}

fn resolve_location(raw: &str) -> Result<PathBuf, String> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return Err("artifact location is empty".to_string());
    }

    let stripped = trimmed.strip_prefix("file://").unwrap_or(trimmed);
    let expanded = if let Some(rest) = stripped.strip_prefix("~/") {
        match directories::UserDirs::new() {
            Some(dirs) => dirs.home_dir().join(rest),
            None => return Err("cannot resolve '~': no home directory".to_string()),
        }
    } else if stripped == "~" {
        match directories::UserDirs::new() {
            Some(dirs) => dirs.home_dir().to_path_buf(),
            None => return Err("cannot resolve '~': no home directory".to_string()),
        }
    } else {
        PathBuf::from(stripped)
    };

    if !Path::new(&expanded).is_absolute() {
        return Err("artifact location must be an absolute path".to_string());
    }

    Ok(expanded)
}

fn location_uses_file_uri(raw: &str) -> bool {
    raw.trim().starts_with("file://")
}

fn location_from_path(path: &Path, file_uri: bool) -> String {
    let path = path.display().to_string();
    if file_uri {
        format!("file://{path}")
    } else {
        path
    }
}

fn revision_kref_query_value<'a>(query: &'a str, key: &str) -> Option<&'a str> {
    query.split('&').find_map(|part| {
        let (candidate, value) = part.split_once('=')?;
        (candidate == key).then_some(value)
    })
}

fn default_artifact_path(
    workspace_dir: &Path,
    revision_kref: &str,
    artifact_name: &str,
) -> Result<PathBuf, String> {
    let rest = revision_kref
        .trim()
        .strip_prefix("kref://")
        .ok_or_else(|| "revision kref must start with kref://".to_string())?;
    let (entity, query) = rest
        .split_once('?')
        .ok_or_else(|| "revision kref must include an exact revision selector".to_string())?;
    let mut parts = entity.split('/').filter(|part| !part.is_empty());
    let project = parts
        .next()
        .ok_or_else(|| "revision kref is missing project".to_string())?;
    let mut path_parts: Vec<&str> = parts.collect();
    let item = path_parts
        .pop()
        .ok_or_else(|| "revision kref is missing item name".to_string())?;
    let revision = revision_kref_query_value(query, "r")
        .ok_or_else(|| "revision kref must include an exact r= revision selector".to_string())?;

    let mut path = workspace_dir
        .join("artifacts")
        .join("kumiho")
        .join(ascii_storage_segment(project, "project"));
    for segment in path_parts {
        path = path.join(ascii_storage_segment(segment, "space"));
    }
    path = path
        .join(ascii_storage_segment(item, "item"))
        .join(format!("r{}", ascii_storage_segment(revision, "latest")))
        .join(sanitize_upload_filename(artifact_name));
    Ok(path)
}

fn intended_resolved_path(path: &Path) -> io::Result<PathBuf> {
    if path.exists() {
        return path.canonicalize();
    }

    let mut ancestor = path;
    let mut missing: Vec<OsString> = Vec::new();
    while !ancestor.exists() {
        if let Some(name) = ancestor.file_name() {
            missing.push(name.to_os_string());
        }
        ancestor = ancestor
            .parent()
            .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "no existing path ancestor"))?;
    }

    let mut resolved = ancestor.canonicalize()?;
    for component in missing.iter().rev() {
        resolved.push(component);
    }
    Ok(resolved)
}

fn validate_artifact_local_path(
    state: &AppState,
    path: &Path,
    operation: &str,
) -> Result<(), Response> {
    let policy = {
        let config = state.config.lock();
        SecurityPolicy::from_config(&config.autonomy, &config.workspace_dir)
    };

    let raw = path.to_string_lossy();
    if !policy.is_path_allowed(&raw) {
        return Err(json_error(
            StatusCode::FORBIDDEN,
            format!(
                "artifact {operation} path is not allowed by workspace policy; move it under the workspace or an allowed root"
            ),
        ));
    }

    let resolved = intended_resolved_path(path).map_err(|e| {
        json_error(
            StatusCode::BAD_REQUEST,
            format!("failed to resolve artifact {operation} path: {e}"),
        )
    })?;
    if !policy.is_resolved_path_allowed(&resolved) {
        return Err(json_error(
            StatusCode::FORBIDDEN,
            format!("artifact {operation} path resolves outside the workspace and allowed roots"),
        ));
    }

    Ok(())
}

fn revision_location(path: &Path, revision_number: i32) -> PathBuf {
    let parent = path.parent().unwrap_or_else(|| Path::new("/"));
    let stem = path
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("artifact");
    let ext = path.extension().and_then(|s| s.to_str());
    let file_name = match ext {
        Some(ext) if !ext.is_empty() => format!("{stem}.r{revision_number}.{ext}"),
        _ => format!("{stem}.r{revision_number}"),
    };
    parent.join(file_name)
}

async fn unique_revision_location(path: &Path, revision_number: i32) -> PathBuf {
    let base = revision_location(path, revision_number);
    if tokio::fs::metadata(&base).await.is_err() {
        return base;
    }

    let parent = base.parent().unwrap_or_else(|| Path::new("/"));
    let stem = base
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("artifact");
    let ext = base.extension().and_then(|s| s.to_str());
    for i in 2..100 {
        let name = match ext {
            Some(ext) if !ext.is_empty() => format!("{stem}.{i}.{ext}"),
            _ => format!("{stem}.{i}"),
        };
        let candidate = parent.join(name);
        if tokio::fs::metadata(&candidate).await.is_err() {
            return candidate;
        }
    }
    base
}

pub async fn handle_create_project(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<CreateProjectBody>,
) -> Response {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }
    let name = match trimmed_required(&body.name, "project name") {
        Ok(name) => name,
        Err(response) => return response,
    };

    match build_kumiho_client(&state)
        .create_project(&name, body.description)
        .await
    {
        Ok(project) => {
            invalidate_proxy_cache();
            Json(serde_json::json!({ "project": project })).into_response()
        }
        Err(e) => kumiho_err(e),
    }
}

pub async fn handle_create_space(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<CreateSpaceBody>,
) -> Response {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }
    let parent_path = match trimmed_required(&body.parent_path, "parent path") {
        Ok(value) => value,
        Err(response) => return response,
    };
    let name = match trimmed_required(&body.name, "space name") {
        Ok(value) => value,
        Err(response) => return response,
    };

    match build_kumiho_client(&state)
        .create_space(&parent_path, &name)
        .await
    {
        Ok(space) => {
            invalidate_proxy_cache();
            Json(serde_json::json!({ "space": space })).into_response()
        }
        Err(e) => kumiho_err(e),
    }
}

pub async fn handle_create_item(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<CreateItemBody>,
) -> Response {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }
    let space_path = match trimmed_required(&body.space_path, "space path") {
        Ok(value) => value,
        Err(response) => return response,
    };
    let item_name = match trimmed_required(&body.item_name, "item name") {
        Ok(value) => value,
        Err(response) => return response,
    };
    let kind = match trimmed_required(&body.kind, "item kind") {
        Ok(value) => value,
        Err(response) => return response,
    };

    match build_kumiho_client(&state)
        .create_item(&space_path, &item_name, &kind, body.metadata)
        .await
    {
        Ok(item) => {
            invalidate_proxy_cache();
            Json(serde_json::json!({ "item": item })).into_response()
        }
        Err(e) => kumiho_err(e),
    }
}

pub async fn handle_create_revision(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<CreateRevisionBody>,
) -> Response {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }
    let item_kref = match trimmed_required(&body.item_kref, "item kref") {
        Ok(value) => value,
        Err(response) => return response,
    };

    match build_kumiho_client(&state)
        .create_revision(&item_kref, body.metadata)
        .await
    {
        Ok(revision) => {
            invalidate_proxy_cache();
            Json(serde_json::json!({ "revision": revision })).into_response()
        }
        Err(e) => kumiho_err(e),
    }
}

pub async fn handle_create_bundle(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<CreateBundleBody>,
) -> Response {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }
    let space_path = match trimmed_required(&body.space_path, "space path") {
        Ok(value) => value,
        Err(response) => return response,
    };
    let bundle_name = match trimmed_required(&body.bundle_name, "bundle name") {
        Ok(value) => value,
        Err(response) => return response,
    };

    match build_kumiho_client(&state)
        .create_bundle(&space_path, &bundle_name, body.metadata)
        .await
    {
        Ok(bundle) => {
            invalidate_proxy_cache();
            Json(serde_json::json!({ "bundle": bundle })).into_response()
        }
        Err(e) => kumiho_err(e),
    }
}

pub async fn handle_create_artifact(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<CreateArtifactBody>,
) -> Response {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }
    if let Some(content) = body.content.as_ref() {
        if content.len() > MAX_EDIT_BYTES {
            return json_error(
                StatusCode::PAYLOAD_TOO_LARGE,
                format!("artifact content is limited to {} bytes", MAX_EDIT_BYTES),
            );
        }
    }
    let revision_kref = match trimmed_required(&body.revision_kref, "revision kref") {
        Ok(value) => value,
        Err(response) => return response,
    };
    let name = match trimmed_required(&body.name, "artifact name") {
        Ok(value) => value,
        Err(response) => return response,
    };
    let auto_location = body.write_file && body.location.trim().is_empty();
    let location = if auto_location {
        let workspace_dir = state.config.lock().workspace_dir.clone();
        match default_artifact_path(&workspace_dir, &revision_kref, &name) {
            Ok(path) => location_from_path(&path, false),
            Err(msg) => return json_error(StatusCode::BAD_REQUEST, msg),
        }
    } else {
        match normalize_artifact_location(&body.location) {
            Ok(value) => value,
            Err(msg) => return json_error(StatusCode::BAD_REQUEST, msg),
        }
    };
    let mut metadata = body.metadata;
    if auto_location {
        metadata
            .entry("storage".to_string())
            .or_insert_with(|| "revka-workspace".to_string());
        metadata
            .entry("generated_location".to_string())
            .or_insert_with(|| "true".to_string());
    }

    if body.write_file {
        let path = match resolve_location(&location) {
            Ok(path) => path,
            Err(msg) => return json_error(StatusCode::BAD_REQUEST, msg),
        };
        if let Err(response) = validate_artifact_local_path(&state, &path, "write") {
            return response;
        }
        if tokio::fs::metadata(&path).await.is_ok() && !body.overwrite {
            return json_error(
                StatusCode::CONFLICT,
                "artifact file already exists; enable overwrite to replace it",
            );
        }
        if let Some(parent) = path.parent() {
            if let Err(e) = tokio::fs::create_dir_all(parent).await {
                return json_error(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    format!("failed to create artifact directory: {e}"),
                );
            }
        }
        let content = body.content.unwrap_or_default();
        if let Err(e) = tokio::fs::write(&path, content.as_bytes()).await {
            return json_error(
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("failed to write artifact file: {e}"),
            );
        }
    } else if body.validate_exists && !is_remote_uri(&location) {
        let path = match resolve_location(&location) {
            Ok(path) => path,
            Err(msg) => return json_error(StatusCode::BAD_REQUEST, msg),
        };
        if let Err(response) = validate_artifact_local_path(&state, &path, "link validation") {
            return response;
        }
        if tokio::fs::metadata(&path).await.is_err() {
            return json_error(StatusCode::BAD_REQUEST, "artifact file does not exist");
        }
    }

    match build_kumiho_client(&state)
        .create_artifact(&revision_kref, &name, &location, metadata)
        .await
    {
        Ok(artifact) => {
            invalidate_proxy_cache();
            Json(serde_json::json!({ "artifact": artifact })).into_response()
        }
        Err(e) => kumiho_err(e),
    }
}

pub async fn handle_create_edge(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<CreateEdgeBody>,
) -> Response {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }
    let source_kref = match trimmed_required(&body.source_kref, "source revision kref") {
        Ok(value) => value,
        Err(response) => return response,
    };
    let target_kref = match trimmed_required(&body.target_kref, "target revision kref") {
        Ok(value) => value,
        Err(response) => return response,
    };
    let edge_type = match trimmed_required(&body.edge_type, "edge type") {
        Ok(value) => value,
        Err(response) => return response,
    };

    match build_kumiho_client(&state)
        .create_edge(&source_kref, &target_kref, &edge_type, body.metadata)
        .await
    {
        Ok(edge) => {
            invalidate_proxy_cache();
            Json(serde_json::json!({ "edge": edge })).into_response()
        }
        Err(e) => kumiho_err(e),
    }
}

pub async fn handle_deprecate_item(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<DeprecateItemBody>,
) -> Response {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    match build_kumiho_client(&state)
        .deprecate_item(&body.kref, body.deprecated)
        .await
    {
        Ok(item) => {
            invalidate_proxy_cache();
            Json(serde_json::json!({ "item": item })).into_response()
        }
        Err(e) => kumiho_err(e),
    }
}

pub async fn handle_deprecate_revision(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<DeprecateRevisionBody>,
) -> Response {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    match build_kumiho_client(&state)
        .deprecate_revision(&body.kref, body.deprecated)
        .await
    {
        Ok(revision) => {
            invalidate_proxy_cache();
            Json(serde_json::json!({ "revision": revision })).into_response()
        }
        Err(e) => kumiho_err(e),
    }
}

pub async fn handle_deprecate_artifact(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<DeprecateArtifactBody>,
) -> Response {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    match build_kumiho_client(&state)
        .deprecate_artifact(&body.kref, body.deprecated)
        .await
    {
        Ok(artifact) => {
            invalidate_proxy_cache();
            Json(serde_json::json!({ "artifact": artifact })).into_response()
        }
        Err(e) => kumiho_err(e),
    }
}

pub async fn handle_publish_revision(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<PublishRevisionBody>,
) -> Response {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let client = build_kumiho_client(&state);
    if let Err(e) = client.tag_revision(&body.kref, "published").await {
        return kumiho_err(e);
    }

    match client.get_revision(&body.kref).await {
        Ok(revision) => {
            invalidate_proxy_cache();
            Json(serde_json::json!({ "revision": revision })).into_response()
        }
        Err(e) => kumiho_err(e),
    }
}

pub async fn handle_tag_revision(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<RevisionTagBody>,
) -> Response {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }
    let kref = match trimmed_required(&body.kref, "revision kref") {
        Ok(value) => value,
        Err(response) => return response,
    };
    let tag = match trimmed_required(&body.tag, "tag") {
        Ok(value) => value,
        Err(response) => return response,
    };
    if tag == "current" {
        return json_error(
            StatusCode::FORBIDDEN,
            "moving the current tag is high-risk; use kumiho_patch_apply",
        );
    }

    let client = build_kumiho_client(&state);
    if let Err(e) = client.tag_revision(&kref, &tag).await {
        return kumiho_err(e);
    }

    match client.get_revision(&kref).await {
        Ok(revision) => {
            invalidate_proxy_cache();
            Json(serde_json::json!({ "revision": revision })).into_response()
        }
        Err(e) => kumiho_err(e),
    }
}

pub async fn handle_untag_revision(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<RevisionTagBody>,
) -> Response {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }
    let kref = match trimmed_required(&body.kref, "revision kref") {
        Ok(value) => value,
        Err(response) => return response,
    };
    let tag = match trimmed_required(&body.tag, "tag") {
        Ok(value) => value,
        Err(response) => return response,
    };
    if tag == "current" {
        return json_error(
            StatusCode::FORBIDDEN,
            "removing the current tag is high-risk; use kumiho_patch_apply",
        );
    }

    let client = build_kumiho_client(&state);
    if let Err(e) = client.untag_revision(&kref, &tag).await {
        return kumiho_err(e);
    }

    match client.get_revision(&kref).await {
        Ok(revision) => {
            invalidate_proxy_cache();
            Json(serde_json::json!({ "revision": revision })).into_response()
        }
        Err(e) => kumiho_err(e),
    }
}

pub async fn handle_list_bundles(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(query): Query<AssetBundlesQuery>,
) -> Response {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }
    let project = match trimmed_required(&query.project, "project") {
        Ok(value) => value,
        Err(response) => return response,
    };

    let client = build_kumiho_client(&state);
    let root = format!("/{project}");
    let paths = if let Some(space_path) = query.space_path.as_deref() {
        let space_path = space_path.trim();
        if space_path != root && !space_path.starts_with(&format!("{root}/")) {
            return json_error(
                StatusCode::BAD_REQUEST,
                "space_path must belong to the selected project",
            );
        }
        vec![space_path.to_string()]
    } else {
        let spaces = match client.list_spaces(&root, true).await {
            Ok(spaces) => spaces,
            Err(e) => return kumiho_err(e),
        };

        let mut paths = vec![root];
        paths.extend(spaces.into_iter().map(|space| space.path));
        paths.sort();
        paths.dedup();
        paths
    };

    let mut bundles = Vec::new();
    for path in paths {
        match client.list_items(&path, false).await {
            Ok(items) => bundles.extend(
                items
                    .into_iter()
                    .filter(|item| item.kind == "bundle" || item.kref.ends_with(".bundle")),
            ),
            Err(err) => {
                tracing::warn!(space_path = path, error = ?err, "failed to list bundle candidates");
            }
        }
    }
    bundles.sort_by(|a, b| a.name.cmp(&b.name).then_with(|| a.kref.cmp(&b.kref)));
    Json(serde_json::json!({ "bundles": bundles })).into_response()
}

pub async fn handle_list_bundle_members(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(query): Query<BundleMembersQuery>,
) -> Response {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }
    let bundle_kref = match trimmed_required(&query.bundle_kref, "bundle kref") {
        Ok(value) => value,
        Err(response) => return response,
    };

    let client = build_kumiho_client(&state);
    let members = match client.list_bundle_members(&bundle_kref).await {
        Ok(members) => members,
        Err(e) => return kumiho_err(e),
    };

    let mut details = Vec::with_capacity(members.members.len());
    for membership in members.members {
        let item = client.get_item_by_kref(&membership.item_kref).await;
        let (item, item_error) = match item {
            Ok(item) => (Some(item), None),
            Err(e) => (None, Some(e.to_string())),
        };
        let latest_revision = match item.as_ref() {
            Some(item) => client.get_latest_revision(&item.kref).await.ok(),
            None => None,
        };
        let current_revision = match item.as_ref() {
            Some(item) => client.get_revision_by_tag(&item.kref, "current").await.ok(),
            None => None,
        };

        details.push(BundleMemberDetail {
            membership,
            item,
            latest_revision,
            current_revision,
            error: item_error,
        });
    }

    Json(BundleMembersDetailResponse {
        members: details,
        total_count: members.total_count,
    })
    .into_response()
}

pub async fn handle_add_bundle_member(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<BundleMemberBody>,
) -> Response {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }
    let bundle_kref = match trimmed_required(&body.bundle_kref, "bundle kref") {
        Ok(value) => value,
        Err(response) => return response,
    };
    let item_kref = match trimmed_required(&body.item_kref, "item kref") {
        Ok(value) => value,
        Err(response) => return response,
    };
    if is_protected_bundle(&bundle_kref) && !body.allow_protected {
        return json_error(
            StatusCode::FORBIDDEN,
            "protected canon bundles require explicit confirmation",
        );
    }

    match build_kumiho_client(&state)
        .add_bundle_member(&bundle_kref, &item_kref, body.metadata)
        .await
    {
        Ok(result) => {
            invalidate_proxy_cache();
            Json(serde_json::json!({ "result": result })).into_response()
        }
        Err(e) => kumiho_err(e),
    }
}

pub async fn handle_remove_bundle_member(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<BundleMemberBody>,
) -> Response {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }
    let bundle_kref = match trimmed_required(&body.bundle_kref, "bundle kref") {
        Ok(value) => value,
        Err(response) => return response,
    };
    let item_kref = match trimmed_required(&body.item_kref, "item kref") {
        Ok(value) => value,
        Err(response) => return response,
    };
    if is_protected_bundle(&bundle_kref) && !body.allow_protected {
        return json_error(
            StatusCode::FORBIDDEN,
            "protected canon bundles require explicit confirmation",
        );
    }

    match build_kumiho_client(&state)
        .remove_bundle_member(&bundle_kref, &item_kref)
        .await
    {
        Ok(result) => {
            invalidate_proxy_cache();
            Json(serde_json::json!({ "result": result })).into_response()
        }
        Err(e) => kumiho_err(e),
    }
}

async fn build_graph_node(
    client: &KumihoClient,
    kref: &str,
    all_edges: &[EdgeResponse],
) -> AssetGraphNode {
    let incoming_edges = all_edges
        .iter()
        .filter(|edge| edge.target_kref == kref)
        .cloned()
        .collect::<Vec<_>>();
    let outgoing_edges = all_edges
        .iter()
        .filter(|edge| edge.source_kref == kref)
        .cloned()
        .collect::<Vec<_>>();

    match client.get_revision(kref).await {
        Ok(revision) => {
            let item = client.get_item_by_kref(&revision.item_kref).await.ok();
            let artifacts = client
                .get_artifacts(&revision.kref)
                .await
                .unwrap_or_default();
            AssetGraphNode {
                kref: revision.kref,
                item_kref: Some(revision.item_kref),
                item_name: item.as_ref().map(|item| item.item_name.clone()),
                kind: item.as_ref().map(|item| item.kind.clone()),
                revision_number: Some(revision.number),
                tags: revision.tags,
                metadata: revision.metadata,
                artifacts,
                incoming_edges,
                outgoing_edges,
                created_at: revision.created_at,
                missing: false,
            }
        }
        Err(_) => AssetGraphNode {
            kref: kref.to_string(),
            item_kref: None,
            item_name: None,
            kind: None,
            revision_number: None,
            tags: Vec::new(),
            metadata: HashMap::new(),
            artifacts: Vec::new(),
            incoming_edges,
            outgoing_edges,
            created_at: None,
            missing: true,
        },
    }
}

pub async fn handle_dependency_graph(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(query): Query<DependencyGraphQuery>,
) -> Response {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let center_kref = match trimmed_required(&query.revision_kref, "revision kref") {
        Ok(value) => value,
        Err(response) => return response,
    };
    let direction = normalize_graph_direction(query.direction.as_deref());
    let depth = query.depth.unwrap_or(1).clamp(1, MAX_GRAPH_DEPTH);
    let node_limit = query
        .node_limit
        .unwrap_or(DEFAULT_GRAPH_NODE_LIMIT)
        .clamp(1, MAX_GRAPH_NODE_LIMIT);
    let edge_type = query
        .edge_type
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty() && *value != "all")
        .map(str::to_string);

    let client = build_kumiho_client(&state);
    let mut visited = HashSet::new();
    let mut frontier = VecDeque::new();
    let mut ordered_nodes = Vec::new();
    let mut edge_map: BTreeMap<String, EdgeResponse> = BTreeMap::new();
    let mut truncated = false;

    visited.insert(center_kref.clone());
    ordered_nodes.push(center_kref.clone());
    frontier.push_back((center_kref.clone(), 0u8));

    while let Some((kref, current_depth)) = frontier.pop_front() {
        if current_depth >= depth {
            continue;
        }
        let fetched_edges = match client
            .list_edges(&kref, edge_type.as_deref(), Some(&direction))
            .await
        {
            Ok(edges) => edges,
            Err(e) => return kumiho_err(e),
        };

        for edge in fetched_edges {
            edge_map
                .entry(edge_key(&edge))
                .or_insert_with(|| edge.clone());
            for neighbor in edge_neighbors(&edge, &kref, &direction) {
                if visited.contains(&neighbor) {
                    continue;
                }
                if ordered_nodes.len() >= node_limit {
                    truncated = true;
                    continue;
                }
                visited.insert(neighbor.clone());
                ordered_nodes.push(neighbor.clone());
                frontier.push_back((neighbor, current_depth + 1));
            }
        }
    }

    let edges = edge_map.into_values().collect::<Vec<_>>();
    let mut nodes = Vec::with_capacity(ordered_nodes.len());
    for node_kref in ordered_nodes {
        nodes.push(build_graph_node(&client, &node_kref, &edges).await);
    }

    Json(AssetDependencyGraphResponse {
        center_kref,
        direction,
        depth,
        edge_type,
        node_limit,
        truncated,
        nodes,
        edges,
    })
    .into_response()
}

pub async fn handle_update_artifact_content(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<UpdateArtifactContentBody>,
) -> Response {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    if body.content.len() > MAX_EDIT_BYTES {
        return json_error(
            StatusCode::PAYLOAD_TOO_LARGE,
            format!("artifact edits are limited to {} bytes", MAX_EDIT_BYTES),
        );
    }

    let client = build_kumiho_client(&state);
    let source_revision = match client.get_revision(&body.revision_kref).await {
        Ok(revision) => revision,
        Err(e) => return kumiho_err(e),
    };
    let source_artifact = match client.get_artifact(&body.artifact_kref).await {
        Ok(artifact) => artifact,
        Err(e) => return kumiho_err(e),
    };

    if source_artifact.revision_kref != source_revision.kref {
        return json_error(
            StatusCode::BAD_REQUEST,
            "artifact does not belong to the selected revision",
        );
    }

    let source_path = match resolve_location(&source_artifact.location) {
        Ok(path) => path,
        Err(msg) => return json_error(StatusCode::BAD_REQUEST, msg),
    };
    let file_uri = location_uses_file_uri(&source_artifact.location);

    if !is_published(&source_revision) {
        if let Err(e) = tokio::fs::write(&source_path, body.content.as_bytes()).await {
            return json_error(
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("failed to write artifact content: {e}"),
            );
        }
        invalidate_proxy_cache();
        return Json(UpdateArtifactContentResponse {
            revision: source_revision,
            artifact: source_artifact,
            created_revision: false,
            copied_artifacts: 0,
        })
        .into_response();
    }

    let mut metadata = source_revision.metadata.clone();
    metadata.insert(
        "edited_from_revision".to_string(),
        source_revision.kref.clone(),
    );
    metadata.insert("edited_artifact".to_string(), source_artifact.name.clone());
    metadata.insert("edited_by".to_string(), "revka-asset-browser".to_string());

    let new_revision = match client
        .create_revision(&source_revision.item_kref, metadata)
        .await
    {
        Ok(revision) => revision,
        Err(e) => return kumiho_err(e),
    };

    let new_path = unique_revision_location(&source_path, new_revision.number).await;
    if let Some(parent) = new_path.parent() {
        if let Err(e) = tokio::fs::create_dir_all(parent).await {
            return json_error(
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("failed to create artifact directory: {e}"),
            );
        }
    }
    if let Err(e) = tokio::fs::write(&new_path, body.content.as_bytes()).await {
        return json_error(
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("failed to write new artifact content: {e}"),
        );
    }

    let source_artifacts = match client.get_artifacts(&source_revision.kref).await {
        Ok(artifacts) => artifacts,
        Err(e) => return kumiho_err(e),
    };

    let mut edited_artifact: Option<ArtifactResponse> = None;
    let mut copied = 0usize;
    for artifact in source_artifacts {
        let (location, metadata) = if artifact.kref == source_artifact.kref {
            (location_from_path(&new_path, file_uri), {
                let mut meta = artifact.metadata.clone();
                meta.insert("edited_from_artifact".to_string(), artifact.kref.clone());
                meta
            })
        } else {
            (artifact.location.clone(), artifact.metadata.clone())
        };

        match client
            .create_artifact(&new_revision.kref, &artifact.name, &location, metadata)
            .await
        {
            Ok(new_artifact) => {
                copied += 1;
                if artifact.kref == source_artifact.kref {
                    edited_artifact = Some(new_artifact);
                }
            }
            Err(e) => return kumiho_err(e),
        }
    }

    let artifact = match edited_artifact {
        Some(artifact) => artifact,
        None => {
            let mut metadata = source_artifact.metadata.clone();
            metadata.insert(
                "edited_from_artifact".to_string(),
                source_artifact.kref.clone(),
            );
            match client
                .create_artifact(
                    &new_revision.kref,
                    &source_artifact.name,
                    &location_from_path(&new_path, file_uri),
                    metadata,
                )
                .await
            {
                Ok(artifact) => artifact,
                Err(e) => return kumiho_err(e),
            }
        }
    };

    invalidate_proxy_cache();
    Json(UpdateArtifactContentResponse {
        revision: new_revision,
        artifact,
        created_revision: true,
        copied_artifacts: copied,
    })
    .into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn revision_location_inserts_revision_before_extension() {
        let path = Path::new("/tmp/report.md");
        assert_eq!(
            revision_location(path, 7),
            PathBuf::from("/tmp/report.r7.md")
        );
    }

    #[test]
    fn revision_location_handles_extensionless_files() {
        let path = Path::new("/tmp/notes");
        assert_eq!(revision_location(path, 3), PathBuf::from("/tmp/notes.r3"));
    }

    #[test]
    fn protected_bundle_detection_uses_bundle_item_name() {
        assert!(is_protected_bundle(
            "kref://StoryProject/Bundles/series-main-canon.bundle"
        ));
        assert!(is_protected_bundle(
            "kref://StoryProject/Bundles/main-canon.bundle"
        ));
        assert!(!is_protected_bundle(
            "kref://StoryProject/Bundles/series-context-packs.bundle"
        ));
    }

    #[test]
    fn graph_direction_aliases_match_kumiho_edge_queries() {
        assert_eq!(normalize_graph_direction(Some("upstream")), "outgoing");
        assert_eq!(normalize_graph_direction(Some("dependents")), "incoming");
        assert_eq!(normalize_graph_direction(Some("both")), "both");
        assert_eq!(normalize_graph_direction(None), "both");
    }

    #[test]
    fn default_artifact_path_uses_revka_workspace_layout() {
        let workspace = Path::new("C:/Users/example/.revka/workspace");
        let path = default_artifact_path(
            workspace,
            "kref://StoryProject/Characters/protagonist.character-state?r=12",
            "STATE.md",
        )
        .expect("default artifact path");
        assert_eq!(
            path,
            workspace
                .join("artifacts")
                .join("kumiho")
                .join("storyproject")
                .join("characters")
                .join("protagonist-character-state")
                .join("r12")
                .join("STATE.md")
        );
    }

    #[test]
    fn default_artifact_path_requires_exact_revision_selector() {
        let workspace = Path::new("C:/Users/example/.revka/workspace");
        let err = default_artifact_path(
            workspace,
            "kref://StoryProject/Characters/protagonist.character-state?t=current",
            "STATE.md",
        )
        .expect_err("tag selector should not be enough for file storage");
        assert!(err.contains("exact r= revision selector"));
    }

    #[test]
    fn intended_resolved_path_keeps_missing_tail_under_existing_parent() {
        let temp = tempfile::tempdir().expect("tempdir");
        let target = temp.path().join("nested").join("artifact.md");
        let resolved = intended_resolved_path(&target).expect("resolve intended path");
        assert_eq!(
            resolved,
            temp.path()
                .canonicalize()
                .expect("canonical tempdir")
                .join("nested")
                .join("artifact.md")
        );
    }
}
