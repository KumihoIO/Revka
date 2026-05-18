//! Serves the raw bytes of a Kumiho artifact's underlying local file.
//!
//! The `/api/artifact-body?location=<path>` endpoint reads a file from the
//! local filesystem (the artifact's `location` as stored in Kumiho) and
//! streams it back with a best-effort Content-Type. Required so the web
//! Asset Browser and Workflow Runs viewers can render text / images /
//! video artifacts without each viewer re-implementing file IO.

use super::AppState;
use super::api::require_auth;
use super::api_agents::build_kumiho_client;
use super::kumiho_client::{ArtifactResponse, RevisionResponse};
use axum::{
    Json,
    extract::{Query, State},
    http::{HeaderMap, HeaderName, HeaderValue, StatusCode, header},
    response::{IntoResponse, Response},
};
use base64::Engine as _;
use serde::Deserialize;
use std::collections::HashMap;
use std::path::{Path, PathBuf};

const MAX_ARTIFACT_BYTES: u64 = 256 * 1024 * 1024; // 256 MiB

#[derive(Deserialize)]
pub struct ArtifactBodyQuery {
    pub location: String,
}

struct ArtifactBodyBytes {
    bytes: Vec<u8>,
    filename: String,
    mime: String,
}

pub async fn handle_artifact_body(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(q): Query<ArtifactBodyQuery>,
) -> Response {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let path = match resolve_location(&q.location) {
        Ok(p) => p,
        Err(msg) => {
            if let Some(fallback) = fallback_from_revision_metadata(&state, &q.location).await {
                return metadata_fallback_response(fallback);
            }
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({ "error": msg })),
            )
                .into_response();
        }
    };

    let meta = match tokio::fs::metadata(&path).await {
        Ok(m) => m,
        Err(e) => {
            if let Some(fallback) = fallback_from_revision_metadata(&state, &q.location).await {
                return metadata_fallback_response(fallback);
            }
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({
                    "error": format!("artifact not found on disk: {e}"),
                    "detail": "No local file was found, and no reconstructable artifact body was found in Kumiho revision metadata.",
                    "path": path.display().to_string(),
                })),
            )
                .into_response();
        }
    };

    if !meta.is_file() {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "error": "artifact location is not a regular file",
                "path": path.display().to_string(),
            })),
        )
            .into_response();
    }

    if meta.len() > MAX_ARTIFACT_BYTES {
        return (
            StatusCode::PAYLOAD_TOO_LARGE,
            Json(serde_json::json!({
                "error": format!(
                    "artifact exceeds {} MiB preview limit",
                    MAX_ARTIFACT_BYTES / (1024 * 1024)
                ),
                "size": meta.len(),
            })),
        )
            .into_response();
    }

    let bytes = match tokio::fs::read(&path).await {
        Ok(b) => b,
        Err(e) => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({ "error": format!("read failed: {e}") })),
            )
                .into_response();
        }
    };

    let mime = mime_guess::from_path(&path)
        .first_or_octet_stream()
        .to_string();

    let filename = path
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("artifact");

    bytes_response(ArtifactBodyBytes {
        bytes,
        filename: filename.to_string(),
        mime,
    })
}

fn resolve_location(raw: &str) -> Result<PathBuf, String> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return Err("location is empty".to_string());
    }

    let stripped = trimmed
        .strip_prefix("file://")
        .unwrap_or(trimmed)
        .to_string();

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
        return Err("location must be an absolute path".to_string());
    }

    Ok(expanded)
}

async fn fallback_from_revision_metadata(
    state: &AppState,
    raw_location: &str,
) -> Option<ArtifactBodyBytes> {
    let client = build_kumiho_client(state);
    let artifacts = match client.get_artifacts_by_location(raw_location).await {
        Ok(artifacts) => artifacts,
        Err(e) => {
            tracing::debug!(
                error = %e,
                "artifact-body metadata fallback lookup failed"
            );
            return None;
        }
    };

    for artifact in artifacts {
        if artifact.deprecated {
            continue;
        }
        let revision = match client.get_revision(&artifact.revision_kref).await {
            Ok(revision) => revision,
            Err(e) => {
                tracing::debug!(
                    artifact_kref = %artifact.kref,
                    revision_kref = %artifact.revision_kref,
                    error = %e,
                    "artifact-body metadata fallback revision lookup failed"
                );
                continue;
            }
        };
        if let Some(body) = body_from_revision_metadata(&artifact, &revision) {
            return Some(body);
        }
    }

    None
}

