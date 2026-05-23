"""Tests for the agent-unused-depends_on validator rule.

The Architect repeatedly generated synthesize/combine steps with correct
``depends_on`` but a prompt that didn't reference any of those upstream
outputs or artifact paths (e.g. ``"Combine the two upstream research reports."`` with
``depends_on: [research_a, research_b]``). The runtime then started the
agent with no actual content from upstream. PR #170's auto-derive only
fires when references already exist, so the inverse case slips through.

This rule errors (not warns) so ``propose_workflow_yaml`` rejects the
proposal and the LLM is forced to add a reference such as
``${X.output_data.artifact_path}``.
"""
from __future__ import annotations

from operator_mcp.workflow.loader import load_workflow_from_dict
from operator_mcp.workflow.validator import validate_workflow


def _wf(steps: list[dict]) -> dict:
    return {"name": "test-wf", "steps": steps}


def _has_unused_dep_error(result, step_id: str, dep: str) -> bool:
    needle = f"Step '{step_id}' depends on '{dep}'"
    return any(
        e.step_id == step_id and needle in e.message for e in result.errors
    )


class TestAgentUnusedDependsOn:
    def test_referenced_dep_is_ok(self):
        """Agent step with depends_on=[a] and prompt referencing
        ${a.output} → no error."""
        wf = load_workflow_from_dict(_wf([
            {"id": "a", "type": "agent", "agent": {"prompt": "do A"}},
            {
                "id": "b",
                "type": "agent",
                "depends_on": ["a"],
                "agent": {"prompt": "extend ${a.output}"},
            },
        ]))
        result = validate_workflow(wf)
        assert not _has_unused_dep_error(result, "b", "a")

    def test_partially_unused_dep_errors_on_missing_one(self):
        """depends_on=[a, b] with prompt mentioning only ${a.output} →
        error mentioning b, no error for a."""
        wf = load_workflow_from_dict(_wf([
            {"id": "a", "type": "agent", "agent": {"prompt": "A"}},
            {"id": "b", "type": "agent", "agent": {"prompt": "B"}},
            {
                "id": "synth",
                "type": "agent",
                "depends_on": ["a", "b"],
                "agent": {"prompt": "summarize ${a.output}"},
            },
        ]))
        result = validate_workflow(wf)
        assert _has_unused_dep_error(result, "synth", "b")
        assert not _has_unused_dep_error(result, "synth", "a")

    def test_completely_unreferenced_deps_error(self):
        """depends_on=[a] with no ${...} references in the prompt →
        error mentioning a."""
        wf = load_workflow_from_dict(_wf([
            {"id": "a", "type": "agent", "agent": {"prompt": "A"}},
            {
                "id": "b",
                "type": "agent",
                "depends_on": ["a"],
                "agent": {"prompt": "do something else"},
            },
        ]))
        result = validate_workflow(wf)
        assert _has_unused_dep_error(result, "b", "a")
        assert not result.valid

    def test_empty_depends_on_no_check(self):
        """Agent step with depends_on=[] → no error from this rule."""
        wf = load_workflow_from_dict(_wf([
            {"id": "a", "type": "agent", "agent": {"prompt": "do A"}},
        ]))
        result = validate_workflow(wf)
        assert not any(
            "depends on" in e.message and e.step_id == "a"
            for e in result.errors
        )

    def test_non_agent_step_not_checked(self):
        """A shell step with depends_on=[a] and unused → no error
        (rule is agent-only; non-agent steps may use depends_on purely
        for ordering)."""
        wf = load_workflow_from_dict(_wf([
            {"id": "a", "type": "agent", "agent": {"prompt": "A"}},
            {
                "id": "cleanup",
                "type": "shell",
                "depends_on": ["a"],
                "shell": {"command": "rm -rf /tmp/scratch"},
            },
        ]))
        result = validate_workflow(wf)
        assert not _has_unused_dep_error(result, "cleanup", "a")

    def test_output_data_field_ref_counts(self):
        """${a.output_data.field} counts as a reference to a → no error."""
        wf = load_workflow_from_dict(_wf([
            {"id": "a", "type": "agent", "agent": {"prompt": "A"}},
            {
                "id": "b",
                "type": "agent",
                "depends_on": ["a"],
                "agent": {"prompt": "consume ${a.output_data.summary}"},
            },
        ]))
        result = validate_workflow(wf)
        assert not _has_unused_dep_error(result, "b", "a")

    def test_artifact_path_ref_counts(self):
        """${a.output_data.artifact_path} is the preferred full-context handoff."""
        wf = load_workflow_from_dict(_wf([
            {"id": "a", "type": "agent", "agent": {"prompt": "A"}},
            {
                "id": "b",
                "type": "agent",
                "depends_on": ["a"],
                "agent": {
                    "prompt": (
                        "Read upstream context from "
                        "${a.output_data.artifact_path}"
                    )
                },
            },
        ]))
        result = validate_workflow(wf)
        assert not _has_unused_dep_error(result, "b", "a")

    def test_synthesize_combine_pattern_from_user_session(self):
        """Reproduces the concrete failure from the user's session: a
        synthesize/combine agent with correct depends_on whose prompt
        does not actually pull in any upstream output. Must reject."""
        wf = load_workflow_from_dict(_wf([
            {"id": "research_construct_claude", "type": "agent",
             "agent": {"prompt": "research construct"}},
            {"id": "research_simai_codex", "type": "agent",
             "agent": {"prompt": "research simai"}},
            {
                "id": "synthesize_report_claude",
                "type": "agent",
                "depends_on": [
                    "research_construct_claude",
                    "research_simai_codex",
                ],
                "agent": {
                    "agent_type": "claude",
                    "role": "researcher",
                    "prompt": (
                        "Write a final comparative research report using "
                        "the two upstream research"
                    ),
                    "timeout": 900,
                },
            },
        ]))
        result = validate_workflow(wf)
        assert not result.valid
        assert _has_unused_dep_error(
            result, "synthesize_report_claude", "research_construct_claude"
        )
        assert _has_unused_dep_error(
            result, "synthesize_report_claude", "research_simai_codex"
        )
