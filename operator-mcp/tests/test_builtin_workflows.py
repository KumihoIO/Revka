"""Structural tests for built-in workflow YAML files.

These tests pin built-in workflows against the schema + validator so a
schema change that breaks a shipped workflow is caught at test time, not
at first run. No agents, no SMTP, no Kumiho writes — pure structural
validation.
"""
from __future__ import annotations

import glob
import os

import pytest

from operator_mcp.workflow.loader import load_workflow_from_yaml
from operator_mcp.workflow.schema import StepType
from operator_mcp.workflow.validator import validate_workflow


_BUILTINS_DIR = os.path.join(
    os.path.dirname(__file__),
    "..",
    "operator_mcp",
    "workflow",
    "builtins",
)


# ---------------------------------------------------------------------------
# smoke-test-all-steps — exercises every StepType
# ---------------------------------------------------------------------------

_SMOKE_TEST_PATH = os.path.join(_BUILTINS_DIR, "smoke-test-all-steps.yaml")
_GITHUB_ISSUE_RESOLVER_PATH = os.path.join(_BUILTINS_DIR, "github-issue-resolver.yaml")


@pytest.fixture(scope="module")
def smoke_workflow():
    """Parse the smoke-test workflow once per module."""
    return load_workflow_from_yaml(_SMOKE_TEST_PATH)


class TestSmokeTestAllSteps:
    """The smoke-test workflow loads cleanly and covers every step type."""

    def test_loads_without_errors(self, smoke_workflow):
        assert smoke_workflow.name == "smoke-test-all-steps"
        assert "smoke-test" in smoke_workflow.tags

    def test_validator_clean(self, smoke_workflow):
        """Zero validation errors. Warnings are allowed (advisory)."""
        result = validate_workflow(smoke_workflow)
        assert result.valid, (
            "smoke-test-all-steps failed validation:\n"
            + "\n".join(f"  - {e}" for e in result.errors)
        )

    def test_covers_every_step_type(self, smoke_workflow):
        """Every StepType enum value appears at least once.

        This is the load-bearing assertion for this workflow — its sole
        purpose is to exercise every dispatch path. A new StepType added
        to the enum without a corresponding step here is a bug.
        """
        covered = {s.type for s in smoke_workflow.steps}
        all_types = set(StepType)
        missing = all_types - covered
        assert not missing, (
            f"smoke-test-all-steps is missing StepType coverage for: "
            f"{sorted(t.value for t in missing)}. Add a step exercising "
            f"each missing type to operator_mcp/workflow/builtins/"
            f"smoke-test-all-steps.yaml."
        )

    def test_uses_canonical_conditional_branches(self, smoke_workflow):
        """Conditional steps must use the `branches` form (PR #217), not
        the legacy flat condition/on_true/on_false syntax."""
        for step in smoke_workflow.steps:
            if step.type == StepType.CONDITIONAL:
                assert step.conditional is not None, (
                    f"conditional step '{step.id}' has no conditional config"
                )
                assert step.conditional.branches, (
                    f"conditional step '{step.id}' has empty branches"
                )

    def test_uses_value_emission(self, smoke_workflow):
        """At least one conditional branch sets `value:` (PR #216)."""
        any_value_branch = False
        for step in smoke_workflow.steps:
            if step.type == StepType.CONDITIONAL and step.conditional:
                for branch in step.conditional.branches:
                    if branch.value:
                        any_value_branch = True
                        break
        assert any_value_branch, (
            "smoke-test should exercise conditional value emission "
            "(PR #216) — at least one branch must set `value:`."
        )

    def test_email_uses_dry_run(self, smoke_workflow):
        """Email steps in this smoke test must be dry-run only."""
        for step in smoke_workflow.steps:
            if step.type == StepType.EMAIL:
                assert step.email is not None
                assert step.email.dry_run is True, (
                    f"email step '{step.id}' must set dry_run: true so "
                    f"smoke runs don't actually send mail."
                )

    def test_resolve_is_fail_soft(self, smoke_workflow):
        """Resolve steps must use fail_if_missing: false so a clean
        install (no published entities) doesn't fail the workflow."""
        for step in smoke_workflow.steps:
            if step.type == StepType.RESOLVE:
                assert step.resolve is not None
                assert step.resolve.fail_if_missing is False, (
                    f"resolve step '{step.id}' must set fail_if_missing: "
                    f"false for the unattended smoke run."
                )

    def test_goto_has_termination_guard(self, smoke_workflow):
        """Goto steps must have a bounded max_iterations."""
        for step in smoke_workflow.steps:
            if step.type == StepType.GOTO:
                assert step.goto is not None
                assert 1 <= step.goto.max_iterations <= 5, (
                    f"goto step '{step.id}' max_iterations should be a "
                    f"small bounded number for the smoke test."
                )

    def test_human_steps_have_short_timeout(self, smoke_workflow):
        """Human gates must have short timeouts — unattended runs."""
        for step in smoke_workflow.steps:
            if step.type == StepType.HUMAN_INPUT:
                assert step.human_input is not None
                assert step.human_input.timeout <= 30, (
                    f"human_input '{step.id}' timeout too long for smoke"
                )
            if step.type == StepType.HUMAN_APPROVAL:
                assert step.human_approval is not None
                assert step.human_approval.timeout <= 30, (
                    f"human_approval '{step.id}' timeout too long for smoke"
                )


