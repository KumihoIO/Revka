//! Axum-based MCP daemon.
//!
//! Endpoints:
//! - `POST /session`        — unauthenticated; mints `{ session_id, token, cwd }`.
//! - `POST /mcp`            — authenticated JSON-RPC 2.0. Returns either plain
//!                            JSON or an SSE stream depending on the method.
//! - `GET  /health`         — simple liveness probe.
//!
//! Session auth headers (required on `POST /mcp`):
//! - `Authorization: Bearer <token>`
//! - `X-Revka-Session: <session_id>`
//!
//! `POST /mcp` dispatch:
//! - `initialize`     → plain JSON response with server info + capabilities.
//! - `tools/list`     → plain JSON response listing all advertised tools.
//! - `tools/call`     → SSE stream. First emits zero or more
//!                      `notifications/progress` events (forwarded from the
//!                      tool's `execute_with_progress`), then one terminal
//!                      JSON-RPC response event, then `event: done`.

use crate::config::Config;
use crate::mcp_server::progress::McpProgressSink;
use crate::mcp_server::registry::{
    SkippedEntry, build_default_tools, build_tools_with_config, build_tools_with_runtime,
};
use crate::mcp_server::runtime::RuntimeHandles;
use crate::mcp_server::session::{ProgressEvent, SessionStore, SharedSessionStore};
use crate::tools::Tool;
use crate::tools::mcp_protocol::{
    INTERNAL_ERROR, INVALID_PARAMS, INVALID_REQUEST, JSONRPC_VERSION, JsonRpcError,
    MCP_PROTOCOL_VERSION, METHOD_NOT_FOUND,
};
use axum::{
    Json, Router,
    extract::{Path, State},
    http::{HeaderMap, StatusCode, header},
    response::{
        IntoResponse, Response,
        sse::{Event, KeepAlive, Sse},
    },
    routing::{get, post},
};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::HashMap;
use std::convert::Infallible;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::OnceLock;
use std::time::Instant;
use tokio::net::TcpListener;
use tokio::sync::broadcast;
use tokio::sync::mpsc;
use tokio_stream::StreamExt;
use tokio_stream::wrappers::BroadcastStream;
use tokio_stream::wrappers::UnboundedReceiverStream;

// ── Router state ───────────────────────────────────────────────────────────

#[derive(Clone)]
pub struct AppState {
    pub sessions: SharedSessionStore,
    pub tools: Arc<HashMap<String, Arc<dyn Tool>>>,
}

// ── Process start-time tracking (used by /health). ────────────────────────
//
// A `OnceLock` is fine here: the daemon process has a single start. Tests
// that spin up multiple routers in-process are all inside the same pid, so
// `started_at` is stable for the test binary lifetime — tests only assert
// shape, not exact value.

struct StartTime {
    instant: Instant,
    wall: DateTime<Utc>,
}

static START_TIME: OnceLock<StartTime> = OnceLock::new();

fn start_time() -> &'static StartTime {
    START_TIME.get_or_init(|| StartTime {
        instant: Instant::now(),
        wall: Utc::now(),
    })
}

#[derive(Debug, Serialize)]
struct HealthResponse {
    status: &'static str,
    pid: u32,
    uptime_seconds: u64,
    started_at: String,
    protocol_version: &'static str,
}

fn build_health_response() -> HealthResponse {
    let st = start_time();
    HealthResponse {
        status: "ok",
        pid: std::process::id(),
        uptime_seconds: st.instant.elapsed().as_secs(),
        started_at: st.wall.to_rfc3339(),
        protocol_version: MCP_PROTOCOL_VERSION,
    }
}

// ── Public handles ─────────────────────────────────────────────────────────

/// Handle returned by `serve_on` so tests can learn the bound port and shut
/// the server down.
pub struct McpServerHandle {
    pub addr: SocketAddr,
    pub shutdown: tokio::sync::oneshot::Sender<()>,
    pub joined: tokio::task::JoinHandle<()>,
}

/// Build the Axum router. Exposed for tests.
#[must_use]
pub fn build_router(state: AppState) -> Router {
    Router::new()
        .route("/health", get(health_handler))
        .route("/session", post(create_session_handler))
        .route("/session/{session_id}/events", get(session_events_handler))
        .route("/mcp", post(mcp_handler))
        .with_state(state)
}

/// Build an [`AppState`] with Revka's baseline tool registry (no Config).
///
/// Kept as the M1 entry point — returns the curated set of ~16 tools and no
/// integrations. Tests targeting the baseline surface call this directly.
#[must_use]
pub fn default_state(workspace_dir: &std::path::Path) -> (AppState, Vec<SkippedEntry>) {
    let (tools, skipped) = build_default_tools(workspace_dir);
    (build_app_state(tools), skipped)
}

