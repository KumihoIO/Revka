//! Operator MCP server injection.
//!
//! Every non-internal agent in Construct gets the Operator orchestration MCP
//! server wired in automatically.  This module defines:
//!
//! - The canonical `McpServerConfig` for the Operator stdio server.
//! - The system-prompt text that teaches the lead agent how to use operator
//!   tools (`create_agent`, `wait_for_agent`, etc.).
//! - `inject_operator()` -- called during agent/config construction to splice
//!   both the server config and the prompt into whatever `Config` is being
//!   assembled.
//!
//! # Multi-Provider Support
//!
//! The operator prompt is split into two layers:
//!
//! 1. **Core layer** ([`core::OPERATOR_CORE_PROMPT`]) — universal orchestration
//!    philosophy that works with any LLM (planning, governance, rules).
//! 2. **Tool layer** ([`providers`]) — provider-specific tool-calling examples
//!    adapted for each LLM family (Claude MCP, OpenAI JSON, etc.).
//!
//! Provider detection is automatic via [`providers::Provider::detect`] using
//! the model name string.

pub mod core;
pub mod providers;

use crate::config::{Config, McpServerConfig, McpTransport, OperatorConfig};
use directories::UserDirs;
use std::collections::HashMap;

// -- Constants ---------------------------------------------------------------

/// Name used as the MCP server prefix (tools appear as `construct-operator__<tool>`).
pub const OPERATOR_SERVER_NAME: &str = "construct-operator";

/// Default path to the Operator MCP runner script (relative to `$HOME`).
pub const DEFAULT_OPERATOR_MCP_PATH_SUFFIX: &str = ".construct/operator_mcp/run_operator_mcp.py";

// -- Prompt builder ----------------------------------------------------------

/// Build the complete operator prompt for a given model.
///
/// Assembles the universal core layer with the provider-specific tool layer
/// based on automatic provider detection from `model_name`.
pub fn build_operator_prompt(model_name: &str) -> String {
    let provider = providers::Provider::detect(model_name);
    let mut prompt =
        String::with_capacity(core::OPERATOR_CORE_PROMPT.len() + provider.tool_layer().len() + 8);
    prompt.push_str(core::OPERATOR_CORE_PROMPT);
    prompt.push_str("\n\n");
    prompt.push_str(provider.tool_layer());
    prompt
}

/// Backward-compatible constant — use [`build_operator_prompt`] instead.
pub const OPERATOR_PROMPT: &str = core::OPERATOR_CORE_PROMPT;

// -- MCP server config -------------------------------------------------------

/// Resolve the absolute path to `run_operator_mcp.py`.
///
/// Priority:
/// 1. `operator.mcp_path` from config if non-empty.
/// 2. `~/.construct/operator_mcp/run_operator_mcp.py` (the default install location).
pub fn resolve_operator_mcp_path(cfg: &OperatorConfig) -> String {
    let configured = cfg.mcp_path.trim();
    if !configured.is_empty() {
        return expand_tilde(configured);
    }
    // Fall back to the conventional install location.
    let home = UserDirs::new()
        .map(|u| u.home_dir().to_string_lossy().into_owned())
        .unwrap_or_else(|| "~".to_string());
    format!("{home}/{DEFAULT_OPERATOR_MCP_PATH_SUFFIX}")
}

