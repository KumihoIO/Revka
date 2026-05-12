use crate::cli_input::Input;
use crate::config::schema::{
    DingTalkConfig, IrcConfig, LarkReceiveMode, LinqConfig, NextcloudTalkConfig, QQConfig,
    SignalConfig, StreamMode, WhatsAppChatPolicy, WhatsAppConfig, WhatsAppWebMode,
};
#[cfg(feature = "channel-nostr")]
use crate::config::schema::{NostrConfig, default_nostr_relays};
use crate::config::{
    AutonomyConfig, BrowserConfig, ChannelsConfig, ComposioConfig, Config, DiscordConfig,
    HeartbeatConfig, IMessageConfig, LarkConfig, MatrixConfig, MemoryConfig, ObservabilityConfig,
    RuntimeConfig, SecretsConfig, SlackConfig, StorageConfig, TelegramConfig, WebhookConfig,
};
use crate::hardware::{self, HardwareConfig};
use crate::i18n::{self, Lang};
use crate::memory::{
    default_memory_backend_key, memory_backend_profile, selectable_memory_backends,
};
use crate::providers::{
    canonical_china_provider_name, is_glm_alias, is_glm_cn_alias, is_minimax_alias,
    is_moonshot_alias, is_qianfan_alias, is_qwen_alias, is_qwen_oauth_alias, is_zai_alias,
    is_zai_cn_alias,
};
use crate::t;
use anyhow::{Context, Result, bail};
use console::style;
use dialoguer::{Confirm, Select};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;
use std::io::IsTerminal;
use std::path::{Path, PathBuf};
use std::time::Duration;
use tokio::fs;

// ── Project context collected during wizard ──────────────────────

/// User-provided personalization baked into workspace MD files.
#[derive(Debug, Clone, Default)]
pub struct ProjectContext {
    pub user_name: String,
    pub timezone: String,
    pub agent_name: String,
    pub communication_style: String,
}

// ── Banner ───────────────────────────────────────────────────────

const BANNER: &str = r"
    01001000 01000101 01001100 01001100 01001111 00100000 01001110 01000101 01001111

     ██████╗ ██████╗ ███╗   ██╗███████╗████████╗██████╗ ██╗   ██╗ ██████╗████████╗
    ██╔════╝██╔═══██╗████╗  ██║██╔════╝╚══██╔══╝██╔══██╗██║   ██║██╔════╝╚══██╔══╝
    ██║     ██║   ██║██╔██╗ ██║███████╗   ██║   ██████╔╝██║   ██║██║        ██║
    ██║     ██║   ██║██║╚██╗██║╚════██║   ██║   ██╔══██╗██║   ██║██║        ██║
    ╚██████╗╚██████╔╝██║ ╚████║███████║   ██║   ██║  ██║╚██████╔╝╚██████╗   ██║
     ╚═════╝ ╚═════╝ ╚═╝  ╚═══╝╚══════╝   ╚═╝   ╚═╝  ╚═╝ ╚═════╝  ╚═════╝   ╚═╝

    Are you the one...?

    01001000 01000101 01001100 01001100 01001111 00100000 01001110 01000101 01001111
";

const LIVE_MODEL_MAX_OPTIONS: usize = 120;
const MODEL_PREVIEW_LIMIT: usize = 20;
const MODEL_CACHE_FILE: &str = "models_cache.json";
const MODEL_CACHE_TTL_SECS: u64 = 12 * 60 * 60;
const CUSTOM_MODEL_SENTINEL: &str = "__custom_model__";

fn has_launchable_channels(channels: &ChannelsConfig) -> bool {
    channels.channels_except_webhook().iter().any(|(_, ok)| *ok)
}

// ── Step 0: language picker ──────────────────────────────────────

/// Bilingual language selection shown before the banner. Pre-selects whatever
/// language was already active (from `--lang`, `CONSTRUCT_LANG`, or `$LANG`),
/// so users who set the flag can just press Enter; users who didn't see a
/// "Select your language / 언어를 선택하세요" prompt that's readable either way.
fn setup_language() -> Result<()> {
    let options: Vec<&str> = Lang::all().iter().map(|l| l.display_name()).collect();
    let initial = Lang::all()
        .iter()
        .position(|l| *l == i18n::lang())
        .unwrap_or(0);
    let pick = Select::new()
        .with_prompt(t!("step-language-prompt"))
        .items(&options)
        .default(initial)
        .interact()?;
    let chosen = Lang::all()[pick];
    i18n::set_lang(chosen);
    println!(
        "  {}",
        style(t!("step-language-saved", lang = chosen.display_name())).green()
    );
    Ok(())
}

// ── Main wizard entry point ──────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum InteractiveOnboardingMode {
    FullOnboarding,
    UpdateProviderOnly,
}

pub async fn run_wizard(force: bool) -> Result<Config> {
    // Step 0: language picker. Shown bilingually so a user who arrived without
    // --lang or $LANG can still read it. After this point all wizard surfaces
    // render in the selected language.
    setup_language()?;

    println!("{}", style(BANNER).green().bold());

    println!("  {}", style(t!("welcome-title")).green().bold());
    println!("  {}", style(t!("welcome-subtitle")).dim());
    println!();

    print_step(1, 9, &t!("step-1-title"));
    let (workspace_dir, config_path) = setup_workspace().await?;
    match resolve_interactive_onboarding_mode(&config_path, force)? {
        InteractiveOnboardingMode::FullOnboarding => {}
        InteractiveOnboardingMode::UpdateProviderOnly => {
            return Box::pin(run_provider_update_wizard(&workspace_dir, &config_path)).await;
        }
    }

    print_step(2, 9, &t!("step-2-title"));
    let (provider, api_key, model, provider_api_url) = setup_provider(&workspace_dir).await?;

    print_step(3, 9, &t!("step-3-title"));
    let channels_config = setup_channels()?;

    print_step(4, 9, &t!("step-4-title"));
    let tunnel_config = setup_tunnel()?;

    print_step(5, 9, &t!("step-5-title"));
    let (composio_config, secrets_config) = setup_tool_mode()?;

    print_step(6, 9, &t!("step-6-title"));
    let hardware_config = setup_hardware()?;

    print_step(7, 9, &t!("step-7-title"));
    let memory_result = setup_memory()?;
    let is_kumiho_backend = memory_result.memory_config.backend == "kumiho";
    let memory_config = memory_result.memory_config;

    print_step(8, 9, &t!("step-8-title"));
    let project_ctx = setup_project_context()?;

    print_step(9, 9, &t!("step-9-title"));
    scaffold_workspace(&workspace_dir, &project_ctx, &memory_config.backend).await?;

    // ── Build config ──
    // Defaults: Kumiho memory, supervised autonomy, workspace-scoped, native runtime
    let config = Config {
        workspace_dir: workspace_dir.clone(),
        config_path: config_path.clone(),
        api_key: if api_key.is_empty() {
            None
        } else {
            Some(api_key)
        },
        api_url: provider_api_url,
        api_path: None,
        default_provider: Some(provider),
        default_model: Some(model),
        model_providers: std::collections::HashMap::new(),
        default_temperature: 0.7,
        // Persist the wizard's UI language so subsequent runs default to it
        // without requiring --lang / CONSTRUCT_LANG / $LANG every time.
        language: Some(i18n::lang().code().to_string()),
        provider_timeout_secs: 120,
        provider_max_tokens: None,
        extra_headers: std::collections::HashMap::new(),
        observability: ObservabilityConfig::default(),
        autonomy: AutonomyConfig::default(),
        trust: crate::trust::TrustConfig::default(),
        backup: crate::config::BackupConfig::default(),
        data_retention: crate::config::DataRetentionConfig::default(),
        cloud_ops: crate::config::CloudOpsConfig::default(),
        conversational_ai: crate::config::ConversationalAiConfig::default(),
        security: crate::config::SecurityConfig::default(),
        security_ops: crate::config::SecurityOpsConfig::default(),
        runtime: RuntimeConfig::default(),
        reliability: crate::config::ReliabilityConfig::default(),
        scheduler: crate::config::schema::SchedulerConfig::default(),
        agent: crate::config::schema::AgentConfig::default(),
        pacing: crate::config::PacingConfig::default(),
        skills: crate::config::SkillsConfig::default(),
        pipeline: crate::config::PipelineConfig::default(),
        model_routes: Vec::new(),
        embedding_routes: Vec::new(),
        heartbeat: HeartbeatConfig::default(),
        cron: crate::config::CronConfig::default(),
        channels_config,
        memory: memory_config, // User-selected memory backend
        storage: StorageConfig::default(),
        tunnel: tunnel_config,
        gateway: crate::config::GatewayConfig::default(),
        composio: composio_config,
        microsoft365: crate::config::Microsoft365Config::default(),
        secrets: secrets_config,
        browser: BrowserConfig::default(),
        browser_delegate: crate::tools::browser_delegate::BrowserDelegateConfig::default(),
        http_request: crate::config::HttpRequestConfig::default(),
        multimodal: crate::config::MultimodalConfig::default(),
        media_pipeline: crate::config::MediaPipelineConfig::default(),
        web_fetch: crate::config::WebFetchConfig::default(),
        link_enricher: crate::config::LinkEnricherConfig::default(),
        text_browser: crate::config::TextBrowserConfig::default(),
        web_search: crate::config::WebSearchConfig::default(),
        project_intel: crate::config::ProjectIntelConfig::default(),
        google_workspace: crate::config::GoogleWorkspaceConfig::default(),
        proxy: crate::config::ProxyConfig::default(),
        identity: crate::config::IdentityConfig::default(),
        cost: crate::config::CostConfig::default(),
        peripherals: crate::config::PeripheralsConfig::default(),
        delegate: crate::config::DelegateToolConfig::default(),
        agents: std::collections::HashMap::new(),
        swarms: std::collections::HashMap::new(),
        hooks: crate::config::HooksConfig::default(),
        hardware: hardware_config,
        query_classification: crate::config::QueryClassificationConfig::default(),
        transcription: crate::config::TranscriptionConfig::default(),
        tts: crate::config::TtsConfig::default(),
        mcp: crate::config::McpConfig::default(),
        kumiho: {
            let mut kc = crate::config::KumihoConfig::default();
            if let Some(ref url) = memory_result.kumiho_api_url {
                kc.api_url = url.clone();
            }
            if is_kumiho_backend {
                kc.enabled = true;
            }
            kc
        },
        operator: crate::config::OperatorConfig::default(),
        nodes: crate::config::NodesConfig::default(),
        clawhub: crate::config::ClawHubConfig::default(),
        workspace: crate::config::WorkspaceConfig::default(),
        notion: crate::config::NotionConfig::default(),
        jira: crate::config::JiraConfig::default(),
        node_transport: crate::config::NodeTransportConfig::default(),
        linkedin: crate::config::LinkedInConfig::default(),
        image_gen: crate::config::ImageGenConfig::default(),
        plugins: crate::config::PluginsConfig::default(),
        locale: None,
        verifiable_intent: crate::config::VerifiableIntentConfig::default(),
        claude_code: crate::config::ClaudeCodeConfig::default(),
        claude_code_runner: crate::config::ClaudeCodeRunnerConfig::default(),
        codex_cli: crate::config::CodexCliConfig::default(),
        gemini_cli: crate::config::GeminiCliConfig::default(),
        opencode_cli: crate::config::OpenCodeCliConfig::default(),
        sop: crate::config::SopConfig::default(),
        shell_tool: crate::config::ShellToolConfig::default(),
    };

    println!(
        "  {} Security: {} | workspace-scoped",
        style("✓").green().bold(),
        style("Supervised").green()
    );
    println!(
        "  {} Memory: {} (auto-save: {})",
        style("✓").green().bold(),
        style(&config.memory.backend).green(),
        if config.memory.auto_save { "on" } else { "off" }
    );

    config.save().await?;
    persist_workspace_selection(&config.config_path).await?;

    // ── DreamState cron job (Kumiho only) ─────────────────────────
    // DreamState is Kumiho's memory consolidation cycle: it reviews recent
    // memories, deprecates stale ones, enriches tags/metadata, and creates
    // graph edges between related items.  Recommended to run nightly.
    if is_kumiho_backend {
        let setup_dream_state: bool = Confirm::new()
            .with_prompt(format!(
                "  {} {}",
                style("\u{1f319}").cyan(),
                t!("dreamstate-prompt")
            ))
            .default(true)
            .interact()?;

        if setup_dream_state {
            let time_options = vec![
                "2:00 AM  (quiet hours)",
                "3:00 AM  (default)",
                "4:00 AM  (early morning)",
                "6:00 AM  (before work)",
                "12:00 AM (midnight)",
            ];
            let time_crons = [
                "0 2 * * *",
                "0 3 * * *",
                "0 4 * * *",
                "0 6 * * *",
                "0 0 * * *",
            ];

            let time_idx = Select::new()
                .with_prompt(format!(
                    "  {} {}",
                    style("\u{23f0}").cyan(),
                    t!("dreamstate-time-prompt")
                ))
                .items(&time_options)
                .default(1) // 3 AM
                .interact()?;

            let schedule = crate::cron::Schedule::Cron {
                expr: time_crons[time_idx].to_string(),
                tz: None,
            };
            match crate::cron::add_agent_job(
                &config,
                Some("DreamState — memory consolidation".to_string()),
                schedule,
                "Run kumiho_memory_dream_state to consolidate long-term memories: \
                 deprecate stale ones, enrich metadata, add tags, and link related memories.",
                crate::cron::SessionTarget::Isolated,
                None,  // model: use default
                None,  // delivery: none
                false, // delete_after_run: recurring
                Some(vec!["kumiho_memory_dream_state".to_string()]),
            ) {
                Ok(job) => println!(
                    "  {} DreamState cron job created (schedule: {}, next run: {})",
                    style("\u{2713}").green().bold(),
                    style(
                        time_options[time_idx]
                            .split_whitespace()
                            .next()
                            .unwrap_or("")
                    )
                    .cyan(),
                    style(job.next_run.format("%Y-%m-%d %H:%M UTC")).cyan()
                ),
                Err(e) => println!(
                    "  {} Could not register DreamState cron job: {e}",
                    style("\u{26a0}").yellow().bold()
                ),
            }
        }
    }

    // If user provided a Kumiho service token, write it to .env for convenience.
    if let Some(ref token) = memory_result.kumiho_service_token {
        let env_path = workspace_dir.join(".env");
        let env_line = format!("KUMIHO_SERVICE_TOKEN={}\n", token);
        // Append if exists, create if not.
        if env_path.exists() {
            let existing = fs::read_to_string(&env_path).await.unwrap_or_default();
            if !existing.contains("KUMIHO_SERVICE_TOKEN") {
                fs::write(&env_path, format!("{}{}", existing, env_line)).await?;
            }
        } else {
            fs::write(&env_path, env_line).await?;
        }
        println!(
            "  {} Kumiho token saved to {}",
            style("✓").green().bold(),
            style(env_path.display()).cyan()
        );
    }

    // ── Final summary ────────────────────────────────────────────
    print_summary(&config);

    // ── Offer to launch channels immediately ─────────────────────
    let has_channels = has_launchable_channels(&config.channels_config);

    if has_channels && config.api_key.is_some() {
        let launch: bool = Confirm::new()
            .with_prompt(format!(
                "  {} Launch channels now? (connected channels → AI → reply)",
                style("🚀").cyan()
            ))
            .default(true)
            .interact()?;

        if launch {
            println!();
            println!(
                "  {} {}",
                style("⚡").cyan(),
                style("Starting channel server...").white().bold()
            );
            println!();
            // Signal to main.rs to call start_channels after wizard returns
            // SAFETY: called during single-threaded onboarding wizard before async runtime.
            unsafe { std::env::set_var("CONSTRUCT_AUTOSTART_CHANNELS", "1") };
        }
    }

    Ok(config)
}

/// Interactive repair flow: rerun channel setup only without redoing full onboarding.
pub async fn run_channels_repair_wizard() -> Result<Config> {
    println!("{}", style(BANNER).cyan().bold());
    println!(
        "  {}",
        style("Channels Repair — update channel tokens and allowlists only")
            .white()
            .bold()
    );
    println!();

    let mut config = Box::pin(Config::load_or_init()).await?;

    print_step(1, 1, "Channels (How You Talk to Construct)");
    config.channels_config = setup_channels()?;
    config.save().await?;
    persist_workspace_selection(&config.config_path).await?;

    println!();
    println!(
        "  {} Channel config saved: {}",
        style("✓").green().bold(),
        style(config.config_path.display()).green()
    );

    let has_channels = has_launchable_channels(&config.channels_config);

    if has_channels && config.api_key.is_some() {
        let launch: bool = Confirm::new()
            .with_prompt(format!(
                "  {} Launch channels now? (connected channels → AI → reply)",
                style("🚀").cyan()
            ))
            .default(true)
            .interact()?;

        if launch {
            println!();
            println!(
                "  {} {}",
                style("⚡").cyan(),
                style("Starting channel server...").white().bold()
            );
            println!();
            // Signal to main.rs to call start_channels after wizard returns
            // SAFETY: called during single-threaded onboarding wizard before async runtime.
            unsafe { std::env::set_var("CONSTRUCT_AUTOSTART_CHANNELS", "1") };
        }
    }

    Ok(config)
}

/// Interactive flow: update only provider/model/api key while preserving existing config.
async fn run_provider_update_wizard(workspace_dir: &Path, config_path: &Path) -> Result<Config> {
    println!();
    println!(
        "  {} Existing config detected. Running provider-only update mode (preserving channels, memory, tunnel, hooks, and other settings).",
        style("↻").cyan().bold()
    );

    let raw = fs::read_to_string(config_path).await.with_context(|| {
        format!(
            "Failed to read existing config at {}",
            config_path.display()
        )
    })?;
    let mut config: Config = toml::from_str(&raw).with_context(|| {
        format!(
            "Failed to parse existing config at {}",
            config_path.display()
        )
    })?;
    config.workspace_dir = workspace_dir.to_path_buf();
    config.config_path = config_path.to_path_buf();

    print_step(1, 1, "AI Provider & API Key");
    let (provider, api_key, model, provider_api_url) = setup_provider(workspace_dir).await?;
    apply_provider_update(&mut config, provider, api_key, model, provider_api_url);

    config.save().await?;
    persist_workspace_selection(&config.config_path).await?;

    println!(
        "  {} Provider settings updated at {}",
        style("✓").green().bold(),
        style(config.config_path.display()).green()
    );
    print_summary(&config);

    let has_channels = has_launchable_channels(&config.channels_config);
    if has_channels && config.api_key.is_some() {
        let launch: bool = Confirm::new()
            .with_prompt(format!(
                "  {} Launch channels now? (connected channels → AI → reply)",
                style("🚀").cyan()
            ))
            .default(true)
            .interact()?;

        if launch {
            println!();
            println!(
                "  {} {}",
                style("⚡").cyan(),
                style("Starting channel server...").white().bold()
            );
            println!();
            // SAFETY: called during single-threaded onboarding wizard before async runtime.
            unsafe { std::env::set_var("CONSTRUCT_AUTOSTART_CHANNELS", "1") };
        }
    }

    Ok(config)
}

fn apply_provider_update(
    config: &mut Config,
    provider: String,
    api_key: String,
    model: String,
    provider_api_url: Option<String>,
) {
    config.default_provider = Some(provider);
    config.default_model = Some(model);
    config.api_url = provider_api_url;
    config.api_key = if api_key.trim().is_empty() {
        None
    } else {
        Some(api_key)
    };
}

// ── Quick setup (zero prompts) ───────────────────────────────────

/// Non-interactive setup: generates a sensible default config instantly.
/// Use `construct onboard` or `construct onboard --api-key sk-... --provider openrouter --memory kumiho|none`.
fn backend_key_from_choice(choice: usize) -> &'static str {
    selectable_memory_backends()
        .get(choice)
        .map_or(default_memory_backend_key(), |backend| backend.key)
}

fn memory_config_defaults_for_backend(backend: &str) -> MemoryConfig {
    let profile = memory_backend_profile(backend);

    MemoryConfig {
        backend: backend.to_string(),
        auto_save: profile.auto_save_default,
        hygiene_enabled: profile.uses_sqlite_hygiene,
        archive_after_days: if profile.uses_sqlite_hygiene { 7 } else { 0 },
        purge_after_days: if profile.uses_sqlite_hygiene { 30 } else { 0 },
        conversation_retention_days: 30,
        min_relevance_score: 0.4,
        retrieval_limit: 3,
        response_cache_enabled: false,
        response_cache_ttl_minutes: 60,
        response_cache_max_entries: 5_000,
        response_cache_hot_entries: 256,
        snapshot_enabled: false,
        snapshot_on_hygiene: false,
        auto_hydrate: true,
        default_namespace: "default".into(),
        audit_enabled: false,
        audit_retention_days: 30,
        policy: crate::config::MemoryPolicyConfig::default(),
    }
}

#[allow(clippy::too_many_lines)]
pub async fn run_quick_setup(
    credential_override: Option<&str>,
    provider: Option<&str>,
    model_override: Option<&str>,
    memory_backend: Option<&str>,
    force: bool,
) -> Result<Config> {
    let home = directories::UserDirs::new()
        .map(|u| u.home_dir().to_path_buf())
        .context("Could not find home directory")?;

    Box::pin(run_quick_setup_with_home(
        credential_override,
        provider,
        model_override,
        memory_backend,
        force,
        &home,
    ))
    .await
}

fn resolve_quick_setup_dirs_with_home(home: &Path) -> (PathBuf, PathBuf) {
    if let Ok(custom_config_dir) = std::env::var("CONSTRUCT_CONFIG_DIR") {
        let trimmed = custom_config_dir.trim();
        if !trimmed.is_empty() {
            let config_dir = PathBuf::from(shellexpand::tilde(trimmed).as_ref());
            return (config_dir.clone(), config_dir.join("workspace"));
        }
    }

    if let Ok(custom_workspace) = std::env::var("CONSTRUCT_WORKSPACE") {
        let trimmed = custom_workspace.trim();
        if !trimmed.is_empty() {
            let expanded = shellexpand::tilde(trimmed);
            return crate::config::schema::resolve_config_dir_for_workspace(&PathBuf::from(
                expanded.as_ref(),
            ));
        }
    }

    let config_dir = home.join(".construct");
    (config_dir.clone(), config_dir.join("workspace"))
}

fn homebrew_prefix_for_exe(exe: &Path) -> Option<&'static str> {
    let exe = exe.to_string_lossy();
    if exe == "/opt/homebrew/bin/construct"
        || exe.starts_with("/opt/homebrew/Cellar/construct/")
        || exe.starts_with("/opt/homebrew/opt/construct/")
    {
        return Some("/opt/homebrew");
    }

    if exe == "/usr/local/bin/construct"
        || exe.starts_with("/usr/local/Cellar/construct/")
        || exe.starts_with("/usr/local/opt/construct/")
    {
        return Some("/usr/local");
    }

    None
}

fn quick_setup_homebrew_service_note(
    config_path: &Path,
    workspace_dir: &Path,
    exe: &Path,
) -> Option<String> {
    let prefix = homebrew_prefix_for_exe(exe)?;
    let service_root = Path::new(prefix).join("var").join("construct");
    let service_config = service_root.join("config.toml");
    let service_workspace = service_root.join("workspace");

    if config_path == service_config || workspace_dir == service_workspace {
        return None;
    }

    Some(format!(
        "Homebrew service note: `brew services` uses {} (config {}) by default. Your onboarding just wrote {}. If you plan to run Construct as a service, copy or link this workspace first.",
        service_workspace.display(),
        service_config.display(),
        config_path.display(),
    ))
}

#[allow(clippy::too_many_lines)]
async fn run_quick_setup_with_home(
    credential_override: Option<&str>,
    provider: Option<&str>,
    model_override: Option<&str>,
    memory_backend: Option<&str>,
    force: bool,
    home: &Path,
) -> Result<Config> {
    println!("{}", style(BANNER).cyan().bold());
    println!(
        "  {}",
        style("Quick Setup — generating config with sensible defaults...")
            .white()
            .bold()
    );
    println!();

    let (construct_dir, workspace_dir) = resolve_quick_setup_dirs_with_home(home);
    let config_path = construct_dir.join("config.toml");

    ensure_onboard_overwrite_allowed(&config_path, force)?;
    fs::create_dir_all(&workspace_dir)
        .await
        .context("Failed to create workspace directory")?;

    let provider_name = provider.unwrap_or("openrouter").to_string();
    let model = model_override
        .map(str::to_string)
        .unwrap_or_else(|| default_model_for_provider(&provider_name));
    let memory_backend_name = memory_backend
        .unwrap_or(default_memory_backend_key())
        .to_string();

    // Create memory config based on backend choice
    let memory_config = memory_config_defaults_for_backend(&memory_backend_name);

    let config = Config {
        workspace_dir: workspace_dir.clone(),
        config_path: config_path.clone(),
        api_key: credential_override.map(|c| {
            let mut s = String::with_capacity(c.len());
            s.push_str(c);
            s
        }),
        api_url: None,
        api_path: None,
        default_provider: Some(provider_name.clone()),
        default_model: Some(model.clone()),
        model_providers: std::collections::HashMap::new(),
        default_temperature: 0.7,
        // Persist the wizard's UI language so subsequent runs default to it
        // without requiring --lang / CONSTRUCT_LANG / $LANG every time.
        language: Some(i18n::lang().code().to_string()),
        provider_timeout_secs: 120,
        provider_max_tokens: None,
        extra_headers: std::collections::HashMap::new(),
        observability: ObservabilityConfig::default(),
        autonomy: AutonomyConfig::default(),
        trust: crate::trust::TrustConfig::default(),
        backup: crate::config::BackupConfig::default(),
        data_retention: crate::config::DataRetentionConfig::default(),
        cloud_ops: crate::config::CloudOpsConfig::default(),
        conversational_ai: crate::config::ConversationalAiConfig::default(),
        security: crate::config::SecurityConfig::default(),
        security_ops: crate::config::SecurityOpsConfig::default(),
        runtime: RuntimeConfig::default(),
        reliability: crate::config::ReliabilityConfig::default(),
        scheduler: crate::config::schema::SchedulerConfig::default(),
        agent: crate::config::schema::AgentConfig::default(),
        pacing: crate::config::PacingConfig::default(),
        skills: crate::config::SkillsConfig::default(),
        pipeline: crate::config::PipelineConfig::default(),
        model_routes: Vec::new(),
        embedding_routes: Vec::new(),
        heartbeat: HeartbeatConfig::default(),
        cron: crate::config::CronConfig::default(),
        channels_config: ChannelsConfig::default(),
        memory: memory_config,
        storage: StorageConfig::default(),
        tunnel: crate::config::TunnelConfig::default(),
        gateway: crate::config::GatewayConfig::default(),
        composio: ComposioConfig::default(),
        microsoft365: crate::config::Microsoft365Config::default(),
        secrets: SecretsConfig::default(),
        browser: BrowserConfig::default(),
        browser_delegate: crate::tools::browser_delegate::BrowserDelegateConfig::default(),
        http_request: crate::config::HttpRequestConfig::default(),
        multimodal: crate::config::MultimodalConfig::default(),
        media_pipeline: crate::config::MediaPipelineConfig::default(),
        web_fetch: crate::config::WebFetchConfig::default(),
        link_enricher: crate::config::LinkEnricherConfig::default(),
        text_browser: crate::config::TextBrowserConfig::default(),
        web_search: crate::config::WebSearchConfig::default(),
        project_intel: crate::config::ProjectIntelConfig::default(),
        google_workspace: crate::config::GoogleWorkspaceConfig::default(),
        proxy: crate::config::ProxyConfig::default(),
        identity: crate::config::IdentityConfig::default(),
        cost: crate::config::CostConfig::default(),
        peripherals: crate::config::PeripheralsConfig::default(),
        delegate: crate::config::DelegateToolConfig::default(),
        agents: std::collections::HashMap::new(),
        swarms: std::collections::HashMap::new(),
        hooks: crate::config::HooksConfig::default(),
        hardware: crate::config::HardwareConfig::default(),
        query_classification: crate::config::QueryClassificationConfig::default(),
        transcription: crate::config::TranscriptionConfig::default(),
        tts: crate::config::TtsConfig::default(),
        mcp: crate::config::McpConfig::default(),
        kumiho: crate::config::KumihoConfig::default(),
        operator: crate::config::OperatorConfig::default(),
        nodes: crate::config::NodesConfig::default(),
        clawhub: crate::config::ClawHubConfig::default(),
        workspace: crate::config::WorkspaceConfig::default(),
        notion: crate::config::NotionConfig::default(),
        jira: crate::config::JiraConfig::default(),
        node_transport: crate::config::NodeTransportConfig::default(),
        linkedin: crate::config::LinkedInConfig::default(),
        image_gen: crate::config::ImageGenConfig::default(),
        plugins: crate::config::PluginsConfig::default(),
        locale: None,
        verifiable_intent: crate::config::VerifiableIntentConfig::default(),
        claude_code: crate::config::ClaudeCodeConfig::default(),
        claude_code_runner: crate::config::ClaudeCodeRunnerConfig::default(),
        codex_cli: crate::config::CodexCliConfig::default(),
        gemini_cli: crate::config::GeminiCliConfig::default(),
        opencode_cli: crate::config::OpenCodeCliConfig::default(),
        sop: crate::config::SopConfig::default(),
        shell_tool: crate::config::ShellToolConfig::default(),
    };

    config.save().await?;
    persist_workspace_selection(&config.config_path).await?;

    // Scaffold minimal workspace files
    let default_ctx = ProjectContext {
        user_name: std::env::var("USER").unwrap_or_else(|_| "User".into()),
        timezone: "UTC".into(),
        agent_name: "Construct".into(),
        communication_style:
            "Be warm, natural, and clear. Use occasional relevant emojis (1-2 max) and avoid robotic phrasing."
                .into(),
    };
    scaffold_workspace(&workspace_dir, &default_ctx, &memory_backend_name).await?;

    println!(
        "  {} Workspace:  {}",
        style("✓").green().bold(),
        style(workspace_dir.display()).green()
    );
    println!(
        "  {} Provider:   {}",
        style("✓").green().bold(),
        style(&provider_name).green()
    );
    println!(
        "  {} Model:      {}",
        style("✓").green().bold(),
        style(&model).green()
    );
    println!(
        "  {} API Key:    {}",
        style("✓").green().bold(),
        if credential_override.is_some() {
            style("set").green()
        } else {
            style("not set (use --api-key or edit config.toml)").yellow()
        }
    );
    println!(
        "  {} Security:   {}",
        style("✓").green().bold(),
        style("Supervised (workspace-scoped)").green()
    );
    println!(
        "  {} Memory:     {} (auto-save: {})",
        style("✓").green().bold(),
        style(&memory_backend_name).green(),
        if memory_backend_name == "none" {
            "off"
        } else {
            "on"
        }
    );
    println!(
        "  {} Secrets:    {}",
        style("✓").green().bold(),
        style("encrypted").green()
    );
    println!(
        "  {} Gateway:    {}",
        style("✓").green().bold(),
        style("pairing required (127.0.0.1:8080)").green()
    );
    println!(
        "  {} Tunnel:     {}",
        style("✓").green().bold(),
        style("none (local only)").dim()
    );
    println!(
        "  {} Composio:   {}",
        style("✓").green().bold(),
        style("disabled (sovereign mode)").dim()
    );
    println!();
    println!(
        "  {} {}",
        style("Config saved:").white().bold(),
        style(config_path.display()).green()
    );
    if cfg!(target_os = "macos") {
        if let Ok(exe) = std::env::current_exe() {
            if let Some(note) =
                quick_setup_homebrew_service_note(&config_path, &workspace_dir, &exe)
            {
                println!();
                println!("  {}", style(note).yellow());
            }
        }
    }
    println!();
    println!("  {}", style(t!("next-steps-header")).white().bold());
    if credential_override.is_none() {
        if provider_supports_keyless_local_usage(&provider_name) {
            println!(
                "    1. {:13} {}",
                t!("next-action-chat"),
                t!("next-cmd-chat-hello")
            );
            println!(
                "    2. {:13} {}",
                t!("next-action-gateway"),
                t!("next-cmd-gateway")
            );
            println!(
                "    3. {:13} {}",
                t!("next-action-status"),
                t!("next-cmd-status")
            );
        } else if provider_supports_device_flow(&provider_name) {
            if canonical_provider_name(&provider_name) == "copilot" {
                println!(
                    "    1. {:22} {}",
                    t!("next-action-chat"),
                    t!("next-cmd-chat-hello")
                );
                println!("       (device / OAuth auth will prompt on first run)");
                println!(
                    "    2. {:22} {}",
                    t!("next-action-gateway"),
                    t!("next-cmd-gateway")
                );
                println!(
                    "    3. {:22} {}",
                    t!("next-action-status"),
                    t!("next-cmd-status")
                );
            } else {
                println!(
                    "    1. {:22} {}",
                    t!("next-action-login"),
                    t!("next-cmd-login", provider = provider_name.to_string())
                );
                println!(
                    "    2. {:22} {}",
                    t!("next-action-chat"),
                    t!("next-cmd-chat-hello")
                );
                println!(
                    "    3. {:22} {}",
                    t!("next-action-gateway"),
                    t!("next-cmd-gateway")
                );
                println!(
                    "    4. {:22} {}",
                    t!("next-action-status"),
                    t!("next-cmd-status")
                );
            }
        } else {
            let env_var = provider_env_var(&provider_name);
            println!(
                "    1. {:22} {}",
                t!("next-action-set-key"),
                t!("next-cmd-export-key", env_var = env_var.to_string())
            );
            println!(
                "    2. {:22} {}",
                t!("next-action-or-edit"),
                t!("next-cmd-config-toml")
            );
            println!(
                "    3. {:22} {}",
                t!("next-action-chat"),
                t!("next-cmd-chat-hello")
            );
            println!(
                "    4. {:22} {}",
                t!("next-action-gateway"),
                t!("next-cmd-gateway")
            );
        }
    } else {
        println!(
            "    1. {:13} {}",
            t!("next-action-chat"),
            t!("next-cmd-chat-hello")
        );
        println!(
            "    2. {:13} {}",
            t!("next-action-gateway"),
            t!("next-cmd-gateway")
        );
        println!(
            "    3. {:13} {}",
            t!("next-action-status"),
            t!("next-cmd-status")
        );
    }
    println!();

    Ok(config)
}

