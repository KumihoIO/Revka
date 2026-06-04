# Coherence Audit — Phase 0-1

**Date:** 2026-05-04
**Branch:** `chore/coherence-audit-2026-05`
**Auditor:** claude/claude-opus-4-7 (xHigh thinking)
**Scope:** Revka improvement plan, Phase 0-1 (coherence audit). Read-only.

## Summary

14 rows audited across the prompt-builder pipeline, identity loader, autonomy / rate-limit defaults, Kumiho + Operator MCP injection, built-in tool registry, tool-description i18n, channel/onboarding scaffolds, tunnel / cloudflared surface, and cron scheduling.

Classification breakdown: **Phantom = 6**, **Drift = 4**, **Orphan = 4**, **Aspirational = 1** (one row is dual-classified Aspirational+Phantom — counted once as Aspirational and once as Phantom). Severity: **high = 1**, **med = 5**, **low = 8**.

The most consequential finding is **Row 7** — the entire built-in `memory_*` tool family (`memory_store`, `memory_recall`, `memory_forget`) was deleted from the tool registry (see comment at `src/tools/mod.rs:416`) but the names persist in the user-facing onboarding wizard, the default `auto_approve` list, the embedded sample `config.toml` strings, the i18n tool descriptions, and the channel non-CLI excluded-tools default. Onboarding actively documents tools that no longer exist.

The rebrand from `FoxClaw → Revka` and `conductor → Operator` (decided 2026-04-16) is essentially complete in active runtime code; only one stale comment in test fixtures remains.

The Kumiho memory contract (Row 1) is the only Aspirational row: `kumiho.enabled` in config gates both the MCP injection AND the prompt block, so the prompt is not emitted into the void from a config standpoint. However `inject_kumiho()` does **not** verify the launcher script actually exists or that the sidecar process is healthy — only the `probe_kumiho_memory_advanced()` filesystem probe exists, and only for the lite-vs-full distinction. The `kumiho.enabled=true` + sidecar-uninstalled case still injects the lite prompt mandating tools that may not load.

`BOOTSTRAP.md` (Row 2) is the cleanest example of a Phantom: the personality loader at `src/agent/personality.rs:22` lists it among well-known files, but no code creates it, no documentation describes its expected content, and grep finds no other reference anywhere in the repo. Per the 2026-05-04 decision recorded in the brief, it is to be deleted from the loader list entirely.

## Audit Table

