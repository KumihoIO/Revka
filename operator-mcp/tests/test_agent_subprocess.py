"""Tests for operator_mcp.agent_subprocess — _build_command, compose_agent_prompt, stderr filtering."""
from __future__ import annotations

from unittest.mock import patch

from operator_mcp.agent_subprocess import (
    _build_command,
    _codex_mcp_overrides,
    _is_stderr_noise,
    _resolve_cli,
    _write_agent_home_configs,
    compose_agent_prompt,
)


class TestResolveCli:
    """asyncio.create_subprocess_exec on Windows doesn't apply PATHEXT, so
    bare names like "claude" / "codex" fail to spawn (claude.cmd is the
    real npm shim). shutil.which() handles PATHEXT and returns the full
    path including extension — _resolve_cli wraps it with a bare-name
    fallback so a missing binary still produces a useful subprocess error.
    """

    def test_returns_resolved_path_when_found(self):
        with patch("operator_mcp.agent_subprocess.shutil.which",
                   return_value="C:\\Users\\x\\AppData\\Roaming\\npm\\claude.cmd"):
            assert _resolve_cli("claude") == "C:\\Users\\x\\AppData\\Roaming\\npm\\claude.cmd"

    def test_falls_back_to_bare_name_when_not_found(self):
        with patch("operator_mcp.agent_subprocess.shutil.which", return_value=None):
            assert _resolve_cli("claude") == "claude"


class TestBuildCommand:
    # Prompt is piped via stdin (not passed as a CLI arg) to dodge ARG_MAX
    # and shell-encoding bugs with Korean/Unicode text — so _build_command
    # only emits flags, not the prompt body.
    #
    # Tests here patch shutil.which to None so cmd[0] is the bare name;
    # the actual path-resolution behavior is covered in TestResolveCli.

    def setup_method(self):
        self._which_patcher = patch(
            "operator_mcp.agent_subprocess.shutil.which", return_value=None
        )
        self._which_patcher.start()

    def teardown_method(self):
        self._which_patcher.stop()

    def test_codex_command(self):
        cmd = _build_command("codex")
        assert cmd[0] == "codex"
        assert "--full-auto" not in cmd
        assert cmd[1:6] == [
            "--ask-for-approval",
            "never",
            "--sandbox",
            "danger-full-access",
            "exec",
        ]
        assert "--skip-git-repo-check" in cmd

    def test_claude_command(self):
        cmd = _build_command("claude")
        assert cmd[0] == "claude"
        assert "--print" in cmd
        assert "--dangerously-skip-permissions" in cmd

    def test_agy_command(self):
        cmd = _build_command("agy")
        assert cmd[0] == "agy"
        assert "--print" in cmd
        assert "--dangerously-skip-permissions" in cmd

    def test_agent_command(self):
        cmd = _build_command("agent")
        assert cmd[0] == "agent"
        assert "--print" in cmd
        assert "--dangerously-skip-permissions" in cmd

    def test_opencode_command(self):
        cmd = _build_command("opencode")
        assert cmd[0] == "opencode"
        assert cmd[1] == "run"

    def test_unknown_defaults_to_claude(self):
        cmd = _build_command("unknown-type")
        assert cmd[0] == "claude"

    def test_claude_with_model(self):
        cmd = _build_command("claude", model="claude-opus-4-7")
        assert "--model" in cmd
        assert "claude-opus-4-7" in cmd

    def test_claude_with_mcp_config(self):
        cmd = _build_command("claude", mcp_config_path="/tmp/mcp.json")
        assert "--mcp-config" in cmd
        assert "/tmp/mcp.json" in cmd

    def test_resolved_binary_replaces_bare_name(self):
        # End-to-end: when shutil.which returns a path, _build_command
        # uses that as cmd[0], not the bare name. Critical for Windows.
        resolved = "C:\\Users\\x\\AppData\\Roaming\\npm\\codex.cmd"
        with patch(
            "operator_mcp.agent_subprocess.shutil.which", return_value=resolved
        ):
            cmd = _build_command("codex")
            assert cmd[0] == resolved

    def test_codex_with_mcp_servers(self):
        servers = {
            "operator-tools": {
                "command": "/path/python3",
                "args": ["/path/script.py"],
                "env": {"FOO": "bar"},
            }
        }
        cmd = _build_command("codex", mcp_servers=servers)
        # Each leaf becomes its own -c flag
        assert cmd.count("-c") == 3
        assert any('mcp_servers.operator-tools.command="/path/python3"' in a for a in cmd)


