#!/usr/bin/env python3
"""Operator-tools MCP server for sub-agents.

Lightweight MCP server exposing only agent lifecycle tools so sub-agents
can spawn children, monitor siblings, and coordinate hierarchically.

Communicates with the main operator sidecar via Unix socket HTTP — same
protocol as the operator itself uses.

Excluded tools (operator-only): archive, budget, goals, clawhub, canvas,
nodes, trust, skills, teams, plans, session history.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

# Add parent dir so we can import operator modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

_log = lambda msg: print(f"[operator-tools] {msg}", file=sys.stderr, flush=True)

# -- Sidecar client (reuses session_manager_client) -------------------------

_sidecar = None

def _get_sidecar():
    global _sidecar
    if _sidecar is None:
        from operator_mcp.session_manager_client import SessionManagerClient
        socket_path = os.environ.get(
            "CONSTRUCT_SIDECAR_SOCKET",
            os.path.expanduser("~/.construct/operator_mcp/session-manager.sock"),
        )
        _sidecar = SessionManagerClient(socket_path)
    return _sidecar


# -- MCP server --------------------------------------------------------------

app = Server("operator-tools")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="create_agent",
            description="Spawn a child agent (claude or codex). The child runs in its own session and can be monitored via wait_for_agent and get_agent_activity.",
            inputSchema={
                "type": "object",
                "properties": {
                    "cwd": {
                        "type": "string",
                        "description": "Working directory for the child agent.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Short title for this agent (max 60 chars).",
                        "maxLength": 60,
                    },
                    "agent_type": {
                        "type": "string",
                        "description": "Agent type.",
                        "enum": ["claude", "codex"],
                        "default": "claude",
                    },
                    "initial_prompt": {
                        "type": "string",
                        "description": "Task prompt for the child agent.",
                    },
                    "allowed_tools": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional tool allowlist. Only these tools will be available to the child agent.",
                    },
                    "max_turns": {
                        "type": "integer",
                        "description": "Max conversation turns (default 200).",
                        "default": 200,
                    },
                },
                "required": ["cwd", "title", "initial_prompt"],
            },
        ),
        Tool(
            name="wait_for_agent",
            description="Wait for a child agent to finish (up to 120s). Returns the agent's final status and output summary.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "The agent ID returned by create_agent.",
                    },
                },
                "required": ["agent_id"],
            },
        ),
        Tool(
            name="send_agent_prompt",
            description="Send a follow-up prompt to an idle child agent.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "The agent ID.",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "The follow-up prompt.",
                    },
                },
                "required": ["agent_id", "prompt"],
            },
        ),
        Tool(
            name="get_agent_activity",
            description="Get the current output and status of a child agent. Returns significant events (tool calls, completions, errors) and the last message.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "The agent ID.",
                    },
                },
                "required": ["agent_id"],
            },
        ),
        Tool(
            name="list_agents",
            description="List all active sibling and child agents with their status.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="chat_post",
            description="Post a message to a chat room. Use mentions to @notify specific agents.",
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
                        "description": "Your agent ID.",
                    },
                    "sender_name": {
                        "type": "string",
                        "description": "Your display name.",
                    },
                    "mentions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Agent IDs to @mention.",
                    },
                },
                "required": ["room_id", "content", "sender_id", "sender_name"],
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
        # -- Workflow tools --
        Tool(
            name="run_workflow",
            description=(
                "Execute a declarative YAML workflow by name. Workflows chain agents, "
                "shell commands, conditionals, and parallel steps. Returns immediately "
                "with a run_id — use get_workflow_status to poll progress."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "workflow": {
                        "type": "string",
                        "description": "Workflow name (e.g. 'quantum-soul-arc-room').",
                    },
                    "inputs": {
                        "type": "object",
                        "description": "Input parameters for the workflow.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory for agent/shell steps.",
                    },
                },
                "required": ["workflow", "cwd"],
            },
        ),
        Tool(
            name="get_workflow_status",
            description="Check the status of a workflow run. Returns step-by-step progress.",
            inputSchema={
                "type": "object",
                "properties": {
                    "run_id": {
                        "type": "string",
                        "description": "The run_id returned by run_workflow.",
                    },
                },
                "required": ["run_id"],
            },
        ),
        Tool(
            name="list_workflows",
            description="List all available declarative workflows.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="get_auth_token",
            description=(
                "Return the decrypted credentials for this step's bound auth "
                "profile. Use only when calling the external API; do not paste "
                "into chat or logs. Returns { token, kind, provider, "
                "profile_name, expires_at } on success, or { error, code } if "
                "no profile is bound or the profile is missing/expired."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        result = await _dispatch(name, arguments)
    except Exception as exc:
        _log(f"Tool {name} error: {exc}")
        result = {"error": str(exc)}

    return [TextContent(type="text", text=json.dumps(result, default=str))]


async def _dispatch(name: str, args: dict[str, Any]) -> dict[str, Any]:
    sidecar = _get_sidecar()

    if not await sidecar.ensure_running():
        return {"error": "Operator sidecar not available"}

    if name == "create_agent":
        expanded_cwd = os.path.realpath(os.path.expanduser(args["cwd"]))
        if not os.path.isdir(expanded_cwd):
            return {"error": f"Directory does not exist: {expanded_cwd}"}

        # Policy check — sub-agents respect the same boundaries
        from .policy import load_policy
        policy = load_policy()
        agent_type = args.get("agent_type", "claude")
        policy_failures = policy.preflight_spawn(expanded_cwd, agent_type)
        if policy_failures:
            fail = policy_failures[0]
            return {"error": f"Policy denied: {fail.reason}"}

        config = {
            "cwd": expanded_cwd,
            "agentType": agent_type,
            "prompt": args["initial_prompt"],
            "title": args["title"][:60],
        }
        # A2: Pass tool allowlist and max turns to sidecar
        if args.get("allowed_tools"):
            config["allowedTools"] = args["allowed_tools"]
        max_turns = args.get("max_turns", 200)
        if max_turns != 200:
            config["maxTurns"] = max_turns
        return await sidecar.create_agent(config)

    if name == "wait_for_agent":
        agent_id = args["agent_id"]
        for _ in range(240):  # 120s at 0.5s intervals
            info = await sidecar.get_agent(agent_id)
            if info is None:
                return {"error": f"Agent not found: {agent_id}"}
            status = info.get("status", "")
            if status in ("idle", "error", "closed"):
                # Fetch recent events for summary
                events = await sidecar.get_events(agent_id, since=0)
                last_msg = ""
                for ev in reversed(events):
                    inner = ev.get("event", ev)
                    if inner.get("type") == "timeline":
                        item = inner.get("item", {})
                        if item.get("type") == "assistant_message":
                            last_msg = item.get("text", "")[:2000]
                            break
                return {
                    "agent_id": agent_id,
                    "status": "completed" if status == "idle" else status,
                    "usage": info.get("usage"),
                    "last_message": last_msg,
                }
            await asyncio.sleep(0.5)
        return {"agent_id": agent_id, "status": "running", "message": "Timed out after 120s"}

    if name == "send_agent_prompt":
        return await sidecar.send_query(args["agent_id"], args["prompt"])

    if name == "get_agent_activity":
        agent_id = args["agent_id"]
        info = await sidecar.get_agent(agent_id)
        events = await sidecar.get_events(agent_id, since=0)
        significant = []
        last_msg = ""
        for ev in events:
            inner = ev.get("event", ev)
            ev_type = inner.get("type", "")
            if ev_type == "timeline":
                item = inner.get("item", {})
                if item.get("type") == "assistant_message":
                    last_msg = item.get("text", "")
                elif item.get("type") == "tool_call":
                    significant.append({
                        "type": "tool_call",
                        "name": item.get("name", ""),
                        "status": item.get("status", ""),
                    })
                elif item.get("type") == "error":
                    significant.append({
                        "type": "error",
                        "message": item.get("message", ""),
                    })
            elif ev_type == "turn_completed":
                significant.append({"type": "completed", "usage": inner.get("usage")})
            elif ev_type == "turn_failed":
                significant.append({"type": "failed", "error": inner.get("error", "")})

        return {
            "agent_id": agent_id,
            "status": info.get("status") if info else "unknown",
            "event_count": len(events),
            "significant_events": significant[-20:],
            "last_message": last_msg[-2000:] if last_msg else "",
        }

    if name == "list_agents":
        agents = await sidecar.list_agents()
        return {"agents": agents}

    # -- Chat --
    if name == "chat_post":
        return await sidecar.chat_post_message(
            args["room_id"],
            args["sender_id"],
            args["sender_name"],
            args["content"],
            args.get("mentions", []),
        )

    if name == "chat_read":
        messages = await sidecar.chat_read_messages(
            args["room_id"],
            args.get("limit", 50),
            args.get("since"),
        )
        return {"messages": messages, "count": len(messages)}

    if name == "chat_list":
        rooms = await sidecar.chat_list_rooms()
        return {"rooms": rooms, "count": len(rooms)}

    # -- Workflow tools --
    if name == "run_workflow":
        from operator_mcp.tool_handlers.workflows import tool_run_workflow
        return await tool_run_workflow(args)

    if name == "get_workflow_status":
        from operator_mcp.tool_handlers.workflows import tool_get_workflow_status
        return await tool_get_workflow_status(args)

    if name == "list_workflows":
        from operator_mcp.tool_handlers.workflows import tool_list_workflows
        return await tool_list_workflows(args)

    if name == "get_auth_token":
        # Resolves the auth profile id propagated via env. Token never lives
        # in this process for longer than the resolve call.
        profile_id = os.environ.get("CONSTRUCT_AUTH_PROFILE_ID", "").strip()
        if not profile_id:
            return {
                "error": "no auth profile bound to this step",
                "code": "auth_profile_not_bound",
            }
        try:
            from operator_mcp.workflow.auth_resolver import (
                AuthResolveError,
                resolve_auth_profile,
            )
            resolved = await resolve_auth_profile(profile_id)
        except AuthResolveError as exc:
            return {"error": str(exc), "code": exc.code}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc), "code": "auth_resolve_failed"}
        return {
            "token": resolved.get("token", ""),
            "kind": resolved.get("kind", ""),
            "provider": resolved.get("provider", ""),
            "profile_name": resolved.get("profile_name", ""),
            "expires_at": resolved.get("expires_at"),
        }

    return {"error": f"Unknown tool: {name}"}


# -- Entry point --------------------------------------------------------------

async def _run() -> None:
    _log("Starting operator-tools sub-agent MCP server...")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
