"""Tests for the codex_image generator tool.

These tests don't shell out to the real `codex` binary (CI doesn't have it
authenticated). They cover:

  * Argument validation — required fields, count bounds.
  * Output-path resolution for single + batch modes (with and without
    a custom output_pattern).
  * The end-to-end happy path with `_spawn_codex_image` mocked, so we
    exercise the canvas push and Kumiho-artifact branches.
  * The artifact-name auto-derivation — the trailing `-N` numeric
    suffix should be stripped when registering the Kumiho item.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from operator_mcp.tool_handlers import codex_image as ci

# `asyncio_mode = auto` is set in pyproject.toml so async tests are
# auto-discovered without an explicit mark; sync tests run as-is.


@pytest.fixture(autouse=True)
def _reset_cache():
    """Each test starts with a clean codex availability cache."""
    ci._reset_availability_cache()
    yield
    ci._reset_availability_cache()


@pytest.fixture
def fake_gw():
    gw = MagicMock()
    gw._available = True
    gw.gateway_url = "http://127.0.0.1:42617"
    gw._headers = MagicMock(return_value={})
    return gw


# -------------------------- argument validation ---------------------------


async def test_missing_prompt_returns_error(fake_gw, tmp_path):
    out = await ci.tool_generate_image_codex(
        {"output_path": str(tmp_path / "x.png")}, fake_gw
    )
    assert out == {"error": "prompt is required"}


async def test_missing_output_path_returns_error(fake_gw):
    out = await ci.tool_generate_image_codex({"prompt": "a fox"}, fake_gw)
    assert out == {"error": "output_path is required"}


@pytest.mark.parametrize("count", [0, 6, 10, -1])
async def test_count_out_of_range_returns_error(fake_gw, tmp_path, count):
    out = await ci.tool_generate_image_codex(
        {
            "prompt": "a fox",
            "output_path": str(tmp_path / "fox.png"),
            "count": count,
        },
        fake_gw,
    )
    assert "count must be 1..5" in out.get("error", "")


async def test_codex_not_on_path_returns_clear_error(fake_gw, tmp_path):
    with patch.object(ci.shutil, "which", return_value=None), patch.object(
        ci, "sys"
    ) as sys_mock:
        sys_mock.platform = "linux"  # disable the Windows fallback for this test
        out = await ci.tool_generate_image_codex(
            {
                "prompt": "a fox",
                "output_path": str(tmp_path / "fox.png"),
                "register_artifact": False,
            },
            fake_gw,
        )
    assert "codex CLI not found" in out.get("error", "")


def test_run_subprocess_sync_redirects_stdin_to_devnull():
    """Regression: codex (or its codex.CMD shim) inherits stdin from the
    MCP server's JSON-RPC pipe and blocks reading from it forever. The
    sync wrapper must explicitly pass `stdin=subprocess.DEVNULL` so the
    child sees EOF and never reads phantom input.

    Symptom of the bug: codex spawns, sits at <0.1s CPU, generate_image_codex
    hangs forever. Confirmed by the user on Windows + MCP stdio transport.
    """
    import inspect

    src = inspect.getsource(ci._run_subprocess_sync)
    # The wrapper must pass DEVNULL to subprocess.run.
    assert "subprocess.DEVNULL" in src, (
        "_run_subprocess_sync must redirect stdin to DEVNULL — "
        "otherwise codex blocks reading the inherited MCP JSON-RPC pipe "
        "on Windows."
    )


def test_subprocess_path_uses_to_thread_not_asyncio_create_subprocess():
    """Regression: on Windows in MCP context, asyncio.create_subprocess_exec
    hangs indefinitely. The implementation must route through
    `_run_subprocess_sync` (called via `asyncio.to_thread`) so subprocess
    work happens in a thread-pool worker, not on the asyncio loop.
    """
    import inspect
    import re

    src = inspect.getsource(ci)
    # Strip docstrings and comments — they're allowed to *mention*
    # create_subprocess_exec for historical context. We only fail on
    # actual invocations: `asyncio.create_subprocess_exec(`.
    code_only_lines = []
    in_triple = False
    for line in src.splitlines():
        stripped = line.strip()
        triples = stripped.count('"""')
        if triples % 2 == 1:
            in_triple = not in_triple
            continue
        if in_triple:
            continue
        if stripped.startswith("#"):
            continue
        # Drop end-of-line comments.
        if "#" in line:
            line = line.split("#", 1)[0]
        code_only_lines.append(line)
    code_only = "\n".join(code_only_lines)

    invocation = re.search(r"asyncio\.create_subprocess_exec\s*\(", code_only)
    assert invocation is None, (
        "codex_image.py must not call asyncio.create_subprocess_exec — "
        "it hangs on Windows + anyio MCP loops. Use asyncio.to_thread + "
        "_run_subprocess_sync instead."
    )
    assert "asyncio.to_thread" in code_only
    assert "_run_subprocess_sync" in code_only