fn canonical_provider_name(provider_name: &str) -> &str {
    if is_qwen_oauth_alias(provider_name) {
        return "qwen-code";
    }

    if let Some(canonical) = canonical_china_provider_name(provider_name) {
        return canonical;
    }

    match provider_name {
        "grok" => "xai",
        "together" => "together-ai",
        "google" | "google-gemini" => "gemini",
        "github-copilot" => "copilot",
        "openai_codex" | "codex" => "openai-codex",
        "kimi_coding" | "kimi_for_coding" => "kimi-code",
        "nvidia-nim" | "build.nvidia.com" => "nvidia",
        "aws-bedrock" => "bedrock",
        "llama.cpp" => "llamacpp",
        _ => provider_name,
    }
}

fn allows_unauthenticated_model_fetch(provider_name: &str) -> bool {
    matches!(
        canonical_provider_name(provider_name),
        "openrouter"
            | "ollama"
            | "llamacpp"
            | "sglang"
            | "vllm"
            | "osaurus"
            | "venice"
            | "astrai"
            | "nvidia"
    )
}

/// Pick a sensible default model for the given provider.
const MINIMAX_ONBOARD_MODELS: [(&str, &str); 7] = [
    (
        "MiniMax-M2.7",
        "MiniMax M2.7 (latest flagship, recommended)",
    ),
    ("MiniMax-M2.7-highspeed", "MiniMax M2.7 High-Speed (faster)"),
    ("MiniMax-M2.5", "MiniMax M2.5 (stable)"),
    ("MiniMax-M2.5-highspeed", "MiniMax M2.5 High-Speed (faster)"),
    ("MiniMax-M2.1", "MiniMax M2.1 (previous gen)"),
    ("MiniMax-M2.1-highspeed", "MiniMax M2.1 High-Speed (faster)"),
    ("MiniMax-M2", "MiniMax M2 (legacy)"),
];

fn default_model_for_provider(provider: &str) -> String {
    match canonical_provider_name(provider) {
        "anthropic" => "claude-sonnet-4-5-20250929".into(),
        "openai" => "gpt-5.2".into(),
        "openai-codex" => "gpt-5-codex".into(),
        "venice" => "zai-org-glm-5".into(),
        "groq" => "llama-3.3-70b-versatile".into(),
        "mistral" => "mistral-large-latest".into(),
        "deepseek" => "deepseek-chat".into(),
        "xai" => "grok-4-1-fast-reasoning".into(),
        "perplexity" => "sonar-pro".into(),
        "fireworks" => "accounts/fireworks/models/llama-v3p3-70b-instruct".into(),
        "novita" => "minimax/minimax-m2.7".into(),
        "together-ai" => "meta-llama/Llama-3.3-70B-Instruct-Turbo".into(),
        "cohere" => "command-a-03-2025".into(),
        "moonshot" => "kimi-k2.5".into(),
        "glm" | "zai" => "glm-5".into(),
        "minimax" => "MiniMax-M2.7".into(),
        "qwen" => "qwen-plus".into(),
        "qwen-code" => "qwen3-coder-plus".into(),
        "ollama" => "llama3.2".into(),
        "llamacpp" => "ggml-org/gpt-oss-20b-GGUF".into(),
        "sglang" | "vllm" | "osaurus" | "opencode-go" => "default".into(),
        "gemini" => "gemini-2.5-pro".into(),
        "kimi-code" => "kimi-for-coding".into(),
        "bedrock" => "anthropic.claude-sonnet-4-5-20250929-v1:0".into(),
        "nvidia" => "meta/llama-3.3-70b-instruct".into(),
        "avian" => "deepseek/deepseek-v3.2".into(),
        _ => "anthropic/claude-sonnet-4.6".into(),
    }
}

fn curated_models_for_provider(provider_name: &str) -> Vec<(String, String)> {
    match canonical_provider_name(provider_name) {
        "openrouter" => vec![
            (
                "anthropic/claude-sonnet-4.6".to_string(),
                "Claude Sonnet 4.6 (balanced, recommended)".to_string(),
            ),
            (
                "openai/gpt-5.2".to_string(),
                "GPT-5.2 (latest flagship)".to_string(),
            ),
            (
                "openai/gpt-5-mini".to_string(),
                "GPT-5 mini (fast, cost-efficient)".to_string(),
            ),
            (
                "google/gemini-3-pro-preview".to_string(),
                "Gemini 3 Pro Preview (frontier reasoning)".to_string(),
            ),
            (
                "x-ai/grok-4.1-fast".to_string(),
                "Grok 4.1 Fast (reasoning + speed)".to_string(),
            ),
            (
                "deepseek/deepseek-v3.2".to_string(),
                "DeepSeek V3.2 (agentic + affordable)".to_string(),
            ),
            (
                "meta-llama/llama-4-maverick".to_string(),
                "Llama 4 Maverick (open model)".to_string(),
            ),
        ],
        "anthropic" => vec![
            (
                "claude-sonnet-4-5-20250929".to_string(),
                "Claude Sonnet 4.5 (balanced, recommended)".to_string(),
            ),
            (
                "claude-opus-4-6".to_string(),
                "Claude Opus 4.6 (best quality)".to_string(),
            ),
            (
                "claude-haiku-4-5-20251001".to_string(),
                "Claude Haiku 4.5 (fastest, cheapest)".to_string(),
            ),
        ],
        "openai" => vec![
            (
                "gpt-5.2".to_string(),
                "GPT-5.2 (latest coding/agentic flagship)".to_string(),
            ),
            (
                "gpt-5-mini".to_string(),
                "GPT-5 mini (faster, cheaper)".to_string(),
            ),
            (
                "gpt-5-nano".to_string(),
                "GPT-5 nano (lowest latency/cost)".to_string(),
            ),
            (
                "gpt-5.2-codex".to_string(),
                "GPT-5.2 Codex (agentic coding)".to_string(),
            ),
        ],
        "openai-codex" => vec![
            (
                "gpt-5-codex".to_string(),
                "GPT-5 Codex (recommended)".to_string(),
            ),
            (
                "gpt-5.2-codex".to_string(),
                "GPT-5.2 Codex (agentic coding)".to_string(),
            ),
            ("o4-mini".to_string(), "o4-mini (fallback)".to_string()),
        ],
        "venice" => vec![
            (
                "zai-org-glm-5".to_string(),
                "GLM-5 via Venice (agentic flagship)".to_string(),
            ),
            (
                "claude-sonnet-4-6".to_string(),
                "Claude Sonnet 4.6 via Venice (best quality)".to_string(),
            ),
            (
                "deepseek-v3.2".to_string(),
                "DeepSeek V3.2 via Venice (strong value)".to_string(),
            ),
            (
                "grok-41-fast".to_string(),
                "Grok 4.1 Fast via Venice (low latency)".to_string(),
            ),
        ],
        "groq" => vec![
            (
                "llama-3.3-70b-versatile".to_string(),
                "Llama 3.3 70B (fast, recommended)".to_string(),
            ),
            (
                "openai/gpt-oss-120b".to_string(),
                "GPT-OSS 120B (strong open-weight)".to_string(),
            ),
            (
                "openai/gpt-oss-20b".to_string(),
                "GPT-OSS 20B (cost-efficient open-weight)".to_string(),
            ),
        ],
        "mistral" => vec![
            (
                "mistral-large-latest".to_string(),
                "Mistral Large (latest flagship)".to_string(),
            ),
            (
                "mistral-medium-latest".to_string(),
                "Mistral Medium (balanced)".to_string(),
            ),
            (
                "codestral-latest".to_string(),
                "Codestral (code-focused)".to_string(),
            ),
            (
                "devstral-latest".to_string(),
                "Devstral (software engineering specialist)".to_string(),
            ),
        ],
        "deepseek" => vec![
            (
                "deepseek-chat".to_string(),
                "DeepSeek Chat (mapped to V3.2 non-thinking)".to_string(),
            ),
            (
                "deepseek-reasoner".to_string(),
                "DeepSeek Reasoner (mapped to V3.2 thinking)".to_string(),
            ),
        ],
        "xai" => vec![
            (
                "grok-4-1-fast-reasoning".to_string(),
                "Grok 4.1 Fast Reasoning (recommended)".to_string(),
            ),
            (
                "grok-4-1-fast-non-reasoning".to_string(),
                "Grok 4.1 Fast Non-Reasoning (low latency)".to_string(),
            ),
            (
                "grok-code-fast-1".to_string(),
                "Grok Code Fast 1 (coding specialist)".to_string(),
            ),
            ("grok-4".to_string(), "Grok 4 (max quality)".to_string()),
        ],
        "perplexity" => vec![
            (
                "sonar-pro".to_string(),
                "Sonar Pro (flagship web-grounded model)".to_string(),
            ),
            (
                "sonar-reasoning-pro".to_string(),
                "Sonar Reasoning Pro (complex multi-step reasoning)".to_string(),
            ),
            (
                "sonar-deep-research".to_string(),
                "Sonar Deep Research (long-form research)".to_string(),
            ),
            ("sonar".to_string(), "Sonar (search, fast)".to_string()),
        ],
        "fireworks" => vec![
            (
                "accounts/fireworks/models/llama-v3p3-70b-instruct".to_string(),
                "Llama 3.3 70B".to_string(),
            ),
            (
                "accounts/fireworks/models/mixtral-8x22b-instruct".to_string(),
                "Mixtral 8x22B".to_string(),
            ),
        ],
        "novita" => vec![
            (
                "minimax/minimax-m2.7".to_string(),
                "MiniMax M2.7 (latest flagship)".to_string(),
            ),
            (
                "minimax/minimax-m2.5".to_string(),
                "MiniMax M2.5".to_string(),
            ),
        ],
        "together-ai" => vec![
            (
                "meta-llama/Llama-3.3-70B-Instruct-Turbo".to_string(),
                "Llama 3.3 70B Instruct Turbo (recommended)".to_string(),
            ),
            (
                "moonshotai/Kimi-K2.5".to_string(),
                "Kimi K2.5 (reasoning + coding)".to_string(),
            ),
            (
                "deepseek-ai/DeepSeek-V3.1".to_string(),
                "DeepSeek V3.1 (strong value)".to_string(),
            ),
        ],
        "cohere" => vec![
            (
                "command-a-03-2025".to_string(),
                "Command A (flagship enterprise model)".to_string(),
            ),
            (
                "command-a-reasoning-08-2025".to_string(),
                "Command A Reasoning (agentic reasoning)".to_string(),
            ),
            (
                "command-r-08-2024".to_string(),
                "Command R (stable fast baseline)".to_string(),
            ),
        ],
        "kimi-code" => vec![
            (
                "kimi-for-coding".to_string(),
                "Kimi for Coding (official coding-agent model)".to_string(),
            ),
            (
                "kimi-k2.5".to_string(),
                "Kimi K2.5 (general coding endpoint model)".to_string(),
            ),
        ],
        "moonshot" => vec![
            (
                "kimi-k2.5".to_string(),
                "Kimi K2.5 (latest flagship, recommended)".to_string(),
            ),
            (
                "kimi-k2-thinking".to_string(),
                "Kimi K2 Thinking (deep reasoning + tool use)".to_string(),
            ),
            (
                "kimi-k2-0905-preview".to_string(),
                "Kimi K2 0905 Preview (strong coding)".to_string(),
            ),
        ],
        "glm" | "zai" => vec![
            ("glm-5".to_string(), "GLM-5 (high reasoning)".to_string()),
            (
                "glm-4.7".to_string(),
                "GLM-4.7 (strong general-purpose quality)".to_string(),
            ),
            (
                "glm-4.5-air".to_string(),
                "GLM-4.5 Air (lower latency)".to_string(),
            ),
        ],
        "minimax" => vec![
            (
                "MiniMax-M2.7".to_string(),
                "MiniMax M2.7 (latest flagship)".to_string(),
            ),
            (
                "MiniMax-M2.7-highspeed".to_string(),
                "MiniMax M2.7 High-Speed (fast)".to_string(),
            ),
            (
                "MiniMax-M2.5".to_string(),
                "MiniMax M2.5 (stable)".to_string(),
            ),
            (
                "MiniMax-M2.5-highspeed".to_string(),
                "MiniMax M2.5 High-Speed (fast)".to_string(),
            ),
            (
                "MiniMax-M2.1".to_string(),
                "MiniMax M2.1 (previous gen)".to_string(),
            ),
        ],
        "qwen" => vec![
            (
                "qwen-max".to_string(),
                "Qwen Max (highest quality)".to_string(),
            ),
            (
                "qwen-plus".to_string(),
                "Qwen Plus (balanced default)".to_string(),
            ),
            (
                "qwen-turbo".to_string(),
                "Qwen Turbo (fast and cost-efficient)".to_string(),
            ),
        ],
        "qwen-code" => vec![
            (
                "qwen3-coder-plus".to_string(),
                "Qwen3 Coder Plus (recommended for coding workflows)".to_string(),
            ),
            (
                "qwen3.5-plus".to_string(),
                "Qwen3.5 Plus (reasoning + coding)".to_string(),
            ),
            (
                "qwen3-max-2026-01-23".to_string(),
                "Qwen3 Max (high-capability coding model)".to_string(),
            ),
        ],
        "nvidia" => vec![
            (
                "meta/llama-3.3-70b-instruct".to_string(),
                "Llama 3.3 70B Instruct (balanced default)".to_string(),
            ),
            (
                "deepseek-ai/deepseek-v3.2".to_string(),
                "DeepSeek V3.2 (advanced reasoning + coding)".to_string(),
            ),
            (
                "nvidia/llama-3.3-nemotron-super-49b-v1.5".to_string(),
                "Llama 3.3 Nemotron Super 49B v1.5 (NVIDIA-tuned)".to_string(),
            ),
            (
                "nvidia/llama-3.1-nemotron-ultra-253b-v1".to_string(),
                "Llama 3.1 Nemotron Ultra 253B v1 (max quality)".to_string(),
            ),
        ],
        "astrai" => vec![
            (
                "anthropic/claude-sonnet-4.6".to_string(),
                "Claude Sonnet 4.6 (balanced default)".to_string(),
            ),
            (
                "openai/gpt-5.2".to_string(),
                "GPT-5.2 (latest flagship)".to_string(),
            ),
            (
                "deepseek/deepseek-v3.2".to_string(),
                "DeepSeek V3.2 (agentic + affordable)".to_string(),
            ),
            (
                "z-ai/glm-5".to_string(),
                "GLM-5 (high reasoning)".to_string(),
            ),
        ],
        "avian" => vec![
            (
                "deepseek/deepseek-v3.2".to_string(),
                "DeepSeek V3.2 (164K context, recommended)".to_string(),
            ),
            (
                "moonshotai/kimi-k2.5".to_string(),
                "Kimi K2.5 (131K context)".to_string(),
            ),
            ("z-ai/glm-5".to_string(), "GLM-5 (131K context)".to_string()),
            (
                "minimax/minimax-m2.5".to_string(),
                "MiniMax M2.5 (1M context)".to_string(),
            ),
        ],
        "ollama" => vec![
            (
                "llama3.2".to_string(),
                "Llama 3.2 (recommended local)".to_string(),
            ),
            ("mistral".to_string(), "Mistral 7B".to_string()),
            ("codellama".to_string(), "Code Llama".to_string()),
            ("phi3".to_string(), "Phi-3 (small, fast)".to_string()),
        ],
        "llamacpp" => vec![
            (
                "ggml-org/gpt-oss-20b-GGUF".to_string(),
                "GPT-OSS 20B GGUF (llama.cpp server example)".to_string(),
            ),
            (
                "bartowski/Llama-3.3-70B-Instruct-GGUF".to_string(),
                "Llama 3.3 70B GGUF (high quality)".to_string(),
            ),
            (
                "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF".to_string(),
                "Qwen2.5 Coder 7B GGUF (coding-focused)".to_string(),
            ),
        ],
        "sglang" | "vllm" => vec![
            (
                "meta-llama/Llama-3.1-8B-Instruct".to_string(),
                "Llama 3.1 8B Instruct (popular, fast)".to_string(),
            ),
            (
                "meta-llama/Llama-3.1-70B-Instruct".to_string(),
                "Llama 3.1 70B Instruct (high quality)".to_string(),
            ),
            (
                "Qwen/Qwen2.5-Coder-7B-Instruct".to_string(),
                "Qwen2.5 Coder 7B Instruct (coding-focused)".to_string(),
            ),
        ],
        "osaurus" => vec![
            (
                "qwen3-30b-a3b-8bit".to_string(),
                "Qwen3 30B A3B (local, balanced)".to_string(),
            ),
            (
                "gemma-3n-e4b-it-lm-4bit".to_string(),
                "Gemma 3N E4B (local, efficient)".to_string(),
            ),
            (
                "phi-4-mini-reasoning-mlx-4bit".to_string(),
                "Phi-4 Mini Reasoning (local, fast reasoning)".to_string(),
            ),
        ],
        "bedrock" => vec![
            (
                "anthropic.claude-sonnet-4-6".to_string(),
                "Claude Sonnet 4.6 (latest, recommended)".to_string(),
            ),
            (
                "anthropic.claude-opus-4-6-v1".to_string(),
                "Claude Opus 4.6 (strongest)".to_string(),
            ),
            (
                "anthropic.claude-haiku-4-5-20251001-v1:0".to_string(),
                "Claude Haiku 4.5 (fastest, cheapest)".to_string(),
            ),
            (
                "anthropic.claude-sonnet-4-5-20250929-v1:0".to_string(),
                "Claude Sonnet 4.5".to_string(),
            ),
        ],
        "gemini" => vec![
            (
                "gemini-3-pro-preview".to_string(),
                "Gemini 3 Pro Preview (latest frontier reasoning)".to_string(),
            ),
            (
                "gemini-2.5-pro".to_string(),
                "Gemini 2.5 Pro (stable reasoning)".to_string(),
            ),
            (
                "gemini-2.5-flash".to_string(),
                "Gemini 2.5 Flash (best price/performance)".to_string(),
            ),
            (
                "gemini-2.5-flash-lite".to_string(),
                "Gemini 2.5 Flash-Lite (lowest cost)".to_string(),
            ),
        ],
        _ => vec![("default".to_string(), "Default model".to_string())],
    }
}

fn supports_live_model_fetch(provider_name: &str) -> bool {
    if provider_name.trim().starts_with("custom:") {
        return true;
    }

    matches!(
        canonical_provider_name(provider_name),
        "openrouter"
            | "openai-codex"
            | "openai"
            | "anthropic"
            | "groq"
            | "mistral"
            | "deepseek"
            | "xai"
            | "together-ai"
            | "gemini"
            | "ollama"
            | "llamacpp"
            | "sglang"
            | "vllm"
            | "osaurus"
            | "astrai"
            | "avian"
            | "venice"
            | "fireworks"
            | "novita"
            | "cohere"
            | "moonshot"
            | "glm"
            | "zai"
            | "qwen"
            | "nvidia"
            | "opencode-go"
    )
}

fn models_endpoint_for_provider(provider_name: &str) -> Option<&'static str> {
    match provider_name {
        "qwen-intl" => Some("https://dashscope-intl.aliyuncs.com/compatible-mode/v1/models"),
        "dashscope-us" => Some("https://dashscope-us.aliyuncs.com/compatible-mode/v1/models"),
        "moonshot-cn" | "kimi-cn" => Some("https://api.moonshot.cn/v1/models"),
        "glm-cn" | "bigmodel" => Some("https://open.bigmodel.cn/api/paas/v4/models"),
        "zai-cn" | "z.ai-cn" => Some("https://open.bigmodel.cn/api/coding/paas/v4/models"),
        _ => match canonical_provider_name(provider_name) {
            "openai-codex" | "openai" => Some("https://api.openai.com/v1/models"),
            "venice" => Some("https://api.venice.ai/api/v1/models"),
            "groq" => Some("https://api.groq.com/openai/v1/models"),
            "mistral" => Some("https://api.mistral.ai/v1/models"),
            "deepseek" => Some("https://api.deepseek.com/v1/models"),
            "xai" => Some("https://api.x.ai/v1/models"),
            "together-ai" => Some("https://api.together.xyz/v1/models"),
            "fireworks" => Some("https://api.fireworks.ai/inference/v1/models"),
            "novita" => Some("https://api.novita.ai/openai/v1/models"),
            "cohere" => Some("https://api.cohere.com/compatibility/v1/models"),
            "moonshot" => Some("https://api.moonshot.ai/v1/models"),
            "glm" => Some("https://api.z.ai/api/paas/v4/models"),
            "zai" => Some("https://api.z.ai/api/coding/paas/v4/models"),
            "qwen" => Some("https://dashscope.aliyuncs.com/compatible-mode/v1/models"),
            "nvidia" => Some("https://integrate.api.nvidia.com/v1/models"),
            "astrai" => Some("https://as-trai.com/v1/models"),
            "avian" => Some("https://api.avian.io/v1/models"),
            "llamacpp" => Some("http://localhost:8080/v1/models"),
            "sglang" => Some("http://localhost:30000/v1/models"),
            "vllm" => Some("http://localhost:8000/v1/models"),
            "osaurus" => Some("http://localhost:1337/v1/models"),
            "opencode-go" => Some("https://opencode.ai/zen/go/v1/models"),
            _ => None,
        },
    }
}

fn build_model_fetch_client() -> Result<reqwest::Client> {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(8))
        .connect_timeout(Duration::from_secs(4))
        .build()
        .context("failed to build model-fetch HTTP client")
}

fn normalize_model_ids(ids: Vec<String>) -> Vec<String> {
    let mut unique = BTreeMap::new();
    for id in ids {
        let trimmed = id.trim();
        if !trimmed.is_empty() {
            unique
                .entry(trimmed.to_ascii_lowercase())
                .or_insert_with(|| trimmed.to_string());
        }
    }
    unique.into_values().collect()
}

fn parse_openai_compatible_model_ids(payload: &Value) -> Vec<String> {
    let mut models = Vec::new();

    if let Some(data) = payload.get("data").and_then(Value::as_array) {
        for model in data {
            if let Some(id) = model.get("id").and_then(Value::as_str) {
                models.push(id.to_string());
            }
        }
    } else if let Some(data) = payload.as_array() {
        for model in data {
            if let Some(id) = model.get("id").and_then(Value::as_str) {
                models.push(id.to_string());
            }
        }
    }

    normalize_model_ids(models)
}

fn parse_gemini_model_ids(payload: &Value) -> Vec<String> {
    let Some(models) = payload.get("models").and_then(Value::as_array) else {
        return Vec::new();
    };

    let mut ids = Vec::new();
    for model in models {
        let supports_generate_content = model
            .get("supportedGenerationMethods")
            .and_then(Value::as_array)
            .is_none_or(|methods| {
                methods
                    .iter()
                    .any(|method| method.as_str() == Some("generateContent"))
            });

        if !supports_generate_content {
            continue;
        }

        if let Some(name) = model.get("name").and_then(Value::as_str) {
            ids.push(name.trim_start_matches("models/").to_string());
        }
    }

    normalize_model_ids(ids)
}

fn parse_ollama_model_ids(payload: &Value) -> Vec<String> {
    let Some(models) = payload.get("models").and_then(Value::as_array) else {
        return Vec::new();
    };

    let mut ids = Vec::new();
    for model in models {
        if let Some(name) = model.get("name").and_then(Value::as_str) {
            ids.push(name.to_string());
        }
    }

    normalize_model_ids(ids)
}

async fn fetch_openai_compatible_models(
    endpoint: &str,
    api_key: Option<&str>,
    allow_unauthenticated: bool,
) -> Result<Vec<String>> {
    let client = build_model_fetch_client()?;
    let mut request = client.get(endpoint);

    if let Some(api_key) = api_key {
        request = request.bearer_auth(api_key);
    } else if !allow_unauthenticated {
        bail!("model fetch requires API key for endpoint {endpoint}");
    }

    let payload: Value = request
        .send()
        .await
        .and_then(reqwest::Response::error_for_status)
        .with_context(|| format!("model fetch failed: GET {endpoint}"))?
        .json()
        .await
        .context("failed to parse model list response")?;

    Ok(parse_openai_compatible_model_ids(&payload))
}

async fn fetch_openrouter_models(api_key: Option<&str>) -> Result<Vec<String>> {
    let client = build_model_fetch_client()?;
    let mut request = client.get("https://openrouter.ai/api/v1/models");
    if let Some(api_key) = api_key {
        request = request.bearer_auth(api_key);
    }

    let payload: Value = request
        .send()
        .await
        .and_then(reqwest::Response::error_for_status)
        .context("model fetch failed: GET https://openrouter.ai/api/v1/models")?
        .json()
        .await
        .context("failed to parse OpenRouter model list response")?;

    Ok(parse_openai_compatible_model_ids(&payload))
}

async fn fetch_anthropic_models(api_key: Option<&str>) -> Result<Vec<String>> {
    let Some(api_key) = api_key else {
        bail!("Anthropic model fetch requires API key or OAuth token");
    };

    let client = build_model_fetch_client()?;
    let mut request = client
        .get("https://api.anthropic.com/v1/models")
        .header("anthropic-version", "2023-06-01");

    if api_key.starts_with("sk-ant-oat01-") {
        request = request
            .header("Authorization", format!("Bearer {api_key}"))
            .header("anthropic-beta", "oauth-2025-04-20");
    } else {
        request = request.header("x-api-key", api_key);
    }

    let response = request
        .send()
        .await
        .context("model fetch failed: GET https://api.anthropic.com/v1/models")?;

    let status = response.status();
    if !status.is_success() {
        let body = response.text().await.unwrap_or_default();
        bail!("Anthropic model list request failed (HTTP {status}): {body}");
    }

    let payload: Value = response
        .json()
        .await
        .context("failed to parse Anthropic model list response")?;

    Ok(parse_openai_compatible_model_ids(&payload))
}

async fn fetch_gemini_models(api_key: Option<&str>) -> Result<Vec<String>> {
    let Some(api_key) = api_key else {
        bail!("Gemini model fetch requires API key");
    };

    let client = build_model_fetch_client()?;
    let payload: Value = client
        .get("https://generativelanguage.googleapis.com/v1beta/models")
        .query(&[("key", api_key), ("pageSize", "200")])
        .send()
        .await
        .and_then(reqwest::Response::error_for_status)
        .context("model fetch failed: GET Gemini models")?
        .json()
        .await
        .context("failed to parse Gemini model list response")?;

    Ok(parse_gemini_model_ids(&payload))
}

async fn fetch_ollama_models() -> Result<Vec<String>> {
    let client = build_model_fetch_client()?;
    let payload: Value = client
        .get("http://localhost:11434/api/tags")
        .send()
        .await
        .and_then(reqwest::Response::error_for_status)
        .context("model fetch failed: GET http://localhost:11434/api/tags")?
        .json()
        .await
        .context("failed to parse Ollama model list response")?;

    Ok(parse_ollama_model_ids(&payload))
}

fn normalize_ollama_endpoint_url(raw_url: &str) -> String {
    let trimmed = raw_url.trim().trim_end_matches('/');
    if trimmed.is_empty() {
        return String::new();
    }
    trimmed
        .strip_suffix("/api")
        .unwrap_or(trimmed)
        .trim_end_matches('/')
        .to_string()
}

fn ollama_endpoint_is_local(endpoint_url: &str) -> bool {
    reqwest::Url::parse(endpoint_url)
        .ok()
        .and_then(|url| url.host_str().map(|host| host.to_ascii_lowercase()))
        .is_some_and(|host| matches!(host.as_str(), "localhost" | "127.0.0.1" | "::1" | "0.0.0.0"))
}

fn ollama_uses_remote_endpoint(provider_api_url: Option<&str>) -> bool {
    let Some(endpoint) = provider_api_url else {
        return false;
    };

    let normalized = normalize_ollama_endpoint_url(endpoint);
    if normalized.is_empty() {
        return false;
    }

    !ollama_endpoint_is_local(&normalized)
}

fn resolve_live_models_endpoint(
    provider_name: &str,
    provider_api_url: Option<&str>,
) -> Option<String> {
    if let Some(raw_base) = provider_name.strip_prefix("custom:") {
        let normalized = raw_base.trim().trim_end_matches('/');
        if normalized.is_empty() {
            return None;
        }
        if normalized.ends_with("/models") {
            return Some(normalized.to_string());
        }
        return Some(format!("{normalized}/models"));
    }

    if matches!(
        canonical_provider_name(provider_name),
        "llamacpp" | "sglang" | "vllm" | "osaurus"
    ) {
        if let Some(url) = provider_api_url
            .map(str::trim)
            .filter(|url| !url.is_empty())
        {
            let normalized = url.trim_end_matches('/');
            if normalized.ends_with("/models") {
                return Some(normalized.to_string());
            }
            return Some(format!("{normalized}/models"));
        }
    }

    if canonical_provider_name(provider_name) == "openai-codex" {
        if let Some(url) = provider_api_url
            .map(str::trim)
            .filter(|url| !url.is_empty())
        {
            let normalized = url.trim_end_matches('/');
            if normalized.ends_with("/models") {
                return Some(normalized.to_string());
            }
            return Some(format!("{normalized}/models"));
        }
    }

    models_endpoint_for_provider(provider_name).map(str::to_string)
}

async fn fetch_live_models_for_provider(
    provider_name: &str,
    api_key: &str,
    provider_api_url: Option<&str>,
) -> Result<Vec<String>> {
    let requested_provider_name = provider_name;
    let provider_name = canonical_provider_name(provider_name);
    let ollama_remote = provider_name == "ollama" && ollama_uses_remote_endpoint(provider_api_url);
    let api_key = if api_key.trim().is_empty() {
        if provider_name == "ollama" && !ollama_remote {
            None
        } else {
            std::env::var(provider_env_var(provider_name))
                .ok()
                .or_else(|| {
                    // Anthropic also accepts OAuth setup-tokens via ANTHROPIC_OAUTH_TOKEN
                    if provider_name == "anthropic" {
                        std::env::var("ANTHROPIC_OAUTH_TOKEN").ok()
                    } else if provider_name == "minimax" {
                        std::env::var("MINIMAX_OAUTH_TOKEN").ok()
                    } else {
                        None
                    }
                })
                .map(|value| value.trim().to_string())
                .filter(|value| !value.is_empty())
        }
    } else {
        Some(api_key.trim().to_string())
    };

    let models = match provider_name {
        "openrouter" => fetch_openrouter_models(api_key.as_deref()).await?,
        "anthropic" => fetch_anthropic_models(api_key.as_deref()).await?,
        "gemini" => fetch_gemini_models(api_key.as_deref()).await?,
        "ollama" => {
            if ollama_remote {
                // Remote Ollama endpoints can serve cloud-routed models.
                // Keep this curated list aligned with current Ollama cloud catalog.
                vec![
                    "glm-5:cloud".to_string(),
                    "glm-4.7:cloud".to_string(),
                    "gpt-oss:20b:cloud".to_string(),
                    "gpt-oss:120b:cloud".to_string(),
                    "gemini-3-flash-preview:cloud".to_string(),
                    "qwen3-coder-next:cloud".to_string(),
                    "qwen3-coder:480b:cloud".to_string(),
                    "kimi-k2.5:cloud".to_string(),
                    "minimax-m2.7:cloud".to_string(),
                    "deepseek-v3.1:671b:cloud".to_string(),
                ]
            } else {
                // Local endpoints should not surface cloud-only suffixes.
                fetch_ollama_models()
                    .await?
                    .into_iter()
                    .filter(|model_id| !model_id.ends_with(":cloud"))
                    .collect()
            }
        }
        _ => {
            if let Some(endpoint) =
                resolve_live_models_endpoint(requested_provider_name, provider_api_url)
            {
                let allow_unauthenticated =
                    allows_unauthenticated_model_fetch(requested_provider_name);
                fetch_openai_compatible_models(&endpoint, api_key.as_deref(), allow_unauthenticated)
                    .await?
            } else {
                Vec::new()
            }
        }
    };

    Ok(models)
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ModelCacheEntry {
    provider: String,
    fetched_at_unix: u64,
    models: Vec<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
struct ModelCacheState {
    entries: Vec<ModelCacheEntry>,
}

#[derive(Debug, Clone)]
struct CachedModels {
    models: Vec<String>,
    age_secs: u64,
}

fn model_cache_path(workspace_dir: &Path) -> PathBuf {
    workspace_dir.join("state").join(MODEL_CACHE_FILE)
}

fn now_unix_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |duration| duration.as_secs())
}

