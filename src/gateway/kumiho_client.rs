//! HTTP client for the Kumiho FastAPI REST API.
//!
//! Wraps `reqwest` calls to the Kumiho service, providing typed methods for
//! item CRUD, revisions, search, and space management.  Used by the agent
//! management API routes (`/api/agents`) and skill management routes
//! (`/api/skills`).

use crate::config::Config;
use axum::http::{HeaderValue, StatusCode, header};
use axum::response::{IntoResponse, Json, Response};
use parking_lot::Mutex;
use rand::RngExt;
use reqwest::{Client, Method};
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, hash_map::DefaultHasher};
use std::hash::{Hash, Hasher};
use std::path::PathBuf;
use std::sync::OnceLock;
use std::time::{Duration, Instant};

/// Build a `KumihoClient` from the top-level `Config`.
///
/// Reads `kumiho.api_url` for the base URL and `KUMIHO_SERVICE_TOKEN` env var
/// for the service token. Used by CLI commands (`construct memory`,
/// `construct migrate openclaw`) that need a Kumiho client without an
/// `AppState`.
pub fn build_client_from_config(config: &Config) -> KumihoClient {
    build_cached_client(config.kumiho.api_url.clone())
}

/// Convert a human-readable name to a kref-safe slug (lowercase, hyphens, no spaces).
pub fn slugify(name: &str) -> String {
    name.trim()
        .to_lowercase()
        .chars()
        .map(|c| {
            if c.is_alphanumeric() || c == '-' {
                c
            } else {
                '-'
            }
        })
        .collect::<String>()
        .split('-')
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join("-")
}

fn item_kref_without_selectors(kref: &str) -> &str {
    kref.split_once('?').map_or(kref, |(base, _)| base)
}

pub(crate) fn configured_service_token() -> String {
    std::env::var("KUMIHO_SERVICE_TOKEN")
        .ok()
        .filter(|v| !v.trim().is_empty())
        .or_else(|| {
            std::env::var("KUMIHO_AUTH_TOKEN")
                .ok()
                .filter(|v| !v.trim().is_empty())
        })
        .unwrap_or_default()
}

pub(crate) fn configured_auth_token(service_token: &str) -> String {
    std::env::var("KUMIHO_AUTH_TOKEN")
        .ok()
        .filter(|v| !v.trim().is_empty())
        .unwrap_or_else(|| service_token.to_string())
}

/// Build a cached gateway `KumihoClient` from the current config + env.
///
/// All gateway Kumiho access should come through this function so token
/// selection, connection pooling, bridge fallback, and response caching stay
/// centralized in [`KumihoClient`].
pub(super) fn build_kumiho_client(state: &super::AppState) -> KumihoClient {
    let base_url = state.config.lock().kumiho.api_url.clone();
    build_cached_client(base_url)
}

#[derive(Clone)]
struct CachedKumihoClient {
    base_url: String,
    service_token: String,
    auth_token: String,
    client: KumihoClient,
}

static KUMIHO_CLIENT: OnceLock<Mutex<Option<CachedKumihoClient>>> = OnceLock::new();

fn build_cached_client(base_url: String) -> KumihoClient {
    let service_token = configured_service_token();
    let auth_token = configured_auth_token(&service_token);
    let lock = KUMIHO_CLIENT.get_or_init(|| Mutex::new(None));
    let mut cached = lock.lock();

    if let Some(entry) = cached.as_ref() {
        if entry.base_url == base_url
            && entry.service_token == service_token
            && entry.auth_token == auth_token
        {
            return entry.client.clone();
        }
    }

    let client = KumihoClient::new_with_auth_token(
        base_url.clone(),
        service_token.clone(),
        auth_token.clone(),
    );
    *cached = Some(CachedKumihoClient {
        base_url,
        service_token,
        auth_token,
        client: client.clone(),
    });
    client
}

/// Kumiho FastAPI client.
#[derive(Clone)]
pub struct KumihoClient {
    client: Client,
    base_url: String,
    service_token: String,
    auth_token: String,
}

// ── Response types (match Kumiho FastAPI JSON) ──────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ItemResponse {
    pub kref: String,
    pub name: String,
    pub item_name: String,
    pub kind: String,
    #[serde(default)]
    pub deprecated: bool,
    pub created_at: Option<String>,
    pub author: Option<String>,
    pub username: Option<String>,
    pub author_display: Option<String>,
    #[serde(default)]
    pub metadata: HashMap<String, String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RevisionResponse {
    pub kref: String,
    pub item_kref: String,
    pub number: i32,
    #[serde(default)]
    pub latest: bool,
    #[serde(default)]
    pub tags: Vec<String>,
    #[serde(default)]
    pub metadata: HashMap<String, String>,
    #[serde(default)]
    pub deprecated: bool,
    pub created_at: Option<String>,
    pub author: Option<String>,
    pub username: Option<String>,
    pub author_display: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BatchRevisionsResponse {
    pub revisions: Vec<RevisionResponse>,
    pub not_found: Vec<String>,
    pub requested_count: i32,
    pub found_count: i32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SearchResult {
    pub item: ItemResponse,
    #[serde(default)]
    pub score: f64,
}

// ── Bundle response types ────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BundleMemberInfo {
    pub item_kref: String,
    pub added_at: Option<String>,
    pub added_by: Option<String>,
    pub added_in_revision: Option<i32>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BundleMembersResponse {
    pub members: Vec<BundleMemberInfo>,
    pub total_count: Option<i32>,
}

// ── Artifact response types ────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ArtifactResponse {
    pub kref: String,
    pub name: String,
    pub location: String,
    pub revision_kref: String,
    pub item_kref: Option<String>,
    #[serde(default)]
    pub deprecated: bool,
    pub created_at: Option<String>,
    pub author: Option<String>,
    pub username: Option<String>,
    pub author_display: Option<String>,
    #[serde(default)]
    pub metadata: HashMap<String, String>,
}

// ── Edge response types ─────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EdgeResponse {
    pub source_kref: String,
    pub target_kref: String,
    pub edge_type: String,
    pub created_at: Option<String>,
    #[serde(default)]
    pub metadata: Option<HashMap<String, String>>,
}

// ── Space response types ───────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SpaceResponse {
    pub path: String,
    pub name: String,
    pub parent_path: Option<String>,
    pub created_at: Option<String>,
    pub author: Option<String>,
    pub username: Option<String>,
    pub author_display: Option<String>,
}

// ── Error type ──────────────────────────────────────────────────────────

#[derive(Debug, thiserror::Error)]
pub enum KumihoError {
    #[error("Kumiho service unreachable: {0}")]
    Unreachable(#[from] reqwest::Error),

    #[error("Kumiho returned {status}: {body}")]
    Api { status: u16, body: String },

    #[error("Kumiho upstream temporarily unavailable (HTTP {status} after {attempts} attempts)")]
    UpstreamUnavailable { status: u16, attempts: u32 },

    #[error("Unexpected response: {0}")]
    Decode(String),
}

pub type Result<T> = std::result::Result<T, KumihoError>;

/// Statuses we treat as a "transient upstream blip" — gateway/CDN-style 5xx
/// codes. Pure 500 (application error) and 501 (not implemented) are NOT
/// retried: those usually mean a real bug, not a connectivity hiccup.
pub(crate) fn is_retryable_status(status: u16) -> bool {
    matches!(status, 502 | 503 | 504 | 520 | 522 | 524)
}

/// Per-attempt request timeout used by the retry helper. Short enough that
/// 3 attempts + jittered backoffs fit inside `TOTAL_BUDGET`.
const PER_ATTEMPT_TIMEOUT: Duration = Duration::from_secs(5);

/// End-to-end wall-time cap for `send_with_retry`. A hung upstream cannot
/// hold a single gateway request open longer than this.
const TOTAL_BUDGET: Duration = Duration::from_secs(15);

/// Would sleeping `delay_ms` still leave room before `deadline`? Used by the
/// retry helper to give up early when there isn't enough budget left to
/// usefully retry.
fn deadline_allows(deadline: Instant, delay_ms: u64) -> bool {
    let now = Instant::now();
    if now >= deadline {
        return false;
    }
    let remaining = deadline.saturating_duration_since(now);
    remaining > Duration::from_millis(delay_ms)
}

/// Sleep for `base_ms` ± 20% to avoid thundering-herd retry waves.
async fn sleep_with_jitter(base_ms: u64) {
    let jitter_range = (base_ms as f64 * 0.2) as i64;
    let jitter: i64 = if jitter_range > 0 {
        rand::rng().random_range(-jitter_range..=jitter_range)
    } else {
        0
    };
    let delay = (base_ms as i64 + jitter).max(0) as u64;
    tokio::time::sleep(Duration::from_millis(delay)).await;
}

/// Heuristic: does this body look like an HTML error page (e.g. Cloudflare's
/// 2KB 502 splash)? Used to keep upstream HTML out of our JSON error responses
/// and out of structured logs. `pub(crate)` so the generic `/api/kumiho/*`
/// proxy can detect HTML bodies without going through `KumihoClient`.
pub(crate) fn looks_like_html_body(body: &str, content_type: Option<&str>) -> bool {
    if let Some(ct) = content_type {
        if ct.to_ascii_lowercase().starts_with("text/html") {
            return true;
        }
    }
    let trimmed = body.trim_start();
    let head: String = trimmed
        .chars()
        .take(16)
        .collect::<String>()
        .to_ascii_lowercase();
    head.starts_with("<!doctype") || head.starts_with("<html")
}

/// Map any `KumihoError` to a clean JSON HTTP response. Centralised so every
/// gateway route returns the same shape. Upstream HTML never leaks past this
/// boundary — `Api` errors with HTML bodies were trimmed in `check_response`.
pub fn kumiho_error_to_response(err: KumihoError) -> Response {
    match err {
        KumihoError::Unreachable(e) => {
            tracing::warn!(error = %e, "Kumiho unreachable");
            // DNS/connect failures map to 503 (service unavailable), not 502:
            // recovery typically takes longer than a per-request upstream blip,
            // so we hint a slightly longer `Retry-After`.
            let mut resp = (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(serde_json::json!({
                    "error": "Kumiho cloud unreachable",
                    "error_code": "kumiho_unreachable",
                    "retry_after_seconds": 10,
                })),
            )
                .into_response();
            resp.headers_mut()
                .insert(header::RETRY_AFTER, HeaderValue::from_static("10"));
            resp
        }
        KumihoError::UpstreamUnavailable { status, attempts } => {
            tracing::warn!(
                upstream_status = status,
                attempts = attempts,
                "Kumiho upstream unavailable after retries"
            );
            let mut resp = (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(serde_json::json!({
                    "error": "Kumiho cloud temporarily unavailable",
                    "error_code": "kumiho_upstream_unavailable",
                    "upstream_status": status,
                    "attempts": attempts,
                    "retry_after_seconds": 5,
                })),
            )
                .into_response();
            resp.headers_mut()
                .insert(header::RETRY_AFTER, HeaderValue::from_static("5"));
            resp
        }
        KumihoError::Api { status, body } => {
            // 5xx that slipped past the retry layer (e.g. a non-retryable 500)
            // — still treat as "temporarily unavailable" so we never forward
            // upstream bodies to the client.
            if status >= 500 {
                tracing::warn!(upstream_status = status, body = %body, "Kumiho 5xx (non-retried)");
                let mut resp = (
                    StatusCode::SERVICE_UNAVAILABLE,
                    Json(serde_json::json!({
                        "error": "Kumiho cloud temporarily unavailable",
                        "error_code": "kumiho_upstream_unavailable",
                        "upstream_status": status,
                        "attempts": 1,
                        "retry_after_seconds": 5,
                    })),
                )
                    .into_response();
                resp.headers_mut()
                    .insert(header::RETRY_AFTER, HeaderValue::from_static("5"));
                return resp;
            }
            // Keep current behaviour for 4xx — callers branch on 404/409/etc.
            // 401/403 from Kumiho are rewritten to 502 so the dashboard doesn't
            // confuse them with pairing auth failures and force a re-pair.
            let code = if status == 401 || status == 403 {
                StatusCode::BAD_GATEWAY
            } else {
                StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_GATEWAY)
            };
            (
                code,
                Json(serde_json::json!({
                    "error": format!("Kumiho upstream: {body}"),
                    "error_code": "kumiho_upstream_error",
                    "upstream_status": status,
                })),
            )
                .into_response()
        }
        KumihoError::Decode(msg) => (
            StatusCode::BAD_GATEWAY,
            Json(serde_json::json!({
                "error": format!("Bad response from Kumiho: {msg}"),
                "error_code": "kumiho_decode_error",
            })),
        )
            .into_response(),
    }
}