# --------- platform-aware sandbox default ---------------------------------


def test_is_per_user_npm_install_detects_appdata_path(monkeypatch):
    """Per-user npm Windows installs land under %APPDATA%\\npm — those are
    the installs whose sandbox helper can't spawn children."""
    monkeypatch.setenv("APPDATA", r"C:\Users\alice\AppData\Roaming")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with patch.object(ci, "sys") as sys_mock:
        sys_mock.platform = "win32"
        assert ci._is_per_user_npm_install(
            r"C:\Users\alice\AppData\Roaming\npm\codex.CMD"
        )
        # Case-insensitive match (Windows paths).
        assert ci._is_per_user_npm_install(
            r"c:\users\alice\appdata\roaming\NPM\Codex.cmd"
        )


def test_is_per_user_npm_install_rejects_system_install(monkeypatch):
    """Admin-installed codex (Program Files / system npm) is NOT per-user."""
    monkeypatch.setenv("APPDATA", r"C:\Users\alice\AppData\Roaming")
    with patch.object(ci, "sys") as sys_mock:
        sys_mock.platform = "win32"
        assert not ci._is_per_user_npm_install(
            r"C:\Program Files\nodejs\codex.cmd"
        )
        assert not ci._is_per_user_npm_install(r"C:\tools\codex.exe")


def test_is_per_user_npm_install_returns_false_on_posix():
    """The per-user helper concept is Windows-only."""
    with patch.object(ci, "sys") as sys_mock:
        sys_mock.platform = "linux"
        assert not ci._is_per_user_npm_install("/home/alice/.npm-global/bin/codex")
        sys_mock.platform = "darwin"
        assert not ci._is_per_user_npm_install("/usr/local/bin/codex")


def test_default_sandbox_picks_danger_for_per_user_windows(monkeypatch):
    """Per-user Windows npm install → danger-full-access default."""
    monkeypatch.setenv("APPDATA", r"C:\Users\alice\AppData\Roaming")
    with patch.object(ci, "sys") as sys_mock:
        sys_mock.platform = "win32"
        assert (
            ci._default_sandbox(r"C:\Users\alice\AppData\Roaming\npm\codex.CMD")
            == "danger-full-access"
        )


def test_default_sandbox_keeps_workspace_write_for_admin_install(monkeypatch):
    """Admin Windows install / macOS / Linux → workspace-write (safer)."""
    monkeypatch.setenv("APPDATA", r"C:\Users\alice\AppData\Roaming")
    with patch.object(ci, "sys") as sys_mock:
        sys_mock.platform = "win32"
        assert (
            ci._default_sandbox(r"C:\Program Files\nodejs\codex.cmd")
            == "workspace-write"
        )
        sys_mock.platform = "linux"
        assert ci._default_sandbox("/usr/local/bin/codex") == "workspace-write"


