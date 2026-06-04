"""Tests for KumihoTeamClient.get_team — edge normalization and reconstruction.

Covers:
- Pure normalization helpers
- SDK path edge reconstruction (outgoing-only, true source/target)
- HTTP path parity
- Self-edge regression (the original bug)
- Deduplication and stable ordering
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from operator_mcp.kumiho_clients import (
    KumihoTeamClient,
    _dedupe_and_sort_edges,
    _normalize_team_edge,
    _normalize_to_item_kref,
)


# ---------------------------------------------------------------------------
# Constants used across tests
# ---------------------------------------------------------------------------

ALICE_ITEM = "kref://Revka/AgentPool/Alice.agent"
BOB_ITEM = "kref://Revka/AgentPool/Bob.agent"
CAROL_ITEM = "kref://Revka/AgentPool/Carol.agent"
EXTERNAL_ITEM = "kref://Revka/AgentPool/External.agent"

ALICE_REV = "kref://Revka/AgentPool/Alice.agent?r=3"
BOB_REV = "kref://Revka/AgentPool/Bob.agent?r=2"
CAROL_REV = "kref://Revka/AgentPool/Carol.agent?r=1"
EXTERNAL_REV = "kref://Revka/AgentPool/External.agent?r=5"

MEMBER_ITEM_KREFS = {ALICE_ITEM, BOB_ITEM, CAROL_ITEM}

REV_TO_ITEM = {
    ALICE_ITEM: ALICE_ITEM,  # stripped rev key → item
    BOB_ITEM: BOB_ITEM,
    CAROL_ITEM: CAROL_ITEM,
}


# ===================================================================
# Test group 1 — pure normalization helpers
# ===================================================================


class TestNormalizeToItemKref:
    def test_revision_kref_strips_query(self):
        result = _normalize_to_item_kref(ALICE_REV, REV_TO_ITEM)
        assert result == ALICE_ITEM

    def test_item_kref_unchanged(self):
        result = _normalize_to_item_kref(ALICE_ITEM, REV_TO_ITEM)
        assert result == ALICE_ITEM

    def test_empty_returns_empty(self):
        result = _normalize_to_item_kref("", REV_TO_ITEM)
        assert result == ""

    def test_unknown_kref_strips_query_fallback(self):
        unknown = "kref://Other/Space/Thing.kind?r=99"
        result = _normalize_to_item_kref(unknown, REV_TO_ITEM)
        assert result == "kref://Other/Space/Thing.kind"

    def test_no_query_no_map_returns_raw(self):
        raw = "kref://Other/Space/Thing.kind"
        result = _normalize_to_item_kref(raw, {})
        assert result == raw


class TestNormalizeTeamEdge:
    def test_valid_outgoing_edge(self):
        raw = {"source_kref": ALICE_REV, "target_kref": BOB_REV, "edge_type": "DEPENDS_ON"}
        result = _normalize_team_edge(raw, MEMBER_ITEM_KREFS, REV_TO_ITEM, expected_source_item=ALICE_ITEM)
        assert result == {"from_kref": ALICE_ITEM, "to_kref": BOB_ITEM, "edge_type": "DEPENDS_ON"}

    def test_incoming_edge_rejected_by_source_guard(self):
        """The original bug: iterating B, an incoming edge A→B would become B→B."""
        raw = {"source_kref": ALICE_REV, "target_kref": BOB_REV, "edge_type": "DEPENDS_ON"}
        result = _normalize_team_edge(raw, MEMBER_ITEM_KREFS, REV_TO_ITEM, expected_source_item=BOB_ITEM)
        assert result is None

    def test_self_edge_rejected(self):
        raw = {"source_kref": ALICE_REV, "target_kref": ALICE_REV, "edge_type": "DEPENDS_ON"}
        result = _normalize_team_edge(raw, MEMBER_ITEM_KREFS, REV_TO_ITEM, expected_source_item=ALICE_ITEM)
        assert result is None

    def test_external_target_rejected(self):
        raw = {"source_kref": ALICE_REV, "target_kref": EXTERNAL_REV, "edge_type": "SUPPORTS"}
        result = _normalize_team_edge(raw, MEMBER_ITEM_KREFS, REV_TO_ITEM, expected_source_item=ALICE_ITEM)
        assert result is None

    def test_external_source_rejected(self):
        raw = {"source_kref": EXTERNAL_REV, "target_kref": BOB_REV, "edge_type": "SUPPORTS"}
        result = _normalize_team_edge(raw, MEMBER_ITEM_KREFS, REV_TO_ITEM, expected_source_item=BOB_ITEM)
        assert result is None

    def test_missing_source_kref_rejected(self):
        raw = {"source_kref": "", "target_kref": BOB_REV, "edge_type": "DEPENDS_ON"}
        result = _normalize_team_edge(raw, MEMBER_ITEM_KREFS, REV_TO_ITEM)
        assert result is None

    def test_missing_target_kref_rejected(self):
        raw = {"source_kref": ALICE_REV, "target_kref": "", "edge_type": "DEPENDS_ON"}
        result = _normalize_team_edge(raw, MEMBER_ITEM_KREFS, REV_TO_ITEM)
        assert result is None

    def test_no_expected_source_allows_any_member(self):
        """Without expected_source_item, any inter-member edge is accepted."""
        raw = {"source_kref": ALICE_REV, "target_kref": BOB_REV, "edge_type": "SUPPORTS"}
        result = _normalize_team_edge(raw, MEMBER_ITEM_KREFS, REV_TO_ITEM)
        assert result == {"from_kref": ALICE_ITEM, "to_kref": BOB_ITEM, "edge_type": "SUPPORTS"}


class TestDedupeAndSortEdges:
    def test_duplicates_removed(self):
        edges = [
            {"from_kref": ALICE_ITEM, "to_kref": BOB_ITEM, "edge_type": "DEPENDS_ON"},
            {"from_kref": ALICE_ITEM, "to_kref": BOB_ITEM, "edge_type": "DEPENDS_ON"},
        ]
        result = _dedupe_and_sort_edges(edges)
        assert len(result) == 1

    def test_stable_sort_order(self):
        edges = [
            {"from_kref": CAROL_ITEM, "to_kref": ALICE_ITEM, "edge_type": "SUPPORTS"},
            {"from_kref": ALICE_ITEM, "to_kref": BOB_ITEM, "edge_type": "DEPENDS_ON"},
            {"from_kref": BOB_ITEM, "to_kref": CAROL_ITEM, "edge_type": "DEPENDS_ON"},
        ]
        result = _dedupe_and_sort_edges(edges)
        assert result[0]["from_kref"] == ALICE_ITEM
        assert result[1]["from_kref"] == BOB_ITEM
        assert result[2]["from_kref"] == CAROL_ITEM

    def test_empty_list(self):
        assert _dedupe_and_sort_edges([]) == []

    def test_different_edge_types_kept(self):
        edges = [
            {"from_kref": ALICE_ITEM, "to_kref": BOB_ITEM, "edge_type": "DEPENDS_ON"},
            {"from_kref": ALICE_ITEM, "to_kref": BOB_ITEM, "edge_type": "SUPPORTS"},
        ]
        result = _dedupe_and_sort_edges(edges)
        assert len(result) == 2


# ===================================================================
# Test group 2 — SDK path reconstruction
# ===================================================================

def _make_mock_sdk():
    """Create a mock KumihoSDKClient with sensible defaults."""
    sdk = AsyncMock()
    sdk.get_bundle_by_kref = AsyncMock(return_value={"kref": "kref://Revka/Teams/TestTeam.bundle", "name": "TestTeam"})
    sdk.get_bundle_members = AsyncMock(return_value=[
        {"item_kref": ALICE_ITEM},
        {"item_kref": BOB_ITEM},
    ])
    sdk.batch_get_revisions = AsyncMock(return_value={
        ALICE_ITEM: {
            "kref": ALICE_REV,
            "metadata": {"role": "planner", "agent_type": "codex", "expertise": "planning", "identity": "Alice"},
        },
        BOB_ITEM: {
            "kref": BOB_REV,
            "metadata": {"role": "coder", "agent_type": "codex", "expertise": "coding", "identity": "Bob"},
        },
    })
    return sdk


@pytest.fixture
def team_client():
    """Create a KumihoTeamClient with SDK/HTTP disabled."""
    with patch.dict("os.environ", {"KUMIHO_API_URL": "", "KUMIHO_AUTH_TOKEN": ""}):
        with patch("operator_mcp.kumiho_clients._get_sdk", return_value=None):
            client = KumihoTeamClient()
            client._available = True
            return client


class TestGetTeamSDK:
    @pytest.mark.asyncio
    async def test_outgoing_edge_reconstructed_correctly(self, team_client):
        sdk = _make_mock_sdk()
        sdk.get_edges = AsyncMock(side_effect=lambda rev_kref, direction: [
            {"source_kref": ALICE_REV, "target_kref": BOB_REV, "edge_type": "DEPENDS_ON"},
        ] if rev_kref == ALICE_REV else [])

        result = await team_client._get_team_sdk(sdk, "kref://Revka/Teams/TestTeam.bundle")
        assert result is not None
        edges = result["edges"]
        assert len(edges) == 1
        assert edges[0] == {"from_kref": ALICE_ITEM, "to_kref": BOB_ITEM, "edge_type": "DEPENDS_ON"}

    @pytest.mark.asyncio
    async def test_incoming_edge_does_not_become_self_edge(self, team_client):
        """Regression: the old bug where incoming A→B on B's iteration became B→B."""
        sdk = _make_mock_sdk()

        def _edge_side_effect(rev_kref, direction):
            # Even if backend erroneously returns this as "outgoing" for B,
            # source guard should catch it
            if rev_kref == BOB_REV:
                return [{"source_kref": ALICE_REV, "target_kref": BOB_REV, "edge_type": "DEPENDS_ON"}]
            return []

        sdk.get_edges = AsyncMock(side_effect=_edge_side_effect)

        result = await team_client._get_team_sdk(sdk, "kref://Revka/Teams/TestTeam.bundle")
        assert result is not None
        edges = result["edges"]
        # No self-edges should exist
        for e in edges:
            assert e["from_kref"] != e["to_kref"], f"Self-edge found: {e}"

    @pytest.mark.asyncio
    async def test_external_edge_ignored(self, team_client):
        sdk = _make_mock_sdk()
        sdk.get_edges = AsyncMock(side_effect=lambda rev_kref, direction: [
            {"source_kref": ALICE_REV, "target_kref": EXTERNAL_REV, "edge_type": "SUPPORTS"},
        ] if rev_kref == ALICE_REV else [])

        result = await team_client._get_team_sdk(sdk, "kref://Revka/Teams/TestTeam.bundle")
        assert result is not None
        assert len(result["edges"]) == 0

    @pytest.mark.asyncio
    async def test_duplicate_raw_edges_deduped(self, team_client):
        sdk = _make_mock_sdk()
        sdk.get_edges = AsyncMock(side_effect=lambda rev_kref, direction: [
            {"source_kref": ALICE_REV, "target_kref": BOB_REV, "edge_type": "DEPENDS_ON"},
            {"source_kref": ALICE_REV, "target_kref": BOB_REV, "edge_type": "DEPENDS_ON"},
        ] if rev_kref == ALICE_REV else [])

        result = await team_client._get_team_sdk(sdk, "kref://Revka/Teams/TestTeam.bundle")
        assert result is not None
        assert len(result["edges"]) == 1

    @pytest.mark.asyncio
    async def test_stable_ordering(self, team_client):
        sdk = _make_mock_sdk()
        sdk.get_bundle_members = AsyncMock(return_value=[
            {"item_kref": ALICE_ITEM},
            {"item_kref": BOB_ITEM},
            {"item_kref": CAROL_ITEM},
        ])
        sdk.batch_get_revisions = AsyncMock(return_value={
            ALICE_ITEM: {"kref": ALICE_REV, "metadata": {"role": "planner", "agent_type": "codex", "expertise": "", "identity": ""}},
            BOB_ITEM: {"kref": BOB_REV, "metadata": {"role": "coder", "agent_type": "codex", "expertise": "", "identity": ""}},
            CAROL_ITEM: {"kref": CAROL_REV, "metadata": {"role": "reviewer", "agent_type": "codex", "expertise": "", "identity": ""}},
        })

        def _edges(rev_kref, direction):
            if rev_kref == CAROL_REV:
                return [{"source_kref": CAROL_REV, "target_kref": ALICE_REV, "edge_type": "SUPPORTS"}]
            if rev_kref == ALICE_REV:
                return [{"source_kref": ALICE_REV, "target_kref": BOB_REV, "edge_type": "DEPENDS_ON"}]
            if rev_kref == BOB_REV:
                return [{"source_kref": BOB_REV, "target_kref": CAROL_REV, "edge_type": "DEPENDS_ON"}]
            return []

        sdk.get_edges = AsyncMock(side_effect=_edges)

        result = await team_client._get_team_sdk(sdk, "kref://Revka/Teams/TestTeam.bundle")
        edges = result["edges"]
        assert len(edges) == 3
        # Sorted by from_kref first
        froms = [e["from_kref"] for e in edges]
        assert froms == sorted(froms)

    @pytest.mark.asyncio
    async def test_uses_outgoing_direction(self, team_client):
        """Verify get_edges is called with direction=1 (outgoing)."""
        sdk = _make_mock_sdk()
        sdk.get_edges = AsyncMock(return_value=[])

        await team_client._get_team_sdk(sdk, "kref://Revka/Teams/TestTeam.bundle")

        for call in sdk.get_edges.call_args_list:
            assert call.kwargs.get("direction", call.args[1] if len(call.args) > 1 else None) == 1

    @pytest.mark.asyncio
    async def test_rev_kref_stripped_from_members(self, team_client):
        sdk = _make_mock_sdk()
        sdk.get_edges = AsyncMock(return_value=[])

        result = await team_client._get_team_sdk(sdk, "kref://Revka/Teams/TestTeam.bundle")
        for m in result["members"]:
            assert "rev_kref" not in m