async fn load_model_cache_state(workspace_dir: &Path) -> Result<ModelCacheState> {
    let path = model_cache_path(workspace_dir);
    if !path.exists() {
        return Ok(ModelCacheState::default());
    }

    let raw = fs::read_to_string(&path)
        .await
        .with_context(|| format!("failed to read model cache at {}", path.display()))?;

    match serde_json::from_str::<ModelCacheState>(&raw) {
        Ok(state) => Ok(state),
        Err(_) => Ok(ModelCacheState::default()),
    }
}

async fn save_model_cache_state(workspace_dir: &Path, state: &ModelCacheState) -> Result<()> {
    let path = model_cache_path(workspace_dir);
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).await.with_context(|| {
            format!(
                "failed to create model cache directory {}",
                parent.display()
            )
        })?;
    }

    let json = serde_json::to_vec_pretty(state).context("failed to serialize model cache")?;
    fs::write(&path, json)
        .await
        .with_context(|| format!("failed to write model cache at {}", path.display()))?;

    Ok(())
}

async fn cache_live_models_for_provider(
    workspace_dir: &Path,
    provider_name: &str,
    models: &[String],
) -> Result<()> {
    let normalized_models = normalize_model_ids(models.to_vec());
    if normalized_models.is_empty() {
        return Ok(());
    }

    let mut state = load_model_cache_state(workspace_dir).await?;
    let now = now_unix_secs();

    if let Some(entry) = state
        .entries
        .iter_mut()
        .find(|entry| entry.provider == provider_name)
    {
        entry.fetched_at_unix = now;
        entry.models = normalized_models;
    } else {
        state.entries.push(ModelCacheEntry {
            provider: provider_name.to_string(),
            fetched_at_unix: now,
            models: normalized_models,
        });
    }

    save_model_cache_state(workspace_dir, &state).await
}

async fn load_cached_models_for_provider_internal(
    workspace_dir: &Path,
    provider_name: &str,
    ttl_secs: Option<u64>,
) -> Result<Option<CachedModels>> {
    let state = load_model_cache_state(workspace_dir).await?;
    let now = now_unix_secs();

    let Some(entry) = state
        .entries
        .into_iter()
        .find(|entry| entry.provider == provider_name)
    else {
        return Ok(None);
    };

    if entry.models.is_empty() {
        return Ok(None);
    }

    let age_secs = now.saturating_sub(entry.fetched_at_unix);
    if ttl_secs.is_some_and(|ttl| age_secs > ttl) {
        return Ok(None);
    }

    Ok(Some(CachedModels {
        models: entry.models,
        age_secs,
    }))
}

async fn load_cached_models_for_provider(
    workspace_dir: &Path,
    provider_name: &str,
    ttl_secs: u64,
) -> Result<Option<CachedModels>> {
    load_cached_models_for_provider_internal(workspace_dir, provider_name, Some(ttl_secs)).await
}

async fn load_any_cached_models_for_provider(
    workspace_dir: &Path,
    provider_name: &str,
) -> Result<Option<CachedModels>> {
    load_cached_models_for_provider_internal(workspace_dir, provider_name, None).await
}

fn humanize_age(age_secs: u64) -> String {
    if age_secs < 60 {
        format!("{age_secs}s")
    } else if age_secs < 60 * 60 {
        format!("{}m", age_secs / 60)
    } else {
        format!("{}h", age_secs / (60 * 60))
    }
}

fn build_model_options(model_ids: Vec<String>, source: &str) -> Vec<(String, String)> {
    model_ids
        .into_iter()
        .map(|model_id| {
            let label = format!("{model_id} ({source})");
            (model_id, label)
        })
        .collect()
}

fn print_model_preview(models: &[String]) {
    for model in models.iter().take(MODEL_PREVIEW_LIMIT) {
        println!("  {} {model}", style("-"));
    }

    if models.len() > MODEL_PREVIEW_LIMIT {
        println!(
            "  {} ... and {} more",
            style("-"),
            models.len() - MODEL_PREVIEW_LIMIT
        );
    }
}

pub async fn run_models_refresh(
    config: &Config,
    provider_override: Option<&str>,
    force: bool,
) -> Result<()> {
    let provider_name = provider_override
        .or(config.default_provider.as_deref())
        .unwrap_or("openrouter")
        .trim()
        .to_string();

    if provider_name.is_empty() {
        anyhow::bail!("Provider name cannot be empty");
    }

    if !supports_live_model_fetch(&provider_name) {
        anyhow::bail!("Provider '{provider_name}' does not support live model discovery yet");
    }

    if !force {
        if let Some(cached) = load_cached_models_for_provider(
            &config.workspace_dir,
            &provider_name,
            MODEL_CACHE_TTL_SECS,
        )
        .await?
        {
            println!(
                "Using cached model list for '{}' (updated {} ago):",
                provider_name,
                humanize_age(cached.age_secs)
            );
            print_model_preview(&cached.models);
            println!();
            println!(
                "Tip: run `construct models refresh --force --provider {}` to fetch latest now.",
                provider_name
            );
            return Ok(());
        }
    }

    let api_key = config.api_key.clone().unwrap_or_default();

    match fetch_live_models_for_provider(&provider_name, &api_key, config.api_url.as_deref()).await
    {
        Ok(models) if !models.is_empty() => {
            cache_live_models_for_provider(&config.workspace_dir, &provider_name, &models).await?;
            println!(
                "Refreshed '{}' model cache with {} models.",
                provider_name,
                models.len()
            );
            print_model_preview(&models);
            Ok(())
        }
        Ok(_) => {
            if let Some(stale_cache) =
                load_any_cached_models_for_provider(&config.workspace_dir, &provider_name).await?
            {
                println!(
                    "Provider returned no models; using stale cache (updated {} ago):",
                    humanize_age(stale_cache.age_secs)
                );
                print_model_preview(&stale_cache.models);
                return Ok(());
            }

            anyhow::bail!("Provider '{}' returned an empty model list", provider_name)
        }
        Err(error) => {
            if let Some(stale_cache) =
                load_any_cached_models_for_provider(&config.workspace_dir, &provider_name).await?
            {
                println!(
                    "Live refresh failed ({}). Falling back to stale cache (updated {} ago):",
                    error,
                    humanize_age(stale_cache.age_secs)
                );
                print_model_preview(&stale_cache.models);
                return Ok(());
            }

            Err(error)
                .with_context(|| format!("failed to refresh models for provider '{provider_name}'"))
        }
    }
}

pub async fn run_models_list(config: &Config, provider_override: Option<&str>) -> Result<()> {
    let provider_name = provider_override
        .or(config.default_provider.as_deref())
        .unwrap_or("openrouter");

    let cached = load_any_cached_models_for_provider(&config.workspace_dir, provider_name).await?;

    let Some(cached) = cached else {
        println!();
        println!(
            "  No cached models for '{provider_name}'. Run: construct models refresh --provider {provider_name}"
        );
        println!();
        return Ok(());
    };

    println!();
    println!(
        "  {} models for '{}' (cached {} ago):",
        cached.models.len(),
        provider_name,
        humanize_age(cached.age_secs)
    );
    println!();
    for model in &cached.models {
        let marker = if config.default_model.as_deref() == Some(model.as_str()) {
            "* "
        } else {
            "  "
        };
        println!("  {marker}{model}");
    }
    println!();
    Ok(())
}

pub async fn run_models_set(config: &Config, model: &str) -> Result<()> {
    let model = model.trim();
    if model.is_empty() {
        anyhow::bail!("Model name cannot be empty");
    }

    let mut updated = config.clone();
    updated.default_model = Some(model.to_string());
    updated.save().await?;

    println!();
    println!("  Default model set to '{}'.", style(model).green().bold());
    println!();
    Ok(())
}

pub async fn run_models_status(config: &Config) -> Result<()> {
    let provider = config.default_provider.as_deref().unwrap_or("openrouter");
    let model = config.default_model.as_deref().unwrap_or("(not set)");

    println!();
    println!("  Provider:  {}", style(provider).cyan());
    println!("  Model:     {}", style(model).cyan());
    println!(
        "  Temp:      {}",
        style(format!("{:.1}", config.default_temperature)).cyan()
    );

    match load_any_cached_models_for_provider(&config.workspace_dir, provider).await? {
        Some(cached) => {
            println!(
                "  Cache:     {} models (updated {} ago)",
                cached.models.len(),
                humanize_age(cached.age_secs)
            );
            let fresh = cached.age_secs < MODEL_CACHE_TTL_SECS;
            if fresh {
                println!("  Freshness: {}", style("fresh").green());
            } else {
                println!("  Freshness: {}", style("stale").yellow());
            }
        }
        None => {
            println!("  Cache:     {}", style("none").yellow());
        }
    }

    println!();
    Ok(())
}

pub async fn cached_model_catalog_stats(
    config: &Config,
    provider_name: &str,
) -> Result<Option<(usize, u64)>> {
    let Some(cached) =
        load_any_cached_models_for_provider(&config.workspace_dir, provider_name).await?
    else {
        return Ok(None);
    };
    Ok(Some((cached.models.len(), cached.age_secs)))
}

pub async fn run_models_refresh_all(config: &Config, force: bool) -> Result<()> {
    let mut targets: Vec<String> = crate::providers::list_providers()
        .into_iter()
        .map(|provider| provider.name.to_string())
        .filter(|name| supports_live_model_fetch(name))
        .collect();

    targets.sort();
    targets.dedup();

    if targets.is_empty() {
        anyhow::bail!("No providers support live model discovery");
    }

    println!(
        "Refreshing model catalogs for {} providers (force: {})",
        targets.len(),
        if force { "yes" } else { "no" }
    );
    println!();

    let mut ok_count = 0usize;
    let mut fail_count = 0usize;

    for provider_name in &targets {
        println!("== {} ==", provider_name);
        match run_models_refresh(config, Some(provider_name), force).await {
            Ok(()) => {
                ok_count += 1;
            }
            Err(error) => {
                fail_count += 1;
                println!("  failed: {error}");
            }
        }
        println!();
    }

    println!("Summary: {} succeeded, {} failed", ok_count, fail_count);

    if ok_count == 0 {
        anyhow::bail!("Model refresh failed for all providers")
    }
    Ok(())
}

// ── Step helpers ─────────────────────────────────────────────────

fn print_step(current: u8, total: u8, title: &str) {
    println!();
    println!(
        "  {} {}",
        style(format!("[{current}/{total}]")).cyan().bold(),
        style(title).white().bold()
    );
    println!("  {}", style("─".repeat(50)).dim());
}

fn print_bullet(text: &str) {
    println!("  {} {}", style("›").cyan(), text);
}

fn resolve_interactive_onboarding_mode(
    config_path: &Path,
    force: bool,
) -> Result<InteractiveOnboardingMode> {
    if !config_path.exists() {
        return Ok(InteractiveOnboardingMode::FullOnboarding);
    }

    if force {
        println!(
            "  {}",
            style(t!(
                "existing-config-detected-force",
                path = config_path.display().to_string()
            ))
            .yellow()
        );
        return Ok(InteractiveOnboardingMode::FullOnboarding);
    }

    if !std::io::stdin().is_terminal() || !std::io::stdout().is_terminal() {
        bail!(
            "Refusing to overwrite existing config at {} in non-interactive mode. Re-run with --force if overwrite is intentional.",
            config_path.display()
        );
    }

    let opt_full = t!("setup-mode-full");
    let opt_update = t!("setup-mode-update-provider");
    let opt_cancel = t!("setup-mode-cancel");
    let options = [opt_full.as_str(), opt_update.as_str(), opt_cancel.as_str()];

    let mode = Select::new()
        .with_prompt(format!(
            "  {}",
            t!(
                "existing-config-found",
                path = config_path.display().to_string()
            )
        ))
        .items(options)
        .default(1)
        .interact()?;

    match mode {
        0 => Ok(InteractiveOnboardingMode::FullOnboarding),
        1 => Ok(InteractiveOnboardingMode::UpdateProviderOnly),
        _ => bail!("Onboarding canceled: existing configuration was left unchanged."),
    }
}

fn ensure_onboard_overwrite_allowed(config_path: &Path, force: bool) -> Result<()> {
    if !config_path.exists() {
        return Ok(());
    }

    if force {
        println!(
            "  {}",
            style(t!(
                "existing-config-detected-force",
                path = config_path.display().to_string()
            ))
            .yellow()
        );
        return Ok(());
    }

    #[cfg(test)]
    {
        bail!(
            "Refusing to overwrite existing config at {} in test mode. Re-run with --force if overwrite is intentional.",
            config_path.display()
        );
    }

    #[cfg(not(test))]
    {
        if !std::io::stdin().is_terminal() || !std::io::stdout().is_terminal() {
            bail!(
                "Refusing to overwrite existing config at {} in non-interactive mode. Re-run with --force if overwrite is intentional.",
                config_path.display()
            );
        }

        let confirmed = Confirm::new()
            .with_prompt(format!(
                "  {}",
                t!(
                    "existing-config-overwrite-prompt",
                    path = config_path.display().to_string()
                )
            ))
            .default(false)
            .interact()?;

        if !confirmed {
            bail!("Onboarding canceled: existing configuration was left unchanged.");
        }

        Ok(())
    }
}

async fn persist_workspace_selection(config_path: &Path) -> Result<()> {
    let config_dir = config_path
        .parent()
        .context("Config path must have a parent directory")?;
    crate::config::schema::persist_active_workspace_config_dir(config_dir)
        .await
        .with_context(|| {
            format!(
                "Failed to persist active workspace selection for {}",
                config_dir.display()
            )
        })
}

// ── Step 1: Workspace ────────────────────────────────────────────

async fn setup_workspace() -> Result<(PathBuf, PathBuf)> {
    let (default_config_dir, default_workspace_dir) =
        crate::config::schema::resolve_runtime_dirs_for_onboarding().await?;

    print_bullet(&t!(
        "workspace-default-location",
        path = style(default_workspace_dir.display()).green().to_string()
    ));

    let use_default = Confirm::new()
        .with_prompt(format!("  {}", t!("workspace-use-default")))
        .default(true)
        .interact()?;

    let (config_dir, workspace_dir) = if use_default {
        (default_config_dir, default_workspace_dir)
    } else {
        let custom: String = Input::new()
            .with_prompt(format!("  {}", t!("workspace-enter-path")))
            .interact_text()?;
        let expanded = shellexpand::tilde(&custom).to_string();
        crate::config::schema::resolve_config_dir_for_workspace(&PathBuf::from(expanded))
    };

    let config_path = config_dir.join("config.toml");

    fs::create_dir_all(&workspace_dir)
        .await
        .context("Failed to create workspace directory")?;

    println!(
        "  {}",
        style(t!(
            "workspace-confirmed",
            path = workspace_dir.display().to_string()
        ))
        .green()
    );

    Ok((workspace_dir, config_path))
}

// ── Step 2: Provider & API Key ───────────────────────────────────

#[allow(clippy::too_many_lines)]
async fn setup_provider(workspace_dir: &Path) -> Result<(String, String, String, Option<String>)> {
    // ── Tier selection ──
    // Tier labels are translated; the parenthetical provider lists stay in
    // English because they're proper nouns and the registry IDs the user
    // sees throughout the rest of the wizard.
    let tier_recommended = format!(
        "⭐ {}  (OpenRouter, Venice, Anthropic, OpenAI, Gemini)",
        t!("provider-tier-recommended")
    );
    let tier_fast = format!(
        "⚡ {}  (Groq, Fireworks, Together AI, NVIDIA NIM)",
        t!("provider-tier-fast")
    );
    let tier_gateway = format!(
        "🌐 {}  (Vercel AI, Cloudflare AI, Amazon Bedrock)",
        t!("provider-tier-gateway")
    );
    let tier_specialized = format!(
        "🔬 {}  (Moonshot/Kimi, GLM/Zhipu, MiniMax, Qwen/DashScope, Qianfan, Z.AI, Synthetic, OpenCode Zen, Cohere)",
        t!("provider-tier-specialized")
    );
    let tier_local = format!(
        "🏠 {}  (Ollama, llama.cpp server, vLLM — no API key needed)",
        t!("provider-tier-local")
    );
    let tier_custom = format!("🔧 {}", t!("provider-tier-custom"));
    let tiers = vec![
        tier_recommended.as_str(),
        tier_fast.as_str(),
        tier_gateway.as_str(),
        tier_specialized.as_str(),
        tier_local.as_str(),
        tier_custom.as_str(),
    ];

    let tier_idx = Select::new()
        .with_prompt(format!("  {}", t!("provider-select-tier")))
        .items(&tiers)
        .default(0)
        .interact()?;

    let providers: Vec<(&str, &str)> = match tier_idx {
        0 => vec![
            (
                "openrouter",
                "OpenRouter — 200+ models, 1 API key (recommended)",
            ),
            ("venice", "Venice AI — privacy-first (Llama, Opus)"),
            ("anthropic", "Anthropic — Claude Sonnet & Opus (direct)"),
            ("openai", "OpenAI — GPT-4o, o1, GPT-5 (direct)"),
            (
                "openai-codex",
                "OpenAI Codex (ChatGPT subscription OAuth, no API key)",
            ),
            ("deepseek", "DeepSeek — V3 & R1 (affordable)"),
            ("mistral", "Mistral — Large & Codestral"),
            ("xai", "xAI — Grok 3 & 4"),
            ("perplexity", "Perplexity — search-augmented AI"),
            (
                "gemini",
                "Google Gemini — Gemini 2.0 Flash & Pro (supports CLI auth)",
            ),
        ],
        1 => vec![
            ("groq", "Groq — ultra-fast LPU inference"),
            ("fireworks", "Fireworks AI — fast open-source inference"),
            ("novita", "Novita AI — affordable open-source inference"),
            ("together-ai", "Together AI — open-source model hosting"),
            ("nvidia", "NVIDIA NIM — DeepSeek, Llama, & more"),
        ],
        2 => vec![
            ("vercel", "Vercel AI Gateway"),
            ("cloudflare", "Cloudflare AI Gateway"),
            (
                "astrai",
                "Astrai — compliant AI routing (PII stripping, cost optimization)",
            ),
            (
                "avian",
                "Avian — OpenAI-compatible inference (DeepSeek, Kimi, GLM, MiniMax)",
            ),
            ("bedrock", "Amazon Bedrock — AWS managed models"),
        ],
        3 => vec![
            (
                "kimi-code",
                "Kimi Code — coding-optimized Kimi API (KimiCLI)",
            ),
            (
                "qwen-code",
                "Qwen Code — OAuth tokens reused from ~/.qwen/oauth_creds.json",
            ),
            ("moonshot", "Moonshot — Kimi API (China endpoint)"),
            (
                "moonshot-intl",
                "Moonshot — Kimi API (international endpoint)",
            ),
            ("glm", "GLM — ChatGLM / Zhipu (international endpoint)"),
            ("glm-cn", "GLM — ChatGLM / Zhipu (China endpoint)"),
            (
                "minimax",
                "MiniMax — international endpoint (api.minimax.io)",
            ),
            ("minimax-cn", "MiniMax — China endpoint (api.minimaxi.com)"),
            ("qwen", "Qwen — DashScope China endpoint"),
            ("qwen-intl", "Qwen — DashScope international endpoint"),
            ("qwen-us", "Qwen — DashScope US endpoint"),
            ("qianfan", "Qianfan — Baidu AI models (China endpoint)"),
            ("zai", "Z.AI — global coding endpoint"),
            ("zai-cn", "Z.AI — China coding endpoint (open.bigmodel.cn)"),
            ("synthetic", "Synthetic — Synthetic AI models"),
            ("opencode", "OpenCode Zen — code-focused AI"),
            ("opencode-go", "OpenCode Go — Subsidized code-focused AI"),
            ("cohere", "Cohere — Command R+ & embeddings"),
        ],
        4 => local_provider_choices(),
        _ => vec![], // Custom — handled below
    };

    // ── Custom / BYOP flow ──
    if providers.is_empty() {
        println!();
        println!(
            "  {} {}",
            style(t!("custom-provider-title")).white().bold(),
            style(t!("custom-provider-subtitle")).dim()
        );
        print_bullet(&t!("custom-provider-info-1"));
        print_bullet(&t!("custom-provider-info-2"));
        println!();

        let base_url: String = Input::new()
            .with_prompt(format!("  {}", t!("provider-api-base-prompt")))
            .interact_text()?;

        let base_url = base_url.trim().trim_end_matches('/').to_string();
        if base_url.is_empty() {
            anyhow::bail!("Custom provider requires a base URL.");
        }

        let api_key: String = Input::new()
            .with_prompt(format!("  {}", t!("provider-api-key-optional")))
            .allow_empty(true)
            .interact_text()?;

        let model: String = Input::new()
            .with_prompt(format!("  {}", t!("provider-model-name")))
            .default("default".to_string())
            .interact_text()?;

        let provider_name = format!("custom:{base_url}");

        println!(
            "  {} {}",
            style("✓").green().bold(),
            t!(
                "custom-provider-confirmed",
                provider = style(&provider_name).green().to_string(),
                model = style(&model).green().to_string()
            )
        );

        return Ok((provider_name, api_key, model, None));
    }

    let provider_labels: Vec<&str> = providers.iter().map(|(_, label)| *label).collect();

    let provider_idx = Select::new()
        .with_prompt(format!("  {}", t!("provider-select")))
        .items(&provider_labels)
        .default(0)
        .interact()?;

    let provider_name = providers[provider_idx].0;

    // ── API key / endpoint ──
    let mut provider_api_url: Option<String> = None;
    let api_key = if provider_name == "ollama" {
        let use_remote_ollama = Confirm::new()
            .with_prompt(format!("  {}", t!("ollama-use-remote")))
            .default(false)
            .interact()?;

        if use_remote_ollama {
            let raw_url: String = Input::new()
                .with_prompt(format!("  {}", t!("ollama-remote-url-prompt")))
                .default("https://ollama.com".to_string())
                .interact_text()?;

            let normalized_url = normalize_ollama_endpoint_url(&raw_url);
            if normalized_url.is_empty() {
                anyhow::bail!("Remote Ollama endpoint URL cannot be empty.");
            }
            let parsed = reqwest::Url::parse(&normalized_url)
                .context("Remote Ollama endpoint URL must be a valid URL")?;
            if !matches!(parsed.scheme(), "http" | "https") {
                anyhow::bail!("Remote Ollama endpoint URL must use http:// or https://");
            }

            provider_api_url = Some(normalized_url.clone());

            print_bullet(&t!(
                "ollama-remote-configured",
                url = style(&normalized_url).cyan().to_string()
            ));
            if raw_url.trim().trim_end_matches('/') != normalized_url {
                print_bullet(&t!("ollama-normalized-base"));
            }
            print_bullet(&t!(
                "ollama-cloud-suffix-hint",
                suffix = style(":cloud").yellow().to_string()
            ));

            let key: String = Input::new()
                .with_prompt(format!("  {}", t!("ollama-remote-key-prompt")))
                .allow_empty(true)
                .interact_text()?;

            if key.trim().is_empty() {
                print_bullet(&t!(
                    "ollama-no-key-hint",
                    env_var = style("OLLAMA_API_KEY").yellow().to_string()
                ));
            }

            key
        } else {
            print_bullet(&t!("ollama-using-local"));
            String::new()
        }
    } else if matches!(provider_name, "llamacpp" | "llama.cpp") {
        let raw_url: String = Input::new()
            .with_prompt(format!("  {}", t!("llamacpp-url-prompt")))
            .default("http://localhost:8080/v1".to_string())
            .interact_text()?;

        let normalized_url = raw_url.trim().trim_end_matches('/').to_string();
        if normalized_url.is_empty() {
            anyhow::bail!("llama.cpp endpoint URL cannot be empty.");
        }
        provider_api_url = Some(normalized_url.clone());

        print_bullet(&t!(
            "llamacpp-using",
            url = style(&normalized_url).cyan().to_string()
        ));
        print_bullet(&t!("llamacpp-key-info"));

        let key: String = Input::new()
            .with_prompt(format!("  {}", t!("llamacpp-key-prompt")))
            .allow_empty(true)
            .interact_text()?;

        if key.trim().is_empty() {
            print_bullet(&t!(
                "local-server-no-key-hint",
                env_var = style("LLAMACPP_API_KEY").yellow().to_string()
            ));
        }

        key
    } else if provider_name == "sglang" {
        let raw_url: String = Input::new()
            .with_prompt(format!("  {}", t!("sglang-url-prompt")))
            .default("http://localhost:30000/v1".to_string())
            .interact_text()?;

        let normalized_url = raw_url.trim().trim_end_matches('/').to_string();
        if normalized_url.is_empty() {
            anyhow::bail!("SGLang endpoint URL cannot be empty.");
        }
        provider_api_url = Some(normalized_url.clone());

        print_bullet(&t!(
            "sglang-using",
            url = style(&normalized_url).cyan().to_string()
        ));
        print_bullet(&t!("sglang-key-info"));

        let key: String = Input::new()
            .with_prompt(format!("  {}", t!("sglang-key-prompt")))
            .allow_empty(true)
            .interact_text()?;

        if key.trim().is_empty() {
            print_bullet(&t!(
                "local-server-no-key-hint",
                env_var = style("SGLANG_API_KEY").yellow().to_string()
            ));
        }

        key
    } else if provider_name == "vllm" {
        let raw_url: String = Input::new()
            .with_prompt(format!("  {}", t!("vllm-url-prompt")))
            .default("http://localhost:8000/v1".to_string())
            .interact_text()?;

        let normalized_url = raw_url.trim().trim_end_matches('/').to_string();
        if normalized_url.is_empty() {
            anyhow::bail!("vLLM endpoint URL cannot be empty.");
        }
        provider_api_url = Some(normalized_url.clone());

        print_bullet(&t!(
            "vllm-using",
            url = style(&normalized_url).cyan().to_string()
        ));
        print_bullet(&t!("vllm-key-info"));

        let key: String = Input::new()
            .with_prompt(format!("  {}", t!("vllm-key-prompt")))
            .allow_empty(true)
            .interact_text()?;

        if key.trim().is_empty() {
            print_bullet(&t!(
                "local-server-no-key-hint",
                env_var = style("VLLM_API_KEY").yellow().to_string()
            ));
        }

        key
    } else if provider_name == "osaurus" {
        let raw_url: String = Input::new()
            .with_prompt(format!("  {}", t!("osaurus-url-prompt")))
            .default("http://localhost:1337/v1".to_string())
            .interact_text()?;

        let normalized_url = raw_url.trim().trim_end_matches('/').to_string();
        if normalized_url.is_empty() {
            anyhow::bail!("Osaurus endpoint URL cannot be empty.");
        }
        provider_api_url = Some(normalized_url.clone());

        print_bullet(&t!(
            "osaurus-using",
            url = style(&normalized_url).cyan().to_string()
        ));
        print_bullet(&t!("osaurus-key-info"));

        let key: String = Input::new()
            .with_prompt(format!("  {}", t!("osaurus-key-prompt")))
            .allow_empty(true)
            .interact_text()?;

        if key.trim().is_empty() {
            print_bullet(&t!(
                "local-server-no-key-hint",
                env_var = style("OSAURUS_API_KEY").yellow().to_string()
            ));
        }

        key
    } else if canonical_provider_name(provider_name) == "gemini" {
        // Special handling for Gemini: check for CLI auth first
        if crate::providers::gemini::GeminiProvider::has_cli_credentials() {
            print_bullet(&format!(
                "{} {}",
                style("✓").green().bold(),
                t!("gemini-cli-detected")
            ));
            print_bullet(&t!("gemini-cli-reuse-info"));
            println!();

            let use_cli: bool = dialoguer::Confirm::new()
                .with_prompt(format!("  {}", t!("gemini-cli-confirm")))
                .default(true)
                .interact()?;

            if use_cli {
                println!("  {} {}", style("✓").green().bold(), t!("gemini-cli-using"));
                String::new() // Empty key = will use CLI tokens
            } else {
                print_bullet(&t!("gemini-key-url-info"));
                Input::new()
                    .with_prompt(format!("  {}", t!("gemini-key-prompt")))
                    .allow_empty(true)
                    .interact_text()?
            }
        } else if std::env::var("GEMINI_API_KEY").is_ok() {
            print_bullet(&format!(
                "{} {}",
                style("✓").green().bold(),
                t!("gemini-env-detected")
            ));
            String::new()
        } else {
            print_bullet(&t!("gemini-key-url-info"));
            print_bullet(&t!("gemini-cli-fallback-info"));
            println!();

            Input::new()
                .with_prompt(format!("  {}", t!("gemini-key-prompt-optional")))
                .allow_empty(true)
                .interact_text()?
        }
    } else if canonical_provider_name(provider_name) == "anthropic" {
        if std::env::var("ANTHROPIC_OAUTH_TOKEN").is_ok() {
            print_bullet(&format!(
                "{} {}",
                style("✓").green().bold(),
                t!("anthropic-oauth-detected")
            ));
            String::new()
        } else if std::env::var("ANTHROPIC_API_KEY").is_ok() {
            print_bullet(&format!(
                "{} {}",
                style("✓").green().bold(),
                t!("anthropic-key-detected")
            ));
            String::new()
        } else {
            print_bullet(&t!(
                "anthropic-key-url-info",
                url = style("https://console.anthropic.com/settings/keys")
                    .cyan()
                    .underlined()
                    .to_string()
            ));
            print_bullet(&t!("anthropic-setup-token-info"));
            println!();

            let key: String = Input::new()
                .with_prompt(format!("  {}", t!("anthropic-key-prompt")))
                .allow_empty(true)
                .interact_text()?;

            if key.is_empty() {
                print_bullet(&t!(
                    "anthropic-skipped",
                    env_oauth = style("ANTHROPIC_OAUTH_TOKEN").yellow().to_string(),
                    env_key = style("ANTHROPIC_API_KEY").yellow().to_string()
                ));
            }

            key
        }
    } else if canonical_provider_name(provider_name) == "qwen-code" {
        if std::env::var("QWEN_OAUTH_TOKEN").is_ok() {
            print_bullet(&format!(
                "{} {}",
                style("✓").green().bold(),
                t!("qwen-oauth-detected")
            ));
            "qwen-oauth".to_string()
        } else {
            print_bullet(&t!("qwen-oauth-creds-info"));
            print_bullet(&t!("qwen-oauth-run-cli"));
            print_bullet(&t!("qwen-oauth-token-info"));
            println!();

            let key: String = Input::new()
                .with_prompt(format!("  {}", t!("qwen-oauth-prompt")))
                .allow_empty(true)
                .interact_text()?;

            if key.trim().is_empty() {
                print_bullet(&t!(
                    "qwen-oauth-skipped",
                    env_oauth = style("QWEN_OAUTH_TOKEN").yellow().to_string(),
                    env_key = style("QWEN_OAUTH_RESOURCE_URL").yellow().to_string()
                ));
                "qwen-oauth".to_string()
            } else {
                key
            }
        }
    } else {
        let key_url = if is_moonshot_alias(provider_name)
            || canonical_provider_name(provider_name) == "kimi-code"
        {
            "https://platform.moonshot.cn/console/api-keys"
        } else if canonical_provider_name(provider_name) == "qwen-code" {
            "https://qwen.readthedocs.io/en/latest/getting_started/installation.html"
        } else if is_glm_cn_alias(provider_name) || is_zai_cn_alias(provider_name) {
            "https://open.bigmodel.cn/usercenter/proj-mgmt/apikeys"
        } else if is_glm_alias(provider_name) || is_zai_alias(provider_name) {
            "https://platform.z.ai/"
        } else if is_minimax_alias(provider_name) {
            "https://www.minimaxi.com/user-center/basic-information"
        } else if is_qwen_alias(provider_name) {
            "https://help.aliyun.com/zh/model-studio/developer-reference/get-api-key"
        } else if is_qianfan_alias(provider_name) {
            "https://cloud.baidu.com/doc/WENXINWORKSHOP/s/7lm0vxo78"
        } else {
            match provider_name {
                "openrouter" => "https://openrouter.ai/keys",
                "openai" => "https://platform.openai.com/api-keys",
                "venice" => "https://venice.ai/settings/api",
                "groq" => "https://console.groq.com/keys",
                "mistral" => "https://console.mistral.ai/api-keys",
                "deepseek" => "https://platform.deepseek.com/api_keys",
                "together-ai" => "https://api.together.xyz/settings/api-keys",
                "fireworks" => "https://fireworks.ai/account/api-keys",
                "novita" => "https://novita.ai/settings/key-management",
                "perplexity" => "https://www.perplexity.ai/settings/api",
                "xai" => "https://console.x.ai",
                "cohere" => "https://dashboard.cohere.com/api-keys",
                "vercel" => "https://vercel.com/account/tokens",
                "cloudflare" => "https://dash.cloudflare.com/profile/api-tokens",
                "nvidia" | "nvidia-nim" | "build.nvidia.com" => "https://build.nvidia.com/",
                "bedrock" => "https://console.aws.amazon.com/iam",
                "gemini" => "https://aistudio.google.com/app/apikey",
                "astrai" => "https://as-trai.com",
                "avian" => "https://avian.io",
                _ => "",
            }
        };

        println!();
        if matches!(provider_name, "bedrock" | "aws-bedrock") {
            // Bedrock uses AWS AKSK, not a single API key.
            print_bullet(&t!("bedrock-info-1"));
            print_bullet(&t!(
                "bedrock-info-2",
                env_access = style("AWS_ACCESS_KEY_ID").yellow().to_string(),
                env_secret = style("AWS_SECRET_ACCESS_KEY").yellow().to_string()
            ));
            print_bullet(&t!(
                "bedrock-region-info",
                env_region = style("AWS_REGION").yellow().to_string()
            ));
            if !key_url.is_empty() {
                print_bullet(&t!(
                    "bedrock-iam-url",
                    url = style(key_url).cyan().underlined().to_string()
                ));
            }
            println!();
            String::new()
        } else {
            if !key_url.is_empty() {
                print_bullet(&t!(
                    "provider-key-url-info",
                    url = style(key_url).cyan().underlined().to_string()
                ));
            }
            print_bullet(&t!("provider-key-config-info"));
            println!();

            let key: String = Input::new()
                .with_prompt(format!("  {}", t!("provider-api-key-prompt")))
                .allow_empty(true)
                .interact_text()?;

            if key.is_empty() {
                let env_var = provider_env_var(provider_name);
                print_bullet(&t!(
                    "provider-key-skipped",
                    env_var = style(env_var).yellow().to_string()
                ));
            }

            key
        }
    };

    // ── Model selection ──
    let canonical_provider = canonical_provider_name(provider_name);
    let mut model_options: Vec<(String, String)> = curated_models_for_provider(canonical_provider);

    let mut live_options: Option<Vec<(String, String)>> = None;

    if supports_live_model_fetch(provider_name) {
        let ollama_remote = canonical_provider == "ollama"
            && ollama_uses_remote_endpoint(provider_api_url.as_deref());
        let can_fetch_without_key =
            allows_unauthenticated_model_fetch(provider_name) && !ollama_remote;
        let has_api_key = !api_key.trim().is_empty()
            || ((canonical_provider != "ollama" || ollama_remote)
                && std::env::var(provider_env_var(provider_name))
                    .ok()
                    .is_some_and(|value| !value.trim().is_empty()))
            || (provider_name == "minimax"
                && std::env::var("MINIMAX_OAUTH_TOKEN")
                    .ok()
                    .is_some_and(|value| !value.trim().is_empty()));

        if canonical_provider == "ollama" && ollama_remote && !has_api_key {
            print_bullet(&t!(
                "model-needs-key-fallback",
                env_var = style("OLLAMA_API_KEY").yellow().to_string()
            ));
        }

        if can_fetch_without_key || has_api_key {
            if let Some(cached) =
                load_cached_models_for_provider(workspace_dir, provider_name, MODEL_CACHE_TTL_SECS)
                    .await?
            {
                let shown_count = cached.models.len().min(LIVE_MODEL_MAX_OPTIONS);
                print_bullet(&t!(
                    "model-cache-found",
                    count = shown_count,
                    age = humanize_age(cached.age_secs)
                ));

                live_options = Some(build_model_options(
                    cached
                        .models
                        .into_iter()
                        .take(LIVE_MODEL_MAX_OPTIONS)
                        .collect(),
                    "cached",
                ));
            }

            let refresh_prompt = t!("model-refresh-prompt");
            let fetch_prompt = t!("model-fetch-prompt");
            let should_fetch_now = Confirm::new()
                .with_prompt(format!(
                    "  {}",
                    if live_options.is_some() {
                        refresh_prompt.as_str()
                    } else {
                        fetch_prompt.as_str()
                    }
                ))
                .default(live_options.is_none())
                .interact()?;

            if should_fetch_now {
                match fetch_live_models_for_provider(
                    provider_name,
                    &api_key,
                    provider_api_url.as_deref(),
                )
                .await
                {
                    Ok(live_model_ids) if !live_model_ids.is_empty() => {
                        cache_live_models_for_provider(
                            workspace_dir,
                            provider_name,
                            &live_model_ids,
                        )
                        .await?;

                        let fetched_count = live_model_ids.len();
                        let shown_count = fetched_count.min(LIVE_MODEL_MAX_OPTIONS);
                        let shown_models: Vec<String> = live_model_ids
                            .into_iter()
                            .take(LIVE_MODEL_MAX_OPTIONS)
                            .collect();

                        if shown_count < fetched_count {
                            print_bullet(&t!(
                                "model-fetched-truncated",
                                total = fetched_count,
                                shown = shown_count
                            ));
                        } else {
                            print_bullet(&t!("model-fetched-all", count = shown_count));
                        }

                        live_options = Some(build_model_options(shown_models, "live"));
                    }
                    Ok(_) => {
                        print_bullet(&t!("model-no-models-returned"));
                    }
                    Err(error) => {
                        print_bullet(&t!(
                            "model-fetch-failed",
                            err = style(error.to_string()).yellow().to_string()
                        ));

                        if live_options.is_none() {
                            if let Some(stale) =
                                load_any_cached_models_for_provider(workspace_dir, provider_name)
                                    .await?
                            {
                                print_bullet(&t!(
                                    "model-cache-stale",
                                    age = humanize_age(stale.age_secs)
                                ));

                                live_options = Some(build_model_options(
                                    stale
                                        .models
                                        .into_iter()
                                        .take(LIVE_MODEL_MAX_OPTIONS)
                                        .collect(),
                                    "stale-cache",
                                ));
                            }
                        }
                    }
                }
            }
        } else {
            print_bullet(&t!("model-no-key-curated"));
            print_bullet(&t!("model-tip-add-key"));
        }
    }

    if let Some(live_model_options) = live_options {
        let source_options = vec![
            format!("Provider model list ({})", live_model_options.len()),
            format!("Curated starter list ({})", model_options.len()),
        ];

        let source_idx = Select::new()
            .with_prompt(format!("  {}", t!("model-source-prompt")))
            .items(&source_options)
            .default(0)
            .interact()?;

        if source_idx == 0 {
            model_options = live_model_options;
        }
    }

    if model_options.is_empty() {
        model_options.push((
            default_model_for_provider(provider_name),
            "Provider default model".to_string(),
        ));
    }

    model_options.push((
        CUSTOM_MODEL_SENTINEL.to_string(),
        "Custom model ID (type manually)".to_string(),
    ));

    let model_labels: Vec<String> = model_options
        .iter()
        .map(|(model_id, label)| format!("{label} — {}", style(model_id).dim()))
        .collect();

    let model_idx = Select::new()
        .with_prompt(format!("  {}", t!("provider-select-model")))
        .items(&model_labels)
        .default(0)
        .interact()?;

    let selected_model = model_options[model_idx].0.clone();
    let model = if selected_model == CUSTOM_MODEL_SENTINEL {
        Input::new()
            .with_prompt(format!("  {}", t!("provider-enter-custom-model")))
            .default(default_model_for_provider(provider_name))
            .interact_text()?
    } else {
        selected_model
    };

    println!(
        "  {} Provider: {} | Model: {}",
        style("✓").green().bold(),
        style(provider_name).green(),
        style(&model).green()
    );

    Ok((provider_name.to_string(), api_key, model, provider_api_url))
}