async def test_tool_uses_platform_default_when_sandbox_arg_omitted(
    fake_gw, tmp_path, monkeypatch
):
    """End-to-end: per-user Windows install + no `sandbox` arg →
    danger-full-access flows through to the spawn."""
    target = tmp_path / "fox.png"
    captured: dict[str, Any] = {}

    async def _fake_spawn(prompt, output_path, cwd, codex_executable=None, sandbox="workspace-write"):
        captured["sandbox"] = sandbox
        output_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        return {"ok": True, "path": str(output_path), "size": 100}

    monkeypatch.setenv("APPDATA", r"C:\Users\alice\AppData\Roaming")
    fake_codex = r"C:\Users\alice\AppData\Roaming\npm\codex.CMD"

    with patch.object(
        ci,
        "_check_codex_available",
        AsyncMock(return_value={"ok": True, "executable": fake_codex}),
    ), patch.object(ci, "_spawn_codex_image", side_effect=_fake_spawn), patch.object(
        ci, "sys"
    ) as sys_mock:
        sys_mock.platform = "win32"
        await ci.tool_generate_image_codex(
            {
                "prompt": "fox",
                "output_path": str(target),
                "register_artifact": False,
            },
            fake_gw,
        )

    assert captured["sandbox"] == "danger-full-access"


def test_resolve_codex_executable_uses_shutil_which_when_available():
    with patch.object(ci.shutil, "which", return_value="/usr/local/bin/codex"):
        assert ci._resolve_codex_executable() == "/usr/local/bin/codex"


async def test_check_codex_available_accepts_login_marker_on_stderr():
    """Windows `.CMD` shim prints `Logged in using ChatGPT` to stderr.

    Regression for the false-negative authentication check that surfaced
    after PR #177: the resolver found codex.CMD and the subprocess ran
    cleanly, but the login-marker scan only looked at stdout.
    """

    def _fake_run(cmd, timeout=None):
        return (0, b"", b"Logged in using ChatGPT\n")

    with patch.object(
        ci, "_resolve_codex_executable", return_value="/fake/codex.CMD"
    ), patch.object(ci, "_run_subprocess_sync", side_effect=_fake_run):
        result = await ci._check_codex_available()
    assert result == {"ok": True, "executable": "/fake/codex.CMD"}


async def test_check_codex_available_rejects_when_neither_stream_has_marker():
    """If neither stdout nor stderr mentions a login, fail with a clear hint."""

    def _fake_run(cmd, timeout=None):
        return (0, b"Not logged in\n", b"")

    with patch.object(
        ci, "_resolve_codex_executable", return_value="/fake/codex"
    ), patch.object(ci, "_run_subprocess_sync", side_effect=_fake_run):
        result = await ci._check_codex_available()
    assert result["ok"] is False
    assert "not authenticated" in result["error"]


def test_resolve_codex_executable_windows_npm_fallback(tmp_path, monkeypatch):
    """When PATH doesn't have codex, Windows checks %APPDATA%\\npm\\codex.CMD."""
    fake_npm = tmp_path / "npm"
    fake_npm.mkdir()
    fake_codex = fake_npm / "codex.CMD"
    fake_codex.write_text("@echo codex shim")

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with patch.object(ci.shutil, "which", return_value=None), patch.object(
        ci, "sys"
    ) as sys_mock:
        sys_mock.platform = "win32"
        resolved = ci._resolve_codex_executable()
    assert resolved == str(fake_codex)


def test_resolve_codex_executable_returns_none_when_truly_missing(monkeypatch):
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with patch.object(ci.shutil, "which", return_value=None), patch.object(
        ci, "sys"
    ) as sys_mock:
        sys_mock.platform = "win32"
        assert ci._resolve_codex_executable() is None


# -------------------------- path resolution -------------------------------


def test_resolve_paths_single_relative(tmp_path):
    paths = ci._resolve_output_paths(
        output_path="logo.png",
        count=1,
        pattern=None,
        cwd=str(tmp_path),
    )
    assert paths == [tmp_path / "logo.png"]


def test_resolve_paths_single_absolute(tmp_path):
    abs_target = tmp_path / "out" / "logo.png"
    paths = ci._resolve_output_paths(
        output_path=str(abs_target),
        count=1,
        pattern=None,
        cwd=str(tmp_path),
    )
    assert paths == [abs_target]


