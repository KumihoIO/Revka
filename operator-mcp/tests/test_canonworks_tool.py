from __future__ import annotations

from typing import Any

import pytest

from operator_mcp.tool_handlers.canonworks import (
    tool_canonworks_commit,
    tool_canonworks_init,
    tool_canonworks_preview,
    tool_canonworks_run_episode,
    tool_canonworks_start,
    tool_canonworks_sync_state,
)


class FakeCanonWorksSDK:
    _available = True

    def __init__(self) -> None:
        self.spaces: set[str] = set()
        self.items: dict[str, dict[str, Any]] = {}
        self.revisions: dict[str, dict[str, Any]] = {}
        self.artifacts: list[dict[str, Any]] = []
        self.bundle_members: dict[str, list[str]] = {}
        self.edges: list[dict[str, Any]] = []

    def _lazy_init(self) -> None:
        self._available = True

    async def ensure_space_path(self, space_path: str) -> None:
        self.spaces.add(space_path.strip("/"))

    async def search_items(
        self,
        context: str = "",
        name: str = "",
        kind: str = "",
        include_metadata: bool = False,
    ) -> list[dict[str, Any]]:
        out = list(self.items.values())
        if context:
            out = [item for item in out if context.strip("/") in item["kref"]]
        if name:
            out = [item for item in out if item["name"] == name or item["item_name"] == name]
        if kind:
            out = [item for item in out if item["kind"] == kind]
        return out

    async def create_item(
        self,
        space_path: str,
        name: str,
        kind: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        kref = f"kref://{space_path.strip('/')}/{name}.{kind}"
        item = {
            "kref": kref,
            "name": f"{name}.{kind}",
            "item_name": f"{name}.{kind}",
            "kind": kind,
            "metadata": metadata or {},
        }
        self.items[kref] = item
        return item

    async def create_revision(
        self,
        item_kref: str,
        metadata: dict[str, Any],
        tag: str | None = "published",
    ) -> dict[str, Any]:
        number = sum(1 for rev in self.revisions.values() if rev["item_kref"] == item_kref) + 1
        revision = {
            "kref": f"{item_kref}?r={number}",
            "item_kref": item_kref,
            "metadata": metadata,
            "tags": [tag] if tag else [],
        }
        self.revisions[revision["kref"]] = revision
        return revision

    async def create_artifact(
        self,
        revision_kref: str,
        name: str,
        location: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        artifact = {
            "kref": f"{revision_kref}&a={name}",
            "revision_kref": revision_kref,
            "name": name,
            "location": location,
            "metadata": metadata or {},
        }
        self.artifacts.append(artifact)
        return artifact

    async def create_bundle(
        self,
        space_path: str,
        name: str,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        kref = f"kref://{space_path.strip('/')}/{name}.bundle"
        bundle = {
            "kref": kref,
            "name": name,
            "item_name": name,
            "kind": "bundle",
            "metadata": metadata or {},
        }
        self.items[kref] = bundle
        self.bundle_members[kref] = []
        return bundle

    async def add_bundle_member(self, bundle_kref: str, item_kref: str) -> bool:
        members = self.bundle_members.setdefault(bundle_kref, [])
        if item_kref in members:
            return False
        members.append(item_kref)
        return True

    async def create_edge(
        self,
        source_rev_kref: str,
        target_rev_kref: str,
        edge_type: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        self.edges.append({
            "source_kref": source_rev_kref,
            "target_kref": target_rev_kref,
            "edge_type": edge_type,
            "metadata": metadata or {},
        })


@pytest.mark.asyncio
async def test_canonworks_init_creates_project_canon_graph(tmp_path):
    sdk = FakeCanonWorksSDK()

    result = await tool_canonworks_init(
        {
            "title": "City of Glass",
            "project": "GlassCity",
            "story_slug": "glass-city",
            "premise": "A serial about a city built from archived memories.",
            "characters": [
                {"id": "mira", "display_name": "Mira", "role": "lead"},
                {"id": "jun", "display_name": "Jun", "role": "rival"},
            ],
            "relationships": [
                {"from": "mira", "to": "jun", "edge_type": "RIVAL_OF", "summary": "Competing investigators."}
            ],
            "timeline_events": [{"position": "Act 1", "summary": "Mira finds the first false memory."}],
            "artifact_root": str(tmp_path),
        },
        sdk,
    )

    assert result["success"] is True
    assert result["project"] == "GlassCity"
    assert result["story_slug"] == "glass-city"
    assert "canon_project:" in result["project_config_yaml"]
    assert "project: GlassCity" in result["project_config_yaml"]
    assert "relationship_map_artifact: kref://GlassCity/Relationships/main.relationship-map?r=1&a=RELATIONSHIP_MAP.md" in result["project_config_yaml"]
    assert result["project_config_item_kref"] == "kref://GlassCity/Config/canonworks-project-config.canonworks-config"
    assert result["next_workflows"] == [
        "canonworks-serial-episode-factory",
        "canonworks-serial-canon-state-sync",
    ]

    assert "GlassCity/Series" in sdk.spaces
    assert "GlassCity/Bundles" in sdk.spaces
    assert "kref://GlassCity/Series/main.series-bible" in sdk.items
    assert "kref://GlassCity/Characters/mira.character" in sdk.items
    assert any(edge["edge_type"] == "RIVAL_OF" for edge in sdk.edges)
    assert result["created"]["warnings"] == []
    assert any(member["bundle"] == "glass-city-main-canon" for member in result["created"]["bundle_members"])
    assert (tmp_path / "glass-city" / "canonworks_config" / "canonworks-project-config.yaml").exists()


@pytest.mark.asyncio
async def test_canonworks_init_requires_title():
    result = await tool_canonworks_init({}, FakeCanonWorksSDK())

    assert result["success"] is False
    assert result["error"] == "title is required"


@pytest.mark.asyncio
async def test_canonworks_init_config_uses_actual_relationship_artifact_revision(tmp_path):
    sdk = FakeCanonWorksSDK()
    args = {
        "title": "City of Glass",
        "project": "GlassCity",
        "story_slug": "glass-city",
        "relationships": [{"from": "mira", "to": "jun"}],
        "characters": [{"id": "mira"}, {"id": "jun"}],
        "artifact_root": str(tmp_path),
    }

    await tool_canonworks_init(args, sdk)
    result = await tool_canonworks_init(args, sdk)

    assert "relationship_map_artifact: kref://GlassCity/Relationships/main.relationship-map?r=2&a=RELATIONSHIP_MAP.md" in result["project_config_yaml"]


@pytest.mark.asyncio
async def test_canonworks_init_warns_when_relationship_endpoint_is_unknown(tmp_path):
    sdk = FakeCanonWorksSDK()

    result = await tool_canonworks_init(
        {
            "title": "City of Glass",
            "project": "GlassCity",
            "story_slug": "glass-city",
            "characters": [{"id": "mira"}],
            "relationships": [{"from": "mira", "to": "unknown", "edge_type": "KNOWS"}],
            "artifact_root": str(tmp_path),
        },
        sdk,
    )

    assert result["created"]["edges"] == []
    assert result["created"]["warnings"] == [
        {
            "type": "relationship_edge_skipped",
            "from": "mira",
            "to": "unknown",
            "reason": "relationship endpoints must match character ids after slug normalization",
        }
    ]


@pytest.mark.asyncio
async def test_canonworks_start_collects_draft_and_questions(tmp_path):
    result = await tool_canonworks_start(
        {
            "state_root": str(tmp_path / "state"),
            "seed": {
                "title": "City of Glass",
                "project": "GlassCity",
                "story_slug": "glass-city",
            },
        }
    )

    assert result["success"] is True
    assert result["session_id"]
    assert result["draft"]["title"] == "City of Glass"
    assert result["readiness"]["ready_to_commit"] is False
    assert [q["field"] for q in result["next_questions"]][:2] == ["premise", "characters"]
    assert result["preview"]["project"] == "GlassCity"
    assert result["project_scaffold"]["status"] == "not_requested"


@pytest.mark.asyncio
async def test_canonworks_start_creates_kumiho_project_scaffold_when_project_is_known(tmp_path):
    sdk = FakeCanonWorksSDK()

    result = await tool_canonworks_start(
        {
            "state_root": str(tmp_path / "state"),
            "seed": {
                "title": "City of Glass",
                "project_name": "GlassCity",
                "story_slug": "glass-city",
            },
        },
        sdk,
    )

    assert result["success"] is True
    assert result["project_scaffold"]["status"] == "ready"
    assert result["project_scaffold"]["project"] == "GlassCity"
    assert "GlassCity" in sdk.spaces
    assert "GlassCity/Series" in sdk.spaces
    assert "GlassCity/CanonRules" in sdk.spaces
    assert "GlassCity/StyleGuides" in sdk.spaces
    assert "GlassCity/Volumes" in sdk.spaces


@pytest.mark.asyncio
async def test_canonworks_start_waits_for_name_before_creating_kumiho_project(tmp_path):
    sdk = FakeCanonWorksSDK()

    result = await tool_canonworks_start({"state_root": str(tmp_path / "state")}, sdk)

    assert result["success"] is True
    assert result["project_scaffold"]["status"] == "waiting_for_project"
    assert sdk.spaces == set()


@pytest.mark.asyncio
async def test_canonworks_preview_blocks_invalid_relationship_endpoint(tmp_path):
    result = await tool_canonworks_preview(
        {
            "state_root": str(tmp_path / "state"),
            "title": "City of Glass",
            "project": "GlassCity",
            "story_slug": "glass-city",
            "premise": "A city built from archived memories.",
            "characters": [{"id": "mira"}],
            "relationships": [{"from": "mira", "to": "jun"}],
        }
    )

    assert result["readiness"]["ready_to_commit"] is False
    assert result["readiness"]["blocking"][0]["field"] == "relationships"
    assert result["next_questions"][0]["field"] == "relationships"
    assert result["preview"]["relationship_edges"] == []
    assert result["preview"]["warnings"][0]["type"] == "relationship_edge_skipped"


@pytest.mark.asyncio
async def test_canonworks_commit_blocks_unready_draft(tmp_path):
    start = await tool_canonworks_start(
        {
            "state_root": str(tmp_path / "state"),
            "seed": {"title": "City of Glass", "project": "GlassCity", "story_slug": "glass-city"},
        }
    )

    result = await tool_canonworks_commit(
        {"state_root": str(tmp_path / "state"), "session_id": start["session_id"]},
        FakeCanonWorksSDK(),
    )

    assert result["success"] is False
    assert result["error"] == "CanonWorks draft is not ready to commit"
    assert {item["field"] for item in result["readiness"]["blocking"]} == {"premise", "characters"}


@pytest.mark.asyncio
async def test_canonworks_commit_blocks_invalid_relationship_endpoint(tmp_path):
    state_root = tmp_path / "state"
    sdk = FakeCanonWorksSDK()
    start = await tool_canonworks_start(
        {
            "state_root": str(state_root),
            "seed": {
                "title": "City of Glass",
                "project": "GlassCity",
                "story_slug": "glass-city",
                "premise": "A city built from archived memories.",
                "characters": [{"id": "mira"}],
                "relationships": [{"from": "mira", "to": "jun"}],
            },
        }
    )

    result = await tool_canonworks_commit(
        {"state_root": str(state_root), "session_id": start["session_id"]},
        sdk,
    )

    assert result["success"] is False
    assert result["readiness"]["blocking"][0]["field"] == "relationships"
    assert sdk.items == {}


@pytest.mark.asyncio
async def test_canonworks_start_reports_corrupt_session_state(tmp_path):
    state_root = tmp_path / "state"
    session_path = state_root / "sessions" / "broken.json"
    session_path.parent.mkdir(parents=True)
    session_path.write_text("{not-json", encoding="utf-8")

    result = await tool_canonworks_start(
        {"state_root": str(state_root), "session_id": "broken"}
    )

    assert result["success"] is False
    assert result["error_code"] == "canonworks_state_error"
    assert result["state_path"] == str(session_path)


@pytest.mark.asyncio
async def test_canonworks_run_episode_reports_corrupt_project_state(tmp_path):
    state_root = tmp_path / "state"
    project_path = state_root / "projects" / "GlassCity__glass-city.json"
    project_path.parent.mkdir(parents=True)
    project_path.write_text("{not-json", encoding="utf-8")

    result = await tool_canonworks_run_episode(
        {
            "state_root": str(state_root),
            "project": "GlassCity",
            "story_slug": "glass-city",
            "cwd": str(tmp_path),
        }
    )

    assert result["success"] is False
    assert result["error_code"] == "canonworks_state_error"
    assert result["state_path"] == str(project_path)


@pytest.mark.asyncio
async def test_canonworks_commit_stores_project_state_for_wrappers(tmp_path, monkeypatch):
    state_root = tmp_path / "state"
    sdk = FakeCanonWorksSDK()
    start = await tool_canonworks_start(
        {
            "state_root": str(state_root),
            "seed": {
                "title": "City of Glass",
                "project": "GlassCity",
                "story_slug": "glass-city",
                "premise": "A city built from archived memories.",
                "characters": [{"id": "mira"}],
                "artifact_root": str(tmp_path / "artifacts"),
            },
        }
    )
    commit = await tool_canonworks_commit(
        {"state_root": str(state_root), "session_id": start["session_id"]},
        sdk,
    )
    calls: list[dict[str, Any]] = []

    async def fake_run_workflow(args: dict[str, Any]) -> dict[str, Any]:
        calls.append(args)
        return {"run_id": "run-1", "workflow": args["workflow"], "status": "started"}

    from operator_mcp.tool_handlers import workflows

    monkeypatch.setattr(workflows, "tool_run_workflow", fake_run_workflow)

    episode = await tool_canonworks_run_episode(
        {
            "state_root": str(state_root),
            "session_id": start["session_id"],
            "episode_goal": "Open with the first archive crime.",
            "cwd": str(tmp_path),
        }
    )
    sync = await tool_canonworks_sync_state(
        {
            "state_root": str(state_root),
            "project": "GlassCity",
            "story_slug": "glass-city",
            "apply_mode": "propose_only",
            "cwd": str(tmp_path),
        }
    )

    assert commit["success"] is True
    assert episode["workflow"] == "canonworks-serial-episode-factory"
    assert sync["workflow"] == "canonworks-serial-canon-state-sync"
    assert calls[0]["inputs"]["project_config_yaml"] == commit["project_config_artifact_path"]
    assert calls[0]["inputs"]["episode_goal"] == "Open with the first archive crime."
    assert calls[1]["inputs"]["project_config_yaml"] == commit["project_config_artifact_path"]
    assert calls[1]["inputs"]["apply_mode"] == "propose_only"
