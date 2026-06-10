"""Sidecar-spawned agy agents must receive MCP servers via a sandboxed HOME.

The session-manager only translates ``mcpServers`` for codex (config flags)
and claude (SDK); agy gets nothing, so workflow steps requiring
``google_agents_cli``/``a2a_*`` ran tool-less and could only narrate tool
calls as text. ``_try_sidecar_create`` must build the same sandbox HOME the
subprocess path uses and pass it through the spawn env.
"""
from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

import operator_mcp.tool_handlers.agents as agents
from operator_mcp.agent_state import CacheSafeParams


class CapturingSidecar:
    def __init__(self) -> None:
        self.captured_config: dict | None = None
        self.socket_path = "/tmp/fake.sock"

    async def ensure_running(self) -> bool:
        return True

    async def create_agent(self, config: dict) -> dict:
        self.captured_config = config
        return {"id": "sidecar-123"}


@pytest.mark.asyncio
async def test_agy_sidecar_create_injects_home_with_mcp_config(tmp_path, monkeypatch):
    sidecar = CapturingSidecar()
    monkeypatch.setattr(agents, "_sidecar_client", sidecar)

    servers = {
        "google_agentops": {
            "command": "python",
            "args": ["server.py"],
            "env": {"TOKEN": "x"},
        }
    }
    params = CacheSafeParams(system_prompt="sp", mcp_servers=servers)

    with patch("operator_mcp.agent_subprocess._PROMPT_DIR", str(tmp_path)):
        result = await agents._try_sidecar_create(
            "agent-agy-wf", "agy", "Preflight", str(tmp_path), "do things",
            cached_params=params,
            skip_budget_check=True,
        )

    assert result == {"id": "sidecar-123"}
    config = sidecar.captured_config
    assert config is not None

    expected_home = os.path.join(str(tmp_path), "homes", "agent-agy-wf")
    assert config["env"]["HOME"] == expected_home

    mcp_path = os.path.join(
        expected_home, ".gemini", "antigravity-cli", "mcp_config.json"
    )
    assert os.path.exists(mcp_path)
    with open(mcp_path, encoding="utf-8") as f:
        data = json.load(f)
    assert "google_agentops" in data["mcpServers"]


@pytest.mark.asyncio
async def test_non_agy_sidecar_create_does_not_override_home(tmp_path, monkeypatch):
    sidecar = CapturingSidecar()
    monkeypatch.setattr(agents, "_sidecar_client", sidecar)

    params = CacheSafeParams(
        system_prompt="sp",
        mcp_servers={"operator-tools": {"command": "python", "args": []}},
    )

    with patch("operator_mcp.agent_subprocess._PROMPT_DIR", str(tmp_path)):
        await agents._try_sidecar_create(
            "agent-codex-wf", "codex", "Coder", str(tmp_path), "do things",
            cached_params=params,
            skip_budget_check=True,
        )

    config = sidecar.captured_config
    assert config is not None
    assert "HOME" not in (config.get("env") or {})
