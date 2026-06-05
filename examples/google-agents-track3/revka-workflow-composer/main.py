"""Cloud Run A2A wrapper around the Revka Track 3 Workflow Composer ADK agent."""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


APP_NAME = "revka-workflow-composer"
SERVICE_VERSION = "1.0.0"
MAX_MESSAGE_CHARS = int(os.getenv("MAX_MESSAGE_CHARS", "12000"))
MAX_TASKS = int(os.getenv("MAX_TASKS", "200"))
ADK_RESPONSE_TIMEOUT_SECONDS = float(os.getenv("ADK_RESPONSE_TIMEOUT_SECONDS", "45"))
A2A_BEARER_TOKEN = os.getenv("A2A_BEARER_TOKEN", "").strip()
AUTH_MODE = "bearer-token" if A2A_BEARER_TOKEN else "public-demo"
ICON_URL = (
    "data:image/svg+xml;base64,"
    "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdC"
    "b3g9IjAgMCA2NCA2NCI+PHJlY3Qgd2lkdGg9IjY0IiBoZWlnaHQ9IjY0IiBy"
    "eD0iMTIiIGZpbGw9IiMxMTE4MjciLz48cGF0aCBkPSJNMjAgMjBoMjR2MjRI"
    "MjB6IiBmaWxsPSJub25lIiBzdHJva2U9IiMxMGI5ODEiIHN0cm9rZS13aWR0"
    "aD0iNCIvPjxjaXJjbGUgY3g9IjMyIiBjeT0iMzIiIHI9IjYiIGZpbGw9IiMx"
    "MGI5ODEiLz48L3N2Zz4="
)
TASKS: dict[str, dict[str, Any]] = {}
TASK_ORDER: deque[str] = deque()

logger = logging.getLogger(APP_NAME)


def _configure_logging() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
    if os.getenv("ENABLE_CLOUD_LOGGING", "").strip().lower() not in {"1", "true", "yes"}:
        return
    try:
        import google.cloud.logging

        google.cloud.logging.Client().setup_logging()
    except Exception as exc:  # noqa: BLE001 - Cloud Logging must not block readiness.
        logger.warning("Cloud Logging setup failed: %s", exc)


_configure_logging()

app = FastAPI(title="Revka Workflow Composer A2A", version=SERVICE_VERSION)

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


def _runtime_metadata() -> dict[str, Any]:
    return {
        "ok": True,
        "service": APP_NAME,
        "version": SERVICE_VERSION,
        "track": "google-startups-ai-agents-track-3",
        "orchestration": "Google ADK",
        "intelligence": "Gemini via Vertex AI",
        "platform": "Google Cloud Run",
        "b2bPackage": "Revka Workflow Composer & Pipeline Architect",
        "auth_mode": AUTH_MODE,
        "max_message_chars": MAX_MESSAGE_CHARS,
        "max_tasks": MAX_TASKS,
        "adk_response_timeout_seconds": ADK_RESPONSE_TIMEOUT_SECONDS,
        "stored_tasks": len(TASKS),
        "cloud_run": {
            "service": os.getenv("K_SERVICE", ""),
            "revision": os.getenv("K_REVISION", ""),
            "location": os.getenv("GOOGLE_CLOUD_LOCATION", ""),
        },
    }


def _adk_imports_available() -> bool:
    try:
        from google.adk.runners import Runner  # noqa: F401
        from google.genai import types  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - readiness should report import availability.
        logger.warning("ADK readiness import check failed: %s", exc)
        return False
    return True


def _authorized(request: Request) -> bool:
    if not A2A_BEARER_TOKEN:
        return True
    auth_header = request.headers.get("Authorization", "")
    scheme, _, token = auth_header.partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(token.strip(), A2A_BEARER_TOKEN)


def _jsonrpc_error(
    payload: JsonRpcRequest,
    *,
    code: str,
    message: str,
    status_code: int,
) -> JSONResponse:
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": payload.id,
            "error": {"code": code, "message": message},
        },
        status_code=status_code,
    )


def _store_task(task_id: str, task: dict[str, Any]) -> None:
    if task_id not in TASKS:
        TASK_ORDER.append(task_id)
    TASKS[task_id] = task
    while len(TASK_ORDER) > MAX_TASKS:
        expired_id = TASK_ORDER.popleft()
        TASKS.pop(expired_id, None)


def _safe_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    return f"{exc.__class__.__name__}: {message[:500]}"