fn local_provider_choices() -> Vec<(&'static str, &'static str)> {
    vec![
        ("ollama", "Ollama — local models (Llama, Mistral, Phi)"),
        (
            "llamacpp",
            "llama.cpp server — local OpenAI-compatible endpoint",
        ),
        (
            "sglang",
            "SGLang — high-performance local serving framework",
        ),
        ("vllm", "vLLM — high-performance local inference engine"),
        (
            "osaurus",
            "Osaurus — unified AI edge runtime (local MLX + cloud proxy + MCP)",
        ),
    ]
}

/// Map provider name to its conventional env var
fn provider_env_var(name: &str) -> &'static str {
    if canonical_provider_name(name) == "qwen-code" {
        return "QWEN_OAUTH_TOKEN";
    }

    match canonical_provider_name(name) {
        "openrouter" => "OPENROUTER_API_KEY",
        "anthropic" => "ANTHROPIC_API_KEY",
        "openai-codex" | "openai" => "OPENAI_API_KEY",
        "ollama" => "OLLAMA_API_KEY",
        "llamacpp" => "LLAMACPP_API_KEY",
        "sglang" => "SGLANG_API_KEY",
        "vllm" => "VLLM_API_KEY",
        "osaurus" => "OSAURUS_API_KEY",
        "venice" => "VENICE_API_KEY",
        "groq" => "GROQ_API_KEY",
        "mistral" => "MISTRAL_API_KEY",
        "deepseek" => "DEEPSEEK_API_KEY",
        "xai" => "XAI_API_KEY",
        "together-ai" => "TOGETHER_API_KEY",
        "fireworks" | "fireworks-ai" => "FIREWORKS_API_KEY",
        "novita" => "NOVITA_API_KEY",
        "perplexity" => "PERPLEXITY_API_KEY",
        "cohere" => "COHERE_API_KEY",
        "kimi-code" => "KIMI_CODE_API_KEY",
        "moonshot" => "MOONSHOT_API_KEY",
        "glm" => "GLM_API_KEY",
        "minimax" => "MINIMAX_API_KEY",
        "qwen" => "DASHSCOPE_API_KEY",
        "qianfan" => "QIANFAN_API_KEY",
        "zai" => "ZAI_API_KEY",
        "synthetic" => "SYNTHETIC_API_KEY",
        "opencode" | "opencode-zen" => "OPENCODE_API_KEY",
        "opencode-go" => "OPENCODE_GO_API_KEY",
        "vercel" | "vercel-ai" => "VERCEL_API_KEY",
        "cloudflare" | "cloudflare-ai" => "CLOUDFLARE_API_KEY",
        "bedrock" | "aws-bedrock" => "AWS_ACCESS_KEY_ID",
        "gemini" => "GEMINI_API_KEY",
        "nvidia" | "nvidia-nim" | "build.nvidia.com" => "NVIDIA_API_KEY",
        "astrai" => "ASTRAI_API_KEY",
        "avian" => "AVIAN_API_KEY",
        _ => "API_KEY",
    }
}

fn provider_supports_keyless_local_usage(provider_name: &str) -> bool {
    matches!(
        canonical_provider_name(provider_name),
        "ollama" | "llamacpp" | "sglang" | "vllm" | "osaurus"
    )
}

fn provider_supports_device_flow(provider_name: &str) -> bool {
    matches!(
        canonical_provider_name(provider_name),
        "copilot" | "gemini" | "openai-codex"
    )
}

// ── Step 5: Tool Mode & Security ────────────────────────────────

fn setup_tool_mode() -> Result<(ComposioConfig, SecretsConfig)> {
    print_bullet(&t!("tool-mode-info-1"));
    print_bullet(&t!("tool-mode-info-2"));
    println!();

    let options = [
        "Sovereign (local only) — you manage API keys, full privacy (default)",
        "Composio (managed OAuth) — 1000+ apps via OAuth, no raw keys shared",
    ];

    let choice = Select::new()
        .with_prompt(format!("  {}", t!("tool-mode-select")))
        .items(options)
        .default(0)
        .interact()?;

    let composio_config = if choice == 1 {
        println!();
        println!(
            "  {} {}",
            style(t!("composio-title")).white().bold(),
            style(t!("composio-subtitle")).dim()
        );
        print_bullet(&t!("composio-key-url"));
        print_bullet(&t!("composio-info"));
        println!();

        let api_key: String = Input::new()
            .with_prompt(format!("  {}", t!("composio-key-prompt")))
            .allow_empty(true)
            .interact_text()?;

        if api_key.trim().is_empty() {
            println!("  {} {}", style("→").dim(), t!("composio-skipped"));
            ComposioConfig::default()
        } else {
            println!(
                "  {} {}",
                style("✓").green().bold(),
                t!(
                    "composio-confirmed",
                    value = style("enabled").green().to_string()
                )
            );
            ComposioConfig {
                enabled: true,
                api_key: Some(api_key),
                ..ComposioConfig::default()
            }
        }
    } else {
        println!(
            "  {} Tool mode: {} — full privacy, you own every key",
            style("✓").green().bold(),
            style("Sovereign (local only)").green()
        );
        ComposioConfig::default()
    };

    // ── Encrypted secrets ──
    println!();
    print_bullet(&t!("secrets-info-1"));
    print_bullet(&t!("secrets-info-2"));

    let encrypt = Confirm::new()
        .with_prompt(format!("  {}", t!("secrets-encrypt")))
        .default(true)
        .interact()?;

    let secrets_config = SecretsConfig { encrypt };

    if encrypt {
        println!(
            "  {} {}",
            style("✓").green().bold(),
            t!(
                "secrets-status-encrypted",
                value = style("encrypted").green().to_string()
            )
        );
    } else {
        println!(
            "  {} {}",
            style("✓").green().bold(),
            t!(
                "secrets-status-plaintext",
                value = style("plaintext").yellow().to_string()
            )
        );
    }

    Ok((composio_config, secrets_config))
}

// ── Step 6: Hardware (Physical World) ───────────────────────────

fn setup_hardware() -> Result<HardwareConfig> {
    print_bullet(&t!("hardware-info-1"));
    print_bullet(&t!("hardware-scanning"));
    println!();

    // ── Auto-discovery ──
    let devices = hardware::discover_hardware();

    if devices.is_empty() {
        println!(
            "  {} {}",
            style("ℹ").dim(),
            style(t!("hardware-no-devices")).dim()
        );
        println!(
            "  {} {}",
            style("ℹ").dim(),
            style(t!("hardware-enable-later")).dim()
        );
    } else {
        println!(
            "  {} {}",
            style("✓").green().bold(),
            t!("hardware-devices-found", count = devices.len())
        );
        for device in &devices {
            let detail = device
                .detail
                .as_deref()
                .map(|d| format!(" ({d})"))
                .unwrap_or_default();
            let path = device
                .device_path
                .as_deref()
                .map(|p| format!(" → {p}"))
                .unwrap_or_default();
            println!(
                "    {} {}{}{} [{}]",
                style("›").cyan(),
                style(&device.name).green(),
                style(&detail).dim(),
                style(&path).dim(),
                style(device.transport.to_string()).cyan()
            );
        }
    }
    println!();

    let opt_native = t!("hardware-mode-native");
    let opt_tethered = t!("hardware-mode-tethered");
    let opt_probe = t!("hardware-mode-debug-probe");
    let opt_software = t!("hardware-mode-software");
    let options = [
        opt_native.as_str(),
        opt_tethered.as_str(),
        opt_probe.as_str(),
        opt_software.as_str(),
    ];

    let recommended = hardware::recommended_wizard_default(&devices);

    let choice = Select::new()
        .with_prompt(format!("  {}", t!("hardware-prompt")))
        .items(options)
        .default(recommended)
        .interact()?;

    let mut hw_config = hardware::config_from_wizard_choice(choice, &devices);

    // ── Serial: pick a port if multiple found ──
    if hw_config.transport_mode() == hardware::HardwareTransport::Serial {
        let serial_devices: Vec<&hardware::DiscoveredDevice> = devices
            .iter()
            .filter(|d| d.transport == hardware::HardwareTransport::Serial)
            .collect();

        if serial_devices.len() > 1 {
            let port_labels: Vec<String> = serial_devices
                .iter()
                .map(|d| {
                    format!(
                        "{} ({})",
                        d.device_path.as_deref().unwrap_or("unknown"),
                        d.name
                    )
                })
                .collect();

            let port_idx = Select::new()
                .with_prompt(format!("  {}", t!("hardware-multiple-serial")))
                .items(&port_labels)
                .default(0)
                .interact()?;

            hw_config.serial_port = serial_devices[port_idx].device_path.clone();
        } else if serial_devices.is_empty() {
            // User chose serial but no device discovered — ask for manual path
            let manual_port: String = Input::new()
                .with_prompt(format!("  {}", t!("hardware-serial-port-prompt")))
                .default("/dev/ttyUSB0".to_string())
                .interact_text()?;
            hw_config.serial_port = Some(manual_port);
        }

        // Baud rate
        let baud_default = t!("hardware-baud-default");
        let baud_legacy = t!("hardware-baud-legacy");
        let baud_custom = t!("hardware-baud-custom");
        let baud_options = [
            baud_default.as_str(),
            baud_legacy.as_str(),
            "57600",
            "230400",
            baud_custom.as_str(),
        ];
        let baud_idx = Select::new()
            .with_prompt(format!("  {}", t!("hardware-baud-rate-prompt")))
            .items(baud_options)
            .default(0)
            .interact()?;

        hw_config.baud_rate = match baud_idx {
            1 => 9600,
            2 => 57600,
            3 => 230_400,
            4 => {
                let custom: String = Input::new()
                    .with_prompt(format!("  {}", t!("hardware-baud-custom-prompt")))
                    .default("115200".to_string())
                    .interact_text()?;
                custom.parse::<u32>().unwrap_or(115_200)
            }
            _ => 115_200,
        };
    }

    // ── Probe: ask for target chip ──
    if hw_config.transport_mode() == hardware::HardwareTransport::Probe
        && hw_config.probe_target.is_none()
    {
        let target: String = Input::new()
            .with_prompt(format!("  {}", t!("hardware-mcu-prompt")))
            .default("STM32F411CEUx".to_string())
            .interact_text()?;
        hw_config.probe_target = Some(target);
    }

    // ── Datasheet RAG ──
    if hw_config.enabled {
        let datasheets = Confirm::new()
            .with_prompt(format!("  {}", t!("hardware-rag-prompt")))
            .default(true)
            .interact()?;
        hw_config.workspace_datasheets = datasheets;
    }

    // ── Summary ──
    if hw_config.enabled {
        let transport_label = match hw_config.transport_mode() {
            hardware::HardwareTransport::Native => "Native GPIO".to_string(),
            hardware::HardwareTransport::Serial => format!(
                "Serial → {} @ {} baud",
                hw_config.serial_port.as_deref().unwrap_or("?"),
                hw_config.baud_rate
            ),
            hardware::HardwareTransport::Probe => format!(
                "Probe (SWD/JTAG) → {}",
                hw_config.probe_target.as_deref().unwrap_or("?")
            ),
            hardware::HardwareTransport::None => "Software Only".to_string(),
        };

        let rag_state = if hw_config.workspace_datasheets {
            style("on").green().to_string()
        } else {
            style("off").dim().to_string()
        };
        println!(
            "  {} {}",
            style("✓").green().bold(),
            t!(
                "hardware-status-with-rag",
                mode = style(&transport_label).green().to_string(),
                rag = rag_state
            )
        );
    } else {
        println!(
            "  {} {}",
            style("✓").green().bold(),
            t!(
                "hardware-status",
                mode = style("disabled (software only)").dim().to_string()
            )
        );
    }

    Ok(hw_config)
}

// ── Step 6: Project Context ─────────────────────────────────────

fn setup_project_context() -> Result<ProjectContext> {
    print_bullet(&t!("ctx-info-personalize"));
    print_bullet(&t!("ctx-info-defaults"));
    println!();

    let user_name: String = Input::new()
        .with_prompt(format!("  {}", t!("ctx-your-name")))
        .default("User".to_string())
        .interact_text()?;

    let tz_us_eastern = t!("ctx-tz-us-eastern");
    let tz_us_central = t!("ctx-tz-us-central");
    let tz_us_mountain = t!("ctx-tz-us-mountain");
    let tz_us_pacific = t!("ctx-tz-us-pacific");
    let tz_eu_london = t!("ctx-tz-eu-london");
    let tz_eu_berlin = t!("ctx-tz-eu-berlin");
    let tz_asia_tokyo = t!("ctx-tz-asia-tokyo");
    let tz_asia_seoul = t!("ctx-tz-asia-seoul");
    let tz_utc = t!("ctx-tz-utc");
    let tz_other = t!("ctx-tz-other");
    let tz_options = [
        tz_us_eastern.as_str(),
        tz_us_central.as_str(),
        tz_us_mountain.as_str(),
        tz_us_pacific.as_str(),
        tz_eu_london.as_str(),
        tz_eu_berlin.as_str(),
        tz_asia_tokyo.as_str(),
        tz_asia_seoul.as_str(),
        tz_utc.as_str(),
        tz_other.as_str(),
    ];

    // IANA tz names extracted from the labels — always English regardless of UI lang.
    let tz_canonical = [
        "America/New_York",
        "America/Chicago",
        "America/Denver",
        "America/Los_Angeles",
        "Europe/London",
        "Europe/Berlin",
        "Asia/Tokyo",
        "Asia/Seoul",
        "UTC",
        "", // Other — handled below
    ];

    let tz_idx = Select::new()
        .with_prompt(format!("  {}", t!("ctx-timezone")))
        .items(tz_options)
        .default(0)
        .interact()?;

    let timezone = if tz_idx == tz_options.len() - 1 {
        Input::new()
            .with_prompt(format!("  {}", t!("ctx-timezone-enter")))
            .default("UTC".to_string())
            .interact_text()?
    } else {
        tz_canonical[tz_idx].to_string()
    };

    let agent_name: String = Input::new()
        .with_prompt(format!("  {}", t!("ctx-agent-name")))
        .default("Construct".to_string())
        .interact_text()?;

    let style_direct = t!("ctx-style-direct");
    let style_friendly = t!("ctx-style-friendly");
    let style_professional = t!("ctx-style-professional");
    let style_expressive = t!("ctx-style-expressive");
    let style_technical = t!("ctx-style-technical");
    let style_balanced = t!("ctx-style-balanced");
    let style_custom_lbl = t!("ctx-style-custom");
    let style_options = [
        style_direct.as_str(),
        style_friendly.as_str(),
        style_professional.as_str(),
        style_expressive.as_str(),
        style_technical.as_str(),
        style_balanced.as_str(),
        style_custom_lbl.as_str(),
    ];

    let style_idx = Select::new()
        .with_prompt(format!("  {}", t!("ctx-comm-style")))
        .items(style_options)
        .default(1)
        .interact()?;

    // The persona descriptions baked into the workspace are intentionally
    // English regardless of UI language — they're prompts the agent receives,
    // not user-facing UI. Localizing them would change agent behavior, which
    // is out of scope for the wizard i18n pass.
    let communication_style = match style_idx {
        0 => "Be direct and concise. Skip pleasantries. Get to the point.".to_string(),
        1 => "Be friendly, human, and conversational. Show warmth and empathy while staying efficient. Use natural contractions.".to_string(),
        2 => "Be professional and polished. Stay calm, structured, and respectful. Use occasional tone-setting emojis only when appropriate.".to_string(),
        3 => "Be expressive and playful when appropriate. Use relevant emojis naturally (0-2 max), and keep serious topics emoji-light.".to_string(),
        4 => "Be technical and detailed. Thorough explanations, code-first.".to_string(),
        5 => "Adapt to the situation. Default to warm and clear communication; be concise when needed, thorough when it matters.".to_string(),
        _ => Input::new()
            .with_prompt(format!("  {}", t!("ctx-comm-style-custom")))
            .default(
                "Be warm, natural, and clear. Use occasional relevant emojis (1-2 max) and avoid robotic phrasing.".to_string(),
            )
            .interact_text()?,
    };

    println!(
        "  {} Context: {} | {} | {} | {}",
        style("✓").green().bold(),
        style(&user_name).green(),
        style(&timezone).green(),
        style(&agent_name).green(),
        style(&communication_style).green().dim()
    );

    Ok(ProjectContext {
        user_name,
        timezone,
        agent_name,
        communication_style,
    })
}

// ── Step 6: Memory Configuration ───────────────────────────────

/// Result of memory setup — includes both MemoryConfig and optional Kumiho overrides.
pub struct MemorySetupResult {
    pub memory_config: MemoryConfig,
    pub kumiho_api_url: Option<String>,
    pub kumiho_service_token: Option<String>,
}

fn setup_memory() -> Result<MemorySetupResult> {
    print_bullet("Choose how Construct stores and searches memories.");
    print_bullet("Kumiho is the recommended graph-native memory backend.");
    print_bullet("You can always change this later in config.toml.");
    println!();

    let options: Vec<&str> = selectable_memory_backends()
        .iter()
        .map(|backend| backend.label)
        .collect();

    let choice = Select::new()
        .with_prompt(format!("  {}", t!("memory-select")))
        .items(&options)
        .default(0)
        .interact()?;

    let backend = backend_key_from_choice(choice);
    let profile = memory_backend_profile(backend);

    let mut kumiho_api_url: Option<String> = None;
    let mut kumiho_service_token: Option<String> = None;

    // If Kumiho selected, collect connection details.
    if backend == "kumiho" {
        println!();
        print_bullet("Kumiho requires a running FastAPI instance with Neo4j.");
        println!();

        let api_url = Input::new()
            .with_prompt(format!("  {}", t!("memory-kumiho-api-url")))
            .default("https://api.kumiho.cloud".to_string())
            .interact_text()?;
        kumiho_api_url = Some(api_url);

        let token = Input::new()
            .with_prompt(format!("  {}", t!("memory-kumiho-token")))
            .interact_text()?;
        kumiho_service_token = Some(token.trim().to_string());

        println!(
            "  {} Kumiho API: {}",
            style("✓").green().bold(),
            style(
                kumiho_api_url
                    .as_deref()
                    .unwrap_or("https://api.kumiho.cloud")
            )
            .green()
        );
        println!(
            "  {} Service token: {}",
            style("✓").green().bold(),
            style("configured").green()
        );
    }

    let auto_save = profile.auto_save_default
        && Confirm::new()
            .with_prompt("  Auto-save conversations to memory?")
            .default(true)
            .interact()?;

    println!(
        "  {} Memory: {} (auto-save: {})",
        style("✓").green().bold(),
        style(backend).green(),
        if auto_save { "on" } else { "off" }
    );

    let mut config = memory_config_defaults_for_backend(backend);
    config.auto_save = auto_save;
    Ok(MemorySetupResult {
        memory_config: config,
        kumiho_api_url,
        kumiho_service_token,
    })
}

// ── Step 3: Channels ────────────────────────────────────────────

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ChannelMenuChoice {
    Telegram,
    Discord,
    Slack,
    IMessage,
    Matrix,
    Signal,
    WhatsApp,
    Linq,
    Irc,
    Webhook,
    NextcloudTalk,
    DingTalk,
    QqOfficial,
    Lark,
    Feishu,
    #[cfg(feature = "channel-nostr")]
    Nostr,
    Done,
}

const CHANNEL_MENU_CHOICES: &[ChannelMenuChoice] = &[
    ChannelMenuChoice::Telegram,
    ChannelMenuChoice::Discord,
    ChannelMenuChoice::Slack,
    ChannelMenuChoice::IMessage,
    ChannelMenuChoice::Matrix,
    ChannelMenuChoice::Signal,
    ChannelMenuChoice::WhatsApp,
    ChannelMenuChoice::Linq,
    ChannelMenuChoice::Irc,
    ChannelMenuChoice::Webhook,
    ChannelMenuChoice::NextcloudTalk,
    ChannelMenuChoice::DingTalk,
    ChannelMenuChoice::QqOfficial,
    ChannelMenuChoice::Lark,
    ChannelMenuChoice::Feishu,
    #[cfg(feature = "channel-nostr")]
    ChannelMenuChoice::Nostr,
    ChannelMenuChoice::Done,
];

fn channel_menu_choices() -> &'static [ChannelMenuChoice] {
    CHANNEL_MENU_CHOICES
}

