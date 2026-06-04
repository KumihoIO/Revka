//! Tool registry used by the MCP daemon.
//!
//! The registry has two tiers:
//!
//! 1. **Baseline** (`build_default_tools`) — a curated set of ~16 zero-dep
//!    tools that need nothing beyond a `SecurityPolicy` and the workspace
//!    path. This keeps M1 green even if config loading fails.
//! 2. **Extended** (`build_tools_with_config`) — adds Revka integrations
//!    (Notion, Jira, Composio, Google Workspace, Microsoft 365, LinkedIn,
//!    plus the three `skills_*` meta-tools) whenever a `Config` is available
//!    with the matching credentials.
//!
//! Tools that require runtime state the daemon does not own (delegate
//! configs, channel handles, workspace sessions, discord memory backend,
//! etc.) are still skipped and reported through the `skipped` list.

use crate::config::Config;
use crate::mcp_server::progress_wrap::ProgressEnvelope;
use crate::mcp_server::runtime::RuntimeHandles;
use crate::mcp_server::skills_tools::{
    DefaultSkillExecutor, DiskSkillSource, SkillSource, SkillsDescribeTool, SkillsExecuteTool,
    SkillsListTool,
};
use crate::security::SecurityPolicy;
use crate::tools::{
    BrowserOpenTool, CalculatorTool, ComposioTool, ContentSearchTool, CronAddTool, CronListTool,
    CronRemoveTool, CronRunTool, CronRunsTool, CronUpdateTool, DiscordSearchTool, FileEditTool,
    FileReadTool, FileWriteTool, GitOperationsTool, GlobSearchTool, GoogleWorkspaceTool,
    HttpRequestTool, ImageGenTool, ImageInfoTool, JiraTool, LinkedInTool, LlmTaskTool,
    Microsoft365Tool, NotionTool, PdfReadTool, PollTool, ScreenshotTool, ShellTool, Tool,
    WeatherTool, WebFetchTool, WebSearchTool, WorkspaceTool,
};
use std::path::PathBuf;
use std::sync::Arc;

/// A `(name, reason)` pair for every tool the registry intentionally skipped.
///
/// Uses `String` rather than `&'static str` because reasons can be generated
/// dynamically from config state (e.g. "missing api_key").
pub type SkippedEntry = (String, String);

/// Build the baseline tool registry (no Config required).
///
/// This is the M1 surface — it stays green even when the daemon fails to
/// load a `Config`. All M1 tests still call this entry point.
#[must_use]
pub fn build_default_tools(
    workspace_dir: &std::path::Path,
) -> (Vec<Arc<dyn Tool>>, Vec<SkippedEntry>) {
    let (tools, skipped) = build_baseline(workspace_dir);
    // Return as owned Strings so callers can interleave with the config-
    // driven skip messages uniformly.
    (
        tools,
        skipped
            .into_iter()
            .map(|(n, r)| (n.to_string(), r.to_string()))
            .collect(),
    )
}

