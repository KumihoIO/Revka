"""Tests for workflow checkpoint cleanup (#394).

The bug: completed/cancelled workflow checkpoints accumulate in
``~/.revka/workflow_checkpoints/`` forever (one full ``WorkflowState`` dump per
run, holding interpolated step inputs/outputs). The fix makes the Kumiho DB the
authoritative terminal record — including CANCELLED, which finalize previously
omitted — so stale terminal checkpoints can be swept safely.

Covers:
  - finalize persists CANCELLED to the DB (A);
  - the resume-rejection cancel path persists CANCELLED (B);
  - ``mark_stale_runs`` skips terminal (cancelled) DB runs — the regression that
    sank PR #518 (deleting a cancelled checkpoint while the DB still said
    "running" got the run reclassified as failed);
  - ``sweep_terminal_checkpoints`` removes stale completed/cancelled checkpoints
    but keeps failed (retry), running/paused (resume), and recent terminal ones.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import pytest

import operator_mcp.workflow.executor as executor
from operator_mcp.workflow.schema import (
    HumanApprovalConfig,
    ShellStepConfig,
    StepDef,
    StepResult,
    StepType,
    WorkflowDef,
    WorkflowState,
    WorkflowStatus,
)


def _sleep_command(seconds: float) -> str:
    return f'"{sys.executable}" -c "import time; time.sleep({seconds})"'


# ── (A) finalize persists CANCELLED to the DB ────────────────────────


@pytest.mark.asyncio
async def test_finalize_persists_cancelled_to_db(tmp_path, monkeypatch):
    """A cancelled run must reach a terminal DB status so the startup stale
    scan does not reclassify it as failed (the root cause of #394 + PR #518)."""
    import operator_mcp.workflow.memory as memory

    calls: list[dict] = []

    async def fake_persist(**kwargs):
        calls.append(kwargs)
        return "kref-x"

    async def fake_link(*_a, **_k):
        return None

    monkeypatch.setattr(memory, "persist_workflow_run", fake_persist)
    monkeypatch.setattr(memory, "link_agents_to_run", fake_link)
    executor.ACTIVE_WORKFLOWS.clear()

    wf = WorkflowDef(
        name="cancel-persist",
        steps=[
            StepDef(
                id="s1",
                type=StepType.SHELL,
                shell=ShellStepConfig(command=_sleep_command(0.05), timeout=5),
            ),
            StepDef(
                id="s2",
                type=StepType.SHELL,
                depends_on=["s1"],
                shell=ShellStepConfig(command=_sleep_command(5), timeout=10),
            ),
        ],
        checkpoint=False,
    )

    async def trip():
        for _ in range(200):
            await asyncio.sleep(0.05)
            for st in list(executor.ACTIVE_WORKFLOWS.values()):
                if st.workflow_name == "cancel-persist" and "s1" in st.step_results:
                    st.cancel_requested = True
                    return

    asyncio.create_task(trip())
    final = await executor.execute_workflow(wf, inputs={}, cwd=str(tmp_path))

    assert final.status == WorkflowStatus.CANCELLED
    statuses = [c.get("status") for c in calls]
    assert "cancelled" in statuses, f"expected a cancelled persist; got {statuses}"
    cancelled = next(c for c in calls if c.get("status") == "cancelled")
    assert cancelled["run_id"] == final.run_id
    assert cancelled.get("completed_at")


# ── (#518 regression) mark_stale_runs skips terminal cancelled runs ──


@pytest.mark.asyncio
async def test_mark_stale_runs_skips_cancelled_but_marks_running(monkeypatch):
    """With CANCELLED now in the DB, the stale scan must skip it (terminal) and
    only fail genuinely orphaned running/paused runs. This is the exact scenario
    PR #518 broke: a cancelled run must never be reclassified as failed."""
    import operator_mcp.operator_mcp as op_mod
    import operator_mcp.workflow.memory as memory
    import kumiho.mcp_server as kms

    revisions = {
        "kref-cancelled": {"metadata": {"status": "cancelled", "run_id": "run-cancelled"}},
        "kref-running": {"metadata": {"status": "running", "run_id": "run-running"}},
    }
    created: list[tuple[str, str]] = []
    checkpoint_marks: list[str] = []

    class FakeSDK:
        _available = True

        async def list_items(self, _space):
            return [{"kref": "kref-cancelled"}, {"kref": "kref-running"}]

        async def create_revision(self, kref, meta, tag="latest"):
            created.append((kref, meta.get("status")))
            return kref

    monkeypatch.setattr(op_mod, "KUMIHO_SDK", FakeSDK(), raising=False)
    monkeypatch.setattr(
        kms, "tool_get_revision_by_tag", lambda kref, _tag: revisions[kref], raising=False
    )
    monkeypatch.setattr(
        memory,
        "_mark_checkpoint_failed",
        lambda run_id, *_a, **_k: checkpoint_marks.append(run_id) or True,
    )

    marked = await memory.mark_stale_runs()

    assert marked == 1, "only the orphaned running run should be marked stale"
    assert created == [("kref-running", "failed")], f"unexpected DB writes: {created}"
    assert checkpoint_marks == ["run-running"], f"unexpected checkpoint marks: {checkpoint_marks}"


# ── (B) resume-rejection cancel path persists CANCELLED ──────────────


@pytest.mark.asyncio
async def test_resume_rejection_persists_cancelled(monkeypatch):
    """Rejecting a human-approval pause with no on_reject_goto cancels the run
    WITHOUT re-entering the executor, so it must persist CANCELLED itself."""
    from operator_mcp.tool_handlers import workflows as wfmod
    import operator_mcp.workflow.loader as loader

    executor.ACTIVE_WORKFLOWS.clear()
    state = WorkflowState(
        workflow_name="approve-wf", run_id="reject-run", status=WorkflowStatus.PAUSED
    )
    state.step_results["gate"] = StepResult(
        step_id="gate", status="pending", output_data={"awaiting_approval": True}
    )
    executor.ACTIVE_WORKFLOWS["reject-run"] = state

    wf = WorkflowDef(
        name="approve-wf",
        steps=[StepDef(id="gate", type=StepType.HUMAN_APPROVAL, human_approval=HumanApprovalConfig())],
        checkpoint=False,
    )

    async def fake_resolve(_name, project_dir=None):
        return (wf, "", "")

    monkeypatch.setattr(loader, "resolve_workflow", fake_resolve)

    persisted: list[dict] = []

    async def fake_persist(**kwargs):
        persisted.append(kwargs)
        return "kref"

    monkeypatch.setattr("operator_mcp.workflow.memory.persist_workflow_run", fake_persist)

    res = await wfmod.tool_resume_workflow({"run_id": "reject-run", "approved": False})

    assert res["status"] == "cancelled"
    statuses = [c.get("status") for c in persisted]
    assert "cancelled" in statuses, f"reject path must persist cancelled; got {statuses}"
    assert "reject-run" not in executor.ACTIVE_WORKFLOWS


# ── (E) startup sweep of stale terminal checkpoints ──────────────────


def _write_checkpoint(ckpt_dir, run_id: str, status: str, age_days: float):
    path = os.path.join(ckpt_dir, f"{run_id}.json")
    with open(path, "w") as f:
        json.dump({"run_id": run_id, "status": status, "workflow_name": "w"}, f)
    old = time.time() - age_days * 86_400
    os.utime(path, (old, old))
    return path


def test_sweep_deletes_old_terminal_keeps_failed_running_and_recent(tmp_path, monkeypatch):
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    monkeypatch.setattr(executor, "_CHECKPOINT_DIR", str(ckpt))

    paths = {
        "old-completed": _write_checkpoint(str(ckpt), "old-completed", "completed", 30),
        "old-cancelled": _write_checkpoint(str(ckpt), "old-cancelled", "cancelled", 30),
        "old-failed": _write_checkpoint(str(ckpt), "old-failed", "failed", 30),
        "old-running": _write_checkpoint(str(ckpt), "old-running", "running", 30),
        "old-paused": _write_checkpoint(str(ckpt), "old-paused", "paused", 30),
        "recent-completed": _write_checkpoint(str(ckpt), "recent-completed", "completed", 1),
    }

    removed = executor.sweep_terminal_checkpoints(retention_days=7)

    assert removed == 2
    assert not os.path.exists(paths["old-completed"])
    assert not os.path.exists(paths["old-cancelled"])
    assert os.path.exists(paths["old-failed"]), "FAILED kept for tool_retry_workflow"
    assert os.path.exists(paths["old-running"]), "RUNNING kept for resume"
    assert os.path.exists(paths["old-paused"]), "PAUSED kept for resume"
    assert os.path.exists(paths["recent-completed"]), "recent terminal kept for status lookup"


def test_sweep_respects_retention_window_env(tmp_path, monkeypatch):
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    monkeypatch.setattr(executor, "_CHECKPOINT_DIR", str(ckpt))
    p = _write_checkpoint(str(ckpt), "two-day-old", "completed", 2)

    # Default window is 7d → a 2-day-old completed checkpoint is kept…
    monkeypatch.delenv("REVKA_WORKFLOW_CHECKPOINT_RETENTION_DAYS", raising=False)
    assert executor.sweep_terminal_checkpoints() == 0
    assert os.path.exists(p)

    # …but a 1-day window sweeps it.
    monkeypatch.setenv("REVKA_WORKFLOW_CHECKPOINT_RETENTION_DAYS", "1")
    assert executor.sweep_terminal_checkpoints() == 1
    assert not os.path.exists(p)


def test_sweep_handles_missing_dir_and_corrupt_files(tmp_path, monkeypatch):
    monkeypatch.setattr(executor, "_CHECKPOINT_DIR", str(tmp_path / "absent"))
    assert executor.sweep_terminal_checkpoints(retention_days=0) == 0

    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    monkeypatch.setattr(executor, "_CHECKPOINT_DIR", str(ckpt))
    bad = ckpt / "corrupt.json"
    bad.write_text("{ not json")
    old = time.time() - 30 * 86_400
    os.utime(bad, (old, old))

    assert executor.sweep_terminal_checkpoints(retention_days=7) == 0
    assert bad.exists(), "corrupt checkpoints are left for manual inspection"
