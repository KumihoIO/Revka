"""Tests for kref artifact passing between team stages."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from operator_mcp.agent_state import AGENTS, ManagedAgent
from operator_mcp.agent_subprocess import compose_agent_prompt
from operator_mcp.tool_handlers.teams import (
    AgentOutcome,
    _capture_git_diff,
    _record_wave_outcomes,
    _build_upstream_handoff,
    _get_upstream_agent_ids,
    _relativize,
    tool_resolve_outcome,
    tool_get_outcome_lineage,
)


@pytest.fixture(autouse=True)
def clean_agents():
    AGENTS.clear()
    yield
    AGENTS.clear()


def _make_agent(agent_id: str, title: str, status: str = "completed", cwd: str = "/tmp") -> ManagedAgent:
    a = ManagedAgent(id=agent_id, agent_type="codex", title=title, cwd=cwd, status=status)
    return a


def _make_outcome(
    agent_id: str, title: str, role: str = "coder",
    rev_kref: str = "", files: list[str] | None = None,
    summary: str = "", error_count: int = 0, errors: list[str] | None = None,
    diff_summary: str = "",
) -> AgentOutcome:
    return AgentOutcome(
        agent_id=agent_id, title=title, role=role,
        status="completed", revision_kref=rev_kref,
        summary=summary, files=files or [],
        tool_call_count=5, error_count=error_count,
        errors=errors or [],
        diff_summary=diff_summary,
    )


# ---------------------------------------------------------------------------
# _relativize
# ---------------------------------------------------------------------------

class TestRelativize:
    def test_absolute_under_cwd(self):
        assert _relativize("/home/neo/project/src/main.py", "/home/neo/project") == "src/main.py"

    def test_absolute_outside_cwd(self):
        assert _relativize("/etc/config.txt", "/home/neo/project") == "/etc/config.txt"

    def test_empty_cwd(self):
        assert _relativize("/src/main.py", "") == "/src/main.py"

    def test_trailing_slash_on_cwd(self):
        assert _relativize("/home/neo/project/src/main.py", "/home/neo/project/") == "src/main.py"


# ---------------------------------------------------------------------------
# _record_wave_outcomes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestRecordWaveOutcomes:
    async def test_no_sdk_returns_empty(self):
        """No Kumiho SDK → graceful empty dict."""
        AGENTS["a1"] = _make_agent("a1", "coder-Alice")
        with patch("operator_mcp.tool_handlers.teams._get_sdk", return_value=None):
            result = await _record_wave_outcomes(["a1"], {}, "test-team")
        assert result == {}

    async def test_creates_item_revision_and_artifacts(self, tmp_path):
        """SDK available → creates item, revision, artifacts, returns AgentOutcome."""
        a = _make_agent("a1", "coder-Alice", cwd=str(tmp_path))
        AGENTS["a1"] = a

        mock_log = MagicMock()
        mock_log.get_summary.return_value = {
            "files_touched": ["/src/main.py", "/src/utils/helpers.py"],
            "last_message": "Done implementing feature",
            "tool_call_count": 12,
            "error_count": 0,
        }
        mock_log.get_errors.return_value = []

        mock_sdk = AsyncMock()
        mock_sdk.ensure_space = AsyncMock()
        mock_sdk.create_item = AsyncMock(return_value={"kref": "kref://Revka/Outcomes/test-team-coder-Alice-a1.outcome"})
        mock_sdk.create_revision = AsyncMock(return_value={"kref": "kref://Revka/Outcomes/test-team-coder-Alice-a1.outcome?r=1"})
        mock_sdk.create_artifact = AsyncMock(return_value={})

        with patch("operator_mcp.tool_handlers.teams._get_sdk", return_value=mock_sdk), \
             patch("operator_mcp.run_log.get_log", return_value=mock_log):
            result = await _record_wave_outcomes(["a1"], {}, "test-team", cwd=str(tmp_path))

        assert "a1" in result
        outcome = result["a1"]
        assert isinstance(outcome, AgentOutcome)
        assert outcome.revision_kref == "kref://Revka/Outcomes/test-team-coder-Alice-a1.outcome?r=1"
        assert outcome.role == "coder"
        assert len(outcome.files) == 2
        assert outcome.summary.startswith("Done implementing")

        # Verify item created under Revka/Outcomes
        mock_sdk.create_item.assert_called_once()
        call_args = mock_sdk.create_item.call_args
        assert call_args[0][0] == "/Revka/Outcomes"
        assert call_args[0][2] == "outcome"

        # Verify 2 artifacts created
        assert mock_sdk.create_artifact.call_count == 2

    async def test_creates_derived_from_edges(self, tmp_path):
        """When upstream_rev_krefs provided, creates DERIVED_FROM edges."""
        AGENTS["a1"] = _make_agent("a1", "reviewer-Bob", cwd=str(tmp_path))

        mock_log = MagicMock()
        mock_log.get_summary.return_value = {
            "files_touched": [], "last_message": "Review done",
            "tool_call_count": 3, "error_count": 0,
        }
        mock_log.get_errors.return_value = []

        mock_sdk = AsyncMock()
        mock_sdk.ensure_space = AsyncMock()
        mock_sdk.create_item = AsyncMock(return_value={"kref": "kref://item"})
        mock_sdk.create_revision = AsyncMock(return_value={"kref": "kref://item?r=1"})
        mock_sdk.create_edge = AsyncMock()

        upstream = ["kref://upstream-coder?r=1", "kref://upstream-researcher?r=1"]

        with patch("operator_mcp.tool_handlers.teams._get_sdk", return_value=mock_sdk), \
             patch("operator_mcp.run_log.get_log", return_value=mock_log):
            result = await _record_wave_outcomes(
                ["a1"], {}, "test-team",
                upstream_rev_krefs=upstream,
            )

        assert "a1" in result
        # Two DERIVED_FROM edges created
        assert mock_sdk.create_edge.call_count == 2
        edge_calls = mock_sdk.create_edge.call_args_list
        assert edge_calls[0][0] == ("kref://item?r=1", "kref://upstream-coder?r=1", "DERIVED_FROM")
        assert edge_calls[1][0] == ("kref://item?r=1", "kref://upstream-researcher?r=1", "DERIVED_FROM")

    async def test_multiple_agents_in_wave(self, tmp_path):
        """Multiple agents in same wave each get their own outcome."""
        AGENTS["a1"] = _make_agent("a1", "coder-Alice", cwd=str(tmp_path))
        AGENTS["a2"] = _make_agent("a2", "tester-Bob", cwd=str(tmp_path))

        mock_log_a1 = MagicMock()
        mock_log_a1.get_summary.return_value = {
            "files_touched": ["/src/main.py"], "last_message": "Done",
            "tool_call_count": 8, "error_count": 0,
        }
        mock_log_a1.get_errors.return_value = []
        mock_log_a2 = MagicMock()
        mock_log_a2.get_summary.return_value = {
            "files_touched": ["/tests/test_main.py"], "last_message": "Tests pass",
            "tool_call_count": 4, "error_count": 0,
        }
        mock_log_a2.get_errors.return_value = []

        def fake_get_log(aid):
            return {"a1": mock_log_a1, "a2": mock_log_a2}.get(aid)

        call_count = [0]

        async def fake_create_item(space, name, kind, metadata=None):
            call_count[0] += 1
            return {"kref": f"kref://Revka/Outcomes/{name}.outcome"}

        async def fake_create_revision(item_kref, metadata=None, tag=None):
            return {"kref": f"{item_kref}?r=1"}

        mock_sdk = AsyncMock()
        mock_sdk.ensure_space = AsyncMock()
        mock_sdk.create_item = AsyncMock(side_effect=fake_create_item)
        mock_sdk.create_revision = AsyncMock(side_effect=fake_create_revision)
        mock_sdk.create_artifact = AsyncMock(return_value={})

        with patch("operator_mcp.tool_handlers.teams._get_sdk", return_value=mock_sdk), \
             patch("operator_mcp.run_log.get_log", side_effect=fake_get_log):
            result = await _record_wave_outcomes(["a1", "a2"], {}, "test-team")

        assert len(result) == 2
        assert isinstance(result["a1"], AgentOutcome)
        assert isinstance(result["a2"], AgentOutcome)
        assert call_count[0] == 2

    async def test_agent_with_no_files(self, tmp_path):
        """Agent that touched no files still gets an outcome (no artifacts)."""
        AGENTS["a1"] = _make_agent("a1", "researcher-Eve", cwd=str(tmp_path))

        mock_log = MagicMock()
        mock_log.get_summary.return_value = {
            "files_touched": [], "last_message": "Analysis complete",
            "tool_call_count": 15, "error_count": 0,
        }
        mock_log.get_errors.return_value = []

        mock_sdk = AsyncMock()
        mock_sdk.ensure_space = AsyncMock()
        mock_sdk.create_item = AsyncMock(return_value={"kref": "kref://item.outcome"})
        mock_sdk.create_revision = AsyncMock(return_value={"kref": "kref://item.outcome?r=1"})
        mock_sdk.create_artifact = AsyncMock()

        with patch("operator_mcp.tool_handlers.teams._get_sdk", return_value=mock_sdk), \
             patch("operator_mcp.run_log.get_log", return_value=mock_log):
            result = await _record_wave_outcomes(["a1"], {}, "test-team")

        assert "a1" in result
        assert result["a1"].files == []
        mock_sdk.create_artifact.assert_not_called()

    async def test_artifact_failure_does_not_break_outcome(self, tmp_path):
        """Artifact creation failure doesn't prevent the outcome from being recorded."""
        AGENTS["a1"] = _make_agent("a1", "coder-Alice", cwd=str(tmp_path))

        mock_log = MagicMock()
        mock_log.get_summary.return_value = {
            "files_touched": ["/src/main.py", "/src/broken.py"],
            "last_message": "Done", "tool_call_count": 6, "error_count": 0,
        }
        mock_log.get_errors.return_value = []

        mock_sdk = AsyncMock()
        mock_sdk.ensure_space = AsyncMock()
        mock_sdk.create_item = AsyncMock(return_value={"kref": "kref://item.outcome"})
        mock_sdk.create_revision = AsyncMock(return_value={"kref": "kref://item.outcome?r=1"})
        mock_sdk.create_artifact = AsyncMock(side_effect=[{}, RuntimeError("conflict")])

        with patch("operator_mcp.tool_handlers.teams._get_sdk", return_value=mock_sdk), \
             patch("operator_mcp.run_log.get_log", return_value=mock_log):
            result = await _record_wave_outcomes(["a1"], {}, "test-team")

        assert "a1" in result
        assert result["a1"].revision_kref == "kref://item.outcome?r=1"

    async def test_captures_errors_in_outcome(self, tmp_path):
        """Agent errors are captured in the outcome metadata."""
        AGENTS["a1"] = _make_agent("a1", "coder-Alice", status="error", cwd=str(tmp_path))

        mock_log = MagicMock()
        mock_log.get_summary.return_value = {
            "files_touched": ["/src/main.py"],
            "last_message": "Build failed",
            "tool_call_count": 10, "error_count": 2,
        }
        mock_log.get_errors.return_value = [
            {"error": "SyntaxError: unexpected indent"},
            {"error": "Build step failed with exit code 1"},
        ]

        mock_sdk = AsyncMock()
        mock_sdk.ensure_space = AsyncMock()
        mock_sdk.create_item = AsyncMock(return_value={"kref": "kref://item.outcome"})
        mock_sdk.create_revision = AsyncMock(return_value={"kref": "kref://item.outcome?r=1"})
        mock_sdk.create_artifact = AsyncMock(return_value={})

        with patch("operator_mcp.tool_handlers.teams._get_sdk", return_value=mock_sdk), \
             patch("operator_mcp.run_log.get_log", return_value=mock_log):
            result = await _record_wave_outcomes(["a1"], {}, "test-team")

        outcome = result["a1"]
        assert outcome.error_count == 2
        assert len(outcome.errors) == 2
        assert "SyntaxError" in outcome.errors[0]

    async def test_relative_artifact_names(self, tmp_path):
        """Artifacts use relative paths from cwd as names."""
        cwd = str(tmp_path)
        fpath = f"{cwd}/src/main.py"
        AGENTS["a1"] = _make_agent("a1", "coder-Alice", cwd=cwd)

        mock_log = MagicMock()
        mock_log.get_summary.return_value = {
            "files_touched": [fpath], "last_message": "Done",
            "tool_call_count": 1, "error_count": 0,
        }
        mock_log.get_errors.return_value = []

        mock_sdk = AsyncMock()
        mock_sdk.ensure_space = AsyncMock()
        mock_sdk.create_item = AsyncMock(return_value={"kref": "kref://item.outcome"})
        mock_sdk.create_revision = AsyncMock(return_value={"kref": "kref://item.outcome?r=1"})
        mock_sdk.create_artifact = AsyncMock(return_value={})

        with patch("operator_mcp.tool_handlers.teams._get_sdk", return_value=mock_sdk), \
             patch("operator_mcp.run_log.get_log", return_value=mock_log):
            await _record_wave_outcomes(["a1"], {}, "test-team", cwd=cwd)

        # Artifact name should be relative, location should be absolute
        artifact_call = mock_sdk.create_artifact.call_args
        assert artifact_call[0][1] == "src/main.py"  # name = relative
        assert artifact_call[0][2] == fpath  # location = absolute


