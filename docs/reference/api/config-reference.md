# Construct Config Reference (Operator-Oriented)

This is a high-signal reference for common config sections and defaults.

Last verified: **May 13, 2026**.

For the complete, machine-readable schema (every key, every default) run:

```bash
construct config schema > schema.json
```

Config path resolution at startup:

1. `CONSTRUCT_WORKSPACE` override (if set)
2. persisted `~/.construct/active_workspace.toml` marker (if present)
3. default `~/.construct/config.toml`

Construct logs the resolved config on startup at `INFO` level:

- `Config loaded` with fields: `path`, `workspace`, `source`, `initialized`

Schema export command:

- `construct config schema` (prints JSON Schema draft 2020-12 to stdout)

## Core Keys

| Key | Default | Notes |
|---|---|---|
| `default_provider` | `openrouter` | provider ID or alias |
| `default_model` | `anthropic/claude-sonnet-4-6` | model routed through selected provider |
| `default_temperature` | `0.7` | model temperature |
| `provider_timeout_secs` | `120` | HTTP request timeout in seconds for LLM provider API calls |
| `provider_max_tokens` | unset (install default: `16256`) | Maximum output tokens included in provider API requests |

<!-- TODO screenshot: editor showing the [observability] section of config.toml -->
![Editor showing the [observability] section of config.toml](../../assets/reference/config-reference-02-observability-section.png)

## `[observability]`

| Key | Default | Purpose |
|---|---|---|
| `backend` | `none` | Observability backend: `none`, `noop`, `log`, `prometheus`, `otel`, `opentelemetry`, or `otlp` |
| `otel_endpoint` | `http://localhost:4318` | OTLP HTTP endpoint used when backend is `otel` |
| `otel_service_name` | `construct` | Service name emitted to OTLP collector |
| `runtime_trace_mode` | `none` | Runtime trace storage mode: `none`, `rolling`, or `full` |
| `runtime_trace_path` | `state/runtime-trace.jsonl` | Runtime trace JSONL path (relative to workspace unless absolute) |
| `runtime_trace_max_entries` | `200` | Maximum retained events when `runtime_trace_mode = "rolling"` |

Notes:

- `backend = "otel"` uses OTLP HTTP export with a blocking exporter client so spans and metrics can be emitted safely from non-Tokio contexts.
- Alias values `opentelemetry` and `otlp` map to the same OTel backend.
- Runtime traces are intended for debugging tool-call failures and malformed model tool payloads. They can contain model output text, so keep this disabled by default on shared hosts.
- Query runtime traces with:
  - `construct doctor traces --limit 20`
  - `construct doctor traces --event tool_call_result --contains \"error\"`
  - `construct doctor traces --id <trace-id>`

Example:

```toml
[observability]
backend = "otel"
otel_endpoint = "http://localhost:4318"
otel_service_name = "construct"
runtime_trace_mode = "rolling"
runtime_trace_path = "state/runtime-trace.jsonl"
runtime_trace_max_entries = 200
```

<!-- TODO screenshot: terminal setting CONSTRUCT_PROVIDER env var and running construct with the overridden provider -->
![Terminal setting CONSTRUCT_PROVIDER env var and running construct with the overridden provider](../../assets/reference/config-reference-01-env-override-terminal.png)

## Environment Provider Overrides

Provider selection can also be controlled by environment variables. Precedence is:

1. `CONSTRUCT_PROVIDER` (explicit override, always wins when non-empty)
2. `PROVIDER` (legacy fallback, only applied when config provider is unset or still `openrouter`)
3. `default_provider` in `config.toml`

Operational note for container users:

- If your `config.toml` sets an explicit custom provider like `custom:https://.../v1`, a default `PROVIDER=openrouter` from Docker/container env will no longer replace it.
- Use `CONSTRUCT_PROVIDER` when you intentionally want runtime env to override a non-default configured provider.

## `[agent]`

| Key | Default | Purpose |
|---|---|---|
| `compact_context` | `true` | When true: bootstrap_max_chars=6000, rag_chunk_limit=2. Use for 13B or smaller models |
| `max_tool_iterations` | `60` | Maximum tool-call loop turns per user message across CLI, gateway, and channels |
| `max_history_messages` | `1000` | Maximum conversation history messages retained per session |
| `max_context_tokens` | `1050000` | Token budget used by loop-level context trimming/compression |
| `model_context_windows` | `{}` | Per-model context window overrides used by Operator chat compression and hard-cap checks |
| `context_window_safety_ratio` | `0.95` | Fraction of the model context window allowed before Construct fails loud |
| `parallel_tools` | `true` | Enable parallel tool execution within a single iteration |
| `tool_dispatcher` | `auto` | Tool dispatch strategy |
| `tool_call_dedup_exempt` | `[]` | Tool names exempt from within-turn duplicate-call suppression |
| `tool_filter_groups` | `[]` | Per-turn MCP tool schema filter groups (see below) |
| `max_tool_result_chars` | `50000` | Maximum characters retained for a single tool result before middle truncation |
| `keep_tool_context_turns` | `2` | Recent turns whose full tool-call/result messages are preserved in channel history |

Notes:

- Setting `max_tool_iterations = 0` falls back to safe default `60`.
- If a channel message exceeds this value, the runtime returns: `Agent exceeded maximum tool iterations (<value>)`.
- In CLI, gateway, and channel tool loops, multiple independent tool calls are executed concurrently by default when the pending calls do not require approval gating; result order remains stable.
- `parallel_tools` applies to the `Agent::turn()` API surface. It does not gate the runtime loop used by CLI, gateway, or channel handlers.
- `tool_call_dedup_exempt` accepts an array of exact tool names. Tools listed here are allowed to be called multiple times with identical arguments in the same turn, bypassing the dedup check. Example: `tool_call_dedup_exempt = ["browser"]`.
- `model_context_windows` keys are matched case-insensitively against full model IDs and provider-stripped model IDs. For TOML bare keys, use `_` in place of `.`: `[agent.model_context_windows] gpt-5_5 = 1050000`.
- `context_window_safety_ratio` is clamped to `1.0`; values `<= 0` fall back to `0.95`.

### `[agent.context_compression]`

