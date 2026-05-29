//! WebSocket PTY terminal handler.
//!
//! Connect: `ws://host:port/ws/terminal?session_id=ID&token=TOKEN`
//!
//! The handler spawns a PTY shell and bridges I/O bidirectionally over
//! the WebSocket. The frontend (xterm.js) sends raw keystrokes as text
//! frames and receives terminal output as text or binary frames.
//!
//! ## Protocol
//!
//! ```text
//! Client -> Server: raw keystroke data (text frame)
//! Client -> Server: {"type":"resize","cols":120,"rows":40}
//! Server -> Client: terminal output (text frame)
//! ```
//!
//! ## Tool-aware Code tab (M2)
//!
//! When `tool=<claude|codex|opencode|gemini|google_agents>` is passed in the query, this
//! handler spawns that CLI inside the PTY instead of the user's shell. When
//! `mcp_session` and `mcp_token` are also present, a per-session MCP config
//! file is written to a temp dir and exported via env vars so the CLI can
//! auto-register the in-process MCP server (run as a tokio task inside the
//! main daemon; discovered via `~/.construct/mcp.json`). The temp dir is
//! removed when the socket closes.

use super::AppState;
use super::mcp_discovery::read_construct_mcp;
use axum::{
    extract::{
        Query, State, WebSocketUpgrade,
        ws::{Message, WebSocket},
    },
    http::{HeaderMap, StatusCode, header},
    response::IntoResponse,
};
use futures_util::{SinkExt, StreamExt};
use portable_pty::{CommandBuilder, NativePtySystem, PtySize, PtySystem};
use serde::Deserialize;
use serde_json::json;
use std::io::{Read, Write};
use std::path::PathBuf;
use tracing::{debug, error, warn};
use uuid::Uuid;

/// The sub-protocol we support for terminal WebSocket.
const WS_PROTOCOL: &str = "construct.v1";

/// Prefix used in `Sec-WebSocket-Protocol` to carry a bearer token.
const BEARER_SUBPROTO_PREFIX: &str = "bearer.";

#[derive(Deserialize, Default)]
pub struct TerminalQuery {
    pub token: Option<String>,
    pub session_id: Option<String>,
    /// Optional CLI tool to launch instead of the default shell.
    /// Known values: `claude` | `codex` | `opencode` | `gemini` | `google_agents`.
    /// Anything else (or `None`) falls back to `$SHELL -l`.
    pub tool: Option<String>,
    /// Optional explicit working directory for the spawned process. Tilde
    /// expansion is applied. Rejected (error frame) if it does not resolve
    /// to a directory.
    pub cwd: Option<String>,
    /// Session id issued by the in-process MCP server (see
    /// `mcp_server` — runs as a tokio task inside this daemon).
    pub mcp_session: Option<String>,
    /// Bearer token issued by the in-process MCP server.
    pub mcp_token: Option<String>,
    /// Initial terminal column count (defaults to 80 if absent/zero). Supplied
    /// by the frontend after xterm's FitAddon measures the container, so the
    /// child process's first layout matches what the user actually sees —
    /// avoiding a 80×24 → resize repaint race that garbles TUIs.
    pub cols: Option<u16>,
    /// Initial terminal row count (defaults to 24 if absent/zero).
    pub rows: Option<u16>,
}

/// Resize message sent from xterm.js frontend.
#[derive(Deserialize)]
struct ResizeMsg {
    #[serde(rename = "type")]
    msg_type: String,
    cols: u16,
    rows: u16,
}

/// Known CLI tools the Code tab can spawn.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CodeTool {
    Claude,
    Codex,
    OpenCode,
    Gemini,
    GoogleAgents,
}

impl CodeTool {
    /// Parse the `tool` query parameter. Unknown strings return `None`, in
    /// which case the handler falls back to spawning the user's shell.
    pub fn from_query(s: &str) -> Option<Self> {
        match s {
            "claude" => Some(Self::Claude),
            "codex" => Some(Self::Codex),
            "opencode" => Some(Self::OpenCode),
            "gemini" => Some(Self::Gemini),
            "google_agents" | "google-agents" | "agents-cli" => Some(Self::GoogleAgents),
            _ => None,
        }
    }

