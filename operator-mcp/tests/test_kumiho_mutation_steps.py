from __future__ import annotations

from typing import Any

import pytest

from operator_mcp.workflow.executor import (
    _exec_kumiho_bundle_update,
    _exec_kumiho_patch_apply,
)
from operator_mcp.workflow.schema import (
    KumihoBundleMemberConfig,
    KumihoBundleUpdateConfig,
    KumihoBundleUpdateEntryConfig,
    KumihoPatchApplyConfig,
    KumihoPatchApplyFlagsConfig,
    KumihoPatchApprovalConfig,
    KumihoPatchBundlePolicyConfig,
    StepDef,
    StepType,
    WorkflowState,
)


def _state() -> WorkflowState:
    return WorkflowState(
        workflow_name="manghan-canon-patch-apply",
        run_id="run-1",
        inputs={},
        step_results={},
    )


class FakeKumihoMutationSDK:
    _available = True

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {
            "kref://ManghanDev/Episodes/mg-ep-027.webnovel-episode": {
                "kref": "kref://ManghanDev/Episodes/mg-ep-027.webnovel-episode",
                "name": "mg-ep-027.webnovel-episode",
                "item_name": "mg-ep-027.webnovel-episode",
                "kind": "webnovel-episode",
            },
            "kref://ManghanDev/CharacterStates/handoyoon.character-state": {
                "kref": "kref://ManghanDev/CharacterStates/handoyoon.character-state",
                "name": "handoyoon.character-state",
                "item_name": "handoyoon.character-state",
                "kind": "character-state",
            },
            "kref://ManghanDev/Patches/mg-ep-027-canon-patch.canon-patch": {
                "kref": "kref://ManghanDev/Patches/mg-ep-027-canon-patch.canon-patch",
                "name": "mg-ep-027-canon-patch.canon-patch",
                "item_name": "mg-ep-027-canon-patch.canon-patch",
                "kind": "canon-patch",
            },
            "kref://ManghanDev/Bundles/manghan-production-episodes.bundle": {
                "kref": "kref://ManghanDev/Bundles/manghan-production-episodes.bundle",
                "name": "manghan-production-episodes",
                "item_name": "manghan-production-episodes",
                "kind": "bundle",
            },
            "kref://ManghanDev/Bundles/manghan-pending-canon-patches.bundle": {
                "kref": "kref://ManghanDev/Bundles/manghan-pending-canon-patches.bundle",
                "name": "manghan-pending-canon-patches",
                "item_name": "manghan-pending-canon-patches",
                "kind": "bundle",
            },
            "kref://ManghanDev/Bundles/manghan-applied-canon-patches.bundle": {
                "kref": "kref://ManghanDev/Bundles/manghan-applied-canon-patches.bundle",
                "name": "manghan-applied-canon-patches",
                "item_name": "manghan-applied-canon-patches",
                "kind": "bundle",
            },
            "kref://ManghanDev/Bundles/manghan-current-character-states.bundle": {
                "kref": "kref://ManghanDev/Bundles/manghan-current-character-states.bundle",
                "name": "manghan-current-character-states",
                "item_name": "manghan-current-character-states",
                "kind": "bundle",
            },
        }
        self.revisions: dict[str, dict[str, Any]] = {
            "kref://ManghanDev/Episodes/mg-ep-027.webnovel-episode?r=1": {
                "kref": "kref://ManghanDev/Episodes/mg-ep-027.webnovel-episode?r=1",
                "item_kref": "kref://ManghanDev/Episodes/mg-ep-027.webnovel-episode",
                "tags": ["production-ready"],
                "metadata": {},
            },
            "kref://ManghanDev/CharacterStates/handoyoon.character-state?r=1": {
                "kref": "kref://ManghanDev/CharacterStates/handoyoon.character-state?r=1",
                "item_kref": "kref://ManghanDev/CharacterStates/handoyoon.character-state",
                "tags": ["current"],
                "metadata": {"summary": "old state"},
            },
            "kref://ManghanDev/Patches/mg-ep-027-canon-patch.canon-patch?r=1": {
                "kref": "kref://ManghanDev/Patches/mg-ep-027-canon-patch.canon-patch?r=1",
                "item_kref": "kref://ManghanDev/Patches/mg-ep-027-canon-patch.canon-patch",
                "tags": ["candidate"],
                "metadata": {
                    "canon_patch": {
                        "patch_id": "mg-ep-027-canon-patch",
                        "patch_status": "candidate",
                        "proposed_revision_updates": {
                            "character_states": [
                                {
                                    "item_name": "handoyoon",
                                    "item_kind": "character-state",
                                    "previous_revision_kref": (
                                        "kref://ManghanDev/CharacterStates/"
                                        "handoyoon.character-state?r=1"
                                    ),
                                    "proposed_change_summary": "Trust advanced slightly.",
                                    "proposed_artifact_patch": "## Current State\n- Trust advanced.",
                                    "evidence_locator": "Ep.027 final scene",
                                }
                            ]
                        },
                        "proposed_edges": [
                            {
                                "from": "kref://ManghanDev/Episodes/mg-ep-027.webnovel-episode?r=1",
                                "edge_type": "UPDATES",
                                "to": (
                                    "kref://ManghanDev/CharacterStates/"
                                    "handoyoon.character-state?r=1"
                                ),
                                "reason": "Episode changes the current state.",
                                "evidence_locator": "Ep.027 final scene",
                            }
                        ],
                    }
                },
            },
        }
        self.bundle_members: dict[str, list[dict[str, str]]] = {
            "kref://ManghanDev/Bundles/manghan-production-episodes.bundle": [],
            "kref://ManghanDev/Bundles/manghan-pending-canon-patches.bundle": [
                {"item_kref": "kref://ManghanDev/Patches/mg-ep-027-canon-patch.canon-patch"}
            ],
            "kref://ManghanDev/Bundles/manghan-applied-canon-patches.bundle": [],
            "kref://ManghanDev/Bundles/manghan-current-character-states.bundle": [],
        }
        self.artifacts_by_revision: dict[str, list[dict[str, Any]]] = {}
        self.created_revisions: list[dict[str, Any]] = []
        self.created_edges: list[dict[str, Any]] = []
        self.artifacts: list[dict[str, Any]] = []
        self.tags_added: list[tuple[str, str]] = []
        self.tags_removed: list[tuple[str, str]] = []

    async def search_items(self, context: str = "", name: str = "", kind: str = "", include_metadata: bool = False):
        values = list(self.items.values())
        if context:
            values = [item for item in values if context in item["kref"]]
        if name:
            values = [item for item in values if item["name"] == name or item["item_name"] == name]
        if kind:
            values = [item for item in values if item["kind"] == kind]
        return values

    async def get_bundle_by_kref(self, kref: str):
        return self.items.get(kref)

    async def create_bundle(self, space_path: str, name: str, metadata: dict[str, str] | None = None):
        kref = f"kref://{space_path}/{name}.bundle"
        bundle = {"kref": kref, "name": name, "item_name": name, "kind": "bundle", "metadata": metadata or {}}
        self.items[kref] = bundle
        self.bundle_members[kref] = []
        return bundle

    async def get_bundle_members(self, bundle_kref: str):
        return list(self.bundle_members.get(bundle_kref, []))

    async def add_bundle_member(self, bundle_kref: str, item_kref: str):
        members = self.bundle_members.setdefault(bundle_kref, [])
        if any(member["item_kref"] == item_kref for member in members):
            return False
        members.append({"item_kref": item_kref})
        return True

    async def remove_bundle_member(self, bundle_kref: str, item_kref: str):
        members = self.bundle_members.setdefault(bundle_kref, [])
        before = len(members)
        self.bundle_members[bundle_kref] = [m for m in members if m["item_kref"] != item_kref]
        return len(self.bundle_members[bundle_kref]) != before

    async def get_item(self, item_kref: str):
        return self.items.get(item_kref)

    async def get_revision(self, revision_kref: str):
        return self.revisions.get(revision_kref)

    async def get_artifacts(self, revision_kref: str):
        return list(self.artifacts_by_revision.get(revision_kref, []))

    async def create_revision(self, item_kref: str, metadata: dict[str, Any], tag: str | None = None):
        number = sum(1 for rev in self.revisions.values() if rev.get("item_kref") == item_kref) + 1
        rev = {
            "kref": f"{item_kref}?r={number}",
            "item_kref": item_kref,
            "metadata": metadata,
            "tags": [tag] if tag else [],
        }
        self.revisions[rev["kref"]] = rev
        self.created_revisions.append(rev)
        return rev

    async def create_artifact(self, revision_kref: str, name: str, location: str, metadata: dict[str, Any] | None = None):
        artifact = {"revision_kref": revision_kref, "name": name, "location": location, "metadata": metadata or {}}
        self.artifacts.append(artifact)
        return {"kref": f"artifact://{len(self.artifacts)}", **artifact}

    async def tag_revision(self, revision_kref: str, tag: str):
        self.tags_added.append((revision_kref, tag))
        self.revisions[revision_kref].setdefault("tags", []).append(tag)

    async def untag_revision(self, revision_kref: str, tag: str):
        self.tags_removed.append((revision_kref, tag))
        tags = self.revisions[revision_kref].setdefault("tags", [])
        self.revisions[revision_kref]["tags"] = [t for t in tags if t != tag]

    async def create_edge(self, source_rev_kref: str, target_rev_kref: str, edge_type: str, metadata: dict[str, str] | None = None):
        self.created_edges.append({
            "source_kref": source_rev_kref,
            "target_kref": target_rev_kref,
            "edge_type": edge_type,
            "metadata": metadata or {},
        })

    async def create_item(self, space_path: str, name: str, kind: str, metadata: dict[str, Any] | None = None):
        kref = f"kref://{space_path}/{name}.{kind}"
        item = {"kref": kref, "name": f"{name}.{kind}", "item_name": f"{name}.{kind}", "kind": kind, "metadata": metadata or {}}
        self.items[kref] = item
        return item


