//! Local self-hosted Kumiho **Community Edition** provisioning.
//!
//! When a user picks the "Local CE" backend during onboarding, Revka:
//! 1. (optionally) bootstraps the required Neo4j (+ optional Redis) via Docker,
//!    bound to **loopback only** (`127.0.0.1`),
//! 2. hands off to the official CE one-liner installer, which downloads &
//!    SHA-256-verifies the `kumiho_server` binary into `~/.kumiho/bin` and runs
//!    its own interactive `kumiho_server onboard` (Neo4j/Redis/embeddings and
//!    **EULA acceptance** — owned entirely by Kumiho, not Revka), and
//! 3. health-checks the running server on `127.0.0.1:9190` (`/api/_live`).
//!
//! Revka never reimplements the binary download, checksum verification, config
//! writing, or EULA flow — those stay owned by the upstream installer so they
//! can never drift from the server. CE is tokenless and loopback-only; Revka
//! only points its `api_url` (and the memory MCP) at the local endpoint.

use std::process::Command;
use std::time::Duration;

use anyhow::{Result, anyhow};
use console::style;
use dialoguer::{Confirm, Input, Password};

use crate::t;

/// Official CE installer (POSIX): downloads + verifies the binary, then onboards.
const CE_INSTALL_SH_URL: &str =
    "https://github.com/KumihoIO/kumiho-server-community/releases/latest/download/install.sh";

/// Official CE installer (Windows PowerShell).
const CE_INSTALL_PS1_URL: &str =
    "https://github.com/KumihoIO/kumiho-server-community/releases/latest/download/install.ps1";

/// CE loopback REST/gRPC endpoint default (`host:port`). Must match the server
/// default and the `kumiho` SDK's `_DEFAULT_LOCAL_CE_PORT` (`9190`).
const CE_DEFAULT_ENDPOINT: &str = "127.0.0.1:9190";

/// Result of `/api/_live`.
#[derive(Debug, Clone)]
pub struct CeHealth {
    pub status: String,
    pub version: Option<String>,
    pub deployment_mode: Option<String>,
}

impl CeHealth {
    /// True when the server self-reports the self-hosted CE deployment mode.
    pub fn is_ce(&self) -> bool {
        self.deployment_mode.as_deref() == Some("self_hosted_ce")
    }
}

/// Outcome of the interactive Local-CE setup, consumed by onboarding.
#[derive(Debug, Clone)]
pub struct LocalCeOutcome {
    /// REST base URL to write into `KumihoConfig.api_url` (loopback http).
    pub api_url: String,
    /// Whether a live CE server was reachable at the end of setup.
    pub healthy: bool,
}

/// Whether `run` created a fresh container or (re)started a pre-existing one.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ContainerAction {
    Created,
    StartedExisting,
}

/// Whether the official prebuilt CE binary supports the current platform.
///
/// The upstream installer hard-rejects Intel macOS (CE ships Apple-Silicon
/// macOS only). On unsupported platforms we skip the download and guide the
/// user toward Docker / a source build instead.
fn platform_supports_prebuilt_ce() -> bool {
    !(cfg!(target_os = "macos") && cfg!(target_arch = "x86_64"))
}

/// Detect an available container runtime for the optional DB bootstrap.
fn detect_container_runtime() -> Option<&'static str> {
    ["docker", "podman"].into_iter().find(|&rt| {
        Command::new(rt)
            .arg("--version")
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status()
            .map(|s| s.success())
            .unwrap_or(false)
    })
}

/// `docker run` args for the CE Neo4j container, published to **loopback only**
/// (`127.0.0.1`). The auth value is passed via the runner's environment
/// (`-e NEO4J_AUTH`, name-only) so the password never lands in the process
/// argv / `docker inspect` command line.
fn neo4j_run_args() -> Vec<String> {
    vec![
        "run".into(),
        "-d".into(),
        "--name".into(),
        "kumiho-neo4j".into(),
        "-p".into(),
        "127.0.0.1:7687:7687".into(),
        "-p".into(),
        "127.0.0.1:7474:7474".into(),
        "-e".into(),
        "NEO4J_AUTH".into(),
        "neo4j:5".into(),
    ]
}