# ---------------------------------------------------------------------------
# _get_upstream_agent_ids
# ---------------------------------------------------------------------------

class TestGetUpstreamAgentIds:
    def test_depends_on(self):
        members = [{"kref": "k1"}, {"kref": "k2"}]
        edges = [{"from_kref": "k2", "to_kref": "k1", "edge_type": "DEPENDS_ON"}]
        spawned_map = {"k1": {"agent_id": "a1", "name": "A", "role": "coder"}}
        assert _get_upstream_agent_ids(1, members, edges, spawned_map) == ["a1"]

    def test_supports(self):
        members = [{"kref": "k1"}, {"kref": "k2"}]
        edges = [{"from_kref": "k1", "to_kref": "k2", "edge_type": "SUPPORTS"}]
        spawned_map = {"k1": {"agent_id": "a1", "name": "A", "role": "coder"}}
        assert _get_upstream_agent_ids(1, members, edges, spawned_map) == ["a1"]

    def test_no_edges(self):
        members = [{"kref": "k1"}]
        assert _get_upstream_agent_ids(0, members, [], {}) == []


# ---------------------------------------------------------------------------
# _build_upstream_handoff
# ---------------------------------------------------------------------------

class TestBuildUpstreamHandoff:
    def test_no_upstream_returns_empty(self):
        members = [{"kref": "k1", "name": "A"}]
        result = _build_upstream_handoff(0, members, [], {}, {})
        assert result == ""

    def test_no_outcomes_falls_back_to_raw_stdout(self):
        """When outcomes are empty, shows raw stdout from AGENTS."""
        members = [{"kref": "k1"}, {"kref": "k2"}]
        edges = [{"from_kref": "k2", "to_kref": "k1", "edge_type": "DEPENDS_ON"}]
        spawned_map = {"k1": {"agent_id": "a1", "name": "A", "role": "coder"}}

        a = _make_agent("a1", "coder-A", status="completed")
        a.stdout_buffer = "Built the feature successfully"
        AGENTS["a1"] = a

        result = _build_upstream_handoff(1, members, edges, spawned_map, {})
        assert "no structured outcome" in result
        assert "Built the feature" in result

    def test_structured_handoff_with_files(self):
        """Structured outcome includes kref, summary, and file listing."""
        members = [
            {"kref": "k1", "name": "A", "role": "coder"},
            {"kref": "k2", "name": "B", "role": "reviewer"},
        ]
        edges = [{"from_kref": "k2", "to_kref": "k1", "edge_type": "DEPENDS_ON"}]
        spawned_map = {"k1": {"agent_id": "a1", "name": "A", "role": "coder"}}

        outcomes = {
            "a1": _make_outcome(
                "a1", "coder-Alice", role="coder",
                rev_kref="kref://Revka/Outcomes/item.outcome?r=1",
                files=["/home/neo/project/src/main.py", "/home/neo/project/tests/test_main.py"],
                summary="Implemented auth middleware with JWT validation",
            ),
        }

        result = _build_upstream_handoff(
            1, members, edges, spawned_map, outcomes,
            cwd="/home/neo/project",
        )
        assert "Upstream Deliverables" in result
        assert "kref://Revka/Outcomes/item.outcome?r=1" in result
        assert "coder-Alice" in result
        assert "src/main.py" in result  # relative path
        assert "tests/test_main.py" in result
        assert "Implemented auth middleware" in result

    def test_shows_errors_when_present(self):
        """Upstream errors are visible to downstream agents."""
        members = [{"kref": "k1"}, {"kref": "k2"}]
        edges = [{"from_kref": "k2", "to_kref": "k1", "edge_type": "DEPENDS_ON"}]
        spawned_map = {"k1": {"agent_id": "a1", "name": "A", "role": "coder"}}

        outcomes = {
            "a1": _make_outcome(
                "a1", "coder-Alice", rev_kref="kref://item?r=1",
                error_count=2, errors=["SyntaxError: unexpected indent"],
            ),
        }

        result = _build_upstream_handoff(1, members, edges, spawned_map, outcomes)
        assert "Errors" in result
        assert "SyntaxError" in result

    def test_multiple_upstream_agents(self):
        """Multiple upstream agents all appear in handoff."""
        members = [
            {"kref": "k1", "name": "A"},
            {"kref": "k2", "name": "B"},
            {"kref": "k3", "name": "C"},
        ]
        edges = [
            {"from_kref": "k3", "to_kref": "k1", "edge_type": "DEPENDS_ON"},
            {"from_kref": "k3", "to_kref": "k2", "edge_type": "DEPENDS_ON"},
        ]
        spawned_map = {
            "k1": {"agent_id": "a1", "name": "A", "role": "researcher"},
            "k2": {"agent_id": "a2", "name": "B", "role": "coder"},
        }
        outcomes = {
            "a1": _make_outcome("a1", "researcher-A", rev_kref="kref://a-out?r=1", summary="Research done"),
            "a2": _make_outcome("a2", "coder-B", rev_kref="kref://b-out?r=1", summary="Code done",
                                files=["/src/impl.py"]),
        }

        result = _build_upstream_handoff(2, members, edges, spawned_map, outcomes)
        assert "researcher-A" in result
        assert "coder-B" in result
        assert "kref://a-out?r=1" in result
        assert "kref://b-out?r=1" in result

    def test_supports_edge_type(self):
        """SUPPORTS edges correctly identify upstream."""
        members = [{"kref": "k1"}, {"kref": "k2"}]
        edges = [{"from_kref": "k1", "to_kref": "k2", "edge_type": "SUPPORTS"}]
        spawned_map = {"k1": {"agent_id": "a1", "name": "A", "role": "coder"}}

        outcomes = {
            "a1": _make_outcome("a1", "coder-A", rev_kref="kref://out?r=1"),
        }

        result = _build_upstream_handoff(1, members, edges, spawned_map, outcomes)
        assert "kref://out?r=1" in result

    def test_diff_summary_included_in_handoff(self):
        """When upstream outcome has a diff_summary, it appears in handoff."""
        members = [{"kref": "k1"}, {"kref": "k2"}]
        edges = [{"from_kref": "k2", "to_kref": "k1", "edge_type": "DEPENDS_ON"}]
        spawned_map = {"k1": {"agent_id": "a1", "name": "A", "role": "coder"}}

        outcomes = {
            "a1": _make_outcome(
                "a1", "coder-Alice", rev_kref="kref://item?r=1",
                files=["/src/main.py"],
                diff_summary="diff --git a/src/main.py\n+def new_function():\n+    pass",
            ),
        }

        result = _build_upstream_handoff(1, members, edges, spawned_map, outcomes)
        assert "Changes" in result
        assert "diff --git" in result
        assert "new_function" in result