@pytest.fixture
def fake_sdk(monkeypatch):
    sdk = FakeKumihoMutationSDK()
    import operator_mcp.operator_mcp as op_mod

    monkeypatch.setattr(op_mod, "KUMIHO_SDK", sdk, raising=False)
    return sdk


@pytest.mark.asyncio
async def test_kumiho_bundle_update_adds_member_idempotently(fake_sdk):
    cfg = KumihoBundleUpdateConfig(
        project="ManghanDev",
        mode="add_members",
        updates=[
            KumihoBundleUpdateEntryConfig(
                bundle="manghan-production-episodes",
                add=[
                    KumihoBundleMemberConfig(
                        item_kref="kref://ManghanDev/Episodes/mg-ep-027.webnovel-episode",
                        reason="Production-ready episode",
                    )
                ],
            )
        ],
    )
    step = StepDef(id="bundle-update", type=StepType.KUMIHO_BUNDLE_UPDATE, kumiho=cfg)

    first = await _exec_kumiho_bundle_update(step, _state())
    second = await _exec_kumiho_bundle_update(step, _state())

    assert first.status == "completed"
    assert first.output_data["changed"] is True
    assert first.output_data["bundles"][0]["added"] == [
        "kref://ManghanDev/Episodes/mg-ep-027.webnovel-episode"
    ]
    assert second.status == "completed"
    assert second.output_data["changed"] is False
    assert second.output_data["bundles"][0]["skipped_existing"] == [
        "kref://ManghanDev/Episodes/mg-ep-027.webnovel-episode"
    ]


