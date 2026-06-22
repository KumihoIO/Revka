//! Local Kumiho Python SDK bridge launcher/client.
//!
//! The hosted FastAPI BFF remains the fallback transport. This module starts a
//! loopback-only Python sidecar from the existing Kumiho venv and lets gateway
//! routes issue FastAPI-shaped `/api/v1/*` calls against the SDK directly.

use super::kumiho_client::KumihoError;
use anyhow::{Context, Result};
use reqwest::{Client, Method, StatusCode};
use serde_json::Value;
use std::fs::OpenOptions;
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::OnceLock;
use std::time::Duration;
use tokio::process::{Child, Command};
use tokio::sync::Mutex;

const BRIDGE_SCRIPT: &str = include_str!("../../resources/sidecars/kumiho_sdk_bridge.py");
const BRIDGE_SCRIPT_NAME: &str = "kumiho_sdk_bridge.py";

/// Process-env var the bridge reads to decide whether tokenless CE routing is
/// allowed. Set by daemon startup only in local-CE mode; cleared in cloud mode.
const CE_ENDPOINT_ENV: &str = "KUMIHO_LOCAL_SERVER_ENDPOINT";
/// CE-only Redis URL exported alongside the CE endpoint; cleared in cloud mode.
const CE_REDIS_ENV: &str = "KUMIHO_UPSTASH_REDIS_URL";

/// Clear stale local-CE env vars so cloud mode is authoritative over routing.
///
/// `send_raw` derives its tokenless-CE decision from the *presence* of
/// `KUMIHO_LOCAL_SERVER_ENDPOINT` in the process env. That var can leak into a
/// cloud-mode daemon (workspace `.env` importer, a prior CE run, or a shell
/// export); left set, a cloud daemon with an empty token would skip the token
/// guard and route gateway traffic tokenlessly to a (typically dead) loopback
/// CE endpoint instead of falling back to hosted FastAPI. Called once early in
/// cloud-mode startup to remove that signal (mirrors how CE mode shadows cloud
/// creds). See memory-3.
pub fn clear_stale_ce_env() {
    // SAFETY: called once early in main before worker threads are spawned.
    unsafe {
        std::env::remove_var(CE_ENDPOINT_ENV);
        std::env::remove_var(CE_REDIS_ENV);
    }
}

#[derive(Debug)]
struct BridgeState {
    base_url: String,
    child: Child,
}

static BRIDGE: OnceLock<Mutex<Option<BridgeState>>> = OnceLock::new();

#[derive(Debug, Clone)]
pub struct BridgeResponse {
    pub status: StatusCode,
    pub body: String,
}

fn bridge_enabled() -> bool {
    !matches!(
        std::env::var("REVKA_KUMIHO_SDK_BRIDGE")
            .unwrap_or_else(|_| "1".to_string())
            .trim()
            .to_ascii_lowercase()
            .as_str(),
        "0" | "false" | "no" | "off"
    )
}

fn kumiho_dir() -> Result<PathBuf> {
    Ok(crate::sidecars::revka_root()?.join("kumiho"))
}

fn bridge_script_path() -> Result<PathBuf> {
    Ok(kumiho_dir()?.join(BRIDGE_SCRIPT_NAME))
}

fn venv_python(dir: &Path) -> Option<PathBuf> {
    if cfg!(windows) {
        let candidate = dir.join("venv").join("Scripts").join("python.exe");
        candidate.exists().then_some(candidate)
    } else {
        let python3 = dir.join("venv").join("bin").join("python3");
        if python3.exists() {
            return Some(python3);
        }
        let python = dir.join("venv").join("bin").join("python");
        python.exists().then_some(python)
    }
}

fn materialize_bridge_script() -> Result<PathBuf> {
    let dir = kumiho_dir()?;
    std::fs::create_dir_all(&dir).with_context(|| format!("creating {}", dir.display()))?;
    let script = bridge_script_path()?;
    let write = match std::fs::read_to_string(&script) {
        Ok(existing) => existing != BRIDGE_SCRIPT,
        Err(_) => true,
    };
    if write {
        std::fs::write(&script, BRIDGE_SCRIPT)
            .with_context(|| format!("writing {}", script.display()))?;
    }
    Ok(script)
}

fn reserve_loopback_port() -> Result<u16> {
    let listener = TcpListener::bind("127.0.0.1:0").context("binding loopback bridge port")?;
    let port = listener.local_addr()?.port();
    drop(listener);
    Ok(port)
}