/// Build an [`AppState`] using a loaded `Config` so integrations with creds
/// (Notion, Jira, Composio, Google Workspace, etc.) and the skills meta-tools
/// get registered alongside the baseline.
#[must_use]
pub fn state_from_config(
    workspace_dir: &std::path::Path,
    config: &Config,
) -> (AppState, Vec<SkippedEntry>) {
    let (tools, skipped) = build_tools_with_config(workspace_dir, config);
    (build_app_state(tools), skipped)
}

/// Build an [`AppState`] using a loaded `Config` plus the gateway's live
/// [`RuntimeHandles`]. This is the full-registry entry point used by the
/// in-process daemon boot.
#[must_use]
pub fn state_from_runtime(
    workspace_dir: &std::path::Path,
    config: &Config,
    runtime: &RuntimeHandles,
) -> (AppState, Vec<SkippedEntry>) {
    let (tools, skipped) = build_tools_with_runtime(workspace_dir, config, runtime);
    (build_app_state(tools), skipped)
}

fn build_app_state(tools: Vec<Arc<dyn Tool>>) -> AppState {
    let map: HashMap<String, Arc<dyn Tool>> = tools
        .into_iter()
        .map(|t| (t.name().to_string(), t))
        .collect();
    AppState {
        sessions: Arc::new(SessionStore::new()),
        tools: Arc::new(map),
    }
}

/// Extend an existing [`AppState`] with additional tools (used by tests).
pub fn state_with_tools(tools: Vec<Arc<dyn Tool>>) -> AppState {
    let map: HashMap<String, Arc<dyn Tool>> = tools
        .into_iter()
        .map(|t| (t.name().to_string(), t))
        .collect();
    AppState {
        sessions: Arc::new(SessionStore::new()),
        tools: Arc::new(map),
    }
}

/// Bind to `addr` (use `127.0.0.1:0` for ephemeral) and serve the router.
/// Returns once the server is listening. Writes no discovery file.
pub async fn serve_on(addr: SocketAddr, state: AppState) -> anyhow::Result<McpServerHandle> {
    let listener = TcpListener::bind(addr).await?;
    let bound = listener.local_addr()?;
    let router = build_router(state);
    let (tx, rx) = tokio::sync::oneshot::channel::<()>();

    let joined = tokio::spawn(async move {
        let res = axum::serve(listener, router)
            .with_graceful_shutdown(async move {
                let _ = rx.await;
            })
            .await;
        if let Err(e) = res {
            tracing::error!("revka-mcp server exited: {e}");
        }
    });

    Ok(McpServerHandle {
        addr: bound,
        shutdown: tx,
        joined,
    })
}

/// Legacy blocking entry point retained for tests that want to boot the MCP
/// server with only a workspace directory (no gateway AppState).
///
/// The main daemon no longer calls this: the gateway wires the MCP task
/// directly via [`state_from_runtime`] + [`serve_on`] so that live runtime
/// handles (workspace manager, channel map, session store, …) can be
/// threaded in.
///
/// Attempts to load a real Revka `Config`. On failure falls back to the
/// baseline registry so the server still advertises the curated tool set.
pub async fn run_daemon(workspace_dir: PathBuf) -> anyhow::Result<()> {
    let _ = start_time();

    let (state, skipped) = match Box::pin(Config::load_or_init()).await {
        Ok(config) => {
            tracing::info!(
                "mcp-server: loaded Revka config from {}",
                config.config_path.display()
            );
            state_from_config(&workspace_dir, &config)
        }
        Err(err) => {
            tracing::warn!(
                "mcp-server: failed to load Revka config ({err}) — continuing with baseline registry"
            );
            default_state(&workspace_dir)
        }
    };

    for (name, reason) in &skipped {
        tracing::info!("mcp-server: skipped tool `{name}` — {reason}");
    }
    tracing::info!("mcp-server: advertising {} tools", state.tools.len());

    let handle = serve_on(SocketAddr::from(([127, 0, 0, 1], 0)), state).await?;
    let url = format!("http://{}/mcp", handle.addr);
    write_discovery_file(&url)?;
    tracing::info!("mcp-server: listening on {url}");

    let _ = tokio::signal::ctrl_c().await;
    let _ = handle.shutdown.send(());
    let _ = handle.joined.await;
    cleanup_discovery_file();
    Ok(())
}

/// Absolute path of the MCP discovery file (`~/.revka/mcp.json`).
#[must_use]
pub fn discovery_path() -> Option<PathBuf> {
    directories::UserDirs::new().map(|u| u.home_dir().join(".revka").join("mcp.json"))
}