def test_resolve_paths_batch_default_pattern(tmp_path):
    paths = ci._resolve_output_paths(
        output_path="logo.png",
        count=3,
        pattern=None,
        cwd=str(tmp_path),
    )
    assert [p.name for p in paths] == ["logo-1.png", "logo-2.png", "logo-3.png"]
    assert all(p.parent == tmp_path for p in paths)


def test_resolve_paths_batch_custom_pattern(tmp_path):
    paths = ci._resolve_output_paths(
        output_path="ignored.png",
        count=2,
        pattern="frame-{n}.png",
        cwd=str(tmp_path),
    )
    assert [p.name for p in paths] == ["frame-1.png", "frame-2.png"]


def test_split_prompt_image_markers_extracts_operator_chat_attachments(tmp_path):
    ref = tmp_path / "reference.png"
    prompt, images = ci._split_prompt_image_markers(
        f"make a variation\n[IMAGE:{ref}]\nkeep the silhouette"
    )

    assert prompt == "make a variation\n\nkeep the silhouette"
    assert images == [str(ref)]


async def test_spawn_codex_image_passes_input_images_to_cli(tmp_path):
    target = tmp_path / "out.png"
    ref = tmp_path / "reference.png"
    ref.write_bytes(b"\x89PNG\r\n\x1a\n")
    captured: dict = {}

    def _fake_run(cmd, timeout=None):
        captured["cmd"] = cmd
        target.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        return (0, b"", b"")

    with patch.object(ci, "_run_subprocess_sync", side_effect=_fake_run):
        out = await ci._spawn_codex_image(
            "make a variation",
            target,
            tmp_path,
            codex_executable="/fake/codex",
            input_images=[ref],
        )

    assert out["ok"] is True
    cmd = captured["cmd"]
    assert cmd[0:2] == ["/fake/codex", "exec"]
    assert "--image" in cmd
    assert cmd[cmd.index("--image") + 1] == str(ref)
    assert "attached image file(s)" in cmd[-1]


async def test_tool_extracts_prompt_image_markers_and_forwards_to_spawn(fake_gw, tmp_path):
    target = tmp_path / "fox.png"
    ref = tmp_path / "reference.png"
    ref.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    captured: dict = {}

    async def _fake_spawn(
        prompt,
        output_path,
        cwd,
        codex_executable=None,
        sandbox="workspace-write",
        input_images=None,
    ):
        captured["prompt"] = prompt
        captured["input_images"] = input_images
        output_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        return {"ok": True, "path": str(output_path), "size": 100}

    with patch.object(
        ci,
        "_check_codex_available",
        AsyncMock(return_value={"ok": True, "executable": "/fake/codex"}),
    ), patch.object(ci, "_spawn_codex_image", side_effect=_fake_spawn):
        out = await ci.tool_generate_image_codex(
            {
                "prompt": f"make a fox variation [IMAGE:{ref}]",
                "output_path": str(target),
                "register_artifact": False,
            },
            fake_gw,
        )

    assert out["generated"] == 1
    assert out["input_images"] == [str(ref.resolve(strict=False))]
    assert captured["prompt"] == "make a fox variation"
    assert captured["input_images"] == [ref.resolve(strict=False)]


async def test_tool_accepts_explicit_input_images_relative_to_cwd(fake_gw, tmp_path):
    target = tmp_path / "fox.png"
    ref = tmp_path / "reference.png"
    ref.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    captured: dict = {}

    async def _fake_spawn(
        prompt,
        output_path,
        cwd,
        codex_executable=None,
        sandbox="workspace-write",
        input_images=None,
    ):
        captured["input_images"] = input_images
        output_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        return {"ok": True, "path": str(output_path), "size": 100}

    with patch.object(
        ci,
        "_check_codex_available",
        AsyncMock(return_value={"ok": True, "executable": "/fake/codex"}),
    ), patch.object(ci, "_spawn_codex_image", side_effect=_fake_spawn):
        out = await ci.tool_generate_image_codex(
            {
                "prompt": "make a fox variation",
                "output_path": str(target),
                "cwd": str(tmp_path),
                "input_images": ["reference.png"],
                "register_artifact": False,
            },
            fake_gw,
        )

    assert out["generated"] == 1
    assert captured["input_images"] == [ref.resolve(strict=False)]