fn log_file(name: &str) -> Option<std::fs::File> {
    let root = crate::sidecars::revka_root().ok()?;
    let dir = root.join("logs");
    std::fs::create_dir_all(&dir).ok()?;
    OpenOptions::new()
        .create(true)
        .append(true)
        .open(dir.join(name))
        .ok()
}

async fn poll_health(client: &Client, base_url: &str) -> bool {
    let deadline = tokio::time::Instant::now() + Duration::from_secs(10);
    while tokio::time::Instant::now() < deadline {
        if let Ok(resp) = client
            .get(format!("{base_url}/health"))
            .timeout(Duration::from_millis(500))
            .send()
            .await
        {
            if resp.status().is_success() {
                return true;
            }
        }
        tokio::time::sleep(Duration::from_millis(150)).await;
    }
    false
}

async fn start_bridge(client: &Client) -> Result<BridgeState> {
    let dir = kumiho_dir()?;
    let python = venv_python(&dir).ok_or_else(|| {
        anyhow::anyhow!(
            "Kumiho sidecar venv not found under {}. Run `revka install --sidecars-only`.",
            dir.display()
        )
    })?;
    let script = materialize_bridge_script()?;
    let port = reserve_loopback_port()?;
    let base_url = format!("http://127.0.0.1:{port}");

    let stderr = log_file("kumiho-sdk-bridge.stderr.log").map(Stdio::from);
    let stdout = log_file("kumiho-sdk-bridge.stdout.log").map(Stdio::from);

    let mut cmd = Command::new(python);
    cmd.arg(script)
        .env("KUMIHO_SDK_BRIDGE_HOST", "127.0.0.1")
        .env("KUMIHO_SDK_BRIDGE_PORT", port.to_string())
        .env("PYTHONUNBUFFERED", "1")
        .env_remove("KUMIHO_AUTO_CONFIGURE")
        .stdin(Stdio::null())
        .stdout(stdout.unwrap_or_else(Stdio::null))
        .stderr(stderr.unwrap_or_else(Stdio::null));

    #[cfg(windows)]
    {
        cmd.creation_flags(0x0800_0000);
    }

    let mut child = cmd.spawn().context("spawning Kumiho SDK bridge")?;
    if poll_health(client, &base_url).await {
        tracing::info!(%base_url, "Kumiho SDK bridge started");
        return Ok(BridgeState { base_url, child });
    }

    let _ = child.kill().await;
    anyhow::bail!("Kumiho SDK bridge did not become healthy");
}

async fn ensure_bridge(client: &Client) -> Option<String> {
    if !bridge_enabled() {
        return None;
    }

    let lock = BRIDGE.get_or_init(|| Mutex::new(None));
    let mut guard = lock.lock().await;
    if let Some(state) = guard.as_mut() {
        match state.child.try_wait() {
            Ok(None) => return Some(state.base_url.clone()),
            Ok(Some(status)) => {
                tracing::warn!(?status, "Kumiho SDK bridge exited; restarting on demand");
                *guard = None;
            }
            Err(err) => {
                tracing::warn!(error = %err, "Kumiho SDK bridge status check failed");
                *guard = None;
            }
        }
    }

    match start_bridge(client).await {
        Ok(state) => {
            let base_url = state.base_url.clone();
            *guard = Some(state);
            Some(base_url)
        }
        Err(err) => {
            tracing::warn!(error = %err, "Kumiho SDK bridge unavailable; falling back to FastAPI");
            None
        }
    }
}

async fn mark_dead() {
    let Some(lock) = BRIDGE.get() else {
        return;
    };
    let mut guard = lock.lock().await;
    if let Some(mut state) = guard.take() {
        let _ = state.child.kill().await;
    }
}

fn is_unsupported_bridge_response(status: StatusCode, body: &str) -> bool {
    if status != StatusCode::NOT_IMPLEMENTED {
        return false;
    }
    serde_json::from_str::<Value>(body)
        .ok()
        .and_then(|v| {
            v.get("error_code")
                .and_then(|c| c.as_str())
                .map(str::to_string)
        })
        .is_some_and(|code| code == "kumiho_sdk_bridge_unsupported")
}

