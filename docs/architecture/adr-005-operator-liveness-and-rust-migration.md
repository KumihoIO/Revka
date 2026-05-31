# ADR-005: Operator Agent Liveness & Rust Migration Plan

**Status:** Proposed

**Date:** 2026-04-15

## Context

The Construct operator orchestrates multi-agent workflows (e.g. quantum-soul
arc-room, episode-room) by spawning Claude and Codex agents through a
three-layer stack:

```
Python Operator (workflow executor, refinement, recovery)
    ↓ Unix socket HTTP
Node.js Session Manager (agent-manager.ts, claude/codex providers)
    ↓ Claude Agent SDK / Codex CLI subprocess
LLM Agent Processes (claude sessions, codex child processes)
```

This architecture has produced a class of **cross-boundary state bugs** where
the layers disagree about agent liveness:

1. **Zombie agents** — agent process dies or the operator daemon restarts, but
   the session manager keeps reporting `status: "running"`. The Python
   operator's `_wait_for_agent` polls the stale status for the full timeout
   (300–900s) or indefinitely if the poll loop was killed by a restart.

2. **Recovery blindness** — `recovery.py` runs on startup and finds interrupted
   runs, but `_wait_and_harvest` only waits 30s. The session manager still
   says `running` for dead agents, so recovery marks them as failed — but only
   after burning 30s per zombie agent.

3. **Spin loop** — `_sync_listen` returned void when a file lock was held,
   causing `_listen_loop` to spin at 100% CPU. This was a Python
   async/threading interaction bug.

4. **Tool surface mismatch** — sub-agents use `subagent_mcp.py` (7 tools)
   while the operator uses `operator_mcp.py` (40+ tools). When workflow
   tools were added to the operator, they weren't added to the sub-agent
   MCP, causing agents to waste 60+ tool calls searching for `run_workflow`.

5. **Empty output treated as success** — rate-limited agents returned empty
   output but the executor marked them `completed`, publishing entities with
   empty metadata downstream.

### Interim Fixes (2026-04-15)

- `_wait_for_agent` now has zombie detection: polls event count every 30s, and
  if no new events for 120s while sidecar reports `running`, declares the agent
  dead and cancels it.
- `_wait_and_harvest` early-exits on zero events (agent never started).
- `_sync_listen` returns `bool`, `_listen_loop` sleeps 60s on `False`.
- `_exec_agent` fails steps with empty output.
- Workflow tools added to `subagent_mcp.py`.

These are heuristic patches. The 120s zombie detection window means every
zombie agent wastes 2 minutes before being caught. The root cause — the
session manager not tracking process liveness — remains.

## Decision

Migrate agent lifecycle management from the Node.js session manager into the
Rust runtime in three phases. Keep the Python workflow executor for iteration
speed. The goal is to eliminate the cross-boundary state gap, not to rewrite
everything.

## Architecture After Migration

```
Python Operator (workflow executor, refinement, recovery)
    ↓ Unix socket HTTP (same API surface)
Rust construct daemon (agent lifecycle, liveness, events, process mgmt)
    ↓ Claude Agent SDK (via subprocess) / Codex CLI subprocess
LLM Agent Processes
```

The Python operator sees the same REST API (`/agents`, `/agents/:id`,
`/agents/:id/events`, etc.) but backed by the Rust process that owns the
child processes directly.

## Phases

### Phase 0: Add liveness to the Node.js session manager (quick win)

**Effort:** 1–2 days
**Risk:** Low — additive change, no migration

The session manager already holds `handle.process` (Codex `ChildProcess`) and
`handle.query` (Claude `AsyncGenerator`). Add heartbeat/liveness tracking:

**Codex (subprocess):**
```typescript
// In codex.ts — the ChildProcess has a .pid and emits 'exit'/'error'
// Already handled: proc.on('close') sets status to idle/error.
// Missing: if the process is killed externally (OOM, signal), the 'close'
// event fires but nobody checks. The session stays in sessions map as
// "running" if the status_changed event was lost.

// Fix: after proc.on('close'), also set a flag on the handle:
handle.processAlive = false;

// In getSessionInfo(), check:
if (session.status === "running" && handle.process === null) {
  session.status = "error"; // process died without clean status transition
}
```