# ---------------------------------------------------------------------------
# _capture_git_diff
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestCaptureGitDiff:
    async def test_empty_files_returns_empty(self):
        result = await _capture_git_diff([], "/tmp")
        assert result == ""

    async def test_empty_cwd_returns_empty(self):
        result = await _capture_git_diff(["/src/main.py"], "")
        assert result == ""

    async def test_captures_diff_output(self, tmp_path):
        """In a real git repo, captures diff text."""
        import subprocess
        # Set up a minimal git repo with a change
        subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True)
        fpath = tmp_path / "hello.py"
        fpath.write_text("print('hello')\n")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True)
        # Make a change
        fpath.write_text("print('hello world')\n")

        result = await _capture_git_diff([str(fpath)], str(tmp_path))
        assert "hello world" in result

    async def test_non_git_dir_returns_empty(self, tmp_path):
        """Non-git directory returns empty string gracefully."""
        fpath = tmp_path / "file.py"
        fpath.write_text("x = 1\n")
        result = await _capture_git_diff([str(fpath)], str(tmp_path))
        assert result == ""


# ---------------------------------------------------------------------------
# _record_wave_outcomes — SUPERSEDES + diff
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestRecordWaveOutcomesSupersedesAndDiff:
    async def test_supersedes_reuses_existing_item(self, tmp_path):
        """When an existing outcome item matches team+role, reuses it."""
        AGENTS["a1"] = _make_agent("a1", "coder-Alice", cwd=str(tmp_path))

        mock_log = MagicMock()
        mock_log.get_summary.return_value = {
            "files_touched": [], "last_message": "Done",
            "tool_call_count": 3, "error_count": 0,
        }
        mock_log.get_errors.return_value = []

        existing_item_kref = "kref://Revka/Outcomes/test-team-coder-Alice.outcome"
        prev_rev_kref = "kref://Revka/Outcomes/test-team-coder-Alice.outcome?r=1"

        mock_sdk = AsyncMock()
        mock_sdk.ensure_space = AsyncMock()
        # search returns an existing item matching team+role
        mock_sdk.search = AsyncMock(return_value=[{
            "item": {
                "kref": existing_item_kref,
                "metadata": {"team": "test-team", "role": "coder"},
            }
        }])
        mock_sdk.get_latest_revision = AsyncMock(return_value={"kref": prev_rev_kref})
        # Should NOT call create_item since we reuse existing
        mock_sdk.create_item = AsyncMock(return_value={"kref": "kref://should-not-be-used"})
        mock_sdk.create_revision = AsyncMock(return_value={"kref": f"{existing_item_kref}?r=2"})
        mock_sdk.create_edge = AsyncMock()

        with patch("operator_mcp.tool_handlers.teams._get_sdk", return_value=mock_sdk), \
             patch("operator_mcp.run_log.get_log", return_value=mock_log), \
             patch("operator_mcp.tool_handlers.teams._capture_git_diff", return_value=""):
            result = await _record_wave_outcomes(["a1"], {}, "test-team", cwd=str(tmp_path))

        assert "a1" in result
        # create_item should NOT have been called — item was reused
        mock_sdk.create_item.assert_not_called()
        # Revision created on existing item
        mock_sdk.create_revision.assert_called_once()
        rev_call = mock_sdk.create_revision.call_args
        assert rev_call[0][0] == existing_item_kref
        # DERIVED_FROM edge to previous published revision (SUPERSEDES)
        mock_sdk.create_edge.assert_called_once()
        edge_call = mock_sdk.create_edge.call_args
        assert edge_call[0][1] == prev_rev_kref
        assert edge_call[0][2] == "DERIVED_FROM"

    async def test_supersedes_search_failure_falls_back_to_new_item(self, tmp_path):
        """If SUPERSEDES search fails, creates a new item normally."""
        AGENTS["a1"] = _make_agent("a1", "coder-Alice", cwd=str(tmp_path))

        mock_log = MagicMock()
        mock_log.get_summary.return_value = {
            "files_touched": [], "last_message": "Done",
            "tool_call_count": 1, "error_count": 0,
        }
        mock_log.get_errors.return_value = []

        mock_sdk = AsyncMock()
        mock_sdk.ensure_space = AsyncMock()
        mock_sdk.search = AsyncMock(side_effect=RuntimeError("search broken"))
        mock_sdk.create_item = AsyncMock(return_value={"kref": "kref://new-item.outcome"})
        mock_sdk.create_revision = AsyncMock(return_value={"kref": "kref://new-item.outcome?r=1"})

        with patch("operator_mcp.tool_handlers.teams._get_sdk", return_value=mock_sdk), \
             patch("operator_mcp.run_log.get_log", return_value=mock_log), \
             patch("operator_mcp.tool_handlers.teams._capture_git_diff", return_value=""):
            result = await _record_wave_outcomes(["a1"], {}, "test-team", cwd=str(tmp_path))

        assert "a1" in result
        mock_sdk.create_item.assert_called_once()

    async def test_diff_captured_and_stored_as_artifact(self, tmp_path):
        """Git diff is captured and stored as changes.diff artifact."""
        AGENTS["a1"] = _make_agent("a1", "coder-Alice", cwd=str(tmp_path))

        mock_log = MagicMock()
        mock_log.get_summary.return_value = {
            "files_touched": ["/src/main.py"], "last_message": "Done",
            "tool_call_count": 5, "error_count": 0,
        }
        mock_log.get_errors.return_value = []

        diff_text = "diff --git a/src/main.py\n+def hello(): pass"

        mock_sdk = AsyncMock()
        mock_sdk.ensure_space = AsyncMock()
        mock_sdk.search = AsyncMock(return_value=[])
        mock_sdk.create_item = AsyncMock(return_value={"kref": "kref://item.outcome"})
        mock_sdk.create_revision = AsyncMock(return_value={"kref": "kref://item.outcome?r=1"})
        mock_sdk.create_artifact = AsyncMock(return_value={})

        with patch("operator_mcp.tool_handlers.teams._get_sdk", return_value=mock_sdk), \
             patch("operator_mcp.run_log.get_log", return_value=mock_log), \
             patch("operator_mcp.tool_handlers.teams._capture_git_diff", return_value=diff_text):
            result = await _record_wave_outcomes(["a1"], {}, "test-team", cwd=str(tmp_path))

        outcome = result["a1"]
        assert outcome.diff_summary == diff_text[:3000]

        # Should have 2 calls: one for the file artifact, one for changes.diff
        assert mock_sdk.create_artifact.call_count == 2
        diff_call = mock_sdk.create_artifact.call_args_list[1]
        assert diff_call[0][1] == "changes.diff"

    async def test_no_diff_means_no_diff_artifact(self, tmp_path):
        """When git diff returns empty, no changes.diff artifact is created."""
        AGENTS["a1"] = _make_agent("a1", "coder-Alice", cwd=str(tmp_path))

        mock_log = MagicMock()
        mock_log.get_summary.return_value = {
            "files_touched": ["/src/main.py"], "last_message": "Done",
            "tool_call_count": 5, "error_count": 0,
        }
        mock_log.get_errors.return_value = []

        mock_sdk = AsyncMock()
        mock_sdk.ensure_space = AsyncMock()
        mock_sdk.search = AsyncMock(return_value=[])
        mock_sdk.create_item = AsyncMock(return_value={"kref": "kref://item.outcome"})
        mock_sdk.create_revision = AsyncMock(return_value={"kref": "kref://item.outcome?r=1"})
        mock_sdk.create_artifact = AsyncMock(return_value={})

        with patch("operator_mcp.tool_handlers.teams._get_sdk", return_value=mock_sdk), \
             patch("operator_mcp.run_log.get_log", return_value=mock_log), \
             patch("operator_mcp.tool_handlers.teams._capture_git_diff", return_value=""):
            result = await _record_wave_outcomes(["a1"], {}, "test-team", cwd=str(tmp_path))

        # Only 1 artifact call (the file), not 2 (no diff artifact)
        assert mock_sdk.create_artifact.call_count == 1
        assert result["a1"].diff_summary == ""