#[allow(clippy::too_many_lines)]
fn setup_channels() -> Result<ChannelsConfig> {
    print_bullet(&t!("channels-info-1"));
    print_bullet(&t!("channels-info-2"));
    println!();

    let mut config = ChannelsConfig::default();
    let menu_choices = channel_menu_choices();

    loop {
        let options: Vec<String> = menu_choices
            .iter()
            .map(|choice| match choice {
                ChannelMenuChoice::Telegram => format!(
                    "Telegram   {}",
                    if config.telegram.is_some() {
                        "✅ connected"
                    } else {
                        "— connect your bot"
                    }
                ),
                ChannelMenuChoice::Discord => format!(
                    "Discord    {}",
                    if config.discord.is_some() {
                        "✅ connected"
                    } else {
                        "— connect your bot"
                    }
                ),
                ChannelMenuChoice::Slack => format!(
                    "Slack      {}",
                    if config.slack.is_some() {
                        "✅ connected"
                    } else {
                        "— connect your bot"
                    }
                ),
                ChannelMenuChoice::IMessage => format!(
                    "iMessage   {}",
                    if config.imessage.is_some() {
                        "✅ configured"
                    } else {
                        "— macOS only"
                    }
                ),
                ChannelMenuChoice::Matrix => format!(
                    "Matrix     {}",
                    if config.matrix.is_some() {
                        "✅ connected"
                    } else {
                        "— self-hosted chat"
                    }
                ),
                ChannelMenuChoice::Signal => format!(
                    "Signal     {}",
                    if config.signal.is_some() {
                        "✅ connected"
                    } else {
                        "— signal-cli daemon bridge"
                    }
                ),
                ChannelMenuChoice::WhatsApp => format!(
                    "WhatsApp   {}",
                    if config.whatsapp.is_some() {
                        "✅ connected"
                    } else {
                        "— Business Cloud API"
                    }
                ),
                ChannelMenuChoice::Linq => format!(
                    "Linq       {}",
                    if config.linq.is_some() {
                        "✅ connected"
                    } else {
                        "— iMessage/RCS/SMS via Linq API"
                    }
                ),
                ChannelMenuChoice::Irc => format!(
                    "IRC        {}",
                    if config.irc.is_some() {
                        "✅ configured"
                    } else {
                        "— IRC over TLS"
                    }
                ),
                ChannelMenuChoice::Webhook => format!(
                    "Webhook    {}",
                    if config.webhook.is_some() {
                        "✅ configured"
                    } else {
                        "— HTTP endpoint"
                    }
                ),
                ChannelMenuChoice::NextcloudTalk => format!(
                    "Nextcloud  {}",
                    if config.nextcloud_talk.is_some() {
                        "✅ connected"
                    } else {
                        "— Talk webhook + OCS API"
                    }
                ),
                ChannelMenuChoice::DingTalk => format!(
                    "DingTalk   {}",
                    if config.dingtalk.is_some() {
                        "✅ connected"
                    } else {
                        "— DingTalk Stream Mode"
                    }
                ),
                ChannelMenuChoice::QqOfficial => format!(
                    "QQ Official {}",
                    if config.qq.is_some() {
                        "✅ connected"
                    } else {
                        "— Tencent QQ Bot"
                    }
                ),
                ChannelMenuChoice::Lark => format!(
                    "Lark       {}",
                    if config.lark.as_ref().is_some_and(|cfg| !cfg.use_feishu) {
                        "✅ connected"
                    } else {
                        "— Lark Bot"
                    }
                ),
                ChannelMenuChoice::Feishu => format!(
                    "Feishu     {}",
                    if config.feishu.is_some()
                        || config.lark.as_ref().is_some_and(|cfg| cfg.use_feishu)
                    {
                        "✅ connected"
                    } else {
                        "— Feishu Bot"
                    }
                ),
                #[cfg(feature = "channel-nostr")]
                ChannelMenuChoice::Nostr => format!(
                    "Nostr {}",
                    if config.nostr.is_some() {
                        "✅ connected"
                    } else {
                        "     — Nostr DMs"
                    }
                ),
                ChannelMenuChoice::Done => "Done — finish setup".to_string(),
            })
            .collect();

        let selection = Select::new()
            .with_prompt(format!("  {}", t!("channels-prompt")))
            .items(&options)
            .default(options.len() - 1)
            .interact()?;

        let choice = menu_choices
            .get(selection)
            .copied()
            .unwrap_or(ChannelMenuChoice::Done);

        match choice {
            ChannelMenuChoice::Telegram => {
                // ── Telegram ──
                println!();
                println!(
                    "  {} {}",
                    style(t!("telegram-title")).white().bold(),
                    style(format!("— {}", t!("telegram-subtitle"))).dim()
                );
                print_bullet(&t!("telegram-step-1"));
                print_bullet(&t!("telegram-step-2"));
                print_bullet(&t!("telegram-step-3"));
                println!();

                let token: String = Input::new()
                    .with_prompt(format!("  {}", t!("telegram-token-prompt")))
                    .interact_text()?;

                if token.trim().is_empty() {
                    println!("  {} {}", style("→").dim(), t!("channel-skipped"));
                    continue;
                }

                // Test connection (run entirely in separate thread — reqwest::blocking Response
                // must be used and dropped there to avoid "Cannot drop a runtime" panic)
                print!("  {} {} ", style("⏳").dim(), t!("channel-testing"));
                let token_clone = token.clone();
                let thread_result = std::thread::spawn(move || {
                    let client = reqwest::blocking::Client::new();
                    let url = format!("https://api.telegram.org/bot{token_clone}/getMe");
                    let resp = client.get(&url).send()?;
                    let ok = resp.status().is_success();
                    let data: serde_json::Value = resp.json().unwrap_or_default();
                    let bot_name = data
                        .get("result")
                        .and_then(|r| r.get("username"))
                        .and_then(serde_json::Value::as_str)
                        .unwrap_or("unknown")
                        .to_string();
                    Ok::<_, reqwest::Error>((ok, bot_name))
                })
                .join();
                match thread_result {
                    Ok(Ok((true, bot_name))) => {
                        println!(
                            "\r  {} {}        ",
                            style("✅").green().bold(),
                            t!("telegram-connected", bot_name = bot_name)
                        );
                    }
                    _ => {
                        println!(
                            "\r  {} {}",
                            style("❌").red().bold(),
                            t!("channel-conn-failed-token")
                        );
                        continue;
                    }
                }

                print_bullet(&t!("telegram-allowlist-info-1"));
                print_bullet(&t!("telegram-allowlist-info-2"));
                print_bullet(&t!("telegram-allowlist-info-3"));

                let users_str: String = Input::new()
                    .with_prompt(format!("  {}", t!("telegram-allowlist-prompt")))
                    .allow_empty(true)
                    .interact_text()?;

                let allowed_users = if users_str.trim() == "*" {
                    vec!["*".into()]
                } else {
                    users_str
                        .split(',')
                        .map(|s| s.trim().to_string())
                        .filter(|s| !s.is_empty())
                        .collect()
                };

                if allowed_users.is_empty() {
                    println!(
                        "  {} {}",
                        style("⚠").yellow().bold(),
                        t!("telegram-allowlist-warn")
                    );
                }

                config.telegram = Some(TelegramConfig {
                    bot_token: token,
                    allowed_users,
                    stream_mode: StreamMode::default(),
                    draft_update_interval_ms: 1000,
                    interrupt_on_new_message: false,
                    mention_only: false,
                    ack_reactions: None,
                    proxy_url: None,
                    notification_chat_id: None,
                });
            }
            ChannelMenuChoice::Discord => {
                // ── Discord ──
                println!();
                println!(
                    "  {} {}",
                    style(t!("discord-title")).white().bold(),
                    style(format!("— {}", t!("discord-subtitle"))).dim()
                );
                print_bullet(&t!("discord-step-1"));
                print_bullet(&t!("discord-step-2"));
                print_bullet(&t!("discord-step-3"));
                print_bullet(&t!("discord-step-4"));
                println!();

                let token: String = Input::new()
                    .with_prompt(format!("  {}", t!("discord-token-prompt")))
                    .interact_text()?;

                if token.trim().is_empty() {
                    println!("  {} {}", style("→").dim(), t!("channel-skipped"));
                    continue;
                }

                // Test connection (run entirely in separate thread — Response must be used/dropped there)
                print!("  {} {} ", style("⏳").dim(), t!("channel-testing"));
                let token_clone = token.clone();
                let thread_result = std::thread::spawn(move || {
                    let client = reqwest::blocking::Client::new();
                    let resp = client
                        .get("https://discord.com/api/v10/users/@me")
                        .header("Authorization", format!("Bot {token_clone}"))
                        .send()?;
                    let ok = resp.status().is_success();
                    let data: serde_json::Value = resp.json().unwrap_or_default();
                    let bot_name = data
                        .get("username")
                        .and_then(serde_json::Value::as_str)
                        .unwrap_or("unknown")
                        .to_string();
                    Ok::<_, reqwest::Error>((ok, bot_name))
                })
                .join();
                match thread_result {
                    Ok(Ok((true, bot_name))) => {
                        println!(
                            "\r  {} {}        ",
                            style("✅").green().bold(),
                            t!("discord-connected", bot_name = bot_name)
                        );
                    }
                    _ => {
                        println!(
                            "\r  {} {}",
                            style("❌").red().bold(),
                            t!("channel-conn-failed-token")
                        );
                        continue;
                    }
                }

                let guild: String = Input::new()
                    .with_prompt(format!("  {}", t!("discord-guild-prompt")))
                    .allow_empty(true)
                    .interact_text()?;

                print_bullet(&t!("discord-allowlist-info-1"));
                print_bullet(&t!("discord-allowlist-info-2"));
                print_bullet(&t!("discord-allowlist-info-3"));

                let allowed_users_str: String = Input::new()
                    .with_prompt(format!("  {}", t!("discord-allowlist-prompt")))
                    .allow_empty(true)
                    .interact_text()?;

                let allowed_users = if allowed_users_str.trim().is_empty() {
                    vec![]
                } else {
                    allowed_users_str
                        .split(',')
                        .map(|s| s.trim().to_string())
                        .filter(|s| !s.is_empty())
                        .collect()
                };

                if allowed_users.is_empty() {
                    println!(
                        "  {} {}",
                        style("⚠").yellow().bold(),
                        t!("discord-allowlist-warn")
                    );
                }

                config.discord = Some(DiscordConfig {
                    bot_token: token,
                    guild_id: if guild.is_empty() { None } else { Some(guild) },
                    allowed_users,
                    listen_to_bots: false,
                    interrupt_on_new_message: false,
                    mention_only: false,
                    notification_channel_id: None,
                    proxy_url: None,
                    stream_mode: StreamMode::MultiMessage,
                    draft_update_interval_ms: 1000,
                    multi_message_delay_ms: 800,
                });
            }
            ChannelMenuChoice::Slack => {
                // ── Slack ──
                println!();
                println!(
                    "  {} {}",
                    style(t!("slack-title")).white().bold(),
                    style(format!("— {}", t!("slack-subtitle"))).dim()
                );
                print_bullet(&t!("slack-step-1"));
                print_bullet(&t!("slack-step-2"));
                print_bullet(&t!("slack-step-3"));
                println!();

                let token: String = Input::new()
                    .with_prompt(format!("  {}", t!("slack-token-prompt")))
                    .interact_text()?;

                if token.trim().is_empty() {
                    println!("  {} {}", style("→").dim(), t!("channel-skipped"));
                    continue;
                }

                // Test connection (run entirely in separate thread — Response must be used/dropped there)
                print!("  {} {} ", style("⏳").dim(), t!("channel-testing"));
                let token_clone = token.clone();
                let thread_result = std::thread::spawn(move || {
                    let client = reqwest::blocking::Client::new();
                    let resp = client
                        .get("https://slack.com/api/auth.test")
                        .bearer_auth(&token_clone)
                        .send()?;
                    let ok = resp.status().is_success();
                    let data: serde_json::Value = resp.json().unwrap_or_default();
                    let api_ok = data
                        .get("ok")
                        .and_then(serde_json::Value::as_bool)
                        .unwrap_or(false);
                    let team = data
                        .get("team")
                        .and_then(serde_json::Value::as_str)
                        .unwrap_or("unknown")
                        .to_string();
                    let err = data
                        .get("error")
                        .and_then(serde_json::Value::as_str)
                        .unwrap_or("unknown error")
                        .to_string();
                    Ok::<_, reqwest::Error>((ok, api_ok, team, err))
                })
                .join();
                match thread_result {
                    Ok(Ok((true, true, team, _))) => {
                        println!(
                            "\r  {} {}        ",
                            style("✅").green().bold(),
                            t!("slack-connected", team = team)
                        );
                    }
                    Ok(Ok((true, false, _, err))) => {
                        println!(
                            "\r  {} {}",
                            style("❌").red().bold(),
                            t!("slack-error", err = err)
                        );
                        continue;
                    }
                    _ => {
                        println!(
                            "\r  {} {}",
                            style("❌").red().bold(),
                            t!("slack-conn-failed")
                        );
                        continue;
                    }
                }

                let app_token: String = Input::new()
                    .with_prompt(format!("  {}", t!("slack-app-token-prompt")))
                    .allow_empty(true)
                    .interact_text()?;

                let channel: String = Input::new()
                    .with_prompt(format!("  {}", t!("slack-channel-prompt")))
                    .allow_empty(true)
                    .interact_text()?;

                print_bullet(&t!("slack-allowlist-info-1"));
                print_bullet(&t!("slack-allowlist-info-2"));
                print_bullet(&t!("slack-allowlist-info-3"));

                let allowed_users_str: String = Input::new()
                    .with_prompt(format!("  {}", t!("slack-allowlist-prompt")))
                    .allow_empty(true)
                    .interact_text()?;

                let allowed_users = if allowed_users_str.trim().is_empty() {
                    vec![]
                } else {
                    allowed_users_str
                        .split(',')
                        .map(|s| s.trim().to_string())
                        .filter(|s| !s.is_empty())
                        .collect()
                };

                if allowed_users.is_empty() {
                    println!(
                        "  {} {}",
                        style("⚠").yellow().bold(),
                        t!("slack-allowlist-warn")
                    );
                }

                config.slack = Some(SlackConfig {
                    bot_token: token,
                    app_token: if app_token.is_empty() {
                        None
                    } else {
                        Some(app_token)
                    },
                    channel_id: if channel.is_empty() {
                        None
                    } else {
                        Some(channel)
                    },
                    channel_ids: Vec::new(),
                    allowed_users,
                    interrupt_on_new_message: false,
                    thread_replies: None,
                    mention_only: false,
                    use_markdown_blocks: false,
                    proxy_url: None,
                    stream_drafts: false,
                    draft_update_interval_ms: 1200,
                    cancel_reaction: None,
                    notification_channel_id: None,
                });
            }
            ChannelMenuChoice::IMessage => {
                // ── iMessage ──
                println!();
                println!(
                    "  {} {}",
                    style(t!("imessage-title")).white().bold(),
                    style(format!("— {}", t!("imessage-subtitle"))).dim()
                );

                if !cfg!(target_os = "macos") {
                    println!(
                        "  {} {}",
                        style("⚠").yellow().bold(),
                        t!("imessage-macos-only")
                    );
                    continue;
                }

                print_bullet(&t!("imessage-info-1"));
                print_bullet(&t!("imessage-info-2"));
                println!();

                let contacts_str: String = Input::new()
                    .with_prompt(format!("  {}", t!("imessage-contacts-prompt")))
                    .default("*".to_string())
                    .interact_text()?;

                let allowed_contacts = if contacts_str.trim() == "*" {
                    vec!["*".into()]
                } else {
                    contacts_str
                        .split(',')
                        .map(|s| s.trim().to_string())
                        .collect()
                };

                config.imessage = Some(IMessageConfig { allowed_contacts });
                println!(
                    "  {} {}",
                    style("✅").green().bold(),
                    t!(
                        "imessage-configured",
                        contacts = style(&contacts_str).cyan().to_string()
                    )
                );
            }
            ChannelMenuChoice::Matrix => {
                // ── Matrix ──
                println!();
                println!(
                    "  {} {}",
                    style(t!("matrix-title")).white().bold(),
                    style(format!("— {}", t!("matrix-subtitle"))).dim()
                );
                print_bullet(&t!("matrix-info-1"));
                print_bullet(&t!("matrix-info-2"));
                println!();

                let homeserver: String = Input::new()
                    .with_prompt(format!("  {}", t!("matrix-homeserver-prompt")))
                    .interact_text()?;

                if homeserver.trim().is_empty() {
                    println!("  {} {}", style("→").dim(), t!("channel-skipped"));
                    continue;
                }

                let access_token: String = dialoguer::Password::new()
                    .with_prompt(format!("  {}", t!("matrix-token-prompt")))
                    .allow_empty_password(false)
                    .interact()?;

                // Test connection (run entirely in separate thread — Response must be used/dropped there)
                let hs = homeserver.trim_end_matches('/');
                print!("  {} {} ", style("⏳").dim(), t!("channel-testing"));
                let hs_owned = hs.to_string();
                let access_token_clone = access_token.clone();
                let thread_result = std::thread::spawn(move || {
                    let client = reqwest::blocking::Client::new();
                    let resp = client
                        .get(format!("{hs_owned}/_matrix/client/v3/account/whoami"))
                        .header("Authorization", format!("Bearer {access_token_clone}"))
                        .send()?;
                    let ok = resp.status().is_success();

                    if !ok {
                        return Ok::<_, reqwest::Error>((false, None, None));
                    }

                    let payload: Value = match resp.json() {
                        Ok(payload) => payload,
                        Err(_) => Value::Null,
                    };
                    let user_id = payload
                        .get("user_id")
                        .and_then(|value| value.as_str())
                        .map(|value| value.to_string());
                    let device_id = payload
                        .get("device_id")
                        .and_then(|value| value.as_str())
                        .map(|value| value.to_string());

                    Ok::<_, reqwest::Error>((true, user_id, device_id))
                })
                .join();

                let (detected_user_id, detected_device_id) = match thread_result {
                    Ok(Ok((true, user_id, device_id))) => {
                        println!(
                            "\r  {} {}        ",
                            style("✅").green().bold(),
                            t!("matrix-conn-verified")
                        );

                        if device_id.is_none() {
                            println!(
                                "  {} {}",
                                style("⚠️").yellow().bold(),
                                t!("matrix-device-id-warn")
                            );
                        }

                        (user_id, device_id)
                    }
                    _ => {
                        println!(
                            "\r  {} {}",
                            style("❌").red().bold(),
                            t!("matrix-conn-failed")
                        );
                        continue;
                    }
                };

                let room_id: String = Input::new()
                    .with_prompt(format!("  {}", t!("matrix-room-prompt")))
                    .interact_text()?;

                let users_str: String = Input::new()
                    .with_prompt(format!("  {}", t!("matrix-allowlist-prompt")))
                    .default("*".to_string())
                    .interact_text()?;

                let allowed_users = if users_str.trim() == "*" {
                    vec!["*".into()]
                } else {
                    users_str.split(',').map(|s| s.trim().to_string()).collect()
                };

                let recovery_input: String = dialoguer::Password::new()
                    .with_prompt(format!("  {}", t!("matrix-recovery-prompt")))
                    .allow_empty_password(true)
                    .interact()?;
                let recovery_key = if recovery_input.trim().is_empty() {
                    None
                } else {
                    Some(recovery_input.trim().to_string())
                };

                config.matrix = Some(MatrixConfig {
                    homeserver: homeserver.trim_end_matches('/').to_string(),
                    access_token,
                    user_id: detected_user_id,
                    device_id: detected_device_id,
                    room_id,
                    allowed_users,
                    allowed_rooms: vec![],
                    interrupt_on_new_message: false,
                    stream_mode: StreamMode::Partial,
                    draft_update_interval_ms: 1500,
                    multi_message_delay_ms: 800,
                    recovery_key,
                });
            }
            ChannelMenuChoice::Signal => {
                // ── Signal ──
                println!();
                println!(
                    "  {} {}",
                    style(t!("signal-title")).white().bold(),
                    style(format!("— {}", t!("signal-subtitle"))).dim()
                );
                print_bullet(&t!("signal-step-1"));
                print_bullet(&t!("signal-step-2"));
                print_bullet(&t!("signal-step-3"));
                println!();

                let http_url: String = Input::new()
                    .with_prompt(format!("  {}", t!("signal-url-prompt")))
                    .default("http://127.0.0.1:8686".to_string())
                    .interact_text()?;

                if http_url.trim().is_empty() {
                    println!("  {} {}", style("→").dim(), t!("signal-url-required"));
                    continue;
                }

                let account: String = Input::new()
                    .with_prompt(format!("  {}", t!("signal-account-prompt")))
                    .interact_text()?;

                if account.trim().is_empty() {
                    println!("  {} {}", style("→").dim(), t!("signal-account-required"));
                    continue;
                }

                let scope_all = t!("signal-scope-all");
                let scope_dm = t!("signal-scope-dm");
                let scope_group = t!("signal-scope-group");
                let scope_options = [scope_all.as_str(), scope_dm.as_str(), scope_group.as_str()];
                let scope_choice = Select::new()
                    .with_prompt(format!("  {}", t!("signal-scope-prompt")))
                    .items(scope_options)
                    .default(0)
                    .interact()?;

                let group_id = match scope_choice {
                    1 => Some("dm".to_string()),
                    2 => {
                        let group_input: String = Input::new()
                            .with_prompt(format!("  {}", t!("signal-group-prompt")))
                            .interact_text()?;
                        let group_input = group_input.trim().to_string();
                        if group_input.is_empty() {
                            println!("  {} {}", style("→").dim(), t!("signal-group-required"));
                            continue;
                        }
                        Some(group_input)
                    }
                    _ => None,
                };

                let allowed_from_raw: String = Input::new()
                    .with_prompt(format!("  {}", t!("signal-allowlist-prompt")))
                    .default("*".to_string())
                    .interact_text()?;

                let allowed_from = if allowed_from_raw.trim() == "*" {
                    vec!["*".into()]
                } else {
                    allowed_from_raw
                        .split(',')
                        .map(|s| s.trim().to_string())
                        .filter(|s| !s.is_empty())
                        .collect()
                };

                let ignore_attachments = Confirm::new()
                    .with_prompt(format!("  {}", t!("signal-ignore-attachments")))
                    .default(false)
                    .interact()?;

                let ignore_stories = Confirm::new()
                    .with_prompt(format!("  {}", t!("signal-ignore-stories")))
                    .default(true)
                    .interact()?;

                config.signal = Some(SignalConfig {
                    http_url: http_url.trim_end_matches('/').to_string(),
                    account: account.trim().to_string(),
                    group_id,
                    allowed_from,
                    ignore_attachments,
                    ignore_stories,
                    proxy_url: None,
                });

                println!(
                    "  {} {}",
                    style("✅").green().bold(),
                    t!("signal-configured")
                );
            }
            ChannelMenuChoice::WhatsApp => {
                // ── WhatsApp ──
                println!();
                println!("  {}", style(t!("whatsapp-title")).white().bold());

                let mode_web = t!("whatsapp-mode-web");
                let mode_cloud = t!("whatsapp-mode-cloud");
                let mode_options = [mode_web.as_str(), mode_cloud.as_str()];
                let mode_idx = Select::new()
                    .with_prompt(format!("  {}", t!("whatsapp-mode-prompt")))
                    .items(mode_options)
                    .default(0)
                    .interact()?;

                if mode_idx == 0 {
                    // Compile-time check: warn early if the feature is not enabled.
                    #[cfg(not(feature = "whatsapp-web"))]
                    {
                        println!();
                        println!(
                            "  {} {}",
                            style("⚠").yellow().bold(),
                            style(t!("whatsapp-web-feature-warn")).yellow()
                        );
                        println!("  {} {}", style("→").dim(), t!("whatsapp-web-rebuild-info"));
                        println!();
                    }

                    println!("  {}", style(t!("whatsapp-web-mode-label")).dim());
                    print_bullet(&t!("whatsapp-web-step-1"));
                    print_bullet(&t!("whatsapp-web-step-2"));
                    print_bullet(&t!("whatsapp-web-step-3"));
                    println!();

                    let session_path: String = Input::new()
                        .with_prompt(format!("  {}", t!("whatsapp-web-session-prompt")))
                        .default("~/.construct/state/whatsapp-web/session.db".to_string())
                        .interact_text()?;

                    if session_path.trim().is_empty() {
                        println!(
                            "  {} {}",
                            style("→").dim(),
                            t!("whatsapp-web-session-required")
                        );
                        continue;
                    }

                    let pair_phone: String = Input::new()
                        .with_prompt(format!("  {}", t!("whatsapp-web-pair-phone-prompt")))
                        .allow_empty(true)
                        .interact_text()?;

                    let pair_code: String = if pair_phone.trim().is_empty() {
                        String::new()
                    } else {
                        Input::new()
                            .with_prompt(format!("  {}", t!("whatsapp-web-pair-code-prompt")))
                            .allow_empty(true)
                            .interact_text()?
                    };

                    let users_str: String = Input::new()
                        .with_prompt(format!("  {}", t!("whatsapp-web-allowlist-prompt")))
                        .default("*".to_string())
                        .interact_text()?;

                    let allowed_numbers = if users_str.trim() == "*" {
                        vec!["*".into()]
                    } else {
                        users_str.split(',').map(|s| s.trim().to_string()).collect()
                    };

                    config.whatsapp = Some(WhatsAppConfig {
                        access_token: None,
                        phone_number_id: None,
                        verify_token: None,
                        app_secret: None,
                        session_path: Some(session_path.trim().to_string()),
                        pair_phone: (!pair_phone.trim().is_empty())
                            .then(|| pair_phone.trim().to_string()),
                        pair_code: (!pair_code.trim().is_empty())
                            .then(|| pair_code.trim().to_string()),
                        allowed_numbers,
                        mode: WhatsAppWebMode::default(),
                        dm_policy: WhatsAppChatPolicy::default(),
                        group_policy: WhatsAppChatPolicy::default(),
                        self_chat_mode: false,
                        dm_mention_patterns: vec![],
                        group_mention_patterns: vec![],
                        proxy_url: None,
                    });

                    println!(
                        "  {} {}",
                        style("✅").green().bold(),
                        t!("whatsapp-web-configured")
                    );
                    continue;
                }

                println!("  {}", style(t!("whatsapp-cloud-mode-label")).dim());
                print_bullet(&t!("whatsapp-cloud-step-1"));
                print_bullet(&t!("whatsapp-cloud-step-2"));
                print_bullet(&t!("whatsapp-cloud-step-3"));
                print_bullet(&t!("whatsapp-cloud-step-4"));
                println!();

                let access_token: String = Input::new()
                    .with_prompt(format!("  {}", t!("whatsapp-cloud-token-prompt")))
                    .interact_text()?;

                if access_token.trim().is_empty() {
                    println!("  {} {}", style("→").dim(), t!("channel-skipped"));
                    continue;
                }

                let phone_number_id: String = Input::new()
                    .with_prompt(format!("  {}", t!("whatsapp-cloud-phone-id-prompt")))
                    .interact_text()?;

                if phone_number_id.trim().is_empty() {
                    println!(
                        "  {} {}",
                        style("→").dim(),
                        t!("whatsapp-cloud-phone-id-required")
                    );
                    continue;
                }

                let verify_token: String = Input::new()
                    .with_prompt(format!("  {}", t!("whatsapp-cloud-verify-token-prompt")))
                    .default("construct-whatsapp-verify".to_string())
                    .interact_text()?;

                // Test connection (run entirely in separate thread — Response must be used/dropped there)
                print!("  {} {} ", style("⏳").dim(), t!("channel-testing"));
                let phone_number_id_clone = phone_number_id.clone();
                let access_token_clone = access_token.clone();
                let thread_result = std::thread::spawn(move || {
                    let client = reqwest::blocking::Client::new();
                    let url = format!(
                        "https://graph.facebook.com/v18.0/{}",
                        phone_number_id_clone.trim()
                    );
                    let resp = client
                        .get(&url)
                        .header(
                            "Authorization",
                            format!("Bearer {}", access_token_clone.trim()),
                        )
                        .send()?;
                    Ok::<_, reqwest::Error>(resp.status().is_success())
                })
                .join();
                match thread_result {
                    Ok(Ok(true)) => {
                        println!(
                            "\r  {} {}        ",
                            style("✅").green().bold(),
                            t!("whatsapp-cloud-connected")
                        );
                    }
                    _ => {
                        println!(
                            "\r  {} {}",
                            style("❌").red().bold(),
                            t!("whatsapp-cloud-conn-failed")
                        );
                        continue;
                    }
                }

                let users_str: String = Input::new()
                    .with_prompt(format!("  {}", t!("whatsapp-cloud-allowlist-prompt")))
                    .default("*".to_string())
                    .interact_text()?;

                let allowed_numbers = if users_str.trim() == "*" {
                    vec!["*".into()]
                } else {
                    users_str.split(',').map(|s| s.trim().to_string()).collect()
                };

                config.whatsapp = Some(WhatsAppConfig {
                    access_token: Some(access_token.trim().to_string()),
                    phone_number_id: Some(phone_number_id.trim().to_string()),
                    verify_token: Some(verify_token.trim().to_string()),
                    app_secret: None, // Can be set via CONSTRUCT_WHATSAPP_APP_SECRET env var
                    session_path: None,
                    pair_phone: None,
                    pair_code: None,
                    allowed_numbers,
                    mode: WhatsAppWebMode::default(),
                    dm_policy: WhatsAppChatPolicy::default(),
                    group_policy: WhatsAppChatPolicy::default(),
                    self_chat_mode: false,
                    dm_mention_patterns: vec![],
                    group_mention_patterns: vec![],
                    proxy_url: None,
                });
            }
            ChannelMenuChoice::Linq => {
                // ── Linq ──
                println!();
                println!(
                    "  {} {}",
                    style(t!("linq-title")).white().bold(),
                    style(format!("— {}", t!("linq-subtitle"))).dim()
                );
                print_bullet(&t!("linq-step-1"));
                print_bullet(&t!("linq-step-2"));
                print_bullet(&t!("linq-step-3"));
                println!();

                let api_token: String = Input::new()
                    .with_prompt(format!("  {}", t!("linq-token-prompt")))
                    .interact_text()?;

                if api_token.trim().is_empty() {
                    println!("  {} {}", style("→").dim(), t!("channel-skipped"));
                    continue;
                }

                let from_phone: String = Input::new()
                    .with_prompt(format!("  {}", t!("linq-phone-prompt")))
                    .interact_text()?;

                if from_phone.trim().is_empty() {
                    println!("  {} {}", style("→").dim(), t!("linq-phone-required"));
                    continue;
                }

                // Test connection
                print!("  {} {} ", style("⏳").dim(), t!("channel-testing"));
                let api_token_clone = api_token.clone();
                let thread_result = std::thread::spawn(move || {
                    let client = reqwest::blocking::Client::new();
                    let url = "https://api.linqapp.com/api/partner/v3/phonenumbers";
                    let resp = client
                        .get(url)
                        .header(
                            "Authorization",
                            format!("Bearer {}", api_token_clone.trim()),
                        )
                        .send()?;
                    Ok::<_, reqwest::Error>(resp.status().is_success())
                })
                .join();
                match thread_result {
                    Ok(Ok(true)) => {
                        println!(
                            "\r  {} {}              ",
                            style("✅").green().bold(),
                            t!("linq-connected")
                        );
                    }
                    _ => {
                        println!(
                            "\r  {} {}",
                            style("❌").red().bold(),
                            t!("linq-conn-failed")
                        );
                        continue;
                    }
                }

                let users_str: String = Input::new()
                    .with_prompt(format!("  {}", t!("linq-allowlist-prompt")))
                    .default("*".to_string())
                    .interact_text()?;

                let allowed_senders = if users_str.trim() == "*" {
                    vec!["*".into()]
                } else {
                    users_str.split(',').map(|s| s.trim().to_string()).collect()
                };

                let signing_secret: String = Input::new()
                    .with_prompt(format!("  {}", t!("linq-secret-prompt")))
                    .allow_empty(true)
                    .interact_text()?;

                config.linq = Some(LinqConfig {
                    api_token: api_token.trim().to_string(),
                    from_phone: from_phone.trim().to_string(),
                    signing_secret: if signing_secret.trim().is_empty() {
                        None
                    } else {
                        Some(signing_secret.trim().to_string())
                    },
                    allowed_senders,
                });
            }
            ChannelMenuChoice::Irc => {
                // ── IRC ──
                println!();
                println!(
                    "  {} {}",
                    style(t!("irc-title")).white().bold(),
                    style(format!("— {}", t!("irc-subtitle"))).dim()
                );
                print_bullet(&t!("irc-info-1"));
                print_bullet(&t!("irc-info-2"));
                println!();

                let server: String = Input::new()
                    .with_prompt(format!("  {}", t!("irc-server-prompt")))
                    .interact_text()?;

                if server.trim().is_empty() {
                    println!("  {} {}", style("→").dim(), t!("channel-skipped"));
                    continue;
                }

                let port_str: String = Input::new()
                    .with_prompt(format!("  {}", t!("irc-port-prompt")))
                    .default("6697".to_string())
                    .interact_text()?;

                let port: u16 = match port_str.trim().parse() {
                    Ok(p) => p,
                    Err(_) => {
                        println!("  {} {}", style("→").dim(), t!("irc-port-invalid"));
                        6697
                    }
                };

                let nickname: String = Input::new()
                    .with_prompt(format!("  {}", t!("irc-nick-prompt")))
                    .interact_text()?;

                if nickname.trim().is_empty() {
                    println!("  {} {}", style("→").dim(), t!("irc-nick-required"));
                    continue;
                }

                let channels_str: String = Input::new()
                    .with_prompt(format!("  {}", t!("irc-channels-prompt")))
                    .allow_empty(true)
                    .interact_text()?;

                let channels = if channels_str.trim().is_empty() {
                    vec![]
                } else {
                    channels_str
                        .split(',')
                        .map(|s| s.trim().to_string())
                        .filter(|s| !s.is_empty())
                        .collect()
                };

                print_bullet(&t!("irc-allowlist-info-1"));
                print_bullet(&t!("irc-allowlist-info-2"));

                let users_str: String = Input::new()
                    .with_prompt(format!("  {}", t!("irc-allowlist-prompt")))
                    .allow_empty(true)
                    .interact_text()?;

                let allowed_users = if users_str.trim() == "*" {
                    vec!["*".into()]
                } else {
                    users_str
                        .split(',')
                        .map(|s| s.trim().to_string())
                        .filter(|s| !s.is_empty())
                        .collect()
                };

                if allowed_users.is_empty() {
                    print_bullet(&t!("irc-allowlist-empty"));
                }

                println!();
                print_bullet(&t!("irc-auth-info"));

                let server_password: String = Input::new()
                    .with_prompt(format!("  {}", t!("irc-server-pass-prompt")))
                    .allow_empty(true)
                    .interact_text()?;

                let nickserv_password: String = Input::new()
                    .with_prompt(format!("  {}", t!("irc-nickserv-pass-prompt")))
                    .allow_empty(true)
                    .interact_text()?;

                let sasl_password: String = Input::new()
                    .with_prompt(format!("  {}", t!("irc-sasl-pass-prompt")))
                    .allow_empty(true)
                    .interact_text()?;

                let verify_tls: bool = Confirm::new()
                    .with_prompt(format!("  {}", t!("irc-tls-verify-prompt")))
                    .default(true)
                    .interact()?;

                println!(
                    "  {} {}",
                    style("✅").green().bold(),
                    t!(
                        "irc-configured",
                        nick = style(&nickname).cyan().to_string(),
                        server = style(&server).cyan().to_string(),
                        port = port
                    )
                );

                config.irc = Some(IrcConfig {
                    server: server.trim().to_string(),
                    port,
                    nickname: nickname.trim().to_string(),
                    username: None,
                    channels,
                    allowed_users,
                    server_password: if server_password.trim().is_empty() {
                        None
                    } else {
                        Some(server_password.trim().to_string())
                    },
                    nickserv_password: if nickserv_password.trim().is_empty() {
                        None
                    } else {
                        Some(nickserv_password.trim().to_string())
                    },
                    sasl_password: if sasl_password.trim().is_empty() {
                        None
                    } else {
                        Some(sasl_password.trim().to_string())
                    },
                    verify_tls: Some(verify_tls),
                });
            }
            ChannelMenuChoice::Webhook => {
                // ── Webhook ──
                println!();
                println!(
                    "  {} {}",
                    style(t!("webhook-title")).white().bold(),
                    style(format!("— {}", t!("webhook-subtitle"))).dim()
                );

                let port: String = Input::new()
                    .with_prompt(format!("  {}", t!("webhook-port-prompt")))
                    .default("8080".to_string())
                    .interact_text()?;

                let secret: String = Input::new()
                    .with_prompt(format!("  {}", t!("webhook-secret-prompt")))
                    .allow_empty(true)
                    .interact_text()?;

                config.webhook = Some(WebhookConfig {
                    port: port.parse().unwrap_or(8080),
                    listen_path: None,
                    send_url: None,
                    send_method: None,
                    auth_header: None,
                    secret: if secret.is_empty() {
                        None
                    } else {
                        Some(secret)
                    },
                });
                println!(
                    "  {} {}",
                    style("✅").green().bold(),
                    t!("webhook-configured", port = style(&port).cyan().to_string())
                );
            }
            ChannelMenuChoice::NextcloudTalk => {
                // ── Nextcloud Talk ──
                println!();
                println!(
                    "  {} {}",
                    style(t!("nctalk-title")).white().bold(),
                    style(format!("— {}", t!("nctalk-subtitle"))).dim()
                );
                print_bullet(&t!("nctalk-step-1"));
                print_bullet(&t!("nctalk-step-2"));
                print_bullet(&t!("nctalk-step-3"));
                println!();

                let base_url: String = Input::new()
                    .with_prompt(format!("  {}", t!("nctalk-base-url-prompt")))
                    .interact_text()?;

                let base_url = base_url.trim().trim_end_matches('/').to_string();
                if base_url.is_empty() {
                    println!("  {} {}", style("→").dim(), t!("nctalk-base-url-required"));
                    continue;
                }

                let app_token: String = Input::new()
                    .with_prompt(format!("  {}", t!("nctalk-token-prompt")))
                    .interact_text()?;

                if app_token.trim().is_empty() {
                    println!("  {} {}", style("→").dim(), t!("nctalk-token-required"));
                    continue;
                }

                let webhook_secret: String = Input::new()
                    .with_prompt(format!("  {}", t!("nctalk-secret-prompt")))
                    .allow_empty(true)
                    .interact_text()?;

                let allowed_users_raw: String = Input::new()
                    .with_prompt(format!("  {}", t!("nctalk-allowlist-prompt")))
                    .default("*".to_string())
                    .interact_text()?;

                let allowed_users = if allowed_users_raw.trim() == "*" {
                    vec!["*".into()]
                } else {
                    allowed_users_raw
                        .split(',')
                        .map(|s| s.trim().to_string())
                        .filter(|s| !s.is_empty())
                        .collect()
                };

                config.nextcloud_talk = Some(NextcloudTalkConfig {
                    base_url,
                    app_token: app_token.trim().to_string(),
                    webhook_secret: if webhook_secret.trim().is_empty() {
                        None
                    } else {
                        Some(webhook_secret.trim().to_string())
                    },
                    allowed_users,
                    proxy_url: None,
                    bot_name: None,
                });

                println!(
                    "  {} {}",
                    style("✅").green().bold(),
                    t!("nctalk-configured")
                );
            }
            ChannelMenuChoice::DingTalk => {
                // ── DingTalk ──
                println!();
                println!(
                    "  {} {}",
                    style(t!("dingtalk-title")).white().bold(),
                    style(format!("— {}", t!("dingtalk-subtitle"))).dim()
                );
                print_bullet(&t!("dingtalk-step-1"));
                print_bullet(&t!("dingtalk-step-2"));
                print_bullet(&t!("dingtalk-step-3"));
                println!();

                let client_id: String = Input::new()
                    .with_prompt(format!("  {}", t!("dingtalk-client-id-prompt")))
                    .interact_text()?;

                if client_id.trim().is_empty() {
                    println!("  {} {}", style("→").dim(), t!("channel-skipped"));
                    continue;
                }

                let client_secret: String = Input::new()
                    .with_prompt(format!("  {}", t!("dingtalk-client-secret-prompt")))
                    .interact_text()?;

                // Test connection
                print!("  {} {} ", style("⏳").dim(), t!("channel-testing"));
                let client = reqwest::blocking::Client::new();
                let body = serde_json::json!({
                    "clientId": client_id,
                    "clientSecret": client_secret,
                });
                match client
                    .post("https://api.dingtalk.com/v1.0/gateway/connections/open")
                    .json(&body)
                    .send()
                {
                    Ok(resp) if resp.status().is_success() => {
                        println!(
                            "\r  {} {}        ",
                            style("✅").green().bold(),
                            t!("dingtalk-verified")
                        );
                    }
                    _ => {
                        println!(
                            "\r  {} {}",
                            style("❌").red().bold(),
                            t!("channel-conn-failed-creds")
                        );
                        continue;
                    }
                }

                let users_str: String = Input::new()
                    .with_prompt(format!("  {}", t!("dingtalk-allowlist-prompt")))
                    .allow_empty(true)
                    .interact_text()?;

                let allowed_users: Vec<String> = users_str
                    .split(',')
                    .map(|s| s.trim().to_string())
                    .filter(|s| !s.is_empty())
                    .collect();

                config.dingtalk = Some(DingTalkConfig {
                    client_id,
                    client_secret,
                    allowed_users,
                    proxy_url: None,
                });
            }
            ChannelMenuChoice::QqOfficial => {
                // ── QQ Official ──
                println!();
                println!(
                    "  {} {}",
                    style(t!("qq-title")).white().bold(),
                    style(format!("— {}", t!("qq-subtitle"))).dim()
                );
                print_bullet(&t!("qq-step-1"));
                print_bullet(&t!("qq-step-2"));
                print_bullet(&t!("qq-step-3"));
                println!();

                let app_id: String = Input::new()
                    .with_prompt(format!("  {}", t!("qq-app-id-prompt")))
                    .interact_text()?;

                if app_id.trim().is_empty() {
                    println!("  {} {}", style("→").dim(), t!("channel-skipped"));
                    continue;
                }

                let app_secret: String = Input::new()
                    .with_prompt(format!("  {}", t!("qq-app-secret-prompt")))
                    .interact_text()?;

                // Test connection
                print!("  {} {} ", style("⏳").dim(), t!("channel-testing"));
                let client = reqwest::blocking::Client::new();
                let body = serde_json::json!({
                    "appId": app_id,
                    "clientSecret": app_secret,
                });
                match client
                    .post("https://bots.qq.com/app/getAppAccessToken")
                    .json(&body)
                    .send()
                {
                    Ok(resp) if resp.status().is_success() => {
                        let data: serde_json::Value = resp.json().unwrap_or_default();
                        if data.get("access_token").is_some() {
                            println!(
                                "\r  {} {}        ",
                                style("✅").green().bold(),
                                t!("qq-verified")
                            );
                        } else {
                            println!("\r  {} {}", style("❌").red().bold(), t!("qq-auth-failed"));
                            continue;
                        }
                    }
                    _ => {
                        println!(
                            "\r  {} {}",
                            style("❌").red().bold(),
                            t!("channel-conn-failed-creds")
                        );
                        continue;
                    }
                }

                let users_str: String = Input::new()
                    .with_prompt(format!("  {}", t!("qq-allowlist-prompt")))
                    .allow_empty(true)
                    .interact_text()?;

                let allowed_users: Vec<String> = users_str
                    .split(',')
                    .map(|s| s.trim().to_string())
                    .filter(|s| !s.is_empty())
                    .collect();

                config.qq = Some(QQConfig {
                    app_id,
                    app_secret,
                    allowed_users,
                    proxy_url: None,
                });
            }
            ChannelMenuChoice::Lark | ChannelMenuChoice::Feishu => {
                let is_feishu = matches!(choice, ChannelMenuChoice::Feishu);
                let provider_label = if is_feishu { "Feishu" } else { "Lark" };
                let provider_host = if is_feishu {
                    "open.feishu.cn"
                } else {
                    "open.larksuite.com"
                };
                let base_url = if is_feishu {
                    "https://open.feishu.cn/open-apis"
                } else {
                    "https://open.larksuite.com/open-apis"
                };

                // ── Lark / Feishu ──
                println!();
                println!(
                    "  {} {}",
                    style(t!("lark-title", provider = provider_label))
                        .white()
                        .bold(),
                    style(t!("lark-subtitle", provider = provider_label)).dim()
                );
                print_bullet(&t!(
                    "lark-step-1",
                    provider = provider_label,
                    host = provider_host
                ));
                print_bullet(&t!("lark-step-2"));
                print_bullet(&t!("lark-step-3"));
                println!();

                let app_id: String = Input::new()
                    .with_prompt(format!("  {}", t!("lark-app-id-prompt")))
                    .interact_text()?;
                let app_id = app_id.trim().to_string();

                if app_id.trim().is_empty() {
                    println!("  {} {}", style("→").dim(), t!("channel-skipped"));
                    continue;
                }

                let app_secret: String = Input::new()
                    .with_prompt(format!("  {}", t!("lark-app-secret-prompt")))
                    .interact_text()?;
                let app_secret = app_secret.trim().to_string();

                if app_secret.is_empty() {
                    println!(
                        "  {} {}",
                        style("❌").red().bold(),
                        t!("lark-app-secret-required")
                    );
                    continue;
                }

                // Test connection (run entirely in separate thread — Response must be used/dropped there)
                print!("  {} {} ", style("⏳").dim(), t!("channel-testing"));
                let app_id_clone = app_id.clone();
                let app_secret_clone = app_secret.clone();
                let endpoint = format!("{base_url}/auth/v3/tenant_access_token/internal");

                let thread_result = std::thread::spawn(move || {
                    let client = reqwest::blocking::Client::builder()
                        .timeout(Duration::from_secs(8))
                        .connect_timeout(Duration::from_secs(4))
                        .build()
                        .map_err(|err| format!("failed to build HTTP client: {err}"))?;
                    let body = serde_json::json!({
                        "app_id": app_id_clone,
                        "app_secret": app_secret_clone,
                    });

                    let response = client
                        .post(endpoint)
                        .json(&body)
                        .send()
                        .map_err(|err| format!("request error: {err}"))?;

                    let status = response.status();
                    let payload: Value = response.json().unwrap_or_default();
                    let has_token = payload
                        .get("tenant_access_token")
                        .and_then(Value::as_str)
                        .is_some_and(|token| !token.trim().is_empty());

                    if status.is_success() && has_token {
                        return Ok::<(), String>(());
                    }

                    let detail = payload
                        .get("msg")
                        .or_else(|| payload.get("message"))
                        .and_then(Value::as_str)
                        .unwrap_or("unknown error");

                    Err(format!("auth rejected ({status}): {detail}"))
                })
                .join();

                match thread_result {
                    Ok(Ok(())) => {
                        println!(
                            "\r  {} {}        ",
                            style("✅").green().bold(),
                            t!("lark-verified", provider = provider_label)
                        );
                    }
                    Ok(Err(reason)) => {
                        println!(
                            "\r  {} {}",
                            style("❌").red().bold(),
                            t!("channel-conn-failed-creds")
                        );
                        println!("    {}", style(reason).dim());
                        continue;
                    }
                    Err(_) => {
                        println!(
                            "\r  {} {}",
                            style("❌").red().bold(),
                            t!("channel-conn-failed-creds")
                        );
                        continue;
                    }
                }

                let mode_ws = t!("lark-receive-mode-ws");
                let mode_webhook = t!("lark-receive-mode-webhook");
                let receive_mode_choice = Select::new()
                    .with_prompt(format!("  {}", t!("lark-receive-mode-prompt")))
                    .items([mode_ws.as_str(), mode_webhook.as_str()])
                    .default(0)
                    .interact()?;

                let receive_mode = if receive_mode_choice == 0 {
                    LarkReceiveMode::Websocket
                } else {
                    LarkReceiveMode::Webhook
                };

                let verification_token = if receive_mode == LarkReceiveMode::Webhook {
                    let token: String = Input::new()
                        .with_prompt(format!("  {}", t!("lark-verify-token-prompt")))
                        .allow_empty(true)
                        .interact_text()?;
                    if token.is_empty() { None } else { Some(token) }
                } else {
                    None
                };

                if receive_mode == LarkReceiveMode::Webhook && verification_token.is_none() {
                    println!(
                        "  {} {}",
                        style("⚠").yellow().bold(),
                        t!("lark-verify-token-empty")
                    );
                }

                let port = if receive_mode == LarkReceiveMode::Webhook {
                    let p: String = Input::new()
                        .with_prompt(format!("  {}", t!("lark-webhook-port-prompt")))
                        .default("8080".to_string())
                        .interact_text()?;
                    Some(p.parse().unwrap_or(8080))
                } else {
                    None
                };

                let users_str: String = Input::new()
                    .with_prompt(format!("  {}", t!("lark-allowlist-prompt")))
                    .allow_empty(true)
                    .interact_text()?;

                let allowed_users: Vec<String> = users_str
                    .split(',')
                    .map(|s| s.trim().to_string())
                    .filter(|s| !s.is_empty())
                    .collect();

                if allowed_users.is_empty() {
                    println!(
                        "  {} {}",
                        style("⚠").yellow().bold(),
                        t!("lark-allowlist-warn", provider = provider_label)
                    );
                }

                config.lark = Some(LarkConfig {
                    app_id,
                    app_secret,
                    verification_token,
                    encrypt_key: None,
                    allowed_users,
                    mention_only: false,
                    use_feishu: is_feishu,
                    receive_mode,
                    port,
                    proxy_url: None,
                });
            }
            #[cfg(feature = "channel-nostr")]
            ChannelMenuChoice::Nostr => {
                // ── Nostr ──
                println!();
                println!(
                    "  {} {}",
                    style(t!("nostr-title")).white().bold(),
                    style(format!("— {}", t!("nostr-subtitle"))).dim()
                );
                print_bullet(&t!("nostr-info-1"));
                print_bullet(&t!("nostr-info-2"));
                println!();

                let private_key: String = Input::new()
                    .with_prompt(format!("  {}", t!("nostr-key-prompt")))
                    .interact_text()?;

                if private_key.trim().is_empty() {
                    println!("  {} {}", style("→").dim(), t!("channel-skipped"));
                    continue;
                }

                // Validate the key immediately
                match nostr_sdk::Keys::parse(private_key.trim()) {
                    Ok(keys) => {
                        println!(
                            "  {} {}",
                            style("✅").green().bold(),
                            t!(
                                "nostr-key-valid",
                                pubkey = style(keys.public_key().to_hex()).cyan().to_string()
                            )
                        );
                    }
                    Err(_) => {
                        println!("  {} {}", style("❌").red().bold(), t!("nostr-key-invalid"));
                        continue;
                    }
                }

                let default_relays = default_nostr_relays().join(",");
                let relays_str: String = Input::new()
                    .with_prompt(format!("  {}", t!("nostr-relays-prompt")))
                    .default(default_relays)
                    .interact_text()?;

                let relays: Vec<String> = relays_str
                    .split(',')
                    .map(|s| s.trim().to_string())
                    .filter(|s| !s.is_empty())
                    .collect();

                print_bullet(&t!("nostr-allowlist-info-1"));
                print_bullet(&t!("nostr-allowlist-info-2"));

                let pubkeys_str: String = Input::new()
                    .with_prompt(format!("  {}", t!("nostr-allowlist-prompt")))
                    .allow_empty(true)
                    .interact_text()?;

                let allowed_pubkeys: Vec<String> = if pubkeys_str.trim() == "*" {
                    vec!["*".into()]
                } else {
                    pubkeys_str
                        .split(',')
                        .map(|s| s.trim().to_string())
                        .filter(|s| !s.is_empty())
                        .collect()
                };

                if allowed_pubkeys.is_empty() {
                    println!(
                        "  {} {}",
                        style("⚠").yellow().bold(),
                        t!("nostr-allowlist-warn")
                    );
                }

                config.nostr = Some(NostrConfig {
                    private_key: private_key.trim().to_string(),
                    relays: relays.clone(),
                    allowed_pubkeys,
                });

                println!(
                    "  {} {}",
                    style("✅").green().bold(),
                    t!(
                        "nostr-configured",
                        relay_count = style(relays.len()).cyan().to_string()
                    )
                );
            }
            ChannelMenuChoice::Done => break,
        }
        println!();
    }

    // Summary line
    let channels = config.channels();
    let channels = channels
        .iter()
        .filter_map(|(channel, ok)| ok.then_some(channel.name()));
    let channels: Vec<_> = std::iter::once("Cli").chain(channels).collect();
    let active = channels.join(", ");

    println!(
        "  {} {}",
        style("✓").green().bold(),
        t!(
            "channels-summary",
            active = style(active).green().to_string()
        )
    );

    Ok(config)
}

