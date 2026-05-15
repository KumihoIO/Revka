//! Static file serving for the web dashboard.
//!
//! The gateway can serve a filesystem dashboard build during development or
//! deployment, then fall back to the embedded `web/dist/` bundle included in
//! release binaries.

use std::path::{Component, Path, PathBuf};

use axum::{
    extract::State,
    http::{StatusCode, Uri, header},
    response::{IntoResponse, Response},
};
use rust_embed::Embed;

use super::AppState;

#[derive(Embed)]
#[folder = "web/dist/"]
struct WebAssets;

enum DashboardSource {
    Filesystem(PathBuf),
    Embedded,
    Unavailable,
}

fn configured_web_root(state: &AppState) -> Option<PathBuf> {
    std::env::var("CONSTRUCT_WEB_ROOT")
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .or_else(|| {
            state
                .config
                .lock()
                .gateway
                .web_root
                .as_deref()
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .map(ToString::to_string)
        })
        .map(PathBuf::from)
}

fn dashboard_source(state: &AppState) -> DashboardSource {
    if let Some(root) = configured_web_root(state) {
        return match root.canonicalize() {
            Ok(canonical) if canonical.is_dir() => DashboardSource::Filesystem(canonical),
            _ => DashboardSource::Unavailable,
        };
    }

    if WebAssets::get("index.html").is_some() {
        DashboardSource::Embedded
    } else {
        DashboardSource::Unavailable
    }
}

fn requested_app_path(uri: &Uri) -> String {
    let path = uri
        .path()
        .strip_prefix("/_app/")
        .unwrap_or(uri.path())
        .trim_start_matches('/');

    if path.is_empty() {
        "index.html".to_string()
    } else {
        path.to_string()
    }
}

fn validate_relative_path(path: &str) -> Result<(), &'static str> {
    let rel = Path::new(path);
    if rel.is_absolute() {
        return Err("absolute path not allowed");
    }
    if path.contains('\\') {
        return Err("backslash path separators are not allowed");
    }
    for component in rel.components() {
        match component {
            Component::Normal(_) => {}
            Component::CurDir => {}
            Component::ParentDir => return Err("parent traversal not allowed"),
            Component::Prefix(_) | Component::RootDir => return Err("absolute path not allowed"),
        }
    }
    Ok(())
}

fn resolve_filesystem_path(root: &Path, rel: &str) -> Result<PathBuf, &'static str> {
    validate_relative_path(rel)?;
    let candidate = root.join(rel);
    let canonical = candidate.canonicalize().map_err(|_| "not found")?;
    if !canonical.starts_with(root) {
        return Err("path escapes web root");
    }
    if !canonical.is_file() {
        return Err("not a file");
    }
    Ok(canonical)
}

/// Serve static files from `/_app/*`.
pub async fn handle_static(State(state): State<AppState>, uri: Uri) -> Response {
    let path = requested_app_path(&uri);

    match dashboard_source(&state) {
        DashboardSource::Filesystem(root) => serve_filesystem_file(&root, &path).await,
        DashboardSource::Embedded => serve_embedded_file(&path),
        DashboardSource::Unavailable => dashboard_unavailable_response(),
    }
}

/// SPA fallback: serve index.html for any non-API, non-static GET request.
/// Injects `window.__CONSTRUCT_BASE__` so the frontend knows the path prefix.
pub async fn handle_spa_fallback(State(state): State<AppState>) -> Response {
    match dashboard_source(&state) {
        DashboardSource::Filesystem(root) => {
            let index_path = match resolve_filesystem_path(&root, "index.html") {
                Ok(path) => path,
                Err(_) => return dashboard_unavailable_response(),
            };
            match tokio::fs::read(index_path).await {
                Ok(bytes) => serve_index_html(&state, &bytes),
                Err(_) => dashboard_unavailable_response(),
            }
        }
        DashboardSource::Embedded => match WebAssets::get("index.html") {
            Some(content) => serve_index_html(&state, &content.data),
            None => dashboard_unavailable_response(),
        },
        DashboardSource::Unavailable => dashboard_unavailable_response(),
    }
}

