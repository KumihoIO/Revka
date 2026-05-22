"""Tests for operator_mcp.mcp_injection — config builders and system prompt layering."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from operator_mcp.mcp_injection import (
    _venv_python,
    _kumiho_forward_env,
    build_mcp_servers,
    build_system_prompt,
    operator_tools_config,
    kumiho_memory_config,
    workflow_memory_alias_config,
)


class TestVenvPython:
    """Platform-aware venv interpreter resolution.

    The kumiho launcher self-execs into the venv interpreter at runtime,
    but `mcp_injection.py` writes the interpreter path into the MCP config
    JSON ahead of time — so it has to pick the right one for the host OS.
    A POSIX-shaped fallback ("python3") doesn't exist on Windows where the
    convention is `python.exe` / `py.exe`, and getting this wrong manifests
    as silent MCP-server-fails-to-start in the spawned subprocess.
    """

    def test_posix_prefers_venv_python3(self, tmp_path):
        venv = tmp_path / "venv"
        (venv / "bin").mkdir(parents=True)
        bin_python3 = venv / "bin" / "python3"
        bin_python3.touch()
        with patch("operator_mcp.mcp_injection.os.name", "posix"):
            assert _venv_python(str(venv)) == str(bin_python3)

    def test_posix_falls_back_to_venv_python(self, tmp_path):
        venv = tmp_path / "venv"
        (venv / "bin").mkdir(parents=True)
        bin_python = venv / "bin" / "python"
        bin_python.touch()
        with patch("operator_mcp.mcp_injection.os.name", "posix"):
            assert _venv_python(str(venv)) == str(bin_python)

    def test_posix_system_fallback(self, tmp_path):
        with patch("operator_mcp.mcp_injection.os.name", "posix"):
            assert _venv_python(str(tmp_path / "missing")) == "python3"

    def test_windows_uses_scripts_python_exe(self, tmp_path):
        venv = tmp_path / "venv"
        (venv / "Scripts").mkdir(parents=True)
        scripts_python = venv / "Scripts" / "python.exe"
        scripts_python.touch()
        with patch("operator_mcp.mcp_injection.os.name", "nt"):
            assert _venv_python(str(venv)) == str(scripts_python)

    def test_windows_system_fallback_is_python_not_python3(self, tmp_path):
        # python3 isn't conventionally on PATH on Windows; falling back to
        # "python3" was the bug fixed in this PR.
        with patch("operator_mcp.mcp_injection.os.name", "nt"):
            assert _venv_python(str(tmp_path / "missing")) == "python"


class TestKumihoMemoryConfig:
    def test_returns_none_when_script_missing(self):
        with patch("os.path.exists", return_value=False):
            assert kumiho_memory_config() is None

    def test_returns_config_when_script_exists(self):
        def exists_side_effect(path):
            return True  # both script and venv python exist
        with patch("os.path.exists", side_effect=exists_side_effect):
            config = kumiho_memory_config()
            assert config is not None
            assert config["type"] == "stdio"
            assert "kumiho" in config["args"][0].lower() or "kumiho" in config["command"].lower() or True
            assert "KUMIHO_AUTO_CONFIGURE" not in config["env"]

    def test_passes_env_vars(self):
        with patch("os.path.exists", return_value=True), \
             patch.dict("os.environ", {"KUMIHO_AUTH_TOKEN": "test-token"}):
            config = kumiho_memory_config()
            assert config["env"]["KUMIHO_AUTH_TOKEN"] == "test-token"

    def test_reads_construct_config_when_env_missing(self, tmp_path):
        cfg = tmp_path / "config.toml"
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        cfg.write_text(
            "\n".join([
                "[kumiho]",
                'api_url = "https://kumiho.example.test"',
                'auth_token = "config-token"',
                'space_prefix = "TeamSpace"',
                'memory_project = "MemoryGraph"',
                'harness_project = "HarnessGraph"',
            ]),
            encoding="utf-8",
        )
        with patch("operator_mcp.construct_config._CONFIG_PATH", str(cfg)), \
             patch("operator_mcp.construct_config._cached_workspace_dir", None), \
             patch.dict("os.environ", {"CONSTRUCT_WORKSPACE": str(workspace)}, clear=True):
            env = _kumiho_forward_env()
            assert env["KUMIHO_AUTH_TOKEN"] == "config-token"
            assert env["KUMIHO_API_URL"] == "https://kumiho.example.test"
            assert env["KUMIHO_SPACE_PREFIX"] == "TeamSpace"
            assert env["KUMIHO_MEMORY_PROJECT"] == "MemoryGraph"
            assert env["KUMIHO_HARNESS_PROJECT"] == "HarnessGraph"

    def test_reads_workspace_env_service_token_as_auth_token(self, tmp_path):
        cfg = tmp_path / "config.toml"
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / ".env").write_text(
            "KUMIHO_SERVICE_TOKEN=workspace-token\n",
            encoding="utf-8",
        )
        cfg.write_text(
            "\n".join([
                "[kumiho]",
                'api_url = "https://kumiho.example.test"',
            ]),
            encoding="utf-8",
        )
        with patch("operator_mcp.construct_config._CONFIG_PATH", str(cfg)), \
             patch("operator_mcp.construct_config._cached_workspace_dir", None), \
             patch.dict("os.environ", {"CONSTRUCT_WORKSPACE": str(workspace)}, clear=True):
            env = _kumiho_forward_env()
            assert env["KUMIHO_AUTH_TOKEN"] == "workspace-token"
            assert env["KUMIHO_SERVICE_TOKEN"] == "workspace-token"
            assert env["KUMIHO_API_URL"] == "https://kumiho.example.test"


class TestOperatorToolsConfig:
    def test_basic_config(self):
        with patch("os.path.exists", return_value=False):
            config = operator_tools_config()
            assert config["type"] == "stdio"
            assert config["command"] in {"python", "python3"}
            assert "subagent_mcp" in config["args"][0]

    def test_with_socket_path(self):
        with patch("os.path.exists", return_value=False):
            config = operator_tools_config(socket_path="/tmp/test.sock")
            assert config["env"]["CONSTRUCT_SIDECAR_SOCKET"] == "/tmp/test.sock"

    def test_no_socket_path(self):
        with patch("os.path.exists", return_value=False):
            config = operator_tools_config()
            assert config["env"] == {}


class TestWorkflowMemoryAliasConfig:
    def test_basic_config(self):
        with patch("os.path.exists", return_value=False):
            config = workflow_memory_alias_config()
            assert config["type"] == "stdio"
            assert config["command"] in {"python", "python3"}
            assert "workflow_memory_alias_mcp" in config["args"][0]

    def test_passes_kumiho_token(self):
        with patch("os.path.exists", return_value=False), \
             patch.dict("os.environ", {"KUMIHO_AUTH_TOKEN": "test-token"}):
            config = workflow_memory_alias_config()
            assert config["env"]["KUMIHO_AUTH_TOKEN"] == "test-token"


class TestBuildMcpServers:
    def test_both_servers(self):
        with patch("os.path.exists", return_value=True):
            servers = build_mcp_servers(include_memory=True, include_operator=True)
            assert "kumiho-memory" in servers
            assert "workflow-memory" in servers
            assert "operator-tools" in servers

    def test_memory_only(self):
        with patch("os.path.exists", return_value=True):
            servers = build_mcp_servers(include_memory=True, include_operator=False)
            assert "kumiho-memory" in servers
            assert "workflow-memory" in servers
            assert "operator-tools" not in servers

    def test_operator_only(self):
        with patch("os.path.exists", return_value=False):
            servers = build_mcp_servers(include_memory=True, include_operator=True)
            # kumiho script doesn't exist, so memory config returns None
            assert "kumiho-memory" not in servers
            assert "workflow-memory" in servers
            assert "operator-tools" in servers

    def test_neither(self):
        servers = build_mcp_servers(include_memory=False, include_operator=False)
        assert servers == {}


class TestBuildSystemPrompt:
    def test_top_level_with_operator(self):
        with patch("operator_mcp.skill_loader.load_skills_for_pattern", return_value=""):
            prompt = build_system_prompt(is_top_level=True, include_operator=True, include_memory=True)
            assert "sub-agent managed by the Construct Operator" in prompt
            assert "kumiho-memory MCP" in prompt

    def test_sub_agent(self):
        with patch("operator_mcp.skill_loader.load_skills_for_pattern", return_value=""):
            prompt = build_system_prompt(is_top_level=False)
            assert "worker agent spawned by a parent" in prompt

    def test_role_identity(self):
        with patch("operator_mcp.skill_loader.load_skills_for_pattern", return_value=""):
            prompt = build_system_prompt(role_identity="Expert Rust developer")
            assert "## Your Role" in prompt
            assert "Expert Rust developer" in prompt

    def test_template_hint(self):
        with patch("operator_mcp.skill_loader.load_skills_for_pattern", return_value=""):
            prompt = build_system_prompt(template_hint="Focus on performance")
            assert "## Context" in prompt
            assert "Focus on performance" in prompt

    def test_skill_pattern_injection(self):
        with patch("operator_mcp.skill_loader.load_skills_for_pattern", return_value="SKILL CONTENT HERE"):
            prompt = build_system_prompt(skill_pattern="team")
            assert "## Orchestration Skills" in prompt
            assert "SKILL CONTENT HERE" in prompt

    def test_no_memory(self):
        with patch("operator_mcp.skill_loader.load_skills_for_pattern", return_value=""):
            prompt = build_system_prompt(is_top_level=True, include_memory=False)
            assert "kumiho-memory" not in prompt

    def test_no_operator(self):
        with patch("operator_mcp.skill_loader.load_skills_for_pattern", return_value=""):
            prompt = build_system_prompt(is_top_level=True, include_operator=False, include_memory=False)
            # sub-agent preamble not present, operator prompt not present
            assert "sub-agent managed by" not in prompt
