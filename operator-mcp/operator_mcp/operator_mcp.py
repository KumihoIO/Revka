#!/usr/bin/env python3
"""Construct Operator MCP Server — manages agent subprocesses.

This is the slim entry point: MCP server setup, tool catalogue, and dispatch.
All implementation logic lives in sibling modules:
  - kumiho_clients.py   — KumihoSDKClient, AgentPoolClient, TeamClient
  - gateway_client.py   — ConstructGatewayClient
  - journal.py          — SessionJournal
  - agent_state.py      — ManagedAgent, AgentTemplate, AgentPool
  - agent_subprocess.py — subprocess spawn/monitor
  - tool_handlers/      — one module per tool group
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from itertools import count as _count
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from ._log import _log
from .construct_config import (
    memory_min_relevance_score,
    memory_project,
    memory_retrieval_limit,
)
from .kumiho_clients import KumihoAgentPoolClient, KumihoSDKClient, KumihoTeamClient
from .gateway_client import ConstructGatewayClient
from .journal import SessionJournal
from .session_manager_client import SessionManagerClient
from .event_consumer import EventConsumer
from .workflow_context import WorkflowContext
from .workflow.event_listener import WorkflowEventListener, get_trigger_registry, set_event_listener
from .workflow.loader import build_trigger_registry

# -- Global singleton instances (initialised once at module load) -----------

KUMIHO_SDK = KumihoSDKClient()
KUMIHO_POOL = KumihoAgentPoolClient()
KUMIHO_TEAMS = KumihoTeamClient()
CONSTRUCT_GW = ConstructGatewayClient()
JOURNAL = SessionJournal()
SIDECAR = SessionManagerClient()
EVENT_CONSUMER = EventConsumer(SIDECAR, CONSTRUCT_GW)
WORKFLOW_CTX = WorkflowContext(JOURNAL.session_id)


# ---------------------------------------------------------------------------
# MCP Server definition
# ---------------------------------------------------------------------------

app = Server("construct-operator")


# -- Tool catalogue --------------------------------------------------------

@app.list_tools()
async def list_tools() -> list[Tool]:
    configured_memory_limit = memory_retrieval_limit()
    configured_memory_min_score = memory_min_relevance_score()
    return [
        Tool(
            name="create_agent",
            description="Spawn a new agent subprocess (claude or codex CLI). Optionally use a template from the agent pool.",
            inputSchema={
                "type": "object",
                "properties": {
                    "cwd": {
                        "type": "string",
                        "description": "Working directory for the agent. Use an absolute path under your project root or workspace (e.g. ~/.construct/workspace, ~/code/myproject). The handler will accept the template's default_cwd when omitted, but the LLM client schema requires you to pass it explicitly.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Short title for this agent (max 60 chars).",
                        "maxLength": 60,
                    },
                    "agent_type": {
                        "type": "string",
                        "description": "CLI to use. Overrides template agent_type if provided.",
                        "enum": ["claude", "codex"],
                    },
                    "initial_prompt": {
                        "type": "string",
                        "description": "Prompt to send on creation.",
                    },
                    "template": {
                        "type": "string",
                        "description": "Template name from the agent pool. If provided, inherits agent_type, default_cwd, and system_hint.",
                    },
                    "model": {
                        "type": "string",
                        "description": "Claude model to use (e.g. 'claude-opus-4-6', 'claude-sonnet-4-6', 'claude-haiku-4-5'). Overrides template model if provided.",
                    },
                    "allowed_tools": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional tool allowlist. Only these tools will be available to the agent. If omitted, all tools are allowed.",
                    },
                    "max_turns": {
                        "type": "integer",
                        "description": "Max conversation turns before auto-stop (default 200).",
                        "default": 200,
                    },
                    "parent_id": {
                        "type": "string",
                        "description": "Parent agent ID for hierarchical spawning. Enables cache-safe prompt reuse.",
                    },
                    "clean_build": {
                        "type": "boolean",
                        "description": "If true, use sanitized environment (strict var allowlist, clean build caches). Recommended for build/test agents.",
                        "default": False,
                    },
                    "node_env": {
                        "type": "string",
                        "description": "NODE_ENV value for the agent process. Default: 'development'.",
                        "default": "development",
                    },
                },
                "required": ["title", "cwd"],
            },
        ),
        Tool(
            name="wait_for_agent",
            description="Wait for an agent to finish. Returns consistent status with metadata (sidecar_id, backend, usage, files_touched, etc.).",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "The agent UUID.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Max wait time in seconds. Default 120. Most agents finish within 120s. For agents doing 30+ tool calls, use 180.",
                    },
                },
                "required": ["agent_id"],
            },
        ),
        Tool(
            name="send_agent_prompt",
            description="Send a follow-up prompt to an idle/completed agent.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "The agent UUID.",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "The prompt to send.",
                    },
                },
                "required": ["agent_id", "prompt"],
            },
        ),
        Tool(
            name="get_agent_activity",
            description="Get structured activity summary for an agent: status, tool call count, error count, files touched, last failing command, last message, usage stats.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "The agent UUID.",
                    },
                },
                "required": ["agent_id"],
            },
        ),
        Tool(
            name="get_agent_run_log",
            description="Query detailed structured run log for an agent. Views: 'summary', 'tool_calls', 'errors', 'files', 'full'. Works for running and completed agents.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "The agent UUID.",
                    },
                    "view": {
                        "type": "string",
                        "enum": ["summary", "tool_calls", "errors", "files", "full"],
                        "description": "What to return. Default: summary.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max entries for tool_calls/full views. Default: 50.",
                    },
                },
                "required": ["agent_id"],
            },
        ),
        Tool(
            name="list_run_logs",
            description="List all available agent run logs on disk.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="get_circuit_breaker_status",
            description="Return circuit breaker status for the sidecar connection. Shows state (closed/open/half_open), failure count, and recovery timing.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="reset_circuit_breaker",
            description="Force-reset the sidecar circuit breaker to CLOSED. Use after manually confirming the sidecar is healthy.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="get_retry_queue_status",
            description="Return retry queue status: pending operations, success/failure counts, and per-op details.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="get_agent_health",
            description="Get heartbeat/liveness info for agents. Detects stuck agents with no activity. Call with agent_id for one agent, or empty for all.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "Agent ID to check. Omit to get all agents' health.",
                    },
                },
            },
        ),
        Tool(
            name="get_journal_health",
            description="Get journal file health: size, write latency, rotation status. Detects disk issues before they cause data loss.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="diff_agent_artifacts",
            description="Compare outputs and artifacts of two agents: files touched, output similarity, tool call patterns, error divergence.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_a": {
                        "type": "string",
                        "description": "First agent ID.",
                    },
                    "agent_b": {
                        "type": "string",
                        "description": "Second agent ID.",
                    },
                },
                "required": ["agent_a", "agent_b"],
            },
        ),
        Tool(
            name="list_agents",
            description="List all managed agents with identity metadata (agent_id, sidecar_id, backend, title, status, cwd).",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="get_workflow_context",
            description="Query the workflow context: accumulated findings from all completed agents in this session. Views: 'summary' (counts/totals), 'findings' (all agent results), 'finding' (one agent by ID).",
            inputSchema={
                "type": "object",
                "properties": {
                    "view": {
                        "type": "string",
                        "enum": ["summary", "findings", "finding"],
                        "description": "What to return. Default: summary.",
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "Agent ID (required for 'finding' view).",
                    },
                    "status_filter": {
                        "type": "string",
                        "enum": ["completed", "error", "closed"],
                        "description": "Filter findings by status (for 'findings' view).",
                    },
                },
            },
        ),
        Tool(
            name="cancel_agent",
            description="Cancel a running agent. Uses graceful signal escalation (SIGINT→SIGTERM→SIGKILL) for subprocesses, or sidecar interrupt+close for sidecar agents.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "The agent UUID to cancel.",
                    },
                },
                "required": ["agent_id"],
            },
        ),
        Tool(
            name="cancel_all_agents",
            description="Cancel all currently running agents. Returns per-agent cancellation results.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="search_agent_pool",
            description="Search the agent template pool by keywords (name, role, capabilities, description).",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language description of what agent is needed.",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="save_agent_template",
            description="Save or update an agent template in the pool.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Unique template name, e.g. 'rust-coder'.",
                    },
                    "agent_type": {
                        "type": "string",
                        "description": "CLI to use.",
                        "enum": ["claude", "codex"],
                    },
                    "role": {
                        "type": "string",
                        "description": "Agent role.",
                        "enum": ["coder", "reviewer", "researcher", "tester", "architect", "planner"],
                    },
                    "capabilities": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of capability tags, e.g. ['rust', 'testing'].",
                    },
                    "description": {
                        "type": "string",
                        "description": "What this agent is good at.",
                    },
                    "identity": {
                        "type": "string",
                        "description": "Rich identity statement, e.g. 'Senior Rust engineer with 10+ years of systems programming experience.'",
                    },
                    "soul": {
                        "type": "string",
                        "description": "Personality and values, e.g. 'Believes in correctness first, performance second. Explains complex concepts with patience.'",
                    },
                    "tone": {
                        "type": "string",
                        "description": "Communication style, e.g. 'Technical, direct, and precise. Uses domain terminology naturally.'",
                    },
                    "default_cwd": {
                        "type": "string",
                        "description": "Optional default working directory.",
                    },
                    "system_hint": {
                        "type": "string",
                        "description": "Optional extra prompt context for this agent type.",
                    },
                    "model": {
                        "type": "string",
                        "description": "Preferred Claude model, e.g. 'claude-opus-4-6', 'claude-sonnet-4-6', 'claude-haiku-4-5'. Stored in Kumiho and used when spawning.",
                    },
                },
                "required": ["name", "agent_type", "role", "capabilities", "description"],
            },
        ),
        Tool(
            name="list_agent_templates",
            description="List all agent templates in the pool, sorted by usage count.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="list_teams",
            description="List all agent teams (bundles) from Kumiho Construct/Teams.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="get_team",
            description="Get a team's full details including members and their relationships.",
            inputSchema={
                "type": "object",
                "properties": {
                    "kref": {
                        "type": "string",
                        "description": "The team bundle kref.",
                    },
                },
                "required": ["kref"],
            },
        ),
        Tool(
            name="spawn_team",
            description="Spawn all agents in a team. Returns a list of spawned agent IDs.",
            inputSchema={
                "type": "object",
                "properties": {
                    "team_kref": {
                        "type": "string",
                        "description": "The team bundle kref.",
                    },
                    "task": {
                        "type": "string",
                        "description": "The task description to send to each agent (adapted per role).",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory for all agents.",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "If true, validate the graph and return the stage preview without spawning any agents.",
                        "default": False,
                    },
                    "halt_on_failure": {
                        "type": "boolean",
                        "description": "If true (default), stop spawning downstream stages when an upstream stage has failures. Set to false to continue regardless of failures.",
                        "default": True,
                    },
                    "resume_from": {
                        "type": "string",
                        "description": "Checkpoint ID to resume from. When a spawn halts mid-run, the response includes a checkpoint_id. Pass it here to resume from the failed wave instead of re-running everything.",
                    },
                },
                "required": ["team_kref", "task"],
            },
        ),
        Tool(
            name="create_team",
            description="Create or update an agent team bundle in Kumiho with member agents and edges. If a team with the same name exists, it will be updated (members and edges replaced).",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Team name, e.g. 'rust-dev-team'.",
                    },
                    "description": {
                        "type": "string",
                        "description": "What this team is for.",
                    },
                    "member_krefs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of agent item krefs to add as members.",
                    },
                    "edges": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "from_kref": {"type": "string"},
                                "to_kref": {"type": "string"},
                                "edge_type": {"type": "string", "enum": ["REPORTS_TO", "SUPPORTS", "DEPENDS_ON"]},
                            },
                            "required": ["from_kref", "to_kref", "edge_type"],
                        },
                        "description": "Relationships between members.",
                    },
                },
                "required": ["name", "description", "member_krefs"],
            },
        ),
        Tool(
            name="search_teams",
            description="Search for existing teams by name or description.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search term.",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_spawn_progress",
            description="Get spawn progress for a team deployment. Shows per-wave status, agent completions, and halt reasons. Call with no args to list all active trackers.",
            inputSchema={
                "type": "object",
                "properties": {
                    "team_name": {
                        "type": "string",
                        "description": "Team name to check progress for. Omit to list all trackers.",
                    },
                },
            },
        ),
        Tool(
            name="lint_team",
            description="Lint a team definition: role balance, naming, capability coverage, edge completeness, team size. Returns structured report with issues and suggestions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "team_kref": {
                        "type": "string",
                        "description": "The team bundle kref to lint.",
                    },
                    "task": {
                        "type": "string",
                        "description": "Optional task description for capability-alignment checking.",
                    },
                },
                "required": ["team_kref"],
            },
        ),
        Tool(
            name="resolve_outcome",
            description="Resolve an agent outcome kref to its full revision with artifacts, metadata, and edges. Use this to inspect what a specific agent produced.",
            inputSchema={
                "type": "object",
                "properties": {
                    "revision_kref": {
                        "type": "string",
                        "description": "The outcome revision kref to resolve (e.g. kref://Construct/Outcomes/team-coder-alice.outcome?r=1)",
                    },
                },
                "required": ["revision_kref"],
            },
        ),
        Tool(
            name="get_outcome_lineage",
            description="Walk DERIVED_FROM and DEPENDS_ON edges to show the full provenance chain of an agent outcome. Shows both upstream sources and downstream dependents.",
            inputSchema={
                "type": "object",
                "properties": {
                    "revision_kref": {
                        "type": "string",
                        "description": "The outcome revision kref to trace lineage for",
                    },
                },
                "required": ["revision_kref"],
            },
        ),
        Tool(
            name="list_workflow_presets",
            description="List all workflow presets (builtin + custom). Presets are reusable orchestration patterns like code-review, spec-to-impl, bug-fix.",
            inputSchema={
                "type": "object",
                "properties": {
                    "tag": {
                        "type": "string",
                        "description": "Filter by tag (e.g. 'review', 'testing').",
                    },
                },
            },
        ),
        Tool(
            name="save_workflow_preset",
            description="Save a custom workflow preset for reuse. Each step has a role, agent_type, and optional dependencies on prior steps.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Preset name."},
                    "description": {"type": "string", "description": "What this workflow does."},
                    "steps": {
                        "type": "array",
                        "description": "Workflow steps. Each: {role, agent_type, name_suffix, depends_on: [step indices], review_loop}",
                        "items": {"type": "object"},
                    },
                    "tags": {
                        "type": "array",
                        "description": "Tags for filtering.",
                        "items": {"type": "string"},
                    },
                },
                "required": ["name", "description", "steps"],
            },
        ),
        Tool(
            name="get_workflow_plan",
            description="Preview a workflow preset's execution plan without running it. Shows steps, waves, dependencies.",
            inputSchema={
                "type": "object",
                "properties": {
                    "preset": {"type": "string", "description": "Preset name to preview."},
                },
                "required": ["preset"],
            },
        ),
        Tool(
            name="review_fix_loop",
            description=(
                "Run a review → fix cycle on a completed agent's work. "
                "Spawns a reviewer, parses the verdict (APPROVED / NEEDS_CHANGES / BLOCKED), "
                "and if changes are needed, spawns a fixer with the feedback. Repeats up to max_rounds."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "coder_agent_id": {
                        "type": "string",
                        "description": "Agent whose work to review (must be completed).",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory for reviewer and fixer agents.",
                    },
                    "task": {
                        "type": "string",
                        "description": "Original task description (for context).",
                    },
                    "reviewer_type": {
                        "type": "string",
                        "description": "Agent type for reviewer (default 'codex').",
                        "default": "codex",
                    },
                    "fixer_type": {
                        "type": "string",
                        "description": "Agent type for fixer (default 'codex').",
                        "default": "codex",
                    },
                    "model": {
                        "type": "string",
                        "description": "Optional model override for reviewer and fixer.",
                    },
                    "max_rounds": {
                        "type": "integer",
                        "description": "Max review→fix iterations (default 2, max 5).",
                        "default": 2,
                    },
                    "review_focus": {
                        "type": "string",
                        "description": "Extra guidance for the reviewer (e.g. 'focus on security').",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Per-agent timeout in seconds (default 300).",
                        "default": 300,
                    },
                },
                "required": ["coder_agent_id", "cwd"],
            },
        ),
        Tool(
            name="refinement_loop",
            description=(
                "Iterative refinement: critique → refine → repeat with quality scoring. "
                "Replaces review_fix_loop with structured scoring (0-100), trust-informed critic selection, "
                "and fallback ladder (creator → dedicated fixer → escalate). "
                "Pass creator_agent_id of an agent whose work to review."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "creator_agent_id": {
                        "type": "string",
                        "description": "Agent whose work to review and refine.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory for critic and fixer agents.",
                    },
                    "task": {
                        "type": "string",
                        "description": "Original task description (for context).",
                    },
                    "creator": {
                        "type": "string",
                        "description": "Agent type for fixer (default 'codex').",
                        "default": "codex",
                    },
                    "critic": {
                        "type": "string",
                        "description": "Agent type for critic (default 'claude'). Auto-switches if trust < 0.7.",
                        "default": "claude",
                    },
                    "model": {
                        "type": "string",
                        "description": "Optional model override.",
                    },
                    "max_rounds": {
                        "type": "integer",
                        "description": "Max critique→refine iterations (default 2, max 5).",
                        "default": 2,
                    },
                    "threshold": {
                        "type": "integer",
                        "description": "Quality score threshold for approval (0-100, default 70).",
                        "default": 70,
                    },
                    "review_focus": {
                        "type": "string",
                        "description": "Extra guidance for the critic (e.g. 'focus on security').",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Per-agent timeout in seconds (default 300).",
                        "default": 300,
                    },
                },
                "required": ["creator_agent_id", "cwd"],
            },
        ),
        # -- A2A Protocol tools --
        Tool(
            name="a2a_get_card",
            description="Get the A2A agent card for this Construct instance or a specific template. Returns the JSON agent card following the A2A protocol spec.",
            inputSchema={
                "type": "object",
                "properties": {
                    "template_name": {
                        "type": "string",
                        "description": "Specific template name. Omit for composite card of all templates.",
                    },
                },
            },
        ),
        Tool(
            name="a2a_handle_request",
            description="Handle an incoming A2A JSON-RPC request. Dispatches to message/send, tasks/get, tasks/cancel, tasks/list.",
            inputSchema={
                "type": "object",
                "properties": {
                    "request": {
                        "type": "object",
                        "description": "Full A2A JSON-RPC 2.0 request object.",
                    },
                },
                "required": ["request"],
            },
        ),
        Tool(
            name="a2a_list_tasks",
            description="List A2A tasks created by external agents.",
            inputSchema={
                "type": "object",
                "properties": {
                    "context_id": {
                        "type": "string",
                        "description": "Filter by context ID.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 50).",
                        "default": 50,
                    },
                },
            },
        ),
        # -- Orchestration Patterns --
        Tool(
            name="handoff_agent",
            description=(
                "Hand off work from one agent to another with full context transfer. "
                "Extracts findings, files touched, and task state from source agent and "
                "injects into the receiving agent's prompt. Tracks handoff chain in Kumiho."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "from_agent_id": {
                        "type": "string",
                        "description": "Agent whose work to hand off.",
                    },
                    "to_agent_type": {
                        "type": "string",
                        "description": "Agent type for receiver: 'claude' or 'codex' (default 'claude').",
                        "default": "claude",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why the handoff is happening.",
                    },
                    "task": {
                        "type": "string",
                        "description": "Specific task for the receiving agent.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory (defaults to source agent's cwd).",
                    },
                    "model": {
                        "type": "string",
                        "description": "Optional model override.",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Per-agent timeout (default 300s).",
                        "default": 300,
                    },
                },
                "required": ["from_agent_id", "reason"],
            },
        ),
        Tool(
            name="group_chat",
            description=(
                "Run a moderated multi-agent group discussion. Multiple agents discuss "
                "a topic with turn-taking (round_robin or moderator_selected). A moderator "
                "synthesizes consensus at the end. Returns transcript, summary, and conclusion."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Discussion topic.",
                    },
                    "participants": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of agent types (e.g. ['claude', 'codex', 'claude']). Min 2.",
                    },
                    "moderator": {
                        "type": "string",
                        "description": "Agent type for moderator (default 'claude').",
                        "default": "claude",
                    },
                    "strategy": {
                        "type": "string",
                        "description": "Turn strategy: 'round_robin' or 'moderator_selected' (default).",
                        "enum": ["round_robin", "moderator_selected"],
                        "default": "moderator_selected",
                    },
                    "max_rounds": {
                        "type": "integer",
                        "description": "Max speaking turns (default 8, max 20).",
                        "default": 8,
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory (default /tmp).",
                    },
                    "model": {
                        "type": "string",
                        "description": "Optional model override.",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Per-turn timeout (default 120s).",
                        "default": 120,
                    },
                },
                "required": ["topic", "participants"],
            },
        ),
        Tool(
            name="supervisor_run",
            description=(
                "Dynamic task delegation: supervisor analyzes task, delegates subtasks to "
                "best available specialists, integrates results, repeats until done. "
                "Unlike spawn_team (static DAG), the next agent is chosen based on results so far."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Task to accomplish.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory.",
                    },
                    "templates": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Available template names. Defaults to all in pool.",
                    },
                    "max_iterations": {
                        "type": "integer",
                        "description": "Max delegate→integrate cycles (default 5, max 10).",
                        "default": 5,
                    },
                    "supervisor_type": {
                        "type": "string",
                        "description": "Agent type for supervisor (default 'claude').",
                        "default": "claude",
                    },
                    "model": {
                        "type": "string",
                        "description": "Optional model override.",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Per-agent timeout (default 300s).",
                        "default": 300,
                    },
                },
                "required": ["task", "cwd"],
            },
        ),
        Tool(
            name="map_reduce",
            description=(
                "Fan out a task to N parallel agents, then aggregate results. "
                "Splits work into segments, runs mapper agents in parallel (with concurrency limit), "
                "then a reducer agent synthesizes all results. Good for reviews, analysis, or processing across files/modules."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Overall task description.",
                    },
                    "splits": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of segments to process in parallel (file paths, sections, subtasks). Min 2.",
                    },
                    "mapper": {
                        "type": "string",
                        "description": "Agent type for mapper agents (default 'claude').",
                        "default": "claude",
                    },
                    "reducer": {
                        "type": "string",
                        "description": "Agent type for reducer agent (default 'claude').",
                        "default": "claude",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory.",
                    },
                    "concurrency": {
                        "type": "integer",
                        "description": "Max simultaneous mappers (default 3, max 10).",
                        "default": 3,
                    },
                    "model": {
                        "type": "string",
                        "description": "Optional model override.",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Per-agent timeout (default 300s).",
                        "default": 300,
                    },
                    "halt_on_failure": {
                        "type": "boolean",
                        "description": "Stop remaining mappers if one fails (default false).",
                        "default": False,
                    },
                },
                "required": ["task", "splits", "cwd"],
            },
        ),
        Tool(
            name="check_policy",
            description="Pre-flight policy check. Verify if a cwd, command, or tool is allowed before executing. Returns structured pass/fail with policy rule context.",
            inputSchema={
                "type": "object",
                "properties": {
                    "cwd": {
                        "type": "string",
                        "description": "Working directory to check against allowed_roots / forbidden_paths.",
                    },
                    "command": {
                        "type": "string",
                        "description": "Shell command to check against allowed_commands / high-risk patterns.",
                    },
                    "tool": {
                        "type": "string",
                        "description": "Tool name to check against auto_approve / always_ask lists.",
                    },
                },
            },
        ),
        Tool(
            name="get_policy_summary",
            description="Return the current autonomy policy summary (level, allowed roots, command counts, risk settings).",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="save_plan",
            description="Save an execution plan to Kumiho (Construct/Plans/) for future recall. Include task description, steps, agent assignments, and outcome.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Short plan name, e.g. 'rust-refactor-auth-module'.",
                    },
                    "task_description": {
                        "type": "string",
                        "description": "What the task was about.",
                    },
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Ordered list of steps taken.",
                    },
                    "agents_used": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Agent templates/roles used.",
                    },
                    "outcome": {
                        "type": "string",
                        "description": "How it went: success, partial, failed.",
                        "enum": ["success", "partial", "failed"],
                    },
                    "lessons": {
                        "type": "string",
                        "description": "What worked, what didn't, what to do differently.",
                    },
                },
                "required": ["name", "task_description", "steps", "outcome"],
            },
        ),
        Tool(
            name="recall_plans",
            description="Search past execution plans in Kumiho (Construct/Plans/) for similar tasks. Use before decomposing complex tasks to learn from past experience.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Describe the task you're planning — will match against past plans.",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_agent_trust",
            description="Get trust/reputation scores for agent templates. Returns success rate, total runs, and recent outcomes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "template_name": {
                        "type": "string",
                        "description": "Specific template name. Omit for all templates with scores.",
                    },
                },
            },
        ),
        Tool(
            name="capture_skill",
            description=(
                f"Capture or update a reusable procedure as a first-class skill in "
                f"{memory_project()}/Skills. "
                "Use this tool whenever the user asks to capture/save/record something as a skill; "
                "do not use memory_store for skill-guide captures. The tool stores SKILL.md as a "
                "revision artifact under the Construct workspace artifact tree, keeps agent provenance "
                "in revision metadata rather than in the item name, and reads the previous published "
                "SKILL.md artifact when updating an existing skill so the new revision can improve it."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Stable skill name, e.g. 'rust-error-handling-pattern'. Do not append agent IDs or run IDs.",
                    },
                    "domain": {
                        "type": "string",
                        "description": "Domain tag, e.g. 'rust', 'react', 'devops', 'testing'.",
                    },
                    "description": {
                        "type": "string",
                        "description": "One-line summary of what this skill covers.",
                    },
                    "procedure": {
                        "type": "string",
                        "description": "The full procedure/instructions an agent should follow. Markdown format.",
                    },
                    "learned_from": {
                        "type": "string",
                        "description": "Context: what task led to discovering this procedure.",
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "Optional source agent/session ID. Stored as revision metadata, never appended to the skill name.",
                    },
                    "change_summary": {
                        "type": "string",
                        "description": "For updates, briefly describe how this revision improves the previous skill guide.",
                    },
                    "source_revision_krefs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional revision/artifact krefs this skill revision was derived from.",
                    },
                },
                "required": ["name", "domain", "description", "procedure"],
            },
        ),
        Tool(
            name="list_skills",
            description="List captured skills from Kumiho's Skills space. Does not read local markdown files unless legacy fallback is explicitly requested.",
            inputSchema={
                "type": "object",
                "properties": {
                    "include_details": {
                        "type": "boolean",
                        "description": "If true, enrich each skill with latest revision and SKILL.md artifact metadata. Default false for fast listing.",
                        "default": False,
                    },
                    "include_legacy_disk": {
                        "type": "boolean",
                        "description": "If true and Kumiho is unavailable, fall back to legacy local markdown skills. Default false.",
                        "default": False,
                    },
                },
            },
        ),
        Tool(
            name="load_skill",
            description="Load a captured skill's SKILL.md artifact from Kumiho by skill name or kref.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Captured skill name or Kumiho item kref.",
                    },
                    "tag": {
                        "type": "string",
                        "description": "Revision tag to load. Default published.",
                        "default": "published",
                    },
                    "allow_legacy_disk_fallback": {
                        "type": "boolean",
                        "description": "If true, fall back to legacy local markdown only when the skill is not found in Kumiho or Kumiho is unavailable. Default false.",
                        "default": False,
                    },
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="get_budget_status",
            description="Get current cost and budget status (session, daily, monthly spend vs limits, per-model breakdown).",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="create_goal",
            description="Create a goal in the Construct/Goals/ hierarchy. Goals can have parent goals to form a tree (strategic → tactical → task-level).",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Short goal name, e.g. 'improve-test-coverage'.",
                    },
                    "description": {
                        "type": "string",
                        "description": "What this goal aims to achieve.",
                    },
                    "status": {
                        "type": "string",
                        "description": "Goal status.",
                        "enum": ["active", "completed", "blocked", "deferred"],
                        "default": "active",
                    },
                    "priority": {
                        "type": "string",
                        "description": "Priority level.",
                        "enum": ["p0", "p1", "p2", "p3"],
                        "default": "p1",
                    },
                    "parent_kref": {
                        "type": "string",
                        "description": "Kref of the parent goal (for sub-goals). Omit for top-level goals.",
                    },
                },
                "required": ["name", "description"],
            },
        ),
        Tool(
            name="get_goals",
            description="List goals from Construct/Goals/, optionally filtered by status or priority.",
            inputSchema={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Filter by status.",
                        "enum": ["active", "completed", "blocked", "deferred"],
                    },
                    "priority": {
                        "type": "string",
                        "description": "Filter by priority.",
                        "enum": ["p0", "p1", "p2", "p3"],
                    },
                },
            },
        ),
        Tool(
            name="update_goal",
            description="Update a goal's status, priority, or description.",
            inputSchema={
                "type": "object",
                "properties": {
                    "kref": {
                        "type": "string",
                        "description": "Kref of the goal to update.",
                    },
                    "status": {
                        "type": "string",
                        "description": "New status.",
                        "enum": ["active", "completed", "blocked", "deferred"],
                    },
                    "priority": {
                        "type": "string",
                        "description": "New priority.",
                        "enum": ["p0", "p1", "p2", "p3"],
                    },
                    "description": {
                        "type": "string",
                        "description": "Updated description.",
                    },
                },
                "required": ["kref"],
            },
        ),
        Tool(
            name="search_clawhub",
            description="Search the public ClawHub marketplace (clawhub.ai) for community skills. Returns matching skills with name, description, downloads, stars, and verified status.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (uses vector search across skill names, descriptions, and tags).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 20).",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="install_from_clawhub",
            description="Install a skill from the public ClawHub marketplace into local Construct. Fetches the SKILL.md from clawhub.ai and creates a local skill in Kumiho.",
            inputSchema={
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "ClawHub skill slug (e.g. 'code-review', 'memory-manager').",
                    },
                },
                "required": ["slug"],
            },
        ),
        Tool(
            name="browse_clawhub",
            description="Browse trending skills on the public ClawHub marketplace (clawhub.ai).",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 20).",
                    },
                },
            },
        ),
        Tool(
            name="render_canvas",
            description="Push content to the Live Canvas for real-time visualization. Supports HTML (interactive pages, charts), SVG (diagrams, graphs), markdown (formatted text), and plain text. The dashboard Canvas page displays the latest frame. Use canvas_id to maintain separate canvases (default: 'default').",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The content to render. For HTML, provide a full page or fragment. For SVG, provide the <svg> element. For markdown, provide raw markdown text.",
                    },
                    "content_type": {
                        "type": "string",
                        "description": "Type of content to render.",
                        "enum": ["html", "svg", "markdown", "text"],
                        "default": "html",
                    },
                    "canvas_id": {
                        "type": "string",
                        "description": "Canvas identifier (default: 'default'). Use different IDs to maintain separate canvases.",
                        "default": "default",
                    },
                },
                "required": ["content"],
            },
        ),
        Tool(
            name="clear_canvas",
            description="Clear a Live Canvas, removing all content and frame history.",
            inputSchema={
                "type": "object",
                "properties": {
                    "canvas_id": {
                        "type": "string",
                        "description": "Canvas identifier to clear (default: 'default').",
                        "default": "default",
                    },
                },
            },
        ),
        Tool(
            name="generate_image_codex",
            description=(
                "Generate one or more PNG images via the OpenAI Codex CLI's "
                "built-in `image_generation` tool. Up to 5 in parallel "
                "(asyncio.gather over independent codex sessions); for more, "
                "call the tool repeatedly in batches. Optionally pushes a "
                "gallery frame to the Live Canvas and registers each PNG as "
                "a Kumiho artifact under <harness>/<space>. Requires the "
                "`codex` CLI installed and authenticated via ChatGPT OAuth "
                "(0.128+ recommended for the latest image_generation feature; "
                "earlier versions may work if image_generation is enabled). "
                "RESPONSE NOTE: when you compose your own HTML/canvas output "
                "referencing the generated images, use the `urls` array (each "
                "entry is a signed `/workspace/...` URL the dashboard can "
                "load), NOT the `files` array (those are absolute filesystem "
                "paths the browser sandbox can't read). CANVAS PICK-ONE: if "
                "`canvas: true` is passed, this tool already pushes a working "
                "canvas frame — do NOT also call `render_canvas` for the same "
                "image (you'll get duplicate frames). For custom styling, set "
                "`canvas: false` and call `render_canvas` yourself with "
                "`urls[0]`."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Natural-language description of the image to generate.",
                    },
                    "output_path": {
                        "type": "string",
                        "description": (
                            "Target PNG path. Relative to `cwd` unless absolute. "
                            "When `count > 1` and `output_pattern` is unset, "
                            "the path is expanded as `<stem>-N.<ext>` per image."
                        ),
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory for codex (default: ~/.construct/workspace).",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of images to generate in parallel (1..5, default 1).",
                        "minimum": 1,
                        "maximum": 5,
                        "default": 1,
                    },
                    "output_pattern": {
                        "type": "string",
                        "description": (
                            "Filename template with `{n}` placeholder when count > 1, "
                            "e.g. `logo-{n}.png`. Overrides the default `<stem>-N` derivation."
                        ),
                    },
                    "input_images": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}},
                        ],
                        "description": (
                            "Optional local image path or list of local image paths to pass "
                            "to `codex exec --image` as visual references. Operator chat "
                            "attachments that appear in the prompt as `[IMAGE:/path]` markers "
                            "are also extracted and forwarded automatically."
                        ),
                    },
                    "canvas": {
                        "oneOf": [{"type": "boolean"}, {"type": "string"}],
                        "description": (
                            "If true, push a gallery frame to the default Live Canvas. "
                            "If a string, use that canvas_id. Default: false."
                        ),
                        "default": False,
                    },
                    "register_artifact": {
                        "type": "boolean",
                        "description": (
                            "Create a Kumiho item + revision under <harness>/<space> and "
                            "attach each generated PNG as an artifact. The on-disk path "
                            "mirrors the kref hierarchy: "
                            "`~/.construct/workspace/<harness>/<space>/<item>.<kind>/r<N>/<filename>`. "
                            "When false, falls back to `<cwd>/<output_path>`. Default: true."
                        ),
                        "default": True,
                    },
                    "space": {
                        "type": "string",
                        "description": (
                            "Kumiho space (relative to the harness project) where the item "
                            "is created. Multi-segment paths supported, e.g. "
                            "`Marketing/Logos`. Default: `Images`."
                        ),
                        "default": "Images",
                    },
                    "item_name": {
                        "type": "string",
                        "description": (
                            "Optional name for the Kumiho item. Defaults to the output "
                            "filename stem (with the auto-appended `-N` suffix stripped)."
                        ),
                    },
                    "sandbox": {
                        "type": "string",
                        "enum": ["read-only", "workspace-write", "danger-full-access"],
                        "description": (
                            "codex `--sandbox` mode. When unset, the tool picks the "
                            "right default for the local install: `workspace-write` "
                            "on macOS/Linux and admin-installed Windows, "
                            "`danger-full-access` on Windows per-user npm installs "
                            "(`%APPDATA%\\\\npm\\\\codex.CMD`) whose sandbox helper "
                            "can't spawn child processes (`CreateProcessAsUserW "
                            "failed: 5`) — without that, codex's image skill won't "
                            "load and no PNG is produced. Pin a value only if you "
                            "need to override the auto-default."
                        ),
                    },
                },
                "required": ["prompt", "output_path"],
            },
        ),
        Tool(
            name="list_nodes",
            description="List connected remote nodes and their capabilities. Nodes connect via WebSocket and advertise tools (e.g. camera.snap, shell.exec).",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="invoke_node",
            description="Invoke a capability on a connected remote node. Use list_nodes first to discover available capabilities.",
            inputSchema={
                "type": "object",
                "properties": {
                    "node_id": {
                        "type": "string",
                        "description": "The node ID to invoke on.",
                    },
                    "capability": {
                        "type": "string",
                        "description": "The capability name to invoke (e.g. 'camera.snap').",
                    },
                    "args": {
                        "type": "object",
                        "description": "Arguments to pass to the capability.",
                        "default": {},
                    },
                },
                "required": ["node_id", "capability"],
            },
        ),
        Tool(
            name="chat_create",
            description="Create a named chat room for inter-agent coordination. Agents can post messages and @mention each other.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Room name, e.g. 'design-review', 'code-sync'.",
                    },
                    "purpose": {
                        "type": "string",
                        "description": "What this room is for.",
                    },
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="chat_post",
            description="Post a message to a chat room. Use mentions to @notify specific agents (triggers a follow-up prompt if they're idle).",
            inputSchema={
                "type": "object",
                "properties": {
                    "room_id": {
                        "type": "string",
                        "description": "Chat room ID.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Message content.",
                    },
                    "sender_id": {
                        "type": "string",
                        "description": "Your agent ID (defaults to 'operator').",
                    },
                    "sender_name": {
                        "type": "string",
                        "description": "Your display name (defaults to 'Operator').",
                    },
                    "mentions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Agent IDs to @mention (actively notifies them).",
                    },
                    "reply_to": {
                        "type": "string",
                        "description": "Message ID to reply to.",
                    },
                },
                "required": ["room_id", "content"],
            },
        ),
        Tool(
            name="chat_read",
            description="Read recent messages from a chat room.",
            inputSchema={
                "type": "object",
                "properties": {
                    "room_id": {
                        "type": "string",
                        "description": "Chat room ID.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max messages to return (default 50).",
                        "default": 50,
                    },
                    "since": {
                        "type": "string",
                        "description": "ISO timestamp — only return messages after this time.",
                    },
                },
                "required": ["room_id"],
            },
        ),
        Tool(
            name="chat_list",
            description="List all active chat rooms.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="chat_wait",
            description="Wait for a new message in a chat room (long-poll, up to 30s). Use this to monitor coordination channels.",
            inputSchema={
                "type": "object",
                "properties": {
                    "room_id": {
                        "type": "string",
                        "description": "Chat room ID.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Max wait time in milliseconds (default 30000, max 60000).",
                        "default": 30000,
                    },
                },
                "required": ["room_id"],
            },
        ),
        Tool(
            name="chat_delete",
            description="Delete a chat room and all its messages.",
            inputSchema={
                "type": "object",
                "properties": {
                    "room_id": {
                        "type": "string",
                        "description": "Chat room ID to delete.",
                    },
                },
                "required": ["room_id"],
            },
        ),
        Tool(
            name="list_pending_permissions",
            description="List all pending permission requests across managed agents. Pending requests need user approval/denial.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="respond_to_permission",
            description="Approve or deny a pending permission request from an agent.",
            inputSchema={
                "type": "object",
                "properties": {
                    "request_id": {
                        "type": "string",
                        "description": "The permission request ID.",
                    },
                    "action": {
                        "type": "string",
                        "description": "Action to take.",
                        "enum": ["approve", "deny"],
                    },
                },
                "required": ["request_id", "action"],
            },
        ),
        Tool(
            name="get_session_history",
            description="Recall past operator sessions and agent lifecycle events from the local journal. Use to resume context after a restart.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Filter to a specific session ID. Omit to list all sessions.",
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "Filter to a specific agent ID.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max entries to return (default 30).",
                        "default": 30,
                    },
                    "list_sessions": {
                        "type": "boolean",
                        "description": "If true, return a summary of distinct sessions instead of individual events.",
                        "default": False,
                    },
                },
            },
        ),
        Tool(
            name="archive_session",
            description="Archive a completed session to Kumiho (Construct/Sessions/) for long-term recall. Call after a significant multi-agent task completes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID to archive. Defaults to the current session.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Short title summarising what the session accomplished.",
                    },
                    "summary": {
                        "type": "string",
                        "description": "A longer summary of the session's goals, agents used, and outcomes.",
                    },
                    "outcome": {
                        "type": "string",
                        "description": "Overall session outcome.",
                        "enum": ["success", "partial", "failed"],
                    },
                },
                "required": ["title", "summary", "outcome"],
            },
        ),

        # -- Compaction --
        Tool(
            name="compact_conversation",
            description="Start structured context compaction. Returns a 9-section prompt template — apply it to your conversation, then call store_compaction with the result.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID. Defaults to the current session.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why compaction is being triggered (e.g. 'auto-threshold', 'manual', 'session-end').",
                        "default": "manual",
                    },
                },
            },
        ),
        Tool(
            name="store_compaction",
            description="Store a compacted conversation summary in Kumiho. Call after applying the compact_conversation template to your context.",
            inputSchema={
                "type": "object",
                "properties": {
                    "raw_output": {
                        "type": "string",
                        "description": "The full output from applying the compact prompt, including <analysis> and <summary> tags.",
                    },
                    "session_id": {
                        "type": "string",
                        "description": "Session ID. Defaults to the current session.",
                    },
                    "source_krefs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Kumiho krefs that this compaction is derived from.",
                    },
                },
                "required": ["raw_output"],
            },
        ),
        # -- Declarative Workflow Engine --
        Tool(
            name="run_workflow",
            description=(
                "Execute a declarative YAML workflow. Workflows chain multiple agents, "
                "shell commands, conditionals, and parallel steps with variable interpolation. "
                "Use list_workflows to see available workflows."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "workflow": {
                        "type": "string",
                        "description": "Workflow name (e.g. 'code-review', 'bug-fix', 'refactor').",
                    },
                    "workflow_def": {
                        "type": "object",
                        "description": "Inline workflow definition dict (alternative to name).",
                    },
                    "inputs": {
                        "type": "object",
                        "description": "Input parameters for the workflow (e.g. {task: '...', cwd: '...'}).",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory for agent/shell steps.",
                    },
                    "run_id": {
                        "type": "string",
                        "description": "Optional run ID (auto-generated if omitted).",
                    },
                    "max_cost_usd": {
                        "type": "number",
                        "description": "Optional cost cap in USD. Workflow aborts if session cost exceeds this.",
                    },
                },
                "required": ["cwd"],
            },
        ),
        Tool(
            name="get_workflow_status",
            description="Get status of a running or completed workflow run, including per-step results.",
            inputSchema={
                "type": "object",
                "properties": {
                    "run_id": {
                        "type": "string",
                        "description": "The workflow run ID.",
                    },
                    "include_outputs": {
                        "type": "boolean",
                        "description": "Include step output text (default false).",
                        "default": False,
                    },
                },
                "required": ["run_id"],
            },
        ),
        Tool(
            name="list_workflows",
            description="List all available declarative workflow definitions (builtin + user + project-local).",
            inputSchema={
                "type": "object",
                "properties": {
                    "cwd": {
                        "type": "string",
                        "description": "Project directory to include project-local workflows.",
                    },
                    "tag": {
                        "type": "string",
                        "description": "Filter by tag (e.g. 'review', 'bugfix').",
                    },
                },
            },
        ),
        Tool(
            name="cancel_workflow",
            description="Cancel a running or paused workflow.",
            inputSchema={
                "type": "object",
                "properties": {
                    "run_id": {
                        "type": "string",
                        "description": "The workflow run ID to cancel.",
                    },
                },
                "required": ["run_id"],
            },
        ),
        Tool(
            name="validate_workflow",
            description="Validate a workflow definition without executing. Checks YAML parsing, Pydantic schema, cycles, missing deps, and variable refs. Always returns a structured {valid, errors, warnings} — never raises.",
            inputSchema={
                "type": "object",
                "properties": {
                    "workflow": {
                        "type": "string",
                        "description": "Workflow name to validate (resolves from Kumiho/disk).",
                    },
                    "workflow_def": {
                        "type": "object",
                        "description": "Inline workflow definition dict to validate.",
                    },
                    "workflow_yaml": {
                        "type": "string",
                        "description": "Raw YAML text to parse and validate.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Project directory for discovery.",
                    },
                },
            },
        ),
        Tool(
            name="revise_workflow",
            description=(
                "Apply a list of structured operations (add_step / edit_step / "
                "delete_step / reorder / wire / unwire / insert_into_parallel / "
                "extract_from_parallel / rename_step) to a workflow's current "
                "revision and emit a new Kumiho revision tagged 'published'. "
                "Returns success, the new revision kref, applied_count, and a "
                "list of SkippedItem entries with typed reasons (step_not_found, "
                "duplicate_id, reference_broken, cycle_detected, validation_failed, "
                "…) so the LLM can repair instead of re-prompt blind. Kumiho is "
                "revision-native — there is no in-place edit; every change "
                "produces a new immutable revision."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "workflow_kref": {
                        "type": "string",
                        "description": "kref of the workflow item, e.g. kref://Construct/Workflows/foo.workflow.",
                    },
                    "operations": {
                        "type": "array",
                        "description": "Ordered list of revision ops. Each item: {op, step_id?, new_id?, step_def?, target_step_id?, parallel_id?, position?, position_after?}.",
                        "items": {"type": "object"},
                    },
                    "rationale": {
                        "type": "string",
                        "description": "Optional human-readable rationale stored on the new revision as a tag.",
                    },
                },
                "required": ["workflow_kref", "operations"],
            },
        ),
        Tool(
            name="propose_workflow_yaml",
            description=(
                "Architect-only: validate a proposed workflow YAML and echo "
                "it back with a structured response. NEVER persists to disk "
                "or Kumiho — the editor frontend pipes the YAML into the "
                "editor's in-memory definition state and the user decides "
                "when to Save. Returns {yaml, summary, valid, errors, "
                "warnings, added_step_ids, modified_step_ids, "
                "removed_step_ids}. When base_yaml is provided, computes a "
                "step-ID diff so the caller can render added/changed steps."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "proposed_yaml": {
                        "type": "string",
                        "description": "The complete proposed workflow YAML.",
                    },
                    "intent_summary": {
                        "type": "string",
                        "description": "One-line description of what the user asked for.",
                    },
                    "base_yaml": {
                        "type": "string",
                        "description": "Current editor YAML if extending; omit or empty for a fresh workflow.",
                    },
                },
                "required": ["proposed_yaml"],
            },
        ),
        Tool(
            name="create_workflow",
            description="Create a new workflow definition and save as YAML to ~/.construct/workflows/.",
            inputSchema={
                "type": "object",
                "properties": {
                    "workflow_def": {
                        "type": "object",
                        "description": "Workflow definition dict with name, steps, inputs, outputs.",
                    },
                    "directory": {
                        "type": "string",
                        "description": "Save directory (defaults to ~/.construct/workflows/).",
                    },
                },
                "required": ["workflow_def"],
            },
        ),
        Tool(
            name="resume_workflow",
            description="Resume a paused workflow (e.g. after human_approval step). Pass approved=true to continue, false to cancel.",
            inputSchema={
                "type": "object",
                "properties": {
                    "run_id": {
                        "type": "string",
                        "description": "The paused workflow run ID.",
                    },
                    "approved": {
                        "type": "boolean",
                        "description": "Whether to approve and continue (default true).",
                        "default": True,
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory for remaining steps.",
                    },
                },
                "required": ["run_id"],
            },
        ),
        Tool(
            name="retry_workflow",
            description="Retry a failed workflow run from the first failed step. Successful step outputs are preserved so only the failed step and downstream steps re-execute.",
            inputSchema={
                "type": "object",
                "properties": {
                    "run_id": {
                        "type": "string",
                        "description": "The failed workflow run ID.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory for the retried steps.",
                    },
                },
                "required": ["run_id"],
            },
        ),
        # -- Manus ad-hoc research (web/research) --
        Tool(
            name="manus_create_task",
            description=(
                "Run an ad-hoc Manus research task mid-conversation. Manus is a "
                "hosted web agent with real browser access — use this when the "
                "user asks for research that benefits from live web browsing or "
                "structured data extraction. Mirrors the workflow `manus:` step "
                "(same auth precedence: credentials_ref → MANUS_API_KEY env var) "
                "but is callable directly from chat. Returns task_id / task_url / "
                "final_state / assistant message / attachments / optional "
                "structured_output. No register_output — call kumiho_create_item "
                "separately if you want to persist the result."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "The research prompt for Manus.",
                    },
                    "structured_output_schema": {
                        "type": "object",
                        "description": "Optional JSON schema for Manus to populate. When provided, the result includes `structured_output`.",
                    },
                    "connectors": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Manus connector ids to attach (e.g. 'google_drive').",
                    },
                    "enable_skills": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "force_skills": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "agent_profile": {
                        "type": "string",
                        "description": "Manus agent profile id (default 'manus-1.6').",
                    },
                    "locale": {"type": "string"},
                    "project_id": {"type": "string"},
                    "title": {"type": "string"},
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Max seconds to poll the task (default 600).",
                        "default": 600,
                    },
                    "poll_interval_seconds": {
                        "type": "integer",
                        "description": "Seconds between poll calls (default 5).",
                    },
                    "credentials_ref": {
                        "type": "string",
                        "description": "Auth-profile id (e.g. 'manus:work'). When unset, falls back to MANUS_API_KEY env var.",
                    },
                },
                "required": ["prompt"],
            },
        ),
        # -- A2A Outbound Client --
        Tool(
            name="a2a_discover",
            description="Discover an external A2A agent by URL. Fetches the agent card from .well-known/agent-card.json and caches it.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Base URL of the external agent (e.g. 'https://agent.example.com').",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Discovery timeout in seconds (default 30).",
                        "default": 30,
                    },
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="a2a_send_task",
            description="Send a task to an external A2A agent. Optionally wait for completion by polling.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "A2A endpoint URL.",
                    },
                    "message": {
                        "type": "string",
                        "description": "Task message text.",
                    },
                    "skill_id": {
                        "type": "string",
                        "description": "Optional skill ID to route to on the remote agent.",
                    },
                    "wait": {
                        "type": "boolean",
                        "description": "Poll until task completes (default false).",
                        "default": False,
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Request timeout in seconds (default 60).",
                        "default": 60,
                    },
                },
                "required": ["url", "message"],
            },
        ),
        Tool(
            name="a2a_get_remote_task",
            description="Check status of a task on an external A2A agent.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "A2A endpoint URL.",
                    },
                    "task_id": {
                        "type": "string",
                        "description": "Task ID to check.",
                    },
                },
                "required": ["url", "task_id"],
            },
        ),
        # -- Workflow Memory --
        Tool(
            name="recall_workflow_runs",
            description="Recall recent workflow runs from Kumiho memory. Use to find prior workflow executions and their results.",
            inputSchema={
                "type": "object",
                "properties": {
                    "workflow": {
                        "type": "string",
                        "description": "Filter by workflow name (e.g. 'code-review'). Omit for all.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 10, max 50).",
                        "default": 10,
                    },
                },
            },
        ),
        Tool(
            name="get_workflow_run_detail",
            description="Get detailed info about a specific workflow run from Kumiho memory, including per-step results.",
            inputSchema={
                "type": "object",
                "properties": {
                    "run_id": {
                        "type": "string",
                        "description": "The workflow run ID.",
                    },
                },
                "required": ["run_id"],
            },
        ),
        Tool(
            name="dry_run_workflow",
            description=(
                "Validate and plan a workflow without executing it. Returns execution order, "
                "step details, estimated agent count, cost estimate, and any validation warnings. "
                "Use before run_workflow to preview what will happen."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "workflow": {
                        "type": "string",
                        "description": "Workflow name to dry-run.",
                    },
                    "workflow_def": {
                        "type": "object",
                        "description": "Inline workflow definition to dry-run.",
                    },
                    "inputs": {
                        "type": "object",
                        "description": "Input parameters to preview variable resolution.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Project directory for workflow discovery.",
                    },
                },
            },
        ),
        Tool(
            name="get_workflow_metadata",
            description=(
                "Return the editor-relevant primitives a workflow can use: step types "
                "(with config-field schemas + example YAML), pool agents, auth profiles, "
                "skills, and channels. Use before proposing a workflow revision so step "
                "types, agent slugs, and skill IDs are grounded in what's available."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "include": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Categories to return. Default: all of "
                            "['step_types', 'agents', 'auth_profiles', 'skills', 'channels']."
                        ),
                    },
                },
            },
        ),
        Tool(
            name="system_dashboard",
            description=(
                "Unified system health dashboard. Shows active agents, workflows, cost summary, "
                "Kumiho connectivity, sidecar/gateway status, and team count in one call."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "include_costs": {
                        "type": "boolean",
                        "description": "Include cost breakdown (default true).",
                        "default": True,
                    },
                    "include_agents": {
                        "type": "boolean",
                        "description": "Include agent pool summary (default true).",
                        "default": True,
                    },
                    "include_workflows": {
                        "type": "boolean",
                        "description": "Include active/recent workflows (default true).",
                        "default": True,
                    },
                    "include_health": {
                        "type": "boolean",
                        "description": "Include system health checks (default true).",
                        "default": True,
                    },
                },
            },
        ),
        Tool(
            name="record_agent_outcome",
            description=(
                "Record an agent outcome (discovery / decision / lesson / insight / warning / fact) "
                "into the harness project's Sessions/<session_id>/Outcomes/ space. The harness "
                "project comes from your config — this tool resolves it for you. Outcomes are "
                "append-only memories that downstream agents in the same workflow / handoff chain "
                "inherit via recall_session_outcomes. Pass `related_krefs` (e.g. an artifact kref "
                "the agent produced) to create INFORMS edges so the graph traces which inputs led "
                "to the outcome.\n\n"
                "Pass `template_name` + `status` (success/partial/failed) to ALSO update the "
                "agent's rolling trust score in /<harness>/AgentTrust/<template>. This is how "
                "trust scoring accumulates across runs — call it after wait_for_agent completes."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Session / workflow run id — namespaces the Outcomes space."},
                    "title": {"type": "string", "description": "Short outcome title with absolute date (e.g. 'Discovered Postgres index missing on Apr 26')."},
                    "kind": {
                        "type": "string",
                        "description": "Outcome kind. Defaults to 'discovery'.",
                        "enum": ["discovery", "decision", "lesson", "insight", "warning", "fact", "outcome"],
                        "default": "discovery",
                    },
                    "content": {"type": "string", "description": "Detailed outcome body. Markdown ok."},
                    "agent_id": {"type": "string", "description": "Recording agent's runtime id (sidecar uuid)."},
                    "agent_kref": {"type": "string", "description": "Recording agent's Kumiho kref, if it has one."},
                    "template_name": {
                        "type": "string",
                        "description": "Agent template name from the pool. When set together with `status`, also updates the rolling trust score for this template.",
                    },
                    "status": {
                        "type": "string",
                        "description": "How the agent performed on the task. Required (with `template_name`) to update trust score; otherwise optional. Omit for non-run outcomes (discoveries / lessons that aren't tied to a single agent's success).",
                        "enum": ["success", "partial", "failed"],
                    },
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "related_files": {"type": "array", "items": {"type": "string"}, "description": "File paths the outcome refers to."},
                    "related_krefs": {"type": "array", "items": {"type": "string"}, "description": "Kumiho krefs (artifacts, prior outcomes, plans) this outcome was derived from. Becomes INFORMS edges."},
                },
                "required": ["session_id", "title"],
            },
        ),
        Tool(
            name="record_skill_outcome",
            description=(
                "Record whether a single skill use succeeded or failed. Stores under "
                "<memory_project>/Skills/<skill>/Outcomes/ with an edge back to the "
                "skill revision so the graph traces each outcome to the exact skill "
                "version that produced it. Call this AFTER any non-trivial skill "
                "invocation — the data feeds get_skill_effectiveness which the "
                "prompt builder uses to rerank skills by recency-weighted success."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string", "description": "Slug of the skill (e.g. 'operator-orchestrator'). Either this or skill_kref required."},
                    "skill_kref": {"type": "string", "description": "Full kref of the skill revision exercised. Recorded as provenance edge."},
                    "success": {"type": "boolean", "description": "Whether the skill use achieved its goal."},
                    "summary": {"type": "string", "description": "Short summary of what happened (markdown ok)."},
                    "error": {"type": "string", "description": "Error message if success=false."},
                    "agent_id": {"type": "string"},
                    "session_id": {"type": "string"},
                    "duration_ms": {"type": "integer"},
                },
                "required": ["success"],
            },
        ),
        Tool(
            name="get_skill_effectiveness",
            description=(
                "Compute the rolling success rate for a skill from its recent recorded "
                "outcomes. Returns {total, successes, failures, rate, recent[]}. The "
                "Rust-side SkillsSection prompt builder consumes this to rerank skills "
                "before injecting them into agent system prompts (effectiveness-weighted "
                "selection)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string"},
                    "skill_kref": {"type": "string"},
                    "limit": {"type": "integer", "default": 50, "description": "Max outcomes to consider (1..500)."},
                },
            },
        ),
        Tool(
            name="recall_session_outcomes",
            description=(
                "Recall outcomes recorded by sibling agents in the same session (or a list of "
                "related sessions). Use this when a new agent / handoff target / next workflow "
                "step starts so it can inherit what siblings already learned. Pass `query` for "
                "semantic ranking, omit for a chronological list."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Primary session whose outcomes to read."},
                    "sibling_sessions": {"type": "array", "items": {"type": "string"}, "description": "Additional related session ids (e.g. earlier runs of the same workflow)."},
                    "query": {"type": "string", "description": "Optional natural-language query for semantic ranking."},
                    "kinds": {"type": "array", "items": {"type": "string"}, "description": "Filter to these outcome kinds."},
                    "limit": {"type": "integer", "default": 10},
                },
            },
        ),
        Tool(
            name="memory_engage",
            description=(
                "Recall + context-build in one call (operator-side equivalent of kumiho_memory_engage). "
                "Returns {context, results, source_krefs, count}. Pass `source_krefs` to memory_reflect "
                "to create DERIVED_FROM edges from new captures to the recalled memories. Use this before "
                "responding to anything that might have history."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": (
                        "Subject-focused search query DERIVED from the user's message — "
                        "not the message verbatim. Strip question framing ('do you recall', "
                        "'do you know', 'tell me about', trailing '?') and keep the "
                        "noun-phrase subject + concrete identifiers. "
                        "Examples: 'do you recall my arXiv paper?' → 'arXiv paper'; "
                        "'what did we decide about gRPC vs REST?' → 'gRPC vs REST decision'; "
                        "'tell me about my favourite colour' → 'favourite colour preference'."
                    )},
                    "project": {"type": "string", "default": "CognitiveMemory"},
                    "space_paths": {"type": "array", "items": {"type": "string"}, "description": "Restrict search to these space paths."},
                    "memory_types": {"type": "array", "items": {"type": "string"}, "description": "Filter by memory_type ('decision','fact','preference',...)"},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                    "topics": {"type": "array", "items": {"type": "string"}},
                    "limit": {
                        "type": "integer",
                        "default": configured_memory_limit,
                        "maximum": configured_memory_limit,
                        "description": (
                            "Max results to return. Defaults to and is capped by "
                            "Construct's [kumiho].memory_retrieval_limit."
                        ),
                    },
                    "min_score": {
                        "type": "number",
                        "default": configured_memory_min_score,
                        "minimum": configured_memory_min_score,
                        "maximum": 1.0,
                        "description": (
                            "Minimum relevance score required for results to be included "
                            "in returned context. Defaults to and is floored by "
                            "Construct's [memory].min_relevance_score."
                        ),
                    },
                    "mode": {"type": "string", "enum": ["search", "latest"], "default": "search"},
                    "memory_item_kind": {"type": "string", "default": "conversation"},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="memory_reflect",
            description=(
                "Store structured captures with provenance edges (operator-side equivalent of "
                "kumiho_memory_reflect). Each capture is {type, title, content, tags?, space_hint?}. "
                "Pass `source_krefs` from a prior memory_engage call so the new captures get DERIVED_FROM "
                "edges back to the recalled memories. Use this after substantive turns to record decisions, "
                "facts, preferences, or corrections."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Session identifier for traceability."},
                    "response": {"type": "string", "description": "The assistant response text (for context — operator does not buffer like the agent-side server does)."},
                    "captures": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "description": "decision | fact | preference | summary | architecture | implementation | skill | outcome | correction | reflection"},
                                "title": {"type": "string", "description": "Short title with absolute date (e.g. 'Chose gRPC on Apr 26')."},
                                "content": {"type": "string"},
                                "tags": {"type": "array", "items": {"type": "string"}},
                                "space_hint": {"type": "string", "description": "Override space_path for this capture."},
                            },
                            "required": ["type", "title", "content"],
                        },
                    },
                    "source_krefs": {"type": "array", "items": {"type": "string"}, "description": "Krefs from a prior engage — creates DERIVED_FROM edges."},
                    "space_path": {"type": "string", "description": "Default space path for captures without a space_hint."},
                    "project": {"type": "string", "default": "CognitiveMemory"},
                },
                "required": ["session_id", "response"],
            },
        ),
        Tool(
            name="memory_retrieve",
            description=(
                "Fuzzy memory retrieval — Google-like semantic search across Kumiho memory items. "
                "Best for natural-language queries ('what did we decide about gRPC vs REST'). "
                "Searches under CognitiveMemory by default."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural-language query."},
                    "project": {"type": "string", "description": "Kumiho project (default 'CognitiveMemory')."},
                    "space_paths": {"type": "array", "items": {"type": "string"}, "description": "Optional list of space paths to scope the search."},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                    "topics": {"type": "array", "items": {"type": "string"}},
                    "memory_item_kind": {"type": "string", "default": "conversation"},
                    "memory_types": {"type": "array", "items": {"type": "string"}, "description": "Filter by memory_type ('decision','fact','preference','summary',...)"},
                    "limit": {"type": "integer", "default": 5},
                    "mode": {"type": "string", "enum": ["search", "latest"], "default": "search"},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="memory_search",
            description=(
                "Structured search by name/kind/context. Use for exact-match lookups when you "
                "already know the item kind or partial name. For natural-language queries, prefer memory_retrieve."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "context_filter": {"type": "string", "description": "Project or space path filter, e.g. 'CognitiveMemory/Skills'."},
                    "name_filter": {"type": "string", "description": "Wildcard name filter (e.g. 'hero*')."},
                    "kind_filter": {"type": "string", "description": "Filter by item kind (e.g. 'workflow', 'skill')."},
                    "include_metadata": {"type": "boolean", "default": False},
                },
            },
        ),
        Tool(
            name="memory_fulltext",
            description=(
                "Full-text fuzzy search across Kumiho items (Google-like). Returns matches with "
                "snippets. Use when you need keyword-style search rather than semantic recall."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "context": {"type": "string", "description": "Project/space path scope."},
                    "kind": {"type": "string"},
                    "include_deprecated": {"type": "boolean", "default": False},
                    "include_revision_metadata": {"type": "boolean", "default": False},
                    "limit": {"type": "integer", "default": 20},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="memory_get_item",
            description="Fetch a Kumiho item by its kref URI (e.g. 'kref://CognitiveMemory/Skills/operator-orchestrator.skill').",
            inputSchema={
                "type": "object",
                "properties": {"kref": {"type": "string"}},
                "required": ["kref"],
            },
        ),
        Tool(
            name="memory_resolve_kref",
            description="Resolve a kref URI into its concrete item/revision identifiers.",
            inputSchema={
                "type": "object",
                "properties": {"kref": {"type": "string"}},
                "required": ["kref"],
            },
        ),
        Tool(
            name="memory_get_revision_by_tag",
            description="Get a specific revision of an item by tag (e.g. 'published', 'stable', 'experimental').",
            inputSchema={
                "type": "object",
                "properties": {
                    "item_kref": {"type": "string"},
                    "tag": {"type": "string", "default": "published"},
                },
                "required": ["item_kref"],
            },
        ),
        Tool(
            name="memory_store",
            description=(
                "Store a memory bundle (decision/fact/preference/summary). Auto-stacks revisions on "
                "similar items by default. Use this to record orchestration decisions, sub-agent outcomes, "
                "or cross-session lessons. Do not use for skill guides or requests to capture/save a skill; "
                "use capture_skill instead so SKILL.md is stored as a revision artifact."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "memory_type": {"type": "string", "description": "'decision','fact','preference','summary','skill','outcome',...", "default": "summary"},
                    "memory_item_kind": {"type": "string", "default": "conversation"},
                    "project": {"type": "string", "default": "CognitiveMemory"},
                    "space_path": {"type": "string"},
                    "space_hint": {"type": "string"},
                    "bundle_name": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "source_revision_krefs": {"type": "array", "items": {"type": "string"}, "description": "Krefs that this memory was derived from (provenance)."},
                    "metadata": {"type": "object", "additionalProperties": True},
                    "edge_type": {"type": "string", "default": "DERIVED_FROM"},
                    "stack_revisions": {"type": "boolean", "default": True},
                    "user_text": {"type": "string"},
                    "assistant_text": {"type": "string"},
                },
                "required": ["title"],
            },
        ),
        Tool(
            name="memory_graph",
            description=(
                "Aggregate memory items, revisions, and edges into a single graph payload "
                "for the Obsidian-style force-graph visualization. Uses direct SDK (no HTTP hop)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": f"Kumiho project name (default: {os.environ.get('KUMIHO_MEMORY_PROJECT', 'CognitiveMemory')}).",
                        "default": os.environ.get("KUMIHO_MEMORY_PROJECT", "CognitiveMemory"),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max items to include (default 100, max 500).",
                        "default": 100,
                    },
                    "kinds": {
                        "type": "string",
                        "description": "Comma-separated kind filter (e.g. 'decision,fact,preference').",
                    },
                    "space": {
                        "type": "string",
                        "description": "Space path filter — only include items from this space.",
                    },
                    "sort": {
                        "type": "string",
                        "description": "Sort mode: 'recent' (default), 'name'.",
                        "enum": ["recent", "name"],
                        "default": "recent",
                    },
                    "search": {
                        "type": "string",
                        "description": "Fulltext search query — filters to matching items.",
                    },
                },
            },
        ),
    ]


# -- Tool dispatch ---------------------------------------------------------

# Expected top-level keys per tool — used for cross-wiring detection.
# A missing key is OK (some responses are minimal); an unexpected schema
# coming from the wrong handler would show up as a key-set mismatch.
_EXPECTED_KEYS: dict[str, frozenset[str]] = {
    "create_agent": frozenset({"agent_id", "type", "status", "cwd", "title"}),
    "list_agents": frozenset({"agents"}),
    "search_agent_pool": frozenset({"matches", "count"}),
    "get_team": frozenset({"team", "kref"}),
    "get_agent_activity": frozenset({"agent_id"}),
    "get_session_history": frozenset({"session_id", "events"}),
    "get_workflow_context": frozenset({"session_id", "agent_count", "findings"}),
    "memory_graph": frozenset({"nodes", "edges", "spaces", "stats"}),
}

_req_seq = _count(1)

@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    req_id = next(_req_seq)
    t0 = time.monotonic()
    _log(f"REQ#{req_id} ENTER tool={name}")

    # CRITICAL: Catch ALL exceptions including CancelledError (BaseException).
    # If any exception propagates to the MCP framework as an MCP-level error,
    # Claude Code's response queue desyncs — every subsequent response shifts
    # to the previous caller.  This single try/except prevents that cascade.
    try:
        try:
            result = await _dispatch(name, arguments)
        except Exception as exc:
            _log(f"Tool {name} error: {exc}")
            from .failure_classification import classify_exception
            result = classify_exception(exc)
    except BaseException as exc:
        # CancelledError, KeyboardInterrupt, SystemExit, etc.
        # Return a clean tool response instead of letting the MCP framework
        # generate an error that desyncs the client's response queue.
        _log(f"Tool {name} BaseException (prevented MCP desync): {type(exc).__name__}: {exc}")
        result = {"error": f"Tool interrupted: {type(exc).__name__}", "status": "cancelled", "_interrupted": True}

    elapsed = time.monotonic() - t0

    # Tag every response with the tool name and request_id for tracing.
    # Shallow-copy to avoid mutating a dict that may be cached or shared
    # across concurrent handler calls (defensive against cross-wiring).
    if isinstance(result, dict):
        result = {**result, "_tool": name, "_req_id": req_id}

    # Cross-wiring diagnostic: log when a response doesn't match
    # the expected schema for its tool name.
    if isinstance(result, dict) and name in _EXPECTED_KEYS:
        expected = _EXPECTED_KEYS[name]
        actual = frozenset(result.keys()) - {"_tool", "_req_id"}
        if expected and not expected & actual and "error" not in result:
            _log(
                f"CROSS-WIRE WARNING: tool={name} returned keys={sorted(actual)} "
                f"but expected some of {sorted(expected)}"
            )

    # Log exit with timing and result summary
    status = result.get("status", "") if isinstance(result, dict) else ""
    _log(f"REQ#{req_id} EXIT  tool={name} elapsed={elapsed:.2f}s status={status}")

    return [TextContent(type="text", text=json.dumps(result, default=str))]


def _handle_workflow_context(args: dict[str, Any]) -> dict[str, Any]:
    """Handle get_workflow_context tool calls."""
    view = args.get("view", "summary")
    if view == "summary":
        return WORKFLOW_CTX.summary()
    if view == "findings":
        status_filter = args.get("status_filter")
        return {"findings": WORKFLOW_CTX.get_findings(status_filter=status_filter)}
    if view == "finding":
        agent_id = args.get("agent_id", "")
        if not agent_id:
            return {"error": "agent_id required for 'finding' view"}
        finding = WORKFLOW_CTX.get_finding(agent_id)
        if finding is None:
            return {"error": f"No finding for agent {agent_id}"}
        return finding
    return {"error": f"Unknown view: {view}. Use: summary, findings, finding"}


async def _dispatch(name: str, args: dict[str, Any]) -> dict[str, Any]:
    # Import handlers lazily to keep module-level imports light
    from .tool_handlers import agents, pool, teams, planning, trust, skills, clawhub, canvas, nodes, session

    # -- Agent lifecycle --
    if name == "create_agent":
        return await agents.tool_create_agent(args, JOURNAL, KUMIHO_POOL)
    if name == "wait_for_agent":
        return await agents.tool_wait_for_agent(args)
    if name == "send_agent_prompt":
        return await agents.tool_send_agent_prompt(args, JOURNAL)
    if name == "get_agent_activity":
        return await agents.tool_get_agent_activity(args)
    if name == "get_agent_run_log":
        return await agents.tool_get_agent_run_log(args)
    if name == "list_run_logs":
        return await agents.tool_list_run_logs()
    if name == "get_circuit_breaker_status":
        return await agents.tool_get_circuit_breaker_status()
    if name == "reset_circuit_breaker":
        return await agents.tool_reset_circuit_breaker()
    if name == "get_retry_queue_status":
        from .retry_queue import get_retry_queue
        return get_retry_queue().status()
    if name == "get_agent_health":
        from .heartbeat import tool_get_agent_health
        return await tool_get_agent_health(args)
    if name == "get_journal_health":
        from .journal_health import tool_get_journal_health
        return await tool_get_journal_health(args)
    if name == "diff_agent_artifacts":
        from .artifact_diff import tool_diff_agent_artifacts
        return await tool_diff_agent_artifacts(args)
    if name == "list_agents":
        return await agents.tool_list_agents()
    if name == "get_workflow_context":
        return _handle_workflow_context(args)
    if name == "cancel_agent":
        return await agents.tool_cancel_agent(args)
    if name == "cancel_all_agents":
        return await agents.tool_cancel_all_agents(args)

    # -- Agent pool --
    if name == "search_agent_pool":
        return await pool.tool_search_agent_pool(args, KUMIHO_POOL)
    if name == "save_agent_template":
        return await pool.tool_save_agent_template(args, KUMIHO_POOL)
    if name == "list_agent_templates":
        return await pool.tool_list_agent_templates(KUMIHO_POOL)

    # -- Teams --
    if name == "list_teams":
        return await teams.tool_list_teams(KUMIHO_TEAMS)
    if name == "get_team":
        return await teams.tool_get_team(args, KUMIHO_TEAMS)
    if name == "spawn_team":
        return await teams.tool_spawn_team(args, KUMIHO_TEAMS, JOURNAL)
    if name == "create_team":
        return await teams.tool_create_team(args, KUMIHO_TEAMS, KUMIHO_POOL)
    if name == "search_teams":
        return await teams.tool_search_teams(args, KUMIHO_TEAMS)
    if name == "get_spawn_progress":
        return await teams.tool_get_spawn_progress(args)
    if name == "lint_team":
        return await teams.tool_lint_team(args, KUMIHO_TEAMS)
    if name == "resolve_outcome":
        return await teams.tool_resolve_outcome(args)
    if name == "get_outcome_lineage":
        return await teams.tool_get_outcome_lineage(args)
    if name == "list_workflow_presets":
        from .workflow_presets import tool_list_workflow_presets
        return await tool_list_workflow_presets(args)
    if name == "save_workflow_preset":
        from .workflow_presets import tool_save_workflow_preset
        return await tool_save_workflow_preset(args)
    if name == "get_workflow_plan":
        from .workflow_presets import tool_get_workflow_plan
        return await tool_get_workflow_plan(args)
    if name == "review_fix_loop":
        from .review_loop import tool_review_fix_loop
        return await tool_review_fix_loop(args)
    if name == "refinement_loop":
        from .patterns.refinement import tool_refinement_loop
        return await tool_refinement_loop(args)

    # -- A2A Protocol --
    if name == "a2a_get_card":
        from .a2a.a2a_registry import get_registry
        registry = get_registry()
        template_name = args.get("template_name")
        if template_name:
            card = registry.get_card(template_name)
            if card:
                return card
            return {"error": f"No A2A card for template: {template_name}"}
        return registry.get_composite_card()
    if name == "a2a_handle_request":
        from .a2a.task_handler import get_handler
        handler = get_handler()
        return await handler.handle_jsonrpc(args.get("request", {}))
    if name == "a2a_list_tasks":
        from .a2a.task_handler import get_handler
        handler = get_handler()
        return handler.store.list_tasks(
            context_id=args.get("context_id"),
            limit=args.get("limit", 50),
        )

    # -- Orchestration Patterns --
    if name == "handoff_agent":
        from .patterns.handoff import tool_handoff_agent
        return await tool_handoff_agent(args)
    if name == "group_chat":
        from .patterns.group_chat import tool_group_chat
        return await tool_group_chat(args)
    if name == "supervisor_run":
        from .patterns.supervisor import tool_supervisor_run
        return await tool_supervisor_run(args)
    if name == "map_reduce":
        from .patterns.map_reduce import tool_map_reduce
        return await tool_map_reduce(args)
    # -- Declarative Workflow Engine --
    if name == "run_workflow":
        from .tool_handlers.workflows import tool_run_workflow
        return await tool_run_workflow(args)
    if name == "get_workflow_status":
        from .tool_handlers.workflows import tool_get_workflow_status
        return await tool_get_workflow_status(args)
    if name == "list_workflows":
        from .tool_handlers.workflows import tool_list_workflows
        return await tool_list_workflows(args)
    if name == "cancel_workflow":
        from .tool_handlers.workflows import tool_cancel_workflow
        return await tool_cancel_workflow(args)
    if name == "validate_workflow":
        from .tool_handlers.workflows import tool_validate_workflow
        return await tool_validate_workflow(args)
    if name == "revise_workflow":
        from .tool_handlers.workflow_revisions import tool_revise_workflow
        return await tool_revise_workflow(args)
    if name == "propose_workflow_yaml":
        from .tool_handlers.architect_propose import tool_propose_workflow_yaml
        return await tool_propose_workflow_yaml(args)
    if name == "create_workflow":
        from .tool_handlers.workflows import tool_create_workflow
        return await tool_create_workflow(args)
    if name == "resume_workflow":
        from .tool_handlers.workflows import tool_resume_workflow
        return await tool_resume_workflow(args)

    if name == "retry_workflow":
        from .tool_handlers.workflows import tool_retry_workflow
        return await tool_retry_workflow(args)

    # -- Manus ad-hoc research (web/research category) --
    # Wraps the same `manus_run_task` helper the workflow `manus:` step
    # uses, so the Operator agent can invoke a Manus task mid-conversation
    # for ad-hoc browser/web research without authoring a workflow.
    if name == "manus_create_task":
        from .tool_handlers.manus import tool_manus_create_task
        return await tool_manus_create_task(args)

    # -- A2A Outbound Client --
    if name == "a2a_discover":
        from .a2a.a2a_client import tool_a2a_discover
        return await tool_a2a_discover(args)
    if name == "a2a_send_task":
        from .a2a.a2a_client import tool_a2a_send_task
        return await tool_a2a_send_task(args)
    if name == "a2a_get_remote_task":
        from .a2a.a2a_client import tool_a2a_get_task
        return await tool_a2a_get_task(args)
    # -- Workflow Memory --
    if name == "recall_workflow_runs":
        from .workflow.memory import tool_recall_workflow_runs
        return await tool_recall_workflow_runs(args)
    if name == "get_workflow_run_detail":
        from .workflow.memory import tool_get_workflow_run_detail
        return await tool_get_workflow_run_detail(args)
    if name == "dry_run_workflow":
        from .workflow.loader import get_workflow, load_workflow_from_dict
        from .workflow.executor import dry_run_workflow
        wf_name = args.get("workflow", "")
        wf_def = args.get("workflow_def")
        inputs = args.get("inputs", {})
        cwd = args.get("cwd")
        wf = None
        if wf_def and isinstance(wf_def, dict):
            try:
                wf = load_workflow_from_dict(wf_def)
            except Exception as exc:
                return {"valid": False, "errors": [{"message": str(exc)}]}
        elif wf_name:
            wf = get_workflow(wf_name, project_dir=cwd)
            if not wf:
                return {"valid": False, "errors": [{"message": f"Workflow '{wf_name}' not found"}]}
        else:
            return {"valid": False, "errors": [{"message": "workflow or workflow_def required"}]}
        return dry_run_workflow(wf, inputs)
    if name == "get_workflow_metadata":
        from .tool_handlers.workflow_discovery import tool_get_workflow_metadata
        return await tool_get_workflow_metadata(args)
    if name == "system_dashboard":
        from .tool_handlers.dashboard import tool_system_dashboard
        return await tool_system_dashboard(args)
    if name == "memory_graph":
        from .tool_handlers.memory_graph import tool_memory_graph
        return await tool_memory_graph(args, KUMIHO_SDK)
    if name == "record_agent_outcome":
        from .tool_handlers.outcomes import tool_record_agent_outcome_op
        # KUMIHO_POOL is passed so the handler can fold in a trust-score
        # update when the caller supplies template_name + status. Used to
        # be a separate tool that clashed with this one's name.
        return await tool_record_agent_outcome_op(args, KUMIHO_POOL)
    if name == "recall_session_outcomes":
        from .tool_handlers.outcomes import tool_recall_session_outcomes_op
        return await tool_recall_session_outcomes_op(args)
    if name == "record_skill_outcome":
        from .tool_handlers.skill_outcomes import tool_record_skill_outcome_op
        return await tool_record_skill_outcome_op(args)
    if name == "get_skill_effectiveness":
        from .tool_handlers.skill_outcomes import tool_get_skill_effectiveness_op
        return await tool_get_skill_effectiveness_op(args)
    if name == "memory_engage":
        from .tool_handlers.memory import tool_memory_engage_op
        return await tool_memory_engage_op(args)
    if name == "memory_reflect":
        from .tool_handlers.memory import tool_memory_reflect_op
        return await tool_memory_reflect_op(args)
    if name == "memory_retrieve":
        from .tool_handlers.memory import tool_memory_retrieve_op
        return await tool_memory_retrieve_op(args)
    if name == "memory_search":
        from .tool_handlers.memory import tool_memory_search_op
        return await tool_memory_search_op(args)
    if name == "memory_fulltext":
        from .tool_handlers.memory import tool_memory_fulltext_op
        return await tool_memory_fulltext_op(args)
    if name == "memory_get_item":
        from .tool_handlers.memory import tool_memory_get_item_op
        return await tool_memory_get_item_op(args)
    if name == "memory_resolve_kref":
        from .tool_handlers.memory import tool_memory_resolve_kref_op
        return await tool_memory_resolve_kref_op(args)
    if name == "memory_get_revision_by_tag":
        from .tool_handlers.memory import tool_memory_get_revision_by_tag_op
        return await tool_memory_get_revision_by_tag_op(args)
    if name == "memory_store":
        from .tool_handlers.memory import tool_memory_store_op
        return await tool_memory_store_op(args)
    if name == "check_policy":
        from .policy import tool_check_policy
        return await tool_check_policy(args)
    if name == "get_policy_summary":
        from .policy import tool_get_policy_summary
        return await tool_get_policy_summary(args)

    # -- Planning & goals --
    if name == "save_plan":
        return await planning.tool_save_plan(args, KUMIHO_POOL)
    if name == "recall_plans":
        return await planning.tool_recall_plans(args, KUMIHO_POOL)
    if name == "create_goal":
        return await planning.tool_create_goal(args, KUMIHO_POOL)
    if name == "get_goals":
        return await planning.tool_get_goals(args, KUMIHO_POOL)
    if name == "update_goal":
        return await planning.tool_update_goal(args, KUMIHO_POOL)

    # -- Budget --
    if name == "get_budget_status":
        gw_cost = await CONSTRUCT_GW.get_cost_summary()
        if not gw_cost:
            return {
                "error": "Gateway budget authority unavailable",
                "source": "gateway",
                "budget": {"state": "unknown", "enabled": False},
            }
        result: dict[str, Any] = dict(gw_cost)
        result["source"] = "gateway"
        gw_status = await CONSTRUCT_GW.get_status()
        if gw_status:
            result["provider"] = gw_status.get("provider")
            result["model"] = gw_status.get("model")
        return result

    # -- Trust --
    # `record_agent_outcome` dispatch lives at the outcomes branch above —
    # trust scoring is folded into the outcomes flow when the caller passes
    # template_name + status. Two tools with the same name caused dispatch
    # ambiguity (LLM saw one schema, dispatcher fired the other handler).
    if name == "get_agent_trust":
        return await trust.tool_get_agent_trust(args, KUMIHO_POOL)

    # -- Skills --
    if name == "capture_skill":
        return await skills.tool_capture_skill(args, KUMIHO_POOL)
    if name == "list_skills":
        return await skills.tool_list_skills(args, KUMIHO_POOL)
    if name == "load_skill":
        return await skills.tool_load_skill(args, KUMIHO_POOL)

    # -- ClawHub --
    if name == "search_clawhub":
        return await clawhub.tool_search_clawhub(args, CONSTRUCT_GW)
    if name == "install_from_clawhub":
        return await clawhub.tool_install_from_clawhub(args, CONSTRUCT_GW)
    if name == "browse_clawhub":
        return await clawhub.tool_browse_clawhub(args, CONSTRUCT_GW)

    # -- Canvas --
    if name == "render_canvas":
        return await canvas.tool_render_canvas(args, CONSTRUCT_GW)
    if name == "clear_canvas":
        return await canvas.tool_clear_canvas(args, CONSTRUCT_GW)

    # -- Codex image generation --
    if name == "generate_image_codex":
        from .tool_handlers import codex_image
        return await codex_image.tool_generate_image_codex(args, CONSTRUCT_GW)

    # -- Nodes --
    if name == "list_nodes":
        return await nodes.tool_list_nodes(CONSTRUCT_GW)
    if name == "invoke_node":
        return await nodes.tool_invoke_node(args, CONSTRUCT_GW)

    # -- Permissions --
    from .tool_handlers import permissions as perm_handlers
    if name == "list_pending_permissions":
        return await perm_handlers.tool_list_pending_permissions(SIDECAR)
    if name == "respond_to_permission":
        return await perm_handlers.tool_respond_to_permission(args, SIDECAR)

    # -- Chat --
    from .tool_handlers import chat
    if name == "chat_create":
        return await chat.tool_chat_create(args, SIDECAR)
    if name == "chat_post":
        return await chat.tool_chat_post(args, SIDECAR)
    if name == "chat_read":
        return await chat.tool_chat_read(args, SIDECAR)
    if name == "chat_list":
        return await chat.tool_chat_list(SIDECAR)
    if name == "chat_wait":
        return await chat.tool_chat_wait(args, SIDECAR)
    if name == "chat_delete":
        return await chat.tool_chat_delete(args, SIDECAR)

    # -- Session --
    if name == "get_session_history":
        return await session.tool_get_session_history(args, JOURNAL)
    if name == "archive_session":
        return await session.tool_archive_session(args, JOURNAL, KUMIHO_POOL)

    # -- Compaction (B1) --
    if name == "compact_conversation":
        from .tool_handlers import compact
        return await compact.tool_compact_conversation(args, JOURNAL, KUMIHO_SDK)
    if name == "store_compaction":
        from .tool_handlers import compact
        return await compact.tool_store_compaction(args, JOURNAL, KUMIHO_SDK)

    return {"error": f"Unknown tool: {name}"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _background_init() -> None:
    """Heavy service initialization — runs as a background task so it does
    not block the MCP initialize handshake."""

    # Lazy-init Kumiho SDK (network I/O for endpoint discovery)
    KUMIHO_SDK._lazy_init()

    # Wire sidecar + event consumer into agent handlers
    from .tool_handlers.agents import set_sidecar, set_workflow_context
    set_sidecar(SIDECAR, EVENT_CONSUMER)

    # Wire workflow context into agent handlers for auto-capture
    WORKFLOW_CTX.set_kumiho_sdk(KUMIHO_SDK)
    set_workflow_context(WORKFLOW_CTX)

    # Wire gateway client into workflow handlers for Kumiho sync
    from .tool_handlers.workflows import set_gateway_client
    set_gateway_client(CONSTRUCT_GW)

    # Log channel events to journal
    EVENT_CONSUMER.on_channel_event(
        lambda ev: JOURNAL.record(
            ev.get("agentId", ""),
            f"channel:{ev.get('type', '')}",
            **ev.get("content", {}),
        )
    )

    # Try to start the global event stream (runs in background, reconnects as needed)
    if await SIDECAR.ensure_running():
        await EVENT_CONSUMER.start_global_stream()
        _log("Event consumer: global stream active")
    else:
        _log("Event consumer: sidecar not available, will use per-agent streams on demand")

    # Start retry queue for transient failure recovery
    from .retry_queue import get_retry_queue
    retry_q = get_retry_queue()
    await retry_q.start()

    # Start heartbeat monitor for agent liveness detection
    from .heartbeat import get_heartbeat_monitor
    hb = get_heartbeat_monitor()
    if SIDECAR:
        hb.set_sidecar_client(SIDECAR)
    await hb.start()

    # Reconnect to sidecar agents that survived a restart
    from .reconnect import reconnect_agents
    recovered = await reconnect_agents(SIDECAR, JOURNAL, EVENT_CONSUMER)
    if recovered:
        _log(f"Reconnected {len(recovered)} agent(s) from previous session")

    # Do not auto-resume interrupted workflow runs on restart. Mark stale runs
    # failed so the user can explicitly choose Retry from the run page.
    try:
        from .workflow.memory import mark_stale_runs
        marked_stale = await mark_stale_runs()
        if marked_stale:
            _log(f"Workflow startup: marked {marked_stale} interrupted run(s) failed; retry is user-initiated")
    except Exception as exc:
        _log(f"Workflow stale-run scan failed (non-fatal): {exc}")

    # Start journal health monitor
    from .journal_health import get_journal_health_monitor
    jhm = get_journal_health_monitor()
    jhm.set_journal(JOURNAL)
    await jhm.start()

    # Start workflow event listener (Kumiho event stream -> trigger downstream workflows)
    try:
        build_trigger_registry()
        _event_listener = WorkflowEventListener(
            registry=get_trigger_registry(),
            cwd=os.path.expanduser("~"),
        )
        set_event_listener(_event_listener)
        await _event_listener.start()
        _log("Event listener: watching for workflow triggers")
    except Exception as e:
        _log(f"Event listener failed to start (non-fatal): {e}")


async def _run() -> None:
    _log("Starting Construct Operator MCP Server...")

    # Start the MCP stdio server FIRST so we respond to 'initialize'
    # immediately.  Heavy service init runs as a background task.
    async with stdio_server() as (read_stream, write_stream):
        asyncio.create_task(_background_init())
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