#[derive(Debug, Clone)]
pub struct RawKumihoResponse {
    pub status: StatusCode,
    pub body: String,
    pub transport: Option<&'static str>,
    pub cache_state: Option<&'static str>,
}

#[derive(Clone, Hash, Eq, PartialEq)]
struct RawCacheKey {
    token_hash: u64,
    url: String,
}

#[derive(Clone)]
struct RawCacheEntry {
    status: StatusCode,
    body: String,
    fetched_at: Instant,
}

static KUMIHO_RAW_CACHE: OnceLock<Mutex<HashMap<RawCacheKey, RawCacheEntry>>> = OnceLock::new();

const RAW_CACHE_TTL: Duration = Duration::from_secs(10);
const RAW_STALE_TTL: Duration = Duration::from_secs(120);

fn token_hash(token: &str) -> u64 {
    let mut hasher = DefaultHasher::new();
    token.hash(&mut hasher);
    hasher.finish()
}

fn raw_cache_key(url: &str, token: &str) -> RawCacheKey {
    RawCacheKey {
        token_hash: token_hash(token),
        url: url.to_string(),
    }
}

fn cached_raw_response(url: &str, token: &str, allow_stale: bool) -> Option<RawKumihoResponse> {
    let lock = KUMIHO_RAW_CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    let cache = lock.lock();
    let entry = cache.get(&raw_cache_key(url, token))?;
    let age = entry.fetched_at.elapsed();
    if age <= RAW_CACHE_TTL {
        return Some(RawKumihoResponse {
            status: entry.status,
            body: entry.body.clone(),
            transport: None,
            cache_state: Some("hit"),
        });
    }
    if allow_stale && age <= RAW_STALE_TTL {
        return Some(RawKumihoResponse {
            status: entry.status,
            body: entry.body.clone(),
            transport: None,
            cache_state: Some("stale"),
        });
    }
    None
}

fn set_cached_raw_response(url: &str, token: &str, status: StatusCode, body: &str) {
    if !status.is_success() {
        return;
    }

    let lock = KUMIHO_RAW_CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    let mut cache = lock.lock();
    cache.retain(|_, entry| entry.fetched_at.elapsed() <= RAW_STALE_TTL);
    cache.insert(
        raw_cache_key(url, token),
        RawCacheEntry {
            status,
            body: body.to_string(),
            fetched_at: Instant::now(),
        },
    );
}

pub(super) fn invalidate_proxy_cache() {
    if let Some(lock) = KUMIHO_RAW_CACHE.get() {
        lock.lock().clear();
    }
}

fn raw_error_allows_stale(err: &KumihoError) -> bool {
    match err {
        KumihoError::Unreachable(_) | KumihoError::UpstreamUnavailable { .. } => true,
        KumihoError::Api { status, .. } => *status >= 500,
        KumihoError::Decode(_) => false,
    }
}

fn is_uuid_like(value: &str) -> bool {
    let bytes = value.as_bytes();
    if bytes.len() != 36 {
        return false;
    }
    for (idx, byte) in bytes.iter().enumerate() {
        if matches!(idx, 8 | 13 | 18 | 23) {
            if *byte != b'-' {
                return false;
            }
            continue;
        }
        if !byte.is_ascii_hexdigit() {
            return false;
        }
    }
    true
}

fn usable_identity(value: Option<&str>) -> Option<String> {
    let value = value?.trim();
    if value.is_empty() || is_uuid_like(value) {
        None
    } else {
        Some(value.to_string())
    }
}

fn kumiho_auth_email_path() -> Option<PathBuf> {
    directories::UserDirs::new().map(|dirs| {
        dirs.home_dir()
            .join(".kumiho")
            .join("kumiho_authentication.json")
    })
}

fn current_kumiho_account_email() -> Option<String> {
    let path = kumiho_auth_email_path()?;
    let content = std::fs::read_to_string(path).ok()?;
    let parsed = serde_json::from_str::<serde_json::Value>(&content).ok()?;
    parsed
        .get("email")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
}

fn display_author_for_object(
    object: &serde_json::Map<String, serde_json::Value>,
    fallback_email: Option<&str>,
) -> Option<String> {
    usable_identity(object.get("username").and_then(|v| v.as_str()))
        .or_else(|| {
            object
                .get("metadata")
                .and_then(|v| v.as_object())
                .and_then(|metadata| {
                    usable_identity(metadata.get("username").and_then(|v| v.as_str()))
                        .or_else(|| {
                            usable_identity(metadata.get("updated_by").and_then(|v| v.as_str()))
                        })
                        .or_else(|| {
                            usable_identity(metadata.get("created_by").and_then(|v| v.as_str()))
                        })
                })
        })
        .or_else(|| usable_identity(object.get("author").and_then(|v| v.as_str())))
        .or_else(|| fallback_email.map(str::to_string))
}

fn enrich_author_display(value: &mut serde_json::Value, fallback_email: Option<&str>) {
    match value {
        serde_json::Value::Array(items) => {
            for item in items {
                enrich_author_display(item, fallback_email);
            }
        }
        serde_json::Value::Object(object) => {
            if (object.contains_key("author") || object.contains_key("username"))
                && !object.contains_key("author_display")
            {
                if let Some(display) = display_author_for_object(object, fallback_email) {
                    object.insert(
                        "author_display".to_string(),
                        serde_json::Value::String(display),
                    );
                }
            }

            for item in object.values_mut() {
                enrich_author_display(item, fallback_email);
            }
        }
        _ => {}
    }
}

fn enrich_success_body(body: String) -> String {
    let Ok(mut value) = serde_json::from_str::<serde_json::Value>(&body) else {
        return body;
    };
    let fallback_email = current_kumiho_account_email();
    enrich_author_display(&mut value, fallback_email.as_deref());
    serde_json::to_string(&value).unwrap_or(body)
}

// ── Request body types ──────────────────────────────────────────────────

#[derive(Serialize)]
struct CreateProjectBody {
    name: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    description: Option<String>,
}

#[derive(Serialize)]
struct CreateSpaceBody {
    parent_path: String,
    name: String,
}

#[derive(Serialize)]
struct CreateItemBody {
    space_path: String,
    item_name: String,
    kind: String,
    #[serde(skip_serializing_if = "HashMap::is_empty")]
    metadata: HashMap<String, String>,
}

#[derive(Serialize)]
struct CreateRevisionBody {
    item_kref: String,
    #[serde(skip_serializing_if = "HashMap::is_empty")]
    metadata: HashMap<String, String>,
}

#[derive(Serialize)]
struct CreateBundleBody {
    space_path: String,
    bundle_name: String,
    #[serde(skip_serializing_if = "HashMap::is_empty")]
    metadata: HashMap<String, String>,
}

#[derive(Serialize)]
struct BundleMemberBody {
    bundle_kref: String,
    item_kref: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    metadata: Option<HashMap<String, String>>,
}

#[derive(Serialize)]
struct RemoveBundleMemberBody {
    bundle_kref: String,
    item_kref: String,
}

#[derive(Serialize)]
struct CreateEdgeBody {
    source_revision_kref: String,
    target_revision_kref: String,
    edge_type: String,
    #[serde(skip_serializing_if = "HashMap::is_empty")]
    metadata: HashMap<String, String>,
}

#[derive(Serialize)]
struct CreateArtifactBody {
    revision_kref: String,
    name: String,
    location: String,
    #[serde(skip_serializing_if = "HashMap::is_empty")]
    metadata: HashMap<String, String>,
}

impl KumihoClient {
    /// Create a new Kumiho client.
    ///
    /// `service_token` is sent as `X-Kumiho-Token` on every request.
    pub fn new(base_url: String, service_token: String) -> Self {
        Self::new_with_auth_token(base_url, service_token.clone(), service_token)
    }