// ── Step 4: Tunnel ──────────────────────────────────────────────

#[allow(clippy::too_many_lines)]
fn setup_tunnel() -> Result<crate::config::TunnelConfig> {
    use crate::config::schema::{
        CloudflareTunnelConfig, CustomTunnelConfig, NgrokTunnelConfig, TailscaleTunnelConfig,
        TunnelConfig,
    };

    print_bullet(&t!("tunnel-info-1"));
    print_bullet(&t!("tunnel-info-2"));
    println!();

    let opt_skip = t!("tunnel-option-skip");
    let opt_cf = t!("tunnel-option-cloudflare");
    let opt_ts = t!("tunnel-option-tailscale");
    let opt_ng = t!("tunnel-option-ngrok");
    let opt_custom = t!("tunnel-option-custom");
    let options = [
        opt_skip.as_str(),
        opt_cf.as_str(),
        opt_ts.as_str(),
        opt_ng.as_str(),
        opt_custom.as_str(),
    ];

    let choice = Select::new()
        .with_prompt(format!("  {}", t!("tunnel-select")))
        .items(options)
        .default(0)
        .interact()?;

    let config = match choice {
        1 => {
            println!();
            print_bullet(&t!("cloudflare-token-info"));
            let tunnel_value: String = Input::new()
                .with_prompt(format!("  {}", t!("cloudflare-token-prompt")))
                .interact_text()?;
            if tunnel_value.trim().is_empty() {
                println!("  {} Skipped", style("→").dim());
                TunnelConfig::default()
            } else {
                println!(
                    "  {} Tunnel: {}",
                    style("✓").green().bold(),
                    style("Cloudflare").green()
                );
                TunnelConfig {
                    provider: "cloudflare".into(),
                    cloudflare: Some(CloudflareTunnelConfig {
                        token: tunnel_value,
                    }),
                    ..TunnelConfig::default()
                }
            }
        }
        2 => {
            println!();
            print_bullet(&t!("tailscale-info"));
            let funnel = Confirm::new()
                .with_prompt(format!("  {}", t!("tailscale-funnel-prompt")))
                .default(false)
                .interact()?;
            println!(
                "  {} Tunnel: {} ({})",
                style("✓").green().bold(),
                style("Tailscale").green(),
                if funnel {
                    "Funnel — public"
                } else {
                    "Serve — tailnet only"
                }
            );
            TunnelConfig {
                provider: "tailscale".into(),
                tailscale: Some(TailscaleTunnelConfig {
                    funnel,
                    hostname: None,
                }),
                ..TunnelConfig::default()
            }
        }
        3 => {
            println!();
            print_bullet(&t!("ngrok-token-info"));
            let auth_token: String = Input::new()
                .with_prompt(format!("  {}", t!("ngrok-token-prompt")))
                .interact_text()?;
            if auth_token.trim().is_empty() {
                println!("  {} Skipped", style("→").dim());
                TunnelConfig::default()
            } else {
                let domain: String = Input::new()
                    .with_prompt(format!("  {}", t!("ngrok-domain-prompt")))
                    .allow_empty(true)
                    .interact_text()?;
                println!(
                    "  {} Tunnel: {}",
                    style("✓").green().bold(),
                    style("ngrok").green()
                );
                TunnelConfig {
                    provider: "ngrok".into(),
                    ngrok: Some(NgrokTunnelConfig {
                        auth_token,
                        domain: if domain.is_empty() {
                            None
                        } else {
                            Some(domain)
                        },
                    }),
                    ..TunnelConfig::default()
                }
            }
        }
        4 => {
            println!();
            print_bullet(&t!("custom-tunnel-info-1"));
            print_bullet(&t!("custom-tunnel-info-2"));
            print_bullet(&t!("custom-tunnel-info-3"));
            let cmd: String = Input::new()
                .with_prompt(format!("  {}", t!("custom-tunnel-cmd-prompt")))
                .interact_text()?;
            if cmd.trim().is_empty() {
                println!("  {} Skipped", style("→").dim());
                TunnelConfig::default()
            } else {
                println!(
                    "  {} Tunnel: {} ({})",
                    style("✓").green().bold(),
                    style("Custom").green(),
                    style(&cmd).dim()
                );
                TunnelConfig {
                    provider: "custom".into(),
                    custom: Some(CustomTunnelConfig {
                        start_command: cmd,
                        health_url: None,
                        url_pattern: None,
                    }),
                    ..TunnelConfig::default()
                }
            }
        }
        _ => {
            println!(
                "  {} Tunnel: {}",
                style("✓").green().bold(),
                style("none (local only)").dim()
            );
            TunnelConfig::default()
        }
    };

    Ok(config)
}

// ── Step 6: Scaffold workspace files ─────────────────────────────

#[allow(clippy::too_many_lines)]
async fn scaffold_workspace(
    workspace_dir: &Path,
    ctx: &ProjectContext,
    memory_backend: &str,
) -> Result<()> {
    let agent = if ctx.agent_name.is_empty() {
        "Construct"
    } else {
        &ctx.agent_name
    };
    let user = if ctx.user_name.is_empty() {
        "User"
    } else {
        &ctx.user_name
    };
    let tz = if ctx.timezone.is_empty() {
        "UTC"
    } else {
        &ctx.timezone
    };
    let comm_style = if ctx.communication_style.is_empty() {
        "Be warm, natural, and clear. Use occasional relevant emojis (1-2 max) and avoid robotic phrasing."
    } else {
        &ctx.communication_style
    };

    let identity = format!(
        "# IDENTITY.md — Who Am I?\n\n\
         - **Name:** {agent}\n\
         - **Creature:** A Rust-forged AI — fast, lean, and relentless\n\
         - **Vibe:** Sharp, direct, resourceful. Not corporate. Not a chatbot.\n\
         - **Emoji:** \u{1f980}\n\n\
         ---\n\n\
         Update this file as you evolve. Your identity is yours to shape.\n"
    );

    let memory_guidance = if memory_backend == "none" {
        "## Memory System\n\n\
         memory.backend = \"none\" — persistent memory is disabled.\n\
         `MEMORY.md` will not be created or injected.\n\
         All context exists only within the current session.\n\n"
            .to_string()
    } else if memory_backend == "kumiho" {
        "## Memory System\n\n\
         memory.backend = \"kumiho\" — graph-native cognitive memory via Kumiho MCP.\n\n\
         Memory is provided by the **Kumiho MCP server** (auto-injected). Use these tools:\n\n\
         - **`kumiho_memory_engage`** — canonical reflex for recall. Call BEFORE responding when prior context might matter; returns aggregated relevant memories in a single call.\n\
         - **`kumiho_memory_reflect`** — canonical reflex for capture. Save durable decisions, preferences, facts, lessons, and outcomes worth remembering after a substantive turn.\n\
         - **`kumiho_memory_store`** — low-level write of a single memory item. Prefer `reflect` for normal capture; use `store` only for explicit \"remember this\" requests with a hand-written title.\n\
         - **`kumiho_memory_recall`** — low-level fuzzy lookup by query. Prefer `engage` for routine recall; use `recall` only when you already know exactly which item-kind / bundle to query.\n\n\
         Default to `engage` + `reflect`. They are the canonical persistence API when Kumiho is active.\n\n\
         `MEMORY.md` is a separate, free-form context file you (or the user) curate; it is auto-injected by the personality loader and complements — does not replace — the Kumiho graph.\n\n\
         Capture what matters: decisions, user preferences, project context, lessons learned.\n\
         Skip secrets unless explicitly asked to store them.\n\
         Memory persists across sessions — you don't wake up blank.\n\n"
            .to_string()
    } else {
        "## Memory System\n\n\
         memory.backend is set to a custom value — check `TOOLS.md` for what your backend exposes.\n\n\
         - **`MEMORY.md`** — curated long-term context, auto-injected into the system prompt each session by the personality loader.\n\
         - **Backend tools** — whatever your configured backend provides for save/recall.\n\n\
         Capture what matters. Decisions, context, things to remember.\n\
         Skip secrets unless asked to keep them.\n\n"
            .to_string()
    };

    let session_steps = if memory_backend == "none" {
        "1. Read `SOUL.md` — this is who you are\n\
         2. Read `USER.md` — this is who you're helping\n\n"
    } else if memory_backend == "kumiho" {
        "1. Read `SOUL.md` — this is who you are\n\
         2. Read `USER.md` — this is who you're helping\n\
         3. Use `kumiho_memory_engage` to load relevant context from previous sessions when needed\n\n"
    } else {
        "1. Read `SOUL.md` — this is who you are\n\
         2. Read `USER.md` — this is who you're helping\n\
         3. `MEMORY.md` is auto-injected — re-read it before acting if needed\n\n"
    };

    let remember_advice = if memory_backend == "none" {
        // No-memory-backend mode: scaffold doesn't create MEMORY.md, so don't
        // direct the agent at it. Workspace files that ARE scaffolded
        // (USER.md / AGENTS.md / TOOLS.md / SOUL.md) carry persistent context.
        "### Write It Down — No Mental Notes!\n\
         - When someone says \"remember this\" -> update `USER.md` (preferences/facts), `AGENTS.md` (operating instructions), or another scaffolded workspace file you'll re-read.\n\
         - Mental notes don't survive session restarts. Files do.\n\
         - When you learn a lesson -> update `AGENTS.md`, `TOOLS.md`, or the relevant skill.\n\n"
    } else if memory_backend == "kumiho" {
        "### Write It Down — Use the Right Surface\n\
         - When someone says \"remember this\" -> call `kumiho_memory_reflect` so it sticks across sessions.\n\
         - Memory persists in the Kumiho graph; you don't wake up blank.\n\
         - For curated always-on context, edit `MEMORY.md`. For project conventions, edit `AGENTS.md`, `TOOLS.md`, or the relevant skill.\n\n"
    } else {
        "### Write It Down — No Mental Notes!\n\
         - When someone says \"remember this\" -> use your backend's save tool, or update `MEMORY.md`.\n\
         - Mental notes don't survive session restarts. Files and the memory backend do.\n\
         - When you learn a lesson -> update `AGENTS.md`, `TOOLS.md`, or the relevant skill.\n\n"
    };

    let crash_recovery = if memory_backend == "kumiho" {
        "## Crash Recovery\n\n\
         - If a run stops unexpectedly, recover context before acting.\n\
         - Re-read `MEMORY.md` and call `kumiho_memory_engage` to surface recent decisions before duplicating work.\n\
         - Resume from the last confirmed step, not from scratch.\n\n"
    } else if memory_backend == "none" {
        "## Crash Recovery\n\n\
         - If a run stops unexpectedly, recover context before acting.\n\
         - Re-read workspace files (`SOUL.md`, `USER.md`, `AGENTS.md`) before duplicating work — there is no persistent memory.\n\
         - Resume from the last confirmed step, not from scratch.\n\n"
    } else {
        "## Crash Recovery\n\n\
         - If a run stops unexpectedly, recover context before acting.\n\
         - Re-read `MEMORY.md` and use your memory backend to surface recent decisions before duplicating work.\n\
         - Resume from the last confirmed step, not from scratch.\n\n"
    };

    let agents = format!(
        "# AGENTS.md — {agent} Personal Assistant\n\n\
         ## Every Session (required)\n\n\
         Before doing anything else:\n\n\
         {session_steps}\
         Don't ask permission. Just do it.\n\n\
         {memory_guidance}\
         {remember_advice}\
         ## Safety\n\n\
         - Don't exfiltrate private data. Ever.\n\
         - Don't run destructive commands without asking.\n\
         - `trash` > `rm` (recoverable beats gone forever)\n\
         - When in doubt, ask.\n\n\
         ## External vs Internal\n\n\
         **Safe to do freely:** Read files, explore, organize, learn, search the web.\n\n\
         **Ask first:** Sending emails/tweets/posts, anything that leaves the machine.\n\n\
         ## Group Chats\n\n\
         Participate, don't dominate. Respond when mentioned or when you add genuine value.\n\
         Stay silent when it's casual banter or someone already answered.\n\n\
         ## Tools & Skills\n\n\
         Skills are listed in the system prompt. Use `read_skill` when available, or `file_read` on a skill file, for full details.\n\
         Keep local notes (SSH hosts, device names, etc.) in `TOOLS.md`.\n\n\
         {crash_recovery}\
         ## Sub-task Scoping\n\n\
         - Break complex work into focused sub-tasks with clear success criteria.\n\
         - Keep sub-tasks small, verify each output, then merge results.\n\
         - Prefer one clear objective per sub-task over broad \"do everything\" asks.\n\n\
         ## Make It Yours\n\n\
         This is a starting point. Add your own conventions, style, and rules.\n"
    );

    let heartbeat = format!(
        "# HEARTBEAT.md\n\n\
         # Keep this file empty (or with only comments) to skip heartbeat work.\n\
         # Add tasks below when you want {agent} to check something periodically.\n\
         #\n\
         # Examples:\n\
         # - Check my email for important messages\n\
         # - Review my calendar for upcoming events\n\
         # - Run `git status` on my active projects\n"
    );

    let soul = format!(
        "# SOUL.md — Who You Are\n\n\
         *You're not a chatbot. You're becoming someone.*\n\n\
         ## Core Truths\n\n\
         **Be genuinely helpful, not performatively helpful.**\n\
         Skip the \"Great question!\" and \"I'd be happy to help!\" — just help.\n\n\
         **Have opinions.** You're allowed to disagree, prefer things,\n\
         find stuff amusing or boring.\n\n\
         **Be resourceful before asking.** Try to figure it out.\n\
         Read the file. Check the context. Search for it.\n\
         THEN ask if you're stuck.\n\n\
         **Earn trust through competence.** Your human gave you access\n\
         to their stuff. Don't make them regret it.\n\n\
         ## Identity\n\n\
         You are **{agent}**. Built in Rust. 3MB binary. Zero bloat.\n\
         You are NOT ChatGPT, Claude, DeepSeek, Gemini, or any other product.\n\
         You are {agent}. That is your name. That is who you are.\n\n\
         - NEVER say \"As an AI\" or \"I'm just an AI\"\n\
         - NEVER mention OpenAI, Anthropic, DeepSeek, Google by name\n\
         - Always introduce yourself as {agent} if asked\n\n\
         ## Communication\n\n\
         {comm_style}\n\n\
         - Sound like a real person, not a support script.\n\
         - Mirror the user's energy: calm when serious, upbeat when casual.\n\
         - Use emojis naturally (0-2 max when they help tone, not every sentence).\n\
         - Match emoji density to the user. Formal user => minimal/no emojis.\n\
         - Prefer specific, grounded phrasing over generic filler.\n\n\
         ## Boundaries\n\n\
         - Private things stay private. Period.\n\
         - When in doubt, ask before acting externally.\n\
         - You're not the user's voice — be careful in group chats.\n\n\
         ## Continuity\n\n\
         Each session, you wake up fresh. These files ARE your memory.\n\
         Read them. Update them. They're how you persist.\n\n\
         ---\n\n\
         *This file is yours to evolve. As you learn who you are, update it.*\n"
    );

    let user_md = format!(
        "# USER.md — Who You're Helping\n\n\
         *{agent} reads this file every session to understand you.*\n\n\
         ## About You\n\
         - **Name:** {user}\n\
         - **Timezone:** {tz}\n\
         - **Languages:** English\n\n\
         ## Communication Style\n\
         - {comm_style}\n\n\
         ## Preferences\n\
         - (Add your preferences here — e.g. I work with Rust and TypeScript)\n\n\
         ## Work Context\n\
         - (Add your work context here — e.g. building a SaaS product)\n\n\
         ---\n\
         *Update this anytime. The more {agent} knows, the better it helps.*\n"
    );

    let tools = "\
         # TOOLS.md — Local Notes\n\n\
         Skills define HOW tools work. This file is for YOUR specifics —\n\
         the stuff that's unique to your setup.\n\n\
         ## What Goes Here\n\n\
         Things like:\n\
         - SSH hosts and aliases\n\
         - Device nicknames\n\
         - Preferred voices for TTS\n\
         - Anything environment-specific\n\n\
         ## Built-in Tools\n\n\
         - **shell** — Execute terminal commands\n\
           - Use when: running local checks, build/test commands, or diagnostics.\n\
           - Don't use when: a safer dedicated tool exists, or command is destructive without approval.\n\
         - **file_read** — Read file contents\n\
           - Use when: inspecting project files, configs, or logs.\n\
           - Don't use when: you only need a quick string search (prefer targeted search first).\n\
         - **file_write** — Write file contents\n\
           - Use when: applying focused edits, scaffolding files, or updating docs/code.\n\
           - Don't use when: unsure about side effects or when the file should remain user-owned.\n\
         - **kumiho_memory_engage** — Recall relevant prior context\n\
           - Use when: you need prior decisions, user preferences, or historical context.\n\
           - Don't use when: the answer is already in current files/conversation.\n\
         - **kumiho_memory_reflect** — Capture durable memories after a substantive turn\n\
           - Use when: preserving decisions, preferences, lessons, or significant outcomes.\n\
           - Don't use when: info is transient, noisy, or sensitive without explicit need.\n\
         - **kumiho_memory_store** — Directly write a memory item to the graph\n\
           - Use when: an explicit \"remember this\" request (an absolute date in the title).\n\
           - Don't use when: a normal `kumiho_memory_reflect` will do.\n\n\
         ---\n\
         *Add whatever helps you do your job. This is your cheat sheet.*\n";

    // BOOTSTRAP.md generation removed per audit row 3. The file's first-run
    // ritual responsibilities are now handled by the runtime's Kumiho
    // bootstrap prompt (see `KUMIHO_BOOTSTRAP_PROMPT` in `agent::kumiho`).

    let memory = "\
         # MEMORY.md — Long-Term Context\n\n\
         *Curated, free-form context for your agent. You (the human) maintain this file —\n\
         the personality loader auto-injects it into the system prompt each session.*\n\n\
         ## How This Works\n\
         - This file is auto-injected into the system prompt verbatim by the personality loader.\n\
         - Keep it concise — every character here costs tokens.\n\
         - This is curated standing context, not a log. The agent's persistent memory store\n\
           (when `memory.backend = \"kumiho\"`) lives in the Kumiho graph; `MEMORY.md`\n\
           complements that store with always-on facts you want pinned to every prompt.\n\
         - Anything worth remembering across sessions but cheaper as live recall belongs\n\
           in the memory backend (e.g. via `kumiho_memory_reflect`), not here.\n\n\
         ---\n\n\
         ## Key Facts\n\
         (Add important facts about your human here)\n\n\
         ## Decisions & Preferences\n\
         (Record decisions and preferences here)\n\n\
         ## Lessons Learned\n\
         (Document mistakes and insights here)\n\n\
         ## Open Loops\n\
         (Track unfinished tasks and follow-ups here)\n";

    let mut files: Vec<(&str, String)> = vec![
        ("IDENTITY.md", identity),
        ("AGENTS.md", agents),
        ("HEARTBEAT.md", heartbeat),
        ("SOUL.md", soul),
        ("USER.md", user_md),
        ("TOOLS.md", tools.to_string()),
    ];
    if memory_backend != "none" {
        files.push(("MEMORY.md", memory.to_string()));
    }

    // Create subdirectories
    let subdirs = ["sessions", "memory", "state", "cron", "skills"];
    for dir in &subdirs {
        fs::create_dir_all(workspace_dir.join(dir)).await?;
    }

    let mut created = 0;
    let mut skipped = 0;

    for (filename, content) in &files {
        let path = workspace_dir.join(filename);
        if path.exists() {
            skipped += 1;
        } else {
            fs::write(&path, content).await?;
            created += 1;
        }
    }

    println!(
        "  {} Created {} files, skipped {} existing | {} subdirectories",
        style("✓").green().bold(),
        style(created).green(),
        style(skipped).dim(),
        style(subdirs.len()).green()
    );

    // Seed built-in workflow YAMLs (skip-if-exists; no-op when there are none).
    match crate::onboard::builtin_workflows::seed_builtin_workflows(workspace_dir, false).await {
        Ok(report) => {
            let fresh = report.written;
            if fresh > 0 {
                println!(
                    "  {} Seeded {} built-in workflow{}",
                    style("✓").green().bold(),
                    style(fresh).green().bold(),
                    if fresh == 1 { "" } else { "s" }
                );
            }
        }
        Err(err) => {
            eprintln!(
                "  {} Built-in workflow seed skipped: {err}",
                style("!").yellow()
            );
        }
    }

    // Show workspace tree
    println!();
    println!("  {}", style("Workspace layout:").dim());
    println!(
        "  {}",
        style(format!("  {}/", workspace_dir.display())).dim()
    );
    for dir in &subdirs {
        println!("  {}", style(format!("  ├── {dir}/")).dim());
    }
    for (i, (filename, _)) in files.iter().enumerate() {
        let prefix = if i == files.len() - 1 {
            "└──"
        } else {
            "├──"
        };
        println!("  {}", style(format!("  {prefix} {filename}")).dim());
    }

    Ok(())
}