# ---------------------------------------------------------------------------
# tool_resolve_outcome
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestToolResolveOutcome:
    async def test_missing_kref(self):
        result = await tool_resolve_outcome({})
        assert "error" in result

    async def test_no_sdk(self):
        with patch("operator_mcp.tool_handlers.teams._get_sdk", return_value=None):
            result = await tool_resolve_outcome({"revision_kref": "kref://item?r=1"})
        assert "error" in result

    async def test_resolves_artifacts_and_edges(self):
        mock_sdk = AsyncMock()
        mock_sdk.get_artifacts = AsyncMock(return_value=[
            {"name": "src/main.py", "location": "/home/neo/src/main.py", "kref": "kref://art1"},
            {"name": "changes.diff", "location": "/tmp/a1.diff", "kref": "kref://art2"},
        ])
        mock_sdk.get_edges = AsyncMock(return_value=[
            {"source_kref": "kref://item?r=1", "target_kref": "kref://upstream?r=1", "edge_type": "DERIVED_FROM"},
        ])

        with patch("operator_mcp.tool_handlers.teams._get_sdk", return_value=mock_sdk):
            result = await tool_resolve_outcome({"revision_kref": "kref://item?r=1"})

        assert result["revision_kref"] == "kref://item?r=1"
        assert result["artifact_count"] == 2
        assert result["artifacts"][0]["name"] == "src/main.py"
        assert result["artifacts"][1]["name"] == "changes.diff"
        assert len(result["edges"]) == 1
        assert result["edges"][0]["type"] == "DERIVED_FROM"

    async def test_sdk_error_returns_error(self):
        mock_sdk = AsyncMock()
        mock_sdk.get_artifacts = AsyncMock(side_effect=RuntimeError("connection lost"))

        with patch("operator_mcp.tool_handlers.teams._get_sdk", return_value=mock_sdk):
            result = await tool_resolve_outcome({"revision_kref": "kref://item?r=1"})
        assert "error" in result


