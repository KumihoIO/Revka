//! Integration tests for the in-process MCP server (formerly the
//! standalone `revka-mcp` daemon — now spawned as a tokio task inside
//! the main daemon, see `gateway::run_gateway`).
//!
//! Brings the Axum server up on a random localhost port, drives it with
//! `reqwest`, and asserts:
//!   1. `POST /session` returns a valid token, and unauthenticated `POST /mcp`
//!      is rejected with HTTP 401.
//!   2. After auth, `initialize` + `tools/list` work and the server advertises
//!      at least 10 tools.
//!   3. A test-only tool that overrides `execute_with_progress` really does
//!      push `notifications/progress` events over the SSE response stream.
//!   4. `build_tools_with_runtime` behaves correctly: with an empty
//!      `RuntimeHandles`, previously-skipped handle-dependent tools
//!      (workspace / discord_search / delegate) stay absent; supplying the
//!      matching handle flips them on.

use async_trait::async_trait;
use revka::mcp_server::RuntimeHandles;
use revka::mcp_server::registry::build_tools_with_runtime;
use revka::mcp_server::server::{AppState, serve_on};
use revka::mcp_server::session::SessionStore;
use revka::tools::Tool;
use revka::tools::progress::{ProgressHandle, ProgressSink};
use revka::tools::traits::ToolResult;
use serde_json::{Value, json};
use std::net::SocketAddr;
use std::sync::Arc;

// ── Test-only progressy tool ──────────────────────────────────────────────

struct SleepyTool;

#[async_trait]
impl Tool for SleepyTool {
    fn name(&self) -> &str {
        "sleepy"
    }
    fn description(&self) -> &str {
        "sleeps briefly and emits 2 progress events"
    }
    fn parameters_schema(&self) -> Value {
        json!({ "type": "object", "properties": {} })
    }
    async fn execute(&self, _args: Value) -> anyhow::Result<ToolResult> {
        Ok(ToolResult {
            success: true,
            output: "done".into(),
            error: None,
        })
    }
    async fn execute_with_progress(
        &self,
        args: Value,
        sink: &dyn ProgressSink,
    ) -> anyhow::Result<ToolResult> {
        let handle = ProgressHandle::new(sink, Some(2));
        handle.update(1, Some("step one"));
        tokio::time::sleep(std::time::Duration::from_millis(20)).await;
        handle.update(2, Some("step two"));
        self.execute(args).await
    }
}

// ── Helpers ────────────────────────────────────────────────────────────────

async fn spawn_default_server() -> (revka::mcp_server::McpServerHandle, reqwest::Client) {
    let (state, _skipped) = revka::mcp_server::server::default_state(std::path::Path::new("."));
    let handle = serve_on(SocketAddr::from(([127, 0, 0, 1], 0)), state)
        .await
        .expect("bind");
    (handle, reqwest::Client::new())
}

async fn spawn_with_tools(
    extra: Vec<Arc<dyn Tool>>,
) -> (revka::mcp_server::McpServerHandle, reqwest::Client) {
    // Start from default registry and append.
    let (mut default_state, _skipped) =
        revka::mcp_server::server::default_state(std::path::Path::new("."));
    // The server state's tool map is `Arc<HashMap>` — build a new one that includes both.
    let mut combined: std::collections::HashMap<String, Arc<dyn Tool>> =
        (*default_state.tools).clone();
    for t in extra {
        combined.insert(t.name().to_string(), t);
    }
    default_state = AppState {
        sessions: Arc::new(SessionStore::new()),
        tools: Arc::new(combined),
    };
    let handle = serve_on(SocketAddr::from(([127, 0, 0, 1], 0)), default_state)
        .await
        .expect("bind");
    (handle, reqwest::Client::new())
}

async fn post_session(client: &reqwest::Client, addr: SocketAddr) -> (String, String) {
    let resp = client
        .post(format!("http://{addr}/session"))
        .json(&json!({ "label": "test" }))
        .send()
        .await
        .expect("POST /session");
    assert!(resp.status().is_success());
    let body: Value = resp.json().await.unwrap();
    (
        body["session_id"].as_str().unwrap().to_string(),
        body["token"].as_str().unwrap().to_string(),
    )
}