| Key | Default | Purpose |
|---|---|---|
| `enabled` | `true` | Enable automatic context compression |
| `threshold_ratio` | `0.5` | Fraction of the context window that triggers compression |
| `protect_first_n` | `3` | Messages protected at the start of history |
| `protect_last_n` | `4` | Recent messages protected from compression |
| `max_passes` | `3` | Maximum compression passes before failing loud |
| `summary_max_chars` | `4000` | Maximum characters retained in stored compaction summaries |
| `source_max_chars` | `50000` | Safety cap for transcript text passed to the summarizer |
| `timeout_secs` | `60` | Timeout for the summarization provider call |
| `identifier_policy` | `strict` | Identifier preservation policy |
| `tool_result_retrim_chars` | `2000` | Maximum characters retained for older tool results during fast trim |
| `live_tool_result_max_chars` | `12000` | Maximum characters retained for live tool results before content-aware compression |
| `input_max_chars` | `24000` | Maximum characters retained for a single large user input before content-aware compression |
| `compact_tool_schemas` | `true` | Shorten native tool descriptions and JSON-schema metadata before each LLM call |
| `compact_system_tool_docs` | `true` | Render compact tool docs in the system prompt when native tool schemas are sent separately |
| `tool_description_max_chars` | `180` | Maximum characters retained for each tool description after schema compaction |
| `schema_description_max_chars` | `120` | Maximum characters retained for each JSON-schema `description` after schema compaction |
| `terse_internal_outputs` | `true` | Enable concise output contracts for internal operator/agent handoffs |
| `tool_result_trim_exempt` | `[]` | Tool names exempt from tool-result trimming |

Construct applies the same content-aware compression layer on four token-heavy axes:
large pasted input, CLI/shell output, general tool output, and code-search output.
The implementation is deterministic and zero-LLM: JSON is reduced to schema and
samples, diffs to file/hunk/change summaries, logs to failure lines plus tail, and
search output to grouped file hits. For semantic code search, the
`semantic_code_search` tool uses Semble when installed and falls back to bounded
local ripgrep results when Semble is unavailable. If neither Semble nor ripgrep
is on `PATH`, it still performs a bounded built-in literal scan so code search
remains available in zero-install environments.
The operator MCP side also compresses oversized agent `last_message` fields in
wait results while preserving the JSON schema; override that budget with
`CONSTRUCT_AGENT_RESULT_MAX_CHARS`.
Base context is reduced separately: native tool-call schemas are compacted
before provider calls, and system prompts omit duplicated parameter schemas
when those schemas are already supplied through the provider's tool interface.
Internal operator/sub-agent prompts use a terse handoff contract; set
`CONSTRUCT_TERSE_INTERNAL_OUTPUTS=0` to disable the Python operator side.
Workflow step skills use compact manifests by default when multiple skill refs
are assigned, avoiding full `SKILL.md` bodies in every agent prompt. Skill
manifests keep the `kref` pointer, resolved markdown path when available, and a
hydrate instruction so the agent can call `memory_resolve_kref` or read the
resolved file only when the compact manifest is insufficient. Use
`CONSTRUCT_WORKFLOW_SKILL_MAX_CHARS` to adjust the per-skill manifest budget,
`CONSTRUCT_WORKFLOW_SKILL_CONTEXT_MODE=pointer` to send only krefs/paths, or
`CONSTRUCT_WORKFLOW_SKILL_CONTEXT_MODE=full` to restore legacy full-inline
skill context.

### `tool_filter_groups`

Reduces per-turn token overhead by limiting which MCP tool schemas are sent to the LLM on each turn. Built-in (non-MCP) tools always pass through unchanged.

Each entry is a table with:

| Field | Type | Purpose |
|---|---|---|
| `mode` | `"always"` \| `"dynamic"` | `always`: tool is included unconditionally. `dynamic`: tool is included only when the user message contains a keyword. |
| `tools` | `[string]` | Tool name patterns. Single `*` wildcard supported (prefix/suffix/infix), e.g. `"mcp_vikunja_*"`. |
| `keywords` | `[string]` | (Dynamic only) Case-insensitive substrings matched against the last user message. |

When `tool_filter_groups` is empty the feature is inactive and all tools pass through (backward-compatible default).

Example:

```toml
[agent]
# Vikunja task-management MCP tools are always available.
[[agent.tool_filter_groups]]
mode = "always"
tools = ["mcp_vikunja_*"]

# Browser MCP tools are only included when the user message mentions browsing.
[[agent.tool_filter_groups]]
mode = "dynamic"
tools = ["mcp_browser_*"]
keywords = ["browse", "navigate", "open url", "screenshot"]
```

## `[pacing]`

Pacing controls for slow/local LLM workloads (Ollama, llama.cpp, vLLM). All keys are optional; when absent, existing behavior is preserved.

| Key | Default | Purpose |
|---|---|---|
| `step_timeout_secs` | _none_ | Per-step timeout: maximum seconds for a single LLM inference turn. Catches a truly hung model without terminating the overall task loop |
| `loop_detection_min_elapsed_secs` | _none_ | Minimum elapsed seconds before loop detection activates. Tasks completing under this threshold get aggressive loop protection; longer-running tasks receive a grace period |
| `loop_ignore_tools` | `[]` | Tool names excluded from identical-output loop detection. Useful for browser workflows where `browser_screenshot` structurally resembles a loop |
| `message_timeout_scale_max` | `4` | Override for the hardcoded timeout scaling cap. The channel message timeout budget is `message_timeout_secs * min(max_tool_iterations, message_timeout_scale_max)` |

Notes:

- These settings are intended for local/slow LLM deployments. Cloud-provider users typically do not need them.
- `step_timeout_secs` operates independently of the total channel message timeout budget. A step timeout abort does not consume the overall budget; the loop simply stops.
- `loop_detection_min_elapsed_secs` delays loop-detection counting, not the task itself. Loop protection remains fully active for short tasks (the default).
- `loop_ignore_tools` only suppresses tool-output-based loop detection for the listed tools. Other safety features (max iterations, overall timeout) remain active.
- `message_timeout_scale_max` must be >= 1. Setting it higher than `max_tool_iterations` has no additional effect (the formula uses `min()`).
- Example configuration for a slow local Ollama deployment:

```toml
[pacing]
step_timeout_secs = 120
loop_detection_min_elapsed_secs = 60
loop_ignore_tools = ["browser_screenshot", "browser_navigate"]
message_timeout_scale_max = 8
```

## `[security.otp]`

| Key | Default | Purpose |
|---|---|---|
| `enabled` | `false` | Enable OTP gating for sensitive actions/domains |
| `method` | `totp` | OTP method (`totp`, `pairing`, `cli-prompt`) |
| `token_ttl_secs` | `30` | TOTP time-step window in seconds |
| `cache_valid_secs` | `300` | Cache window for recently validated OTP codes |
| `gated_actions` | `["shell","file_write","browser_open","browser"]` | Tool actions protected by OTP |
| `gated_domains` | `[]` | Explicit domain patterns requiring OTP (`*.example.com`, `login.example.com`) |
| `gated_domain_categories` | `[]` | Domain preset categories (`banking`, `medical`, `government`, `identity_providers`) |

