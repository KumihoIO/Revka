"""Tests for the "run to here" feature — ``execute_workflow(target_step_id=...)``.

Covers:

  1. Linear chain ``a → b → c`` with target=``b`` runs ``a`` then ``b``,
     NOT ``c``.
  2. Parallel: ``wrapper{x, y} → join`` with target=``x`` runs ``wrapper``
     then ``x``, not ``y`` or ``join``.
  3. Parallel-DOWNSTREAM: target downstream of a parallel runs ALL children
     plus the target (no false-green from filtered-empty children).
  4. for_each-INSIDE: target inside a for_each body pulls in the wrapper
     and runs the target, nothing else.
  5. Diamond: ``a → b → d``, ``a → c → d``; target=``d`` runs all four.
  6. Target is the first step (no ancestors) → only the target runs.
  7. Unknown target step → ``tool_run_workflow`` returns
     ``unknown_target_step`` classified validation error AND
     ``execute_workflow`` directly returns FAILED with that error message
     (defence in depth — the gateway/poller path bypasses the tool).
  8. ``goto`` whose target falls outside the closure logs and is skipped
     (no exception, downstream step still runs).
  9. Recovery: a paused run-to-here resumed from checkpoint honours the
     persisted ``target_step_id`` (closure is re-derived).
 10. Schema: duplicate step ids are rejected at parse time.

Each test uses ``shell`` steps that write a unique sentinel file so we can
assert which steps did and did NOT execute. Hermetic fixtures monkeypatch
the run-lock and checkpoint dirs into ``tmp_path`` so global state under
``~/.construct`` never bleeds into a test run.
"""
from __future__ import annotations

import os
import uuid

import pytest

