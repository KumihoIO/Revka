"""Tests for the legacy-flat-conditional → canonical-branches bridge.

The frontend editor (and many hand-written workflows) emit conditional
steps in flat form::

    type: conditional
    condition: "${X.status} == 'completed'"
    on_true: step_a
    on_false: step_b

The validator + executor only consume the nested form
(``conditional.branches: [...]``). The bridge in
``StepDef.bridge_legacy_conditional`` translates flat to nested at
parse time so both shapes load identically.

Covers:
  - Legacy flat → loads + has populated branches
  - Canonical form → loads unchanged
  - Mixed (both) → branches wins, top-level dropped
  - Single-sided (on_true only / on_false only)
  - on_true_value / on_false_value flow into branch.value
  - Whitespace / empty-string in legacy fields treated as missing
  - Validator passes for the bridged result
  - Loader auto-derives depends_on from refs in flat ``condition``
  - Real-world failing snippet from the user's report
"""
from __future__ import annotations

from operator_mcp.workflow.loader import load_workflow_from_dict
from operator_mcp.workflow.schema import StepDef, StepType, WorkflowDef
from operator_mcp.workflow.validator import validate_workflow


# ---------------------------------------------------------------------------
# Bridge — direct StepDef construction
# ---------------------------------------------------------------------------


class TestBridgeLegacyFlat:
    def test_flat_both_sides(self) -> None:
        step = StepDef.model_validate({
            "id": "gate",
            "type": "conditional",
            "condition": "${review.status} == 'completed'",
            "on_true": "publish",
            "on_false": "fix",
        })
        assert step.conditional is not None
        assert len(step.conditional.branches) == 2
        b0, b1 = step.conditional.branches
        assert b0.condition == "${review.status} == 'completed'"
        assert b0.goto == "publish"
        assert b0.value is None
        assert b1.condition == "default"
        assert b1.goto == "fix"
        assert b1.value is None

    def test_flat_only_on_true(self) -> None:
        step = StepDef.model_validate({
            "id": "gate",
            "type": "conditional",
            "condition": "score > 0.8",
            "on_true": "ship",
        })
        assert step.conditional is not None
        assert len(step.conditional.branches) == 1
        assert step.conditional.branches[0].goto == "ship"
        assert step.conditional.branches[0].condition == "score > 0.8"

    def test_flat_only_on_false(self) -> None:
        step = StepDef.model_validate({
            "id": "gate",
            "type": "conditional",
            "condition": "score > 0.8",
            "on_false": "fix",
        })
        assert step.conditional is not None
        assert len(step.conditional.branches) == 1
        assert step.conditional.branches[0].goto == "fix"
        assert step.conditional.branches[0].condition == "default"

    def test_flat_with_values(self) -> None:
        step = StepDef.model_validate({
            "id": "gate",
            "type": "conditional",
            "condition": "score > 0.8",
            "on_true": "ship",
            "on_false": "fix",
            "on_true_value": "'approved'",
            "on_false_value": "'rejected'",
        })
        assert step.conditional is not None
        assert step.conditional.branches[0].value == "'approved'"
        assert step.conditional.branches[1].value == "'rejected'"

    def test_flat_legacy_fields_dropped(self) -> None:
        # Legacy keys must not survive on the StepDef model — they're
        # not declared, so Pydantic would silently ignore them, but the
        # bridge pops them so model_dump() stays clean.
        step = StepDef.model_validate({
            "id": "gate",
            "type": "conditional",
            "condition": "x == 1",
            "on_true": "a",
            "on_false": "b",
        })
        dumped = step.model_dump(exclude_none=True)
        for k in ("condition", "on_true", "on_false",
                  "on_true_value", "on_false_value"):
            assert k not in dumped, f"legacy key {k!r} leaked into model_dump()"