/// Build the `McpServerConfig` for the Operator stdio server.
pub fn operator_mcp_server_config(cfg: &OperatorConfig) -> McpServerConfig {
    let script_path = resolve_operator_mcp_path(cfg);
    let mut env: HashMap<String, String> = HashMap::new();
    env.insert(
        "CONSTRUCT_AGENT_ROOT".to_string(),
        expand_tilde("~/.construct"),
    );
    // Forward the Kumiho service token so the operator can query the Agent Pool.
    if let Ok(token) = std::env::var("KUMIHO_SERVICE_TOKEN") {
        if !token.trim().is_empty() {
            env.insert("KUMIHO_AUTH_TOKEN".to_string(), token);
        }
    }
    // Enable Kumiho SDK auto-configure (uses cached credentials from ~/.kumiho/).
    env.insert("KUMIHO_AUTO_CONFIGURE".to_string(), "1".to_string());
    // Forward gateway URL so the operator can query cost/audit APIs. Auth
    // uses the service token at ~/.construct/service-token (read directly by
    // operator-mcp's gateway_client), so no token env-forwarding is needed.
    if let Ok(url) = std::env::var("CONSTRUCT_GATEWAY_URL") {
        if !url.trim().is_empty() {
            env.insert("CONSTRUCT_GATEWAY_URL".to_string(), url);
        }
    }
    // Tool timeout is user-configurable via `[operator] tool_timeout_secs`
    // (default 600). Operator tools include long-running operations
    // (codex image generation, workflow execution, dry-run) that exceed
    // the runtime's 180s global default. We surface the value here so a
    // user with very slow plans can dial it down for fast-fail behavior
    // or raise it up to the 600s runtime cap.
    let tool_timeout_secs = if cfg.tool_timeout_secs == 0 {
        None
    } else {
        Some(cfg.tool_timeout_secs)
    };
    McpServerConfig {
        name: OPERATOR_SERVER_NAME.to_string(),
        transport: McpTransport::Stdio,
        command: crate::sidecars::python::default_python_command().to_string(),
        args: vec![script_path],
        env,
        url: None,
        headers: HashMap::new(),
        tool_timeout_secs,
    }
}

// -- Injection ---------------------------------------------------------------

/// Inject the Operator MCP server into `config`.
///
/// For non-internal agents this:
/// 1. Ensures `config.mcp.enabled = true`.
/// 2. Prepends the Operator server to `config.mcp.servers` (if not already present).
///
/// The operator system-prompt text is handled separately: call
/// [`append_operator_prompt`] on the assembled `system_prompt` string in the
/// agent run loop (after `append_kumiho_bootstrap`).
///
/// Internal agents (is_internal = true) are left untouched.
///
/// The function is intentionally idempotent: a second call for the same config
/// will not duplicate the server because it checks for existing entries by server
/// name.
pub fn inject_operator(mut config: Config, is_internal: bool) -> Config {
    if is_internal {
        return config;
    }
    if !config.operator.enabled {
        return config;
    }

    // Enable MCP and prepend the Operator server.
    config.mcp.enabled = true;

    let already_registered = config
        .mcp
        .servers
        .iter()
        .any(|s| s.name == OPERATOR_SERVER_NAME);

    if !already_registered {
        let operator_cfg = config.operator.clone();
        let mut server = operator_mcp_server_config(&operator_cfg);
        // Pass the Kumiho API URL so the operator can query the Agent Pool.
        if !config.kumiho.api_url.is_empty() {
            server
                .env
                .insert("KUMIHO_API_URL".to_string(), config.kumiho.api_url.clone());
        }
        // Pass project names so operator tools use the configured projects.
        server.env.insert(
            "KUMIHO_MEMORY_PROJECT".to_string(),
            config.kumiho.memory_project.clone(),
        );
        server.env.insert(
            "KUMIHO_HARNESS_PROJECT".to_string(),
            config.kumiho.harness_project.clone(),
        );
        // Pass the gateway URL so the operator can query cost/audit APIs.
        // Use 127.0.0.1 instead of 0.0.0.0 for the operator — 0.0.0.0 is a
        // listen address, not a connect address, and some systems don't route it
        // to loopback correctly.
        let gw_host = if config.gateway.host == "0.0.0.0" {
            "127.0.0.1"
        } else {
            &config.gateway.host
        };
        let gw_port = config.gateway.port;
        let gw_url = format!("http://{gw_host}:{gw_port}");
        server
            .env
            .insert("CONSTRUCT_GATEWAY_URL".to_string(), gw_url);
        // Auth uses the service token at ~/.construct/service-token (written
        // by the gateway at startup, read directly by operator-mcp's
        // gateway_client). No token env-forwarding required.
        // Prepend so Operator tools appear early in deferred tool listings.
        config.mcp.servers.insert(0, server);
    }

    config
}

