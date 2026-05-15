"""Tests for the workflow compute step and explicit expression syntax."""
from __future__ import annotations

import pytest

from operator_mcp.workflow.executor import _exec_compute, interpolate
from operator_mcp.workflow.loader import load_workflow_from_yaml
from operator_mcp.workflow.schema import (
    ComputeStepConfig,
    StepDef,
    StepResult,
    StepType,
    WorkflowState,
)
from operator_mcp.workflow.validator import validate_workflow


@pytest.mark.asyncio
async def test_compute_outputs_are_typed_and_can_reference_prior_outputs() -> None:
    state = WorkflowState(
        workflow_name="wf",
        run_id="run-1",
        inputs={"volume": "1", "end": "6"},
        step_results={
            "arc-loader": StepResult(
                step_id="arc-loader",
                status="completed",
                output_data={"metadata": {"arc_number": "1", "end": "6"}},
            ),
        },
    )
    step = StepDef(
        id="next-arc-context",
        type=StepType.COMPUTE,
        compute=ComputeStepConfig(
            outputs={
                "arc_number": "${{ int(arc_loader.output_data.metadata.arc_number) + 1 }}",
                "start": "${{ int(arc_loader.output_data.metadata.end) + 1 }}",
                "end": "${{ outputs.start + 5 }}",
                "episode_range": "${{ outputs.start }}..${{ outputs.end }}",
                "entity_name": "cc-vol-${inputs.volume}-arc-${{ outputs.arc_number }}",
            }
        ),
    )

    result = await _exec_compute(step, state)

    assert result.status == "completed"
    assert result.output_data["arc_number"] == 2
    assert result.output_data["start"] == 7
    assert result.output_data["end"] == 12
    assert result.output_data["episode_range"] == "7..12"
    assert result.output_data["entity_name"] == "cc-vol-1-arc-2"


def test_explicit_expression_placeholders_interpolate_without_changing_legacy_syntax() -> None:
    state = WorkflowState(
        workflow_name="wf",
        run_id="run-1",
        inputs={"end": "6"},
    )

    assert interpolate("next=${{ int(inputs.end) + 1 }}", state) == "next=7"
    assert interpolate("legacy=${inputs.end}", state) == "legacy=6"


@pytest.mark.asyncio
async def test_compute_failure_returns_partial_outputs() -> None:
    state = WorkflowState(workflow_name="wf", run_id="run-1")
    step = StepDef(
        id="bad-compute",
        type=StepType.COMPUTE,
        compute=ComputeStepConfig(
            outputs={
                "ok": "${{ 1 + 1 }}",
                "bad": "${{ missing.output }}",
            }
        ),
    )

    result = await _exec_compute(step, state)

    assert result.status == "failed"
    assert result.output_data == {"ok": 2}
    assert "compute failed" in result.error


def test_loader_infers_compute_expression_dependencies(tmp_path) -> None:
    path = tmp_path / "compute.yaml"
    path.write_text(
        """
name: compute-deps
steps:
  - id: arc-loader
    type: python
    python:
      code: |
        import json, sys
        json.dump({"end": 6}, sys.stdout)
  - id: next-arc-context
    type: compute
    compute:
      outputs:
        start: "${{ int(arc_loader.output_data.end) + 1 }}"
""",
        encoding="utf-8",
    )

    wf = load_workflow_from_yaml(str(path))
    result = validate_workflow(wf)
    compute = wf.step_by_id("next-arc-context")

    assert result.valid, result.errors
    assert compute is not None
    assert compute.depends_on == ["arc-loader"]