class TestBridgePassthrough:
    def test_layout_position_preserved_in_model_dump(self) -> None:
        step = StepDef.model_validate({
            "id": "draft",
            "type": "agent",
            "position": {"x": 120.5, "y": -44},
            "agent": {"prompt": "draft"},
        })
        dumped = step.model_dump(exclude_none=True)
        assert dumped["position"] == {"x": 120.5, "y": -44.0}

    def test_canonical_unchanged(self) -> None:
        step = StepDef.model_validate({
            "id": "gate",
            "type": "conditional",
            "conditional": {
                "branches": [
                    {"condition": "x == 1", "goto": "a"},
                    {"condition": "default", "goto": "b"},
                ],
            },
        })
        assert step.conditional is not None
        assert len(step.conditional.branches) == 2
        assert step.conditional.branches[0].goto == "a"
        assert step.conditional.branches[1].condition == "default"

    def test_mixed_branches_wins(self) -> None:
        # Caller provided canonical branches AND a stray top-level
        # `condition` — branches win, top-level is silently dropped.
        step = StepDef.model_validate({
            "id": "gate",
            "type": "conditional",
            "condition": "stray == 1",
            "on_true": "stray_target",
            "conditional": {
                "branches": [
                    {"condition": "real == 1", "goto": "real_target"},
                ],
            },
        })
        assert step.conditional is not None
        assert len(step.conditional.branches) == 1
        assert step.conditional.branches[0].goto == "real_target"
        assert step.conditional.branches[0].condition == "real == 1"


class TestBridgeEdgeCases:
    def test_whitespace_targets_treated_as_missing(self) -> None:
        # `on_true: "   "` should not produce a branch.
        step = StepDef.model_validate({
            "id": "gate",
            "type": "conditional",
            "condition": "x == 1",
            "on_true": "   ",
            "on_false": "real_target",
        })
        assert step.conditional is not None
        assert len(step.conditional.branches) == 1
        assert step.conditional.branches[0].goto == "real_target"

    def test_only_condition_no_targets_left_alone(self) -> None:
        # Without on_true/on_false there's nothing to translate — the
        # validator will emit its clearer "missing config" error later.
        # Pydantic on the StepDef itself accepts this since `conditional`
        # is optional; the validator catches the empty-branches case.
        step = StepDef.model_validate({
            "id": "gate",
            "type": "conditional",
            "condition": "x == 1",
        })
        assert step.conditional is None

    def test_same_target_both_branches(self) -> None:
        # `on_true == on_false` — emit both anyway, executor handles.
        step = StepDef.model_validate({
            "id": "gate",
            "type": "conditional",
            "condition": "x == 1",
            "on_true": "merge",
            "on_false": "merge",
        })
        assert step.conditional is not None
        assert len(step.conditional.branches) == 2
        assert step.conditional.branches[0].goto == "merge"
        assert step.conditional.branches[1].goto == "merge"


# ---------------------------------------------------------------------------
# End-to-end — full WorkflowDef round-trip + validation
# ---------------------------------------------------------------------------


def _wf(steps: list[dict]) -> dict:
    return {"name": "bridge-test", "steps": steps}


class TestWorkflowEnd2End:
    def test_legacy_flat_workflow_validates(self) -> None:
        wf = load_workflow_from_dict(_wf([
            {
                "id": "review",
                "type": "agent",
                "agent": {"prompt": "review this"},
            },
            {
                "id": "gate",
                "type": "conditional",
                "condition": "${review.status} == 'completed'",
                "on_true": "publish",
                "on_false": "fix",
                "depends_on": ["review"],
            },
            {
                "id": "publish",
                "type": "shell",
                "shell": {"command": "echo publish"},
            },
            {
                "id": "fix",
                "type": "shell",
                "shell": {"command": "echo fix"},
            },
        ]))
        # The bridge fires inside StepDef construction — gate has branches.
        gate = wf.step_by_id("gate")
        assert gate is not None
        assert gate.conditional is not None
        assert {b.goto for b in gate.conditional.branches} == {"publish", "fix"}

        result = validate_workflow(wf)
        assert result.valid, f"validation failed: {result.errors!r}"

    def test_loader_auto_deps_from_flat_condition(self) -> None:
        # The bridge runs first → branches[0].condition holds the ref.
        # Loader's _scan_step_for_refs walks branches[].condition, so
        # the ref to `review` should auto-add to gate.depends_on even if
        # the user didn't declare it.
        wf = load_workflow_from_dict(_wf([
            {
                "id": "review",
                "type": "agent",
                "agent": {"prompt": "review"},
            },
            {
                "id": "gate",
                "type": "conditional",
                "condition": "${review.status} == 'completed'",
                "on_true": "publish",
                "on_false": "fix",
                # No explicit depends_on — must be inferred.
            },
            {"id": "publish", "type": "shell", "shell": {"command": "echo p"}},
            {"id": "fix", "type": "shell", "shell": {"command": "echo f"}},
        ]))
        gate = wf.step_by_id("gate")
        assert gate is not None
        assert "review" in gate.depends_on

    def test_layout_position_survives_full_workflow_load(self) -> None:
        wf = load_workflow_from_dict(_wf([
            {
                "id": "draft",
                "type": "agent",
                "position": {"x": 320, "y": 180.25},
                "agent": {"prompt": "draft"},
            },
        ]))
        step = wf.step_by_id("draft")
        assert step is not None
        assert step.position is not None
        assert step.position.x == 320
        assert step.position.y == 180.25


