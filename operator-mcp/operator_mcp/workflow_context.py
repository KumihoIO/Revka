"""Workflow-scoped memory substrate for the operator session.

Accumulates structured findings from agent completions so the orchestrating
agent can query what its children discovered without re-reading run logs.

Design:
  - In-memory store keyed by session_id, populated automatically when
    ``wait_for_agent`` observes a terminal state.
  - Optional async persistence to Kumiho (best-effort, fire-and-forget)
    so findings survive operator restarts and are queryable across sessions.
  - Deliberately thin: no new infrastructure, reuses KumihoSDKClient patterns.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ._log import _log
from .revka_config import harness_project


# ---------------------------------------------------------------------------
# Finding — a single agent's contribution to the workflow context
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class AgentFinding:
    """Structured snapshot of what an agent produced at completion."""

    agent_id: str
    title: str
    status: str  # "completed" | "error" | "closed"
    last_message: str = ""
    files_touched: list[str] = field(default_factory=list)
    error_count: int = 0
    tool_call_count: int = 0
    usage: dict[str, Any] = field(default_factory=dict)
    captured_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "title": self.title,
            "status": self.status,
            "last_message": self.last_message,
            "files_touched": self.files_touched,
            "error_count": self.error_count,
            "tool_call_count": self.tool_call_count,
            "usage": self.usage,
            "captured_at": self.captured_at,
        }


# ---------------------------------------------------------------------------
# WorkflowContext — session-scoped accumulator
# ---------------------------------------------------------------------------

_LAST_MESSAGE_CAP = 4000  # Truncate to keep context window friendly


class WorkflowContext:
    """Accumulates agent findings for the current operator session.

    Thread-safe for single-writer (the operator event loop) — no locking
    needed since all callers are on the same asyncio loop.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._findings: dict[str, AgentFinding] = {}  # agent_id -> finding
        self._kumiho_sdk: Any = None  # Set via set_kumiho_sdk()
        self._kumiho_space: str = ""  # e.g. "<harness_project>/Workflows"
        _log(f"WorkflowContext initialized for session {session_id}")

    # -- Kumiho wiring (optional) --------------------------------------------

    def set_kumiho_sdk(self, sdk: Any, space: str | None = None) -> None:
        """Inject the KumihoSDKClient for optional persistence."""
        self._kumiho_sdk = sdk
        self._kumiho_space = space if space is not None else f"{harness_project()}/Workflows"

    # -- Core API ------------------------------------------------------------

    def capture(self, wait_result: dict[str, Any]) -> AgentFinding | None:
        """Extract and store a finding from a terminal wait_for_agent result.

        Returns the AgentFinding if captured, None if the result is
        non-terminal or already captured for this agent.
        """
        status = wait_result.get("status", "")
        if status not in ("completed", "error", "closed"):
            return None

        agent_id = wait_result.get("agent_id", "")
        if not agent_id:
            return None

        # Idempotent: don't overwrite an existing finding for the same agent
        if agent_id in self._findings:
            return self._findings[agent_id]

        last_message = wait_result.get("last_message", "") or ""
        if len(last_message) > _LAST_MESSAGE_CAP:
            last_message = last_message[-_LAST_MESSAGE_CAP:]

        finding = AgentFinding(
            agent_id=agent_id,
            title=wait_result.get("title", ""),
            status=status,
            last_message=last_message,
            files_touched=wait_result.get("files_touched", []) or [],
            error_count=wait_result.get("error_count", 0) or 0,
            tool_call_count=wait_result.get("tool_call_count", 0) or 0,
            usage=wait_result.get("usage") or {},
        )

        self._findings[agent_id] = finding
        _log(f"WorkflowContext: captured finding for {agent_id[:8]} ({finding.title})")
        return finding

    def get_findings(self, *, status_filter: str | None = None) -> list[dict[str, Any]]:
        """Return all findings, optionally filtered by status."""
        findings = list(self._findings.values())
        if status_filter:
            findings = [f for f in findings if f.status == status_filter]
        return [f.to_dict() for f in findings]

    def get_finding(self, agent_id: str) -> dict[str, Any] | None:
        """Return a single finding by agent_id, or None."""
        f = self._findings.get(agent_id)
        return f.to_dict() if f else None

    def summary(self) -> dict[str, Any]:
        """High-level summary for the orchestrating agent."""
        findings = list(self._findings.values())
        total_files = set()
        total_errors = 0
        total_tools = 0
        by_status: dict[str, int] = {}

        for f in findings:
            total_files.update(f.files_touched)
            total_errors += f.error_count
            total_tools += f.tool_call_count
            by_status[f.status] = by_status.get(f.status, 0) + 1

        return {
            "session_id": self.session_id,
            "agent_count": len(findings),
            "by_status": by_status,
            "total_files_touched": len(total_files),
            "total_errors": total_errors,
            "total_tool_calls": total_tools,
        }

    def clear(self) -> None:
        """Reset all findings (e.g. new workflow phase)."""
        self._findings.clear()
        _log("WorkflowContext: cleared all findings")

    # -- Kumiho persistence (best-effort) ------------------------------------

    async def persist_finding(self, finding: AgentFinding) -> bool:
        """Persist a finding to Kumiho. Returns True on success.

        Fire-and-forget from the caller's perspective — failures are logged
        but never propagate.
        """
        sdk = self._kumiho_sdk
        if sdk is None or not getattr(sdk, "_available", False):
            return False

        try:
            await sdk.ensure_space(harness_project(), "Workflows")

            item_name = f"wf-{self.session_id}-{finding.agent_id[:8]}"
            item = await sdk.create_item(
                self._kumiho_space,
                item_name,
                kind="workflow-finding",
                metadata={
                    "session_id": self.session_id,
                    "agent_id": finding.agent_id,
                    "title": finding.title,
                    "status": finding.status,
                },
            )

            item_kref = item.get("kref", "")
            if not item_kref:
                _log(f"WorkflowContext: no kref returned for {item_name}")
                return False

            await sdk.create_revision(
                item_kref,
                metadata={
                    "last_message": finding.last_message[:2000],
                    "files_touched": ",".join(finding.files_touched[:50]),
                    "error_count": str(finding.error_count),
                    "tool_call_count": str(finding.tool_call_count),
                    "captured_at": finding.captured_at,
                },
                tag="latest",
            )

            _log(f"WorkflowContext: persisted finding {item_name} to Kumiho")
            return True
        except Exception as e:
            _log(f"WorkflowContext: Kumiho persist failed for {finding.agent_id[:8]}: {e}")
            return False