from operator_mcp.tool_handlers.workflows import tool_run_workflow
from operator_mcp.workflow.executor import (
    ACTIVE_WORKFLOWS,
    compute_ancestor_closure,
    execute_workflow,
)
from operator_mcp.workflow.schema import (
    ForEachStepConfig,
    GotoStepConfig,
    JoinStrategy,
    ParallelStepConfig,
    ShellStepConfig,
    StepDef,
    StepResult,
    StepType,
    WorkflowDef,
    WorkflowState,
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


@pytest.fixture(autouse=True)
def _isolate_workflow_state(monkeypatch, tmp_path):
    """Hermetic file-state fixtures for every test in this module.

    The executor and recovery modules reach into ``~/.construct`` for
    per-run file locks and checkpoint files. On a developer machine with a
    stale lock or pre-existing checkpoint, tests that share a run_id (or
    that lock-claim a run_id from an earlier crash) fail before reaching
    the feature assertions. Pin both dirs to a per-test ``tmp_path`` so
    every test starts from clean slate.
    """
    from operator_mcp.workflow import executor, recovery

    lock_dir = tmp_path / "workflow_locks"
    ckpt_dir = tmp_path / "workflow_checkpoints"
    lock_dir.mkdir(parents=True, exist_ok=True)
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(executor, "_CHECKPOINT_DIR", str(ckpt_dir))
    monkeypatch.setattr(recovery, "_CHECKPOINT_DIR", str(ckpt_dir))
    monkeypatch.setattr(recovery, "_RUN_LOCK_DIR", str(lock_dir))
    yield


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

    def test_parallel_downstream_pulls_in_all_children(self) -> None:
        # Target is DOWNSTREAM of a parallel — closure must include every
        # child of the wrapper. Otherwise _exec_parallel filters cfg.steps to
        # an empty list and the join falsely reports 0/0 success.
        wf = WorkflowDef(
            name="par-down",
            steps=[
                StepDef(
                    id="wrapper",
                    type=StepType.PARALLEL,
                    parallel=ParallelStepConfig(steps=["x", "y"], join=JoinStrategy.ALL),
                ),
                _shell_step("x", "/tmp/x"),
                _shell_step("y", "/tmp/y"),
                _shell_step("consumer", "/tmp/c", depends_on=["wrapper"]),
            ],
            checkpoint=False,
        )
        assert compute_ancestor_closure(wf, "consumer") == {
            "wrapper",
            "x",
            "y",
            "consumer",
        }

    def test_for_each_body_pulls_in_wrapper(self) -> None:
        wf = WorkflowDef(
            name="fe",
            steps=[
                StepDef(
                    id="loop",
                    type=StepType.FOR_EACH,
                    for_each=ForEachStepConfig(
                        variable="i",
                        items=["1", "2"],
                        steps=["body"],
                    ),
                ),
                _shell_step("body", "/tmp/body"),
            ],
            checkpoint=False,
        )
        # Targeting the body step must pull in the wrapper. Without this the
        # main loop's _for_each_owned exclusion would leave remaining empty
        # and the run would falsely report COMPLETED having executed nothing.
        assert compute_ancestor_closure(wf, "body") == {"loop", "body"}

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
        # Empty set signals "unknown" — execute_workflow now hard-fails
        # before falling through to full-run mode.
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
    @pytest.mark.parametrize("join", [JoinStrategy.ALL, JoinStrategy.ANY])
    async def test_parallel_downstream_target_runs_all_children(
        self, tmp_path, join: JoinStrategy
    ) -> None:
        """Run-to-step on a step DOWNSTREAM of a parallel must execute every
        parallel child, not skip them. The previous behaviour filtered
        cfg.steps to an empty list (closure only contained
        ``{wrapper, consumer}``), gave the join a 0/0 success, and reported
        COMPLETED having executed nothing — a false-green. Both `all` and
        `any` joins must behave consistently."""
        sentinel_x = tmp_path / "x.touched"
        sentinel_y = tmp_path / "y.touched"
        sentinel_c = tmp_path / "c.touched"
        wf = WorkflowDef(
            name=f"parallel-down-{join.value}",
            steps=[
                StepDef(
                    id="wrapper",
                    type=StepType.PARALLEL,
                    parallel=ParallelStepConfig(steps=["x", "y"], join=join),
                ),
                _shell_step("x", str(sentinel_x)),
                _shell_step("y", str(sentinel_y)),
                _shell_step("consumer", str(sentinel_c), depends_on=["wrapper"]),
            ],
            checkpoint=False,
        )

        state = await execute_workflow(
            wf, inputs={}, cwd=str(tmp_path), target_step_id="consumer"
        )

        assert state.status == WorkflowStatus.COMPLETED, state.error
        assert sentinel_x.exists(), "parallel child 'x' MUST run"
        assert sentinel_y.exists(), "parallel child 'y' MUST run"
        assert sentinel_c.exists(), "consumer MUST run"

    @pytest.mark.asyncio
    async def test_for_each_body_target_runs_wrapper_and_body(self, tmp_path) -> None:
        """Targeting a step inside a for_each body: the wrapper must run
        (driving its iterations) and the body step must run; nothing else.

        The previous behaviour silently reported COMPLETED having executed
        nothing — the main loop pre-removes for_each-owned children from
        ``remaining`` and the closure ``{body}`` left ``remaining`` empty
        before the wrapper ever dispatched. Closure now pulls in the
        wrapper, so iterations actually run.
        """
        sentinel_outside = tmp_path / "outside.touched"
        body_sentinel = tmp_path / "body.touched"
        # The body step touches a single sentinel each iteration. We just
        # need to verify that the for_each ran (any iteration touched the
        # file) — the exact iteration count isn't the property under test.
        wf = WorkflowDef(
            name="fe-body-rt",
            steps=[
                _shell_step("outside", str(sentinel_outside)),
                StepDef(
                    id="loop",
                    type=StepType.FOR_EACH,
                    for_each=ForEachStepConfig(
                        variable="i",
                        items=["1", "2", "3"],
                        steps=["body"],
                    ),
                ),
                _shell_step("body", str(body_sentinel)),
            ],
            checkpoint=False,
        )

        state = await execute_workflow(
            wf, inputs={}, cwd=str(tmp_path), target_step_id="body"
        )

        assert state.status == WorkflowStatus.COMPLETED, state.error
        assert not sentinel_outside.exists(), "unrelated step must NOT run"
        # Critical: body actually ran (the bug was that nothing ran).
        assert body_sentinel.exists(), "for_each body 'body' MUST run"
        # The for_each wrapper completed.
        assert state.step_results["loop"].status == "completed"

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
        self, tmp_path
    ) -> None:
        """The MCP-tool layer must reject unknown target step ids with a
        classified ``unknown_target_step`` error rather than letting the
        executor silently no-op."""
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
        assert "error" in result, f"expected classified error, got {result}"
        assert result.get("error_code") == "unknown_target_step", result
        assert result.get("error_category") == "validation_error", result

    @pytest.mark.asyncio
    async def test_unknown_target_in_executor_directly_fails_run(
        self, tmp_path
    ) -> None:
        """Defence in depth — the gateway/poller path passes target_step_id
        through Kumiho metadata and reaches execute_workflow without going
        through tool_run_workflow's validation. The executor itself MUST
        fail the run with ``unknown_target_step`` rather than silently
        executing the entire workflow."""
        sentinel_a = tmp_path / "a.touched"
        sentinel_b = tmp_path / "b.touched"
        wf = WorkflowDef(
            name="executor-direct-unknown",
            steps=[
                _shell_step("a", str(sentinel_a)),
                _shell_step("b", str(sentinel_b), depends_on=["a"]),
            ],
            checkpoint=False,
        )

        state = await execute_workflow(
            wf, inputs={}, cwd=str(tmp_path), target_step_id="bogus"
        )

        assert state.status == WorkflowStatus.FAILED
        assert "unknown_target_step" in state.error
        # Critical: nothing should have actually run.
        assert not sentinel_a.exists()
        assert not sentinel_b.exists()

    @pytest.mark.asyncio
    async def test_goto_target_outside_closure_is_skipped(self, tmp_path) -> None:
        """A goto step whose target is outside the run_to closure must NOT
        re-trigger the loop. Downstream step inside the closure should still
        run."""
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

        assert state.status == WorkflowStatus.COMPLETED, state.error
        assert sentinel_setup.exists()
        assert sentinel_final.exists(), "final must run after the goto no-op"
        assert not sentinel_else.exists(), (
            "goto target outside closure must be skipped, not followed"
        )
        assert state.step_results["branch"].status == "completed"


