//! `/api/mcp/*` — discovery + health proxy for the in-process MCP server.
//!
//! The MCP server now runs as a tokio task inside the main construct daemon
//! (see `gateway::run_gateway`), not as a separate `construct-mcp` process. It
//! binds an *ephemeral* port and writes the real URL to
//! `~/.construct/mcp.json`; the frontend reads that instead of hardcoding a
//! port.
//!
//! This module exposes `GET /api/mcp/discovery` which:
//! 1. Reads the discovery file (mtime-cached).
//! 2. Issues a short-timeout `GET <url>/health` against the MCP server.
//! 3. Returns a uniform JSON shape the UI can use to drive the status badge.

use super::AppState;
use super::mcp_discovery::{McpDiscovery, read_construct_mcp};
use crate::config::schema::{McpServerConfig, McpTransport};
use crate::tools::mcp_client::McpServer;
use axum::{
    body::Body,
    extract::{Path, State},
    http::{HeaderMap, StatusCode, header},
    response::{IntoResponse, Json, Response},
};
use futures_util::StreamExt;
use serde::Deserialize;
use serde_json::{Value, json};
use std::collections::HashMap;
use std::time::{Duration, Instant};
use tokio::time::timeout;

/// Map a discovery URL (e.g. `http://127.0.0.1:51234/mcp`) to its health URL
/// (`http://127.0.0.1:51234/health`). Robust to either form (with or without
/// the trailing `/mcp`).
fn health_url_from_discovery(url: &str) -> String {
    let trimmed = url.trim_end_matches('/');
    match trimmed.strip_suffix("/mcp") {
        Some(base) => format!("{base}/health"),
        None => format!("{trimmed}/health"),
    }
}

/// Health probe interface — trivially mockable in tests.
#[async_trait::async_trait]
pub trait HealthProbe: Send + Sync {
    async fn get_health(&self, url: &str) -> Result<Value, String>;
}

/// Default `reqwest`-backed probe with a 500ms timeout.
pub struct ReqwestHealthProbe;

#[async_trait::async_trait]
impl HealthProbe for ReqwestHealthProbe {
    async fn get_health(&self, url: &str) -> Result<Value, String> {
        let client = reqwest::Client::builder()
            .timeout(Duration::from_millis(500))
            .build()
            .map_err(|e| e.to_string())?;
        let resp = client.get(url).send().await.map_err(|e| e.to_string())?;
        if !resp.status().is_success() {
            return Err(format!("health status {}", resp.status()));
        }
        resp.json::<Value>().await.map_err(|e| e.to_string())
    }
}

/// Core decision logic, factored so tests can inject fakes.
pub async fn build_discovery_payload(
    discovery: Option<McpDiscovery>,
    probe: &dyn HealthProbe,
) -> Value {
    let Some(d) = discovery else {
        return json!({
            "available": false,
            "reason": "discovery file missing",
        });
    };
    let health_url = health_url_from_discovery(&d.url);
    match probe.get_health(&health_url).await {
        Ok(health) => json!({
            "available": true,
            "url": d.url,
            "health": health,
        }),
        Err(_) => json!({
            "available": false,
            "reason": "health check failed",
        }),
    }
}

/// GET /api/mcp/discovery
pub async fn handle_api_mcp_discovery(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if let Err(e) = super::api::require_auth(&state, &headers) {
        return e.into_response();
    }

    let discovery = read_construct_mcp().ok();
    let payload = build_discovery_payload(discovery, &ReqwestHealthProbe).await;
    (StatusCode::OK, Json(payload)).into_response()
}

// ───────────────────────────────────────────────────────────────────────────
// Reverse-proxy to the in-process MCP server (`127.0.0.1:<ephemeral>`).
//
// Why: the browser lives on the gateway's origin. The MCP axum router has no
// CORS layer and listens on a different port, so the V2 Code tab can't POST
// `/session` directly (ERR_CONNECTION_REFUSED / CORS). Funneling those
// requests through `/api/mcp/*` keeps the browser same-origin and reuses the
// gateway's existing bearer-auth middleware. External MCP clients (e.g.
// Claude Desktop) still read `~/.construct/mcp.json` and talk to the MCP port
// directly — nothing changes for them.
// ───────────────────────────────────────────────────────────────────────────

