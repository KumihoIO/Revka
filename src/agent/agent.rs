use crate::agent::dispatcher::{
    NativeToolDispatcher, ParsedToolCall, ToolDispatcher, ToolExecutionResult, XmlToolDispatcher,
};
use crate::agent::memory_loader::{DefaultMemoryLoader, MemoryLoader};
use crate::agent::prompt::{PromptContext, SystemPromptBuilder};
use crate::config::Config;
use crate::i18n::ToolDescriptions;
use crate::memory::{self, Memory, MemoryCategory};
use crate::observability::{self, Observer, ObserverEvent};
use crate::providers::{self, ChatMessage, ChatRequest, ConversationMessage, Provider};
use crate::runtime;
use crate::security::SecurityPolicy;
use crate::tools::{self, Tool, ToolSpec};
use anyhow::Result;
use chrono::{Datelike, Timelike};
use std::collections::HashMap;
use std::io::Write as IoWrite;
use std::sync::Arc;
use std::time::{Duration, Instant};

/// Events emitted during a streamed agent turn.
///
/// Consumers receive these through a `tokio::sync::mpsc::Sender<TurnEvent>`
/// passed to [`Agent::turn_streamed`].
#[derive(Debug, Clone)]
pub enum TurnEvent {
    /// A text chunk from the LLM response (may arrive many times).
    Chunk { delta: String },
    /// A reasoning/thinking chunk from a thinking model (may arrive many times).
    Thinking { delta: String },
    /// The agent is invoking a tool.
    ToolCall {
        name: String,
        args: serde_json::Value,
    },
    /// A tool has returned a result.
    ToolResult { name: String, output: String },
    /// A operator orchestration status update (spawning/waiting/collecting).
    OperatorStatus { phase: String, detail: String },
}

/// Substring the Architect frontend embeds in `page_context` on every chat
/// turn (see `web/src/construct/components/workflows/ArchitectPanel.tsx`).
/// The gateway forwards `page_context` containing this marker into the user
/// message before the agent turn starts.  Regular Operator chats never carry
/// the marker, so its presence is a reliable per-turn Architect signal.
pub(crate) const ARCHITECT_EDITOR_STATE_MARKER: &str = "<editor-state>";

/// Tools that persist workflow state outside the editor's control (writing
/// files, creating Kumiho revisions, kicking off runs, etc.).  In Architect
/// mode the only legitimate proposal channel is `propose_workflow_yaml`, so
/// we strip these from the tool list advertised to the LLM — the LLM cannot
/// call what it cannot see.  Names match the bare operator-mcp tool name;
/// the `construct-operator__` prefix is stripped before comparison.
///
/// `validate_workflow` and `dry_run_workflow` are also denied: they give the
/// LLM a "I sanity-checked, my job is done, I'll just print the YAML in chat"
/// exit path that doesn't surface the YAML to the editor.
/// `propose_workflow_yaml` validates internally — it's the only validation
/// path the Architect needs.
pub(crate) const ARCHITECT_DENIED_TOOLS: &[&str] = &[
    "create_workflow",
    "revise_workflow",
    "register_workflow",
    "save_workflow_yaml",
    "save_workflow_preset",
    "run_workflow",
    "delete_workflow",
    "deprecate_workflow",
    "validate_workflow",
    "dry_run_workflow",
];

const EMPTY_FINAL_AFTER_TOOLS_RETRY_PROMPT: &str = "The previous model response was empty after tool execution. Provide the final answer to the user now, based on the completed tool results. Do not leave the response blank.";
const EMPTY_FINAL_AFTER_TOOLS_FALLBACK: &str = "I completed tool work, but the model returned an empty final response. Please retry the request or ask me to summarize the latest tool results.";
const EMPTY_FINAL_FALLBACK: &str =
    "The model returned an empty response. Please retry the request.";

/// True when the current turn's user message carries the Architect
/// editor-state marker.  See [`ARCHITECT_EDITOR_STATE_MARKER`].
pub(crate) fn is_architect_turn(user_message: &str) -> bool {
    user_message.contains(ARCHITECT_EDITOR_STATE_MARKER)
}

/// Filter Architect-denied persistence tools out of `tool_specs` when the
/// turn is operating in Architect mode.  No-op for regular operator chats.
pub(crate) fn filter_tool_specs_for_architect(tool_specs: &mut Vec<ToolSpec>, user_message: &str) {
    if !is_architect_turn(user_message) {
        return;
    }
    tool_specs.retain(|spec| {
        // MCP tools come prefixed with `construct-operator__`; bare tools
        // (built into the gateway) appear with their plain name.  Match
        // against the suffix after the last `__` so both forms are caught.
        let bare = spec.name.rsplit("__").next().unwrap_or(spec.name.as_str());
        !ARCHITECT_DENIED_TOOLS.contains(&bare)
    });
}

/// Approximate context window (tokens) for a given model name.
///
/// Used by `turn_streamed` to size `ContextCompressor` so the Operator chat
/// can compress *before* it exceeds the provider's hard limit.  Mirrors the
/// model→window heuristic used in `loop_.rs` for the interactive loop.
///
/// Falls back to a conservative `128_000` for unknown models — high enough
/// not to over-compress, low enough that a single 1M-token tool result will
/// trigger compaction.
pub(crate) fn context_window_for_model(model: &str) -> usize {
    // Strip provider prefix (e.g. "anthropic/claude-opus-4-7" -> "claude-opus-4-7")
    // so OpenRouter-style and bare model names map to the same window.
    let bare = model.rsplit('/').next().unwrap_or(model);

    // Anthropic Claude 4-family (Opus/Sonnet/Haiku 4.x): 1M context.
    if bare.starts_with("claude-opus-4")
        || bare.starts_with("claude-sonnet-4")
        || bare.starts_with("claude-haiku-4")
        || bare.starts_with("claude-4")
    {
        1_000_000
    } else if bare.starts_with("claude-3") || bare.starts_with("claude-") {
        // Older Claude 3.x / 3.5 / 3.7: 200K.
        200_000
    } else if bare.starts_with("gpt-4o") || bare.starts_with("gpt-5") {
        128_000
    } else if bare.starts_with("o1") || bare.starts_with("o3") {
        200_000
    } else if bare.starts_with("gemini-2") || bare.starts_with("gemini-1.5") {
        1_000_000
    } else {
        // Conservative default — better to over-compress than blow up the request.
        128_000
    }
}

pub struct Agent {
    provider: Box<dyn Provider>,
    /// Logical provider name (e.g. "anthropic", "openrouter") used for cost
    /// tracker pricing lookup and observer event attribution.
    provider_name: String,
    tools: Vec<Box<dyn Tool>>,
    tool_specs: Vec<ToolSpec>,
    memory: Arc<dyn Memory>,
    observer: Arc<dyn Observer>,
    prompt_builder: SystemPromptBuilder,
    tool_dispatcher: Box<dyn ToolDispatcher>,
    memory_loader: Box<dyn MemoryLoader>,
    config: crate::config::AgentConfig,
    model_name: String,
    temperature: f64,
    workspace_dir: std::path::PathBuf,
    identity_config: crate::config::IdentityConfig,
    skills: Vec<crate::skills::Skill>,
    skills_prompt_mode: crate::config::SkillsPromptInjectionMode,
    auto_save: bool,
    memory_session_id: Option<String>,
    history: Vec<ConversationMessage>,
    classification_config: crate::config::QueryClassificationConfig,
    available_hints: Vec<String>,
    route_model_by_hint: HashMap<String, String>,
    allowed_tools: Option<Vec<String>>,
    response_cache: Option<Arc<crate::memory::response_cache::ResponseCache>>,
    tool_descriptions: Option<ToolDescriptions>,
    /// Pre-rendered security policy summary injected into the system prompt
    /// so the LLM knows the concrete constraints before making tool calls.
    security_summary: Option<String>,
    /// Autonomy level from config; controls safety prompt instructions.
    autonomy_level: crate::security::AutonomyLevel,
    /// Activated MCP tools for deferred loading mode.
    /// When MCP deferred loading is enabled, tools are activated via `tool_search`
    /// and stored here for lookup during tool execution.
    activated_tools: Option<Arc<std::sync::Mutex<crate::tools::ActivatedToolSet>>>,
    /// Whether Kumiho memory is enabled — used to append the session-bootstrap
    /// prompt to the system prompt so the agent knows how to use Kumiho MCP tools.
    kumiho_enabled: bool,
    /// Whether the high-level Kumiho memory tools (`kumiho_memory_engage`,
    /// `reflect`, `recall`, `consolidate`, `dream_state`) are registered in
    /// the sidecar — i.e. whether the `kumiho_memory` Python package is
    /// installed in the venv. Drives lite-vs-full bootstrap prompt selection.
    kumiho_memory_advanced_available: bool,
    /// Whether Operator orchestration is enabled — used to append the operator
    /// prompt so the agent knows how to delegate to sub-agents.
    operator_enabled: bool,
    /// Optional process-wide cache of skill effectiveness scores.  When
    /// present, the prompt builder reranks skills by recency-weighted
    /// success rate so high-performing skills appear first in the
    /// `<available_skills>` block.  Built once at daemon startup and
    /// shared across all agent constructions.
    skill_effectiveness: Option<Arc<crate::skills::EffectivenessCache>>,
    /// Pre-rendered "Deferred Tools" prompt section listing MCP tools
    /// (e.g. user-added servers like OpenCrab) that are not eagerly
    /// loaded but are discoverable via `tool_search`. Each server's
    /// tools are grouped under its `instructions` header so the agent
    /// can route to the right server by domain even if the user doesn't
    /// name it explicitly. Empty when no deferred MCP tools are
    /// configured. Appended to the system prompt by `build_system_prompt`.
    mcp_deferred_section: String,
}

pub struct AgentBuilder {
    provider: Option<Box<dyn Provider>>,
    provider_name: Option<String>,
    tools: Option<Vec<Box<dyn Tool>>>,
    memory: Option<Arc<dyn Memory>>,
    observer: Option<Arc<dyn Observer>>,
    prompt_builder: Option<SystemPromptBuilder>,
    tool_dispatcher: Option<Box<dyn ToolDispatcher>>,
    memory_loader: Option<Box<dyn MemoryLoader>>,
    config: Option<crate::config::AgentConfig>,
    model_name: Option<String>,
    temperature: Option<f64>,
    workspace_dir: Option<std::path::PathBuf>,
    identity_config: Option<crate::config::IdentityConfig>,
    skills: Option<Vec<crate::skills::Skill>>,
    skills_prompt_mode: Option<crate::config::SkillsPromptInjectionMode>,
    auto_save: Option<bool>,
    memory_session_id: Option<String>,
    classification_config: Option<crate::config::QueryClassificationConfig>,
    available_hints: Option<Vec<String>>,
    route_model_by_hint: Option<HashMap<String, String>>,
    allowed_tools: Option<Vec<String>>,
    response_cache: Option<Arc<crate::memory::response_cache::ResponseCache>>,
    tool_descriptions: Option<ToolDescriptions>,
    security_summary: Option<String>,
    autonomy_level: Option<crate::security::AutonomyLevel>,
    activated_tools: Option<Arc<std::sync::Mutex<crate::tools::ActivatedToolSet>>>,
    kumiho_enabled: bool,
    kumiho_memory_advanced_available: bool,
    operator_enabled: bool,
    skill_effectiveness: Option<Arc<crate::skills::EffectivenessCache>>,
    mcp_deferred_section: String,
}

impl AgentBuilder {
    pub fn new() -> Self {
        Self {
            provider: None,
            provider_name: None,
            tools: None,
            memory: None,
            observer: None,
            prompt_builder: None,
            tool_dispatcher: None,
            memory_loader: None,
            config: None,
            model_name: None,
            temperature: None,
            workspace_dir: None,
            identity_config: None,
            skills: None,
            skills_prompt_mode: None,
            auto_save: None,
            memory_session_id: None,
            classification_config: None,
            available_hints: None,
            route_model_by_hint: None,
            allowed_tools: None,
            response_cache: None,
            tool_descriptions: None,
            security_summary: None,
            autonomy_level: None,
            activated_tools: None,
            kumiho_enabled: false,
            kumiho_memory_advanced_available: false,
            operator_enabled: false,
            skill_effectiveness: None,
            mcp_deferred_section: String::new(),
        }
    }

    pub fn provider(mut self, provider: Box<dyn Provider>) -> Self {
        self.provider = Some(provider);
        self
    }

    pub fn provider_name(mut self, name: impl Into<String>) -> Self {
        self.provider_name = Some(name.into());
        self
    }

    pub fn tools(mut self, tools: Vec<Box<dyn Tool>>) -> Self {
        self.tools = Some(tools);
        self
    }

    pub fn memory(mut self, memory: Arc<dyn Memory>) -> Self {
        self.memory = Some(memory);
        self
    }