    /// Binary name to look up in `$PATH`.
    pub fn binary(self) -> &'static str {
        match self {
            Self::Claude => "claude",
            Self::Codex => "codex",
            Self::OpenCode => "opencode",
            Self::Gemini => "gemini",
            Self::GoogleAgents => "agents-cli",
        }
    }

    /// Whether this CLI can be wired to Construct's in-process MCP server via
    /// a known config-file adapter.
    pub fn supports_mcp_config(self) -> bool {
        !matches!(self, Self::GoogleAgents)
    }

    /// Env variable each CLI (allegedly) looks at to find an MCP config file.
    ///
    /// Kept for backwards-compat with any older CLI version that honored a
    /// generic env var. The authoritative mechanism for M3 is the per-CLI
    /// adapter in `write_cli_config` — these env names are best-effort and
    /// not relied upon by callers.
    pub fn config_env(self) -> &'static str {
        match self {
            Self::Claude => "CLAUDE_MCP_CONFIG",
            Self::Codex => "CODEX_MCP_CONFIG",
            Self::OpenCode => "OPENCODE_MCP_CONFIG",
            Self::Gemini => "GEMINI_MCP_CONFIG",
            Self::GoogleAgents => "GOOGLE_AGENTS_MCP_CONFIG",
        }
    }
}

/// Adapter result: concrete files written under the spawn's temp HOME, plus
/// any CLI-specific args to pass on the command line.
#[derive(Debug)]
pub struct CliInjection {
    /// Extra argv to append after the binary.
    pub args: Vec<String>,
    /// Files written, for test assertion & debugging: (relative_path, content_sample).
    pub files_written: Vec<PathBuf>,
}

/// Write the per-CLI MCP config under `temp_home`, returning any args that
/// need to be appended to the command line.
///
/// This is the M3 per-CLI adapter. Each branch cites the source that
/// documents the config location / mechanism so future maintainers can
/// re-verify when the upstream CLI changes.
pub fn write_cli_config(
    tool: CodeTool,
    temp_home: &std::path::Path,
    mcp_url: &str,
    session_id: &str,
    token: &str,
) -> Result<CliInjection, String> {
    match tool {
        // ── Claude Code ────────────────────────────────────────────────
        // Source: https://docs.claude.com/en/docs/claude-code/mcp
        //   > claude --mcp-config <path-to-json>
        // The flag accepts the same `{ "mcpServers": { ... } }` shape
        // used by `.mcp.json`. We prefer the flag over dropping `.mcp.json`
        // into the cwd because it avoids mutating the user's repo.
        CodeTool::Claude => {
            let cfg_path = temp_home.join(".mcp.json");
            let cfg = build_mcp_config_json(mcp_url, session_id, token);
            std::fs::write(
                &cfg_path,
                serde_json::to_vec_pretty(&cfg).expect("serialize claude mcp config"),
            )
            .map_err(|e| format!("writing claude mcp config: {e}"))?;
            Ok(CliInjection {
                args: vec![
                    "--mcp-config".into(),
                    cfg_path.to_string_lossy().into_owned(),
                ],
                files_written: vec![cfg_path],
            })
        }

        // ── Codex (OpenAI) ─────────────────────────────────────────────
        // Source: https://github.com/openai/codex — `~/.codex/config.toml`
        // with `[mcp_servers.<name>]` blocks. Recent versions support a
        // `url` + `transport = "http"` entry for Streamable HTTP; we rely
        // on that here. Codex has no per-invocation config flag, so we
        // must redirect HOME to the temp dir.
        CodeTool::Codex => {
            let dir = temp_home.join(".codex");
            std::fs::create_dir_all(&dir).map_err(|e| format!("creating ~/.codex: {e}"))?;
            let cfg_path = dir.join("config.toml");
            let mut toml = String::new();
            toml.push_str("[mcp_servers.construct]\n");
            toml.push_str(&format!("url = {}\n", toml_string(mcp_url)));
            toml.push_str("transport = \"http\"\n");
            toml.push_str("[mcp_servers.construct.headers]\n");
            toml.push_str(&format!(
                "Authorization = {}\n",
                toml_string(&format!("Bearer {token}"))
            ));
            toml.push_str(&format!(
                "X-Construct-Session = {}\n",
                toml_string(session_id)
            ));
            std::fs::write(&cfg_path, toml.as_bytes())
                .map_err(|e| format!("writing codex config: {e}"))?;
            Ok(CliInjection {
                args: vec![],
                files_written: vec![cfg_path],
            })
        }

        // ── OpenCode ───────────────────────────────────────────────────
        // Source: https://opencode.ai/docs — config at
        // `~/.config/opencode/config.json` (XDG_CONFIG_HOME respected)
        // with top-level `mcp` map keyed by server name: each value is
        // `{ type: "remote", url, headers }` for HTTP servers.
        CodeTool::OpenCode => {
            let dir = temp_home.join(".config").join("opencode");
            std::fs::create_dir_all(&dir)
                .map_err(|e| format!("creating opencode config dir: {e}"))?;
            let cfg_path = dir.join("config.json");
            let cfg = json!({
                "$schema": "https://opencode.ai/config.json",
                "mcp": {
                    "construct": {
                        "type": "remote",
                        "url": mcp_url,
                        "enabled": true,
                        "headers": {
                            "Authorization": format!("Bearer {token}"),
                            "X-Construct-Session": session_id,
                        }
                    }
                }
            });
            std::fs::write(
                &cfg_path,
                serde_json::to_vec_pretty(&cfg).expect("serialize opencode config"),
            )
            .map_err(|e| format!("writing opencode config: {e}"))?;
            Ok(CliInjection {
                args: vec![],
                files_written: vec![cfg_path],
            })
        }

        // ── Gemini CLI ─────────────────────────────────────────────────
        // Source: https://github.com/google-gemini/gemini-cli — settings
        // file at `~/.gemini/settings.json`, with an `mcpServers` map
        // matching the shape used by Claude/Codex. HTTP servers use the
        // `httpUrl` key (not `url`), per the documented schema.
        CodeTool::Gemini => {
            let dir = temp_home.join(".gemini");
            std::fs::create_dir_all(&dir).map_err(|e| format!("creating ~/.gemini: {e}"))?;
            let cfg_path = dir.join("settings.json");
            let cfg = json!({
                "mcpServers": {
                    "construct": {
                        "httpUrl": mcp_url,
                        "headers": {
                            "Authorization": format!("Bearer {token}"),
                            "X-Construct-Session": session_id,
                        }
                    }
                }
            });
            std::fs::write(
                &cfg_path,
                serde_json::to_vec_pretty(&cfg).expect("serialize gemini config"),
            )
            .map_err(|e| format!("writing gemini config: {e}"))?;
            Ok(CliInjection {
                args: vec![],
                files_written: vec![cfg_path],
            })
        }

        // Google Agents CLI (`agents-cli`) does not currently expose a
        // documented MCP config-file adapter equivalent to Claude/Codex/Gemini.
        // The terminal still launches it, but we avoid redirecting HOME for MCP
        // injection so user Google auth and project config remain visible.
        CodeTool::GoogleAgents => Ok(CliInjection {
            args: vec![],
            files_written: vec![],
        }),
    }
}