@pytest.mark.asyncio
async def test_kumiho_bundle_update_rejects_protected_bundle(fake_sdk):
    cfg = KumihoBundleUpdateConfig(
        project="ManghanDev",
        mode="add_members",
        updates=[
            KumihoBundleUpdateEntryConfig(
                bundle="manghan-main-canon",
                add=[KumihoBundleMemberConfig(item_kref="kref://ManghanDev/Episodes/mg-ep-027.webnovel-episode")],
            )
        ],
    )
    step = StepDef(id="bundle-update", type=StepType.KUMIHO_BUNDLE_UPDATE, kumiho=cfg)

    result = await _exec_kumiho_bundle_update(step, _state())

    assert result.status == "failed"
    assert result.output_data["errors"][0]["type"] == "protected_bundle"


@pytest.mark.asyncio
async def test_kumiho_bundle_update_allows_protected_bundle_with_override(fake_sdk):
    cfg = KumihoBundleUpdateConfig(
        project="ManghanDev",
        mode="add_members",
        allow_protected=True,
        updates=[
            KumihoBundleUpdateEntryConfig(
                bundle="manghan-current-character-states",
                add=[
                    KumihoBundleMemberConfig(
                        item_kref="kref://ManghanDev/CharacterStates/handoyoon.character-state"
                    )
                ],
            )
        ],
    )
    step = StepDef(id="bundle-update", type=StepType.KUMIHO_BUNDLE_UPDATE, kumiho=cfg)

    result = await _exec_kumiho_bundle_update(step, _state())

    assert result.status == "completed"
    assert result.output_data["bundles"][0]["added"] == [
        "kref://ManghanDev/CharacterStates/handoyoon.character-state"
    ]


@pytest.mark.asyncio
async def test_kumiho_patch_apply_dry_run_plans_without_mutation(fake_sdk):
    cfg = KumihoPatchApplyConfig(
        project="ManghanDev",
        patch_kref="kref://ManghanDev/Patches/mg-ep-027-canon-patch.canon-patch?r=1",
        dry_run=True,
        approval=KumihoPatchApprovalConfig(required=False),
    )
    step = StepDef(id="apply-patch", type=StepType.KUMIHO_PATCH_APPLY, kumiho=cfg)

    result = await _exec_kumiho_patch_apply(step, _state())

    assert result.status == "completed"
    assert result.output_data["dry_run"] is True
    assert result.output_data["applied"] is False
    assert [op["op"] for op in result.output_data["planned_operations"]] == [
        "create_revision",
        "create_edge",
    ]
    assert fake_sdk.created_revisions == []
    assert fake_sdk.created_edges == []