# ---------------------------------------------------------------------------
# Regression: every shipped built-in workflow must validate cleanly.
# Catches schema drift breaking any *.yaml under builtins (e.g. PR #216
# missed quantum-soul-production-room.yaml — this test would have caught it).
# ---------------------------------------------------------------------------

_ALL_BUILTIN_YAMLS = sorted(glob.glob(os.path.join(_BUILTINS_DIR, "*.yaml")))

_CANONWORKS_EPISODE_PATH = os.path.join(_BUILTINS_DIR, "canonworks-serial-episode-factory.yaml")
_CANONWORKS_SYNC_PATH = os.path.join(_BUILTINS_DIR, "canonworks-serial-canon-state-sync.yaml")

_CANONWORKS_EPISODE_STEPS = [
    "project-config",
    "latest-production-episode",
    "next-episode-info",
    "episode-context",
    "volume-canon-alignment",
    "relationship-pressure-plan",
    "opencrab-reference-builder",
    "episode-intent-planner",
    "episode-beat-planner",
    "episode-draft-writer",
    "episode-prose-reviser",
    "draft-canon-auditor",
    "episode-finalizer",
    "final-canon-auditor",
    "final-gate-router",
    "production-route-gate",
    "canon-patch-builder",
    "emit-final-episode",
    "production-emit-gate",
    "emit-canon-patch-candidate",
    "emit-context-pack",
    "production-output-gate",
    "update-output-bundles",
    "emit-blocked-episode",
    "blocked-output-gate",
    "update-blocked-bundle",
    "run-summary",
]

_CANONWORKS_SYNC_STEPS = [
    "project-config",
    "latest-production-episode",
    "sync-info",
    "canon-patch-candidate",
    "state-sync-context",
    "state-delta-context-lite",
    "state-delta-extractor",
    "state-delta-review",
    "current-snapshot-builder",
    "emit-character-state-snapshot",
    "emit-relationship-state-snapshot",
    "emit-timeline-progress-snapshot",
    "emit-storyline-progress-snapshot",
    "emit-foreshadow-progress-snapshot",
    "emit-post-episode-sync-report",
    "update-state-sync-bundles",
    "run-summary",
]


