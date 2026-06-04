"""Tests for role-aware task decomposition, toposort, and relationship injection."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from operator_mcp.tool_handlers.teams import (
    _build_relationship_context,
    _collect_upstream_output,
    _decompose_task_for_role,
    _ROLE_TASK_TEMPLATES,
    _ROLE_PRIORITY,
    _toposort_members,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _make_members(*names_roles):
    """Helper: _make_members(("Alice", "coder"), ("Bob", "reviewer")) -> list of member dicts."""
    return [
        {"kref": f"kref://Revka/AgentPool/{name}.agent", "name": name, "role": role}
        for name, role in names_roles
    ]


def _make_edge(from_name: str, to_name: str, edge_type: str = "DEPENDS_ON") -> dict[str, str]:
    return {
        "from_kref": f"kref://Revka/AgentPool/{from_name}.agent",
        "to_kref": f"kref://Revka/AgentPool/{to_name}.agent",
        "edge_type": edge_type,
    }


# ---------------------------------------------------------------------------
# TestDecomposeTaskForRole
# ---------------------------------------------------------------------------

class TestDecomposeTaskForRole:
    def test_coder_gets_implement_directive(self):
        result = _decompose_task_for_role("coder", "Add auth middleware", [])
        assert "IMPLEMENT" in result
        assert "Add auth middleware" in result

    def test_reviewer_gets_review_directive(self):
        result = _decompose_task_for_role("reviewer", "Add auth middleware", [])
        assert "REVIEW" in result
        assert "Do NOT implement" in result
        assert "Add auth middleware" in result

    def test_researcher_gets_research_directive(self):
        result = _decompose_task_for_role("researcher", "Add auth middleware", [])
        assert "RESEARCH" in result
        assert "Do NOT implement" in result

    def test_tester_gets_test_directive(self):
        result = _decompose_task_for_role("tester", "Add auth middleware", [])
        assert "WRITE TESTS" in result
        assert "Do NOT implement" in result

    def test_architect_gets_design_directive(self):
        result = _decompose_task_for_role("architect", "Add auth middleware", [])
        assert "DESIGN" in result
        assert "Do NOT implement" in result

    def test_unknown_role_defaults_to_coder(self):
        result = _decompose_task_for_role("janitor", "Fix stuff", [])
        assert "IMPLEMENT" in result

    def test_team_context_included_when_multiple_members(self):
        ctx = [{"name": "Alice", "role": "coder"}, {"name": "Bob", "role": "reviewer"}]
        result = _decompose_task_for_role("coder", "Add feature", ctx)
        assert "Team Members" in result
        assert "Alice (coder)" in result
        assert "Bob (reviewer)" in result
        assert "Stay in your lane" in result

    def test_team_context_omitted_for_solo(self):
        ctx = [{"name": "Alice", "role": "coder"}]
        result = _decompose_task_for_role("coder", "Add feature", ctx)
        assert "Team Members" not in result

    def test_different_roles_produce_different_tasks(self):
        task = "Refactor the database layer"
        coder_task = _decompose_task_for_role("coder", task, [])
        reviewer_task = _decompose_task_for_role("reviewer", task, [])
        researcher_task = _decompose_task_for_role("researcher", task, [])
        assert task in coder_task and task in reviewer_task and task in researcher_task
        assert coder_task != reviewer_task != researcher_task


# ---------------------------------------------------------------------------
# TestRolePriority / TestRoleTaskTemplates
# ---------------------------------------------------------------------------

class TestRolePriority:
    def test_architect_and_researcher_are_tier_0(self):
        assert _ROLE_PRIORITY["architect"] == 0
        assert _ROLE_PRIORITY["researcher"] == 0

    def test_coder_is_tier_1(self):
        assert _ROLE_PRIORITY["coder"] == 1

    def test_reviewer_and_tester_are_tier_2(self):
        assert _ROLE_PRIORITY["reviewer"] == 2
        assert _ROLE_PRIORITY["tester"] == 2

    def test_ordering_architect_before_coder_before_reviewer(self):
        assert _ROLE_PRIORITY["architect"] < _ROLE_PRIORITY["coder"] < _ROLE_PRIORITY["reviewer"]


class TestRoleTaskTemplates:
    def test_all_priority_roles_have_templates(self):
        for role in _ROLE_PRIORITY:
            assert role in _ROLE_TASK_TEMPLATES

    def test_templates_contain_task_placeholder(self):
        for role, template in _ROLE_TASK_TEMPLATES.items():
            assert "{task}" in template


# ---------------------------------------------------------------------------
# TestToposortMembers — edge-driven execution waves
# ---------------------------------------------------------------------------

class TestToposortMembers:
    def test_no_edges_falls_back_to_role_priority(self):
        members = _make_members(("Alice", "coder"), ("Bob", "reviewer"), ("Carol", "researcher"))
        waves = _toposort_members(members, [])
        # researcher (tier 0) first, coder (tier 1) second, reviewer (tier 2) last
        assert len(waves) == 3
        names_by_wave = [[members[i]["name"] for i in w] for w in waves]
        assert names_by_wave[0] == ["Carol"]
        assert names_by_wave[1] == ["Alice"]
        assert names_by_wave[2] == ["Bob"]

    def test_depends_on_edge_orders_dependency_first(self):
        members = _make_members(("Coder", "coder"), ("Reviewer", "reviewer"))
        # Reviewer DEPENDS_ON Coder → Coder runs first
        edges = [_make_edge("Reviewer", "Coder", "DEPENDS_ON")]
        waves = _toposort_members(members, edges)
        names_by_wave = [[members[i]["name"] for i in w] for w in waves]
        assert names_by_wave[0] == ["Coder"]
        assert names_by_wave[1] == ["Reviewer"]

    def test_supports_edge_orders_supporter_first(self):
        members = _make_members(("Researcher", "researcher"), ("Coder", "coder"))
        # Researcher SUPPORTS Coder → Researcher runs first
        edges = [_make_edge("Researcher", "Coder", "SUPPORTS")]
        waves = _toposort_members(members, edges)
        names_by_wave = [[members[i]["name"] for i in w] for w in waves]
        assert names_by_wave[0] == ["Researcher"]
        assert names_by_wave[1] == ["Coder"]

    def test_parallel_members_in_same_wave(self):
        members = _make_members(("Alice", "coder"), ("Bob", "coder"), ("Carol", "reviewer"))
        # Carol depends on both Alice and Bob
        edges = [
            _make_edge("Carol", "Alice", "DEPENDS_ON"),
            _make_edge("Carol", "Bob", "DEPENDS_ON"),
        ]
        waves = _toposort_members(members, edges)
        # Alice and Bob should be in wave 0 (parallel), Carol in wave 1
        assert len(waves) == 2
        wave0_names = {members[i]["name"] for i in waves[0]}
        wave1_names = {members[i]["name"] for i in waves[1]}
        assert wave0_names == {"Alice", "Bob"}
        assert wave1_names == {"Carol"}

    def test_three_tier_chain(self):
        members = _make_members(
            ("Researcher", "researcher"), ("Coder", "coder"), ("Reviewer", "reviewer"),
        )
        edges = [
            _make_edge("Coder", "Researcher", "DEPENDS_ON"),
            _make_edge("Reviewer", "Coder", "DEPENDS_ON"),
        ]
        waves = _toposort_members(members, edges)
        names_by_wave = [[members[i]["name"] for i in w] for w in waves]
        assert names_by_wave == [["Researcher"], ["Coder"], ["Reviewer"]]

    def test_cycle_detected_raises_error(self):
        members = _make_members(("A", "coder"), ("B", "coder"))
        # A depends on B, B depends on A — cycle
        edges = [
            _make_edge("A", "B", "DEPENDS_ON"),
            _make_edge("B", "A", "DEPENDS_ON"),
        ]
        with pytest.raises(ValueError, match="Dependency cycle detected"):
            _toposort_members(members, edges)

    def test_single_member_no_edges(self):
        members = _make_members(("Solo", "coder"))
        waves = _toposort_members(members, [])
        assert waves == [[0]]

    def test_edges_with_unknown_krefs_ignored(self):
        members = _make_members(("Alice", "coder"))
        edges = [_make_edge("Alice", "Ghost", "DEPENDS_ON")]
        waves = _toposort_members(members, edges)
        # Ghost doesn't match any member — edge ignored, falls back to role priority
        # Since there's effectively no valid edge, it falls back
        assert len(waves) >= 1
        total = sum(len(w) for w in waves)
        assert total == 1

    def test_feeds_into_treated_like_supports(self):
        members = _make_members(("Producer", "researcher"), ("Consumer", "coder"))
        edges = [_make_edge("Producer", "Consumer", "FEEDS_INTO")]
        waves = _toposort_members(members, edges)
        names_by_wave = [[members[i]["name"] for i in w] for w in waves]
        assert names_by_wave[0] == ["Producer"]
        assert names_by_wave[1] == ["Consumer"]


# ---------------------------------------------------------------------------
# TestBuildRelationshipContext
# ---------------------------------------------------------------------------

class TestBuildRelationshipContext:
    def test_upstream_from_depends_on(self):
        members = _make_members(("Coder", "coder"), ("Reviewer", "reviewer"))
        edges = [_make_edge("Reviewer", "Coder", "DEPENDS_ON")]
        spawned_map = {
            "kref://Revka/AgentPool/Coder.agent": {
                "agent_id": "coder-001", "name": "Coder", "role": "coder",
            },
        }
        # Build context for Reviewer (index 1)
        ctx = _build_relationship_context(1, members, edges, spawned_map)
        assert "Upstream" in ctx
        assert "Coder" in ctx
        assert "coder-001" in ctx

    def test_downstream_from_depends_on(self):
        members = _make_members(("Coder", "coder"), ("Reviewer", "reviewer"))
        edges = [_make_edge("Reviewer", "Coder", "DEPENDS_ON")]
        spawned_map = {
            "kref://Revka/AgentPool/Reviewer.agent": {
                "agent_id": "rev-001", "name": "Reviewer", "role": "reviewer",
            },
        }
        # Build context for Coder (index 0) — Reviewer depends on them
        ctx = _build_relationship_context(0, members, edges, spawned_map)
        assert "Downstream" in ctx
        assert "Reviewer" in ctx

    def test_supports_edge_upstream(self):
        members = _make_members(("Researcher", "researcher"), ("Coder", "coder"))
        edges = [_make_edge("Researcher", "Coder", "SUPPORTS")]
        spawned_map = {
            "kref://Revka/AgentPool/Researcher.agent": {
                "agent_id": "res-001", "name": "Researcher", "role": "researcher",
            },
        }
        # Coder should see Researcher as upstream
        ctx = _build_relationship_context(1, members, edges, spawned_map)
        assert "Upstream" in ctx
        assert "Researcher" in ctx

    def test_no_edges_returns_empty(self):
        members = _make_members(("Alice", "coder"))
        ctx = _build_relationship_context(0, members, [], {})
        assert ctx == ""

    def test_no_kref_returns_empty(self):
        members = [{"name": "Alice", "role": "coder"}]  # no kref
        edges = [_make_edge("Alice", "Bob", "DEPENDS_ON")]
        ctx = _build_relationship_context(0, members, edges, {})
        assert ctx == ""

    def test_unspawned_upstream_not_included(self):
        members = _make_members(("Coder", "coder"), ("Reviewer", "reviewer"))
        edges = [_make_edge("Reviewer", "Coder", "DEPENDS_ON")]
        # Coder not in spawned_map yet
        ctx = _build_relationship_context(1, members, edges, {})
        assert ctx == ""


# ---------------------------------------------------------------------------
# TestCollectUpstreamOutput
# ---------------------------------------------------------------------------

class TestCollectUpstreamOutput:
    def test_collects_stdout_from_upstream(self):
        from operator_mcp.agent_state import AGENTS, ManagedAgent

        members = _make_members(("Coder", "coder"), ("Reviewer", "reviewer"))
        edges = [_make_edge("Reviewer", "Coder", "DEPENDS_ON")]

        # Create a mock agent with stdout
        agent = ManagedAgent(
            id="coder-001", agent_type="codex", title="coder-Coder",
            cwd="/tmp", status="idle",
        )
        agent.stdout_buffer = "Fixed the auth bug in middleware.py\n"
        AGENTS["coder-001"] = agent

        spawned_map = {
            "kref://Revka/AgentPool/Coder.agent": {
                "agent_id": "coder-001", "name": "Coder", "role": "coder",
            },
        }

        output = _collect_upstream_output(1, members, edges, spawned_map)
        assert "Upstream Output" in output
        assert "Coder" in output
        assert "Fixed the auth bug" in output

        # Cleanup
        del AGENTS["coder-001"]

    def test_empty_when_no_upstream(self):
        members = _make_members(("Alice", "coder"))
        output = _collect_upstream_output(0, members, [], {})
        assert output == ""

    def test_empty_when_upstream_has_no_output(self):
        from operator_mcp.agent_state import AGENTS, ManagedAgent

        members = _make_members(("Coder", "coder"), ("Reviewer", "reviewer"))
        edges = [_make_edge("Reviewer", "Coder", "DEPENDS_ON")]

        agent = ManagedAgent(
            id="coder-002", agent_type="codex", title="coder-Coder",
            cwd="/tmp", status="idle",
        )
        agent.stdout_buffer = ""
        AGENTS["coder-002"] = agent

        spawned_map = {
            "kref://Revka/AgentPool/Coder.agent": {
                "agent_id": "coder-002", "name": "Coder", "role": "coder",
            },
        }

        output = _collect_upstream_output(1, members, edges, spawned_map)
        assert output == ""

        del AGENTS["coder-002"]

    def test_truncates_long_output(self):
        from operator_mcp.agent_state import AGENTS, ManagedAgent

        members = _make_members(("Coder", "coder"), ("Reviewer", "reviewer"))
        edges = [_make_edge("Reviewer", "Coder", "DEPENDS_ON")]

        agent = ManagedAgent(
            id="coder-003", agent_type="codex", title="coder-Coder",
            cwd="/tmp", status="idle",
        )
        agent.stdout_buffer = "x" * 10000
        AGENTS["coder-003"] = agent

        spawned_map = {
            "kref://Revka/AgentPool/Coder.agent": {
                "agent_id": "coder-003", "name": "Coder", "role": "coder",
            },
        }

        output = _collect_upstream_output(1, members, edges, spawned_map)
        # Output should be truncated to 3000 chars of agent output
        assert len(output) < 3500  # 3000 + markdown overhead

        del AGENTS["coder-003"]

    def test_supports_edge_collects_output(self):
        from operator_mcp.agent_state import AGENTS, ManagedAgent

        members = _make_members(("Researcher", "researcher"), ("Coder", "coder"))
        edges = [_make_edge("Researcher", "Coder", "SUPPORTS")]

        agent = ManagedAgent(
            id="res-001", agent_type="claude", title="researcher-Researcher",
            cwd="/tmp", status="idle",
        )
        agent.stdout_buffer = "Found 3 relevant patterns in the codebase.\n"
        AGENTS["res-001"] = agent

        spawned_map = {
            "kref://Revka/AgentPool/Researcher.agent": {
                "agent_id": "res-001", "name": "Researcher", "role": "researcher",
            },
        }

        # Coder (index 1) should see Researcher's output
        output = _collect_upstream_output(1, members, edges, spawned_map)
        assert "Researcher" in output
        assert "Found 3 relevant patterns" in output

        del AGENTS["res-001"]
