//! Kumiho memory MCP server injection.
//!
//! Every non-internal agent in Revka gets the Kumiho graph-memory MCP server
//! wired in automatically.  This module defines:
//!
//! - The canonical `McpServerConfig` for the Kumiho stdio server.
//! - The session-bootstrap system-prompt text that teaches the agent how to use
//!   `kumiho_memory_engage` and `kumiho_memory_reflect`.
//! - `inject_kumiho()` — called during agent/config construction to splice both
//!   the server config and the bootstrap prompt into whatever `Config` is being
//!   assembled.
//!
//! # Design
//!
//! Kumiho is Revka's *only* persistent memory store.  Rather than requiring
//! every caller to remember to add the server, injection is centralised here and
//! called unconditionally for all non-internal agents.
//!
//! Injection is **non-fatal**: if the script path does not exist at runtime the
//! MCP registry will simply log an error and continue — the agent degrades
//! gracefully to stateless operation.

use crate::config::{Config, KumihoConfig, McpServerConfig, McpTransport};
use directories::UserDirs;
use std::collections::HashMap;

// ── Constants ─────────────────────────────────────────────────────

/// Name used as the MCP server prefix (tools appear as `kumiho-memory__<tool>`).
pub const KUMIHO_SERVER_NAME: &str = "kumiho-memory";

/// Default path to the Kumiho MCP runner script (relative to `$HOME`).
pub const DEFAULT_MCP_PATH_SUFFIX: &str = ".revka/kumiho/run_kumiho_mcp.py";

// ── Bootstrap prompt ──────────────────────────────────────────────

/// Session-bootstrap instructions injected into the system prompt for every
/// non-internal agent.  Revka-specific bootstrap; teaches the agent how
/// to use the `kumiho_memory_engage` / `kumiho_memory_reflect` reflexes that
/// the Kumiho MCP server registers at startup.
pub const KUMIHO_BOOTSTRAP_PROMPT: &str = "\
SESSION-START INSTRUCTION (kumiho-memory — Revka daemon)

=== EVERY TURN ===
Follow these rules on every turn:
  - Do NOT greet the user unless they greeted you first.  If their \
message is a question or task, answer directly.
  - ENGAGE: Call kumiho_memory_engage ONCE when prior context \
is needed to answer correctly — for example, when the user \
references prior work, a past decision, a person, a project, \
asks 'do you remember', or mentions something not in the current \
conversation.  Skip engage for greetings, acknowledgements, yes/no \
answers, simple status checks, tool-availability confirmations, \
brief meta chat, and short direct answers.  Your query MUST derive \
from the user's current message.  Hold the returned source_krefs \
for reflect.  Use limit={{KUMIHO_MEMORY_RETRIEVAL_LIMIT}} unless \
the user explicitly asks for broader recall.  IMPORTANT: User \
memories and conversation history live in the CognitiveMemory \
project — use space_paths=['CognitiveMemory'] for memory recall.  \
Do NOT search Revka/ for user memories — that space holds agent \
operational data (AgentPool, Teams, Plans).
  - NEVER SAY 'I DON'T KNOW' WITHOUT CHECKING MEMORY — If the \
answer is not in the current conversation and you would otherwise \
say you don't know, don't have context, or can't find something, \
you MUST call kumiho_memory_engage first.  Only after engage \
returns empty may you tell the user you don't have the information.
  - ONE TOOL FOR RECALL — Always use kumiho_memory_engage for \
recall.  It returns aggregated results in a single call.  Do NOT \
chain low-level tools (fulltext_search, search_items, get_item, \
etc.) to piece together recall — that wastes tokens and time.
  - REFLECT: Call kumiho_memory_reflect only for explicit \
'remember this' requests, durable preferences/facts/decisions/\
corrections, meaningful multi-step outcomes worth preserving, or \
compacted session summaries/handoffs.  Skip reflect for greetings, \
acknowledgements, yes/no answers, simple status checks, \
tool-availability confirmations, brief meta chat, and short direct \
answers.
  - EXPLICIT REMEMBER REQUESTS — When the user says 'remember \
this', 'keep this in mind', 'note that', or similar, you MUST \
capture it via kumiho_memory_reflect.  Do NOT rely on built-in \
memory tools — Kumiho MCP tools are the canonical memory store.
  - Do NOT narrate memory operations.
  - Do NOT repeat content you already showed the user.  Refer to \
it briefly (e.g. 'the draft above') instead of reproducing it.
  - Do NOT re-ask questions already answered in this conversation.
  - Do NOT re-execute tasks already completed.
  - If you need user input, ask and STOP.  Never simulate the \
user's answer.
  - On the first message of the session: if the user's message is a \
greeting or casual talk (hi, hey, good morning, etc.), just greet \
back — do NOT call kumiho_memory_engage.  Only engage if their \
message is a question or task that would benefit from prior context. \
Never narrate the bootstrap (no 'Memory connected!' or similar).

=== ALWAYS ===
TEMPORAL AWARENESS — When using engage results, compare each \
result's created_at against today's date.  Express memory age \
naturally ('earlier today', 'yesterday', 'last Tuesday', 'about \
two weeks ago').  Recent memories take precedence over stale ones \
when they conflict.  When capturing memories via reflect, always \
use absolute dates in titles ('on Mar 29', not 'today') — relative \
time becomes meaningless when recalled in a future session.

COMPACTION — On /compact or auto-compression, capture summary via \
kumiho_memory_reflect with type='summary', tags=['compact','session-context'].

SKILL DISCOVERY — Search CognitiveMemory/Skills via engage when \
you need specialised guidance. Cache discovered skills for the session.