Notes:

- Domain patterns support wildcard `*`.
- Category presets expand to curated domain sets during validation.
- Invalid domain globs or unknown categories fail fast at startup.
- When `enabled = true` and no OTP secret exists, Construct generates one and prints an enrollment URI once.

Example:

```toml
[security.otp]
enabled = true
method = "totp"
token_ttl_secs = 30
cache_valid_secs = 300
gated_actions = ["shell", "browser_open"]
gated_domains = ["*.chase.com", "accounts.google.com"]
gated_domain_categories = ["banking"]
```

## `[security.estop]`

| Key | Default | Purpose |
|---|---|---|
| `enabled` | `false` | Enable emergency-stop state machine and CLI |
| `state_file` | `~/.construct/estop-state.json` | Persistent estop state path |
| `require_otp_to_resume` | `true` | Require OTP validation before resume operations |

Notes:

- Estop state is persisted atomically and reloaded on startup.
- Corrupted/unreadable estop state falls back to fail-closed `kill_all`.
- Use CLI command `construct estop` to engage and `construct estop resume` to clear levels.

## `[agents.<name>]`

Delegate sub-agent configurations. Each key under `[agents]` defines a named sub-agent that the primary agent can delegate to.

| Key | Default | Purpose |
|---|---|---|
| `provider` | _required_ | Provider name (e.g. `"ollama"`, `"openrouter"`, `"anthropic"`) |
| `model` | _required_ | Model name for the sub-agent |
| `system_prompt` | unset | Optional system prompt override for the sub-agent |
| `api_key` | unset | Optional API key override (stored encrypted when `secrets.encrypt = true`) |
| `temperature` | unset | Temperature override for the sub-agent |
| `max_depth` | `3` | Max recursion depth for nested delegation |
| `agentic` | `false` | Enable multi-turn tool-call loop mode for the sub-agent |
| `allowed_tools` | `[]` | Tool allowlist for agentic mode |
| `max_iterations` | `10` | Max tool-call iterations for agentic mode |
| `timeout_secs` | `120` | Timeout in seconds for non-agentic provider calls (1–3600) |
| `agentic_timeout_secs` | `300` | Timeout in seconds for agentic sub-agent loops (1–3600) |
| `skills_directory` | unset | Optional skills directory path (workspace-relative) for scoped skill loading |

Notes:

- `agentic = false` preserves existing single prompt→response delegate behavior.
- `agentic = true` requires at least one matching entry in `allowed_tools`.
- The `delegate` tool is excluded from sub-agent allowlists to prevent re-entrant delegation loops.
- Sub-agents receive an enriched system prompt containing: tools section (allowed tools with parameters), skills section (from scoped or default directory), workspace path, current date/time, safety constraints, and shell policy when `shell` is in the effective tool list.
- When `skills_directory` is unset or empty, the sub-agent loads skills from the default workspace `skills/` directory. When set, skills are loaded exclusively from that directory (relative to workspace root), enabling per-agent scoped skill sets.

```toml
[agents.researcher]
provider = "openrouter"
model = "anthropic/claude-sonnet-4-6"
system_prompt = "You are a research assistant."
max_depth = 2
agentic = true
allowed_tools = ["web_search", "http_request", "file_read"]
max_iterations = 8
agentic_timeout_secs = 600

[agents.coder]
provider = "ollama"
model = "qwen2.5-coder:32b"
temperature = 0.2
timeout_secs = 60

[agents.code_reviewer]
provider = "anthropic"
model = "claude-opus-4-5"
system_prompt = "You are an expert code reviewer focused on security and performance."
agentic = true
allowed_tools = ["file_read", "shell"]
skills_directory = "skills/code-review"
```

## `[runtime]`

| Key | Default | Purpose |
|---|---|---|
| `reasoning_enabled` | unset (`None`) | Global reasoning/thinking override for providers that support explicit controls |

Notes:

- `reasoning_enabled = false` explicitly disables provider-side reasoning for supported providers (currently `ollama`, via request field `think: false`).
- `reasoning_enabled = true` explicitly requests reasoning for supported providers (`think: true` on `ollama`).
- Unset keeps provider defaults.

## `[skills]`

| Key | Default | Purpose |
|---|---|---|
| `open_skills_enabled` | `false` | Opt-in loading/sync of community `open-skills` repository |
| `open_skills_dir` | unset | Optional local path for `open-skills` (defaults to `$HOME/open-skills` when enabled) |
| `prompt_injection_mode` | `full` | Skill prompt verbosity: `full` (inline instructions/tools) or `compact` (name/description/location only) |

Notes:

- Security-first default: Construct does **not** clone or sync `open-skills` unless `open_skills_enabled = true`.
- Environment overrides:
  - `CONSTRUCT_OPEN_SKILLS_ENABLED` accepts `1/0`, `true/false`, `yes/no`, `on/off`.
  - `CONSTRUCT_OPEN_SKILLS_DIR` overrides the repository path when non-empty.
  - `CONSTRUCT_SKILLS_PROMPT_MODE` accepts `full` or `compact`.
- Precedence for enable flag: `CONSTRUCT_OPEN_SKILLS_ENABLED` → `skills.open_skills_enabled` in `config.toml` → default `false`.
- `prompt_injection_mode = "compact"` is recommended on low-context local models to reduce startup prompt size while keeping skill files available on demand.
- Skill loading and `construct skills install` both apply a static security audit. Skills that contain symlinks, script-like files, high-risk shell payload snippets, or unsafe markdown link traversal are rejected.

## `[composio]`

| Key | Default | Purpose |
|---|---|---|
| `enabled` | `false` | Enable Composio managed OAuth tools |
| `api_key` | unset | Composio API key used by the `composio` tool |
| `entity_id` | `default` | Default `user_id` sent on connect/execute calls |

Notes:

- Backward compatibility: legacy `enable = true` is accepted as an alias for `enabled = true`.
- If `enabled = false` or `api_key` is missing, the `composio` tool is not registered.
- Construct requests Composio v3 tools with `toolkit_versions=latest` and executes tools with `version="latest"` to avoid stale default tool revisions.
- Typical flow: call `connect`, complete browser OAuth, then run `execute` for the desired tool action.
- If Composio returns a missing connected-account reference error, call `list_accounts` (optionally with `app`) and pass the returned `connected_account_id` to `execute`.

## `[cost]`

