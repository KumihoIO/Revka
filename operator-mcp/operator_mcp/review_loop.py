"""Reviewer/fix loop — verdict parsing + the legacy ``review_fix_loop`` tool.

``review_fix_loop`` is superseded by ``refinement_loop``
(``operator_mcp/patterns/refinement.py``), which adds structured quality scoring,
trust-informed critic selection, and — crucially — a hardened subprocess
fallback that injects MCP servers + a layered system prompt and enforces the
per-agent budget. The original in-module review→fix loop spawned reviewers and
fixers through a degraded fallback (no MCP servers, no system prompt, no budget
gate) whenever the agent sidecar was down (#452), so ``tool_review_fix_loop`` now
delegates to ``tool_refinement_loop``.

Only ``parse_verdict`` (imported by tests and kept for reuse) remains here; the
review/fix execution machinery moved to ``refinement.py``.
"""
from __future__ import annotations

import re
from typing import Any

from ._log import _log


# ---------------------------------------------------------------------------
# Verdict parsing
# ---------------------------------------------------------------------------

_VERDICT_PATTERNS = [
    # Explicit structured verdicts (preferred — reviewer prompt asks for these)
    (re.compile(r"VERDICT:\s*APPROVED", re.IGNORECASE), "approved"),
    (re.compile(r"VERDICT:\s*NEEDS[_\s]?CHANGES", re.IGNORECASE), "needs_changes"),
    (re.compile(r"VERDICT:\s*BLOCKED", re.IGNORECASE), "blocked"),
    # Fallback heuristics
    (re.compile(r"\bLGTM\b", re.IGNORECASE), "approved"),
    (re.compile(r"\bapproved?\b", re.IGNORECASE), "approved"),
    (re.compile(r"\bneeds?\s+changes?\b", re.IGNORECASE), "needs_changes"),
    (re.compile(r"\brequest(?:ed|ing)?\s+changes?\b", re.IGNORECASE), "needs_changes"),
]


def parse_verdict(text: str) -> str:
    """Extract a verdict from reviewer output text.

    Returns one of: "approved", "needs_changes", "blocked", "unclear".
    Structured VERDICT: lines take priority over heuristic matches.
    """
    if not text:
        return "unclear"
    for pattern, verdict in _VERDICT_PATTERNS:
        if pattern.search(text):
            return verdict
    return "unclear"


# ---------------------------------------------------------------------------
# Tool handler (deprecated — delegates to refinement_loop)
# ---------------------------------------------------------------------------

async def tool_review_fix_loop(args: dict[str, Any]) -> dict[str, Any]:
    """Deprecated alias for ``refinement_loop`` — kept for backwards compatibility.

    The legacy in-module review→fix loop spawned reviewers/fixers through a
    degraded subprocess fallback (no MCP servers, no system prompt, no budget
    gate) when the agent sidecar was down (#452). ``refinement_loop`` supersedes
    it with a hardened fallback that provisions all three, plus structured
    scoring and trust-informed critic selection. The legacy
    ``coder_agent_id`` / ``reviewer_type`` / ``fixer_type`` args are accepted as
    backwards-compatible aliases by ``tool_refinement_loop``; the legacy
    ``coder_agent_id`` return key is mirrored back for callers that read it.
    """
    from .patterns.refinement import tool_refinement_loop

    _log("review_fix_loop: deprecated — delegating to refinement_loop (#452)")
    result = await tool_refinement_loop(args)
    # Preserve the legacy return key so existing callers keep working: refinement
    # reports the reviewed agent as ``creator_agent_id``.
    if (
        isinstance(result, dict)
        and "coder_agent_id" not in result
        and "creator_agent_id" in result
    ):
        result["coder_agent_id"] = result["creator_agent_id"]
    return result
