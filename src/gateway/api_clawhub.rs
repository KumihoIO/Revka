//! ClawHub marketplace integration — proxy endpoints for the Construct gateway.
//!
//! Routes:
//!   GET  /api/clawhub/search?q=...&limit=...     — search skills on ClawHub
//!   GET  /api/clawhub/trending?limit=...          — trending skills
//!   GET  /api/clawhub/skills/:slug                — skill detail
//!   POST /api/clawhub/install/:slug               — install skill into local Kumiho

use axum::{
    Json,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
};
use serde::Deserialize;
use std::collections::HashMap;

use super::AppState;
use super::api::require_auth;
use super::api_agents::build_kumiho_client;

// ── Query types ─────────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct SearchQuery {
    pub q: String,
    #[serde(default = "default_limit")]
    pub limit: u32,
}

#[derive(Deserialize)]
pub struct TrendingQuery {
    #[serde(default = "default_limit")]
    pub limit: u32,
}

fn default_limit() -> u32 {
    20
}

// ── Helpers ─────────────────────────────────────────────────────────────────

/// Extract ClawHub config without holding the MutexGuard.
fn clawhub_config(state: &AppState) -> (bool, String, Option<String>) {
    let config = state.config.lock();
    (
        config.clawhub.enabled,
        config.clawhub.api_url.trim_end_matches('/').to_string(),
        config.clawhub.api_token.clone(),
    )
}

fn make_client(token: &Option<String>) -> reqwest::Client {
    let mut builder = reqwest::Client::builder().timeout(std::time::Duration::from_secs(15));
    if let Some(t) = token {
        if !t.is_empty() {
            let mut headers = reqwest::header::HeaderMap::new();
            if let Ok(v) = reqwest::header::HeaderValue::from_str(&format!("Bearer {t}")) {
                headers.insert(reqwest::header::AUTHORIZATION, v);
            }
            builder = builder.default_headers(headers);
        }
    }
    builder.build().unwrap_or_default()
}

fn err_json(status: StatusCode, msg: impl std::fmt::Display) -> Response {
    (status, Json(serde_json::json!({"error": msg.to_string()}))).into_response()
}

async fn proxy_get(client: &reqwest::Client, url: &str) -> Result<serde_json::Value, Response> {
    let resp = client
        .get(url)
        .send()
        .await
        .map_err(|e| err_json(StatusCode::BAD_GATEWAY, format!("ClawHub unreachable: {e}")))?;
    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        let body = resp.text().await.unwrap_or_default();
        return Err(err_json(
            StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_GATEWAY),
            body,
        ));
    }
    resp.json::<serde_json::Value>()
        .await
        .map_err(|e| err_json(StatusCode::BAD_GATEWAY, format!("Parse error: {e}")))
}

// ── Handlers ────────────────────────────────────────────────────────────────

/// GET /api/clawhub/search?q=...&limit=...
pub async fn handle_clawhub_search(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(query): Query<SearchQuery>,
) -> Response {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }
    let (enabled, base, token) = clawhub_config(&state);
    if !enabled {
        return err_json(StatusCode::BAD_REQUEST, "ClawHub integration disabled");
    }

    let client = make_client(&token);
    let url = format!(
        "{base}/api/v1/search?q={}&limit={}",
        urlencoding::encode(&query.q),
        query.limit
    );
    match proxy_get(&client, &url).await {
        Ok(body) => Json(body).into_response(),
        Err(e) => e,
    }
}

/// GET /api/clawhub/trending?limit=...
pub async fn handle_clawhub_trending(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(query): Query<TrendingQuery>,
) -> Response {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }
    let (enabled, base, token) = clawhub_config(&state);
    if !enabled {
        return err_json(StatusCode::BAD_REQUEST, "ClawHub integration disabled");
    }

    let client = make_client(&token);
    let url = format!(
        "{base}/api/v1/skills?sort=trending&limit={}&nonSuspiciousOnly=true",
        query.limit
    );
    match proxy_get(&client, &url).await {
        Ok(body) => Json(body).into_response(),
        Err(e) => e,
    }
}

/// GET /api/clawhub/skills/:slug
pub async fn handle_clawhub_skill_detail(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(slug): Path<String>,
) -> Response {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }
    let (enabled, base, token) = clawhub_config(&state);
    if !enabled {
        return err_json(StatusCode::BAD_REQUEST, "ClawHub integration disabled");
    }

    let client = make_client(&token);

    // Fetch skill detail + SKILL.md content in parallel
    let detail_url = format!("{base}/api/v1/skills/{slug}");
    let content_url = format!("{base}/api/v1/skills/{slug}/file?path=SKILL.md&tag=latest");
    let c2 = client.clone();
    let (detail_res, content_res) = tokio::join!(
        proxy_get(&client, &detail_url),
        proxy_get(&c2, &content_url)
    );

    let mut detail_json = match detail_res {
        Ok(v) => v,
        Err(e) => return e,
    };

    // Attach SKILL.md content if available
    if let Ok(v) = content_res {
        if let Some(text) = v.as_str() {
            detail_json["skill_md"] = serde_json::Value::String(text.to_string());
        } else {
            detail_json["skill_md"] = v;
        }
    }

    Json(detail_json).into_response()
}

