"""Tests for workflow trigger registry construction."""
from __future__ import annotations

import pytest

from operator_mcp.workflow import loader
from operator_mcp.workflow.event_listener import get_trigger_registry
from operator_mcp.workflow.schema import StepDef, WorkflowDef


def _noop_workflow(name: str) -> WorkflowDef:
    return WorkflowDef(
        name=name,
        steps=[
            StepDef(
                id="noop",
                type="compute",
                compute={"outputs": {"ok": True}},
            )
        ],
    )


@pytest.mark.asyncio
async def test_trigger_registry_overlays_latest_kumiho_revision(
    tmp_path,
    monkeypatch,
):
    """Latest workflow revisions must win over stale base disk copies.

    The editable ``~/.construct/workflows/foo.yaml`` file can lag behind the
    latest Kumiho revision artifact ``foo.rN.yaml``. Event chaining should
    register triggers from the latest revision because that is what
    ``resolve_workflow`` executes.
    """
    registry = get_trigger_registry()
    registry.rebuild({})

    stale_disk_wf = _noop_workflow("cross-chronicle-episode-room")
    monkeypatch.setattr(
        loader,
        "load_all_workflows",
        lambda _project_dir=None: {"cross-chronicle-episode-room": stale_disk_wf},
    )

    latest_yaml = tmp_path / "cross-chronicle-episode-room.r4.yaml"
    latest_yaml.write_text(
        """
name: cross-chronicle-episode-room
version: "1.0"
triggers:
  - on_kind: arc-blueprint
    on_tag: ready
    on_name_pattern: "cc-vol-*"
    on_space: CrossChronicle/ArcBlueprints
steps:
  - id: noop
    type: compute
    compute:
      outputs:
        ok: true
""",
        encoding="utf-8",
    )

    class FakeKumihoSDK:
        _available = True

        async def list_items(self, _space: str):
            return [
                {
                    "item_name": "wf-finding",
                    "kref": "kref://Construct/Workflows/wf-finding.workflow-finding",
                },
                {
                    "item_name": "cross-chronicle-episode-room",
                    "kref": "kref://Construct/Workflows/cross-chronicle-episode-room.workflow",
                }
            ]

        async def get_latest_revision(self, item_kref: str, tag: str | None = None):
            assert item_kref.endswith("cross-chronicle-episode-room.workflow")
            assert tag == "latest"
            return {
                "kref": (
                    "kref://Construct/Workflows/"
                    "cross-chronicle-episode-room.workflow?r=4"
                ),
                "tags": ["latest"],
            }

        async def get_artifacts(self, revision_kref: str):
            assert revision_kref.endswith("?r=4")
            return [{"location": f"file://{latest_yaml}"}]

    import operator_mcp.operator_mcp as op_mod
    monkeypatch.setattr(op_mod, "KUMIHO_SDK", FakeKumihoSDK(), raising=False)

    try:
        trigger_count = await loader.build_trigger_registry_async()

        matches = registry.match(
            "arc-blueprint",
            "ready",
            "cc-vol-1-arc-6",
            "CrossChronicle/ArcBlueprints",
        )
        assert trigger_count == 1
        assert [rule.workflow_name for rule in matches] == [
            "cross-chronicle-episode-room"
        ]
    finally:
        registry.rebuild({})