async def test_tool_rejects_non_local_input_images(fake_gw, tmp_path):
    out = await ci.tool_generate_image_codex(
        {
            "prompt": "make a fox variation",
            "output_path": str(tmp_path / "fox.png"),
            "input_images": ["https://example.com/reference.png"],
            "register_artifact": False,
        },
        fake_gw,
    )

    assert "local file paths" in out.get("error", "")


# -------------------------- happy path ------------------------------------


async def test_happy_path_single_image_with_canvas_and_artifact(fake_gw, tmp_path):
    """Mock codex spawn + canvas + Kumiho; verify the full path end-to-end."""

    async def _fake_spawn(prompt, output_path, cwd, codex_executable=None, sandbox="workspace-write"):
        # Write a non-empty PNG-like file to simulate codex success.
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        return {"ok": True, "path": str(output_path), "size": output_path.stat().st_size}

    async def _fake_canvas(paths, canvas_id, gw, workspace_dir):
        return {"canvas_id": canvas_id, "frame_id": "frame-1", "image_count": len(paths)}

    fake_artifact_kref = "kref://Revka/Images/fox.image?r=1#a1"
    fake_rev_kref = "kref://Revka/Images/fox.image?r=1"
    fake_item_kref = "kref://Revka/Images/fox.image"

    sdk_mock = MagicMock()
    sdk_mock.ensure_space = AsyncMock(return_value=None)
    sdk_mock.create_item = AsyncMock(return_value={"kref": fake_item_kref})
    sdk_mock.create_revision = AsyncMock(return_value={"kref": fake_rev_kref})
    sdk_mock.create_artifact = AsyncMock(return_value={"kref": fake_artifact_kref})

    with patch.object(
        ci, "_check_codex_available", AsyncMock(return_value={"ok": True, "executable": "/usr/local/bin/codex"})
    ), patch.object(ci, "_spawn_codex_image", side_effect=_fake_spawn), patch.object(
        ci, "_push_to_canvas", side_effect=_fake_canvas
    ), patch("operator_mcp.operator_mcp.KUMIHO_SDK", sdk_mock), patch.object(
        ci, "harness_project", lambda: "Revka"
    ), patch.object(ci, "_WORKSPACE_ROOT", tmp_path):
        out = await ci.tool_generate_image_codex(
            {
                "prompt": "a fox in a forest",
                "output_path": "fox.png",
                "count": 1,
                "canvas": True,
                "register_artifact": True,
            },
            fake_gw,
        )

    expected_path = tmp_path / "Revka" / "Images" / "fox.image" / "r1" / "fox.png"
    assert out["generated"] == 1
    assert out["files"] == [str(expected_path)]
    assert out["canvas"]["frame_id"] == "frame-1"
    assert out["artifact"]["item_kref"] == fake_item_kref
    assert out["artifact"]["revision_kref"] == fake_rev_kref
    assert out["artifact"]["revision_number"] == 1
    assert fake_artifact_kref in out["artifact"]["artifact_krefs"]
    assert out["artifact"]["space_path"] == "Revka/Images"
    assert out["artifact"]["directory"] == str(expected_path.parent)

    sdk_mock.create_item.assert_awaited_once()
    sdk_mock.create_revision.assert_awaited_once()
    sdk_mock.create_artifact.assert_awaited_once()