    pub fn observer(mut self, observer: Arc<dyn Observer>) -> Self {
        self.observer = Some(observer);
        self
    }

    pub fn prompt_builder(mut self, prompt_builder: SystemPromptBuilder) -> Self {
        self.prompt_builder = Some(prompt_builder);
        self
    }

    pub fn tool_dispatcher(mut self, tool_dispatcher: Box<dyn ToolDispatcher>) -> Self {
        self.tool_dispatcher = Some(tool_dispatcher);
        self
    }

    pub fn memory_loader(mut self, memory_loader: Box<dyn MemoryLoader>) -> Self {
        self.memory_loader = Some(memory_loader);
        self
    }

    pub fn config(mut self, config: crate::config::AgentConfig) -> Self {
        self.config = Some(config);
        self
    }

    pub fn model_name(mut self, model_name: String) -> Self {
        self.model_name = Some(model_name);
        self
    }

    pub fn temperature(mut self, temperature: f64) -> Self {
        self.temperature = Some(temperature);
        self
    }

    pub fn workspace_dir(mut self, workspace_dir: std::path::PathBuf) -> Self {
        self.workspace_dir = Some(workspace_dir);
        self
    }

    pub fn identity_config(mut self, identity_config: crate::config::IdentityConfig) -> Self {
        self.identity_config = Some(identity_config);
        self
    }

    pub fn skills(mut self, skills: Vec<crate::skills::Skill>) -> Self {
        self.skills = Some(skills);
        self
    }

    pub fn skills_prompt_mode(
        mut self,
        skills_prompt_mode: crate::config::SkillsPromptInjectionMode,
    ) -> Self {
        self.skills_prompt_mode = Some(skills_prompt_mode);
        self
    }

    pub fn auto_save(mut self, auto_save: bool) -> Self {
        self.auto_save = Some(auto_save);
        self
    }

    pub fn memory_session_id(mut self, memory_session_id: Option<String>) -> Self {
        self.memory_session_id = memory_session_id;
        self
    }

    pub fn classification_config(
        mut self,
        classification_config: crate::config::QueryClassificationConfig,
    ) -> Self {
        self.classification_config = Some(classification_config);
        self
    }

    pub fn available_hints(mut self, available_hints: Vec<String>) -> Self {
        self.available_hints = Some(available_hints);
        self
    }

    pub fn route_model_by_hint(mut self, route_model_by_hint: HashMap<String, String>) -> Self {
        self.route_model_by_hint = Some(route_model_by_hint);
        self
    }

    pub fn allowed_tools(mut self, allowed_tools: Option<Vec<String>>) -> Self {
        self.allowed_tools = allowed_tools;
        self
    }

    pub fn response_cache(
        mut self,
        cache: Option<Arc<crate::memory::response_cache::ResponseCache>>,
    ) -> Self {
        self.response_cache = cache;
        self
    }

    pub fn tool_descriptions(mut self, tool_descriptions: Option<ToolDescriptions>) -> Self {
        self.tool_descriptions = tool_descriptions;
        self
    }

    pub fn security_summary(mut self, summary: Option<String>) -> Self {
        self.security_summary = summary;
        self
    }

    pub fn autonomy_level(mut self, level: crate::security::AutonomyLevel) -> Self {
        self.autonomy_level = Some(level);
        self
    }

    pub fn activated_tools(
        mut self,
        activated: Option<Arc<std::sync::Mutex<tools::ActivatedToolSet>>>,
    ) -> Self {
        self.activated_tools = activated;
        self
    }

    pub fn kumiho_enabled(mut self, enabled: bool) -> Self {
        self.kumiho_enabled = enabled;
        self
    }

    /// Mark whether the high-level Kumiho memory tools (engage / reflect /
    /// recall / consolidate / dream_state) are registered in the sidecar.
    /// When `false`, the lite bootstrap prompt is used instead of the full
    /// one — see [`crate::agent::kumiho::registry_has_advanced_kumiho_tools`].
    pub fn kumiho_memory_advanced_available(mut self, available: bool) -> Self {
        self.kumiho_memory_advanced_available = available;
        self
    }

    pub fn operator_enabled(mut self, enabled: bool) -> Self {
        self.operator_enabled = enabled;
        self
    }

    /// Attach a process-wide [`EffectivenessCache`] so the prompt builder
    /// can rerank skills by recency-weighted success rate.  Pass the same
    /// `Arc` to every agent the daemon spawns — the cache is intended to
    /// be shared.
    ///
    /// [`EffectivenessCache`]: crate::skills::EffectivenessCache
    pub fn skill_effectiveness(mut self, cache: Arc<crate::skills::EffectivenessCache>) -> Self {
        self.skill_effectiveness = Some(cache);
        self
    }

    /// Set the pre-rendered "Deferred Tools" prompt section. Empty string is
    /// equivalent to unset. See `Agent::mcp_deferred_section`.
    pub fn mcp_deferred_section(mut self, section: String) -> Self {
        self.mcp_deferred_section = section;
        self
    }

    pub fn build(self) -> Result<Agent> {
        let mut tools = self
            .tools
            .ok_or_else(|| anyhow::anyhow!("tools are required"))?;
        let allowed = self.allowed_tools.clone();
        if let Some(ref allow_list) = allowed {
            tools.retain(|t| allow_list.iter().any(|name| name == t.name()));
        }
        let tool_specs = tools.iter().map(|tool| tool.spec()).collect();

        Ok(Agent {
            provider: self
                .provider
                .ok_or_else(|| anyhow::anyhow!("provider is required"))?,
            provider_name: self.provider_name.unwrap_or_else(|| "unknown".into()),
            tools,
            tool_specs,
            memory: self
                .memory
                .ok_or_else(|| anyhow::anyhow!("memory is required"))?,
            observer: self
                .observer
                .ok_or_else(|| anyhow::anyhow!("observer is required"))?,
            prompt_builder: self
                .prompt_builder
                .unwrap_or_else(SystemPromptBuilder::with_defaults),
            tool_dispatcher: self
                .tool_dispatcher
                .ok_or_else(|| anyhow::anyhow!("tool_dispatcher is required"))?,
            memory_loader: self
                .memory_loader
                .unwrap_or_else(|| Box::new(DefaultMemoryLoader::default())),
            config: self.config.unwrap_or_default(),
            model_name: self
                .model_name
                .unwrap_or_else(|| "anthropic/claude-sonnet-4-20250514".into()),
            temperature: self.temperature.unwrap_or(0.7),
            workspace_dir: self
                .workspace_dir
                .unwrap_or_else(|| std::path::PathBuf::from(".")),
            identity_config: self.identity_config.unwrap_or_default(),
            skills: self.skills.unwrap_or_default(),
            skills_prompt_mode: self.skills_prompt_mode.unwrap_or_default(),
            auto_save: self.auto_save.unwrap_or(false),
            memory_session_id: self.memory_session_id,
            history: Vec::new(),
            classification_config: self.classification_config.unwrap_or_default(),
            available_hints: self.available_hints.unwrap_or_default(),
            route_model_by_hint: self.route_model_by_hint.unwrap_or_default(),
            allowed_tools: allowed,
            response_cache: self.response_cache,
            tool_descriptions: self.tool_descriptions,
            security_summary: self.security_summary,
            autonomy_level: self
                .autonomy_level
                .unwrap_or(crate::security::AutonomyLevel::Supervised),
            activated_tools: self.activated_tools,
            kumiho_enabled: self.kumiho_enabled,
            kumiho_memory_advanced_available: self.kumiho_memory_advanced_available,
            operator_enabled: self.operator_enabled,
            skill_effectiveness: self.skill_effectiveness,
            mcp_deferred_section: self.mcp_deferred_section,
        })
    }
}

impl Agent {
    pub fn builder() -> AgentBuilder {
        AgentBuilder::new()
    }

    pub fn history(&self) -> &[ConversationMessage] {
        &self.history
    }

    pub fn clear_history(&mut self) {
        self.history.clear();
    }

    /// Test-only: override the model name the agent uses for compression
    /// sizing.  Production code sets this through the builder; the tests use
    /// it to exercise specific context-window tiers without a full builder
    /// dance.
    #[cfg(test)]
    pub fn set_model_name_for_test(&mut self, model: impl Into<String>) {
        self.model_name = model.into();
    }

    pub fn set_memory_session_id(&mut self, session_id: Option<String>) {
        self.memory_session_id = session_id;
    }

    /// Hydrate the agent with prior chat messages (e.g. from a session backend).
    ///
    /// Ensures a system prompt is prepended if history is empty, then appends all
    /// non-system messages from the seed. System messages in the seed are skipped
    /// to avoid duplicating the system prompt.
    pub fn seed_history(&mut self, messages: &[ChatMessage]) {
        if self.history.is_empty() {
            if let Ok(sys) = self.build_system_prompt() {
                self.history
                    .push(ConversationMessage::Chat(ChatMessage::system(sys)));
            }
        }
        for msg in messages {
            if msg.role != "system" {
                self.history.push(ConversationMessage::Chat(msg.clone()));
            }
        }
    }

    pub async fn from_config(config: &Config) -> Result<Self> {
        Self::from_config_with_mcp_registry(config, None).await
    }

