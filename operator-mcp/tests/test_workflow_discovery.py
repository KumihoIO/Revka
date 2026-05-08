"""Tests for tool_handlers.workflow_discovery — get_workflow_metadata MCP tool."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from operator_mcp.tool_handlers import workflow_discovery as wd
from operator_mcp.tool_handlers.workflow_discovery import (
    _annotation_to_string,
    _compute_step_types,
    tool_get_workflow_metadata,
)
from operator_mcp.workflow.schema import StepType


# ---------------------------------------------------------------------------
# step_types — pure introspection, no gateway needed
# ---------------------------------------------------------------------------

class TestStepTypes:
    def test_all_step_types_present(self):
        types = _compute_step_types()
        # Every StepType enum value must appear in the output.
        returned = {t["type"] for t in types}
        expected = {st.value for st in StepType}
        assert returned == expected
        # And we have at least 17 types as the brief promises.
        assert len(types) >= 17

    def test_each_type_has_config_fields_and_example(self):
        types = _compute_step_types()
        for t in types:
            assert isinstance(t["type"], str) and t["type"]
            assert isinstance(t["label"], str) and t["label"]
            assert isinstance(t["description"], str)
            assert isinstance(t["config_fields"], list)
            # Every type we ship has at least one config field.
            assert len(t["config_fields"]) > 0, f"{t['type']} has no config_fields"
            for f in t["config_fields"]:
                assert "name" in f and "type" in f and "required" in f and "description" in f
            # Example YAML is a non-empty short string.
            assert isinstance(t["example_yaml"], str)
            assert t["example_yaml"].strip()

    def test_agent_step_fields_match_schema(self):
        """Agent step must surface prompt/agent_type/role as field names."""
        types = _compute_step_types()
        agent = next(t for t in types if t["type"] == "agent")
        names = {f["name"] for f in agent["config_fields"]}
        assert {"prompt", "agent_type", "role"}.issubset(names)


class TestAnnotationToString:
    def test_primitives(self):
        assert _annotation_to_string(str) == "string"
        assert _annotation_to_string(int) == "int"
        assert _annotation_to_string(bool) == "bool"

    def test_optional_str(self):
        assert _annotation_to_string(str | None) == "string | null"

    def test_list_str(self):
        assert _annotation_to_string(list[str]) == "list[string]"


# ---------------------------------------------------------------------------
# tool_get_workflow_metadata — gateway interactions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestToolGetWorkflowMetadata:
    async def test_gateway_unreachable_degrades_gracefully(self, monkeypatch):
        """If every gateway call returns None, sections are [] + warnings."""
        gw = MagicMock()
        gw.get_agents = AsyncMock(return_value=None)
        gw.get_auth_profiles = AsyncMock(return_value=None)
        gw.get_skills = AsyncMock(return_value=None)
        gw.get_channels = AsyncMock(return_value=None)
        monkeypatch.setattr(wd, "_gateway", lambda: gw)

        result = await tool_get_workflow_metadata({})
        # Step types still come back (static data).
        assert isinstance(result["step_types"], list) and result["step_types"]
        # Live sections all empty lists.
        assert result["agents"] == []
        assert result["auth_profiles"] == []
        assert result["skills"] == []
        assert result["channels"] == []
        # Each got a warning, no exceptions raised.
        warnings = result.get("_warnings", [])
        assert any("agents" in w for w in warnings)
        assert any("auth_profiles" in w for w in warnings)
        assert any("skills" in w for w in warnings)
        assert any("channels" in w for w in warnings)

    async def test_auth_profiles_response_has_no_token_bytes(self, monkeypatch):
        """Audit: even if upstream leaks a token field, we strip it."""
        gw = MagicMock()
        gw.get_agents = AsyncMock(return_value=[])
        gw.get_auth_profiles = AsyncMock(return_value=[
            {
                "id": "github:default",
                "provider": "github",
                "profile_name": "default",
                "kind": "token",
                # Hostile upstream — these MUST NOT appear in output.
                "token": "ghp_super_secret",
                "access_token": "should_never_leak",
                "refresh_token": "also_secret",
                "id_token": "jwt_secret",
            }
        ])
        gw.get_skills = AsyncMock(return_value=[])
        gw.get_channels = AsyncMock(return_value=[])
        monkeypatch.setattr(wd, "_gateway", lambda: gw)

        result = await tool_get_workflow_metadata({"include": ["auth_profiles"]})
        profiles = result["auth_profiles"]
        assert len(profiles) == 1
        p = profiles[0]
        # Whitelisted keys only.
        assert set(p.keys()) == {"id", "provider", "profile_name", "kind"}
        # Audit a flat string of the entire result for token-shaped substrings.
        import json
        blob = json.dumps(result)
        for forbidden in ("token", "access_token", "refresh_token", "id_token", "ghp_super_secret"):
            # "token" is a legitimate value of `kind` so we only forbid the
            # secret bytes themselves and the explicit field names that would
            # imply we are echoing them.
            if forbidden in {"token"}:
                continue
            assert forbidden not in blob, f"leaked '{forbidden}' in response"
        # And the actual secret string must be absent.
        assert "ghp_super_secret" not in blob
        assert "should_never_leak" not in blob

    async def test_include_filter_narrows_response(self, monkeypatch):
        gw = MagicMock()
        gw.get_agents = AsyncMock(return_value=[])
        gw.get_auth_profiles = AsyncMock(return_value=[])
        gw.get_skills = AsyncMock(return_value=[])
        gw.get_channels = AsyncMock(return_value=[])
        monkeypatch.setattr(wd, "_gateway", lambda: gw)

        result = await tool_get_workflow_metadata({"include": ["step_types"]})
        assert "step_types" in result
        assert "agents" not in result
        assert "auth_profiles" not in result
        assert "skills" not in result
        assert "channels" not in result
        # Gateway methods should not have been called.
        gw.get_agents.assert_not_awaited()
        gw.get_auth_profiles.assert_not_awaited()

    async def test_live_data_is_slimmed(self, monkeypatch):
        gw = MagicMock()
        gw.get_agents = AsyncMock(return_value=[
            {
                "kref": "kr://...",
                "name": "Coder Bot",
                "item_name": "coder-bot",
                "agent_type": "codex",
                "role": "coder",
                "expertise": ["python", "rust"],
                "identity": "writes code",
                "soul": "should_be_dropped",
                "model": "should_be_dropped",
            }
        ])
        gw.get_auth_profiles = AsyncMock(return_value=[])
        gw.get_skills = AsyncMock(return_value=[
            {"name": "review", "description": "do a review", "content": "drop_me"}
        ])
        gw.get_channels = AsyncMock(return_value=[
            {"name": "discord", "type": "discord", "enabled": True, "drop": "me"}
        ])
        monkeypatch.setattr(wd, "_gateway", lambda: gw)

        result = await tool_get_workflow_metadata({
            "include": ["agents", "skills", "channels"]
        })
        agent = result["agents"][0]
        assert set(agent.keys()) == {
            "item_name", "name", "agent_type", "role", "expertise", "identity",
        }
        assert agent["item_name"] == "coder-bot"
        skill = result["skills"][0]
        assert set(skill.keys()) == {"name", "description"}
        chan = result["channels"][0]
        assert set(chan.keys()) == {"name", "kind"}
        assert chan["kind"] == "discord"
