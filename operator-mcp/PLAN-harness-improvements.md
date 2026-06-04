# Revka Harness Improvements — Adoption Plan

**Source:** Open Claude architecture study (2026-04-01)
**Repo:** `~/revka/operator-mcp`

---

## Phase A — Foundations (no dependencies between items)

### A1. Cache-Safe Prompt Parameters `P0`

- Add `CacheSafeParams` dataclass: frozen system prompt bytes + user context + tool context
- Snapshot after parent's first API turn, store on `ManagedAgent`
- Child agents receive parent's exact bytes via `_try_sidecar_create()` override
- Skip `build_system_prompt()` for children — use frozen copy

**Files:** `agent_state.py`, `mcp_injection.py`, `tool_handlers/agents.py`

### A2. Tool Allowlists Per Template `P1`

- Add `allowed_tools: list[str] | None` to `AgentTemplate` (None = full access)
- Add `max_turns: int` field (default 200)
- Pass to sidecar config and enforce in `subagent_mcp.py` tool dispatch
- Explore agent pattern: `["Read", "Grep", "Glob", "WebFetch"]`

**Files:** `agent_state.py`, `tool_handlers/agents.py`, `subagent_mcp.py`

### A3. Denial Tracking in Permission Flow `P1`

- Track `consecutive_denials` and `total_denials` per agent in sidecar
- After 3 consecutive or 20 total auto-denials, escalate to channel
- Reset consecutive count on any approval

**Files:** `session-manager/src/permission-handler.ts`

### A4. System Prompt Section Caching `P2`

- Split `build_system_prompt()` into named sections with `_cache` dict
- Volatile sections (time, git status) marked as cache-breakers
- Invalidate on session clear or compaction

**Files:** `mcp_injection.py`

---

## Phase B — Compaction & Memory (B1 before B2)

### B1. Structured Context Compaction → Kumiho `P0`

- Port Open Claude's 9-section compact prompt template
- Add `compact_conversation()` async function in new `operator/compaction.py`
- Strips `<analysis>` block, extracts `<summary>` content
- **Store compacted summary as Kumiho creative memory:**
  - Space: `CognitiveMemory/compactions/<session_id>`
  - Type: `summary` with tags `['compact', 'session-context', 'audit']`
  - Links `DERIVED_FROM` source session krefs
  - Title includes absolute date + session scope
- Expose as MCP tool: `compact_conversation` (manual trigger)
- Auto-trigger at configurable token threshold (default 150k)

**Files:** new `operator/compaction.py`, `operator_mcp.py`, new `tool_handlers/compact.py`

### B2. Session Memory via Background Agent → Kumiho `P2`

- Fork lightweight background agent on token + tool-call thresholds (10k init, 5k between updates, 3 tool calls)
- Agent extracts structured session state using fixed-section template
- **Registers to Kumiho via `kumiho_memory_reflect`** with captures:
  - `type: "summary"` — session state snapshot
  - `type: "decision"` — decisions made this session
  - `type: "fact"` — new facts discovered
  - Tags: `['session-memory', 'auto-extract', project_name]`
- No local markdown file — Kumiho is the single source of truth
- Uses `kumiho_memory_engage` before extraction to avoid duplicating existing memories

**Files:** new `operator/session_memory.py`, hooks in `operator_mcp.py`

---

## Phase C — Isolation & Communication

### C1. Git Worktree Isolation `P1`

- Add `create_worktree` / `destroy_worktree` functions in new `operator/worktree.py`
- Sidecar creates worktree per agent on `tool_create_agent` when `isolation: "worktree"`
- Cleanup on agent close (force remove worktree)
- Pass worktree path as agent's `cwd`

**Files:** new `operator/worktree.py`, `tool_handlers/agents.py`, `session-manager/src/agent-manager.ts`

### C2. Resolve-Once Permission Races `P2`

- Add atomic `claim()` pattern to `PermissionHandler`
- Race: policy auto-approve, channel response, timeout
- First to claim wins, others are no-ops

**Files:** `session-manager/src/permission-handler.ts`

### C3. File-Based Mailbox Fallback `P3`

- Add `~/.revka/operator_mcp/mailbox/{agent_id}.json` as disk persistence
- Write on chat post, read on sidecar restart
- Supplements in-memory chat rooms for durability

**Files:** new `session-manager/src/mailbox.ts`, `session-manager/src/chat-service.ts`

---

## Execution Order

```
Phase A (parallel, no deps):  A1 + A2 + A3 + A4
Phase B (sequential):         B1 → B2
Phase C (parallel after A):   C1 + C2 + C3
```

**Total: 9 work items across 3 phases.**
