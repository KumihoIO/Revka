"""Shared fixtures for operator MCP tests."""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Test isolation: prevent user's ~/.revka/config.toml from leaking into
# tests that hardcode the default project names. The harness/memory project
# helpers cache their first read, so a developer machine with a non-default
# `[kumiho].harness_project` (e.g. legacy "FoxClaw") would otherwise make
# tests assert against a value the test author never anticipated.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_revka_config(monkeypatch):
    from operator_mcp import revka_config, policy

    monkeypatch.setattr(revka_config, "_CONFIG_PATH", "/nonexistent/config.toml")
    monkeypatch.setattr(revka_config, "_cached_harness", None)
    monkeypatch.setattr(revka_config, "_cached_memory", None)
    monkeypatch.setattr(revka_config, "_cached_memory_retrieval_limit", None)
    monkeypatch.setattr(revka_config, "_cached_memory_min_relevance_score", None)
    monkeypatch.delenv("KUMIHO_MEMORY_PROJECT", raising=False)
    monkeypatch.delenv("KUMIHO_MEMORY_RETRIEVAL_LIMIT", raising=False)
    monkeypatch.delenv("REVKA_MEMORY_MIN_RELEVANCE_SCORE", raising=False)

    # Same isolation for the autonomy policy. The user's local
    # ~/.revka/config.toml typically contains workspace_only roots
    # like ~/Revka and forbidden_paths like /private/var — the
    # latter blocks pytest's macOS tmp_path. Pointing at a nonexistent
    # config file makes load_policy() return a default Policy() with
    # empty lists, which allows every cwd.
    monkeypatch.setattr(policy, "_CONFIG_PATH", "/nonexistent/config.toml")
    monkeypatch.setattr(policy, "_cached_policy", None)
    yield


@pytest.fixture(autouse=True)
def _clear_global_agent_state():
    """Reset module-global agent state between tests.

    AGENTS and _terminal_result_cache live at module scope so a test that
    registers agent_id 'a1' leaks into the next test that uses the same
    id — surfacing as bizarre `cached terminal result` returns when the
    test author expected a fresh state machine.
    """
    from operator_mcp.agent_state import AGENTS
    from operator_mcp.tool_handlers import agents as _agents_handler

    AGENTS.clear()
    _agents_handler._terminal_result_cache.clear()
    yield
    AGENTS.clear()
    _agents_handler._terminal_result_cache.clear()


@pytest.fixture(autouse=True)
def _allow_agent_budget_checks(monkeypatch, request):
    """Agent-handler unit tests should not require a live Rust gateway."""
    if request.node.path.name == "test_budget_authority.py":
        yield
        return

    from operator_mcp import budget_authority
    from operator_mcp.tool_handlers import agents as _agents_handler

    async def _ok_budget():
        return None

    async def _ok_check(*_args, **_kwargs):
        return None

    async def _ok_require(*_args, **_kwargs):
        return None

    monkeypatch.setattr(_agents_handler, "_check_gateway_budget_before_spawn", _ok_budget)
    monkeypatch.setattr(_agents_handler, "require_agent_budget", _ok_require)
    monkeypatch.setattr(budget_authority, "check_agent_budget", _ok_check)
    monkeypatch.setattr(budget_authority, "require_agent_budget", _ok_require)
    yield


# ---------------------------------------------------------------------------
# tmp_path-based fixtures for file I/O tests
# ---------------------------------------------------------------------------

@pytest.fixture
def journal_path(tmp_path):
    """Temporary path for a session journal JSONL file."""
    return str(tmp_path / "test_journal.jsonl")


@pytest.fixture
def pool_path(tmp_path):
    """Temporary path for an agent pool JSON file."""
    return str(tmp_path / "agent_pool.json")


@pytest.fixture
def skills_dir(tmp_path):
    """Temporary skills directory with sample skill files."""
    d = tmp_path / "skills"
    d.mkdir()
    (d / "operator-orchestrator.md").write_text("# Orchestrator Skill\nTeam coordination.")
    (d / "operator-loop.md").write_text("# Loop Skill\nIterative cycles.")
    (d / "operator-committee.md").write_text("# Committee Skill\nDual analysis.")
    (d / "operator-handoff.md").write_text("# Handoff Skill\nTask transfer.")
    (d / "operator-chat.md").write_text("# Chat Skill\nAsync coordination.")
    return str(d)


# ---------------------------------------------------------------------------
# Mock sidecar client
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_sidecar():
    """Mock SessionManagerClient with all async methods stubbed."""
    sidecar = AsyncMock()
    sidecar.ensure_running = AsyncMock(return_value=True)
    sidecar.is_running = True
    sidecar.socket_path = "/tmp/test-operator.sock"
    sidecar.create_agent = AsyncMock(return_value={"id": "sc-123", "status": "running"})
    sidecar.get_agent = AsyncMock(return_value={"id": "sc-123", "status": "idle"})
    sidecar.send_query = AsyncMock(return_value={"status": "ok"})
    sidecar.chat_create_room = AsyncMock(return_value={"id": "room-1", "name": "test-room", "purpose": "testing"})
    sidecar.chat_list_rooms = AsyncMock(return_value=[])
    sidecar.chat_post_message = AsyncMock(return_value={"id": "msg-1"})
    sidecar.chat_read_messages = AsyncMock(return_value=[])
    sidecar.chat_wait_message = AsyncMock(return_value=None)
    sidecar.chat_delete_room = AsyncMock(return_value={"deleted": True})
    sidecar.list_pending_permissions = AsyncMock(return_value=[])
    sidecar.respond_to_permission = AsyncMock(return_value={"ok": True})
    return sidecar


# ---------------------------------------------------------------------------
# Mock gateway client
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_gateway():
    """Mock RevkaGatewayClient."""
    gw = AsyncMock()
    gw._available = True
    gw.push_channel_event = AsyncMock(return_value=True)
    gw.get_cost_summary = AsyncMock(return_value={"total": 0.0})
    gw.get_status = AsyncMock(return_value={"status": "ok"})
    return gw


# ---------------------------------------------------------------------------
# Mock Kumiho pool client
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_pool_client():
    """Mock KumihoAgentPoolClient."""
    pc = AsyncMock()
    pc._available = False
    pc.search_agents = AsyncMock(return_value=[])
    pc.list_agents = AsyncMock(return_value=[])
    pc.save_agent = AsyncMock(return_value=False)
    pc.item_to_template_dict = MagicMock(side_effect=lambda item: {
        "name": item.get("item_name", "unknown"),
        "agent_type": "claude",
        "role": "coder",
        "capabilities": [],
        "description": "",
        "use_count": 0,
    })
    return pc
