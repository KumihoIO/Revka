from __future__ import annotations

from typing import Any

import pytest

from operator_mcp.tool_handlers import canonworks as cw
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

    async def get_revision_by_tag(self, item_kref: str, tag: str) -> dict[str, Any] | None:
        matches = [
            rev for rev in self.revisions.values()
            if rev["item_kref"] == item_kref and tag in (rev.get("tags") or [])
        ]
        return matches[-1] if matches else None

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


# ---------------------------------------------------------------------------
# Canon ontology integration (Deliverable 2 / 3)
# ---------------------------------------------------------------------------


def _glass_city_ontology_seed(tmp_path) -> dict[str, Any]:
    return {
        "title": "Glass City",
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
        "storylines": [
            {"id": "archive-murder", "summary": "Mira traces a memory-edit murder.", "goal": "Expose the write path.", "characters": ["mira", "jun"]}
        ],
        "foreshadow_threads": [
            {"id": "jun-private-archive", "summary": "Jun hides an archive.", "payoff_target": "archive-murder"}
        ],
        "timeline_events": [
            {"position": "prelude", "summary": "The archive accepts its first memory backup."}
        ],
        "artifact_root": str(tmp_path),
    }


@pytest.mark.asyncio
async def test_canonworks_init_creates_canon_ontology_item_in_main_canon(tmp_path):
    sdk = FakeCanonWorksSDK()

    result = await tool_canonworks_init(_glass_city_ontology_seed(tmp_path), sdk)

    assert result["success"] is True
    ontology_kref = "kref://GlassCity/CanonRules/canon-ontology.canon-ontology"
    assert ontology_kref in sdk.items
    assert any(
        member["bundle"] == "glass-city-main-canon" and member["item_kref"] == ontology_kref
        for member in result["created"]["bundle_members"]
    )
    doc_path = tmp_path / "glass-city" / "canon_ontology" / "CANON_ONTOLOGY.md"
    assert doc_path.exists()
    assert "Canon Ontology" in doc_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_canonworks_init_creates_story_structure_items_and_bundles(tmp_path):
    sdk = FakeCanonWorksSDK()

    result = await tool_canonworks_init(_glass_city_ontology_seed(tmp_path), sdk)

    assert "kref://GlassCity/Roadmaps/archive-murder.storyline" in sdk.items
    assert "kref://GlassCity/Roadmaps/jun-private-archive.foreshadow-thread" in sdk.items
    assert "kref://GlassCity/Timeline/event-001.timeline-event" in sdk.items

    members = result["created"]["bundle_members"]
    assert any(
        m["bundle"] == "glass-city-active-storylines"
        and m["item_kref"] == "kref://GlassCity/Roadmaps/archive-murder.storyline"
        for m in members
    )
    assert any(
        m["bundle"] == "glass-city-active-foreshadow"
        and m["item_kref"] == "kref://GlassCity/Roadmaps/jun-private-archive.foreshadow-thread"
        for m in members
    )


@pytest.mark.asyncio
async def test_canonworks_init_creates_structural_edges(tmp_path):
    sdk = FakeCanonWorksSDK()

    result = await tool_canonworks_init(_glass_city_ontology_seed(tmp_path), sdk)

    edge_types = {edge["edge_type"] for edge in sdk.edges}
    assert {"APPEARS_IN", "INVOLVES", "FORESHADOWS", "BELONGS_TO"} <= edge_types

    structural = result["created"]["structural_edges"]
    assert {"from": "mira", "to": "series-bible", "edge_type": "APPEARS_IN"} in structural
    assert {"from": "archive-murder", "to": "mira", "edge_type": "INVOLVES"} in structural
    assert {"from": "jun-private-archive", "to": "archive-murder", "edge_type": "FORESHADOWS"} in structural
    assert {"from": "event-001", "to": "timeline", "edge_type": "BELONGS_TO"} in structural


