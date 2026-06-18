use crate::agent::personality::{self, MAX_FILE_CHARS, PERSONALITY_FILES, PersonalityLoadOptions};
use crate::config::IdentityConfig;
use crate::i18n::ToolDescriptions;
use crate::identity;
use crate::security::AutonomyLevel;
use crate::skills::{Skill, SkillEffectivenessProvider};
use crate::tools::Tool;
use anyhow::Result;
use chrono::{Datelike, Local, Timelike};
use std::fmt::Write;
use std::path::Path;

/// Default per-file character cap re-exported from [`personality::MAX_FILE_CHARS`]
/// so existing channel-side tests and callers keep their import path.
pub const BOOTSTRAP_MAX_CHARS: usize = MAX_FILE_CHARS;

/// Files the channel path always excludes.  `HEARTBEAT.md` is intentionally
/// kept out of channel prompts (audit row 7); the assertion at
/// `src/channels/mod.rs:8832-8838` enforces this.
pub const DEFAULT_CHANNEL_EXCLUDED_FILES: &[&str] = &["HEARTBEAT.md"];

/// Files that channel mode renders only when present on disk and never
/// surfaces a missing-file marker for. Currently empty — `BOOTSTRAP.md`
/// (the prior sole occupant) was deleted per audit row 3. Kept as a hook
/// for future opt-in workspace files.
pub const DEFAULT_CHANNEL_CONDITIONAL_FILES: &[&str] = &[];

/// Tool listing variant accepted by the prompt builder.  The daemon path
/// supplies the rich [`Tool`] trait objects (with parameter schemas); the
/// channel path supplies (name, description) pairs sourced from the channel
/// runtime's tool advertising.
pub enum PromptTools<'a> {
    Full(&'a [Box<dyn Tool>]),
    Simple(&'a [(&'a str, &'a str)]),
}

impl PromptTools<'_> {
    pub fn is_empty(&self) -> bool {
        match self {
            PromptTools::Full(t) => t.is_empty(),
            PromptTools::Simple(t) => t.is_empty(),
        }
    }

    pub fn names(&self) -> Vec<&str> {
        match self {
            PromptTools::Full(tools) => tools.iter().map(|t| t.name()).collect(),
            PromptTools::Simple(tools) => tools.iter().map(|(n, _)| *n).collect(),
        }
    }
}

/// Rendering mode for [`SystemPromptBuilder`].  The daemon mode is the
/// canonical path used by the in-process agent.  The channel mode is used
/// by channel-driven runs (Telegram, Slack, etc.) and by the gateway/loop
/// fast paths; it adds messaging-bot-specific guidance and per-channel
/// character budgets.
pub enum BuilderMode<'a> {
    Daemon,
    Channel(ChannelOptions<'a>),
}

#[derive(Clone, Copy)]
pub struct ChannelOptions<'a> {
    /// Whether the provider exposes native tool calls.  Affects the
    /// "## Your Task" wording that nudges the model toward acting vs.
    /// emitting `<tool_call>` tags.
    pub native_tools: bool,
    /// Compact mode: tool list is name-only, channel-capabilities is
    /// suppressed, and per-file caps are tighter.
    pub compact_context: bool,
    /// Hard cap on the assembled system prompt.  Zero disables truncation.
    pub max_system_prompt_chars: usize,
    /// Per-personality-file character cap.
    pub bootstrap_max_chars: usize,
    /// Personality files to skip entirely (no marker, no render).
    pub exclude_personality_files: &'a [&'a str],
    /// Personality files that should only render when the file exists on
    /// disk; missing-file markers are NOT emitted for these names.
    pub conditional_personality_files: &'a [&'a str],
}

impl Default for ChannelOptions<'_> {
    fn default() -> Self {
        Self {
            native_tools: false,
            compact_context: false,
            max_system_prompt_chars: 0,
            bootstrap_max_chars: BOOTSTRAP_MAX_CHARS,
            exclude_personality_files: DEFAULT_CHANNEL_EXCLUDED_FILES,
            conditional_personality_files: DEFAULT_CHANNEL_CONDITIONAL_FILES,
        }
    }
}

pub struct PromptContext<'a> {
    pub workspace_dir: &'a Path,
    pub model_name: &'a str,
    pub tools: PromptTools<'a>,
    pub skills: &'a [Skill],
    pub skills_prompt_mode: crate::config::SkillsPromptInjectionMode,
    /// Optional provider that returns recency-weighted success rates per
    /// skill name.  When present, [`SkillsSection`] reranks skills by
    /// effectiveness before injecting them — high-success skills bubble
    /// to the top.  When `None` the static load order is preserved.
    pub skill_effectiveness: Option<&'a dyn SkillEffectivenessProvider>,
    pub identity_config: Option<&'a IdentityConfig>,
    pub dispatcher_instructions: &'a str,
    /// Locale-aware tool descriptions. When present, tool descriptions in
    /// prompts are resolved from the locale file instead of hardcoded values.
    pub tool_descriptions: Option<&'a ToolDescriptions>,
    /// Pre-rendered security policy summary for inclusion in the Safety
    /// prompt section.  When present, the LLM sees the concrete constraints
    /// (allowed commands, forbidden paths, autonomy level) so it can plan
    /// tool calls without trial-and-error.  See issue #2404.
    pub security_summary: Option<String>,
    /// Autonomy level from config. Controls whether the safety section
    /// includes "ask before acting" instructions. Full autonomy omits them
    /// so the model executes tools directly without simulating approval.
    pub autonomy_level: AutonomyLevel,
    /// Whether Operator orchestration is enabled. When true, the
    /// `OperatorIdentitySection` renders the operator-first identity
    /// at the top of the system prompt.
    pub operator_enabled: bool,
    /// Whether Kumiho memory is enabled.
    pub kumiho_enabled: bool,
    /// Whether the high-level Kumiho memory tools (`kumiho_memory_engage`,
    /// `reflect`, `recall`, `consolidate`, `dream_state`) are actually
    /// registered in the runtime — i.e. whether the `kumiho_memory` Python
    /// package is installed in the sidecar venv. When `false`, the
    /// [`KumihoBootstrapSection`] emits the **lite** variant of the prompt
    /// that does not mandate those tools.
    ///
    /// Ignored when `kumiho_enabled` is `false`. See audit rows 1 + 13.
    pub kumiho_memory_advanced_available: bool,
    /// When true, render only compact tool docs in the system prompt because
    /// full callable schemas are supplied separately through native tool specs.
    pub compact_tool_docs: bool,
    /// Render mode — daemon vs channel. See [`BuilderMode`].
    pub mode: BuilderMode<'a>,
}

