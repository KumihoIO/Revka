"""A2A server for the reviewer agent — speaks Revka's A2A dialect.

Compatibility contract (operator-mcp/operator_mcp/a2a/a2a_client.py):
  - Agent card served at GET /.well-known/agent-card.json and /agent-card.json.
  - JSON-RPC 2.0 POSTed to the base URL ("/", plus "/a2a" for safety):
      message/send  params: {message: {role, parts: [{type: "text", text}]},
                             id?, contextId?, metadata?}
      tasks/get     params: {id}
      tasks/cancel  params: {id}
  - The JSON-RPC "result" is the Task object itself:
      {id, contextId, status: {state, timestamp}, artifacts: [...], history: [...]}
  - Artifact parts use {"type": "text", "text": ...} (legacy keying — the
    a2a-sdk's "kind" keying is NOT understood by Revka's client, which is why
    ADK's built-in to_a2a()/`adk api_server --a2a` cannot be used here).
  - Terminal states: completed | failed | canceled.

Tasks run asynchronously: message/send returns state "working" immediately and
the caller polls tasks/get (Revka's a2a_send_task wait=true does this).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

AGENT_NAME = "Revka Reviewer Agent"
AGENT_DESCRIPTION = (
    "ADK/Gemini review executor: fetches a GitHub pull request diff and "
    "reviews it for correctness, safety, and test coverage. Returns JSON "
    "{review_status, findings, summary}."
)
SKILL = {
    "id": "reviewer-review-pr",
    "name": "Review GitHub pull request",
    "description": (
        "Input JSON: {repo_name, pr_number}. Fetches the PR diff via the GitHub "
        "API and reviews correctness, safety, and test coverage. Output JSON: "
        '{review_status: "approved"|"needs_changes", findings: [...], summary}.'
    ),
    "tags": ["code-review", "github", "pull-request", "adk", "gemini", "a2a"],
    "examples": ['{"repo_name": "acme/api", "pr_number": 57}'],
    "inputModes": ["text/plain", "application/json"],
    "outputModes": ["text/plain", "application/json"],
}
REQUIRED_FIELDS = ("repo_name", "pr_number")

SERVICE_VERSION = "1.0.0"
MAX_TASKS = int(os.getenv("MAX_TASKS", "100"))
TASK_TIMEOUT_SECONDS = float(os.getenv("TASK_TIMEOUT_SECONDS", "600"))

logger = logging.getLogger("reviewer-a2a")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())

app = FastAPI(title=AGENT_NAME, version=SERVICE_VERSION)

TASKS: dict[str, dict[str, Any]] = {}
TASK_ORDER: deque[str] = deque()
RUNNING: dict[str, asyncio.Task] = {}

# One heavyweight task at a time per instance (deploy with --max-instances 1).
_EXEC_LOCK = asyncio.Lock()

_runner: Any | None = None
_session_service: Any | None = None


class JsonRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: str | int | None = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _base_url(request: Request) -> str:
    configured = os.getenv("PUBLIC_BASE_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    return str(request.base_url).rstrip("/")


def build_agent_card(base_url: str) -> dict[str, Any]:
    """Agent card in the shape Revka's a2a_discover expects."""
    return {
        "protocolVersion": "0.3",
        "name": AGENT_NAME,
        "description": AGENT_DESCRIPTION,
        "url": base_url,
        "version": SERVICE_VERSION,
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "capabilities": {"streaming": False, "pushNotifications": False},
        "provider": {"organization": "Revka / KumihoIO", "url": base_url},
        "skills": [SKILL],
    }


def parse_task_input(text: str) -> dict[str, Any]:
    """Parse and validate the JSON task payload from the A2A message text.

    Accepts either a bare JSON object or text containing one. Raises
    ValueError when no object is found or required fields are missing.
    """
    text = (text or "").strip()
    data: Any = None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(text[start : end + 1])
            except (json.JSONDecodeError, ValueError):
                data = None
    if not isinstance(data, dict):
        raise ValueError("task input must be a JSON object")
    missing = [f for f in REQUIRED_FIELDS if data.get(f) in (None, "")]
    if missing:
        raise ValueError(f"missing required fields: {', '.join(missing)}")
    return data


def _message_text(params: dict[str, Any]) -> str:
    parts: list[str] = []
    for part in params.get("message", {}).get("parts", []):
        kind = part.get("type") or part.get("kind")
        if kind == "text":
            parts.append(str(part.get("text", "")))
        elif kind == "data":
            parts.append(json.dumps(part.get("data", {})))
    return "\n".join(p for p in parts if p).strip()


def _make_task(task_id: str, context_id: str, state: str, message: str) -> dict[str, Any]:
    return {
        "id": task_id,
        "contextId": context_id,
        "status": {"state": state, "timestamp": _now()},
        "history": [
            {
                "role": "user",
                "messageId": f"message-{task_id}",
                "parts": [{"type": "text", "text": message}],
            }
        ],
        "artifacts": [],
        "metadata": {
            "agent": AGENT_NAME,
            "platform": "Google Cloud Run",
            "orchestration": "Google ADK",
            "intelligence": "Gemini via Vertex AI",
        },
    }


def _store_task(task: dict[str, Any]) -> None:
    task_id = task["id"]
    if task_id not in TASKS:
        TASK_ORDER.append(task_id)
    TASKS[task_id] = task
    while len(TASK_ORDER) > MAX_TASKS:
        expired = TASK_ORDER.popleft()
        TASKS.pop(expired, None)
        RUNNING.pop(expired, None)


