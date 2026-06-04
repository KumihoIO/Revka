//! REST API handlers for auth-profile dropdown surface.
//!
//! Two endpoints:
//!   - `GET /api/auth/profiles` — bearer-auth — lists profile metadata
//!     for the editor dropdown. **Token bytes never appear in the response.**
//!   - `POST /api/auth/profiles/{id}/resolve` — service-token-auth —
//!     decrypts and returns the bound credential for a single profile.
//!     Used only by the operator-mcp runtime at step-execution time.
//!
//! The actual encryption + storage lives in `crate::auth::profiles::AuthProfilesStore`
//! (ChaCha20-Poly1305 AEAD via `crate::security::SecretStore`). This module is a
//! thin readonly view over that store.

use super::AppState;
use super::api::require_auth;
use super::client_key_from_request;
use crate::auth::profiles::{AuthProfile, AuthProfileKind, profile_id};
use axum::{
    extract::{ConnectInfo, Path, State},
    http::{HeaderMap, StatusCode, header},
    response::{IntoResponse, Json},
};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::net::SocketAddr;

const SERVICE_TOKEN_HEADER: &str = "X-Revka-Service-Token";

// ── Response shapes ─────────────────────────────────────────────────────

/// Metadata-only profile summary returned from `GET /api/auth/profiles`.
///
/// **Audit point:** confirm by code-read that this struct contains no token,
/// access_token, refresh_token, id_token, or any other decrypted material —
/// only display metadata + safe lifecycle hints.
#[derive(Serialize, Clone)]
pub struct AuthProfileSummary {
    pub id: String,
    pub provider: String,
    pub profile_name: String,
    /// "oauth" or "token".
    pub kind: String,
    pub account_id: Option<String>,
    pub workspace_id: Option<String>,
    /// OAuth profiles only — used by the editor to show expiry chips.
    pub expires_at: Option<DateTime<Utc>>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

/// Resolved credential — only ever returned from the service-token-gated
/// resolve endpoint. **Never serialized into list responses or YAML.**
#[derive(Serialize)]
pub struct ResolvedAuth {
    pub token: String,
    /// "oauth" or "token".
    pub kind: String,
    pub provider: String,
    pub profile_name: String,
    pub expires_at: Option<DateTime<Utc>>,
}

// ── List handler ────────────────────────────────────────────────────────

/// `GET /api/auth/profiles` — bearer-auth, metadata-only listing.
pub async fn handle_list_auth_profiles(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let Some(store) = state.auth_profiles.as_ref() else {
        // Auth profile store not configured — return empty list rather than 500.
        return Json(serde_json::json!({ "profiles": [] })).into_response();
    };

    let data = match store.load().await {
        Ok(d) => d,
        Err(err) => {
            tracing::warn!(error = %err, "auth-profiles list failed");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({ "error": "Failed to load auth profiles" })),
            )
                .into_response();
        }
    };

    let mut profiles: Vec<AuthProfileSummary> = data
        .profiles
        .into_values()
        .map(|p| AuthProfileSummary {
            id: p.id,
            provider: p.provider,
            profile_name: p.profile_name,
            kind: match p.kind {
                AuthProfileKind::OAuth => "oauth".to_string(),
                AuthProfileKind::Token => "token".to_string(),
            },
            account_id: p.account_id,
            workspace_id: p.workspace_id,
            expires_at: p.token_set.as_ref().and_then(|t| t.expires_at),
            created_at: p.created_at,
            updated_at: p.updated_at,
        })
        .collect();
    profiles.sort_by(|a, b| {
        a.provider
            .cmp(&b.provider)
            .then_with(|| a.profile_name.cmp(&b.profile_name))
    });

    Json(serde_json::json!({ "profiles": profiles })).into_response()
}

// ── Create handler ──────────────────────────────────────────────────────

/// Body for `POST /api/auth/profiles`. Only static-token / API-key flows
/// are supported here — OAuth profiles must be created through the existing
/// /config flow that drives the gateway's interactive consent dance.
#[derive(Deserialize)]
pub struct CreateAuthProfileBody {
    pub provider: String,
    pub profile_name: String,
    /// Raw bearer / API key. Encrypted by the store before being persisted.
    pub token: String,
    #[serde(default)]
    pub account_id: Option<String>,
    /// Defaults to "token". "api_key" is accepted as a synonym; "oauth" is
    /// rejected with 400 because the runtime requires a refresh-token flow.
    #[serde(default)]
    pub kind: Option<String>,
}