async def test_batch_mode_lays_files_under_revision_dir(fake_gw, tmp_path):
    """Batch generation places all PNGs under <ws>/<harness>/<space>/<item>.<kind>/r<N>/."""

    async def _fake_spawn(prompt, output_path, cwd, codex_executable=None, sandbox="workspace-write"):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        return {"ok": True, "path": str(output_path), "size": 100}

    sdk_mock = MagicMock()
    sdk_mock.ensure_space = AsyncMock(return_value=None)
    sdk_mock.create_item = AsyncMock(return_value={"kref": "kref://Revka/Images/logo.image"})
    sdk_mock.create_revision = AsyncMock(
        return_value={"kref": "kref://Revka/Images/logo.image?r=2"}
    )
    sdk_mock.create_artifact = AsyncMock(return_value={"kref": "art"})

    with patch.object(
        ci, "_check_codex_available", AsyncMock(return_value={"ok": True, "executable": "/usr/local/bin/codex"})
    ), patch.object(ci, "_spawn_codex_image", side_effect=_fake_spawn), patch(
        "operator_mcp.operator_mcp.KUMIHO_SDK", sdk_mock
    ), patch.object(ci, "harness_project", lambda: "Revka"), patch.object(
        ci, "_WORKSPACE_ROOT", tmp_path
    ):
        out = await ci.tool_generate_image_codex(
            {
                "prompt": "revka logo",
                "output_path": "logo.png",
                "count": 3,
                "canvas": False,
                "register_artifact": True,
            },
            fake_gw,
        )

    rev_dir = tmp_path / "Revka" / "Images" / "logo.image" / "r2"
    assert out["generated"] == 3
    assert out["files"] == [
        str(rev_dir / "logo-1.png"),
        str(rev_dir / "logo-2.png"),
        str(rev_dir / "logo-3.png"),
    ]
    assert out["artifact"]["revision_number"] == 2
    assert out["artifact"]["directory"] == str(rev_dir)
    # The item name passed to create_item should be the bare stem, not "logo-1".
    create_item_kwargs = sdk_mock.create_item.await_args.kwargs
    assert create_item_kwargs["name"] == "logo"
    assert create_item_kwargs["space_path"] == "Revka/Images"
    # All 3 PNGs are attached as artifacts to the same revision.
    assert sdk_mock.create_artifact.await_count == 3


async def test_register_artifact_false_uses_legacy_cwd_layout(fake_gw, tmp_path):
    """When register_artifact is off, fall back to the cwd-rooted output_path."""
    target = tmp_path / "fox.png"

    async def _fake_spawn(prompt, output_path, cwd, codex_executable=None, sandbox="workspace-write"):
        output_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        return {"ok": True, "path": str(output_path), "size": 100}

    sdk_mock = MagicMock()
    sdk_mock.create_item = AsyncMock()

    with patch.object(
        ci, "_check_codex_available", AsyncMock(return_value={"ok": True, "executable": "/usr/local/bin/codex"})
    ), patch.object(ci, "_spawn_codex_image", side_effect=_fake_spawn), patch(
        "operator_mcp.operator_mcp.KUMIHO_SDK", sdk_mock
    ):
        out = await ci.tool_generate_image_codex(
            {
                "prompt": "a fox",
                "output_path": str(target),
                "register_artifact": False,
            },
            fake_gw,
        )

    assert out["generated"] == 1
    assert out["files"] == [str(target)]  # legacy layout honored
    assert "artifact" not in out
    sdk_mock.create_item.assert_not_called()


async def test_custom_space_and_item_name_drive_kref_path(fake_gw, tmp_path):
    """User-provided `space` and `item_name` flow through to the on-disk path."""

    async def _fake_spawn(prompt, output_path, cwd, codex_executable=None, sandbox="workspace-write"):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        return {"ok": True, "path": str(output_path), "size": 100}

    sdk_mock = MagicMock()
    sdk_mock.ensure_space = AsyncMock(return_value=None)
    sdk_mock.create_item = AsyncMock(return_value={"kref": "i"})
    sdk_mock.create_revision = AsyncMock(
        return_value={"kref": "kref://Revka/Marketing/Logos/q2-rebrand.image?r=1"}
    )
    sdk_mock.create_artifact = AsyncMock(return_value={"kref": "a"})

    with patch.object(
        ci, "_check_codex_available", AsyncMock(return_value={"ok": True, "executable": "/usr/local/bin/codex"})
    ), patch.object(ci, "_spawn_codex_image", side_effect=_fake_spawn), patch(
        "operator_mcp.operator_mcp.KUMIHO_SDK", sdk_mock
    ), patch.object(ci, "harness_project", lambda: "Revka"), patch.object(
        ci, "_WORKSPACE_ROOT", tmp_path
    ):
        out = await ci.tool_generate_image_codex(
            {
                "prompt": "revka quarterly logo",
                "output_path": "logo.png",
                "register_artifact": True,
                "space": "Marketing/Logos",
                "item_name": "q2-rebrand",
            },
            fake_gw,
        )

    expected_path = (
        tmp_path / "Revka" / "Marketing" / "Logos" / "q2-rebrand.image" / "r1" / "logo.png"
    )
    assert out["files"] == [str(expected_path)]
    assert out["artifact"]["space_path"] == "Revka/Marketing/Logos"
    assert out["artifact"]["directory"] == str(expected_path.parent)
    create_item_kwargs = sdk_mock.create_item.await_args.kwargs
    assert create_item_kwargs["name"] == "q2-rebrand"
    assert create_item_kwargs["space_path"] == "Revka/Marketing/Logos"
    # ensure_space is called with the top segment of a multi-segment space.
    sdk_mock.ensure_space.assert_awaited_once_with("Revka", "Marketing")


