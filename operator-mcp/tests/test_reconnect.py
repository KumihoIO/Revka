"""Tests for agent reconnection on startup."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from operator_mcp.agent_state import AGENTS, ManagedAgent
from operator_mcp.reconnect import (
    _LAST_LOG_AT,
    _build_journal_index,
    _log_limited,
    _map_sidecar_status,
    reconnect_agents,
)


@pytest.fixture(autouse=True)
def clean_agents():
    AGENTS.clear()
    _LAST_LOG_AT.clear()
    yield
    AGENTS.clear()
    _LAST_LOG_AT.clear()


# ---------------------------------------------------------------------------
# _map_sidecar_status
# ---------------------------------------------------------------------------

class TestMapSidecarStatus:
    def test_running(self):
        assert _map_sidecar_status("running") == "running"

    def test_idle(self):
        assert _map_sidecar_status("idle") == "idle"

    def test_waiting_maps_to_running(self):
        assert _map_sidecar_status("waiting") == "running"

    def test_error(self):
        assert _map_sidecar_status("error") == "error"

    def test_unknown_defaults_to_running(self):
        assert _map_sidecar_status("something_new") == "running"


# ---------------------------------------------------------------------------
# _build_journal_index
# ---------------------------------------------------------------------------

class TestBuildJournalIndex:
    def test_builds_index_from_journal(self):
        journal = MagicMock()
        journal.load_history.return_value = [
            {"sidecar_id": "sc-1", "agent_id": "a1", "title": "coder-A"},
            {"sidecar_id": "sc-2", "agent_id": "a2", "title": "reviewer-B"},
            {"agent_id": "a3"},  # no sidecar_id — skipped
        ]
        index = _build_journal_index(journal)
        assert "sc-1" in index
        assert "sc-2" in index
        assert index["sc-1"]["agent_id"] == "a1"
        assert len(index) == 2

    def test_first_entry_wins_for_same_sidecar_id(self):
        journal = MagicMock()
        journal.load_history.return_value = [
            {"sidecar_id": "sc-1", "agent_id": "a1", "title": "first"},
            {"sidecar_id": "sc-1", "agent_id": "a1", "title": "second"},
        ]
        index = _build_journal_index(journal)
        assert index["sc-1"]["title"] == "first"

    def test_handles_journal_failure(self):
        journal = MagicMock()
        journal.load_history.side_effect = Exception("disk error")
        index = _build_journal_index(journal)
        assert index == {}


def test_log_limited_throttles_repeated_messages(monkeypatch):
    messages: list[str] = []
    monkeypatch.setattr("operator_mcp.reconnect._log", messages.append)

    _log_limited("same", "first", interval=300)
    _log_limited("same", "second", interval=300)
    _log_limited("other", "third", interval=300)

    assert messages == ["first", "third"]


# ---------------------------------------------------------------------------
# reconnect_agents
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestReconnectAgents:
    async def test_no_sidecar_returns_empty(self):
        result = await reconnect_agents(None, MagicMock(), MagicMock())
        assert result == []

    async def test_sidecar_failure_returns_empty(self):
        sidecar = AsyncMock()
        sidecar.list_agents = AsyncMock(side_effect=Exception("connection refused"))
        result = await reconnect_agents(sidecar, MagicMock(), MagicMock())
        assert result == []

    async def test_no_active_agents_returns_empty(self):
        sidecar = AsyncMock()
        sidecar.list_agents = AsyncMock(return_value=[])
        result = await reconnect_agents(sidecar, MagicMock(), MagicMock())
        assert result == []

    async def test_recovers_agent_with_journal_match(self):
        sidecar = AsyncMock()
        sidecar.list_agents = AsyncMock(return_value=[
            {"id": "sc-abc", "status": "running", "title": "coder-Pixel", "cwd": "/tmp/work"},
        ])

        journal = MagicMock()
        journal.load_history.return_value = [
            {
                "sidecar_id": "sc-abc",
                "agent_id": "original-uuid",
                "agent_type": "codex",
                "title": "coder-Pixel",
                "cwd": "/tmp/work",
            },
        ]

        event_consumer = AsyncMock()
        result = await reconnect_agents(sidecar, journal, event_consumer)

        assert len(result) == 1
        assert result[0]["agent_id"] == "original-uuid"
        assert result[0]["sidecar_id"] == "sc-abc"
        assert result[0]["from_journal"] is True
        assert "original-uuid" in AGENTS
        assert AGENTS["original-uuid"]._sidecar_id == "sc-abc"
        event_consumer.subscribe.assert_called_once_with("sc-abc", "coder-Pixel")

    async def test_recovers_agent_without_journal_match(self):
        sidecar = AsyncMock()
        sidecar.list_agents = AsyncMock(return_value=[
            {"id": "sc-orphan", "status": "running", "agentType": "claude", "title": "orphan-agent"},
        ])

        journal = MagicMock()
        journal.load_history.return_value = []  # no matching entry

        event_consumer = AsyncMock()
        result = await reconnect_agents(sidecar, journal, event_consumer)

        assert len(result) == 1
        assert result[0]["agent_id"] == "sc-orphan"
        assert result[0]["from_journal"] is False
        assert "sc-orphan" in AGENTS

    async def test_skips_closed_agents(self):
        sidecar = AsyncMock()
        sidecar.list_agents = AsyncMock(return_value=[
            {"id": "sc-done", "status": "closed"},
            {"id": "sc-err", "status": "error"},
            {"id": "sc-live", "status": "running", "title": "alive"},
        ])

        journal = MagicMock()
        journal.load_history.return_value = []

        result = await reconnect_agents(sidecar, journal, AsyncMock())

        assert len(result) == 1
        assert result[0]["sidecar_id"] == "sc-live"

    async def test_does_not_double_register(self):
        # Pre-populate AGENTS
        AGENTS["existing-id"] = ManagedAgent(
            id="existing-id", agent_type="claude", title="already-here",
            cwd="/tmp", status="running",
        )

        sidecar = AsyncMock()
        sidecar.list_agents = AsyncMock(return_value=[
            {"id": "sc-match", "status": "running"},
        ])

        journal = MagicMock()
        journal.load_history.return_value = [
            {"sidecar_id": "sc-match", "agent_id": "existing-id"},
        ]

        result = await reconnect_agents(sidecar, journal, AsyncMock())
        assert len(result) == 0  # already registered, skip

    async def test_multiple_agents_recovered(self):
        sidecar = AsyncMock()
        sidecar.list_agents = AsyncMock(return_value=[
            {"id": "sc-1", "status": "running", "title": "agent-1"},
            {"id": "sc-2", "status": "idle", "title": "agent-2"},
        ])

        journal = MagicMock()
        journal.load_history.return_value = [
            {"sidecar_id": "sc-1", "agent_id": "a1", "agent_type": "codex", "title": "agent-1", "cwd": "/w1"},
            {"sidecar_id": "sc-2", "agent_id": "a2", "agent_type": "claude", "title": "agent-2", "cwd": "/w2"},
        ]

        result = await reconnect_agents(sidecar, journal, AsyncMock())
        assert len(result) == 2
        assert "a1" in AGENTS
        assert "a2" in AGENTS
        assert AGENTS["a1"].status == "running"
        assert AGENTS["a2"].status == "idle"

    async def test_none_event_consumer_doesnt_crash(self):
        sidecar = AsyncMock()
        sidecar.list_agents = AsyncMock(return_value=[
            {"id": "sc-1", "status": "running", "title": "test"},
        ])

        journal = MagicMock()
        journal.load_history.return_value = []

        # event_consumer is None — should not crash
        result = await reconnect_agents(sidecar, journal, None)
        assert len(result) == 1
