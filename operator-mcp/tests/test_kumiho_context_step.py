from __future__ import annotations

from typing import Any

import pytest

from operator_mcp.workflow.executor import _exec_kumiho_context
from operator_mcp.workflow.schema import (
    KumihoContextConfig,
    KumihoFiltersConfig,
    KumihoLockConfig,
    KumihoOutputConfig,
    KumihoSeedConfig,
    KumihoTraversalConfig,
    StepDef,
    StepResult,
    StepType,
    WorkflowState,
)


def _state(inputs: dict[str, Any] | None = None, results: dict[str, Any] | None = None) -> WorkflowState:
    return WorkflowState(
        workflow_name="manghan-episode-factory",
        run_id="run-1",
        inputs=dict(inputs or {}),
        step_results=dict(results or {}),
    )


class FakeKumihoSDK:
    _available = True

    def __init__(self) -> None:
        self.items = {
            "kref://ManghanDev/CharacterStates/handoyoon.character-state": {
                "kref": "kref://ManghanDev/CharacterStates/handoyoon.character-state",
                "name": "handoyoon.character-state",
                "item_name": "handoyoon.character-state",
                "kind": "character-state",
                "metadata": {},
                "deprecated": False,
            },
            "kref://ManghanDev/CanonRules/handoyoon-money.canon-rule": {
                "kref": "kref://ManghanDev/CanonRules/handoyoon-money.canon-rule",
                "name": "handoyoon-money.canon-rule",
                "item_name": "handoyoon-money.canon-rule",
                "kind": "canon-rule",
                "metadata": {},
                "deprecated": False,
            },
            "kref://ManghanDev/Episodes/ep-001.webnovel-episode": {
                "kref": "kref://ManghanDev/Episodes/ep-001.webnovel-episode",
                "name": "ep-001.webnovel-episode",
                "item_name": "ep-001.webnovel-episode",
                "kind": "webnovel-episode",
                "metadata": {},
                "deprecated": False,
            },
            "kref://ManghanDev/Bundles/manghan-main-canon.bundle": {
                "kref": "kref://ManghanDev/Bundles/manghan-main-canon.bundle",
                "name": "manghan-main-canon",
                "item_name": "manghan-main-canon",
                "kind": "bundle",
                "metadata": {},
                "deprecated": False,
            },
        }
        self.revisions = {
            "kref://ManghanDev/CharacterStates/handoyoon.character-state?r=12": {
                "kref": "kref://ManghanDev/CharacterStates/handoyoon.character-state?r=12",
                "item_kref": "kref://ManghanDev/CharacterStates/handoyoon.character-state",
                "number": 12,
                "tags": ["current"],
                "metadata": {"summary": "Han Doyoon conditionally trusts Operator."},
                "created_at": "2026-05-01T00:00:00+00:00",
                "deprecated": False,
                "default_artifact": "state.md",
            },
            "kref://ManghanDev/CanonRules/handoyoon-money.canon-rule?r=1": {
                "kref": "kref://ManghanDev/CanonRules/handoyoon-money.canon-rule?r=1",
                "item_kref": "kref://ManghanDev/CanonRules/handoyoon-money.canon-rule",
                "number": 1,
                "tags": ["published"],
                "metadata": {"summary": "No immediate large profit confirmation."},
                "created_at": "2026-04-01T00:00:00+00:00",
                "deprecated": False,
                "default_artifact": "rule.md",
            },
            "kref://ManghanDev/Episodes/ep-001.webnovel-episode?r=1": {
                "kref": "kref://ManghanDev/Episodes/ep-001.webnovel-episode?r=1",
                "item_kref": "kref://ManghanDev/Episodes/ep-001.webnovel-episode",
                "number": 1,
                "tags": ["production-ready"],
                "metadata": {"episode_number": "1"},
                "created_at": "2026-05-02T00:00:00+00:00",
                "deprecated": False,
                "default_artifact": "episode.md",
            },
        }
        self.revisions_by_item_tag = {
            ("kref://ManghanDev/CharacterStates/handoyoon.character-state", "current"):
                self.revisions["kref://ManghanDev/CharacterStates/handoyoon.character-state?r=12"],
            ("kref://ManghanDev/CanonRules/handoyoon-money.canon-rule", "published"):
                self.revisions["kref://ManghanDev/CanonRules/handoyoon-money.canon-rule?r=1"],
            ("kref://ManghanDev/Episodes/ep-001.webnovel-episode", "production-ready"):
                self.revisions["kref://ManghanDev/Episodes/ep-001.webnovel-episode?r=1"],
        }
        self.bundle_members = {
            "kref://ManghanDev/Bundles/manghan-main-canon.bundle": [
                {"item_kref": "kref://ManghanDev/CharacterStates/handoyoon.character-state"},
            ]
        }
        self.edges = {
            "kref://ManghanDev/CharacterStates/handoyoon.character-state?r=12": [
                {
                    "source_kref": "kref://ManghanDev/CharacterStates/handoyoon.character-state?r=12",
                    "target_kref": "kref://ManghanDev/CanonRules/handoyoon-money.canon-rule?r=1",
                    "edge_type": "BLOCKS",
                    "metadata": {},
                }
            ],
            "kref://ManghanDev/CanonRules/handoyoon-money.canon-rule?r=1": [
                {
                    "source_kref": "kref://ManghanDev/CharacterStates/handoyoon.character-state?r=12",
                    "target_kref": "kref://ManghanDev/CanonRules/handoyoon-money.canon-rule?r=1",
                    "edge_type": "BLOCKS",
                    "metadata": {},
                }
            ],
        }
        self.artifacts = {
            "kref://ManghanDev/CharacterStates/handoyoon.character-state?r=12": [
                {
                    "name": "state.md",
                    "kref": "artifact://state",
                    "location": "",
                    "metadata": {"summary": "Han Doyoon has conditional trust in Operator."},
                }
            ],
            "kref://ManghanDev/CanonRules/handoyoon-money.canon-rule?r=1": [
                {
                    "name": "rule.md",
                    "kref": "artifact://rule",
                    "location": "",
                    "metadata": {"summary": "Do not confirm immediate large profit."},
                }
            ],
        }

    async def search_items(self, context: str = "", name: str = "", kind: str = "", include_metadata: bool = False):
        values = list(self.items.values())
        if context:
            values = [item for item in values if context in item["kref"]]
        if kind:
            values = [item for item in values if item["kind"] == kind]
        if name:
            values = [item for item in values if name in {item["name"], item["item_name"]}]
        return values

    async def get_bundle_members(self, bundle_kref: str):
        return list(self.bundle_members.get(bundle_kref, []))

    async def get_item(self, item_kref: str):
        return self.items.get(item_kref)

    async def get_revision(self, revision_kref: str):
        return self.revisions.get(revision_kref)

    async def get_revision_by_tag(self, item_kref: str, tag: str):
        return self.revisions_by_item_tag.get((item_kref, tag))

    async def get_latest_revision(self, item_kref: str, tag: str = "published"):
        return self.revisions_by_item_tag.get((item_kref, tag))

    async def get_edges(self, rev_kref: str, direction: int = 0):
        return list(self.edges.get(rev_kref, []))

    async def search(self, query: str, context: str = "", kind: str = "", include_revision_metadata: bool = False):
        return []

    async def get_artifacts(self, revision_kref: str):
        return list(self.artifacts.get(revision_kref, []))

    async def get_item_revisions(self, item_kref: str, include_metadata: bool = True):
        return [rev for rev in self.revisions.values() if rev["item_kref"] == item_kref]