/// Write the MCP discovery file atomically (tempfile + rename) so external
/// readers never observe a half-written JSON document. Payload shape is
/// `{url, pid, started_at}` — frozen by contract, do not change.
pub fn write_discovery_file(url: &str) -> anyhow::Result<()> {
    let Some(path) = discovery_path() else {
        anyhow::bail!("could not resolve home directory");
    };
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let payload = json!({
        "url": url,
        "pid": std::process::id(),
        "started_at": Utc::now().to_rfc3339(),
    });
    let bytes = serde_json::to_vec_pretty(&payload)?;

    // Atomic write: tempfile in the same directory, then rename over the
    // target. Rename is atomic on POSIX filesystems so concurrent readers
    // never see a partial write.
    let parent = path
        .parent()
        .ok_or_else(|| anyhow::anyhow!("discovery path has no parent"))?;
    let tmp_name = format!(
        ".mcp.json.{}.{}",
        std::process::id(),
        Utc::now().timestamp_nanos_opt().unwrap_or(0)
    );
    let tmp_path = parent.join(tmp_name);
    std::fs::write(&tmp_path, &bytes)?;
    std::fs::rename(&tmp_path, &path)?;
    Ok(())
}

/// Remove the MCP discovery file on shutdown. Safe to call when the file
/// doesn't exist.
pub fn cleanup_discovery_file() {
    if let Some(path) = discovery_path() {
        let _ = std::fs::remove_file(path);
    }
}

// ── Handlers ───────────────────────────────────────────────────────────────

async fn health_handler() -> Response {
    (StatusCode::OK, Json(build_health_response())).into_response()
}

#[derive(Debug, Deserialize, Default)]
struct CreateSessionBody {
    cwd: Option<String>,
    label: Option<String>,
}

#[derive(Debug, Serialize)]
struct CreateSessionResponse {
    session_id: String,
    token: String,
    cwd: String,
}

async fn create_session_handler(
    State(state): State<AppState>,
    body: Option<Json<CreateSessionBody>>,
) -> Response {
    let body = body.map(|Json(b)| b).unwrap_or_default();
    let cwd = resolve_cwd(body.cwd.as_deref());
    let sess = state.sessions.create(cwd.clone(), body.label).await;
    let resp = CreateSessionResponse {
        session_id: sess.id,
        token: sess.token,
        cwd: cwd.to_string_lossy().into_owned(),
    };
    (StatusCode::OK, Json(resp)).into_response()
}

fn resolve_cwd(supplied: Option<&str>) -> PathBuf {
    if let Some(s) = supplied {
        let p = PathBuf::from(shellexpand::tilde(s).into_owned());
        if let Ok(canon) = p.canonicalize() {
            return canon;
        }
        return p;
    }
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

/// GET /session/<session_id>/events — session-wide progress SSE stream.
///
/// Auth: `Authorization: Bearer <token>` where `<token>` matches the token
/// that was issued by `POST /session`. `X-Revka-Session` is not required
/// here because the session id is already in the URL.
///
/// Every `ProgressEvent` published to the session's broadcast sender (by any
/// in-flight `tools/call`) is forwarded as a single `event:` line containing
/// the JSON-serialized payload. The stream terminates when the client
/// disconnects.
async fn session_events_handler(
    State(state): State<AppState>,
    Path(session_id): Path<String>,
    headers: HeaderMap,
) -> Response {
    // Reuse the `Authorization: Bearer <token>` header; `X-Revka-Session`
    // would duplicate `session_id` so we skip it.
    let Some(token) = headers
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .and_then(|s| s.strip_prefix("Bearer "))
        .map(str::trim)
    else {
        return (
            StatusCode::UNAUTHORIZED,
            "missing Authorization: Bearer <token>",
        )
            .into_response();
    };

    let Some(_session) = state.sessions.authenticate(&session_id, token).await else {
        return (StatusCode::UNAUTHORIZED, "invalid session or token").into_response();
    };

    // Subscribe to the session's broadcast channel. `authenticate` already
    // proved the session exists, but we re-fetch the sender to clone it.
    let Some(sender) = state.sessions.event_sender(&session_id).await else {
        return (StatusCode::NOT_FOUND, "session vanished").into_response();
    };

    let rx = sender.subscribe();
    let stream = BroadcastStream::new(rx).filter_map(|msg| match msg {
        Ok(ev) => Some(Ok::<_, Infallible>(
            Event::default().data(serde_json::to_string(&ev).unwrap_or_else(|_| "{}".into())),
        )),
        // Lagged: slow consumer missed N frames. We drop those frames
        // silently — progress is advisory.
        Err(_) => None,
    });

    Sse::new(stream)
        .keep_alive(KeepAlive::default())
        .into_response()
}

fn auth_or_401(headers: &HeaderMap) -> Result<(String, String), Response> {
    let bearer = headers
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .and_then(|s| s.strip_prefix("Bearer "))
        .map(str::trim);
    let session_id = headers
        .get("x-revka-session")
        .and_then(|v| v.to_str().ok())
        .map(str::trim);
    match (bearer, session_id) {
        (Some(t), Some(s)) if !t.is_empty() && !s.is_empty() => Ok((s.to_string(), t.to_string())),
        _ => Err((
            StatusCode::UNAUTHORIZED,
            "missing Authorization: Bearer <token> or X-Revka-Session header",
        )
            .into_response()),
    }
}

async fn mcp_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(req): Json<Value>,
) -> Response {
    let (session_id, token) = match auth_or_401(&headers) {
        Ok(pair) => pair,
        Err(resp) => return resp,
    };
    let Some(session) = state.sessions.authenticate(&session_id, &token).await else {
        return (StatusCode::UNAUTHORIZED, "invalid session or token").into_response();
    };

    // Pull id + method. `id` may be absent for JSON-RPC notifications; for
    // method calls we require it.
    let id = req.get("id").cloned();
    let method = req
        .get("method")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let params = req.get("params").cloned().unwrap_or(Value::Null);

    match method.as_str() {
        "initialize" => plain_ok(id, initialize_result()),
        "tools/list" => plain_ok(id, tools_list_result(&state)),
        "tools/call" => stream_tool_call(state, session.events.clone(), id, params),
        "notifications/initialized" | "notifications/cancelled" => {
            // No response body for notifications.
            StatusCode::ACCEPTED.into_response()
        }
        "" => plain_err(id, INVALID_REQUEST, "missing method"),
        other => plain_err(id, METHOD_NOT_FOUND, &format!("unknown method: {other}")),
    }
}