    pub async fn from_config_with_mcp_registry(
        config: &Config,
        shared_mcp_registry: Option<Arc<tools::McpRegistry>>,
    ) -> Result<Self> {
        // Inject Kumiho memory MCP server and Operator orchestration MCP server
        // so dashboard/WebSocket agents also get persistent memory and multi-agent
        // tools.  Both inject functions are idempotent.
        let config = crate::agent::kumiho::inject_kumiho(config.clone(), false);
        let config = &crate::agent::operator::inject_operator(config, false);

        let observer: Arc<dyn Observer> =
            Arc::from(observability::create_observer(&config.observability));
        let runtime: Arc<dyn runtime::RuntimeAdapter> =
            Arc::from(runtime::create_runtime(&config.runtime)?);
        let security = Arc::new(SecurityPolicy::from_config(
            &config.autonomy,
            &config.workspace_dir,
        ));

        let memory: Arc<dyn Memory> = Arc::from(memory::create_memory_with_storage_and_routes(
            &config.memory,
            &config.embedding_routes,
            Some(&config.storage.provider.config),
            &config.workspace_dir,
            config.api_key.as_deref(),
        )?);

        let composio_key = if config.composio.enabled {
            config.composio.api_key.as_deref()
        } else {
            None
        };
        let composio_entity_id = if config.composio.enabled {
            Some(config.composio.entity_id.as_str())
        } else {
            None
        };

        let (
            mut tools,
            delegate_handle,
            _reaction_handle,
            _channel_map_handle,
            _ask_user_handle,
            _escalate_handle,
        ) = tools::all_tools_with_runtime(
            Arc::new(config.clone()),
            &security,
            runtime,
            memory.clone(),
            composio_key,
            composio_entity_id,
            &config.browser,
            &config.http_request,
            &config.web_fetch,
            &config.workspace_dir,
            &config.agents,
            config.api_key.as_deref(),
            config,
            None,
        );

        // ── Wire MCP tools (non-fatal) ─────────────────────────────
        // Replicates the same MCP initialization logic used in the CLI
        // and webhook paths (loop_.rs) so that the WebSocket/daemon UI
        // path also has access to MCP tools.
        let mut activated_tools: Option<Arc<std::sync::Mutex<tools::ActivatedToolSet>>> = None;
        let mut kumiho_advanced = false;
        let mut deferred_section_for_prompt = String::new();
        if config.mcp.enabled && !config.mcp.servers.is_empty() {
            let registry_result: Result<Arc<tools::McpRegistry>> =
                if let Some(registry) = shared_mcp_registry.as_ref() {
                    tracing::info!(
                        "Using shared MCP registry — {} server(s), {} tool(s)",
                        registry.server_count(),
                        registry.tool_count()
                    );
                    Ok(Arc::clone(registry))
                } else {
                    tracing::info!(
                        "Initializing MCP client — {} server(s) configured",
                        config.mcp.servers.len()
                    );
                    tools::McpRegistry::connect_all(&config.mcp.servers)
                        .await
                        .map(Arc::new)
                };
            match registry_result {
                Ok(registry) => {
                    // Registry-based probe for the high-level Kumiho memory
                    // reflexes. See coherence audit row 1 + 13: the prompt
                    // gate must reflect actual runtime tool availability,
                    // not a filesystem heuristic.
                    kumiho_advanced = crate::agent::kumiho::registry_has_advanced_kumiho_tools(
                        &registry.tool_names(),
                    );
                    crate::agent::kumiho::warn_if_kumiho_advanced_missing(config, kumiho_advanced);
                    if config.mcp.deferred_loading {
                        let operator_prefix =
                            format!("{}__", crate::agent::operator::OPERATOR_SERVER_NAME);

                        // Eagerly load operator tools so they are always
                        // available without a tool_search round-trip.
                        let all_names = registry.tool_names();
                        let mut eager_count = 0usize;
                        for name in &all_names {
                            if name.starts_with(&operator_prefix) {
                                if let Some(def) = registry.get_tool_def(name).await {
                                    let wrapper: std::sync::Arc<dyn tools::Tool> =
                                        std::sync::Arc::new(tools::McpToolWrapper::new(
                                            name.clone(),
                                            def,
                                            std::sync::Arc::clone(&registry),
                                        ));
                                    if let Some(ref handle) = delegate_handle {
                                        handle.write().push(std::sync::Arc::clone(&wrapper));
                                    }
                                    tools.push(Box::new(tools::ArcToolRef(wrapper)));
                                    eager_count += 1;
                                }
                            }
                        }

                        // Defer everything else (kumiho-memory, etc.)
                        let deferred_set = tools::DeferredMcpToolSet::from_registry_filtered(
                            std::sync::Arc::clone(&registry),
                            |name| !name.starts_with(&operator_prefix),
                        )
                        .await;
                        tracing::info!(
                            "MCP hybrid: {} eager operator tool(s), {} deferred stub(s) from {} server(s)",
                            eager_count,
                            deferred_set.len(),
                            registry.server_count()
                        );
                        // Build the deferred-tools section now so it can be
                        // injected into the system prompt by `build_system_prompt`.
                        // Without this the agent has `tool_search` available
                        // but no idea which servers/tools to search for —
                        // user-added MCP servers like OpenCrab are invisible.
                        let server_instructions = registry.server_instructions().await;
                        deferred_section_for_prompt =
                            crate::tools::mcp_deferred::build_deferred_tools_section_with_instructions(
                                &deferred_set,
                                &server_instructions,
                            );
                        let activated =
                            Arc::new(std::sync::Mutex::new(tools::ActivatedToolSet::new()));
                        activated_tools = Some(Arc::clone(&activated));
                        tools.push(Box::new(tools::ToolSearchTool::new(
                            deferred_set,
                            activated,
                        )));
                    } else {
                        let names = registry.tool_names();
                        let mut registered = 0usize;
                        for name in names {
                            if let Some(def) = registry.get_tool_def(&name).await {
                                let wrapper: std::sync::Arc<dyn tools::Tool> =
                                    std::sync::Arc::new(tools::McpToolWrapper::new(
                                        name,
                                        def,
                                        std::sync::Arc::clone(&registry),
                                    ));
                                if let Some(ref handle) = delegate_handle {
                                    handle.write().push(std::sync::Arc::clone(&wrapper));
                                }
                                tools.push(Box::new(tools::ArcToolRef(wrapper)));
                                registered += 1;
                            }
                        }
                        tracing::info!(
                            "MCP: {} tool(s) registered from {} server(s)",
                            registered,
                            registry.server_count()
                        );
                    }
                }
                Err(e) => {
                    tracing::error!("MCP registry failed to initialize: {e:#}");
                }
            }
        }

        let provider_name = config.default_provider.as_deref().unwrap_or("openrouter");

        let model_name = config
            .default_model
            .as_deref()
            .unwrap_or("anthropic/claude-sonnet-4-20250514")
            .to_string();

        let provider_runtime_options = providers::provider_runtime_options_from_config(config);

        let provider: Box<dyn Provider> = providers::create_routed_provider_with_options(
            provider_name,
            config.api_key.as_deref(),
            config.api_url.as_deref(),
            &config.reliability,
            &config.model_routes,
            &model_name,
            &provider_runtime_options,
        )?;

        let dispatcher_choice = config.agent.tool_dispatcher.as_str();
        let tool_dispatcher: Box<dyn ToolDispatcher> = match dispatcher_choice {
            "native" => Box::new(NativeToolDispatcher),
            "xml" => Box::new(XmlToolDispatcher),
            _ if provider.supports_native_tools() => Box::new(NativeToolDispatcher),
            _ => Box::new(XmlToolDispatcher),
        };

        let route_model_by_hint: HashMap<String, String> = config
            .model_routes
            .iter()
            .map(|route| (route.hint.clone(), route.model.clone()))
            .collect();
        let available_hints: Vec<String> = route_model_by_hint.keys().cloned().collect();

        let response_cache = if config.memory.response_cache_enabled {
            crate::memory::response_cache::ResponseCache::with_hot_cache(
                &config.workspace_dir,
                config.memory.response_cache_ttl_minutes,
                config.memory.response_cache_max_entries,
                config.memory.response_cache_hot_entries,
            )
            .ok()
            .map(Arc::new)
        } else {
            None
        };

        // Use operator-aware iteration limit when operator is active.
        let mut agent_config = config.agent.clone();
        agent_config.max_tool_iterations =
            crate::agent::loop_::effective_max_tool_iterations(config);

        Agent::builder()
            .provider(provider)
            .provider_name(provider_name.to_string())
            .tools(tools)
            .memory(memory)
            .observer(observer)
            .response_cache(response_cache)
            .tool_dispatcher(tool_dispatcher)
            .memory_loader(Box::new(DefaultMemoryLoader::new(
                config.kumiho.memory_retrieval_limit,
                config.memory.min_relevance_score,
            )))
            .prompt_builder(SystemPromptBuilder::with_defaults())
            .config(agent_config)
            .model_name(model_name)
            .temperature(config.default_temperature)
            .workspace_dir(config.workspace_dir.clone())
            .classification_config(config.query_classification.clone())
            .available_hints(available_hints)
            .route_model_by_hint(route_model_by_hint)
            .identity_config(config.identity.clone())
            .skills(crate::skills::load_skills_with_config(
                &config.workspace_dir,
                config,
            ))
            .skills_prompt_mode(config.skills.prompt_injection_mode)
            .auto_save(config.memory.auto_save)
            .security_summary(Some(security.prompt_summary()))
            .autonomy_level(config.autonomy.level)
            .activated_tools(activated_tools)
            .kumiho_enabled(config.kumiho.enabled)
            .kumiho_memory_advanced_available(kumiho_advanced)
            .operator_enabled(config.operator.enabled)
            .mcp_deferred_section(deferred_section_for_prompt)
            .build()
    }

    fn trim_history(&mut self) {
        let max = self.config.max_history_messages;
        if self.history.len() <= max {
            return;
        }

        let mut system_messages = Vec::new();
        let mut other_messages = Vec::new();

        for msg in self.history.drain(..) {
            match &msg {
                ConversationMessage::Chat(chat) if chat.role == "system" => {
                    system_messages.push(msg);
                }
                _ => other_messages.push(msg),
            }
        }

        if other_messages.len() > max {
            let mut drop_count = other_messages.len() - max;
            // Anthropic requires every `tool_result` block be preceded by a matching
            // `tool_use` block. If our cut would leave a `ToolResults` orphaned at
            // the new front (because its paired `AssistantToolCalls` is in the
            // dropped slice), advance the boundary forward until we land on a
            // non-orphan message. We may drop a couple more messages than the
            // configured target — preferable to a 400 from the provider.
            while drop_count < other_messages.len()
                && matches!(
                    other_messages[drop_count],
                    ConversationMessage::ToolResults(_)
                )
            {
                drop_count += 1;
            }
            other_messages.drain(0..drop_count);
        }

        self.history = system_messages;
        self.history.extend(other_messages);
    }

