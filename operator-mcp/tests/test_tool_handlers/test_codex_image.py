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
    with patch.object(ci.shutil, "which", return_value=None):
        out = await ci.tool_generate_image_codex(
            {
                "prompt": "a fox",
                "output_path": str(tmp_path / "fox.png"),
                "register_artifact": False,
            },
            fake_gw,
        )
    assert "codex CLI not found" in out.get("error", "")


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


# -------------------------- happy path ------------------------------------


async def test_happy_path_single_image_with_canvas_and_artifact(fake_gw, tmp_path):
    """Mock codex spawn + canvas + Kumiho; verify the full path end-to-end."""
    target = tmp_path / "fox.png"

    async def _fake_spawn(prompt, output_path, cwd):
        # Write a non-empty PNG-like file to simulate codex success.
        output_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        return {"ok": True, "path": str(output_path), "size": output_path.stat().st_size}

    async def _fake_canvas(paths, canvas_id, gw):
        return {"canvas_id": canvas_id, "frame_id": "frame-1", "image_count": len(paths)}

    fake_artifact_kref = "kref://Construct/Images/fox.image?r=1#a1"
    fake_rev_kref = "kref://Construct/Images/fox.image?r=1"
    fake_item_kref = "kref://Construct/Images/fox.image"

    sdk_mock = MagicMock()
    sdk_mock.ensure_space = AsyncMock(return_value=None)
    sdk_mock.create_item = AsyncMock(return_value={"kref": fake_item_kref})
    sdk_mock.create_revision = AsyncMock(return_value={"kref": fake_rev_kref})
    sdk_mock.create_artifact = AsyncMock(return_value={"kref": fake_artifact_kref})

    with patch.object(
        ci, "_check_codex_available", AsyncMock(return_value={"ok": True})
    ), patch.object(ci, "_spawn_codex_image", side_effect=_fake_spawn), patch.object(
        ci, "_push_to_canvas", side_effect=_fake_canvas
    ), patch("operator_mcp.operator_mcp.KUMIHO_SDK", sdk_mock), patch.object(
        ci, "harness_project", lambda: "Construct"
    ):
        out = await ci.tool_generate_image_codex(
            {
                "prompt": "a fox in a forest",
                "output_path": str(target),
                "count": 1,
                "canvas": True,
                "register_artifact": True,
            },
            fake_gw,
        )

    assert out["generated"] == 1
    assert out["files"] == [str(target)]
    assert out["canvas"]["frame_id"] == "frame-1"
    assert out["artifact"]["item_kref"] == fake_item_kref
    assert out["artifact"]["revision_kref"] == fake_rev_kref
    assert fake_artifact_kref in out["artifact"]["artifact_krefs"]
    assert out["artifact"]["space_path"] == "Construct/Images"

    sdk_mock.create_item.assert_awaited_once()
    sdk_mock.create_revision.assert_awaited_once()
    sdk_mock.create_artifact.assert_awaited_once()


async def test_batch_mode_strips_n_suffix_for_item_name(fake_gw, tmp_path):
    """For count=3 with default pattern, the Kumiho item name is the bare stem."""
    target = tmp_path / "logo.png"

    async def _fake_spawn(prompt, output_path, cwd):
        output_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        return {"ok": True, "path": str(output_path), "size": 100}

    sdk_mock = MagicMock()
    sdk_mock.ensure_space = AsyncMock(return_value=None)
    sdk_mock.create_item = AsyncMock(return_value={"kref": "kref://Construct/Images/logo.image"})
    sdk_mock.create_revision = AsyncMock(return_value={"kref": "rev"})
    sdk_mock.create_artifact = AsyncMock(return_value={"kref": "art"})

    with patch.object(
        ci, "_check_codex_available", AsyncMock(return_value={"ok": True})
    ), patch.object(ci, "_spawn_codex_image", side_effect=_fake_spawn), patch(
        "operator_mcp.operator_mcp.KUMIHO_SDK", sdk_mock
    ), patch.object(ci, "harness_project", lambda: "Construct"):
        out = await ci.tool_generate_image_codex(
            {
                "prompt": "construct logo",
                "output_path": str(target),
                "count": 3,
                "canvas": False,
                "register_artifact": True,
            },
            fake_gw,
        )

    assert out["generated"] == 3
    assert [Path(p).name for p in out["files"]] == [
        "logo-1.png",
        "logo-2.png",
        "logo-3.png",
    ]
    # The item name passed to create_item should be the bare stem, not "logo-1".
    create_item_kwargs = sdk_mock.create_item.await_args.kwargs
    assert create_item_kwargs["name"] == "logo"
    assert create_item_kwargs["space_path"] == "Construct/Images"
    # All 3 PNGs are attached as artifacts to the same revision.
    assert sdk_mock.create_artifact.await_count == 3