async fn serve_filesystem_file(root: &Path, path: &str) -> Response {
    let resolved = match resolve_filesystem_path(root, path) {
        Ok(path) => path,
        Err("not found") => return (StatusCode::NOT_FOUND, "Not found").into_response(),
        Err("not a file") => return (StatusCode::NOT_FOUND, "Not found").into_response(),
        Err(e) => return (StatusCode::BAD_REQUEST, format!("Invalid path: {e}")).into_response(),
    };

    match tokio::fs::read(&resolved).await {
        Ok(bytes) => serve_bytes(path, bytes),
        Err(_) => (StatusCode::NOT_FOUND, "Not found").into_response(),
    }
}

fn serve_embedded_file(path: &str) -> Response {
    match WebAssets::get(path) {
        Some(content) => serve_bytes(path, content.data.to_vec()),
        None => (StatusCode::NOT_FOUND, "Not found").into_response(),
    }
}

fn serve_index_html(state: &AppState, bytes: &[u8]) -> Response {
    let html = String::from_utf8_lossy(bytes);

    let html = if state.path_prefix.is_empty() {
        html.into_owned()
    } else {
        let pfx = &state.path_prefix;
        let json_pfx = serde_json::to_string(pfx).unwrap_or_else(|_| "\"\"".to_string());
        let script = format!("<script>window.__CONSTRUCT_BASE__={json_pfx};</script>");
        html.replace("/_app/", &format!("{pfx}/_app/"))
            .replace("<head>", &format!("<head>{script}"))
    };

    (
        StatusCode::OK,
        [
            (header::CONTENT_TYPE, "text/html; charset=utf-8".to_string()),
            (header::CACHE_CONTROL, "no-cache".to_string()),
        ],
        html,
    )
        .into_response()
}

fn serve_bytes(path: &str, bytes: Vec<u8>) -> Response {
    let mime = mime_guess::from_path(path)
        .first_or_octet_stream()
        .to_string();

    (
        StatusCode::OK,
        [
            (header::CONTENT_TYPE, mime),
            (
                header::CACHE_CONTROL,
                if is_immutable_asset(path) {
                    "public, max-age=31536000, immutable".to_string()
                } else {
                    "no-cache".to_string()
                },
            ),
        ],
        bytes,
    )
        .into_response()
}

fn is_immutable_asset(path: &str) -> bool {
    path.contains("assets/") && path != "index.html"
}

fn dashboard_unavailable_response() -> Response {
    (
        StatusCode::SERVICE_UNAVAILABLE,
        "Web dashboard not available. Run `cd web && npm ci && npm run build`, set CONSTRUCT_WEB_ROOT, or build with CONSTRUCT_BUILD_WEB=1.",
    )
        .into_response()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn validate_relative_path_rejects_traversal() {
        assert!(validate_relative_path("../index.html").is_err());
        assert!(validate_relative_path("assets/../../index.html").is_err());
        assert!(validate_relative_path("/tmp/index.html").is_err());
        assert!(validate_relative_path("assets\\index.js").is_err());
    }

    #[test]
    fn resolve_filesystem_path_rejects_symlink_escape() {
        #[cfg_attr(not(unix), allow(unused_variables))]
        let root = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        let outside_file = outside.path().join("secret.txt");
        fs::write(&outside_file, "secret").unwrap();

        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(&outside_file, root.path().join("escape.txt")).unwrap();
            let canonical_root = root.path().canonicalize().unwrap();
            assert!(resolve_filesystem_path(&canonical_root, "escape.txt").is_err());
        }
    }

    #[test]
    fn immutable_cache_is_limited_to_asset_paths() {
        assert!(is_immutable_asset("assets/index-abc123.js"));
        assert!(!is_immutable_asset("index.html"));
        assert!(!is_immutable_asset("favicon-192.png"));
    }
}
