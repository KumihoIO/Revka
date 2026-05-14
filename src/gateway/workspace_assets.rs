//! Workspace asset HTTP route + HMAC-signed URL helpers.
//!
//! Generated images, agent outputs, and other workspace artifacts live
//! under `config.workspace_dir`. For the dashboard (and other tools that
//! emit canvas/HTML content) to embed these in `<img>` / `<a>` tags, the
//! gateway exposes them at `GET /workspace/{*path}`.
//!
//! ## Auth model
//!
//! Browsers don't attach `Authorization` headers to subresource fetches
//! (image loads, etc.), so requiring a bearer header would break the
//! common case. Instead, URLs carry an HMAC-SHA256 signature and an
//! expiry in the query string:
//!
//! ```text
//! /workspace/<rel-path>?exp=<unix-ts>&sig=<hex-sha256-hmac>
//! ```
//!
//! The signing key is the gateway's `service_token` — already shared with
//! operator-mcp (read from `~/.construct/service-token`), so tools can
//! mint URLs without round-tripping through the gateway.
//!
//! Paths are resolved under `workspace_dir` with traversal rejection
//! (no leading `/`, no `..` segments, canonicalized result must stay
//! under the canonical workspace root).

use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use axum::{
    extract::{Path as AxumPath, Query, State},
    http::{StatusCode, header},
    response::{IntoResponse, Response},
};
use hmac::{Hmac, Mac};
use serde::Deserialize;
use sha2::Sha256;

use super::AppState;

type HmacSha256 = Hmac<Sha256>;

/// Default URL lifetime when minting from `sign_url`.
pub const DEFAULT_TTL_SECS: u64 = 3600;

#[derive(Debug, Deserialize)]
pub struct AssetQuery {
    pub exp: Option<u64>,
    pub sig: Option<String>,
}

fn hmac_hex(rel_path: &str, exp: u64, secret: &[u8]) -> String {
    let mut mac = HmacSha256::new_from_slice(secret).expect("HMAC-SHA256 accepts any key length");
    mac.update(rel_path.as_bytes());
    mac.update(b"\n");
    mac.update(exp.to_string().as_bytes());
    hex::encode(mac.finalize().into_bytes())
}

/// Mint a signed workspace-asset URL.
///
/// Returns a relative URL (`/workspace/<rel-path>?exp=…&sig=…`) — let
/// the browser resolve against whatever origin the dashboard is on, so
/// the same URL works locally and through tunnels.
pub fn sign_url(rel_path: &str, ttl_secs: u64, secret: &[u8]) -> String {
    let exp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
        + ttl_secs;
    let sig = hmac_hex(rel_path, exp, secret);
    format!("/workspace/{rel_path}?exp={exp}&sig={sig}")
}

fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut diff = 0u8;
    for (x, y) in a.iter().zip(b.iter()) {
        diff |= x ^ y;
    }
    diff == 0
}

fn verify_signature(rel_path: &str, query: &AssetQuery, secret: &[u8]) -> Result<(), &'static str> {
    let exp = query.exp.ok_or("missing exp")?;
    let sig = query.sig.as_deref().ok_or("missing sig")?;

    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    if exp < now {
        return Err("expired");
    }

    let expected = hmac_hex(rel_path, exp, secret);
    if !constant_time_eq(sig.as_bytes(), expected.as_bytes()) {
        return Err("bad signature");
    }
    Ok(())
}

/// Resolve `rel_path` under `root`, rejecting absolute paths and parent
/// traversal. Returns the absolute on-disk path or an error.
fn safe_resolve(root: &Path, rel: &str) -> Result<PathBuf, &'static str> {
    if rel.is_empty() {
        return Err("empty path");
    }
    if rel.starts_with('/') || rel.starts_with('\\') {
        return Err("absolute path not allowed");
    }
    // Reject `..` as a path component anywhere.
    for part in rel.split(['/', '\\']) {
        if part == ".." {
            return Err("parent traversal not allowed");
        }
    }
    let candidate = root.join(rel);
    // Canonicalize both ends to defend against symlink escapes when both
    // sides exist; fall back to lexical containment otherwise.
    if let (Ok(canonical_root), Ok(canonical_candidate)) =
        (root.canonicalize(), candidate.canonicalize())
    {
        if !canonical_candidate.starts_with(&canonical_root) {
            return Err("path escapes workspace root");
        }
    }
    Ok(candidate)
}