/// Shared implementation used by both `build_default_tools` (no config) and
/// `build_tools_with_config` (config-aware).
fn build_baseline(
    workspace_dir: &std::path::Path,
) -> (Vec<Arc<dyn Tool>>, Vec<(&'static str, &'static str)>) {
    let security = Arc::new(SecurityPolicy::default());
    let runtime = Arc::new(crate::runtime::NativeRuntime::new());

    let mut tools: Vec<Arc<dyn Tool>> = Vec::new();

    // ── Filesystem + shell ──────────────────────────────────────────────
    tools.push(Arc::new(ShellTool::new(security.clone(), runtime.clone())));
    tools.push(Arc::new(FileReadTool::new(security.clone())));
    tools.push(Arc::new(FileWriteTool::new(security.clone())));
    tools.push(Arc::new(FileEditTool::new(security.clone())));
    tools.push(Arc::new(GlobSearchTool::new(security.clone())));
    tools.push(Arc::new(ContentSearchTool::new(security.clone())));

    // ── Simple utilities ────────────────────────────────────────────────
    tools.push(Arc::new(CalculatorTool::new()));
    tools.push(Arc::new(WeatherTool::new()));

    // ── Git (workspace-bound) ───────────────────────────────────────────
    tools.push(Arc::new(GitOperationsTool::new(
        security.clone(),
        PathBuf::from(workspace_dir),
    )));

    // ── Network: HTTP, web_fetch, web_search ────────────────────────────
    tools.push(Arc::new(HttpRequestTool::new(
        security.clone(),
        vec!["*".to_string()],
        10 * 1024 * 1024,
        30,
        false,
    )));
    tools.push(Arc::new(WebFetchTool::new(
        security.clone(),
        vec!["*".to_string()],
        Vec::new(),
        10 * 1024 * 1024,
        30,
        crate::config::schema::FirecrawlConfig::default(),
        Vec::new(),
    )));
    tools.push(Arc::new(WebSearchTool::new(
        "duckduckgo".to_string(),
        None,
        5,
        15,
    )));

    // ── Vision / document ───────────────────────────────────────────────
    tools.push(Arc::new(PdfReadTool::new(security.clone())));
    tools.push(Arc::new(ScreenshotTool::new(security.clone())));
    tools.push(Arc::new(ImageInfoTool::new(security.clone())));

    // ── Browser (URL open only; full automation omitted — needs backend) ─
    tools.push(Arc::new(BrowserOpenTool::new(
        security.clone(),
        vec!["*".to_string()],
    )));

    let skipped: Vec<(&'static str, &'static str)> = vec![
        ("delegate", "needs agent config + credentials"),
        ("swarm", "needs agent config"),
        ("workspace", "needs WorkspaceManager"),
        ("cron_*", "needs boot Config + scheduler"),
        ("poll", "needs channel map handle"),
        ("reaction", "needs channel map handle"),
        ("ask_user", "needs channel map handle"),
        ("escalate", "needs channel map handle"),
        ("discord_search", "needs discord.db Memory backend"),
        ("llm_task", "needs provider creds"),
        ("image_gen", "needs API key"),
        ("sessions_*", "needs workspace SessionStore"),
        ("mcp_*", "wraps external MCP servers, out of scope"),
        ("browser (full)", "needs browser backend config"),
        ("browser_delegate", "needs delegate config"),
        (
            "claude_code / codex_cli / gemini_cli / opencode_cli",
            "these are the clients connecting TO this daemon",
        ),
        (
            "security_ops / cloud_ops / cloud_patterns",
            "needs opt-in config",
        ),
        ("sop_*", "needs sops_dir config"),
    ];

    (tools, skipped)
}