class TestUserBlogPostSnippet:
    """Reproduces the user's actual failing workflow shape — every gate
    uses flat `condition`/`on_true`/`on_false`."""

    def test_revka_blog_post_loads(self) -> None:
        steps = [
            {"id": "zeroclaw-resolve", "type": "agent",
             "agent": {"prompt": "resolve zeroclaw"}},
            {
                "id": "zeroclaw-check",
                "type": "conditional",
                "description": "Skip research if cached",
                "condition": "${zeroclaw-resolve.status} == \"completed\"",
                "on_true": "draft_post",
                "on_false": "research_zeroclaw",
                "depends_on": ["zeroclaw-resolve"],
            },
            {"id": "openclaw-resolve", "type": "agent",
             "agent": {"prompt": "resolve openclaw"}},
            {
                "id": "openclaw-check",
                "type": "conditional",
                "description": "If research is missing run research agent step to register",
                "condition": "${openclaw-resolve.status} == \"completed\"",
                "on_true": "draft_post",
                "on_false": "research_openclaw",
                "depends_on": ["openclaw-resolve"],
            },
            {"id": "sim-resolve", "type": "agent",
             "agent": {"prompt": "resolve sim"}},
            {
                "id": "sim-check",
                "type": "conditional",
                "condition": "${sim-resolve.status} == \"completed\"",
                "on_true": "draft_post",
                "on_false": "research_sim",
                "depends_on": ["sim-resolve"],
            },
            {"id": "hermes-resolve", "type": "agent",
             "agent": {"prompt": "resolve hermes"}},
            {
                "id": "hermes-check",
                "type": "conditional",
                "condition": "${hermes-resolve.status} == \"completed\"",
                "on_true": "draft_post",
                "on_false": "research_hermes",
                "depends_on": ["hermes-resolve"],
            },
            {"id": "research_zeroclaw", "type": "shell",
             "shell": {"command": "echo r1"}},
            {"id": "research_openclaw", "type": "shell",
             "shell": {"command": "echo r2"}},
            {"id": "research_sim", "type": "shell",
             "shell": {"command": "echo r3"}},
            {"id": "research_hermes", "type": "shell",
             "shell": {"command": "echo r4"}},
            {"id": "draft_post", "type": "shell",
             "shell": {"command": "echo draft"}},
        ]
        wf = load_workflow_from_dict({
            "name": "revka-blog-post",
            "steps": steps,
        })
        # All four gates bridged.
        for gate_id in ("zeroclaw-check", "openclaw-check",
                        "sim-check", "hermes-check"):
            gate = wf.step_by_id(gate_id)
            assert gate is not None
            assert gate.type == StepType.CONDITIONAL
            assert gate.conditional is not None
            gotos = {b.goto for b in gate.conditional.branches}
            assert "draft_post" in gotos

        result = validate_workflow(wf)
        assert result.valid, f"validation failed: {result.errors!r}"
