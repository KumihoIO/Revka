"""Tests for workflow cancellation (cancel_requested signal + subprocess kill).

Covers all four parts of the cancel mechanism:

  1. ``tool_cancel_workflow`` MCP-tool semantics:
     - flips ``cancel_requested`` on a known active run
     - returns ``cancelled=False`` (not an error) for unknown run_ids — the
       gateway maps that to a 404
     - is idempotent across repeated calls and terminal-state runs

  2. Cooperative cancel inside the executor's main loop: setting
     ``cancel_requested`` mid-run causes the scheduler to break at the
     next step boundary and transition the run to ``CANCELLED``.

  3. Mid-step subprocess kill: a long-running shell step polls the cancel
     flag every 250ms and kills its subprocess promptly when fired.

  4. Shell-timeout orphan fix: the previous executor left subprocesses
     orphaned when ``asyncio.wait_for`` raised ``TimeoutError``. We now
     kill the proc — verify the PID is gone after the step returns.
"""
from __future__ import annotations

import asyncio
import os
import time

import pytest

from operator_mcp.tool_handlers.workflows import tool_cancel_workflow
from operator_mcp.workflow.executor import (
    ACTIVE_WORKFLOWS,
    _exec_shell,
    execute_workflow,
)
from operator_mcp.workflow.schema import (
    ShellStepConfig,
    StepDef,
    StepType,
    WorkflowDef,
    WorkflowState,
    WorkflowStatus,
)


def _state_for(run_id: str, status: WorkflowStatus = WorkflowStatus.RUNNING) -> WorkflowState:
    return WorkflowState(workflow_name="t", run_id=run_id, status=status)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, OSError):
        return False


# ── tool_cancel_workflow ────────────────────────────────────────────


class TestCancelTool:
    def setup_method(self) -> None:
        # Each test owns its own active-registry slot; clear stale entries.
        ACTIVE_WORKFLOWS.clear()

    @pytest.mark.asyncio
    async def test_unknown_run_returns_cancelled_false(self) -> None:
        """Unknown run_ids must NOT raise — gateway maps to a 404 by reading
        cancelled=false + reason=not_found_or_already_finished."""
        res = await tool_cancel_workflow({"run_id": "nope"})
        assert res["cancelled"] is False
        assert res["reason"] == "not_found_or_already_finished"

    @pytest.mark.asyncio
    async def test_active_run_flips_flag(self) -> None:
        state = _state_for("r1")
        ACTIVE_WORKFLOWS["r1"] = state
        assert state.cancel_requested is False
        res = await tool_cancel_workflow({"run_id": "r1"})
        assert res["cancelled"] is True
        assert res["run_id"] == "r1"
        assert state.cancel_requested is True

    @pytest.mark.asyncio
    async def test_idempotent_double_call(self) -> None:
        state = _state_for("r2")
        ACTIVE_WORKFLOWS["r2"] = state
        first = await tool_cancel_workflow({"run_id": "r2"})
        second = await tool_cancel_workflow({"run_id": "r2"})
        assert first["cancelled"] is True
        assert second["cancelled"] is True
        assert state.cancel_requested is True

    @pytest.mark.asyncio
    async def test_terminal_state_returns_cancelled_false(self) -> None:
        state = _state_for("r3", status=WorkflowStatus.COMPLETED)
        ACTIVE_WORKFLOWS["r3"] = state
        res = await tool_cancel_workflow({"run_id": "r3"})
        assert res["cancelled"] is False
        assert res["reason"] == "already_terminal"
        assert res["status"] == "completed"

    @pytest.mark.asyncio
    async def test_missing_run_id_validation_error(self) -> None:
        res = await tool_cancel_workflow({})
        # classified_error returns dict with 'error' key
        assert "error" in res or res.get("code") == "missing_run_id"


# ── cooperative cancel in executor's main loop ───────────────────────