def _agent_card(base_url: str) -> dict[str, Any]:
    return {
        "protocolVersion": "0.3",
        "name": "Revka Workflow Composer & Pipeline Architect",
        "description": (
            "B2B A2A agent that designs, audits, compiles, and registers secure, "
            "governed production workflows and pipeline DAGs on Kumiho."
        ),
        "url": base_url,
        "iconUrl": ICON_URL,
        "version": SERVICE_VERSION,
        "supportedInterfaces": [
            {
                "url": f"{base_url}/",
                "protocolBinding": "JSONRPC",
                "protocolVersion": "0.3",
            },
            {
                "url": f"{base_url}/a2a",
                "protocolBinding": "JSONRPC",
                "protocolVersion": "0.3",
            },
            {
                "url": f"{base_url}/message:send",
                "protocolBinding": "HTTP+JSON",
                "protocolVersion": "0.3",
            },
        ],
        "securitySchemes": {
            "BearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
            }
        },
        "security": [
            {
                "BearerAuth": [],
            }
        ],
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
        },
        "provider": {
            "organization": "Revka / KumihoIO",
            "url": base_url,
        },
        "skills": [
            {
                "id": "revka-workflow-composition",
                "name": "Revka Workflow Composition",
                "description": (
                    "Designs and compiles governed pipeline DAGs with approval gates, "
                    "specialized agent orchestration, and Kumiho persistence."
                ),
                "tags": [
                    "b2b",
                    "workflows",
                    "composition",
                    "a2a",
                    "kumiho-sdk",
                    "adk",
                    "gemini",
                ],
                "examples": [
                    (
                        "Design a production incident response pipeline that has triage, "
                        "risk audit, approval gate, and SRE rollback tasks."
                    )
                ],
                "inputModes": ["text/plain", "application/json"],
                "outputModes": ["text/plain", "application/json"],
            },
            {
                "id": "kapathy-style-compliance-audit",
                "name": "Kapathy Style Compliance Audit",
                "description": (
                    "Audits software changes, infrastructure code, or workflow plans "
                    "against Andrej Karpathy's simplicity and clean-room coding rules."
                ),
                "tags": [
                    "kapathy",
                    "simplicity",
                    "code-audit",
                    "compliance",
                    "zero-speculative"
                ],
                "examples": [
                    "Audit this SRE rollback script for over-engineering and dependency bloat."
                ],
                "inputModes": ["text/plain", "application/json"],
                "outputModes": ["text/plain", "application/json"],
            }
        ],
    }


def _message_text(params: dict[str, Any]) -> str:
    message = params.get("message", {})
    text_parts: list[str] = []
    for part in message.get("parts", []):
        kind = part.get("type") or part.get("kind")
        if kind == "text":
            text_parts.append(str(part.get("text", "")))
        elif kind == "data":
            text_parts.append(str(part.get("data", "")))
    return "\n".join(part for part in text_parts if part).strip()


def _event_text(event: Any) -> str:
    content = getattr(event, "content", None)
    if content is None:
        return ""
    pieces: list[str] = []
    for part in getattr(content, "parts", []) or []:
        text = getattr(part, "text", None)
        if text:
            pieces.append(text)
    return "".join(pieces)


async def _adk_response(prompt: str, *, user_id: str, session_id: str) -> str:
    """Run the ADK agent lazily so health and discovery remain lightweight."""
    global _runner, _session_service

    if _runner is None or _session_service is None:
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService

        from agent import APP_NAME as ADK_APP_NAME
        from agent import root_agent

        _session_service = InMemorySessionService()
        _runner = Runner(
            agent=root_agent,
            app_name=ADK_APP_NAME,
            session_service=_session_service,
            auto_create_session=False,
        )

    from google.genai import types

    await _session_service.create_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
        state={
            "track": "google-startups-ai-agents-track-3",
            "business_context": "workflow-composer",
        },
    )
    content = types.Content(
        role="user",
        parts=[types.Part.from_text(text=prompt)],
    )
    chunks: list[str] = []
    async for event in _runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=content,
    ):
        text = _event_text(event)
        if text:
            chunks.append(text)

    return "".join(chunks).strip() or "No textual response returned by ADK agent."


def _task(
    *,
    task_id: str,
    context_id: str,
    state: str,
    message: str,
    response: str = "",
    error: str = "",
) -> dict[str, Any]:
    artifacts = []
    if response:
        artifacts.append(
            {
                "artifactId": f"artifact-{task_id}",
                "name": "revka-workflow-composition-plan",
                "description": "Gemini/ADK generated pipeline DAG or compliance audit report.",
                "parts": [{"type": "text", "text": response}],
            }
        )
    if error:
        artifacts.append(
            {
                "artifactId": f"artifact-{task_id}-error",
                "name": "agent-error",
                "description": "Runtime error captured for composer diagnosis.",
                "parts": [{"type": "text", "text": error}],
            }
        )

    return {
        "id": task_id,
        "contextId": context_id,
        "status": {
            "state": state,
            "timestamp": _now(),
        },
        "history": [
            {
                "role": "user",
                "messageId": f"message-{task_id}",
                "parts": [{"type": "text", "text": message}],
            }
        ],
        "artifacts": artifacts,
        "metadata": {
            "platform": "Google Cloud Run",
            "orchestration": "Google ADK",
            "intelligence": "Gemini via Vertex AI",
            "b2bPackage": "Revka Workflow Composer & Pipeline Architect",
        },
    }


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return _runtime_metadata()