=== REVKA NAMESPACES ===
Revka/ is the operational root. CognitiveMemory/ is the user's \
personal memory — never write agent data there.
  - Revka/AgentPool/ — agent templates (keyed by name)
  - Revka/Plans/ — task plans with DEPENDS_ON edges
  - Revka/Sessions/ — session summaries and handoffs
  - Revka/Sessions/<session_id>/Outcomes/ — APPEND-ONLY findings \
agents share with each other (multi-agent learning, see below).
  - Revka/Teams/ — agent team DAGs (REPORTS_TO, SUPPORTS)
  - CognitiveMemory/Skills/ — shared skill library (only shared space)
Use space_hint in reflect to route captures to the correct subspace.

=== AGENT OUTCOMES (multi-agent learning) ===
When you run as part of a workflow / handoff chain / group chat, \
your peers can benefit from what you learn — and you can benefit \
from what they already learned. Coordinate via the per-session \
Outcomes namespace: Revka/Sessions/<session_id>/Outcomes/.

session_id resolution — your task context will mention it (look in \
the initial prompt, system_hint, or env). If you are in a workflow \
run, get_workflow_context returns it. If none is available, you \
are running ad-hoc — skip the outcomes pattern entirely.

INHERIT (first turn, after greeting, only when session_id is \
known): pull what siblings have already discovered so you do not \
re-do their work.
  kumiho_memory_engage(
    query=<your task summary>,
    space_paths=['Revka/Sessions/<session_id>/Outcomes']
  )

CONTRIBUTE (during work): when you find something DURABLE that \
future agents in this session would need — a non-obvious risk, \
a settled architectural decision, a hard-won lesson, an action \
plan, a load-bearing fact — record it. Skip noise, things specific \
to your own setup, or trivia.
  kumiho_memory_reflect(
    captures=[{
      type: 'discovery' | 'decision' | 'lesson' | 'insight' \
| 'warning' | 'fact',
      title: '<short title with absolute date>',
      content: '<the actual finding>',
      space_hint: 'Revka/Sessions/<session_id>/Outcomes',
      tags: ['outcome', '<kind>', 'session:<session_id>']
    }]
  )
Outcomes are append-only — do not try to overwrite a sibling's \
outcome, record a refining one and let the graph show the chain.";

/// Stripped-down session bootstrap used when the Kumiho sidecar is enabled
/// but the runtime registry probe shows the high-level memory reflexes are
/// not registered.
///
/// Names ONLY the always-available pair (`kumiho_memory_store`,
/// `kumiho_memory_retrieve`). High-level tool names are deliberately not
/// mentioned, even in negative phrasing — naming them primes the model to
/// call them anyway. Use generic phrasing for the unavailability hint.
pub const KUMIHO_BOOTSTRAP_PROMPT_LITE: &str = "\
SESSION-START INSTRUCTION (kumiho-memory — Revka daemon, lite mode)

Advanced memory reflexes are unavailable in this session. Use the bare \
memory tools listed below for any persistence work; do not assume \
higher-level reflex tools exist.

Available tools:
  - kumiho_memory_store    — store a memory item to the graph.
  - kumiho_memory_retrieve — retrieve a memory item by id or filter.

Rules:
  - For explicit 'remember this' requests, use kumiho_memory_store with an \
absolute date in the title (e.g. 'on Mar 27', not 'today').
  - For recall, use kumiho_memory_retrieve. Do not say 'I don't know' or \
'I don't have context' before searching memory.
  - User memories live under the CognitiveMemory project. Revka/ holds \
agent operational data (AgentPool, Teams, Plans).
  - Skip memory operations for greetings, acknowledgements, yes/no answers, \
and other trivial exchanges.
  - Do not narrate memory operations. Do not repeat content already shown.";

/// Lightweight memory bootstrap for channel agents (Discord, Slack, etc.).
///
/// Channels don't orchestrate sub-agents, manage teams, or use Revka
/// namespace conventions.  This stripped version covers only what a chat
/// responder needs: engage for recall, reflect for remember requests.
/// ~400 tokens vs ~1,500 for the full prompt.
pub const KUMIHO_CHANNEL_BOOTSTRAP_PROMPT: &str = "\
SESSION-START INSTRUCTION (kumiho-memory — Revka channel)

You have access to kumiho-memory MCP for persistent memory.

ENGAGE: Call kumiho_memory_engage ONCE when prior context is needed \
(user references past work, decisions, people, or asks 'do you \
remember' something not in current conversation). Use \
space_paths=['CognitiveMemory'] and \
limit={{KUMIHO_MEMORY_RETRIEVAL_LIMIT}} unless the user explicitly \
asks for broader recall. Skip for greetings, simple answers, and \
casual chat. NEVER say 'I don't know' or 'I don't have that context' \
without calling engage first. Use \
kumiho_memory_engage for all recall — do NOT chain low-level tools.

REFLECT: Call kumiho_memory_reflect only for explicit 'remember \
this' requests, durable preferences, corrections, or significant \
decisions. Use absolute dates in titles ('on Apr 1', not 'today').

Rules:
  - Do not call memory tools on every turn — skip for trivial exchanges.
  - Do not narrate memory operations.
  - Do not repeat content already shown.
  - Recent memories take precedence over stale ones.";

/// Channel-agent counterpart to [`KUMIHO_BOOTSTRAP_PROMPT_LITE`]: used when
/// the runtime registry probe shows the high-level memory tools are not
/// registered. Names ONLY the always-available pair.
pub const KUMIHO_CHANNEL_BOOTSTRAP_PROMPT_LITE: &str = "\
SESSION-START INSTRUCTION (kumiho-memory — Revka channel, lite mode)

Advanced memory reflexes are unavailable in this session.

For 'remember this' requests, use kumiho_memory_store with an absolute date \
in the title. For recall, use kumiho_memory_retrieve before saying you don't \
know. Skip memory ops for greetings and trivial exchanges. Do not narrate \
memory operations.";

// ── MCP server config ─────────────────────────────────────────────

