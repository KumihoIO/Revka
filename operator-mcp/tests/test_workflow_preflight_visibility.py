"""Regression tests for workflow failures that happen before step execution."""
from __future__ import annotations

import pytest

from operator_mcp.workflow import executor, memory, recovery
from operator_mcp.workflow.schema import (
    ShellStepConfig,
    StepDef,
    StepType,
    WorkflowDef,
    WorkflowStatus,
)


@pytest.fixture(autouse=True)
def _isolate_executor_state(monkeypatch, tmp_path):
    lock_dir = tmp_path / "workflow_locks"
    ckpt_dir = tmp_path / "workflow_checkpoints"
    lock_dir.mkdir(parents=True, exist_ok=True)
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    executor.ACTIVE_WORKFLOWS.clear()
    monkeypatch.setattr(executor, "_CHECKPOINT_DIR", str(ckpt_dir))
    monkeypatch.setattr(recovery, "_RUN_LOCK_DIR", str(lock_dir))
    yield
    executor.ACTIVE_WORKFLOWS.clear()


def _shell_step(step_id: str, command: str = "echo ok") -> StepDef:
    return StepDef(
        id=step_id,
        type=StepType.SHELL,
        shell=ShellStepConfig(command=command, timeout=10),
    )


@pytest.mark.asyncio
async def test_cost_guard_preflight_failure_is_persisted(monkeypatch, tmp_path):
    persisted: list[dict] = []

    async def fake_check_cost_guard(_max_cost_usd=None):
        return "Budget exceeded: daily configured limit reached"

    async def fake_persist_workflow_run(**kwargs):
        persisted.append(kwargs)
        return "kref://Construct/WorkflowRuns/cost-blocked.workflow_run"

    monkeypatch.setattr(executor, "_check_cost_guard", fake_check_cost_guard)
    monkeypatch.setattr(memory, "persist_workflow_run", fake_persist_workflow_run)

    sentinel = tmp_path / "should-not-run"
    wf = WorkflowDef(
        name="cost-blocked",
        steps=[_shell_step("write", f"touch {sentinel}")],
        checkpoint=False,
    )

    state = await executor.execute_workflow(
        wf,
        inputs={"topic": "ux"},
        cwd=str(tmp_path),
        run_id="cost-preflight-run",
        workflow_item_kref="kref://Construct/Workflows/cost-blocked.workflow",
        workflow_revision_kref="kref://Construct/Workflows/cost-blocked.workflow?r=7",
    )

    assert state.status == WorkflowStatus.FAILED
    assert state.error.startswith("Cost guard: Budget exceeded")
    assert not sentinel.exists()
    assert len(persisted) == 1
    assert persisted[0]["run_id"] == "cost-preflight-run"
    assert persisted[0]["status"] == "failed"
    assert persisted[0]["error"].startswith("Cost guard: Budget exceeded")
    assert persisted[0]["steps_total"] == 1
    assert (
        persisted[0]["workflow_item_kref"]
        == "kref://Construct/Workflows/cost-blocked.workflow"
    )
    assert (
        persisted[0]["workflow_revision_kref"]
        == "kref://Construct/Workflows/cost-blocked.workflow?r=7"
    )


@pytest.mark.asyncio
async def test_validation_preflight_failure_is_persisted(monkeypatch, tmp_path):
    persisted: list[dict] = []

    async def fake_persist_workflow_run(**kwargs):
        persisted.append(kwargs)
        return "kref://Construct/WorkflowRuns/invalid.workflow_run"

    monkeypatch.setattr(memory, "persist_workflow_run", fake_persist_workflow_run)

    wf = WorkflowDef(
        name="invalid-preflight",
        steps=[
            StepDef(
                id="blocked",
                type=StepType.SHELL,
                depends_on=["missing"],
                shell=ShellStepConfig(command="echo should-not-run", timeout=10),
            )
        ],
        checkpoint=False,
    )

    state = await executor.execute_workflow(
        wf,
        inputs={},
        cwd=str(tmp_path),
        run_id="validation-preflight-run",
    )

    assert state.status == WorkflowStatus.FAILED
    assert state.error.startswith("Validation failed:")
    assert len(persisted) == 1
    assert persisted[0]["run_id"] == "validation-preflight-run"
    assert persisted[0]["status"] == "failed"
    assert persisted[0]["steps_total"] == 1