# ===================================================================
# Test group 3 — HTTP parity
# ===================================================================

class TestGetTeamHTTPParity:
    """Ensure HTTP path produces the same normalized edges as SDK path."""

    def _make_mock_response(self, status_code=200, json_data=None):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json = MagicMock(return_value=json_data or {})
        resp.raise_for_status = MagicMock()
        return resp

    @pytest.mark.asyncio
    async def test_happy_path_edges(self, team_client):
        team_client.api_url = "http://localhost:9999"
        team_client._headers = MagicMock(return_value={})

        bundle_resp = self._make_mock_response(json_data={"kref": "kref://Revka/Teams/T.bundle", "name": "T"})
        members_resp = self._make_mock_response(json_data={"members": [{"item_kref": ALICE_ITEM}, {"item_kref": BOB_ITEM}]})
        alice_rev_resp = self._make_mock_response(json_data={"kref": ALICE_REV, "metadata": {"role": "planner", "agent_type": "codex", "expertise": "", "identity": ""}})
        bob_rev_resp = self._make_mock_response(json_data={"kref": BOB_REV, "metadata": {"role": "coder", "agent_type": "codex", "expertise": "", "identity": ""}})
        alice_edges_resp = self._make_mock_response(json_data=[
            {"source_kref": ALICE_REV, "target_kref": BOB_REV, "edge_type": "DEPENDS_ON"},
        ])
        bob_edges_resp = self._make_mock_response(json_data=[])

        async def _mock_get(url, **kwargs):
            params = kwargs.get("params", {})
            if "bundles/by-kref" in url:
                return bundle_resp
            if "bundles/members" in url:
                return members_resp
            if "revisions/by-kref" in url:
                kref = params.get("kref", "")
                if kref == ALICE_ITEM:
                    return alice_rev_resp
                if kref == BOB_ITEM:
                    return bob_rev_resp
            if "edges" in url:
                kref = params.get("kref", "")
                if kref == ALICE_REV:
                    return alice_edges_resp
                return bob_edges_resp
            return self._make_mock_response(404)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=_mock_get)

        with patch("operator_mcp.kumiho_clients.httpx.AsyncClient") as MockHttpx:
            MockHttpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockHttpx.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await team_client._get_team_http("kref://Revka/Teams/T.bundle")

        assert result is not None
        edges = result["edges"]
        assert len(edges) == 1
        assert edges[0] == {"from_kref": ALICE_ITEM, "to_kref": BOB_ITEM, "edge_type": "DEPENDS_ON"}

    @pytest.mark.asyncio
    async def test_external_edge_ignored_http(self, team_client):
        team_client.api_url = "http://localhost:9999"
        team_client._headers = MagicMock(return_value={})

        bundle_resp = self._make_mock_response(json_data={"kref": "kref://Revka/Teams/T.bundle", "name": "T"})
        members_resp = self._make_mock_response(json_data={"members": [{"item_kref": ALICE_ITEM}, {"item_kref": BOB_ITEM}]})
        alice_rev_resp = self._make_mock_response(json_data={"kref": ALICE_REV, "metadata": {"role": "planner", "agent_type": "codex", "expertise": "", "identity": ""}})
        bob_rev_resp = self._make_mock_response(json_data={"kref": BOB_REV, "metadata": {"role": "coder", "agent_type": "codex", "expertise": "", "identity": ""}})
        # Alice has edge to external
        alice_edges_resp = self._make_mock_response(json_data=[
            {"source_kref": ALICE_REV, "target_kref": EXTERNAL_REV, "edge_type": "SUPPORTS"},
        ])
        bob_edges_resp = self._make_mock_response(json_data=[])

        async def _mock_get(url, **kwargs):
            params = kwargs.get("params", {})
            if "bundles/by-kref" in url:
                return bundle_resp
            if "bundles/members" in url:
                return members_resp
            if "revisions/by-kref" in url:
                kref = params.get("kref", "")
                if kref == ALICE_ITEM:
                    return alice_rev_resp
                if kref == BOB_ITEM:
                    return bob_rev_resp
            if "edges" in url:
                kref = params.get("kref", "")
                if kref == ALICE_REV:
                    return alice_edges_resp
                return bob_edges_resp
            return self._make_mock_response(404)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=_mock_get)

        with patch("operator_mcp.kumiho_clients.httpx.AsyncClient") as MockHttpx:
            MockHttpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockHttpx.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await team_client._get_team_http("kref://Revka/Teams/T.bundle")

        assert result is not None
        assert len(result["edges"]) == 0

    @pytest.mark.asyncio
    async def test_duplicate_deduped_http(self, team_client):
        team_client.api_url = "http://localhost:9999"
        team_client._headers = MagicMock(return_value={})

        bundle_resp = self._make_mock_response(json_data={"kref": "kref://Revka/Teams/T.bundle", "name": "T"})
        members_resp = self._make_mock_response(json_data={"members": [{"item_kref": ALICE_ITEM}, {"item_kref": BOB_ITEM}]})
        alice_rev_resp = self._make_mock_response(json_data={"kref": ALICE_REV, "metadata": {"role": "planner", "agent_type": "codex", "expertise": "", "identity": ""}})
        bob_rev_resp = self._make_mock_response(json_data={"kref": BOB_REV, "metadata": {"role": "coder", "agent_type": "codex", "expertise": "", "identity": ""}})
        # Duplicate edges
        alice_edges_resp = self._make_mock_response(json_data=[
            {"source_kref": ALICE_REV, "target_kref": BOB_REV, "edge_type": "DEPENDS_ON"},
            {"source_kref": ALICE_REV, "target_kref": BOB_REV, "edge_type": "DEPENDS_ON"},
        ])
        bob_edges_resp = self._make_mock_response(json_data=[])

        async def _mock_get(url, **kwargs):
            params = kwargs.get("params", {})
            if "bundles/by-kref" in url:
                return bundle_resp
            if "bundles/members" in url:
                return members_resp
            if "revisions/by-kref" in url:
                kref = params.get("kref", "")
                if kref == ALICE_ITEM:
                    return alice_rev_resp
                if kref == BOB_ITEM:
                    return bob_rev_resp
            if "edges" in url:
                kref = params.get("kref", "")
                if kref == ALICE_REV:
                    return alice_edges_resp
                return bob_edges_resp
            return self._make_mock_response(404)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=_mock_get)

        with patch("operator_mcp.kumiho_clients.httpx.AsyncClient") as MockHttpx:
            MockHttpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockHttpx.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await team_client._get_team_http("kref://Revka/Teams/T.bundle")

        assert result is not None
        assert len(result["edges"]) == 1