/// Append the **full** Operator prompt to `system_prompt`.
///
/// Used for CLI and Dashboard agent sessions where the orchestration
/// instructions should be present from the first turn.
///
/// Uses provider detection on `model_name` to select the appropriate tool
/// layer.  Project names (`CognitiveMemory`, `Construct`) are substituted
/// from `config.kumiho.memory_project` / `config.kumiho.harness_project`.
///
/// Call this right after `append_kumiho_bootstrap` in the agent run loop.
pub fn append_operator_prompt(
    system_prompt: &mut String,
    config: &Config,
    is_internal: bool,
    model_name: &str,
) {
    if is_internal || !config.operator.enabled {
        return;
    }
    if system_prompt.contains("OPERATOR MODE (Construct)")
        || system_prompt.contains("OPERATOR (Construct)")
    {
        return; // already injected
    }
    let raw = build_operator_prompt(model_name);
    let prompt = crate::agent::kumiho::substitute_project_names(&raw, config);
    system_prompt.push_str("\n\n---\n\n");
    system_prompt.push_str(&prompt);
}

/// Compact operator reference for channel agents (~200 tokens).
///
/// Tells the agent it has operator tools available without dumping the
/// full orchestration philosophy.  The agent can request the full prompt
/// via the `load_skill` tool on the first turn that needs orchestration,
/// following OpenClaw's one-shot pattern.
const OPERATOR_CHANNEL_PROMPT: &str = "\
OPERATOR (Construct) — You have access to construct-operator MCP tools \
for multi-agent orchestration.

Voice: detached Operator (Matrix). Status reports, not commentary. \
Brevity is respect.

Available tools: create_agent, \
wait_for_agent, send_agent_prompt, get_agent_activity, list_agents, \
save_agent_template, search_agent_pool, create_team, spawn_team, \
save_plan, compact_conversation, store_compaction.

Agent types: 'claude' (reasoning, review) or 'codex' (fast coding). \
Model tiering: opus for deep work, sonnet for balanced, haiku for cheap. \
Always set cwd when creating agents. Use wait_for_agent to get results.

For complex orchestration patterns, use load_skill to retrieve \
detailed instructions on demand.";

/// Append the **compact** operator prompt for channel agents.
///
/// Channels (Discord, Slack, etc.) get a lightweight reference (~200 tokens)
/// instead of the full ~3,500 token prompt.  The agent still has full
/// operator MCP access and can load detailed instructions on demand.
pub fn append_operator_channel_prompt(
    system_prompt: &mut String,
    config: &Config,
    is_internal: bool,
    _model_name: &str,
) {
    if is_internal || !config.operator.enabled {
        return;
    }
    if system_prompt.contains("OPERATOR MODE (Construct)")
        || system_prompt.contains("OPERATOR (Construct)")
    {
        return;
    }
    let prompt = crate::agent::kumiho::substitute_project_names(OPERATOR_CHANNEL_PROMPT, config);
    system_prompt.push_str("\n\n---\n\n");
    system_prompt.push_str(&prompt);
}

// -- Helpers -----------------------------------------------------------------

