#!/usr/bin/env python3
"""Reduced MCP surface for Google Agent Platform workflow agents."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

_log = lambda msg: print(f"[google-agentops-tools] {msg}", file=sys.stderr, flush=True)

app = Server("google-agentops-tools")


def _google_agents_cli_tool() -> Tool:
    return Tool(
        name="google_agents_cli",
        description=(
            "Run Google Agents CLI (agents-cli) lifecycle commands for ADK/A2A "
            "agents: setup, create, scaffold, install, lint, run, eval, deploy, "
            "publish, infra, data-ingestion, playground, update, login --status, "
            "and info. Uses argv tokens, not a shell."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "command": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Arguments after agents-cli, e.g. ['deploy', '--no-wait'], "
                        "['eval', 'run'], ['publish', 'gemini-enterprise', '--list'], "
                        "or ['run']."
                    ),
                },
                "prompt": {
                    "type": "string",
                    "description": "Prompt appended to `agents-cli run`. If command is omitted, defaults to ['run'].",
                },
                "working_directory": {
                    "type": "string",
                    "description": "ADK project directory. Defaults to Revka workspace.",
                },
                "timeout": {
                    "type": "number",
                    "description": "Maximum runtime in seconds. Default 600.",
                },
                "max_output_bytes": {
                    "type": "integer",
                    "description": "Maximum stdout bytes before truncation. Default 2097152.",
                },
                "allow_interactive": {
                    "type": "boolean",
                    "description": "Allow interactive flags. Defaults to false.",
                    "default": False,
                },
                "env_passthrough": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Additional environment variable names to pass through.",
                },
            },
        },
    )


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        _google_agents_cli_tool(),
        Tool(
            name="a2a_discover",
            description="Discover an external A2A agent by URL. Fetches the agent card from .well-known/agent-card.json and caches it.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Base URL of the external agent."},
                    "cloud_run_identity_token": {
                        "type": "string",
                        "description": (
                            "Optional Google identity token for private Cloud Run services. "
                            "Sent as X-Serverless-Authorization: Bearer <token>."
                        ),
                    },
                    "cloud_run_auth": {
                        "type": "string",
                        "description": "Set to 'gcloud' to mint a Cloud Run identity token with gcloud.",
                    },
                    "cloud_run_config": {
                        "type": "string",
                        "description": "Optional gcloud configuration name to use when minting the Cloud Run identity token.",
                    },
                    "cloud_run_audience": {
                        "type": "string",
                        "description": "Optional Cloud Run token audience. Defaults to the service origin URL.",
                    },
                    "cloud_run_auth_timeout": {
                        "type": "number",
                        "description": "Maximum seconds to wait for gcloud identity-token minting. Default 20.",
                        "default": 20,
                    },
                    "auth_token": {
                        "type": "string",
                        "description": (
                            "Optional A2A application bearer token. Sent as "
                            "Authorization: Bearer <token>."
                        ),
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Discovery timeout in seconds. Default 30.",
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
                    "url": {"type": "string", "description": "A2A endpoint URL."},
                    "message": {"type": "string", "description": "Task message text."},
                    "skill_id": {"type": "string", "description": "Optional skill ID to route to on the remote agent."},
                    "cloud_run_identity_token": {
                        "type": "string",
                        "description": (
                            "Optional Google identity token for private Cloud Run services. "
                            "Sent as X-Serverless-Authorization: Bearer <token>."
                        ),
                    },
                    "cloud_run_auth": {
                        "type": "string",
                        "description": "Set to 'gcloud' to mint a Cloud Run identity token with gcloud.",
                    },
                    "cloud_run_config": {
                        "type": "string",
                        "description": "Optional gcloud configuration name to use when minting the Cloud Run identity token.",
                    },
                    "cloud_run_audience": {
                        "type": "string",
                        "description": "Optional Cloud Run token audience. Defaults to the service origin URL.",
                    },
                    "cloud_run_auth_timeout": {
                        "type": "number",
                        "description": "Maximum seconds to wait for gcloud identity-token minting. Default 20.",
                        "default": 20,
                    },
                    "auth_token": {
                        "type": "string",
                        "description": (
                            "Optional A2A application bearer token. Sent as "
                            "Authorization: Bearer <token>."
                        ),
                    },
                    "wait": {
                        "type": "boolean",
                        "description": "Poll until task completes. Default false.",
                        "default": False,
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Request timeout in seconds. Default 60.",
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
                    "url": {"type": "string", "description": "A2A endpoint URL."},
                    "task_id": {"type": "string", "description": "Task ID to check."},
                    "cloud_run_identity_token": {
                        "type": "string",
                        "description": (
                            "Optional Google identity token for private Cloud Run services. "
                            "Sent as X-Serverless-Authorization: Bearer <token>."
                        ),
                    },
                    "cloud_run_auth": {
                        "type": "string",
                        "description": "Set to 'gcloud' to mint a Cloud Run identity token with gcloud.",
                    },
                    "cloud_run_config": {
                        "type": "string",
                        "description": "Optional gcloud configuration name to use when minting the Cloud Run identity token.",
                    },
                    "cloud_run_audience": {
                        "type": "string",
                        "description": "Optional Cloud Run token audience. Defaults to the service origin URL.",
                    },
                    "cloud_run_auth_timeout": {
                        "type": "number",
                        "description": "Maximum seconds to wait for gcloud identity-token minting. Default 20.",
                        "default": 20,
                    },
                    "auth_token": {
                        "type": "string",
                        "description": (
                            "Optional A2A application bearer token. Sent as "
                            "Authorization: Bearer <token>."
                        ),
                    },
                },
                "required": ["url", "task_id"],
            },
        ),
        Tool(
            name="get_auth_token",
            description=(
                "Return the decrypted credentials for this step's bound auth "
                "profile. Use only when calling the external API; do not paste "
                "into chat or logs."
            ),
            inputSchema={"type": "object", "properties": {}},
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
    if name == "google_agents_cli":
        from operator_mcp.tool_handlers.google_agents_cli import tool_google_agents_cli
        return await tool_google_agents_cli(args)

    if name == "a2a_discover":
        from operator_mcp.a2a.a2a_client import tool_a2a_discover
        return await tool_a2a_discover(args)

    if name == "a2a_send_task":
        from operator_mcp.a2a.a2a_client import tool_a2a_send_task
        return await tool_a2a_send_task(args)

    if name == "a2a_get_remote_task":
        from operator_mcp.a2a.a2a_client import tool_a2a_get_task
        return await tool_a2a_get_task(args)

    if name == "get_auth_token":
        profile_id = os.environ.get("REVKA_AUTH_PROFILE_ID", "").strip()
        if not profile_id:
            return {"error": "no auth profile bound to this step", "code": "auth_profile_not_bound"}
        try:
            from operator_mcp.workflow.auth_resolver import AuthResolveError, resolve_auth_profile
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


async def _run() -> None:
    _log("Starting google-agentops-tools MCP server...")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
