"""Cloud Run A2A wrapper around the Construct Track 3 ADK agent."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


APP_NAME = "construct-agentops-a2a"
SERVICE_VERSION = "1.0.0"
ICON_URL = (
    "data:image/svg+xml;base64,"
    "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdC"
    "b3g9IjAgMCA2NCA2NCI+PHJlY3Qgd2lkdGg9IjY0IiBoZWlnaHQ9IjY0IiBy"
    "eD0iMTIiIGZpbGw9IiMxMTE4MjciLz48cGF0aCBkPSJNMTYgMzZoMTBsNi0x"
    "OCA2IDI4IDYtMTZoOCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSIjMzhiZGY4IiBz"
    "dHJva2Utd2lkdGg9IjUiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tl"
    "LWxpbmVqb2luPSJyb3VuZCIvPjwvc3ZnPg=="
)
TASKS: dict[str, dict[str, Any]] = {}

app = FastAPI(title="Construct AgentOps A2A", version=SERVICE_VERSION)

_runner: Any | None = None
_session_service: Any | None = None


class JsonRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: str | int | None = None
    method: str
    params: dict[str, Any] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _base_url(request: Request) -> str:
    configured = os.getenv("PUBLIC_BASE_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    return str(request.base_url).rstrip("/")


def _agent_card(base_url: str) -> dict[str, Any]:
    return {
        "protocolVersion": "0.3",
        "name": "Construct Enterprise AgentOps Control Plane",
        "description": (
            "B2B A2A agent that coordinates incident triage, governance, "
            "deployment evidence, and rollback planning for enterprise software teams."
        ),
        "url": base_url,
        "iconUrl": ICON_URL,
        "version": SERVICE_VERSION,
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
        },
        "provider": {
            "organization": "Construct / KumihoIO",
            "url": base_url,
        },
        "skills": [
            {
                "id": "enterprise-agentops-incident-plan",
                "name": "Enterprise AgentOps Incident Plan",
                "description": (
                    "Builds a governed incident plan with A2A handoff, Google Cloud "
                    "evidence, rollback, and approval boundaries."
                ),
                "tags": [
                    "b2b",
                    "agentops",
                    "incident-response",
                    "a2a",
                    "google-cloud",
                    "adk",
                    "gemini",
                ],
                "examples": [
                    (
                        "A payments deploy failed after a config change. Build an "
                        "enterprise incident plan with owner, rollback, evidence, and A2A handoff."
                    )
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
            "business_context": "enterprise-agentops",
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
                "name": "enterprise-agentops-plan",
                "description": "Gemini/ADK generated B2B incident response plan.",
                "parts": [{"type": "text", "text": response}],
            }
        )
    if error:
        artifacts.append(
            {
                "artifactId": f"artifact-{task_id}-error",
                "name": "agent-error",
                "description": "Runtime error captured for operator diagnosis.",
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
            "b2bPackage": "Construct Enterprise AgentOps Control Plane",
        },
    }


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {
        "ok": True,
        "service": APP_NAME,
        "track": "google-startups-ai-agents-track-3",
        "orchestration": "Google ADK",
        "intelligence": "Gemini via Vertex AI",
    }


@app.get("/statusz")
async def statusz() -> dict[str, Any]:
    return await healthz()


@app.get("/runtime")
async def runtime() -> dict[str, Any]:
    return await healthz()


@app.get("/.well-known/agent-card.json")
async def well_known_agent_card(request: Request) -> dict[str, Any]:
    return _agent_card(_base_url(request))


@app.get("/agent-card.json")
async def agent_card(request: Request) -> dict[str, Any]:
    return _agent_card(_base_url(request))


@app.post("/")
@app.post("/a2a")
async def jsonrpc_endpoint(payload: JsonRpcRequest, request: Request) -> JSONResponse:
    if payload.method == "agent/card":
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": payload.id,
                "result": _agent_card(_base_url(request)),
            }
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
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": payload.id,
                "error": {"code": "InvalidRequest", "message": "No text content in message"},
            },
            status_code=400,
        )

    task_id = str(payload.params.get("id") or f"task-{uuid.uuid4()}")
    context_id = str(payload.params.get("contextId") or f"ctx-{uuid.uuid4()}")
    user_id = str(payload.params.get("metadata", {}).get("user_id") or "track3-demo-user")

    try:
        response = await _adk_response(message, user_id=user_id, session_id=context_id)
        task = _task(
            task_id=task_id,
            context_id=context_id,
            state="completed",
            message=message,
            response=response,
        )
    except Exception as exc:
        task = _task(
            task_id=task_id,
            context_id=context_id,
            state="failed",
            message=message,
            error=str(exc),
        )

    TASKS[task_id] = task
    return JSONResponse({"jsonrpc": "2.0", "id": payload.id, "result": task})
