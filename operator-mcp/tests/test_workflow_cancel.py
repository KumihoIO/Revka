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
import sys
import time

import pytest

from operator_mcp.tool_handlers.workflows import tool_cancel_workflow
import operator_mcp.workflow.executor as executor
from operator_mcp.workflow.schema import (
    AgentStepConfig,
    ForEachStepConfig,
    PythonStepConfig,
    ShellStepConfig,
    StepDef,
    StepResult,
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


def _sleep_command(seconds: float) -> str:
    return f'"{sys.executable}" -c "import time; time.sleep({seconds})"'


@pytest.fixture(autouse=True)
def isolate_workflow_locks(tmp_path, monkeypatch):
    """Keep per-run lock files inside pytest's writable temp tree."""
    import operator_mcp.workflow.recovery as recovery

    monkeypatch.setattr(recovery, "_RUN_LOCK_DIR", str(tmp_path / "workflow_locks"))


# ── tool_cancel_workflow ────────────────────────────────────────────


class TestCancelTool:
    def setup_method(self) -> None:
        # Each test owns its own active-registry slot; clear stale entries.
        executor.ACTIVE_WORKFLOWS.clear()

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
        executor.ACTIVE_WORKFLOWS["r1"] = state
        assert state.cancel_requested is False
        res = await tool_cancel_workflow({"run_id": "r1"})
        assert res["cancelled"] is True
        assert res["run_id"] == "r1"
        assert state.cancel_requested is True

    @pytest.mark.asyncio
    async def test_idempotent_double_call(self) -> None:
        state = _state_for("r2")
        executor.ACTIVE_WORKFLOWS["r2"] = state
        first = await tool_cancel_workflow({"run_id": "r2"})
        second = await tool_cancel_workflow({"run_id": "r2"})
        assert first["cancelled"] is True
        assert second["cancelled"] is True
        assert state.cancel_requested is True

    @pytest.mark.asyncio
    async def test_terminal_state_returns_cancelled_false(self) -> None:
        state = _state_for("r3", status=WorkflowStatus.COMPLETED)
        executor.ACTIVE_WORKFLOWS["r3"] = state
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

        async def trip_cancel_when_s1_done() -> None:
            # Wait until s2 starts, then cancel.
            for _ in range(200):
                await asyncio.sleep(0.05)
                for state in executor.ACTIVE_WORKFLOWS.values():
                    if state.workflow_name == "cancel-mid" and "s1" in state.step_results:
                        state.cancel_requested = True
                        return

        asyncio.create_task(trip_cancel_when_s1_done())
        t0 = time.monotonic()
        final = await executor.execute_workflow(wf, inputs={}, cwd=str(tmp_path))
        elapsed = time.monotonic() - t0

        assert final.status == WorkflowStatus.CANCELLED
        assert final.error
        # We should NOT have waited the full sleep 5 — cancel should land
        # quickly. Allow generous slack for CI; the point is "well under 5s".
        assert elapsed < 4.0, f"expected fast cancel, took {elapsed:.2f}s"

    @pytest.mark.asyncio
    async def test_resume_state_still_obeys_run_lock(self, tmp_path, monkeypatch) -> None:
        """Retry/resume executions must not bypass duplicate-run locking."""
        import operator_mcp.workflow.recovery as recovery

        monkeypatch.setattr(recovery, "_acquire_run_lock", lambda _run_id: None)
        wf = WorkflowDef(
            name="locked-resume",
            steps=[
                StepDef(
                    id="s1",
                    type=StepType.SHELL,
                    shell=ShellStepConfig(command="true", timeout=5),
                ),
            ],
            checkpoint=False,
        )
        state = _state_for("locked-resume-run")

        final = await executor.execute_workflow(wf, inputs={}, cwd=str(tmp_path), resume_state=state)

        assert final.status == WorkflowStatus.CANCELLED
        assert final.error == "Duplicate execution prevented by run lock"


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
            shell=ShellStepConfig(command=_sleep_command(5), timeout=10),
        )

        async def trip_cancel() -> None:
            await asyncio.sleep(0.3)
            state.cancel_requested = True

        asyncio.create_task(trip_cancel())
        t0 = time.monotonic()
        result = await executor._exec_shell(step, state, str(tmp_path))
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
            shell=ShellStepConfig(command=_sleep_command(5), timeout=0.5),
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
        result = await executor._exec_shell(step, state, str(tmp_path))
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


# ── process-group kill (grandchildren cleanup) ─────────────────────────