class TestCodexMcpOverrides:
    def test_empty_dict(self):
        assert _codex_mcp_overrides({}) == []

    def test_single_server_no_env(self):
        flags = _codex_mcp_overrides({
            "srv": {"command": "/bin/x", "args": ["-y"]},
        })
        assert flags == [
            "-c", 'mcp_servers.srv.command="/bin/x"',
            "-c", 'mcp_servers.srv.args=["-y"]',
        ]

    def test_env_leaves_emit_individually(self):
        # Each env key becomes its own -c so codex's TOML parser doesn't
        # have to handle nested inline tables with edge-case escaping.
        flags = _codex_mcp_overrides({
            "srv": {
                "command": "/bin/x",
                "args": [],
                "env": {"A": "1", "B": "2"},
            },
        })
        assert '-c' in flags
        assert any('env.A="1"' in f for f in flags)
        assert any('env.B="2"' in f for f in flags)

    def test_special_chars_in_env_value(self):
        # json.dumps must escape quotes/backslashes so the value parses
        # as a TOML basic string. Otherwise codex rejects the override.
        flags = _codex_mcp_overrides({
            "srv": {
                "command": "/x",
                "args": [],
                "env": {"K": 'val with "quote" and \\back'},
            },
        })
        env_flag = next(f for f in flags if "env.K=" in f)
        assert '\\"quote\\"' in env_flag
        assert '\\\\back' in env_flag

    def test_hyphenated_server_name(self):
        # Hyphens are valid TOML bare keys; codex normalizes them to
        # underscores in its tool prefix but the config key keeps the
        # original.
        flags = _codex_mcp_overrides({
            "kumiho-memory": {"command": "/x", "args": []},
        })
        assert any("mcp_servers.kumiho-memory.command" in f for f in flags)


class TestComposeAgentPrompt:
    def test_minimal(self):
        prompt = compose_agent_prompt("Agent1", "coder", "", [], "Fix bug")
        assert "You are Agent1, a coder agent." in prompt
        assert "## Task" in prompt
        assert "Fix bug" in prompt

    def test_with_identity(self):
        prompt = compose_agent_prompt("Agent2", "reviewer", "A meticulous reviewer", [], "Review PR")
        assert "## Identity" in prompt
        assert "A meticulous reviewer" in prompt

    def test_with_expertise(self):
        prompt = compose_agent_prompt("Agent3", "coder", "", ["rust", "typescript"], "Build API")
        assert "## Expertise" in prompt
        assert "rust, typescript" in prompt

    def test_full(self):
        prompt = compose_agent_prompt(
            "Agent4", "researcher", "Deep thinker",
            ["security", "cryptography"], "Audit the auth module",
        )
        assert "You are Agent4, a researcher agent." in prompt
        assert "## Identity" in prompt
        assert "Deep thinker" in prompt
        assert "## Expertise" in prompt
        assert "security, cryptography" in prompt
        assert "## Task" in prompt
        assert "Audit the auth module" in prompt

    def test_empty_identity_skipped(self):
        prompt = compose_agent_prompt("A", "coder", "", ["python"], "task")
        assert "## Identity" not in prompt

    def test_empty_expertise_skipped(self):
        prompt = compose_agent_prompt("A", "coder", "identity", [], "task")
        assert "## Expertise" not in prompt