/// Minimal TOML string escaper (double-quoted basic string form).
/// Sufficient for the URL + bearer strings we write; not a general-purpose
/// escaper. Escapes backslash and double-quote.
fn toml_string(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '"' => out.push_str("\\\""),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04X}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

/// Extract bearer token from WS-compatible sources (header > subprotocol > query param).
fn extract_ws_token<'a>(headers: &'a HeaderMap, query_token: Option<&'a str>) -> Option<&'a str> {
    // 1. Authorization header
    if let Some(t) = headers
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .and_then(|auth| auth.strip_prefix("Bearer "))
    {
        if !t.is_empty() {
            return Some(t);
        }
    }

    // 2. Sec-WebSocket-Protocol: bearer.<token>
    if let Some(t) = headers
        .get("sec-websocket-protocol")
        .and_then(|v| v.to_str().ok())
        .and_then(|protos| {
            protos
                .split(',')
                .map(|p| p.trim())
                .find_map(|p| p.strip_prefix(BEARER_SUBPROTO_PREFIX))
        })
    {
        if !t.is_empty() {
            return Some(t);
        }
    }

    // 3. ?token= query parameter
    if let Some(t) = query_token {
        if !t.is_empty() {
            return Some(t);
        }
    }

    None
}

/// Build the generic `mcpServers` config document pointed at the local daemon.
///
/// Format chosen to match the `mcp.json` convention adopted by most
/// MCP-aware CLIs:
///
/// ```json
/// { "mcpServers": { "construct": { "url": "...", "headers": { ... } } } }
/// ```
pub fn build_mcp_config_json(mcp_url: &str, session_id: &str, token: &str) -> serde_json::Value {
    json!({
        "mcpServers": {
            "construct": {
                "type": "http",
                "url": mcp_url,
                "headers": {
                    "Authorization": format!("Bearer {token}"),
                    "X-Construct-Session": session_id,
                }
            }
        }
    })
}

/// Holds a temp dir that is removed on drop. Used to clean up per-session
/// MCP config files when the WS closes.
struct TempSpawnDir(PathBuf);