/// Build the extended registry using a loaded `Config`.
///
/// Starts from the baseline and conditionally appends integration tools
/// whenever the matching credentials are present. Every skip is reported
/// through the returned `skipped` list so daemon boot logs tell the operator
/// exactly which integrations were off and why.
#[must_use]
pub fn build_tools_with_config(
    workspace_dir: &std::path::Path,
    config: &Config,
) -> (Vec<Arc<dyn Tool>>, Vec<SkippedEntry>) {
    let security = Arc::new(SecurityPolicy::default());
    let (mut tools, baseline_skipped) = build_baseline(workspace_dir);
    let mut skipped: Vec<SkippedEntry> = baseline_skipped
        .into_iter()
        .map(|(n, r)| (n.to_string(), r.to_string()))
        .collect();

    // ── Skills meta-tools (always on) ───────────────────────────────────
    let skill_source: Arc<dyn SkillSource> = Arc::new(DiskSkillSource::new(
        workspace_dir.to_path_buf(),
        config.skills.open_skills_enabled,
        config.skills.open_skills_dir.clone(),
    ));
    tools.push(Arc::new(SkillsListTool::new(skill_source.clone())));
    tools.push(Arc::new(SkillsDescribeTool::new(skill_source.clone())));
    tools.push(Arc::new(SkillsExecuteTool::new(
        skill_source,
        Arc::new(DefaultSkillExecutor::new(security.clone())),
    )));

    // ── Notion ──────────────────────────────────────────────────────────
    if config.notion.enabled {
        let key = if config.notion.api_key.trim().is_empty() {
            std::env::var("NOTION_API_KEY").unwrap_or_default()
        } else {
            config.notion.api_key.trim().to_string()
        };
        if key.is_empty() {
            skipped.push((
                "notion".into(),
                "enabled but notion.api_key / NOTION_API_KEY missing".into(),
            ));
        } else {
            let notion: Arc<dyn Tool> = Arc::new(NotionTool::new(key, security.clone()));
            // Opt-in progress: bookend events for the Notion call.
            tools.push(
                ProgressEnvelope::new(
                    notion,
                    "notion: sending request",
                    "notion: response received",
                )
                .into_arc(),
            );
        }
    } else {
        skipped.push(("notion".into(), "disabled (notion.enabled=false)".into()));
    }

    // ── Jira ────────────────────────────────────────────────────────────
    if config.jira.enabled {
        let token = if config.jira.api_token.trim().is_empty() {
            std::env::var("JIRA_API_TOKEN").unwrap_or_default()
        } else {
            config.jira.api_token.trim().to_string()
        };
        if token.is_empty() {
            skipped.push((
                "jira".into(),
                "enabled but jira.api_token / JIRA_API_TOKEN missing".into(),
            ));
        } else if config.jira.base_url.trim().is_empty() {
            skipped.push(("jira".into(), "enabled but jira.base_url empty".into()));
        } else if config.jira.email.trim().is_empty() {
            skipped.push(("jira".into(), "enabled but jira.email empty".into()));
        } else {
            tools.push(Arc::new(JiraTool::new(
                config.jira.base_url.trim().to_string(),
                config.jira.email.trim().to_string(),
                token,
                config.jira.allowed_actions.clone(),
                security.clone(),
                config.jira.timeout_secs,
            )));
        }
    } else {
        skipped.push(("jira".into(), "disabled (jira.enabled=false)".into()));
    }

    // ── Composio ────────────────────────────────────────────────────────
    if config.composio.enabled {
        let key = match &config.composio.api_key {
            Some(k) if !k.trim().is_empty() => k.trim().to_string(),
            _ => std::env::var("COMPOSIO_API_KEY").unwrap_or_default(),
        };
        if key.is_empty() {
            skipped.push((
                "composio".into(),
                "enabled but composio.api_key / COMPOSIO_API_KEY missing".into(),
            ));
        } else {
            tools.push(Arc::new(ComposioTool::new(
                &key,
                Some(&config.composio.entity_id),
                security.clone(),
            )));
        }
    } else {
        skipped.push((
            "composio".into(),
            "disabled (composio.enabled=false)".into(),
        ));
    }

    // ── Google Workspace (requires `gws` CLI — register regardless; the
    //    tool itself reports missing CLI at call time) ────────────────────
    if config.google_workspace.enabled {
        let gws: Arc<dyn Tool> = Arc::new(GoogleWorkspaceTool::new(
            security.clone(),
            config.google_workspace.allowed_services.clone(),
            config.google_workspace.allowed_operations.clone(),
            config.google_workspace.credentials_path.clone(),
            config.google_workspace.default_account.clone(),
            config.google_workspace.rate_limit_per_minute,
            config.google_workspace.timeout_secs,
            config.google_workspace.audit_log,
        ));
        // Opt-in progress: GWS calls may list Drive / Gmail pages.
        tools.push(
            ProgressEnvelope::new(
                gws,
                "google_workspace: invoking gws",
                "google_workspace: done",
            )
            .into_arc(),
        );
    } else {
        skipped.push((
            "google_workspace".into(),
            "disabled (google_workspace.enabled=false)".into(),
        ));
    }

    // ── LinkedIn ────────────────────────────────────────────────────────
    if config.linkedin.enabled {
        tools.push(Arc::new(LinkedInTool::new(
            security.clone(),
            workspace_dir.to_path_buf(),
            config.linkedin.api_version.clone(),
            config.linkedin.content.clone(),
            config.linkedin.image.clone(),
        )));
    } else {
        skipped.push((
            "linkedin".into(),
            "disabled (linkedin.enabled=false)".into(),
        ));
    }

    // ── Microsoft 365 ───────────────────────────────────────────────────
    if config.microsoft365.enabled {
        let ms = &config.microsoft365;
        let tenant_id = ms.tenant_id.as_deref().unwrap_or("").trim().to_string();
        let client_id = ms.client_id.as_deref().unwrap_or("").trim().to_string();
        if tenant_id.is_empty() || client_id.is_empty() {
            skipped.push((
                "microsoft365".into(),
                "enabled but tenant_id or client_id empty".into(),
            ));
        } else if ms.auth_flow.trim() == "client_credentials"
            && ms
                .client_secret
                .as_deref()
                .is_none_or(|s| s.trim().is_empty())
        {
            skipped.push((
                "microsoft365".into(),
                "client_credentials flow needs a client_secret".into(),
            ));
        } else {
            let resolved = crate::tools::microsoft365::types::Microsoft365ResolvedConfig {
                tenant_id,
                client_id,
                client_secret: ms.client_secret.clone(),
                auth_flow: ms.auth_flow.clone(),
                scopes: ms.scopes.clone(),
                token_cache_encrypted: ms.token_cache_encrypted,
                user_id: ms.user_id.as_deref().unwrap_or("me").to_string(),
            };
            // Cache next to config.toml, same policy as the real gateway.
            let cache_dir = config.config_path.parent().unwrap_or(workspace_dir);
            match Microsoft365Tool::new(resolved, security.clone(), cache_dir) {
                Ok(tool) => tools.push(Arc::new(tool)),
                Err(e) => skipped.push((
                    "microsoft365".into(),
                    format!("token cache init failed: {e}"),
                )),
            }
        }
    } else {
        skipped.push((
            "microsoft365".into(),
            "disabled (microsoft365.enabled=false)".into(),
        ));
    }

    (tools, skipped)
}