/// Join a (possibly trailing-slash) MCP base URL with a request path.
/// Split out for unit tests — the real call site pulls `base` from `AppState`.
fn join_mcp_url(base: &str, path: &str) -> String {
    format!("{}{path}", base.trim_end_matches('/'))
}

/// Build an upstream URL (`<mcp_base>/<path>`) from the stored local MCP base.
/// Returns `None` when the MCP server failed to bind during startup.
fn mcp_upstream_url(state: &AppState, path: &str) -> Option<String> {
    let base = state.mcp_local_url.as_ref()?;
    Some(join_mcp_url(base, path))
}

/// Uniform 503 body for when MCP never bound. Mirrors the shape used by
/// `/api/mcp/discovery` so the frontend can treat it identically.
fn mcp_unavailable() -> Response {
    (
        StatusCode::SERVICE_UNAVAILABLE,
        Json(json!({
            "available": false,
            "reason": "mcp server not bound",
        })),
    )
        .into_response()
}

/// GET /api/mcp/health — direct proxy to the MCP server's `/health` endpoint
/// (convenience for the UI). Not required for session setup.
pub async fn handle_api_mcp_health(State(state): State<AppState>, headers: HeaderMap) -> Response {
    if let Err(e) = super::api::require_auth(&state, &headers) {
        return e.into_response();
    }
    let Some(url) = mcp_upstream_url(&state, "/health") else {
        return mcp_unavailable();
    };
    let client = match reqwest::Client::builder()
        .timeout(Duration::from_secs(5))
        .build()
    {
        Ok(c) => c,
        Err(e) => {
            tracing::warn!("api_mcp: build client failed: {e}");
            return (StatusCode::INTERNAL_SERVER_ERROR, "client build failed").into_response();
        }
    };
    match client.get(&url).send().await {
        Ok(resp) => {
            let status =
                StatusCode::from_u16(resp.status().as_u16()).unwrap_or(StatusCode::BAD_GATEWAY);
            let ct = resp
                .headers()
                .get(header::CONTENT_TYPE)
                .and_then(|v| v.to_str().ok())
                .unwrap_or("application/json")
                .to_string();
            let body = resp.bytes().await.unwrap_or_default();
            (status, [(header::CONTENT_TYPE, ct)], body).into_response()
        }
        Err(e) => {
            tracing::warn!("api_mcp: health upstream error: {e}");
            (StatusCode::BAD_GATEWAY, "mcp upstream error").into_response()
        }
    }
}