class TestShellProcessGroupKill:
    @pytest.mark.asyncio
    async def test_cancel_kills_backgrounded_grandchild(self, tmp_path) -> None:
        """A shell command that backgrounds a child must have BOTH the parent
        and the grandchild killed on cancel. Pre-fix, ``proc.kill()`` only
        terminated the direct child and the backgrounded sleep was orphaned."""
        if os.name != "posix":
            pytest.skip("process-group semantics POSIX-only")

        state = _state_for("rpgkill")
        # Parent shell forks `sleep 60` to the background, prints the PID,
        # then sleeps too. After cancel both must be gone.
        sentinel = tmp_path / "child.pid"
        cmd = (
            f'sleep 60 & echo $! > {sentinel}; '
            f'sleep 60'
        )
        step = StepDef(
            id="bg",
            type=StepType.SHELL,
            shell=ShellStepConfig(command=cmd, timeout=30),
        )

        captured_parent_pid: dict[str, int] = {}

        async def snapshot_parent_pid() -> None:
            for _ in range(200):
                await asyncio.sleep(0.02)
                if state.running_processes:
                    captured_parent_pid["pid"] = state.running_processes[0].pid
                    return

        async def trip_cancel_after_child_written() -> None:
            # Wait until the shell has written the grandchild PID file.
            for _ in range(200):
                await asyncio.sleep(0.05)
                if sentinel.exists() and sentinel.read_text().strip():
                    state.cancel_requested = True
                    return
            state.cancel_requested = True  # fallback

        asyncio.create_task(snapshot_parent_pid())
        asyncio.create_task(trip_cancel_after_child_written())
        result = await executor._exec_shell(step, state, str(tmp_path))
        assert result.status == "failed"
        assert "Cancelled" in result.error

        parent_pid = captured_parent_pid.get("pid")
        assert parent_pid is not None, "did not capture parent shell PID"
        assert sentinel.exists(), "shell did not write child PID sentinel"
        child_pid = int(sentinel.read_text().strip())

        # Wait briefly for the OS to reap both processes.
        for _ in range(50):
            if not _pid_alive(parent_pid) and not _pid_alive(child_pid):
                break
            await asyncio.sleep(0.05)

        # Verify BOTH are dead via os.kill(pid, 0) raising ProcessLookupError.
        for pid, label in ((parent_pid, "parent"), (child_pid, "grandchild")):
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)


# ── for_each cancel between iterations ──────────────────────────────────


class TestForEachCancelBetweenIterations:
    @pytest.mark.asyncio
    async def test_cancel_breaks_for_each_early(self, tmp_path) -> None:
        """A for_each with 5 iterations of a fast shell sleep should observe
        cancel between iterations, end early with iterations_completed
        reflecting the partial progress, and the workflow should be CANCELLED."""
        sub = StepDef(
            id="tick",
            type=StepType.SHELL,
            shell=ShellStepConfig(command=_sleep_command(0.5), timeout=5),
        )
        loop = StepDef(
            id="loop",
            type=StepType.FOR_EACH,
            for_each=ForEachStepConfig(
                range="1..5",
                variable="i",
                steps=["tick"],
                fail_fast=True,
            ),
        )
        wf = WorkflowDef(name="for-each-cancel", steps=[loop, sub], checkpoint=False)

        async def trip_cancel_after_two_iters() -> None:
            for _ in range(400):
                await asyncio.sleep(0.05)
                for s in executor.ACTIVE_WORKFLOWS.values():
                    if s.workflow_name != "for-each-cancel":
                        continue
                    done = sum(
                        1 for k in s.step_results
                        if k.startswith("tick__iter_")
                        and s.step_results[k].status == "completed"
                    )
                    if done >= 2:
                        s.cancel_requested = True
                        return

        asyncio.create_task(trip_cancel_after_two_iters())
        final = await executor.execute_workflow(wf, inputs={}, cwd=str(tmp_path))

        assert final.status == WorkflowStatus.CANCELLED
        loop_result = final.step_results.get("loop")
        assert loop_result is not None, "for_each step result missing"
        completed = loop_result.output_data.get("iterations_completed")
        assert completed is not None
        # Allow ±1 slack for scheduler timing.
        assert 1 <= completed <= 3, f"unexpected iterations_completed={completed}"
        assert loop_result.output_data.get("cancelled_after_iteration") == completed