/// Build the full runtime-aware registry.
///
/// Starts from [`build_tools_with_config`] (so all the integration tools that
/// only need a `Config` are already in) and then, for each of the tools the
/// standalone binary used to skip, checks whether the matching handle is
/// present on `runtime`. If so, the tool is constructed and registered; if
/// not, it goes onto the `skipped` list with a "needs X" reason.
///
/// If `runtime.pre_built_tools` is supplied, those tools are merged in last
/// and win any name collision. This is how the main daemon forwards its
/// already-built `all_tools_with_runtime` registry (which contains delegate,
/// swarm, workspace tool, session tools, every channel-aware tool, etc.) to
/// MCP clients without the registry having to re-invoke every constructor.
#[must_use]
pub fn build_tools_with_runtime(
    workspace_dir: &std::path::Path,
    config: &Config,
    runtime: &RuntimeHandles,
) -> (Vec<Arc<dyn Tool>>, Vec<SkippedEntry>) {
    let security = Arc::new(SecurityPolicy::default());
    let (mut tools, mut skipped) = build_tools_with_config(workspace_dir, config);

    // Drop baseline skip entries that we're about to override — otherwise the
    // operator sees "needs X" next to tools we actually registered.
    let config_arc = Arc::new(config.clone());

    // ── Cron (Config-only; no runtime handle needed) ────────────────────
    tools.push(Arc::new(CronAddTool::new(
        config_arc.clone(),
        security.clone(),
    )));
    tools.push(Arc::new(CronListTool::new(config_arc.clone())));
    tools.push(Arc::new(CronRemoveTool::new(
        config_arc.clone(),
        security.clone(),
    )));
    tools.push(Arc::new(CronUpdateTool::new(
        config_arc.clone(),
        security.clone(),
    )));
    tools.push(Arc::new(CronRunTool::new(
        config_arc.clone(),
        security.clone(),
    )));
    tools.push(Arc::new(CronRunsTool::new(config_arc.clone())));
    drop_skip(&mut skipped, "cron_*");

    // ── llm_task (provider wiring comes from root_config) ───────────────
    {
        let provider = config
            .default_provider
            .clone()
            .unwrap_or_else(|| "openrouter".into());
        let model = config
            .default_model
            .clone()
            .unwrap_or_else(|| "openai/gpt-4o-mini".into());
        let runtime_opts = runtime
            .provider_runtime_options
            .as_deref()
            .cloned()
            .unwrap_or_else(|| crate::providers::ProviderRuntimeOptions {
                auth_profile_override: None,
                provider_api_url: config.api_url.clone(),
                revka_dir: config.config_path.parent().map(std::path::PathBuf::from),
                secrets_encrypt: config.secrets.encrypt,
                reasoning_enabled: config.runtime.reasoning_enabled,
                reasoning_effort: config.runtime.reasoning_effort.clone(),
                provider_timeout_secs: Some(config.provider_timeout_secs),
                extra_headers: config.extra_headers.clone(),
                api_path: config.api_path.clone(),
                provider_max_tokens: config.provider_max_tokens,
            });
        tools.push(Arc::new(LlmTaskTool::new(
            security.clone(),
            provider,
            model,
            config.default_temperature,
            config.api_key.clone(),
            runtime_opts,
        )));
        drop_skip(&mut skipped, "llm_task");
    }

    // ── image_gen (config-gated) ────────────────────────────────────────
    if config.image_gen.enabled {
        tools.push(Arc::new(ImageGenTool::new(
            security.clone(),
            workspace_dir.to_path_buf(),
            config.image_gen.default_model.clone(),
            config.image_gen.api_key_env.clone(),
        )));
    }
    drop_skip(&mut skipped, "image_gen");

    // ── SOP tools (config-gated by sops_dir) ────────────────────────────
    if config.sop.sops_dir.is_some() {
        let engine = Arc::new(std::sync::Mutex::new(crate::sop::SopEngine::new(
            config.sop.clone(),
        )));
        tools.push(Arc::new(crate::tools::SopListTool::new(engine.clone())));
        tools.push(Arc::new(crate::tools::SopExecuteTool::new(engine.clone())));
        tools.push(Arc::new(crate::tools::SopAdvanceTool::new(engine.clone())));
        tools.push(Arc::new(crate::tools::SopApproveTool::new(engine.clone())));
        tools.push(Arc::new(crate::tools::SopStatusTool::new(engine)));
        drop_skip(&mut skipped, "sop_*");
    }

    // ── workspace (needs WorkspaceManager) ──────────────────────────────
    if let Some(ws) = runtime.workspace_manager.clone() {
        tools.push(Arc::new(WorkspaceTool::new(ws, security.clone())));
        drop_skip(&mut skipped, "workspace");
    }

    // ── poll (needs channel_map) ────────────────────────────────────────
    if let Some(map) = runtime.channel_map.clone() {
        tools.push(Arc::new(PollTool::new(security.clone(), map)));
        drop_skip(&mut skipped, "poll");
    }

    // ── reaction / ask_user / escalate (need channel_map) ───────────────
    //
    // These tools each keep their own `ChannelMapHandle`. We want the tool we
    // register here to *share* the same handle the gateway uses, so the
    // channel supervisor's `populate()` call updates both. We therefore
    // revka them with `new()` and then `populate_handle` via the
    // pre-built-tools path below if the gateway supplies them. Until then,
    // register a fresh tool keyed on the supplied handle.
    //
    // Practical note: the cleanest way is for the caller to pass its already-
    // built tool instances via `pre_built_tools` so the underlying handle is
    // literally the same Arc. Constructing new tools here with `new()` gives
    // a fresh empty map — fine for "tool exists" but channel dispatch won't
    // actually route anywhere. We prefer the pre-built path.
    if runtime.reaction_channels.is_some() {
        // Pre-built path will supply the wired-up tool; suppress the skip.
        drop_skip(&mut skipped, "reaction");
    }
    if runtime.ask_user_channels.is_some() {
        drop_skip(&mut skipped, "ask_user");
    }
    if runtime.escalate_channels.is_some() {
        drop_skip(&mut skipped, "escalate");
    }

    // ── discord_search (needs discord-backed Memory) ────────────────────
    if let Some(mem) = runtime.discord_memory.clone() {
        tools.push(Arc::new(DiscordSearchTool::new(mem)));
        drop_skip(&mut skipped, "discord_search");
    }

    // ── sessions_* (need SessionBackend) ────────────────────────────────
    if let Some(backend) = runtime.session_store.clone() {
        tools.push(Arc::new(crate::tools::SessionsListTool::new(
            backend.clone(),
        )));
        tools.push(Arc::new(crate::tools::SessionsHistoryTool::new(
            backend.clone(),
            security.clone(),
        )));
        tools.push(Arc::new(crate::tools::SessionsSendTool::new(
            backend,
            security.clone(),
        )));
        drop_skip(&mut skipped, "sessions_*");
    }

    // ── delegate / swarm ────────────────────────────────────────────────
    //
    // Both depend on the configured `agents` map plus the caller-supplied
    // `ProviderRuntimeOptions`. If the gateway forwards its already-built
    // DelegateTool via `pre_built_tools`, that wins; otherwise we build a
    // fresh one here so at minimum it advertises.
    if let (Some(agents), Some(opts)) = (
        runtime.agent_config.clone(),
        runtime.provider_runtime_options.clone(),
    ) {
        if !agents.is_empty() {
            let fallback = runtime
                .fallback_api_key
                .as_ref()
                .map(|s| s.as_ref().to_string());
            let delegate = crate::tools::DelegateTool::new_with_options(
                (*agents).clone(),
                fallback.clone(),
                security.clone(),
                (*opts).clone(),
            );
            tools.push(Arc::new(delegate));
            drop_skip(&mut skipped, "delegate");

            if !config.swarms.is_empty() {
                tools.push(Arc::new(crate::tools::SwarmTool::new(
                    config.swarms.clone(),
                    (*agents).clone(),
                    fallback,
                    security.clone(),
                    (*opts).clone(),
                )));
                drop_skip(&mut skipped, "swarm");
            }
        }
    }

    // ── pre_built_tools — merge in wholesale (wins collisions) ──────────
    //
    // This is the primary integration point for the main daemon: the gateway
    // hands over its `all_tools_with_runtime()` vec (fully-wired delegate,
    // channel-bound poll/reaction/ask_user/escalate, session-store-backed
    // sessions tools, etc.) and we fold them in, overriding any placeholder
    // we may have built above.
    if let Some(pre) = runtime.pre_built_tools.as_ref() {
        // Strip every collision from the existing tool list first.
        let pre_names: std::collections::HashSet<String> =
            pre.iter().map(|t| t.name().to_string()).collect();
        tools.retain(|t| !pre_names.contains(t.name()));
        tools.extend(pre.iter().cloned());

        // Any tool now present clears the matching skip entry.
        let present_names: std::collections::HashSet<String> =
            tools.iter().map(|t| t.name().to_string()).collect();
        skipped.retain(|(name, _)| {
            // Keep the skip only if the tool truly isn't registered. Handle
            // wildcard `foo_*` entries conservatively: keep them unless any
            // registered tool's prefix matches.
            if let Some(prefix) = name.strip_suffix('*') {
                !present_names.iter().any(|n| n.starts_with(prefix))
            } else {
                !present_names.contains(name)
            }
        });
    }

    (tools, skipped)
}

