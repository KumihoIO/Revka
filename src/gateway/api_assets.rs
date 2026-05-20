//! Mutating Asset Browser API routes.
//!
//! The generic `/api/kumiho/*` proxy is GET-only by design. These routes keep
//! Asset Browser writes typed and idempotency-aware while still delegating the
//! persistent model to Kumiho.

use super::AppState;
use super::api::require_auth;
use super::api_agents::build_kumiho_client;
use super::api_kumiho_proxy::invalidate_proxy_cache;
use super::kumiho_client::{
    ArtifactResponse, KumihoError, RevisionResponse, kumiho_error_to_response,
};
use axum::{
    Json,
    extract::State,
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

const MAX_EDIT_BYTES: usize = 1024 * 1024;

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

#[derive(Serialize)]
pub struct UpdateArtifactContentResponse {
    pub revision: RevisionResponse,
    pub artifact: ArtifactResponse,
    pub created_revision: bool,
    pub copied_artifacts: usize,
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

fn is_published(revision: &RevisionResponse) -> bool {
    revision.tags.iter().any(|tag| tag == "published")
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
    metadata.insert(
        "edited_by".to_string(),
        "construct-asset-browser".to_string(),
    );

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
}