impl Drop for TempSpawnDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// Resolve an optional cwd string: tilde-expand, canonicalize, verify it
/// is a directory. Returns `None` if unspecified, `Err` if invalid.
fn resolve_cwd(raw: Option<&str>) -> Result<Option<PathBuf>, String> {
    let Some(s) = raw.filter(|s| !s.is_empty()) else {
        return Ok(None);
    };
    let expanded = shellexpand::tilde(s).into_owned();
    let p = PathBuf::from(&expanded);
    let canon = p.canonicalize().map_err(|e| format!("{s}: {e}"))?;
    if !canon.is_dir() {
        return Err(format!("{} is not a directory", canon.display()));
    }
    Ok(Some(canon))
}

/// GET /ws/terminal — WebSocket upgrade for PTY terminal
pub async fn handle_ws_terminal(
    State(state): State<AppState>,
    Query(params): Query<TerminalQuery>,
    headers: HeaderMap,
    ws: WebSocketUpgrade,
) -> impl IntoResponse {
    // Auth check
    if state.pairing.require_pairing() {
        let token = extract_ws_token(&headers, params.token.as_deref()).unwrap_or("");
        if !state.pairing.is_authenticated(token) {
            return (StatusCode::UNAUTHORIZED, "Unauthorized").into_response();
        }
    }

    // Echo sub-protocol if client requests it
    let ws = if headers
        .get("sec-websocket-protocol")
        .and_then(|v| v.to_str().ok())
        .map_or(false, |protos| {
            protos.split(',').any(|p| p.trim() == WS_PROTOCOL)
        }) {
        ws.protocols([WS_PROTOCOL])
    } else {
        ws
    };

    if let Some(ref logger) = state.audit_logger {
        let _ = logger.log_security_event("dashboard", "WebSocket terminal session connected");
    }

    ws.on_upgrade(move |socket| handle_terminal_socket(socket, params))
        .into_response()
}

/// Helper: send a red error frame over the WS. Silently swallows errors.
async fn send_err(ws_sender: &mut futures_util::stream::SplitSink<WebSocket, Message>, msg: &str) {
    let _ = ws_sender
        .send(Message::Text(format!("\x1b[31m{msg}\x1b[0m\r\n").into()))
        .await;
}

/// Spawn configuration assembled for the child process.
struct SpawnPlan {
    cmd: CommandBuilder,
    /// Kept alive for the duration of the PTY session — Drop removes the temp dir.
    _temp: Option<TempSpawnDir>,
}

/// Assemble the `CommandBuilder` for the spawned process. Split out from
/// `build_command` (which got tangled) for readability.
fn plan_spawn(
    tool: Option<CodeTool>,
    cwd: Option<PathBuf>,
    mcp_session: Option<&str>,
    mcp_token: Option<&str>,
) -> Result<SpawnPlan, String> {
    // `plan_spawn_with_discovery` is the real implementation; the non-suffixed
    // variant calls `read_construct_mcp()` for the prod path. Split out so the
    // per-CLI adapter tests can pass in a fake discovery URL without needing
    // `~/.construct/mcp.json` on disk.
    let discovery_url = if tool.map(CodeTool::supports_mcp_config).unwrap_or(false)
        && mcp_session.is_some()
        && mcp_token.is_some()
    {
        Some(
            read_construct_mcp()
                .map_err(|e| format!("in-process MCP server not available: {e}"))?
                .url,
        )
    } else {
        None
    };
    plan_spawn_with_discovery(tool, cwd, mcp_session, mcp_token, discovery_url.as_deref())
}