@pytest.mark.asyncio
async def test_canonworks_init_normalizes_english_alias_edge_type(tmp_path):
    sdk = FakeCanonWorksSDK()
    args = _glass_city_ontology_seed(tmp_path)
    args["relationships"] = [{"from": "mira", "to": "jun", "edge_type": "rival"}]

    result = await tool_canonworks_init(args, sdk)

    rival_edges = [e for e in sdk.edges if e["edge_type"] == "RIVAL_OF"]
    assert len(rival_edges) == 1
    assert result["created"]["warnings"] == []
    assert {"from": "mira", "to": "jun", "edge_type": "RIVAL_OF", "in_vocabulary": True} in result["created"]["edges"]


@pytest.mark.asyncio
async def test_canonworks_init_normalizes_korean_alias_edge_type(tmp_path):
    sdk = FakeCanonWorksSDK()
    args = _glass_city_ontology_seed(tmp_path)
    args["relationships"] = [{"from": "mira", "to": "jun", "edge_type": "라이벌"}]

    result = await tool_canonworks_init(args, sdk)

    assert any(e["edge_type"] == "RIVAL_OF" for e in sdk.edges)
    assert result["created"]["warnings"] == []


@pytest.mark.asyncio
async def test_canonworks_init_preserves_unknown_edge_type_with_warning(tmp_path):
    sdk = FakeCanonWorksSDK()
    args = _glass_city_ontology_seed(tmp_path)
    args["relationships"] = [{"from": "mira", "to": "jun", "edge_type": "HAUNTS"}]

    result = await tool_canonworks_init(args, sdk)

    assert any(e["edge_type"] == "HAUNTS" for e in sdk.edges)
    unknown = [w for w in result["created"]["warnings"] if w["type"] == "relationship_edge_type_unknown"]
    assert len(unknown) == 1
    assert unknown[0]["edge_type"] == "HAUNTS"
    assert unknown[0]["declared_type"] == "HAUNTS"
    assert {"from": "mira", "to": "jun", "edge_type": "HAUNTS", "in_vocabulary": False} in result["created"]["edges"]


@pytest.mark.asyncio
async def test_canonworks_init_create_inverse_edges_only_for_asymmetric(tmp_path):
    sdk = FakeCanonWorksSDK()
    args = _glass_city_ontology_seed(tmp_path)
    args["characters"] = [{"id": "mira"}, {"id": "jun"}, {"id": "aki"}]
    args["storylines"] = []
    args["foreshadow_threads"] = []
    args["relationships"] = [
        {"from": "mira", "to": "jun", "edge_type": "MENTOR_OF"},
        {"from": "mira", "to": "aki", "edge_type": "RIVAL_OF"},
    ]
    args["create_inverse_edges"] = True

    result = await tool_canonworks_init(args, sdk)

    relationship_edges = [e for e in sdk.edges if e["edge_type"] in {"MENTOR_OF", "MENTEE_OF", "RIVAL_OF", "ALLY_OF"}]
    kinds = sorted(e["edge_type"] for e in relationship_edges)
    # asymmetric MENTOR_OF gets its inverse MENTEE_OF; symmetric RIVAL_OF does not duplicate.
    assert kinds == ["MENTEE_OF", "MENTOR_OF", "RIVAL_OF"]
    assert {"from": "jun", "to": "mira", "edge_type": "MENTEE_OF", "derived": "inverse"} in result["created"]["edges"]


@pytest.mark.asyncio
async def test_canonworks_init_without_inverse_edges_by_default(tmp_path):
    sdk = FakeCanonWorksSDK()
    args = _glass_city_ontology_seed(tmp_path)
    args["storylines"] = []
    args["foreshadow_threads"] = []
    args["relationships"] = [{"from": "mira", "to": "jun", "edge_type": "MENTOR_OF"}]

    await tool_canonworks_init(args, sdk)

    assert not any(e["edge_type"] == "MENTEE_OF" for e in sdk.edges)


@pytest.mark.asyncio
async def test_canonworks_init_config_contains_ontology_section(tmp_path):
    sdk = FakeCanonWorksSDK()

    result = await tool_canonworks_init(_glass_city_ontology_seed(tmp_path), sdk)

    yaml_text = result["project_config_yaml"]
    assert "ontology:" in yaml_text
    assert "character_edge_types:" in yaml_text
    assert "structural_edge_types:" in yaml_text
    assert "RIVAL_OF" in yaml_text
    assert "canon_ontology: kref://GlassCity/CanonRules/canon-ontology.canon-ontology" in yaml_text


