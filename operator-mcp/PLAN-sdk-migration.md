# Construct Operator: Agent SDK Migration Plan

## Overview

Replace the current fire-and-forget `claude --print` subprocess model with
long-lived, multi-turn agent sessions using the **TypeScript Agent SDK**
(`@anthropic-ai/claude-agent-sdk` v0.2.x) via a Node.js session manager
sidecar — bringing Paseo-level orchestration to Construct.

The Python operator keeps its role as MCP tool dispatcher, Kumiho gRPC client,
and state manager. The TS sidecar handles only agent lifecycle, streaming, and
chat — the part where SDK maturity matters most.

### Why TypeScript SDK over Python SDK

| | TypeScript SDK (v0.2.89) | Python SDK (v0.1.53) |
|---|---|---|
| Maturity | 6+ months, battle-tested in Paseo | Alpha, weeks old |
| Streaming | Proven async generator model | Untested at scale |
| Session resume | Verified in production | Unverified |
| Hooks | Full coverage (permission, subagent, compact) | API exists but untested |
| Risk | Low — same ecosystem as claude-code itself | Medium — alpha bugs possible |

The Python SDK remains available as fallback (`claude-agent-sdk>=0.1.50` in
requirements.txt). If the TS sidecar is unavailable, the operator degrades
gracefully to Python SDK, then to subprocess.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ Construct Gateway (Rust)                                          │
│   ├── REST API, WebSocket, TLS, Auth                            │
│   ├── Channel Bridge (Slack, Discord, etc.)  ← agent events     │
│   └── SSE / WebSocket to Dashboard                              │
├─────────────────────────────────────────────────────────────────┤
│ Operator (Python, MCP stdio server)                            │
│   ├── Tool dispatch (31+ MCP tools)                             │
│   ├── Kumiho clients (gRPC via SDK, HTTP fallback)              │
│   ├── Agent state registry (AGENTS dict)                        │
│   ├── Session journal (NDJSON persistence)                      │
│   └── Gateway client (cost, status, canvas, nodes)              │
│         ↕ HTTP (localhost)                                      │
├─────────────────────────────────────────────────────────────────┤
│ Session Manager (TypeScript, Node.js sidecar)                   │
│   ├── ClaudeSDKClient sessions (long-lived, multi-turn)         │
│   ├── Codex AppServer sessions                                  │
│   ├── Google Agents CLI runs (agents-cli run)                   │
│   ├── Streaming timeline (NDJSON events)                        │
│   ├── Chat rooms (inter-agent coordination)                     │
│   ├── Permission flow (hooks → operator → user)                │
│   └── Event broadcast (→ gateway channels)                      │
│         ↕ Agent SDK / CLI subprocesses (claude/codex/agents-cli)│
├─────────────────────────────────────────────────────────────────┤
│ Agent Sessions (claude / codex / google_agents)                 │
│   ├── Injected MCP: kumiho-memory (gRPC → kumiho-server)        │
│   ├── Injected MCP: operator-tools (subset for sub-agents)     │
│   └── Injected MCP: channel-tools (post to channels)            │
└─────────────────────────────────────────────────────────────────┘
```

### Operator ↔ Session Manager Protocol

Local HTTP API on a Unix socket (`~/.construct/operator_mcp/session-manager.sock`):

```
POST   /agents              → create agent session
POST   /agents/:id/query    → send follow-up prompt
GET    /agents/:id/stream   → SSE stream of timeline events
POST   /agents/:id/interrupt → cancel in-flight query
DELETE /agents/:id          → disconnect and close
GET    /agents              → list active sessions
POST   /agents/:id/fork     → fork conversation branch