fn body_from_revision_metadata(
    artifact: &ArtifactResponse,
    revision: &RevisionResponse,
) -> Option<ArtifactBodyBytes> {
    let filename = artifact_filename(artifact);
    let mime = mime_guess::from_path(&filename)
        .first_or_octet_stream()
        .to_string();

    if let Some(bytes) = decode_base64_payload(&artifact.metadata)
        .or_else(|| decode_base64_payload(&revision.metadata))
    {
        return Some(ArtifactBodyBytes {
            bytes,
            filename,
            mime,
        });
    }

    let text = text_payload_from_metadata(artifact, revision)?;
    Some(ArtifactBodyBytes {
        bytes: text.into_bytes(),
        filename,
        mime,
    })
}

fn text_payload_from_metadata(
    artifact: &ArtifactResponse,
    revision: &RevisionResponse,
) -> Option<String> {
    if is_workflow_artifact(artifact, revision) {
        return metadata_value(
            &revision.metadata,
            &["definition", "workflow_yaml", "content"],
        );
    }

    if !is_text_like_artifact(artifact) {
        return None;
    }

    metadata_value(
        &artifact.metadata,
        &["content", "body", "text", "markdown", "procedure"],
    )
    .or_else(|| {
        metadata_value(
            &revision.metadata,
            &[
                "content",
                "body",
                "text",
                "markdown",
                "procedure",
                "definition",
            ],
        )
    })
}

fn metadata_value(metadata: &HashMap<String, String>, keys: &[&str]) -> Option<String> {
    keys.iter()
        .filter_map(|key| metadata.get(*key))
        .find(|value| !value.is_empty())
        .cloned()
}

fn decode_base64_payload(metadata: &HashMap<String, String>) -> Option<Vec<u8>> {
    let encoded = metadata_value(
        metadata,
        &[
            "content_base64",
            "data_base64",
            "payload_base64",
            "body_base64",
        ],
    )?;
    base64::engine::general_purpose::STANDARD
        .decode(encoded.as_bytes())
        .ok()
        .or_else(|| {
            base64::engine::general_purpose::STANDARD_NO_PAD
                .decode(encoded.as_bytes())
                .ok()
        })
}

fn is_workflow_artifact(artifact: &ArtifactResponse, revision: &RevisionResponse) -> bool {
    revision.item_kref.ends_with(".workflow")
        || artifact.name.eq_ignore_ascii_case("workflow.yaml")
        || path_ext_is(&artifact.location, &["yaml", "yml"])
}

fn is_text_like_artifact(artifact: &ArtifactResponse) -> bool {
    let ext = extension_of(&artifact.location)
        .or_else(|| extension_of(&artifact.name))
        .unwrap_or_default();
    matches!(
        ext.as_str(),
        "txt"
            | "log"
            | "md"
            | "markdown"
            | "mdx"
            | "json"
            | "yaml"
            | "yml"
            | "toml"
            | "ini"
            | "csv"
            | "tsv"
            | "xml"
            | "html"
            | "css"
            | "js"
            | "ts"
            | "tsx"
            | "jsx"
            | "py"
            | "rs"
            | "go"
            | "sh"
            | "bash"
            | "zsh"
            | "sql"
            | "env"
    ) || artifact.name.eq_ignore_ascii_case("chat_io")
}

fn path_ext_is(path: &str, exts: &[&str]) -> bool {
    extension_of(path).is_some_and(|ext| exts.iter().any(|candidate| ext == *candidate))
}

fn extension_of(path: &str) -> Option<String> {
    let clean = path
        .split_once('?')
        .map_or(path, |(before, _)| before)
        .split_once('#')
        .map_or(path, |(before, _)| before);
    clean
        .rsplit_once('.')
        .map(|(_, ext)| ext.to_ascii_lowercase())
        .filter(|ext| !ext.is_empty())
}

