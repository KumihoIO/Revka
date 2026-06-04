"""Tests for KumihoTeamClient.resolve_team_kref — team name/kref resolution."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from operator_mcp.kumiho_clients import KumihoTeamClient


@pytest.fixture
def team_client():
    """Create a KumihoTeamClient with SDK/HTTP disabled."""
    with patch.dict("os.environ", {"KUMIHO_API_URL": "", "KUMIHO_AUTH_TOKEN": ""}):
        with patch("operator_mcp.kumiho_clients._get_sdk", return_value=None):
            client = KumihoTeamClient()
            client._available = True  # pretend available for tests
            return client


MOCK_TEAMS = [
    {
        "kref": "kref://Revka/Teams/Assessment.bundle",
        "item_name": "Assessment.bundle",
        "deprecated": False,
        "metadata": {"description": "Assessment team"},
    },
    {
        "kref": "kref://Revka/Teams/CodeReview.bundle",
        "item_name": "CodeReview.bundle",
        "deprecated": False,
        "metadata": {"description": "Code review team"},
    },
]


class TestResolveTeamKref:
    @pytest.mark.asyncio
    async def test_exact_kref_match(self, team_client):
        team_client.list_teams = AsyncMock(return_value=MOCK_TEAMS)
        result = await team_client.resolve_team_kref("kref://Revka/Teams/Assessment.bundle")
        assert result == "kref://Revka/Teams/Assessment.bundle"

    @pytest.mark.asyncio
    async def test_case_insensitive_kref(self, team_client):
        team_client.list_teams = AsyncMock(return_value=MOCK_TEAMS)
        result = await team_client.resolve_team_kref("kref://revka/teams/assessment.bundle")
        assert result == "kref://Revka/Teams/Assessment.bundle"

    @pytest.mark.asyncio
    async def test_bare_name_with_kind_suffix(self, team_client):
        team_client.list_teams = AsyncMock(return_value=MOCK_TEAMS)
        result = await team_client.resolve_team_kref("Assessment.bundle")
        assert result == "kref://Revka/Teams/Assessment.bundle"

    @pytest.mark.asyncio
    async def test_bare_name_without_suffix(self, team_client):
        team_client.list_teams = AsyncMock(return_value=MOCK_TEAMS)
        result = await team_client.resolve_team_kref("Assessment")
        assert result == "kref://Revka/Teams/Assessment.bundle"

    @pytest.mark.asyncio
    async def test_case_insensitive_name(self, team_client):
        team_client.list_teams = AsyncMock(return_value=MOCK_TEAMS)
        result = await team_client.resolve_team_kref("assessment")
        assert result == "kref://Revka/Teams/Assessment.bundle"

    @pytest.mark.asyncio
    async def test_kref_uri_with_wrong_suffix(self, team_client):
        """LLM might emit kref://Revka/Teams/assessment — no .bundle suffix."""
        team_client.list_teams = AsyncMock(return_value=MOCK_TEAMS)
        result = await team_client.resolve_team_kref("kref://Revka/Teams/assessment")
        assert result == "kref://Revka/Teams/Assessment.bundle"

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self, team_client):
        team_client.list_teams = AsyncMock(return_value=MOCK_TEAMS)
        result = await team_client.resolve_team_kref("nonexistent-team")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_teams_returns_none(self, team_client):
        team_client.list_teams = AsyncMock(return_value=[])
        result = await team_client.resolve_team_kref("Assessment")
        assert result is None

    @pytest.mark.asyncio
    async def test_code_review_by_name(self, team_client):
        team_client.list_teams = AsyncMock(return_value=MOCK_TEAMS)
        result = await team_client.resolve_team_kref("CodeReview")
        assert result == "kref://Revka/Teams/CodeReview.bundle"