# ── Recovery / resume ────────────────────────────────────────────────


class TestRunToStepRecovery:
    @pytest.mark.asyncio
    async def test_resume_honours_persisted_target_step_id(self, tmp_path) -> None:
        """A run-to-here that pauses (or simulates a checkpoint+resume) must
        preserve its scoping. Build a synthetic resume: pre-populate ``a``'s
        result and call execute_workflow with resume_state — without target
        propagation the resumed run would treat closure as empty (full run)
        and execute ``c``."""
        sentinel_a = tmp_path / "a.touched"
        sentinel_b = tmp_path / "b.touched"
        sentinel_c = tmp_path / "c.touched"
        wf = WorkflowDef(
            name="resume-rt",
            steps=[
                _shell_step("a", str(sentinel_a)),
                _shell_step("b", str(sentinel_b), depends_on=["a"]),
                _shell_step("c", str(sentinel_c), depends_on=["b"]),
            ],
            checkpoint=False,
        )

        # Simulate "a was already done before the pause", target was "b".
        run_id = str(uuid.uuid4())
        resumed_state = WorkflowState(
            workflow_name=wf.name,
            run_id=run_id,
            status=WorkflowStatus.RUNNING,
            inputs={},
            step_results={
                "a": StepResult(step_id="a", status="completed", output="ok"),
            },
            target_step_id="b",
        )
        # Touch the sentinel so we can verify ``a`` did NOT re-run.
        sentinel_a.write_text("pre-existing")

        final_state = await execute_workflow(
            wf,
            inputs={},
            cwd=str(tmp_path),
            run_id=run_id,
            resume_state=resumed_state,
        )

        assert final_state.status == WorkflowStatus.COMPLETED, final_state.error
        # 'b' must have run (the target).
        assert sentinel_b.exists(), "target 'b' must have run on resume"
        # 'c' is downstream of the target → out of closure → must NOT run.
        assert not sentinel_c.exists(), "downstream 'c' must NOT run on resume"
        assert "c" not in final_state.step_results
        # target_step_id should still be persisted on the state.
        assert final_state.target_step_id == "b"


# ── Schema-level validation ──────────────────────────────────────────


class TestSchemaValidation:
    def test_duplicate_step_ids_rejected(self) -> None:
        """Two steps sharing an id is always a bug — the frontend
        ``new Map(tasks.map(...))`` keeps the last while the backend's
        ``step_by_id`` returns the first, so closures disagree. Reject at
        parse time."""
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError) as exc_info:
            WorkflowDef(
                name="dup-ids",
                steps=[
                    _shell_step("a", "/tmp/a"),
                    _shell_step("a", "/tmp/a2"),  # duplicate
                ],
                checkpoint=False,
            )
        assert "Duplicate step id" in str(exc_info.value)

    def test_parallel_duplicate_child_refs_rejected(self) -> None:
        """``parallel.steps: [x, x]`` is always a bug — _exec_parallel keys
        results by step_id (so two refs collapse to one entry) but counts
        ``total = len(cfg.steps)``, producing a false-fail
        ``completed: 1, total: 2``. Reject at parse time."""
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError) as exc_info:
            ParallelStepConfig(steps=["x", "x"], join=JoinStrategy.ALL)
        msg = str(exc_info.value)
        assert "duplicate" in msg
        assert "'x'" in msg

    def test_for_each_duplicate_child_refs_rejected(self) -> None:
        """Same hazard as parallel: ``<step_id>__iter_<N>`` keys collide
        when the same child id is listed twice. Reject at parse time."""
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError) as exc_info:
            ForEachStepConfig(items=["a", "b"], steps=["x", "x"])
        msg = str(exc_info.value)
        assert "duplicate" in msg
        assert "'x'" in msg