| Key | Default | Purpose |
|---|---|---|
| `enabled` | `false` | Enable cost tracking |
| `daily_limit_usd` | `10.00` | Daily spending limit in USD |
| `monthly_limit_usd` | `100.00` | Monthly spending limit in USD |
| `warn_at_percent` | `80` | Warn when spending reaches this percentage of limit |
| `allow_override` | `false` | Allow requests to exceed budget with `--override` flag |

Notes:

- When `enabled = true`, the runtime tracks per-request cost estimates and enforces daily/monthly limits.
- At `warn_at_percent` threshold, a warning is emitted but requests continue.
- When a limit is reached, requests are rejected unless `allow_override = true` and the `--override` flag is passed.

## `[identity]`

| Key | Default | Purpose |
|---|---|---|
| `format` | `openclaw` | Identity format: `"openclaw"` (default) or `"aieos"` |
| `aieos_path` | unset | Path to AIEOS JSON file (relative to workspace) |
| `aieos_inline` | unset | Inline AIEOS JSON (alternative to file path) |

Notes:

- Use `format = "aieos"` with either `aieos_path` or `aieos_inline` to load an AIEOS / OpenClaw identity document.
- Only one of `aieos_path` or `aieos_inline` should be set; `aieos_path` takes precedence.

## `[multimodal]`

| Key | Default | Purpose |
|---|---|---|
| `max_images` | `4` | Maximum image markers accepted per request |
| `max_image_size_mb` | `5` | Per-image size limit before base64 encoding |
| `allow_remote_fetch` | `false` | Allow fetching `http(s)` image URLs from markers |

Notes:

- Runtime accepts image markers in user messages with syntax: ``[IMAGE:<source>]``.
- Supported sources:
  - Local file path (for example ``[IMAGE:/tmp/screenshot.png]``)
- Data URI (for example ``[IMAGE:data:image/png;base64,...]``)
- Remote URL only when `allow_remote_fetch = true`
- Allowed MIME types: `image/png`, `image/jpeg`, `image/webp`, `image/gif`, `image/bmp`.
- When the active provider does not support vision, requests fail with a structured capability error (`capability=vision`) instead of silently dropping images.

## `[browser]`

| Key | Default | Purpose |
|---|---|---|
| `enabled` | `false` | Enable `browser_open` tool (opens URLs in the system browser without scraping) |
| `allowed_domains` | `[]` | Allowed domains for `browser_open` (exact/subdomain match, or `"*"` for all public domains) |
| `session_name` | unset | Browser session name (for agent-browser automation) |
| `backend` | `agent_browser` | Browser automation backend: `"agent_browser"`, `"rust_native"`, `"computer_use"`, or `"auto"` |
| `native_headless` | `true` | Headless mode for rust-native backend |
| `native_webdriver_url` | `http://127.0.0.1:9515` | WebDriver endpoint URL for rust-native backend |
| `native_chrome_path` | unset | Optional Chrome/Chromium executable path for rust-native backend |

### `[browser.computer_use]`

| Key | Default | Purpose |
|---|---|---|
| `endpoint` | `http://127.0.0.1:8787/v1/actions` | Sidecar endpoint for computer-use actions (OS-level mouse/keyboard/screenshot) |
| `api_key` | unset | Optional bearer token for computer-use sidecar (stored encrypted) |
| `timeout_ms` | `15000` | Per-action request timeout in milliseconds |
| `allow_remote_endpoint` | `false` | Allow remote/public endpoint for computer-use sidecar |
| `window_allowlist` | `[]` | Optional window title/process allowlist forwarded to sidecar policy |
| `max_coordinate_x` | unset | Optional X-axis boundary for coordinate-based actions |
| `max_coordinate_y` | unset | Optional Y-axis boundary for coordinate-based actions |

Notes:

- When `backend = "computer_use"`, the agent delegates browser actions to the sidecar at `computer_use.endpoint`.
- `allow_remote_endpoint = false` (default) rejects any non-loopback endpoint to prevent accidental public exposure.
- Use `window_allowlist` to restrict which OS windows the sidecar can interact with.

## `[http_request]`

| Key | Default | Purpose |
|---|---|---|
| `enabled` | `false` | Enable `http_request` tool for API interactions |
| `allowed_domains` | `[]` | Allowed domains for HTTP requests (exact/subdomain match, or `"*"` for all public domains) |
| `max_response_size` | `1000000` | Maximum response size in bytes (default: 1 MB) |
| `timeout_secs` | `30` | Request timeout in seconds |

Notes:

- Deny-by-default: if `allowed_domains` is empty, all HTTP requests are rejected.
- Use exact domain or subdomain matching (e.g. `"api.example.com"`, `"example.com"`), or `"*"` to allow any public domain.
- Local/private targets are still blocked even when `"*"` is configured.

## `[google_workspace]`

| Key | Default | Purpose |
|---|---|---|
| `enabled` | `false` | Enable the `google_workspace` tool |
| `credentials_path` | unset | Path to Google service account or OAuth credentials JSON |
| `default_account` | unset | Default Google account passed as `--account` to `gws` |
| `allowed_services` | (built-in list) | Services the agent may access: `drive`, `gmail`, `calendar`, `sheets`, `docs`, `slides`, `tasks`, `people`, `chat`, `classroom`, `forms`, `keep`, `meet`, `events` |
| `rate_limit_per_minute` | `60` | Maximum `gws` calls per minute |
| `timeout_secs` | `30` | Per-call execution timeout before kill |
| `audit_log` | `false` | Emit an `INFO` log line for every `gws` call |

### `[[google_workspace.allowed_operations]]`

When this array is non-empty, only exact matches pass. An entry matches a call when
`service`, `resource`, `sub_resource`, and `method` all agree. When the array is
empty (the default), all combinations within `allowed_services` are available.

| Key | Required | Purpose |
|---|---|---|
| `service` | yes | Service identifier (must match an entry in `allowed_services`) |
| `resource` | yes | Top-level resource name (`users` for Gmail, `files` for Drive, `events` for Calendar) |
| `sub_resource` | no | Sub-resource for 4-segment gws commands. Gmail operations use `gws gmail users <sub_resource> <method>`, so Gmail entries need `sub_resource` to match at runtime. Drive, Calendar, and most other services use 3-segment commands and omit it. |
| `methods` | yes | One or more method names allowed on that resource/sub_resource |

Gmail uses `gws gmail users <sub_resource> <method>` for all operations. A Gmail
entry without `sub_resource` will never match at runtime. Drive and Calendar use
3-segment commands and omit `sub_resource`.

```toml
[google_workspace]
enabled = true
default_account = "owner@company.com"
allowed_services = ["gmail"]
audit_log = true

[[google_workspace.allowed_operations]]
service = "gmail"
resource = "users"
sub_resource = "messages"
methods = ["list", "get"]

[[google_workspace.allowed_operations]]
service = "gmail"
resource = "users"
sub_resource = "drafts"
methods = ["list", "get", "create", "update"]
```