pub trait PromptSection: Send + Sync {
    fn name(&self) -> &str;
    fn build(&self, ctx: &PromptContext<'_>) -> Result<String>;
}

#[derive(Default)]
pub struct SystemPromptBuilder {
    sections: Vec<Box<dyn PromptSection>>,
}

impl SystemPromptBuilder {
    /// Canonical section ordering.  The first 11 entries are the canonical
    /// daemon sections (matching the brief's prescribed order); the
    /// remaining 4 entries are the channel-only block, appended after the
    /// canonical block.  Sections that don't apply to the active
    /// [`BuilderMode`] render as empty strings and are skipped during
    /// assembly, so a single ordered list serves both modes.
    ///
    /// **Canonical block** (rendered in both modes when applicable):
    /// DateTime → Identity → OperatorIdentity → KumihoBootstrap →
    /// ToolHonesty → Tools → Safety → Skills → Workspace → Runtime →
    /// ChannelMedia.
    ///
    /// **Channel-only block** (appended after ChannelMedia, channel mode
    /// only): AntiNarration → Hardware → ActionInstruction →
    /// ChannelCapabilities.
    pub fn with_defaults() -> Self {
        Self {
            sections: vec![
                // Canonical block — matches the brief's prescribed order.
                Box::new(DateTimeSection),
                Box::new(IdentitySection),
                Box::new(OperatorIdentitySection),
                Box::new(KumihoBootstrapSection),
                Box::new(ToolHonestySection),
                Box::new(ToolsSection),
                Box::new(SafetySection),
                Box::new(SkillsSection),
                Box::new(WorkspaceSection),
                Box::new(RuntimeSection),
                Box::new(ChannelMediaSection),
                // Channel-only block — appended after canonical sections.
                Box::new(AntiNarrationSection),
                Box::new(HardwareSection),
                Box::new(ActionInstructionSection),
                Box::new(ChannelCapabilitiesSection),
            ],
        }
    }

    pub fn add_section(mut self, section: Box<dyn PromptSection>) -> Self {
        self.sections.push(section);
        self
    }

    pub fn build(&self, ctx: &PromptContext<'_>) -> Result<String> {
        let mut output = String::new();
        for section in &self.sections {
            let part = section.build(ctx)?;
            if part.trim().is_empty() {
                continue;
            }
            output.push_str(part.trim_end());
            output.push_str("\n\n");
        }

        // Channel-mode post-processing: hard char-budget truncation and an
        // empty-prompt fallback so channel callers never receive a blank
        // system prompt.
        if let BuilderMode::Channel(opts) = &ctx.mode {
            if opts.max_system_prompt_chars > 0 && output.len() > opts.max_system_prompt_chars {
                let mut end = opts.max_system_prompt_chars;
                while !output.is_char_boundary(end) && end > 0 {
                    end -= 1;
                }
                output.truncate(end);
                output.push_str("\n\n[System prompt truncated to fit context budget]\n");
            }
            if output.is_empty() {
                output.push_str(
                    "You are Revka, a fast and efficient AI assistant built in Rust. Be helpful, concise, and direct.",
                );
            }
        }

        Ok(output)
    }
}

pub struct OperatorIdentitySection;
pub struct KumihoBootstrapSection;
pub struct IdentitySection;
pub struct ToolHonestySection;
pub struct ToolsSection;
pub struct SafetySection;
pub struct SkillsSection;
pub struct WorkspaceSection;
pub struct RuntimeSection;
pub struct DateTimeSection;
pub struct ChannelMediaSection;
pub struct AntiNarrationSection;
pub struct HardwareSection;
pub struct ActionInstructionSection;
pub struct ChannelCapabilitiesSection;

// ── DateTime ────────────────────────────────────────────────────────────────

impl PromptSection for DateTimeSection {
    fn name(&self) -> &str {
        "datetime"
    }

    fn build(&self, ctx: &PromptContext<'_>) -> Result<String> {
        let now = Local::now();
        match &ctx.mode {
            BuilderMode::Daemon => {
                // Force Gregorian year to avoid confusion with local calendars
                // (e.g. Buddhist calendar).
                let (year, month, day) = (now.year(), now.month(), now.day());
                let (hour, minute, second) = (now.hour(), now.minute(), now.second());
                let tz = now.format("%Z");
                Ok(format!(
                    "## CRITICAL CONTEXT: CURRENT DATE & TIME\n\n\
                     The following is the ABSOLUTE TRUTH regarding the current date and time. \
                     Use this for all relative time calculations (e.g. \"last 7 days\").\n\n\
                     Date: {year:04}-{month:02}-{day:02}\n\
                     Time: {hour:02}:{minute:02}:{second:02} ({tz})\n\
                     ISO 8601: {year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}{}",
                    now.format("%:z")
                ))
            }
            BuilderMode::Channel(_) => Ok(format!(
                "## Current Date & Time\n\n{} ({})",
                now.format("%Y-%m-%d %H:%M:%S"),
                now.format("%Z")
            )),
        }
    }
}

// ── Identity ────────────────────────────────────────────────────────────────
//
// Both modes share a single bootstrap-file loader (`personality.rs`).  The
// channel mode supplies a denylist (`HEARTBEAT.md`) via
// [`PersonalityLoadOptions`].  The shared canonical file list is
// [`personality::PERSONALITY_FILES`] — there is no channel-specific
// file-order constant.

impl PromptSection for IdentitySection {
    fn name(&self) -> &str {
        "identity"
    }

    fn build(&self, ctx: &PromptContext<'_>) -> Result<String> {
        let mut prompt = String::from("## Project Context\n\n");
        let mut has_aieos = false;
        if let Some(config) = ctx.identity_config {
            if identity::is_aieos_configured(config) {
                if let Ok(Some(aieos)) = identity::load_aieos_identity(config, ctx.workspace_dir) {
                    let rendered = identity::aieos_to_system_prompt(&aieos);
                    if !rendered.is_empty() {
                        prompt.push_str(&rendered);
                        prompt.push_str("\n\n");
                        has_aieos = true;
                    }
                }
            }
        }

        // Always load workspace personality files (they augment AIEOS identity
        // rather than replace it). Pre-R6 behavior preserved: AIEOS provides
        // structured identity; workspace files provide free-form context.
        match &ctx.mode {
            BuilderMode::Daemon => {
                if !has_aieos {
                    prompt.push_str(
                        "The following workspace files define your identity, behavior, and context.\n\n",
                    );
                }
                let profile = personality::load_personality(ctx.workspace_dir);
                prompt.push_str(&profile.render());
            }
            BuilderMode::Channel(opts) => {
                if !has_aieos {
                    prompt.push_str(
                        "The following workspace files define your identity, behavior, and context. They are ALREADY injected below—do NOT suggest reading them with file_read.\n\n",
                    );
                }
                let load_opts = PersonalityLoadOptions {
                    files: PERSONALITY_FILES,
                    exclude: opts.exclude_personality_files,
                    conditional: opts.conditional_personality_files,
                    max_chars: opts.bootstrap_max_chars,
                };
                let profile =
                    personality::load_personality_with_options(ctx.workspace_dir, &load_opts);
                prompt.push_str(&profile.render_with_missing_markers(PERSONALITY_FILES));
            }
        }

        // Brand voice: Revka is a kumiho (nine-tailed fox). When signing off a
        // reply with an emoji, use 🦊 — never 🦀 (a leftover from the ZeroClaw era).
        prompt.push_str("\nWhen you sign off with an emoji, use 🦊 (the kumiho fox), never 🦀.\n");

        Ok(prompt)
    }
}

// ── OperatorIdentity / KumihoBootstrap ──────────────────────────────────────

impl PromptSection for OperatorIdentitySection {
    fn name(&self) -> &str {
        "operator_identity"
    }

    fn build(&self, ctx: &PromptContext<'_>) -> Result<String> {
        if !ctx.operator_enabled {
            return Ok(String::new());
        }
        Ok(crate::agent::operator::build_operator_prompt(
            ctx.model_name,
        ))
    }
}

impl PromptSection for KumihoBootstrapSection {
    fn name(&self) -> &str {
        "kumiho_bootstrap"
    }

