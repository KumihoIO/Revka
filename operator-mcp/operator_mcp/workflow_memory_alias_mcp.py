#!/usr/bin/env python3
"""Stable workflow-memory MCP aliases for child agents.

This intentionally exposes only the small Kumiho-backed surface workflow
agents need for capture/publish handoffs. It avoids depending on upstream
Kumiho MCP tool-name drift and avoids giving ``tools: memory`` agents the
broader operator-tools control surface.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from operator_mcp.kumiho_clients import KumihoAgentPoolClient
from operator_mcp.tool_handlers.skills import tool_capture_skill

_log = lambda msg: print(f"[workflow-memory] {msg}", file=sys.stderr, flush=True)

app = Server("workflow-memory")
_pool = KumihoAgentPoolClient()


def _capture_skill_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Stable skill name. Do not append agent IDs or run IDs.",
            },
            "domain": {
                "type": "string",
                "description": "Domain tag, e.g. rust, react, devops, writing.",
            },
            "description": {
                "type": "string",
                "description": "One-line summary of what this skill covers.",
            },
            "procedure": {
                "type": "string",
                "description": "Full procedure/instructions in Markdown.",
            },
            "learned_from": {
                "type": "string",
                "description": "Context that led to this skill.",
            },
            "agent_id": {
                "type": "string",
                "description": "Optional source agent/session ID.",
            },
            "change_summary": {
                "type": "string",
                "description": "For updates, how this revision improves the previous guide.",
            },
            "source_revision_krefs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional provenance krefs.",
            },
        },
        "required": ["name", "domain", "description", "procedure"],
    }


def _tag_revision_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "revision_kref": {
                "type": "string",
                "description": "Revision kref to tag.",
            },
            "kref": {
                "type": "string",
                "description": "Alias for revision_kref.",
            },
            "tag": {
                "type": "string",
                "description": "Tag to apply to the revision.",
            },
        },
        "required": ["tag"],
    }


@app.list_tools()
async def list_tools() -> list[Tool]:
    capture_schema = _capture_skill_schema()
    tag_schema = _tag_revision_schema()
    return [
        Tool(
            name="capture_skill",
            description=(
                "Capture or update a reusable procedure as a first-class "
                "Kumiho skill. Returns revision_kref on success."
            ),
            inputSchema=capture_schema,
        ),
        Tool(
            name="kumiho_capture_skill",
            description="Alias for capture_skill.",
            inputSchema=capture_schema,
        ),
        Tool(
            name="tag_revision",
            description="Apply a tag to a Kumiho revision. Returns tagged=true on success.",
            inputSchema=tag_schema,
        ),
        Tool(
            name="kumiho_tag_revision",
            description="Alias for tag_revision.",
            inputSchema=tag_schema,
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        result = await _dispatch(name, arguments or {})
    except Exception as exc:  # noqa: BLE001
        _log(f"Tool {name} error: {exc}")
        result = {"error": str(exc)}
    return [TextContent(type="text", text=json.dumps(result, default=str))]


async def _dispatch(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name in {"capture_skill", "kumiho_capture_skill"}:
        return await tool_capture_skill(args, _pool)

    if name in {"tag_revision", "kumiho_tag_revision"}:
        revision_kref = str(args.get("revision_kref") or args.get("kref") or "")
        tag = str(args.get("tag") or "")
        if not revision_kref:
            return {"error": "revision_kref is required"}
        if not tag:
            return {"error": "tag is required"}
        if hasattr(_pool, "_ensure_available") and not _pool._ensure_available():  # type: ignore[attr-defined]
            return {"error": "Kumiho is not available"}

        item_kref = ""
        if "?r=" not in revision_kref:
            item_kref = revision_kref
            latest = await _pool.get_latest_revision(item_kref, tag="latest")
            revision_kref = str((latest or {}).get("kref") or "")
            if not revision_kref:
                return {"error": f"no latest revision found for item: {item_kref}"}

        await _pool.tag_revision(revision_kref, tag)
        result = {"tagged": True, "revision_kref": revision_kref, "tag": tag}
        if item_kref:
            result["item_kref"] = item_kref
        return result

    return {"error": f"Unknown tool: {name}"}


async def main() -> None:
    _log("Starting workflow-memory MCP server...")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