    /// Token-aware compression for the Operator chat history.
    ///
    /// `trim_history` only caps by **message count**, so a single huge tool
    /// result (Manus task output, Kumiho revision content, web fetch) can
    /// pin the chat at 1M+ tokens with the 500-message cap still satisfied
    /// and break with `prompt is too long`.
    ///
    /// This wraps [`ContextCompressor::compress_if_needed`] — the same path
    /// the workflow loop (`loop_.rs`) and channel handlers (`channels/mod.rs`)
    /// use — and surfaces the result through `self.history`.  When the
    /// compressor decides to summarize, we preserve structural typing by:
    ///   1. Keeping all leading system messages intact.
    ///   2. Keeping the trailing `protect_last_n` original messages intact
    ///      (skipping the boundary forward past any orphaned `ToolResults`
    ///      to satisfy Anthropic's tool_use/tool_result pairing rule).
    ///   3. Replacing the middle with one synthetic assistant message
    ///      carrying the compressor's summary.
    ///
    /// Returns the [`CompressionResult`] (with `compressed: false` when the
    /// history is under threshold).  Errors are logged and treated as a no-op
    /// so a single compressor failure can't kill a turn.
    async fn compress_history_if_needed(
        &mut self,
        model: &str,
    ) -> crate::agent::context_compressor::CompressionResult {
        use crate::agent::context_compressor::{
            CompressionResult, ContextCompressor, estimate_tokens,
        };

        // Cheap short-circuit — most turns never come close to the threshold.
        // Avoids cloning the full flat-message vec when there's nothing to do.
        if !self.config.context_compression.enabled {
            return CompressionResult {
                compressed: false,
                tokens_before: 0,
                tokens_after: 0,
                passes_used: 0,
            };
        }

        let context_window = context_window_for_model(model);
        let compressor =
            ContextCompressor::new(self.config.context_compression.clone(), context_window)
                .with_memory(Arc::clone(&self.memory));

        // Flatten history for the compressor.  We rebuild `self.history` from
        // the compressed result if compression actually fires.
        let mut flat = self.tool_dispatcher.to_provider_messages(&self.history);
        let before_len = flat.len();

        let result = match compressor
            .compress_if_needed(&mut flat, self.provider.as_ref(), model)
            .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::warn!(
                    error = %e,
                    "context_compressor: compression failed; continuing with full history (provider may reject)"
                );
                let tokens =
                    estimate_tokens(&self.tool_dispatcher.to_provider_messages(&self.history));
                return CompressionResult {
                    compressed: false,
                    tokens_before: tokens,
                    tokens_after: tokens,
                    passes_used: 0,
                };
            }
        };

        if !result.compressed {
            return result;
        }

        tracing::info!(
            tokens_before = result.tokens_before,
            tokens_after = result.tokens_after,
            passes_used = result.passes_used,
            messages_before = before_len,
            messages_after = flat.len(),
            "context_compressor: compressed Operator chat history"
        );

        self.apply_compressed_history(&flat);

        result
    }

    fn compression_pending(&self, model: &str) -> Option<(usize, usize)> {
        if !self.config.context_compression.enabled {
            return None;
        }

        let context_window = context_window_for_model(model);
        #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
        let threshold =
            (context_window as f64 * self.config.context_compression.threshold_ratio) as usize;
        let flat = self.tool_dispatcher.to_provider_messages(&self.history);
        let tokens = crate::agent::context_compressor::estimate_tokens(&flat);

        (tokens > threshold).then_some((tokens, threshold))
    }

    async fn compress_history_if_needed_streamed(
        &mut self,
        model: &str,
        event_tx: &tokio::sync::mpsc::Sender<TurnEvent>,
    ) -> crate::agent::context_compressor::CompressionResult {
        let Some((tokens, threshold)) = self.compression_pending(model) else {
            return self.compress_history_if_needed(model).await;
        };

        let _ = event_tx
            .send(TurnEvent::OperatorStatus {
                phase: "compressing".to_string(),
                detail: format!(
                    "Compacting conversation context ({tokens} estimated tokens, threshold {threshold})"
                ),
            })
            .await;

        let compress = self.compress_history_if_needed(model);
        tokio::pin!(compress);
        let mut heartbeat = Box::pin(tokio::time::sleep(Duration::from_secs(15)));
        let mut elapsed_secs = 0u64;

        loop {
            tokio::select! {
                result = &mut compress => {
                    if result.compressed {
                        let _ = event_tx
                            .send(TurnEvent::OperatorStatus {
                                phase: "compressing".to_string(),
                                detail: format!(
                                    "Context compacted: {} -> {} estimated tokens",
                                    result.tokens_before, result.tokens_after
                                ),
                            })
                            .await;
                    }
                    return result;
                }
                () = &mut heartbeat => {
                    elapsed_secs += 15;
                    let _ = event_tx
                        .send(TurnEvent::OperatorStatus {
                            phase: "compressing".to_string(),
                            detail: format!("Still compacting conversation context ({elapsed_secs}s)"),
                        })
                        .await;
                    heartbeat
                        .as_mut()
                        .reset(tokio::time::Instant::now() + Duration::from_secs(15));
                }
            }
        }
    }

    /// Rebuild `self.history` after the compressor mutated the flat message
    /// list.  Preserves system messages and the structural tail; replaces the
    /// summarised middle with a single synthetic assistant message carrying
    /// the summary text the compressor emitted.
    ///
    /// We can't perfectly reverse `to_provider_messages` (it's lossy for
    /// `AssistantToolCalls`/`ToolResults`), so this is the pragmatic v1
    /// described in the fix plan: head system + summary + last-N originals.
    fn apply_compressed_history(&mut self, compressed: &[ChatMessage]) {
        // Extract the summary text the compressor produced.  The compressor
        // tags it with "[CONTEXT SUMMARY" — find the first such message in
        // the compressed flat list.  Fall back to a generic notice if the
        // marker is missing (e.g. compressor used only fast_trim).
        let summary_text = compressed
            .iter()
            .find(|m| m.content.starts_with("[CONTEXT SUMMARY"))
            .map(|m| m.content.clone())
            .unwrap_or_else(|| {
                "[CONTEXT SUMMARY — earlier conversation compacted by fast-trim]".to_string()
            });

        let protect_last_n = self.config.context_compression.protect_last_n;

        // Split `self.history` into [system...] | [middle...] | [tail].
        let mut system_msgs: Vec<ConversationMessage> = Vec::new();
        let mut rest: Vec<ConversationMessage> = Vec::new();
        for msg in self.history.drain(..) {
            match &msg {
                ConversationMessage::Chat(c) if c.role == "system" => system_msgs.push(msg),
                _ => rest.push(msg),
            }
        }

        let tail_start = rest.len().saturating_sub(protect_last_n);
        // Drop any leading ToolResults in the tail — they'd be orphaned by
        // the splice (their paired AssistantToolCalls just got summarised).
        let mut tail_start_safe = tail_start;
        while tail_start_safe < rest.len()
            && matches!(rest[tail_start_safe], ConversationMessage::ToolResults(_))
        {
            tail_start_safe += 1;
        }
        let tail: Vec<ConversationMessage> = rest.split_off(tail_start_safe);

        let mut rebuilt = system_msgs;
        rebuilt.push(ConversationMessage::Chat(ChatMessage::assistant(
            summary_text,
        )));
        rebuilt.extend(tail);
        self.history = rebuilt;
    }

    fn build_system_prompt(&self) -> Result<String> {
        let instructions = self.tool_dispatcher.prompt_instructions(&self.tools);
        let ctx = PromptContext {
            workspace_dir: &self.workspace_dir,
            model_name: &self.model_name,
            tools: crate::agent::prompt::PromptTools::Full(&self.tools),
            skills: &self.skills,
            skills_prompt_mode: self.skills_prompt_mode,
            skill_effectiveness: self
                .skill_effectiveness
                .as_ref()
                .map(|c| c.as_ref() as &dyn crate::skills::SkillEffectivenessProvider),
            identity_config: Some(&self.identity_config),
            dispatcher_instructions: &instructions,
            tool_descriptions: self.tool_descriptions.as_ref(),
            security_summary: self.security_summary.clone(),
            autonomy_level: self.autonomy_level,
            operator_enabled: self.operator_enabled,
            kumiho_enabled: self.kumiho_enabled,
            kumiho_memory_advanced_available: self.kumiho_memory_advanced_available,
            mode: crate::agent::prompt::BuilderMode::Daemon,
        };
        let mut prompt = self.prompt_builder.build(&ctx)?;
        // Append the deferred-tools section listing MCP tools the agent
        // can `tool_search` for. Without this the dashboard agent never
        // sees that user-added MCP servers (e.g. OpenCrab) exist or what
        // they're for, even though `tool_search` would surface them.
        if !self.mcp_deferred_section.is_empty() {
            if !prompt.ends_with('\n') {
                prompt.push('\n');
            }
            prompt.push('\n');
            prompt.push_str(&self.mcp_deferred_section);
        }
        Ok(prompt)
    }

    async fn execute_tool_call(&self, call: &ParsedToolCall) -> ToolExecutionResult {
        let start = Instant::now();

        // First try to find tool in static registry, then in activated MCP tools.
        let result = if let Some(tool) = self.tools.iter().find(|t| t.name() == call.name) {
            match tool.execute(call.arguments.clone()).await {
                Ok(r) => {
                    self.observer.record_event(&ObserverEvent::ToolCall {
                        tool: call.name.clone(),
                        duration: start.elapsed(),
                        success: r.success,
                    });
                    if r.success {
                        r.output
                    } else {
                        format!("Error: {}", r.error.unwrap_or(r.output))
                    }
                }
                Err(e) => {
                    self.observer.record_event(&ObserverEvent::ToolCall {
                        tool: call.name.clone(),
                        duration: start.elapsed(),
                        success: false,
                    });
                    format!("Error executing {}: {e}", call.name)
                }
            }
        } else if let Some(activated_arc) = self.activated_tools.as_ref() {
            // Try to find in activated MCP tools.
            let activated_opt = activated_arc.lock().unwrap().get_resolved(&call.name);
            if let Some(tool) = activated_opt {
                match tool.execute(call.arguments.clone()).await {
                    Ok(r) => {
                        self.observer.record_event(&ObserverEvent::ToolCall {
                            tool: call.name.clone(),
                            duration: start.elapsed(),
                            success: r.success,
                        });
                        if r.success {
                            r.output
                        } else {
                            format!("Error: {}", r.error.unwrap_or(r.output))
                        }
                    }
                    Err(e) => {
                        self.observer.record_event(&ObserverEvent::ToolCall {
                            tool: call.name.clone(),
                            duration: start.elapsed(),
                            success: false,
                        });
                        format!("Error executing {}: {e}", call.name)
                    }
                }
            } else {
                format!("Unknown tool: {}", call.name)
            }
        } else {
            format!("Unknown tool: {}", call.name)
        };

        ToolExecutionResult {
            name: call.name.clone(),
            output: result,
            success: true,
            tool_call_id: call.tool_call_id.clone(),
        }
    }

    async fn execute_tools(&self, calls: &[ParsedToolCall]) -> Vec<ToolExecutionResult> {
        if !self.config.parallel_tools {
            let mut results = Vec::with_capacity(calls.len());
            for call in calls {
                results.push(self.execute_tool_call(call).await);
            }
            return results;
        }

        let futs: Vec<_> = calls
            .iter()
            .map(|call| self.execute_tool_call(call))
            .collect();
        futures_util::future::join_all(futs).await
    }

    fn classify_model(&self, user_message: &str) -> String {
        if let Some(decision) =
            super::classifier::classify_with_decision(&self.classification_config, user_message)
        {
            if self.available_hints.contains(&decision.hint) {
                let resolved_model = self
                    .route_model_by_hint
                    .get(&decision.hint)
                    .map(String::as_str)
                    .unwrap_or("unknown");
                tracing::info!(
                    target: "query_classification",
                    hint = decision.hint.as_str(),
                    model = resolved_model,
                    rule_priority = decision.priority,
                    message_length = user_message.len(),
                    "Classified message route"
                );
                return format!("hint:{}", decision.hint);
            }
        }

        // Fallback: auto-classify by complexity when no rule matched.
        if let Some(ref ac) = self.config.auto_classify {
            let tier = super::eval::estimate_complexity(user_message);
            if let Some(hint) = ac.hint_for(tier) {
                if self.available_hints.contains(&hint.to_string()) {
                    tracing::info!(
                        target: "query_classification",
                        hint = hint,
                        complexity = ?tier,
                        message_length = user_message.len(),
                        "Auto-classified by complexity"
                    );
                    return format!("hint:{hint}");
                }
            }
        }

        self.model_name.clone()
    }

    pub async fn turn(&mut self, user_message: &str) -> Result<String> {
        if self.history.is_empty() {
            let system_prompt = self.build_system_prompt()?;
            self.history
                .push(ConversationMessage::Chat(ChatMessage::system(
                    system_prompt,
                )));
        }

        let context = self
            .memory_loader
            .load_context(
                self.memory.as_ref(),
                user_message,
                self.memory_session_id.as_deref(),
            )
            .await
            .unwrap_or_default();

        if self.auto_save {
            let _ = self
                .memory
                .store(
                    "user_msg",
                    user_message,
                    MemoryCategory::Conversation,
                    self.memory_session_id.as_deref(),
                )
                .await;
        }

        let now = chrono::Local::now();
        let (year, month, day) = (now.year(), now.month(), now.day());
        let (hour, minute, second) = (now.hour(), now.minute(), now.second());
        let tz = now.format("%Z");
        let date_str =
            format!("{year:04}-{month:02}-{day:02} {hour:02}:{minute:02}:{second:02} {tz}");

        let enriched = if context.is_empty() {
            format!("[CURRENT DATE & TIME: {date_str}]\n\n{user_message}")
        } else {
            format!("[CURRENT DATE & TIME: {date_str}]\n\n{context}\n\n{user_message}")
        };

        self.history
            .push(ConversationMessage::Chat(ChatMessage::user(enriched)));

        let effective_model = self.classify_model(user_message);

        let mut saw_tool_calls = false;
        let mut empty_final_retries = 0usize;

        for _ in 0..self.config.max_tool_iterations {
            // Token-aware compression — keeps history under the model's
            // context window.  See `compress_history_if_needed` for the
            // rationale (turn_streamed shares the exact same bug).
            let _ = self.compress_history_if_needed(&effective_model).await;

            let messages = self.tool_dispatcher.to_provider_messages(&self.history);

            // Hard safety cap: fail loud rather than ship a request the
            // provider will reject after thinking time has already burned.
            {
                let window = context_window_for_model(&effective_model);
                let est = crate::agent::context_compressor::estimate_tokens(&messages);
                let hard_cap = (window as f64 * 0.95) as usize;
                if window > 0 && est > hard_cap {
                    anyhow::bail!(
                        "Conversation too long even after compression \
                         ({est} tokens > {hard_cap} cap for model {effective_model}). \
                         Start a new chat tab to continue."
                    );
                }
            }

            // Response cache: check before LLM call (only for deterministic, text-only prompts)
            let cache_key = if self.temperature == 0.0 {
                self.response_cache.as_ref().map(|_| {
                    let last_user = messages
                        .iter()
                        .rfind(|m| m.role == "user")
                        .map(|m| m.content.as_str())
                        .unwrap_or("");
                    let system = messages
                        .iter()
                        .find(|m| m.role == "system")
                        .map(|m| m.content.as_str());
                    crate::memory::response_cache::ResponseCache::cache_key(
                        &effective_model,
                        system,
                        last_user,
                    )
                })
            } else {
                None
            };

            if let (Some(cache), Some(key)) = (&self.response_cache, &cache_key) {
                if let Ok(Some(cached)) = cache.get(key) {
                    self.observer.record_event(&ObserverEvent::CacheHit {
                        cache_type: "response".into(),
                        tokens_saved: 0,
                    });
                    self.history
                        .push(ConversationMessage::Chat(ChatMessage::assistant(
                            cached.clone(),
                        )));
                    self.trim_history();
                    return Ok(cached);
                }
                self.observer.record_event(&ObserverEvent::CacheMiss {
                    cache_type: "response".into(),
                });
            }

            // Rebuild tool_specs per iteration so newly activated deferred
            // tools (via tool_search) appear in subsequent LLM calls.
            let mut iter_tool_specs: Vec<ToolSpec> = self.tools.iter().map(|t| t.spec()).collect();
            if let Some(at) = self.activated_tools.as_ref() {
                for spec in at.lock().unwrap().tool_specs() {
                    iter_tool_specs.push(spec);
                }
            }

            // Architect-mode runtime guard: when the gateway forwards an
            // `<editor-state>` block in the user message, hide the workflow
            // persistence tools so the LLM literally cannot call them. The
            // documented Architect contract is to PROPOSE YAML via
            // `propose_workflow_yaml` and let the editor own persistence.
            filter_tool_specs_for_architect(&mut iter_tool_specs, user_message);

            let response = match self
                .provider
                .chat(
                    ChatRequest {
                        messages: &messages,
                        tools: if self.tool_dispatcher.should_send_tool_specs() {
                            Some(&iter_tool_specs)
                        } else {
                            None
                        },
                    },
                    &effective_model,
                    self.temperature,
                )
                .await
            {
                Ok(resp) => resp,
                Err(err) => return Err(err),
            };

            let (text, calls) = self.tool_dispatcher.parse_response(&response);
            if calls.is_empty() {
                let mut final_text = if text.is_empty() {
                    response.text.unwrap_or_default()
                } else {
                    text
                };
                let mut synthesized_empty_fallback = false;

                if final_text.trim().is_empty() {
                    if saw_tool_calls && empty_final_retries == 0 {
                        empty_final_retries += 1;
                        tracing::warn!(
                            "Provider returned empty final response after tool calls; retrying once"
                        );
                        self.history
                            .push(ConversationMessage::Chat(ChatMessage::user(
                                EMPTY_FINAL_AFTER_TOOLS_RETRY_PROMPT,
                            )));
                        continue;
                    }
                    final_text = if saw_tool_calls {
                        EMPTY_FINAL_AFTER_TOOLS_FALLBACK.to_string()
                    } else {
                        EMPTY_FINAL_FALLBACK.to_string()
                    };
                    synthesized_empty_fallback = true;
                }

                // Store in response cache (text-only, no tool calls)
                if !synthesized_empty_fallback
                    && let (Some(cache), Some(key)) = (&self.response_cache, &cache_key)
                {
                    let token_count = response
                        .usage
                        .as_ref()
                        .and_then(|u| u.output_tokens)
                        .unwrap_or(0);
                    #[allow(clippy::cast_possible_truncation)]
                    let _ = cache.put(key, &effective_model, &final_text, token_count as u32);
                }

                self.history
                    .push(ConversationMessage::Chat(ChatMessage::assistant(
                        final_text.clone(),
                    )));
                self.trim_history();

                return Ok(final_text);
            }

            saw_tool_calls = true;
            if !text.is_empty() {
                self.history
                    .push(ConversationMessage::Chat(ChatMessage::assistant(
                        text.clone(),
                    )));
                print!("{text}");
                let _ = std::io::stdout().flush();
            }

            self.history.push(ConversationMessage::AssistantToolCalls {
                text: response.text.clone(),
                tool_calls: response.tool_calls.clone(),
                reasoning_content: response.reasoning_content.clone(),
            });

            let results = self.execute_tools(&calls).await;
            let formatted = self.tool_dispatcher.format_results(&results);
            self.history.push(formatted);
            self.trim_history();
        }

        anyhow::bail!(
            "Agent exceeded maximum tool iterations ({})",
            self.config.max_tool_iterations
        )
    }

    /// Execute a single agent turn while streaming intermediate events.
    ///
    /// Behaves identically to [`turn`](Self::turn) but forwards [`TurnEvent`]s
    /// through the provided channel so callers (e.g. the WebSocket gateway)
    /// can relay incremental updates to clients.
    ///
    /// The returned `String` is the final, complete assistant response — the
    /// same value that `turn` would return.
    pub async fn turn_streamed(
        &mut self,
        user_message: &str,
        event_tx: tokio::sync::mpsc::Sender<TurnEvent>,
    ) -> Result<String> {
        // ── Preamble (identical to turn) ───────────────────────────────
        if self.history.is_empty() {
            let system_prompt = self.build_system_prompt()?;
            self.history
                .push(ConversationMessage::Chat(ChatMessage::system(
                    system_prompt,
                )));
        }

        let context = self
            .memory_loader
            .load_context(
                self.memory.as_ref(),
                user_message,
                self.memory_session_id.as_deref(),
            )
            .await
            .unwrap_or_default();

        if self.auto_save {
            let _ = self
                .memory
                .store(
                    "user_msg",
                    user_message,
                    MemoryCategory::Conversation,
                    self.memory_session_id.as_deref(),
                )
                .await;
        }

        let now = chrono::Local::now().format("%Y-%m-%d %H:%M:%S %Z");
        let enriched = if context.is_empty() {
            format!("[{now}] {user_message}")
        } else {
            format!("{context}[{now}] {user_message}")
        };

        self.history
            .push(ConversationMessage::Chat(ChatMessage::user(enriched)));

        let effective_model = self.classify_model(user_message);

        // ── Turn loop ──────────────────────────────────────────────────
        let mut saw_tool_calls = false;
        let mut empty_final_retries = 0usize;

        for _ in 0..self.config.max_tool_iterations {
            // Token-aware compression — keeps the Operator chat under the
            // model's context window.  Without this, accumulating tool
            // results (Manus task output, Kumiho revisions, web fetches)
            // can push past 1M tokens and break with `prompt is too long`.
            // `trim_history` alone is message-count based and does not
            // protect against this.
            let _ = self
                .compress_history_if_needed_streamed(&effective_model, &event_tx)
                .await;

            let messages = self.tool_dispatcher.to_provider_messages(&self.history);

            // Hard safety cap: even after compression, if we'd still send a
            // request that exceeds ~95% of the context window, fail loud
            // rather than producing nonsense or a confusing provider 400.
            // Surfaces inline in the chat panel via the Err return.
            {
                let window = context_window_for_model(&effective_model);
                let est = crate::agent::context_compressor::estimate_tokens(&messages);
                let hard_cap = (window as f64 * 0.95) as usize;
                if window > 0 && est > hard_cap {
                    anyhow::bail!(
                        "Conversation too long even after compression \
                         ({est} tokens > {hard_cap} cap for model {effective_model}). \
                         Start a new chat tab to continue."
                    );
                }
            }

            // Response cache check (same as turn)
            let cache_key = if self.temperature == 0.0 {
                self.response_cache.as_ref().map(|_| {
                    let last_user = messages
                        .iter()
                        .rfind(|m| m.role == "user")
                        .map(|m| m.content.as_str())
                        .unwrap_or("");
                    let system = messages
                        .iter()
                        .find(|m| m.role == "system")
                        .map(|m| m.content.as_str());
                    crate::memory::response_cache::ResponseCache::cache_key(
                        &effective_model,
                        system,
                        last_user,
                    )
                })
            } else {
                None
            };

            if let (Some(cache), Some(key)) = (&self.response_cache, &cache_key) {
                if let Ok(Some(cached)) = cache.get(key) {
                    self.observer.record_event(&ObserverEvent::CacheHit {
                        cache_type: "response".into(),
                        tokens_saved: 0,
                    });
                    self.history
                        .push(ConversationMessage::Chat(ChatMessage::assistant(
                            cached.clone(),
                        )));
                    self.trim_history();
                    return Ok(cached);
                }
                self.observer.record_event(&ObserverEvent::CacheMiss {
                    cache_type: "response".into(),
                });
            }

            // ── Streaming LLM call ────────────────────────────────────
            // Try streaming first; if the provider returns content we
            // forward deltas.  Otherwise fall back to non-streaming chat.
            use futures_util::StreamExt;

            // Rebuild tool_specs each iteration so newly activated deferred
            // tools (via tool_search) appear in the next LLM call.  Matches
            // run_tool_call_loop's pattern in loop_.rs.
            let mut iter_tool_specs: Vec<ToolSpec> = self.tools.iter().map(|t| t.spec()).collect();
            if let Some(at) = self.activated_tools.as_ref() {
                for spec in at.lock().unwrap().tool_specs() {
                    iter_tool_specs.push(spec);
                }
            }

            // Architect-mode runtime guard: when the gateway forwards an
            // `<editor-state>` block in the user message, hide the workflow
            // persistence tools so the LLM literally cannot call them. The
            // documented Architect contract is to PROPOSE YAML via
            // `propose_workflow_yaml` and let the editor own persistence.
            filter_tool_specs_for_architect(&mut iter_tool_specs, user_message);

            let stream_opts = crate::providers::traits::StreamOptions::new(true);
            let mut stream = self.provider.stream_chat(
                crate::providers::ChatRequest {
                    messages: &messages,
                    tools: if self.tool_dispatcher.should_send_tool_specs() {
                        Some(&iter_tool_specs)
                    } else {
                        None
                    },
                },
                &effective_model,
                self.temperature,
                stream_opts,
            );

            let mut streamed_text = String::new();
            let mut streamed_tool_calls: Vec<crate::providers::traits::ToolCall> = Vec::new();
            let mut got_stream = false;
            let mut streamed_usage: Option<crate::providers::traits::TokenUsage> = None;

            while let Some(item) = stream.next().await {
                match item {
                    Ok(event) => match event {
                        crate::providers::traits::StreamEvent::TextDelta(chunk) => {
                            if let Some(reasoning) = chunk.reasoning {
                                if !reasoning.is_empty() {
                                    let _ = event_tx
                                        .send(TurnEvent::Thinking { delta: reasoning })
                                        .await;
                                }
                            }
                            if !chunk.delta.is_empty() {
                                got_stream = true;
                                streamed_text.push_str(&chunk.delta);
                                let _ =
                                    event_tx.send(TurnEvent::Chunk { delta: chunk.delta }).await;
                            }
                        }
                        crate::providers::traits::StreamEvent::ToolCall(tc) => {
                            got_stream = true;
                            let _ = event_tx
                                .send(TurnEvent::ToolCall {
                                    name: tc.name.clone(),
                                    args: serde_json::from_str(&tc.arguments).unwrap_or_default(),
                                })
                                .await;
                            streamed_tool_calls.push(tc);
                        }
                        crate::providers::traits::StreamEvent::PreExecutedToolCall {
                            name,
                            args,
                        } => {
                            let _ = event_tx
                                .send(TurnEvent::ToolCall {
                                    name,
                                    args: serde_json::from_str(&args).unwrap_or_default(),
                                })
                                .await;
                            // NOT pushed to streamed_tool_calls — already executed by proxy
                        }
                        crate::providers::traits::StreamEvent::PreExecutedToolResult {
                            name,
                            output,
                        } => {
                            let _ = event_tx.send(TurnEvent::ToolResult { name, output }).await;
                        }
                        crate::providers::traits::StreamEvent::Usage(usage) => {
                            // Merge into accumulator — providers may emit multiple
                            // Usage events (Anthropic sends partial input on
                            // message_start, final output on message_delta).
                            let acc = streamed_usage.get_or_insert_with(Default::default);
                            if let Some(v) = usage.input_tokens {
                                acc.input_tokens = Some(v);
                            }
                            if let Some(v) = usage.output_tokens {
                                acc.output_tokens = Some(v);
                            }
                            if let Some(v) = usage.cached_input_tokens {
                                acc.cached_input_tokens = Some(v);
                            }
                        }
                        crate::providers::traits::StreamEvent::Final => break,
                    },
                    Err(_) => break,
                }
            }
            // Drop the stream so we release the borrow on provider.
            drop(stream);

            // If streaming produced text, use it as the response and
            // check for tool calls via the dispatcher.
            let response = if got_stream {
                // Record cost via the task-local tracker when the provider
                // reported usage mid-stream. No-op when the tracker context
                // isn't scoped (tests, CLI without cost config).
                let usage_for_cost = streamed_usage
                    .clone()
                    .unwrap_or_else(crate::providers::traits::TokenUsage::default);
                let _ = crate::agent::cost::record_tool_loop_cost_usage(
                    &self.provider_name,
                    &effective_model,
                    &usage_for_cost,
                );
                // Build a synthetic ChatResponse from streamed text
                crate::providers::ChatResponse {
                    text: Some(streamed_text),
                    tool_calls: streamed_tool_calls,
                    usage: streamed_usage.take(),
                    reasoning_content: None,
                }
            } else {
                // Fall back to non-streaming chat
                let resp = match self
                    .provider
                    .chat(
                        ChatRequest {
                            messages: &messages,
                            tools: if self.tool_dispatcher.should_send_tool_specs() {
                                Some(&iter_tool_specs)
                            } else {
                                None
                            },
                        },
                        &effective_model,
                        self.temperature,
                    )
                    .await
                {
                    Ok(resp) => resp,
                    Err(err) => {
                        // Reactive context-window recovery: if the provider
                        // signals overflow, parse the actual limit out of
                        // the error and compress, then retry the loop.
                        // Mirrors `loop_.rs:4579-4615`.
                        if crate::providers::reliable::is_context_window_exceeded(&err) {
                            tracing::warn!(
                                "Context overflow in Operator chat, attempting compression recovery"
                            );
                            let window = context_window_for_model(&effective_model);
                            let mut compressor =
                                crate::agent::context_compressor::ContextCompressor::new(
                                    self.config.context_compression.clone(),
                                    window,
                                )
                                .with_memory(Arc::clone(&self.memory));
                            let mut flat = self.tool_dispatcher.to_provider_messages(&self.history);
                            let error_msg = format!("{err}");
                            match compressor
                                .compress_on_error(
                                    &mut flat,
                                    self.provider.as_ref(),
                                    &effective_model,
                                    &error_msg,
                                )
                                .await
                            {
                                Ok(true) => {
                                    tracing::info!(
                                        "Context recovered via compression, retrying turn"
                                    );
                                    self.apply_compressed_history(&flat);
                                    continue;
                                }
                                Ok(false) => {
                                    tracing::warn!(
                                        "Compression ran but couldn't reduce enough below provider limit"
                                    );
                                }
                                Err(ce) => {
                                    tracing::warn!(
                                        error = %ce,
                                        "Compression failed during error recovery"
                                    );
                                }
                            }
                        }
                        return Err(err);
                    }
                };
                // Record cost — always, even when the provider omits usage — so
                // request_count on the cost page reflects every turn.
                let usage_for_cost = resp
                    .usage
                    .clone()
                    .unwrap_or_else(crate::providers::traits::TokenUsage::default);
                let _ = crate::agent::cost::record_tool_loop_cost_usage(
                    &self.provider_name,
                    &effective_model,
                    &usage_for_cost,
                );
                resp
            };

            let (text, calls) = self.tool_dispatcher.parse_response(&response);
            if calls.is_empty() {
                let mut final_text = if text.is_empty() {
                    response.text.unwrap_or_default()
                } else {
                    text
                };
                let mut synthesized_empty_fallback = false;

                if final_text.trim().is_empty() {
                    if saw_tool_calls && empty_final_retries == 0 {
                        empty_final_retries += 1;
                        tracing::warn!(
                            "Provider returned empty final response after tool calls; retrying once"
                        );
                        self.history
                            .push(ConversationMessage::Chat(ChatMessage::user(
                                EMPTY_FINAL_AFTER_TOOLS_RETRY_PROMPT,
                            )));
                        continue;
                    }
                    final_text = if saw_tool_calls {
                        EMPTY_FINAL_AFTER_TOOLS_FALLBACK.to_string()
                    } else {
                        EMPTY_FINAL_FALLBACK.to_string()
                    };
                    synthesized_empty_fallback = true;
                }

                // Store in response cache
                if !synthesized_empty_fallback
                    && let (Some(cache), Some(key)) = (&self.response_cache, &cache_key)
                {
                    let token_count = response
                        .usage
                        .as_ref()
                        .and_then(|u| u.output_tokens)
                        .unwrap_or(0);
                    #[allow(clippy::cast_possible_truncation)]
                    let _ = cache.put(key, &effective_model, &final_text, token_count as u32);
                }

                // If we didn't stream, send the full response as a single chunk
                if !got_stream && !final_text.is_empty() {
                    let _ = event_tx
                        .send(TurnEvent::Chunk {
                            delta: final_text.clone(),
                        })
                        .await;
                }

                self.history
                    .push(ConversationMessage::Chat(ChatMessage::assistant(
                        final_text.clone(),
                    )));
                self.trim_history();

                return Ok(final_text);
            }

            // ── Tool calls ─────────────────────────────────────────────
            saw_tool_calls = true;
            if !text.is_empty() {
                self.history
                    .push(ConversationMessage::Chat(ChatMessage::assistant(
                        text.clone(),
                    )));
            }

            self.history.push(ConversationMessage::AssistantToolCalls {
                text: response.text.clone(),
                tool_calls: response.tool_calls.clone(),
                reasoning_content: response.reasoning_content.clone(),
            });

            // Notify about each tool call (with operator status for orchestration tools)
            for call in &calls {
                // Emit operator status for construct-operator tools
                if let Some(status) = operator_status_for_tool_call(&call.name, &call.arguments) {
                    let _ = event_tx
                        .send(TurnEvent::OperatorStatus {
                            phase: status.0,
                            detail: status.1,
                        })
                        .await;
                }
                let _ = event_tx
                    .send(TurnEvent::ToolCall {
                        name: call.name.clone(),
                        args: call.arguments.clone(),
                    })
                    .await;
            }

            let results = self.execute_tools(&calls).await;

            // Notify about each tool result (with operator status for completion)
            for result in &results {
                if let Some(status) = operator_status_for_tool_result(&result.name, &result.output)
                {
                    let _ = event_tx
                        .send(TurnEvent::OperatorStatus {
                            phase: status.0,
                            detail: status.1,
                        })
                        .await;
                }
                let _ = event_tx
                    .send(TurnEvent::ToolResult {
                        name: result.name.clone(),
                        output: result.output.clone(),
                    })
                    .await;
            }

            let formatted = self.tool_dispatcher.format_results(&results);
            self.history.push(formatted);
            self.trim_history();
        }

        anyhow::bail!(
            "Agent exceeded maximum tool iterations ({})",
            self.config.max_tool_iterations
        )
    }

    pub async fn run_single(&mut self, message: &str) -> Result<String> {
        self.turn(message).await
    }

    pub async fn run_interactive(&mut self) -> Result<()> {
        println!("🦀 Construct Interactive Mode");
        println!("Type /quit to exit.\n");

        let (tx, mut rx) = tokio::sync::mpsc::channel(32);
        let cli = crate::channels::CliChannel::new();

        let listen_handle = tokio::spawn(async move {
            let _ = crate::channels::Channel::listen(&cli, tx).await;
        });

        while let Some(msg) = rx.recv().await {
            let response = match self.turn(&msg.content).await {
                Ok(resp) => resp,
                Err(e) => {
                    eprintln!("\nError: {e}\n");
                    continue;
                }
            };
            println!("\n{response}\n");
        }

        listen_handle.abort();
        Ok(())
    }
}