POST   /chat/rooms          → create chat room
POST   /chat/rooms/:id/post → post message
GET    /chat/rooms/:id      → read messages
GET    /chat/rooms          → list rooms
```

---

## Operator Modularization (Phase 0)

**Goal:** Split the 3400-line monolith before adding new capabilities.

### New module structure

```
~/.construct/operator_mcp/
├── operator_mcp.py          # MCP server entry, tool dispatch (slim ~400 lines)
├── kumiho_clients.py          # KumihoSDKClient, AgentPoolClient, TeamClient
├── agent_state.py             # ManagedAgent, SDKManagedAgent, AgentPool, timeline
├── agent_subprocess.py        # Legacy subprocess spawn/monitor (codex, fallback)
├── session_manager_client.py  # HTTP client to TS sidecar
├── chat_service.py            # ChatRoom, ChatMessage, mention routing
├── gateway_client.py          # ConstructGatewayClient (cost, status, canvas, nodes)
├── journal.py                 # SessionJournal (NDJSON persistence)
├── tool_handlers/
│   ├── agents.py              # create_agent, wait, send, list, activity
│   ├── teams.py               # create_team, spawn_team, list, get, search
│   ├── pool.py                # search_pool, save_template, list_templates
│   ├── planning.py            # save_plan, recall_plans, goals
│   ├── trust.py               # record_outcome, get_trust
│   ├── skills.py              # capture_skill
│   ├── clawhub.py             # search, install, browse
│   ├── chat.py                # chat_create, post, read, list, wait
│   ├── canvas.py              # render, clear
│   ├── session.py             # history, archive
│   └── nodes.py               # list_nodes, invoke_node
├── requirements.txt
├── run_operator_mcp.py       # Venv bootstrap
├── PLAN-sdk-migration.md
└── session-manager/           # TypeScript sidecar (Phase 1)
    ├── package.json
    ├── tsconfig.json
    ├── src/
    │   ├── index.ts           # HTTP server entry
    │   ├── agent-manager.ts   # Session lifecycle, streaming
    │   ├── chat-service.ts    # Chat rooms, mentions
    │   ├── event-emitter.ts   # Broadcast to operator/gateway
    │   └── providers/
    │       ├── claude.ts      # ClaudeSDKClient wrapper
    │       ├── codex.ts       # Codex AppServer wrapper
    │       └── google-agents.ts # Google Agents CLI subprocess wrapper
    └── node_modules/