class TestExecutorCooperativeCancel:
    @pytest.mark.asyncio
    async def test_cancel_during_run_terminates_at_step_boundary(
        self, tmp_path
    ) -> None:
        """Set cancel_requested while two long shell steps are running;
        executor should observe at next boundary and transition to CANCELLED."""
        wf = WorkflowDef(
            name="cancel-mid",
            steps=[
                StepDef(
                    id="s1",
                    type=StepType.SHELL,
                    shell=ShellStepConfig(command="sleep 0.05", timeout=5),
                ),
                StepDef(
                    id="s2",
                    type=StepType.SHELL,
                    depends_on=["s1"],
                    shell=ShellStepConfig(command="sleep 5", timeout=10),
                ),
            ],
            checkpoint=False,
        )

        async def trip_cancel_when_s1_done() -> None:
            # Wait until s2 starts, then cancel.
            for _ in range(200):
                await asyncio.sleep(0.05)
                for state in ACTIVE_WORKFLOWS.values():
                    if state.workflow_name == "cancel-mid" and "s1" in state.step_results:
                        state.cancel_requested = True
                        return

        asyncio.create_task(trip_cancel_when_s1_done())
        t0 = time.monotonic()
        final = await execute_workflow(wf, inputs={}, cwd=str(tmp_path))
        elapsed = time.monotonic() - t0

        assert final.status == WorkflowStatus.CANCELLED
        assert final.error
        # We should NOT have waited the full sleep 5 — cancel should land
        # quickly. Allow generous slack for CI; the point is "well under 5s".
        assert elapsed < 4.0, f"expected fast cancel, took {elapsed:.2f}s"


# ── _exec_shell mid-step kill + timeout-orphan fix ───────────────────


class TestShellSubprocessKill:
    @pytest.mark.asyncio
    async def test_cancel_kills_running_shell_within_a_second(
        self, tmp_path
    ) -> None:
        state = _state_for("rsh1")
        step = StepDef(
            id="long",
            type=StepType.SHELL,
            shell=ShellStepConfig(command="sleep 5", timeout=10),
        )

        async def trip_cancel() -> None:
            await asyncio.sleep(0.3)
            state.cancel_requested = True

        asyncio.create_task(trip_cancel())
        t0 = time.monotonic()
        result = await _exec_shell(step, state, str(tmp_path))
        elapsed = time.monotonic() - t0

        assert result.status == "failed"
        assert "Cancelled" in result.error
        assert elapsed < 2.0, f"shell cancel was sluggish: {elapsed:.2f}s"
        # No leaked tracking entries
        assert state.running_processes == []

    @pytest.mark.asyncio
    async def test_timeout_kills_subprocess_no_orphan(self, tmp_path) -> None:
        """The pre-fix executor returned immediately on TimeoutError without
        proc.kill(), leaving the subprocess running. Verify it's dead now."""
        state = _state_for("rsh2")
        # Unique sentinel so we can find the child PID via the parent's
        # proc handle. We snapshot proc.pid out of state.running_processes
        # the instant the subprocess starts, then assert the PID is gone
        # after _exec_shell returns.
        step = StepDef(
            id="t",
            type=StepType.SHELL,
            shell=ShellStepConfig(command="sleep 5", timeout=0.5),
        )

        captured_pid: dict[str, int] = {}

        async def snapshot_pid() -> None:
            for _ in range(100):
                await asyncio.sleep(0.02)
                if state.running_processes:
                    proc = state.running_processes[0]
                    captured_pid["pid"] = proc.pid
                    return

        asyncio.create_task(snapshot_pid())
        result = await _exec_shell(step, state, str(tmp_path))
        assert result.status == "failed"
        assert "timed out" in result.error
        # Proc untracked
        assert state.running_processes == []
        # Wait briefly for the OS to reap; then verify it's gone.
        pid = captured_pid.get("pid")
        assert pid is not None, "did not capture subprocess PID in time"
        for _ in range(50):
            if not _pid_alive(pid):
                break
            await asyncio.sleep(0.05)
        assert not _pid_alive(pid), f"orphan subprocess {pid} still alive"