async def test_register_artifact_false_skips_kumiho(fake_gw, tmp_path):
    target = tmp_path / "fox.png"

    async def _fake_spawn(prompt, output_path, cwd):
        output_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        return {"ok": True, "path": str(output_path), "size": 100}

    sdk_mock = MagicMock()
    sdk_mock.create_item = AsyncMock()

    with patch.object(
        ci, "_check_codex_available", AsyncMock(return_value={"ok": True})
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
    assert "artifact" not in out
    sdk_mock.create_item.assert_not_called()


async def test_custom_space_and_item_name_are_used_verbatim(fake_gw, tmp_path):
    """User-provided `space` and `item_name` override the defaults."""
    target = tmp_path / "fox.png"

    async def _fake_spawn(prompt, output_path, cwd):
        output_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        return {"ok": True, "path": str(output_path), "size": 100}

    sdk_mock = MagicMock()
    sdk_mock.ensure_space = AsyncMock(return_value=None)
    sdk_mock.create_item = AsyncMock(return_value={"kref": "i"})
    sdk_mock.create_revision = AsyncMock(return_value={"kref": "r"})
    sdk_mock.create_artifact = AsyncMock(return_value={"kref": "a"})

    with patch.object(
        ci, "_check_codex_available", AsyncMock(return_value={"ok": True})
    ), patch.object(ci, "_spawn_codex_image", side_effect=_fake_spawn), patch(
        "operator_mcp.operator_mcp.KUMIHO_SDK", sdk_mock
    ), patch.object(ci, "harness_project", lambda: "Construct"):
        out = await ci.tool_generate_image_codex(
            {
                "prompt": "construct quarterly logo",
                "output_path": str(target),
                "register_artifact": True,
                "space": "Marketing/Logos",
                "item_name": "q2-rebrand",
            },
            fake_gw,
        )

    assert out["artifact"]["space_path"] == "Construct/Marketing/Logos"
    create_item_kwargs = sdk_mock.create_item.await_args.kwargs
    assert create_item_kwargs["name"] == "q2-rebrand"
    assert create_item_kwargs["space_path"] == "Construct/Marketing/Logos"
    # ensure_space is called with the top segment of a multi-segment space.
    sdk_mock.ensure_space.assert_awaited_once_with("Construct", "Marketing")


async def test_space_default_is_images(fake_gw, tmp_path):
    """Omitting `space` falls back to `Images`; ensure_space sees that."""
    target = tmp_path / "fox.png"

    async def _fake_spawn(prompt, output_path, cwd):
        output_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        return {"ok": True, "path": str(output_path), "size": 100}

    sdk_mock = MagicMock()
    sdk_mock.ensure_space = AsyncMock(return_value=None)
    sdk_mock.create_item = AsyncMock(return_value={"kref": "i"})
    sdk_mock.create_revision = AsyncMock(return_value={"kref": "r"})
    sdk_mock.create_artifact = AsyncMock(return_value={"kref": "a"})

    with patch.object(
        ci, "_check_codex_available", AsyncMock(return_value={"ok": True})
    ), patch.object(ci, "_spawn_codex_image", side_effect=_fake_spawn), patch(
        "operator_mcp.operator_mcp.KUMIHO_SDK", sdk_mock
    ), patch.object(ci, "harness_project", lambda: "Construct"):
        out = await ci.tool_generate_image_codex(
            {
                "prompt": "fox",
                "output_path": str(target),
                "register_artifact": True,
            },
            fake_gw,
        )

    assert out["artifact"]["space_path"] == "Construct/Images"
    sdk_mock.ensure_space.assert_awaited_once_with("Construct", "Images")


async def test_partial_failure_reports_failures_and_keeps_successes(fake_gw, tmp_path):
    """If 1 of 2 codex spawns fails, the response includes both arrays."""
    target = tmp_path / "fox.png"

    async def _flaky_spawn(prompt, output_path, cwd):
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
        ci, "_check_codex_available", AsyncMock(return_value={"ok": True})
    ), patch.object(ci, "_spawn_codex_image", side_effect=_flaky_spawn), patch(
        "operator_mcp.operator_mcp.KUMIHO_SDK", sdk_mock
    ), patch.object(ci, "harness_project", lambda: "Construct"):
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