**Claude (SDK query pump):**
```typescript
// The async pump resolves (via 'result' message) or throws.
// If it exits silently (SDK bug, network drop), the session stays "running".

// Fix: set a flag when pump exits for any reason:
handle.pumpAlive = false;

// In getSessionInfo(), check:
if (session.status === "running" && !handle.pumpAlive && handle.query === null) {
  session.status = "error";
}
```

**Both providers — last event timestamp:**
```typescript
interface ManagedSession {
  // ... existing fields ...
  lastEventAt: number;  // Date.now() at last event emission
}

// In onEvent handler:
session.lastEventAt = Date.now();

// Expose in getSessionInfo() → Python operator can use it instead of
// fetching full event list for zombie detection:
return {
  ...existing,
  lastEventAt: session.lastEventAt,
};
```

**Result:** Python operator's zombie detection becomes trivial — if
`lastEventAt` hasn't changed in 120s and status is `running`, it's dead. No
need to fetch and count events.

### Phase 1: Rust agent process manager

**Effort:** 1–2 weeks
**Risk:** Medium — new Rust module, existing API compatibility required

Create `src/agent/process_manager.rs` in the construct Rust codebase:

1. **Process spawning** — spawn Claude CLI and Codex CLI as child processes
   using `tokio::process::Command`. Track the `Child` handle and PID.

2. **Liveness via `waitpid`** — `tokio::process::Child` automatically notifies
   via `.wait()` when the process exits. No polling needed. When the child
   exits, immediately transition status to `idle` (exit 0) or `error`
   (non-zero / signal).

3. **Event stream** — read stdout/stderr via `tokio::io::BufReader` on the
   child's pipes. Parse Claude SDK JSON messages and Codex output into a
   unified `AgentEvent` enum. Store in a bounded `VecDeque<AgentEvent>` per
   agent.

4. **State machine** — `AgentStatus` enum with explicit transitions:
   ```rust
   enum AgentStatus {
       Initializing,
       Running { pid: u32, started_at: Instant },
       Idle { completed_at: Instant },
       Error { error: String, died_at: Instant },
       Closed,
   }
   ```
   The `Running` variant carries the PID. Transition to `Error` is automatic
   when the child process exits non-zero or is killed.

5. **HTTP API** — serve the same endpoints the Python operator expects:
   - `GET /agents` → list all agents with status
   - `GET /agents/:id` → single agent info (includes `lastEventAt`, PID)
   - `POST /agents` → create agent (spawn process)
   - `POST /agents/:id/query` → send follow-up prompt
   - `POST /agents/:id/interrupt` → SIGTERM the child
   - `DELETE /agents/:id` → close session
   - `GET /agents/:id/events?since=N` → event stream catchup
   - `GET /agents/:id/stream` → SSE live stream

6. **Integration point** — the `construct service` command already manages the
   Rust daemon. The agent process manager runs as a `tokio::task` inside the
   daemon, replacing the separate Node.js session manager process.

**Key design constraints:**
- The Rust process manager must handle the Claude Agent SDK's streaming
  protocol (JSON messages on stdout). This is currently done in `claude.ts`
  with `translateMessage()`. Port this logic to Rust.
- Codex is simpler — just stdout/stderr capture with exit code handling.
- MCP server injection for sub-agents (kumiho-memory, operator-tools) must
  be passed as CLI args or env vars to the spawned processes.

**Files involved:**
- New: `src/agent/process_manager.rs` — core process lifecycle
- New: `src/agent/providers/claude_cli.rs` — Claude SDK message parser
- New: `src/agent/providers/codex_cli.rs` — Codex output parser
- Modify: `src/gateway/mod.rs` — route agent API to new module
- Modify: `src/service.rs` — start process manager task on daemon boot
- Remove: dependency on `session-manager/` Node.js process