# ---------------------------------------------------------------------------
# tool_get_outcome_lineage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestToolGetOutcomeLineage:
    async def test_missing_kref(self):
        result = await tool_get_outcome_lineage({})
        assert "error" in result

    async def test_no_sdk(self):
        with patch("operator_mcp.tool_handlers.teams._get_sdk", return_value=None):
            result = await tool_get_outcome_lineage({"revision_kref": "kref://item?r=1"})
        assert "error" in result

    async def test_walks_upstream_and_downstream(self):
        mock_sdk = AsyncMock()
        # Outgoing edges (upstream — what this was derived from)
        mock_sdk.get_edges = AsyncMock(side_effect=[
            # First call: outgoing (direction=1)
            [{"edge_type": "DERIVED_FROM", "target_kref": "kref://coder-out?r=1"}],
            # Second call: incoming (direction=2)
            [{"edge_type": "DERIVED_FROM", "source_kref": "kref://reviewer-out?r=1"}],
        ])
        mock_sdk.get_artifacts = AsyncMock(side_effect=[
            # Artifacts for upstream
            [{"name": "src/impl.py", "location": "/src/impl.py"}],
            # Artifacts for downstream
            [{"name": "review.md", "location": "/review.md"}],
        ])

        with patch("operator_mcp.tool_handlers.teams._get_sdk", return_value=mock_sdk):
            result = await tool_get_outcome_lineage({"revision_kref": "kref://item?r=1"})

        assert result["revision_kref"] == "kref://item?r=1"
        assert result["upstream_count"] == 1
        assert result["upstream"][0]["revision_kref"] == "kref://coder-out?r=1"
        assert result["upstream"][0]["artifacts"][0]["name"] == "src/impl.py"
        assert result["downstream_count"] == 1
        assert result["downstream"][0]["revision_kref"] == "kref://reviewer-out?r=1"

    async def test_no_lineage_returns_empty(self):
        mock_sdk = AsyncMock()
        mock_sdk.get_edges = AsyncMock(return_value=[])

        with patch("operator_mcp.tool_handlers.teams._get_sdk", return_value=mock_sdk):
            result = await tool_get_outcome_lineage({"revision_kref": "kref://item?r=1"})

        assert result["upstream"] == []
        assert result["downstream"] == []
        assert result["upstream_count"] == 0
        assert result["downstream_count"] == 0