    /// Create a new Kumiho client with separate FastAPI and SDK auth tokens.
    pub fn new_with_auth_token(
        base_url: String,
        service_token: String,
        auth_token: String,
    ) -> Self {
        let client = Client::builder()
            .timeout(std::time::Duration::from_secs(20))
            .connect_timeout(std::time::Duration::from_secs(5))
            .pool_idle_timeout(Some(std::time::Duration::from_secs(90)))
            .pool_max_idle_per_host(32)
            .tcp_keepalive(Some(std::time::Duration::from_secs(60)))
            .build()
            .unwrap_or_else(|_| Client::new());
        Self {
            client,
            base_url: base_url.trim_end_matches('/').to_string(),
            service_token,
            auth_token,
        }
    }

    /// Access the inner HTTP client (for proxy use).
    pub fn client(&self) -> &Client {
        &self.client
    }

    // ── Helpers ─────────────────────────────────────────────────────

    fn url(&self, path: &str) -> String {
        format!("{}/api/v1{}", self.base_url, path)
    }

    fn bridge_token(&self) -> &str {
        if self.auth_token.trim().is_empty() {
            &self.service_token
        } else {
            &self.auth_token
        }
    }

    fn raw_url(&self, path: &str, query: &[(String, String)]) -> String {
        let mut url = self.url(path);
        if !query.is_empty() {
            let qs: Vec<String> = query
                .iter()
                .map(|(k, v)| format!("{}={}", urlencoding::encode(k), urlencoding::encode(v)))
                .collect();
            url = format!("{}?{}", url, qs.join("&"));
        }
        url
    }

    async fn bridge_raw(
        &self,
        method: Method,
        path: &str,
        query: Vec<(String, String)>,
        body: Option<serde_json::Value>,
    ) -> Option<Result<crate::gateway::kumiho_bridge::BridgeResponse>> {
        crate::gateway::kumiho_bridge::send_raw(
            &self.client,
            method,
            path,
            query,
            self.bridge_token(),
            body,
        )
        .await
    }

    async fn bridge_json<T>(
        &self,
        method: Method,
        path: &str,
        query: Vec<(String, String)>,
        body: Option<serde_json::Value>,
    ) -> Option<Result<T>>
    where
        T: DeserializeOwned,
    {
        let response = self.bridge_raw(method, path, query, body).await?;
        Some(response.and_then(|raw| {
            serde_json::from_str::<T>(&raw.body).map_err(|e| KumihoError::Decode(e.to_string()))
        }))
    }

    async fn bridge_unit(
        &self,
        method: Method,
        path: &str,
        query: Vec<(String, String)>,
        body: Option<serde_json::Value>,
    ) -> Option<Result<()>> {
        let response = self.bridge_raw(method, path, query, body).await?;
        Some(response.map(|_| ()))
    }

    fn raw_with_stale_or_error(
        &self,
        url: &str,
        cache_token: &str,
        err: KumihoError,
    ) -> Result<RawKumihoResponse> {
        if raw_error_allows_stale(&err) {
            if let Some(cached) = cached_raw_response(url, cache_token, true) {
                return Ok(cached);
            }
        }
        Err(err)
    }