/// POST /api/clawhub/install/:slug
///
/// Fetches the SKILL.md from ClawHub and creates a local skill in Kumiho.
pub async fn handle_clawhub_install(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(slug): Path<String>,
) -> Response {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }
    let (enabled, base, token) = clawhub_config(&state);
    if !enabled {
        return err_json(StatusCode::BAD_REQUEST, "ClawHub integration disabled");
    }

    let client = make_client(&token);

    // 1. Fetch skill metadata
    let detail = match proxy_get(&client, &format!("{base}/api/v1/skills/{slug}")).await {
        Ok(v) => v,
        Err(e) => return e,
    };

    // 2. Fetch SKILL.md content
    let skill_md = match client
        .get(format!(
            "{base}/api/v1/skills/{slug}/file?path=SKILL.md&tag=latest"
        ))
        .send()
        .await
    {
        Ok(r) if r.status().is_success() => r.text().await.unwrap_or_default(),
        _ => {
            return err_json(
                StatusCode::BAD_GATEWAY,
                "Could not fetch SKILL.md from ClawHub",
            );
        }
    };

    // 3. Extract metadata
    let display_name = detail
        .get("displayName")
        .or_else(|| detail.get("name"))
        .and_then(|v| v.as_str())
        .unwrap_or(&slug)
        .to_string();
    let description = detail
        .get("description")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let version = detail
        .get("version")
        .or_else(|| detail.get("latestVersion").and_then(|v| v.get("version")))
        .and_then(|v| v.as_str())
        .unwrap_or("1.0.0")
        .to_string();

    // 4. Create local skill via Kumiho
    let kumiho = build_kumiho_client(&state);
    let memory_project = {
        let config = state.config.lock();
        config.kumiho.memory_project.clone()
    };

    if let Err(e) = kumiho.ensure_project(&memory_project).await {
        return err_json(
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("Kumiho project error: {e}"),
        );
    }
    if let Err(e) = kumiho.ensure_space(&memory_project, "Skills").await {
        return err_json(
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("Kumiho space error: {e}"),
        );
    }

    // Lightweight metadata only — no content (that goes into the local file)
    let mut metadata = HashMap::new();
    metadata.insert("description".to_string(), description.clone());
    metadata.insert("domain".to_string(), "Other".to_string());
    metadata.insert("tags".to_string(), format!("clawhub,{slug}"));
    metadata.insert("clawhub_slug".to_string(), slug.clone());
    metadata.insert("clawhub_version".to_string(), version);
    metadata.insert("source".to_string(), "clawhub".to_string());

    let skill_space_path = format!("/{memory_project}/Skills");
    let item = match kumiho
        .create_item(
            &skill_space_path,
            &slug,
            crate::skills::registration::SKILL_ITEM_KIND,
            HashMap::new(),
        )
        .await
    {
        Ok(item) => item,
        Err(e) => {
            return err_json(
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("Failed to create skill: {e}"),
            );
        }
    };

    let rev = match kumiho.create_revision(&item.kref, metadata).await {
        Ok(rev) => rev,
        Err(e) => {
            return err_json(
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("Failed to create revision: {e}"),
            );
        }
    };

    // Write content to local file and create artifact BEFORE publishing
    {
        let home = std::env::var("HOME").unwrap_or_else(|_| ".".to_string());
        let skills_dir = std::path::PathBuf::from(home).join(".construct/workspace/skills");
        let _ = tokio::fs::create_dir_all(&skills_dir).await;
        let file_path = skills_dir.join(format!("{slug}.md"));
        let location = super::api_skills::file_uri_for_path(&file_path);

        if let Err(e) = tokio::fs::write(&file_path, &skill_md).await {
            tracing::warn!("Failed to write skill file for {slug}: {e}");
        }

        if let Err(e) = kumiho
            .create_artifact(&rev.kref, "SKILL.md", &location, HashMap::new())
            .await
        {
            tracing::warn!("Failed to create SKILL.md artifact for {}: {e}", item.kref);
        }
    }

    let _ = kumiho.tag_revision(&rev.kref, "published").await;

    (
        StatusCode::CREATED,
        Json(serde_json::json!({
            "installed": true,
            "slug": slug,
            "name": display_name,
            "kref": item.kref,
            "description": description,
        })),
    )
        .into_response()
}