/// POST /api/mcp/session — proxy to the MCP server's `POST /session`.
///
/// Body passes through verbatim (`{ cwd, label }` today, but we don't parse
/// it). Returns whatever the MCP server returns — `{ session_id, token, cwd }`
/// on success.
pub async fn handle_api_mcp_session_create(
    State(state): State<AppState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(e) = super::api::require_auth(&state, &headers) {
        return e.into_response();
    }
    let Some(url) = mcp_upstream_url(&state, "/session") else {
        return mcp_unavailable();
    };
    let client = match reqwest::Client::builder()
        .timeout(Duration::from_secs(10))
        .build()
    {
        Ok(c) => c,
        Err(e) => {
            tracing::warn!("api_mcp: build client failed: {e}");
            return (StatusCode::INTERNAL_SERVER_ERROR, "client build failed").into_response();
        }
    };
    let mut req = client.post(&url).body(body.to_vec());
    if let Some(ct) = headers
        .get(header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
    {
        req = req.header(header::CONTENT_TYPE, ct);
    } else {
        req = req.header(header::CONTENT_TYPE, "application/json");
    }
    match req.send().await {
        Ok(resp) => {
            let status =
                StatusCode::from_u16(resp.status().as_u16()).unwrap_or(StatusCode::BAD_GATEWAY);
            let ct = resp
                .headers()
                .get(header::CONTENT_TYPE)
                .and_then(|v| v.to_str().ok())
                .unwrap_or("application/json")
                .to_string();
            let bytes = resp.bytes().await.unwrap_or_default();
            (status, [(header::CONTENT_TYPE, ct)], bytes).into_response()
        }
        Err(e) => {
            tracing::warn!("api_mcp: session upstream error: {e}");
            (StatusCode::BAD_GATEWAY, "mcp upstream error").into_response()
        }
    }
}

/// POST /api/mcp/call — proxy to the MCP server's `POST /mcp` (JSON-RPC 2.0).
pub async fn handle_api_mcp_call(
    State(state): State<AppState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(e) = super::api::require_auth(&state, &headers) {
        return e.into_response();
    }
    let Some(url) = mcp_upstream_url(&state, "/mcp") else {
        return mcp_unavailable();
    };
    // JSON-RPC calls can include long-running tool invocations; allow enough
    // headroom but still bound it so a runaway call doesn't tie up a worker.
    let client = match reqwest::Client::builder()
        .timeout(Duration::from_secs(120))
        .build()
    {
        Ok(c) => c,
        Err(e) => {
            tracing::warn!("api_mcp: build client failed: {e}");
            return (StatusCode::INTERNAL_SERVER_ERROR, "client build failed").into_response();
        }
    };
    let mut req = client.post(&url).body(body.to_vec());
    if let Some(ct) = headers
        .get(header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
    {
        req = req.header(header::CONTENT_TYPE, ct);
    } else {
        req = req.header(header::CONTENT_TYPE, "application/json");
    }
    if let Some(auth) = headers
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
    {
        // JSON-RPC calls require the per-session bearer the MCP server issued
        // via POST /session. That token is opaque to the gateway; pass it
        // through so callers that already hold one can reuse it.
        req = req.header(header::AUTHORIZATION, auth);
    }
    if let Some(session) = headers
        .get("X-Construct-Session")
        .and_then(|v| v.to_str().ok())
    {
        // The MCP server validates the bearer token against its session id.
        // Without this header the proxy path returns 401 even though session
        // creation succeeded.
        req = req.header("X-Construct-Session", session);
    }
    match req.send().await {
        Ok(resp) => {
            let status =
                StatusCode::from_u16(resp.status().as_u16()).unwrap_or(StatusCode::BAD_GATEWAY);
            let ct = resp
                .headers()
                .get(header::CONTENT_TYPE)
                .and_then(|v| v.to_str().ok())
                .unwrap_or("application/json")
                .to_string();
            let bytes = resp.bytes().await.unwrap_or_default();
            (status, [(header::CONTENT_TYPE, ct)], bytes).into_response()
        }
        Err(e) => {
            tracing::warn!("api_mcp: call upstream error: {e}");
            (StatusCode::BAD_GATEWAY, "mcp upstream error").into_response()
        }
    }
}

/// GET /api/mcp/session/{id}/events — SSE passthrough.
///
/// Keeps the stream alive for the life of the session; reqwest's default
/// timeout is disabled via a long connect timeout + no request timeout so the
/// server-push stream isn't severed mid-flight.
pub async fn handle_api_mcp_session_events(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(session_id): Path<String>,
) -> Response {
    if let Err(e) = super::api::require_auth(&state, &headers) {
        return e.into_response();
    }
    let Some(url) = mcp_upstream_url(&state, &format!("/session/{session_id}/events")) else {
        return mcp_unavailable();
    };
    let client = match reqwest::Client::builder()
        .connect_timeout(Duration::from_secs(5))
        // No request-level timeout: SSE is long-lived.
        .build()
    {
        Ok(c) => c,
        Err(e) => {
            tracing::warn!("api_mcp: build sse client failed: {e}");
            return (StatusCode::INTERNAL_SERVER_ERROR, "client build failed").into_response();
        }
    };
    let mut req = client.get(&url).header(header::ACCEPT, "text/event-stream");
    if let Some(auth) = headers
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
    {
        req = req.header(header::AUTHORIZATION, auth);
    }
    if let Some(session) = headers
        .get("X-Construct-Session")
        .and_then(|v| v.to_str().ok())
    {
        req = req.header("X-Construct-Session", session);
    }
    let upstream = match req.send().await {
        Ok(r) => r,
        Err(e) => {
            tracing::warn!("api_mcp: sse upstream connect failed: {e}");
            return (StatusCode::BAD_GATEWAY, "mcp upstream error").into_response();
        }
    };
    if !upstream.status().is_success() {
        let status =
            StatusCode::from_u16(upstream.status().as_u16()).unwrap_or(StatusCode::BAD_GATEWAY);
        let body = upstream.bytes().await.unwrap_or_default();
        return (status, body).into_response();
    }
    let byte_stream = upstream
        .bytes_stream()
        .map(|r| r.map_err(std::io::Error::other));
    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "text/event-stream")
        .header(header::CACHE_CONTROL, "no-cache")
        .header("x-accel-buffering", "no")
        .body(Body::from_stream(byte_stream))
        .unwrap_or_else(|_| {
            (StatusCode::INTERNAL_SERVER_ERROR, "response build failed").into_response()
        })
}

// ───────────────────────────────────────────────────────────────────────────
// POST /api/mcp/servers/test — "Test" button in ConfigV2 MCP editor.
// Performs the same `initialize` + `tools/list` handshake an external CLI
// would, and reports success/failure + tool count + latency.
// ───────────────────────────────────────────────────────────────────────────

/// Hard ceiling on the full handshake (initialize + tools/list).
/// Bounded so a misconfigured server cannot tie up a request thread.
const TEST_HANDSHAKE_TIMEOUT_SECS: u64 = 10;

/// Wire shape posted by ConfigV2's `McpServerEntry` — tolerant of unset fields
/// for the transport the user didn't select. We translate to
/// [`McpServerConfig`] before handing off to the existing client.
#[derive(Debug, Deserialize)]
pub struct TestServerRequest {
    pub name: String,
    pub transport: String,
    #[serde(default)]
    pub command: Option<String>,
    #[serde(default)]
    pub args: Option<Vec<String>>,
    #[serde(default)]
    pub env: Option<HashMap<String, String>>,
    #[serde(default)]
    pub url: Option<String>,
    #[serde(default)]
    pub headers: Option<HashMap<String, String>>,
    #[serde(default)]
    pub timeout_ms: Option<u64>,
}

/// Validate the request and build an [`McpServerConfig`] ready to connect.
///
/// Returns a human-readable error string suitable for the response body
/// when a required field for the chosen transport is missing or the
/// transport itself is unknown.
pub fn request_to_config(req: &TestServerRequest) -> Result<McpServerConfig, String> {
    if req.name.trim().is_empty() {
        return Err("name is required".to_string());
    }
    let transport = match req.transport.as_str() {
        "stdio" => McpTransport::Stdio,
        "http" => McpTransport::Http,
        "sse" => McpTransport::Sse,
        other => return Err(format!("unknown transport `{other}`")),
    };
    match transport {
        McpTransport::Stdio => {
            if req
                .command
                .as_deref()
                .map(str::trim)
                .unwrap_or("")
                .is_empty()
            {
                return Err("command is required for stdio transport".to_string());
            }
        }
        McpTransport::Http | McpTransport::Sse => {
            if req.url.as_deref().map(str::trim).unwrap_or("").is_empty() {
                return Err("url is required for http/sse transport".to_string());
            }
        }
    }
    let tool_timeout_secs = req.timeout_ms.map(|ms| (ms / 1000).max(1));
    Ok(McpServerConfig {
        name: req.name.clone(),
        transport,
        url: req.url.clone(),
        command: req.command.clone().unwrap_or_default(),
        args: req.args.clone().unwrap_or_default(),
        env: req.env.clone().unwrap_or_default(),
        headers: req.headers.clone().unwrap_or_default(),
        tool_timeout_secs,
    })
}

/// POST /api/mcp/servers/test
pub async fn handle_api_mcp_servers_test(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(req): Json<TestServerRequest>,
) -> impl IntoResponse {
    if let Err(e) = super::api::require_auth(&state, &headers) {
        return e.into_response();
    }

    let config = match request_to_config(&req) {
        Ok(c) => c,
        Err(msg) => {
            return (
                StatusCode::OK,
                Json(json!({
                    "ok": false,
                    "error": msg,
                    "latency_ms": 0,
                })),
            )
                .into_response();
        }
    };

    let started = Instant::now();
    let result = timeout(
        Duration::from_secs(TEST_HANDSHAKE_TIMEOUT_SECS),
        McpServer::connect(config),
    )
    .await;
    let latency_ms = started.elapsed().as_millis() as u64;

    let payload = match result {
        Ok(Ok(server)) => {
            let tools = server.tools().await;
            let tool_names: Vec<String> = tools.iter().map(|t| t.name.clone()).collect();
            json!({
                "ok": true,
                "tool_count": tools.len(),
                "tools": tool_names,
                "latency_ms": latency_ms,
            })
        }
        Ok(Err(e)) => json!({
            "ok": false,
            "error": format!("{e:#}"),
            "latency_ms": latency_ms,
        }),
        Err(_) => json!({
            "ok": false,
            "error": format!("timed out after {TEST_HANDSHAKE_TIMEOUT_SECS}s"),
            "latency_ms": latency_ms,
        }),
    };

    (StatusCode::OK, Json(payload)).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    struct FakeProbeOk;
    #[async_trait::async_trait]
    impl HealthProbe for FakeProbeOk {
        async fn get_health(&self, _url: &str) -> Result<Value, String> {
            Ok(json!({
                "status": "ok",
                "pid": 123,
                "uptime_seconds": 5,
                "started_at": "2026-04-17T00:00:00Z",
                "protocol_version": "2024-11-05",
            }))
        }
    }

    struct FakeProbeErr;
    #[async_trait::async_trait]
    impl HealthProbe for FakeProbeErr {
        async fn get_health(&self, _url: &str) -> Result<Value, String> {
            Err("connection refused".into())
        }
    }

    struct CountingProbe(AtomicUsize);
    #[async_trait::async_trait]
    impl HealthProbe for CountingProbe {
        async fn get_health(&self, url: &str) -> Result<Value, String> {
            self.0.fetch_add(1, Ordering::SeqCst);
            Ok(json!({"hit": url}))
        }
    }

    #[test]
    fn join_mcp_url_composes_base_and_path() {
        assert_eq!(
            join_mcp_url("http://127.0.0.1:60004", "/session"),
            "http://127.0.0.1:60004/session"
        );
        assert_eq!(
            join_mcp_url("http://127.0.0.1:60004/", "/session"),
            "http://127.0.0.1:60004/session"
        );
        assert_eq!(
            join_mcp_url("http://127.0.0.1:60004", "/session/abc/events"),
            "http://127.0.0.1:60004/session/abc/events"
        );
        assert_eq!(
            join_mcp_url("http://127.0.0.1:60004", "/mcp"),
            "http://127.0.0.1:60004/mcp"
        );
    }

    #[test]
    fn health_url_strips_mcp_suffix() {
        assert_eq!(
            health_url_from_discovery("http://127.0.0.1:54500/mcp"),
            "http://127.0.0.1:54500/health"
        );
        assert_eq!(
            health_url_from_discovery("http://127.0.0.1:54500/mcp/"),
            "http://127.0.0.1:54500/health"
        );
        assert_eq!(
            health_url_from_discovery("http://127.0.0.1:54500"),
            "http://127.0.0.1:54500/health"
        );
    }

    #[tokio::test]
    async fn discovery_missing_file() {
        let v = build_discovery_payload(None, &FakeProbeOk).await;
        assert_eq!(v["available"], false);
        assert_eq!(v["reason"], "discovery file missing");
    }

    #[tokio::test]
    async fn discovery_present_daemon_reachable() {
        let d = McpDiscovery {
            url: "http://127.0.0.1:50000/mcp".into(),
            pid: Some(42),
            started_at: None,
        };
        let v = build_discovery_payload(Some(d), &FakeProbeOk).await;
        assert_eq!(v["available"], true);
        assert_eq!(v["url"], "http://127.0.0.1:50000/mcp");
        assert_eq!(v["health"]["status"], "ok");
        assert_eq!(v["health"]["pid"], 123);
    }

    #[tokio::test]
    async fn discovery_present_daemon_unreachable() {
        let d = McpDiscovery {
            url: "http://127.0.0.1:50000/mcp".into(),
            pid: Some(42),
            started_at: None,
        };
        let v = build_discovery_payload(Some(d), &FakeProbeErr).await;
        assert_eq!(v["available"], false);
        assert_eq!(v["reason"], "health check failed");
    }

    #[test]
    fn request_to_config_rejects_empty_name() {
        let req = TestServerRequest {
            name: "  ".into(),
            transport: "stdio".into(),
            command: Some("x".into()),
            args: None,
            env: None,
            url: None,
            headers: None,
            timeout_ms: None,
        };
        assert!(request_to_config(&req).unwrap_err().contains("name"));
    }

    #[test]
    fn request_to_config_rejects_unknown_transport() {
        let req = TestServerRequest {
            name: "m".into(),
            transport: "carrier-pigeon".into(),
            command: None,
            args: None,
            env: None,
            url: None,
            headers: None,
            timeout_ms: None,
        };
        assert!(
            request_to_config(&req)
                .unwrap_err()
                .contains("unknown transport")
        );
    }

    #[test]
    fn request_to_config_stdio_requires_command() {
        let req = TestServerRequest {
            name: "m".into(),
            transport: "stdio".into(),
            command: Some("   ".into()),
            args: None,
            env: None,
            url: None,
            headers: None,
            timeout_ms: None,
        };
        assert!(request_to_config(&req).unwrap_err().contains("command"));
    }

    #[test]
    fn request_to_config_http_requires_url() {
        let req = TestServerRequest {
            name: "m".into(),
            transport: "http".into(),
            command: None,
            args: None,
            env: None,
            url: Some("".into()),
            headers: None,
            timeout_ms: None,
        };
        assert!(request_to_config(&req).unwrap_err().contains("url"));
    }

    #[test]
    fn request_to_config_maps_stdio_fields() {
        let mut env = HashMap::new();
        env.insert("API_KEY".into(), "secret".into());
        let req = TestServerRequest {
            name: "memory".into(),
            transport: "stdio".into(),
            command: Some("/usr/local/bin/mcp".into()),
            args: Some(vec!["--flag".into(), "v".into()]),
            env: Some(env.clone()),
            url: None,
            headers: None,
            timeout_ms: Some(30_000),
        };
        let cfg = request_to_config(&req).unwrap();
        assert_eq!(cfg.name, "memory");
        assert_eq!(cfg.transport, McpTransport::Stdio);
        assert_eq!(cfg.command, "/usr/local/bin/mcp");
        assert_eq!(cfg.args, vec!["--flag", "v"]);
        assert_eq!(cfg.env, env);
        assert_eq!(cfg.tool_timeout_secs, Some(30));
    }

    #[test]
    fn request_to_config_maps_http_fields() {
        let mut hdr = HashMap::new();
        hdr.insert("X-Auth".into(), "abc".into());
        let req = TestServerRequest {
            name: "remote".into(),
            transport: "sse".into(),
            command: None,
            args: None,
            env: None,
            url: Some("https://example.com/mcp".into()),
            headers: Some(hdr.clone()),
            timeout_ms: Some(500),
        };
        let cfg = request_to_config(&req).unwrap();
        assert_eq!(cfg.transport, McpTransport::Sse);
        assert_eq!(cfg.url.as_deref(), Some("https://example.com/mcp"));
        assert_eq!(cfg.headers, hdr);
        // sub-second timeouts clamp up to 1s so we don't pass 0 downstream.
        assert_eq!(cfg.tool_timeout_secs, Some(1));
    }

    #[tokio::test]
    async fn discovery_hits_health_url_only_once() {
        let probe = CountingProbe(AtomicUsize::new(0));
        let d = McpDiscovery {
            url: "http://127.0.0.1:50000/mcp".into(),
            pid: None,
            started_at: None,
        };
        let _ = build_discovery_payload(Some(d), &probe).await;
        assert_eq!(probe.0.load(Ordering::SeqCst), 1);
    }
}
