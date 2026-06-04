"""Tests for the workflow `image:` step type.

The image step is a first-class wrapper around the
``generate_image_codex`` operator-MCP tool. Plain ``codex`` agent steps
in workflows don't have access to that tool — the subagent MCP server
intentionally excludes operator-tier tools to keep their surface area
small. So workflows that need a deterministic image artifact pipeline
must use this step type, not prose-prompt a codex agent.

These tests pin:
  - schema acceptance and step-level timeout propagation
  - validator rejects an image step with no prompt
  - executor interpolates the prompt against ${...} references
  - executor calls ``tool_generate_image_codex`` with the right args
  - executor maps the tool response (urls, files, item_kref, canvas) into
    StepResult.output_data the way downstream steps expect
  - executor surfaces tool failures (no PNGs produced) as failed status
  - executor enforces the per-step timeout
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from operator_mcp.workflow.executor import _dispatch_step, _exec_image
from operator_mcp.workflow.schema import (
    ImageStepConfig,
    StepDef,
    StepResult,
    StepType,
    WorkflowDef,
    WorkflowState,
)
from operator_mcp.workflow.validator import validate_workflow


def _state(inputs: dict | None = None) -> WorkflowState:
    return WorkflowState(
        workflow_name="test-wf",
        run_id="test-run",
        inputs=dict(inputs or {}),
    )


def _step(cfg: ImageStepConfig, step_id: str = "gen") -> StepDef:
    return StepDef(id=step_id, type=StepType.IMAGE, image=cfg)


# ── Schema ──────────────────────────────────────────────────────────


class TestSchema:
    def test_minimal_image_step_accepts_prompt_only(self):
        cfg = ImageStepConfig(prompt="a cat in a hat")
        assert cfg.prompt == "a cat in a hat"
        assert cfg.count == 1
        assert cfg.canvas is True
        assert cfg.register_artifact is True
        assert cfg.timeout == 1200.0

    def test_step_level_timeout_propagates_into_image_config(self):
        # Authors set `timeout: 60` at the step level; the validator
        # pushes that into the per-type config so the executor enforces
        # the right value without a fallback chain.
        s = StepDef(
            id="gen",
            type=StepType.IMAGE,
            image=ImageStepConfig(prompt="x"),
            timeout=60.0,
        )
        assert s.image.timeout == 60.0  # type: ignore[union-attr]

    def test_count_bounds(self):
        ImageStepConfig(prompt="x", count=1)
        ImageStepConfig(prompt="x", count=5)
        with pytest.raises(Exception):
            ImageStepConfig(prompt="x", count=0)
        with pytest.raises(Exception):
            ImageStepConfig(prompt="x", count=6)

    def test_sandbox_is_constrained(self):
        ImageStepConfig(prompt="x", sandbox="workspace-write")
        with pytest.raises(Exception):
            ImageStepConfig(prompt="x", sandbox="bogus")  # type: ignore[arg-type]

    def test_input_images_accepts_string_or_list(self):
        assert (
            ImageStepConfig(prompt="x", input_images="/ws/ref.png").input_images
            == "/ws/ref.png"
        )
        assert ImageStepConfig(
            prompt="x",
            input_images=["/ws/ref-a.png", "/ws/ref-b.png"],
        ).input_images == ["/ws/ref-a.png", "/ws/ref-b.png"]


# ── Validator ───────────────────────────────────────────────────────


class TestValidator:
    def test_rejects_image_step_with_empty_prompt(self):
        wf = WorkflowDef(
            name="bad",
            steps=[_step(ImageStepConfig(prompt=""))],
        )
        result = validate_workflow(wf)
        assert not result.valid
        assert any(
            "prompt" in e.message and e.step_id == "gen" for e in result.errors
        )

    def test_accepts_image_step_with_prompt(self):
        wf = WorkflowDef(
            name="ok",
            steps=[_step(ImageStepConfig(prompt="a logo"))],
        )
        result = validate_workflow(wf)
        assert result.valid, [e.message for e in result.errors]


# ── Executor ────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestExecutor:
    """The executor is the integration point. We mock
    `tool_generate_image_codex` so the test doesn't try to spawn codex,
    and inspect the args passed in plus the StepResult shape."""

    async def test_calls_tool_with_interpolated_prompt(self):
        cfg = ImageStepConfig(
            prompt="render: ${inputs.subject}",
            input_images=["${inputs.reference_image}"],
        )
        step = _step(cfg)
        state = _state(
            inputs={
                "subject": "Seoul Station 2040",
                "reference_image": "/ws/reference.png",
            }
        )

        captured: dict = {}

        async def fake_tool(args, gw):
            captured.update(args)
            return {
                "requested": 1,
                "generated": 1,
                "files": ["/ws/Revka/Images/gen.image/r1/gen.png"],
                "urls": ["/workspace/Revka/Images/gen.image/r1/gen.png?exp=1&sig=abc"],
                "canvas": {"canvas_id": "default", "frame_id": "frame-1"},
                "artifact": {
                    "item_kref": "kref://Revka/Images/gen.image",
                    "revision_kref": "kref://Revka/Images/gen.image?r=1",
                    "revision_number": 1,
                    "artifact_krefs": ["kref://Revka/Images/gen.image?r=1#0"],
                    "space_path": "Revka/Images",
                    "directory": "/ws/Revka/Images/gen.image/r1",
                },
            }

        with patch(
            "operator_mcp.tool_handlers.codex_image.tool_generate_image_codex",
            side_effect=fake_tool,
        ):
            result = await _exec_image(step, state, cwd="/ws")

        assert result.status == "completed"
        assert captured["prompt"] == "render: Seoul Station 2040"
        assert captured["output_path"] == "gen.png"
        assert captured["item_name"] == "gen"
        assert captured["count"] == 1
        assert captured["canvas"] is True
        assert captured["register_artifact"] is True
        assert captured["input_images"] == ["/ws/reference.png"]
        assert captured["cwd"] == "/ws"

    async def test_maps_response_into_output_data(self):
        cfg = ImageStepConfig(prompt="x")
        step = _step(cfg)

        async def fake_tool(args, gw):
            return {
                "requested": 2,
                "generated": 2,
                "files": ["/ws/a.png", "/ws/b.png"],
                "urls": ["/workspace/a.png?sig=1", "/workspace/b.png?sig=2"],
                "canvas": {"canvas_id": "default", "frame_id": "f-9"},
                "artifact": {
                    "item_kref": "kref://Revka/Images/x.image",
                    "revision_kref": "kref://Revka/Images/x.image?r=3",
                    "artifact_krefs": ["k0", "k1"],
                },
            }

        with patch(
            "operator_mcp.tool_handlers.codex_image.tool_generate_image_codex",
            side_effect=fake_tool,
        ):
            result = await _exec_image(step, _state(), cwd="")

        assert result.status == "completed"
        assert result.files_touched == ["/ws/a.png", "/ws/b.png"]
        assert result.output_data["urls"] == [
            "/workspace/a.png?sig=1",
            "/workspace/b.png?sig=2",
        ]
        assert result.output_data["item_kref"] == "kref://Revka/Images/x.image"
        assert result.output_data["canvas_frame_id"] == "f-9"
        # Downstream steps reference these via ${gen.output_data.urls} and
        # ${gen.output_data.item_kref}; pinning the keys keeps us honest
        # about the public shape.

    async def test_failure_when_no_pngs_produced(self):
        cfg = ImageStepConfig(prompt="x")
        step = _step(cfg)

        async def fake_tool(args, gw):
            return {
                "requested": 1,
                "generated": 0,
                "files": [],
                "error": "codex unavailable",
            }

        with patch(
            "operator_mcp.tool_handlers.codex_image.tool_generate_image_codex",
            side_effect=fake_tool,
        ):
            result = await _exec_image(step, _state(), cwd="")

        assert result.status == "failed"
        assert "codex unavailable" in result.error

    async def test_timeout_returns_failed_status(self):
        import asyncio

        cfg = ImageStepConfig(prompt="x", timeout=0.05)
        step = _step(cfg)

        async def slow_tool(args, gw):
            await asyncio.sleep(1.0)
            return {"generated": 1, "files": ["/x"]}

        with patch(
            "operator_mcp.tool_handlers.codex_image.tool_generate_image_codex",
            side_effect=slow_tool,
        ):
            result = await _exec_image(step, _state(), cwd="")

        assert result.status == "failed"
        assert "timed out" in result.error

    async def test_dispatch_routes_image_step(self):
        """Dispatch wiring is the real subject of this test — the wrong
        StepType branch would fall through to the unknown-type error."""
        cfg = ImageStepConfig(prompt="x")
        step = _step(cfg)
        wf = WorkflowDef(name="t", steps=[step])

        async def fake_tool(args, gw):
            return {
                "generated": 1,
                "files": ["/ws/x.png"],
                "urls": ["/workspace/x.png?sig=z"],
            }

        with patch(
            "operator_mcp.tool_handlers.codex_image.tool_generate_image_codex",
            side_effect=fake_tool,
        ):
            result = await _dispatch_step(step, _state(), cwd="/ws", wf=wf)

        assert isinstance(result, StepResult)
        assert result.status == "completed"
        assert result.files_touched == ["/ws/x.png"]