@pytest.fixture
def fake_sdk(monkeypatch):
    sdk = FakeKumihoSDK()
    import operator_mcp.operator_mcp as op_mod
    monkeypatch.setattr(op_mod, "KUMIHO_SDK", sdk, raising=False)
    return sdk


@pytest.mark.asyncio
async def test_kumiho_context_compiles_locked_pack_from_bundle_and_edges(fake_sdk):
    cfg = KumihoContextConfig(
        project="ManghanDev",
        mode="graph_augmented_context",
        seed=KumihoSeedConfig(bundles=["manghan-main-canon"]),
        traversal=KumihoTraversalConfig(max_depth=1, direction="out", edge_types=["BLOCKS"]),
        filters=KumihoFiltersConfig(
            include_kinds=["character-state", "canon-rule"],
            max_items=10,
        ),
        lock=KumihoLockConfig(tag_preference=["current", "published", "latest"]),
        output=KumihoOutputConfig(format="episode_context_pack"),
    )
    step = StepDef(id="episode-context", type=StepType.KUMIHO_CONTEXT, kumiho=cfg)

    result = await _exec_kumiho_context(step, _state())

    assert result.status == "completed"
    assert result.output_data["found"] is True
    assert result.output_data["locked_manifest"] == result.output_data["context_pack"]["locked_manifest"]
    source_krefs = result.output_data["source_krefs"]
    assert "kref://ManghanDev/CharacterStates/handoyoon.character-state?r=12" in source_krefs
    assert "kref://ManghanDev/CanonRules/handoyoon-money.canon-rule?r=1" in source_krefs
    assert result.output_data["edge_map"][0]["edge_type"] == "BLOCKS"
    assert result.output_data["conflict_warnings"][0]["type"] == "blocks_edge"
    assert "## Locked Manifest" in result.output_data["artifact_content"]
    assert result.output == result.output_data["artifact_content"][:6000]


@pytest.mark.asyncio
async def test_kumiho_context_interpolates_seed_krefs_and_queries(fake_sdk):
    state = _state(
        inputs={"goal": "operator trust"},
        results={
            "latest": StepResult(
                step_id="latest",
                status="completed",
                output_data={
                    "kref": "kref://ManghanDev/Episodes/ep-001.webnovel-episode?r=1",
                },
            )
        },
    )
    cfg = KumihoContextConfig(
        project="ManghanDev",
        mode="graph_augmented_context",
        seed=KumihoSeedConfig(
            krefs=["${latest.output_data.kref}"],
            queries=["${inputs.goal}"],
        ),
        traversal=KumihoTraversalConfig(max_depth=0),
        filters=KumihoFiltersConfig(include_kinds=["webnovel-episode"]),
        lock=KumihoLockConfig(tag_preference=["production-ready", "latest"]),
    )
    step = StepDef(id="episode-context", type=StepType.KUMIHO_CONTEXT, kumiho=cfg)

    result = await _exec_kumiho_context(step, state)

    assert result.status == "completed"
    assert result.input_data["seed_kref_count"] == 1
    assert result.input_data["seed_query_count"] == 1
    assert result.output_data["source_krefs"] == [
        "kref://ManghanDev/Episodes/ep-001.webnovel-episode?r=1"
    ]


@pytest.mark.asyncio
async def test_kumiho_context_reports_missing_required_bundle(fake_sdk):
    cfg = KumihoContextConfig(
        project="ManghanDev",
        mode="bundle_context",
        seed=KumihoSeedConfig(bundles=["missing-bundle"]),
    )
    step = StepDef(id="ctx", type=StepType.KUMIHO_CONTEXT, kumiho=cfg)

    result = await _exec_kumiho_context(step, _state())

    assert result.status == "completed"
    assert result.output_data["found"] is False
    assert result.output_data["error"]["type"] == "missing_bundle"
    assert result.output_data["locked_manifest"] == []
