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
        # Matched goto is stashed on workflow state, NOT on output_data —
        # keeps the sentinel out of ${gate.output_data.*} interpolation
        # and out of the simpleeval names dict.
        assert state.conditional_routes["gate"] == "done"
        assert "__matched_goto__" not in result.output_data
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
        assert state.conditional_routes["gate"] == "done"

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
        assert state.conditional_routes["check_verdict"] == "summary"

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


# ---------------------------------------------------------------------------
# Regression: string-literal contamination in _preprocess_expr
# ---------------------------------------------------------------------------

class TestStringLiteralPreservation:
    """``_preprocess_expr`` must not rewrite operators that appear inside
    quoted string literals — only operators in code position.
    """

    def test_double_amp_inside_single_quotes(self):
        out = _preprocess_expr("x == 'foo&&bar'")
        assert "'foo&&bar'" in out
        assert "foo and bar" not in out

    def test_double_pipe_inside_single_quotes(self):
        out = _preprocess_expr("x == 'a||b'")
        assert "'a||b'" in out
        assert "a or b" not in out

    def test_bang_inside_single_quotes(self):
        out = _preprocess_expr("y == 'hello!'")
        assert "'hello!'" in out
        assert "not hello" not in out

    def test_contains_inside_single_quotes(self):
        # `contains` inside a string literal must not split the expression.
        out = _preprocess_expr("x == 'this contains stuff'")
        assert "'this contains stuff'" in out
        # No `in` rewrite — the original `==` stays put.
        assert out.startswith("x ==") or "x ==" in out

    def test_method_call_contains_not_munged(self):
        # `foo.contains(bar)` is a method-style call — preprocessor must
        # leave it intact (no LHS-RHS split).
        out = _preprocess_expr("foo.contains(bar)")
        assert "foo.contains(bar)" in out

    def test_double_quotes_also_preserved(self):
        out = _preprocess_expr('x == "foo&&bar"')
        assert '"foo&&bar"' in out

    def test_escaped_quote_inside_string(self):
        # \' inside a single-quoted string must NOT close the literal.
        out = _preprocess_expr(r"x == 'it\'s && fine'")
        # Inside the literal `&&` stays put.
        assert "&&" in out

    def test_eval_string_literal_with_amp(self, state):
        # End-to-end: a literal RHS containing `&&` survives intact.
        state.step_results["x"] = StepResult(step_id="x", status="completed", output="foo&&bar")
        assert _eval_condition("x.output == 'foo&&bar'", state) is True

    def test_eval_string_literal_with_bang(self, state):
        state.step_results["y"] = StepResult(step_id="y", status="completed", output="hello!")
        assert _eval_condition("y.output == 'hello!'", state) is True

    def test_eval_string_literal_with_contains_word(self, state):
        # The literal contains the word `contains` — must not parse-error.
        state.step_results["z"] = StepResult(
            step_id="z", status="completed", output="this contains stuff"
        )
        assert _eval_condition("z.output == 'this contains stuff'", state) is True


# ---------------------------------------------------------------------------
# Regression: dunder/sentinel leak protection
# ---------------------------------------------------------------------------

class TestDunderLeakProtection:
    """Dunder-prefixed and leading-underscore keys must NEVER be reachable
    via simpleeval names lookup or ${...} interpolation. Defense in depth
    so a malicious agent JSON output can't exfiltrate Python internals.
    """

    def test_dunder_key_not_in_eval_names(self, state):
        # An agent step put `__class__` into output_data (perhaps via JSON
        # extraction). It must NOT be reachable as `step.output_data.__class__`.
        state.step_results["evil"] = StepResult(
            step_id="evil",
            status="completed",
            output="",
            output_data={"__class__": "pwn", "safe_field": "ok"},
        )
        # safe_field works
        assert _eval_condition("evil.output_data.safe_field == 'ok'", state) is True
        # __class__ is not reachable — eval fails, _eval_condition returns False
        assert _eval_condition("evil.output_data.__class__ == 'pwn'", state) is False

    def test_dunder_key_not_in_interpolation(self, state):
        from operator_mcp.workflow.executor import interpolate

        state.step_results["evil"] = StepResult(
            step_id="evil",
            status="completed",
            output="",
            output_data={"__class__": "pwn", "ok": "yes"},
        )
        # Safe key resolves
        assert interpolate("${evil.output_data.ok}", state) == "yes"
        # Dunder key does NOT — placeholder is left unresolved.
        out = interpolate("${evil.output_data.__class__}", state)
        assert "pwn" not in out
        assert "${evil.output_data.__class__}" == out

    def test_matched_goto_not_leaked_via_output_data(self, state):
        # Run a conditional and confirm the matched-goto sentinel does NOT
        # appear in StepResult.output_data (it now lives in state.conditional_routes).
        step = StepDef(
            id="gate",
            type=StepType.CONDITIONAL,
            conditional=ConditionalStepConfig(branches=[
                ConditionalBranch(condition="default", goto="next"),
            ]),
        )
        result = _exec_conditional(step, state)
        assert "__matched_goto__" not in result.output_data
        assert state.conditional_routes["gate"] == "next"
        # And it must not be reachable via interpolation.
        from operator_mcp.workflow.executor import interpolate
        state.step_results["gate"] = result
        out = interpolate("${gate.output_data.__matched_goto__}", state)
        assert "next" not in out


# ---------------------------------------------------------------------------
# Regression: lower()/upper() registered as safe functions
# ---------------------------------------------------------------------------