class TestStderrNoiseFilter:
    def test_ev_poll_posix_is_noise(self):
        assert _is_stderr_noise("ev_poll_posix.cc: some gRPC fd warning")

    def test_grpc_warning_is_noise(self):
        assert _is_stderr_noise("grpc_chttp2_transport: warning stuff")

    def test_growthbook_is_noise(self):
        assert _is_stderr_noise("GrowthBook: feature flag loaded")

    def test_telemetry_is_noise(self):
        assert _is_stderr_noise("telemetry: sending batch")

    def test_experimental_warning_is_noise(self):
        assert _is_stderr_noise("ExperimentalWarning: something")

    def test_blank_line_is_noise(self):
        assert _is_stderr_noise("   \n")

    def test_real_error_is_not_noise(self):
        assert not _is_stderr_noise("Not logged in · Please run /login")

    def test_permission_error_is_not_noise(self):
        assert not _is_stderr_noise("Error: permission denied")

    def test_connection_refused_is_not_noise(self):
        assert not _is_stderr_noise("ConnectionRefusedError: connect ECONNREFUSED")


class TestAgentHomeConfigs:
    def test_opencode_home_configs(self, tmp_path):
        import json
        import os
        servers = {
            "test-server": {
                "command": "python",
                "args": ["run.py"],
                "env": {"FOO": "bar"}
            }
        }
        with patch("operator_mcp.agent_subprocess._PROMPT_DIR", str(tmp_path)):
            home_dir, env_overrides = _write_agent_home_configs("agent-opencode", "opencode", servers)
            
            assert home_dir == os.path.join(str(tmp_path), "homes", "agent-opencode")
            assert env_overrides["HOME"] == home_dir
            assert env_overrides["XDG_CONFIG_HOME"] == os.path.join(home_dir, ".config")
            
            # Check if files config.json and opencode.json were written properly
            config_dir = os.path.join(home_dir, ".config", "opencode")
            for filename in ("config.json", "opencode.json"):
                path = os.path.join(config_dir, filename)
                assert os.path.exists(path)
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                assert "mcp" in data
                assert "test-server" in data["mcp"]
                server_cfg = data["mcp"]["test-server"]
                assert server_cfg["type"] == "local"
                assert server_cfg["command"] == ["python", "run.py"]
                assert server_cfg["environment"] == {"FOO": "bar"}

    def test_agy_home_configs(self, tmp_path):
        import json
        import os
        servers = {
            "test-server": {
                "command": "python",
                "args": ["run.py"],
                "env": {"FOO": "bar"}
            }
        }
        with patch("operator_mcp.agent_subprocess._PROMPT_DIR", str(tmp_path)):
            home_dir, env_overrides = _write_agent_home_configs("agent-agy", "agy", servers)
            
            assert home_dir == os.path.join(str(tmp_path), "homes", "agent-agy")
            
            config_file = os.path.join(home_dir, ".gemini", "antigravity-cli", "mcp_config.json")
            assert os.path.exists(config_file)
            with open(config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert "mcpServers" in data
            assert "test-server" in data["mcpServers"]
            server_cfg = data["mcpServers"]["test-server"]
            assert server_cfg["command"] == "python"
            assert server_cfg["args"] == ["run.py"]
            assert server_cfg["env"] == {"FOO": "bar"}

    def test_agent_home_configs(self, tmp_path):
        import json
        import os
        servers = {
            "test-server": {
                "command": "python",
                "args": ["run.py"],
                "env": {"FOO": "bar"}
            }
        }
        with patch("operator_mcp.agent_subprocess._PROMPT_DIR", str(tmp_path)):
            home_dir, env_overrides = _write_agent_home_configs("agent-cursor", "agent", servers)
            
            assert home_dir == os.path.join(str(tmp_path), "homes", "agent-cursor")
            
            config_file = os.path.join(home_dir, ".cursor", "mcp.json")
            assert os.path.exists(config_file)
            with open(config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert "mcpServers" in data
            assert data["mcpServers"] == servers