/// Remove a named entry from `skipped` (used when the runtime-aware registry
/// successfully registers the tool the baseline had listed as skipped).
fn drop_skip(skipped: &mut Vec<SkippedEntry>, name: &str) {
    skipped.retain(|(n, _)| n != name);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Config;

    fn names(tools: &[Arc<dyn Tool>]) -> Vec<&str> {
        tools.iter().map(|t| t.name()).collect()
    }

    #[test]
    fn baseline_registry_is_stable() {
        let (tools, _) = build_default_tools(std::path::Path::new("."));
        let n = names(&tools);
        // The M1 curated tools we committed to expose.
        for must in [
            "shell",
            "file_read",
            "file_write",
            "file_edit",
            "glob_search",
            "content_search",
            "calculator",
            "weather",
            "git_operations",
            "http_request",
            "web_fetch",
            "web_search_tool",
            "pdf_read",
            "screenshot",
            "image_info",
            "browser_open",
        ] {
            assert!(n.contains(&must), "missing baseline tool `{must}`");
        }
    }

    #[test]
    fn empty_config_skips_all_integrations_but_keeps_baseline_and_skills() {
        // Empty default config → no integration creds set, every integration
        // should be skipped with a reason. Baseline + skills meta-tools must
        // still be present.
        let config = Config::default();
        let (tools, skipped) = build_tools_with_config(std::path::Path::new("."), &config);
        let n = names(&tools);
        assert!(n.contains(&"shell"));
        assert!(n.contains(&"skills_list"));
        assert!(n.contains(&"skills_describe"));
        assert!(n.contains(&"skills_execute"));
        // None of the integrations registered.
        assert!(!n.contains(&"notion"));
        assert!(!n.contains(&"jira"));
        assert!(!n.contains(&"composio"));
        assert!(!n.contains(&"google_workspace"));
        assert!(!n.contains(&"linkedin"));
        assert!(!n.contains(&"microsoft365"));
        // And each of them should appear in `skipped` with a disabled reason.
        let names_skipped: Vec<&str> = skipped.iter().map(|(n, _)| n.as_str()).collect();
        for must in [
            "notion",
            "jira",
            "composio",
            "google_workspace",
            "linkedin",
            "microsoft365",
        ] {
            assert!(
                names_skipped.contains(&must),
                "expected `{must}` in skipped list"
            );
        }
    }

    #[test]
    fn notion_registered_when_api_key_present() {
        let mut config = Config::default();
        config.notion.enabled = true;
        config.notion.api_key = "secret_dummy_for_test".into();
        let (tools, _skipped) = build_tools_with_config(std::path::Path::new("."), &config);
        let n = names(&tools);
        assert!(n.contains(&"notion"), "expected notion tool; got {n:?}");
    }

    #[test]
    fn jira_enabled_but_missing_creds_is_skipped() {
        let mut config = Config::default();
        config.jira.enabled = true; // no base_url / email / token
        let (tools, skipped) = build_tools_with_config(std::path::Path::new("."), &config);
        let n = names(&tools);
        assert!(!n.contains(&"jira"));
        assert!(skipped.iter().any(|(name, _)| name == "jira"));
    }
}