@pytest.mark.asyncio
async def test_canonworks_init_warns_on_unknown_storyline_character(tmp_path):
    sdk = FakeCanonWorksSDK()
    args = _glass_city_ontology_seed(tmp_path)
    args["storylines"] = [{"id": "archive-murder", "characters": ["mira", "ghost"]}]

    result = await tool_canonworks_init(args, sdk)

    skipped = [w for w in result["created"]["warnings"] if w["type"] == "storyline_character_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["character"] == "ghost"
    assert skipped[0]["storyline"] == "archive-murder"


@pytest.mark.asyncio
async def test_canonworks_preview_shows_structural_edges_and_vocabulary_flags(tmp_path):
    result = await tool_canonworks_preview(
        {
            "state_root": str(tmp_path / "state"),
            "title": "Glass City",
            "project": "GlassCity",
            "story_slug": "glass-city",
            "premise": "A city built from archived memories.",
            "characters": [{"id": "mira"}, {"id": "jun"}],
            "relationships": [
                {"from": "mira", "to": "jun", "edge_type": "rival"},
                {"from": "mira", "to": "jun", "edge_type": "HAUNTS"},
            ],
            "storylines": [{"id": "archive-murder", "characters": ["mira"]}],
            "foreshadow_threads": [{"id": "jun-secret", "payoff_target": "archive-murder"}],
            "timeline_events": [{"position": "prelude", "summary": "First backup."}],
        }
    )

    preview = result["preview"]
    struct_types = {e["edge_type"] for e in preview["structural_edges"]}
    assert {"APPEARS_IN", "INVOLVES", "FORESHADOWS", "BELONGS_TO"} <= struct_types

    rel_by_type = {e["edge_type"]: e for e in preview["relationship_edges"]}
    assert rel_by_type["RIVAL_OF"]["in_vocabulary"] is True
    assert rel_by_type["HAUNTS"]["in_vocabulary"] is False
    assert preview["ontology"]["version"] == "1"
    assert "RIVAL_OF" in preview["ontology"]["character_edge_types"]


# ---------------------------------------------------------------------------
# Kumiho typed-graph projection (Deliverable A / B / C / D consumption)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_canonworks_init_annotates_kumiho_node_kind_metadata(tmp_path):
    # (A) Canon items are annotated with the mapped Kumiho node kind so a
    # cross-referencing reader / the typed projection picks the right kind.
    sdk = FakeCanonWorksSDK()
    await tool_canonworks_init(_glass_city_ontology_seed(tmp_path), sdk)

    char = sdk.items["kref://GlassCity/Characters/mira.character"]
    assert char["metadata"]["kumiho_node_kind"] == "entity"
    event = sdk.items["kref://GlassCity/Timeline/event-001.timeline-event"]
    assert event["metadata"]["kumiho_node_kind"] == "event"
    # A kind with no natural fit carries no node-kind annotation.
    config = sdk.items["kref://GlassCity/Config/canonworks-project-config.canonworks-config"]
    assert "kumiho_node_kind" not in config["metadata"]


@pytest.mark.asyncio
async def test_canonworks_init_noops_projection_when_kumiho_absent(tmp_path, monkeypatch):
    # (B) When kumiho-memory decompose is unavailable, the projection no-ops
    # gracefully and init still succeeds — raw canon edges are unaffected.
    monkeypatch.setattr(cw, "_HAS_KUMIHO_MEMORY_DECOMPOSE", False)
    monkeypatch.setattr(cw, "_km_tool_memory_decompose", None)

    sdk = FakeCanonWorksSDK()
    result = await tool_canonworks_init(_glass_city_ontology_seed(tmp_path), sdk)

    assert result["success"] is True
    projection = result["typed_graph_projection"]
    assert projection["projected"] is False
    assert "unavailable" in projection["reason"]
    # The raw narrative canon edge is still written verbatim.
    assert any(e["edge_type"] == "RIVAL_OF" for e in sdk.edges)


@pytest.mark.asyncio
async def test_canonworks_init_routes_durable_facts_through_decompose(tmp_path, monkeypatch):
    # (B/C) When decompose is present, canon routes durable facts/relations
    # through it IN ADDITION to the raw canon edges, and the narrative predicate
    # is passed through for Kumiho to fold (RELATES_TO for narrative types).
    captured: list[dict[str, Any]] = []

    def fake_decompose(payload: dict[str, Any]) -> dict[str, Any]:
        captured.append(payload)
        return {
            "decomposed": {
                "entities": len(payload["entities"]),
                "facts": len(payload["facts"]),
                "relations": len(payload["relations"]),
            },
            "kref": payload["kref"],
        }

    monkeypatch.setattr(cw, "_HAS_KUMIHO_MEMORY_DECOMPOSE", True)
    monkeypatch.setattr(cw, "_km_tool_memory_decompose", fake_decompose)

    sdk = FakeCanonWorksSDK()
    result = await tool_canonworks_init(_glass_city_ontology_seed(tmp_path), sdk)

    assert result["success"] is True
    assert len(captured) == 1
    payload = captured[0]
    # Anchored to a real revision (the series bible).
    assert payload["kref"].startswith("kref://GlassCity/Series/main.series-bible")
    # Entities include the named characters + storyline + foreshadow thread.
    entity_names = {e["name"] for e in payload["entities"]}
    assert {"Mira", "Jun"} <= entity_names
    # Relations carry the narrative predicate verbatim (Kumiho folds it).
    predicates = {(r["subject"], r["predicate"], r["object"]) for r in payload["relations"]}
    assert ("Mira", "RIVAL_OF", "Jun") in predicates
    assert any(p == "INVOLVES" for _, p, _ in predicates)
    assert any(p == "FORESHADOWS" for _, p, _ in predicates)

    projection = result["typed_graph_projection"]
    assert projection["projected"] is True
    assert projection["decomposed"]["relations"] >= 1
    # (C) resolve_predicate fold recorded: narrative RIVAL_OF -> RELATES_TO.
    folds = {f["predicate"]: f for f in projection["predicate_folds"]}
    assert folds["RIVAL_OF"]["projected_edge"] == "RELATES_TO"
    assert folds["RIVAL_OF"]["fallback"] is True

    # The raw narrative canon edge is preserved verbatim (not folded).
    assert any(e["edge_type"] == "RIVAL_OF" for e in sdk.edges)


@pytest.mark.asyncio
async def test_canonworks_init_projection_survives_decompose_failure(tmp_path, monkeypatch):
    # (B) A raising decompose never breaks init — the failure is a notice.
    def boom(payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("kumiho exploded")

    monkeypatch.setattr(cw, "_HAS_KUMIHO_MEMORY_DECOMPOSE", True)
    monkeypatch.setattr(cw, "_km_tool_memory_decompose", boom)

    sdk = FakeCanonWorksSDK()
    result = await tool_canonworks_init(_glass_city_ontology_seed(tmp_path), sdk)

    assert result["success"] is True
    assert result["typed_graph_projection"]["projected"] is False
    assert "decompose raised" in result["typed_graph_projection"]["reason"]


@pytest.mark.asyncio
async def test_canonworks_init_ontology_publish_is_idempotent_at_same_version(tmp_path):
    # (D) Re-publishing the canon ontology at the same canon spec version does
    # not create a duplicate revision (mirrors ontology_spec seed semantics).
    sdk = FakeCanonWorksSDK()
    args = _glass_city_ontology_seed(tmp_path)

    await tool_canonworks_init(args, sdk)
    await tool_canonworks_init(args, sdk)

    ontology_kref = "kref://GlassCity/CanonRules/canon-ontology.canon-ontology"
    published = [
        rev for rev in sdk.revisions.values()
        if rev["item_kref"] == ontology_kref and "published" in (rev.get("tags") or [])
    ]
    assert len(published) == 1
    # The single published revision records canon's own spec version and (when
    # kumiho-memory is present) references Kumiho's spec identity.
    meta = published[0]["metadata"]
    assert meta["canon_spec_version"] == "canonworks.ontology.v1"
