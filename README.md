<p align="center">
  <img src="docs/assets/revka-banner.png" alt="Revka" width="600" />
</p>

<h1 align="center">Revka</h1>

<p align="center">
  <strong>A memory-native agent runtime. Graph-backed orchestration, trust-scored agents, a full Web UI — and a complete record of everything your agents have ever done.</strong>
</p>

<p align="center">
  <a href="https://www.rust-lang.org"><img src="https://img.shields.io/badge/rust-edition%202024-orange?logo=rust" alt="Rust Edition 2024" /></a>
  <a href="LICENSE-APACHE"><img src="https://img.shields.io/badge/license-MIT%20OR%20Apache%202.0-blue.svg" alt="License: MIT OR Apache-2.0" /></a>
  <a href="https://kumiho.io/pricing"><img src="https://img.shields.io/badge/Kumiho%20Cloud-Free%205k%20nodes-7c3aed" alt="Kumiho Cloud free tier" /></a>
  <a href="https://kumiho.io/en/resources/community-edition"><img src="https://img.shields.io/badge/Kumiho%20CE-self--host-4CAF50?logo=github" alt="Kumiho CE self-host" /></a>
</p>

<p align="center">
  <a href="README.md">🇺🇸 English</a> · <a href="README.ko.md">🇰🇷 한국어</a>
</p>

---

## How Revka fits together

Revka is **open source**. The persistent graph memory it depends on — Kumiho — ships in two server flavors: a **managed cloud service** (free tier → paid, zero ops) and **Kumiho CE** (Community Edition, prebuilt binary, free to download, self-host on your own infra). Open SDKs, Enterprise self-host also available. Three layers, plain language:

| Layer | What it is | License |
|---|---|---|
| **Revka** (this repo) | Rust gateway + daemon + agent loop + channels + tools + peripherals + embedded React dashboard + Tauri desktop app + CLI + Operator Python MCP | MIT **or** Apache 2.0 — your choice |
| **Kumiho SDKs** ([kumiho-SDKs](https://github.com/KumihoIO/kumiho-SDKs)) | Python clients: `kumiho` (the graph backend) and `kumiho-memory` (the AI cognitive memory layer Revka integrates with) | Open source |
| **Kumiho server — Cloud** | Managed control plane: version-controlled, schemaless, typed-edge graph engine, DreamState consolidation, Neo4j-backed. Reached via the **kumiho-FastAPI BFF** at `api.kumiho.cloud`. No ops required. | Free 5k nodes → paid tiers from $40/mo · Enterprise dedicated |
| **Kumiho server — CE** | Community Edition — prebuilt binary, free to download, self-host on your own infra. Set `[kumiho].api_url` to point at it. Neo4j-backed, full graph API, same SDKs. Source not included. | Free download · [kumiho.io/en/resources/community-edition](https://kumiho.io/en/resources/community-edition) |

LLM inference is **bring-your-own-provider**. We don't proxy or mark up tokens. You point Revka at Anthropic, OpenAI, OpenRouter, Ollama, GLM, or any of [14+ providers](docs/reference/api/providers-reference.md), and your keys stay yours.

**Operator** (the orchestration layer) needs a provider for its own LLM calls — supervisor decompose, map-reduce reduce, refinement critic, group-chat moderate. It defaults to an API key and also supports OAuth, configured under `[operator]`.

**Agents that Operator spawns behave differently.** Each `agent` step invokes a coding CLI subprocess — supporting **Claude Code**, **Codex**, **Antigravity CLI (agy)**, **Cursor CLI (agent)**, and **OpenCode** — with prompts piped over stdin to avoid `ARG_MAX` and shell-encoding issues. The CLI's existing OAuth/login handles auth, so your subscription becomes the spawned-agent runtime: no per-call API spend on the spawned agents themselves. Any Revka-defined MCP servers (such as `kumiho-memory` and `operator-tools`) are cleanly injected and inherited on a per-CLI basis. Direct API keys still apply to one-shot `revka agent` calls and channel-routed conversations.

Revka talks to Kumiho over HTTP via `[kumiho].api_url` in your config — point it at the **cloud service** (`https://api.kumiho.cloud`) or at your own **Kumiho CE** instance. Without a reachable Kumiho endpoint, Revka degrades to stateless single-agent operation — useful for demos and CI, but the cross-session memory, provenance edges, audit chain, and trust scoring all live in the graph. Cloud pricing: [kumiho.io/pricing](https://kumiho.io/pricing). CE self-host: [kumiho.io/en/resources/community-edition](https://kumiho.io/en/resources/community-edition).

> **Upstream.** Revka's core Rust runtime is a fork of [ZeroClaw](https://github.com/zeroclaw-labs/zeroclaw). The agent loop, provider/channel/tool architecture, hardware peripheral layer, and CLI scaffolding trace back to that upstream; full attribution in [`NOTICE`](NOTICE) and the consolidated overview at [`docs/upstream/zeroclaw-attribution.md`](docs/upstream/zeroclaw-attribution.md). "ZeroClaw" and the ZeroClaw logo are trademarks of ZeroClaw Labs; Revka is not affiliated with, endorsed by, or sponsored by ZeroClaw Labs.

---

## What is Revka?

Revka is a Rust-native AI agent runtime with persistent cognitive memory, multi-agent orchestration, and a shared skill/template marketplace. Every agent session, plan, skill, and trust score lives in a graph — if it happened, it's queryable. Built on Kumiho's graph-native memory system, Revka treats memory not as a feature but as the substrate every agent wakes up in.

At the core: a **Rust gateway** (Axum) serves a **React/TypeScript Web Dashboard**, a Python **Operator** drives multi-agent orchestration, and **Kumiho** (graph-native, Neo4j-backed) holds all persistent state. Define declarative YAML workflows, watch agents execute them in real time via a DAG-based live view, trace every tool call and output, and see trust scores evolve across runs — all from a browser.

No hidden state. No forgotten runs. The only thing the system asks of you is that you notice.

<!-- TODO screenshot: Revka dashboard at http://127.0.0.1:42617 showing live runtime posture — active sessions, channels, audit chain, cost metrics, and recent workflow runs -->
![Revka dashboard at localhost:42617 showing live runtime posture — active sessions, channels, audit chain, cost metrics, and recent workflow runs](docs/assets/dashboard/readme-01-dashboard-hero.png)

---

## Free tier, Studio trial, and referrals

Kumiho is the persistent backend; Revka is its reference runtime. Trying the whole stack costs nothing to start.

- **Free tier — 5,000 nodes** (5× the previous limit, bumped for Revka GA). Matching ingest and retrieval limits. Real evaluation territory for solo use, not a demo cap.
- **30-day Studio trial** — unlocks the moment Revka stores its first memory, no credit card. 500,000 nodes, cross-session recall, audit visibility. Whatever you build during the trial, you **keep** when you revert to free; we don't claw back data.
- **Referrals** — each successful referral earns you another 30 days of Studio, stackable up to 3 (90 days). Beyond the cap, additional referrals convert to account credit. Friend signs up via your link and ingests their first memory; you both win.
- **Inactivity** — accounts unused for 90 days move to **cold storage**, never deleted. Log back in and the graph re-indexes within minutes. The product promise is *we don't forget*; that includes your account.
- **Self-host (CE)** — **Kumiho CE** is a prebuilt binary you can download and run on your own infrastructure at no cost. No node limits, no cloud dependency. Source not included. See [kumiho.io/en/resources/community-edition](https://kumiho.io/en/resources/community-edition).
- **Self-host (Enterprise)** — closed-source license with dedicated SLA, support, and additional hardening. Email <enterprise@kumiho.io> or see [kumiho.io/pricing](https://kumiho.io/pricing).

Full tier matrix and per-feature limits at [kumiho.io/pricing](https://kumiho.io/pricing).

---

## Web Dashboard & UI

The embedded web frontend (`http://127.0.0.1:42617`) is a single pane over every layer of the stack — 18 app routes built with React, TypeScript, Tailwind CSS, and Vite, then baked into the Rust binary at compile time via `rust-embed`. The primary sidebar exposes 17 of them; `Workflow Runs` (`/runs`) is a routed page reached contextually from workflow and dashboard links rather than the sidebar itself. One binary. One entry point. The whole signal.

The dashboard's primary navigation is organized into three sidebar sections (Orchestration, Operations, Inspection — see `web/src/revka/components/layout/revka-navigation.ts`).

### Core Views — Orchestration

| View | Path | Description |
|------|------|-------------|
| **Dashboard** | `/dashboard` | Live runtime posture — active sessions, channels, audit chain, cost, workflow metrics, recent runs, risk rail |
| **Workflows** | `/workflows` | Full CRUD for declarative YAML workflows with YAML editor, DAG workspace, step definition, and dispatch |
| **Workflow Runs** | `/runs` | Run history, run detail with DAG + RunLog drill-down, retry, delete, approve, and agent activity view |
| **Agents** | `/agents` | CRUD for agent templates — identity, soul, expertise, tone, model, allowed tools |
| **Canvas** | `/canvas` | Real-time HTML/CSS/JS sandbox with WebSocket-driven rendering and frame history |
| **Teams** | `/teams` | Team builder with graph view of agent relationships and delegation topology |

### Live Workflow Execution View

<!-- TODO screenshot: live workflow execution view — interactive DAG with steps shifting through pending → running → completed, WebSocket event streaming, and per-step detail panel (Live Events / Tool Calls / Output) -->
![Live workflow execution view: interactive DAG with steps shifting through pending, running, and completed states, WebSocket event streaming, and per-step detail panel](docs/assets/dashboard/readme-02-workflow-dag.png)

When a workflow runs, you watch the signal propagate:

- **Interactive DAG graph** — nodes are steps (agent, shell, output, notify, etc.), edges are dependencies. Colors shift in real time as steps move through pending → running → completed/failed. The topology breathes.
- **WebSocket event streaming** — `agent.started`, `agent.tool_use`, `agent.completed`, `agent.error` events arrive as they happen.
- **Step detail panel** with three tabs:
  - **Live Events** — real-time event feed for the selected step
  - **Tool Calls** — detailed agent activity from RunLog (tool name, arguments, results, status) fetched via REST on demand
  - **Output** — the agent's final message/deliverable
- **Per-agent RunLog** — every call, argument, result, and error committed to disk as JSONL and queryable via `GET /api/workflows/agent-activity/{agent_id}` with views: summary, tool_calls, messages, errors, full.

### Operations

| View | Path | Description |
|------|------|-------------|
| **Assets** | `/assets` | Kumiho asset browser — projects, spaces, items, revisions, artifacts |
| **Skills** | `/skills` | Skill library management with search/filter and ClawHub integration |
| **Tools** | `/tools` | Agent tool catalog and discovered CLI-binary catalog (`/api/tools`, `/api/cli-tools`) |
| **Integrations** | `/integrations` | External integrations and channel surface configuration |
| **Cron** | `/cron` | Scheduled job management — create, edit, delete, view run history |
| **Pairing** | `/pairing` | Device enrolment and issued-token management |
| **Config** | `/config` | TOML editor with schema-backed validation for providers, agents, skills, teams, workflows, channels |
| **Cost** | `/cost` | Per-model token counts, spend breakdown, and budget governance |

### Inspection

| View | Path | Description |
|------|------|-------------|
| **Memory** | `/memory` | Kumiho memory graph explorer (force-directed), revisions, content search (`/memory-auditor` redirects here) |
| **Logs** | `/logs` | Operational log viewer with filtering |
| **Audit** | `/audit` | Merkle hash-chain tamper-evident event log with chain verification |
| **Doctor** | `/doctor` | Automated runtime diagnostics and recovery posture |

### Operator Chat (Header Dropdown)

The **Operator** isn't only available through workflows — every page in the dashboard exposes a one-click chat dropdown from the header. Drop it down, ask a question, get an answer streamed back from the same Operator that drives your workflows. The chat tab and a terminal tab live side-by-side, so you can switch between asking and inspecting without leaving the page you're on.

![Revka dashboard with the Operator chat dropdown open over the Workflows view, showing a streaming reply alongside the chat / terminal tabs](docs/assets/dashboard/readme-06-operator-chat.png)

Backed by the same `/ws/chat` endpoint listed below, with auto-focus on open and per-route session memory so context carries across navigation.

### Real-time Capabilities

- **WebSocket Chat** (`/ws/chat`) — streaming agent responses with token-by-token rendering
- **Canvas WebSocket** (`/ws/canvas/{id}`) — live HTML/CSS/JS preview with iframe rendering and frame history
- **Node Status WebSocket** (`/ws/nodes`) — multi-node capability discovery and status
- **PTY Terminal WebSocket** (`/ws/terminal`) — interactive shell over WebSocket
- **MCP Session Event WebSocket** (`/ws/mcp/events`) — proxy to the in-process MCP server's session events
- **SSE Event Stream** (`/api/events`) — server-sent events for dashboard and activity updates
- **SSE Daemon Log Stream** (`/api/daemon/logs`) — streaming daemon log tail

---

## The Operator (Workflow Orchestration)

The **Operator** is Revka's hand on the controls — a Python MCP server that drives declarative YAML workflows through 20 step types and several advanced orchestration patterns. Agents run inside the Revka; the Operator sees the whole board.

Workflows are stored as YAML artifacts attached to Kumiho revisions — every save creates a new revision, giving you full history, version-to-version diffs, and promotion tagging (e.g., `published`) without an external version control system. The on-disk YAMLs under `~/.revka/workflows/` use a two-tier layout: the base file (`name.yaml`) is your editable working copy, discovered by the filesystem scan, while each save freezes a snapshot as `name.r{N}.yaml` bound to revision N as an immutable artifact via `file://` URI. The `.r{N}` files are skipped by directory scans and reachable only through kref resolution — so running an older revision is just `kref://...#rN` → Kumiho looks up the artifact URI → loader reads that exact frozen YAML from disk. The revision graph is the source of truth; the disk layout is just where the artifact bytes happen to live. Multi-agent patterns like `supervisor`, `group_chat`, `map_reduce`, `handoff`, and `human_approval` are first-class step types, so a multi-agent pipeline reads end-to-end in one file without a Python interpreter in the loop. See [WORKFLOWS.md](WORKFLOWS.md) for the full DSL reference.

<!-- TODO screenshot: embedded dashboard served by the Rust gateway, showing the current app shell and live operational surface -->
![Revka dashboard gateway landing page showing the embedded web dashboard served by the local gateway](docs/assets/dashboard-dev-01-gateway-landing.png)

### Step Types

Canonical types from `StepType` in `operator-mcp/operator_mcp/workflow/schema.py`.

| Step Type | Description |
|-----------|-------------|
| `agent` | Spawn a Revka coding agent (claude/codex/agy/agent/opencode) with prompt, role, model, tools, timeout |
| `shell` | Execute shell commands with timeout and failure handling |
| `python` | Run a Python script or inline Python with JSON I/O |
| `email` | Send outbound email via SMTP with optional click tracking and dry-run |
| `conditional` | Branch based on expressions over prior step outputs |
| `parallel` | Run sub-steps concurrently with join strategies (ALL, ANY, MAJORITY) |
| `goto` | Loop revka with `max_iterations` guard |
| `human_approval` | Pause for yes/no human confirmation (timeout configurable) |
| `human_input` | Pause for freeform human response via dashboard/Slack/Discord |
| `notify` | Push a fire-and-forget notification to one or more channels |
| `output` | Emit structured output (text/json/markdown) with template interpolation |
| `a2a` | Send tasks to external A2A agents via JSON-RPC 2.0 |
| `map_reduce` | Fan-out to N parallel mapper agents, reducer synthesizes (2-10 concurrency) |
| `supervisor` | Dynamic delegation: supervisor decomposes task, selects specialists iteratively |
| `group_chat` | Moderated multi-agent discussion (round_robin or moderator_selected) |
| `handoff` | Agent-to-agent context transfer with full message/files/tool-calls history |
| `for_each` | Sequential iteration over a range or list with `${for_each.*}` variables |
| `resolve` | Deterministic Kumiho entity lookup (returns kref/revision) |
| `tag` | Tag a Kumiho revision from within a workflow |
| `deprecate` | Deprecate a Kumiho item from within a workflow |

Editor actions like `action: notify` are resolved during loading via `ACTION_DEFAULTS` in `workflow/schema.py`; `notify` itself is also a real `StepType`.

### Agent Structured Output

Agent steps can declare required structured fields:

```yaml
agent:
  output_fields: [verdict, production_ready]
```

When `output_fields` is set, Revka appends final-output instructions to the
agent prompt, parses full JSON objects, final fenced `json` blocks, or
`FINAL_OUTPUT:` YAML blocks, then fails the step with `structured_output_missing`
if any declared field is absent. Downstream steps can route on fields such as
`${review.output_data.production_ready}`.

### Orchestration Patterns

**Parallel Execution** — concurrent step groups with three join strategies (ALL, ANY, MAJORITY) and configurable concurrency limits (1-10).

**Map-Reduce** — fan task to N parallel mapper agents, then a reducer synthesizes. Semaphore-controlled concurrency with optional halt-on-failure.

**Supervisor** — LLM-driven dynamic delegation loop. The supervisor decomposes tasks and selects specialist agents from the pool (trust-informed), iterating through DELEGATE/COMPLETE/REQUEST_INFO decisions.

**Group Chat** — moderated multi-agent discussion with topic framing. Supports round-robin turn-taking or moderator-selected next speaker. Produces structured synthesis (summary, consensus, conclusions, open questions).

**Handoff** — agent-to-agent context transfer preserving last message, files touched, and tool call summaries. Records `HANDED_OFF_TO` edges in Kumiho for provenance.

**Refinement Loop** — creator/critic pattern with structured quality scoring (0-100), verdict classification (APPROVED/NEEDS_CHANGES/BLOCKED), and fallback ladder.

### Variable Interpolation

Workflow steps support template interpolation across 9 namespaces:

```
${inputs.field}          — Workflow input parameters
${step_id.output}        — Step text output
${step_id.status}        — Step status
${step_id.output_data.k} — Structured output field
${step_id.files}         — Comma-separated file list
${step_id.agent_id}      — Agent ID that executed step
${loop.iteration}        — Current goto iteration count
${env.VAR}               — Environment variables
${run_id}                — Workflow run ID
```

### Execution Features

- **Dependency ordering** — `depends_on` with topological sort and circular dependency detection
- **Retry** — per-step retry (0-5) with configurable delay
- **Checkpointing** — auto-save to `~/.revka/workflow_checkpoints/` for crash recovery and resume
- **Dry run** — validate syntax and dependencies without execution
- **Condition evaluation** — expression-based branching over step results
- **Per-step timeout** — default 300s, configurable per step
- **RunLog JSONL** — per-agent persistent audit trail at `~/.revka/operator_mcp/runlogs/`

### Reactive Graph — Tag-Triggered Workflows

The Kumiho memory graph isn't just storage — it's the event bus. When a workflow's `output` step publishes an entity (kind + tag), the daemon emits a `revision.tagged` event. Any workflow with a matching `triggers:` block launches automatically, with trigger metadata mapped into its inputs:

```yaml
triggers:
  - on_kind: "qs-arc-plan"      # Watch for this entity kind
    on_tag: "ready"             # When tagged with this
    input_map:
      arc_kref: "${trigger.entity_kref}"
      arc_name: "${trigger.metadata.arc_name}"
```

This turns the graph into a reactive substrate: revising or tagging a Kumiho item is a side-effect-bearing action that fans out into downstream pipelines — no cron, no external webhook, no glue script. Workflow A publishes → Workflow B fires → Workflow C resolves the cursor → and so on, all driven by the data shape rather than orchestration code. Cron triggers (`cron: "0 9 * * 1"`) coexist for time-based schedules. See [WORKFLOWS.md](WORKFLOWS.md#triggers-and-workflow-chaining) for chaining patterns and the multi-run continuity recipe.

---

## Agent Pool & Templates

Reusable agent definitions stored in `~/.revka/operator_mcp/agent_pool.json` and synced to Kumiho under `Revka/AgentPool/`.

### Template Fields

| Field | Description |
|-------|-------------|
| `name` | Unique identifier |
| `agent_type` | `claude`, `codex`, `agy` (Antigravity), `agent` (Cursor), or `opencode` (OpenCode) |
| `role` | coder, reviewer, researcher, tester, architect, planner |
| `capabilities` | Skill tags (e.g., `["rust", "security-audit", "testing"]`) |
| `description` | What this agent excels at |
| `identity` | Rich identity statement |
| `soul` | Personality and values |
| `tone` | Communication style |
| `model` | Model override for CLIs that accept one |
| `system_hint` | Extra prompt context |
| `allowed_tools` | Tool allowlist (None = all) |
| `max_turns` | Conversation turn limit (default 200) |
| `use_count` | Usage statistics (auto-incremented) |

### Pool Operations

- Keyword search across name, role, capabilities, description
- Template validation quality gates before use
- High-performing templates surface first via usage tracking
- Integration with ClawHub for publishing and installing community templates

---

## Trust & Reputation System

Every agent execution is scored. Reputation is not assumed — it's earned, recorded, and queryable in Kumiho under `Revka/AgentTrust/`.

<!-- TODO screenshot: AgentTrust view in the dashboard showing per-agent trust score, total runs, recent outcome chips (success/partial/failed), and template attribution -->
![AgentTrust view: per-agent trust score, total runs, recent outcomes, and template attribution](docs/assets/dashboard/readme-04-trust-scoring.png)

| Metric | Description |
|--------|-------------|
| `trust_score` | Running average (0.0–1.0), computed as `total_score / total_runs` |
| `total_runs` | Number of task executions |
| `recent_outcomes` | Last 10 outcomes with format `outcome:task_summary:timestamp` |
| `template_name` | Reference to the agent template used |

**Outcome weights:** success = 1.0, partial = 0.5, failed = 0.0

Trust scores inform the Supervisor pattern's agent selection — higher-trust agents are preferred for delegation. Scores are recorded via `tool_record_agent_outcome` and retrieved via `tool_get_agent_trust` (sorted by score descending).

---

## A2A Protocol Support

Revka implements the [Google Agent-to-Agent (A2A) protocol](https://google.github.io/A2A/) for interoperability with external agent systems.

- **Discovery** — HTTP GET to `/.well-known/agent-card.json` returns agent capabilities, skills, and identity
- **Task lifecycle** — JSON-RPC 2.0: `message/send` (create), `tasks/get` (poll), `tasks/cancel`
- **Registry** — unified search across local Revka templates and external A2A agents
- **Retry** — up to 2 retries with exponential backoff
- **Workflow integration** — `type: a2a` workflow steps with explicit agent URL, message, and timeout

---

## Kumiho Memory Integration

Kumiho is the sole persistent backend. Everything the system knows lives here, as graph-native items with full versioning, provenance tracking, and edge relationships. If it happened, there is a trace.

<!-- TODO screenshot: Memory graph explorer at /memory — force-directed visualization of nodes, revisions, and provenance edges across Revka namespaces (AgentPool, Plans, Sessions, AgentTrust, etc.) -->
![Kumiho memory graph explorer: force-directed visualization of nodes, revisions, and provenance edges across Revka namespaces](docs/assets/dashboard/readme-05-memory-graph.png)

The namespaces below are Operator/Revka **conventions** — normal Kumiho spaces under `space_prefix = "Revka"` (set in `config.toml`) and, for skills, under `KUMIHO_MEMORY_PROJECT` (default `CognitiveMemory`). They are not schema-enforced typed namespaces.

| Namespace | Purpose |
|-----------|---------|
| `Revka/AgentPool/` | Agent templates (role, capabilities, model preferences) |
| `Revka/Plans/` | Execution plans with steps, dependencies, and status |
| `Revka/Sessions/` | Session summaries, handoff notes, cross-session continuity |
| `Revka/Goals/` | Strategic, tactical, and task-level goal tracking |
| `Revka/AgentTrust/` | Trust scores and interaction history |
| `Revka/ClawHub/` | Published templates, skills, and team configurations |
| `Revka/Teams/` | Team bundles (agent composition) |
| `Revka/WorkflowRuns/` | Operator workflow run records and run history |
| `Revka/Outcomes/` | Per-agent outcome records used by trust scoring |
| `CognitiveMemory/Skills/` | Shared skill library accessible to all agents |

For the integration patterns — engage/reflect, capture types, provenance edges, space organisation, skill discovery — see [`docs/contributing/kumiho-memory-integration.md`](docs/contributing/kumiho-memory-integration.md).

---

## Additional Features

- **ClawHub Marketplace** — publish, search, and install agent templates, skills, and team configurations from a shared registry (`src/gateway/api_clawhub.rs`)
- **Multi-Node Distribution** (`src/nodes/`) — distribute agent workloads across remote nodes via WebSocket for horizontal scaling
- **Cross-Session Continuity** — session journals capture handoff notes; Kumiho archival lets a new session resume with full recall
- **Goal Hierarchy** — three-tier tracking (strategic/tactical/task) with graph-persisted status, dependencies, and progress
- **Skill Library** (`src/skills/`, `CognitiveMemory/Skills/`) — agents discover, register, and execute reusable skills shared across the runtime; markdown prompts plus `SKILL.toml` metadata, addressable as Kumiho krefs
- **Self-Improving Skills** (`src/skills/auto_improve.rs`, `effectiveness.rs`, `auto_rollback.rs`) — the operator records per-call success/failure outcomes; recency-weighted effectiveness scores surface regressing skills; an LLM rewrites their markdown prompts; the new content publishes as a fresh Kumiho-versioned revision tagged `published` (with auto-rollback if the rewrite itself regresses)
- **Audit Trail** (`src/security/audit.rs`) — Merkle hash-chain tamper-evident logging with cryptographic verification
- **Cost Tracking** (`src/cost/`) — per-model token and cost breakdown with budget governance at agent or system level
- **Cron Scheduling** (`src/cron/`) — recurring, one-shot, and interval jobs with history and catch-up behaviour
- **Device Pairing** (`src/security/pairing.rs`) — 6-digit code-based device authentication; optional WebAuthn hardware keys (`--features webauthn`)
- **Approval Gateway** (`src/approval/`, `/api/workflows/runs/{id}/approve`) — human-in-the-loop approvals with dashboard toaster
- **Hooks** (`src/hooks/`) — built-in and user-defined hook runners with a Claude Code hook endpoint (`POST /hooks/claude-code`)
- **Routines** (`src/routines/`) — event-matched routines that trigger based on incoming events
- **SOP Engine** (`src/sop/`) — standard-operating-procedure dispatch with conditions and metrics
- **Observability** (`src/observability/`) — OpenTelemetry, Prometheus (`/metrics`), DORA metrics, verbose/log sinks, runtime trace
- **Health & Heartbeat** (`src/health/`, `src/heartbeat/`) — liveness/readiness, heartbeat engine and store for daemon self-checks
- **Doctor** (`src/doctor/`) — diagnostics for daemon/scheduler/channel freshness and model/trace sub-commands
- **E-Stop** (`src/security/estop.rs`) — emergency stop with level engagement (network-kill, domain-block, tool-freeze) and resume
- **Verifiable Intent** (`src/verifiable_intent/`) — cryptographic issuance/verification for agent-action intent receipts
- **RAG** (`src/rag/`) — retrieval-augmented helpers that layer over the memory backends
- **Multimodal** (`src/multimodal.rs`) — image, voice, and media ingestion primitives shared across channels
- **Runtime Sandboxing** (`src/runtime/`, `src/security/`) — native, Docker, and WASM runtimes; Seatbelt/Landlock/Firejail/Bubblewrap sandbox wrappers; Nevis secrets, domain matching, prompt guard
- **Tunnels** (`src/tunnel/`) — Cloudflare, ngrok, Pinggy, Tailscale, OpenVPN, and custom tunnel providers to expose the gateway
- **MCP Server** (`src/mcp_server/`) — in-process MCP server with session registry, progress wrapping, and skills-as-tools
- **ACP Server** — JSON-RPC 2.0 over stdio for IDE integration (`revka acp`)
- **WASM Plugins** (`src/plugins/`, `--features plugins-wasm`) — load signed WASM tools and channels at runtime
- **Onboard Wizard** (`src/onboard/`) — interactive and quick-mode first-run configuration
- **OS Service Management** (`src/service/`) — launchd/systemd/OpenRC install for `revka daemon`
- **Update Pipeline** (`src/commands/update.rs`) — 6-phase update with preflight, backup, validate, swap, smoke test, and auto-rollback
- **Internationalization** (`src/i18n.rs`) — runtime locale; supported docs locales: `en`, `ko`, `vi`, `zh-CN`

---

## Hardware & Peripherals

Revka runs as a single Rust binary on x86_64 and arm64 Linux (including Raspberry Pi 3/4/5), macOS, and Windows — the release profile is tuned for low-memory targets (`codegen-units = 1`, `opt-level = "z"`, `panic = "abort"`). Full features — persistent memory, multi-agent workflows, the embedded dashboard — require an out-of-process Kumiho memory service and Python 3.11+ for the Operator MCP; without them, Revka degrades gracefully to a stateless single-agent runtime.

Embedded boards are supported as **peripherals driven over serial/USB from a Revka host**, not as standalone Revka runtimes. Running the full daemon on bare microcontrollers is an explicit non-goal — the host does the thinking, the board does the I/O.

| Surface | What's there |
|---------|--------------|
| **Host targets** | macOS, Linux (x86_64 / arm64, incl. Raspberry Pi 3/4/5), Windows — one static binary with embedded dashboard |
| **Peripheral boards** | STM32 Nucleo, Arduino Uno / Uno Q, ESP32, Raspberry Pi Pico — firmware sources under `firmware/arduino/`, `firmware/esp32/`, `firmware/nucleo/`, `firmware/pico/`, `firmware/uno-q-bridge/` |
| **Peripheral runtime** | `src/peripherals/` — Arduino/Nucleo flashers, Uno Q bridge, RPi host, capabilities tool, shared serial transport, `Peripheral` trait |
| **Hardware adapters** | `src/hardware/` — Total Phase Aardvark I2C/SPI adapter (SDK-vendored), device discovery, GPIO, UF2/Pico flashing, board registry, datasheet introspection |
| **Crates** | `crates/aardvark-sys` (FFI bindings; SDK currently stubbed — vendor the Total Phase SDK to enable), `crates/robot-kit` (Pi 5-first robot hardware abstraction) |
| **Feature flags** | `--features hardware` for Aardvark/I2C/SPI; `--features peripheral-rpi` for Raspberry Pi GPIO (Linux only); `--features probe` for probe-rs on-chip debugging |

Agent tools expose these as LLM-callable surfaces, so a prompt like *"blink the ACT LED three times and then read the I2C sensor on 0x48"* dispatches through the same tool loop as a file edit. See [docs/hardware/](docs/hardware/) for per-board setup and the host-mediated vs. edge-native tier model.

---

## REST API

The gateway exposes 90+ REST endpoints and 5 WebSocket routes grouped by domain. All authenticated routes require a bearer token under `/api/`.

<details>
<summary>API endpoint groups (click to expand)</summary>

| Group | Key Endpoints |
|-------|--------------|
| **Auth / Pairing** | `POST /pair`, `GET /pair/code`, `POST /api/pairing/initiate`, `POST /api/pair`, `GET /api/devices`, `DELETE /api/devices/{id}`, `POST /api/devices/{id}/token/rotate` |
| **Admin** | `POST /admin/shutdown`, `GET /admin/paircode`, `POST /admin/paircode/new` (localhost-bound) |
| **Health** | `GET /health`, `GET /api/health`, `GET /api/status`, `GET /metrics` (Prometheus) |
| **Config** | `GET/PUT /api/config` (larger body limit on PUT) |
| **Tools** | `GET /api/tools` (agent tool specs), `GET /api/cli-tools` (discovered CLI binaries) |
| **Agents** | `GET/POST /api/agents`, `PUT/DELETE /api/agents/{*kref}`, `POST /api/agents/deprecate` |
| **Skills** | `GET/POST /api/skills`, `GET/PUT/DELETE /api/skills/{*kref}`, `POST /api/skills/deprecate` |
| **Teams** | `GET/POST /api/teams`, `GET/PUT/DELETE /api/teams/{*kref}`, `POST /api/teams/deprecate` |
| **Workflows** | `GET/POST /api/workflows`, `PUT/DELETE /api/workflows/{*kref}`, `POST /api/workflows/deprecate`, `POST /api/workflows/run/{name}`, `GET /api/workflows/revisions/{*kref}`, `GET /api/workflows/runs`, `GET/DELETE /api/workflows/runs/{run_id}`, `POST /api/workflows/runs/{run_id}/approve`, `POST /api/workflows/runs/{run_id}/retry`, `GET /api/workflows/agent-activity/{agent_id}`, `GET /api/workflows/dashboard` |
| **ClawHub** | `GET /api/clawhub/search`, `/trending`, `/skills/{slug}`, `POST /api/clawhub/install/{slug}` |
| **Sessions** | `GET /api/sessions`, `GET /api/sessions/running`, `GET /api/sessions/{id}/messages`, `GET /api/sessions/{id}/state`, `PUT/DELETE /api/sessions/{id}` |
| **Memory Graph** | `GET/POST/DELETE /api/memory` and memory-graph routes (60s timeout) |
| **Cron** | `GET/POST /api/cron`, update/delete by id, `GET /api/cron/{id}/runs`, settings |
| **Audit** | `GET /api/audit`, `GET /api/audit/verify` |
| **Cost** | `GET /api/cost` |
| **Canvas** | `GET /api/canvas`, `GET/POST/DELETE /api/canvas/{id}`, `GET /api/canvas/{id}/history`, WebSocket at `/ws/canvas/{id}` |
| **Nodes** | `GET /api/nodes`, `POST /api/nodes/{node_id}/invoke`, WebSocket at `/ws/nodes` |
| **Channels** | `GET /api/channels`, `POST /api/channel-events` |
| **Integrations** | `GET /api/integrations`, list/configure integration registry |
| **MCP** | `GET /api/mcp/discovery`, `GET /api/mcp/health`, `POST /api/mcp/servers/test`, `POST /api/mcp/session`, `POST /api/mcp/call`, WebSocket at `/ws/mcp/events` |
| **Kumiho Proxy** | `GET /api/kumiho/{*path}` — generic proxy to Kumiho FastAPI (Asset/Memory browsers use this) |
| **WebAuthn** | `POST /api/webauthn/{register,auth}/{start,finish}`, `GET/DELETE /api/webauthn/credentials[/{id}]` (feature: `webauthn`) |
| **Plugins** | `GET /api/plugins` (feature: `plugins-wasm`) |
| **Real-time** | SSE at `/api/events`, SSE at `/api/daemon/logs`, WebSockets at `/ws/chat`, `/ws/canvas/{id}`, `/ws/nodes`, `/ws/terminal`, `/ws/mcp/events` |
| **Webhooks** | `POST /webhook`, `POST /whatsapp` (+ `GET` verify), `POST /wati` (+ `GET` verify), `POST /linq`, `POST /nextcloud-talk`, `POST /webhook/gmail`, `POST /hooks/claude-code` |
| **Static / SPA** | `GET /_app/{*path}`, SPA fallback for non-API GETs |

</details>

---

## Quick Start

### Install (one command)

```bash
# macOS / Linux / WSL
curl -fsSL https://revka.ai/install.sh | bash
```

```powershell
# Windows (PowerShell)
irm https://revka.ai/install.ps1 | iex
```

On macOS / Linux / WSL the installer sets up `revka` (building from source, installing Rust via rustup if missing, or fetching a prebuilt binary when available) plus the Kumiho + Operator Python MCP sidecars under `~/.revka/`; on Windows it installs the prebuilt `revka.exe` (sidecars provision on first agent run). Both then run `revka onboard` for interactive provider + API-key setup and open the dashboard at `http://127.0.0.1:42617`.

> The installers are [`install.sh`](install.sh) and [`install.ps1`](install.ps1) — inspect them first if you like (`curl -fsSL https://revka.ai/install.sh | less`). To build from source, see [From source](#from-source-developers) below.

To fetch a prebuilt release binary instead of building from source — faster, and it includes the web dashboard without needing Node.js — add `--prefer-prebuilt`:

```bash
curl -fsSL https://revka.ai/install.sh | bash -s -- --prefer-prebuilt
```

Once installed:

```bash
revka gateway                  # start the HTTP gateway + dashboard
revka agent -m "Hello"         # one-shot message
revka status                   # health check
revka doctor                   # diagnose config / sidecar / channel issues
revka daemon                   # long-running autonomous runtime (service-managed)
```

### From source (developers)

```bash
cargo build --release --locked
./scripts/install-sidecars.sh      # or scripts\install-sidecars.bat on Windows
./target/release/revka onboard
./target/release/revka gateway
```

Add `--features channel-matrix,channel-lark,browser-native,hardware,rag-pdf,observability-otel` for the full build; see `Cargo.toml` for the full feature matrix.

### Prerequisites

- **Rust stable (1.89+)** — `install.sh` / `setup.bat` will install it via rustup if missing.
- **Python 3.11+** — required for the Kumiho and Operator Python MCP sidecars. On Debian/Ubuntu also install pip and venv: `sudo apt install -y python3-pip python3-venv`.
- **Node.js 18+ (20 LTS recommended)** — builds the embedded React dashboard on a source install. `install.sh` builds it automatically when Node is present (set `REVKA_BUILD_WEB=0` to skip); **without Node a source build is backend-only** — install Node and re-run, or use `--prefer-prebuilt` for a release binary with the dashboard already embedded. Manual rebuild: `cd web && npm ci && npm run build`. The dashboard is embedded into the Rust binary at compile time via `rust-embed`.
- **Kumiho endpoint** — an HTTP endpoint via `[kumiho].api_url` in `~/.revka/config.toml`. Two options: ① **Cloud** — default points at the **kumiho-FastAPI BFF** (`https://api.kumiho.cloud`), free tier 5,000 nodes, sign up at [kumiho.io](https://kumiho.io); ② **CE** — run [Kumiho CE](https://kumiho.io/en/resources/community-edition) on your own infra and point `api_url` at it (no node limits, no cloud dependency). Without a reachable endpoint Revka runs statelessly. See [docs/setup-guides/kumiho-operator-setup.md](docs/setup-guides/kumiho-operator-setup.md).
- **Disk / RAM** — source build needs ~6 GB free disk and ~2 GB free RAM; prebuilt binary is ~200 MB.

> **Sidecar re-install.** To re-run sidecar install independently of the main bootstrap:
>
> - `revka install --sidecars-only` — cross-platform; embedded in the `revka` binary, runs the right script for your OS. Use this once you have `revka` on PATH.
> - `./scripts/install-sidecars.sh` / `scripts\install-sidecars.bat` — same logic, straight from a source checkout.
>
> Both paths are idempotent — they never overwrite an existing `config.toml`, `.env`, or user-authored launcher.

### CLI Commands

Top-level `revka` subcommands (from `src/main.rs`). Full reference: [docs/reference/cli/commands-reference.md](docs/reference/cli/commands-reference.md).

| Command | Description |
|---------|-------------|
| `onboard` | Initialize workspace and configuration (interactive or `--quick`) |
| `agent` | Interactive agent loop or single-shot with `-m`, provider/model override, peripheral attach |
| `gateway` | Start / restart / inspect the HTTP + WebSocket gateway; `get-paircode` |
| `acp` | Start ACP (JSON-RPC 2.0 over stdio) server for IDE/tool integration |
| `daemon` | Long-running runtime: gateway + channels + heartbeat + scheduler |
| `service` | Install/manage OS service (launchd / systemd / OpenRC) |
| `doctor` | Diagnostics (models, traces, daemon/scheduler/channel freshness) |
| `status` | System status, with `--format exit-code` for Docker HEALTHCHECK |
| `estop` | Engage/resume emergency stop (network-kill, domain-block, tool-freeze) |
| `cron` | Manage scheduled tasks (cron/at/every/once, pause/resume/update) |
| `models` | Provider model catalog (refresh, list, set, status) |
| `providers` | List supported AI providers |
| `channel` | Manage channels (list, add, remove, send, doctor, bind) |
| `integrations` | Browse 50+ integrations |
| `skills` | Manage user-defined skills |
| `migrate` | Migrate data from other agent runtimes |
| `auth` | Provider subscription auth profiles (login, paste-token, refresh, logout, use, list, status) |
| `hardware` | Discover/introspect USB hardware (probe-rs, ST-Link) |
| `peripheral` | Add/list/flash/configure hardware peripherals |
| `memory` | Manage agent memory entries (list, get, stats, clear) |
| `config` | `schema` — dump full JSON Schema for the config file |
| `plugin` | Manage WASM plugins (feature: `plugins-wasm`) |
| `update` | Check/apply updates with 6-phase pipeline and auto-rollback |
| `self-test` | Run diagnostic self-tests (`--quick` for offline) |
| `completions` | Generate shell completion script (bash/zsh/fish/powershell/elvish) |
| `desktop` | Launch or `--install` the companion Tauri desktop app |

### Frontend Development

```bash
cd web
npm install
npm run dev          # Vite dev server with hot reload

# Production build (embedded into Rust binary)
npx vite build       # outputs to web/dist/
cd .. && cargo build --release  # re-embeds frontend via rust-embed
```

See [docs/setup-guides/dashboard-dev.md](docs/setup-guides/dashboard-dev.md) for the full frontend development guide.

## Configuration

Revka uses TOML configuration at `~/.revka/config.toml`:

```toml
default_provider = "anthropic"
api_key = "sk-ant-..."

[kumiho]
enabled = true
mcp_path = "~/.revka/kumiho/run_kumiho_mcp.py"
space_prefix = "Revka"
api_url = "https://api.kumiho.cloud"      # or your self-hosted URL on Enterprise

[operator]
enabled = true
mcp_path = "~/.revka/operator_mcp/run_operator_mcp.py"
```

See [`docs/reference/api/config-reference.md`](docs/reference/api/config-reference.md) for the full reference covering providers, channels, tools, security, and gateway settings.

## Tech Stack

| Layer | Technology |
|-------|------------|
| Runtime & Gateway | Rust (edition 2024), Axum + Tower, Hyper; embedded frontend via `rust-embed` |
| Agent Loop | Rust (async/tokio, 14+ provider adapters: Anthropic, OpenAI, OpenAI-Codex, Bedrock, Azure OpenAI, Gemini, Gemini CLI, GLM, Copilot, Ollama, OpenRouter, Kilo CLI, Telnyx, Claude Code, plus reliable/router/compatible wrappers). Operator's own orchestration calls (decompose / reduce / critic / moderate) use a provider via API key or OAuth under `[operator]`; agents Operator spawns invoke coding CLIs (Claude Code, Codex, agy, Cursor CLI, or OpenCode) as subprocesses, cleanly inheriting and accessing Revka-defined MCP servers. |
| Orchestration | Python 3.11+ Operator MCP server (20 step types, map-reduce / supervisor / group-chat / handoff / refinement patterns) |
| Web Dashboard | React 19, TypeScript, Tailwind CSS 4, Vite 6, ReactFlow + react-force-graph-2d |
| Memory Backend | Kumiho — graph-native, schemaless, version-controlled, typed-edge graph. Two deployment flavors: **Kumiho Cloud** (managed service via kumiho-FastAPI BFF at `api.kumiho.cloud`, free → paid tiers) and **Kumiho CE** (prebuilt binary, free download, self-host — [kumiho.io/en/resources/community-edition](https://kumiho.io/en/resources/community-edition)); Enterprise self-host also available. Python SDKs at [github.com/KumihoIO/kumiho-SDKs](https://github.com/KumihoIO/kumiho-SDKs) — `kumiho` (graph) + `kumiho-memory` (AI cognitive memory) |
| Local Storage | SQLite via `rusqlite` for cron store, pairing, heartbeat, channel sessions, WhatsApp cache (not for primary memory) |
| Desktop App | Tauri 2 companion under `apps/tauri` (system-tray / menu-bar) launched via `revka desktop` |
| Real-time | WebSocket (`/ws/chat`, `/ws/canvas/{id}`, `/ws/nodes`, `/ws/terminal`, `/ws/mcp/events`), SSE (`/api/events`, `/api/daemon/logs`) |
| Protocol | A2A (Google Agent-to-Agent, JSON-RPC 2.0), ACP (JSON-RPC 2.0 over stdio), MCP (server + clients) |
| Observability | OpenTelemetry, Prometheus `/metrics`, DORA metrics, runtime tracing |
| Security | Merkle hash-chain audit, 6-digit device pairing, optional WebAuthn, OTP, workspace boundary, prompt guard, secrets scanning, E-Stop, verifiable-intent receipts |
| Sandboxing | Native / Docker / WASM runtimes; Seatbelt (macOS), Landlock (Linux), Firejail, Bubblewrap wrappers |
| Tunnels | Cloudflare, ngrok, Pinggy, Tailscale, OpenVPN, custom |
| Firmware / Hardware | STM32 Nucleo, Arduino (Uno / Uno Q), ESP32, Raspberry Pi Pico, RPi host; Aardvark I2C/SPI adapter (see Hardware section) |

---

## Documentation

- **Documentation hub:** [`docs/README.md`](docs/README.md) — entry points by audience
- **Unified TOC:** [`docs/SUMMARY.md`](docs/SUMMARY.md)
- **Setup & onboarding:** [`docs/setup-guides/`](docs/setup-guides/) — one-click bootstrap, Kumiho/Operator sidecars, Windows, dashboard dev, channels
- **Reference (CLI / API / config):** [`docs/reference/`](docs/reference/)
- **Operations runbook:** [`docs/ops/`](docs/ops/)
- **Security:** [`docs/security/`](docs/security/) — sandboxing, audit logging, Matrix E2EE, agnostic security model
- **Hardware & peripherals:** [`docs/hardware/`](docs/hardware/)
- **Integration patterns:** [`docs/contributing/kumiho-memory-integration.md`](docs/contributing/kumiho-memory-integration.md)
- **Architecture (ADRs):** [`docs/architecture/`](docs/architecture/)
- **Localized hubs:** [한국어](docs/i18n/ko/README.md) · [Tiếng Việt](docs/i18n/vi/README.md) · [简体中文](docs/i18n/zh-CN/README.md)

## Related projects

- **Kumiho Cloud** — managed graph memory backend. Sign up and pricing at [kumiho.io/pricing](https://kumiho.io/pricing). SDKs at [github.com/KumihoIO/kumiho-SDKs](https://github.com/KumihoIO/kumiho-SDKs).
- **Kumiho CE** — Community Edition prebuilt binary, free to download and self-host. Same graph engine, no source included. [kumiho.io/en/resources/community-edition](https://kumiho.io/en/resources/community-edition).
- **ZeroClaw** — upstream Rust agent runtime that Revka's core forks from. [github.com/zeroclaw-labs/zeroclaw](https://github.com/zeroclaw-labs/zeroclaw).
- **OpenClaw** — separate TypeScript agent platform; Revka can import its data via `revka migrate`. [github.com/openclaw/openclaw](https://github.com/openclaw/openclaw).

## License

**Revka** (this repository) is dual-licensed at your option under:

- [MIT](LICENSE-MIT)
- [Apache License 2.0](LICENSE-APACHE)

Both licenses preserve the upstream `Copyright (c) 2025 ZeroClaw Labs` per fork-attribution requirements; see [`NOTICE`](NOTICE) and [`docs/upstream/zeroclaw-attribution.md`](docs/upstream/zeroclaw-attribution.md).

The **Kumiho server** (the control plane where Revka's memory lives) is *not* covered by these licenses. It ships in two flavors: **Kumiho CE** — a prebuilt binary, free to download and self-host ([kumiho.io/en/resources/community-edition](https://kumiho.io/en/resources/community-edition), source not included) — and the **managed cloud service** reached via the **kumiho-FastAPI BFF** at `api.kumiho.cloud`. The Kumiho **SDKs** are open source at [github.com/KumihoIO/kumiho-SDKs](https://github.com/KumihoIO/kumiho-SDKs). Enterprise self-host (closed-source, dedicated SLA) is also available — see [kumiho.io/pricing](https://kumiho.io/pricing).

Contributions to this repository are accepted under the dual license model; the Apache 2.0 patent grant protects all contributors. See [`docs/contributing/cla.md`](docs/contributing/cla.md).