@pytest.mark.parametrize(
    "yaml_path",
    _ALL_BUILTIN_YAMLS,
    ids=lambda p: os.path.basename(p),
)
def test_builtin_workflow_validates(yaml_path: str) -> None:
    wf = load_workflow_from_yaml(yaml_path)
    result = validate_workflow(wf)
    assert result.valid, (
        f"{os.path.basename(yaml_path)} failed validation: "
        f"errors={result.errors} warnings={result.warnings}"
    )


def test_github_issue_resolver_matches_demo_run_contract() -> None:
    wf = load_workflow_from_yaml(_GITHUB_ISSUE_RESOLVER_PATH)

    assert wf.name == "github-issue-resolver"
    assert [step.id for step in wf.steps] == [
        "assess_issue",
        "google_agentops_preflight",
        "human_approval_gate_1",
        "deploy_coder_agent",
        "review_pr",
        "address_review_findings",
        "human_approval_gate_2",
        "merge_and_close",
    ]
    assert len(wf.triggers) == 1
    assert wf.triggers[0].on_kind == "github-issue"

    input_names = {input_def.name for input_def in wf.inputs}
    assert {"github_payload", "repo_name", "track3_a2a_url"} == input_names

    assess = wf.step_by_id("assess_issue")
    assert assess.agent.agent_type == "agy"
    assert assess.agent.output_fields == [
        "issue_number",
        "issue_title",
        "issue_body",
        "likely_files",
        "strategy",
        "risk_notes",
    ]

    deploy = wf.step_by_id("deploy_coder_agent")
    assert deploy.agent.agent_type == "agy"
    assert deploy.agent.output_fields == [
        "branch_name",
        "commit_sha",
        "pr_url",
        "files_changed",
        "test_summary",
        "clippy_summary",
        "google_agentops_evidence",
        "residual_risks",
    ]

    merge = wf.step_by_id("merge_and_close")
    assert merge.agent.agent_type == "agy"
    assert merge.agent.output_fields == [
        "merge_status",
        "issue_closed",
        "closing_comment_url",
        "merged_pr_url",
        "audit_summary",
    ]


def test_canonworks_episode_factory_preserves_generalized_example_contract() -> None:
    wf = load_workflow_from_yaml(_CANONWORKS_EPISODE_PATH)

    assert [step.id for step in wf.steps] == _CANONWORKS_EPISODE_STEPS
    assert wf.inputs[0].name == "project_config_yaml"
    assert wf.inputs[0].required is True


def test_canonworks_state_sync_preserves_generalized_example_contract() -> None:
    wf = load_workflow_from_yaml(_CANONWORKS_SYNC_PATH)

    assert [step.id for step in wf.steps] == _CANONWORKS_SYNC_STEPS
    assert wf.inputs[0].name == "project_config_yaml"
    assert wf.inputs[0].required is True


@pytest.mark.parametrize("yaml_path", [_CANONWORKS_EPISODE_PATH, _CANONWORKS_SYNC_PATH])
def test_canonworks_builtins_have_no_legacy_project_literals(yaml_path: str) -> None:
    with open(yaml_path, encoding="utf-8") as f:
        text = f.read()
    forbidden = [
        "ManghanDev",
        "manghan",
        "mg-ep",
        "StoryRoom",
        "storyroom",
        "cross-chronicle",
        "\ub9dd\ud55c \uac1c\ubc1c\uc790\ub294",
    ]

    for token in forbidden:
        assert token not in text


@pytest.mark.parametrize("yaml_path", [_CANONWORKS_EPISODE_PATH, _CANONWORKS_SYNC_PATH])
def test_canonworks_project_config_fallbacks_match_init_defaults(yaml_path: str) -> None:
    with open(yaml_path, encoding="utf-8") as f:
        text = f.read()

    assert "episode_name_prefix = first(naming.get('episode_name_prefix'), 'ep')" in text
    assert "RELATIONSHIP_MAP.md" in text
    assert "Roadmaps/long-arc.series-roadmap" in text
    assert "main.relationship-map.md" not in text
    assert "series-roadmap.series-roadmap" not in text