fn plan_spawn_with_discovery(
    tool: Option<CodeTool>,
    cwd: Option<PathBuf>,
    mcp_session: Option<&str>,
    mcp_token: Option<&str>,
    mcp_url: Option<&str>,
) -> Result<SpawnPlan, String> {
    let (mut cmd, temp) = match tool {
        Some(t) => {
            let bin = which::which(t.binary())
                .map_err(|_| format!("{} not found in PATH", t.binary()))?;
            let mut cmd = CommandBuilder::new(bin);

            if t.supports_mcp_config() {
                if let (Some(sess), Some(tok), Some(url)) = (mcp_session, mcp_token, mcp_url) {
                    let dir =
                        std::env::temp_dir().join(format!("construct-code-{}", Uuid::new_v4()));
                    std::fs::create_dir_all(&dir).map_err(|e| format!("creating temp dir: {e}"))?;

                    // Per-CLI adapter: writes the right config file(s) under
                    // `dir` (which we then expose to the child as HOME) and
                    // returns any argv the CLI needs.
                    let injection = write_cli_config(t, &dir, url, sess, tok)?;
                    for a in &injection.args {
                        cmd.arg(a);
                    }

                    // Redirect HOME + XDG_CONFIG_HOME so CLIs that read from
                    // `~/.codex/...`, `~/.gemini/...`, `~/.config/opencode/...`
                    // pick up our freshly written config instead of the user's
                    // real dotfiles.
                    cmd.env("HOME", &dir);
                    cmd.env("XDG_CONFIG_HOME", dir.join(".config"));

                    // Stable fallback env — harmless if unused.
                    cmd.env("CONSTRUCT_MCP_URL", url);
                    cmd.env("CONSTRUCT_MCP_SESSION", sess);
                    cmd.env("CONSTRUCT_MCP_TOKEN", tok);

                    (cmd, Some(TempSpawnDir(dir)))
                } else {
                    (cmd, None)
                }
            } else {
                (cmd, None)
            }
        }
        None => {
            // SHELL isn't set on Windows by default and `/bin/sh` doesn't
            // exist there. Pick the platform-native default so spawn
            // doesn't fail with "specified path cannot be found".
            let shell = std::env::var("SHELL").unwrap_or_else(|_| {
                if cfg!(windows) {
                    std::env::var("COMSPEC").unwrap_or_else(|_| "cmd.exe".to_string())
                } else {
                    "/bin/sh".to_string()
                }
            });
            let mut cmd = CommandBuilder::new(&shell);
            // `-l` is a Unix login-shell flag; cmd.exe / powershell.exe
            // reject it.
            if !cfg!(windows) {
                cmd.arg("-l");
            }
            (cmd, None)
        }
    };

    if let Some(c) = cwd {
        cmd.cwd(c);
    }

    // portable_pty::CommandBuilder starts with an empty env — the child would
    // otherwise have no PATH, TERM, locale, etc. Readline / zle need TERM to
    // map ^? (DEL) to backward-delete-char; without it, backspace silently
    // fails. We pin TERM/COLORTERM to values xterm.js speaks, then carry a
    // small allow-list of user env vars forward.
    cmd.env("TERM", "xterm-256color");
    cmd.env("COLORTERM", "truecolor");
    for key in [
        "PATH", "LANG", "LC_ALL", "LC_CTYPE", "USER", "LOGNAME", "SHELL", "TZ",
    ] {
        if let Ok(val) = std::env::var(key) {
            cmd.env(key, val);
        }
    }
    // Tool-branch has already redirected HOME to a per-session temp dir (so the
    // CLI picks up our freshly-written MCP config) — don't clobber that. The
    // shell-fallback branch (`temp.is_none()`) needs the real HOME so rc files
    // load normally.
    if temp.is_none() {
        if let Ok(home) = std::env::var("HOME") {
            cmd.env("HOME", home);
        }
    }

    Ok(SpawnPlan { cmd, _temp: temp })
}