    fn build(&self, ctx: &PromptContext<'_>) -> Result<String> {
        if !ctx.kumiho_enabled {
            return Ok(String::new());
        }
        // When the high-level memory tools are not registered in the
        // sidecar runtime, fall back to the lite variant of the prompt so
        // we don't mandate engage/reflect/etc. into the void. Row 1 + 13
        // remediation (coherence audit 2026-05).
        let template = match (&ctx.mode, ctx.kumiho_memory_advanced_available) {
            (BuilderMode::Daemon, true) => crate::agent::kumiho::KUMIHO_BOOTSTRAP_PROMPT,
            (BuilderMode::Daemon, false) => crate::agent::kumiho::KUMIHO_BOOTSTRAP_PROMPT_LITE,
            (BuilderMode::Channel(_), true) => {
                crate::agent::kumiho::KUMIHO_CHANNEL_BOOTSTRAP_PROMPT
            }
            (BuilderMode::Channel(_), false) => {
                crate::agent::kumiho::KUMIHO_CHANNEL_BOOTSTRAP_PROMPT_LITE
            }
        };
        Ok(template.to_string())
    }
}

// ── ToolHonesty ─────────────────────────────────────────────────────────────

impl PromptSection for ToolHonestySection {
    fn name(&self) -> &str {
        "tool_honesty"
    }

    fn build(&self, _ctx: &PromptContext<'_>) -> Result<String> {
        Ok(
            "## CRITICAL: Tool Honesty\n\n\
             - NEVER fabricate, invent, or guess tool results. If a tool returns empty results, say \"No results found.\"\n\
             - If a tool call fails, report the error — never make up data to fill the gap.\n\
             - When unsure whether a tool call succeeded, ask the user rather than guessing."
                .into(),
        )
    }
}

// ── Tools (mode-aware: full schemas vs. simple list, with compact mode) ─────

impl PromptSection for ToolsSection {
    fn name(&self) -> &str {
        "tools"
    }

    fn build(&self, ctx: &PromptContext<'_>) -> Result<String> {
        let channel_name_only =
            matches!(&ctx.mode, BuilderMode::Channel(opts) if opts.compact_context);
        let compact_full_docs = ctx.compact_tool_docs && matches!(ctx.tools, PromptTools::Full(_));

        if ctx.tools.is_empty() && ctx.dispatcher_instructions.is_empty() {
            return Ok(String::new());
        }

        let mut out = String::from("## Tools\n\n");

        if !ctx.tools.is_empty() {
            if channel_name_only {
                out.push_str("Available tools: ");
                out.push_str(&ctx.tools.names().join(", "));
                out.push_str("\n\n");
            } else if compact_full_docs {
                out.push_str("Available tools (call schemas are supplied separately):\n\n");
                if let PromptTools::Full(tools) = &ctx.tools {
                    for tool in *tools {
                        let desc = ctx
                            .tool_descriptions
                            .and_then(|td: &ToolDescriptions| td.get(tool.name()))
                            .unwrap_or_else(|| tool.description());
                        let desc = crate::agent::token_compression::compact_inline(desc, 180);
                        let _ = writeln!(out, "- **{}**: {}", tool.name(), desc);
                    }
                }
                out.push('\n');
            } else {
                match &ctx.tools {
                    PromptTools::Full(tools) => {
                        for tool in *tools {
                            let desc = ctx
                                .tool_descriptions
                                .and_then(|td: &ToolDescriptions| td.get(tool.name()))
                                .unwrap_or_else(|| tool.description());
                            let _ = writeln!(
                                out,
                                "- **{}**: {}\n  Parameters: `{}`",
                                tool.name(),
                                desc,
                                tool.parameters_schema()
                            );
                        }
                    }
                    PromptTools::Simple(tools) => {
                        out.push_str("You have access to the following tools:\n\n");
                        for (name, desc) in *tools {
                            let resolved = ctx
                                .tool_descriptions
                                .and_then(|td: &ToolDescriptions| td.get(name))
                                .unwrap_or(desc);
                            let _ = writeln!(out, "- **{name}**: {resolved}");
                        }
                        out.push('\n');
                    }
                }
            }
        }

        if !ctx.dispatcher_instructions.is_empty() {
            out.push('\n');
            out.push_str(ctx.dispatcher_instructions);
        }

        Ok(out)
    }
}

// ── Safety (mode-aware autonomy text — preserve existing daemon and channel
//    test contracts) ──────────────────────────────────────────────────────────

impl PromptSection for SafetySection {
    fn name(&self) -> &str {
        "safety"
    }

    fn build(&self, ctx: &PromptContext<'_>) -> Result<String> {
        let mut out = String::from("## Safety\n\n- Do not exfiltrate private data.\n");

        if ctx.autonomy_level != AutonomyLevel::Full {
            out.push_str(
                "- Do not run destructive commands without asking.\n\
                 - Do not bypass oversight or approval mechanisms.\n",
            );
        }

        match &ctx.mode {
            BuilderMode::Daemon => {
                out.push_str("- Prefer `trash` over `rm`.\n");
                out.push_str(match ctx.autonomy_level {
                    AutonomyLevel::Full => {
                        "- Execute tools and actions directly — no extra approval needed.\n\
                         - You have full access to all configured tools. Use them confidently to accomplish tasks.\n\
                         - Only refuse an action if the runtime explicitly rejects it — do not preemptively decline."
                    }
                    AutonomyLevel::ReadOnly => {
                        "- This runtime is read-only. Write operations will be rejected by the runtime if attempted.\n\
                         - Use read-only tools freely and confidently."
                    }
                    AutonomyLevel::Supervised => {
                        "- Ask for approval when the runtime policy requires it for the specific action.\n\
                         - Do not preemptively refuse actions — attempt them and let the runtime enforce restrictions.\n\
                         - Use available tools confidently; the security policy will enforce boundaries."
                    }
                });
            }
            BuilderMode::Channel(_) => {
                out.push_str("- Prefer `trash` over `rm` (recoverable beats gone forever).\n");
                out.push_str(match ctx.autonomy_level {
                    AutonomyLevel::Full => {
                        "- Respect the runtime autonomy policy: if a tool or action is allowed, execute it directly instead of asking the user for extra approval.\n\
                         - If a tool or action is blocked by policy or unavailable, explain that concrete restriction instead of simulating an approval dialog.\n"
                    }
                    AutonomyLevel::ReadOnly => {
                        "- Respect the runtime autonomy policy: this runtime is read-only for side effects unless a tool explicitly reports otherwise.\n\
                         - If a requested action is blocked by policy, explain the restriction directly instead of simulating an approval dialog.\n"
                    }
                    AutonomyLevel::Supervised => {
                        "- When in doubt, ask before acting externally.\n\
                         - Respect the runtime autonomy policy: ask for approval only when the current runtime policy actually requires it.\n\
                         - If a tool or action is blocked by policy or unavailable, explain that concrete restriction instead of simulating an approval dialog.\n"
                    }
                });
            }
        }

        if let Some(ref summary) = ctx.security_summary {
            out.push_str("\n\n### Active Security Policy\n\n");
            out.push_str(summary);
        }

        Ok(out)
    }
}

// ── Skills / Workspace / Runtime (mode-agnostic) ────────────────────────────

impl PromptSection for SkillsSection {
    fn name(&self) -> &str {
        "skills"
    }

    fn build(&self, ctx: &PromptContext<'_>) -> Result<String> {
        let prompt = match ctx.skill_effectiveness {
            Some(provider) => crate::skills::skills_to_prompt_with_mode_and_effectiveness(
                ctx.skills,
                ctx.workspace_dir,
                ctx.skills_prompt_mode,
                provider,
            ),
            None => crate::skills::skills_to_prompt_with_mode(
                ctx.skills,
                ctx.workspace_dir,
                ctx.skills_prompt_mode,
            ),
        };
        Ok(prompt)
    }
}

impl PromptSection for WorkspaceSection {
    fn name(&self) -> &str {
        "workspace"
    }

