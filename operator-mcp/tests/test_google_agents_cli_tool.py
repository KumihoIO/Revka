import os

import pytest

import operator_mcp.tool_handlers.google_agents_cli as google_agents_cli


def _write_fake_agents_cli(bin_dir, body: str):
    script = bin_dir / "agents-cli"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, time\n"
        f"{body}\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _prepend_path(monkeypatch, bin_dir):
    old_path = str(bin_dir)
    if old := os.environ.get("PATH"):
        old_path += os.pathsep + old
    monkeypatch.setenv("PATH", old_path)


def test_google_agents_cli_accepts_public_lifecycle_commands():
    for command in [
        "setup",
        "create",
        "scaffold",
        "install",
        "lint",
        "run",
        "eval",
        "deploy",
        "publish",
        "infra",
        "data-ingestion",
        "playground",
        "update",
        "login",
        "info",
    ]:
        args = [command, "--status"] if command == "login" else [command]
        assert google_agents_cli._validate_command(args, allow_interactive=False) is None


@pytest.mark.asyncio
async def test_google_agents_cli_requires_command_or_prompt():
    result = await google_agents_cli.tool_google_agents_cli({})
    assert result["error"]
    assert result["error_code"] == "missing_command"


@pytest.mark.asyncio
async def test_google_agents_cli_rejects_interactive_login_by_default():
    result = await google_agents_cli.tool_google_agents_cli({"command": ["login", "--interactive"]})
    assert result["error"]
    assert result["error_code"] == "invalid_command"
    assert "Interactive" in result["error"]


@pytest.mark.asyncio
async def test_google_agents_cli_prompt_only_allowed_with_run():
    result = await google_agents_cli.tool_google_agents_cli({
        "command": ["deploy"],
        "prompt": "run this",
    })
    assert result["error"]
    assert result["error_code"] == "invalid_command"
    assert "prompt" in result["error"]


@pytest.mark.asyncio
async def test_google_agents_cli_rejects_non_string_command_tokens():
    result = await google_agents_cli.tool_google_agents_cli({"command": ["run", 1]})
    assert result["error"]
    assert result["error_code"] == "invalid_command"
    assert "strings" in result["error"]


@pytest.mark.asyncio
async def test_google_agents_cli_rejects_whitespace_padded_command_tokens():
    result = await google_agents_cli.tool_google_agents_cli({"command": [" run"]})
    assert result["error"]
    assert result["error_code"] == "invalid_command"
    assert "whitespace" in result["error"]