/// Map a operator MCP tool call to a human-friendly status message.
///
/// Returns `Some((phase, detail))` for `construct-operator__*` tools, `None` otherwise.
fn operator_status_for_tool_call(
    tool_name: &str,
    args: &serde_json::Value,
) -> Option<(String, String)> {
    let suffix = tool_name.strip_prefix("construct-operator__")?;
    match suffix {
        "create_agent" => {
            let title = args
                .get("title")
                .and_then(|v| v.as_str())
                .unwrap_or("agent");
            Some(("spawning".into(), format!("Spawning agent: {title}")))
        }
        "wait_for_agent" => {
            let id = args
                .get("agent_id")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown");
            Some(("waiting".into(), format!("Waiting for agent {id}…")))
        }
        "send_agent_prompt" => {
            let id = args
                .get("agent_id")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown");
            Some((
                "delegating".into(),
                format!("Sending follow-up to agent {id}"),
            ))
        }
        "get_agent_activity" => {
            let id = args
                .get("agent_id")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown");
            Some((
                "collecting".into(),
                format!("Collecting results from agent {id}"),
            ))
        }
        "get_agent_status" => Some(("checking".into(), "Checking agent status…".into())),
        "list_agents" => Some(("listing".into(), "Listing active agents…".into())),
        "search_agent_pool" | "list_agent_templates" => {
            Some(("searching".into(), "Searching agent pool…".into()))
        }
        "save_agent_template" => {
            let name = args
                .get("name")
                .and_then(|v| v.as_str())
                .unwrap_or("template");
            Some(("saving".into(), format!("Saving agent template: {name}")))
        }
        "list_teams" => Some(("searching".into(), "Listing agent teams…".into())),
        "get_team" => Some(("searching".into(), "Loading team details…".into())),
        "spawn_team" => {
            let task = args
                .get("task")
                .and_then(|v| v.as_str())
                .map(|t| {
                    if t.chars().count() > 60 {
                        let end = t.char_indices().nth(60).map_or(t.len(), |(i, _)| i);
                        &t[..end]
                    } else {
                        t
                    }
                })
                .unwrap_or("task");
            Some(("spawning".into(), format!("Deploying team for: {task}…")))
        }
        "create_team" => {
            let name = args.get("name").and_then(|v| v.as_str()).unwrap_or("team");
            Some(("saving".into(), format!("Creating team: {name}")))
        }
        "search_teams" => Some(("searching".into(), "Searching for teams…".into())),
        "get_budget_status" => Some(("checking".into(), "Checking budget status…".into())),
        "save_plan" => Some(("saving".into(), "Saving execution plan…".into())),
        "recall_plans" => Some(("searching".into(), "Searching past plans…".into())),
        "create_goal" => {
            let name = args.get("name").and_then(|v| v.as_str()).unwrap_or("goal");
            Some(("saving".into(), format!("Creating goal: {name}")))
        }
        "get_goals" => Some(("searching".into(), "Loading goals…".into())),
        "update_goal" => Some(("saving".into(), "Updating goal…".into())),
        "record_agent_outcome" => Some(("saving".into(), "Recording agent outcome…".into())),
        "get_agent_trust" => Some(("searching".into(), "Checking agent trust scores…".into())),
        "publish_to_clawhub" => Some(("saving".into(), "Publishing to ClawHub…".into())),
        "search_clawhub" => Some(("searching".into(), "Searching ClawHub marketplace…".into())),
        "install_from_clawhub" => Some(("saving".into(), "Installing from ClawHub…".into())),
        "list_nodes" => Some(("searching".into(), "Discovering connected nodes…".into())),
        "invoke_node" => Some(("working".into(), "Invoking node capability…".into())),
        "get_session_history" => Some(("searching".into(), "Loading session history…".into())),
        "archive_session" => Some(("saving".into(), "Archiving session…".into())),
        "capture_skill" => {
            let name = args.get("name").and_then(|v| v.as_str()).unwrap_or("skill");
            Some(("saving".into(), format!("Capturing skill: {name}")))
        }
        _ => Some(("working".into(), format!("Operator: {suffix}"))),
    }
}

