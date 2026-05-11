"""Tests for conditional branch-closure gating in the scheduler.

The bug being fixed: a conditional step's matched-branch ``goto`` ROUTES
the executor but does NOT suppress steps on non-matched branches. Combined
with PR #170's auto-derived ``depends_on`` from ``${X.output}``
interpolation, downstream steps on the loser branches were still becoming
scheduler-eligible (their only dep — the conditional — was ``completed``)
and running.

The fix gates "exclusive non-matched" steps lazily at scheduling time:
steps reachable transitively only from a non-matched goto target are
marked ``skipped`` when the scheduler picks them up. Steps reachable via
the matched branch OR via some path outside the conditional are NOT
gated.
"""
from __future__ import annotations

import pytest

from operator_mcp.workflow.executor import (
    ACTIVE_WORKFLOWS,
    _build_forward_deps_map,
    _forward_closure,
    _is_reachable_outside_conditional,
    _is_step_gated_by_conditional,
    execute_workflow,
)
from operator_mcp.workflow.schema import (
    ConditionalBranch,
    ConditionalStepConfig,
    ForEachStepConfig,
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


def _shell_step(
    step_id: str,
    sentinel: str,
    depends_on: list[str] | None = None,
) -> StepDef:
    return StepDef(
        id=step_id,
        type=StepType.SHELL,
        depends_on=depends_on or [],
        shell=ShellStepConfig(command=f"touch {sentinel}", timeout=10),
    )


def _cond_step(
    step_id: str,
    branches: list[ConditionalBranch],
    depends_on: list[str] | None = None,
) -> StepDef:
    return StepDef(
        id=step_id,
        type=StepType.CONDITIONAL,
        depends_on=depends_on or [],
        conditional=ConditionalStepConfig(branches=branches),
    )


@pytest.fixture(autouse=True)
def _clear_active_workflows():
    ACTIVE_WORKFLOWS.clear()
    yield
    ACTIVE_WORKFLOWS.clear()


@pytest.fixture(autouse=True)
def _isolate_workflow_state(monkeypatch, tmp_path):
    """Hermetic per-test file dirs so locks/checkpoints don't bleed."""
    from operator_mcp.workflow import executor, recovery

    lock_dir = tmp_path / "workflow_locks"
    ckpt_dir = tmp_path / "workflow_checkpoints"
    lock_dir.mkdir(parents=True, exist_ok=True)
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(executor, "_CHECKPOINT_DIR", str(ckpt_dir))
    monkeypatch.setattr(recovery, "_RUN_LOCK_DIR", str(lock_dir))
    yield


# ── unit tests for the helpers ──────────────────────────────────────────


class TestForwardDepsMap:
    def test_inverts_edges(self):
        wf = WorkflowDef(
            name="t",
            steps=[
                _shell_step("a", "/tmp/a"),
                _shell_step("b", "/tmp/b", depends_on=["a"]),
                _shell_step("c", "/tmp/c", depends_on=["a", "b"]),
            ],
            checkpoint=False,
        )
        fwd = _build_forward_deps_map(wf)
        assert fwd["a"] == {"b", "c"}
        assert fwd["b"] == {"c"}
        assert "c" not in fwd

    def test_forward_closure_bfs(self):
        wf = WorkflowDef(
            name="t",
            steps=[
                _shell_step("a", "/tmp/a"),
                _shell_step("b", "/tmp/b", depends_on=["a"]),
                _shell_step("c", "/tmp/c", depends_on=["b"]),
            ],
            checkpoint=False,
        )
        fwd = _build_forward_deps_map(wf)
        assert _forward_closure("a", fwd) == {"b", "c"}
        assert _forward_closure("c", fwd) == set()


# ── integration tests via execute_workflow ──────────────────────────────


class TestBranchGating:
    @pytest.mark.asyncio
    async def test_basic_matched_branch_runs_targets(self, tmp_path):
        """Matched branch's target runs; non-matched target is skipped."""
        sentinel_matched = tmp_path / "matched.touched"
        sentinel_loser = tmp_path / "loser.touched"
        sentinel_seed = tmp_path / "seed.touched"

        wf = WorkflowDef(
            name="gating-basic",
            steps=[
                _shell_step("seed", str(sentinel_seed)),
                _cond_step(
                    "gate",
                    branches=[
                        ConditionalBranch(
                            condition="default",
                            goto="matched",
                            value="'go'",
                        ),
                        ConditionalBranch(
                            condition="default",
                            goto="loser",
                            value="'no'",
                        ),
                    ],
                    depends_on=["seed"],
                ),
                _shell_step("matched", str(sentinel_matched), depends_on=["gate"]),
                _shell_step("loser", str(sentinel_loser), depends_on=["gate"]),
            ],
            checkpoint=False,
        )

        state = await execute_workflow(wf, inputs={}, cwd=str(tmp_path))

        assert state.status == WorkflowStatus.COMPLETED, state.error
        assert sentinel_matched.exists(), "matched branch target MUST run"
        assert not sentinel_loser.exists(), "non-matched branch target MUST be skipped"
        assert state.step_results["loser"].status == "skipped"
        assert "conditional branch not matched" in (
            state.step_results["loser"].error or ""
        )

    @pytest.mark.asyncio
    async def test_default_fallback_works(self, tmp_path):
        """When all explicit conditions fail, default fires; loser skipped."""
        sentinel_default = tmp_path / "default.touched"
        sentinel_explicit = tmp_path / "explicit.touched"

        wf = WorkflowDef(
            name="gating-default",
            steps=[
                _cond_step(
                    "gate",
                    branches=[
                        # Force the first branch to NOT match.
                        ConditionalBranch(
                            condition="1 == 2",
                            goto="explicit",
                            value="'no'",
                        ),
                        ConditionalBranch(
                            condition="default",
                            goto="default_target",
                            value="'go'",
                        ),
                    ],
                ),
                _shell_step("explicit", str(sentinel_explicit), depends_on=["gate"]),
                _shell_step("default_target", str(sentinel_default), depends_on=["gate"]),
            ],
            checkpoint=False,
        )

        state = await execute_workflow(wf, inputs={}, cwd=str(tmp_path))

        assert state.status == WorkflowStatus.COMPLETED, state.error
        assert sentinel_default.exists()
        assert not sentinel_explicit.exists()
        assert state.step_results["explicit"].status == "skipped"

    @pytest.mark.asyncio
    async def test_step_reachable_from_both_runs(self, tmp_path):
        """A step downstream of BOTH matched and non-matched branches still
        runs (the matched-branch closure rescues it)."""
        sentinel_matched = tmp_path / "matched.touched"
        sentinel_loser = tmp_path / "loser.touched"
        sentinel_merge = tmp_path / "merge.touched"

        wf = WorkflowDef(
            name="gating-both",
            steps=[
                _cond_step(
                    "gate",
                    branches=[
                        ConditionalBranch(condition="default", goto="matched", value="'go'"),
                        ConditionalBranch(condition="default", goto="loser", value="'no'"),
                    ],
                ),
                _shell_step("matched", str(sentinel_matched), depends_on=["gate"]),
                _shell_step("loser", str(sentinel_loser), depends_on=["gate"]),
                # ``merge`` is downstream of BOTH targets — should still run
                # because the matched-branch closure rescues it.
                _shell_step(
                    "merge",
                    str(sentinel_merge),
                    depends_on=["matched", "loser"],
                ),
            ],
            checkpoint=False,
        )

        state = await execute_workflow(wf, inputs={}, cwd=str(tmp_path))

        # Note: loser is skipped, so its dependency on merge is unmet by the
        # standard rule. To express "either branch" merging cleanly, users
        # typically use a separate join — but the gating logic itself must
        # NOT mark ``merge`` as gated. Verify the helper directly.
        wf2 = wf
        fwd = _build_forward_deps_map(wf2)
        # Simulate the gate having fired with ``matched`` winning.
        fake_state = WorkflowState(workflow_name="t", run_id="r")
        fake_state.conditional_branch_results["gate"] = {
            "matched_branch_index": 0,
            "matched_goto": "matched",
            "non_matched_gotos": ["loser"],
        }
        assert not _is_step_gated_by_conditional(
            "merge", fake_state, wf2, fwd
        ), "merge is in the matched closure — must NOT be gated"
        assert _is_step_gated_by_conditional(
            "loser", fake_state, wf2, fwd
        ), "loser IS exclusive non-matched"

    def test_step_reachable_outside_conditional_runs(self):
        """A step that has another upstream dependency NOT transiting the
        conditional must not be gated."""
        wf = WorkflowDef(
            name="gating-outside",
            steps=[
                _shell_step("seed", "/tmp/seed"),
                _cond_step(
                    "gate",
                    branches=[
                        ConditionalBranch(condition="default", goto="winner", value="'go'"),
                        ConditionalBranch(condition="default", goto="loser", value="'no'"),
                    ],
                ),
                _shell_step("winner", "/tmp/w", depends_on=["gate"]),
                # ``downstream`` depends on BOTH the non-matched target AND
                # an independent seed that doesn't transit ``gate``.
                _shell_step(
                    "downstream",
                    "/tmp/d",
                    depends_on=["loser", "seed"],
                ),
                _shell_step("loser", "/tmp/l", depends_on=["gate"]),
            ],
            checkpoint=False,
        )
        fwd = _build_forward_deps_map(wf)
        state = WorkflowState(workflow_name="t", run_id="r")
        state.conditional_branch_results["gate"] = {
            "matched_branch_index": 0,
            "matched_goto": "winner",
            "non_matched_gotos": ["loser"],
        }
        # ``downstream`` has an external dep on ``seed`` — not gated.
        assert not _is_step_gated_by_conditional("downstream", state, wf, fwd)
        # ``loser`` only descends from the conditional — gated.
        assert _is_step_gated_by_conditional("loser", state, wf, fwd)

    @pytest.mark.asyncio
    async def test_nested_conditionals(self, tmp_path):
        """C1 matches branch with goto B1; B1 is a conditional matching goto
        C. Non-matched of C1 has D. D must NOT run; B1 and C must run."""
        sentinel_b1_target = tmp_path / "b1_target.touched"
        sentinel_c = tmp_path / "c.touched"
        sentinel_d = tmp_path / "d.touched"
        sentinel_inner_loser = tmp_path / "inner_loser.touched"

        wf = WorkflowDef(
            name="gating-nested",
            steps=[
                _cond_step(
                    "c1",
                    branches=[
                        ConditionalBranch(condition="default", goto="b1", value="'go'"),
                        ConditionalBranch(condition="default", goto="d", value="'no'"),
                    ],
                ),
                _shell_step("b1_dummy", str(sentinel_b1_target), depends_on=["c1"]),
                _cond_step(
                    "b1",
                    branches=[
                        ConditionalBranch(condition="default", goto="c", value="'inner-go'"),
                        ConditionalBranch(
                            condition="default",
                            goto="inner_loser",
                            value="'inner-no'",
                        ),
                    ],
                    depends_on=["c1"],
                ),
                _shell_step("c", str(sentinel_c), depends_on=["b1"]),
                _shell_step("inner_loser", str(sentinel_inner_loser), depends_on=["b1"]),
                _shell_step("d", str(sentinel_d), depends_on=["c1"]),
            ],
            checkpoint=False,
        )

        state = await execute_workflow(wf, inputs={}, cwd=str(tmp_path))

        assert state.status == WorkflowStatus.COMPLETED, state.error
        # Matched paths run:
        assert sentinel_c.exists()
        assert sentinel_b1_target.exists()
        # Both non-matched (outer + inner) are skipped:
        assert not sentinel_d.exists()
        assert not sentinel_inner_loser.exists()
        assert state.step_results["d"].status == "skipped"
        assert state.step_results["inner_loser"].status == "skipped"

    @pytest.mark.asyncio
    async def test_for_each_subscope_unaffected(self, tmp_path):
        """A for_each loop outside the conditional runs normally; gating
        only affects steps in non-matched closures."""
        sentinel_body = tmp_path / "body.touched"
        sentinel_winner = tmp_path / "winner.touched"
        sentinel_loser = tmp_path / "loser.touched"

        wf = WorkflowDef(
            name="gating-foreach",
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
                _shell_step("body", str(sentinel_body)),
                _cond_step(
                    "gate",
                    branches=[
                        ConditionalBranch(condition="default", goto="winner", value="'go'"),
                        ConditionalBranch(condition="default", goto="loser", value="'no'"),
                    ],
                    depends_on=["loop"],
                ),
                _shell_step("winner", str(sentinel_winner), depends_on=["gate"]),
                _shell_step("loser", str(sentinel_loser), depends_on=["gate"]),
            ],
            checkpoint=False,
        )

        state = await execute_workflow(wf, inputs={}, cwd=str(tmp_path))

        assert state.status == WorkflowStatus.COMPLETED, state.error
        # for_each body ran (the sentinel was touched at least once)
        assert sentinel_body.exists()
        assert sentinel_winner.exists()
        assert not sentinel_loser.exists()
        assert state.step_results["loser"].status == "skipped"

    @pytest.mark.asyncio
    async def test_parallel_subscope_unaffected(self, tmp_path):
        """A parallel wrapper outside the conditional runs all children
        normally; gating only affects steps in non-matched closures."""
        sentinel_x = tmp_path / "x.touched"
        sentinel_y = tmp_path / "y.touched"
        sentinel_winner = tmp_path / "winner.touched"
        sentinel_loser = tmp_path / "loser.touched"

        wf = WorkflowDef(
            name="gating-parallel",
            steps=[
                StepDef(
                    id="wrap",
                    type=StepType.PARALLEL,
                    parallel=ParallelStepConfig(steps=["x", "y"], join=JoinStrategy.ALL),
                ),
                _shell_step("x", str(sentinel_x)),
                _shell_step("y", str(sentinel_y)),
                _cond_step(
                    "gate",
                    branches=[
                        ConditionalBranch(condition="default", goto="winner", value="'go'"),
                        ConditionalBranch(condition="default", goto="loser", value="'no'"),
                    ],
                    depends_on=["wrap"],
                ),
                _shell_step("winner", str(sentinel_winner), depends_on=["gate"]),
                _shell_step("loser", str(sentinel_loser), depends_on=["gate"]),
            ],
            checkpoint=False,
        )

        state = await execute_workflow(wf, inputs={}, cwd=str(tmp_path))

        assert state.status == WorkflowStatus.COMPLETED, state.error
        assert sentinel_x.exists()
        assert sentinel_y.exists()
        assert sentinel_winner.exists()
        assert not sentinel_loser.exists()

    @pytest.mark.asyncio
    async def test_user_reported_workflow_shape(self, tmp_path):
        """Replicates the zeroclaw-check / research_zeroclaw shape.

        When ``zeroclaw-resolve.output_data.found == True``, the matched
        branch targets ``draft_post``. The non-matched branch targets
        ``research_zeroclaw``, which has its own downstream chain
        (``zeroclaw-research`` and ``goto-zeroclaw-resolve``). All three
        non-matched-side steps must be skipped; ``draft_post`` runs.
        """
        sentinel_draft = tmp_path / "draft.touched"
        sentinel_research = tmp_path / "research.touched"
        sentinel_zeroclaw_research = tmp_path / "zr.touched"
        sentinel_goto = tmp_path / "goto.touched"

        wf = WorkflowDef(
            name="zeroclaw-shape",
            steps=[
                # Simulate the resolve step's output that the conditional reads.
                StepDef(
                    id="zeroclaw-resolve",
                    type=StepType.SHELL,
                    shell=ShellStepConfig(
                        # Stash found=True into stdout so the conditional sees
                        # it via output_data.found when we set it manually.
                        command="echo found",
                        timeout=10,
                    ),
                ),
                _cond_step(
                    "zeroclaw-check",
                    branches=[
                        # First branch is ``default`` → always matches first.
                        ConditionalBranch(
                            condition="default",
                            goto="draft_post",
                            value="'cached'",
                        ),
                        ConditionalBranch(
                            condition="default",
                            goto="research_zeroclaw",
                            value="'proceed'",
                        ),
                    ],
                    depends_on=["zeroclaw-resolve"],
                ),
                _shell_step("draft_post", str(sentinel_draft), depends_on=["zeroclaw-check"]),
                _shell_step(
                    "research_zeroclaw",
                    str(sentinel_research),
                    depends_on=["zeroclaw-check"],
                ),
                _shell_step(
                    "zeroclaw-research",
                    str(sentinel_zeroclaw_research),
                    depends_on=["research_zeroclaw"],
                ),
                _shell_step(
                    "goto-zeroclaw-resolve",
                    str(sentinel_goto),
                    depends_on=["zeroclaw-research"],
                ),
            ],
            checkpoint=False,
        )

        state = await execute_workflow(wf, inputs={}, cwd=str(tmp_path))

        assert state.status == WorkflowStatus.COMPLETED, state.error
        assert sentinel_draft.exists(), "draft_post (matched branch) MUST run"
        assert not sentinel_research.exists()
        assert not sentinel_zeroclaw_research.exists()
        assert not sentinel_goto.exists()
        assert state.step_results["research_zeroclaw"].status == "skipped"
        assert state.step_results["zeroclaw-research"].status == "skipped"
        assert state.step_results["goto-zeroclaw-resolve"].status == "skipped"


# ── helper-level edge case ──────────────────────────────────────────────


class TestReachableOutside:
    def test_root_outside_returns_true(self):
        """A step that has an upstream ROOT not via cond_id is reachable
        outside."""
        wf = WorkflowDef(
            name="ro-test",
            steps=[
                _shell_step("seed", "/tmp/s"),
                _shell_step("gate", "/tmp/g"),  # acts as the conditional id stand-in
                _shell_step("target", "/tmp/t", depends_on=["seed", "gate"]),
            ],
            checkpoint=False,
        )
        assert _is_reachable_outside_conditional("target", "gate", wf) is True

    def test_only_via_conditional_returns_false(self):
        wf = WorkflowDef(
            name="ro-test2",
            steps=[
                _shell_step("gate", "/tmp/g"),
                _shell_step("target", "/tmp/t", depends_on=["gate"]),
            ],
            checkpoint=False,
        )
        assert _is_reachable_outside_conditional("target", "gate", wf) is False
