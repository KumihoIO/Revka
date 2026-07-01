"""Tests for forwarding named ~/.revka/config.toml [[mcp.servers]] entries
(e.g. a user-registered OpenCrab server) into workflow agent steps.

Covers the full chain: revka_config.mcp_servers_by_name (read + match),
mcp_injection._external_server_config / build_mcp_servers (convert + merge),
AgentStepConfig.mcp_servers (the opt-in step field), and
executor._agent_required_tool_visible / _preflight_required_tool_visibility
(so a typo'd server name fails loudly instead of silently no-op'ing or,
worse, false-positive-passing preflight the way the pre-existing "unknown
tool name + tools=all" fallback does).
"""
from __future__ import annotations

from unittest.mock import patch

from operator_mcp.mcp_injection import _external_server_config, build_mcp_servers
from operator_mcp.revka_config import mcp_servers_by_name
from operator_mcp.workflow.executor import (
    _agent_required_tool_visible,
    _preflight_required_tool_visibility,
)
from operator_mcp.workflow.schema import AgentStepConfig, WorkflowDef


_CONFIG_TOML = "\n".join([
    "[[mcp.servers]]",
    'name = "OpenCrab"',
    'transport = "http"',
    'url = "https://opencrab.sh/api/mcp/abc123"',
    "",
    "[[mcp.servers]]",
    'name = "LocalTool"',
    'transport = "stdio"',
    'command = "python3"',
    'args = ["-m", "local_tool"]',
])


class TestMcpServersByName:
    """revka_config's [[mcp.servers]] reader — no such helper existed before."""

    def test_matches_case_insensitively(self, tmp_path):
        cfg = tmp_path / "config.toml"
        cfg.write_text(_CONFIG_TOML, encoding="utf-8")
        with patch("operator_mcp.revka_config._CONFIG_PATH", str(cfg)):
            matched = mcp_servers_by_name(["opencrab"])
        assert set(matched) == {"OpenCrab"}
        assert matched["OpenCrab"]["url"] == "https://opencrab.sh/api/mcp/abc123"

    def test_unmatched_name_omitted(self, tmp_path):
        cfg = tmp_path / "config.toml"
        cfg.write_text(_CONFIG_TOML, encoding="utf-8")
        with patch("operator_mcp.revka_config._CONFIG_PATH", str(cfg)):
            matched = mcp_servers_by_name(["DoesNotExist"])
        assert matched == {}

    def test_empty_names_short_circuits(self, tmp_path):
        cfg = tmp_path / "config.toml"
        cfg.write_text(_CONFIG_TOML, encoding="utf-8")
        with patch("operator_mcp.revka_config._CONFIG_PATH", str(cfg)):
            assert mcp_servers_by_name([]) == {}

    def test_missing_config_file_returns_empty(self, tmp_path):
        with patch("operator_mcp.revka_config._CONFIG_PATH", str(tmp_path / "missing.toml")):
            assert mcp_servers_by_name(["OpenCrab"]) == {}

    def test_multiple_names(self, tmp_path):
        cfg = tmp_path / "config.toml"
        cfg.write_text(_CONFIG_TOML, encoding="utf-8")
        with patch("operator_mcp.revka_config._CONFIG_PATH", str(cfg)):
            matched = mcp_servers_by_name(["OpenCrab", "LocalTool"])
        assert set(matched) == {"OpenCrab", "LocalTool"}


class TestExternalServerConfig:
    """Converting a config.toml entry to the shape agent_subprocess.py expects."""

    def test_stdio_entry(self):
        cfg = _external_server_config({
            "name": "LocalTool", "transport": "stdio",
            "command": "python3", "args": ["-m", "local_tool"], "env": {"X": "1"},
        })
        assert cfg == {"type": "stdio", "command": "python3", "args": ["-m", "local_tool"], "env": {"X": "1"}}

    def test_http_entry(self):
        cfg = _external_server_config({
            "name": "OpenCrab", "transport": "http", "url": "https://opencrab.sh/api/mcp/x",
        })
        assert cfg == {"type": "http", "url": "https://opencrab.sh/api/mcp/x"}

    def test_http_entry_with_headers(self):
        cfg = _external_server_config({
            "name": "OpenCrab", "transport": "http",
            "url": "https://opencrab.sh/api/mcp/x", "headers": {"Authorization": "Bearer t"},
        })
        assert cfg["headers"] == {"Authorization": "Bearer t"}

    def test_sse_entry(self):
        cfg = _external_server_config({"name": "S", "transport": "sse", "url": "https://s.example/mcp"})
        assert cfg == {"type": "sse", "url": "https://s.example/mcp"}

    def test_defaults_to_stdio_transport(self):
        # McpTransport::Stdio is the Rust-side default when `transport` is omitted.
        assert _external_server_config({"name": "S", "command": "cmd"}) == {
            "type": "stdio", "command": "cmd", "args": [], "env": {},
        }

    def test_stdio_missing_command_is_malformed(self):
        assert _external_server_config({"name": "S", "transport": "stdio"}) is None

    def test_http_missing_url_is_malformed(self):
        assert _external_server_config({"name": "S", "transport": "http"}) is None

    def test_unknown_transport_is_malformed(self):
        assert _external_server_config({"name": "S", "transport": "carrier-pigeon", "url": "x"}) is None