/// Parse a operator agent/team JSON result into a (phase, detail) pair.
///
/// Checks the actual `status` and `error_count` fields in the JSON response
/// rather than naive string matching, which would false-positive on field names
/// like `"error_count": 0`.
fn operator_parse_agent_result(output: &str) -> (String, String) {
    if let Ok(json) = serde_json::from_str::<serde_json::Value>(output) {
        // Check explicit status field first
        let status = json.get("status").and_then(|v| v.as_str()).unwrap_or("");
        match status {
            "error" | "failed" | "backend_unreachable" => {
                let error_msg = json
                    .get("error")
                    .and_then(|v| v.as_str())
                    .or_else(|| json.get("hint").and_then(|v| v.as_str()))
                    .unwrap_or("Agent finished with errors");
                return ("failed".into(), error_msg.to_string());
            }
            "permission_blocked" => {
                let hint = json
                    .get("hint")
                    .and_then(|v| v.as_str())
                    .unwrap_or("Agent blocked on permissions");
                return ("blocked".into(), hint.to_string());
            }
            "running" => {
                return ("running".into(), "Agent still running".into());
            }
            _ => {}
        }

        // Check error_count for actual errors (not just the field existing)
        let error_count = json
            .get("error_count")
            .and_then(|v| v.as_u64())
            .unwrap_or(0);
        if error_count > 0 {
            let detail = format!("Agent completed with {} error(s)", error_count);
            return ("failed".into(), detail);
        }

        // Check for top-level "error" string field (non-status responses like spawn failures)
        if let Some(err) = json.get("error").and_then(|v| v.as_str()) {
            return ("failed".into(), err.to_string());
        }

        ("completed".into(), "Agent finished successfully".into())
    } else {
        // Fallback: couldn't parse JSON — use conservative heuristic
        // Only match "error" as a JSON value pattern, not as a field name
        if output.contains("\"status\":\"error\"") || output.contains("\"status\": \"error\"") {
            ("failed".into(), "Agent finished with errors".into())
        } else {
            ("completed".into(), "Agent finished successfully".into())
        }
    }
}

/// Map a operator MCP tool result to a human-friendly status message.
///
/// Returns `Some((phase, detail))` for `construct-operator__*` tools, `None` otherwise.
fn operator_status_for_tool_result(tool_name: &str, output: &str) -> Option<(String, String)> {
    let suffix = tool_name.strip_prefix("construct-operator__")?;
    match suffix {
        "create_agent" => Some(("spawned".into(), "Agent created successfully".into())),
        "wait_for_agent" => Some(operator_parse_agent_result(output)),
        "get_agent_activity" => Some(("collected".into(), "Results collected".into())),
        "send_agent_prompt" => Some(("completed".into(), "Follow-up sent".into())),
        "list_agents" => Some(("completed".into(), "Agent list retrieved".into())),
        "search_agent_pool" | "list_agent_templates" => {
            Some(("completed".into(), "Pool search complete".into()))
        }
        "save_agent_template" => Some(("completed".into(), "Template saved".into())),
        "list_teams" | "search_teams" => Some(("completed".into(), "Team search complete".into())),
        "get_team" => Some(("completed".into(), "Team details loaded".into())),
        "spawn_team" => Some(operator_parse_agent_result(output)),
        "create_team" => Some(("completed".into(), "Team created".into())),
        "get_budget_status" => Some(("completed".into(), "Budget status retrieved".into())),
        "save_plan" => Some(("completed".into(), "Plan saved".into())),
        "recall_plans" => Some(("completed".into(), "Past plans retrieved".into())),
        "create_goal" => Some(("completed".into(), "Goal created".into())),
        "get_goals" => Some(("completed".into(), "Goals loaded".into())),
        "update_goal" => Some(("completed".into(), "Goal updated".into())),
        "record_agent_outcome" => Some(("completed".into(), "Outcome recorded".into())),
        "get_agent_trust" => Some(("completed".into(), "Trust scores retrieved".into())),
        "capture_skill" => Some(("completed".into(), "Skill captured".into())),
        "publish_to_clawhub" => Some(("completed".into(), "Published to ClawHub".into())),
        "search_clawhub" => Some(("completed".into(), "ClawHub search complete".into())),
        "install_from_clawhub" => Some(("completed".into(), "Installed from ClawHub".into())),
        "list_nodes" => Some(("completed".into(), "Nodes discovered".into())),
        "invoke_node" => Some(("completed".into(), "Node invocation complete".into())),
        "get_session_history" => Some(("completed".into(), "Session history loaded".into())),
        "archive_session" => Some(("completed".into(), "Session archived".into())),
        _ => None,
    }
}