| # | Surface | Claim location | Source of truth | Type | Severity | Remediation | Notes |
|---|---------|----------------|-----------------|------|----------|-------------|-------|
| 1 | Kumiho memory contract (engage/reflect mandates) | `src/agent/kumiho.rs:40-161` (`KUMIHO_BOOTSTRAP_PROMPT`) | `src/agent/kumiho.rs:416-488` (`inject_kumiho`); `src/agent/prompt.rs:130-150` (`KumihoBootstrapSection`) | Aspirational | med | rewrite-both | Lite/full split via filesystem probe is in place; sidecar liveness still unchecked. |
| 2 | `BOOTSTRAP.md` personality file | `src/agent/personality.rs:22` | (none — no creator, no docs) | Phantom | med | delete | Per brief 2026-05-04: remove entry from `PERSONALITY_FILES` and any rendering paths. |
| 3 | Other phantom personality files (`USER.md`, `TOOLS.md`, `HEARTBEAT.md`, `MEMORY.md`) | `src/agent/personality.rs:18, 20, 21, 23` | `src/config/schema.rs:8781-8813` (`ensure_bootstrap_files` only creates IDENTITY.md + SOUL.md); no docs describing schema | Phantom | low | rewrite-both | Same defect family as Row 2. Either define + document each, or trim the loader list. |
| 4 | First-turn instruction "Invoke the kumiho-memory:kumiho-memory skill" | `src/agent/kumiho.rs:92` | `src/agent/loop_.rs` system-prompt assembly + `inject_kumiho()`; no Revka skill-invocation mechanism | Phantom | low | config-follows-code | Kumiho MCP is auto-injected; the agent has no `Skill` tool with this signature. Vestige of the Paseo bootstrap source. |
| 5 | `max_actions_per_hour` default documented as `100` | `src/config/schema.rs:5250` | `src/config/schema.rs:5376` (`max_actions_per_hour: 20`) | Drift | med | config-follows-code | Docstring is 5× the actual default. |
| 6 | `max_cost_per_day_cents` default documented as `1000` | `src/config/schema.rs:5252` | `src/config/schema.rs:5377` (`max_cost_per_day_cents: 500`) | Drift | med | config-follows-code | Docstring is 2× the actual default. |
| 7 | Built-in `memory_*` tools (store/recall/forget) | `tool_descriptions/en.toml:38-40`; `src/config/schema.rs:5295, 7260, 11472, 11522, 15850, 15864-15865`; `src/onboard/wizard.rs:5915, 6068-6074, 7099-7118`; `src/channels/mod.rs:5356-5364, 8786-8792` | `src/tools/mod.rs:416` ("Old memory tools (store, recall, forget, export, purge) removed — use Kumiho MCP tools.") | Phantom | high | config-follows-code | Most consequential single mismatch — onboarding hands users tool names that no longer exist. |
| 8 | `FoxClaw` legacy name in test fixture | `operator-mcp/tests/conftest.py:15` | n/a (rebrand decided 2026-04-16) | Drift | low | config-follows-code | Comment-only; replace with generic phrasing or current project name. |
| 9 | MCP servers / Kumiho + Operator env wiring | `.env.example` (no `KUMIHO_*` / `REVKA_OPERATOR_*` keys) | `src/agent/kumiho.rs:312-397, 416-488`; `src/agent/operator/mod.rs:79-115, 134-196` | Orphan | low | config-follows-code | Runtime forwards 12+ env vars (`KUMIHO_AUTH_TOKEN`, `KUMIHO_SERVICE_TOKEN`, `KUMIHO_CONTROL_PLANE_URL`, `KUMIHO_LLM_*`, etc.) that are nowhere documented for operators. |
| 10 | Browser / web hardcoded gated-domain categories | `src/security/domain_matcher.rs` (`BANKING_DOMAINS`, `MEDICAL_DOMAINS`, `GOVERNMENT_DOMAINS`, `IDENTITY_PROVIDER_DOMAINS`) | (no `.env.example` or docs entry) | Orphan | low | config-follows-code | Hardcoded blocklist categories are user-invisible; document so operators can predict denials. |
| 11 | Tunnel / Cloudflared / Tailscale / ngrok / OpenVPN / Pinggy / custom | `src/config/schema.rs` `TunnelConfig` + 6 provider-specific structs; `src/tunnel/mod.rs:1-80` (real `Tunnel` trait + `create_tunnel` factory) | `.env.example` (zero tunnel docs); `docs/ops/network-deployment.md` mentions tunnel concepts only loosely | Orphan | low | config-follows-code | Working code, no operator-facing config recipe. |
| 12 | `AGENTS.md` Low/Medium/High risk tiers | `AGENTS.md:54-58` (PR-review tiers) | `src/security/policy.rs:20-26` (`CommandRiskLevel::Low/Medium/High` for shell commands only); `src/security/iam_policy.rs:1-100` (Nevis-role IAM, deny-by-default) | Drift | low | rewrite-both | Same name space, different scopes. Disambiguate or rename one to remove the false coupling. |
| 13 | `AGENTS.md` listed as a workspace personality file | `src/agent/personality.rs:19` | Repo root `AGENTS.md` (cross-tool contributing instructions, not a workspace persona) | Drift | low | rewrite-both | When an agent runs with the repo as its workspace, `AGENTS.md` gets injected into the persona prompt — likely unintended. Decide whether AGENTS.md is contributor-facing or persona-facing and stop using it for both. |
| 14 | Operator MCP launcher availability | `src/agent/operator/mod.rs:134-196` (`inject_operator`) — only checks `config.operator.enabled` | (no `probe_*` analogue to Kumiho's `probe_kumiho_memory_advanced` exists) | Orphan | low | code-follows-config | Inject path proceeds even when `~/.revka/operator_mcp/run_operator_mcp.py` is absent; symmetry with Kumiho would justify a probe + lite/full operator prompt or a clear warning. |

## Per-row Narrative

### Row 1: Kumiho memory contract

**Claim:** `src/agent/kumiho.rs:40-161` (`KUMIHO_BOOTSTRAP_PROMPT`) issues hard mandates such as:
> `ENGAGE: Call kumiho_memory_engage ONCE when prior context is needed…` (line 49)
> `NEVER SAY 'I DON'T KNOW' WITHOUT CHECKING MEMORY — … you MUST call kumiho_memory_engage first.` (lines 62-66)
> `EXPLICIT REMEMBER REQUESTS — … you MUST capture it via kumiho_memory_reflect.` (lines 78-81)

The prompt is appended unconditionally inside `append_kumiho_bootstrap()` whenever `config.kumiho.enabled` is true (`src/agent/kumiho.rs:537-551`).

**Actual:** `inject_kumiho()` (lines 416-488) only checks `config.kumiho.enabled`. It does not verify that `~/.revka/kumiho/run_kumiho_mcp.py` exists, that the sidecar venv is set up, or that the MCP server actually started. The `probe_kumiho_memory_advanced()` filesystem probe (lines 276-309) exists and is consulted by `KumihoBootstrapSection::build()` (`src/agent/prompt.rs:135-149`) to decide between full and lite variants — but the lite variant still mandates `kumiho_memory_store` / `kumiho_memory_retrieve`, which themselves only exist when the sidecar is running.

The lite/full split (introduced in this very audit's prior remediation, per the in-source comment at `src/agent/prompt.rs:139-142`) handles the most common Aspirational-failure case (`kumiho_memory` Python package missing) but does not handle the case of `kumiho.enabled=true` + sidecar process failed to start. There the prompt mandates tools that are not registered.

**Why this classification:** The prompt issues "MUST call X" instructions for tools whose presence is not guaranteed by the code path that emits the prompt. That is the textbook Aspirational pattern. The remediation already in flight (probe + lite variant) is the right shape — extend it to one more conditional: emit the bootstrap only when MCP registration has confirmed Kumiho tools are reachable, otherwise emit nothing or a degraded note.

### Row 2: `BOOTSTRAP.md` personality file

**Claim:** `src/agent/personality.rs:14-24` defines:
```rust
const PERSONALITY_FILES: &[&str] = &[
    "SOUL.md",
    "IDENTITY.md",
    "USER.md",
    "AGENTS.md",
    "TOOLS.md",
    "HEARTBEAT.md",
    "BOOTSTRAP.md",
    "MEMORY.md",
];
```
`load_personality()` (lines 85-117) reads each from the workspace root and `IdentitySection::build()` (`src/agent/prompt.rs:152-185`) injects the rendered content into the system prompt.

**Actual:** `ensure_bootstrap_files()` (`src/config/schema.rs:8781-8813`) creates only `IDENTITY.md` and `SOUL.md` defaults. `BOOTSTRAP.md` is created nowhere. No documentation describes a schema for `BOOTSTRAP.md`. `grep -r BOOTSTRAP\\.md` finds the single reference at `src/agent/personality.rs:22`. The file does not exist in this repo.

**Why this classification:** A name listed in code as a load target with no creator, no schema, no docs is a Phantom. Per the brief's 2026-05-04 user decision — "BOOTSTRAP.md is to be deleted entirely. Its single useful instruction (call `kumiho_memory_engage` on first turn) belongs in the prompt-builder's runtime first-turn template, not as an auto-loaded markdown file." Remediation is **delete** from `PERSONALITY_FILES`.

### Row 3: Other phantom personality files (`USER.md`, `TOOLS.md`, `HEARTBEAT.md`, `MEMORY.md`)

**Claim:** Same loader list at `src/agent/personality.rs:18, 20, 21, 23`.

**Actual:** Same as Row 2 — `ensure_bootstrap_files()` creates none of them; no docs define their schemas; nothing else in `src/` references them by string literal except the loader and its tests.

**Why this classification:** Identical Phantom shape to Row 2. The remediation is `rewrite-both` (rather than blanket-delete) because a designed identity contract MIGHT want a `USER.md` (user profile) or `TOOLS.md` (tool overrides). Decision goes to the operator/architect: either define and document each, or trim the list to only what `ensure_bootstrap_files()` populates.

### Row 4: First-turn instruction "Invoke the kumiho-memory:kumiho-memory skill"

**Claim:** `src/agent/kumiho.rs:90-97`:
```
=== FIRST MESSAGE ONLY ===
Skip this block on all subsequent messages.
  1. Invoke the kumiho-memory:kumiho-memory skill.
  2. If the user's first message is a greeting…
```

**Actual:** Revka has no `Skill` invocation tool whose argument is a `<plugin>:<skill>` slug. `read_skill` (`src/tools/read_skill.rs`) reads skill markdown files; it does not "invoke" anything. The Kumiho MCP server is wired into the config at `inject_kumiho()` (line 416-488) before the first turn ever runs, so there is nothing for the agent to manually "invoke" — the tools are already registered. The instruction is a vestige of the Paseo `session-bootstrap.py` template (per the source comment at `src/agent/kumiho.rs:38`).

**Why this classification:** The instruction names a runtime action the agent cannot perform — a Phantom. Severity is low because the rest of the bootstrap is correct and the agent typically ignores the bullet without consequence; the "skill" reference is just dead weight that lives next to live instructions, increasing the risk of confusion.

### Row 5: `max_actions_per_hour` documented default

**Claim:** `src/config/schema.rs:5250`:
> `/// Maximum actions allowed per hour per policy. Default: \`100\`.`

**Actual:** `src/config/schema.rs:5376` in `impl Default for AutonomyConfig`:
```rust
max_actions_per_hour: 20,
```

**Why this classification:** Plain Drift between docstring and `Default` impl. The runtime tracker (`ActionTracker::record`, `src/security/policy.rs:42-58`) honours whatever value `SecurityPolicy.max_actions_per_hour` carries, with a 3,600-second sliding window — so the *behaviour* is correct, but the *documented default* misleads operators by 5×.

### Row 6: `max_cost_per_day_cents` documented default

**Claim:** `src/config/schema.rs:5252`:
> `/// Maximum cost per day in cents per policy. Default: \`1000\`.`

**Actual:** `src/config/schema.rs:5377`:
```rust
max_cost_per_day_cents: 500,
```

**Why this classification:** Same defect family as Row 5. Docstring is 2× the actual default.

### Row 7: Built-in `memory_*` tool family Phantom

**Claim:** Multiple surfaces describe `memory_store`, `memory_recall`, and `memory_forget` as available built-in tools:

- `tool_descriptions/en.toml:38-40` provides full localised descriptions:
  > `memory_forget = "Remove a memory by key. …"`
  > `memory_recall = "Search long-term memory for relevant facts, preferences, or context. …"`
  > `memory_store = "Store a fact, preference, or note in long-term memory. …"`
- `src/config/schema.rs:5295` includes `"memory_recall"` in `default_auto_approve()`.
- `src/config/schema.rs:7260, 11472, 11522, 15850, 15864-15865` includes them in embedded sample `config.toml` and default lists.
- `src/onboard/wizard.rs:6068-6074` directly lists them as tools available to the user during onboarding:
  > `**memory_store** — Save to memory`
  > `**memory_recall** — Search memory`
  > `**memory_forget** — Delete a memory entry`
- `src/onboard/wizard.rs:7116-7118` writes them into the workspace `TOOLS.md` scaffold.
- `src/channels/mod.rs:5356-5364` references them in the non-CLI excluded-tools default (a list that only matters if the tools exist).

**Actual:** `src/tools/mod.rs:416` (inside `all_tools_with_runtime()`):
```rust
// Old memory tools (store, recall, forget, export, purge) removed — use Kumiho MCP tools.
```
The tools are not constructed, not registered, and grep of `src/tools/` confirms no `MemoryStoreTool` / `MemoryRecallTool` / `MemoryForgetTool` Rust struct exists. The runtime ships only Kumiho MCP equivalents (`kumiho_memory_store`, `kumiho_memory_retrieve`, etc.), and `src/onboard/wizard.rs:5915` does call this out — but the same wizard then turns around and lists the disabled names as tools at lines 6068-6074. Internal contradiction.

**Why this classification:** Phantom across at least five surfaces. Severity is **high** because the onboarding wizard actively teaches new users to call tools that will return "tool not found." This is the only high-severity row in the audit.

### Row 8: `FoxClaw` legacy name in test fixture

**Claim:** `operator-mcp/tests/conftest.py:15`:
```python
# `[kumiho].harness_project` (e.g. legacy "FoxClaw") would otherwise make
```

**Actual:** Project rebrand from `FoxClaw → Revka` was decided 2026-04-16. The active runtime no longer uses `FoxClaw` anywhere — `grep -ri foxclaw` in `src/` returns only the unrelated provenance comment at `src/agent/personality.rs:4` ("Ported from RustyClaw…").

**Why this classification:** Single comment-only Drift. Replace with generic phrasing (e.g. "a non-default harness project name") to remove the dated reference. No runtime impact.

### Row 9: MCP servers / Kumiho + Operator env wiring undocumented

**Claim:** `.env.example` (root) lists LLM provider keys and a few app integrations but nothing about Kumiho or Operator sidecars.

**Actual:** `kumiho_mcp_server_config()` (`src/agent/kumiho.rs:312-397`) forwards the following env vars to the spawned MCP process when present: `REVKA_AGENT_ROOT`, `KUMIHO_SPACE_PREFIX`, `KUMIHO_MEMORY_PROJECT`, `KUMIHO_HARNESS_PROJECT`, `KUMIHO_AUTH_TOKEN`, `KUMIHO_SERVICE_TOKEN` (used as fallback source for `KUMIHO_AUTH_TOKEN`), `KUMIHO_CONTROL_PLANE_URL`, `KUMIHO_AUTO_CONFIGURE`, `KUMIHO_LLM_API_KEY`, `KUMIHO_LLM_PROVIDER`, `KUMIHO_LLM_MODEL`, `KUMIHO_LLM_LIGHT_MODEL`, `KUMIHO_LLM_BASE_URL`, plus `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` as summarisation fallbacks. `inject_operator()` (`src/agent/operator/mod.rs:134-196`) similarly forwards `KUMIHO_AUTH_TOKEN`, `KUMIHO_AUTO_CONFIGURE`, `REVKA_GATEWAY_URL`, `REVKA_GATEWAY_TOKEN`, `KUMIHO_API_URL`, `KUMIHO_MEMORY_PROJECT`, `KUMIHO_HARNESS_PROJECT`.

**Why this classification:** Working runtime feature, no operator-facing knob documented. Orphan. The fix is to add a `# ── Kumiho memory sidecar ──` block to `.env.example` listing the variables and what each controls.

### Row 10: Browser / web hardcoded gated-domain categories

**Claim:** No documentation describes that Revka hard-blocks certain domain families even before any user-configured allowlist.

**Actual:** `src/security/domain_matcher.rs` ships hardcoded constants: `BANKING_DOMAINS`, `MEDICAL_DOMAINS`, `GOVERNMENT_DOMAINS`, `IDENTITY_PROVIDER_DOMAINS`. `DomainMatcher::is_gated()` checks them against any URL passed through `validate_target_url()` (called from `WebFetchTool::validate_url`, `src/tools/web_fetch.rs:54-62`).

**Why this classification:** Behaviour exists, no surfaced configuration or docs. Orphan. The fix is documentation + (optional) a config knob to extend the categories per deployment.

### Row 11: Tunnel / Cloudflared etc. undocumented

**Claim:** `src/config/schema.rs` declares `TunnelConfig` with sub-configs for `cloudflare`, `tailscale`, `ngrok`, `openvpn`, `pinggy`, `custom`. `.env.example` documents none.

**Actual:** `src/tunnel/mod.rs:1-80` defines the `Tunnel` trait and `create_tunnel()` factory; `src/tunnel/cloudflare.rs`, `src/tunnel/tailscale.rs`, `src/tunnel/ngrok.rs`, `src/tunnel/pinggy.rs`, `src/tunnel/openvpn.rs`, `src/tunnel/custom.rs` provide real implementations that wrap the corresponding binary. `~/.cloudflared/config.yml` does not exist on this machine, so end-to-end Cloudflare routing could not be verified — but the Revka-side code for spawning `cloudflared` is real, not a stub.

**Why this classification:** Orphan — config schema and runtime implementations exist but operators have no `.env.example` or `docs/ops/` cookbook describing how to enable a tunnel.

### Row 12: `AGENTS.md` risk tiers vs runtime `CommandRiskLevel`

**Claim:** `AGENTS.md:54-58`:
> - **Low risk**: docs/chore/tests-only changes
> - **Medium risk**: most `src/**` behavior changes without boundary/security impact
> - **High risk**: `src/security/**`, `src/runtime/**`, `src/gateway/**`, `src/tools/**`, `.github/workflows/**`, access-control boundaries

**Actual:** `src/security/policy.rs:20-26`:
```rust
pub enum CommandRiskLevel { Low, Medium, High }
```
This enum classifies *shell command risk* for runtime execution gating (used by `require_approval_for_medium_risk` and `block_high_risk_commands`). It is not consumed by the AGENTS.md tier system. Conversely, `src/security/iam_policy.rs:1-100` implements role-based access control for Nevis with deny-by-default, also unrelated to the AGENTS.md tiers.

**Why this classification:** Same vocabulary (Low/Medium/High risk), two unrelated mechanisms. Drift in the soft sense — when an operator reads either side, they may assume coupling that does not exist. Remediation `rewrite-both`: either rename one tier system or add a paragraph in AGENTS.md clarifying that the dev tiers are a PR-review heuristic, not enforced at runtime.

### Row 13: `AGENTS.md` listed as a workspace personality file

**Claim:** `src/agent/personality.rs:19` includes `"AGENTS.md"` in `PERSONALITY_FILES`.

**Actual:** The repo root `AGENTS.md` (and the user's CLAUDE.md note that it overlays this file) is cross-tool agent contributing instructions — what to do when *editing* the codebase. It is not a persona/identity file. When an agent runs with the repo as its workspace_dir, `IdentitySection` (`src/agent/prompt.rs:152-185`) loads `AGENTS.md` and inlines it into the persona block under `### AGENTS.md`. This conflates two different intents: contributor guidance and runtime persona.

**Why this classification:** Drift in the role of the file. The fix (`rewrite-both`) is to either (a) drop `AGENTS.md` from the personality loader and let it be read on-demand by tools that need contributor guidance, or (b) split the file into a contributor-facing AGENTS.md and a workspace-personality file with a different name.

### Row 14: Operator MCP launcher availability not probed

**Claim:** `src/agent/operator/mod.rs:134-196` `inject_operator()`:
```rust
if !config.operator.enabled { return config; }
…
config.mcp.servers.insert(0, server);
```

**Actual:** Unlike Kumiho's `probe_kumiho_memory_advanced()` (`src/agent/kumiho.rs:276-309`), there is no equivalent probe for the operator launcher. If `~/.revka/operator_mcp/run_operator_mcp.py` is missing, `inject_operator()` still adds the server entry; the MCP registry will fail to spawn it at first use. The operator prompt (which lists ~30 tools at `src/agent/operator/core.rs:35-48`) is appended unconditionally on `config.operator.enabled` (`src/agent/operator/mod.rs:208-226`), mandating tools that may not load.

**Why this classification:** Orphan / weak symmetry. Same defect as Row 1 in shape, but for the operator side. Remediation `code-follows-config`: add a probe mirroring `probe_kumiho_memory_advanced`, then either fall back to a lite operator prompt or refuse to inject the operator block when the sidecar is unavailable.

## Verified-coherent surfaces (no remediation row)

These were checked and found to align; recorded here so they don't reappear in later phases as "unchecked."

- **Allowed-paths / file-tool guard.** `src/config/schema.rs:5243-5282` (`workspace_only` + `allowed_roots` semantics) matches enforcement at `src/tools/file_read.rs:66-71` via `SecurityPolicy::is_path_allowed` and `src/security/workspace_boundary.rs:74-102`. Config and runtime agree.
- **Cron timezone.** `Schedule::Cron { tz: Option<String> }` at `src/cron/types.rs:90-104` matches `next_run_for_schedule()` at `src/cron/schedule.rs:8-26`, which uses `chrono_tz::Tz::from_str(tz)` for IANA lookup and falls back to UTC. Tool description at `src/tools/cron_add.rs:87` documents the behaviour correctly.
- **Operator MCP tool list (prompt vs server).** Every tool named in `OPERATOR_CORE_PROMPT` (`src/agent/operator/core.rs:35-48`) and `OPERATOR_CHANNEL_PROMPT` (`src/agent/operator/mod.rs:234-246`) maps to a real `name="…"` entry in `operator-mcp/operator_mcp/operator_mcp.py` (verified for `create_agent`, `wait_for_agent`, `send_agent_prompt`, `get_agent_activity`, `list_agents`, `search_agent_pool`, `save_agent_template`, `list_agent_templates`, `list_teams`, `get_team`, `spawn_team`, `create_team`, `search_teams`, `save_plan`, `recall_plans`, `create_goal`, `get_goals`, `update_goal`, `get_budget_status`, `record_agent_outcome`, `get_agent_trust`, `capture_skill`, `list_skills`, `load_skill`, `search_clawhub`, `browse_clawhub`, `install_from_clawhub`, `list_nodes`, `invoke_node`, `get_session_history`, `archive_session`, `compact_conversation`, `store_compaction`). The reverse direction is asymmetric — operator-mcp registers ~95 tools and the prompt only references ~30 — but that is intentional compaction (per the design comment at `src/agent/operator/core.rs:6-12`), so it does not warrant a Drift row.
- **Built-in tool registry vs `ToolsSection`.** `ToolsSection::build` (`src/agent/prompt.rs:208-228`) iterates `ctx.tools` and renders every tool's `name() / description() / parameters_schema()`. There is no static prompt-side tool list to drift; the only stale entries are the i18n descriptions for the deleted `memory_*` tools (Row 7).
- **Kumiho `kumiho_memory_*` advanced tools probe.** `probe_kumiho_memory_advanced` (`src/agent/kumiho.rs:276-309`) and `KumihoBootstrapSection` (`src/agent/prompt.rs:130-150`) collaborate correctly — when the package is missing, the lite variant is emitted and the advanced reflexes are not mandated. This was Row 1 + 13 of an earlier audit pass; it is now the existing remediation pattern, not a new mismatch.

## Recommendations Summary (drives Phase 0-2)

Grouped by remediation class, ordered by severity within each group.

### `delete`

- **Row 2** (med) — Drop `BOOTSTRAP.md` from `PERSONALITY_FILES` at `src/agent/personality.rs:22`. If keeping the test that asserts missing-file handling, switch the assertion target to a different filename or remove that specific test row. Move any first-turn instruction value to the prompt builder per the brief.

### `config-follows-code`

- **Row 7** (high) — Strip every `memory_store` / `memory_recall` / `memory_forget` reference from: `tool_descriptions/en.toml:38-40` (and parity locales — `ar.toml … zh-CN.toml`), `src/config/schema.rs:5295, 7260, 11472, 11522, 15850, 15864-15865`, `src/onboard/wizard.rs:6068-6074, 7099-7118`, `src/channels/mod.rs:5356-5364`, and any tests that assert their presence. Where appropriate (auto_approve list especially), substitute the Kumiho equivalents. Cross-check that `src/onboard/wizard.rs:5913-5915` remains consistent after the removal.
- **Row 5** (med) — Update `src/config/schema.rs:5250` docstring to `Default: 20.` (or change the runtime default to 100 if 100 is what the project actually wants — defer to product owner; the audit's job is to flag the divergence).
- **Row 6** (med) — Same as Row 5 for `src/config/schema.rs:5252` / 5377 (`500` vs `1000`).
- **Row 4** (low) — Remove the `1. Invoke the kumiho-memory:kumiho-memory skill.` line from `src/agent/kumiho.rs:90-97`. The remaining bullets in the FIRST MESSAGE block remain useful.
- **Row 8** (low) — Replace `FoxClaw` in `operator-mcp/tests/conftest.py:15` with `e.g. a non-default harness project name`.
- **Row 9** (low) — Append a Kumiho + Operator section to `.env.example` documenting the env vars enumerated in Row 9 above.
- **Row 10** (low) — Either add a docs page describing the gated-domain categories in `src/security/domain_matcher.rs`, or expose them via a config field.
- **Row 11** (low) — Add tunnel-provider examples to `.env.example` and a `docs/ops/tunneling.md` page covering Cloudflare, Tailscale, ngrok, Pinggy, OpenVPN, custom.

### `rewrite-both`

- **Row 1** (med) — Extend the Kumiho prompt-conditional pattern: only emit `KUMIHO_BOOTSTRAP_PROMPT*` once MCP registration has confirmed Kumiho tools are reachable. Otherwise emit a one-liner ("Kumiho memory disabled this session.") or nothing. Rename `probe_kumiho_memory_advanced` to a broader liveness probe, or add a sibling `probe_kumiho_sidecar_runnable` and gate `append_kumiho_bootstrap` on it.
- **Row 3** (low) — For each of `USER.md`, `TOOLS.md`, `HEARTBEAT.md`, `MEMORY.md`: either define a schema + add a default + document it (and add a creator in `ensure_bootstrap_files`), or delete the entry from `PERSONALITY_FILES`. Recommend deletion unless there is a concrete near-term use case.
- **Row 12** (low) — Either rename one of the two `Low/Medium/High` tier vocabularies, or add a one-paragraph note in `AGENTS.md` and `src/security/policy.rs` cross-referencing each other to dispel the false coupling.
- **Row 13** (low) — Decide AGENTS.md's role. If it remains contributor-facing, drop it from `PERSONALITY_FILES` (it should not be inlined into the agent persona). If a workspace persona file is needed, give it a distinct name (e.g. `WORKSPACE.md`).

### `code-follows-config`

- **Row 14** (low) — Add `probe_operator_sidecar_installed()` mirroring `probe_kumiho_memory_advanced()`, and have `inject_operator()` skip injection (or emit a warning + lite prompt) when the launcher is missing. Also avoid appending `OPERATOR_CORE_PROMPT` when the sidecar is unavailable.
