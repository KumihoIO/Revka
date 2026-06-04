"""Tests for operator.workflow_context — workflow-scoped memory substrate."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from operator_mcp.workflow_context import AgentFinding, WorkflowContext


# ---------------------------------------------------------------------------
# AgentFinding
# ---------------------------------------------------------------------------

class TestAgentFinding:
    def test_to_dict_roundtrip(self):
        f = AgentFinding(
            agent_id="abc-123",
            title="Test Agent",
            status="completed",
            last_message="All done",
            files_touched=["src/main.py"],
            error_count=0,
            tool_call_count=5,
            usage={"input_tokens": 100},
        )
        d = f.to_dict()
        assert d["agent_id"] == "abc-123"
        assert d["title"] == "Test Agent"
        assert d["status"] == "completed"
        assert d["last_message"] == "All done"
        assert d["files_touched"] == ["src/main.py"]
        assert d["tool_call_count"] == 5
        assert "captured_at" in d

    def test_defaults(self):
        f = AgentFinding(agent_id="x", title="T", status="error")
        assert f.last_message == ""
        assert f.files_touched == []
        assert f.error_count == 0
        assert f.usage == {}


# ---------------------------------------------------------------------------
# WorkflowContext.capture
# ---------------------------------------------------------------------------

class TestCapture:
    def test_captures_terminal_result(self):
        ctx = WorkflowContext("sess-1")
        result = {
            "agent_id": "a1",
            "title": "Coder",
            "status": "completed",
            "last_message": "Done coding",
            "files_touched": ["a.py", "b.py"],
            "error_count": 0,
            "tool_call_count": 12,
            "usage": {"input_tokens": 500},
        }
        finding = ctx.capture(result)
        assert finding is not None
        assert finding.agent_id == "a1"
        assert finding.title == "Coder"
        assert finding.status == "completed"
        assert finding.files_touched == ["a.py", "b.py"]

    def test_ignores_non_terminal(self):
        ctx = WorkflowContext("sess-1")
        result = {"agent_id": "a1", "status": "running", "title": "X"}
        assert ctx.capture(result) is None

    def test_ignores_backend_unreachable(self):
        ctx = WorkflowContext("sess-1")
        result = {"agent_id": "a1", "status": "backend_unreachable", "title": "X"}
        assert ctx.capture(result) is None

    def test_idempotent(self):
        ctx = WorkflowContext("sess-1")
        r = {"agent_id": "a1", "title": "X", "status": "completed", "last_message": "v1"}
        f1 = ctx.capture(r)
        # Second capture with different message should return same finding
        r2 = {**r, "last_message": "v2"}
        f2 = ctx.capture(r2)
        assert f1 is f2
        assert f2.last_message == "v1"  # Original preserved

    def test_captures_error_status(self):
        ctx = WorkflowContext("sess-1")
        result = {"agent_id": "a2", "title": "Tester", "status": "error", "error_count": 3}
        finding = ctx.capture(result)
        assert finding is not None
        assert finding.status == "error"
        assert finding.error_count == 3

    def test_truncates_long_message(self):
        ctx = WorkflowContext("sess-1")
        long_msg = "x" * 10_000
        result = {"agent_id": "a3", "title": "T", "status": "completed", "last_message": long_msg}
        finding = ctx.capture(result)
        assert len(finding.last_message) == 4000

    def test_missing_agent_id(self):
        ctx = WorkflowContext("sess-1")
        result = {"status": "completed", "title": "X"}
        assert ctx.capture(result) is None


# ---------------------------------------------------------------------------
# WorkflowContext queries
# ---------------------------------------------------------------------------

class TestQueries:
    @pytest.fixture
    def ctx_with_findings(self):
        ctx = WorkflowContext("sess-q")
        ctx.capture({"agent_id": "a1", "title": "Coder", "status": "completed", "tool_call_count": 10, "files_touched": ["x.py"]})
        ctx.capture({"agent_id": "a2", "title": "Tester", "status": "error", "error_count": 2, "tool_call_count": 5})
        ctx.capture({"agent_id": "a3", "title": "Reviewer", "status": "completed", "tool_call_count": 3, "files_touched": ["x.py", "y.py"]})
        return ctx

    def test_get_findings_all(self, ctx_with_findings):
        findings = ctx_with_findings.get_findings()
        assert len(findings) == 3
        ids = {f["agent_id"] for f in findings}
        assert ids == {"a1", "a2", "a3"}

    def test_get_findings_filtered(self, ctx_with_findings):
        errors = ctx_with_findings.get_findings(status_filter="error")
        assert len(errors) == 1
        assert errors[0]["agent_id"] == "a2"

    def test_get_finding_by_id(self, ctx_with_findings):
        f = ctx_with_findings.get_finding("a1")
        assert f is not None
        assert f["title"] == "Coder"

    def test_get_finding_not_found(self, ctx_with_findings):
        assert ctx_with_findings.get_finding("nonexistent") is None

    def test_summary(self, ctx_with_findings):
        s = ctx_with_findings.summary()
        assert s["session_id"] == "sess-q"
        assert s["agent_count"] == 3
        assert s["by_status"] == {"completed": 2, "error": 1}
        assert s["total_files_touched"] == 2  # x.py, y.py (deduplicated)
        assert s["total_errors"] == 2
        assert s["total_tool_calls"] == 18

    def test_clear(self, ctx_with_findings):
        ctx_with_findings.clear()
        assert ctx_with_findings.get_findings() == []
        assert ctx_with_findings.summary()["agent_count"] == 0


# ---------------------------------------------------------------------------
# Kumiho persistence (best-effort)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestKumihoPersistence:
    async def test_persist_succeeds(self):
        ctx = WorkflowContext("sess-k")
        mock_sdk = AsyncMock()
        mock_sdk._available = True
        mock_sdk.ensure_space = AsyncMock()
        mock_sdk.create_item = AsyncMock(return_value={"kref": "k://item/1"})
        mock_sdk.create_revision = AsyncMock(return_value={"kref": "k://rev/1"})
        ctx.set_kumiho_sdk(mock_sdk)

        finding = AgentFinding(agent_id="a1", title="Coder", status="completed")
        result = await ctx.persist_finding(finding)
        assert result is True
        from operator_mcp.revka_config import harness_project
        mock_sdk.ensure_space.assert_awaited_once_with(harness_project(), "Workflows")
        mock_sdk.create_item.assert_awaited_once()
        mock_sdk.create_revision.assert_awaited_once()

    async def test_persist_no_sdk(self):
        ctx = WorkflowContext("sess-k")
        finding = AgentFinding(agent_id="a1", title="Coder", status="completed")
        result = await ctx.persist_finding(finding)
        assert result is False

    async def test_persist_sdk_unavailable(self):
        ctx = WorkflowContext("sess-k")
        mock_sdk = AsyncMock()
        mock_sdk._available = False
        ctx.set_kumiho_sdk(mock_sdk)

        finding = AgentFinding(agent_id="a1", title="Coder", status="completed")
        result = await ctx.persist_finding(finding)
        assert result is False

    async def test_persist_swallows_exceptions(self):
        ctx = WorkflowContext("sess-k")
        mock_sdk = AsyncMock()
        mock_sdk._available = True
        mock_sdk.ensure_space = AsyncMock(side_effect=RuntimeError("boom"))
        ctx.set_kumiho_sdk(mock_sdk)

        finding = AgentFinding(agent_id="a1", title="Coder", status="completed")
        result = await ctx.persist_finding(finding)
        assert result is False  # No exception propagated


# ---------------------------------------------------------------------------
# Integration: auto-capture in _build_wait_result
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestAutoCapture:
    async def test_build_wait_result_captures_terminal(self, tmp_path):
        """Verify that _build_wait_result auto-captures into workflow context."""
        from operator_mcp.agent_state import AGENTS, ManagedAgent
        from operator_mcp.tool_handlers.agents import (
            _build_wait_result,
            _terminal_result_cache,
            set_workflow_context,
        )
        import operator_mcp.tool_handlers.agents as agents_mod

        ctx = WorkflowContext("sess-int")
        old_ctx = agents_mod._workflow_ctx
        set_workflow_context(ctx)

        agent = ManagedAgent(
            id="int-1", agent_type="claude", title="Integration Test",
            cwd=str(tmp_path), status="completed",
        )
        agent.stdout_buffer = "test output"
        AGENTS["int-1"] = agent

        try:
            result = await _build_wait_result("int-1", agent)
            assert result["status"] == "completed"

            # Verify finding was captured
            finding = ctx.get_finding("int-1")
            assert finding is not None
            assert finding["title"] == "Integration Test"
            assert finding["status"] == "completed"
        finally:
            AGENTS.pop("int-1", None)
            _terminal_result_cache.pop("int-1", None)
            agents_mod._workflow_ctx = old_ctx