def test_recovery_module_only_exposes_run_lock_helpers() -> None:
    """Interrupted workflow runs are not auto-resumed on operator startup.

    The recovery module is intentionally limited to lock helpers used by the
    executor; stale runs are failed on startup and retried only by user action.
    """
    import operator_mcp.workflow.recovery as recovery

    assert not hasattr(recovery, "recover_interrupted_runs")
    assert hasattr(recovery, "_acquire_run_lock")
    assert hasattr(recovery, "_release_run_lock")


def test_mark_stale_checkpoint_preserves_retry_state(tmp_path, monkeypatch) -> None:
    """Startup stale marking keeps checkpoints loadable for explicit Retry."""
    from operator_mcp.workflow.memory import _mark_checkpoint_failed

    home = tmp_path / "home"
    checkpoint_dir = home / ".revka" / "workflow_checkpoints"
    checkpoint_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(executor, "_CHECKPOINT_DIR", str(checkpoint_dir))

    state = _state_for("retry-after-stale", status=WorkflowStatus.RUNNING)
    state.step_results["done"] = StepResult(
        step_id="done",
        status="completed",
        output="ok",
    )
    executor._save_checkpoint(state)

    assert _mark_checkpoint_failed("retry-after-stale", "interrupted", "2026-05-10T00:00:00Z")
    loaded = executor.load_checkpoint("retry-after-stale")

    assert loaded is not None
    assert loaded.status == WorkflowStatus.FAILED
    assert loaded.error == "interrupted"
    assert loaded.step_results["done"].status == "completed"


def test_workflow_progress_snapshot_splits_loop_instances() -> None:
    state = _state_for("loop-progress")
    state.steps_total = 2
    state.current_step = "child"
    state.inputs["__for_each__"] = {
        "loop_id": "episode-loop",
        "iteration": 3,
        "total": 5,
    }
    state.step_results["episode-loop"] = StepResult(
        step_id="episode-loop",
        status="completed",
        output="loop wrapper",
    )
    state.step_results["child"] = StepResult(
        step_id="child",
        status="completed",
        output="top-level child",
    )
    state.step_results["child__iter_1"] = StepResult(
        step_id="child__iter_1",
        status="completed",
        output="one",
    )
    state.step_results["child__iter_2"] = StepResult(
        step_id="child__iter_2",
        status="completed",
        output="two",
    )

    progress = executor.workflow_progress_snapshot(state)

    assert progress["top_level_steps_completed"] == 2
    assert progress["top_level_steps_total"] == 2
    assert progress["expanded_steps_completed"] == 4
    assert progress["current_loop"] == "episode-loop"
    assert progress["current_iteration"] == 3
    assert progress["current_loop_total"] == 5
    assert progress["current_step_instance"] == "child__iter_3"


@pytest.mark.asyncio
async def test_wait_for_agent_initializing_timeout_is_bounded(tmp_path, monkeypatch) -> None:
    import operator_mcp.patterns.refinement as refinement
    import operator_mcp.tool_handlers.agents as agents
    from operator_mcp.agent_state import AGENTS, ManagedAgent

    class StuckInitializingSidecar:
        async def get_agent(self, _agent_id):
            return {"status": "initializing"}

        async def get_events(self, _agent_id, since=0):
            return [{"type": "status_changed", "status": "initializing"}]

        async def interrupt_agent(self, _agent_id):
            return {"ok": True}

        async def close_agent(self, _agent_id):
            return {"closed": True}

    monkeypatch.setenv("REVKA_AGENT_INITIALIZING_TIMEOUT_SECS", "0.1")
    monkeypatch.setattr(agents, "_sidecar_client", StuckInitializingSidecar())
    monkeypatch.setattr(agents, "_event_consumer", None)

    agent = ManagedAgent(
        id="initializing-agent",
        agent_type="codex",
        title="Stuck",
        cwd=str(tmp_path),
        status="running",
    )
    agent._sidecar_id = "sidecar-initializing-agent"
    AGENTS[agent.id] = agent

    try:
        output = await refinement._wait_for_agent(agent, timeout=0.3)
    finally:
        AGENTS.pop(agent.id, None)

    assert "INITIALIZATION TIMEOUT" in output
    assert agent.status == "error"