/// `POST /api/auth/profiles` — bearer-auth, creates a static-token profile.
///
/// 201 with the new `AuthProfileSummary` on success. 400 for missing
/// fields or unsupported `kind` (e.g. "oauth"). 409 if a profile with the
/// same provider+name already exists. The token is encrypted by the
/// underlying `AuthProfilesStore` before persist; the response never
/// echoes it back.
///
/// Rate-limited via the shared `AuthRateLimiter` (see `auth_rate_limit.rs`,
/// 10 attempts per 60s window per peer IP, then a 5-minute lockout). The
/// 409-on-duplicate response is otherwise a weak `{provider, profile_name}`
/// enumeration oracle. Loopback peers are exempt — the limiter only
/// trusts the socket peer, never `X-Forwarded-For`.
pub async fn handle_create_auth_profile(
    State(state): State<AppState>,
    ConnectInfo(peer_addr): ConnectInfo<SocketAddr>,
    headers: HeaderMap,
    Json(body): Json<CreateAuthProfileBody>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    // Rate-limit before any work — applies even to authenticated callers
    // because the duplicate-detection response (409) is a side-channel.
    let rate_key =
        client_key_from_request(Some(peer_addr), &headers, state.trust_forwarded_headers);
    let peer_is_loopback = peer_addr.ip().is_loopback();
    if let Err(e) = state
        .auth_limiter
        .check_rate_limit(&rate_key, peer_is_loopback)
    {
        tracing::warn!("auth-profiles create: rate limit exceeded for {rate_key}");
        return (
            StatusCode::TOO_MANY_REQUESTS,
            Json(serde_json::json!({
                "error": format!("Too many create attempts. Try again in {}s.", e.retry_after_secs),
                "retry_after": e.retry_after_secs,
                "code": "auth_profile_rate_limited"
            })),
        )
            .into_response();
    }
    state
        .auth_limiter
        .record_attempt(&rate_key, peer_is_loopback);

    // Trim early so empty-after-trim is treated as missing.
    let provider = body.provider.trim().to_string();
    let profile_name = body.profile_name.trim().to_string();
    let token = body.token.clone();
    let account_id = body.account_id.and_then(|s| {
        let trimmed = s.trim().to_string();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed)
        }
    });

    if provider.is_empty() || profile_name.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "error": "provider and profile_name are required",
                "code": "auth_profile_missing_fields"
            })),
        )
            .into_response();
    }

    if token.trim().is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "error": "token is required",
                "code": "auth_profile_missing_token"
            })),
        )
            .into_response();
    }

    let kind = body.kind.as_deref().unwrap_or("token").to_ascii_lowercase();
    match kind.as_str() {
        "token" | "api_key" => {}
        "oauth" => {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({
                    "error": "OAuth profiles must be created via the /config flow",
                    "code": "auth_profile_oauth_unsupported"
                })),
            )
                .into_response();
        }
        other => {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({
                    "error": format!("unsupported auth profile kind: {other}"),
                    "code": "auth_profile_invalid_kind"
                })),
            )
                .into_response();
        }
    }

    let Some(store) = state.auth_profiles.as_ref() else {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(serde_json::json!({
                "error": "auth profile store not configured on this gateway",
                "code": "auth_store_unavailable"
            })),
        )
            .into_response();
    };

    // Conflict check — load existing profiles first, refuse if the id is taken.
    // The store has no separate "create-only" entry point; reusing
    // `upsert_profile` would silently overwrite, so we gate it explicitly.
    let id = profile_id(&provider, &profile_name);
    match store.load().await {
        Ok(data) => {
            if data.profiles.contains_key(&id) {
                return (
                    StatusCode::CONFLICT,
                    Json(serde_json::json!({
                        "error": format!("auth profile already exists: {id}"),
                        "code": "auth_profile_already_exists"
                    })),
                )
                    .into_response();
            }
        }
        Err(err) => {
            tracing::warn!(error = %err, "auth-profiles create: load failed");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({
                    "error": "Failed to load auth profiles",
                    "code": "auth_store_load_failed"
                })),
            )
                .into_response();
        }
    }

    let mut profile = AuthProfile::new_token(&provider, &profile_name, token);
    profile.account_id = account_id;

    if let Err(err) = store.upsert_profile(profile.clone(), false).await {
        tracing::warn!(error = %err, "auth-profiles create: persist failed");
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({
                "error": "Failed to save auth profile",
                "code": "auth_store_save_failed"
            })),
        )
            .into_response();
    }

    let summary = AuthProfileSummary {
        id: profile.id.clone(),
        provider: profile.provider.clone(),
        profile_name: profile.profile_name.clone(),
        kind: match profile.kind {
            AuthProfileKind::OAuth => "oauth".to_string(),
            AuthProfileKind::Token => "token".to_string(),
        },
        account_id: profile.account_id.clone(),
        workspace_id: profile.workspace_id.clone(),
        // Static-token profiles never carry an expires_at.
        expires_at: None,
        created_at: profile.created_at,
        updated_at: profile.updated_at,
    };

    (StatusCode::CREATED, Json(summary)).into_response()
}

// ── Delete handler ──────────────────────────────────────────────────────