Notes:

- Requires `gws` to be installed and authenticated (`gws auth login`). Install: `npm install -g @googleworkspace/cli`.
- `credentials_path` sets `GOOGLE_APPLICATION_CREDENTIALS` before each call.
- `allowed_services` defaults to the built-in list if omitted or empty.
- Validation rejects duplicate `(service, resource)` pairs and duplicate methods within a single entry.
- See `docs/superpowers/specs/2026-03-19-google-workspace-operation-allowlist.md` for the full policy model and verified workflow examples.

## `[gateway]`

| Key | Default | Purpose |
|---|---|---|
| `host` | `127.0.0.1` | bind address |
| `port` | `42617` | gateway listen port |
| `require_pairing` | `true` | require pairing before bearer auth |
| `allow_public_bind` | `false` | block accidental public exposure |
| `path_prefix` | _(none)_ | URL path prefix for reverse-proxy deployments (e.g. `"/construct"`) |
| `web_root` | _(none)_ | optional filesystem `web/dist` root for gateway-served dashboard assets |

When deploying behind a reverse proxy that maps Construct to a sub-path,
set `path_prefix` to that sub-path (e.g. `"/construct"`). All gateway
routes will be served under this prefix. The value must start with `/`
and must not end with `/`.

Dashboard static asset resolution uses this order:

1. `CONSTRUCT_WEB_ROOT`, when set and non-empty
2. `gateway.web_root`, when set
3. embedded `web/dist` in the binary
4. dashboard unavailable response

`CONSTRUCT_BUILD_WEB=1` opts back into the legacy `build.rs` behavior that
attempts `npm ci` / `npm run build` during a Rust build. By default, Rust
builds do not require Node.js.

## `[tunnel]`

Optional public tunnel for the gateway. Construct ships built-in
adapters that wrap external tunnel binaries — they spawn the binary
as a managed subprocess once the gateway is listening, watch its
output for the public URL, and stop it on daemon shutdown.

| Key | Default | Purpose |
|---|---|---|
| `provider` | `"none"` | Tunnel provider: `"none"`, `"cloudflare"`, `"tailscale"`, `"ngrok"`, `"openvpn"`, `"pinggy"`, or `"custom"`. Case-insensitive. |