**What the Python operator gets:**
- Accurate status — no more `running` for dead agents
- `lastEventAt` timestamp — instant zombie detection without event counting
- PID in agent info — can verify process existence if needed
- Faster API — no Node.js ↔ Python ↔ Unix socket overhead; Rust HTTP directly

### Phase 2: Consolidate MCP tool surface

**Effort:** 3–5 days
**Risk:** Low — once Phase 1 is stable

Currently the tool surface is split across three files:
- `operator_mcp.py` — 40+ tools for the operator agent
- `subagent_mcp.py` — 12 tools for sub-agents
- `src/tools/delegate.rs` — native Rust delegate tool for direct CLI usage

After Phase 1, the Rust daemon owns agent lifecycle. Consolidate:

1. **Move agent lifecycle tools to Rust** — `create_agent`, `wait_for_agent`,
   `send_agent_prompt`, `get_agent_activity`, `list_agents` become Rust-native
   tools in `src/tools/operator.rs`, backed by the process manager from
   Phase 1. No more Python → Unix socket → Node.js round-trip.

2. **Keep workflow tools in Python** — `run_workflow`, `get_workflow_status`,
   `list_workflows` stay in Python because the executor is Python. But expose
   them as a single `operator-workflow` MCP server, not split across two
   files.

3. **Single MCP server for sub-agents** — sub-agents get the Rust-native agent
   lifecycle tools automatically (they're part of the daemon). The
   `subagent_mcp.py` shrinks to only workflow tools, or is eliminated entirely
   if workflow tools move to the Rust MCP.

4. **Unify `delegate.rs` and process manager** — the native Rust delegate tool
   and the operator's agent spawning use the same process manager. No more
   two separate agent management systems.

**Result:** One source of truth for what tools agents have access to. No more
"forgot to add `run_workflow` to `subagent_mcp.py`" class of bugs.

### Phase 3: Optional — Move workflow executor to Rust

**Effort:** 3–6 weeks
**Risk:** High — large rewrite, high iteration cost

This phase is **optional** and should only be attempted after Phase 1 and 2
are stable in production for several weeks.

**What moves:**
- Workflow YAML parsing and validation (`schema.py`, `validator.py`)
- Step execution and ordering (`executor.py`)
- Variable interpolation
- Checkpoint persistence
- Recovery logic (`recovery.py`)

**What stays in Python:**
- MCP server for Kumiho integration (unless Kumiho gets a Rust client)
- Any rapid-prototyping workflow features

**Trade-offs:**
| Factor | Python (current) | Rust (Phase 3) |
|--------|------------------|-----------------|
| Iteration speed | Seconds (restart daemon) | 14-min release build |
| Correctness guarantees | Runtime errors | Compile-time checks |
| Async reliability | asyncio gotchas (spin loops, task cancellation) | tokio is battle-tested |
| State management | Dict/class mutation, easy to leak | Ownership model prevents dangling state |
| Kumiho integration | Native Python SDK | HTTP client (extra dependency) |
| Debug cycle | print → restart → test | compile → restart → test |

**Recommendation:** Only do Phase 3 if the workflow executor is stable and
changes infrequently. If you're still iterating on workflow features (new step
types, new patterns), the Python iteration speed is worth more than Rust's
compile-time safety.

## Migration Path

```
Current State
├── Node.js session-manager (agent lifecycle)
├── Python operator_mcp.py (40+ tools)
├── Python subagent_mcp.py (12 tools)
├── Python executor.py (workflow engine)
└── Rust construct daemon (service mgmt, gateway, CLI tools)

Phase 0 (days) — Add liveness tracking to Node.js
├── Node.js session-manager + liveness flags + lastEventAt
├── Python operator reads lastEventAt (simplified zombie detection)
└── Everything else unchanged

Phase 1 (weeks) — Rust process manager
├── Rust construct daemon + process_manager.rs (agent lifecycle)
├── Remove Node.js session-manager
├── Python operator talks to Rust daemon (same API)
└── Python executor.py unchanged

Phase 2 (days) — Consolidate tools
├── Rust-native agent lifecycle tools
├── Single workflow MCP (Python, slim)
├── Sub-agents get full tool surface automatically
└── Remove subagent_mcp.py

Phase 3 (optional, weeks) — Rust executor
├── Rust workflow engine
├── Python only for Kumiho SDK glue
└── Single binary for everything
```