/// `docker run` args for the optional CE Redis container (loopback only).
fn redis_run_args() -> Vec<String> {
    vec![
        "run".into(),
        "-d".into(),
        "--name".into(),
        "kumiho-redis".into(),
        "-p".into(),
        "127.0.0.1:6379:6379".into(),
        "redis:7".into(),
    ]
}

/// Start a named container; if one already exists, start it instead of failing.
///
/// `env` pairs are set on the `docker run` invocation (used to pass secrets such
/// as the Neo4j password without placing them on the command line). Returns
/// whether a fresh container was created or a pre-existing one was (re)started —
/// the latter ignores any newly-supplied env/auth, which the caller must
/// account for.
fn run_or_start_container(
    runtime: &str,
    name: &str,
    run_args: &[String],
    env: &[(&str, String)],
) -> Result<ContainerAction> {
    let run_refs: Vec<&str> = run_args.iter().map(String::as_str).collect();
    let mut cmd = Command::new(runtime);
    cmd.args(&run_refs);
    for (key, value) in env {
        cmd.env(key, value);
    }
    let out = cmd.output()?;
    if out.status.success() {
        return Ok(ContainerAction::Created);
    }
    let stderr = String::from_utf8_lossy(&out.stderr);
    // Most likely cause of failure: the container name is already in use.
    if stderr.contains("already in use") || stderr.contains("Conflict") {
        let start = Command::new(runtime).args(["start", name]).output()?;
        if start.status.success() {
            return Ok(ContainerAction::StartedExisting);
        }
        return Err(anyhow!(
            "could not start existing {name} container: {}",
            String::from_utf8_lossy(&start.stderr).trim()
        ));
    }
    Err(anyhow!("`{runtime} run {name}` failed: {}", stderr.trim()))
}

/// Poll a loopback TCP port until it accepts a connection (or attempts run out).
fn wait_tcp(addr: &str, attempts: u32, interval: Duration) -> bool {
    use std::net::TcpStream;
    for _ in 0..attempts {
        if TcpStream::connect(addr).is_ok() {
            return true;
        }
        std::thread::sleep(interval);
    }
    false
}

/// True if `endpoint` (`host` or `host:port`) names a loopback address.
fn is_loopback_endpoint(endpoint: &str) -> bool {
    let host = match endpoint.rsplit_once(':') {
        // Bare `host` (no port) or an unbracketed IPv6 — fall back to the whole
        // string; bracketed IPv6 (`[::1]:p`) splits cleanly on the last colon.
        Some((h, _)) if h.contains(':') && !h.starts_with('[') => endpoint,
        Some((h, _)) => h,
        None => endpoint,
    };
    let host = host.trim_start_matches('[').trim_end_matches(']');
    if host.eq_ignore_ascii_case("localhost") {
        return true;
    }
    host.parse::<std::net::IpAddr>()
        .map(|ip| ip.is_loopback())
        .unwrap_or(false)
}

/// Blocking HTTP GET on a dedicated OS thread.
///
/// Building/dropping a `reqwest::blocking` client on a fresh thread avoids the
/// "cannot start a runtime from within a runtime" panic when called from inside
/// an async context (e.g. the gateway's `doctor::diagnose`). Note this still
/// **blocks the calling thread** until the child returns (panic-safe, not
/// non-blocking).
fn http_get(url: String, timeout: Duration) -> Result<(u16, String)> {
    std::thread::spawn(move || -> Result<(u16, String)> {
        let client = reqwest::blocking::Client::builder()
            .timeout(timeout)
            .build()?;
        let resp = client.get(&url).send()?;
        let code = resp.status().as_u16();
        let text = resp.text().unwrap_or_default();
        Ok((code, text))
    })
    .join()
    .map_err(|_| anyhow!("CE health probe thread panicked"))?
}