# ---------------------------------------------------------------------------
# compose_agent_prompt — upstream_deliverables
# ---------------------------------------------------------------------------

class TestComposeAgentPromptDeliverables:
    def test_no_deliverables(self):
        prompt = compose_agent_prompt("Alice", "coder", "", [], "Build feature")
        assert "Upstream Deliverables" not in prompt
        assert "Build feature" in prompt

    def test_with_deliverables(self):
        deliverables = (
            "### coder-Bob — completed\n"
            "- **Outcome kref**: `kref://item?r=1`\n"
            "- **Files produced** (1):\n"
            "  - `src/main.py` → `/home/neo/src/main.py`"
        )
        prompt = compose_agent_prompt("Alice", "reviewer", "", [], "Review code", upstream_deliverables=deliverables)
        assert "Upstream Deliverables" in prompt
        assert "coder-Bob" in prompt
        assert "kref://item?r=1" in prompt
        assert "How to use deliverables" in prompt
        assert "Review code" in prompt

    def test_deliverables_before_task(self):
        """Deliverables section appears before the Task section."""
        deliverables = "### upstream\n- kref: `kref://x`"
        prompt = compose_agent_prompt("A", "reviewer", "", [], "Do review", upstream_deliverables=deliverables)
        deliv_pos = prompt.index("Upstream Deliverables")
        task_pos = prompt.index("## Task")
        assert deliv_pos < task_pos