// ── Tests ──────────────────────────────────────────────────────────────────

#[tokio::test]
async fn mcp_session_and_tools_list() {
    let (handle, client) = spawn_default_server().await;
    let addr = handle.addr;

    // /mcp without auth → 401
    let unauth = client
        .post(format!("http://{addr}/mcp"))
        .json(&json!({ "jsonrpc": "2.0", "id": 1, "method": "initialize" }))
        .send()
        .await
        .unwrap();
    assert_eq!(unauth.status(), reqwest::StatusCode::UNAUTHORIZED);

    let (session_id, token) = post_session(&client, addr).await;

    // initialize
    let init: Value = client
        .post(format!("http://{addr}/mcp"))
        .bearer_auth(&token)
        .header("X-Revka-Session", &session_id)
        .json(&json!({ "jsonrpc": "2.0", "id": 1, "method": "initialize" }))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(init["jsonrpc"], "2.0");
    assert_eq!(init["result"]["serverInfo"]["name"], "revka-mcp");
    assert_eq!(init["result"]["protocolVersion"], "2024-11-05");

    // tools/list
    let list: Value = client
        .post(format!("http://{addr}/mcp"))
        .bearer_auth(&token)
        .header("X-Revka-Session", &session_id)
        .json(&json!({ "jsonrpc": "2.0", "id": 2, "method": "tools/list" }))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let tools = list["result"]["tools"].as_array().expect("tools array");
    assert!(
        tools.len() >= 10,
        "expected at least 10 tools, got {}",
        tools.len()
    );
    // Sanity — shell + file_read must be present.
    let names: Vec<&str> = tools.iter().filter_map(|t| t["name"].as_str()).collect();
    assert!(names.contains(&"shell"));
    assert!(names.contains(&"file_read"));

    let _ = handle.shutdown.send(());
    let _ = handle.joined.await;
}

#[tokio::test]
async fn mcp_progress_events_stream_over_sse() {
    let (handle, client) = spawn_with_tools(vec![Arc::new(SleepyTool)]).await;
    let addr = handle.addr;
    let (session_id, token) = post_session(&client, addr).await;

    let resp = client
        .post(format!("http://{addr}/mcp"))
        .bearer_auth(&token)
        .header("X-Revka-Session", &session_id)
        .json(&json!({
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": {
                "name": "sleepy",
                "arguments": {},
                "_meta": { "progressToken": 777 }
            }
        }))
        .send()
        .await
        .unwrap();

    assert_eq!(resp.status(), reqwest::StatusCode::OK);
    let ct = resp
        .headers()
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_string();
    assert!(
        ct.starts_with("text/event-stream"),
        "expected SSE content type, got `{ct}`"
    );

    // Read chunks until we see a terminal (id-bearing) JSON-RPC message.
    // Buffer across chunk boundaries and split on "\n\n" (SSE event separator).
    let mut buf = String::new();
    let mut progresses: Vec<Value> = Vec::new();
    let mut terminal: Option<Value> = None;
    let mut resp = resp;
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
    while terminal.is_none() && std::time::Instant::now() < deadline {
        let chunk = tokio::time::timeout(std::time::Duration::from_secs(3), resp.chunk())
            .await
            .expect("chunk within timeout")
            .expect("chunk read");
        let Some(bytes) = chunk else { break };
        buf.push_str(&String::from_utf8_lossy(&bytes));
        while let Some(idx) = buf.find("\n\n") {
            let event: String = buf.drain(..idx + 2).collect();
            for line in event.lines() {
                if let Some(payload) = line.strip_prefix("data: ") {
                    let Ok(v) = serde_json::from_str::<Value>(payload) else {
                        continue;
                    };
                    if v.get("method").and_then(Value::as_str) == Some("notifications/progress") {
                        progresses.push(v);
                    } else if v.get("id").is_some() {
                        terminal = Some(v);
                    }
                }
            }
        }
    }

    assert_eq!(
        progresses.len(),
        2,
        "expected 2 progress events, got {}",
        progresses.len()
    );
    assert_eq!(progresses[0]["params"]["progressToken"], 777);
    assert_eq!(progresses[0]["params"]["progress"], 1);
    assert_eq!(progresses[0]["params"]["message"], "step one");
    assert_eq!(progresses[1]["params"]["progress"], 2);

    let final_msg = terminal.expect("terminal response arrived");
    assert_eq!(final_msg["id"], 99);
    assert_eq!(final_msg["result"]["isError"], false);
    assert_eq!(final_msg["result"]["content"][0]["type"], "text");

    let _ = handle.shutdown.send(());
    let _ = handle.joined.await;
}