    /// Fetch a raw Kumiho REST JSON endpoint through the same client transport
    /// used by typed gateway routes.
    ///
    /// The local SDK bridge is attempted first, then hosted FastAPI is used as
    /// fallback. Retry, HTML trimming, account-scoped cache keys, stale cache
    /// fallback, and author display enrichment all live here so the generic
    /// `/api/kumiho/*` proxy does not duplicate Kumiho transport policy.
    pub async fn get_raw(
        &self,
        path: &str,
        params: &HashMap<String, String>,
    ) -> Result<RawKumihoResponse> {
        let path = format!("/{}", path.trim_start_matches('/'));
        let mut query: Vec<(String, String)> = params
            .iter()
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect();
        query.sort_by(|a, b| a.0.cmp(&b.0));

        let url = self.raw_url(&path, &query);
        let cache_token = self.bridge_token();
        if let Some(cached) = cached_raw_response(&url, cache_token, false) {
            return Ok(cached);
        }

        if let Some(result) = self
            .bridge_raw(Method::GET, &path, query.clone(), None)
            .await
        {
            match result {
                Ok(raw) => {
                    let body = enrich_success_body(raw.body);
                    set_cached_raw_response(&url, cache_token, raw.status, &body);
                    return Ok(RawKumihoResponse {
                        status: raw.status,
                        body,
                        transport: Some("sdk-bridge"),
                        cache_state: None,
                    });
                }
                Err(err) => return self.raw_with_stale_or_error(&url, cache_token, err),
            }
        }

        let resp = match self
            .send_with_retry(|| {
                self.client
                    .get(&url)
                    .header("X-Kumiho-Token", &self.service_token)
            })
            .await
        {
            Ok(resp) => resp,
            Err(err) => return self.raw_with_stale_or_error(&url, cache_token, err),
        };

        let resp = match self.check_response(resp).await {
            Ok(resp) => resp,
            Err(err) => return self.raw_with_stale_or_error(&url, cache_token, err),
        };
        let status = resp.status();
        let body = resp
            .text()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))?;
        let body = enrich_success_body(body);
        set_cached_raw_response(&url, cache_token, status, &body);
        Ok(RawKumihoResponse {
            status,
            body,
            transport: Some("fastapi"),
            cache_state: None,
        })
    }

    async fn check_response(&self, resp: reqwest::Response) -> Result<reqwest::Response> {
        let status = resp.status();
        if status.is_success() {
            Ok(resp)
        } else {
            let code = status.as_u16();
            let content_type = resp
                .headers()
                .get(reqwest::header::CONTENT_TYPE)
                .and_then(|v| v.to_str().ok())
                .map(str::to_owned);
            let body = resp.text().await.unwrap_or_default();
            let body = if looks_like_html_body(&body, content_type.as_deref()) {
                // Log once at warn so the full body is still debuggable from
                // the gateway logs, then trim it out of the in-memory error
                // before any caller can forward it to the dashboard.
                tracing::warn!(
                    status = code,
                    content_type = content_type.as_deref().unwrap_or(""),
                    body_preview = body.chars().take(256).collect::<String>(),
                    "Kumiho returned HTML error body (trimming before propagation)"
                );
                "<HTML error page — see gateway logs>".to_string()
            } else {
                body
            };
            Err(KumihoError::Api { status: code, body })
        }
    }

    /// Send a Kumiho request with bounded retries on transient upstream
    /// failures. The caller passes a closure that re-builds the
    /// `RequestBuilder` on each attempt, since `reqwest::RequestBuilder` is
    /// consumed by `.send()`.
    ///
    /// Retry policy (for safe, idempotent reads — see `send_no_retry` for
    /// POST/PUT/PATCH/DELETE write paths):
    /// - `reqwest::Error` (network / timeout / dropped connection): retry.
    /// - HTTP 502 / 503 / 504 / 520 / 522 / 524: retry.
    /// - Any other non-2xx (incl. 500/501): no retry, returned via
    ///   [`check_response`] as `KumihoError::Api`.
    /// - Up to 3 attempts. Delays: 500ms, 1500ms (jittered ±20%).
    /// - Each attempt is capped at `PER_ATTEMPT_TIMEOUT` (5s) — short enough
    ///   that 3 retries + backoff fit inside `TOTAL_BUDGET` (15s).
    /// - Total wall time across attempts is capped at `TOTAL_BUDGET`; if the
    ///   remaining budget is shorter than the next backoff, retries stop and
    ///   we surface `UpstreamUnavailable` immediately.
    ///
    /// On retry-budget exhaustion against a 5xx, returns
    /// [`KumihoError::UpstreamUnavailable`] rather than `Api { body: ... }`
    /// so the (potentially HTML) upstream body never propagates.
    async fn send_with_retry<F>(&self, build: F) -> Result<reqwest::Response>
    where
        F: Fn() -> reqwest::RequestBuilder,
    {
        self.send_with_retry_deadline(build, Instant::now() + TOTAL_BUDGET)
            .await
    }

    /// Variant of [`send_with_retry`] that shares a deadline with the caller.
    /// Used by methods that issue multiple retried calls (e.g.
    /// `get_published_or_latest`) so the combined wall time is still bounded
    /// by `TOTAL_BUDGET`, not 2× it.
    async fn send_with_retry_deadline<F>(
        &self,
        build: F,
        deadline: Instant,
    ) -> Result<reqwest::Response>
    where
        F: Fn() -> reqwest::RequestBuilder,
    {
        const MAX_ATTEMPTS: u32 = 3;
        const BASE_DELAYS_MS: [u64; 2] = [500, 1500];

        let mut last_status: Option<u16> = None;
        for attempt in 1..=MAX_ATTEMPTS {
            // Cap each attempt at the smaller of `PER_ATTEMPT_TIMEOUT` and the
            // remaining budget, so a hung upstream can't blow past the
            // end-to-end deadline.
            let now = Instant::now();
            if now >= deadline {
                break;
            }
            let attempt_cap = PER_ATTEMPT_TIMEOUT.min(deadline.saturating_duration_since(now));
            let attempt_request = build().timeout(attempt_cap);
            let result = attempt_request.send().await;
            match result {
                Ok(resp) => {
                    let status = resp.status().as_u16();
                    if is_retryable_status(status) {
                        last_status = Some(status);
                        if attempt < MAX_ATTEMPTS {
                            let delay_ms = BASE_DELAYS_MS[(attempt - 1) as usize];
                            if !deadline_allows(deadline, delay_ms) {
                                drop(resp);
                                break;
                            }
                            tracing::warn!(
                                attempt = attempt,
                                max_attempts = MAX_ATTEMPTS,
                                upstream_status = status,
                                "Kumiho returned transient 5xx; retrying"
                            );
                            drop(resp);
                            sleep_with_jitter(delay_ms).await;
                            continue;
                        }
                        // Final attempt still returned a retryable 5xx — drop
                        // the (likely HTML) body and surface a clean error.
                        drop(resp);
                        break;
                    }
                    return Ok(resp);
                }
                Err(e) => {
                    if attempt < MAX_ATTEMPTS {
                        let delay_ms = BASE_DELAYS_MS[(attempt - 1) as usize];
                        if !deadline_allows(deadline, delay_ms) {
                            return Err(KumihoError::Unreachable(e));
                        }
                        tracing::warn!(
                            attempt = attempt,
                            max_attempts = MAX_ATTEMPTS,
                            error = %e,
                            "Kumiho request failed (network); retrying"
                        );
                        sleep_with_jitter(delay_ms).await;
                        continue;
                    }
                    return Err(KumihoError::Unreachable(e));
                }
            }
        }

        // Budget exhausted on a retryable status. Drop the body — it's almost
        // certainly the upstream HTML splash page we don't want to forward.
        Err(KumihoError::UpstreamUnavailable {
            status: last_status.unwrap_or(502),
            attempts: MAX_ATTEMPTS,
        })
    }

    /// Single-attempt send used by write methods (POST/PUT/PATCH/DELETE).
    /// We deliberately skip the retry loop because Kumiho's API does NOT
    /// honour idempotency keys; retrying a create that succeeded but whose
    /// response was dropped (or rewritten to 502 by a CDN) would create a
    /// duplicate item. Prefer a clean error over a duplicate-write race.
    ///
    /// Still trims HTML bodies via `check_response`, and still surfaces
    /// retryable 5xx as `UpstreamUnavailable` (so the central mapper returns
    /// the same 503 shape as for retried paths).
    async fn send_no_retry<F>(&self, build: F) -> Result<reqwest::Response>
    where
        F: FnOnce() -> reqwest::RequestBuilder,
    {
        let result = build().timeout(PER_ATTEMPT_TIMEOUT).send().await;
        match result {
            Ok(resp) => {
                let status = resp.status().as_u16();
                if is_retryable_status(status) {
                    // Drop the body — it's almost certainly the upstream HTML
                    // splash page we don't want to forward.
                    drop(resp);
                    return Err(KumihoError::UpstreamUnavailable {
                        status,
                        attempts: 1,
                    });
                }
                Ok(resp)
            }
            Err(e) => Err(KumihoError::Unreachable(e)),
        }
    }

    // ── Project management ─────────────────────────────────────────

    /// Ensure a project exists (idempotent).  Ignores 409 Conflict (already exists).
    pub async fn ensure_project(&self, project_name: &str) -> Result<()> {
        let body = CreateProjectBody {
            name: project_name.to_string(),
            description: None,
        };

        if let Some(result) = self
            .bridge_unit(
                Method::POST,
                "/projects",
                Vec::new(),
                Some(serde_json::to_value(&body).unwrap_or_default()),
            )
            .await
        {
            match result {
                Ok(()) => return Ok(()),
                Err(KumihoError::Api { status: 409, .. }) => return Ok(()),
                Err(e) => return Err(e),
            }
        }

        let resp = self
            .send_no_retry(|| {
                self.client
                    .post(self.url("/projects"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .json(&body)
            })
            .await?;

        let status = resp.status().as_u16();
        if resp.status().is_success() || status == 409 {
            Ok(())
        } else {
            // Defer to check_response so HTML bodies get trimmed here too.
            let _ = self.check_response(resp).await?;
            Ok(())
        }
    }

    // ── Space management ────────────────────────────────────────────

    /// Ensure a space exists (idempotent).  Ignores 409 Conflict (already exists).
    pub async fn ensure_space(&self, project: &str, space_name: &str) -> Result<()> {
        let body = CreateSpaceBody {
            parent_path: format!("/{project}"),
            name: space_name.to_string(),
        };

        if let Some(result) = self
            .bridge_unit(
                Method::POST,
                "/spaces",
                Vec::new(),
                Some(serde_json::to_value(&body).unwrap_or_default()),
            )
            .await
        {
            match result {
                Ok(()) => return Ok(()),
                Err(KumihoError::Api { status: 409, .. }) => return Ok(()),
                Err(e) => return Err(e),
            }
        }

        let resp = self
            .send_no_retry(|| {
                self.client
                    .post(self.url("/spaces"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .json(&body)
            })
            .await?;

        let status = resp.status().as_u16();
        // 409 = already exists — that's fine
        if resp.status().is_success() || status == 409 {
            Ok(())
        } else {
            let _ = self.check_response(resp).await?;
            Ok(())
        }
    }

    /// Ensure a nested space exists under a parent (idempotent).
    pub async fn ensure_child_space(
        &self,
        _project: &str,
        parent_path: &str,
        space_name: &str,
    ) -> Result<()> {
        let body = CreateSpaceBody {
            parent_path: parent_path.to_string(),
            name: space_name.to_string(),
        };

        if let Some(result) = self
            .bridge_unit(
                Method::POST,
                "/spaces",
                Vec::new(),
                Some(serde_json::to_value(&body).unwrap_or_default()),
            )
            .await
        {
            match result {
                Ok(()) => return Ok(()),
                Err(KumihoError::Api { status: 409, .. }) => return Ok(()),
                Err(e) => return Err(e),
            }
        }

        let resp = self
            .send_no_retry(|| {
                self.client
                    .post(self.url("/spaces"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .json(&body)
            })
            .await?;

        let status = resp.status().as_u16();
        if resp.status().is_success() || status == 409 {
            Ok(())
        } else {
            let _ = self.check_response(resp).await?;
            Ok(())
        }
    }

    /// List spaces under a parent path (optionally recursive).
    pub async fn list_spaces(
        &self,
        parent_path: &str,
        recursive: bool,
    ) -> Result<Vec<SpaceResponse>> {
        if let Some(result) = self
            .bridge_json(
                Method::GET,
                "/spaces",
                vec![
                    ("parent_path".to_string(), parent_path.to_string()),
                    ("recursive".to_string(), recursive.to_string()),
                ],
                None,
            )
            .await
        {
            return result;
        }

        let resp = self
            .send_with_retry(|| {
                self.client
                    .get(self.url("/spaces"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .query(&[
                        ("parent_path", parent_path),
                        ("recursive", if recursive { "true" } else { "false" }),
                    ])
            })
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<Vec<SpaceResponse>>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    // ── Item CRUD ───────────────────────────────────────────────────

    /// List items in a space.
    pub async fn list_items(
        &self,
        space_path: &str,
        include_deprecated: bool,
    ) -> Result<Vec<ItemResponse>> {
        self.list_items_paged(space_path, include_deprecated, 100, 0)
            .await
    }

    /// List items with explicit pagination.
    pub async fn list_items_paged(
        &self,
        space_path: &str,
        include_deprecated: bool,
        limit: u32,
        offset: u32,
    ) -> Result<Vec<ItemResponse>> {
        let limit_s = limit.to_string();
        let offset_s = offset.to_string();
        if let Some(result) = self
            .bridge_json(
                Method::GET,
                "/items",
                vec![
                    ("space_path".to_string(), space_path.to_string()),
                    (
                        "include_deprecated".to_string(),
                        include_deprecated.to_string(),
                    ),
                    ("limit".to_string(), limit_s.clone()),
                    ("offset".to_string(), offset_s.clone()),
                ],
                None,
            )
            .await
        {
            return result;
        }

        let resp = self
            .send_with_retry(|| {
                self.client
                    .get(self.url("/items"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .query(&[
                        ("space_path", space_path),
                        (
                            "include_deprecated",
                            if include_deprecated { "true" } else { "false" },
                        ),
                        ("limit", limit_s.as_str()),
                        ("offset", offset_s.as_str()),
                    ])
            })
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<Vec<ItemResponse>>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// List items in a space filtered by name substring.
    ///
    /// Uses the `name_filter` query parameter to reduce result size,
    /// staying under Kumiho's gRPC message limit for large spaces.
    pub async fn list_items_filtered(
        &self,
        space_path: &str,
        name_filter: &str,
        include_deprecated: bool,
    ) -> Result<Vec<ItemResponse>> {
        if let Some(result) = self
            .bridge_json(
                Method::GET,
                "/items",
                vec![
                    ("space_path".to_string(), space_path.to_string()),
                    ("name_filter".to_string(), name_filter.to_string()),
                    (
                        "include_deprecated".to_string(),
                        include_deprecated.to_string(),
                    ),
                ],
                None,
            )
            .await
        {
            return result;
        }

        let resp = self
            .send_with_retry(|| {
                self.client
                    .get(self.url("/items"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .query(&[
                        ("space_path", space_path),
                        ("name_filter", name_filter),
                        (
                            "include_deprecated",
                            if include_deprecated { "true" } else { "false" },
                        ),
                    ])
            })
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<Vec<ItemResponse>>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// Create an item.
    pub async fn create_item(
        &self,
        space_path: &str,
        item_name: &str,
        kind: &str,
        metadata: HashMap<String, String>,
    ) -> Result<ItemResponse> {
        let body = CreateItemBody {
            space_path: space_path.to_string(),
            item_name: item_name.to_string(),
            kind: kind.to_string(),
            metadata,
        };

        if let Some(result) = self
            .bridge_json(
                Method::POST,
                "/items",
                Vec::new(),
                Some(serde_json::to_value(&body).unwrap_or_default()),
            )
            .await
        {
            return result;
        }

        let resp = self
            .send_no_retry(|| {
                self.client
                    .post(self.url("/items"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .json(&body)
            })
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<ItemResponse>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// Deprecate or restore an item.
    pub async fn deprecate_item(&self, kref: &str, deprecated: bool) -> Result<ItemResponse> {
        let item_kref = item_kref_without_selectors(kref);
        if let Some(result) = self
            .bridge_json(
                Method::POST,
                "/items/deprecate",
                vec![
                    ("kref".to_string(), item_kref.to_string()),
                    ("deprecated".to_string(), deprecated.to_string()),
                ],
                None,
            )
            .await
        {
            return result;
        }

        let resp = self
            .send_no_retry(|| {
                self.client
                    .post(self.url("/items/deprecate"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .query(&[
                        ("kref", item_kref),
                        ("deprecated", if deprecated { "true" } else { "false" }),
                    ])
            })
            .await?;

        let resp = match self.check_response(resp).await {
            Ok(resp) => resp,
            Err(KumihoError::Api { status, .. }) if status == 404 && deprecated => {
                self.delete_item_with_force(item_kref, false).await?;
                return self.get_item_by_kref(item_kref).await;
            }
            Err(e) => return Err(e),
        };
        resp.json::<ItemResponse>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// Get an item by kref.
    pub async fn get_item_by_kref(&self, kref: &str) -> Result<ItemResponse> {
        let item_kref = item_kref_without_selectors(kref);
        if let Some(result) = self
            .bridge_json(
                Method::GET,
                "/items/by-kref",
                vec![("kref".to_string(), item_kref.to_string())],
                None,
            )
            .await
        {
            return result;
        }

        let resp = self
            .send_with_retry(|| {
                self.client
                    .get(self.url("/items/by-kref"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .query(&[("kref", item_kref)])
            })
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<ItemResponse>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// Delete an item (force).
    pub async fn delete_item(&self, kref: &str) -> Result<()> {
        self.delete_item_with_force(kref, true).await
    }

    async fn delete_item_with_force(&self, kref: &str, force: bool) -> Result<()> {
        let item_kref = item_kref_without_selectors(kref);
        if let Some(result) = self
            .bridge_unit(
                Method::DELETE,
                "/items/by-kref",
                vec![
                    ("kref".to_string(), item_kref.to_string()),
                    ("force".to_string(), force.to_string()),
                ],
                None,
            )
            .await
        {
            return result;
        }

        let resp = self
            .send_no_retry(|| {
                self.client
                    .delete(self.url("/items/by-kref"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .query(&[
                        ("kref", item_kref),
                        ("force", if force { "true" } else { "false" }),
                    ])
            })
            .await?;

        let _ = self.check_response(resp).await?;
        Ok(())
    }

    /// Full-text search across items.
    pub async fn search_items(
        &self,
        query: &str,
        context: &str,
        kind: &str,
        include_deprecated: bool,
    ) -> Result<Vec<SearchResult>> {
        if let Some(result) = self
            .bridge_json(
                Method::GET,
                "/items/fulltext-search",
                vec![
                    ("query".to_string(), query.to_string()),
                    ("context".to_string(), context.to_string()),
                    ("kind".to_string(), kind.to_string()),
                    (
                        "include_deprecated".to_string(),
                        include_deprecated.to_string(),
                    ),
                ],
                None,
            )
            .await
        {
            return result;
        }

        let resp = self
            .send_with_retry(|| {
                self.client
                    .get(self.url("/items/fulltext-search"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .query(&[
                        ("query", query),
                        ("context", context),
                        ("kind", kind),
                        (
                            "include_deprecated",
                            if include_deprecated { "true" } else { "false" },
                        ),
                    ])
            })
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<Vec<SearchResult>>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    // ── Revisions ───────────────────────────────────────────────────

    /// Create a new revision on an item.
    pub async fn create_revision(
        &self,
        item_kref: &str,
        metadata: HashMap<String, String>,
    ) -> Result<RevisionResponse> {
        let body = CreateRevisionBody {
            item_kref: item_kref.to_string(),
            metadata,
        };

        if let Some(result) = self
            .bridge_json(
                Method::POST,
                "/revisions",
                Vec::new(),
                Some(serde_json::to_value(&body).unwrap_or_default()),
            )
            .await
        {
            return result;
        }

        let resp = self
            .send_no_retry(|| {
                self.client
                    .post(self.url("/revisions"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .json(&body)
            })
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<RevisionResponse>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// List all revisions for an item, ordered by number.
    ///
    /// Backed by `GET /api/v1/revisions?item_kref=...` on Kumiho. Used by the
    /// editor's revision-history strip (Architect feature).
    pub async fn list_item_revisions(&self, item_kref: &str) -> Result<Vec<RevisionResponse>> {
        if let Some(result) = self
            .bridge_json(
                Method::GET,
                "/revisions",
                vec![("item_kref".to_string(), item_kref.to_string())],
                None,
            )
            .await
        {
            return result;
        }

        let resp = self
            .send_with_retry(|| {
                self.client
                    .get(self.url("/revisions"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .query(&[("item_kref", item_kref)])
            })
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<Vec<RevisionResponse>>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// Tag a revision (e.g. "published").
    pub async fn tag_revision(&self, revision_kref: &str, tag: &str) -> Result<()> {
        let body = serde_json::json!({ "tag": tag });
        if let Some(result) = self
            .bridge_unit(
                Method::POST,
                "/revisions/tags",
                vec![("kref".to_string(), revision_kref.to_string())],
                Some(body.clone()),
            )
            .await
        {
            return result;
        }

        let resp = self
            .send_no_retry(|| {
                self.client
                    .post(self.url("/revisions/tags"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .query(&[("kref", revision_kref)])
                    .json(&body)
            })
            .await?;

        let _ = self.check_response(resp).await?;
        Ok(())
    }

    /// Deprecate or restore a revision.
    pub async fn deprecate_revision(
        &self,
        revision_kref: &str,
        deprecated: bool,
    ) -> Result<RevisionResponse> {
        if let Some(result) = self
            .bridge_json(
                Method::POST,
                "/revisions/deprecate",
                vec![
                    ("kref".to_string(), revision_kref.to_string()),
                    ("deprecated".to_string(), deprecated.to_string()),
                ],
                None,
            )
            .await
        {
            return result;
        }

        let resp = self
            .send_no_retry(|| {
                self.client
                    .post(self.url("/revisions/deprecate"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .query(&[
                        ("kref", revision_kref),
                        ("deprecated", if deprecated { "true" } else { "false" }),
                    ])
            })
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<RevisionResponse>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// Get a revision by tag (e.g. "published").
    pub async fn get_revision_by_tag(
        &self,
        item_kref: &str,
        tag: &str,
    ) -> Result<RevisionResponse> {
        if let Some(result) = self
            .bridge_json(
                Method::GET,
                "/revisions/by-kref",
                vec![
                    ("kref".to_string(), item_kref.to_string()),
                    ("t".to_string(), tag.to_string()),
                ],
                None,
            )
            .await
        {
            return result;
        }

        let resp = self
            .send_with_retry(|| {
                self.client
                    .get(self.url("/revisions/by-kref"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .query(&[("kref", item_kref), ("t", tag)])
            })
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<RevisionResponse>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// Get a specific revision by its own revision_kref (e.g. "…?r=5").
    /// The Kumiho server's `/revisions/by-kref` endpoint parses the `?r=N`
    /// suffix out of the kref and returns that exact revision's metadata.
    pub async fn get_revision(&self, revision_kref: &str) -> Result<RevisionResponse> {
        if let Some(result) = self
            .bridge_json(
                Method::GET,
                "/revisions/by-kref",
                vec![("kref".to_string(), revision_kref.to_string())],
                None,
            )
            .await
        {
            return result;
        }

        let resp = self
            .send_with_retry(|| {
                self.client
                    .get(self.url("/revisions/by-kref"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .query(&[("kref", revision_kref)])
            })
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<RevisionResponse>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// Get the latest revision for an item.
    pub async fn get_latest_revision(&self, item_kref: &str) -> Result<RevisionResponse> {
        if let Some(result) = self
            .bridge_json(
                Method::GET,
                "/revisions/latest",
                vec![("item_kref".to_string(), item_kref.to_string())],
                None,
            )
            .await
        {
            return result;
        }

        let resp = self
            .send_with_retry(|| {
                self.client
                    .get(self.url("/revisions/latest"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .query(&[("item_kref", item_kref)])
            })
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<RevisionResponse>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// Get the published revision, falling back to latest.
    ///
    /// Both inner calls share ONE retry budget rather than each getting their
    /// own — otherwise a degraded upstream could hold a single gateway
    /// request open for ~2× `TOTAL_BUDGET`.
    pub async fn get_published_or_latest(&self, item_kref: &str) -> Result<RevisionResponse> {
        if let Ok(revision) = self.get_revision_by_tag(item_kref, "published").await {
            return Ok(revision);
        }
        if let Some(result) = self
            .bridge_json(
                Method::GET,
                "/revisions/latest",
                vec![("item_kref".to_string(), item_kref.to_string())],
                None,
            )
            .await
        {
            return result;
        }

        let deadline = Instant::now() + TOTAL_BUDGET;
        let by_tag = self
            .send_with_retry_deadline(
                || {
                    self.client
                        .get(self.url("/revisions/by-kref"))
                        .header("X-Kumiho-Token", &self.service_token)
                        .query(&[("kref", item_kref), ("t", "published")])
                },
                deadline,
            )
            .await;
        match by_tag {
            Ok(resp) => {
                let resp = self.check_response(resp).await?;
                resp.json::<RevisionResponse>()
                    .await
                    .map_err(|e| KumihoError::Decode(e.to_string()))
            }
            Err(_) => {
                let resp = self
                    .send_with_retry_deadline(
                        || {
                            self.client
                                .get(self.url("/revisions/latest"))
                                .header("X-Kumiho-Token", &self.service_token)
                                .query(&[("item_kref", item_kref)])
                        },
                        deadline,
                    )
                    .await?;
                let resp = self.check_response(resp).await?;
                resp.json::<RevisionResponse>()
                    .await
                    .map_err(|e| KumihoError::Decode(e.to_string()))
            }
        }
    }

    /// Batch fetch revisions for multiple items by tag in a single HTTP call.
    ///
    /// Returns a map of item_kref → RevisionResponse for items that were found.
    pub async fn batch_get_revisions(
        &self,
        item_krefs: &[String],
        tag: &str,
    ) -> Result<HashMap<String, RevisionResponse>> {
        if item_krefs.is_empty() {
            return Ok(HashMap::new());
        }

        let body = serde_json::json!({
            "item_krefs": item_krefs,
            "tag": tag,
            "allow_partial": true,
        });

        if let Some(result) = self
            .bridge_json::<BatchRevisionsResponse>(
                Method::POST,
                "/revisions/batch",
                Vec::new(),
                Some(body.clone()),
            )
            .await
        {
            let batch = result?;
            let mut map = HashMap::with_capacity(batch.revisions.len());
            for rev in batch.revisions {
                map.insert(rev.item_kref.clone(), rev);
            }
            return Ok(map);
        }

        // POST used as a read (batch fetch by body). This endpoint is
        // side-effect free, so retry it like other reads; otherwise a single
        // transient CDN/reset event can make workflow and asset pages look
        // disconnected even though the follow-up attempt would succeed.
        let resp = self
            .send_with_retry(|| {
                self.client
                    .post(self.url("/revisions/batch"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .json(&body)
            })
            .await?;

        let resp = self.check_response(resp).await?;
        let batch: BatchRevisionsResponse = resp
            .json()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))?;

        let mut map = HashMap::with_capacity(batch.revisions.len());
        for rev in batch.revisions {
            map.insert(rev.item_kref.clone(), rev);
        }
        Ok(map)
    }

    // ── Skill convenience methods ──────────────────────────────────

    /// List skills in the given project's Skills space.
    pub async fn list_skills(
        &self,
        project: &str,
        include_deprecated: bool,
    ) -> Result<Vec<ItemResponse>> {
        let space_path = format!("/{project}/Skills");
        self.list_items(&space_path, include_deprecated).await
    }

    /// Search skills by query within the given project.
    ///
    /// Searches both the canonical [`crate::skills::registration::SKILL_ITEM_KIND`]
    /// and the legacy [`crate::skills::registration::LEGACY_SKILL_ITEM_KIND`]
    /// so items created before the kind rename remain discoverable.
    /// Results from the two queries are unioned and de-duplicated by
    /// `item.kref`; on a successful new-kind query, a failure on the
    /// legacy query is logged + ignored (the new-kind results are still
    /// returned).
    pub async fn search_skills(
        &self,
        query: &str,
        project: &str,
        include_deprecated: bool,
    ) -> Result<Vec<SearchResult>> {
        self.search_items_with_legacy(
            query,
            project,
            crate::skills::registration::SKILL_ITEM_KIND,
            crate::skills::registration::LEGACY_SKILL_ITEM_KIND,
            include_deprecated,
        )
        .await
    }

    /// Run two `search_items` queries (one per kind) and union the
    /// results by `item.kref`.  On `legacy` failure we log + return the
    /// `primary` results; `primary` failures bubble up as before.
    ///
    /// Used for the skill-kind transition (`skill` ↔ `skilldef`); kept
    /// generic so the same shape can serve other kind renames.
    pub async fn search_items_with_legacy(
        &self,
        query: &str,
        context: &str,
        primary: &str,
        legacy: &str,
        include_deprecated: bool,
    ) -> Result<Vec<SearchResult>> {
        let primary_results = self
            .search_items(query, context, primary, include_deprecated)
            .await?;
        let legacy_results = match self
            .search_items(query, context, legacy, include_deprecated)
            .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::warn!(
                    primary = primary,
                    legacy = legacy,
                    context = context,
                    error = ?e,
                    "search_items_with_legacy: legacy-kind query failed; \
                     returning primary results only",
                );
                Vec::new()
            }
        };

        let mut seen: std::collections::HashSet<String> =
            std::collections::HashSet::with_capacity(primary_results.len() + legacy_results.len());
        let mut merged: Vec<SearchResult> =
            Vec::with_capacity(primary_results.len() + legacy_results.len());
        for r in primary_results.into_iter().chain(legacy_results) {
            if seen.insert(r.item.kref.clone()) {
                merged.push(r);
            }
        }
        Ok(merged)
    }

    /// Create a new skill item + first revision in the given project.
    pub async fn create_skill(
        &self,
        project: &str,
        name: &str,
        metadata: HashMap<String, String>,
    ) -> Result<(ItemResponse, RevisionResponse)> {
        self.ensure_space(project, "Skills").await.ok();
        let space_path = format!("/{project}/Skills");
        let item = self
            .create_item(
                &space_path,
                name,
                crate::skills::registration::SKILL_ITEM_KIND,
                HashMap::new(),
            )
            .await?;
        let revision = self.create_revision(&item.kref, metadata).await?;
        Ok((item, revision))
    }

    /// Deprecate or restore a skill.
    pub async fn deprecate_skill(&self, kref: &str, deprecated: bool) -> Result<ItemResponse> {
        self.deprecate_item(kref, deprecated).await
    }

    // ── Bundle methods ─────────────────────────────────────────────

    /// Create a bundle.
    pub async fn create_bundle(
        &self,
        space_path: &str,
        bundle_name: &str,
        metadata: HashMap<String, String>,
    ) -> Result<ItemResponse> {
        let body = CreateBundleBody {
            space_path: space_path.to_string(),
            bundle_name: bundle_name.to_string(),
            metadata,
        };

        if let Some(result) = self
            .bridge_json(
                Method::POST,
                "/bundles",
                Vec::new(),
                Some(serde_json::to_value(&body).unwrap_or_default()),
            )
            .await
        {
            return result;
        }

        let resp = self
            .send_no_retry(|| {
                self.client
                    .post(self.url("/bundles"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .json(&body)
            })
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<ItemResponse>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// Get a bundle by kref.
    pub async fn get_bundle(&self, kref: &str) -> Result<ItemResponse> {
        if let Some(result) = self
            .bridge_json(
                Method::GET,
                "/bundles/by-kref",
                vec![("kref".to_string(), kref.to_string())],
                None,
            )
            .await
        {
            return result;
        }

        let resp = self
            .send_with_retry(|| {
                self.client
                    .get(self.url("/bundles/by-kref"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .query(&[("kref", kref)])
            })
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<ItemResponse>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// Delete a bundle (force).
    pub async fn delete_bundle(&self, kref: &str) -> Result<()> {
        if let Some(result) = self
            .bridge_unit(
                Method::DELETE,
                "/bundles/by-kref",
                vec![
                    ("kref".to_string(), kref.to_string()),
                    ("force".to_string(), "true".to_string()),
                ],
                None,
            )
            .await
        {
            return result;
        }

        let resp = self
            .send_no_retry(|| {
                self.client
                    .delete(self.url("/bundles/by-kref"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .query(&[("kref", kref), ("force", "true")])
            })
            .await?;

        let _ = self.check_response(resp).await?;
        Ok(())
    }

    /// Add a member to a bundle.
    pub async fn add_bundle_member(
        &self,
        bundle_kref: &str,
        item_kref: &str,
        metadata: HashMap<String, String>,
    ) -> Result<serde_json::Value> {
        let body = BundleMemberBody {
            bundle_kref: bundle_kref.to_string(),
            item_kref: item_kref.to_string(),
            metadata: if metadata.is_empty() {
                None
            } else {
                Some(metadata)
            },
        };

        if let Some(result) = self
            .bridge_json(
                Method::POST,
                "/bundles/members/add",
                Vec::new(),
                Some(serde_json::to_value(&body).unwrap_or_default()),
            )
            .await
        {
            return result;
        }

        let resp = self
            .send_no_retry(|| {
                self.client
                    .post(self.url("/bundles/members/add"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .json(&body)
            })
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<serde_json::Value>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// Remove a member from a bundle.
    pub async fn remove_bundle_member(
        &self,
        bundle_kref: &str,
        item_kref: &str,
    ) -> Result<serde_json::Value> {
        let body = RemoveBundleMemberBody {
            bundle_kref: bundle_kref.to_string(),
            item_kref: item_kref.to_string(),
        };

        if let Some(result) = self
            .bridge_json(
                Method::POST,
                "/bundles/members/remove",
                Vec::new(),
                Some(serde_json::to_value(&body).unwrap_or_default()),
            )
            .await
        {
            return result;
        }

        let resp = self
            .send_no_retry(|| {
                self.client
                    .post(self.url("/bundles/members/remove"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .json(&body)
            })
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<serde_json::Value>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// List members of a bundle.
    pub async fn list_bundle_members(&self, bundle_kref: &str) -> Result<BundleMembersResponse> {
        if let Some(result) = self
            .bridge_json(
                Method::GET,
                "/bundles/members",
                vec![("bundle_kref".to_string(), bundle_kref.to_string())],
                None,
            )
            .await
        {
            return result;
        }

        let resp = self
            .send_with_retry(|| {
                self.client
                    .get(self.url("/bundles/members"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .query(&[("bundle_kref", bundle_kref)])
            })
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<BundleMembersResponse>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    // ── Edge methods ───────────────────────────────────────────────

    /// Create an edge between two revisions.
    pub async fn create_edge(
        &self,
        source_kref: &str,
        target_kref: &str,
        edge_type: &str,
        metadata: HashMap<String, String>,
    ) -> Result<EdgeResponse> {
        let body = CreateEdgeBody {
            source_revision_kref: source_kref.to_string(),
            target_revision_kref: target_kref.to_string(),
            edge_type: edge_type.to_string(),
            metadata,
        };

        if let Some(result) = self
            .bridge_json(
                Method::POST,
                "/edges",
                Vec::new(),
                Some(serde_json::to_value(&body).unwrap_or_default()),
            )
            .await
        {
            return result;
        }

        let resp = self
            .send_no_retry(|| {
                self.client
                    .post(self.url("/edges"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .json(&body)
            })
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<EdgeResponse>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// List edges for a revision.
    ///
    /// `direction`: 0 = outgoing, 1 = incoming, 2 = both.
    pub async fn list_edges(
        &self,
        revision_kref: &str,
        edge_type: Option<&str>,
        direction: Option<&str>,
    ) -> Result<Vec<EdgeResponse>> {
        // Map string directions to numeric values expected by Kumiho API
        let dir_num = direction.map(|d| match d {
            "outgoing" | "out" => "0",
            "incoming" | "in" => "1",
            "both" => "2",
            other => other, // pass through if already numeric
        });

        let mut query_params: Vec<(&str, &str)> = vec![("kref", revision_kref)];
        if let Some(et) = edge_type {
            query_params.push(("edge_type", et));
        }
        if let Some(dir) = dir_num.as_deref() {
            query_params.push(("direction", dir));
        }

        let bridge_query = query_params
            .iter()
            .map(|(key, value)| ((*key).to_string(), (*value).to_string()))
            .collect::<Vec<_>>();
        if let Some(result) = self
            .bridge_json(Method::GET, "/edges", bridge_query, None)
            .await
        {
            return result;
        }

        let resp = self
            .send_with_retry(|| {
                self.client
                    .get(self.url("/edges"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .query(&query_params)
            })
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<Vec<EdgeResponse>>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// Delete an edge.
    pub async fn delete_edge(
        &self,
        source_kref: &str,
        target_kref: &str,
        edge_type: &str,
    ) -> Result<()> {
        if let Some(result) = self
            .bridge_unit(
                Method::DELETE,
                "/edges",
                vec![
                    ("source_kref".to_string(), source_kref.to_string()),
                    ("target_kref".to_string(), target_kref.to_string()),
                    ("edge_type".to_string(), edge_type.to_string()),
                ],
                None,
            )
            .await
        {
            return result;
        }

        let resp = self
            .send_no_retry(|| {
                self.client
                    .delete(self.url("/edges"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .query(&[
                        ("source_kref", source_kref),
                        ("target_kref", target_kref),
                        ("edge_type", edge_type),
                    ])
            })
            .await?;

        let _ = self.check_response(resp).await?;
        Ok(())
    }

    // ── Artifact methods ──────────────────────────────────────────

    /// Create an artifact associated with a revision.
    pub async fn create_artifact(
        &self,
        revision_kref: &str,
        name: &str,
        location: &str,
        metadata: HashMap<String, String>,
    ) -> Result<ArtifactResponse> {
        let body = CreateArtifactBody {
            revision_kref: revision_kref.to_string(),
            name: name.to_string(),
            location: location.to_string(),
            metadata,
        };

        if let Some(result) = self
            .bridge_json(
                Method::POST,
                "/artifacts",
                Vec::new(),
                Some(serde_json::to_value(&body).unwrap_or_default()),
            )
            .await
        {
            return result;
        }

        let resp = self
            .send_no_retry(|| {
                self.client
                    .post(self.url("/artifacts"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .json(&body)
            })
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<ArtifactResponse>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// List artifacts for a revision.
    pub async fn get_artifacts(&self, revision_kref: &str) -> Result<Vec<ArtifactResponse>> {
        if let Some(result) = self
            .bridge_json(
                Method::GET,
                "/artifacts",
                vec![("revision_kref".to_string(), revision_kref.to_string())],
                None,
            )
            .await
        {
            return result;
        }

        let resp = self
            .send_with_retry(|| {
                self.client
                    .get(self.url("/artifacts"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .query(&[("revision_kref", revision_kref)])
            })
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<Vec<ArtifactResponse>>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// Find artifacts by their stored location.
    pub async fn get_artifacts_by_location(&self, location: &str) -> Result<Vec<ArtifactResponse>> {
        if let Some(result) = self
            .bridge_json(
                Method::GET,
                "/artifacts/by-location",
                vec![("location".to_string(), location.to_string())],
                None,
            )
            .await
        {
            return result;
        }

        let resp = self
            .send_with_retry(|| {
                self.client
                    .get(self.url("/artifacts/by-location"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .query(&[("location", location)])
            })
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<Vec<ArtifactResponse>>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// Get a specific artifact by revision kref and name.
    pub async fn get_artifact_by_name(
        &self,
        revision_kref: &str,
        name: &str,
    ) -> Result<ArtifactResponse> {
        if let Some(result) = self
            .bridge_json(
                Method::GET,
                "/artifacts/by-kref",
                vec![
                    ("revision_kref".to_string(), revision_kref.to_string()),
                    ("name".to_string(), name.to_string()),
                ],
                None,
            )
            .await
        {
            return result;
        }

        let resp = self
            .send_with_retry(|| {
                self.client
                    .get(self.url("/artifacts/by-kref"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .query(&[("revision_kref", revision_kref), ("name", name)])
            })
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<ArtifactResponse>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// Get a specific artifact by artifact kref.
    pub async fn get_artifact(&self, artifact_kref: &str) -> Result<ArtifactResponse> {
        if let Some(result) = self
            .bridge_json(
                Method::GET,
                "/artifacts/by-kref",
                vec![("kref".to_string(), artifact_kref.to_string())],
                None,
            )
            .await
        {
            return result;
        }

        let resp = self
            .send_with_retry(|| {
                self.client
                    .get(self.url("/artifacts/by-kref"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .query(&[("kref", artifact_kref)])
            })
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<ArtifactResponse>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// Deprecate or restore an artifact.
    pub async fn deprecate_artifact(
        &self,
        artifact_kref: &str,
        deprecated: bool,
    ) -> Result<ArtifactResponse> {
        if let Some(result) = self
            .bridge_json(
                Method::POST,
                "/artifacts/deprecate",
                vec![
                    ("kref".to_string(), artifact_kref.to_string()),
                    ("deprecated".to_string(), deprecated.to_string()),
                ],
                None,
            )
            .await
        {
            return result;
        }

        let resp = self
            .send_no_retry(|| {
                self.client
                    .post(self.url("/artifacts/deprecate"))
                    .header("X-Kumiho-Token", &self.service_token)
                    .query(&[
                        ("kref", artifact_kref),
                        ("deprecated", if deprecated { "true" } else { "false" }),
                    ])
            })
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<ArtifactResponse>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    // ── Team convenience methods ───────────────────────────────────

    /// List teams in the given `<project>/Teams` space.
    pub async fn list_teams_in(
        &self,
        space_path: &str,
        include_deprecated: bool,
    ) -> Result<Vec<ItemResponse>> {
        self.list_items(space_path, include_deprecated).await
    }

    /// Deprecate or restore a team.
    pub async fn deprecate_team(&self, kref: &str, deprecated: bool) -> Result<()> {
        self.deprecate_item(kref, deprecated).await?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    //! Tests for retry behaviour and HTML-body trimming. The goal is to lock
    //! in three guarantees the dashboard depends on:
    //!
    //!   1. Transient gateway 5xx (Cloudflare 502/503/504/52x) is retried up
    //!      to 3 times with backoff, then surfaces as `UpstreamUnavailable`
    //!      (NOT `Api`), so upstream HTML never reaches the gateway response.
    //!   2. Non-retryable status codes (4xx / 500 / 501) fail immediately.
    //!   3. HTML response bodies on any non-2xx are replaced with a short
    //!      placeholder before becoming part of `KumihoError::Api`.
    use super::*;
    use std::sync::Arc;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use wiremock::matchers::{method, path, query_param};
    use wiremock::{Mock, MockServer, Respond, ResponseTemplate};

    /// Counts each request hit so tests can assert retry-attempt totals.
    struct CountingResponder {
        responses: Vec<ResponseTemplate>,
        counter: Arc<AtomicUsize>,
    }

    impl Respond for CountingResponder {
        fn respond(&self, _request: &wiremock::Request) -> ResponseTemplate {
            let idx = self.counter.fetch_add(1, Ordering::SeqCst);
            let last = self.responses.len() - 1;
            self.responses[idx.min(last)].clone()
        }
    }

    fn make_client(base_url: &str) -> KumihoClient {
        KumihoClient::new(base_url.to_string(), "test-token".to_string())
    }

    fn make_raw_client(base_url: &str) -> KumihoClient {
        // Empty token keeps the local SDK bridge disabled for this unit test;
        // the raw proxy path still exercises hosted FastAPI fallback logic.
        KumihoClient::new(base_url.to_string(), String::new())
    }

    #[tokio::test]
    async fn raw_get_502_html_returns_clean_upstream_unavailable() {
        let server = MockServer::start().await;
        Mock::given(method("GET"))
            .and(path("/api/v1/projects"))
            .respond_with(
                ResponseTemplate::new(502)
                    .insert_header("content-type", "text/html; charset=utf-8")
                    .set_body_string("<!DOCTYPE html><html><body>Bad Gateway</body></html>"),
            )
            .mount(&server)
            .await;

        let client = make_raw_client(&server.uri());
        let err = client
            .get_raw("projects", &HashMap::new())
            .await
            .expect_err("502 should surface as clean upstream unavailable");

        match err {
            KumihoError::UpstreamUnavailable { status, attempts } => {
                assert_eq!(status, 502);
                assert_eq!(attempts, 3);
            }
            other => panic!("expected UpstreamUnavailable, got {other:?}"),
        }
    }

    #[test]
    fn enrich_author_display_prefers_readable_username() {
        let mut value = serde_json::json!({
            "author": "b10101cf-d714-4ddc-a686-8680ef7114d2",
            "username": "neo@example.com"
        });
        enrich_author_display(&mut value, Some("fallback@example.com"));
        assert_eq!(value["author_display"], "neo@example.com");
    }

    #[test]
    fn enrich_author_display_uses_fallback_for_uuid_identity() {
        let mut value = serde_json::json!([{
            "author": "b10101cf-d714-4ddc-a686-8680ef7114d2",
            "username": "b10101cf-d714-4ddc-a686-8680ef7114d2",
            "metadata": {
                "created_by": "b10101cf-d714-4ddc-a686-8680ef7114d2"
            }
        }]);
        enrich_author_display(&mut value, Some("neo@example.com"));
        assert_eq!(value[0]["author_display"], "neo@example.com");
    }

    #[tokio::test]
    async fn deprecate_item_strips_revision_selectors_from_kref() {
        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/api/v1/items/deprecate"))
            .and(query_param("kref", "kref://Project/Skills/example.skill"))
            .and(query_param("deprecated", "true"))
            .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
                "kref": "kref://Project/Skills/example.skill",
                "name": "example",
                "item_name": "example",
                "kind": "skill",
                "deprecated": true,
                "created_at": null,
                "metadata": {}
            })))
            .mount(&server)
            .await;

        let client = make_client(&server.uri());
        let item = client
            .deprecate_item("kref://Project/Skills/example.skill?r=2&a=SKILL.md", true)
            .await
            .expect("deprecate item should use the base item kref");

        assert!(item.deprecated);
        assert_eq!(item.kref, "kref://Project/Skills/example.skill");
    }

    #[tokio::test]
    async fn deprecate_item_falls_back_to_soft_delete_when_upstream_set_deprecated_404s() {
        let server = MockServer::start().await;
        let item = serde_json::json!({
            "kref": "kref://Project/Skills/example.skill",
            "name": "example",
            "item_name": "example",
            "kind": "skill",
            "deprecated": true,
            "created_at": null,
            "metadata": {}
        });

        Mock::given(method("POST"))
            .and(path("/api/v1/items/deprecate"))
            .and(query_param("kref", "kref://Project/Skills/example.skill"))
            .and(query_param("deprecated", "true"))
            .respond_with(ResponseTemplate::new(404).set_body_string("not found"))
            .expect(1)
            .mount(&server)
            .await;
        Mock::given(method("DELETE"))
            .and(path("/api/v1/items/by-kref"))
            .and(query_param("kref", "kref://Project/Skills/example.skill"))
            .and(query_param("force", "false"))
            .respond_with(ResponseTemplate::new(204))
            .expect(1)
            .mount(&server)
            .await;
        Mock::given(method("GET"))
            .and(path("/api/v1/items/by-kref"))
            .and(query_param("kref", "kref://Project/Skills/example.skill"))
            .respond_with(ResponseTemplate::new(200).set_body_json(item))
            .expect(1)
            .mount(&server)
            .await;

        let client = make_client(&server.uri());
        let item = client
            .deprecate_item("kref://Project/Skills/example.skill", true)
            .await
            .expect("deprecate item should fall back to soft delete");

        assert!(item.deprecated);
    }

    #[tokio::test]
    async fn retries_on_502_then_succeeds_on_third_attempt() {
        let server = MockServer::start().await;
        let counter = Arc::new(AtomicUsize::new(0));
        Mock::given(method("GET"))
            .and(path("/api/v1/spaces"))
            .respond_with(CountingResponder {
                responses: vec![
                    ResponseTemplate::new(502).set_body_string("<html>boom</html>"),
                    ResponseTemplate::new(502).set_body_string("<html>boom</html>"),
                    ResponseTemplate::new(200).set_body_json(serde_json::json!([])),
                ],
                counter: counter.clone(),
            })
            .mount(&server)
            .await;

        let client = make_client(&server.uri());
        let result = client.list_spaces("/foo", false).await;
        assert!(result.is_ok(), "expected Ok after retries, got {result:?}");
        assert_eq!(
            counter.load(Ordering::SeqCst),
            3,
            "should have hit upstream 3x"
        );
    }

    #[tokio::test]
    async fn three_502s_returns_upstream_unavailable_not_api() {
        let server = MockServer::start().await;
        let counter = Arc::new(AtomicUsize::new(0));
        Mock::given(method("GET"))
            .and(path("/api/v1/spaces"))
            .respond_with(CountingResponder {
                responses: vec![
                    ResponseTemplate::new(502)
                        .insert_header("content-type", "text/html")
                        .set_body_string("<!DOCTYPE html><html>cloudflare</html>"),
                ],
                counter: counter.clone(),
            })
            .mount(&server)
            .await;

        let client = make_client(&server.uri());
        let err = client
            .list_spaces("/foo", false)
            .await
            .expect_err("must fail after 3 attempts");

        match err {
            KumihoError::UpstreamUnavailable { status, attempts } => {
                assert_eq!(status, 502);
                assert_eq!(attempts, 3);
            }
            other => panic!("expected UpstreamUnavailable, got {other:?}"),
        }
        assert_eq!(counter.load(Ordering::SeqCst), 3);
    }

    #[tokio::test]
    async fn non_retryable_4xx_returns_api_immediately() {
        let server = MockServer::start().await;
        let counter = Arc::new(AtomicUsize::new(0));
        Mock::given(method("GET"))
            .and(path("/api/v1/spaces"))
            .respond_with(CountingResponder {
                responses: vec![ResponseTemplate::new(400).set_body_string("bad request")],
                counter: counter.clone(),
            })
            .mount(&server)
            .await;

        let client = make_client(&server.uri());
        let err = client
            .list_spaces("/foo", false)
            .await
            .expect_err("400 must surface");
        match err {
            KumihoError::Api { status, body } => {
                assert_eq!(status, 400);
                assert_eq!(body, "bad request");
            }
            other => panic!("expected Api, got {other:?}"),
        }
        assert_eq!(counter.load(Ordering::SeqCst), 1, "no retry on 4xx");
    }

    #[tokio::test]
    async fn html_body_on_non_retryable_status_is_trimmed() {
        // 404 is not retryable. Even so, an HTML body must not enter the
        // error variant — the dashboard would render it verbatim.
        let server = MockServer::start().await;
        let big_html = format!(
            "<!doctype html><html><body>{}</body></html>",
            "padding ".repeat(200) // ~1.6 KB body
        );
        Mock::given(method("GET"))
            .and(path("/api/v1/spaces"))
            .respond_with(
                ResponseTemplate::new(404)
                    .insert_header("content-type", "text/html; charset=utf-8")
                    .set_body_string(big_html.clone()),
            )
            .mount(&server)
            .await;

        let client = make_client(&server.uri());
        let err = client
            .list_spaces("/foo", false)
            .await
            .expect_err("404 must surface");
        match err {
            KumihoError::Api { status, body } => {
                assert_eq!(status, 404);
                assert!(
                    !body.contains("<html") && !body.contains("<!doctype"),
                    "HTML body leaked into error: {body}"
                );
                assert!(
                    body.len() < 200,
                    "trimmed body should be a short placeholder, got {} bytes",
                    body.len()
                );
            }
            other => panic!("expected Api, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn connection_refused_eventually_returns_unreachable() {
        // Point at a port we know is closed. reqwest's connect failure is a
        // network error, which the retry helper treats as "retry, then
        // surface as Unreachable".
        let client = make_client("http://127.0.0.1:1"); // port 1 is reserved/closed
        let err = client
            .list_spaces("/foo", false)
            .await
            .expect_err("connection must fail");
        assert!(
            matches!(err, KumihoError::Unreachable(_)),
            "expected Unreachable, got {err:?}"
        );
    }

    // ── Pure-function tests (no network) ─────────────────────────────

    #[test]
    fn is_retryable_status_covers_gateway_codes() {
        for s in [502, 503, 504, 520, 522, 524] {
            assert!(is_retryable_status(s), "{s} should retry");
        }
        for s in [200, 400, 401, 404, 409, 500, 501, 505] {
            assert!(!is_retryable_status(s), "{s} should NOT retry");
        }
    }

    #[test]
    fn looks_like_html_body_detects_common_shapes() {
        assert!(looks_like_html_body("<!DOCTYPE html><html>", None));
        assert!(looks_like_html_body("<!doctype html>", None));
        assert!(looks_like_html_body("<html><body>x</body></html>", None));
        assert!(looks_like_html_body("   <HTML>", None));
        assert!(looks_like_html_body(
            "{\"ok\":true}",
            Some("text/html; charset=utf-8")
        ));
        assert!(!looks_like_html_body(
            "{\"error\":\"x\"}",
            Some("application/json")
        ));
        assert!(!looks_like_html_body("plain text", None));
    }

    #[test]
    fn item_kref_without_selectors_drops_revision_and_artifact_query() {
        assert_eq!(
            item_kref_without_selectors("kref://Project/Skills/example.skill?r=2&a=SKILL.md"),
            "kref://Project/Skills/example.skill"
        );
        assert_eq!(
            item_kref_without_selectors("kref://Project/Skills/example.skill"),
            "kref://Project/Skills/example.skill"
        );
    }

    // ── Bounded-retry-time tests (Finding #2) ────────────────────────

    /// A hung upstream must not let a single retried request blow past the
    /// `TOTAL_BUDGET` (15s) end-to-end cap. Worst case is 3 attempts × 5s
    /// per-attempt timeout + ~2s of jittered backoffs ≈ 17s without the
    /// deadline check; with the deadline check, retries stop early.
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn hung_upstream_respects_total_budget() {
        let server = MockServer::start().await;
        // 10s delay per response, far longer than PER_ATTEMPT_TIMEOUT (5s).
        Mock::given(method("GET"))
            .and(path("/api/v1/spaces"))
            .respond_with(
                ResponseTemplate::new(502)
                    .set_body_string("hang")
                    .set_delay(Duration::from_secs(10)),
            )
            .mount(&server)
            .await;

        let client = make_client(&server.uri());
        let started = Instant::now();
        let err = client
            .list_spaces("/foo", false)
            .await
            .expect_err("hung upstream must fail");
        let elapsed = started.elapsed();

        // 15s budget + small slack for jitter and scheduling.
        assert!(
            elapsed <= Duration::from_millis(15_500),
            "retries blew past budget: elapsed={elapsed:?}"
        );
        // We expect Unreachable (per-attempt timeout) here; either Unreachable
        // or UpstreamUnavailable is acceptable, but Api with the HTML body
        // would mean we leaked the upstream payload.
        assert!(
            matches!(
                err,
                KumihoError::Unreachable(_) | KumihoError::UpstreamUnavailable { .. }
            ),
            "expected Unreachable / UpstreamUnavailable, got {err:?}"
        );
    }

    // ── POST-skip-retry tests (Finding #3) ───────────────────────────

    /// POSTs must NOT be retried on 502 — retrying a create that already
    /// succeeded on the upstream (but was rewritten by a CDN to 502) would
    /// produce a duplicate item. The error must still be the clean
    /// `UpstreamUnavailable` shape (not `Api { body: "<html>" }`).
    #[tokio::test]
    async fn post_502_does_not_retry_returns_upstream_unavailable() {
        let server = MockServer::start().await;
        let counter = Arc::new(AtomicUsize::new(0));
        Mock::given(method("POST"))
            .and(path("/api/v1/items"))
            .respond_with(CountingResponder {
                responses: vec![
                    ResponseTemplate::new(502)
                        .insert_header("content-type", "text/html")
                        .set_body_string("<!DOCTYPE html><html>cloudflare</html>"),
                ],
                counter: counter.clone(),
            })
            .mount(&server)
            .await;

        let client = make_client(&server.uri());
        let err = client
            .create_item("/foo", "item", "kind", HashMap::new())
            .await
            .expect_err("POST 502 must surface");
        match err {
            KumihoError::UpstreamUnavailable { status, attempts } => {
                assert_eq!(status, 502);
                assert_eq!(
                    attempts, 1,
                    "POST must not retry (idempotency-key not honoured by Kumiho)"
                );
            }
            other => panic!("expected UpstreamUnavailable, got {other:?}"),
        }
        assert_eq!(
            counter.load(Ordering::SeqCst),
            1,
            "POST must hit upstream exactly once"
        );
    }

    /// Counterpart: GETs SHOULD still retry. Locks in the asymmetry.
    #[tokio::test]
    async fn get_502_retries_three_times() {
        let server = MockServer::start().await;
        let counter = Arc::new(AtomicUsize::new(0));
        Mock::given(method("GET"))
            .and(path("/api/v1/items"))
            .respond_with(CountingResponder {
                responses: vec![ResponseTemplate::new(502).set_body_string("<html>x</html>")],
                counter: counter.clone(),
            })
            .mount(&server)
            .await;

        let client = make_client(&server.uri());
        let _ = client
            .list_items("/foo", false)
            .await
            .expect_err("3 retries");
        assert_eq!(counter.load(Ordering::SeqCst), 3, "GET must retry 3x");
    }
}