class TestBuildMcpServersExtraServerNames:
    """extra_server_names is additive and independent of the tools tier."""

    def test_merges_matching_external_server(self, tmp_path):
        cfg = tmp_path / "config.toml"
        cfg.write_text(_CONFIG_TOML, encoding="utf-8")
        with patch("operator_mcp.revka_config._CONFIG_PATH", str(cfg)):
            servers = build_mcp_servers(
                include_memory=False, include_operator=False,
                extra_server_names=["OpenCrab"],
            )
        assert servers == {"OpenCrab": {"type": "http", "url": "https://opencrab.sh/api/mcp/abc123"}}

    def test_no_extra_names_unaffected(self):
        # Regression guard: adding the parameter must not change any existing caller's output.
        assert build_mcp_servers(include_memory=False, include_operator=False) == {}

    def test_unmatched_extra_name_silently_skipped(self, tmp_path):
        cfg = tmp_path / "config.toml"
        cfg.write_text(_CONFIG_TOML, encoding="utf-8")
        with patch("operator_mcp.revka_config._CONFIG_PATH", str(cfg)):
            servers = build_mcp_servers(
                include_memory=False, include_operator=False,
                extra_server_names=["Typo'dName"],
            )
        assert servers == {}

    def test_coexists_with_builtin_tiers(self, tmp_path):
        cfg = tmp_path / "config.toml"
        cfg.write_text(_CONFIG_TOML, encoding="utf-8")
        with patch("operator_mcp.revka_config._CONFIG_PATH", str(cfg)), \
             patch("os.path.exists", return_value=False):
            servers = build_mcp_servers(
                include_memory=False, include_operator=True,
                extra_server_names=["OpenCrab"],
            )
        assert "operator-tools" in servers
        assert "OpenCrab" in servers


class TestAgentStepConfigMcpServers:
    """The opt-in step field itself."""

    def test_defaults_to_empty_list(self):
        assert AgentStepConfig().mcp_servers == []

    def test_parses_from_dict(self):
        cfg = AgentStepConfig.model_validate({"mcp_servers": ["OpenCrab"], "prompt": "hi"})
        assert cfg.mcp_servers == ["OpenCrab"]

    def test_independent_of_tools_tier(self):
        # A step can forward a named server while tools stays at its "none" default —
        # this is the whole point (render-style steps that want one external server
        # without opting into the full operator-tools/kumiho-memory bundle).
        cfg = AgentStepConfig.model_validate({"mcp_servers": ["OpenCrab"]})
        assert cfg.tools == "none"
        assert cfg.mcp_servers == ["OpenCrab"]


class TestRequiredToolVisibilityForExternalServers:
    """_agent_required_tool_visible's new branch, and the false-positive it avoids.

    Before this feature, an unrecognized tool name only ever passed preflight
    via `cfg.tools == "all" and "operator-tools" in mcp_servers` — true for ANY
    unknown name under tools: all, whether or not that tool actually exists.
    The new mcp_servers-aware branch is checked first and is name-specific:
    it only passes for a tool whose server prefix was both declared in
    `mcp_servers:` AND actually resolved (present in the built mcp_servers dict).
    """

    def test_visible_when_server_declared_and_present(self):
        cfg = AgentStepConfig(mcp_servers=["OpenCrab"])
        assert _agent_required_tool_visible(
            "OpenCrab__opencrab_query", cfg=cfg, mcp_servers={"OpenCrab": {"type": "http", "url": "x"}},
        )

    def test_case_insensitive_prefix_match(self):
        cfg = AgentStepConfig(mcp_servers=["opencrab"])
        assert _agent_required_tool_visible(
            "OpenCrab__opencrab_query", cfg=cfg, mcp_servers={"OpenCrab": {"type": "http", "url": "x"}},
        )

    def test_not_visible_when_declared_but_unresolved(self):
        # Declared in mcp_servers: but config.toml had no matching entry (typo,
        # or the server was removed) - build_mcp_servers already dropped it, so
        # this must fail, not silently pass.
        cfg = AgentStepConfig(mcp_servers=["OpenCrab"])
        assert not _agent_required_tool_visible(
            "OpenCrab__opencrab_query", cfg=cfg, mcp_servers={},
        )

    def test_undeclared_server_does_not_use_this_branch(self):
        # tool's prefix doesn't match anything in cfg.mcp_servers - falls through
        # to the pre-existing (unrelated, untouched) fallback logic.
        cfg = AgentStepConfig(mcp_servers=[], tools="none")
        assert not _agent_required_tool_visible(
            "OpenCrab__opencrab_query", cfg=cfg, mcp_servers={"OpenCrab": {"type": "http", "url": "x"}},
        )

    def test_preflight_passes_for_correctly_configured_server(self, tmp_path):
        cfg = tmp_path / "config.toml"
        cfg.write_text(_CONFIG_TOML, encoding="utf-8")
        wf = WorkflowDef.model_validate({
            "name": "wf",
            "steps": [{
                "id": "render",
                "type": "agent",
                "agent": {
                    "prompt": "do the thing",
                    "mcp_servers": ["OpenCrab"],
                    "required_tools": ["OpenCrab__opencrab_query"],
                },
            }],
        })
        with patch("operator_mcp.revka_config._CONFIG_PATH", str(cfg)):
            assert _preflight_required_tool_visibility(wf) is None

    def test_preflight_fails_loudly_for_undeclared_server_name(self, tmp_path):
        cfg = tmp_path / "config.toml"
        cfg.write_text(_CONFIG_TOML, encoding="utf-8")
        wf = WorkflowDef.model_validate({
            "name": "wf",
            "steps": [{
                "id": "render",
                "type": "agent",
                "agent": {
                    "prompt": "do the thing",
                    "mcp_servers": ["TypoedServerName"],
                    "required_tools": ["TypoedServerName__opencrab_query"],
                },
            }],
        })
        with patch("operator_mcp.revka_config._CONFIG_PATH", str(cfg)):
            error = _preflight_required_tool_visibility(wf)
        assert error is not None
        assert "render" in error
