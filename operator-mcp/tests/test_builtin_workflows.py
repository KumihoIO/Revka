"""Structural tests for built-in workflow YAML files.

These tests pin built-in workflows against the schema + validator so a
schema change that breaks a shipped workflow is caught at test time, not
at first run. No agents, no SMTP, no Kumiho writes — pure structural
validation.
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys

import pytest
import yaml

from operator_mcp import canon_ontology
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


# ---------------------------------------------------------------------------
# Ontology-aware graph traversal (canonworks ontology workflows PR).
# ---------------------------------------------------------------------------


def _kumiho_context_edge_types(yaml_path: str) -> dict[str, set[str]]:
    """Return {step_id: set(traversal.edge_types)} for every kumiho_context step.

    Parsed straight from the raw YAML so the assertion pins the shipped edge
    lists, independent of how the loader models a kumiho_context step.
    """
    with open(yaml_path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    out: dict[str, set[str]] = {}
    for step in doc.get("steps", []) or []:
        if step.get("type") != "kumiho_context":
            continue
        traversal = (step.get("kumiho") or {}).get("traversal") or {}
        edge_types = [str(e).strip() for e in (traversal.get("edge_types") or []) if str(e).strip()]
        out[str(step.get("id"))] = set(edge_types)
    return out


@pytest.mark.parametrize(
    "yaml_path",
    [_CANONWORKS_EPISODE_PATH, _CANONWORKS_SYNC_PATH],
    ids=lambda p: os.path.basename(p),
)
def test_canonworks_kumiho_context_traverses_full_ontology(yaml_path: str) -> None:
    """Ontology drift guard (load-bearing).

    Every character relationship type and structural edge type declared in
    ``operator_mcp.canon_ontology`` must appear literally in every
    ``kumiho_context`` step's ``traversal.edge_types`` in both builtin
    workflows. The expected set is DERIVED FROM the ontology module (imported),
    never a copied literal — so adding a vocabulary type without teaching the
    workflows to traverse it fails here.
    """
    ontology_edge_types = set(canon_ontology.relationship_type_names()) | set(
        canon_ontology.structural_edge_names()
    )
    # Sanity: the ontology registry is non-trivial (guards an empty-import fluke).
    assert "RIVAL_OF" in ontology_edge_types
    assert "INVOLVES" in ontology_edge_types

    steps = _kumiho_context_edge_types(yaml_path)
    assert steps, (
        f"{os.path.basename(yaml_path)} has no kumiho_context steps to check — "
        f"the drift guard would be vacuous."
    )
    for step_id, edge_types in steps.items():
        missing = ontology_edge_types - edge_types
        assert not missing, (
            f"{os.path.basename(yaml_path)} kumiho_context step '{step_id}' is "
            f"missing ontology edge types from traversal.edge_types: "
            f"{sorted(missing)}. Every canon_ontology relationship + structural "
            f"edge type must be traversable (exact string match)."
        )


def _project_config_python_code(yaml_path: str) -> str:
    with open(yaml_path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    for step in doc.get("steps", []) or []:
        if step.get("id") == "project-config":
            code = (step.get("python") or {}).get("code")
            assert code, f"project-config step in {yaml_path} has no python.code"
            return code
    raise AssertionError(f"no project-config step in {yaml_path}")


def _run_project_config(code: str, project_config: dict) -> dict:
    """Execute the project-config python.code block the way the executor does.

    The step reads a JSON payload on stdin and writes a JSON dict on stdout.
    """
    payload = {
        "args": {"project_config_yaml": json.dumps(project_config)},
        "context": {"inputs": {}},
    }
    proc = subprocess.run(
        [sys.executable, "-c", code],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert proc.returncode == 0, (
        f"project-config step exited {proc.returncode}: {proc.stderr}"
    )
    return json.loads(proc.stdout)


@pytest.mark.parametrize(
    "yaml_path",
    [_CANONWORKS_EPISODE_PATH, _CANONWORKS_SYNC_PATH],
    ids=lambda p: os.path.basename(p),
)
def test_canonworks_project_config_echoes_ontology_section(yaml_path: str) -> None:
    """A config WITH an ontology section is echoed by the project-config step."""
    code = _project_config_python_code(yaml_path)
    project_config = {
        "canon_project": {
            "project": "GlassCity",
            "title": "Glass City",
            "krefs": {
                "canon_ontology": "kref://GlassCity/CanonRules/canon-ontology.canon-ontology",
            },
            "ontology": {
                "version": "1",
                "kref": "kref://GlassCity/CanonRules/canon-ontology.canon-ontology",
                "character_edge_types": ["RELATED_TO", "RIVAL_OF", "ALLY_OF"],
                "structural_edge_types": ["APPEARS_IN", "INVOLVES"],
            },
        }
    }
    out = _run_project_config(code, project_config)
    assert out["ontology_version"] == "1"
    assert (
        out["canon_ontology_kref"]
        == "kref://GlassCity/CanonRules/canon-ontology.canon-ontology"
    )
    assert out["ontology_character_edge_types_text"] == "RELATED_TO, RIVAL_OF, ALLY_OF"
    assert out["ontology_structural_edge_types_text"] == "APPEARS_IN, INVOLVES"


@pytest.mark.parametrize(
    "yaml_path",
    [_CANONWORKS_EPISODE_PATH, _CANONWORKS_SYNC_PATH],
    ids=lambda p: os.path.basename(p),
)
def test_canonworks_project_config_ontology_fallbacks_match_canon_ontology(yaml_path: str) -> None:
    """A pre-ontology config (no ontology section) gets fallback defaults, and
    the hardcoded fallback vocabulary matches ``operator_mcp.canon_ontology``.

    This is the sync assert: it makes the YAML's hardcoded fallback lists drift
    from the ontology module impossible without a test failure.
    """
    code = _project_config_python_code(yaml_path)
    project_config = {
        "canon_project": {
            "project": "GlassCity",
            "title": "Glass City",
        }
    }
    out = _run_project_config(code, project_config)
    assert out["ontology_version"] == canon_ontology.ONTOLOGY_VERSION
    assert (
        out["canon_ontology_kref"]
        == "kref://GlassCity/CanonRules/canon-ontology.canon-ontology"
    )
    assert out["ontology_character_edge_types_text"] == ", ".join(
        canon_ontology.relationship_type_names()
    )
    assert out["ontology_structural_edge_types_text"] == ", ".join(
        canon_ontology.structural_edge_names()
    )


def _kumiho_context_boost_edge_types(yaml_path: str) -> dict[str, set[str]]:
    """Return {step_id: set(ranking.boost_edge_types keys)} per kumiho_context step."""
    with open(yaml_path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    out: dict[str, set[str]] = {}
    for step in doc.get("steps", []) or []:
        if step.get("type") != "kumiho_context":
            continue
        ranking = (step.get("kumiho") or {}).get("ranking") or {}
        boosts = ranking.get("boost_edge_types") or {}
        out[str(step.get("id"))] = {str(k) for k in boosts.keys()}
    return out


@pytest.mark.parametrize(
    "yaml_path",
    [_CANONWORKS_EPISODE_PATH, _CANONWORKS_SYNC_PATH],
    ids=lambda p: os.path.basename(p),
)
def test_canonworks_context_does_not_boost_inert_character_relationship_edges(
    yaml_path: str,
) -> None:
    """``boost_edge_types`` must not reward inert character-relationship edges.

    ``ranking.boost_edge_types`` only rewards an edge type that surfaces as a
    ``via_edge`` in the assembled pack's ``edge_map``. In the graph
    ``canonworks_init`` builds, every character is reached via ``APPEARS_IN``
    from the depth-0 series-bible seed before its own relationship edges are
    examined, so character-to-character relationship edges (``RIVAL_OF``,
    ``ALLY_OF``, ``BETRAYED``, ...) never enter ``edge_map`` and a boost on them
    is inert. Structural edges (``APPEARS_IN`` / ``INVOLVES`` / ``BELONGS_TO`` /
    ``FORESHADOWS``) do surface and may be boosted. This guard keeps an inert
    relationship boost from being (re-)added; relationship-kind context is
    conveyed via the relationship-map artifact, not a via-edge boost.
    """
    relationship_types = set(canon_ontology.relationship_type_names())
    steps = _kumiho_context_boost_edge_types(yaml_path)
    assert steps, f"{os.path.basename(yaml_path)} has no kumiho_context steps"
    for step_id, boosts in steps.items():
        inert = boosts & relationship_types
        assert not inert, (
            f"{os.path.basename(yaml_path)} kumiho_context step '{step_id}' "
            f"boosts character-relationship ontology edges that never surface as "
            f"via_edges in the canonworks_init graph (inert boosts): "
            f"{sorted(inert)}. Remove them — relationship context flows through "
            f"the relationship-map artifact."
        )