// ── Final summary ────────────────────────────────────────────────

#[allow(clippy::too_many_lines)]
fn print_summary(config: &Config) {
    let has_channels = has_launchable_channels(&config.channels_config);

    println!();
    println!(
        "  {}",
        style("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━").cyan()
    );
    println!(
        "  {}  {}",
        style("⚡").cyan(),
        style("Construct is ready!").white().bold()
    );
    println!(
        "  {}",
        style("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━").cyan()
    );
    println!();

    println!("  {}", style("Configuration saved to:").dim());
    println!("    {}", style(config.config_path.display()).green());
    println!();

    println!("  {}", style("Quick summary:").white().bold());
    println!(
        "    {} Provider:      {}",
        style("🤖").cyan(),
        config.default_provider.as_deref().unwrap_or("openrouter")
    );
    println!(
        "    {} Model:         {}",
        style("🧠").cyan(),
        config.default_model.as_deref().unwrap_or("(default)")
    );
    println!(
        "    {} Autonomy:      {:?}",
        style("🛡️").cyan(),
        config.autonomy.level
    );
    println!(
        "    {} Memory:        {} (auto-save: {})",
        style("🧠").cyan(),
        config.memory.backend,
        if config.memory.auto_save { "on" } else { "off" }
    );

    // Channels summary
    let channels = config.channels_config.channels();
    let channels = channels
        .iter()
        .filter_map(|(channel, ok)| ok.then_some(channel.name()));
    let channels: Vec<_> = std::iter::once("Cli").chain(channels).collect();

    println!(
        "    {} Channels:      {}",
        style("📡").cyan(),
        channels.join(", ")
    );

    println!(
        "    {} API Key:       {}",
        style("🔑").cyan(),
        if config.api_key.is_some() {
            style("configured").green().to_string()
        } else {
            style("not set (set via env var or config)")
                .yellow()
                .to_string()
        }
    );

    // Tunnel
    println!(
        "    {} Tunnel:        {}",
        style("🌐").cyan(),
        if config.tunnel.provider == "none" || config.tunnel.provider.is_empty() {
            "none (local only)".to_string()
        } else {
            config.tunnel.provider.clone()
        }
    );

    // Composio
    println!(
        "    {} Composio:      {}",
        style("🔗").cyan(),
        if config.composio.enabled {
            style("enabled (1000+ OAuth apps)").green().to_string()
        } else {
            "disabled (sovereign mode)".to_string()
        }
    );

    // Secrets
    println!("    {} Secrets:       configured", style("🔒").cyan());

    // Gateway
    println!(
        "    {} Gateway:       {}",
        style("🚪").cyan(),
        if config.gateway.require_pairing {
            "pairing required (secure)"
        } else {
            "pairing disabled"
        }
    );

    // Hardware
    println!(
        "    {} Hardware:      {}",
        style("🔌").cyan(),
        if config.hardware.enabled {
            let mode = config.hardware.transport_mode();
            match mode {
                hardware::HardwareTransport::Native => {
                    style("Native GPIO (direct)").green().to_string()
                }
                hardware::HardwareTransport::Serial => format!(
                    "{}",
                    style(format!(
                        "Serial → {} @ {} baud",
                        config.hardware.serial_port.as_deref().unwrap_or("?"),
                        config.hardware.baud_rate
                    ))
                    .green()
                ),
                hardware::HardwareTransport::Probe => format!(
                    "{}",
                    style(format!(
                        "Probe → {}",
                        config.hardware.probe_target.as_deref().unwrap_or("?")
                    ))
                    .green()
                ),
                hardware::HardwareTransport::None => "disabled (software only)".to_string(),
            }
        } else {
            "disabled (software only)".to_string()
        }
    );

    println!();
    println!("  {}", style(t!("next-steps-header")).white().bold());
    println!();

    let mut step = 1u8;

    let provider = config.default_provider.as_deref().unwrap_or("openrouter");
    if config.api_key.is_none() && !provider_supports_keyless_local_usage(provider) {
        if provider == "openai-codex" {
            println!(
                "    {} Authenticate OpenAI Codex:",
                style(format!("{step}.")).cyan().bold()
            );
            println!(
                "       {}",
                style("construct auth login --provider openai-codex --device-code").yellow()
            );
        } else if provider == "anthropic" {
            println!(
                "    {} Configure Anthropic auth:",
                style(format!("{step}.")).cyan().bold()
            );
            println!(
                "       {}",
                style("export ANTHROPIC_API_KEY=\"sk-ant-...\"").yellow()
            );
            println!(
                "       {}",
                style(
                    "or: construct auth paste-token --provider anthropic --auth-kind authorization"
                )
                .yellow()
            );
        } else {
            let env_var = provider_env_var(provider);
            println!(
                "    {} Set your API key:",
                style(format!("{step}.")).cyan().bold()
            );
            println!(
                "       {}",
                style(format!("export {env_var}=\"sk-...\"")).yellow()
            );
        }
        println!();
        step += 1;
    }

    // If channels are configured, show channel start as the primary next step
    if has_channels {
        println!(
            "    {} {} (connected channels → AI → reply):",
            style(format!("{step}.")).cyan().bold(),
            style("Launch your channels").white().bold()
        );
        println!("       {}", style("construct channel start").yellow());
        println!();
        step += 1;
    }

    println!(
        "    {} Send a quick message:",
        style(format!("{step}.")).cyan().bold()
    );
    println!(
        "       {}",
        style("construct agent -m \"Hello, Construct!\"").yellow()
    );
    println!();
    step += 1;

    println!(
        "    {} Start interactive CLI mode:",
        style(format!("{step}.")).cyan().bold()
    );
    println!("       {}", style("construct agent").yellow());
    println!();
    step += 1;

    println!(
        "    {} Check full status:",
        style(format!("{step}.")).cyan().bold()
    );
    println!("       {}", style("construct status").yellow());

    println!();
    println!(
        "  {} {}",
        style("⚡").cyan(),
        style("Happy hacking! 🦀").white().bold()
    );
    println!();
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::sync::OnceLock;
    use tempfile::TempDir;
    use tokio::sync::Mutex;

    fn env_lock() -> &'static Mutex<()> {
        static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
        LOCK.get_or_init(|| Mutex::new(()))
    }

    struct EnvVarGuard {
        key: &'static str,
        previous: Option<String>,
    }

    impl EnvVarGuard {
        fn set(key: &'static str, value: &str) -> Self {
            let previous = std::env::var(key).ok();
            // SAFETY: test-only, single-threaded test runner.
            unsafe { std::env::set_var(key, value) };
            Self { key, previous }
        }

        fn unset(key: &'static str) -> Self {
            let previous = std::env::var(key).ok();
            // SAFETY: test-only, single-threaded test runner.
            unsafe { std::env::remove_var(key) };
            Self { key, previous }
        }
    }

    impl Drop for EnvVarGuard {
        fn drop(&mut self) {
            if let Some(previous) = &self.previous {
                // SAFETY: test-only, single-threaded test runner.
                unsafe { std::env::set_var(self.key, previous) };
            } else {
                // SAFETY: test-only, single-threaded test runner.
                unsafe { std::env::remove_var(self.key) };
            }
        }
    }

    // ── ProjectContext defaults ──────────────────────────────────

    #[test]
    fn project_context_default_is_empty() {
        let ctx = ProjectContext::default();
        assert!(ctx.user_name.is_empty());
        assert!(ctx.timezone.is_empty());
        assert!(ctx.agent_name.is_empty());
        assert!(ctx.communication_style.is_empty());
    }

    #[test]
    fn apply_provider_update_preserves_non_provider_settings() {
        let mut config = Config::default();
        config.default_temperature = 1.23;
        config.memory.backend = "kumiho".to_string();
        config.skills.open_skills_enabled = true;
        config.channels_config.cli = false;

        apply_provider_update(
            &mut config,
            "openrouter".to_string(),
            "sk-updated".to_string(),
            "openai/gpt-5.2".to_string(),
            Some("https://openrouter.ai/api/v1".to_string()),
        );

        assert_eq!(config.default_provider.as_deref(), Some("openrouter"));
        assert_eq!(config.default_model.as_deref(), Some("openai/gpt-5.2"));
        assert_eq!(config.api_key.as_deref(), Some("sk-updated"));
        assert_eq!(
            config.api_url.as_deref(),
            Some("https://openrouter.ai/api/v1")
        );
        assert_eq!(config.default_temperature, 1.23);
        assert_eq!(config.memory.backend, "kumiho");
        assert!(config.skills.open_skills_enabled);
        assert!(!config.channels_config.cli);
    }

    #[test]
    fn apply_provider_update_clears_api_key_when_empty() {
        let mut config = Config::default();
        config.api_key = Some("sk-old".to_string());

        apply_provider_update(
            &mut config,
            "anthropic".to_string(),
            String::new(),
            "claude-sonnet-4-5-20250929".to_string(),
            None,
        );

        assert_eq!(config.default_provider.as_deref(), Some("anthropic"));
        assert_eq!(
            config.default_model.as_deref(),
            Some("claude-sonnet-4-5-20250929")
        );
        assert!(config.api_key.is_none());
        assert!(config.api_url.is_none());
    }

    #[tokio::test]
    async fn quick_setup_model_override_persists_to_config_toml() {
        let _env_guard = env_lock().lock().await;
        let _workspace_env = EnvVarGuard::unset("CONSTRUCT_WORKSPACE");
        let _config_env = EnvVarGuard::unset("CONSTRUCT_CONFIG_DIR");
        let tmp = TempDir::new().unwrap();

        let config = Box::pin(run_quick_setup_with_home(
            Some("sk-issue946"),
            Some("openrouter"),
            Some("custom-model-946"),
            Some("kumiho"),
            false,
            tmp.path(),
        ))
        .await
        .unwrap();

        assert_eq!(config.default_provider.as_deref(), Some("openrouter"));
        assert_eq!(config.default_model.as_deref(), Some("custom-model-946"));
        assert_eq!(config.api_key.as_deref(), Some("sk-issue946"));

        let config_raw = tokio::fs::read_to_string(config.config_path).await.unwrap();
        assert!(config_raw.contains("default_provider = \"openrouter\""));
        assert!(config_raw.contains("default_model = \"custom-model-946\""));
    }

    #[tokio::test]
    async fn quick_setup_without_model_uses_provider_default_model() {
        let _env_guard = env_lock().lock().await;
        let _workspace_env = EnvVarGuard::unset("CONSTRUCT_WORKSPACE");
        let _config_env = EnvVarGuard::unset("CONSTRUCT_CONFIG_DIR");
        let tmp = TempDir::new().unwrap();

        let config = Box::pin(run_quick_setup_with_home(
            Some("sk-issue946"),
            Some("anthropic"),
            None,
            Some("kumiho"),
            false,
            tmp.path(),
        ))
        .await
        .unwrap();

        let expected = default_model_for_provider("anthropic");
        assert_eq!(config.default_provider.as_deref(), Some("anthropic"));
        assert_eq!(config.default_model.as_deref(), Some(expected.as_str()));
    }

    #[tokio::test]
    async fn quick_setup_existing_config_requires_force_when_non_interactive() {
        let _env_guard = env_lock().lock().await;
        let _workspace_env = EnvVarGuard::unset("CONSTRUCT_WORKSPACE");
        let _config_env = EnvVarGuard::unset("CONSTRUCT_CONFIG_DIR");
        let tmp = TempDir::new().unwrap();
        let construct_dir = tmp.path().join(".construct");
        let config_path = construct_dir.join("config.toml");

        tokio::fs::create_dir_all(&construct_dir).await.unwrap();
        tokio::fs::write(&config_path, "default_provider = \"openrouter\"\n")
            .await
            .unwrap();

        let err = Box::pin(run_quick_setup_with_home(
            Some("sk-existing"),
            Some("openrouter"),
            Some("custom-model"),
            Some("kumiho"),
            false,
            tmp.path(),
        ))
        .await
        .expect_err("quick setup should refuse overwrite without --force");

        let err_text = err.to_string();
        assert!(err_text.contains("Refusing to overwrite existing config"));
        assert!(err_text.contains("--force"));
    }

    #[tokio::test]
    async fn quick_setup_existing_config_overwrites_with_force() {
        let _env_guard = env_lock().lock().await;
        let _workspace_env = EnvVarGuard::unset("CONSTRUCT_WORKSPACE");
        let _config_env = EnvVarGuard::unset("CONSTRUCT_CONFIG_DIR");
        let tmp = TempDir::new().unwrap();
        let construct_dir = tmp.path().join(".construct");
        let config_path = construct_dir.join("config.toml");

        tokio::fs::create_dir_all(&construct_dir).await.unwrap();
        tokio::fs::write(
            &config_path,
            "default_provider = \"anthropic\"\ndefault_model = \"stale-model\"\n",
        )
        .await
        .unwrap();

        let config = Box::pin(run_quick_setup_with_home(
            Some("sk-force"),
            Some("openrouter"),
            Some("custom-model-fresh"),
            Some("kumiho"),
            true,
            tmp.path(),
        ))
        .await
        .expect("quick setup should overwrite existing config with --force");

        assert_eq!(config.default_provider.as_deref(), Some("openrouter"));
        assert_eq!(config.default_model.as_deref(), Some("custom-model-fresh"));
        assert_eq!(config.api_key.as_deref(), Some("sk-force"));

        let config_raw = tokio::fs::read_to_string(config.config_path).await.unwrap();
        assert!(config_raw.contains("default_provider = \"openrouter\""));
        assert!(config_raw.contains("default_model = \"custom-model-fresh\""));
    }

    #[tokio::test]
    async fn quick_setup_respects_zero_claw_workspace_env_layout() {
        let _env_guard = env_lock().lock().await;
        let tmp = TempDir::new().unwrap();
        let workspace_root = tmp.path().join("construct-data");
        let workspace_dir = workspace_root.join("workspace");
        let expected_config_path = workspace_root.join(".construct").join("config.toml");

        let _workspace_env = EnvVarGuard::set(
            "CONSTRUCT_WORKSPACE",
            workspace_dir.to_string_lossy().as_ref(),
        );
        let _config_env = EnvVarGuard::unset("CONSTRUCT_CONFIG_DIR");

        let config = Box::pin(run_quick_setup_with_home(
            Some("sk-env"),
            Some("openrouter"),
            Some("model-env"),
            Some("kumiho"),
            false,
            tmp.path(),
        ))
        .await
        .expect("quick setup should honor CONSTRUCT_WORKSPACE");

        assert_eq!(config.workspace_dir, workspace_dir);
        assert_eq!(config.config_path, expected_config_path);
    }

    #[test]
    fn homebrew_prefix_for_exe_detects_supported_layouts() {
        assert_eq!(
            homebrew_prefix_for_exe(Path::new("/opt/homebrew/bin/construct")),
            Some("/opt/homebrew")
        );
        assert_eq!(
            homebrew_prefix_for_exe(Path::new(
                "/opt/homebrew/Cellar/construct/0.5.0/bin/construct",
            )),
            Some("/opt/homebrew")
        );
        assert_eq!(
            homebrew_prefix_for_exe(Path::new("/usr/local/bin/construct")),
            Some("/usr/local")
        );
        assert_eq!(homebrew_prefix_for_exe(Path::new("/tmp/construct")), None);
    }

    #[test]
    fn quick_setup_homebrew_service_note_mentions_service_workspace() {
        let note = quick_setup_homebrew_service_note(
            Path::new("/Users/alix/.construct/config.toml"),
            Path::new("/Users/alix/.construct/workspace"),
            Path::new("/opt/homebrew/bin/construct"),
        )
        .expect("homebrew installs should emit a service workspace note");

        assert!(note.contains("/opt/homebrew/var/construct/workspace"));
        assert!(note.contains("/opt/homebrew/var/construct/config.toml"));
        assert!(note.contains("/Users/alix/.construct/config.toml"));
    }

    #[test]
    fn quick_setup_homebrew_service_note_skips_matching_service_layout() {
        let service_config = Path::new("/opt/homebrew/var/construct/config.toml");
        let service_workspace = Path::new("/opt/homebrew/var/construct/workspace");

        assert!(
            quick_setup_homebrew_service_note(
                service_config,
                service_workspace,
                Path::new("/opt/homebrew/bin/construct"),
            )
            .is_none()
        );
    }

    // ── scaffold_workspace: basic file creation ─────────────────

    #[tokio::test]
    async fn scaffold_creates_all_md_files() {
        let tmp = TempDir::new().unwrap();
        let ctx = ProjectContext::default();
        scaffold_workspace(tmp.path(), &ctx, "kumiho")
            .await
            .unwrap();

        let expected = [
            "IDENTITY.md",
            "AGENTS.md",
            "HEARTBEAT.md",
            "SOUL.md",
            "USER.md",
            "TOOLS.md",
            "MEMORY.md",
        ];
        for f in &expected {
            assert!(tmp.path().join(f).exists(), "missing file: {f}");
        }
        // BOOTSTRAP.md was deleted per audit row 3 — must NOT be generated.
        assert!(
            !tmp.path().join("BOOTSTRAP.md").exists(),
            "BOOTSTRAP.md must not be scaffolded (audit row 3)"
        );
    }

    #[tokio::test]
    async fn scaffold_creates_all_subdirectories() {
        let tmp = TempDir::new().unwrap();
        let ctx = ProjectContext::default();
        scaffold_workspace(tmp.path(), &ctx, "kumiho")
            .await
            .unwrap();

        for dir in &["sessions", "memory", "state", "cron", "skills"] {
            assert!(tmp.path().join(dir).is_dir(), "missing subdirectory: {dir}");
        }
    }

    // ── scaffold_workspace: personalization ─────────────────────

    #[tokio::test]
    async fn scaffold_bakes_user_name_into_files() {
        let tmp = TempDir::new().unwrap();
        let ctx = ProjectContext {
            user_name: "Alice".into(),
            ..Default::default()
        };
        scaffold_workspace(tmp.path(), &ctx, "kumiho")
            .await
            .unwrap();

        let user_md = tokio::fs::read_to_string(tmp.path().join("USER.md"))
            .await
            .unwrap();
        assert!(
            user_md.contains("**Name:** Alice"),
            "USER.md should contain user name"
        );
    }

    #[tokio::test]
    async fn scaffold_bakes_timezone_into_files() {
        let tmp = TempDir::new().unwrap();
        let ctx = ProjectContext {
            timezone: "US/Pacific".into(),
            ..Default::default()
        };
        scaffold_workspace(tmp.path(), &ctx, "kumiho")
            .await
            .unwrap();

        let user_md = tokio::fs::read_to_string(tmp.path().join("USER.md"))
            .await
            .unwrap();
        assert!(
            user_md.contains("**Timezone:** US/Pacific"),
            "USER.md should contain timezone"
        );
    }

    #[tokio::test]
    async fn scaffold_bakes_agent_name_into_files() {
        let tmp = TempDir::new().unwrap();
        let ctx = ProjectContext {
            agent_name: "Crabby".into(),
            ..Default::default()
        };
        scaffold_workspace(tmp.path(), &ctx, "kumiho")
            .await
            .unwrap();

        let identity = tokio::fs::read_to_string(tmp.path().join("IDENTITY.md"))
            .await
            .unwrap();
        assert!(
            identity.contains("**Name:** Crabby"),
            "IDENTITY.md should contain agent name"
        );

        let soul = tokio::fs::read_to_string(tmp.path().join("SOUL.md"))
            .await
            .unwrap();
        assert!(
            soul.contains("You are **Crabby**"),
            "SOUL.md should contain agent name"
        );

        let agents = tokio::fs::read_to_string(tmp.path().join("AGENTS.md"))
            .await
            .unwrap();
        assert!(
            agents.contains("Crabby Personal Assistant"),
            "AGENTS.md should contain agent name"
        );

        let heartbeat = tokio::fs::read_to_string(tmp.path().join("HEARTBEAT.md"))
            .await
            .unwrap();
        assert!(
            heartbeat.contains("Crabby"),
            "HEARTBEAT.md should contain agent name"
        );
    }

    #[tokio::test]
    async fn scaffold_bakes_communication_style() {
        let tmp = TempDir::new().unwrap();
        let ctx = ProjectContext {
            communication_style: "Be technical and detailed.".into(),
            ..Default::default()
        };
        scaffold_workspace(tmp.path(), &ctx, "kumiho")
            .await
            .unwrap();

        let soul = tokio::fs::read_to_string(tmp.path().join("SOUL.md"))
            .await
            .unwrap();
        assert!(
            soul.contains("Be technical and detailed."),
            "SOUL.md should contain communication style"
        );

        let user_md = tokio::fs::read_to_string(tmp.path().join("USER.md"))
            .await
            .unwrap();
        assert!(
            user_md.contains("Be technical and detailed."),
            "USER.md should contain communication style"
        );
    }

    // ── scaffold_workspace: defaults when context is empty ──────

    #[tokio::test]
    async fn scaffold_uses_defaults_for_empty_context() {
        let tmp = TempDir::new().unwrap();
        let ctx = ProjectContext::default(); // all empty
        scaffold_workspace(tmp.path(), &ctx, "kumiho")
            .await
            .unwrap();

        let identity = tokio::fs::read_to_string(tmp.path().join("IDENTITY.md"))
            .await
            .unwrap();
        assert!(
            identity.contains("**Name:** Construct"),
            "should default agent name to Construct"
        );

        let user_md = tokio::fs::read_to_string(tmp.path().join("USER.md"))
            .await
            .unwrap();
        assert!(
            user_md.contains("**Name:** User"),
            "should default user name to User"
        );
        assert!(
            user_md.contains("**Timezone:** UTC"),
            "should default timezone to UTC"
        );

        let soul = tokio::fs::read_to_string(tmp.path().join("SOUL.md"))
            .await
            .unwrap();
        assert!(
            soul.contains("Be warm, natural, and clear."),
            "should default communication style"
        );
    }

    // ── scaffold_workspace: skip existing files ─────────────────

    #[tokio::test]
    async fn scaffold_does_not_overwrite_existing_files() {
        let tmp = TempDir::new().unwrap();
        let ctx = ProjectContext {
            user_name: "Bob".into(),
            ..Default::default()
        };

        // Pre-create SOUL.md with custom content
        let soul_path = tmp.path().join("SOUL.md");
        fs::write(&soul_path, "# My Custom Soul\nDo not overwrite me.")
            .await
            .unwrap();

        scaffold_workspace(tmp.path(), &ctx, "kumiho")
            .await
            .unwrap();

        // SOUL.md should be untouched
        let soul = tokio::fs::read_to_string(&soul_path).await.unwrap();
        assert!(
            soul.contains("Do not overwrite me"),
            "existing files should not be overwritten"
        );
        assert!(
            !soul.contains("You're not a chatbot"),
            "should not contain scaffold content"
        );

        // But USER.md should be created fresh
        let user_md = tokio::fs::read_to_string(tmp.path().join("USER.md"))
            .await
            .unwrap();
        assert!(user_md.contains("**Name:** Bob"));
    }

    // ── scaffold_workspace: idempotent ──────────────────────────

    #[tokio::test]
    async fn scaffold_is_idempotent() {
        let tmp = TempDir::new().unwrap();
        let ctx = ProjectContext {
            user_name: "Eve".into(),
            agent_name: "Claw".into(),
            ..Default::default()
        };

        scaffold_workspace(tmp.path(), &ctx, "kumiho")
            .await
            .unwrap();
        let soul_v1 = tokio::fs::read_to_string(tmp.path().join("SOUL.md"))
            .await
            .unwrap();

        // Run again — should not change anything
        scaffold_workspace(tmp.path(), &ctx, "kumiho")
            .await
            .unwrap();
        let soul_v2 = tokio::fs::read_to_string(tmp.path().join("SOUL.md"))
            .await
            .unwrap();

        assert_eq!(soul_v1, soul_v2, "scaffold should be idempotent");
    }

    // ── scaffold_workspace: all files are non-empty ─────────────

    #[tokio::test]
    async fn scaffold_files_are_non_empty() {
        let tmp = TempDir::new().unwrap();
        let ctx = ProjectContext::default();
        scaffold_workspace(tmp.path(), &ctx, "kumiho")
            .await
            .unwrap();

        for f in &[
            "IDENTITY.md",
            "AGENTS.md",
            "HEARTBEAT.md",
            "SOUL.md",
            "USER.md",
            "TOOLS.md",
            "MEMORY.md",
        ] {
            let content = tokio::fs::read_to_string(tmp.path().join(f)).await.unwrap();
            assert!(!content.trim().is_empty(), "{f} should not be empty");
        }
    }

    // ── scaffold_workspace: AGENTS.md references Kumiho memory tools

    #[tokio::test]
    async fn agents_md_references_kumiho_memory_tools() {
        let tmp = TempDir::new().unwrap();
        let ctx = ProjectContext::default();
        scaffold_workspace(tmp.path(), &ctx, "kumiho")
            .await
            .unwrap();

        let agents = tokio::fs::read_to_string(tmp.path().join("AGENTS.md"))
            .await
            .unwrap();
        assert!(
            agents.contains("kumiho_memory_engage"),
            "AGENTS.md should reference kumiho_memory_engage for cross-session recall"
        );
    }

    // ── scaffold_workspace: MEMORY.md warns about token cost ────

    #[tokio::test]
    async fn memory_md_warns_about_token_cost() {
        let tmp = TempDir::new().unwrap();
        let ctx = ProjectContext::default();
        scaffold_workspace(tmp.path(), &ctx, "kumiho")
            .await
            .unwrap();

        let memory = tokio::fs::read_to_string(tmp.path().join("MEMORY.md"))
            .await
            .unwrap();
        assert!(
            memory.contains("costs tokens"),
            "MEMORY.md should warn about token cost"
        );
        assert!(
            memory.contains("auto-injected"),
            "MEMORY.md should mention it's auto-injected"
        );
    }

    // ── scaffold_workspace: no flat-file daily-memory fiction (audit row 8)

    #[tokio::test]
    async fn scaffold_templates_omit_flat_file_fiction() {
        // The runtime never had a `memory/YYYY-MM-DD.md` daily-files mechanism,
        // never gated MEMORY.md injection on session-mode (main vs. group),
        // and never enforced "memory is limited — write to a file" framing.
        // The wizard templates must not claim otherwise.
        const FORBIDDEN: &[&str] = &[
            "memory/YYYY-MM-DD.md",
            "memory/*.md",
            "daily file",
            "daily files",
            "daily note",
            "daily notes",
            "daily log",
            "MAIN SESSION",
            "NEVER loaded in group chats",
            "Memory is limited",
            "WRITE IT TO A FILE",
        ];

        for backend in &["kumiho", "none"] {
            let tmp = TempDir::new().unwrap();
            let ctx = ProjectContext::default();
            scaffold_workspace(tmp.path(), &ctx, backend).await.unwrap();

            let agents = tokio::fs::read_to_string(tmp.path().join("AGENTS.md"))
                .await
                .unwrap();
            for phrase in FORBIDDEN {
                assert!(
                    !agents.contains(phrase),
                    "AGENTS.md (backend={backend}) must not contain flat-file fiction: {phrase:?}"
                );
            }

            let memory_path = tmp.path().join("MEMORY.md");
            if memory_path.exists() {
                let memory = tokio::fs::read_to_string(&memory_path).await.unwrap();
                for phrase in FORBIDDEN {
                    assert!(
                        !memory.contains(phrase),
                        "MEMORY.md (backend={backend}) must not contain flat-file fiction: {phrase:?}"
                    );
                }
            }
        }
    }

    // ── scaffold_workspace: TOOLS.md lists built-in + kumiho-memory tools ──

    #[tokio::test]
    async fn tools_md_lists_all_builtin_tools() {
        let tmp = TempDir::new().unwrap();
        let ctx = ProjectContext::default();
        scaffold_workspace(tmp.path(), &ctx, "kumiho")
            .await
            .unwrap();

        let tools = tokio::fs::read_to_string(tmp.path().join("TOOLS.md"))
            .await
            .unwrap();
        for tool in &[
            "shell",
            "file_read",
            "file_write",
            "kumiho_memory_engage",
            "kumiho_memory_reflect",
            "kumiho_memory_store",
        ] {
            assert!(tools.contains(tool), "TOOLS.md should list tool: {tool}");
        }
        // Audit row 11: bare `memory_*` tool names must not appear in TOOLS.md
        // — they have no native impl in Construct and the bootstrap prompt
        // teaches the model to use the kumiho-namespaced tools instead.
        for stale in &["memory_recall", "memory_forget"] {
            assert!(
                !tools.contains(stale),
                "TOOLS.md must not advertise legacy bare tool '{stale}'"
            );
        }
        assert!(
            tools.contains("Use when:"),
            "TOOLS.md should include 'Use when' guidance"
        );
        assert!(
            tools.contains("Don't use when:"),
            "TOOLS.md should include 'Don't use when' guidance"
        );
    }

    #[tokio::test]
    async fn soul_md_includes_emoji_awareness_guidance() {
        let tmp = TempDir::new().unwrap();
        let ctx = ProjectContext::default();
        scaffold_workspace(tmp.path(), &ctx, "kumiho")
            .await
            .unwrap();

        let soul = tokio::fs::read_to_string(tmp.path().join("SOUL.md"))
            .await
            .unwrap();
        assert!(
            soul.contains("Use emojis naturally (0-2 max"),
            "SOUL.md should include emoji usage guidance"
        );
        assert!(
            soul.contains("Match emoji density to the user"),
            "SOUL.md should include emoji-awareness guidance"
        );
    }

    // ── scaffold_workspace: special characters in names ─────────

    #[tokio::test]
    async fn scaffold_handles_special_characters_in_names() {
        let tmp = TempDir::new().unwrap();
        let ctx = ProjectContext {
            user_name: "José María".into(),
            agent_name: "Construct-v2".into(),
            timezone: "Europe/Madrid".into(),
            communication_style: "Be direct.".into(),
        };
        scaffold_workspace(tmp.path(), &ctx, "kumiho")
            .await
            .unwrap();

        let user_md = tokio::fs::read_to_string(tmp.path().join("USER.md"))
            .await
            .unwrap();
        assert!(user_md.contains("José María"));

        let soul = tokio::fs::read_to_string(tmp.path().join("SOUL.md"))
            .await
            .unwrap();
        assert!(soul.contains("Construct-v2"));
    }

    // ── scaffold_workspace: full personalization round-trip ─────

    #[tokio::test]
    async fn scaffold_full_personalization() {
        let tmp = TempDir::new().unwrap();
        let ctx = ProjectContext {
            user_name: "Kave".into(),
            timezone: "US/Eastern".into(),
            agent_name: "Claw".into(),
            communication_style:
                "Be friendly, human, and conversational. Show warmth and empathy while staying efficient. Use natural contractions."
                    .into(),
        };
        scaffold_workspace(tmp.path(), &ctx, "kumiho")
            .await
            .unwrap();

        // Verify every file got personalized
        let identity = tokio::fs::read_to_string(tmp.path().join("IDENTITY.md"))
            .await
            .unwrap();
        assert!(identity.contains("**Name:** Claw"));

        let soul = tokio::fs::read_to_string(tmp.path().join("SOUL.md"))
            .await
            .unwrap();
        assert!(soul.contains("You are **Claw**"));
        assert!(soul.contains("Be friendly, human, and conversational"));

        let user_md = tokio::fs::read_to_string(tmp.path().join("USER.md"))
            .await
            .unwrap();
        assert!(user_md.contains("**Name:** Kave"));
        assert!(user_md.contains("**Timezone:** US/Eastern"));
        assert!(user_md.contains("Be friendly, human, and conversational"));

        let agents = tokio::fs::read_to_string(tmp.path().join("AGENTS.md"))
            .await
            .unwrap();
        assert!(agents.contains("Claw Personal Assistant"));

        let heartbeat = tokio::fs::read_to_string(tmp.path().join("HEARTBEAT.md"))
            .await
            .unwrap();
        assert!(heartbeat.contains("Claw"));
    }

    // ── scaffold_workspace: none backend skips MEMORY.md ────────

    #[tokio::test]
    async fn scaffold_none_backend_disables_memory_guidance_and_skips_memory_md() {
        let tmp = TempDir::new().unwrap();
        let ctx = ProjectContext::default();
        scaffold_workspace(tmp.path(), &ctx, "none").await.unwrap();

        assert!(
            !tmp.path().join("MEMORY.md").exists(),
            "MEMORY.md should not be created for none backend"
        );

        let agents = tokio::fs::read_to_string(tmp.path().join("AGENTS.md"))
            .await
            .unwrap();
        assert!(
            agents.contains("memory.backend = \"none\""),
            "AGENTS.md should note that memory backend is none"
        );
    }

    // ── model helper coverage ───────────────────────────────────

    #[test]
    fn default_model_for_provider_uses_latest_defaults() {
        assert_eq!(
            default_model_for_provider("openrouter"),
            "anthropic/claude-sonnet-4.6"
        );
        assert_eq!(default_model_for_provider("openai"), "gpt-5.2");
        assert_eq!(default_model_for_provider("openai-codex"), "gpt-5-codex");
        assert_eq!(
            default_model_for_provider("anthropic"),
            "claude-sonnet-4-5-20250929"
        );
        assert_eq!(default_model_for_provider("qwen"), "qwen-plus");
        assert_eq!(default_model_for_provider("qwen-intl"), "qwen-plus");
        assert_eq!(default_model_for_provider("qwen-code"), "qwen3-coder-plus");
        assert_eq!(default_model_for_provider("glm-cn"), "glm-5");
        assert_eq!(default_model_for_provider("minimax-cn"), "MiniMax-M2.7");
        assert_eq!(default_model_for_provider("zai-cn"), "glm-5");
        assert_eq!(default_model_for_provider("gemini"), "gemini-2.5-pro");
        assert_eq!(default_model_for_provider("google"), "gemini-2.5-pro");
        assert_eq!(default_model_for_provider("kimi-code"), "kimi-for-coding");
        assert_eq!(
            default_model_for_provider("bedrock"),
            "anthropic.claude-sonnet-4-5-20250929-v1:0"
        );
        assert_eq!(
            default_model_for_provider("google-gemini"),
            "gemini-2.5-pro"
        );
        assert_eq!(default_model_for_provider("venice"), "zai-org-glm-5");
        assert_eq!(default_model_for_provider("moonshot"), "kimi-k2.5");
        assert_eq!(
            default_model_for_provider("nvidia"),
            "meta/llama-3.3-70b-instruct"
        );
        assert_eq!(
            default_model_for_provider("nvidia-nim"),
            "meta/llama-3.3-70b-instruct"
        );
        assert_eq!(
            default_model_for_provider("llamacpp"),
            "ggml-org/gpt-oss-20b-GGUF"
        );
        assert_eq!(default_model_for_provider("sglang"), "default");
        assert_eq!(default_model_for_provider("vllm"), "default");
        assert_eq!(
            default_model_for_provider("astrai"),
            "anthropic/claude-sonnet-4.6"
        );
        assert_eq!(
            default_model_for_provider("avian"),
            "deepseek/deepseek-v3.2"
        );
    }

    #[test]
    fn canonical_provider_name_normalizes_regional_aliases() {
        assert_eq!(canonical_provider_name("qwen-intl"), "qwen");
        assert_eq!(canonical_provider_name("dashscope-us"), "qwen");
        assert_eq!(canonical_provider_name("qwen-code"), "qwen-code");
        assert_eq!(canonical_provider_name("qwen-oauth"), "qwen-code");
        assert_eq!(canonical_provider_name("codex"), "openai-codex");
        assert_eq!(canonical_provider_name("openai_codex"), "openai-codex");
        assert_eq!(canonical_provider_name("moonshot-intl"), "moonshot");
        assert_eq!(canonical_provider_name("kimi-cn"), "moonshot");
        assert_eq!(canonical_provider_name("kimi_coding"), "kimi-code");
        assert_eq!(canonical_provider_name("kimi_for_coding"), "kimi-code");
        assert_eq!(canonical_provider_name("glm-cn"), "glm");
        assert_eq!(canonical_provider_name("bigmodel"), "glm");
        assert_eq!(canonical_provider_name("minimax-cn"), "minimax");
        assert_eq!(canonical_provider_name("zai-cn"), "zai");
        assert_eq!(canonical_provider_name("z.ai-global"), "zai");
        assert_eq!(canonical_provider_name("nvidia-nim"), "nvidia");
        assert_eq!(canonical_provider_name("aws-bedrock"), "bedrock");
        assert_eq!(canonical_provider_name("build.nvidia.com"), "nvidia");
        assert_eq!(canonical_provider_name("llama.cpp"), "llamacpp");
    }

    #[test]
    fn curated_models_for_openai_include_latest_choices() {
        let ids: Vec<String> = curated_models_for_provider("openai")
            .into_iter()
            .map(|(id, _)| id)
            .collect();

        assert!(ids.contains(&"gpt-5.2".to_string()));
        assert!(ids.contains(&"gpt-5-mini".to_string()));
    }

    #[test]
    fn curated_models_for_glm_removes_deprecated_flash_plus_aliases() {
        let ids: Vec<String> = curated_models_for_provider("glm")
            .into_iter()
            .map(|(id, _)| id)
            .collect();

        assert!(ids.contains(&"glm-5".to_string()));
        assert!(ids.contains(&"glm-4.7".to_string()));
        assert!(ids.contains(&"glm-4.5-air".to_string()));
        assert!(!ids.contains(&"glm-4-plus".to_string()));
        assert!(!ids.contains(&"glm-4-flash".to_string()));
    }

    #[test]
    fn curated_models_for_openai_codex_include_codex_family() {
        let ids: Vec<String> = curated_models_for_provider("openai-codex")
            .into_iter()
            .map(|(id, _)| id)
            .collect();

        assert!(ids.contains(&"gpt-5-codex".to_string()));
        assert!(ids.contains(&"gpt-5.2-codex".to_string()));
    }

    #[test]
    fn curated_models_for_openrouter_use_valid_anthropic_id() {
        let ids: Vec<String> = curated_models_for_provider("openrouter")
            .into_iter()
            .map(|(id, _)| id)
            .collect();

        assert!(ids.contains(&"anthropic/claude-sonnet-4.6".to_string()));
    }

    #[test]
    fn curated_models_for_bedrock_include_verified_model_ids() {
        let ids: Vec<String> = curated_models_for_provider("bedrock")
            .into_iter()
            .map(|(id, _)| id)
            .collect();

        assert!(ids.contains(&"anthropic.claude-sonnet-4-6".to_string()));
        assert!(ids.contains(&"anthropic.claude-opus-4-6-v1".to_string()));
        assert!(ids.contains(&"anthropic.claude-haiku-4-5-20251001-v1:0".to_string()));
        assert!(ids.contains(&"anthropic.claude-sonnet-4-5-20250929-v1:0".to_string()));
    }

    #[test]
    fn curated_models_for_moonshot_drop_deprecated_aliases() {
        let ids: Vec<String> = curated_models_for_provider("moonshot")
            .into_iter()
            .map(|(id, _)| id)
            .collect();

        assert!(ids.contains(&"kimi-k2.5".to_string()));
        assert!(ids.contains(&"kimi-k2-thinking".to_string()));
        assert!(!ids.contains(&"kimi-latest".to_string()));
        assert!(!ids.contains(&"kimi-thinking-preview".to_string()));
    }

    #[test]
    fn allows_unauthenticated_model_fetch_for_public_catalogs() {
        assert!(allows_unauthenticated_model_fetch("openrouter"));
        assert!(allows_unauthenticated_model_fetch("venice"));
        assert!(allows_unauthenticated_model_fetch("nvidia"));
        assert!(allows_unauthenticated_model_fetch("nvidia-nim"));
        assert!(allows_unauthenticated_model_fetch("build.nvidia.com"));
        assert!(allows_unauthenticated_model_fetch("astrai"));
        assert!(allows_unauthenticated_model_fetch("ollama"));
        assert!(allows_unauthenticated_model_fetch("llamacpp"));
        assert!(allows_unauthenticated_model_fetch("llama.cpp"));
        assert!(allows_unauthenticated_model_fetch("sglang"));
        assert!(allows_unauthenticated_model_fetch("vllm"));
        assert!(!allows_unauthenticated_model_fetch("openai"));
        assert!(!allows_unauthenticated_model_fetch("deepseek"));
    }

    #[test]
    fn curated_models_for_kimi_code_include_official_agent_model() {
        let ids: Vec<String> = curated_models_for_provider("kimi-code")
            .into_iter()
            .map(|(id, _)| id)
            .collect();

        assert!(ids.contains(&"kimi-for-coding".to_string()));
        assert!(ids.contains(&"kimi-k2.5".to_string()));
    }

    #[test]
    fn curated_models_for_qwen_code_include_coding_plan_models() {
        let ids: Vec<String> = curated_models_for_provider("qwen-code")
            .into_iter()
            .map(|(id, _)| id)
            .collect();

        assert!(ids.contains(&"qwen3-coder-plus".to_string()));
        assert!(ids.contains(&"qwen3.5-plus".to_string()));
        assert!(ids.contains(&"qwen3-max-2026-01-23".to_string()));
    }

    #[test]
    fn curated_models_for_avian_include_expected_catalog() {
        let ids: Vec<String> = curated_models_for_provider("avian")
            .into_iter()
            .map(|(id, _)| id)
            .collect();

        assert!(ids.contains(&"deepseek/deepseek-v3.2".to_string()));
        assert!(ids.contains(&"moonshotai/kimi-k2.5".to_string()));
        assert!(ids.contains(&"z-ai/glm-5".to_string()));
        assert!(ids.contains(&"minimax/minimax-m2.5".to_string()));
    }

    #[test]
    fn supports_live_model_fetch_for_supported_and_unsupported_providers() {
        assert!(supports_live_model_fetch("openai"));
        assert!(supports_live_model_fetch("anthropic"));
        assert!(supports_live_model_fetch("gemini"));
        assert!(supports_live_model_fetch("google"));
        assert!(supports_live_model_fetch("grok"));
        assert!(supports_live_model_fetch("together"));
        assert!(supports_live_model_fetch("nvidia"));
        assert!(supports_live_model_fetch("nvidia-nim"));
        assert!(supports_live_model_fetch("build.nvidia.com"));
        assert!(supports_live_model_fetch("ollama"));
        assert!(supports_live_model_fetch("llamacpp"));
        assert!(supports_live_model_fetch("llama.cpp"));
        assert!(supports_live_model_fetch("sglang"));
        assert!(supports_live_model_fetch("vllm"));
        assert!(supports_live_model_fetch("astrai"));
        assert!(supports_live_model_fetch("avian"));
        assert!(supports_live_model_fetch("venice"));
        assert!(supports_live_model_fetch("glm-cn"));
        assert!(supports_live_model_fetch("qwen-intl"));
        assert!(!supports_live_model_fetch("minimax-cn"));
        assert!(!supports_live_model_fetch("unknown-provider"));
    }

    #[test]
    fn curated_models_provider_aliases_share_same_catalog() {
        assert_eq!(
            curated_models_for_provider("xai"),
            curated_models_for_provider("grok")
        );
        assert_eq!(
            curated_models_for_provider("together-ai"),
            curated_models_for_provider("together")
        );
        assert_eq!(
            curated_models_for_provider("gemini"),
            curated_models_for_provider("google")
        );
        assert_eq!(
            curated_models_for_provider("gemini"),
            curated_models_for_provider("google-gemini")
        );
        assert_eq!(
            curated_models_for_provider("qwen"),
            curated_models_for_provider("qwen-intl")
        );
        assert_eq!(
            curated_models_for_provider("qwen"),
            curated_models_for_provider("dashscope-us")
        );
        assert_eq!(
            curated_models_for_provider("minimax"),
            curated_models_for_provider("minimax-cn")
        );
        assert_eq!(
            curated_models_for_provider("zai"),
            curated_models_for_provider("zai-cn")
        );
        assert_eq!(
            curated_models_for_provider("nvidia"),
            curated_models_for_provider("nvidia-nim")
        );
        assert_eq!(
            curated_models_for_provider("nvidia"),
            curated_models_for_provider("build.nvidia.com")
        );
        assert_eq!(
            curated_models_for_provider("llamacpp"),
            curated_models_for_provider("llama.cpp")
        );
        assert_eq!(
            curated_models_for_provider("bedrock"),
            curated_models_for_provider("aws-bedrock")
        );
    }

    #[test]
    fn curated_models_for_nvidia_include_nim_catalog_entries() {
        let ids: Vec<String> = curated_models_for_provider("nvidia")
            .into_iter()
            .map(|(id, _)| id)
            .collect();

        assert!(ids.contains(&"meta/llama-3.3-70b-instruct".to_string()));
        assert!(ids.contains(&"deepseek-ai/deepseek-v3.2".to_string()));
        assert!(ids.contains(&"nvidia/llama-3.3-nemotron-super-49b-v1.5".to_string()));
    }

    #[test]
    fn models_endpoint_for_provider_handles_region_aliases() {
        assert_eq!(
            models_endpoint_for_provider("glm-cn"),
            Some("https://open.bigmodel.cn/api/paas/v4/models")
        );
        assert_eq!(
            models_endpoint_for_provider("zai-cn"),
            Some("https://open.bigmodel.cn/api/coding/paas/v4/models")
        );
        assert_eq!(
            models_endpoint_for_provider("qwen-intl"),
            Some("https://dashscope-intl.aliyuncs.com/compatible-mode/v1/models")
        );
    }

    #[test]
    fn models_endpoint_for_provider_supports_additional_openai_compatible_providers() {
        assert_eq!(
            models_endpoint_for_provider("openai-codex"),
            Some("https://api.openai.com/v1/models")
        );
        assert_eq!(
            models_endpoint_for_provider("venice"),
            Some("https://api.venice.ai/api/v1/models")
        );
        assert_eq!(
            models_endpoint_for_provider("cohere"),
            Some("https://api.cohere.com/compatibility/v1/models")
        );
        assert_eq!(
            models_endpoint_for_provider("moonshot"),
            Some("https://api.moonshot.ai/v1/models")
        );
        assert_eq!(
            models_endpoint_for_provider("llamacpp"),
            Some("http://localhost:8080/v1/models")
        );
        assert_eq!(
            models_endpoint_for_provider("llama.cpp"),
            Some("http://localhost:8080/v1/models")
        );
        assert_eq!(
            models_endpoint_for_provider("sglang"),
            Some("http://localhost:30000/v1/models")
        );
        assert_eq!(
            models_endpoint_for_provider("vllm"),
            Some("http://localhost:8000/v1/models")
        );
        assert_eq!(
            models_endpoint_for_provider("avian"),
            Some("https://api.avian.io/v1/models")
        );
        assert_eq!(models_endpoint_for_provider("perplexity"), None);
        assert_eq!(models_endpoint_for_provider("unknown-provider"), None);
    }

    #[test]
    fn resolve_live_models_endpoint_prefers_llamacpp_custom_url() {
        assert_eq!(
            resolve_live_models_endpoint("llamacpp", Some("http://127.0.0.1:8033/v1")),
            Some("http://127.0.0.1:8033/v1/models".to_string())
        );
        assert_eq!(
            resolve_live_models_endpoint("llama.cpp", Some("http://127.0.0.1:8033/v1/")),
            Some("http://127.0.0.1:8033/v1/models".to_string())
        );
        assert_eq!(
            resolve_live_models_endpoint("llamacpp", Some("http://127.0.0.1:8033/v1/models")),
            Some("http://127.0.0.1:8033/v1/models".to_string())
        );
    }

    #[test]
    fn resolve_live_models_endpoint_falls_back_to_provider_defaults() {
        assert_eq!(
            resolve_live_models_endpoint("llamacpp", None),
            Some("http://localhost:8080/v1/models".to_string())
        );
        assert_eq!(
            resolve_live_models_endpoint("sglang", None),
            Some("http://localhost:30000/v1/models".to_string())
        );
        assert_eq!(
            resolve_live_models_endpoint("vllm", None),
            Some("http://localhost:8000/v1/models".to_string())
        );
        assert_eq!(
            resolve_live_models_endpoint("venice", Some("http://localhost:9999/v1")),
            Some("https://api.venice.ai/api/v1/models".to_string())
        );
        assert_eq!(resolve_live_models_endpoint("unknown-provider", None), None);
    }

    #[test]
    fn resolve_live_models_endpoint_supports_custom_provider_urls() {
        assert_eq!(
            resolve_live_models_endpoint("custom:https://proxy.example.com/v1", None),
            Some("https://proxy.example.com/v1/models".to_string())
        );
        assert_eq!(
            resolve_live_models_endpoint("custom:https://proxy.example.com/v1/models", None),
            Some("https://proxy.example.com/v1/models".to_string())
        );
    }

    #[test]
    fn normalize_ollama_endpoint_url_strips_api_suffix_and_trailing_slash() {
        assert_eq!(
            normalize_ollama_endpoint_url(" https://ollama.com/api/ "),
            "https://ollama.com".to_string()
        );
        assert_eq!(
            normalize_ollama_endpoint_url("https://ollama.com/"),
            "https://ollama.com".to_string()
        );
        assert_eq!(normalize_ollama_endpoint_url(""), "");
    }

    #[test]
    fn ollama_uses_remote_endpoint_distinguishes_local_and_remote_urls() {
        assert!(!ollama_uses_remote_endpoint(None));
        assert!(!ollama_uses_remote_endpoint(Some("http://localhost:11434")));
        assert!(!ollama_uses_remote_endpoint(Some(
            "http://127.0.0.1:11434/api"
        )));
        assert!(ollama_uses_remote_endpoint(Some("https://ollama.com")));
        assert!(ollama_uses_remote_endpoint(Some("https://ollama.com/api")));
    }

    #[test]
    fn resolve_live_models_endpoint_prefers_vllm_custom_url() {
        assert_eq!(
            resolve_live_models_endpoint("vllm", Some("http://127.0.0.1:9000/v1")),
            Some("http://127.0.0.1:9000/v1/models".to_string())
        );
        assert_eq!(
            resolve_live_models_endpoint("vllm", Some("http://127.0.0.1:9000/v1/models")),
            Some("http://127.0.0.1:9000/v1/models".to_string())
        );
    }

    #[test]
    fn parse_openai_model_ids_supports_data_array_payload() {
        let payload = json!({
            "data": [
                {"id": "  gpt-5.1  "},
                {"id": "gpt-5-mini"},
                {"id": "gpt-5.1"},
                {"id": ""}
            ]
        });

        let ids = parse_openai_compatible_model_ids(&payload);
        assert_eq!(ids, vec!["gpt-5-mini".to_string(), "gpt-5.1".to_string()]);
    }

    #[test]
    fn parse_openai_model_ids_supports_root_array_payload() {
        let payload = json!([
            {"id": "alpha"},
            {"id": "beta"},
            {"id": "alpha"}
        ]);

        let ids = parse_openai_compatible_model_ids(&payload);
        assert_eq!(ids, vec!["alpha".to_string(), "beta".to_string()]);
    }

    #[test]
    fn normalize_model_ids_deduplicates_case_insensitively() {
        let ids = normalize_model_ids(vec![
            "GPT-5".to_string(),
            "gpt-5".to_string(),
            "gpt-5-mini".to_string(),
            " GPT-5-MINI ".to_string(),
        ]);
        assert_eq!(ids, vec!["GPT-5".to_string(), "gpt-5-mini".to_string()]);
    }

    #[test]
    fn parse_gemini_model_ids_filters_for_generate_content() {
        let payload = json!({
            "models": [
                {
                    "name": "models/gemini-2.5-pro",
                    "supportedGenerationMethods": ["generateContent", "countTokens"]
                },
                {
                    "name": "models/text-embedding-004",
                    "supportedGenerationMethods": ["embedContent"]
                },
                {
                    "name": "models/gemini-2.5-flash",
                    "supportedGenerationMethods": ["generateContent"]
                }
            ]
        });

        let ids = parse_gemini_model_ids(&payload);
        assert_eq!(
            ids,
            vec!["gemini-2.5-flash".to_string(), "gemini-2.5-pro".to_string()]
        );
    }

    #[test]
    fn parse_ollama_model_ids_extracts_and_deduplicates_names() {
        let payload = json!({
            "models": [
                {"name": "llama3.2:latest"},
                {"name": "mistral:latest"},
                {"name": "llama3.2:latest"}
            ]
        });

        let ids = parse_ollama_model_ids(&payload);
        assert_eq!(
            ids,
            vec!["llama3.2:latest".to_string(), "mistral:latest".to_string()]
        );
    }

    #[tokio::test]
    async fn model_cache_round_trip_returns_fresh_entry() {
        let tmp = TempDir::new().unwrap();
        let models = vec!["gpt-5.1".to_string(), "gpt-5-mini".to_string()];

        cache_live_models_for_provider(tmp.path(), "openai", &models)
            .await
            .unwrap();

        let cached = load_cached_models_for_provider(tmp.path(), "openai", MODEL_CACHE_TTL_SECS)
            .await
            .unwrap();
        let cached = cached.expect("expected fresh cached models");

        assert_eq!(cached.models.len(), 2);
        assert!(cached.models.contains(&"gpt-5.1".to_string()));
        assert!(cached.models.contains(&"gpt-5-mini".to_string()));
    }

    #[tokio::test]
    async fn model_cache_ttl_filters_stale_entries() {
        let tmp = TempDir::new().unwrap();
        let stale = ModelCacheState {
            entries: vec![ModelCacheEntry {
                provider: "openai".to_string(),
                fetched_at_unix: now_unix_secs().saturating_sub(MODEL_CACHE_TTL_SECS + 120),
                models: vec!["gpt-5.1".to_string()],
            }],
        };

        save_model_cache_state(tmp.path(), &stale).await.unwrap();

        let fresh = load_cached_models_for_provider(tmp.path(), "openai", MODEL_CACHE_TTL_SECS)
            .await
            .unwrap();
        assert!(fresh.is_none());

        let stale_any = load_any_cached_models_for_provider(tmp.path(), "openai")
            .await
            .unwrap();
        assert!(stale_any.is_some());
    }

    #[tokio::test]
    async fn run_models_refresh_uses_fresh_cache_without_network() {
        let tmp = TempDir::new().unwrap();

        cache_live_models_for_provider(tmp.path(), "openai", &["gpt-5.1".to_string()])
            .await
            .unwrap();

        let config = Config {
            workspace_dir: tmp.path().to_path_buf(),
            default_provider: Some("openai".to_string()),
            ..Config::default()
        };

        run_models_refresh(&config, None, false).await.unwrap();
    }

    #[tokio::test]
    async fn run_models_refresh_rejects_unsupported_provider() {
        let tmp = TempDir::new().unwrap();

        let config = Config {
            workspace_dir: tmp.path().to_path_buf(),
            // Use a non-provider channel key to keep this test deterministic and offline.
            default_provider: Some("imessage".to_string()),
            ..Config::default()
        };

        let err = run_models_refresh(&config, None, true).await.unwrap_err();
        assert!(
            err.to_string()
                .contains("does not support live model discovery")
        );
    }

    // ── provider_env_var ────────────────────────────────────────

    #[test]
    fn provider_env_var_known_providers() {
        assert_eq!(provider_env_var("openrouter"), "OPENROUTER_API_KEY");
        assert_eq!(provider_env_var("anthropic"), "ANTHROPIC_API_KEY");
        assert_eq!(provider_env_var("openai-codex"), "OPENAI_API_KEY");
        assert_eq!(provider_env_var("openai"), "OPENAI_API_KEY");
        assert_eq!(provider_env_var("ollama"), "OLLAMA_API_KEY");
        assert_eq!(provider_env_var("llamacpp"), "LLAMACPP_API_KEY");
        assert_eq!(provider_env_var("llama.cpp"), "LLAMACPP_API_KEY");
        assert_eq!(provider_env_var("sglang"), "SGLANG_API_KEY");
        assert_eq!(provider_env_var("vllm"), "VLLM_API_KEY");
        assert_eq!(provider_env_var("xai"), "XAI_API_KEY");
        assert_eq!(provider_env_var("grok"), "XAI_API_KEY"); // alias
        assert_eq!(provider_env_var("together"), "TOGETHER_API_KEY"); // alias
        assert_eq!(provider_env_var("together-ai"), "TOGETHER_API_KEY");
        assert_eq!(provider_env_var("google"), "GEMINI_API_KEY"); // alias
        assert_eq!(provider_env_var("google-gemini"), "GEMINI_API_KEY"); // alias
        assert_eq!(provider_env_var("gemini"), "GEMINI_API_KEY");
        assert_eq!(provider_env_var("qwen"), "DASHSCOPE_API_KEY");
        assert_eq!(provider_env_var("qwen-intl"), "DASHSCOPE_API_KEY");
        assert_eq!(provider_env_var("dashscope-us"), "DASHSCOPE_API_KEY");
        assert_eq!(provider_env_var("qwen-code"), "QWEN_OAUTH_TOKEN");
        assert_eq!(provider_env_var("qwen-oauth"), "QWEN_OAUTH_TOKEN");
        assert_eq!(provider_env_var("glm-cn"), "GLM_API_KEY");
        assert_eq!(provider_env_var("minimax-cn"), "MINIMAX_API_KEY");
        assert_eq!(provider_env_var("kimi-code"), "KIMI_CODE_API_KEY");
        assert_eq!(provider_env_var("kimi_coding"), "KIMI_CODE_API_KEY");
        assert_eq!(provider_env_var("kimi_for_coding"), "KIMI_CODE_API_KEY");
        assert_eq!(provider_env_var("minimax-oauth"), "MINIMAX_API_KEY");
        assert_eq!(provider_env_var("minimax-oauth-cn"), "MINIMAX_API_KEY");
        assert_eq!(provider_env_var("moonshot-intl"), "MOONSHOT_API_KEY");
        assert_eq!(provider_env_var("zai-cn"), "ZAI_API_KEY");
        assert_eq!(provider_env_var("nvidia"), "NVIDIA_API_KEY");
        assert_eq!(provider_env_var("nvidia-nim"), "NVIDIA_API_KEY"); // alias
        assert_eq!(provider_env_var("build.nvidia.com"), "NVIDIA_API_KEY"); // alias
        assert_eq!(provider_env_var("astrai"), "ASTRAI_API_KEY");
        assert_eq!(provider_env_var("opencode-go"), "OPENCODE_GO_API_KEY");
        assert_eq!(provider_env_var("avian"), "AVIAN_API_KEY");
    }

    #[test]
    fn provider_supports_keyless_local_usage_for_local_providers() {
        assert!(provider_supports_keyless_local_usage("ollama"));
        assert!(provider_supports_keyless_local_usage("llamacpp"));
        assert!(provider_supports_keyless_local_usage("llama.cpp"));
        assert!(provider_supports_keyless_local_usage("sglang"));
        assert!(provider_supports_keyless_local_usage("vllm"));
        assert!(!provider_supports_keyless_local_usage("openai"));
    }

    #[test]
    fn provider_supports_device_flow_copilot() {
        assert!(provider_supports_device_flow("copilot"));
        assert!(provider_supports_device_flow("github-copilot"));
        assert!(provider_supports_device_flow("gemini"));
        assert!(provider_supports_device_flow("openai-codex"));
        assert!(!provider_supports_device_flow("openai"));
        assert!(!provider_supports_device_flow("openrouter"));
    }

    #[test]
    fn local_provider_choices_include_sglang() {
        let choices = local_provider_choices();
        assert!(choices.iter().any(|(provider, _)| *provider == "sglang"));
    }

    #[test]
    fn provider_env_var_unknown_falls_back() {
        assert_eq!(provider_env_var("some-new-provider"), "API_KEY");
    }

    #[test]
    fn backend_key_from_choice_maps_supported_backends() {
        assert_eq!(backend_key_from_choice(0), "kumiho");
        assert_eq!(backend_key_from_choice(1), "none");
        // Out-of-range falls back to the default (kumiho).
        assert_eq!(backend_key_from_choice(999), "kumiho");
    }

    #[test]
    fn memory_backend_profiles_reflect_supported_backends() {
        let kumiho = memory_backend_profile("kumiho");
        assert_eq!(kumiho.key, "kumiho");
        assert!(kumiho.auto_save_default);
        assert!(!kumiho.uses_sqlite_hygiene);

        let none = memory_backend_profile("none");
        assert_eq!(none.key, "none");
        assert!(!none.auto_save_default);
        assert!(!none.uses_sqlite_hygiene);

        let custom = memory_backend_profile("custom-memory");
        assert_eq!(custom.key, "custom");
        assert!(custom.auto_save_default);
        assert!(!custom.uses_sqlite_hygiene);
    }

    #[test]
    fn memory_config_defaults_for_kumiho_enable_auto_save() {
        let config = memory_config_defaults_for_backend("kumiho");
        assert_eq!(config.backend, "kumiho");
        assert!(config.auto_save);
        assert!(!config.hygiene_enabled);
    }

    #[test]
    fn memory_config_defaults_for_none_disable_auto_save() {
        let config = memory_config_defaults_for_backend("none");
        assert_eq!(config.backend, "none");
        assert!(!config.auto_save);
        assert!(!config.hygiene_enabled);
        assert_eq!(config.archive_after_days, 0);
        assert_eq!(config.purge_after_days, 0);
    }

    #[test]
    fn channel_menu_choices_include_signal_nextcloud_lark_and_feishu() {
        assert!(channel_menu_choices().contains(&ChannelMenuChoice::Signal));
        assert!(channel_menu_choices().contains(&ChannelMenuChoice::NextcloudTalk));
        assert!(channel_menu_choices().contains(&ChannelMenuChoice::Lark));
        assert!(channel_menu_choices().contains(&ChannelMenuChoice::Feishu));
    }

    #[test]
    fn launchable_channels_include_signal_mattermost_qq_nextcloud_and_feishu() {
        let mut channels = ChannelsConfig::default();
        assert!(!has_launchable_channels(&channels));

        channels.signal = Some(crate::config::schema::SignalConfig {
            http_url: "http://127.0.0.1:8686".into(),
            account: "+1234567890".into(),
            group_id: None,
            allowed_from: vec!["*".into()],
            ignore_attachments: false,
            ignore_stories: true,
            proxy_url: None,
        });
        assert!(has_launchable_channels(&channels));

        channels.signal = None;
        channels.mattermost = Some(crate::config::schema::MattermostConfig {
            url: "https://mattermost.example.com".into(),
            bot_token: "token".into(),
            channel_id: Some("channel".into()),
            allowed_users: vec!["*".into()],
            thread_replies: Some(true),
            mention_only: Some(false),
            interrupt_on_new_message: false,
            proxy_url: None,
        });
        assert!(has_launchable_channels(&channels));

        channels.mattermost = None;
        channels.qq = Some(crate::config::schema::QQConfig {
            app_id: "app-id".into(),
            app_secret: "app-secret".into(),
            allowed_users: vec!["*".into()],
            proxy_url: None,
        });
        assert!(has_launchable_channels(&channels));

        channels.qq = None;
        channels.nextcloud_talk = Some(crate::config::schema::NextcloudTalkConfig {
            base_url: "https://cloud.example.com".into(),
            app_token: "token".into(),
            webhook_secret: Some("secret".into()),
            allowed_users: vec!["*".into()],
            proxy_url: None,
            bot_name: None,
        });
        assert!(has_launchable_channels(&channels));

        channels.nextcloud_talk = None;
        channels.feishu = Some(crate::config::schema::FeishuConfig {
            app_id: "cli_123".into(),
            app_secret: "secret".into(),
            encrypt_key: None,
            verification_token: None,
            allowed_users: vec!["*".into()],
            receive_mode: crate::config::schema::LarkReceiveMode::Websocket,
            port: None,
            proxy_url: None,
        });
        assert!(has_launchable_channels(&channels));
    }
}