/// `DELETE /api/auth/profiles/{id}` — bearer-auth, removes a stored profile.
///
/// 204 on successful delete, 404 if the profile id doesn't exist. The
/// underlying `AuthProfilesStore::remove_profile` is idempotent on cleared
/// active references — no separate purge call needed.
pub async fn handle_delete_auth_profile(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let Some(store) = state.auth_profiles.as_ref() else {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(serde_json::json!({
                "error": "auth profile store not configured",
                "code": "auth_store_unavailable"
            })),
        )
            .into_response();
    };

    match store.remove_profile(&id).await {
        Ok(true) => StatusCode::NO_CONTENT.into_response(),
        Ok(false) => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({
                "error": format!("auth profile not found: {id}"),
                "code": "auth_profile_not_found"
            })),
        )
            .into_response(),
        Err(err) => {
            tracing::warn!(error = %err, "auth-profile delete failed");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({
                    "error": "Failed to delete auth profile",
                    "code": "auth_store_delete_failed"
                })),
            )
                .into_response()
        }
    }
}

// ── Resolve handler ─────────────────────────────────────────────────────

fn require_service_token(
    state: &AppState,
    headers: &HeaderMap,
) -> Result<(), (StatusCode, Json<serde_json::Value>)> {
    let provided = headers
        .get(SERVICE_TOKEN_HEADER)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    let expected = state.service_token.as_ref();
    if expected.is_empty() {
        return Err((
            StatusCode::SERVICE_UNAVAILABLE,
            Json(serde_json::json!({
                "error": "service token not configured on this gateway"
            })),
        ));
    }
    // Constant-time compare to dodge timing oracles even though both bytes
    // are local — same posture the rest of the gateway uses for shared secrets.
    if crate::security::pairing::constant_time_eq(provided, expected) {
        Ok(())
    } else {
        Err((
            StatusCode::UNAUTHORIZED,
            Json(serde_json::json!({
                "error": "missing or invalid X-Revka-Service-Token header"
            })),
        ))
    }
}

/// `POST /api/auth/profiles/{id}/resolve` — service-token only.
///
/// Loads the named profile, returns the decrypted credential. 404 if the
/// profile doesn't exist; 410 Gone for OAuth profiles whose `expires_at` is
/// in the past (the runtime should fail the step rather than silently send
/// a stale token).
pub async fn handle_resolve_auth_profile(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> impl IntoResponse {
    if let Err(e) = require_service_token(&state, &headers) {
        return e.into_response();
    }

    let Some(store) = state.auth_profiles.as_ref() else {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(serde_json::json!({
                "error": "auth profile store not configured",
                "code": "auth_store_unavailable"
            })),
        )
            .into_response();
    };

    let data = match store.load().await {
        Ok(d) => d,
        Err(err) => {
            tracing::warn!(error = %err, "auth-profile resolve: load failed");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({
                    "error": "Failed to load auth profiles",
                    "code": "auth_store_load_failed"
                })),
            )
                .into_response();
        }
    };

    let Some(profile) = data.profiles.get(&id) else {
        return (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({
                "error": format!("auth profile not found: {id}"),
                "code": "auth_profile_not_found"
            })),
        )
            .into_response();
    };

    match profile.kind {
        AuthProfileKind::Token => {
            let token = profile
                .token
                .clone()
                .filter(|t| !t.trim().is_empty())
                .unwrap_or_default();
            if token.is_empty() {
                return (
                    StatusCode::GONE,
                    Json(serde_json::json!({
                        "error": "auth profile is empty",
                        "code": "auth_profile_empty"
                    })),
                )
                    .into_response();
            }
            // Avoid the X-Revka-Service-Token bouncing back into proxies'
            // shared cache layers.
            let mut resp = Json(ResolvedAuth {
                token,
                kind: "token".into(),
                provider: profile.provider.clone(),
                profile_name: profile.profile_name.clone(),
                expires_at: None,
            })
            .into_response();
            resp.headers_mut()
                .insert(header::CACHE_CONTROL, "no-store".parse().unwrap());
            resp
        }
        AuthProfileKind::OAuth => {
            let Some(token_set) = profile.token_set.as_ref() else {
                return (
                    StatusCode::GONE,
                    Json(serde_json::json!({
                        "error": "OAuth profile missing token_set",
                        "code": "auth_profile_missing_tokens"
                    })),
                )
                    .into_response();
            };
            // Already-expired? 410 Gone — the runtime should classify this as
            // auth_resolve_failed with the structured reason.
            if let Some(expires_at) = token_set.expires_at {
                if expires_at <= Utc::now() {
                    return (
                        StatusCode::GONE,
                        Json(serde_json::json!({
                            "error": "OAuth profile expired",
                            "code": "auth_profile_expired",
                            "expired_at": expires_at,
                        })),
                    )
                        .into_response();
                }
            }
            let mut resp = Json(ResolvedAuth {
                token: token_set.access_token.clone(),
                kind: "oauth".into(),
                provider: profile.provider.clone(),
                profile_name: profile.profile_name.clone(),
                expires_at: token_set.expires_at,
            })
            .into_response();
            resp.headers_mut()
                .insert(header::CACHE_CONTROL, "no-store".parse().unwrap());
            resp
        }
    }
}