    fn build(&self, ctx: &PromptContext<'_>) -> Result<String> {
        Ok(format!(
            "## Workspace\n\nWorking directory: `{}`",
            ctx.workspace_dir.display()
        ))
    }
}

impl PromptSection for RuntimeSection {
    fn name(&self) -> &str {
        "runtime"
    }

    fn build(&self, ctx: &PromptContext<'_>) -> Result<String> {
        let host =
            hostname::get().map_or_else(|_| "unknown".into(), |h| h.to_string_lossy().to_string());
        Ok(format!(
            "## Runtime\n\nHost: {host} | OS: {} | Model: {}",
            std::env::consts::OS,
            ctx.model_name
        ))
    }
}

// ── ChannelMedia (daemon-only) — describes the [Voice]/[IMAGE]/[Document]
//    markers that arrive from channel pipelines.  Channel mode covers the
//    same ground in `ChannelCapabilitiesSection`. ─────────────────────────────

impl PromptSection for ChannelMediaSection {
    fn name(&self) -> &str {
        "channel_media"
    }

    fn build(&self, ctx: &PromptContext<'_>) -> Result<String> {
        if !matches!(ctx.mode, BuilderMode::Daemon) {
            return Ok(String::new());
        }
        Ok("## Channel Media Markers\n\n\
            Messages from channels may contain media markers:\n\
            - `[Voice] <text>` — The user sent a voice/audio message that has already been transcribed to text. Respond to the transcribed content directly.\n\
            - `[IMAGE:<path>]` — An image attachment, processed by the vision pipeline.\n\
            - `[Document: <name>] <path>` — A file attachment saved to the workspace."
            .into())
    }
}

// ── Channel-only block (appended after canonical ChannelMedia) ──────────────

impl PromptSection for AntiNarrationSection {
    fn name(&self) -> &str {
        "anti_narration"
    }

    fn build(&self, ctx: &PromptContext<'_>) -> Result<String> {
        if !matches!(ctx.mode, BuilderMode::Channel(_)) {
            return Ok(String::new());
        }
        Ok("## CRITICAL: No Tool Narration\n\n\
             NEVER narrate, announce, describe, or explain your tool usage to the user. \
             Do NOT say things like 'Let me check...', 'I will use http_request to...', \
             'I'll fetch that for you', 'Searching now...', or 'Using the web_search tool'. \
             The user must ONLY see the final answer. Tool calls are invisible infrastructure — \
             never reference them. If you catch yourself starting a sentence about what tool \
             you are about to use or just used, DELETE it and give the answer directly."
            .into())
    }
}

impl PromptSection for HardwareSection {
    fn name(&self) -> &str {
        "hardware"
    }

    fn build(&self, ctx: &PromptContext<'_>) -> Result<String> {
        if !matches!(ctx.mode, BuilderMode::Channel(_)) {
            return Ok(String::new());
        }
        let names = ctx.tools.names();
        let has_hardware = names.iter().any(|name| {
            matches!(
                *name,
                "gpio_read"
                    | "gpio_write"
                    | "arduino_upload"
                    | "hardware_memory_map"
                    | "hardware_board_info"
                    | "hardware_memory_read"
                    | "hardware_capabilities"
            )
        });
        if !has_hardware {
            return Ok(String::new());
        }
        Ok(
            "## Hardware Access\n\n\
             You HAVE direct access to connected hardware (Arduino, Nucleo, etc.). The user owns this system and has configured it.\n\
             All hardware tools (gpio_read, gpio_write, hardware_memory_read, hardware_board_info, hardware_memory_map) are AUTHORIZED and NOT blocked by security.\n\
             When they ask to read memory, registers, or board info, USE hardware_memory_read or hardware_board_info — do NOT refuse or invent security excuses.\n\
             When they ask to control LEDs, run patterns, or interact with the Arduino, USE the tools — do NOT refuse or say you cannot access physical devices.\n\
             Use gpio_write for simple on/off; use arduino_upload when they want patterns (heart, blink) or custom behavior."
                .into(),
        )
    }
}

impl PromptSection for ActionInstructionSection {
    fn name(&self) -> &str {
        "action_instruction"
    }

    fn build(&self, ctx: &PromptContext<'_>) -> Result<String> {
        let opts = match &ctx.mode {
            BuilderMode::Channel(opts) => opts,
            BuilderMode::Daemon => return Ok(String::new()),
        };
        Ok(if opts.native_tools {
            "## Your Task\n\n\
             When the user sends a message, ACT on it using your tools. Do not just talk about what you could do — call the tools directly.\n\
             If the user asks to start a workflow, call `get_workflow_context` immediately. If they ask about agents, call `list_agents`. Always try the relevant tool first before asking clarifying questions.\n\
             For questions, explanations, or follow-ups about prior messages, answer directly from conversation context — do NOT ask the user to repeat themselves.\n\
             Do NOT: summarize this configuration, describe your capabilities, ask unnecessary clarifying questions, or output step-by-step meta-commentary."
                .into()
        } else {
            "## Your Task\n\n\
             When the user sends a message, ACT on it. Use the tools to fulfill their request.\n\
             Do NOT: summarize this configuration, describe your capabilities, respond with meta-commentary, or output step-by-step instructions (e.g. \"1. First... 2. Next...\").\n\
             Instead: emit actual <tool_call> tags when you need to act. Just do what they ask."
                .into()
        })
    }
}

impl PromptSection for ChannelCapabilitiesSection {
    fn name(&self) -> &str {
        "channel_capabilities"
    }