# ===================================================================
# Test group 4 — round-trip regression
# ===================================================================

class TestRoundTripRegression:
    @pytest.mark.asyncio
    async def test_linear_dag_round_trip(self, team_client):
        """Planner → Coder → Reviewer should come back as exactly 2 edges."""
        sdk = _make_mock_sdk()
        sdk.get_bundle_members = AsyncMock(return_value=[
            {"item_kref": ALICE_ITEM},
            {"item_kref": BOB_ITEM},
            {"item_kref": CAROL_ITEM},
        ])
        sdk.batch_get_revisions = AsyncMock(return_value={
            ALICE_ITEM: {"kref": ALICE_REV, "metadata": {"role": "planner", "agent_type": "codex", "expertise": "", "identity": ""}},
            BOB_ITEM: {"kref": BOB_REV, "metadata": {"role": "coder", "agent_type": "codex", "expertise": "", "identity": ""}},
            CAROL_ITEM: {"kref": CAROL_REV, "metadata": {"role": "reviewer", "agent_type": "codex", "expertise": "", "identity": ""}},
        })

        def _edges(rev_kref, direction):
            if rev_kref == ALICE_REV:
                return [{"source_kref": ALICE_REV, "target_kref": BOB_REV, "edge_type": "DEPENDS_ON"}]
            if rev_kref == BOB_REV:
                return [{"source_kref": BOB_REV, "target_kref": CAROL_REV, "edge_type": "DEPENDS_ON"}]
            return []

        sdk.get_edges = AsyncMock(side_effect=_edges)

        result = await team_client._get_team_sdk(sdk, "kref://Revka/Teams/TestTeam.bundle")

        assert result is not None
        edges = result["edges"]
        assert len(edges) == 2

        edge_set = {(e["from_kref"], e["to_kref"]) for e in edges}
        assert (ALICE_ITEM, BOB_ITEM) in edge_set
        assert (BOB_ITEM, CAROL_ITEM) in edge_set

        # No self-edges
        for e in edges:
            assert e["from_kref"] != e["to_kref"]

        # Members have no rev_kref leak
        for m in result["members"]:
            assert "rev_kref" not in m

    @pytest.mark.asyncio
    async def test_no_edges_when_members_isolated(self, team_client):
        sdk = _make_mock_sdk()
        sdk.get_edges = AsyncMock(return_value=[])

        result = await team_client._get_team_sdk(sdk, "kref://Revka/Teams/TestTeam.bundle")
        assert result is not None
        assert result["edges"] == []