@pytest.mark.asyncio
async def test_wait_for_agent_prompt_only_runlog_times_out(tmp_path, monkeypatch) -> None:
    import operator_mcp.patterns.refinement as refinement
    import operator_mcp.tool_handlers.agents as agents
    from operator_mcp.agent_state import AGENTS, ManagedAgent

    class RunningSidecar:
        async def get_agent(self, _agent_id):
            return {"status": "running"}

        async def get_events(self, _agent_id, since=0):
            return []

        async def interrupt_agent(self, _agent_id):
            return {"ok": True}

        async def close_agent(self, _agent_id):
            return {"closed": True}

    class PromptOnlyLog:
        def __init__(self):
            self.lifecycle_errors: list[dict] = []

        def get_summary(self):
            return {
                "total_events": 2,
                "last_message": "",
                "tool_call_count": 0,
                "error_count": 0,
            }

        def record_lifecycle_error(self, message, **kwargs):
            self.lifecycle_errors.append({"message": message, **kwargs})

    fake_log = PromptOnlyLog()
    monkeypatch.setenv("REVKA_AGENT_INITIALIZING_TIMEOUT_SECS", "0.1")
    monkeypatch.setattr(agents, "_sidecar_client", RunningSidecar())
    monkeypatch.setattr(agents, "_event_consumer", None)
    monkeypatch.setattr(refinement, "get_log", lambda _agent_id: fake_log)

    agent = ManagedAgent(
        id="prompt-only-agent",
        agent_type="codex",
        title="Prompt only",
        cwd=str(tmp_path),
        status="running",
    )
    agent._sidecar_id = "sidecar-prompt-only-agent"
    AGENTS[agent.id] = agent

    try:
        output = await refinement._wait_for_agent(agent, timeout=0.3)
    finally:
        AGENTS.pop(agent.id, None)

    assert "INITIALIZATION TIMEOUT" in output
    assert agent.status == "error"
    assert fake_log.lifecycle_errors
    assert fake_log.lifecycle_errors[-1]["code"] == "agent_initialization_timeout"


