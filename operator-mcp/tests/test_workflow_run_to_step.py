"""Tests for the "run to here" feature — ``execute_workflow(target_step_id=...)``.

Covers:

  1. Linear chain ``a → b → c`` with target=``b`` runs ``a`` then ``b``,
     NOT ``c``.
  2. Parallel: ``wrapper{x, y} → join`` with target=``x`` runs ``wrapper``
     then ``x``, not ``y`` or ``join``.
  3. Diamond: ``a → b → d``, ``a → c → d``; target=``d`` runs all four.
  4. Target is the first step (no ancestors) → only the target runs.
  5. Unknown target step → ``tool_run_workflow`` returns ``unknown_target_step``
     classified validation error.
  6. ``goto`` whose target falls outside the closure logs and is skipped (no
     exception, downstream step still runs).

Each test uses ``shell`` steps that write a unique sentinel file so we can
assert which steps did and did NOT execute. Sentinels are ordered with a
counter file so we can also check execution ORDER for the linear-chain case.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from operator_mcp.tool_handlers.workflows import tool_run_workflow
from operator_mcp.workflow.executor import (
    ACTIVE_WORKFLOWS,
    compute_ancestor_closure,
    execute_workflow,
)
from operator_mcp.workflow.schema import (
    GotoStepConfig,
    JoinStrategy,
    ParallelStepConfig,
    ShellStepConfig,
    StepDef,
    StepType,
    WorkflowDef,
    WorkflowStatus,
)


def _shell_step(step_id: str, sentinel: str, depends_on: list[str] | None = None) -> StepDef:
    """Create a shell step that touches a sentinel file when it runs.

    The sentinel-file pattern is the simplest reliable signal: pytest's
    tmp_path is empty per-test, so any file present after run() must have
    been written by a step that actually executed.
    """
    return StepDef(
        id=step_id,
        type=StepType.SHELL,
        depends_on=depends_on or [],
        shell=ShellStepConfig(command=f"touch {sentinel}", timeout=10),
    )


@pytest.fixture(autouse=True)
def _clear_active_workflows():
    """Per-test isolation for the global ACTIVE_WORKFLOWS registry."""
    ACTIVE_WORKFLOWS.clear()
    yield
    ACTIVE_WORKFLOWS.clear()


# ── compute_ancestor_closure unit tests ──────────────────────────────


class TestAncestorClosure:
    def test_linear_chain_target_middle(self) -> None:
        wf = WorkflowDef(
            name="linear",
            steps=[
                _shell_step("a", "/tmp/a"),
                _shell_step("b", "/tmp/b", depends_on=["a"]),
                _shell_step("c", "/tmp/c", depends_on=["b"]),
            ],
            checkpoint=False,
        )
        assert compute_ancestor_closure(wf, "b") == {"a", "b"}

    def test_parallel_child_pulls_in_wrapper(self) -> None:
        wf = WorkflowDef(
            name="par",
            steps=[
                StepDef(
                    id="wrapper",
                    type=StepType.PARALLEL,
                    parallel=ParallelStepConfig(steps=["x", "y"], join=JoinStrategy.ALL),
                ),
                _shell_step("x", "/tmp/x"),
                _shell_step("y", "/tmp/y"),
                _shell_step("join", "/tmp/j", depends_on=["wrapper"]),
            ],
            checkpoint=False,
        )
        # Target x: closure = {wrapper, x}. Sibling y not included; join not
        # included. wrapper appears because x is implicitly a descendant.
        assert compute_ancestor_closure(wf, "x") == {"wrapper", "x"}

    def test_diamond_target_d(self) -> None:
        wf = WorkflowDef(
            name="diamond",
            steps=[
                _shell_step("a", "/tmp/a"),
                _shell_step("b", "/tmp/b", depends_on=["a"]),
                _shell_step("c", "/tmp/c", depends_on=["a"]),
                _shell_step("d", "/tmp/d", depends_on=["b", "c"]),
            ],
            checkpoint=False,
        )
        assert compute_ancestor_closure(wf, "d") == {"a", "b", "c", "d"}

    def test_unknown_target_returns_empty(self) -> None:
        wf = WorkflowDef(
            name="x",
            steps=[_shell_step("a", "/tmp/a")],
            checkpoint=False,
        )
        assert compute_ancestor_closure(wf, "nope") == set()

    def test_target_is_root_returns_just_target(self) -> None:
        wf = WorkflowDef(
            name="x",
            steps=[
                _shell_step("a", "/tmp/a"),
                _shell_step("b", "/tmp/b", depends_on=["a"]),
            ],
            checkpoint=False,
        )
        assert compute_ancestor_closure(wf, "a") == {"a"}


# ── End-to-end execution scenarios ───────────────────────────────────


class TestRunToStepExecution:
    @pytest.mark.asyncio
    async def test_linear_chain_runs_ancestors_and_target_only(self, tmp_path) -> None:
        """target=b → a runs, b runs, c does NOT run."""
        sentinel_a = tmp_path / "a.touched"
        sentinel_b = tmp_path / "b.touched"
        sentinel_c = tmp_path / "c.touched"
        wf = WorkflowDef(
            name="linear-chain",
            steps=[
                _shell_step("a", str(sentinel_a)),
                _shell_step("b", str(sentinel_b), depends_on=["a"]),
                _shell_step("c", str(sentinel_c), depends_on=["b"]),
            ],
            checkpoint=False,
        )

        state = await execute_workflow(
            wf, inputs={}, cwd=str(tmp_path), target_step_id="b"
        )

        assert state.status == WorkflowStatus.COMPLETED, state.error
        assert sentinel_a.exists(), "ancestor 'a' should have run"
        assert sentinel_b.exists(), "target 'b' should have run"
        assert not sentinel_c.exists(), "downstream 'c' must NOT run"
        # step_results should reflect only what ran
        assert state.step_results["a"].status == "completed"
        assert state.step_results["b"].status == "completed"
        assert "c" not in state.step_results, "c was outside closure — never dispatched"

    @pytest.mark.asyncio
    async def test_parallel_target_x_runs_only_wrapper_and_x(self, tmp_path) -> None:
        sentinel_x = tmp_path / "x.touched"
        sentinel_y = tmp_path / "y.touched"
        sentinel_j = tmp_path / "j.touched"
        wf = WorkflowDef(
            name="parallel-rt",
            steps=[
                StepDef(
                    id="wrapper",
                    type=StepType.PARALLEL,
                    parallel=ParallelStepConfig(
                        steps=["x", "y"], join=JoinStrategy.ALL
                    ),
                ),
                _shell_step("x", str(sentinel_x)),
                _shell_step("y", str(sentinel_y)),
                _shell_step("join", str(sentinel_j), depends_on=["wrapper"]),
            ],
            checkpoint=False,
        )

        state = await execute_workflow(
            wf, inputs={}, cwd=str(tmp_path), target_step_id="x"
        )

        assert state.status == WorkflowStatus.COMPLETED, state.error
        assert sentinel_x.exists(), "target 'x' should have run"
        assert not sentinel_y.exists(), "sibling 'y' must NOT run"
        assert not sentinel_j.exists(), "downstream 'join' must NOT run"
        # The wrapper itself ran (it's the parent of x). Closure also includes
        # wrapper + x.
        assert state.step_results["wrapper"].status == "completed"
        assert state.step_results["x"].status == "completed"
        assert "join" not in state.step_results

    @pytest.mark.asyncio
    async def test_diamond_target_d_runs_all_four(self, tmp_path) -> None:
        sentinel_a = tmp_path / "a.touched"
        sentinel_b = tmp_path / "b.touched"
        sentinel_c = tmp_path / "c.touched"
        sentinel_d = tmp_path / "d.touched"
        wf = WorkflowDef(
            name="diamond-rt",
            steps=[
                _shell_step("a", str(sentinel_a)),
                _shell_step("b", str(sentinel_b), depends_on=["a"]),
                _shell_step("c", str(sentinel_c), depends_on=["a"]),
                _shell_step("d", str(sentinel_d), depends_on=["b", "c"]),
            ],
            checkpoint=False,
        )

        state = await execute_workflow(
            wf, inputs={}, cwd=str(tmp_path), target_step_id="d"
        )

        assert state.status == WorkflowStatus.COMPLETED, state.error
        assert sentinel_a.exists()
        assert sentinel_b.exists()
        assert sentinel_c.exists()
        assert sentinel_d.exists()

    @pytest.mark.asyncio
    async def test_target_is_first_step_runs_only_target(self, tmp_path) -> None:
        sentinel_a = tmp_path / "a.touched"
        sentinel_b = tmp_path / "b.touched"
        wf = WorkflowDef(
            name="root-rt",
            steps=[
                _shell_step("a", str(sentinel_a)),
                _shell_step("b", str(sentinel_b), depends_on=["a"]),
            ],
            checkpoint=False,
        )

        state = await execute_workflow(
            wf, inputs={}, cwd=str(tmp_path), target_step_id="a"
        )

        assert state.status == WorkflowStatus.COMPLETED, state.error
        assert sentinel_a.exists()
        assert not sentinel_b.exists(), "downstream 'b' must NOT run"
        assert state.step_results["a"].status == "completed"
        assert "b" not in state.step_results

    @pytest.mark.asyncio
    async def test_unknown_target_returns_classified_validation_error(
        self, tmp_path, monkeypatch
    ) -> None:
        """The MCP-tool layer must reject unknown target step ids with a
        classified ``unknown_target_step`` error rather than letting the
        executor silently no-op."""
        # Build an inline workflow_def so tool_run_workflow can resolve it
        # without hitting the loader / Kumiho.
        wf_dict = {
            "name": "unknown-target-rt",
            "steps": [
                {"id": "a", "type": "shell", "shell": {"command": "echo a"}},
                {
                    "id": "b",
                    "type": "shell",
                    "depends_on": ["a"],
                    "shell": {"command": "echo b"},
                },
            ],
            "checkpoint": False,
        }
        result = await tool_run_workflow(
            {
                "workflow_def": wf_dict,
                "cwd": str(tmp_path),
                "run_id": str(uuid.uuid4()),
                "target_step_id": "does_not_exist",
            }
        )
        # classified_error returns a dict with {error, error_code,
        # error_category, retryable}. The keys are *prefixed* — see
        # operator_mcp.failure_classification.classified_error.
        assert "error" in result, f"expected classified error, got {result}"
        assert result.get("error_code") == "unknown_target_step", result
        assert result.get("error_category") == "validation_error", result

    @pytest.mark.asyncio
    async def test_goto_target_outside_closure_is_skipped(self, tmp_path) -> None:
        """A goto step whose target is outside the run_to closure must NOT
        re-trigger the loop (otherwise we'd re-run already-completed
        ancestors or jump into territory we never planned to execute).
        Downstream step inside the closure should still run."""
        # Layout:
        #   setup → loop_check (goto target=setup) → final
        # Target = final. closure = {setup, loop_check, final}.
        # goto.target = "setup" which IS in closure (since it's an ancestor).
        # So we need a more nuanced layout where the goto target is OUTSIDE
        # closure. Use: setup → branch (goto target=elsewhere) → final, with
        # a side-step "elsewhere" not depended on by final.
        sentinel_setup = tmp_path / "setup.touched"
        sentinel_final = tmp_path / "final.touched"
        sentinel_else = tmp_path / "elsewhere.touched"
        wf = WorkflowDef(
            name="goto-rt",
            steps=[
                _shell_step("setup", str(sentinel_setup)),
                _shell_step("elsewhere", str(sentinel_else)),
                StepDef(
                    id="branch",
                    type=StepType.GOTO,
                    depends_on=["setup"],
                    goto=GotoStepConfig(target="elsewhere", max_iterations=3),
                ),
                _shell_step("final", str(sentinel_final), depends_on=["branch"]),
            ],
            checkpoint=False,
        )

        state = await execute_workflow(
            wf, inputs={}, cwd=str(tmp_path), target_step_id="final"
        )

        # No exception, run completed cleanly.
        assert state.status == WorkflowStatus.COMPLETED, state.error
        assert sentinel_setup.exists()
        assert sentinel_final.exists(), "final must run after the goto no-op"
        # `elsewhere` is OUTSIDE closure — it should never have executed,
        # neither as a normal step nor via the goto jump.
        assert not sentinel_else.exists(), (
            "goto target outside closure must be skipped, not followed"
        )
        # branch itself ran (it's an ancestor of final via depends_on).
        assert state.step_results["branch"].status == "completed"