/// Probe `GET http://{endpoint}/api/_health` and return the parsed JSON
/// (component readiness for Neo4j, the Redis event stream, and embeddings).
pub fn probe_health(endpoint: &str, timeout: Duration) -> Result<serde_json::Value> {
    let (status, body) = http_get(format!("http://{endpoint}/api/_health"), timeout)?;
    let json: serde_json::Value = serde_json::from_str(&body)
        .map_err(|e| anyhow!("CE /api/_health returned non-JSON (HTTP {status}): {e}"))?;
    Ok(json)
}

/// Probe `GET http://{endpoint}/api/_live` once.
pub fn probe_live(endpoint: &str, timeout: Duration) -> Result<CeHealth> {
    let (status, body) = http_get(format!("http://{endpoint}/api/_live"), timeout)?;
    if status >= 400 {
        return Err(anyhow!("CE /api/_live returned HTTP {status}"));
    }
    let json: serde_json::Value =
        serde_json::from_str(&body).map_err(|e| anyhow!("CE /api/_live returned non-JSON: {e}"))?;
    Ok(CeHealth {
        status: json
            .get("status")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        version: json
            .get("version")
            .and_then(|v| v.as_str())
            .map(str::to_string),
        deployment_mode: json
            .get("deployment_mode")
            .and_then(|v| v.as_str())
            .map(str::to_string),
    })
}

/// Poll `/api/_live` until the server reports CE mode (or attempts run out).
fn wait_for_health(endpoint: &str, attempts: u32, interval: Duration) -> Option<CeHealth> {
    for _ in 0..attempts {
        if let Ok(health) = probe_live(endpoint, Duration::from_millis(800)) {
            if health.is_ce() {
                return Some(health);
            }
        }
        std::thread::sleep(interval);
    }
    None
}

