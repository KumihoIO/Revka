"""A2A Task Handler — handles incoming A2A JSON-RPC requests.

Maps A2A protocol methods to Construct operator operations:
  message/send   → create_agent + wait (or immediate response)
  message/stream → create_agent + SSE stream
  tasks/get      → poll agent status
  tasks/cancel   → cancel_agent
"""
from __future__ import annotations

import uuid
from typing import Any, AsyncGenerator

from .._log import _log
from ..agent_state import AGENTS, ManagedAgent
from ..budget_authority import BudgetGateError
from .task_store import A2ATaskStore, A2ATask, COMPLETED, FAILED, CANCELED


# ---------------------------------------------------------------------------
# A2A error codes
# ---------------------------------------------------------------------------

def _a2a_error(code: str, message: str) -> dict[str, Any]:
    """Build a JSON-RPC error response body."""
    return {"error": {"code": code, "message": message}}


TASK_NOT_FOUND = "TaskNotFoundError"
UNSUPPORTED_OP = "UnsupportedOperationError"
CONTENT_TYPE_ERROR = "ContentTypeNotSupportedError"


# ---------------------------------------------------------------------------
# A2A Task Handler
# ---------------------------------------------------------------------------

class A2ATaskHandler:
    """Handles A2A JSON-RPC method dispatch.

    Integrates with Construct operator to spawn agents for incoming
    A2A tasks and map their lifecycle to A2A task states.
    """

    def __init__(self, task_store: A2ATaskStore | None = None):
        self._store = task_store or A2ATaskStore()

    @property
    def store(self) -> A2ATaskStore:
        return self._store

    async def handle_jsonrpc(self, request: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a JSON-RPC 2.0 request to the appropriate handler.

        Args:
            request: JSON-RPC request with 'method', 'params', 'id'.

        Returns:
            JSON-RPC response dict.
        """
        method = request.get("method", "")
        params = request.get("params", {})
        req_id = request.get("id")

        _log(f"a2a_handler: method={method} id={req_id}")

        handler_map = {
            "message/send": self._handle_message_send,
            "tasks/get": self._handle_tasks_get,
            "tasks/cancel": self._handle_tasks_cancel,
            "tasks/list": self._handle_tasks_list,
        }

        handler = handler_map.get(method)
        if not handler:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": UNSUPPORTED_OP, "message": f"Method not supported: {method}"},
            }

        try:
            result = await handler(params)
            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        except Exception as exc:
            _log(f"a2a_handler: {method} error: {exc}")
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": "InternalError", "message": str(exc)},
            }

    # -- Method handlers --

    async def _handle_message_send(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle message/send — spawn a Construct agent for the task.

        Returns either a Task (long-running) or Message (immediate).
        """
        message = params.get("message", {})
        config = params.get("configuration", {})

        # Extract text from message parts
        text_parts = []
        for part in message.get("parts", []):
            kind = part.get("kind", "text")
            if kind == "text":
                text_parts.append(part.get("text", ""))
            elif kind == "data":
                text_parts.append(str(part.get("data", "")))
        prompt = "\n".join(text_parts)

        if not prompt:
            return _a2a_error("InvalidRequest", "No text content in message")

        context_id = message.get("contextId") or str(uuid.uuid4())

        # Determine skill → template mapping
        skill_id = None
        # A2A clients may specify which skill to target
        if "skill" in params:
            skill_id = params["skill"]

        # Spawn a Construct agent
        from ..tool_handlers.agents import _try_sidecar_create, _event_consumer
        from ..agent_subprocess import compose_agent_prompt, spawn_agent

        # Select agent type based on skill or default
        agent_type = self._select_agent_type(skill_id)
        agent_id = str(uuid.uuid4())
        title = f"a2a-{context_id[:8]}"

        # Default cwd — A2A tasks get a temp workspace
        import tempfile
        cwd = config.get("cwd", tempfile.gettempdir())

        agent = ManagedAgent(
            id=agent_id,
            agent_type=agent_type,
            title=title,
            cwd=cwd,
            status="idle",
        )
        AGENTS[agent_id] = agent

        # Create A2A task
        task = self._store.create_task(agent_id, context_id=context_id, message=message)
        task.add_history_message("user", prompt)

        # Spawn via sidecar
        full_prompt = compose_agent_prompt("a2a-worker", "coder", "", [], prompt)

        try:
            sidecar_info = await _try_sidecar_create(
                agent_id, agent_type, title, cwd, full_prompt,
            )
        except BudgetGateError as exc:
            agent.status = "error"
            task.status = FAILED
            task.add_history_message("agent", str(exc.response.get("error", exc)))
            return {"task": task.to_dict(), "budget": exc.response}
        if sidecar_info:
            agent.status = "running"
            agent._sidecar_id = sidecar_info.get("id", "")
            if _event_consumer and agent._sidecar_id:
                await _event_consumer.subscribe(agent._sidecar_id, title)
        else:
            from ..operator_mcp import JOURNAL
            try:
                await spawn_agent(agent, full_prompt, JOURNAL)
            except Exception as exc:
                agent.status = "error"
                task.add_history_message("agent", f"Spawn failed: {exc}")
                return {"task": task.to_dict()}

        _log(f"a2a_handler: spawned agent {agent_id[:8]} for task {task.task_id[:12]}")

        # Return task (long-running)
        return {"task": task.to_dict()}

    async def _handle_tasks_get(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tasks/get — return current task state."""
        task_id = params.get("id", "")
        task_dict = self._store.get_task_dict(task_id)
        if not task_dict:
            return _a2a_error(TASK_NOT_FOUND, f"Task not found: {task_id}")

        # Check if task just completed — finalize artifacts
        task = self._store.get_task(task_id)
        if task and task.is_terminal and not task.artifacts:
            self._store.finalize_task(task_id)
            task_dict = self._store.get_task_dict(task_id)

        return {"task": task_dict}

    async def _handle_tasks_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tasks/cancel — cancel the underlying agent."""
        task_id = params.get("id", "")
        task = self._store.get_task(task_id)
        if not task:
            return _a2a_error(TASK_NOT_FOUND, f"Task not found: {task_id}")

        if task.is_terminal:
            return _a2a_error("TaskNotCancelableError", "Task already in terminal state")

        # Cancel the Construct agent
        from ..tool_handlers.agents import tool_cancel_agent
        await tool_cancel_agent({"agent_id": task.agent_id})

        return {"task": task.to_dict()}

    async def _handle_tasks_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tasks/list — list tasks."""
        context_id = params.get("contextId")
        limit = params.get("limit", 50)
        tasks = self._store.list_tasks(context_id=context_id, limit=limit)
        return {"tasks": tasks}

    # -- Helpers --

    def _select_agent_type(self, skill_id: str | None) -> str:
        """Map A2A skill ID to Construct agent type."""
        if not skill_id:
            return "claude"  # default

        # Try to find template by skill ID
        from ..agent_state import POOL
        for tmpl in POOL.list_all():
            if f"construct-{tmpl.name}" == skill_id:
                return tmpl.agent_type
        return "claude"


# -- Module-level singleton --
_handler: A2ATaskHandler | None = None


def get_handler() -> A2ATaskHandler:
    """Get or create the global A2A task handler."""
    global _handler
    if _handler is None:
        _handler = A2ATaskHandler()
    return _handler
