//! Discovery of the local in-process MCP server.
//!
//! The MCP server runs as a tokio task inside the main revka daemon
//! (see `gateway::run_gateway`). On boot it writes `~/.revka/mcp.json`
//! containing `{url, pid, started_at}`. Gateway code (notably the WS
//! terminal handler that launches CLI tools, plus the `/api/mcp/discovery`
//! route) reads that file to learn where MCP is listening. Same file
//! shape external CLIs (Claude Code, Codex, etc.) use for discovery.
//!
//! ## Caching
//!
//! The gateway is long-running; the daemon may restart between requests,
//! which changes the port. Caching naively with `OnceLock` would serve a
//! stale URL forever. Instead we cache the parsed value together with the
//! file's mtime in an `RwLock` — on each read we stat the file and only
//! re-parse when the mtime changed.
//!
//! `read_revka_mcp()` keeps its historical name and now transparently
//! does the mtime check so existing callers (terminal spawner) stay hot.
//!
//! Returns a clear error when the file is absent so the caller can surface
//! a helpful message to the frontend instead of crashing.

use anyhow::{Context, Result, anyhow};
use serde::Deserialize;
use std::path::PathBuf;
use std::sync::RwLock;
use std::time::SystemTime;

#[derive(Debug, Clone, Deserialize)]
pub struct McpDiscovery {
    pub url: String,
    #[serde(default)]
    pub pid: Option<u32>,
    #[serde(default)]
    pub started_at: Option<String>,
}

#[derive(Clone)]
struct Cached {
    mtime: Option<SystemTime>,
    value: McpDiscovery,
}

static CACHE: RwLock<Option<Cached>> = RwLock::new(None);

/// Canonical location of the daemon's discovery file.
pub fn discovery_path() -> Option<PathBuf> {
    directories::UserDirs::new().map(|u| u.home_dir().join(".revka").join("mcp.json"))
}

fn file_mtime(path: &std::path::Path) -> Option<SystemTime> {
    std::fs::metadata(path).and_then(|m| m.modified()).ok()
}

/// Read the discovery file, caching the result keyed by file mtime.
///
/// Returns Err when the file is missing or malformed. Callers should report
/// this to the user as "no MCP daemon available".
pub fn read_revka_mcp() -> Result<McpDiscovery> {
    let path = discovery_path()
        .ok_or_else(|| anyhow!("could not resolve home directory for ~/.revka/mcp.json"))?;
    let current_mtime = file_mtime(&path);

    // Fast path: cached, mtime matches.
    if let Some(cached) = CACHE.read().ok().and_then(|g| g.clone()) {
        if cached.mtime == current_mtime {
            return Ok(cached.value);
        }
    }

    // Slow path: re-read + re-parse.
    let bytes = std::fs::read(&path).with_context(|| format!("reading {}", path.display()))?;
    let parsed: McpDiscovery =
        serde_json::from_slice(&bytes).with_context(|| format!("parsing {}", path.display()))?;

    if let Ok(mut guard) = CACHE.write() {
        *guard = Some(Cached {
            mtime: current_mtime,
            value: parsed.clone(),
        });
    }
    Ok(parsed)
}

/// Test-only helper to parse an explicit JSON payload (no filesystem).
#[cfg(test)]
pub fn parse_discovery(bytes: &[u8]) -> Result<McpDiscovery> {
    Ok(serde_json::from_slice(bytes)?)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_valid_discovery() {
        let payload =
            br#"{"url":"http://127.0.0.1:54500/mcp","pid":1,"started_at":"2026-04-17T00:00:00Z"}"#;
        let d = parse_discovery(payload).unwrap();
        assert_eq!(d.url, "http://127.0.0.1:54500/mcp");
        assert_eq!(d.pid, Some(1));
    }

    #[test]
    fn parses_minimal_discovery() {
        let payload = br#"{"url":"http://x/y"}"#;
        let d = parse_discovery(payload).unwrap();
        assert_eq!(d.url, "http://x/y");
        assert_eq!(d.pid, None);
    }

    #[test]
    fn rejects_bad_json() {
        assert!(parse_discovery(b"not json").is_err());
    }
}
