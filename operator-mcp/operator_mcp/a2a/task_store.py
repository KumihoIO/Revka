"""A2A Task Store — maps Revka agent lifecycle to A2A task states.

A2A TaskState mapping:
    SUBMITTED      ← agent created, not yet started
    WORKING        ← running
    COMPLETED      ← completed (exit 0)
    FAILED         ← error
    CANCELED       ← cancelled
    INPUT_REQUIRED ← permission_blocked
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from .._log import _log
from ..agent_state import AGENTS


# ---------------------------------------------------------------------------
# A2A Task state constants
# ---------------------------------------------------------------------------

SUBMITTED = "submitted"
WORKING = "working"
COMPLETED = "completed"
FAILED = "failed"
CANCELED = "canceled"
INPUT_REQUIRED = "input-required"

_TERMINAL_STATES = {COMPLETED, FAILED, CANCELED}

# Revka → A2A state mapping
_STATUS_MAP = {
    "idle": SUBMITTED,
    "running": WORKING,
    "completed": COMPLETED,
    "error": FAILED,
    "closed": COMPLETED,
    "cancelled": CANCELED,
    "permission_blocked": INPUT_REQUIRED,
}


# ---------------------------------------------------------------------------
# Task data
# ---------------------------------------------------------------------------

class A2ATask:
    """Represents an A2A task backed by a Revka agent."""

    def __init__(
        self,
        task_id: str,
        context_id: str,
        agent_id: str,
        *,
        message: dict[str, Any] | None = None,
    ):
        self.task_id = task_id
        self.context_id = context_id
        self.agent_id = agent_id
        self.created_at = datetime.now(timezone.utc)
        self.initial_message = message
        self.artifacts: list[dict[str, Any]] = []
        self.history: list[dict[str, Any]] = []

    @property
    def state(self) -> str:
        """Get current A2A state from underlying Revka agent."""
        agent = AGENTS.get(self.agent_id)
        if not agent:
            return FAILED
        return _STATUS_MAP.get(agent.status, WORKING)

    @property
    def is_terminal(self) -> bool:
        return self.state in _TERMINAL_STATES

    def to_dict(self) -> dict[str, Any]:
        """Serialize to A2A Task JSON format."""
        return {
            "id": self.task_id,
            "contextId": self.context_id,
            "status": {
                "state": self.state,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            "artifacts": self.artifacts,
            "history": self.history[-20:],  # bounded
        }

    def add_artifact(
        self,
        *,
        name: str,
        description: str = "",
        parts: list[dict[str, Any]] | None = None,
    ) -> None:
        """Add an output artifact to the task."""
        self.artifacts.append({
            "artifactId": str(uuid.uuid4()),
            "name": name,
            "description": description,
            "parts": parts or [],
        })

    def add_history_message(self, role: str, text: str) -> None:
        """Add a message to task history."""
        self.history.append({
            "role": role,
            "messageId": str(uuid.uuid4()),
            "parts": [{"kind": "text", "text": text}],
        })


# ---------------------------------------------------------------------------
# Task Store
# ---------------------------------------------------------------------------

class A2ATaskStore:
    """In-memory task store mapping A2A tasks to Revka agents."""

    def __init__(self):
        self._tasks: dict[str, A2ATask] = {}
        self._agent_to_task: dict[str, str] = {}
        self._terminal_cache: dict[str, dict[str, Any]] = {}

    def create_task(
        self,
        agent_id: str,
        *,
        context_id: str | None = None,
        message: dict[str, Any] | None = None,
    ) -> A2ATask:
        """Create a new A2A task backed by a Revka agent."""
        task_id = f"task-{uuid.uuid4()}"
        ctx_id = context_id or f"ctx-{uuid.uuid4()}"
        task = A2ATask(task_id, ctx_id, agent_id, message=message)
        self._tasks[task_id] = task
        self._agent_to_task[agent_id] = task_id
        _log(f"a2a_store: created task {task_id[:12]} → agent {agent_id[:8]}")
        return task

    def get_task(self, task_id: str) -> A2ATask | None:
        """Get task by ID."""
        return self._tasks.get(task_id)

    def get_task_for_agent(self, agent_id: str) -> A2ATask | None:
        """Get A2A task associated with a Revka agent."""
        task_id = self._agent_to_task.get(agent_id)
        if task_id:
            return self._tasks.get(task_id)
        return None

    def get_task_dict(self, task_id: str) -> dict[str, Any] | None:
        """Get task as dict, using terminal cache for completed tasks."""
        cached = self._terminal_cache.get(task_id)
        if cached:
            return cached

        task = self._tasks.get(task_id)
        if not task:
            return None

        result = task.to_dict()
        if task.is_terminal:
            self._terminal_cache[task_id] = result

        return result

    def list_tasks(
        self,
        *,
        context_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List tasks, optionally filtered by context."""
        tasks = list(self._tasks.values())
        if context_id:
            tasks = [t for t in tasks if t.context_id == context_id]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return [t.to_dict() for t in tasks[:limit]]

    def finalize_task(self, task_id: str) -> None:
        """Finalize a completed task — build artifacts from agent output."""
        task = self._tasks.get(task_id)
        if not task:
            return

        agent = AGENTS.get(task.agent_id)
        if not agent:
            return

        # Import here to avoid circular dependency
        from ..run_log import get_log
        run_log = get_log(task.agent_id)
        sidecar_id = getattr(agent, "_sidecar_id", None)
        if run_log is None and sidecar_id:
            run_log = get_log(sidecar_id)

        if run_log:
            summary = run_log.get_summary()
            # Add last message as text artifact
            last_msg = summary.get("last_message", "")
            if last_msg:
                task.add_artifact(
                    name="agent_output",
                    description="Final output from the agent",
                    parts=[{"kind": "text", "text": last_msg[:10000]}],
                )
            # Add files touched as file artifacts
            files = summary.get("files_touched", [])
            if files:
                task.add_artifact(
                    name="files_touched",
                    description=f"{len(files)} files modified",
                    parts=[{"kind": "data", "data": {"files": files}}],
                )

        _log(f"a2a_store: finalized task {task_id[:12]} with {len(task.artifacts)} artifacts")