@pytest.mark.asyncio
async def test_wait_for_agent_dead_health_fails_running_agent(tmp_path, monkeypatch) -> None:
    import operator_mcp.patterns.refinement as refinement
    import operator_mcp.tool_handlers.agents as agents
    from operator_mcp.agent_state import AGENTS, ManagedAgent

    class RunningSidecar:
        async def get_agent(self, _agent_id):
            return {"status": "running"}

        async def interrupt_agent(self, _agent_id):
            return {"ok": True}

        async def close_agent(self, _agent_id):
            return {"closed": True}

    class DeadMonitor:
        def get_health(self, _agent_id):
            # stale_seconds must exceed the caller's min_dead_seconds
            # (max(300, 40% of step timeout)) for the verdict to be trusted.
            return {
                "health": "dead",
                "status": "running",
                "alive": False,
                "stale_seconds": 9_999.0,
            }

    class FakeLog:
        def get_summary(self):
            return {}

        def record_lifecycle_error(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(agents, "_sidecar_client", RunningSidecar())
    monkeypatch.setattr(agents, "_event_consumer", None)
    monkeypatch.setattr(refinement, "get_log", lambda _agent_id: FakeLog())
    monkeypatch.setattr(
        "operator_mcp.heartbeat.get_heartbeat_monitor",
        lambda: DeadMonitor(),
    )

    agent = ManagedAgent(
        id="dead-health-agent",
        agent_type="codex",
        title="Dead",
        cwd=str(tmp_path),
        status="running",
    )
    agent._sidecar_id = "sidecar-dead-health-agent"
    AGENTS[agent.id] = agent

    try:
        output = await refinement._wait_for_agent(agent, timeout=1.0)
    finally:
        AGENTS.pop(agent.id, None)

    assert "DEAD AGENT" in output
    assert agent.status == "error"


@pytest.mark.asyncio
async def test_wait_for_agent_ignores_dead_health_below_min_silence(tmp_path, monkeypatch) -> None:
    """A dead-while-running verdict with less silence than max(300s, 40% of
    the step timeout) must be ignored — print-mode agents (agy) emit nothing
    for the whole task and were being executed at the monitor's global 300s
    threshold despite a much larger step budget."""
    import operator_mcp.patterns.refinement as refinement
    import operator_mcp.tool_handlers.agents as agents
    from operator_mcp.agent_state import AGENTS, ManagedAgent

    class EventuallyIdleSidecar:
        """Reports running for two polls, then idle — exits the wait loop."""

        def __init__(self) -> None:
            self.polls = 0

        async def get_agent(self, _agent_id):
            self.polls += 1
            return {"status": "running" if self.polls <= 2 else "idle"}

        async def interrupt_agent(self, _agent_id):
            return {"ok": True}

        async def close_agent(self, _agent_id):
            return {"closed": True}

    class QuietButWorkingMonitor:
        def get_health(self, _agent_id):
            # Classified dead by the monitor's global 300s threshold, but
            # silent for far less than this step's min window (960s).
            return {
                "health": "dead",
                "status": "running",
                "alive": False,
                "stale_seconds": 301.0,
            }

    class FakeLog:
        def get_summary(self):
            return {}

        def get_last_message(self):
            return "done"

        def record_lifecycle_error(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(agents, "_sidecar_client", EventuallyIdleSidecar())
    monkeypatch.setattr(agents, "_event_consumer", None)
    monkeypatch.setattr(refinement, "get_log", lambda _agent_id: FakeLog())
    monkeypatch.setattr(
        "operator_mcp.heartbeat.get_heartbeat_monitor",
        lambda: QuietButWorkingMonitor(),
    )

    agent = ManagedAgent(
        id="quiet-agy-agent",
        agent_type="agy",
        title="Quiet",
        cwd=str(tmp_path),
        status="running",
    )
    agent._sidecar_id = "sidecar-quiet-agy-agent"
    AGENTS[agent.id] = agent

    try:
        # Step budget 2400s → min silence max(300, 0.4*2400) = 960s; the
        # 301s-stale dead verdict must be ignored and the agent allowed to
        # finish (sidecar goes idle on the third poll).
        output = await refinement._wait_for_agent(agent, timeout=2400.0)
    finally:
        AGENTS.pop(agent.id, None)

    assert "DEAD AGENT" not in output
    assert agent.status == "completed"


@pytest.mark.asyncio
async def test_wait_for_subprocess_agent_prompt_only_timeout_is_bounded(tmp_path, monkeypatch) -> None:
    import operator_mcp.patterns.refinement as refinement
    from operator_mcp.agent_state import AGENTS, ManagedAgent

    monkeypatch.setenv("REVKA_AGENT_INITIALIZING_TIMEOUT_SECS", "0.1")

    cancelled: dict[str, bool] = {}

    async def fake_cancel(agent):
        cancelled["called"] = True
        if agent._reader_task:
            agent._reader_task.cancel()

    monkeypatch.setattr(refinement, "_cancel_timed_out_agent", fake_cancel)

    agent = ManagedAgent(
        id="prompt-only-subprocess-agent",
        agent_type="codex",
        title="Stuck subprocess",
        cwd=str(tmp_path),
        status="running",
    )
    agent._reader_task = asyncio.create_task(asyncio.sleep(10))
    AGENTS[agent.id] = agent

    try:
        output = await refinement._wait_for_agent(agent, timeout=0.3)
    finally:
        if agent._reader_task and not agent._reader_task.done():
            agent._reader_task.cancel()
        AGENTS.pop(agent.id, None)

    assert "INITIALIZATION TIMEOUT" in output
    assert cancelled["called"] is True
    assert agent.status == "error"


@pytest.mark.asyncio
async def test_agent_retry_cleans_failed_child_agent(monkeypatch, tmp_path) -> None:
    from operator_mcp.agent_state import AGENTS, ManagedAgent

    attempts = {"count": 0}
    bad_agent = ManagedAgent(
        id="failed-child-agent",
        agent_type="codex",
        title="Failed child",
        cwd=str(tmp_path),
        status="error",
    )
    AGENTS[bad_agent.id] = bad_agent

    async def fake_dispatch(step, state, cwd, wf):
        attempts["count"] += 1
        if attempts["count"] == 1:
            return StepResult(
                step_id=step.id,
                status="failed",
                error="Agent died during initialization",
                output_data={"managed_agent_id": bad_agent.id},
            )
        return StepResult(step_id=step.id, status="completed", output="ok")

    monkeypatch.setattr(executor, "_dispatch_step", fake_dispatch)

    step = StepDef(
        id="agent-step",
        type=StepType.AGENT,
        retry=1,
        retry_delay=0,
        agent=AgentStepConfig(agent_type="codex", tools="none", prompt="Do it."),
    )
    state = WorkflowState(workflow_name="retry-cleanup", run_id="retry-cleanup-run")
    wf = WorkflowDef(name="retry-cleanup", steps=[step], checkpoint=False)

    result = await executor._execute_step_with_retry(step, state, str(tmp_path), wf)

    assert result.status == "completed"
    assert attempts["count"] == 2
    assert bad_agent.id not in AGENTS


@pytest.mark.asyncio
async def test_agent_step_cancel_does_not_retry(monkeypatch, tmp_path) -> None:
    attempts = {"count": 0}
    cleanup = {"called": False}
    state = WorkflowState(workflow_name="cancel-no-retry", run_id="cancel-no-retry-run")

    async def fake_dispatch(step, dispatch_state, cwd, wf):
        attempts["count"] += 1
        dispatch_state.cancel_requested = True
        return StepResult(
            step_id=step.id,
            status="failed",
            error="Cancelled by user",
            output_data={"managed_agent_id": "agent-to-stop"},
        )

    async def fake_cleanup(result):
        cleanup["called"] = True

    monkeypatch.setattr(executor, "_dispatch_step", fake_dispatch)
    monkeypatch.setattr(executor, "_cleanup_failed_agent_attempt", fake_cleanup)

    step = StepDef(
        id="agent-step",
        type=StepType.AGENT,
        retry=3,
        retry_delay=0,
        agent=AgentStepConfig(agent_type="codex", tools="none", prompt="Do it."),
    )
    wf = WorkflowDef(name="cancel-no-retry", steps=[step], checkpoint=False)

    result = await executor._execute_step_with_retry(step, state, str(tmp_path), wf)

    assert result.status == "failed"
    assert result.error == "Cancelled by user"
    assert attempts["count"] == 1
    assert cleanup["called"] is True


@pytest.mark.asyncio
async def test_wait_for_agent_cancel_check_cancels_child(tmp_path, monkeypatch) -> None:
    import operator_mcp.patterns.refinement as refinement
    import operator_mcp.tool_handlers.agents as agents
    from operator_mcp.agent_state import AGENTS, ManagedAgent

    cancelled: dict[str, bool] = {}

    async def fake_cancel(agent):
        cancelled["called"] = True

    class FakeSidecar:
        async def get_agent(self, _agent_id):
            return {"status": "running"}

    monkeypatch.setattr(refinement, "_cancel_timed_out_agent", fake_cancel)
    monkeypatch.setattr(agents, "_sidecar_client", FakeSidecar())

    agent = ManagedAgent(
        id="cancel-check-agent",
        agent_type="codex",
        title="Cancel check",
        cwd=str(tmp_path),
        status="running",
    )
    agent._sidecar_id = "cancel-check-sidecar"
    AGENTS[agent.id] = agent

    try:
        output = await refinement._wait_for_agent(
            agent,
            timeout=5.0,
            cancel_check=lambda: True,
        )
    finally:
        AGENTS.pop(agent.id, None)

    assert output == "[CANCELLED]"
    assert cancelled["called"] is True
    assert agent.status == "cancelled"


@pytest.mark.asyncio
async def test_refinement_subprocess_fallback_injects_workflow_memory_alias(tmp_path, monkeypatch) -> None:
    import operator_mcp.patterns.refinement as refinement
    import operator_mcp.tool_handlers.agents as agents
    from operator_mcp.agent_state import AGENTS

    captured: dict[str, object] = {}

    async def fake_try_sidecar_create(*args, **kwargs):
        return None

    async def fake_spawn(agent, prompt, journal, **kwargs):
        captured["prompt"] = prompt
        captured["mcp_servers"] = kwargs.get("mcp_servers")
        agent.status = "running"
        agent.stdout_buffer = "ok"
        done = asyncio.get_running_loop().create_future()
        done.set_result(None)
        agent._reader_task = done

    async def fake_wait(agent, *, timeout, cancel_check=None):
        return "ok"

    monkeypatch.setattr(agents, "_try_sidecar_create", fake_try_sidecar_create)
    monkeypatch.setattr(refinement, "spawn_agent", fake_spawn)
    monkeypatch.setattr(refinement, "_wait_for_agent", fake_wait)
    monkeypatch.setattr("operator_mcp.skill_loader.load_skills_for_pattern", lambda _pattern: "")

    agent, output = await refinement._spawn_and_wait(
        "codex",
        "workflow-step",
        str(tmp_path),
        "capture the result",
        timeout=0.1,
        include_memory=True,
        include_operator=False,
    )

    try:
        assert output == "ok"
        servers = captured["mcp_servers"]
        assert isinstance(servers, dict)
        assert "workflow-memory" in servers
        assert "capture_skill" in str(captured["prompt"])
        assert "tag_revision" in str(captured["prompt"])
    finally:
        AGENTS.pop(agent.id, None)