@app.get("/statusz")
async def statusz() -> dict[str, Any]:
    return _runtime_metadata()


@app.get("/runtime")
async def runtime() -> dict[str, Any]:
    return _runtime_metadata()


@app.get("/readyz")
async def readyz() -> dict[str, Any]:
    metadata = _runtime_metadata()
    metadata["ready"] = True
    metadata["adk_imports"] = _adk_imports_available()
    return metadata


@app.get("/.well-known/agent-card.json")
async def well_known_agent_card(request: Request) -> dict[str, Any]:
    return _agent_card(_base_url(request))


@app.get("/agent-card.json")
async def agent_card(request: Request) -> dict[str, Any]:
    return _agent_card(_base_url(request))


@app.post("/")
@app.post("/a2a")
async def jsonrpc_endpoint(payload: JsonRpcRequest, request: Request) -> JSONResponse:
    # Normalize methods for dual A2A spec mapping
    if payload.method in {"GetAgentCard", "AgentCard"}:
        payload.method = "agent/card"
    elif payload.method == "SendMessage":
        payload.method = "message/send"
    elif payload.method == "GetTask":
        payload.method = "tasks/get"
    elif payload.method == "ListTasks":
        payload.method = "tasks/list"
    elif payload.method == "CancelTask":
        payload.method = "tasks/cancel"

    if payload.method == "agent/card":
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": payload.id,
                "result": _agent_card(_base_url(request)),
            }
        )

    if not _authorized(request):
        return _jsonrpc_error(
            payload,
            code="Unauthorized",
            message="A2A invocation requires a valid bearer token",
            status_code=401,
        )

    if payload.method == "tasks/get":
        task_id = payload.params.get("id") or payload.params.get("taskId")
        task = TASKS.get(str(task_id))
        if not task:
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": payload.id,
                    "error": {"code": "TaskNotFoundError", "message": f"Task not found: {task_id}"},
                },
                status_code=404,
            )
        return JSONResponse({"jsonrpc": "2.0", "id": payload.id, "result": task})

    if payload.method == "tasks/list":
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": payload.id,
                "result": {"tasks": list(TASKS.values())[-50:]},
            }
        )

    if payload.method == "tasks/cancel":
        task_id = payload.params.get("id") or payload.params.get("taskId")
        task = TASKS.get(str(task_id))
        if not task:
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": payload.id,
                    "error": {"code": "TaskNotFoundError", "message": f"Task not found: {task_id}"},
                },
                status_code=404,
            )
        task["status"] = {"state": "canceled", "timestamp": _now()}
        return JSONResponse({"jsonrpc": "2.0", "id": payload.id, "result": task})

    if payload.method != "message/send":
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": payload.id,
                "error": {
                    "code": "UnsupportedOperationError",
                    "message": f"Method not supported: {payload.method}",
                },
            },
            status_code=400,
        )

    message = _message_text(payload.params)
    if not message:
        return _jsonrpc_error(
            payload,
            code="InvalidRequest",
            message="No text content in message",
            status_code=400,
        )
    if len(message) > MAX_MESSAGE_CHARS:
        return _jsonrpc_error(
            payload,
            code="InvalidRequest",
            message=f"Message exceeds MAX_MESSAGE_CHARS limit of {MAX_MESSAGE_CHARS}",
            status_code=413,
        )

    task_id = str(payload.params.get("id") or f"task-{uuid.uuid4()}")
    context_id = str(payload.params.get("contextId") or f"ctx-{uuid.uuid4()}")
    user_id = str(payload.params.get("metadata", {}).get("user_id") or "track3-demo-user")

    started = time.monotonic()
    try:
        response = await asyncio.wait_for(
            _adk_response(message, user_id=user_id, session_id=context_id),
            timeout=ADK_RESPONSE_TIMEOUT_SECONDS,
        )
        task = _task(
            task_id=task_id,
            context_id=context_id,
            state="completed",
            message=message,
            response=response,
        )
        logger.info(
            "completed A2A task %s in %.2fs",
            task_id,
            time.monotonic() - started,
        )
    except asyncio.TimeoutError:
        error = f"ADK response timed out after {ADK_RESPONSE_TIMEOUT_SECONDS} seconds"
        task = _task(
            task_id=task_id,
            context_id=context_id,
            state="failed",
            message=message,
            error=error,
        )
        logger.warning("timed out A2A task %s after %.2fs", task_id, time.monotonic() - started)
    except Exception as exc:
        error = _safe_error(exc)
        task = _task(
            task_id=task_id,
            context_id=context_id,
            state="failed",
            message=message,
            error=error,
        )
        logger.exception("failed A2A task %s after %.2fs", task_id, time.monotonic() - started)

    _store_task(task_id, task)
    return JSONResponse({"jsonrpc": "2.0", "id": payload.id, "result": task})