fn initialize_result() -> Value {
    json!({
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "capabilities": {
            "tools": { "listChanged": false }
        },
        "serverInfo": {
            "name": "revka-mcp",
            "version": env!("CARGO_PKG_VERSION"),
        }
    })
}

fn tools_list_result(state: &AppState) -> Value {
    let mut tools: Vec<Value> = state
        .tools
        .values()
        .map(|t| {
            json!({
                "name": t.name(),
                "description": t.description(),
                "inputSchema": t.parameters_schema(),
            })
        })
        .collect();
    tools.sort_by(|a, b| a["name"].as_str().cmp(&b["name"].as_str()));
    json!({ "tools": tools })
}

fn plain_ok(id: Option<Value>, result: Value) -> Response {
    let body = json!({
        "jsonrpc": JSONRPC_VERSION,
        "id": id.unwrap_or(Value::Null),
        "result": result,
    });
    (StatusCode::OK, Json(body)).into_response()
}

fn plain_err(id: Option<Value>, code: i32, msg: &str) -> Response {
    let err = JsonRpcError {
        code,
        message: msg.to_string(),
        data: None,
    };
    let body = json!({
        "jsonrpc": JSONRPC_VERSION,
        "id": id.unwrap_or(Value::Null),
        "error": err,
    });
    (StatusCode::OK, Json(body)).into_response()
}

fn stream_tool_call(
    state: AppState,
    session_events: broadcast::Sender<ProgressEvent>,
    id: Option<Value>,
    params: Value,
) -> Response {
    let name = params
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let args = params.get("arguments").cloned().unwrap_or(Value::Null);
    let meta_token = params
        .get("_meta")
        .and_then(|m| m.get("progressToken"))
        .and_then(Value::as_u64);

    let Some(tool) = state.tools.get(&name).cloned() else {
        return plain_err(id, INVALID_PARAMS, &format!("unknown tool: {name}"));
    };

    let (tx, rx) = mpsc::unbounded_channel::<Value>();
    let sink = Arc::new(McpProgressSink::with_session(
        tx.clone(),
        meta_token,
        session_events,
        name.clone(),
    ));

    // Kick off the tool in the background; the task pushes the terminal
    // JSON-RPC response onto `tx` once finished.
    let tx_final = tx.clone();
    let id_for_task = id.clone();
    tokio::spawn(async move {
        let result = tool.execute_with_progress(args, sink.as_ref()).await;
        let final_msg = match result {
            Ok(tool_result) => {
                let content = tool_result_to_content(&tool_result);
                let payload = json!({
                    "content": content,
                    "isError": !tool_result.success,
                });
                json!({
                    "jsonrpc": JSONRPC_VERSION,
                    "id": id_for_task.unwrap_or(Value::Null),
                    "result": payload,
                })
            }
            Err(err) => {
                json!({
                    "jsonrpc": JSONRPC_VERSION,
                    "id": id_for_task.unwrap_or(Value::Null),
                    "error": {
                        "code": INTERNAL_ERROR,
                        "message": err.to_string(),
                    }
                })
            }
        };
        let _ = tx_final.send(final_msg);
        // Dropping the last sender will close `rx` for the SSE stream.
    });

    let event_stream = UnboundedReceiverStream::new(rx)
        .map(|msg| Ok::<_, Infallible>(Event::default().data(msg.to_string())));

    Sse::new(event_stream)
        .keep_alive(KeepAlive::default())
        .into_response()
}

