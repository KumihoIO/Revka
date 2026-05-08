# Kumiho Graph-Native Cognitive Memory Integration

**Audience:** Integration developers wiring Construct agents into Kumiho's persistent graph memory.

This document describes the integration patterns Construct uses on top of the **Kumiho MCP** sidecar — Construct's graph-native, cross-session memory backend. For installation and config, start at [`../setup-guides/kumiho-operator-setup.md`](../setup-guides/kumiho-operator-setup.md). For the `[kumiho]` config block, see [`../reference/api/config-reference.md`](../reference/api/config-reference.md).

---

## Why Kumiho memory matters

A stateless LLM call forgets everything once the response is sent. Kumiho gives every Construct agent four properties a stateless agent cannot have:

- **Cross-session continuity** — what an agent learned yesterday is still load-bearing today.
- **Graph-structured recall** — memories carry typed edges (`DERIVED_FROM`, `DEPENDS_ON`, `REFERENCED`, `CONTAINS`, `CREATED_FROM`, `BELONGS_TO`), so chain-of-decision queries traverse provenance instead of fuzzy-matching strings.
- **Provenance** — every capture can link back to the sources that produced it, so a downstream agent can audit how a fact was derived.
- **Shared substrate** — multiple agents on the same Kumiho control plane see each other's reflections, which is the foundation for handoff (`HANDED_OFF_TO`), supervisor delegation, and team memory.

Construct does not own this memory; the Kumiho control plane (`api.kumiho.cloud` by default) does. Construct talks to it through the **Kumiho MCP server** that runs as a sidecar of the Construct daemon.

---

## Prerequisites