/// Send a FastAPI-shaped request through the local SDK bridge.
///
/// Returns `None` when the bridge is disabled/unavailable or does not support
/// the route, allowing callers to fall back to the hosted FastAPI transport.
pub async fn send_raw(
    client: &Client,
    method: Method,
    path: &str,
    query: Vec<(String, String)>,
    token: &str,
    body: Option<Value>,
) -> Option<std::result::Result<BridgeResponse, KumihoError>> {
    let token = token.trim();
    // Cloud requires a token to use the bridge. Local self-hosted CE is
    // tokenless: when a CE endpoint is configured, still route through the bridge
    // — CE serves gRPC (not JSON REST), so the hosted FastAPI `/api/v1` fallback
    // would receive an undecodable gRPC body. The bridge shim builds a tokenless
    // CE client from KUMIHO_LOCAL_SERVER_ENDPOINT.
    let ce_configured = std::env::var(CE_ENDPOINT_ENV)
        .map(|v| !v.trim().is_empty())
        .unwrap_or(false);
    if token.is_empty() && !ce_configured {
        return None;
    }

    let base_url = ensure_bridge(client).await?;
    let url = if path == "/health" {
        format!("{}/health", base_url.trim_end_matches('/'))
    } else {
        format!("{}/api/v1{}", base_url.trim_end_matches('/'), path)
    };
    let mut req = client
        .request(method, &url)
        .header("X-Kumiho-Token", token)
        .timeout(Duration::from_secs(10));
    if !query.is_empty() {
        req = req.query(&query);
    }
    if let Some(body) = body {
        req = req.json(&body);
    }

    let resp = match req.send().await {
        Ok(resp) => resp,
        Err(err) => {
            tracing::warn!(error = %err, path = %path, "Kumiho SDK bridge request failed");
            mark_dead().await;
            return None;
        }
    };
    let status = resp.status();
    let body = resp.text().await.unwrap_or_default();
    if is_unsupported_bridge_response(status, &body) {
        return None;
    }
    if status.is_server_error() {
        tracing::warn!(
            upstream_status = status.as_u16(),
            path = %path,
            body = %body,
            "Kumiho SDK bridge returned 5xx; falling back to FastAPI"
        );
        return None;
    }
    if !status.is_success() {
        return Some(Err(KumihoError::Api {
            status: status.as_u16(),
            body,
        }));
    }
    Some(Ok(BridgeResponse { status, body }))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Regression for the stale-`KUMIHO_LOCAL_SERVER_ENDPOINT` misroute
    /// (memory-3). Reproduces the leak the fix targets: a cloud-mode daemon
    /// starts with a *stale* `KUMIHO_LOCAL_SERVER_ENDPOINT` set and an empty
    /// service token. Pre-fix, `send_raw` would compute `ce_configured == true`
    /// from that leaked var, skip the token guard, and route tokenlessly to a
    /// dead loopback CE endpoint. The cloud-mode fix is `clear_stale_ce_env()`;
    /// after it runs, the bridge guard must short-circuit to `None` so the
    /// caller falls back to hosted FastAPI. Reverting the clear would leave the
    /// var set and flip this assertion. The env is process-global, so the
    /// mutation is serialized and the prior value restored.
    #[tokio::test]
    async fn cloud_startup_clears_stale_ce_endpoint_so_empty_token_returns_none() {
        static ENV_GUARD: std::sync::Mutex<()> = std::sync::Mutex::new(());
        let _guard = ENV_GUARD.lock().unwrap_or_else(|e| e.into_inner());

        let prev = std::env::var(CE_ENDPOINT_ENV).ok();

        // Simulate the leak: a stale CE endpoint is present in the process env
        // at cloud-mode startup. SAFETY: env mutation is serialized by
        // ENV_GUARD for the duration of this test; restored before returning.
        unsafe { std::env::set_var(CE_ENDPOINT_ENV, "http://127.0.0.1:1/stale") };

        // Pre-condition: with the stale var set, the bridge would treat this as
        // CE-configured and bypass the empty-token guard — the bug.
        let ce_configured = std::env::var(CE_ENDPOINT_ENV)
            .map(|v| !v.trim().is_empty())
            .unwrap_or(false);
        assert!(
            ce_configured,
            "test setup: stale CE endpoint should read as CE-configured before the fix runs"
        );

        // Apply the cloud-mode fix: clear the leaked CE env vars.
        clear_stale_ce_env();

        let client = Client::new();
        let result = send_raw(&client, Method::GET, "/memory/recall", Vec::new(), "", None).await;

        // SAFETY: see above — restore prior state under the same guard.
        unsafe {
            match prev {
                Some(v) => std::env::set_var(CE_ENDPOINT_ENV, v),
                None => std::env::remove_var(CE_ENDPOINT_ENV),
            }
        }

        assert!(
            result.is_none(),
            "after clearing the stale CE endpoint, a cloud-mode bridge call with \
             an empty token must return None to fall back to hosted FastAPI"
        );
    }
}