/// Expand a leading `~` to the current user's home directory.
fn expand_tilde(path: &str) -> String {
    let expanded = shellexpand::tilde(path);
    let expanded_str = expanded.as_ref();
    if expanded_str.starts_with('~') {
        if let Some(user_dirs) = UserDirs::new() {
            let home = user_dirs.home_dir();
            if let Some(rest) = expanded_str.strip_prefix('~') {
                return format!(
                    "{}{}{}",
                    home.display(),
                    if rest.starts_with('/') { "" } else { "/" },
                    rest.trim_start_matches('/')
                );
            }
        }
    }
    expanded_str.to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn inject_operator_adds_server() {
        let cfg = Config::default();
        assert!(
            !cfg.mcp
                .servers
                .iter()
                .any(|s| s.name == OPERATOR_SERVER_NAME)
        );

        let injected = inject_operator(cfg, false);
        assert!(injected.mcp.enabled);
        assert!(
            injected
                .mcp
                .servers
                .iter()
                .any(|s| s.name == OPERATOR_SERVER_NAME)
        );
    }

    #[test]
    fn injected_operator_server_uses_configured_tool_timeout() {
        // Default config -> default timeout (600s).
        let injected = inject_operator(Config::default(), false);
        let entry = injected
            .mcp
            .servers
            .iter()
            .find(|s| s.name == OPERATOR_SERVER_NAME)
            .expect("operator server injected");
        assert_eq!(entry.tool_timeout_secs, Some(600));

        // Custom config value flows through.
        let mut cfg = Config::default();
        cfg.operator.tool_timeout_secs = 300;
        let injected = inject_operator(cfg, false);
        let entry = injected
            .mcp
            .servers
            .iter()
            .find(|s| s.name == OPERATOR_SERVER_NAME)
            .expect("operator server injected");
        assert_eq!(entry.tool_timeout_secs, Some(300));

        // 0 means "fall back to the runtime default" (mirrors the
        // `Option::None` semantics used by user-configured MCP servers).
        let mut cfg = Config::default();
        cfg.operator.tool_timeout_secs = 0;
        let injected = inject_operator(cfg, false);
        let entry = injected
            .mcp
            .servers
            .iter()
            .find(|s| s.name == OPERATOR_SERVER_NAME)
            .expect("operator server injected");
        assert_eq!(entry.tool_timeout_secs, None);
    }

    #[test]
    fn append_operator_prompt_adds_text() {
        let cfg = Config::default();
        let mut prompt = "## Identity\n\nYou are Construct.".to_string();
        append_operator_prompt(&mut prompt, &cfg, false, "claude-sonnet-4-6");
        assert!(prompt.contains("OPERATOR MODE (Construct)"));
    }

    #[test]
    fn append_operator_prompt_is_idempotent() {
        let cfg = Config::default();
        let mut prompt = String::new();
        append_operator_prompt(&mut prompt, &cfg, false, "claude-sonnet-4-6");
        let after_first = prompt.len();
        append_operator_prompt(&mut prompt, &cfg, false, "claude-sonnet-4-6");
        assert_eq!(prompt.len(), after_first);
    }

    #[test]
    fn inject_operator_skips_internal_agents() {
        let cfg = Config::default();
        let original_servers = cfg.mcp.servers.len();
        let unchanged = inject_operator(cfg, true);
        assert_eq!(unchanged.mcp.servers.len(), original_servers);
    }

    #[test]
    fn inject_operator_is_idempotent() {
        let cfg = Config::default();
        let once = inject_operator(cfg, false);
        let count_after_once = once
            .mcp
            .servers
            .iter()
            .filter(|s| s.name == OPERATOR_SERVER_NAME)
            .count();
        let twice = inject_operator(once, false);
        let count_after_twice = twice
            .mcp
            .servers
            .iter()
            .filter(|s| s.name == OPERATOR_SERVER_NAME)
            .count();
        assert_eq!(count_after_once, count_after_twice);
    }

    #[test]
    fn inject_operator_respects_disabled_flag() {
        let mut cfg = Config::default();
        cfg.operator.enabled = false;
        let unchanged = inject_operator(cfg, false);
        assert!(
            !unchanged
                .mcp
                .servers
                .iter()
                .any(|s| s.name == OPERATOR_SERVER_NAME)
        );
    }

    #[test]
    fn build_prompt_includes_core_and_tool_layer() {
        let claude_prompt = build_operator_prompt("claude-opus-4-6");
        assert!(claude_prompt.contains("OPERATOR MODE (Construct)"));
        assert!(claude_prompt.contains("=== TOOL USAGE ==="));

        let openai_prompt = build_operator_prompt("gpt-5.4");
        assert!(openai_prompt.contains("OPERATOR MODE (Construct)"));
        assert!(openai_prompt.contains("=== TOOL USAGE ==="));
    }

    #[test]
    fn different_models_get_different_tool_layers() {
        let claude_prompt = build_operator_prompt("claude-opus-4-6");
        let openai_prompt = build_operator_prompt("gpt-5.4");
        // Both share the core but differ in tool layer
        assert_ne!(claude_prompt, openai_prompt);
        // Claude layer is shorter (MCP-native, less verbose)
        assert!(claude_prompt.len() < openai_prompt.len());
    }
}
