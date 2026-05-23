"""Tests for operator.tool_handlers.skills — skill tool handlers."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from operator_mcp.tool_handlers.skills import (
    _artifact_path_from_location,
    tool_capture_skill,
    tool_list_skills,
    tool_load_skill,
)


class FakeSkillPool:
    def __init__(self):
        self.items = []
        self.revisions = {}
        self.artifacts = {}
        self.created_items = []
        self.created_revisions = []
        self.created_artifacts = []
        self.tagged = []

    def _ensure_available(self):
        return True

    async def ensure_space(self, project, space):
        self.project = project
        self.space = space

    async def list_items(self, space_path):
        self.space_path = space_path
        return self.items

    async def create_item(self, space_path, name, kind, metadata=None):
        item = {
            "kref": f"kref://{space_path.strip('/')}/{name}.{kind}",
            "name": name,
            "kind": kind,
            "metadata": metadata or {},
        }
        self.items.append(item)
        self.created_items.append((space_path, name, kind, metadata or {}))
        return item

    async def get_latest_revision(self, item_kref, tag="published"):
        return self.revisions.get((item_kref, tag))

    async def batch_get_revisions(self, item_krefs, tag="published"):
        return {
            item_kref: self.revisions[(item_kref, tag)]
            for item_kref in item_krefs
            if (item_kref, tag) in self.revisions
        }

    async def get_artifacts(self, revision_kref):
        return self.artifacts.get(revision_kref, [])

    async def create_revision(self, item_kref, metadata, tag=None):
        rev = {"kref": f"{item_kref}?r={len(self.created_revisions) + 1}"}
        self.created_revisions.append((item_kref, metadata, tag))
        return rev

    async def create_artifact(self, revision_kref, name, location, metadata=None):
        artifact = {
            "kref": f"{revision_kref}#{name}",
            "name": name,
            "location": location,
            "metadata": metadata or {},
        }
        self.created_artifacts.append((revision_kref, name, location, metadata or {}))
        return artifact

    async def tag_revision(self, revision_kref, tag):
        self.tagged.append((revision_kref, tag))


def _normalized_path(value: str) -> str:
    return str(_artifact_path_from_location(value)).replace("\\", "/")


def test_artifact_path_from_location_accepts_windows_file_uris():
    assert (
        _normalized_path("file:///C:/Users/neo/Skill%20Guide.md")
        == "C:/Users/neo/Skill Guide.md"
    )
    assert (
        _normalized_path(r"file://C:\Users\neo\Skill Guide.md")
        == "C:/Users/neo/Skill Guide.md"
    )


@pytest.mark.asyncio
class TestToolListSkills:
    async def test_returns_kumiho_skills(self, tmp_path):
        pool = FakeSkillPool()
        item = {
            "kref": "kref://CognitiveMemory/Skills/operator-loop.skill",
            "name": "operator-loop.skill",
            "kind": "skill",
            "metadata": {"description": "Loop orchestration.", "domain": "operator"},
        }
        bundle = {
            "kref": "kref://CognitiveMemory/Skills/operator-loop.bundle",
            "name": "operator-loop",
            "kind": "bundle",
        }
        pool.items.extend([item, bundle])
        pool.revisions[(item["kref"], "published")] = {
            "kref": "kref://CognitiveMemory/Skills/operator-loop.skill?r=2",
            "metadata": {"change_summary": "Updated loop flow."},
        }
        pool.artifacts["kref://CognitiveMemory/Skills/operator-loop.skill?r=2"] = [{
            "kref": "kref://artifact/operator-loop",
            "name": "SKILL.md",
            "location": str(tmp_path / "SKILL.md"),
        }]

        with patch("operator_mcp.tool_handlers.skills.memory_project", return_value="CognitiveMemory"):
            result = await tool_list_skills({}, pool)
        assert result["source"] == "kumiho"
        assert result["count"] == 1
        assert result["details_included"] is False
        assert result["skills"][0]["name"] == "operator-loop"
        assert result["skills"][0]["kind"] == "skill"
        assert "revision_kref" not in result["skills"][0]
        assert "artifact_kref" not in result["skills"][0]

    async def test_include_details_returns_revision_and_artifact(self, tmp_path):
        pool = FakeSkillPool()
        item = {
            "kref": "kref://CognitiveMemory/Skills/operator-loop.skill",
            "name": "operator-loop.skill",
            "kind": "skill",
            "metadata": {"description": "Loop orchestration.", "domain": "operator"},
        }
        pool.items.append(item)
        pool.revisions[(item["kref"], "published")] = {
            "kref": "kref://CognitiveMemory/Skills/operator-loop.skill?r=2",
            "metadata": {"change_summary": "Updated loop flow."},
        }
        pool.artifacts["kref://CognitiveMemory/Skills/operator-loop.skill?r=2"] = [{
            "kref": "kref://artifact/operator-loop",
            "name": "SKILL.md",
            "location": str(tmp_path / "SKILL.md"),
        }]

        with patch("operator_mcp.tool_handlers.skills.memory_project", return_value="CognitiveMemory"):
            result = await tool_list_skills({"include_details": True}, pool)
        assert result["source"] == "kumiho"
        assert result["count"] == 1
        assert result["details_included"] is True
        assert result["skills"][0]["revision_kref"].endswith("?r=2")
        assert result["skills"][0]["artifact_kref"] == "kref://artifact/operator-loop"

    async def test_empty(self):
        pool = FakeSkillPool()
        result = await tool_list_skills({}, pool)
        assert result["count"] == 0

    async def test_legacy_disk_requires_explicit_fallback(self):
        pool = FakeSkillPool()
        pool._ensure_available = lambda: False
        with patch("operator_mcp.tool_handlers.skills.list_local_skills", return_value=[
            {"name": "operator-loop", "title": "Loop Skill", "path": "/skills/operator-loop.md"},
        ]):
            result = await tool_list_skills({}, pool)
            assert result["count"] == 0
            assert result["source"] == "kumiho"
            assert "error" in result

            fallback = await tool_list_skills({"include_legacy_disk": True}, pool)
            assert fallback["count"] == 1
            assert fallback["skills"][0]["source"] == "local_legacy"


@pytest.mark.asyncio
class TestToolLoadSkill:
    async def test_found_in_kumiho(self, tmp_path):
        pool = FakeSkillPool()
        item = {
            "kref": "kref://CognitiveMemory/Skills/operator-chat.skill",
            "name": "operator-chat.skill",
            "kind": "skill",
        }
        pool.items.append(item)
        revision_kref = "kref://CognitiveMemory/Skills/operator-chat.skill?r=1"
        pool.revisions[(item["kref"], "published")] = {"kref": revision_kref}
        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("# Skill Content", encoding="utf-8")
        pool.artifacts[revision_kref] = [{
            "kref": "kref://artifact/operator-chat",
            "name": "SKILL.md",
            "location": str(skill_path),
        }]

        with patch("operator_mcp.tool_handlers.skills.memory_project", return_value="CognitiveMemory"):
            result = await tool_load_skill({"name": "operator-chat"}, pool)
        assert result["source"] == "kumiho"
        assert result["name"] == "operator-chat"
        assert result["content"] == "# Skill Content"
        assert result["artifact_kref"] == "kref://artifact/operator-chat"

    async def test_not_found(self):
        pool = FakeSkillPool()
        result = await tool_load_skill({"name": "nonexistent"}, pool)
        assert "error" in result
        assert "not found" in result["error"].lower()

    async def test_legacy_disk_requires_explicit_fallback(self):
        pool = FakeSkillPool()
        pool._ensure_available = lambda: False
        with patch("operator_mcp.tool_handlers.skills.load_local_skill", return_value="# Local Skill"):
            result = await tool_load_skill({"name": "operator-chat"}, pool)
            assert result["source"] == "kumiho"
            assert "error" in result

            fallback = await tool_load_skill({"name": "operator-chat", "allow_legacy_disk_fallback": True}, pool)
            assert fallback["source"] == "local_legacy"
            assert fallback["content"] == "# Local Skill"


@pytest.mark.asyncio
class TestToolCaptureSkill:
    async def test_creates_skill_artifact_under_workspace_without_agent_id_in_name(self, tmp_path):
        pool = FakeSkillPool()

        with (
            patch("operator_mcp.tool_handlers.skills.memory_project", return_value="CognitiveMemory"),
            patch("operator_mcp.tool_handlers.skills.workspace_dir", return_value=str(tmp_path)),
        ):
            result = await tool_capture_skill({
                "name": "rust-error-handling-pattern agent-1234567890abcdef",
                "domain": "rust",
                "description": "Handle Rust errors consistently.",
                "procedure": "# Rust Error Handling\n\nUse anyhow at boundaries.",
                "learned_from": "agent session",
                "agent_id": "agent-1234567890abcdef",
            }, pool)

        assert result["captured"] is True
        assert result["name"] == "rust-error-handling-pattern"
        assert pool.created_items[0][1] == "rust-error-handling-pattern"
        assert "agent" not in pool.created_items[0][1]
        _, rev_meta, tag = pool.created_revisions[0]
        assert tag is None
        assert rev_meta["agent_id"] == "agent-1234567890abcdef"
        assert pool.tagged == [(result["revision_kref"], "published")]

        artifact_path = tmp_path / "artifact" / "cognitivememory" / "skills" / "rust-error-handling-pattern" / "skill" / "SKILL.md"
        assert result["artifact_path"] == str(artifact_path)
        assert artifact_path.read_text(encoding="utf-8").startswith("# Rust Error Handling")
        assert pool.created_artifacts == [(
            result["revision_kref"],
            "SKILL.md",
            str(artifact_path),
            {
                "summary": "Rust Error Handling: Use anyhow at boundaries.",
                "summary_source": "extractive",
            },
        )]
        assert result["artifact_summary"] == "Rust Error Handling: Use anyhow at boundaries."

    async def test_updates_existing_skill_and_reads_previous_artifact(self, tmp_path):
        pool = FakeSkillPool()
        item = {
            "kref": "kref://CognitiveMemory/Skills/operator-review.skill",
            "name": "operator-review.skill",
        }
        pool.items.append(item)
        previous_path = tmp_path / "old" / "SKILL.md"
        previous_path.parent.mkdir(parents=True)
        previous_path.write_text("# Old Guide\n\nReview quickly.", encoding="utf-8")
        pool.revisions[(item["kref"], "published")] = {
            "kref": "kref://CognitiveMemory/Skills/operator-review.skill?r=2",
        }
        pool.artifacts["kref://CognitiveMemory/Skills/operator-review.skill?r=2"] = [{
            "kref": "kref://artifact/old",
            "name": "SKILL.md",
            "location": str(previous_path),
        }]

        with (
            patch("operator_mcp.tool_handlers.skills.memory_project", return_value="CognitiveMemory"),
            patch("operator_mcp.tool_handlers.skills.workspace_dir", return_value=str(tmp_path)),
        ):
            result = await tool_capture_skill({
                "name": "operator-review",
                "domain": "review",
                "description": "Review implementation changes.",
                "procedure": "# Improved Guide\n\nReview rigorously.",
                "change_summary": "Adds stricter verification.",
            }, pool)

        assert result["updated_existing"] is True
        assert result["read_previous_artifact"] is True
        assert result["previous_revision_kref"].endswith("?r=2")
        assert result["previous_artifact_path"] == str(previous_path)
        assert pool.created_items == []
        _, rev_meta, _ = pool.created_revisions[0]
        assert rev_meta["previous_revision_kref"].endswith("?r=2")
        assert rev_meta["previous_artifact_path"] == str(previous_path)
        assert rev_meta["previous_content_length"] == str(len("# Old Guide\n\nReview quickly."))

    async def test_same_name_bundle_is_not_updated_as_skill(self, tmp_path):
        pool = FakeSkillPool()
        bundle = {
            "kref": "kref://CognitiveMemory/Skills/cc-char-morgas.bundle",
            "name": "cc-char-morgas",
            "kind": "bundle",
        }
        pool.items.append(bundle)
        pool.revisions[(bundle["kref"], "published")] = {
            "kref": "kref://CognitiveMemory/Skills/cc-char-morgas.bundle?r=3",
        }

        with (
            patch("operator_mcp.tool_handlers.skills.memory_project", return_value="CognitiveMemory"),
            patch("operator_mcp.tool_handlers.skills.workspace_dir", return_value=str(tmp_path)),
        ):
            result = await tool_capture_skill({
                "name": "cc-char-morgas",
                "domain": "writing",
                "description": "Character guide.",
                "procedure": "# Morgas\n\nUse the current guide.",
            }, pool)

        assert result["captured"] is True
        assert result["updated_existing"] is False
        assert result["item_kref"].endswith("/cc-char-morgas.skill")
        assert not result["item_kref"].endswith(".bundle")
        assert pool.created_items == [
            (
                "/CognitiveMemory/Skills",
                "cc-char-morgas",
                "skill",
                {
                    "description": "Character guide.",
                    "domain": "writing",
                    "source": "operator-capture-skill",
                },
            ),
        ]
        assert pool.created_revisions[0][0] == result["item_kref"]

    async def test_same_name_skilldef_is_not_updated_as_skill(self, tmp_path):
        pool = FakeSkillPool()
        skilldef = {
            "kref": "kref://CognitiveMemory/Skills/cc-char-morgas.skilldef",
            "name": "cc-char-morgas",
            "kind": "skilldef",
        }
        pool.items.append(skilldef)
        pool.revisions[(skilldef["kref"], "published")] = {
            "kref": "kref://CognitiveMemory/Skills/cc-char-morgas.skilldef?r=3",
        }

        with (
            patch("operator_mcp.tool_handlers.skills.memory_project", return_value="CognitiveMemory"),
            patch("operator_mcp.tool_handlers.skills.workspace_dir", return_value=str(tmp_path)),
        ):
            result = await tool_capture_skill({
                "name": "cc-char-morgas",
                "domain": "writing",
                "description": "Character guide.",
                "procedure": "# Morgas\n\nUse the current guide.",
            }, pool)

        assert result["captured"] is True
        assert result["updated_existing"] is False
        assert result["item_kref"].endswith("/cc-char-morgas.skill")
        assert pool.created_items[0][2] == "skill"
        assert pool.created_revisions[0][0] == result["item_kref"]