When `provider != "none"`, the matching `[tunnel.<provider>]` sub-section
must be present (validated at startup; the daemon refuses to come up
if the provider's required fields are missing).

If you already run a tunnel binary externally (e.g. `cloudflared` under
`launchd` or `systemd`) keep `provider = "none"` so Construct does not
spawn a duplicate.

### `[tunnel.cloudflare]`

Required when `provider = "cloudflare"`. Construct runs
`cloudflared tunnel --no-autoupdate run --token <TOKEN> --url
http://localhost:<port>` and parses the public URL from the binary's
stderr.

| Key | Default | Purpose |
|---|---|---|
| `token` | _(required)_ | Cloudflare Tunnel token from the Zero Trust dashboard |

```toml
[tunnel]
provider = "cloudflare"

[tunnel.cloudflare]
token = "eyJhIjoiMTI..."
```

### `[tunnel.tailscale]`

Optional when `provider = "tailscale"`. Defaults to Tailscale Serve
(tailnet-only); set `funnel = true` for public Tailscale Funnel.

| Key | Default | Purpose |
|---|---|---|
| `funnel` | `false` | `true` for public Funnel; `false` for tailnet-only Serve |
| `hostname` | _(none)_ | Optional hostname override |

### `[tunnel.ngrok]`

Required when `provider = "ngrok"`.

| Key | Default | Purpose |
|---|---|---|
| `auth_token` | _(required)_ | ngrok account auth token |
| `domain` | _(none)_ | Optional reserved custom domain |

### `[tunnel.openvpn]`

Required when `provider = "openvpn"`.

| Key | Default | Purpose |
|---|---|---|
| `config_file` | _(required)_ | Path to `.ovpn` configuration file |
| `auth_file` | _(none)_ | Optional path to `--auth-user-pass` credentials file |
| `advertise_address` | _(none)_ | Address advertised once VPN is up (e.g. `"10.8.0.2:42617"`); falls back to `local_host:local_port` if omitted |
| `connect_timeout_secs` | `30` | Connection timeout (must be > 0) |

### `[tunnel.pinggy]`

Required when `provider = "pinggy"`.

| Key | Default | Purpose |
|---|---|---|
| `token` | _(required)_ | Pinggy access token |
| `region` | _(none)_ | Optional region override |

### `[tunnel.custom]`

Required when `provider = "custom"` — bring-your-own tunnel binary.

| Key | Default | Purpose |
|---|---|---|
| `start_command` | _(required)_ | Shell command Construct runs to launch the tunnel; supports `{host}` and `{port}` placeholders |
| `health_url` | _(none)_ | Optional URL Construct probes to confirm the tunnel is up |
| `url_pattern` | _(none)_ | Regex extracted from the binary's stderr to pull the public URL (capture group 1 is the URL) |

## `[autonomy]`

| Key | Default | Purpose |
|---|---|---|
| `level` | `supervised` | `read_only`, `supervised`, or `full` |
| `workspace_only` | `true` | reject absolute path inputs unless explicitly disabled |
| `allowed_commands` | _required for shell execution_ | allowlist of executable names, explicit executable paths, or `"*"` |
| `forbidden_paths` | built-in protected list | explicit path denylist (system paths + sensitive dotdirs by default) |
| `allowed_roots` | `["~/.construct/workflows"]` | additional roots allowed outside workspace after canonicalization |
| `max_actions_per_hour` | `20` | per-policy action budget |
| `max_cost_per_day_cents` | `500` | per-policy spend guardrail |
| `require_approval_for_medium_risk` | `true` | approval gate for medium-risk commands (see [Command Risk Classification](../../security/command-risk-classification.md)) |
| `block_high_risk_commands` | `true` | hard block for high-risk commands (see [Command Risk Classification](../../security/command-risk-classification.md)) |
| `auto_approve` | `[]` | tool operations always auto-approved |
| `always_ask` | `[]` | tool operations that always require approval |

Notes:

- `level = "full"` skips medium-risk approval gating for shell execution, while still enforcing configured guardrails.
- Access outside the workspace requires `allowed_roots`, even when `workspace_only = false`.
- `allowed_roots` supports absolute paths, `~/...`, and workspace-relative paths.
- `allowed_commands` entries can be command names (for example, `"git"`), explicit executable paths (for example, `"/usr/bin/antigravity"`), or `"*"` to allow any command name/path (risk gates still apply).
- Shell separator/operator parsing is quote-aware. Characters like `;` inside quoted arguments are treated as literals, not command separators.
- Unquoted shell chaining/operators are still enforced by policy checks (`;`, `|`, `&&`, `||`, background chaining, and redirects).

```toml
[autonomy]
workspace_only = false
forbidden_paths = ["/etc", "/root", "/proc", "/sys", "~/.ssh", "~/.gnupg", "~/.aws"]
allowed_roots = ["~/Desktop/projects", "/opt/shared-repo"]
```

## `[memory]`

| Key | Default | Purpose |
|---|---|---|
| `backend` | `sqlite` | `sqlite`, `lucid`, `markdown`, `none` |
| `auto_save` | `true` | persist user-stated inputs only (assistant outputs are excluded) |
| `embedding_provider` | `none` | `none`, `openai`, or custom endpoint |
| `embedding_model` | `text-embedding-3-small` | embedding model ID, or `hint:<name>` route |
| `embedding_dimensions` | `1536` | expected vector size for selected embedding model |
| `vector_weight` | `0.7` | hybrid ranking vector weight |
| `keyword_weight` | `0.3` | hybrid ranking keyword weight |

Notes:

- Memory context injection ignores legacy `assistant_resp*` auto-save keys to prevent old model-authored summaries from being treated as facts.

## `[[model_routes]]` and `[[embedding_routes]]`

Use route hints so integrations can keep stable names while model IDs evolve.

### `[[model_routes]]`

| Key | Default | Purpose |
|---|---|---|
| `hint` | _required_ | Task hint name (e.g. `"reasoning"`, `"fast"`, `"code"`, `"summarize"`) |
| `provider` | _required_ | Provider to route to (must match a known provider name) |
| `model` | _required_ | Model to use with that provider |
| `api_key` | unset | Optional API key override for this route's provider |

### `[[embedding_routes]]`

| Key | Default | Purpose |
|---|---|---|
| `hint` | _required_ | Route hint name (e.g. `"semantic"`, `"archive"`, `"faq"`) |
| `provider` | _required_ | Embedding provider (`"none"`, `"openai"`, or `"custom:<url>"`) |
| `model` | _required_ | Embedding model to use with that provider |
| `dimensions` | unset | Optional embedding dimension override for this route |
| `api_key` | unset | Optional API key override for this route's provider |

```toml
[memory]
embedding_model = "hint:semantic"

[[model_routes]]
hint = "reasoning"
provider = "openrouter"
model = "provider/model-id"

[[embedding_routes]]
hint = "semantic"
provider = "openai"
model = "text-embedding-3-small"
dimensions = 1536
```

Upgrade strategy:

1. Keep hints stable (`hint:reasoning`, `hint:semantic`).
2. Update only `model = "...new-version..."` in the route entries.
3. Validate with `construct doctor` before restart/rollout.

Natural-language config path:

- During normal agent chat, ask the assistant to rewire routes in plain language.
- The runtime can persist these updates via tool `model_routing_config` (defaults, scenarios, and delegate sub-agents) without manual TOML editing.

Example requests:

- `Set conversation to provider kimi, model moonshot-v1-8k.`
- `Set coding to provider openai, model gpt-5.3-codex, and auto-route when message contains code blocks.`
- `Create a coder sub-agent using openai/gpt-5.3-codex with tools file_read,file_write,shell.`

## `[query_classification]`

Automatic model hint routing — maps user messages to `[[model_routes]]` hints based on content patterns.

| Key | Default | Purpose |
|---|---|---|
| `enabled` | `false` | Enable automatic query classification |
| `rules` | `[]` | Classification rules (evaluated in priority order) |

Each rule in `rules`:

| Key | Default | Purpose |
|---|---|---|
| `hint` | _required_ | Must match a `[[model_routes]]` hint value |
| `keywords` | `[]` | Case-insensitive substring matches |
| `patterns` | `[]` | Case-sensitive literal matches (for code fences, keywords like `"fn "`) |
| `min_length` | unset | Only match if message length ≥ N chars |
| `max_length` | unset | Only match if message length ≤ N chars |
| `priority` | `0` | Higher priority rules are checked first |

```toml
[query_classification]
enabled = true

[[query_classification.rules]]
hint = "reasoning"
keywords = ["explain", "analyze", "why"]
min_length = 200
priority = 10

[[query_classification.rules]]
hint = "fast"
keywords = ["hi", "hello", "thanks"]
max_length = 50
priority = 5
```

## `[channels_config]`

Top-level channel options are configured under `channels_config`.

| Key | Default | Purpose |
|---|---|---|
| `message_timeout_secs` | `300` | Base timeout in seconds for channel message processing; runtime scales this with tool-loop depth (up to 4x, overridable via `[pacing].message_timeout_scale_max`) |

Examples:

- `[channels_config.telegram]`
- `[channels_config.discord]`
- `[channels_config.whatsapp]`
- `[channels_config.linq]`
- `[channels_config.nextcloud_talk]`
- `[channels_config.email]`
- `[channels_config.nostr]`

Notes:

- Default `300s` is optimized for on-device LLMs (Ollama) which are slower than cloud APIs.
- Runtime timeout budget is `message_timeout_secs * scale`, where `scale = min(max_tool_iterations, cap)` and a minimum of `1`. The default cap is `4`; override with `[pacing].message_timeout_scale_max`.
- This scaling avoids false timeouts when the first LLM turn is slow/retried but later tool-loop turns still need to complete.
- If using cloud APIs (OpenAI, Anthropic, etc.), you can reduce this to `60` or lower.
- Values below `30` are clamped to `30` to avoid immediate timeout churn.
- When a timeout occurs, users receive: `⚠️ Request timed out while waiting for the model. Please try again.`
- Telegram-only interruption behavior is controlled with `channels_config.telegram.interrupt_on_new_message` (default `false`).
  When enabled, a newer message from the same sender in the same chat cancels the in-flight request and preserves interrupted user context.
- While `construct channel start` is running, updates to `default_provider`, `default_model`, `default_temperature`, `api_key`, `api_url`, and `reliability.*` are hot-applied from `config.toml` on the next inbound message.

### `[channels_config.nostr]`

| Key | Default | Purpose |
|---|---|---|
| `private_key` | _required_ | Nostr private key (hex or `nsec1…` bech32); encrypted at rest when `secrets.encrypt = true` |
| `relays` | see note | List of relay WebSocket URLs; defaults to `relay.damus.io`, `nos.lol`, `relay.primal.net`, `relay.snort.social` |
| `allowed_pubkeys` | `[]` (deny all) | Sender allowlist (hex or `npub1…`); use `"*"` to allow all senders |

Notes:

- Supports both NIP-04 (legacy encrypted DMs) and NIP-17 (gift-wrapped private messages). Replies mirror the sender's protocol automatically.
- The `private_key` is a high-value secret; keep `secrets.encrypt = true` (the default) in production.

See detailed channel matrix and allowlist behavior in [channels-reference.md](channels-reference.md).

### `[channels_config.whatsapp]`

WhatsApp supports two backends under one config table.

Cloud API mode (Meta webhook):

| Key | Required | Purpose |
|---|---|---|
| `access_token` | Yes | Meta Cloud API bearer token |
| `phone_number_id` | Yes | Meta phone number ID |
| `verify_token` | Yes | Webhook verification token |
| `app_secret` | Optional | Enables webhook signature verification (`X-Hub-Signature-256`) |
| `allowed_numbers` | Recommended | Allowed inbound numbers (`[]` = deny all, `"*"` = allow all) |

WhatsApp Web mode (native client):

| Key | Required | Purpose |
|---|---|---|
| `session_path` | Yes | Persistent SQLite session path |
| `pair_phone` | Optional | Pair-code flow phone number (digits only) |
| `pair_code` | Optional | Custom pair code (otherwise auto-generated) |
| `allowed_numbers` | Recommended | Allowed inbound numbers (`[]` = deny all, `"*"` = allow all) |

Notes:

- WhatsApp Web requires build flag `whatsapp-web`.
- If both Cloud and Web fields are present, Cloud mode wins for backward compatibility.

### `[channels_config.linq]`

Linq Partner V3 API integration for iMessage, RCS, and SMS.

| Key | Required | Purpose |
|---|---|---|
| `api_token` | Yes | Linq Partner API bearer token |
| `from_phone` | Yes | Phone number to send from (E.164 format) |
| `signing_secret` | Optional | Webhook signing secret for HMAC-SHA256 signature verification |
| `allowed_senders` | Recommended | Allowed inbound phone numbers (`[]` = deny all, `"*"` = allow all) |

Notes:

- Webhook endpoint is `POST /linq`.
- `CONSTRUCT_LINQ_SIGNING_SECRET` overrides `signing_secret` when set.
- Signatures use `X-Webhook-Signature` and `X-Webhook-Timestamp` headers; stale timestamps (>300s) are rejected.
- See [channels-reference.md](channels-reference.md) for full config examples.

### `[channels_config.nextcloud_talk]`

Native Nextcloud Talk bot integration (webhook receive + OCS send API).

| Key | Required | Purpose |
|---|---|---|
| `base_url` | Yes | Nextcloud base URL (e.g. `https://cloud.example.com`) |
| `app_token` | Yes | Bot app token used for OCS bearer auth |
| `webhook_secret` | Optional | Enables webhook signature verification |
| `allowed_users` | Recommended | Allowed Nextcloud actor IDs (`[]` = deny all, `"*"` = allow all) |
| `bot_name` | Optional | Display name of the bot in Nextcloud Talk (e.g. `"construct"`). Used to filter out the bot's own messages and prevent feedback loops. |

Notes:

- Webhook endpoint is `POST /nextcloud-talk`.
- `CONSTRUCT_NEXTCLOUD_TALK_WEBHOOK_SECRET` overrides `webhook_secret` when set.
- See [nextcloud-talk-setup.md](../../setup-guides/nextcloud-talk-setup.md) for setup and troubleshooting.

## `[hardware]`

Hardware wizard configuration for physical-world access (STM32, probe, serial).

| Key | Default | Purpose |
|---|---|---|
| `enabled` | `false` | Whether hardware access is enabled |
| `transport` | `none` | Transport mode: `"none"`, `"native"`, `"serial"`, or `"probe"` |
| `serial_port` | unset | Serial port path (e.g. `"/dev/ttyACM0"`) |
| `baud_rate` | `115200` | Serial baud rate |
| `probe_target` | unset | Probe target chip (e.g. `"STM32F401RE"`) |
| `workspace_datasheets` | `false` | Enable workspace datasheet RAG (index PDF schematics for AI pin lookups) |

Notes:

- Use `transport = "serial"` with `serial_port` for USB-serial connections.
- Use `transport = "probe"` with `probe_target` for debug-probe flashing (e.g. ST-Link).
- See [hardware-peripherals-design.md](../../hardware/hardware-peripherals-design.md) for protocol details.

## `[peripherals]`

Higher-level peripheral board configuration. Boards become agent tools when enabled.

| Key | Default | Purpose |
|---|---|---|
| `enabled` | `false` | Enable peripheral support (boards become agent tools) |
| `boards` | `[]` | Board configurations |
| `datasheet_dir` | unset | Path to datasheet docs (relative to workspace) for RAG retrieval |

Each entry in `boards`:

| Key | Default | Purpose |
|---|---|---|
| `board` | _required_ | Board type: `"nucleo-f401re"`, `"rpi-gpio"`, `"esp32"`, etc. |
| `transport` | `serial` | Transport: `"serial"`, `"native"`, `"websocket"` |
| `path` | unset | Path for serial: `"/dev/ttyACM0"`, `"/dev/ttyUSB0"` |
| `baud` | `115200` | Baud rate for serial |

```toml
[peripherals]
enabled = true
datasheet_dir = "docs/datasheets"

[[peripherals.boards]]
board = "nucleo-f401re"
transport = "serial"
path = "/dev/ttyACM0"
baud = 115200

[[peripherals.boards]]
board = "rpi-gpio"
transport = "native"
```

Notes:

- Place `.md`/`.txt` datasheet files named by board (e.g. `nucleo-f401re.md`, `rpi-gpio.md`) in `datasheet_dir` for RAG retrieval.
- See [hardware-peripherals-design.md](../../hardware/hardware-peripherals-design.md) for board protocol and firmware notes.

## `[kumiho]`

Kumiho is Construct's canonical persistent graph memory backend. The runtime
automatically injects the Kumiho MCP server and the session-bootstrap system
prompt into every non-internal agent.

| Key | Default | Purpose |
|---|---|---|
| `enabled` | `true` | Enable Kumiho memory injection for non-internal agents |
| `mcp_path` | `~/.construct/kumiho/run_kumiho_mcp.py` | Absolute path to the MCP runner script |
| `space_prefix` | `Construct` | Project/space prefix used to scope memories (e.g. `Construct/AgentPool/`) |
| `api_url` | `https://api.kumiho.cloud` | Base URL for the Kumiho FastAPI REST API used by the agent-management proxy |
| `memory_project` | (default) | Project for user memories, sessions, and compactions |
| `harness_project` | (default) | Project for skills, operational data, and ClawHub installs |
| `memory_retrieval_limit` | `3` | Default maximum memories returned by Kumiho recall/engage |

Notes:

- Disable on deployments where Kumiho is not installed: `enabled = false`.
- The `api_url` remains the fallback URL for dashboard/API Kumiho traffic.
  When the installed Kumiho sidecar is available, Construct first tries the
  local Kumiho SDK bridge and only falls back to `api_url` when the bridge is
  disabled, unavailable, or does not support a route. Set
  `CONSTRUCT_KUMIHO_SDK_BRIDGE=0` to force the hosted FastAPI transport.
- `KUMIHO_AUTH_TOKEN` is preferred for the local SDK bridge; `KUMIHO_SERVICE_TOKEN`
  remains the FastAPI header token and is used as a fallback when
  `KUMIHO_AUTH_TOKEN` is not set. Response caches are keyed by the effective
  token to avoid cross-account data bleed. When the bridge creates an SDK
  client for a new token, it also forces Kumiho discovery refresh so local
  discovery cache entries from another account are not reused.
- Namespaces used by Construct under `space_prefix` include `AgentPool`,
  `Plans`, `Sessions`, `Goals`, `AgentTrust`, `ClawHub`, `Teams`, and
  `CognitiveMemory/Skills`.

## `[google_agents_cli]`

Google Agents CLI integration for ADK / Agent Platform lifecycle commands.
This enables the `google_agents_cli` tool. `agents-cli` is a lifecycle tool
for coding agents, not a Construct `agent_type` or session provider.

| Key | Default | Purpose |
|---|---|---|
| `enabled` | `false` | Enable the `google_agents_cli` tool |
| `timeout_secs` | `600` | Maximum subprocess runtime for `agents-cli` commands |
| `max_output_bytes` | `2097152` | Maximum captured stdout bytes before truncation |
| `env_passthrough` | `[]` | Extra environment variables to pass to the subprocess |

Notes:

- Install/authenticate `agents-cli` outside Construct. The tool runs the
  existing binary from `PATH`.
- Safe Google auth/project variables such as `GOOGLE_API_KEY`,
  `GEMINI_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`,
  `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, and
  `GOOGLE_GENAI_USE_VERTEXAI` are passed by default when present.
- Use a `claude` or `codex` agent when delegation is useful, and have that
  agent call `google_agents_cli` for `agents-cli run`, `eval`, `deploy`, or
  publish commands. Use `agents-cli info` to inspect project/tooling state.
  The ADK project generally owns model selection.

## `[operator]`

The Operator is a Python MCP server driving declarative YAML workflows with
typed steps and advanced orchestration patterns. It is automatically injected into
every non-internal agent.

| Key | Default | Purpose |
|---|---|---|
| `enabled` | `true` | Enable Operator injection for non-internal agents |
| `mcp_path` | `~/.construct/operator_mcp/run_operator_mcp.py` | Absolute path to the MCP runner script |
| `max_tool_iterations` | `80` | Override `agent.max_tool_iterations` for operator-enabled sessions (operator tasks are multi-step by nature) |
| `tool_timeout_secs` | `600` | Per-tool timeout for the auto-injected operator MCP server. Some operator tools are inherently slow (codex image generation, workflow execution, dry-run, bulk recall); the runtime's global default (180 s) is too tight. Capped at `600` (the runtime's `MAX_TOOL_TIMEOUT_SECS`); higher values are silently truncated. Set to `0` to fall back to the global default. |

Notes:

- Workflow checkpoints are written to `~/.construct/workflow_checkpoints/`.
- Per-agent RunLog JSONL audit trails are written to
  `~/.construct/operator_mcp/runlogs/`.
- Step types currently supported include `agent`, `shell`, `python`, `compute`,
  `email`, `image`, `output`, `notify`, `a2a`, `conditional`, `parallel`,
  `goto`, `human_approval`, `human_input`, `map_reduce`, `supervisor`,
  `group_chat`, `handoff`, `resolve`, `kumiho_context`,
  `kumiho_bundle_update`, `kumiho_patch_apply`, `for_each`, `tag`,
  `deprecate`, and `manus`.
- Agent steps may declare `agent.output_fields`. When set, the executor appends
  structured-output instructions, parses direct JSON, final fenced `json`, or
  `FINAL_OUTPUT:` YAML, and fails the step with `structured_output_missing` if
  any declared field is absent.

## `[clawhub]`

ClawHub skill/template marketplace integration.

| Key | Default | Purpose |
|---|---|---|
| `enabled` | `true` | Enable ClawHub integration |
| `api_token` | unset | ClawHub API token (`clh_…`) — required only for publishing |
| `api_url` | `https://clawhub.ai` | Base URL for the ClawHub API |

Notes:

- Anonymous browsing and installs work without a token.
- Dashboard surfaces ClawHub under the `Skills` view; REST endpoints are at
  `GET /api/clawhub/search`, `/trending`, `/skills/{slug}`, and
  `POST /api/clawhub/install/{slug}`.

## `[trust]`

Trust scoring for domains/tools (regression detection). Agent-template trust
scores are stored in Kumiho under `Construct/AgentTrust/` and are separate from
this config section.

| Key | Default | Purpose |
|---|---|---|
| `initial_score` | `0.8` | Initial trust score for new domains |
| `decay_half_life_days` | `30` | Trust decay half-life in days |
| `regression_threshold` | `0.5` | Score below which regression is flagged |
| `correction_penalty` | `0.05` | Score penalty per correction event |
| `success_boost` | `0.01` | Score boost per success event |

## `[verifiable_intent]`

Verifiable Intent (VI) credential verification for commerce tool calls.

| Key | Default | Purpose |
|---|---|---|
| `enabled` | `false` | Enable VI credential verification on commerce calls |
| `strictness` | `strict` | Constraint evaluation: `strict` (fail-closed on unknown constraint types) or `permissive` (skip with warning) |

## Security-Relevant Defaults

- deny-by-default channel allowlists (`[]` means deny all)
- pairing required on gateway by default
- public bind disabled by default
- Kumiho and Operator MCP servers are enabled by default; disable explicitly
  for deployments that do not run them

## Validation Commands

After editing config:

```bash
construct status
construct doctor
construct channel doctor
construct service restart
```

## Related Docs

- [channels-reference.md](channels-reference.md)
- [providers-reference.md](providers-reference.md)
- [operations-runbook.md](../../ops/operations-runbook.md)
- [troubleshooting.md](../../ops/troubleshooting.md)