async fn handle_terminal_socket(socket: WebSocket, params: TerminalQuery) {
    let (mut ws_sender, mut ws_receiver) = socket.split();

    // Validate the `tool` name: known -> CodeTool, unknown -> fallback to shell
    // (matches docs: "anything else, keep current behavior").
    let tool = params.tool.as_deref().and_then(CodeTool::from_query);

    // Resolve cwd (Err = user-visible).
    let cwd = match resolve_cwd(params.cwd.as_deref()) {
        Ok(c) => c,
        Err(msg) => {
            send_err(&mut ws_sender, &format!("Invalid cwd: {msg}")).await;
            return;
        }
    };

    // Assemble the spawn plan (binary lookup, MCP config, env vars).
    let plan = match plan_spawn(
        tool,
        cwd,
        params.mcp_session.as_deref(),
        params.mcp_token.as_deref(),
    ) {
        Ok(p) => p,
        Err(msg) => {
            send_err(&mut ws_sender, &msg).await;
            let _ = ws_sender.send(Message::Close(None)).await;
            return;
        }
    };

    let initial_size = PtySize {
        rows: params.rows.filter(|r| *r > 0).unwrap_or(24),
        cols: params.cols.filter(|c| *c > 0).unwrap_or(80),
        pixel_width: 0,
        pixel_height: 0,
    };

    // Spawn PTY
    let pty_system = NativePtySystem::default();
    let pair = match pty_system.openpty(initial_size) {
        Ok(pair) => pair,
        Err(e) => {
            error!(error = %e, "Failed to open PTY");
            send_err(&mut ws_sender, &format!("Failed to open PTY: {e}")).await;
            return;
        }
    };

    let SpawnPlan { cmd, _temp } = plan;
    let _child = match pair.slave.spawn_command(cmd) {
        Ok(child) => child,
        Err(e) => {
            error!(error = %e, "Failed to spawn child");
            send_err(&mut ws_sender, &format!("Failed to spawn child: {e}")).await;
            return;
        }
    };
    // Drop slave — master owns the PTY fd now
    drop(pair.slave);

    let master = pair.master;

    let mut pty_reader = match master.try_clone_reader() {
        Ok(r) => r,
        Err(e) => {
            error!(error = %e, "Failed to clone PTY reader");
            return;
        }
    };

    let mut pty_writer: Box<dyn Write + Send> = match master.take_writer() {
        Ok(w) => w,
        Err(e) => {
            error!(error = %e, "Failed to take PTY writer");
            return;
        }
    };

    // Channels to bridge blocking PTY I/O with async WebSocket
    let (pty_out_tx, mut pty_out_rx) = tokio::sync::mpsc::channel::<Vec<u8>>(64);
    let (resize_tx, mut resize_rx) = tokio::sync::mpsc::channel::<(u16, u16)>(4);

    // Blocking task: PTY stdout -> mpsc channel
    tokio::task::spawn_blocking(move || {
        let mut buf = [0u8; 4096];
        loop {
            match pty_reader.read(&mut buf) {
                Ok(0) => break,
                Ok(n) => {
                    if pty_out_tx.blocking_send(buf[..n].to_vec()).is_err() {
                        break;
                    }
                }
                Err(_) => break,
            }
        }
    });

    // Async task: handle resize requests
    tokio::spawn(async move {
        while let Some((cols, rows)) = resize_rx.recv().await {
            let _ = master.resize(PtySize {
                rows,
                cols,
                pixel_width: 0,
                pixel_height: 0,
            });
        }
    });

    // Periodic Ping keepalive. Without it, an idle terminal (e.g. user
    // reading output without typing) lets macOS/browser/proxy close the TCP
    // socket, surfacing as `Connection reset without closing handshake` in
    // logs. Browsers auto-pong at the protocol level.
    let mut ping_interval = tokio::time::interval(std::time::Duration::from_secs(30));
    ping_interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    ping_interval.tick().await;

    // Main loop: bridge WebSocket <-> PTY
    loop {
        tokio::select! {
            // PTY output -> WebSocket
            Some(data) = pty_out_rx.recv() => {
                let text = String::from_utf8_lossy(&data).into_owned();
                if ws_sender.send(Message::Text(text.into())).await.is_err() {
                    break;
                }
            }
            // WebSocket input -> PTY
            msg = ws_receiver.next() => {
                match msg {
                    Some(Ok(Message::Text(text))) => {
                        // Check if it's a resize message
                        if let Ok(resize) = serde_json::from_str::<ResizeMsg>(&text) {
                            if resize.msg_type == "resize" {
                                let _ = resize_tx.send((resize.cols, resize.rows)).await;
                                continue;
                            }
                        }
                        // Raw keystroke input
                        if pty_writer.write_all(text.as_bytes()).is_err() {
                            break;
                        }
                    }
                    Some(Ok(Message::Binary(data))) => {
                        if pty_writer.write_all(&data).is_err() {
                            break;
                        }
                    }
                    Some(Ok(Message::Close(_))) | None => {
                        debug!("Terminal WebSocket closed");
                        break;
                    }
                    Some(Ok(_)) => {} // Ping/Pong handled by axum
                    Some(Err(e)) => {
                        warn!(error = %e, "Terminal WebSocket error");
                        break;
                    }
                }
            }
            // Keepalive Ping
            _ = ping_interval.tick() => {
                if ws_sender.send(Message::Ping(Vec::new().into())).await.is_err() {
                    break;
                }
            }
        }
    }

    // `_temp` drops here, removing the per-session config dir.
    drop(_temp);

    debug!("Terminal session ended");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tool_mapping_known() {
        assert_eq!(CodeTool::from_query("claude"), Some(CodeTool::Claude));
        assert_eq!(CodeTool::from_query("codex"), Some(CodeTool::Codex));
        assert_eq!(CodeTool::from_query("opencode"), Some(CodeTool::OpenCode));
        assert_eq!(CodeTool::from_query("gemini"), Some(CodeTool::Gemini));
        assert_eq!(
            CodeTool::from_query("google_agents"),
            Some(CodeTool::GoogleAgents)
        );
        assert_eq!(
            CodeTool::from_query("agents-cli"),
            Some(CodeTool::GoogleAgents)
        );
    }

    #[test]
    fn tool_mapping_unknown_falls_back() {
        assert_eq!(CodeTool::from_query(""), None);
        assert_eq!(CodeTool::from_query("bash"), None);
        assert_eq!(CodeTool::from_query("Claude"), None); // case-sensitive
        assert_eq!(CodeTool::from_query("nonsense"), None);
    }

    #[test]
    fn tool_binaries_match_docs() {
        assert_eq!(CodeTool::Claude.binary(), "claude");
        assert_eq!(CodeTool::Codex.binary(), "codex");
        assert_eq!(CodeTool::OpenCode.binary(), "opencode");
        assert_eq!(CodeTool::Gemini.binary(), "gemini");
        assert_eq!(CodeTool::GoogleAgents.binary(), "agents-cli");
    }

    #[test]
    fn tool_config_env_vars() {
        assert_eq!(CodeTool::Claude.config_env(), "CLAUDE_MCP_CONFIG");
        assert_eq!(CodeTool::Codex.config_env(), "CODEX_MCP_CONFIG");
        assert_eq!(CodeTool::OpenCode.config_env(), "OPENCODE_MCP_CONFIG");
        assert_eq!(CodeTool::Gemini.config_env(), "GEMINI_MCP_CONFIG");
        assert_eq!(
            CodeTool::GoogleAgents.config_env(),
            "GOOGLE_AGENTS_MCP_CONFIG"
        );
        assert!(!CodeTool::GoogleAgents.supports_mcp_config());
    }

    #[test]
    fn mcp_config_json_has_expected_shape() {
        let v = build_mcp_config_json("http://127.0.0.1:54500/mcp", "sess-abc", "tok-xyz");
        let srv = &v["mcpServers"]["construct"];
        assert_eq!(srv["url"], "http://127.0.0.1:54500/mcp");
        assert_eq!(srv["type"], "http");
        assert_eq!(srv["headers"]["Authorization"], "Bearer tok-xyz");
        assert_eq!(srv["headers"]["X-Construct-Session"], "sess-abc");
        // JSON round-trips cleanly.
        let s = serde_json::to_string(&v).unwrap();
        let back: serde_json::Value = serde_json::from_str(&s).unwrap();
        assert_eq!(back, v);
    }

    #[test]
    fn resolve_cwd_none_when_unset() {
        assert!(matches!(resolve_cwd(None), Ok(None)));
        assert!(matches!(resolve_cwd(Some("")), Ok(None)));
    }

    #[test]
    fn resolve_cwd_rejects_missing_path() {
        assert!(resolve_cwd(Some("/this/should/not/exist/construct-xyz")).is_err());
    }

    #[test]
    fn resolve_cwd_accepts_tmp() {
        let tmp = std::env::temp_dir();
        let got = resolve_cwd(Some(tmp.to_str().unwrap())).unwrap().unwrap();
        assert!(got.is_dir());
    }

    #[test]
    fn plan_spawn_shell_fallback_no_tool() {
        // No tool -> shell fallback; must not touch $PATH or MCP discovery.
        let plan = plan_spawn(None, None, None, None).expect("shell fallback works");
        // We can't inspect CommandBuilder's inner bin easily without unstable API,
        // but we can at least confirm no temp dir was created.
        assert!(plan._temp.is_none());
    }

    #[test]
    fn plan_spawn_missing_binary_errors() {
        // Unless the user happens to have `gemini` installed during `cargo test`,
        // this should return a "not found in PATH" error. If it *is* installed,
        // the call succeeds and we just accept that.
        match plan_spawn(Some(CodeTool::Gemini), None, None, None) {
            Ok(_) => {} // gemini installed on this machine
            Err(msg) => assert!(msg.contains("not found in PATH"), "got: {msg}"),
        }
    }

    // Backwards-compat: the default `TerminalQuery` (what deserialization of
    // an empty query string produces) must have no tool selected, so
    // `handle_terminal_socket` takes the shell fallback path.
    #[test]
    fn terminal_query_default_falls_back_to_shell() {
        let q = TerminalQuery::default();
        assert!(q.tool.is_none());
        assert!(q.cwd.is_none());
        assert!(q.mcp_session.is_none());
        assert!(q.mcp_token.is_none());
        assert!(q.tool.as_deref().and_then(CodeTool::from_query).is_none());
    }

    // ── Per-CLI adapter tests ───────────────────────────────────────

    fn tempdir() -> PathBuf {
        let p = std::env::temp_dir().join(format!("construct-test-{}", Uuid::new_v4()));
        std::fs::create_dir_all(&p).unwrap();
        p
    }

    const URL: &str = "http://127.0.0.1:54500/mcp";
    const SESS: &str = "sess-abc";
    const TOK: &str = "tok-xyz";

    #[test]
    fn claude_adapter_writes_mcp_json_and_passes_flag() {
        let home = tempdir();
        let inj = write_cli_config(CodeTool::Claude, &home, URL, SESS, TOK).unwrap();

        // Flag layout: --mcp-config <path>
        assert_eq!(inj.args.len(), 2);
        assert_eq!(inj.args[0], "--mcp-config");
        let cfg_path = PathBuf::from(&inj.args[1]);
        assert!(cfg_path.starts_with(&home));
        assert!(cfg_path.ends_with(".mcp.json"));
        assert!(cfg_path.exists());

        let content: serde_json::Value =
            serde_json::from_slice(&std::fs::read(&cfg_path).unwrap()).unwrap();
        assert_eq!(content["mcpServers"]["construct"]["url"], URL);
        assert_eq!(
            content["mcpServers"]["construct"]["headers"]["Authorization"],
            format!("Bearer {TOK}")
        );
        assert_eq!(
            content["mcpServers"]["construct"]["headers"]["X-Construct-Session"],
            SESS
        );
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn codex_adapter_writes_toml_at_home_dot_codex() {
        let home = tempdir();
        let inj = write_cli_config(CodeTool::Codex, &home, URL, SESS, TOK).unwrap();
        assert!(inj.args.is_empty(), "codex has no flag mechanism");
        let cfg = home.join(".codex").join("config.toml");
        assert!(cfg.exists(), "{} should exist", cfg.display());
        let body = std::fs::read_to_string(&cfg).unwrap();
        assert!(body.contains("[mcp_servers.construct]"));
        assert!(body.contains(&format!("url = \"{URL}\"")), "body: {body}");
        assert!(body.contains("transport = \"http\""));
        assert!(body.contains("[mcp_servers.construct.headers]"));
        assert!(
            body.contains(&format!("Authorization = \"Bearer {TOK}\"")),
            "body: {body}"
        );
        assert!(
            body.contains(&format!("X-Construct-Session = \"{SESS}\"")),
            "body: {body}"
        );
        // Sanity: parses as TOML round-trip.
        let _: toml::Value = toml::from_str(&body).expect("codex config should be valid TOML");
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn opencode_adapter_writes_xdg_json() {
        let home = tempdir();
        let inj = write_cli_config(CodeTool::OpenCode, &home, URL, SESS, TOK).unwrap();
        assert!(inj.args.is_empty());
        let cfg = home.join(".config").join("opencode").join("config.json");
        assert!(cfg.exists());
        let v: serde_json::Value = serde_json::from_slice(&std::fs::read(&cfg).unwrap()).unwrap();
        let srv = &v["mcp"]["construct"];
        assert_eq!(srv["type"], "remote");
        assert_eq!(srv["url"], URL);
        assert_eq!(srv["enabled"], true);
        assert_eq!(srv["headers"]["Authorization"], format!("Bearer {TOK}"));
        assert_eq!(srv["headers"]["X-Construct-Session"], SESS);
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn gemini_adapter_writes_settings_json() {
        let home = tempdir();
        let inj = write_cli_config(CodeTool::Gemini, &home, URL, SESS, TOK).unwrap();
        assert!(inj.args.is_empty());
        let cfg = home.join(".gemini").join("settings.json");
        assert!(cfg.exists());
        let v: serde_json::Value = serde_json::from_slice(&std::fs::read(&cfg).unwrap()).unwrap();
        let srv = &v["mcpServers"]["construct"];
        assert_eq!(srv["httpUrl"], URL);
        assert_eq!(srv["headers"]["Authorization"], format!("Bearer {TOK}"));
        assert_eq!(srv["headers"]["X-Construct-Session"], SESS);
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn plan_spawn_with_discovery_no_creds_no_tempdir() {
        // Even with a discovery URL, without session+token we don't write
        // anything — matches the gating in the handler.
        let plan = plan_spawn_with_discovery(None, None, None, None, Some(URL))
            .expect("shell fallback works");
        assert!(plan._temp.is_none());
    }
}
