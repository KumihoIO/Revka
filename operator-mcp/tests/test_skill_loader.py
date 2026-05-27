"""Tests for operator.skill_loader — load_skill, list_skills, load_skills_for_pattern."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from operator_mcp.skill_loader import load_skill, list_skills, load_skills_for_pattern, _skill_cache


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear skill cache before each test."""
    _skill_cache.clear()
    yield
    _skill_cache.clear()


class TestLoadSkill:
    def test_load_known_skill(self, skills_dir):
        with patch("operator_mcp.skill_loader._SKILLS_DIRS", [skills_dir]), \
             patch("operator_mcp.skill_loader._SKILL_FILES", {"operator-orchestrator": "operator-orchestrator.md"}):
            content = load_skill("operator-orchestrator")
            assert content is not None
            assert "Orchestrator Skill" in content

    def test_load_unknown_skill(self, skills_dir):
        with patch("operator_mcp.skill_loader._SKILLS_DIRS", [skills_dir]):
            content = load_skill("nonexistent-skill")
            assert content is None

    def test_cache_hit(self, skills_dir):
        with patch("operator_mcp.skill_loader._SKILLS_DIRS", [skills_dir]), \
             patch("operator_mcp.skill_loader._SKILL_FILES", {"operator-loop": "operator-loop.md"}):
            content1 = load_skill("operator-loop")
            content2 = load_skill("operator-loop")
            assert content1 == content2
            assert "operator-loop" in _skill_cache

    def test_direct_file_lookup(self, skills_dir):
        """When name is not in _SKILL_FILES, try direct file lookup."""
        with patch("operator_mcp.skill_loader._SKILLS_DIRS", [skills_dir]), \
             patch("operator_mcp.skill_loader._SKILL_FILES", {}):
            content = load_skill("operator-chat")
            assert content is not None
            assert "Chat Skill" in content

    def test_missing_file_returns_none(self, skills_dir):
        with patch("operator_mcp.skill_loader._SKILLS_DIRS", [skills_dir]), \
             patch("operator_mcp.skill_loader._SKILL_FILES", {"missing": "missing.md"}):
            content = load_skill("missing")
            assert content is None

    def test_load_kumiho_sdk_skill(self, skills_dir, tmp_path):
        kumiho_dir = tmp_path / "kumiho_skills"
        kumiho_dir.mkdir()
        (kumiho_dir / "kumiho-sdk.md").write_text("# Kumiho SDK\nExact graph operations.")

        with patch("operator_mcp.skill_loader._SKILLS_DIRS", [str(kumiho_dir)]):
            content = load_skill("kumiho-sdk")
            assert content is not None
            assert "Kumiho SDK" in content


class TestListSkills:
    def test_list_all(self, skills_dir):
        with patch("operator_mcp.skill_loader._SKILLS_DIRS", [skills_dir]):
            skills = list_skills()
            assert len(skills) == 5
            names = {s["name"] for s in skills}
            assert "operator-orchestrator" in names
            assert "operator-loop" in names

    def test_titles_extracted(self, skills_dir):
        with patch("operator_mcp.skill_loader._SKILLS_DIRS", [skills_dir]):
            skills = list_skills()
            titles = {s["name"]: s["title"] for s in skills}
            assert titles["operator-orchestrator"] == "Orchestrator Skill"

    def test_empty_dir(self, tmp_path):
        empty = str(tmp_path / "empty_skills")
        with patch("operator_mcp.skill_loader._SKILLS_DIRS", [empty]):
            skills = list_skills()
            assert skills == []

    def test_nonexistent_dir(self):
        with patch("operator_mcp.skill_loader._SKILLS_DIRS", ["/nonexistent/path"]):
            skills = list_skills()
            assert skills == []


class TestLoadSkillsForPattern:
    def test_team_pattern(self, skills_dir):
        with patch("operator_mcp.skill_loader._SKILLS_DIRS", [skills_dir]), \
             patch("operator_mcp.skill_loader._SKILL_FILES", {
                 "operator-orchestrator": "operator-orchestrator.md",
                 "operator-chat": "operator-chat.md",
             }):
            content = load_skills_for_pattern("team")
            assert "Orchestrator Skill" in content
            assert "Chat Skill" in content

    def test_loop_pattern(self, skills_dir):
        with patch("operator_mcp.skill_loader._SKILLS_DIRS", [skills_dir]), \
             patch("operator_mcp.skill_loader._SKILL_FILES", {
                 "operator-loop": "operator-loop.md",
                 "operator-chat": "operator-chat.md",
             }):
            content = load_skills_for_pattern("loop")
            assert "Loop Skill" in content
            assert "Chat Skill" in content

    def test_committee_pattern(self, skills_dir):
        with patch("operator_mcp.skill_loader._SKILLS_DIRS", [skills_dir]), \
             patch("operator_mcp.skill_loader._SKILL_FILES", {
                 "operator-committee": "operator-committee.md",
             }):
            content = load_skills_for_pattern("committee")
            assert "Committee Skill" in content

    def test_unknown_pattern_loads_all(self, skills_dir):
        with patch("operator_mcp.skill_loader._SKILLS_DIRS", [skills_dir]), \
             patch("operator_mcp.skill_loader._SKILL_FILES", {
                 "operator-orchestrator": "operator-orchestrator.md",
                 "operator-loop": "operator-loop.md",
             }):
            content = load_skills_for_pattern("unknown")
            assert "Orchestrator Skill" in content
            assert "Loop Skill" in content

    def test_separator_between_skills(self, skills_dir):
        with patch("operator_mcp.skill_loader._SKILLS_DIRS", [skills_dir]), \
             patch("operator_mcp.skill_loader._SKILL_FILES", {
                 "operator-orchestrator": "operator-orchestrator.md",
                 "operator-chat": "operator-chat.md",
             }):
            content = load_skills_for_pattern("team")
            assert "\n\n---\n\n" in content

    def test_kumiho_pattern(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "kumiho-sdk.md").write_text("# Kumiho SDK\nUse exact krefs.")

        with patch("operator_mcp.skill_loader._SKILLS_DIRS", [str(skills_dir)]), \
             patch("operator_mcp.skill_loader._SKILL_FILES", {"kumiho-sdk": "kumiho-sdk.md"}):
            content = load_skills_for_pattern("kumiho")
            assert "Kumiho SDK" in content