/// Whether PowerShell 7+ (`pwsh`) is available on PATH. Preferred over the
/// built-in Windows PowerShell, which on some hosts is an older build missing
/// cmdlets the installer relies on.
fn powershell7_available() -> bool {
    Command::new("pwsh")
        .args(["-NoProfile", "-NoLogo", "-Command", "exit 0"])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

/// Run the official CE one-liner installer interactively (inherits stdio so the
/// user can complete `kumiho_server onboard` — Neo4j creds, ports, embeddings,
/// and EULA acceptance — directly). Prefers `pwsh` (PowerShell 7+) on Windows,
/// falling back to `powershell`; uses `sh` elsewhere.
fn run_official_installer() -> Result<()> {
    let status = if cfg!(windows) {
        // Prefer PowerShell 7+ (`pwsh`) when present. The built-in Windows
        // PowerShell (`powershell.exe`) can be an older build (e.g. 3.0/4.0)
        // that lacks cmdlets the CE installer uses (`Get-FileHash`,
        // `Expand-Archive`); `pwsh` always has them. Fall back to
        // `powershell` only when `pwsh` is not installed.
        let shell = if powershell7_available() {
            "pwsh"
        } else {
            "powershell"
        };
        Command::new(shell)
            .args([
                "-NoProfile",
                "-Command",
                &format!("irm {CE_INSTALL_PS1_URL} | iex"),
            ])
            .status()
    } else {
        Command::new("sh")
            .arg("-c")
            .arg(format!("curl -fsSL {CE_INSTALL_SH_URL} | sh"))
            .status()
    }
    .map_err(|e| anyhow!("failed to launch CE installer: {e}"))?;
    if !status.success() {
        return Err(anyhow!(
            "CE installer exited with status {}",
            status.code().unwrap_or(-1)
        ));
    }
    Ok(())
}

/// Interactive Local-CE setup, invoked from the onboarding wizard.
///
/// The container/installer sub-steps are **best-effort**: each failure is
/// surfaced as a warning and setup continues (the user has already chosen the
/// Local-CE backend, so the returned `api_url` is written regardless and
/// `revka doctor` verifies the server later). Interactive-prompt failures
/// (e.g. no TTY, or the user aborting) propagate as `Err`; the caller recovers
/// by recording the Local-CE mode + default endpoint anyway.
pub fn setup_local_ce() -> Result<LocalCeOutcome> {
    println!();
    println!(
        "  {} {}",
        style("◆").cyan().bold(),
        style(t!("ce-title")).cyan().bold()
    );
    println!("    {}", t!("ce-intro-1"));
    println!("    {}", t!("ce-intro-2"));
    println!("    {}", t!("ce-intro-3"));
    println!("    {}", t!("ce-intro-4"));
    println!();

    // ── Optional: bootstrap Neo4j (+ Redis) via Docker ────────────────
    if let Some(runtime) = detect_container_runtime() {
        let want_db = Confirm::new()
            .with_prompt(format!(
                "  {}",
                t!("ce-neo4j-start-prompt", runtime = runtime)
            ))
            .default(true)
            .interact()?;
        if want_db {
            let password: String = Password::new()
                .with_prompt(format!("  {}", t!("ce-neo4j-password")))
                .with_confirmation(
                    format!("  {}", t!("ce-confirm-password")),
                    format!("  {}", t!("ce-password-mismatch")),
                )
                .interact()?;
            let with_redis = Confirm::new()
                .with_prompt(format!("  {}", t!("ce-redis-start-prompt")))
                .default(true)
                .interact()?;

            let mut reused_neo4j = false;
            match run_or_start_container(
                runtime,
                "kumiho-neo4j",
                &neo4j_run_args(),
                &[("NEO4J_AUTH", format!("neo4j/{password}"))],
            ) {
                Ok(ContainerAction::Created) => {
                    print_ok(&t!("ce-neo4j-starting", runtime = runtime));
                    if wait_tcp("127.0.0.1:7687", 30, Duration::from_secs(2)) {
                        print_ok(&t!("ce-neo4j-ready"));
                    } else {
                        print_warn(&t!("ce-neo4j-not-ready"));
                    }
                }
                Ok(ContainerAction::StartedExisting) => {
                    reused_neo4j = true;
                    print_warn(&t!("ce-neo4j-reused"));
                }
                Err(e) => print_warn(&t!("ce-neo4j-failed", err = e.to_string())),
            }

            if with_redis {
                match run_or_start_container(runtime, "kumiho-redis", &redis_run_args(), &[]) {
                    Ok(_) => print_ok(&t!("ce-redis-starting")),
                    Err(e) => print_warn(&t!("ce-redis-failed", err = e.to_string())),
                }
            }

            println!();
            println!("  {} {}", style("→").cyan(), t!("ce-wizard-hint-header"));
            let pw_hint = if reused_neo4j {
                t!("ce-pw-hint-existing")
            } else {
                t!("ce-pw-hint-new")
            };
            println!("      {}", t!("ce-neo4j-creds", hint = pw_hint));
            if with_redis {
                println!("      {}", t!("ce-redis-port"));
            }
            println!();
        }
    } else {
        print_warn(&t!("ce-no-docker"));
        println!();
    }

    // ── Install + onboard the CE server ───────────────────────────────
    if !platform_supports_prebuilt_ce() {
        print_warn(&t!("ce-no-prebuilt-macos"));
        return Ok(LocalCeOutcome {
            api_url: format!("http://{CE_DEFAULT_ENDPOINT}"),
            healthy: false,
        });
    }

    let run_now = Confirm::new()
        .with_prompt(format!("  {}", t!("ce-install-prompt")))
        .default(true)
        .interact()?;

    if !run_now {
        print_warn(&format!(
            "{}\n      curl -fsSL {CE_INSTALL_SH_URL} | sh",
            t!("ce-install-skipped")
        ));
        return Ok(LocalCeOutcome {
            api_url: format!("http://{CE_DEFAULT_ENDPOINT}"),
            healthy: false,
        });
    }

    println!();
    println!("  {} {}", style("▶").cyan().bold(), t!("ce-handoff"));
    println!();

    if let Err(e) = run_official_installer() {
        print_warn(&format!(
            "{}\n      {}  curl -fsSL {CE_INSTALL_SH_URL} | sh",
            t!("ce-installer-failed", err = e.to_string()),
            t!("ce-installer-rerun-hint")
        ));
    }

    // Custom endpoint support: let advanced users point at a non-default port if
    // their CE server listens elsewhere. CE is loopback-only, so warn (but do
    // not hard-block) on a non-loopback host.
    let endpoint: String = Input::new()
        .with_prompt(format!("  {}", t!("ce-endpoint-prompt")))
        .default(CE_DEFAULT_ENDPOINT.to_string())
        .interact_text()?;
    let endpoint = endpoint.trim().to_string();
    if !is_loopback_endpoint(&endpoint) {
        print_warn(&t!("ce-non-loopback", endpoint = &endpoint));
    }

    println!();
    println!("  {} {}", style("…").cyan(), t!("ce-checking"));
    let healthy = match wait_for_health(&endpoint, 20, Duration::from_secs(1)) {
        Some(health) => {
            let suffix = health
                .version
                .map(|v| format!(", v{v}"))
                .unwrap_or_default();
            print_ok(&t!("ce-reachable", suffix = suffix));
            true
        }
        None => {
            print_warn(&t!("ce-unreachable", endpoint = &endpoint));
            false
        }
    };

    Ok(LocalCeOutcome {
        api_url: format!("http://{endpoint}"),
        healthy,
    })
}

fn print_ok(msg: &str) {
    println!("  {} {msg}", style("✓").green().bold());
}

fn print_warn(msg: &str) {
    println!("  {} {msg}", style("!").yellow().bold());
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn neo4j_args_publish_loopback_ports_and_pass_auth_via_env() {
        let args = neo4j_run_args();
        // Ports must be bound to loopback, not 0.0.0.0.
        assert!(args.contains(&"127.0.0.1:7687:7687".to_string()));
        assert!(args.contains(&"127.0.0.1:7474:7474".to_string()));
        assert!(!args.iter().any(|a| a == "7687:7687"));
        assert!(args.contains(&"neo4j:5".to_string()));
        // Password is passed via env (name-only -e), never on the command line.
        assert!(args.contains(&"NEO4J_AUTH".to_string()));
        assert!(!args.iter().any(|a| a.contains("NEO4J_AUTH=")));
    }

    #[test]
    fn redis_args_publish_loopback_port() {
        let args = redis_run_args();
        assert!(args.contains(&"127.0.0.1:6379:6379".to_string()));
        assert!(!args.iter().any(|a| a == "6379:6379"));
        assert!(args.contains(&"redis:7".to_string()));
    }

    #[test]
    fn ce_health_detects_self_hosted_mode() {
        let ce = CeHealth {
            status: "ok".into(),
            version: Some("1.3.0".into()),
            deployment_mode: Some("self_hosted_ce".into()),
        };
        assert!(ce.is_ce());
        let cloud = CeHealth {
            status: "ok".into(),
            version: None,
            deployment_mode: Some("cloud".into()),
        };
        assert!(!cloud.is_ce());
    }

    #[test]
    fn loopback_endpoint_detection() {
        assert!(is_loopback_endpoint("127.0.0.1:9190"));
        assert!(is_loopback_endpoint("127.0.0.1"));
        assert!(is_loopback_endpoint("localhost:9190"));
        assert!(is_loopback_endpoint("[::1]:9190"));
        assert!(!is_loopback_endpoint("10.0.0.5:9190"));
        assert!(!is_loopback_endpoint("example.com:9190"));
        assert!(!is_loopback_endpoint("0.0.0.0:9190"));
    }
}