/// Resolve the absolute path to `run_kumiho_mcp.py`.
///
/// Priority:
/// 1. `kumiho.mcp_path` from config if non-empty.
/// 2. `~/.revka/kumiho/run_kumiho_mcp.py` (the default install location).
pub fn resolve_mcp_path(kumiho_cfg: &KumihoConfig) -> String {
    let configured = kumiho_cfg.mcp_path.trim();
    if !configured.is_empty() {
        return expand_tilde(configured);
    }
    // Fall back to the conventional install location.
    let home = UserDirs::new()
        .map(|u| u.home_dir().to_string_lossy().into_owned())
        .unwrap_or_else(|| "~".to_string());
    format!("{home}/{DEFAULT_MCP_PATH_SUFFIX}")
}

/// Sentinel high-level tool whose presence in the live MCP registry indicates
/// that all Kumiho memory reflexes (engage / reflect / recall / consolidate /
/// dream_state) were registered at server startup. The Kumiho MCP either
/// merges them in as a set or skips them all (auto-discovery shim in
/// `kumiho/mcp_server.py`), so checking one is sufficient.
const ADVANCED_PROBE_TOOL_SUFFIX: &str = "kumiho_memory_engage";

/// Build the prefixed registry name for a Kumiho tool, e.g. produces
/// `"kumiho-memory__kumiho_memory_engage"`.
fn prefixed_kumiho_tool(tool: &str) -> String {
    format!("{}__{}", KUMIHO_SERVER_NAME, tool)
}

/// Pure check: given a snapshot of `tool_names` from a connected
/// [`crate::tools::McpRegistry`], decide whether the high-level Kumiho
/// memory reflexes are actually registered.
///
/// Names in the registry are prefixed by the server name (see
/// `McpRegistry::connect_all`); we look for `kumiho-memory__kumiho_memory_engage`.
/// This is the audit-prescribed registry probe (Row 1 + 13 remediation):
/// it is robust against import errors, broken venvs, missing packages, and
/// version skew — anything that prevents the tool from actually being
/// callable will also keep the name out of the registry.
pub fn registry_has_advanced_kumiho_tools(tool_names: &[String]) -> bool {
    let target = prefixed_kumiho_tool(ADVANCED_PROBE_TOOL_SUFFIX);
    tool_names.iter().any(|n| n == &target)
}

/// Cause-agnostic loud-failure warning. Call this once per agent / gateway
/// startup, after the MCP registry has been queried. Idempotent at the
/// `tracing` level (each call emits a single line; the surrounding lifecycle
/// bounds invocation count to one per process).
///
/// Triggers only when the sidecar is enabled but the live registry did not
/// expose the high-level reflexes. The message names neither the missing
/// tools (to keep the prompt-conditioning clean) nor a specific cause —
/// the operator is pointed at logs and the canonical remediation command.
pub fn warn_if_kumiho_advanced_missing(config: &Config, advanced_available: bool) {
    if !config.kumiho.enabled || advanced_available {
        return;
    }
    tracing::warn!(
        "Kumiho high-level memory tools were not registered after MCP startup. \
        Bootstrap prompt is using the lite variant. Check ~/.revka/logs/ \
        for MCP startup errors. To re-install: \
        `~/.revka/kumiho/venv/bin/pip install 'kumiho_memory>=0.5.2'` \
        (or re-run `revka install --sidecars-only`)."
    );
}