1. The Kumiho MCP sidecar is installed and running. If you haven't, follow [`../setup-guides/kumiho-operator-setup.md`](../setup-guides/kumiho-operator-setup.md) — this puts a Python venv at `~/.construct/kumiho/venv/` and a runner shim at `~/.construct/kumiho/run_kumiho_mcp.py`.
2. `[kumiho]` is configured in `~/.construct/config.toml` with `api_url`, `auth_token` (or `KUMIHO_AUTH_TOKEN` env), and the desired `space_prefix` (Construct's default is `Construct`).
3. The Construct daemon has Kumiho MCP wired up — verify by querying the dashboard at `http://127.0.0.1:42617/tools` and confirming the `kumiho_memory_*` tools appear in the agent tool catalog.

If MCP tools don't appear, check `~/.construct/logs/` for the Kumiho MCP stderr trail. Most failures are missing `KUMIHO_AUTH_TOKEN` or an unreachable `api_url`.

---

## The two-reflex pattern

Almost every Construct agent loop follows the same shape: **engage** memory before responding, **reflect** after responding. This is the canonical integration pattern.

### Engage — before you respond

When the user's request touches anything that might have history, call `kumiho_memory_engage` exactly once. Derive the query from the user's current message (not from memory of what you've already searched).

```json
{
  "tool": "kumiho_memory_engage",
  "args": {
    "query": "<query derived from user's message>",
    "graph_augmented": false,
    "limit": 5,
    "space_paths": ["Construct/AgentPool", "CognitiveMemory/Skills"]
  }
}
```

The call returns:

- `context` — a pre-built natural-language summary suitable for prompt injection.
- `results` — the raw memories with metadata (titles, types, `created_at`).
- `source_krefs` — opaque identifiers you must pass to the matching `reflect` so newly captured memories link back to what inspired them.

Skip engage when the answer is already visible in the conversation — engaging unnecessarily wastes tokens and pollutes the graph with low-signal queries.

### Reflect — after you respond

After a substantive response, call `kumiho_memory_reflect` once with your response text and any structured captures you want preserved.

```json
{
  "tool": "kumiho_memory_reflect",
  "args": {
    "session_id": "<session id>",
    "response": "<your response text>",
    "captures": [
      {
        "type": "decision",
        "title": "Chose gRPC over REST on Apr 27",
        "content": "Picked gRPC for the operator <-> daemon channel because of bidirectional streaming requirements; REST was rejected as backpressure-blind."
      },
      {
        "type": "preference",
        "title": "Operator prefers concise daemon logs",
        "content": "Operator user wants daemon.log lines under 200 chars; verbose payloads should go to per-tool sidecar logs."
      }
    ],
    "source_krefs": ["<from engage results>"],
    "discover_edges": true
  }
}
```

A single `reflect` call does three things:

1. Buffers the assistant response for session continuity (so the next engage in the same session sees it).
2. Stores each capture as a graph memory with `DERIVED_FROM` edges to the supplied `source_krefs`.
3. Optionally discovers additional edges via the server-side LLM when `discover_edges: true` (default) — this finds non-obvious links to existing memories.

Skip captures for trivial exchanges; reflect with `captures: []` still buffers the response.

---

## Capture types

Pick the type that matches what you're storing. Type drives downstream retrieval ranking and DreamState consolidation behaviour.

| Type | When to use | Example title |
|---|---|---|
| `decision` | A choice with a rationale that should bind future agents. | "Chose Axum over Actix on Mar 14" |
| `preference` | Stable user/operator preference. | "User wants tests run before commits" |
| `fact` | Verified fact about the world or the system. | "Daemon binds 0.0.0.0:42617 in network mode" |
| `correction` | Overrides a prior memory that proved wrong. | "Operator does NOT auto-restart on panic (correction)" |
| `architecture` | High-level structural choice or constraint. | "Operator/Rust split per ADR-005 on Apr 18" |
| `implementation` | Concrete how-it-works knowledge. | "Skill kref resolution flow on Apr 20" |
| `synthesis` | Aggregated insight derived from multiple sources. | "Q1 channel error patterns rolled up Apr 27" |
| `reflection` | Post-mortem or lesson learned. | "Postmortem: stash overlap on Apr 27" |
| `summary` | Compacted session or thread summary. | "Apr 27 docs-restructure session summary" |
| `skill` | A reusable procedure to be discovered later. | "creative-memory" |

Always use **absolute dates** in titles (`"on Apr 27"`, not `"today"`). Memories outlive their session — relative time becomes meaningless on recall.

---

## Provenance edges

Provenance is what separates a graph memory from a flat note. Construct uses six edge types:

- `DERIVED_FROM` — implied automatically when you pass `source_krefs` to `reflect`. The new capture is "derived from" each kref.
- `DEPENDS_ON` — used by Construct plans where step B cannot start until step A completes. Set explicitly via `kumiho_create_edge`.
- `REFERENCED` — soft pointer; one memory mentions another without depending on it.
- `CONTAINS` — bundle/membership semantics; e.g., a session contains its captures.
- `CREATED_FROM` — used when one item is a forked or transformed version of another.
- `BELONGS_TO` — ownership or scope; ties an artifact to a project, workflow run, or agent.

When in doubt, let `discover_edges: true` on `reflect` infer the edges. Manually create edges only when you have semantic information the LLM can't infer (`DEPENDS_ON` ordering is the typical case).

---

## Space organisation

Kumiho organises memory into hierarchical **spaces**. Construct's prefix is `Construct/...`; the `CognitiveMemory/...` tree is shared across Construct and its sibling agents.

Conventions Construct relies on:

| Space | Purpose |
|---|---|
| `Construct/AgentPool` | Per-agent state — identity, expertise, tone, allowed tools. |
| `Construct/Plans/<project>` | Plans the Operator decomposed for a project. |
| `Construct/Sessions` | Active and historical chat sessions. |
| `Construct/Goals` | Long-running goals an agent is working toward. |
| `Construct/AgentTrust` | Per-agent trust scores and recent outcome buffer. |
| `Construct/ClawHub` | Marketplace catalog state (skills, agents, workflows). |
| `Construct/Teams` | Team composition and delegation topology. |
| `Construct/WorkflowRuns` | Operator workflow run state and history. |
| `Construct/Outcomes` | Per-run outcomes feeding the trust score calculation. |
| `CognitiveMemory/Skills` | Shared skill library (cross-agent procedures). |

When writing new captures, set `space_hint` on the capture itself, not just the top-level `space_path`, so per-capture space targeting wins over the bulk default.

---

## Skill discovery

Skills are reusable procedures stored in `CognitiveMemory/Skills`. Before improvising an unfamiliar procedure, search there first:

```json
{
  "tool": "kumiho_memory_engage",
  "args": {
    "query": "creative output tracking",
    "space_paths": ["CognitiveMemory/Skills"]
  }
}
```

If a skill matches, follow it; cache it in working context for the rest of the session. If no skill matches and you improvised a procedure, capture it back as a `skill`-type memory with `space_hint: "CognitiveMemory/Skills"` so DreamState can refine it overnight.

---

## Consolidation

After ~20 exchanges, or when the session ends, trigger consolidation:

```json
{
  "tool": "kumiho_memory_consolidate",
  "args": {
    "session_id": "<session id>"
  }
}
```

This compacts session-buffered responses into durable summaries, runs DreamState edge discovery on the session's new captures, and clears the session buffer. Construct's daemon does this automatically on session end, but long-running agents should self-trigger periodically.

---

## Edge cases and pitfalls

- **Stale memories.** A memory naming a file, function, or flag is a claim that the target existed when the memory was written. Before recommending an action based on a recalled memory, verify the target still exists. If it doesn't, treat the memory as stale and either `kumiho_deprecate_item` it or update it.
- **Absolute dates.** Always use absolute dates in capture titles. Relative time decays.
- **Kumiho is canonical.** Do not rely on Claude's auto-memory or any in-process state for cross-session knowledge. Kumiho MCP is the canonical store. If a feature in your code path looks like it's "remembering" something across sessions, it must reach Kumiho.
- **Explicit "remember this".** When the user says "remember this", "keep this in mind", "note that", or similar, you MUST capture via `reflect`. Never silently skip an explicit remember request.
- **Privacy.** Raw conversation transcripts stay local. The Kumiho control plane stores only summaries and structured captures. Never put credentials, secrets, or sensitive payloads into capture content.
- **One engage per turn.** The Kumiho MCP server enforces a 5-second deduplication window on engage calls. Calling engage twice in one turn either wastes a slot or returns cached results.
- **Silent degraded mode.** If the control plane is unreachable, Kumiho MCP fails closed — engage and reflect return errors, and the agent should continue without persistence rather than block. Always wrap engage/reflect in try/catch and log the error to `~/.construct/logs/` for operator review.

---

## Testing your integration

A four-step smoke test for any new Kumiho-aware agent path:

1. **Tools visible.** `GET /api/tools` returns at least `kumiho_memory_engage`, `kumiho_memory_reflect`, `kumiho_memory_recall`, `kumiho_memory_consolidate`. If not, check the daemon log line `Kumiho MCP script not found:` — the sidecar isn't running.
2. **Engage round-trip.** Call `kumiho_memory_engage` with a known query that should hit something the agent stored earlier; confirm `results` is non-empty.
3. **Reflect with captures.** Call `kumiho_memory_reflect` with one capture; verify the kref returns and that the capture appears in the dashboard's Memory view (`/memory`).
4. **Edge formation.** Confirm that running an engage → reflect pair produces a `DERIVED_FROM` edge from the new capture to the source krefs. Use `kumiho_get_edges` or the Memory force-graph to inspect.

If any step fails, the failure is almost always one of: missing auth token, unreachable control plane, MCP sidecar not started, or the daemon's `[kumiho]` config block disagreeing with the running sidecar's environment.

---

## Further reading

- [`../setup-guides/kumiho-operator-setup.md`](../setup-guides/kumiho-operator-setup.md) — install the Kumiho and Operator MCP sidecars.
- [`../reference/api/config-reference.md`](../reference/api/config-reference.md) — the `[kumiho]` and `[operator]` config blocks.
- [`../architecture/adr-005-operator-liveness-and-rust-migration.md`](../architecture/adr-005-operator-liveness-and-rust-migration.md) — proposed Rust migration of the Operator liveness layer.
- [`./custom-providers.md`](./custom-providers.md) — the neighbouring contributor pattern doc for plugging in new providers.
