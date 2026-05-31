"""Tests for operator.agent_state — ManagedAgent, AgentTemplate, AgentPool."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from operator_mcp.agent_state import AgentPool, AgentTemplate, ManagedAgent


# ---------------------------------------------------------------------------
# ManagedAgent
# ---------------------------------------------------------------------------

class TestManagedAgent:
    def test_create_defaults(self):
        agent = ManagedAgent(id="a1", agent_type="claude", title="test", cwd="/tmp", status="idle")
        assert agent.id == "a1"
        assert agent.agent_type == "claude"
        assert agent.status == "idle"
        assert agent.process is None
        assert agent.stdout_buffer == ""
        assert agent.stderr_buffer == ""
        assert agent._reader_task is None
        assert agent._sidecar_id is None
        assert isinstance(agent.created_at, datetime)

    def test_status_mutation(self):
        agent = ManagedAgent(id="a2", agent_type="codex", title="coder", cwd="/tmp", status="running")
        agent.status = "error"
        assert agent.status == "error"

    def test_buffer_append(self):
        agent = ManagedAgent(id="a3", agent_type="claude", title="t", cwd="/tmp", status="running")
        agent.stdout_buffer += "line1\n"
        agent.stdout_buffer += "line2\n"
        assert "line1" in agent.stdout_buffer
        assert "line2" in agent.stdout_buffer

    def test_sidecar_id(self):
        agent = ManagedAgent(id="a4", agent_type="claude", title="t", cwd="/tmp", status="idle")
        agent._sidecar_id = "sc-xyz"
        assert agent._sidecar_id == "sc-xyz"


# ---------------------------------------------------------------------------
# AgentTemplate
# ---------------------------------------------------------------------------

class TestAgentTemplate:
    def test_create_minimal(self):
        t = AgentTemplate(
            name="rust-coder",
            agent_type="codex",
            role="coder",
            capabilities=["rust", "testing"],
            description="Rust specialist",
        )
        assert t.name == "rust-coder"
        assert t.agent_type == "codex"
        assert t.role == "coder"
        assert t.capabilities == ["rust", "testing"]
        assert t.use_count == 0
        assert t.identity is None
        assert t.soul is None
        assert t.tone is None

    def test_create_full(self):
        t = AgentTemplate(
            name="react-reviewer",
            agent_type="claude",
            role="reviewer",
            capabilities=["react", "typescript"],
            description="Frontend reviewer",
            identity="You are a meticulous code reviewer",
            soul="Thorough and constructive",
            tone="Professional",
            default_cwd="~/projects/frontend",
            system_hint="Focus on accessibility",
            created_at="2025-01-01T00:00:00Z",
        )
        assert t.identity == "You are a meticulous code reviewer"
        assert t.default_cwd == "~/projects/frontend"
        assert t.system_hint == "Focus on accessibility"


# ---------------------------------------------------------------------------
# AgentPool
# ---------------------------------------------------------------------------

class TestAgentPool:
    def test_init_creates_file(self, pool_path):
        pool = AgentPool(pool_path)
        assert len(pool.templates) == 0
        with open(pool_path) as f:
            assert json.load(f) == {}

    def test_add_and_list(self, pool_path):
        pool = AgentPool(pool_path)
        t = AgentTemplate(
            name="test-agent", agent_type="claude", role="coder",
            capabilities=["python"], description="Test agent",
        )
        pool.add(t)
        assert "test-agent" in pool.templates
        assert len(pool.list_all()) == 1

    def test_remove(self, pool_path):
        pool = AgentPool(pool_path)
        t = AgentTemplate(
            name="to-remove", agent_type="codex", role="reviewer",
            capabilities=[], description="Temporary",
        )
        pool.add(t)
        pool.remove("to-remove")
        assert "to-remove" not in pool.templates

    def test_remove_nonexistent(self, pool_path):
        pool = AgentPool(pool_path)
        pool.remove("does-not-exist")  # should not raise

    def test_persistence(self, pool_path):
        pool1 = AgentPool(pool_path)
        pool1.add(AgentTemplate(
            name="persist-test", agent_type="claude", role="coder",
            capabilities=["go"], description="Go coder",
        ))
        pool2 = AgentPool(pool_path)
        assert "persist-test" in pool2.templates
        assert pool2.templates["persist-test"].description == "Go coder"

    def test_record_use(self, pool_path):
        pool = AgentPool(pool_path)
        pool.add(AgentTemplate(
            name="used-agent", agent_type="claude", role="coder",
            capabilities=[], description="Used often",
        ))
        pool.record_use("used-agent")
        pool.record_use("used-agent")
        assert pool.templates["used-agent"].use_count == 2
        assert pool.templates["used-agent"].last_used is not None

    def test_record_use_nonexistent(self, pool_path):
        pool = AgentPool(pool_path)
        pool.record_use("ghost")  # should not raise

    def test_list_all_sorted_by_use(self, pool_path):
        pool = AgentPool(pool_path)
        pool.add(AgentTemplate(name="a", agent_type="claude", role="coder", capabilities=[], description="A", use_count=1))
        pool.add(AgentTemplate(name="b", agent_type="claude", role="coder", capabilities=[], description="B", use_count=5))
        pool.add(AgentTemplate(name="c", agent_type="claude", role="coder", capabilities=[], description="C", use_count=3))
        result = pool.list_all()
        assert [t.name for t in result] == ["b", "c", "a"]

    def test_search_empty_query(self, pool_path):
        pool = AgentPool(pool_path)
        pool.add(AgentTemplate(name="x", agent_type="claude", role="coder", capabilities=[], description="X"))
        assert len(pool.search("")) == 1  # returns all

    def test_search_by_name(self, pool_path):
        pool = AgentPool(pool_path)
        pool.add(AgentTemplate(name="rust-coder", agent_type="codex", role="coder", capabilities=["rust"], description="Rust dev"))
        pool.add(AgentTemplate(name="py-reviewer", agent_type="claude", role="reviewer", capabilities=["python"], description="Python reviewer"))
        results = pool.search("rust")
        assert len(results) == 1
        assert results[0].name == "rust-coder"

    def test_search_by_capability(self, pool_path):
        pool = AgentPool(pool_path)
        pool.add(AgentTemplate(name="ts-coder", agent_type="claude", role="coder", capabilities=["typescript", "react"], description="Frontend"))
        pool.add(AgentTemplate(name="go-coder", agent_type="codex", role="coder", capabilities=["go", "grpc"], description="Backend"))
        results = pool.search("react")
        assert len(results) == 1
        assert results[0].name == "ts-coder"

    def test_search_multi_keyword(self, pool_path):
        pool = AgentPool(pool_path)
        pool.add(AgentTemplate(name="full-stack", agent_type="claude", role="coder", capabilities=["rust", "react"], description="Full stack"))
        pool.add(AgentTemplate(name="rust-only", agent_type="codex", role="coder", capabilities=["rust"], description="Rust only"))
        results = pool.search("rust react")
        assert results[0].name == "full-stack"  # matches both keywords

    def test_search_no_match(self, pool_path):
        pool = AgentPool(pool_path)
        pool.add(AgentTemplate(name="x", agent_type="claude", role="coder", capabilities=["python"], description="X"))
        assert pool.search("haskell") == []
