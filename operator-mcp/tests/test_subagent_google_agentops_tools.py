"""Sub-agent Operator MCP surface for Google Agent Platform tooling."""
from __future__ import annotations

import pytest

from operator_mcp import subagent_mcp


@pytest.mark.asyncio
async def test_operator_tools_exposes_google_agentops_surface():
    tools = {tool.name for tool in await subagent_mcp.list_tools()}

    assert {
        "google_agents_cli",
        "a2a_discover",
        "a2a_send_task",
        "a2a_get_remote_task",
    }.issubset(tools)


@pytest.mark.asyncio
async def test_google_agents_cli_dispatch_does_not_require_sidecar(monkeypatch):
    from operator_mcp.tool_handlers import google_agents_cli

    async def fake_tool(args):
        return {"success": True, "command": args["command"]}

    monkeypatch.setattr(google_agents_cli, "tool_google_agents_cli", fake_tool)
    monkeypatch.setattr(
        subagent_mcp,
        "_get_sidecar",
        lambda: pytest.fail("google_agents_cli should not require sidecar"),
    )

    result = await subagent_mcp._dispatch("google_agents_cli", {"command": ["info"]})

    assert result == {"success": True, "command": ["info"]}


@pytest.mark.asyncio
async def test_a2a_dispatch_does_not_require_sidecar(monkeypatch):
    from operator_mcp.a2a import a2a_client

    async def fake_discover(args):
        return {"discovered": True, "url": args["url"]}

    monkeypatch.setattr(a2a_client, "tool_a2a_discover", fake_discover)
    monkeypatch.setattr(
        subagent_mcp,
        "_get_sidecar",
        lambda: pytest.fail("a2a_discover should not require sidecar"),
    )

    result = await subagent_mcp._dispatch(
        "a2a_discover",
        {"url": "https://agent.example.test"},
    )

    assert result == {"discovered": True, "url": "https://agent.example.test"}