@pytest.mark.asyncio
async def test_google_agents_cli_rejects_working_directory_outside_workspace(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(google_agents_cli, "workspace_dir", lambda: str(workspace))

    result = await google_agents_cli.tool_google_agents_cli({
        "command": ["login", "--status"],
        "working_directory": str(outside),
    })

    assert result["error"]
    assert result["error_code"] == "bad_working_directory"
    assert "outside the workspace" in result["error"]


@pytest.mark.asyncio
async def test_google_agents_cli_missing_binary_returns_structured_error(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()
    monkeypatch.setattr(google_agents_cli, "workspace_dir", lambda: str(workspace))
    monkeypatch.setenv("PATH", str(empty_bin))

    result = await google_agents_cli.tool_google_agents_cli({"command": ["login", "--status"]})

    assert result["error"]
    assert result["error_code"] == "agents_cli_missing"
    assert result["error_category"] == "runtime_env_error"


@pytest.mark.asyncio
async def test_google_agents_cli_success_command(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    project = workspace / "adk-project"
    project.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_agents_cli(
        bin_dir,
        "print('ok:' + ' '.join(sys.argv[1:]))",
    )
    _prepend_path(monkeypatch, bin_dir)
    monkeypatch.setattr(google_agents_cli, "workspace_dir", lambda: str(workspace))

    result = await google_agents_cli.tool_google_agents_cli({
        "command": ["lint"],
        "working_directory": "adk-project",
    })

    assert result["success"] is True
    assert result["status"] == "completed"
    assert result["exit_code"] == 0
    assert result["command"] == ["lint"]
    assert result["cwd"] == str(project)
    assert "ok:lint" in result["output"]


@pytest.mark.asyncio
async def test_google_agents_cli_accepts_info_command(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_agents_cli(bin_dir, "print('project info ok')")
    _prepend_path(monkeypatch, bin_dir)
    monkeypatch.setattr(google_agents_cli, "workspace_dir", lambda: str(workspace))

    result = await google_agents_cli.tool_google_agents_cli({"command": ["info"]})

    assert result["success"] is True
    assert result["command"] == ["info"]
    assert "project info ok" in result["output"]


@pytest.mark.asyncio
async def test_google_agents_cli_failed_command_preserves_stdout_and_stderr(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_agents_cli(
        bin_dir,
        "print('partial output')\n"
        "print('eval failed on surge pricing edge case', file=sys.stderr)\n"
        "sys.exit(7)",
    )
    _prepend_path(monkeypatch, bin_dir)
    monkeypatch.setattr(google_agents_cli, "workspace_dir", lambda: str(workspace))

    result = await google_agents_cli.tool_google_agents_cli({"command": ["eval", "run"]})

    assert result["success"] is False
    assert result["status"] == "failed"
    assert result["exit_code"] == 7
    assert "partial output" in result["output"]
    assert "surge pricing" in result["stderr"]
    assert "surge pricing" in result["error"]


@pytest.mark.asyncio
async def test_google_agents_cli_prompt_defaults_to_run_and_redacts_preview(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_agents_cli(
        bin_dir,
        "print(sys.argv[1])\n"
        "print(sys.argv[2])",
    )
    _prepend_path(monkeypatch, bin_dir)
    monkeypatch.setattr(google_agents_cli, "workspace_dir", lambda: str(workspace))

    result = await google_agents_cli.tool_google_agents_cli({"prompt": "simulate an outage"})

    assert result["success"] is True
    assert result["command"] == ["run", "..."]
    assert "run" in result["output"]
    assert "simulate an outage" in result["output"]


@pytest.mark.asyncio
async def test_google_agents_cli_timeout_returns_demo_safe_result(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_agents_cli(bin_dir, "time.sleep(5)")
    _prepend_path(monkeypatch, bin_dir)
    monkeypatch.setattr(google_agents_cli, "workspace_dir", lambda: str(workspace))

    result = await google_agents_cli.tool_google_agents_cli({
        "command": ["run"],
        "timeout": 0.01,
    })

    assert result["success"] is False
    assert result["status"] == "timeout"
    assert result["exit_code"] is None
    assert "timed out" in result["error"]


@pytest.mark.asyncio
async def test_google_agents_cli_truncates_large_output(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_agents_cli(bin_dir, "print('abcdef')")
    _prepend_path(monkeypatch, bin_dir)
    monkeypatch.setattr(google_agents_cli, "workspace_dir", lambda: str(workspace))

    result = await google_agents_cli.tool_google_agents_cli({
        "command": ["run"],
        "max_output_bytes": 3,
    })

    assert result["success"] is True
    assert result["output"].startswith("abc")
    assert "[output truncated]" in result["output"]


@pytest.mark.asyncio
async def test_google_agents_cli_spawn_error_returns_structured_error(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    fake_binary = tmp_path / "agents-cli"
    fake_binary.write_text("", encoding="utf-8")
    monkeypatch.setattr(google_agents_cli, "workspace_dir", lambda: str(workspace))
    monkeypatch.setattr(google_agents_cli.shutil, "which", lambda _: str(fake_binary))

    async def raise_os_error(*args, **kwargs):
        raise PermissionError("permission denied")

    monkeypatch.setattr(google_agents_cli.asyncio, "create_subprocess_exec", raise_os_error)

    result = await google_agents_cli.tool_google_agents_cli({"command": ["login", "--status"]})

    assert result["error"]
    assert result["error_code"] == "agents_cli_spawn_failed"
    assert result["error_category"] == "runtime_env_error"
    assert result["retryable"] is True