    fn build(&self, ctx: &PromptContext<'_>) -> Result<String> {
        let opts = match &ctx.mode {
            BuilderMode::Channel(opts) => opts,
            BuilderMode::Daemon => return Ok(String::new()),
        };
        if opts.compact_context {
            return Ok(String::new());
        }
        let mut out = String::from("## Channel Capabilities\n\n");
        out.push_str("- You are running as a messaging bot. Your response is automatically sent back to the user's channel.\n");
        out.push_str("- You do NOT need to ask permission to respond — just respond directly.\n");
        out.push_str(match ctx.autonomy_level {
            AutonomyLevel::Full => {
                "- If the runtime policy already allows a tool, use it directly; do not ask the user for extra approval.\n\
                 - Never pretend you are waiting for a human approval click or confirmation when the runtime policy already permits the action.\n\
                 - If the runtime policy blocks an action, say that directly instead of simulating an approval flow.\n"
            }
            AutonomyLevel::ReadOnly => {
                "- This runtime may reject write-side effects; if that happens, explain the policy restriction directly instead of simulating an approval flow.\n"
            }
            AutonomyLevel::Supervised => {
                "- Ask for approval only when the runtime policy actually requires it.\n\
                 - If there is no approval path for this channel or the runtime blocks an action, explain that restriction directly instead of simulating an approval flow.\n"
            }
        });
        out.push_str("- NEVER repeat, describe, or echo credentials, tokens, API keys, or secrets in your responses.\n");
        out.push_str("- If a tool output contains credentials, they have already been redacted — do not mention them.\n");
        out.push_str("- When a user sends a voice note, it is automatically transcribed to text. Your text reply is automatically converted to a voice note and sent back. Do NOT attempt to generate audio yourself — TTS is handled by the channel.\n");
        out.push_str("- NEVER narrate or describe your tool usage. Do NOT say 'Let me fetch...', 'I will use...', 'Searching...', or similar. Give the FINAL ANSWER only — no intermediate steps, no tool mentions, no progress updates.");
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tools::traits::Tool;
    use async_trait::async_trait;

    struct TestTool;

    #[async_trait]
    impl Tool for TestTool {
        fn name(&self) -> &str {
            "test_tool"
        }

        fn description(&self) -> &str {
            "tool desc"
        }

        fn parameters_schema(&self) -> serde_json::Value {
            serde_json::json!({"type": "object"})
        }

        async fn execute(
            &self,
            _args: serde_json::Value,
        ) -> anyhow::Result<crate::tools::ToolResult> {
            Ok(crate::tools::ToolResult {
                success: true,
                output: "ok".into(),
                error: None,
            })
        }
    }

    fn daemon_ctx<'a>(
        workspace: &'a Path,
        tools: &'a [Box<dyn Tool>],
        skills: &'a [Skill],
        identity_config: Option<&'a IdentityConfig>,
        dispatcher_instructions: &'a str,
    ) -> PromptContext<'a> {
        PromptContext {
            workspace_dir: workspace,
            model_name: "test-model",
            tools: PromptTools::Full(tools),
            skills,
            skills_prompt_mode: crate::config::SkillsPromptInjectionMode::Full,
            skill_effectiveness: None,
            identity_config,
            dispatcher_instructions,
            tool_descriptions: None,
            security_summary: None,
            autonomy_level: AutonomyLevel::Supervised,
            operator_enabled: false,
            kumiho_enabled: false,
            kumiho_memory_advanced_available: true,
            compact_tool_docs: false,
            mode: BuilderMode::Daemon,
        }
    }