fn artifact_filename(artifact: &ArtifactResponse) -> String {
    if !artifact.name.trim().is_empty() {
        return artifact.name.clone();
    }
    Path::new(&artifact.location)
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("artifact")
        .to_string()
}

fn bytes_response(body: ArtifactBodyBytes) -> Response {
    let mut resp = (StatusCode::OK, body.bytes).into_response();
    let headers = resp.headers_mut();
    headers.insert(
        header::CONTENT_TYPE,
        HeaderValue::from_str(&body.mime)
            .unwrap_or_else(|_| HeaderValue::from_static("application/octet-stream")),
    );
    headers.insert(
        header::CACHE_CONTROL,
        HeaderValue::from_static("private, max-age=60"),
    );
    let filename = body.filename.replace(['"', '\\', '\r', '\n'], "_");
    if let Ok(value) = HeaderValue::from_str(&format!("inline; filename=\"{filename}\"")) {
        headers.insert(header::CONTENT_DISPOSITION, value);
    }
    resp
}

fn metadata_fallback_response(body: ArtifactBodyBytes) -> Response {
    let mut resp = bytes_response(body);
    resp.headers_mut().insert(
        HeaderName::from_static("x-construct-artifact-source"),
        HeaderValue::from_static("revision-metadata"),
    );
    resp
}

#[cfg(test)]
mod tests {
    use super::*;

    fn artifact(name: &str, location: &str) -> ArtifactResponse {
        ArtifactResponse {
            kref: "kref://Construct/Workflows/demo.workflow?r=2&a=workflow.yaml".to_string(),
            name: name.to_string(),
            location: location.to_string(),
            revision_kref: "kref://Construct/Workflows/demo.workflow?r=2".to_string(),
            item_kref: Some("kref://Construct/Workflows/demo.workflow".to_string()),
            deprecated: false,
            created_at: None,
            author: None,
            username: None,
            author_display: None,
            metadata: HashMap::new(),
        }
    }

    fn revision(item_kref: &str, metadata: HashMap<String, String>) -> RevisionResponse {
        RevisionResponse {
            kref: format!("{item_kref}?r=2"),
            item_kref: item_kref.to_string(),
            number: 2,
            latest: true,
            tags: vec!["latest".to_string()],
            metadata,
            deprecated: false,
            created_at: None,
            author: None,
            username: None,
            author_display: None,
        }
    }

    #[test]
    fn metadata_fallback_serves_workflow_definition() {
        let artifact = artifact(
            "workflow.yaml",
            "file:///Users/neo/.construct/workflows/demo.r2.yaml",
        );
        let mut metadata = HashMap::new();
        metadata.insert(
            "definition".to_string(),
            "name: demo\nsteps: []\n".to_string(),
        );
        let revision = revision("kref://Construct/Workflows/demo.workflow", metadata);

        let body = body_from_revision_metadata(&artifact, &revision).unwrap();

        assert_eq!(body.filename, "workflow.yaml");
        assert!(body.mime.contains("yaml"));
        assert_eq!(body.bytes, b"name: demo\nsteps: []\n");
    }

    #[test]
    fn metadata_fallback_does_not_treat_image_prompt_as_body() {
        let artifact = artifact(
            "image.png",
            "C:\\Users\\isake\\.construct\\workspace\\Construct\\Images\\demo\\r1\\image.png",
        );
        let mut metadata = HashMap::new();
        metadata.insert("prompt".to_string(), "a red apple".to_string());
        let revision = revision("kref://Construct/Images/demo.image", metadata);

        assert!(body_from_revision_metadata(&artifact, &revision).is_none());
    }

    #[test]
    fn metadata_fallback_decodes_base64_payload() {
        let mut artifact = artifact("image.png", "C:\\tmp\\image.png");
        artifact
            .metadata
            .insert("content_base64".to_string(), "iVBORw0KGgo=".to_string());
        let revision = revision("kref://Construct/Images/demo.image", HashMap::new());

        let body = body_from_revision_metadata(&artifact, &revision).unwrap();

        assert_eq!(body.mime, "image/png");
        assert_eq!(body.bytes, b"\x89PNG\r\n\x1a\n");
    }
}
