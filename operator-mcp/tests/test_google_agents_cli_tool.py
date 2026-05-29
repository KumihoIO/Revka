import pytest

import operator_mcp.tool_handlers.google_agents_cli as google_agents_cli


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