@pytest.mark.asyncio
async def test_kumiho_patch_apply_creates_revision_tags_edge_and_bundle_moves(fake_sdk):
    cfg = KumihoPatchApplyConfig(
        project="ManghanDev",
        patch_kref="kref://ManghanDev/Patches/mg-ep-027-canon-patch.canon-patch?r=1",
        dry_run=False,
        approval=KumihoPatchApprovalConfig(
            required=True,
            approved="true",
            approved_by="reviewer",
        ),
        apply=KumihoPatchApplyFlagsConfig(save_apply_report=False),
        bundle_policy=KumihoPatchBundlePolicyConfig(
            pending_patch_bundle="manghan-pending-canon-patches",
            applied_patch_bundle="manghan-applied-canon-patches",
            current_state_bundle="manghan-current-character-states",
        ),
    )
    step = StepDef(id="apply-patch", type=StepType.KUMIHO_PATCH_APPLY, kumiho=cfg)

    result = await _exec_kumiho_patch_apply(step, _state())

    assert result.status == "completed"
    assert result.output_data["applied"] is True
    created = result.output_data["created_revisions"][0]
    assert created["old_revision_kref"] == "kref://ManghanDev/CharacterStates/handoyoon.character-state?r=1"
    assert created["new_revision_kref"] == "kref://ManghanDev/CharacterStates/handoyoon.character-state?r=2"
    assert ("kref://ManghanDev/CharacterStates/handoyoon.character-state?r=2", "current") in fake_sdk.tags_added
    assert ("kref://ManghanDev/CharacterStates/handoyoon.character-state?r=1", "current") in fake_sdk.tags_removed
    assert fake_sdk.created_edges[0]["edge_type"] == "UPDATES"
    assert fake_sdk.created_edges[0]["target_kref"] == "kref://ManghanDev/CharacterStates/handoyoon.character-state?r=2"
    assert {"item_kref": "kref://ManghanDev/Patches/mg-ep-027-canon-patch.canon-patch"} not in fake_sdk.bundle_members[
        "kref://ManghanDev/Bundles/manghan-pending-canon-patches.bundle"
    ]
    assert {"item_kref": "kref://ManghanDev/Patches/mg-ep-027-canon-patch.canon-patch"} in fake_sdk.bundle_members[
        "kref://ManghanDev/Bundles/manghan-applied-canon-patches.bundle"
    ]
    assert {"item_kref": "kref://ManghanDev/CharacterStates/handoyoon.character-state"} in fake_sdk.bundle_members[
        "kref://ManghanDev/Bundles/manghan-current-character-states.bundle"
    ]


@pytest.mark.asyncio
async def test_kumiho_patch_apply_loads_file_uri_patch_artifact(fake_sdk, tmp_path):
    patch_kref = "kref://ManghanDev/Patches/mg-ep-027-canon-patch.canon-patch?r=1"
    fake_sdk.revisions[patch_kref]["metadata"] = {}
    patch_path = tmp_path / "canon-patch.yaml"
    patch_path.write_text(
        """
canon_patch:
  patch_id: artifact-patch
  patch_status: candidate
  proposed_revision_updates:
    character_states:
      - item_name: handoyoon
        item_kind: character-state
        previous_revision_kref: kref://ManghanDev/CharacterStates/handoyoon.character-state?r=1
        proposed_change_summary: Loaded from file URI artifact.
        proposed_artifact_patch: "## Current State\\n- Loaded from file URI artifact."
        evidence_locator: Ep.027 artifact fixture
""".strip(),
        encoding="utf-8",
    )
    fake_sdk.artifacts_by_revision[patch_kref] = [
        {
            "name": "canon-patch.yaml",
            "location": patch_path.as_uri(),
            "metadata": {},
        }
    ]
    cfg = KumihoPatchApplyConfig(
        project="ManghanDev",
        patch_kref=patch_kref,
        dry_run=True,
        approval=KumihoPatchApprovalConfig(required=False),
    )
    step = StepDef(id="apply-patch", type=StepType.KUMIHO_PATCH_APPLY, kumiho=cfg)

    result = await _exec_kumiho_patch_apply(step, _state())

    assert result.status == "completed"
    assert result.output_data["planned_operations"][0]["summary"] == "Loaded from file URI artifact."