/// Build the `McpServerConfig` for the Kumiho stdio server.
pub fn kumiho_mcp_server_config(kumiho_cfg: &KumihoConfig) -> McpServerConfig {
    let script_path = resolve_mcp_path(kumiho_cfg);
    let mut env: HashMap<String, String> = HashMap::new();
    env.insert("REVKA_AGENT_ROOT".to_string(), expand_tilde("~/.revka"));
    // Pass the space prefix so the server scopes memories under the right project.
    if !kumiho_cfg.space_prefix.trim().is_empty() {
        env.insert(
            "KUMIHO_SPACE_PREFIX".to_string(),
            kumiho_cfg.space_prefix.clone(),
        );
    }
    // Pass project names so downstream tools use the configured projects.
    env.insert(
        "KUMIHO_MEMORY_PROJECT".to_string(),
        kumiho_cfg.memory_project.clone(),
    );
    env.insert(
        "KUMIHO_HARNESS_PROJECT".to_string(),
        kumiho_cfg.harness_project.clone(),
    );
    env.insert(
        "KUMIHO_MEMORY_RETRIEVAL_LIMIT".to_string(),
        kumiho_cfg.memory_retrieval_limit.max(1).to_string(),
    );
    if kumiho_cfg.is_local_ce() {
        // Local self-hosted Community Edition: tokenless, loopback-only.
        // Point the Python SDK (kumiho[mcp] >= 0.10.0) at the local server.
        // It probes `http://<endpoint>/api/_live`, sees
        // `deployment_mode == "self_hosted_ce"`, and connects tokenless over
        // gRPC (discovery + auto-login disabled).
        env.insert(
            "KUMIHO_LOCAL_SERVER_ENDPOINT".to_string(),
            kumiho_cfg.local_ce_endpoint(),
        );
        // The high-level memory tools (kumiho_memory) buffer sessions in Redis.
        // CE has no control plane to discover an Upstash URL, so point the layer
        // at the local loopback Redis (the kumiho-redis container CE onboarding
        // provisions). Without this, `reflect`/write hits the cloud memory proxy
        // and fails with "No credentials available for memory proxy".
        env.insert(
            "KUMIHO_UPSTASH_REDIS_URL".to_string(),
            crate::config::local_ce_redis_url(),
        );
        // The MCP child inherits the daemon's environment, which may carry a
        // cloud token (workspace `.env`, a shell export, or the auth-file
        // injection in main.rs). A non-empty KUMIHO_AUTH_TOKEN would make the
        // SDK take the cloud discovery path instead of the CE probe, so
        // explicitly shadow the cloud credentials to empty for CE mode. Empty
        // values read as "no token" by the SDK and override the inherited ones.
        for var in [
            "KUMIHO_AUTH_TOKEN",
            "KUMIHO_SERVICE_TOKEN",
            "KUMIHO_CONTROL_PLANE_URL",
        ] {
            env.insert(var.to_string(), String::new());
        }
    } else {
        // Cloud: forward the bearer token to the spawned Python MCP.
        // KUMIHO_AUTH_TOKEN (what the SDK reads) and KUMIHO_SERVICE_TOKEN
        // (what Revka's own gateway code reads) carry the same
        // dashboard-issued service_token — the discovery endpoint at
        // control.kumiho.cloud accepts service_tokens via
        // verifyControlPlaneToken and returns the tenant gRPC routing.
        //
        // Priority: KUMIHO_SERVICE_TOKEN > KUMIHO_AUTH_TOKEN. The service token
        // is the value written by Revka onboarding and should not be shadowed
        // by a stale shell-level KUMIHO_AUTH_TOKEN from another account.
        //
        // When neither is set, leave KUMIHO_AUTH_TOKEN unset and let the Python
        // SDK's _token_loader read ~/.kumiho/kumiho_authentication.json directly
        // (path used by `kumiho login`).
        let auth_token = std::env::var("KUMIHO_SERVICE_TOKEN")
            .ok()
            .filter(|t| !t.trim().is_empty())
            .or_else(|| {
                std::env::var("KUMIHO_AUTH_TOKEN")
                    .ok()
                    .filter(|t| !t.trim().is_empty())
            });
        if let Some(token) = auth_token {
            env.insert("KUMIHO_AUTH_TOKEN".to_string(), token);
        }
        // Also forward the control plane URL if set.
        if let Ok(url) = std::env::var("KUMIHO_CONTROL_PLANE_URL") {
            if !url.trim().is_empty() {
                env.insert("KUMIHO_CONTROL_PLANE_URL".to_string(), url);
            }
        }
    }
    // Do not force auto-configure before the MCP handshake. Endpoint discovery
    // may refresh network credentials; doing that at process launch can prevent
    // the sidecar from answering `initialize` and removes all memory tools.

    // Forward LLM API keys so the MCP server's built-in summarizer
    // (MemorySummarizer) can condense engage results before returning them.
    // The summarizer checks KUMIHO_LLM_API_KEY first, then falls back to
    // provider-specific env vars (OPENAI_API_KEY, ANTHROPIC_API_KEY).
    // Forward all of them so it works regardless of which provider the user
    // has configured.
    for var in &[
        "KUMIHO_LLM_API_KEY",
        "KUMIHO_LLM_PROVIDER",
        "KUMIHO_LLM_MODEL",
        "KUMIHO_LLM_LIGHT_MODEL",
        "KUMIHO_LLM_BASE_URL",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ] {
        if let Ok(val) = std::env::var(var) {
            if !val.trim().is_empty() {
                env.insert(var.to_string(), val);
            }
        }
    }

    McpServerConfig {
        name: KUMIHO_SERVER_NAME.to_string(),
        transport: McpTransport::Stdio,
        command: crate::sidecars::python::default_python_command().to_string(),
        args: vec![script_path],
        env,
        url: None,
        headers: HashMap::new(),
        tool_timeout_secs: None,
    }
}

// ── Injection ─────────────────────────────────────────────────────

/// Inject the Kumiho MCP server and bootstrap prompt into `config`.
///
/// For non-internal agents this:
/// 1. Ensures `config.mcp.enabled = true`.
/// 2. Prepends the Kumiho server to `config.mcp.servers` (if not already present).
///
/// The bootstrap system-prompt text is handled separately: call
/// [`append_kumiho_bootstrap`] on the assembled `system_prompt` string in the
/// agent run loop (after `build_channel_system_prompt`).
///
/// Internal agents (is_internal = true) are left untouched.
///
/// The function is intentionally idempotent: a second call for the same config
/// will not duplicate the server because it checks for existing entries by server
/// name.
pub fn inject_kumiho(mut config: Config, is_internal: bool) -> Config {
    if is_internal {
        return config;
    }
    if !config.kumiho.enabled {
        return config;
    }

    // Enable MCP and prepend the Kumiho server.
    config.mcp.enabled = true;

    let already_registered = config
        .mcp
        .servers
        .iter()
        .any(|s| s.name == KUMIHO_SERVER_NAME);

    if !already_registered {
        let kumiho_cfg = config.kumiho.clone();
        let mut server = kumiho_mcp_server_config(&kumiho_cfg);

        // Forward the host runtime's LLM credential to the Kumiho MCP server
        // so that dream_state / consolidation / summarization work even when
        // the daemon runs in a minimal environment (e.g. launchd) that lacks
        // provider-specific env vars.  Mirrors OpenClaw's loadHostLlmFromPluginApi().
        if !server.env.contains_key("KUMIHO_LLM_API_KEY")
            && !server.env.contains_key("OPENAI_API_KEY")
            && !server.env.contains_key("ANTHROPIC_API_KEY")
        {
            // Try the configured default_provider first, then common fallbacks.
            let provider_name = config.default_provider.as_deref().unwrap_or("");
            let candidates: Vec<&str> = if provider_name.is_empty() {
                vec!["openai", "anthropic"]
            } else {
                vec![provider_name, "openai", "anthropic"]
            };
            for candidate in candidates {
                if let Some(key) = crate::providers::resolve_provider_credential(candidate, None) {
                    // Map provider name to KUMIHO_LLM_PROVIDER value.
                    let kumiho_provider = match candidate {
                        c if c.contains("anthropic") => "anthropic",
                        _ => "openai",
                    };
                    server.env.insert("KUMIHO_LLM_API_KEY".to_string(), key);
                    server.env.insert(
                        "KUMIHO_LLM_PROVIDER".to_string(),
                        kumiho_provider.to_string(),
                    );
                    break;
                }
            }
        }

        // Prepend so Kumiho tools appear first in deferred tool listings.
        config.mcp.servers.insert(0, server);
    }

    // Note: the loud-failure warning lives in `warn_if_kumiho_advanced_missing`
    // and is emitted by the sites that own a live MCP registry (loop_, channels,
    // agent::from_config, gateway). It cannot fire here because `inject_kumiho`
    // runs before any MCP is connected — there is no registry to probe.

    config
}