// ── RuntimeHandles wiring ──────────────────────────────────────────────────
//
// These tests prove the merger mechanism: a handle-less `RuntimeHandles`
// produces the same skip list as `build_tools_with_config`, and supplying a
// single handle flips the matching tool on in `tools/list`.

fn tool_names_map(tools: &[Arc<dyn Tool>]) -> Vec<String> {
    tools.iter().map(|t| t.name().to_string()).collect()
}

#[tokio::test]
async fn runtime_without_handles_skips_handle_dependent_tools() {
    // Empty RuntimeHandles: every handle is None, so workspace / discord_search
    // must NOT appear. (Some tools — llm_task, cron_*, sop_* — only need the
    // Config and so are registered; that's orthogonal to this test.)
    let config = revka::config::Config::default();
    let runtime = RuntimeHandles::empty();
    let (tools, _skipped) = build_tools_with_runtime(std::path::Path::new("."), &config, &runtime);

    let names = tool_names_map(&tools);
    assert!(
        !names.contains(&"workspace".to_string()),
        "workspace should be absent without WorkspaceManager handle; got {names:?}"
    );
    assert!(
        !names.contains(&"discord_search".to_string()),
        "discord_search should be absent without discord_memory handle"
    );
    assert!(
        !names.contains(&"sessions_list".to_string()),
        "sessions_* should be absent without session_store handle"
    );
}

#[tokio::test]
async fn runtime_with_session_store_registers_sessions_tools() {
    // Supply the `session_store` handle (easiest of the handle-backed tools
    // to revka: FS-backed, no credentials) and verify the corresponding
    // tools flip on in tools/list.
    let tmp = tempfile::tempdir().expect("tempdir");
    let store =
        revka::channels::session_store::SessionStore::new(tmp.path()).expect("open session store");
    let backend: Arc<dyn revka::channels::session_backend::SessionBackend> = Arc::new(store);

    let config = revka::config::Config::default();
    let mut runtime = RuntimeHandles::empty();
    runtime.session_store = Some(backend);

    let (tools, _skipped) = build_tools_with_runtime(tmp.path(), &config, &runtime);
    let names = tool_names_map(&tools);
    assert!(
        names.contains(&"sessions_list".to_string()),
        "sessions_list should register when session_store is supplied; got {names:?}"
    );

    // End-to-end: spawn the MCP router with this registry and verify the
    // tool shows up in tools/list over JSON-RPC.
    let map: std::collections::HashMap<String, Arc<dyn Tool>> = tools
        .into_iter()
        .map(|t| (t.name().to_string(), t))
        .collect();
    let state = AppState {
        sessions: Arc::new(SessionStore::new()),
        tools: Arc::new(map),
    };
    let handle = serve_on(SocketAddr::from(([127, 0, 0, 1], 0)), state)
        .await
        .expect("bind");
    let client = reqwest::Client::new();
    let (session_id, token) = post_session(&client, handle.addr).await;

    let list: Value = client
        .post(format!("http://{}/mcp", handle.addr))
        .bearer_auth(&token)
        .header("X-Revka-Session", &session_id)
        .json(&json!({ "jsonrpc": "2.0", "id": 42, "method": "tools/list" }))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let tools = list["result"]["tools"].as_array().expect("tools array");
    let wire_names: Vec<&str> = tools.iter().filter_map(|t| t["name"].as_str()).collect();
    assert!(
        wire_names.contains(&"sessions_list"),
        "sessions_list missing from tools/list; got {wire_names:?}"
    );

    let _ = handle.shutdown.send(());
    let _ = handle.joined.await;
}