async def test_space_default_is_images(fake_gw, tmp_path):
    """Omitting `space` falls back to `Images`; ensure_space sees that."""

    async def _fake_spawn(prompt, output_path, cwd, codex_executable=None, sandbox="workspace-write"):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        return {"ok": True, "path": str(output_path), "size": 100}

    sdk_mock = MagicMock()
    sdk_mock.ensure_space = AsyncMock(return_value=None)
    sdk_mock.create_item = AsyncMock(return_value={"kref": "i"})
    sdk_mock.create_revision = AsyncMock(return_value={"kref": "r"})
    sdk_mock.create_artifact = AsyncMock(return_value={"kref": "a"})

    with patch.object(
        ci, "_check_codex_available", AsyncMock(return_value={"ok": True, "executable": "/usr/local/bin/codex"})
    ), patch.object(ci, "_spawn_codex_image", side_effect=_fake_spawn), patch(
        "operator_mcp.operator_mcp.KUMIHO_SDK", sdk_mock
    ), patch.object(ci, "harness_project", lambda: "Revka"), patch.object(
        ci, "_WORKSPACE_ROOT", tmp_path
    ):
        out = await ci.tool_generate_image_codex(
            {
                "prompt": "fox",
                "output_path": "fox.png",
                "register_artifact": True,
            },
            fake_gw,
        )

    assert out["artifact"]["space_path"] == "Revka/Images"
    sdk_mock.ensure_space.assert_awaited_once_with("Revka", "Images")


async def test_create_item_failure_falls_back_to_legacy_layout(fake_gw, tmp_path):
    """If Kumiho item creation fails, the PNG still lands at the cwd path."""
    target = tmp_path / "fox.png"

    async def _fake_spawn(prompt, output_path, cwd, codex_executable=None, sandbox="workspace-write"):
        output_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        return {"ok": True, "path": str(output_path), "size": 100}

    sdk_mock = MagicMock()
    sdk_mock.ensure_space = AsyncMock(return_value=None)
    sdk_mock.create_item = AsyncMock(side_effect=RuntimeError("kumiho down"))
    sdk_mock.create_revision = AsyncMock()
    sdk_mock.create_artifact = AsyncMock()

    with patch.object(
        ci, "_check_codex_available", AsyncMock(return_value={"ok": True, "executable": "/usr/local/bin/codex"})
    ), patch.object(ci, "_spawn_codex_image", side_effect=_fake_spawn), patch(
        "operator_mcp.operator_mcp.KUMIHO_SDK", sdk_mock
    ), patch.object(ci, "harness_project", lambda: "Revka"):
        out = await ci.tool_generate_image_codex(
            {
                "prompt": "a fox",
                "output_path": str(target),
                "register_artifact": True,
                "cwd": str(tmp_path),
            },
            fake_gw,
        )

    # Falls back to legacy cwd-rooted layout when Kumiho item creation fails.
    assert out["generated"] == 1
    assert out["files"] == [str(target)]
    # The error from create_item is surfaced under `artifact`.
    assert "create_item failed" in out["artifact"]["error"]