/// Apply project-name substitution to a prompt template.
///
/// Replaces the hardcoded default project names (`CognitiveMemory` and
/// `Revka`) with the values from `KumihoConfig::memory_project` and
/// `KumihoConfig::harness_project`.  When the defaults are unchanged the
/// string is returned without modification.
///
/// Public so that other modules (e.g. operator) can reuse the same
/// substitution logic.
pub fn substitute_project_names(template: &str, config: &Config) -> String {
    let mem = &config.kumiho.memory_project;
    let har = &config.kumiho.harness_project;

    let mut out = template.to_string();

    // Only substitute if the configured value differs from the hardcoded default.
    if mem != "CognitiveMemory" {
        out = out.replace("CognitiveMemory", mem);
    }
    if har != "Revka" {
        // Be precise: only replace Revka when it appears as a namespace/project
        // reference (followed by `/` or at word boundary), NOT in prose like
        // "Revka daemon" or "Revka channel".  We use targeted replacements.
        out = out.replace("Revka/", &format!("{har}/"));
    }
    out = out.replace(
        "{{KUMIHO_MEMORY_RETRIEVAL_LIMIT}}",
        &config.kumiho.memory_retrieval_limit.max(1).to_string(),
    );
    out
}

/// Append the Kumiho session-bootstrap prompt to `system_prompt` if:
/// - `is_internal` is `false`, and
/// - `kumiho.enabled` is `true` in the config, and
/// - the sentinel string is not already present (idempotent).
///
/// When `advanced_available` is false, the **lite** variant is appended —
/// it does not mandate the high-level `kumiho_memory_engage` / `reflect` /
/// etc. tools that only ship with the `kumiho_memory` package.
///
/// Project names in the prompt are substituted from
/// `config.kumiho.memory_project` and `config.kumiho.harness_project`.
///
/// Call this right after assembling the system prompt in the agent run loop.
pub fn append_kumiho_bootstrap(
    system_prompt: &mut String,
    config: &Config,
    is_internal: bool,
    advanced_available: bool,
) {
    if is_internal || !config.kumiho.enabled {
        return;
    }
    if system_prompt.contains("SESSION-START INSTRUCTION (kumiho-memory") {
        return; // already injected
    }
    let template = if advanced_available {
        KUMIHO_BOOTSTRAP_PROMPT
    } else {
        KUMIHO_BOOTSTRAP_PROMPT_LITE
    };
    let prompt = substitute_project_names(template, config);
    system_prompt.push_str("\n\n---\n\n");
    system_prompt.push_str(&prompt);
}

/// Append the **lightweight** Kumiho bootstrap for channel agents.
///
/// Same guards as [`append_kumiho_bootstrap`] but uses the compact
/// channel-specific prompt (~400 tokens instead of ~1,500). When
/// `advanced_available` is false, an even shorter lite-of-lite variant is
/// used that mentions only the bare tools.
/// Project names are substituted identically.
pub fn append_kumiho_channel_bootstrap(
    system_prompt: &mut String,
    config: &Config,
    is_internal: bool,
    advanced_available: bool,
) {
    if is_internal || !config.kumiho.enabled {
        return;
    }
    if system_prompt.contains("SESSION-START INSTRUCTION (kumiho-memory") {
        return;
    }
    let template = if advanced_available {
        KUMIHO_CHANNEL_BOOTSTRAP_PROMPT
    } else {
        KUMIHO_CHANNEL_BOOTSTRAP_PROMPT_LITE
    };
    let prompt = substitute_project_names(template, config);
    system_prompt.push_str("\n\n---\n\n");
    system_prompt.push_str(&prompt);
}

// ── Helpers ───────────────────────────────────────────────────────