def _finish_task(task_id: str, state: str, text: str, *, artifact_name: str) -> None:
    task = TASKS.get(task_id)
    if not task or task["status"]["state"] in {"completed", "failed", "canceled"}:
        return
    task["status"] = {"state": state, "timestamp": _now()}
    task["artifacts"] = [
        {
            "artifactId": f"artifact-{task_id}",
            "name": artifact_name,
            "parts": [{"type": "text", "text": text}],
        }
    ]


async def _run_adk(prompt: str, *, session_id: str) -> str:
    """Run the ADK agent and return its final text. Imports lazily so the
    card/health endpoints (and unit tests) never need google-adk loaded."""
    global _runner, _session_service

    if _runner is None or _session_service is None:
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService

        from agent import APP_NAME, root_agent

        _session_service = InMemorySessionService()
        _runner = Runner(agent=root_agent, app_name=APP_NAME, session_service=_session_service)

    from google.genai import types

    from agent import APP_NAME

    user_id = "revka-a2a"
    await _session_service.create_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )
    content = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
    chunks: list[str] = []
    async for event in _runner.run_async(
        user_id=user_id, session_id=session_id, new_message=content
    ):
        event_content = getattr(event, "content", None)
        for part in getattr(event_content, "parts", None) or []:
            text = getattr(part, "text", None)
            if text:
                chunks.append(text)
    # The final model message is the agent's JSON answer; keep everything but
    # prefer the tail if the transcript is huge.
    return "".join(chunks).strip()[-20_000:] or "agent produced no text output"


async def _execute_task(task_id: str, payload: dict[str, Any], raw_text: str) -> None:
    async with _EXEC_LOCK:
        task = TASKS.get(task_id)
        if not task or task["status"]["state"] == "canceled":
            return
        task["status"] = {"state": "working", "timestamp": _now()}

        workdir = tempfile.mkdtemp(prefix="reviewer-task-")
        try:
            import agent as agent_module

            if hasattr(agent_module, "set_workspace"):
                agent_module.set_workspace(workdir)

            prompt = json.dumps(payload)
            result = await asyncio.wait_for(
                _run_adk(prompt, session_id=task_id), timeout=TASK_TIMEOUT_SECONDS
            )
            _finish_task(task_id, "completed", result, artifact_name="reviewer-result")
            logger.info("task %s completed", task_id)
        except asyncio.CancelledError:
            _finish_task(task_id, "canceled", "task canceled", artifact_name="reviewer-result")
            raise
        except asyncio.TimeoutError:
            _finish_task(
                task_id,
                "failed",
                f"task timed out after {TASK_TIMEOUT_SECONDS:.0f}s",
                artifact_name="agent-error",
            )
            logger.warning("task %s timed out", task_id)
        except Exception as exc:  # noqa: BLE001 - report failure through the task
            _finish_task(
                task_id,
                "failed",
                f"{exc.__class__.__name__}: {str(exc)[:800]}",
                artifact_name="agent-error",
            )
            logger.exception("task %s failed", task_id)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
            RUNNING.pop(task_id, None)


def _jsonrpc_result(req_id: Any, result: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": result})


def _jsonrpc_error(req_id: Any, code: str, message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}},
        status_code=status_code,
    )


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {
        "ok": True,
        "service": AGENT_NAME,
        "version": SERVICE_VERSION,
        "stored_tasks": len(TASKS),
        "running_tasks": len(RUNNING),
        "cloud_run": {
            "service": os.getenv("K_SERVICE", ""),
            "revision": os.getenv("K_REVISION", ""),
        },
    }


@app.get("/.well-known/agent-card.json")
async def well_known_agent_card(request: Request) -> dict[str, Any]:
    return build_agent_card(_base_url(request))


@app.get("/agent-card.json")
async def agent_card(request: Request) -> dict[str, Any]:
    return build_agent_card(_base_url(request))


@app.post("/")
@app.post("/a2a")
async def jsonrpc_endpoint(payload: JsonRpcRequest, request: Request) -> JSONResponse:
    method = payload.method

    if method == "message/send":
        text = _message_text(payload.params)
        if not text:
            return _jsonrpc_error(payload.id, "InvalidRequest", "No text content in message")

        task_id = str(payload.params.get("id") or f"task-{uuid.uuid4()}")
        context_id = str(payload.params.get("contextId") or f"ctx-{uuid.uuid4()}")
        task = _make_task(task_id, context_id, "submitted", text)
        _store_task(task)

        try:
            data = parse_task_input(text)
        except ValueError as exc:
            _finish_task(task_id, "failed", f"invalid task input: {exc}", artifact_name="agent-error")
            return _jsonrpc_result(payload.id, task)

        RUNNING[task_id] = asyncio.create_task(_execute_task(task_id, data, text))
        return _jsonrpc_result(payload.id, task)

    if method == "tasks/get":
        task_id = str(payload.params.get("id") or payload.params.get("taskId") or "")
        task = TASKS.get(task_id)
        if not task:
            return _jsonrpc_error(payload.id, "TaskNotFoundError", f"Task not found: {task_id}", 404)
        return _jsonrpc_result(payload.id, task)

    if method == "tasks/cancel":
        task_id = str(payload.params.get("id") or payload.params.get("taskId") or "")
        task = TASKS.get(task_id)
        if not task:
            return _jsonrpc_error(payload.id, "TaskNotFoundError", f"Task not found: {task_id}", 404)
        running = RUNNING.pop(task_id, None)
        if running:
            running.cancel()
        if task["status"]["state"] not in {"completed", "failed", "canceled"}:
            task["status"] = {"state": "canceled", "timestamp": _now()}
        return _jsonrpc_result(payload.id, task)

    if method == "tasks/list":
        return _jsonrpc_result(payload.id, {"tasks": list(TASKS.values())[-50:]})

    return _jsonrpc_error(payload.id, "UnsupportedOperationError", f"Method not supported: {method}")