async def test_sandbox_arg_is_threaded_into_codex_command(fake_gw, tmp_path):
    """User-supplied `sandbox` reaches `_spawn_codex_image`."""
    target = tmp_path / "fox.png"
    captured: dict[str, Any] = {}

    async def _fake_spawn(prompt, output_path, cwd, codex_executable=None, sandbox="workspace-write"):
        captured["sandbox"] = sandbox
        output_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        return {"ok": True, "path": str(output_path), "size": 100}

    with patch.object(
        ci, "_check_codex_available", AsyncMock(return_value={"ok": True, "executable": "/fake/codex"})
    ), patch.object(ci, "_spawn_codex_image", side_effect=_fake_spawn):
        out = await ci.tool_generate_image_codex(
            {
                "prompt": "fox",
                "output_path": str(target),
                "register_artifact": False,
                "sandbox": "danger-full-access",
            },
            fake_gw,
        )

    assert out["generated"] == 1
    assert captured["sandbox"] == "danger-full-access"


async def test_sandbox_default_is_workspace_write(fake_gw, tmp_path):
    """Omitting `sandbox` defaults to workspace-write."""
    target = tmp_path / "fox.png"
    captured: dict[str, Any] = {}

    async def _fake_spawn(prompt, output_path, cwd, codex_executable=None, sandbox="workspace-write"):
        captured["sandbox"] = sandbox
        output_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        return {"ok": True, "path": str(output_path), "size": 100}

    with patch.object(
        ci, "_check_codex_available", AsyncMock(return_value={"ok": True, "executable": "/fake/codex"})
    ), patch.object(ci, "_spawn_codex_image", side_effect=_fake_spawn):
        await ci.tool_generate_image_codex(
            {
                "prompt": "fox",
                "output_path": str(target),
                "register_artifact": False,
            },
            fake_gw,
        )

    assert captured["sandbox"] == "workspace-write"


async def test_invalid_sandbox_value_is_rejected(fake_gw, tmp_path):
    out = await ci.tool_generate_image_codex(
        {
            "prompt": "fox",
            "output_path": str(tmp_path / "fox.png"),
            "register_artifact": False,
            "sandbox": "wide-open",
        },
        fake_gw,
    )
    assert "sandbox must be one of" in out.get("error", "")


async def test_partial_failure_reports_failures_and_keeps_successes(fake_gw, tmp_path):
    """If 1 of 2 codex spawns fails, the response includes both arrays."""
    target = tmp_path / "fox.png"

    async def _flaky_spawn(prompt, output_path, cwd, codex_executable=None, sandbox="workspace-write"):
        if output_path.name == "fox-1.png":
            output_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
            return {"ok": True, "path": str(output_path), "size": 100}
        return {"ok": False, "path": str(output_path), "error": "simulated codex failure"}

    sdk_mock = MagicMock()
    sdk_mock.ensure_space = AsyncMock(return_value=None)
    sdk_mock.create_item = AsyncMock(return_value={"kref": "i"})
    sdk_mock.create_revision = AsyncMock(return_value={"kref": "r"})
    sdk_mock.create_artifact = AsyncMock(return_value={"kref": "a"})

    with patch.object(
        ci, "_check_codex_available", AsyncMock(return_value={"ok": True, "executable": "/usr/local/bin/codex"})
    ), patch.object(ci, "_spawn_codex_image", side_effect=_flaky_spawn), patch(
        "operator_mcp.operator_mcp.KUMIHO_SDK", sdk_mock
    ), patch.object(ci, "harness_project", lambda: "Revka"):
        out = await ci.tool_generate_image_codex(
            {
                "prompt": "a fox",
                "output_path": str(target),
                "count": 2,
                "register_artifact": True,
            },
            fake_gw,
        )

    assert out["generated"] == 1
    assert out["requested"] == 2
    assert len(out["failures"]) == 1
    assert "simulated codex failure" in out["failures"][0]["error"]