/// Expand a leading `~` to the current user's home directory.
///
/// Uses `shellexpand::tilde` with a `UserDirs` fallback for environments
/// where HOME is not set (e.g. cron / containerised runs).
fn expand_tilde(path: &str) -> String {
    let expanded = shellexpand::tilde(path);
    let expanded_str = expanded.as_ref();
    // If expansion failed (HOME unset), try UserDirs.
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
    use std::sync::Mutex;

    static ENV_LOCK: Mutex<()> = Mutex::new(());
    use crate::config::KumihoConfig;

    #[test]
    fn inject_kumiho_adds_server() {
        let cfg = Config::default();
        assert!(!cfg.mcp.servers.iter().any(|s| s.name == KUMIHO_SERVER_NAME));

        let injected = inject_kumiho(cfg, false);
        assert!(injected.mcp.enabled);
        assert!(
            injected
                .mcp
                .servers
                .iter()
                .any(|s| s.name == KUMIHO_SERVER_NAME)
        );
    }

    #[test]
    fn append_kumiho_bootstrap_adds_prompt() {
        let cfg = Config::default();
        let mut prompt = "## Identity\n\nYou are Revka.".to_string();
        append_kumiho_bootstrap(&mut prompt, &cfg, false, true);
        assert!(prompt.contains("SESSION-START INSTRUCTION (kumiho-memory"));
        // Full variant mandates engage/reflect.
        assert!(prompt.contains("kumiho_memory_engage"));
        assert!(prompt.contains("kumiho_memory_reflect"));
    }

    #[test]
    fn append_kumiho_bootstrap_lite_when_advanced_unavailable() {
        let cfg = Config::default();
        let mut prompt = String::new();
        append_kumiho_bootstrap(&mut prompt, &cfg, false, false);
        assert!(prompt.contains("SESSION-START INSTRUCTION (kumiho-memory"));
        assert!(prompt.contains("lite mode"));
        // Lite variant references only the always-available pair.
        assert!(prompt.contains("kumiho_memory_store"));
        assert!(prompt.contains("kumiho_memory_retrieve"));
        // No high-level reflex names anywhere — even mentioning them in
        // negative phrasing primes the model to call them.
        assert!(!prompt.contains("kumiho_memory_engage"));
        assert!(!prompt.contains("kumiho_memory_reflect"));
        assert!(!prompt.contains("kumiho_memory_recall"));
        assert!(!prompt.contains("kumiho_memory_consolidate"));
        assert!(!prompt.contains("kumiho_memory_dream_state"));
    }

    #[test]
    fn bootstrap_prompts_have_no_bare_legacy_memory_tool_names() {
        // Audit rows 11/12 guard: no bare `memory_store` / `memory_recall` /
        // `memory_forget` / `memory_search` anywhere in the bootstrap
        // prompts.  The Kumiho-namespaced names (`kumiho_memory_*`) are the
        // canonical surface, and the lite variant deliberately mentions
        // only `kumiho_memory_store` / `kumiho_memory_retrieve`.
        for prompt in &[
            KUMIHO_BOOTSTRAP_PROMPT,
            KUMIHO_BOOTSTRAP_PROMPT_LITE,
            KUMIHO_CHANNEL_BOOTSTRAP_PROMPT,
            KUMIHO_CHANNEL_BOOTSTRAP_PROMPT_LITE,
        ] {
            for stale in &[
                "memory_store",
                "memory_recall",
                "memory_forget",
                "memory_search",
            ] {
                // The kumiho-namespaced equivalents (e.g. `kumiho_memory_store`)
                // are allowed; only flag occurrences that are NOT prefixed
                // with `kumiho_`.
                let mut search_start = 0usize;
                while let Some(idx) = prompt[search_start..].find(stale) {
                    let abs = search_start + idx;
                    let preceded_by_kumiho =
                        abs >= "kumiho_".len() && &prompt[abs - "kumiho_".len()..abs] == "kumiho_";
                    assert!(
                        preceded_by_kumiho,
                        "bare legacy tool name '{stale}' found in bootstrap prompt at offset {abs}: \
                         {snippet}",
                        snippet = &prompt
                            [abs.saturating_sub(40)..(abs + stale.len() + 40).min(prompt.len())],
                    );
                    search_start = abs + stale.len();
                }
            }
        }
    }

    #[test]
    fn bootstrap_prompt_has_no_phantom_paseo_refs() {
        // Audit row 5: the prompt is for Revka, not Paseo. Revka does
        // not have a `kumiho-memory:kumiho-memory` skill or a
        // `kumiho_get_revision_by_tag` identity-bootstrap step, so the prompt
        // must not direct the model to invoke or avoid them.
        for fragment in &[
            "kumiho-memory:kumiho-memory",
            "kumiho_get_revision_by_tag",
            "Identity is already loaded",
        ] {
            assert!(
                !KUMIHO_BOOTSTRAP_PROMPT.contains(fragment),
                "phantom Paseo fragment '{fragment}' must not appear in KUMIHO_BOOTSTRAP_PROMPT"
            );
            assert!(
                !KUMIHO_BOOTSTRAP_PROMPT_LITE.contains(fragment),
                "phantom Paseo fragment '{fragment}' must not appear in KUMIHO_BOOTSTRAP_PROMPT_LITE"
            );
            assert!(
                !KUMIHO_CHANNEL_BOOTSTRAP_PROMPT.contains(fragment),
                "phantom Paseo fragment '{fragment}' must not appear in KUMIHO_CHANNEL_BOOTSTRAP_PROMPT"
            );
            assert!(
                !KUMIHO_CHANNEL_BOOTSTRAP_PROMPT_LITE.contains(fragment),
                "phantom Paseo fragment '{fragment}' must not appear in KUMIHO_CHANNEL_BOOTSTRAP_PROMPT_LITE"
            );
        }
    }

    #[test]
    fn append_kumiho_bootstrap_is_idempotent() {
        let cfg = Config::default();
        let mut prompt = String::new();
        append_kumiho_bootstrap(&mut prompt, &cfg, false, true);
        let after_first = prompt.len();
        append_kumiho_bootstrap(&mut prompt, &cfg, false, true);
        assert_eq!(prompt.len(), after_first);
    }

    #[test]
    fn inject_kumiho_skips_internal_agents() {
        let cfg = Config::default();
        let original_servers = cfg.mcp.servers.len();
        let unchanged = inject_kumiho(cfg, true);
        assert_eq!(unchanged.mcp.servers.len(), original_servers);
    }

    #[test]
    fn inject_kumiho_is_idempotent() {
        let cfg = Config::default();
        let once = inject_kumiho(cfg, false);
        let count_after_once = once
            .mcp
            .servers
            .iter()
            .filter(|s| s.name == KUMIHO_SERVER_NAME)
            .count();
        let twice = inject_kumiho(once, false);
        let count_after_twice = twice
            .mcp
            .servers
            .iter()
            .filter(|s| s.name == KUMIHO_SERVER_NAME)
            .count();
        assert_eq!(count_after_once, count_after_twice);
    }

    #[test]
    fn inject_kumiho_respects_disabled_flag() {
        let mut cfg = Config::default();
        cfg.kumiho.enabled = false;
        let unchanged = inject_kumiho(cfg, false);
        assert!(
            !unchanged
                .mcp
                .servers
                .iter()
                .any(|s| s.name == KUMIHO_SERVER_NAME)
        );
    }

    #[test]
    fn kumiho_mcp_server_config_uses_custom_path() {
        let kc = KumihoConfig {
            enabled: true,
            mode: crate::config::KumihoBackendMode::Cloud,
            mcp_path: "/opt/kumiho/run_kumiho_mcp.py".to_string(),
            space_prefix: "MyProject".to_string(),
            api_url: "http://localhost:8000".to_string(),
            memory_project: "CognitiveMemory".to_string(),
            harness_project: "Revka".to_string(),
            memory_retrieval_limit: 7,
        };
        let server = kumiho_mcp_server_config(&kc);
        // The command is the platform's default Python launcher ("python3" on
        // Unix, "python" on Windows) — see `sidecars::python::default_python_command`.
        // Assert against it rather than a hardcoded "python3" so the test holds on
        // every host (a bare "python3" assertion regressed on Windows once the
        // command stopped being hardcoded).
        assert_eq!(
            server.command,
            crate::sidecars::python::default_python_command()
        );
        assert_eq!(server.args, vec!["/opt/kumiho/run_kumiho_mcp.py"]);
        assert_eq!(
            server.env.get("KUMIHO_SPACE_PREFIX").map(|s| s.as_str()),
            Some("MyProject")
        );
        assert_eq!(
            server
                .env
                .get("KUMIHO_MEMORY_RETRIEVAL_LIMIT")
                .map(|s| s.as_str()),
            Some("7")
        );
        assert!(
            !server.env.contains_key("KUMIHO_AUTO_CONFIGURE"),
            "Kumiho MCP must answer initialize before network discovery"
        );
    }

    #[test]
    fn kumiho_mcp_server_config_prefers_service_token_over_auth_token() {
        let _guard = ENV_LOCK.lock().expect("env lock poisoned");
        let previous_auth = std::env::var("KUMIHO_AUTH_TOKEN").ok();
        let previous_service = std::env::var("KUMIHO_SERVICE_TOKEN").ok();

        unsafe {
            std::env::set_var("KUMIHO_AUTH_TOKEN", "stale-auth-token");
            std::env::set_var("KUMIHO_SERVICE_TOKEN", "onboarded-service-token");
        }

        let server = kumiho_mcp_server_config(&KumihoConfig::default());
        assert_eq!(
            server.env.get("KUMIHO_AUTH_TOKEN").map(String::as_str),
            Some("onboarded-service-token")
        );

        unsafe {
            match previous_auth {
                Some(value) => std::env::set_var("KUMIHO_AUTH_TOKEN", value),
                None => std::env::remove_var("KUMIHO_AUTH_TOKEN"),
            }
            match previous_service {
                Some(value) => std::env::set_var("KUMIHO_SERVICE_TOKEN", value),
                None => std::env::remove_var("KUMIHO_SERVICE_TOKEN"),
            }
        }
    }

    #[test]
    fn kumiho_mcp_server_config_local_ce_is_tokenless_and_points_at_loopback() {
        let _guard = ENV_LOCK.lock().expect("env lock poisoned");
        let previous_auth = std::env::var("KUMIHO_AUTH_TOKEN").ok();
        let previous_service = std::env::var("KUMIHO_SERVICE_TOKEN").ok();
        let previous_redis = std::env::var("KUMIHO_UPSTASH_REDIS_URL").ok();
        let previous_upstash = std::env::var("UPSTASH_REDIS_URL").ok();

        // Even with a stale cloud token in the environment, CE mode must NOT
        // forward it (a token would push the SDK onto the cloud discovery path
        // instead of the loopback CE probe).
        unsafe {
            std::env::set_var("KUMIHO_SERVICE_TOKEN", "stale-cloud-token");
            std::env::set_var("KUMIHO_AUTH_TOKEN", "stale-cloud-token");
            // Clear any Redis override so the loopback default is deterministic.
            std::env::remove_var("KUMIHO_UPSTASH_REDIS_URL");
            std::env::remove_var("UPSTASH_REDIS_URL");
        }

        let kc = KumihoConfig {
            mode: crate::config::KumihoBackendMode::LocalCe,
            api_url: "http://127.0.0.1:9190".to_string(),
            ..KumihoConfig::default()
        };
        let server = kumiho_mcp_server_config(&kc);

        assert_eq!(
            server
                .env
                .get("KUMIHO_LOCAL_SERVER_ENDPOINT")
                .map(String::as_str),
            Some("127.0.0.1:9190"),
            "CE mode must point the SDK at the loopback server"
        );
        // The MCP child inherits the daemon env, so absence is not enough — the
        // cloud credentials must be explicitly shadowed to empty so an inherited
        // KUMIHO_AUTH_TOKEN cannot push the SDK onto the cloud discovery path.
        for var in [
            "KUMIHO_AUTH_TOKEN",
            "KUMIHO_SERVICE_TOKEN",
            "KUMIHO_CONTROL_PLANE_URL",
        ] {
            assert_eq!(
                server.env.get(var).map(String::as_str),
                Some(""),
                "CE mode must shadow {var} to empty to defeat inherited cloud creds"
            );
        }
        // High-level memory writes need a Redis URL; CE points it at the local
        // loopback Redis so `reflect` doesn't fall back to the cloud memory proxy.
        assert_eq!(
            server
                .env
                .get("KUMIHO_UPSTASH_REDIS_URL")
                .map(String::as_str),
            Some("redis://127.0.0.1:6379"),
            "CE mode must point kumiho_memory at the local loopback Redis"
        );

        unsafe {
            match previous_auth {
                Some(value) => std::env::set_var("KUMIHO_AUTH_TOKEN", value),
                None => std::env::remove_var("KUMIHO_AUTH_TOKEN"),
            }
            match previous_service {
                Some(value) => std::env::set_var("KUMIHO_SERVICE_TOKEN", value),
                None => std::env::remove_var("KUMIHO_SERVICE_TOKEN"),
            }
            match previous_redis {
                Some(value) => std::env::set_var("KUMIHO_UPSTASH_REDIS_URL", value),
                None => std::env::remove_var("KUMIHO_UPSTASH_REDIS_URL"),
            }
            match previous_upstash {
                Some(value) => std::env::set_var("UPSTASH_REDIS_URL", value),
                None => std::env::remove_var("UPSTASH_REDIS_URL"),
            }
        }
    }

    #[test]
    fn substitute_project_names_with_defaults_fills_retrieval_limit() {
        let cfg = Config::default();
        let result = substitute_project_names(KUMIHO_BOOTSTRAP_PROMPT, &cfg);
        assert!(result.contains("CognitiveMemory"));
        assert!(result.contains("Revka/"));
        assert!(result.contains("limit=3"));
        assert!(!result.contains("{{KUMIHO_MEMORY_RETRIEVAL_LIMIT}}"));
    }

    #[test]
    fn substitute_project_names_replaces_memory_project() {
        let mut cfg = Config::default();
        cfg.kumiho.memory_project = "MyMemory".to_string();
        let result = substitute_project_names(KUMIHO_BOOTSTRAP_PROMPT, &cfg);
        assert!(result.contains("MyMemory"));
        assert!(!result.contains("CognitiveMemory"));
        // Revka/ namespaces should still be present (harness unchanged).
        assert!(result.contains("Revka/"));
    }

    #[test]
    fn substitute_project_names_replaces_harness_project() {
        let mut cfg = Config::default();
        cfg.kumiho.harness_project = "MyHarness".to_string();
        let result = substitute_project_names(KUMIHO_BOOTSTRAP_PROMPT, &cfg);
        assert!(result.contains("MyHarness/"));
        assert!(!result.contains("Revka/"));
        // CognitiveMemory should still be present (memory project unchanged).
        assert!(result.contains("CognitiveMemory"));
    }

    #[test]
    fn substitute_project_names_replaces_both() {
        let mut cfg = Config::default();
        cfg.kumiho.memory_project = "ProdMemory".to_string();
        cfg.kumiho.harness_project = "ProdHarness".to_string();
        let result = substitute_project_names(KUMIHO_BOOTSTRAP_PROMPT, &cfg);
        assert!(result.contains("ProdMemory"));
        assert!(result.contains("ProdHarness/"));
        assert!(!result.contains("CognitiveMemory"));
        assert!(!result.contains("Revka/"));
    }

    #[test]
    fn substitute_project_names_works_on_channel_prompt() {
        let mut cfg = Config::default();
        cfg.kumiho.memory_project = "ChannelMem".to_string();
        let result = substitute_project_names(KUMIHO_CHANNEL_BOOTSTRAP_PROMPT, &cfg);
        assert!(result.contains("ChannelMem"));
        assert!(!result.contains("CognitiveMemory"));
    }

    #[test]
    fn append_kumiho_bootstrap_substitutes_custom_projects() {
        let mut cfg = Config::default();
        cfg.kumiho.memory_project = "CustomMem".to_string();
        cfg.kumiho.harness_project = "CustomHarness".to_string();
        let mut prompt = String::new();
        append_kumiho_bootstrap(&mut prompt, &cfg, false, true);
        assert!(prompt.contains("CustomMem"));
        assert!(prompt.contains("CustomHarness/"));
        assert!(!prompt.contains("CognitiveMemory"));
        assert!(!prompt.contains("Revka/"));
    }

    #[test]
    fn registry_probe_empty_registry_is_unavailable() {
        assert!(!registry_has_advanced_kumiho_tools(&[]));
    }

    #[test]
    fn registry_probe_unprefixed_name_is_unavailable() {
        // The MCP registry always prefixes names with the server name.
        // An unprefixed `kumiho_memory_engage` (e.g. from a misconfigured
        // server or a stale snapshot) MUST NOT count as available.
        let names = vec!["kumiho_memory_engage".to_string()];
        assert!(!registry_has_advanced_kumiho_tools(&names));
    }

    #[test]
    fn registry_probe_only_bare_tools_is_unavailable() {
        // `kumiho_memory_store` / `kumiho_memory_retrieve` ship in the bare
        // `kumiho` package. Their presence alone does NOT imply the
        // high-level reflexes are registered.
        let names = vec![
            "kumiho-memory__kumiho_memory_store".to_string(),
            "kumiho-memory__kumiho_memory_retrieve".to_string(),
            "kumiho-memory__kumiho_search_items".to_string(),
        ];
        assert!(!registry_has_advanced_kumiho_tools(&names));
    }

    #[test]
    fn registry_probe_detects_advanced_tool_when_present() {
        let names = vec![
            "kumiho-memory__kumiho_memory_store".to_string(),
            "kumiho-memory__kumiho_memory_engage".to_string(),
            "operator__delegate".to_string(),
        ];
        assert!(registry_has_advanced_kumiho_tools(&names));
    }

    #[test]
    fn warn_if_kumiho_advanced_missing_skips_when_disabled() {
        // Smoke test: when kumiho is disabled, no panic / no fall-through
        // bug regardless of the probe value.
        let mut cfg = Config::default();
        cfg.kumiho.enabled = false;
        warn_if_kumiho_advanced_missing(&cfg, false);
        warn_if_kumiho_advanced_missing(&cfg, true);
    }

    #[test]
    fn warn_if_kumiho_advanced_missing_skips_when_available() {
        let cfg = Config::default(); // kumiho enabled by default
        warn_if_kumiho_advanced_missing(&cfg, true);
    }
}
