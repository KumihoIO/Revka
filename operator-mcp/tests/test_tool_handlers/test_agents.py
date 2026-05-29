"""Tests for operator.tool_handlers.agents — agent lifecycle handlers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from operator_mcp.agent_state import AGENTS, AgentPool, AgentTemplate, ManagedAgent
from operator_mcp.journal import SessionJournal
from operator_mcp.tool_handlers.agents import (
    agent_is_active,
    set_sidecar,
    tool_cancel_agent,
    tool_cancel_all_agents,
    tool_create_agent,
    tool_get_agent_activity,
    tool_list_agents,
    tool_prune_completed_agents,
    tool_send_agent_prompt,
    tool_wait_for_agent,
)


@pytest.fixture
def journal(journal_path):
    return SessionJournal(journal_path)


@pytest.fixture(autouse=True)
def clean_agents():
    """Clear global AGENTS dict before/after each test."""
    AGENTS.clear()
    yield
    AGENTS.clear()


@pytest.fixture(autouse=True)
def reset_sidecar():
    """Reset sidecar globals."""
    import operator_mcp.tool_handlers.agents as mod
    old_sc, old_ec = mod._sidecar_client, mod._event_consumer
    mod._sidecar_client = None
    mod._event_consumer = None
    yield
    mod._sidecar_client = old_sc
    mod._event_consumer = old_ec


@pytest.fixture(autouse=True)
def permissive_policy():
    """Patch load_policy to return a permissive policy for test dirs."""
    from operator_mcp.policy import Policy
    permissive = Policy(
        level="autonomous",
        workspace_only=False,
        forbidden_paths=[],
        allowed_roots=[],
        block_high_risk_commands=False,
    )
    with patch("operator_mcp.policy.load_policy", return_value=permissive):
        yield


# ---------------------------------------------------------------------------
# set_sidecar
# ---------------------------------------------------------------------------

class TestSetSidecar:
    def test_sets_globals(self):
        import operator_mcp.tool_handlers.agents as mod
        mock_sc = MagicMock()
        mock_ec = MagicMock()
        set_sidecar(mock_sc, mock_ec)
        assert mod._sidecar_client is mock_sc
        assert mod._event_consumer is mock_ec


# ---------------------------------------------------------------------------
# tool_create_agent
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestToolCreateAgent:
    async def test_basic_create_no_prompt(self, journal, mock_pool_client, tmp_path):
        result = await tool_create_agent({
            "title": "Test Agent",
            "cwd": str(tmp_path),
        }, journal, mock_pool_client)
        assert "agent_id" in result
        assert result["status"] == "idle"
        assert result["type"] == "claude"
        assert result["backend"] == "subprocess"

    async def test_invalid_agent_type(self, journal, mock_pool_client, tmp_path):
        result = await tool_create_agent({
            "title": "Bad",
            "agent_type": "gpt4",
            "cwd": str(tmp_path),
        }, journal, mock_pool_client)
        assert "error" in result

    async def test_google_agents_agent_type_alias(self, journal, mock_pool_client, tmp_path):
        result = await tool_create_agent({
            "title": "Google Agent",
            "agent_type": "agents-cli",
            "cwd": str(tmp_path),
        }, journal, mock_pool_client)
        assert result["type"] == "google_agents"
        assert result["status"] == "idle"

    async def test_missing_cwd(self, journal, mock_pool_client):
        # The schema requires cwd, but the handler also validates at runtime
        # for non-schema-validating callers (and for the template-fallback
        # path where cwd may be omitted in favor of template.default_cwd).
        # Error message should hint at both ways out.
        result = await tool_create_agent({
            "title": "No CWD",
        }, journal, mock_pool_client)
        assert "error" in result
        assert result.get("error_code") == "missing_cwd"
        assert "default_cwd" in result["error"]
        assert "absolute path" in result["error"]

    async def test_nonexistent_cwd(self, journal, mock_pool_client):
        result = await tool_create_agent({
            "title": "Bad CWD",
            "cwd": "/nonexistent/path/12345",
        }, journal, mock_pool_client)
        assert "error" in result

    async def test_agent_limit(self, journal, mock_pool_client, tmp_path):
        for i in range(10):
            AGENTS[f"a-{i}"] = ManagedAgent(
                id=f"a-{i}", agent_type="claude", title=f"Agent {i}",
                cwd=str(tmp_path), status="running",
            )
        result = await tool_create_agent({
            "title": "One Too Many",
            "cwd": str(tmp_path),
        }, journal, mock_pool_client)
        assert "error" in result
        assert "limit" in result["error"].lower()

    async def test_template_not_found(self, journal, mock_pool_client, tmp_path):
        result = await tool_create_agent({
            "title": "Tmpl Agent",
            "template": "nonexistent-template",
            "cwd": str(tmp_path),
        }, journal, mock_pool_client)
        assert "error" in result
        assert "not found" in result["error"].lower()

    async def test_with_template(self, journal, mock_pool_client, tmp_path, pool_path):
        pool = AgentPool(pool_path)
        pool.add(AgentTemplate(
            name="test-tmpl", agent_type="codex", role="coder",
            capabilities=["python"], description="Test",
            default_cwd=str(tmp_path),
        ))
        with patch("operator_mcp.tool_handlers.agents.POOL", pool):
            result = await tool_create_agent({
                "title": "Template Agent",
                "template": "test-tmpl",
                "initial_prompt": "Do work",
                "cwd": str(tmp_path),
            }, journal, mock_pool_client)
            assert result["type"] == "codex"
            assert result["template"] == "test-tmpl"

    async def test_budget_denial_does_not_register_agent(self, journal, mock_pool_client, tmp_path, monkeypatch):
        async def deny_budget():
            return {
                "error": "Budget exceeded",
                "code": "budget_exceeded",
                "source": "gateway",
            }

        import operator_mcp.tool_handlers.agents as mod
        monkeypatch.setattr(mod, "_check_gateway_budget_before_spawn", deny_budget)

        result = await tool_create_agent({
            "title": "Denied Agent",
            "initial_prompt": "Do work",
            "cwd": str(tmp_path),
        }, journal, mock_pool_client)

        assert result["code"] == "budget_exceeded"
        assert "agent_id" not in result
        assert AGENTS == {}
        assert journal.load_history() == []


# ---------------------------------------------------------------------------
# tool_wait_for_agent
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestToolWaitForAgent:
    async def test_agent_not_found(self):
        result = await tool_wait_for_agent({"agent_id": "nonexistent"})
        assert "error" in result

    async def test_already_idle(self, tmp_path):
        agent = ManagedAgent(id="a1", agent_type="claude", title="T", cwd=str(tmp_path), status="idle")
        agent.stdout_buffer = "Done"
        AGENTS["a1"] = agent
        result = await tool_wait_for_agent({"agent_id": "a1"})
        assert result["status"] == "idle"
        assert result["last_message"] == "Done"

    async def test_error_status(self, tmp_path):
        agent = ManagedAgent(id="a2", agent_type="claude", title="T", cwd=str(tmp_path), status="error")
        agent.stderr_buffer = "Failed"
        AGENTS["a2"] = agent
        result = await tool_wait_for_agent({"agent_id": "a2"})
        assert result["status"] == "error"

    async def test_dead_health_running_agent_returns_error(self, tmp_path, monkeypatch):
        class DeadMonitor:
            def get_health(self, _agent_id):
                return {"health": "dead", "status": "running", "alive": False}

        class FakeLog:
            def record_lifecycle_error(self, *_args, **_kwargs):
                return None

        agent = ManagedAgent(
            id="a-dead",
            agent_type="codex",
            title="Dead",
            cwd=str(tmp_path),
            status="running",
        )
        AGENTS[agent.id] = agent
        monkeypatch.setattr(
            "operator_mcp.heartbeat.get_heartbeat_monitor",
            lambda: DeadMonitor(),
        )
        monkeypatch.setattr(
            "operator_mcp.tool_handlers.agents.get_or_create_log",
            lambda *_args, **_kwargs: FakeLog(),
        )

        result = await tool_wait_for_agent({"agent_id": agent.id, "timeout": 1})

        assert result["status"] == "error"
        assert result["error"] == "Agent health is dead while status is running"
        assert agent.status == "error"


# ---------------------------------------------------------------------------
# tool_send_agent_prompt
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestToolSendAgentPrompt:
    async def test_agent_not_found(self, journal):
        result = await tool_send_agent_prompt({"agent_id": "ghost", "prompt": "hi"}, journal)
        assert "error" in result

    async def test_agent_still_running(self, journal, tmp_path):
        agent = ManagedAgent(id="a1", agent_type="claude", title="T", cwd=str(tmp_path), status="running")
        AGENTS["a1"] = agent
        result = await tool_send_agent_prompt({"agent_id": "a1", "prompt": "more work"}, journal)
        assert "error" in result
        assert "still running" in result["error"].lower()


# ---------------------------------------------------------------------------
# tool_get_agent_activity
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestToolGetAgentActivity:
    async def test_agent_not_found(self):
        result = await tool_get_agent_activity({"agent_id": "ghost"})
        assert "error" in result

    async def test_subprocess_activity(self, tmp_path):
        agent = ManagedAgent(id="a1", agent_type="claude", title="T", cwd=str(tmp_path), status="idle")
        agent.stdout_buffer = "output here"
        AGENTS["a1"] = agent
        result = await tool_get_agent_activity({"agent_id": "a1"})
        assert result["agent_id"] == "a1"
        assert result["backend"] == "subprocess"
        assert result["title"] == "T"
        assert "output here" in result["last_message"]


# ---------------------------------------------------------------------------
# tool_list_agents
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestToolListAgents:
    async def test_empty(self):
        result = await tool_list_agents()
        assert result["agents"] == []

    async def test_with_agents(self, tmp_path):
        AGENTS["a1"] = ManagedAgent(id="a1", agent_type="claude", title="Agent One", cwd=str(tmp_path), status="running")
        AGENTS["a2"] = ManagedAgent(id="a2", agent_type="codex", title="Agent Two", cwd=str(tmp_path), status="idle")
        result = await tool_list_agents()
        assert len(result["agents"]) == 2
        ids = {a["agent_id"] for a in result["agents"]}
        assert ids == {"a1", "a2"}

    async def test_sidecar_backend_shown(self, tmp_path):
        agent = ManagedAgent(id="a3", agent_type="claude", title="SC Agent", cwd=str(tmp_path), status="running")
        agent._sidecar_id = "sc-456"
        AGENTS["a3"] = agent
        result = await tool_list_agents()
        entry = result["agents"][0]
        assert entry["backend"] == "sidecar"
        assert entry["sidecar_id"] == "sc-456"


# ---------------------------------------------------------------------------
# cancellation / cleanup
# ---------------------------------------------------------------------------

class UnsupportedSignalProcess:
    """Async subprocess stand-in that mimics Windows rejecting SIGINT."""

    def __init__(self):
        self.returncode = None
        self.killed = False

    def send_signal(self, _signal):
        raise ValueError("Unsupported signal: 2")

    def kill(self):
        self.killed = True
        self.returncode = -9

    def terminate(self):
        self.returncode = -15

    async def wait(self):
        return self.returncode


@pytest.mark.asyncio
class TestAgentCancellation:
    async def test_cancel_agent_falls_back_when_sigint_unsupported(self, tmp_path):
        proc = UnsupportedSignalProcess()
        agent = ManagedAgent(
            id="a-win",
            agent_type="codex",
            title="Windows Agent",
            cwd=str(tmp_path),
            status="cancelling",
            process=proc,
        )
        AGENTS[agent.id] = agent

        result = await tool_cancel_agent({"agent_id": agent.id})

        assert result["status"] == "cancelled"
        assert result["method"] == "kill_unsupported_signal"
        assert proc.killed is True
        assert agent.status == "cancelled"

    async def test_cancel_all_agents_terminalizes_cancelling_dead_agents(self, tmp_path):
        live = UnsupportedSignalProcess()
        dead = UnsupportedSignalProcess()
        dead.returncode = 0
        AGENTS["live"] = ManagedAgent(
            id="live",
            agent_type="codex",
            title="Live",
            cwd=str(tmp_path),
            status="running",
            process=live,
        )
        AGENTS["dead"] = ManagedAgent(
            id="dead",
            agent_type="codex",
            title="Dead",
            cwd=str(tmp_path),
            status="cancelling",
            process=dead,
        )

        result = await tool_cancel_all_agents({})

        assert result["cancelled"] == 2
        by_id = {entry["agent_id"]: entry for entry in result["results"]}
        assert by_id["dead"]["method"] == "already_stopped"
        assert AGENTS["dead"].status == "cancelled"
        assert AGENTS["live"].status == "cancelled"

    async def test_prune_completed_agents_removes_only_inactive_records(self, tmp_path):
        old = datetime.now(timezone.utc) - timedelta(seconds=120)
        active_proc = UnsupportedSignalProcess()
        AGENTS["active"] = ManagedAgent(
            id="active",
            agent_type="codex",
            title="Active",
            cwd=str(tmp_path),
            status="running",
            process=active_proc,
            created_at=old,
        )
        AGENTS["idle"] = ManagedAgent(
            id="idle",
            agent_type="codex",
            title="Idle",
            cwd=str(tmp_path),
            status="idle",
            created_at=old,
        )
        AGENTS["cancelled"] = ManagedAgent(
            id="cancelled",
            agent_type="codex",
            title="Cancelled",
            cwd=str(tmp_path),
            status="cancelled",
            created_at=old,
        )

        dry_run = await tool_prune_completed_agents({"older_than_seconds": 60, "dry_run": True})
        assert dry_run["would_prune"] == 2
        assert set(AGENTS) == {"active", "idle", "cancelled"}

        pruned = await tool_prune_completed_agents({"older_than_seconds": 60})
        assert pruned["pruned"] == 2
        assert set(AGENTS) == {"active"}
        assert agent_is_active(AGENTS["active"]) is True
        assert pruned["remaining_managed"] == 1