class TestSafeFunctions:
    def test_lower_function(self, state):
        state.step_results["r"] = StepResult(
            step_id="r", status="completed", output="Approved"
        )
        assert _eval_condition("'approve' in lower(r.output)", state) is True

    def test_upper_function(self, state):
        state.step_results["r"] = StepResult(
            step_id="r", status="completed", output="approved"
        )
        assert _eval_condition("'APPROVE' in upper(r.output)", state) is True


# ---------------------------------------------------------------------------
# Regression: quantum-soul-production-room approval gate
# ---------------------------------------------------------------------------

class TestQuantumSoulApprovalGate:
    """The quantum-soul YAML's approval_gate now uses conditional.branches
    with `lower()` for case-insensitive matching. Verify both branches.
    """

    def _gate_step(self) -> StepDef:
        return StepDef(
            id="approval_gate",
            type=StepType.CONDITIONAL,
            conditional=ConditionalStepConfig(branches=[
                ConditionalBranch(
                    condition="'approve' in lower(review_approval.output)",
                    goto="publish_episode",
                ),
                ConditionalBranch(condition="default", goto="revision_producer"),
            ]),
        )

    def test_approved_routes_to_publish(self):
        s = WorkflowState(workflow_name="qs", run_id="r")
        s.step_results["review_approval"] = StepResult(
            step_id="review_approval", status="completed", output="Approved"
        )
        _exec_conditional(self._gate_step(), s)
        assert s.conditional_routes["approval_gate"] == "publish_episode"

    def test_rejected_routes_to_revision(self):
        s = WorkflowState(workflow_name="qs", run_id="r")
        s.step_results["review_approval"] = StepResult(
            step_id="review_approval", status="completed", output="rejected, please redo"
        )
        _exec_conditional(self._gate_step(), s)
        assert s.conditional_routes["approval_gate"] == "revision_producer"

    def test_lowercase_approve_routes_to_publish(self):
        s = WorkflowState(workflow_name="qs", run_id="r")
        s.step_results["review_approval"] = StepResult(
            step_id="review_approval", status="completed", output="approve this episode"
        )
        _exec_conditional(self._gate_step(), s)
        assert s.conditional_routes["approval_gate"] == "publish_episode"


# ---------------------------------------------------------------------------
# Regression: MAX_POWER cap
# ---------------------------------------------------------------------------

class TestMaxPowerCap:
    def test_huge_exponent_blocked(self, state):
        # 2**100000 would normally chew CPU/RAM. Our evaluator caps MAX_POWER
        # at 1000, so simpleeval raises and _eval_condition returns False.
        assert _eval_condition("2 ** 100000 > 0", state) is False

    def test_safe_exponent_works(self, state):
        # Within the cap — works fine.
        assert _eval_condition("2 ** 10 == 1024", state) is True


# ---------------------------------------------------------------------------
# Regression: hyphenated step IDs in expressions
#
# Step IDs like ``zeroclaw-resolve`` are not valid Python identifiers. AST
# parses ``zeroclaw-resolve.output`` as ``zeroclaw - resolve.output`` —
# subtraction with two undefined names — and the prior code silently swallowed
# the NameError, making EVERY conditional that referenced a hyphenated step ID
# fall through to the default branch. The fix registers an underscored alias
# in the names dict and rewrites bare references to use it.
# ---------------------------------------------------------------------------

class TestHyphenatedStepIds:
    @pytest.fixture
    def hyphen_state(self) -> WorkflowState:
        s = WorkflowState(workflow_name="t", run_id="r")
        s.step_results["zeroclaw-resolve"] = StepResult(
            step_id="zeroclaw-resolve",
            status="completed",
            output="ok",
            output_data={"found": True, "matched_kref": "kref://x/y/z"},
        )
        s.step_results["openclaw_resolve"] = StepResult(
            step_id="openclaw_resolve",
            status="completed",
            output="ok",
            output_data={"found": True},
        )
        s.step_results["x"] = StepResult(step_id="x", status="completed", output="x-val")
        return s

    def test_hyphenated_step_id_dotted_access_works(self, hyphen_state):
        assert _eval_condition(
            "zeroclaw-resolve.output_data.found == True", hyphen_state
        ) is True

    def test_hyphenated_step_id_in_value_expr(self, hyphen_state):
        # `value:` expressions go through _eval_branch_value → _eval_expression.
        out = _eval_branch_value(
            "zeroclaw-resolve.output_data.matched_kref", hyphen_state
        )
        assert out == "kref://x/y/z"

    def test_hyphenated_id_inside_string_literal_preserved(self, hyphen_state):
        # The literal ``'zeroclaw-resolve'`` is a string, not a name reference.
        # Sanitization must NOT touch it (otherwise ``x.output == 'zeroclaw_resolve'``
        # would never match the actual step name).
        hyphen_state.step_results["x"] = StepResult(
            step_id="x", status="completed", output="zeroclaw-resolve"
        )
        assert _eval_condition("x.output == 'zeroclaw-resolve'", hyphen_state) is True
        assert _eval_condition("x.output == 'zeroclaw_resolve'", hyphen_state) is False

    def test_underscored_step_id_still_works_unchanged(self, hyphen_state):
        # Non-hyphenated IDs remain untouched by the rewrite.
        assert _eval_condition(
            "openclaw_resolve.output_data.found == True", hyphen_state
        ) is True

    def test_mixed_hyphen_and_underscore_in_compound_expression(self, hyphen_state):
        # Both forms in one expression. Note: _preprocess_expr translates
        # ``AND`` to ``and`` only via the lowercase ``&&`` path; this test
        # uses Python-form ``and`` (which already works) to keep it focused
        # on the hyphen-sanitization guarantee.
        assert _eval_condition(
            "zeroclaw-resolve.output_data.found and openclaw_resolve.output_data.found",
            hyphen_state,
        ) is True