```

---

## Phase 1 — Session Manager Sidecar (TypeScript)

**Goal:** Node.js process managing agent SDK sessions, started by the operator.

### 1.1 Sidecar lifecycle

The operator starts the session manager as a child process on first agent
spawn. Communicates via Unix socket HTTP. If sidecar dies, operator detects
via health check and restarts it.

```python
# In operator_mcp.py
async def _ensure_session_manager():
    if SESSION_MANAGER.is_alive():
        return
    proc = await asyncio.create_subprocess_exec(
        "node", str(SIDECAR_DIR / "dist/index.js"),
        "--socket", str(SOCKET_PATH),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await _wait_for_socket(SOCKET_PATH, timeout=10)
```

### 1.2 Agent session creation

```typescript
// session-manager/src/agent-manager.ts
async createAgent(config: {
    cwd: string;
    agentType: 'claude' | 'codex' | 'google_agents';
    prompt: string;
    systemPrompt?: string;
    model?: string;
    maxTurns?: number;
    maxBudgetUsd?: number;
    mcpServers?: Record<string, McpServerConfig>;
    parentId?: string;
    env?: Record<string, string>;
}): Promise<AgentSession> {
    const options: ClaudeAgentOptions = {
        cwd: config.cwd,
        permission_mode: 'bypassPermissions',
        mcp_servers: {
            'kumiho-memory': kumihoMemoryConfig(),
            'operator-tools': operatorToolsConfig(),
            ...config.mcpServers,
        },
        system_prompt: config.systemPrompt,
        max_turns: config.maxTurns ?? 50,
        max_budget_usd: config.maxBudgetUsd ?? 5.0,
        include_partial_messages: true,
        model: config.model,
        env: config.env,
    };
    const client = new ClaudeSDKClient(options);
    await client.connect(config.prompt);
    // Start consuming messages...
}
```

### 1.3 Multi-turn follow-up

```typescript
async sendQuery(agentId: string, prompt: string): Promise<void> {
    const session = this.sessions.get(agentId);
    if (!session || session.status !== 'idle') throw new Error('Agent not idle');
    session.status = 'running';
    await session.client.query(prompt);
    // Stream consumer picks up new messages automatically
}
```

### 1.4 Streaming timeline via SSE

```typescript
// GET /agents/:id/stream
app.get('/agents/:id/stream', (req, res) => {
    res.setHeader('Content-Type', 'text/event-stream');
    const unsub = agentManager.subscribe(req.params.id, (event) => {
        res.write(`data: ${JSON.stringify(event)}\n\n`);
    });
    req.on('close', unsub);
});
```

### 1.5 Fallback chain

```
1. Try TS sidecar (ClaudeSDKClient via HTTP API)
2. If sidecar unavailable → try Python SDK (ClaudeSDKClient in-process)
3. If Python SDK unavailable → subprocess (claude --print, current behavior)
```

### 1.6 SDK options reference

| Option | Purpose | Default |
|--------|---------|---------|
| `max_turns` | Cap agentic depth | 50 |
| `max_budget_usd` | Per-agent cost limit | 5.0 |
| `permission_mode` | "bypassPermissions" for automated agents | — |
| `include_partial_messages` | Stream tool_use/text events | true |
| `session_id` | Resume session across restarts | auto |
| `fork_session` | Branch conversation for parallel exploration | false |
| `model` | Override model (opus/sonnet/haiku) | inherited |
| `effort` | Reasoning effort (low/medium/high/max) | null |
| `task_budget` | Token/cost budget per task | null |
| `hooks` | Permission, subagent, compact hooks | Phase 6 |

### 1.7 Exit code reference (from claude-code source)

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Error (validation, permission, execution) |
| 129 | SIGHUP (terminal closed) |
| 143 | SIGTERM |
| Negative | Killed by signal (abs value = signal number) |

---

## Phase 2 — Streaming Timeline & Channel Events

**Goal:** Structured event log with broadcast to Construct channels.

### 2.1 Timeline event model

```typescript
interface TimelineEvent {
    seq: number;
    timestamp: string;              // ISO 8601
    agentId: string;
    eventType: string;              // assistant_message | tool_use | tool_result |
                                    // error | completed | rate_limited
    content: Record<string, any>;   // Type-specific payload
    epoch: number;                  // Increments per query() call
}
```

### 2.2 SDK message type mapping

| SDK Type | Timeline eventType | Content |
|----------|-------------------|---------|
| `AssistantMessage` | `assistant_message` | text blocks, thinking blocks |
| `StreamEvent` (tool_use) | `tool_use` | tool name, input args |
| `StreamEvent` (tool_result) | `tool_result` | output, is_error |
| `ResultMessage` | `completed` | is_error, usage stats, cost |
| `RateLimitEvent` | `rate_limited` | type, retry_after |
| `SystemMessage` | `system` | content |

### 2.3 Channel integration

Timeline events flow: Session Manager → Operator → Gateway → Channels

```
Agent completes task
  → ResultMessage in SSE stream
  → Operator receives via session_manager_client
  → Operator pushes to gateway WebSocket
  → Gateway forwards to channel bridge
  → User sees: "🔧 agent coder-Rusty completed: 3 files changed, 47 lines"
```

**Channel event types:**
- `agent.started` — agent spawned with task summary
- `agent.tool_use` — significant tool calls (file edits, not reads)
- `agent.completed` — final result with usage stats
- `agent.error` — failure with stderr excerpt
- `agent.permission` — permission request needing user approval
- `team.progress` — periodic team status rollup
- `chat.mention` — agent-to-agent mention (visible to user)

**User interaction via channels:**
- Reply to permission request → approve/deny
- Send message to agent → `send_agent_prompt` via operator
- `/status` command → list active agents and progress
- `/stop <agent>` → interrupt agent

### 2.4 Updated operator tools

- `get_agent_activity` → return curated timeline summary from sidecar
- `wait_for_agent` → subscribe to SSE, wait for `completed` event
- Timeline capped at 200 events per agent

---

## Phase 3 — MCP Injection (Hierarchical Spawning)

**Goal:** Sub-agents can spawn their own sub-agents and access memory.

### 3.1 Kumiho-memory MCP injection

```typescript
const kumihoMemoryConfig: McpStdioServerConfig = {
    type: 'stdio',
    command: path.join(os.homedir(), '.kumiho/venv/bin/python3'),
    args: [path.join(os.homedir(),
        '.construct/workspace/kumiho-plugins/claude/scripts/run_kumiho_mcp.py')],
    env: {
        CLAUDE_PLUGIN_ROOT: path.join(os.homedir(),
            '.construct/workspace/kumiho-plugins/claude'),
        KUMIHO_AUTO_CONFIGURE: '1',
    },
};
```

### 3.2 Operator-tools MCP (subset for sub-agents)

Lightweight MCP server exposing only:
- `create_agent` — spawn child agents (hierarchical)
- `wait_for_agent` — block until child finishes
- `send_agent_prompt` — follow-up to child
- `get_agent_activity` — check child output
- `list_agents` — see siblings
- `chat_post` / `chat_read` — inter-agent communication (Phase 4)

Excluded from sub-agents: `delete`, `archive`, `budget`, `goal`, `clawhub`,
`canvas`, `node` tools (operator-only).

### 3.3 System prompt layering

**Top-level agents** (no parent_id):
1. Operator prompt (plan, delegate, synthesize — from Paseo)
2. Kumiho memory bootstrap (engage/reflect protocol)
3. User-provided task prompt

**Sub-agents** (has parent_id):
1. Kumiho memory bootstrap
2. Role identity (from template/team member)
3. Task prompt from parent

---

## Phase 4 — Inter-Agent Communication (Chat Rooms)

**Goal:** Async coordination via persistent chat rooms (Paseo pattern).

### 4.1 Chat service (in TS sidecar)

```typescript
interface ChatRoom {
    id: string;
    name: string;
    purpose: string;
    messages: ChatMessage[];
    createdAt: Date;
}

interface ChatMessage {
    id: string;
    senderId: string;       // Agent ID
    senderName: string;
    content: string;
    mentions: string[];     // Agent IDs for active notification
    replyTo?: string;
    timestamp: Date;
}
```

### 4.2 New MCP tools (exposed via operator)

| Tool | Description |
|------|-------------|
| `chat_create` | Create named room with purpose |
| `chat_post` | Post message, optionally @mention agents |
| `chat_read` | Read messages (bounded: `--limit N`) |
| `chat_list` | List active rooms |
| `chat_wait` | Block until new message in room |
| `chat_delete` | Delete room |

### 4.3 Mention-based interrupts

When agent A posts with `mentions=[agent_B_id]`:
- If B is idle → inject system message with chat content, trigger new turn
- If B is running → queue notification for next idle state
- Mentions are active interrupts — use sparingly (per Paseo skill guidance)

### 4.4 Channel visibility

Chat room activity is forwarded to Construct channels:
- Users can see agent-to-agent coordination in real-time
- Users can post to chat rooms via channel commands
- Enables human-in-the-loop team collaboration

### 4.5 Persistence

In-memory during session. Optionally persist to Kumiho
(`Construct/ChatRooms/`) for cross-session continuity.

---

## Phase 5 — Orchestration Skills

**Goal:** Port Paseo orchestration patterns as Construct skills.

### 5.1 Skills to port

| Paseo Skill | Construct Equivalent | Key Pattern |
|-------------|-------------------|-------------|
| `paseo-orchestrator` | `operator-orchestrator` | Chat-room-centric team coordination |
| `paseo-loop` | `operator-loop` | Worker/verifier iterative cycles |
| `paseo-committee` | `operator-committee` | Dual high-reasoning agents plan, coders execute |
| `paseo-handoff` | `operator-handoff` | Full-context task transfer between agents |
| `paseo-chat` | `operator-chat` | Async coordination protocol |

### 5.2 Adaptation notes

- Replace `paseo run/send/wait` CLI refs → operator MCP tool calls
- Replace `paseo chat` CLI → `chat_create/post/read` tools
- Keep cross-provider review: Codex implements → Claude reviews
- Keep provider strength guidance: Codex = methodical, Claude = fast tool use
- Leverage Construct-specific features: agent pool, trust scores, budget governance
- Skills must work across channels (Slack, Discord, dashboard) — no CLI assumptions

### 5.3 Delivery

Save as skills in `CognitiveMemory/Skills` via `capture_skill` tool, and
optionally install via `~/.construct/skills/` directory.

---

## Phase 6 — Permission Flow

**Goal:** Sub-agent permission requests flow to operator → channels → user.

### 6.1 SDK hooks (in TS sidecar)

```typescript
hooks: {
    PermissionRequest: [{ callback: handlePermission }],
    SubagentStart: [{ callback: onSubagentStart }],
    SubagentStop: [{ callback: onSubagentStop }],
    PreCompact: [{ callback: onPreCompact }],
}
```

### 6.2 Auto-approve policy

| Operation | Policy |
|-----------|--------|
| Read-only (file read, grep, glob) | Auto-approve |
| File edits in cwd | Auto-approve for coder role |
| Bash commands | Auto-approve if no network/destructive |
| Network / external API | Escalate to user via channel |
| MCP tool calls | Approve by default |

### 6.3 Channel-based approval

Permission requests that need user input:
1. Session manager emits `permission_request` event
2. Operator forwards to gateway
3. Gateway sends to active channel (Slack, Discord, dashboard)
4. User replies with approve/deny
5. Response routes back: channel → gateway → operator → sidecar → agent

### 6.4 New MCP tools

- `list_pending_permissions()` — all pending across agents
- `respond_to_permission(agent_id, request_id, action)` — allow/deny

---

## Phase 7 — Session Persistence & Resume

**Goal:** Agents survive operator/sidecar restarts.

### 7.1 Agent state file

`~/.construct/operator_mcp/agents/{agent_id}.json`:
```json
{
    "id": "...",
    "title": "...",
    "cwd": "...",
    "agent_type": "claude",
    "session_id": "...",
    "status": "idle",
    "parent_id": null,
    "timeline_tail": [],
    "token_usage": {},
    "created_at": "...",
    "last_activity": "..."
}
```

### 7.2 Resume on sidecar startup

```typescript
async resumeAgents(): Promise<void> {
    const stateDir = path.join(OPERATOR_HOME, 'agents');
    for (const file of fs.readdirSync(stateDir)) {
        const state = JSON.parse(fs.readFileSync(path.join(stateDir, file), 'utf-8'));
        if (['running', 'idle'].includes(state.status)) {
            const options = { ...baseOptions, resume: state.session_id };
            const client = new ClaudeSDKClient(options);
            await client.connect();
            this.sessions.set(state.id, { client, ...state });
        }
    }
}
```

### 7.3 fork_session for parallel exploration

Use `fork_session: true` when branching an agent's conversation for parallel
hypotheses — original session stays intact, fork gets new session_id.

---

## Execution Order

```
Phase 0 (Modularize operator)     ← Prep — split monolith into modules
    ↓
Phase 1 (TS sidecar + SDK)        ← Foundation — everything depends on this
    ↓
Phase 2 (Timeline + channels)     ← Needs Phase 1 for streaming + gateway bridge
    ↓
Phase 3 (MCP injection)           ← Needs Phase 1 for agent options
    ↓
Phase 4 (Chat rooms)              ← Can parallel with Phase 3
    ↓
Phase 5 (Skills)                   ← Needs Phase 3 + 4 for tool references
    ↓
Phase 6 (Permissions)              ← Needs Phase 1 + 3 for hooks + channel flow
    ↓
Phase 7 (Persistence)              ← Last — needs stable agent model
```

### Milestones

**M1 — Phase 0:** Modularized operator, no behavior change. Confidence test:
all 31 existing tools still work.

**M2 — Phases 1-2:** SDK sessions + streaming + channel events. Confidence
test: spawn a Claude agent, send follow-up, see timeline events in dashboard
and Slack channel.

**M3 — Phases 3-4:** Hierarchical spawning + chat rooms. Confidence test:
top-level agent spawns sub-agent via MCP, sub-agents coordinate via chat room.

**M4 — Phases 5-7:** Full orchestration. Confidence test: deploy a team,
agents coordinate via chat, user approves permission via Slack, results
synthesized and reported.

### Effort estimates

| Phase | New code | Complexity | Dependencies |
|-------|----------|-----------|--------------|
| Phase 0 | ~0 (refactor) | Low | None |
| Phase 1 | ~600 TS + ~200 Python | High | Node.js, Agent SDK |
| Phase 2 | ~300 TS + ~150 Python | Medium | Phase 1, gateway WS |
| Phase 3 | ~200 TS + ~100 Python | Medium | Phase 1 |
| Phase 4 | ~400 TS + ~150 Python | Medium | Phase 1 |
| Phase 5 | ~500 (skill markdown) | Low | Phase 3+4 |
| Phase 6 | ~300 TS + ~100 Python | Medium | Phase 1+3, channels |
| Phase 7 | ~200 TS + ~50 Python | Low | Phase 1 |

---

## Design Decisions & Trade-offs

### Why TS sidecar instead of pure Python

The Python Agent SDK (v0.1.x alpha) is a risk for the core agent lifecycle
path. The TS SDK (v0.2.x) is proven in Paseo and shares the same ecosystem
as claude-code. The sidecar adds a process boundary but gains:
- Battle-tested streaming and session management
- Direct reuse of Paseo patterns (agent-manager.ts, chat-service.ts)
- Same SDK that claude-code uses internally
- Python SDK remains as fallback if sidecar is unavailable

### Why not port everything to TypeScript

Construct's operator has deep Python integrations:
- Kumiho gRPC via `kumiho` Python SDK (not available in JS)
- 31 MCP tools with complex Kumiho state management
- MCP server protocol via `mcp` Python library

Rewriting all of this in TypeScript would be a multi-week effort with no
user-visible benefit. The hybrid approach leverages each language's strengths.

### Why channels matter from Phase 2

Agents running in the background are invisible without channels. Users need:
- Real-time visibility into what agents are doing
- Ability to approve/deny from wherever they are (phone, Slack, dashboard)
- Team progress without opening the dashboard
- Human-in-the-loop collaboration with agent teams

Building channel support into the event model from the start avoids a
painful retrofit later.

---

## References

- TypeScript Agent SDK: `@anthropic-ai/claude-agent-sdk` v0.2.89
- Python Agent SDK: `claude-agent-sdk` v0.1.53 (fallback)
- Paseo source: `~/.construct/workspace/paseo/packages/server/src/server/agent/`
- Claude Code source: `~/git/claude-code/src/`
- Current operator: `~/.construct/operator_mcp/operator_mcp.py` (3,422 lines, 31 tools)
- SDK protocol: NDJSON over stdio (`--output-format=stream-json`)
- Channel bridge: `construct/src/gateway/channels/`