## What NOT to Do

- **Don't rewrite everything at once.** Each phase delivers independent value
  and can be deployed, tested, and rolled back independently.

- **Don't move Kumiho integration to Rust prematurely.** The Python SDK is
  mature and battle-tested. A Rust HTTP client would work but adds a
  maintenance surface for no immediate gain.

- **Don't eliminate Python entirely.** The workflow executor changes frequently
  as new step types and patterns are added. Python's iteration speed is a
  feature, not a bug, for this layer.

- **Don't over-engineer the Rust process manager.** It needs to spawn, monitor,
  and kill child processes, serve an HTTP API, and manage an event buffer.
  That's it. No workflow logic, no Kumiho integration, no MCP tool routing.

## Files Reference

### Current Stack (what exists today)

| File | Language | Role |
|------|----------|------|
| `~/.construct/operator/session-manager/src/agent-manager.ts` | TypeScript | Agent lifecycle, session state |
| `~/.construct/operator/session-manager/src/providers/claude.ts` | TypeScript | Claude SDK query pump |
| `~/.construct/operator/session-manager/src/providers/codex.ts` | TypeScript | Codex subprocess management |
| `~/.construct/operator/session-manager/src/persistence.ts` | TypeScript | Agent state persistence to disk |
| `~/.construct/operator/session-manager/src/event-emitter.ts` | TypeScript | SSE event broadcasting |
| `~/.construct/operator/operator_mcp.py` | Python | Full operator MCP (40+ tools) |
| `~/.construct/operator/subagent_mcp.py` | Python | Sub-agent MCP (12 tools) |
| `~/.construct/operator/workflow/executor.py` | Python | Workflow step execution |
| `~/.construct/operator/workflow/recovery.py` | Python | Interrupted run recovery |
| `~/.construct/operator/patterns/refinement.py` | Python | Agent spawn/wait/refinement loop |
| `~/.construct/operator/session_manager_client.py` | Python | HTTP client for session manager |
| `~/construct/src/tools/delegate.rs` | Rust | Native agent delegation tool |
| `~/construct/src/agent/loop_.rs` | Rust | Agent execution loop |
| `~/construct/src/agent/operator/mod.rs` | Rust | Operator MCP injection |
| `~/construct/src/gateway/mod.rs` | Rust | HTTP gateway routes |

### Phase 1 Target (new Rust files)

| File | Role |
|------|------|
| `src/agent/process_manager.rs` | Core process lifecycle and status tracking |
| `src/agent/providers/claude_cli.rs` | Claude SDK stdout message parser |
| `src/agent/providers/codex_cli.rs` | Codex stdout/stderr capture |
| `src/gateway/api_operator_agents.rs` | HTTP API for agent management |

## Success Criteria

- **Phase 0:** Zombie detection drops from 120s heuristic to instant (status
  is accurate at query time). No Python-side event count polling needed.

- **Phase 1:** `construct service restart` brings up all agent management. No
  separate Node.js process. Agent death is detected within 1 second via
  `waitpid`. The Python operator's `_wait_for_agent` can remove zombie
  detection logic entirely — it just trusts the status.

- **Phase 2:** Sub-agents have the same tool surface as the operator for
  agent lifecycle operations. No more "tool not found" waste. Workflow tools
  exposed via a single MCP server.

- **Phase 3:** (if pursued) Single `construct` binary handles everything.
  Daemon restart resumes workflows from checkpoint with zero external
  dependencies.
