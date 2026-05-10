"""Tests for the simpleeval-based workflow conditional evaluator.

Covers _eval_condition (gate predicate), _eval_branch_value (branch value
emission), and _exec_conditional / _resolve_conditional integration:

  - Basic comparisons (==, !=, >, <)
  - Workflow-language operators (&&, ||, !, ?:)
  - Python-form operators (and, or, not, if/else)
  - String literals (quoted) and name resolution (review.status)
  - `default` keyword fallback
  - Missing variables / parse errors → False (logged, not crash)
  - Branch value emission to StepResult.output
  - Backward compat with `${X.field}` interpolation form
  - Backward compat with bare-word `contains` RHS
"""
from __future__ import annotations

import pytest

from operator_mcp.workflow.executor import (
    _eval_branch_value,
    _eval_condition,
    _exec_conditional,
    _preprocess_expr,
    _resolve_conditional,
)
from operator_mcp.workflow.schema import (
    ConditionalBranch,
    ConditionalStepConfig,
    StepDef,
    StepResult,
    StepType,
    WorkflowState,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def state() -> WorkflowState:
    s = WorkflowState(workflow_name="test", run_id="run-1")
    s.step_results["review"] = StepResult(
        step_id="review",
        status="completed",
        output="APPROVED looks good",
        output_data={"score": 0.9, "note": "pass"},
    )
    s.step_results["build"] = StepResult(
        step_id="build",
        status="failed",
        output="compile error",
        error="exit 1",
    )
    s.inputs = {"threshold": 0.8, "name": "alice"}
    return s


# ---------------------------------------------------------------------------
# _preprocess_expr
# ---------------------------------------------------------------------------

class TestPreprocess:
    def test_and_or(self):
        assert "and" in _preprocess_expr("a && b")
        assert "or" in _preprocess_expr("a || b")

    def test_not(self):
        assert "not" in _preprocess_expr("!flag")
        assert "not" in _preprocess_expr("!(a == b)")

    def test_neq_preserved(self):
        # `!=` must NOT become `not =`.
        assert "!=" in _preprocess_expr("a != b")

    def test_ternary(self):
        out = _preprocess_expr("cond ? a : b")
        assert "if" in out and "else" in out

    def test_contains_quotes_bare_rhs(self):
        # Legacy YAML form: `${X} contains APPROVED` (bare word RHS).
        out = _preprocess_expr("foo contains APPROVED")
        # RHS gets quoted, sides flipped to `in`.
        assert "in" in out
        assert "'APPROVED'" in out


# ---------------------------------------------------------------------------
# _eval_condition — basic comparisons
# ---------------------------------------------------------------------------

class TestBasicComparisons:
    def test_equality_via_name(self, state):
        assert _eval_condition("review.status == 'completed'", state) is True
        assert _eval_condition("review.status == 'failed'", state) is False

    def test_inequality_via_name(self, state):
        assert _eval_condition("review.status != 'failed'", state) is True

    def test_numeric_comparisons(self, state):
        assert _eval_condition("review.output_data.score > 0.5", state) is True
        assert _eval_condition("review.output_data.score < 0.5", state) is False
        assert _eval_condition("review.output_data.score >= 0.9", state) is True

    def test_input_compare(self, state):
        assert _eval_condition(
            "review.output_data.score >= inputs.threshold", state
        ) is True


# ---------------------------------------------------------------------------
# _eval_condition — boolean operators / parens
# ---------------------------------------------------------------------------

class TestBooleanOps:
    def test_python_and_or(self, state):
        assert _eval_condition(
            "review.status == 'completed' and review.output_data.score > 0.5",
            state,
        ) is True
        assert _eval_condition(
            "review.status == 'failed' or review.output_data.score > 0.5",
            state,
        ) is True

    def test_workflow_and_or(self, state):
        assert _eval_condition(
            "review.status == 'completed' && review.output_data.score > 0.5",
            state,
        ) is True
        assert _eval_condition(
            "review.status == 'failed' || review.output_data.score > 0.5",
            state,
        ) is True

    def test_parens(self, state):
        assert _eval_condition(
            "(review.status == 'completed') and (review.output_data.score >= inputs.threshold)",
            state,
        ) is True

    def test_negation(self, state):
        assert _eval_condition("!(review.status == 'failed')", state) is True


# ---------------------------------------------------------------------------
# `default` and empty
# ---------------------------------------------------------------------------

class TestDefault:
    def test_default_keyword(self, state):
        assert _eval_condition("default", state) is True
        assert _eval_condition("DEFAULT", state) is True
        assert _eval_condition("  default  ", state) is True

    def test_empty(self, state):
        assert _eval_condition("", state) is True


# ---------------------------------------------------------------------------
# Failure modes — missing names, syntax errors → False (no crash)
# ---------------------------------------------------------------------------

class TestFailureModes:
    def test_missing_variable_returns_false(self, state):
        assert _eval_condition("nonexistent.field == 'x'", state) is False

    def test_syntax_error_returns_false(self, state):
        assert _eval_condition("@@invalid syntax@@", state) is False

    def test_arithmetic_typeerror_returns_false(self, state):
        # review.status is a string; > on string vs int fails in Py3.
        assert _eval_condition("review.status > 5", state) is False


# ---------------------------------------------------------------------------
# Backward compat — legacy `${X.field}` interpolation form
# ---------------------------------------------------------------------------

class TestLegacyInterpolation:
    def test_legacy_eq_with_quoted_var(self, state):
        # Ye olde recipe: interpolated value gets auto-quoted now so the
        # bare identifier doesn't trip simpleeval.
        assert _eval_condition("${review.status} == 'completed'", state) is True
        assert _eval_condition("${review.status} != 'failed'", state) is True

    def test_legacy_numeric_comparison(self, state):
        # Numeric substitutions stay unquoted.
        assert _eval_condition("${review.output_data.score} >= 0.5", state) is True

    def test_legacy_contains_bare_rhs(self, state):
        # `code-review.yaml` form: bare-word RHS.
        assert _eval_condition(
            "${review.output} contains APPROVED", state
        ) is True
        assert _eval_condition(
            "${review.output} contains REJECTED", state
        ) is False

    def test_legacy_contains_quoted_rhs(self, state):
        assert _eval_condition(
            "${review.output} contains 'APPROVED'", state
        ) is True


# ---------------------------------------------------------------------------
# String contains via `in` (Python form)
# ---------------------------------------------------------------------------

class TestStringContains:
    def test_python_in(self, state):
        assert _eval_condition("'APPROVED' in review.output", state) is True
        assert _eval_condition("'REJECTED' in review.output", state) is False

    def test_workflow_contains(self, state):
        assert _eval_condition("review.output contains 'APPROVED'", state) is True
        assert _eval_condition("review.output contains 'REJECTED'", state) is False


# ---------------------------------------------------------------------------
# Branch value emission
# ---------------------------------------------------------------------------

class TestBranchValue:
    def test_value_string_literal(self, state):
        assert _eval_branch_value("'approved'", state) == "approved"

    def test_value_name_reference(self, state):
        assert _eval_branch_value("review.status", state) == "completed"

    def test_value_arithmetic(self, state):
        # Branch values can compute on numerics. (Float precision: avoid
        # 0.9 + 0.05 binary-rep noise — use an integer-clean expression.)
        assert _eval_branch_value("review.output_data.score * 10", state) == "9.0"

    def test_value_ternary(self, state):
        assert _eval_branch_value(
            "review.status == 'completed' ? 'go' : 'stop'", state
        ) == "go"

    def test_value_python_ternary(self, state):
        assert _eval_branch_value(
            "'go' if review.status == 'completed' else 'stop'", state
        ) == "go"

    def test_value_missing_returns_empty(self, state):
        assert _eval_branch_value(None, state) == ""
        assert _eval_branch_value("", state) == ""

    def test_value_eval_failure_returns_empty(self, state):
        # Missing var → eval error → empty string (logged).
        assert _eval_branch_value("missing.thing", state) == ""

    def test_value_bool_coercion(self, state):
        assert _eval_branch_value("review.output_data.score > 0", state) == "true"


# ---------------------------------------------------------------------------
# Integration: _exec_conditional + _resolve_conditional
# ---------------------------------------------------------------------------

class TestExecConditional:
    def test_first_match_wins(self, state):
        step = StepDef(
            id="gate",
            type=StepType.CONDITIONAL,
            conditional=ConditionalStepConfig(branches=[
                ConditionalBranch(condition="review.status == 'failed'", goto="fix"),
                ConditionalBranch(condition="default", goto="done"),
            ]),
        )
        result = _exec_conditional(step, state)
        assert result.status == "completed"
        assert result.output_data["__matched_goto__"] == "done"
        assert result.output == ""  # no value set

        state.step_results["gate"] = result
        assert _resolve_conditional(step, state) == "done"

    def test_value_emitted_to_output(self, state):
        step = StepDef(
            id="gate",
            type=StepType.CONDITIONAL,
            conditional=ConditionalStepConfig(branches=[
                ConditionalBranch(
                    condition="review.status == 'completed'",
                    goto="done",
                    value="'approved'",
                ),
                ConditionalBranch(condition="default", goto="fix", value="'rejected'"),
            ]),
        )
        result = _exec_conditional(step, state)
        assert result.output == "approved"
        assert result.output_data["__matched_goto__"] == "done"

    def test_value_with_ternary(self, state):
        step = StepDef(
            id="gate",
            type=StepType.CONDITIONAL,
            conditional=ConditionalStepConfig(branches=[
                ConditionalBranch(
                    condition="default",
                    goto="next",
                    value="review.output_data.score > 0.8 ? 'high' : 'low'",
                ),
            ]),
        )
        result = _exec_conditional(step, state)
        assert result.output == "high"

    def test_legacy_yaml_still_works(self, state):
        """Existing built-in code-review.yaml form: `${X} contains APPROVED`."""
        step = StepDef(
            id="check_verdict",
            type=StepType.CONDITIONAL,
            conditional=ConditionalStepConfig(branches=[
                ConditionalBranch(
                    condition="${review.output} contains APPROVED",
                    goto="summary",
                ),
                ConditionalBranch(condition="default", goto="fix"),
            ]),
        )
        result = _exec_conditional(step, state)
        assert result.output_data["__matched_goto__"] == "summary"

    def test_resolve_falls_back_when_no_cached_match(self, state):
        # If the StepResult is absent (e.g. someone calls _resolve_conditional
        # directly), it re-evaluates from scratch. Important for test/debug
        # paths and for older checkpoints reloaded after this change.
        step = StepDef(
            id="gate",
            type=StepType.CONDITIONAL,
            conditional=ConditionalStepConfig(branches=[
                ConditionalBranch(condition="review.status == 'completed'", goto="ok"),
            ]),
        )
        # No state.step_results['gate'] → fallback path.
        assert _resolve_conditional(step, state) == "ok"


# ---------------------------------------------------------------------------
# Schema sanity: ConditionalBranch accepts optional `value`
# ---------------------------------------------------------------------------

class TestSchema:
    def test_value_field_optional(self):
        # Without value
        b = ConditionalBranch(condition="x", goto="y")
        assert b.value is None
        # With value
        b2 = ConditionalBranch(condition="x", goto="y", value="'hi'")
        assert b2.value == "'hi'"