/// `GET /workspace/{*path}` handler.
pub async fn handle_workspace_asset(
    State(state): State<AppState>,
    AxumPath(path): AxumPath<String>,
    Query(query): Query<AssetQuery>,
) -> Response {
    // Verify signature against the service-token HMAC key.
    if let Err(e) = verify_signature(&path, &query, state.service_token.as_bytes()) {
        tracing::warn!("workspace asset rejected ({e}): {path}");
        return (StatusCode::FORBIDDEN, format!("forbidden: {e}")).into_response();
    }

    // Resolve under config.workspace_dir.
    let workspace_dir = state.config.lock().workspace_dir.clone();
    let resolved = match safe_resolve(&workspace_dir, &path) {
        Ok(p) => p,
        Err(e) => {
            tracing::warn!("workspace asset bad path ({e}): {path}");
            return (StatusCode::BAD_REQUEST, format!("invalid path: {e}")).into_response();
        }
    };

    match tokio::fs::read(&resolved).await {
        Ok(bytes) => {
            let mime = mime_guess::from_path(&resolved)
                .first_or_octet_stream()
                .to_string();
            (
                StatusCode::OK,
                [
                    (header::CONTENT_TYPE, mime),
                    (header::CACHE_CONTROL, "private, max-age=300".to_string()),
                ],
                bytes,
            )
                .into_response()
        }
        Err(_) => (StatusCode::NOT_FOUND, "not found").into_response(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::tempdir;

    const SECRET: &[u8] = b"test-secret-token";

    fn parse_query(url: &str) -> AssetQuery {
        let qs = url.split_once('?').expect("query string present").1;
        let mut exp: Option<u64> = None;
        let mut sig: Option<String> = None;
        for pair in qs.split('&') {
            if let Some((k, v)) = pair.split_once('=') {
                match k {
                    "exp" => exp = v.parse().ok(),
                    "sig" => sig = Some(v.to_string()),
                    _ => {}
                }
            }
        }
        AssetQuery { exp, sig }
    }

    #[test]
    fn sign_then_verify_round_trips() {
        let url = sign_url("Construct/Images/foo.png", 3600, SECRET);
        let parsed = parse_query(&url);
        assert!(verify_signature("Construct/Images/foo.png", &parsed, SECRET).is_ok());
    }

    #[test]
    fn verify_rejects_tampered_path() {
        let url = sign_url("Construct/Images/foo.png", 3600, SECRET);
        let parsed = parse_query(&url);
        assert!(verify_signature("Construct/Images/EVIL.png", &parsed, SECRET).is_err());
    }

    #[test]
    fn verify_rejects_wrong_secret() {
        let url = sign_url("a.png", 3600, SECRET);
        let parsed = parse_query(&url);
        assert!(verify_signature("a.png", &parsed, b"different").is_err());
    }

    #[test]
    fn verify_rejects_expired() {
        let bad_exp = 1; // 1970 — long in the past.
        let sig = hmac_hex("a.png", bad_exp, SECRET);
        let q = AssetQuery {
            exp: Some(bad_exp),
            sig: Some(sig),
        };
        assert!(verify_signature("a.png", &q, SECRET).is_err());
    }

    #[test]
    fn safe_resolve_blocks_parent_traversal() {
        let root = tempdir().unwrap();
        assert!(safe_resolve(root.path(), "../etc/passwd").is_err());
        assert!(safe_resolve(root.path(), "a/../../b").is_err());
        assert!(safe_resolve(root.path(), "a/..").is_err());
    }

    #[test]
    fn safe_resolve_blocks_absolute_paths() {
        let root = tempdir().unwrap();
        assert!(safe_resolve(root.path(), "/etc/passwd").is_err());
        assert!(safe_resolve(root.path(), "\\etc\\passwd").is_err());
    }

    #[test]
    fn safe_resolve_accepts_legitimate_paths() {
        let root = tempdir().unwrap();
        let nested = root.path().join("Construct").join("Images");
        fs::create_dir_all(&nested).unwrap();
        fs::write(nested.join("foo.png"), b"\x89PNG").unwrap();

        let resolved = safe_resolve(root.path(), "Construct/Images/foo.png").unwrap();
        assert!(resolved.exists());
        assert_eq!(fs::read(&resolved).unwrap(), b"\x89PNG");
    }
}
