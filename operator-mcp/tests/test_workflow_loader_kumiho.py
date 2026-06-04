from __future__ import annotations

import pytest

from operator_mcp.workflow import loader


MINIMAL_WORKFLOW_YAML = """
name: metadata-backed-workflow
version: "1.0"
steps:
  - id: draft
    type: agent
    agent:
      agent_type: codex
      role: writer
      prompt: Draft the result.
"""


class FakeKumihoSDK:
    _available = True

    def __init__(self, metadata: dict[str, str]) -> None:
        self.metadata = metadata

    async def get_latest_revision(self, item_kref: str, tag: str = "latest") -> dict:
        return {
            "kref": f"{item_kref}?r=7",
            "metadata": self.metadata,
            "tags": ["latest"],
        }

    async def get_artifacts(self, revision_kref: str) -> list[dict]:
        return [
            {
                "name": "workflow.yaml",
                "location": "file:///Users/neo/.revka/workflows/metadata-backed-workflow.r7.yaml",
            }
        ]


@pytest.mark.asyncio
async def test_kumiho_workflow_loads_from_metadata_when_artifact_path_is_remote(monkeypatch):
    import operator_mcp.operator_mcp as operator_module

    monkeypatch.setattr(
        operator_module,
        "KUMIHO_SDK",
        FakeKumihoSDK({"definition": MINIMAL_WORKFLOW_YAML}),
    )

    result = await loader._load_workflow_item_from_kumiho(
        {
            "item_name": "metadata-backed-workflow",
            "kref": "kref://Revka/Workflows/metadata-backed-workflow.workflow",
        }
    )

    assert result is not None
    workflow, item_kref, revision_kref = result
    assert workflow.name == "metadata-backed-workflow"
    assert workflow.steps[0].id == "draft"
    assert item_kref == "kref://Revka/Workflows/metadata-backed-workflow.workflow"
    assert revision_kref.endswith("?r=7")


@pytest.mark.asyncio
async def test_kumiho_workflow_errors_only_when_artifact_and_metadata_are_unusable(monkeypatch):
    import operator_mcp.operator_mcp as operator_module

    monkeypatch.setattr(operator_module, "KUMIHO_SDK", FakeKumihoSDK({}))

    with pytest.raises(RuntimeError, match="metadata has no workflow definition"):
        await loader._load_workflow_item_from_kumiho(
            {
                "item_name": "metadata-backed-workflow",
                "kref": "kref://Revka/Workflows/metadata-backed-workflow.workflow",
            }
        )
