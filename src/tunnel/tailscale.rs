use super::{SharedProcess, Tunnel, TunnelProcess, kill_shared, new_shared_process};
use anyhow::{Result, bail};
use std::sync::atomic::{AtomicBool, Ordering};
use tokio::process::Command;

/// Tailscale Tunnel — uses `tailscale serve` (tailnet-only) or
/// `tailscale funnel` (public internet).
///
/// Requires Tailscale installed and authenticated (`tailscale up`).
pub struct TailscaleTunnel {
    funnel: bool,
    hostname: Option<String>,
    proc: SharedProcess,
    /// Whether `start()` has configured an active serve/funnel that `stop()` (or
    /// `Drop`) must reset. A plain flag (not the async `proc` lock) so the Drop
    /// guard can read it without ever failing open on lock contention (#427).
    started: AtomicBool,
}

impl TailscaleTunnel {
    pub fn new(funnel: bool, hostname: Option<String>) -> Self {
        Self {
            funnel,
            hostname,
            proc: new_shared_process(),
            started: AtomicBool::new(false),
        }
    }
}

#[async_trait::async_trait]
impl Tunnel for TailscaleTunnel {
    fn name(&self) -> &str {
        "tailscale"
    }

    async fn start(&self, _local_host: &str, local_port: u16) -> Result<String> {
        let subcommand = if self.funnel { "funnel" } else { "serve" };

        // Get the tailscale hostname for URL construction
        let hostname = if let Some(ref h) = self.hostname {
            h.clone()
        } else {
            // Query tailscale for the current hostname
            let output = Command::new("tailscale")
                .args(["status", "--json"])
                .output()
                .await?;

            if !output.status.success() {
                bail!(
                    "tailscale status failed: {}",
                    String::from_utf8_lossy(&output.stderr)
                );
            }

            let status: serde_json::Value =
                serde_json::from_slice(&output.stdout).unwrap_or_default();
            status["Self"]["DNSName"]
                .as_str()
                .unwrap_or("localhost")
                .trim_end_matches('.')
                .to_string()
        };

        // tailscale serve|funnel <port>
        let child = Command::new("tailscale")
            .args([subcommand, &local_port.to_string()])
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .kill_on_drop(true)
            .spawn()?;

        let public_url = format!("https://{hostname}:{local_port}");

        let mut guard = self.proc.lock().await;
        *guard = Some(TunnelProcess {
            child,
            public_url: public_url.clone(),
        });
        self.started.store(true, Ordering::Relaxed);

        Ok(public_url)
    }

    async fn stop(&self) -> Result<()> {
        // No longer active — also disarms the Drop-time reset below.
        self.started.store(false, Ordering::Relaxed);
        // Also reset the tailscale serve/funnel
        let subcommand = if self.funnel { "funnel" } else { "serve" };
        Command::new("tailscale")
            .args([subcommand, "reset"])
            .output()
            .await
            .ok();

        kill_shared(&self.proc).await
    }

    async fn health_check(&self) -> bool {
        let guard = self.proc.lock().await;
        guard.as_ref().is_some_and(|tp| tp.child.id().is_some())
    }

    fn public_url(&self) -> Option<String> {
        self.proc
            .try_lock()
            .ok()
            .and_then(|g| g.as_ref().map(|tp| tp.public_url.clone()))
    }
}

impl Drop for TailscaleTunnel {
    /// Best-effort **synchronous** teardown of the public exposure (#427).
    ///
    /// The async `stop()` is the normal path, but on abrupt shutdown — the
    /// daemon aborts the gateway on SIGTERM, then runtime teardown cancels the
    /// tunnel supervisor — `stop()` may never run. Unlike the child-process
    /// backends, a Tailscale funnel lives in `tailscaled`, so `kill_on_drop` of
    /// the spawned CLI child does NOT remove it; without this reset the public
    /// funnel can outlive the gateway. This runs on every drop path (abort,
    /// teardown, panic) and is a no-op (idempotent) if `stop()` already reset.
    fn drop(&mut self) {
        // Only reset if a serve/funnel is active — avoids shelling out to
        // `tailscale` for every dropped (never-started or already-stopped)
        // instance. A plain atomic load never fails open under lock contention.
        if !self.started.load(Ordering::Relaxed) {
            return;
        }
        let subcommand = if self.funnel { "funnel" } else { "serve" };
        let _ = std::process::Command::new("tailscale")
            .args([subcommand, "reset"])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn constructor_stores_hostname_and_mode() {
        let tunnel = TailscaleTunnel::new(true, Some("myhost.tailnet.ts.net".into()));
        assert!(tunnel.funnel);
        assert_eq!(tunnel.hostname.as_deref(), Some("myhost.tailnet.ts.net"));
    }

    #[test]
    fn public_url_is_none_before_start() {
        let tunnel = TailscaleTunnel::new(false, None);
        assert!(tunnel.public_url().is_none());
    }

    #[tokio::test]
    async fn health_check_is_false_before_start() {
        let tunnel = TailscaleTunnel::new(false, None);
        assert!(!tunnel.health_check().await);
    }

    #[tokio::test]
    async fn stop_without_started_process_is_ok() {
        let tunnel = TailscaleTunnel::new(false, None);
        let result = tunnel.stop().await;
        assert!(result.is_ok());
    }

    #[test]
    fn drop_without_started_process_is_a_noop() {
        // The Drop guard must early-return when the tunnel was never started, so
        // it does not invoke the `tailscale` CLI for unstarted instances (#427).
        let tunnel = TailscaleTunnel::new(true, None);
        drop(tunnel); // must not panic or shell out
    }
}
