"""Tests for operator.review_loop — verdict parsing, prompt building."""
from __future__ import annotations

import pytest

from operator_mcp.review_loop import parse_verdict


class TestParseVerdict:
    def test_explicit_approved(self):
        assert parse_verdict("Everything looks good.\nVERDICT: APPROVED") == "approved"

    def test_explicit_needs_changes(self):
        assert parse_verdict("Several issues found.\nVERDICT: NEEDS_CHANGES") == "needs_changes"

    def test_explicit_needs_changes_space(self):
        assert parse_verdict("VERDICT: NEEDS CHANGES") == "needs_changes"

    def test_explicit_blocked(self):
        assert parse_verdict("Critical security flaw.\nVERDICT: BLOCKED") == "blocked"

    def test_case_insensitive(self):
        assert parse_verdict("verdict: approved") == "approved"
        assert parse_verdict("Verdict: Needs_Changes") == "needs_changes"

    def test_lgtm_heuristic(self):
        assert parse_verdict("Code looks clean. LGTM!") == "approved"

    def test_approve_heuristic(self):
        assert parse_verdict("I approve this change.") == "approved"

    def test_needs_changes_heuristic(self):
        assert parse_verdict("This needs changes before merging.") == "needs_changes"

    def test_requesting_changes_heuristic(self):
        assert parse_verdict("I'm requesting changes on lines 42-50.") == "needs_changes"

    def test_empty_text(self):
        assert parse_verdict("") == "unclear"

    def test_no_verdict(self):
        assert parse_verdict("Here is my review of the code.") == "unclear"

    def test_explicit_takes_priority(self):
        """Explicit VERDICT: line should override heuristic matches."""
        text = "Code LGTM but needs minor fix.\nVERDICT: NEEDS_CHANGES"
        assert parse_verdict(text) == "needs_changes"

    def test_approved_explicit_over_needs_changes_heuristic(self):
        text = "The requested changes have been addressed.\nVERDICT: APPROVED"
        assert parse_verdict(text) == "approved"

    def test_multiline_review(self):
        text = """
## Code Review

### Issues Found
1. Missing null check on line 42
2. Unused import on line 5
3. Test coverage insufficient

### Summary
The code has several issues that need to be addressed.

VERDICT: NEEDS_CHANGES
"""
        assert parse_verdict(text) == "needs_changes"


# ── review_fix_loop now delegates to refinement_loop (#452) ──────────


@pytest.mark.asyncio
async def test_review_fix_loop_delegates_to_refinement(monkeypatch):
    """review_fix_loop is deprecated and must delegate to refinement_loop, which
    has the hardened subprocess fallback (MCP servers + system prompt + budget).
    refinement already accepts coder_agent_id/reviewer_type/fixer_type aliases,
    so the args pass through verbatim."""
    import operator_mcp.patterns.refinement as refinement
    from operator_mcp.review_loop import tool_review_fix_loop

    captured: dict = {}

    async def fake_refinement(args):
        captured["args"] = args
        return {
            "creator_agent_id": "agent-1",
            "total_rounds": 1,
            "final_verdict": "approved",
            "final_action": "accepted",
            "rounds": [],
        }

    monkeypatch.setattr(refinement, "tool_refinement_loop", fake_refinement)

    args = {
        "coder_agent_id": "agent-1",
        "cwd": "/x",
        "reviewer_type": "codex",
        "fixer_type": "claude",
    }
    result = await tool_review_fix_loop(args)

    assert captured["args"] is args, "args must pass through to refinement verbatim"
    assert result["final_verdict"] == "approved"
    assert result["total_rounds"] == 1
    # Legacy return key preserved for backwards-compatible callers.
    assert result["coder_agent_id"] == "agent-1"


@pytest.mark.asyncio
async def test_review_fix_loop_preserves_explicit_coder_agent_id(monkeypatch):
    """If refinement already returns coder_agent_id, the compat mirror must not
    clobber it with creator_agent_id."""
    import operator_mcp.patterns.refinement as refinement
    from operator_mcp.review_loop import tool_review_fix_loop

    async def fake_refinement(_args):
        return {
            "coder_agent_id": "explicit",
            "creator_agent_id": "creator",
            "final_verdict": "approved",
        }

    monkeypatch.setattr(refinement, "tool_refinement_loop", fake_refinement)

    result = await tool_review_fix_loop({"coder_agent_id": "explicit", "cwd": "/x"})
    assert result["coder_agent_id"] == "explicit"


@pytest.mark.asyncio
async def test_review_fix_loop_passes_through_error_results(monkeypatch):
    """A non-success (e.g. validation error) result from refinement is returned
    unchanged — no coder_agent_id is fabricated."""
    import operator_mcp.patterns.refinement as refinement
    from operator_mcp.review_loop import tool_review_fix_loop

    async def fake_refinement(_args):
        return {"error": "cwd is required for refinement_loop", "code": "missing_cwd"}

    monkeypatch.setattr(refinement, "tool_refinement_loop", fake_refinement)

    result = await tool_review_fix_loop({})
    assert result["code"] == "missing_cwd"
    assert "coder_agent_id" not in result
