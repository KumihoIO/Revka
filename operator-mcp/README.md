# Revka Operator MCP

The orchestration brain of Revka. A Python MCP server that manages agent
lifecycles, executes declarative YAML workflows, and coordinates multi-agent
patterns (refinement, map-reduce, supervisor, group-chat, handoff).

## Prerequisites

- Python 3.11+
- Node.js 18+ (for session-manager sidecar)
- A running Kumiho server (for memory/artifact persistence)

## Directory Layout

```
operator-mcp/
  operator/           # Python MCP server (the operator itself)
    workflow/           #   Workflow engine: schema, executor, validator, loader, memory
    tool_handlers/      #   89 MCP tool handlers grouped by domain
    patterns/           #   Multi-agent orchestration patterns
    operator_mcp.py    #   Main MCP server entry point
    run_operator_mcp.py #  Bootstrap script (dep installer + launcher)
  session-manager/     # Node.js sidecar — agent process manager + WS event relay
  skills/              # Orchestration skill definitions (.md)
  requirements.txt     # Python dependencies
  Makefile             # Build and install targets
```

## Build & Install

```bash
cd operator-mcp

# Full install (Python + session-manager + skills + bootstrap)
make install

# Just rebuild the TypeScript sidecar
make build-ts

# Dev mode (symlinks instead of copies — edits take effect immediately)
make dev-install
```

`make install` deploys everything to `~/.revka/operator_mcp/`:

| Component | Install path | Purpose |
|-----------|-------------|---------|
| Python operator | `~/.revka/operator_mcp/*.py` | MCP server + tool handlers |
| Session manager | `~/.revka/operator_mcp/session-manager/` | Agent process manager, WS event relay |
| Skills | `~/.revka/skills/` | Orchestration skill definitions |
| Bootstrap | `~/.revka/operator_mcp/run_operator_mcp.py` | Dep installer + MCP launcher |
| requirements.txt | `~/.revka/operator_mcp/requirements.txt` | Python deps (copied by bootstrap) |

## Runtime Architecture

```
Daemon (Rust binary)
  |
  |-- spawns per-agent-session -->  Operator MCP (Python, via run_operator_mcp.py)
  |                                   |
  |                                   |-- spawns --> Session Manager (Node.js sidecar)
  |                                   |                |
  |                                   |                |-- manages agent subprocesses
  |                                   |                |-- relays agent events to WS channel
  |                                   |
  |                                   |-- 89 MCP tools (agents, workflows, teams, etc.)
  |
  |-- WebSocket <-- agent events <-- Session Manager
```

Each agent chat session spawns a **fresh** operator MCP process. The Rust daemon
does NOT need a restart when operator code changes — just run `make install` and
start a new chat.

The daemon DOES need a restart when:
- Rust gateway code changes (recompile with `cargo build --release`)
- Frontend assets change (embedded in the binary)

## Key Gotchas

**`make install` is the canonical deployment.** Do not manually rsync or copy
individual files — the install has multiple components (Python, Node.js sidecar,
skills, bootstrap script, requirements.txt) that must all be in sync.

**Session manager is required for live execution view.** Without it, agents spawn
via direct subprocess and no events reach the WebSocket. The UI's live workflow
DAG overlay depends on the sidecar relaying `agent.started`, `agent.tool_use`,
`agent.completed` etc. events.

**requirements.txt must be present at `~/.revka/operator_mcp/requirements.txt`.**
The bootstrap script (`run_operator_mcp.py`) installs deps on first launch.
Missing requirements = operator crashes on startup = 0 operator tools available
to the agent.

## Workflow Engine

See [WORKFLOWS.md](../WORKFLOWS.md) for the full workflow authoring guide.

Quick reference:
- Workflow definitions: `~/.revka/workflows/*.yaml` (user), `operator/workflow/builtins/` (shipped)
- Executor: `operator/workflow/executor.py` — runs steps in topological order
- Validator: `operator/workflow/validator.py` — 6-pass validation before execution
- Loader: `operator/workflow/loader.py` — discovers YAML from builtin > user > project > Kumiho
- Memory: `operator/workflow/memory.py` — persists runs to Kumiho `/Revka/WorkflowRuns`

## Development

```bash
# Type-check the session-manager
make typecheck

# Clean build artifacts
make clean

# Run operator directly (for debugging)
cd ~/.revka/operator_mcp && python3 run_operator_mcp.py
```