@app.post("/message:send")
@app.post("/message/send")
async def rest_message_send(request: Request) -> JSONResponse:
    if not _authorized(request):
        return JSONResponse(
            {"error": {"code": "Unauthorized", "message": "A2A invocation requires a valid bearer token"}},
            status_code=401,
        )
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": {"code": "InvalidRequest", "message": "Invalid JSON body"}},
            status_code=400,
        )
    message_text = _message_text(body)
    if not message_text:
        return JSONResponse(
            {"error": {"code": "InvalidRequest", "message": "No text content in message"}},
            status_code=400,
        )
    if len(message_text) > MAX_MESSAGE_CHARS:
        return JSONResponse(
            {"error": {"code": "InvalidRequest", "message": f"Message exceeds MAX_MESSAGE_CHARS limit of {MAX_MESSAGE_CHARS}"}},
            status_code=413,
        )
    task_id = str(body.get("id") or body.get("taskId") or f"task-{uuid.uuid4()}")
    context_id = str(body.get("contextId") or f"ctx-{uuid.uuid4()}")
    user_id = str(body.get("metadata", {}).get("user_id") or "track3-demo-user")
    started = time.monotonic()
    try:
        response = await asyncio.wait_for(
            _adk_response(message_text, user_id=user_id, session_id=context_id),
            timeout=ADK_RESPONSE_TIMEOUT_SECONDS,
        )
        task = _task(
            task_id=task_id,
            context_id=context_id,
            state="completed",
            message=message_text,
            response=response,
        )
        logger.info("completed A2A task %s in %.2fs", task_id, time.monotonic() - started)
    except asyncio.TimeoutError:
        error = f"ADK response timed out after {ADK_RESPONSE_TIMEOUT_SECONDS} seconds"
        task = _task(
            task_id=task_id,
            context_id=context_id,
            state="failed",
            message=message_text,
            error=error,
        )
        logger.warning("timed out A2A task %s after %.2fs", task_id, time.monotonic() - started)
    except Exception as exc:
        error = _safe_error(exc)
        task = _task(
            task_id=task_id,
            context_id=context_id,
            state="failed",
            message=message_text,
            error=error,
        )
        logger.exception("failed A2A task %s after %.2fs", task_id, time.monotonic() - started)
    _store_task(task_id, task)
    return JSONResponse(task)


@app.get("/tasks")
async def rest_list_tasks(request: Request) -> JSONResponse:
    if not _authorized(request):
        return JSONResponse(
            {"error": {"code": "Unauthorized", "message": "A2A invocation requires a valid bearer token"}},
            status_code=401,
        )
    return JSONResponse({"tasks": list(TASKS.values())[-50:]})


@app.get("/tasks/{task_id}")
async def rest_get_task(task_id: str, request: Request) -> JSONResponse:
    if not _authorized(request):
        return JSONResponse(
            {"error": {"code": "Unauthorized", "message": "A2A invocation requires a valid bearer token"}},
            status_code=401,
        )
    task = TASKS.get(task_id)
    if not task:
        return JSONResponse(
            {"error": {"code": "TaskNotFoundError", "message": f"Task not found: {task_id}"}},
            status_code=404,
        )
    return JSONResponse(task)


@app.post("/tasks/{task_id}:cancel")
@app.post("/tasks/{task_id}/cancel")
async def rest_cancel_task(task_id: str, request: Request) -> JSONResponse:
    if not _authorized(request):
        return JSONResponse(
            {"error": {"code": "Unauthorized", "message": "A2A invocation requires a valid bearer token"}},
            status_code=401,
        )
    task = TASKS.get(task_id)
    if not task:
        return JSONResponse(
            {"error": {"code": "TaskNotFoundError", "message": f"Task not found: {task_id}"}},
            status_code=404,
        )
    task["status"] = {"state": "canceled", "timestamp": _now()}
    return JSONResponse(task)