fn tool_result_to_content(result: &crate::tools::traits::ToolResult) -> Value {
    let text = if result.success {
        result.output.clone()
    } else {
        result
            .error
            .clone()
            .unwrap_or_else(|| result.output.clone())
    };
    json!([{ "type": "text", "text": text }])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn health_response_shape() {
        let h = build_health_response();
        // Serialize to JSON and assert every documented field is present with
        // the expected type.
        let v = serde_json::to_value(&h).unwrap();
        assert_eq!(v["status"], "ok");
        assert!(v["pid"].as_u64().is_some());
        assert!(v["uptime_seconds"].as_u64().is_some());
        assert!(
            v["started_at"].as_str().is_some_and(|s| !s.is_empty()),
            "started_at should be a non-empty rfc3339 string"
        );
        assert_eq!(v["protocol_version"], MCP_PROTOCOL_VERSION);
    }

    #[tokio::test]
    async fn health_handler_returns_200_json() {
        let resp = health_handler().await;
        assert_eq!(resp.status(), StatusCode::OK);
        // Best effort: response should carry a JSON content-type.
        let ct = resp
            .headers()
            .get(header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("");
        assert!(
            ct.contains("application/json"),
            "expected JSON content-type, got: {ct}"
        );
    }

    // ── Session-wide progress SSE (`/session/<id>/events`) ────────────────

    fn fresh_app_state() -> AppState {
        let tmp = std::env::temp_dir();
        let (state, _) = default_state(&tmp);
        state
    }

    #[tokio::test]
    async fn session_events_rejects_missing_auth() {
        let state = fresh_app_state();
        let sess = state.sessions.create(std::env::temp_dir(), None).await;
        let headers = HeaderMap::new();
        let resp = session_events_handler(State(state), Path(sess.id.clone()), headers).await;
        assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);
    }

    #[tokio::test]
    async fn session_events_rejects_wrong_token() {
        let state = fresh_app_state();
        let sess = state.sessions.create(std::env::temp_dir(), None).await;
        let mut headers = HeaderMap::new();
        headers.insert(
            header::AUTHORIZATION,
            "Bearer not-the-token".parse().unwrap(),
        );
        let resp = session_events_handler(State(state), Path(sess.id.clone()), headers).await;
        assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);
    }

    #[tokio::test]
    async fn session_events_accepts_correct_token() {
        let state = fresh_app_state();
        let sess = state.sessions.create(std::env::temp_dir(), None).await;
        let mut headers = HeaderMap::new();
        headers.insert(
            header::AUTHORIZATION,
            format!("Bearer {}", sess.token).parse().unwrap(),
        );
        let resp = session_events_handler(State(state), Path(sess.id.clone()), headers).await;
        assert_eq!(resp.status(), StatusCode::OK);
        // SSE content-type marker.
        let ct = resp
            .headers()
            .get(header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("");
        assert!(
            ct.contains("text/event-stream"),
            "expected SSE content-type, got: {ct}"
        );
    }

    #[tokio::test]
    async fn session_broadcast_round_trip_through_store() {
        let state = fresh_app_state();
        let sess = state.sessions.create(std::env::temp_dir(), None).await;

        // Subscribe via the authenticated lookup path the handler uses.
        let sender = state
            .sessions
            .event_sender(&sess.id)
            .await
            .expect("session present");
        let mut rx = sender.subscribe();

        // Simulate a synthetic progress event (as if a tool had emitted it).
        let ev = ProgressEvent::new(
            5,
            2,
            Some(4),
            Some("half way".into()),
            Some("notion".into()),
        );
        sender.send(ev).expect("send ok");

        let got = rx.recv().await.expect("recv ok");
        assert_eq!(got.progress, 2);
        assert_eq!(got.tool.as_deref(), Some("notion"));
    }
}