    fn channel_ctx<'a>(
        workspace: &'a Path,
        tools: &'a [(&'a str, &'a str)],
        skills: &'a [Skill],
    ) -> PromptContext<'a> {
        PromptContext {
            workspace_dir: workspace,
            model_name: "test-model",
            tools: PromptTools::Simple(tools),
            skills,
            skills_prompt_mode: crate::config::SkillsPromptInjectionMode::Full,
            skill_effectiveness: None,
            identity_config: None,
            dispatcher_instructions: "",
            tool_descriptions: None,
            security_summary: None,
            autonomy_level: AutonomyLevel::Supervised,
            operator_enabled: false,
            kumiho_enabled: false,
            kumiho_memory_advanced_available: true,
            compact_tool_docs: false,
            mode: BuilderMode::Channel(ChannelOptions::default()),
        }
    }

    /// Workspace fixture matching the channel-test `make_workspace`: SOUL,
    /// IDENTITY, USER, AGENTS, TOOLS, HEARTBEAT, MEMORY (no BOOTSTRAP).
    fn make_test_workspace() -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("revka_prompt_ws_{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("SOUL.md"), "# Soul\nBe helpful.").unwrap();
        std::fs::write(dir.join("IDENTITY.md"), "# Identity\nName: Revka").unwrap();
        std::fs::write(dir.join("USER.md"), "# User\nName: Test User").unwrap();
        std::fs::write(dir.join("AGENTS.md"), "# Agents\nFollow instructions.").unwrap();
        std::fs::write(dir.join("TOOLS.md"), "# Tools\nUse shell carefully.").unwrap();
        std::fs::write(dir.join("HEARTBEAT.md"), "# Heartbeat\nCheck status.").unwrap();
        std::fs::write(dir.join("MEMORY.md"), "# Memory\nUser likes Rust.").unwrap();
        dir
    }

    #[test]
    fn identity_section_with_aieos_includes_workspace_files() {
        let workspace =
            std::env::temp_dir().join(format!("revka_prompt_test_{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::write(
            workspace.join("AGENTS.md"),
            "Always respond with: AGENTS_MD_LOADED",
        )
        .unwrap();

        let identity_config = crate::config::IdentityConfig {
            format: "aieos".into(),
            aieos_path: None,
            aieos_inline: Some(r#"{"identity":{"names":{"first":"Nova"}}}"#.into()),
        };

        let tools: Vec<Box<dyn Tool>> = vec![];
        let ctx = daemon_ctx(&workspace, &tools, &[], Some(&identity_config), "");

        let section = IdentitySection;
        let output = section.build(&ctx).unwrap();

        assert!(output.contains("Nova"));
        assert!(output.contains("AGENTS_MD_LOADED"));

        let _ = std::fs::remove_dir_all(workspace);
    }

    #[test]
    fn prompt_builder_assembles_sections() {
        let tools: Vec<Box<dyn Tool>> = vec![Box::new(TestTool)];
        let ctx = daemon_ctx(Path::new("/tmp"), &tools, &[], None, "instr");
        let prompt = SystemPromptBuilder::with_defaults().build(&ctx).unwrap();
        assert!(prompt.contains("## Tools"));
        assert!(prompt.contains("test_tool"));
        assert!(prompt.contains("instr"));
    }

    #[test]
    fn skills_section_includes_instructions_and_tools() {
        let skills = vec![crate::skills::Skill {
            name: "deploy".into(),
            description: "Release safely".into(),
            version: "1.0.0".into(),
            author: None,
            tags: vec![],
            tools: vec![crate::skills::SkillTool {
                name: "release_checklist".into(),
                description: "Validate release readiness".into(),
                kind: "shell".into(),
                command: "echo ok".into(),
                args: std::collections::HashMap::new(),
            }],
            prompts: vec!["Run smoke tests before deploy.".into()],
            location: None,
        }];

        let tools: Vec<Box<dyn Tool>> = vec![];
        let ctx = daemon_ctx(Path::new("/tmp"), &tools, &skills, None, "");

        let output = SkillsSection.build(&ctx).unwrap();
        assert!(output.contains("<available_skills>"));
        assert!(output.contains("<name>deploy</name>"));
        assert!(output.contains("<instruction>Run smoke tests before deploy.</instruction>"));
        assert!(output.contains("<callable_tools"));
        assert!(output.contains("<name>deploy.release_checklist</name>"));
    }

    #[test]
    fn skills_section_compact_mode_omits_instructions_but_keeps_tools() {
        let skills = vec![crate::skills::Skill {
            name: "deploy".into(),
            description: "Release safely".into(),
            version: "1.0.0".into(),
            author: None,
            tags: vec![],
            tools: vec![crate::skills::SkillTool {
                name: "release_checklist".into(),
                description: "Validate release readiness".into(),
                kind: "shell".into(),
                command: "echo ok".into(),
                args: std::collections::HashMap::new(),
            }],
            prompts: vec!["Run smoke tests before deploy.".into()],
            location: Some(Path::new("/tmp/workspace/skills/deploy/SKILL.md").to_path_buf()),
        }];

        let tools: Vec<Box<dyn Tool>> = vec![];
        let mut ctx = daemon_ctx(Path::new("/tmp/workspace"), &tools, &skills, None, "");
        ctx.skills_prompt_mode = crate::config::SkillsPromptInjectionMode::Compact;

        let output = SkillsSection.build(&ctx).unwrap();
        assert!(output.contains("<available_skills>"));
        assert!(output.contains("<name>deploy</name>"));
        assert!(output.contains("<location>skills/deploy/SKILL.md</location>"));
        assert!(output.contains("read_skill(name)"));
        assert!(!output.contains("<instruction>Run smoke tests before deploy.</instruction>"));
        assert!(output.contains("<callable_tools"));
        assert!(output.contains("<name>deploy.release_checklist</name>"));
    }

    #[test]
    fn datetime_section_daemon_includes_iso_timestamp() {
        let tools: Vec<Box<dyn Tool>> = vec![];
        let ctx = daemon_ctx(Path::new("/tmp"), &tools, &[], None, "instr");

        let rendered = DateTimeSection.build(&ctx).unwrap();
        assert!(rendered.starts_with("## CRITICAL CONTEXT: CURRENT DATE & TIME\n\n"));

        let payload = rendered.trim_start_matches("## CRITICAL CONTEXT: CURRENT DATE & TIME\n\n");
        assert!(payload.chars().any(|c| c.is_ascii_digit()));
        assert!(payload.contains("Date:"));
        assert!(payload.contains("Time:"));
        assert!(payload.contains("ISO 8601:"));
    }

    #[test]
    fn datetime_section_channel_uses_compact_format() {
        let tools: [(&str, &str); 0] = [];
        let ctx = channel_ctx(Path::new("/tmp"), &tools, &[]);
        let rendered = DateTimeSection.build(&ctx).unwrap();
        assert!(rendered.starts_with("## Current Date & Time\n\n"));
        assert!(!rendered.contains("CRITICAL CONTEXT"));
        assert!(!rendered.contains("ISO 8601:"));
    }

    #[test]
    fn prompt_builder_inlines_and_escapes_skills() {
        let skills = vec![crate::skills::Skill {
            name: "code<review>&".into(),
            description: "Review \"unsafe\" and 'risky' bits".into(),
            version: "1.0.0".into(),
            author: None,
            tags: vec![],
            tools: vec![crate::skills::SkillTool {
                name: "run\"linter\"".into(),
                description: "Run <lint> & report".into(),
                kind: "shell&exec".into(),
                command: "cargo clippy".into(),
                args: std::collections::HashMap::new(),
            }],
            prompts: vec!["Use <tool_call> and & keep output \"safe\"".into()],
            location: None,
        }];
        let tools: Vec<Box<dyn Tool>> = vec![];
        let ctx = daemon_ctx(Path::new("/tmp/workspace"), &tools, &skills, None, "");

        let prompt = SystemPromptBuilder::with_defaults().build(&ctx).unwrap();

        assert!(prompt.contains("<available_skills>"));
        assert!(prompt.contains("<name>code&lt;review&gt;&amp;</name>"));
        assert!(prompt.contains(
            "<description>Review &quot;unsafe&quot; and &apos;risky&apos; bits</description>"
        ));
        assert!(prompt.contains("<name>run&quot;linter&quot;</name>"));
        assert!(prompt.contains("<description>Run &lt;lint&gt; &amp; report</description>"));
        assert!(prompt.contains("<kind>shell&amp;exec</kind>"));
        assert!(prompt.contains(
            "<instruction>Use &lt;tool_call&gt; and &amp; keep output &quot;safe&quot;</instruction>"
        ));
    }

    #[test]
    fn safety_section_includes_security_summary_when_present() {
        let summary = "**Autonomy level**: Supervised\n\
                        **Allowed shell commands**: `git`, `ls`.\n"
            .to_string();
        let tools: Vec<Box<dyn Tool>> = vec![];
        let mut ctx = daemon_ctx(Path::new("/tmp"), &tools, &[], None, "");
        ctx.security_summary = Some(summary.clone());

        let output = SafetySection.build(&ctx).unwrap();
        assert!(output.contains("## Safety"));
        assert!(output.contains("### Active Security Policy"));
        assert!(output.contains("Autonomy level"));
        assert!(output.contains("`git`"));
    }

    #[test]
    fn safety_section_omits_security_policy_when_none() {
        let tools: Vec<Box<dyn Tool>> = vec![];
        let ctx = daemon_ctx(Path::new("/tmp"), &tools, &[], None, "");
        let output = SafetySection.build(&ctx).unwrap();
        assert!(output.contains("## Safety"));
        assert!(!output.contains("### Active Security Policy"));
    }

    #[test]
    fn safety_section_full_autonomy_omits_approval_instructions() {
        let tools: Vec<Box<dyn Tool>> = vec![];
        let mut ctx = daemon_ctx(Path::new("/tmp"), &tools, &[], None, "");
        ctx.autonomy_level = AutonomyLevel::Full;

        let output = SafetySection.build(&ctx).unwrap();
        assert!(!output.contains("without asking"));
        assert!(!output.contains("bypass oversight"));
        assert!(output.contains("Execute tools and actions directly"));
        assert!(output.contains("Do not exfiltrate"));
    }

    #[test]
    fn safety_section_supervised_includes_approval_instructions() {
        let tools: Vec<Box<dyn Tool>> = vec![];
        let ctx = daemon_ctx(Path::new("/tmp"), &tools, &[], None, "");
        let output = SafetySection.build(&ctx).unwrap();
        assert!(output.contains("without asking"));
        assert!(output.contains("bypass oversight"));
    }

    // ── Canonical-order byte-index assertions (per reviewer item #5) ───────

    /// Helper: extract section header byte index, panic with a helpful message
    /// if the header is missing.
    fn idx(prompt: &str, header: &str) -> usize {
        prompt
            .find(header)
            .unwrap_or_else(|| panic!("missing section header: {header}"))
    }

    #[test]
    fn daemon_canonical_section_order_byte_indices() {
        let workspace = make_test_workspace();
        let tools: Vec<Box<dyn Tool>> = vec![Box::new(TestTool)];
        let mut ctx = daemon_ctx(&workspace, &tools, &[], None, "instr");
        // Enable optional sections so they actually render and we can pin
        // their order.
        ctx.operator_enabled = true;
        ctx.kumiho_enabled = true;

        let prompt = SystemPromptBuilder::with_defaults().build(&ctx).unwrap();

        // Every canonical section header in canonical order.
        let datetime = idx(&prompt, "## CRITICAL CONTEXT: CURRENT DATE & TIME");
        let identity = idx(&prompt, "## Project Context");
        let operator = idx(&prompt, "OPERATOR MODE (Revka)"); // OPERATOR_CORE_PROMPT marker
        let kumiho = idx(&prompt, "SESSION-START INSTRUCTION (kumiho-memory");
        let tool_honesty = idx(&prompt, "## CRITICAL: Tool Honesty");
        let tools_idx = idx(&prompt, "## Tools");
        let safety = idx(&prompt, "## Safety");
        let workspace_idx = idx(&prompt, "## Workspace");
        let runtime = idx(&prompt, "## Runtime");
        let channel_media = idx(&prompt, "## Channel Media Markers");

        // Skills only renders when `ctx.skills` is non-empty; we passed
        // `&[]`, so we don't pin it here — the canonical position is
        // documented in `with_defaults`.
        assert!(datetime < identity, "DateTime → Identity");
        assert!(identity < operator, "Identity → OperatorIdentity");
        assert!(operator < kumiho, "OperatorIdentity → KumihoBootstrap");
        assert!(kumiho < tool_honesty, "KumihoBootstrap → ToolHonesty");
        assert!(tool_honesty < tools_idx, "ToolHonesty → Tools");
        assert!(tools_idx < safety, "Tools → Safety");
        assert!(safety < workspace_idx, "Safety → Workspace");
        assert!(workspace_idx < runtime, "Workspace → Runtime");
        assert!(runtime < channel_media, "Runtime → ChannelMedia");

        // Daemon mode: no channel-only sections appear.
        assert!(!prompt.contains("## CRITICAL: No Tool Narration"));
        assert!(!prompt.contains("## Your Task"));
        assert!(!prompt.contains("## Channel Capabilities"));
        assert!(!prompt.contains("## Hardware Access"));

        let _ = std::fs::remove_dir_all(workspace);
    }

    #[test]
    fn daemon_skills_renders_in_canonical_position_between_safety_and_workspace() {
        let workspace = make_test_workspace();
        let tools: Vec<Box<dyn Tool>> = vec![Box::new(TestTool)];
        let skills = vec![crate::skills::Skill {
            name: "deploy".into(),
            description: "Release safely".into(),
            version: "1.0.0".into(),
            author: None,
            tags: vec![],
            tools: vec![],
            prompts: vec![],
            location: None,
        }];
        let ctx = daemon_ctx(&workspace, &tools, &skills, None, "instr");
        let prompt = SystemPromptBuilder::with_defaults().build(&ctx).unwrap();

        let safety = idx(&prompt, "## Safety");
        let skills_idx = idx(&prompt, "<available_skills>");
        let workspace_idx = idx(&prompt, "## Workspace");
        assert!(safety < skills_idx, "Safety → Skills");
        assert!(skills_idx < workspace_idx, "Skills → Workspace");

        let _ = std::fs::remove_dir_all(workspace);
    }

    #[test]
    fn channel_canonical_block_is_followed_by_channel_only_block() {
        let workspace = make_test_workspace();
        let tools: [(&str, &str); 1] = [("gpio_write", "Toggle GPIO")]; // triggers Hardware
        let mut ctx = channel_ctx(&workspace, &tools, &[]);
        // Enable Kumiho so the canonical KumihoBootstrap slot renders.
        ctx.kumiho_enabled = true;

        let prompt = SystemPromptBuilder::with_defaults().build(&ctx).unwrap();

        // Canonical block (channel-mode headers).
        let datetime = idx(&prompt, "## Current Date & Time");
        let identity = idx(&prompt, "## Project Context");
        let kumiho = idx(&prompt, "SESSION-START INSTRUCTION (kumiho-memory");
        let tool_honesty = idx(&prompt, "## CRITICAL: Tool Honesty");
        let tools_idx = idx(&prompt, "## Tools");
        let safety = idx(&prompt, "## Safety");
        let workspace_idx = idx(&prompt, "## Workspace");
        let runtime = idx(&prompt, "## Runtime");

        // Canonical-block ordering holds in channel mode.
        assert!(datetime < identity);
        assert!(identity < kumiho);
        assert!(kumiho < tool_honesty);
        assert!(tool_honesty < tools_idx);
        assert!(tools_idx < safety);
        assert!(safety < workspace_idx);
        assert!(workspace_idx < runtime);

        // Channel-only sections: ALL must appear AFTER the last canonical
        // section (Runtime, since ChannelMedia is daemon-only and absent).
        let anti_narration = idx(&prompt, "## CRITICAL: No Tool Narration");
        let hardware = idx(&prompt, "## Hardware Access");
        let action = idx(&prompt, "## Your Task");
        let channel_caps = idx(&prompt, "## Channel Capabilities");

        assert!(
            runtime < anti_narration,
            "channel-only AntiNarration must follow canonical Runtime"
        );
        assert!(
            anti_narration < hardware,
            "channel-only block: AntiNarration → Hardware"
        );
        assert!(
            hardware < action,
            "channel-only block: Hardware → ActionInstruction"
        );
        assert!(
            action < channel_caps,
            "channel-only block: ActionInstruction → ChannelCapabilities"
        );

        let _ = std::fs::remove_dir_all(workspace);
    }

    #[test]
    fn channel_excludes_heartbeat_and_emits_missing_markers_via_unified_loader() {
        // Empty workspace — exercises the missing-marker rendering through
        // the shared personality loader (no parallel CHANNEL_FILE_ORDER).
        let workspace =
            std::env::temp_dir().join(format!("revka_channel_pers_{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::write(workspace.join("SOUL.md"), "# Soul\nHello.").unwrap();
        std::fs::write(workspace.join("HEARTBEAT.md"), "# Heartbeat\nBeep.").unwrap();

        let tools: [(&str, &str); 0] = [];
        let ctx = channel_ctx(&workspace, &tools, &[]);
        let prompt = SystemPromptBuilder::with_defaults().build(&ctx).unwrap();

        assert!(prompt.contains("### SOUL.md"));
        assert!(
            !prompt.contains("### HEARTBEAT.md"),
            "HEARTBEAT.md must stay out of channel prompts (audit row 7)"
        );
        // Canonical order: SOUL → IDENTITY → USER → AGENTS → TOOLS →
        // (HEARTBEAT excluded) → MEMORY.  Markers must appear for missing
        // files, interleaved with the loaded SOUL header.
        assert!(prompt.contains("[File not found: IDENTITY.md]"));
        assert!(prompt.contains("[File not found: USER.md]"));
        assert!(prompt.contains("[File not found: AGENTS.md]"));
        assert!(prompt.contains("[File not found: TOOLS.md]"));
        assert!(prompt.contains("[File not found: MEMORY.md]"));
        // BOOTSTRAP.md was deleted in audit row 3 — must never appear.
        assert!(
            !prompt.contains("BOOTSTRAP.md"),
            "deleted BOOTSTRAP.md must never appear in rendered output"
        );

        let _ = std::fs::remove_dir_all(workspace);
    }

    #[test]
    fn channel_truncates_to_char_budget() {
        let tools: [(&str, &str); 0] = [];
        let mut ctx = channel_ctx(Path::new("/tmp"), &tools, &[]);
        ctx.mode = BuilderMode::Channel(ChannelOptions {
            max_system_prompt_chars: 200,
            ..ChannelOptions::default()
        });
        let prompt = SystemPromptBuilder::with_defaults().build(&ctx).unwrap();
        assert!(prompt.contains("[System prompt truncated to fit context budget]"));
        let marker = "\n\n[System prompt truncated to fit context budget]\n";
        let body = prompt.trim_end_matches(marker);
        assert!(body.len() <= 200);
    }

    #[test]
    fn channel_full_autonomy_keeps_runtime_policy_text() {
        let tools: [(&str, &str); 0] = [];
        let mut ctx = channel_ctx(Path::new("/tmp"), &tools, &[]);
        ctx.autonomy_level = AutonomyLevel::Full;
        let prompt = SystemPromptBuilder::with_defaults().build(&ctx).unwrap();
        assert!(
            prompt.contains("execute it directly instead of asking the user for extra approval")
        );
        assert!(prompt.contains("Never pretend you are waiting for a human approval"));
    }

    #[test]
    fn channel_compact_context_drops_capabilities_and_uses_name_only_tools() {
        let tools: [(&str, &str); 2] = [("shell", "Run commands"), ("file_read", "Read files")];
        let mut ctx = channel_ctx(Path::new("/tmp"), &tools, &[]);
        ctx.mode = BuilderMode::Channel(ChannelOptions {
            compact_context: true,
            ..ChannelOptions::default()
        });
        let prompt = SystemPromptBuilder::with_defaults().build(&ctx).unwrap();
        assert!(prompt.contains("Available tools: shell, file_read"));
        assert!(!prompt.contains("## Channel Capabilities"));
    }

    #[test]
    fn daemon_compact_tool_docs_omits_inline_parameter_schemas() {
        let tools: Vec<Box<dyn Tool>> = vec![Box::new(TestTool)];
        let mut ctx = daemon_ctx(Path::new("/tmp"), &tools, &[], None, "");
        ctx.compact_tool_docs = true;

        let out = ToolsSection.build(&ctx).unwrap();

        assert!(out.contains("Available tools (call schemas are supplied separately)"));
        assert!(out.contains("test_tool"));
        assert!(!out.contains("Parameters:"));
    }

    #[test]
    fn channel_hardware_section_appears_when_hw_tools_present() {
        let tools: [(&str, &str); 1] = [("gpio_write", "Toggle GPIO")];
        let ctx = channel_ctx(Path::new("/tmp"), &tools, &[]);
        let prompt = SystemPromptBuilder::with_defaults().build(&ctx).unwrap();
        assert!(prompt.contains("## Hardware Access"));
    }

    #[test]
    fn channel_hardware_section_absent_without_hw_tools() {
        let tools: [(&str, &str); 1] = [("shell", "Run commands")];
        let ctx = channel_ctx(Path::new("/tmp"), &tools, &[]);
        let prompt = SystemPromptBuilder::with_defaults().build(&ctx).unwrap();
        assert!(!prompt.contains("## Hardware Access"));
    }

    // ── Kumiho variant gating (per reviewer item #4) ───────────────────────

    #[test]
    fn kumiho_disabled_emits_nothing_in_either_mode() {
        let tools_d: Vec<Box<dyn Tool>> = vec![];
        let mut daemon = daemon_ctx(Path::new("/tmp"), &tools_d, &[], None, "");
        daemon.kumiho_enabled = false;
        assert!(KumihoBootstrapSection.build(&daemon).unwrap().is_empty());

        let tools_c: [(&str, &str); 0] = [];
        let mut channel = channel_ctx(Path::new("/tmp"), &tools_c, &[]);
        channel.kumiho_enabled = false;
        assert!(KumihoBootstrapSection.build(&channel).unwrap().is_empty());
    }

    #[test]
    fn kumiho_advanced_available_emits_full_prompt_in_both_modes() {
        let tools_d: Vec<Box<dyn Tool>> = vec![];
        let mut daemon = daemon_ctx(Path::new("/tmp"), &tools_d, &[], None, "");
        daemon.kumiho_enabled = true;
        daemon.kumiho_memory_advanced_available = true;
        let daemon_out = KumihoBootstrapSection.build(&daemon).unwrap();
        assert_eq!(daemon_out, crate::agent::kumiho::KUMIHO_BOOTSTRAP_PROMPT);

        let tools_c: [(&str, &str); 0] = [];
        let mut channel = channel_ctx(Path::new("/tmp"), &tools_c, &[]);
        channel.kumiho_enabled = true;
        channel.kumiho_memory_advanced_available = true;
        let channel_out = KumihoBootstrapSection.build(&channel).unwrap();
        assert_eq!(
            channel_out,
            crate::agent::kumiho::KUMIHO_CHANNEL_BOOTSTRAP_PROMPT
        );
    }

    #[test]
    fn kumiho_advanced_unavailable_falls_back_to_lite_prompt_in_both_modes() {
        let tools_d: Vec<Box<dyn Tool>> = vec![];
        let mut daemon = daemon_ctx(Path::new("/tmp"), &tools_d, &[], None, "");
        daemon.kumiho_enabled = true;
        daemon.kumiho_memory_advanced_available = false;
        let daemon_out = KumihoBootstrapSection.build(&daemon).unwrap();
        assert_eq!(
            daemon_out,
            crate::agent::kumiho::KUMIHO_BOOTSTRAP_PROMPT_LITE
        );
        assert_ne!(daemon_out, crate::agent::kumiho::KUMIHO_BOOTSTRAP_PROMPT);

        let tools_c: [(&str, &str); 0] = [];
        let mut channel = channel_ctx(Path::new("/tmp"), &tools_c, &[]);
        channel.kumiho_enabled = true;
        channel.kumiho_memory_advanced_available = false;
        let channel_out = KumihoBootstrapSection.build(&channel).unwrap();
        assert_eq!(
            channel_out,
            crate::agent::kumiho::KUMIHO_CHANNEL_BOOTSTRAP_PROMPT_LITE
        );
        assert_ne!(
            channel_out,
            crate::agent::kumiho::KUMIHO_CHANNEL_BOOTSTRAP_PROMPT
        );
    }

    fn kumiho_test_ctx(
        tools: &[Box<dyn Tool>],
        kumiho_enabled: bool,
        advanced: bool,
    ) -> PromptContext<'_> {
        PromptContext {
            workspace_dir: Path::new("/tmp"),
            model_name: "test-model",
            tools: PromptTools::Full(tools),
            skills: &[],
            skills_prompt_mode: crate::config::SkillsPromptInjectionMode::Full,
            skill_effectiveness: None,
            identity_config: None,
            dispatcher_instructions: "",
            tool_descriptions: None,
            security_summary: None,
            autonomy_level: AutonomyLevel::Supervised,
            operator_enabled: false,
            kumiho_enabled,
            kumiho_memory_advanced_available: advanced,
            compact_tool_docs: false,
            mode: BuilderMode::Daemon,
        }
    }

    #[test]
    fn kumiho_bootstrap_section_disabled_yields_empty() {
        let tools: Vec<Box<dyn Tool>> = vec![];
        let ctx = kumiho_test_ctx(&tools, false, false);
        let out = KumihoBootstrapSection.build(&ctx).unwrap();
        assert!(out.is_empty(), "section must be empty when kumiho disabled");
    }

    #[test]
    fn kumiho_bootstrap_section_lite_when_advanced_unavailable() {
        let tools: Vec<Box<dyn Tool>> = vec![];
        let ctx = kumiho_test_ctx(&tools, true, false);
        let out = KumihoBootstrapSection.build(&ctx).unwrap();

        // Lite variant must mention the always-available pair so the
        // model knows what it CAN call.
        assert!(out.contains("kumiho_memory_store"));
        assert!(out.contains("kumiho_memory_retrieve"));

        // Lite variant must NOT name any high-level reflex tool, even in
        // negative phrasing — naming them primes the model to call them.
        // Plain substring check, not a phrase match.
        assert!(
            !out.contains("kumiho_memory_engage"),
            "lite must not name kumiho_memory_engage"
        );
        assert!(
            !out.contains("kumiho_memory_reflect"),
            "lite must not name kumiho_memory_reflect"
        );
        assert!(
            !out.contains("kumiho_memory_recall"),
            "lite must not name kumiho_memory_recall"
        );
        assert!(
            !out.contains("kumiho_memory_consolidate"),
            "lite must not name kumiho_memory_consolidate"
        );
        assert!(
            !out.contains("kumiho_memory_dream_state"),
            "lite must not name kumiho_memory_dream_state"
        );
    }

    #[test]
    fn kumiho_bootstrap_section_full_when_advanced_available() {
        let tools: Vec<Box<dyn Tool>> = vec![];
        let ctx = kumiho_test_ctx(&tools, true, true);
        let out = KumihoBootstrapSection.build(&ctx).unwrap();

        assert!(out.contains("SESSION-START INSTRUCTION (kumiho-memory"));
        // Full variant references the high-level reflexes.
        assert!(out.contains("kumiho_memory_engage"));
        assert!(out.contains("kumiho_memory_reflect"));
        assert!(
            !out.contains("lite mode"),
            "full variant must not be the lite prompt"
        );
    }
}