pub async fn run(
    config: Config,
    message: Option<String>,
    provider_override: Option<String>,
    model_override: Option<String>,
    temperature: f64,
) -> Result<()> {
    let start = Instant::now();

    let mut effective_config = config;
    if let Some(p) = provider_override {
        effective_config.default_provider = Some(p);
    }
    if let Some(m) = model_override {
        effective_config.default_model = Some(m);
    }
    effective_config.default_temperature = temperature;

    let mut agent = Agent::from_config(&effective_config).await?;

    let provider_name = effective_config
        .default_provider
        .as_deref()
        .unwrap_or("openrouter")
        .to_string();
    let model_name = effective_config
        .default_model
        .as_deref()
        .unwrap_or("anthropic/claude-sonnet-4-20250514")
        .to_string();

    agent.observer.record_event(&ObserverEvent::AgentStart {
        provider: provider_name.clone(),
        model: model_name.clone(),
    });

    if let Some(msg) = message {
        let response = agent.run_single(&msg).await?;
        println!("{response}");
    } else {
        agent.run_interactive().await?;
    }

    agent.observer.record_event(&ObserverEvent::AgentEnd {
        provider: provider_name,
        model: model_name,
        duration: start.elapsed(),
        tokens_used: None,
        cost_usd: None,
    });

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use async_trait::async_trait;
    use parking_lot::Mutex;
    use std::collections::HashMap;

    struct MockProvider {
        responses: Mutex<Vec<crate::providers::ChatResponse>>,
    }

    #[async_trait]
    impl Provider for MockProvider {
        async fn chat_with_system(
            &self,
            _system_prompt: Option<&str>,
            _message: &str,
            _model: &str,
            _temperature: f64,
        ) -> Result<String> {
            Ok("ok".into())
        }

        async fn chat(
            &self,
            _request: ChatRequest<'_>,
            _model: &str,
            _temperature: f64,
        ) -> Result<crate::providers::ChatResponse> {
            let mut guard = self.responses.lock();
            if guard.is_empty() {
                return Ok(crate::providers::ChatResponse {
                    text: Some("done".into()),
                    tool_calls: vec![],
                    usage: None,
                    reasoning_content: None,
                });
            }
            Ok(guard.remove(0))
        }
    }

    struct ModelCaptureProvider {
        responses: Mutex<Vec<crate::providers::ChatResponse>>,
        seen_models: Arc<Mutex<Vec<String>>>,
    }

    #[async_trait]
    impl Provider for ModelCaptureProvider {
        async fn chat_with_system(
            &self,
            _system_prompt: Option<&str>,
            _message: &str,
            _model: &str,
            _temperature: f64,
        ) -> Result<String> {
            Ok("ok".into())
        }

        async fn chat(
            &self,
            _request: ChatRequest<'_>,
            model: &str,
            _temperature: f64,
        ) -> Result<crate::providers::ChatResponse> {
            self.seen_models.lock().push(model.to_string());
            let mut guard = self.responses.lock();
            if guard.is_empty() {
                return Ok(crate::providers::ChatResponse {
                    text: Some("done".into()),
                    tool_calls: vec![],
                    usage: None,
                    reasoning_content: None,
                });
            }
            Ok(guard.remove(0))
        }
    }

    struct MockTool;

    #[async_trait]
    impl Tool for MockTool {
        fn name(&self) -> &str {
            "echo"
        }

        fn description(&self) -> &str {
            "echo"
        }

        fn parameters_schema(&self) -> serde_json::Value {
            serde_json::json!({"type": "object"})
        }

        async fn execute(&self, _args: serde_json::Value) -> Result<crate::tools::ToolResult> {
            Ok(crate::tools::ToolResult {
                success: true,
                output: "tool-out".into(),
                error: None,
            })
        }
    }

    #[tokio::test]
    async fn turn_without_tools_returns_text() {
        let provider = Box::new(MockProvider {
            responses: Mutex::new(vec![crate::providers::ChatResponse {
                text: Some("hello".into()),
                tool_calls: vec![],
                usage: None,
                reasoning_content: None,
            }]),
        });

        let memory_cfg = crate::config::MemoryConfig {
            backend: "none".into(),
            ..crate::config::MemoryConfig::default()
        };
        let mem: Arc<dyn Memory> = Arc::from(
            crate::memory::create_memory(&memory_cfg, std::path::Path::new("/tmp"), None)
                .expect("memory creation should succeed with valid config"),
        );

        let observer: Arc<dyn Observer> = Arc::from(crate::observability::NoopObserver {});
        let mut agent = Agent::builder()
            .provider(provider)
            .tools(vec![Box::new(MockTool)])
            .memory(mem)
            .observer(observer)
            .tool_dispatcher(Box::new(XmlToolDispatcher))
            .workspace_dir(std::path::PathBuf::from("/tmp"))
            .build()
            .expect("agent builder should succeed with valid config");

        let response = agent.turn("hi").await.unwrap();
        assert_eq!(response, "hello");
    }

    #[tokio::test]
    async fn turn_with_native_dispatcher_handles_tool_results_variant() {
        let provider = Box::new(MockProvider {
            responses: Mutex::new(vec![
                crate::providers::ChatResponse {
                    text: Some(String::new()),
                    tool_calls: vec![crate::providers::ToolCall {
                        id: "tc1".into(),
                        name: "echo".into(),
                        arguments: "{}".into(),
                    }],
                    usage: None,
                    reasoning_content: None,
                },
                crate::providers::ChatResponse {
                    text: Some("done".into()),
                    tool_calls: vec![],
                    usage: None,
                    reasoning_content: None,
                },
            ]),
        });

        let memory_cfg = crate::config::MemoryConfig {
            backend: "none".into(),
            ..crate::config::MemoryConfig::default()
        };
        let mem: Arc<dyn Memory> = Arc::from(
            crate::memory::create_memory(&memory_cfg, std::path::Path::new("/tmp"), None)
                .expect("memory creation should succeed with valid config"),
        );

        let observer: Arc<dyn Observer> = Arc::from(crate::observability::NoopObserver {});
        let mut agent = Agent::builder()
            .provider(provider)
            .tools(vec![Box::new(MockTool)])
            .memory(mem)
            .observer(observer)
            .tool_dispatcher(Box::new(NativeToolDispatcher))
            .workspace_dir(std::path::PathBuf::from("/tmp"))
            .build()
            .expect("agent builder should succeed with valid config");

        let response = agent.turn("hi").await.unwrap();
        assert_eq!(response, "done");
        assert!(
            agent
                .history()
                .iter()
                .any(|msg| matches!(msg, ConversationMessage::ToolResults(_)))
        );
    }

    #[tokio::test]
    async fn turn_routes_with_hint_when_query_classification_matches() {
        let seen_models = Arc::new(Mutex::new(Vec::new()));
        let provider = Box::new(ModelCaptureProvider {
            responses: Mutex::new(vec![crate::providers::ChatResponse {
                text: Some("classified".into()),
                tool_calls: vec![],
                usage: None,
                reasoning_content: None,
            }]),
            seen_models: seen_models.clone(),
        });

        let memory_cfg = crate::config::MemoryConfig {
            backend: "none".into(),
            ..crate::config::MemoryConfig::default()
        };
        let mem: Arc<dyn Memory> = Arc::from(
            crate::memory::create_memory(&memory_cfg, std::path::Path::new("/tmp"), None)
                .expect("memory creation should succeed with valid config"),
        );

        let observer: Arc<dyn Observer> = Arc::from(crate::observability::NoopObserver {});
        let mut route_model_by_hint = HashMap::new();
        route_model_by_hint.insert("fast".to_string(), "anthropic/claude-haiku-4-5".to_string());
        let mut agent = Agent::builder()
            .provider(provider)
            .tools(vec![Box::new(MockTool)])
            .memory(mem)
            .observer(observer)
            .tool_dispatcher(Box::new(NativeToolDispatcher))
            .workspace_dir(std::path::PathBuf::from("/tmp"))
            .classification_config(crate::config::QueryClassificationConfig {
                enabled: true,
                rules: vec![crate::config::ClassificationRule {
                    hint: "fast".to_string(),
                    keywords: vec!["quick".to_string()],
                    patterns: vec![],
                    min_length: None,
                    max_length: None,
                    priority: 10,
                }],
            })
            .available_hints(vec!["fast".to_string()])
            .route_model_by_hint(route_model_by_hint)
            .build()
            .expect("agent builder should succeed with valid config");

        let response = agent.turn("quick summary please").await.unwrap();
        assert_eq!(response, "classified");
        let seen = seen_models.lock();
        assert_eq!(seen.as_slice(), &["hint:fast".to_string()]);
    }

    #[tokio::test]
    async fn from_config_passes_extra_headers_to_custom_provider() {
        use axum::{Json, Router, http::HeaderMap, routing::post};
        use tempfile::TempDir;
        use tokio::net::TcpListener;

        let captured_headers: Arc<std::sync::Mutex<Option<HashMap<String, String>>>> =
            Arc::new(std::sync::Mutex::new(None));
        let captured_headers_clone = captured_headers.clone();

        let app = Router::new().route(
            "/chat/completions",
            post(
                move |headers: HeaderMap, Json(_body): Json<serde_json::Value>| {
                    let captured_headers = captured_headers_clone.clone();
                    async move {
                        let collected = headers
                            .iter()
                            .filter_map(|(name, value)| {
                                value
                                    .to_str()
                                    .ok()
                                    .map(|value| (name.as_str().to_string(), value.to_string()))
                            })
                            .collect();
                        *captured_headers.lock().unwrap() = Some(collected);
                        Json(serde_json::json!({
                            "choices": [{
                                "message": {
                                    "content": "hello from mock"
                                }
                            }]
                        }))
                    }
                },
            ),
        );

        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let server_handle = tokio::spawn(async move {
            axum::serve(listener, app).await.unwrap();
        });

        let tmp = TempDir::new().expect("temp dir");
        let workspace_dir = tmp.path().join("workspace");
        std::fs::create_dir_all(&workspace_dir).unwrap();

        let mut config = crate::config::Config::default();
        config.workspace_dir = workspace_dir;
        config.config_path = tmp.path().join("config.toml");
        config.api_key = Some("test-key".to_string());
        config.default_provider = Some(format!("custom:http://{addr}"));
        config.default_model = Some("test-model".to_string());
        config.memory.backend = "none".to_string();
        config.memory.auto_save = false;
        config.extra_headers.insert(
            "User-Agent".to_string(),
            "construct-web-test/1.0".to_string(),
        );
        config
            .extra_headers
            .insert("X-Title".to_string(), "construct-web".to_string());

        let mut agent = Agent::from_config(&config)
            .await
            .expect("agent from config");
        let response = agent.turn("hello").await.expect("agent turn");

        assert_eq!(response, "hello from mock");

        let headers = captured_headers
            .lock()
            .unwrap()
            .clone()
            .expect("captured headers");
        assert_eq!(
            headers.get("user-agent").map(String::as_str),
            Some("construct-web-test/1.0")
        );
        assert_eq!(
            headers.get("x-title").map(String::as_str),
            Some("construct-web")
        );

        server_handle.abort();
    }

    #[test]
    fn builder_allowed_tools_none_keeps_all_tools() {
        let provider = Box::new(MockProvider {
            responses: Mutex::new(vec![]),
        });

        let memory_cfg = crate::config::MemoryConfig {
            backend: "none".into(),
            ..crate::config::MemoryConfig::default()
        };
        let mem: Arc<dyn Memory> = Arc::from(
            crate::memory::create_memory(&memory_cfg, std::path::Path::new("/tmp"), None)
                .expect("memory creation should succeed with valid config"),
        );

        let observer: Arc<dyn Observer> = Arc::from(crate::observability::NoopObserver {});
        let agent = Agent::builder()
            .provider(provider)
            .tools(vec![Box::new(MockTool)])
            .memory(mem)
            .observer(observer)
            .tool_dispatcher(Box::new(NativeToolDispatcher))
            .workspace_dir(std::path::PathBuf::from("/tmp"))
            .allowed_tools(None)
            .build()
            .expect("agent builder should succeed with valid config");

        assert_eq!(agent.tool_specs.len(), 1);
        assert_eq!(agent.tool_specs[0].name, "echo");
    }

    #[test]
    fn builder_allowed_tools_some_filters_tools() {
        let provider = Box::new(MockProvider {
            responses: Mutex::new(vec![]),
        });

        let memory_cfg = crate::config::MemoryConfig {
            backend: "none".into(),
            ..crate::config::MemoryConfig::default()
        };
        let mem: Arc<dyn Memory> = Arc::from(
            crate::memory::create_memory(&memory_cfg, std::path::Path::new("/tmp"), None)
                .expect("memory creation should succeed with valid config"),
        );

        let observer: Arc<dyn Observer> = Arc::from(crate::observability::NoopObserver {});
        let agent = Agent::builder()
            .provider(provider)
            .tools(vec![Box::new(MockTool)])
            .memory(mem)
            .observer(observer)
            .tool_dispatcher(Box::new(NativeToolDispatcher))
            .workspace_dir(std::path::PathBuf::from("/tmp"))
            .allowed_tools(Some(vec!["nonexistent".to_string()]))
            .build()
            .expect("agent builder should succeed with valid config");

        assert!(
            agent.tool_specs.is_empty(),
            "No tools should match a non-existent allowlist entry"
        );
    }

    #[test]
    fn seed_history_prepends_system_and_skips_system_from_seed() {
        let provider = Box::new(MockProvider {
            responses: Mutex::new(vec![]),
        });

        let memory_cfg = crate::config::MemoryConfig {
            backend: "none".into(),
            ..crate::config::MemoryConfig::default()
        };
        let mem: Arc<dyn Memory> = Arc::from(
            crate::memory::create_memory(&memory_cfg, std::path::Path::new("/tmp"), None)
                .expect("memory creation should succeed with valid config"),
        );

        let observer: Arc<dyn Observer> = Arc::from(crate::observability::NoopObserver {});
        let mut agent = Agent::builder()
            .provider(provider)
            .tools(vec![Box::new(MockTool)])
            .memory(mem)
            .observer(observer)
            .tool_dispatcher(Box::new(NativeToolDispatcher))
            .workspace_dir(std::path::PathBuf::from("/tmp"))
            .build()
            .expect("agent builder should succeed with valid config");

        let seed = vec![
            ChatMessage::system("old system prompt"),
            ChatMessage::user("hello"),
            ChatMessage::assistant("hi there"),
        ];
        agent.seed_history(&seed);

        let history = agent.history();
        // First message should be a freshly built system prompt (not the seed one)
        assert!(matches!(&history[0], ConversationMessage::Chat(m) if m.role == "system"));
        // System message from seed should be skipped, so next is user
        assert!(
            matches!(&history[1], ConversationMessage::Chat(m) if m.role == "user" && m.content == "hello")
        );
        assert!(
            matches!(&history[2], ConversationMessage::Chat(m) if m.role == "assistant" && m.content == "hi there")
        );
        assert_eq!(history.len(), 3);
    }

    /// Construct a minimal agent suitable for testing `trim_history` in isolation.
    fn make_trim_test_agent(max: usize) -> Agent {
        let provider = Box::new(MockProvider {
            responses: Mutex::new(vec![]),
        });

        let memory_cfg = crate::config::MemoryConfig {
            backend: "none".into(),
            ..crate::config::MemoryConfig::default()
        };
        let mem: Arc<dyn Memory> = Arc::from(
            crate::memory::create_memory(&memory_cfg, std::path::Path::new("/tmp"), None)
                .expect("memory creation should succeed with valid config"),
        );

        let observer: Arc<dyn Observer> = Arc::from(crate::observability::NoopObserver {});
        let mut agent = Agent::builder()
            .provider(provider)
            .tools(vec![Box::new(MockTool)])
            .memory(mem)
            .observer(observer)
            .tool_dispatcher(Box::new(NativeToolDispatcher))
            .workspace_dir(std::path::PathBuf::from("/tmp"))
            .build()
            .expect("agent builder should succeed with valid config");
        agent.config.max_history_messages = max;
        agent
    }

    fn chat_user(text: &str) -> ConversationMessage {
        ConversationMessage::Chat(ChatMessage::user(text))
    }

    fn assistant_tool_calls(id: &str) -> ConversationMessage {
        ConversationMessage::AssistantToolCalls {
            text: None,
            tool_calls: vec![crate::providers::ToolCall {
                id: id.into(),
                name: "echo".into(),
                arguments: "{}".into(),
            }],
            reasoning_content: None,
        }
    }

    fn tool_results(id: &str) -> ConversationMessage {
        ConversationMessage::ToolResults(vec![crate::providers::ToolResultMessage {
            tool_call_id: id.into(),
            content: "ok".into(),
        }])
    }

    #[test]
    fn trim_history_no_tools_keeps_last_n() {
        let mut agent = make_trim_test_agent(5);
        for i in 0..10 {
            agent.history.push(chat_user(&format!("u{i}")));
        }
        agent.trim_history();
        assert_eq!(agent.history.len(), 5);
        // First kept message should be u5 (dropped u0..u4).
        assert!(
            matches!(&agent.history[0], ConversationMessage::Chat(m) if m.content == "u5"),
            "expected first kept message to be u5, got {:?}",
            agent.history[0]
        );
    }

    #[test]
    fn trim_history_preserves_intact_tool_pair() {
        // [Chat, Chat, Chat, AssistantToolCalls, ToolResults, Chat, Chat, Chat] max=5
        // drop_count = 8 - 5 = 3, lands on AssistantToolCalls (kept) -> no advance.
        let mut agent = make_trim_test_agent(5);
        agent.history.push(chat_user("c0"));
        agent.history.push(chat_user("c1"));
        agent.history.push(chat_user("c2"));
        agent.history.push(assistant_tool_calls("tc1"));
        agent.history.push(tool_results("tc1"));
        agent.history.push(chat_user("c3"));
        agent.history.push(chat_user("c4"));
        agent.history.push(chat_user("c5"));

        agent.trim_history();

        assert_eq!(agent.history.len(), 5);
        assert!(matches!(
            &agent.history[0],
            ConversationMessage::AssistantToolCalls { .. }
        ));
        assert!(matches!(
            &agent.history[1],
            ConversationMessage::ToolResults(_)
        ));
    }

    #[test]
    fn trim_history_advances_past_orphan_tool_results() {
        // [Chat, Chat, AssistantToolCalls, ToolResults, Chat, Chat, Chat, Chat, Chat, Chat] max=7
        // drop_count = 10 - 7 = 3, lands on ToolResults (orphan) -> advance to 4 (Chat).
        // Result: 6 messages, all trailing Chats.
        let mut agent = make_trim_test_agent(7);
        agent.history.push(chat_user("c0"));
        agent.history.push(chat_user("c1"));
        agent.history.push(assistant_tool_calls("tc1"));
        agent.history.push(tool_results("tc1"));
        for i in 0..6 {
            agent.history.push(chat_user(&format!("t{i}")));
        }

        agent.trim_history();

        assert_eq!(agent.history.len(), 6);
        // First kept message must NOT be a ToolResults.
        assert!(
            !matches!(&agent.history[0], ConversationMessage::ToolResults(_)),
            "first message after trim must not be an orphaned ToolResults"
        );
        assert!(
            matches!(&agent.history[0], ConversationMessage::Chat(m) if m.content == "t0"),
            "expected first kept message to be t0, got {:?}",
            agent.history[0]
        );
    }

    #[test]
    fn trim_history_advances_past_consecutive_tool_results() {
        // [Chat, AssistantToolCalls, ToolResults, ToolResults, Chat] max=2
        // drop_count = 5 - 2 = 3, lands on ToolResults at idx 3 -> advance to 4 (Chat).
        // We sacrifice the configured max=2 -> actual=1 to avoid orphan.
        let mut agent = make_trim_test_agent(2);
        agent.history.push(chat_user("c0"));
        agent.history.push(assistant_tool_calls("tc1"));
        agent.history.push(tool_results("tc1"));
        agent.history.push(tool_results("tc1"));
        agent.history.push(chat_user("c1"));

        agent.trim_history();

        assert_eq!(agent.history.len(), 1);
        assert!(
            matches!(&agent.history[0], ConversationMessage::Chat(m) if m.content == "c1"),
            "expected only the trailing Chat to remain, got {:?}",
            agent.history[0]
        );
    }

    /// Mock provider that captures whether tool specs were passed to `stream_chat`
    /// and returns a tool call followed by a text response through the stream.
    struct StreamToolCaptureProvider {
        tools_received: Arc<Mutex<Vec<bool>>>,
        call_count: Arc<Mutex<usize>>,
    }

    #[async_trait]
    impl Provider for StreamToolCaptureProvider {
        async fn chat_with_system(
            &self,
            _system_prompt: Option<&str>,
            _message: &str,
            _model: &str,
            _temperature: f64,
        ) -> Result<String> {
            Ok("ok".into())
        }

        async fn chat(
            &self,
            request: ChatRequest<'_>,
            _model: &str,
            _temperature: f64,
        ) -> Result<crate::providers::ChatResponse> {
            self.tools_received.lock().push(request.tools.is_some());
            let mut count = self.call_count.lock();
            *count += 1;
            if *count == 1 {
                Ok(crate::providers::ChatResponse {
                    text: Some(String::new()),
                    tool_calls: vec![crate::providers::ToolCall {
                        id: "tc_stream_1".into(),
                        name: "echo".into(),
                        arguments: "{}".into(),
                    }],
                    usage: None,
                    reasoning_content: None,
                })
            } else {
                Ok(crate::providers::ChatResponse {
                    text: Some("stream-done".into()),
                    tool_calls: vec![],
                    usage: None,
                    reasoning_content: None,
                })
            }
        }

        fn supports_native_tools(&self) -> bool {
            true
        }

        fn stream_chat(
            &self,
            request: ChatRequest<'_>,
            _model: &str,
            _temperature: f64,
            _options: crate::providers::traits::StreamOptions,
        ) -> futures_util::stream::BoxStream<
            'static,
            crate::providers::traits::StreamResult<crate::providers::traits::StreamEvent>,
        > {
            use futures_util::stream::{self, StreamExt};
            self.tools_received.lock().push(request.tools.is_some());
            let mut count = self.call_count.lock();
            *count += 1;
            if *count == 1 {
                let tc =
                    crate::providers::traits::StreamEvent::ToolCall(crate::providers::ToolCall {
                        id: "tc_stream_1".into(),
                        name: "echo".into(),
                        arguments: "{}".into(),
                    });
                stream::iter(vec![
                    Ok(tc),
                    Ok(crate::providers::traits::StreamEvent::Final),
                ])
                .boxed()
            } else {
                let chunk = crate::providers::traits::StreamEvent::TextDelta(
                    crate::providers::traits::StreamChunk {
                        delta: "stream-done".into(),
                        is_final: false,
                        reasoning: None,
                        token_count: 0,
                    },
                );
                stream::iter(vec![
                    Ok(chunk),
                    Ok(crate::providers::traits::StreamEvent::Final),
                ])
                .boxed()
            }
        }
    }

    #[tokio::test]
    async fn turn_streamed_passes_tool_specs_to_provider() {
        let tools_received = Arc::new(Mutex::new(Vec::new()));
        let provider = Box::new(StreamToolCaptureProvider {
            tools_received: tools_received.clone(),
            call_count: Arc::new(Mutex::new(0)),
        });

        let memory_cfg = crate::config::MemoryConfig {
            backend: "none".into(),
            ..crate::config::MemoryConfig::default()
        };
        let mem: Arc<dyn Memory> = Arc::from(
            crate::memory::create_memory(&memory_cfg, std::path::Path::new("/tmp"), None)
                .expect("memory creation should succeed with valid config"),
        );

        let observer: Arc<dyn Observer> = Arc::from(crate::observability::NoopObserver {});
        let mut agent = Agent::builder()
            .provider(provider)
            .tools(vec![Box::new(MockTool)])
            .memory(mem)
            .observer(observer)
            .tool_dispatcher(Box::new(NativeToolDispatcher))
            .workspace_dir(std::path::PathBuf::from("/tmp"))
            .build()
            .expect("agent builder should succeed with valid config");

        let (event_tx, mut event_rx) = tokio::sync::mpsc::channel::<TurnEvent>(64);
        let response = agent
            .turn_streamed("use the echo tool", event_tx)
            .await
            .unwrap();
        assert_eq!(response, "stream-done");

        // Verify tools were passed in both stream_chat calls
        let received = tools_received.lock();
        assert!(
            received.len() >= 2,
            "Expected at least 2 stream_chat calls, got {}",
            received.len()
        );
        assert!(
            received[0],
            "First stream_chat call should have received tool specs"
        );
        assert!(
            received[1],
            "Second stream_chat call should have received tool specs"
        );

        // Collect events and verify tool call + tool result were emitted
        let mut events = Vec::new();
        while let Ok(ev) = event_rx.try_recv() {
            events.push(ev);
        }
        let has_tool_call = events
            .iter()
            .any(|e| matches!(e, TurnEvent::ToolCall { name, .. } if name == "echo"));
        let has_tool_result = events
            .iter()
            .any(|e| matches!(e, TurnEvent::ToolResult { name, .. } if name == "echo"));
        assert!(
            has_tool_call,
            "Should have emitted a ToolCall event for 'echo'"
        );
        assert!(
            has_tool_result,
            "Should have emitted a ToolResult event for 'echo'"
        );
    }
}
