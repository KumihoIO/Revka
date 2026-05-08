"""A2A outbound client — discover and call external A2A agents.

Implements the client side of the Google A2A protocol:
  - Agent discovery via .well-known/agent-card.json
  - Task creation via message/send (JSON-RPC 2.0)
  - Task polling via tasks/get
  - Task cancellation via tasks/cancel
  - Agent card caching in the A2ARegistry

Usage:
    client = A2AClient()
    card = await client.discover("https://agent.example.com")
    task = await client.send_task(card["url"], message="Review this code", skill_id="reviewer")
    result = await client.poll_task(card["url"], task["id"])
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from .._log import _log

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False


# ---------------------------------------------------------------------------
# A2A task states (from spec)
# ---------------------------------------------------------------------------

TERMINAL_STATES = {"completed", "failed", "canceled"}


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class A2AClient:
    """Outbound A2A protocol client."""

    def __init__(self, *, timeout: float = 60.0, max_retries: int = 2):
        if not _HAS_HTTPX:
            raise RuntimeError("httpx is required for A2A client — pip install httpx")
        self._timeout = timeout
        self._max_retries = max_retries
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                follow_redirects=True,
                limits=httpx.Limits(max_connections=10),
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # -- Discovery --

    async def discover(self, base_url: str) -> dict[str, Any]:
        """Fetch an agent card from a remote A2A endpoint.

        Tries /.well-known/agent-card.json first, then /agent-card.json.
        Caches the card in the A2ARegistry.

        Args:
            base_url: The agent's base URL (e.g. https://agent.example.com).

        Returns:
            The agent card dict.

        Raises:
            A2ADiscoveryError: If the agent card cannot be fetched.
        """
        base = base_url.rstrip("/")
        client = await self._get_client()

        card_urls = [
            f"{base}/.well-known/agent-card.json",
            f"{base}/agent-card.json",
        ]

        last_error = None
        for url in card_urls:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    card = resp.json()
                    # Validate minimum required fields
                    if not card.get("name"):
                        continue
                    # Cache in registry
                    from .a2a_registry import get_registry
                    registry = get_registry()
                    registry.register_external(base, card)
                    _log(f"a2a_client: discovered '{card.get('name')}' at {base}")
                    return card
            except Exception as exc:
                last_error = exc
                continue

        raise A2ADiscoveryError(
            f"Could not discover agent at {base}: {last_error or 'no valid card found'}"
        )

    # -- Task lifecycle --

    async def send_task(
        self,
        endpoint_url: str,
        *,
        message: str,
        skill_id: str | None = None,
        context_id: str | None = None,
        task_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        auth_token: str | None = None,
    ) -> dict[str, Any]:
        """Send a task to a remote A2A agent via message/send.

        Args:
            endpoint_url: The agent's A2A endpoint URL.
            message: The task message text.
            skill_id: Optional skill ID to route to.
            context_id: Optional conversation context ID.
            task_id: Optional task ID (generated if omitted).
            metadata: Optional metadata dict.

        Returns:
            The A2A task response dict.
        """
        client = await self._get_client()
        task_id = task_id or str(uuid.uuid4())

        params: dict[str, Any] = {
            "message": {
                "role": "user",
                "parts": [{"type": "text", "text": message}],
            },
        }
        if skill_id:
            params.setdefault("metadata", {})["skill_id"] = skill_id
        if context_id:
            params["contextId"] = context_id
        if task_id:
            params["id"] = task_id
        if metadata:
            params.setdefault("metadata", {}).update(metadata)

        request = {
            "jsonrpc": "2.0",
            "method": "message/send",
            "id": str(uuid.uuid4()),
            "params": params,
        }

        resp = await self._jsonrpc_call(client, endpoint_url, request, auth_token=auth_token)
        return resp.get("result", resp)

    async def get_task(self, endpoint_url: str, task_id: str) -> dict[str, Any]:
        """Poll task status from a remote A2A agent.

        Args:
            endpoint_url: The agent's A2A endpoint URL.
            task_id: The task ID to check.

        Returns:
            The task status dict.
        """
        client = await self._get_client()
        request = {
            "jsonrpc": "2.0",
            "method": "tasks/get",
            "id": str(uuid.uuid4()),
            "params": {"id": task_id},
        }
        resp = await self._jsonrpc_call(client, endpoint_url, request)
        return resp.get("result", resp)

    async def cancel_task(self, endpoint_url: str, task_id: str) -> dict[str, Any]:
        """Cancel a task on a remote A2A agent."""
        client = await self._get_client()
        request = {
            "jsonrpc": "2.0",
            "method": "tasks/cancel",
            "id": str(uuid.uuid4()),
            "params": {"id": task_id},
        }
        resp = await self._jsonrpc_call(client, endpoint_url, request)
        return resp.get("result", resp)

    async def poll_until_complete(
        self,
        endpoint_url: str,
        task_id: str,
        *,
        poll_interval: float = 5.0,
        max_polls: int = 60,
    ) -> dict[str, Any]:
        """Poll a task until it reaches a terminal state.

        Args:
            endpoint_url: The agent's A2A endpoint URL.
            task_id: The task ID.
            poll_interval: Seconds between polls (default 5).
            max_polls: Maximum number of polls (default 60 = 5 min).

        Returns:
            The final task dict.
        """
        for i in range(max_polls):
            task = await self.get_task(endpoint_url, task_id)
            status = task.get("status", {})
            state = status.get("state", "unknown")

            if state in TERMINAL_STATES:
                return task

            _log(f"a2a_client: poll {i+1}/{max_polls} task={task_id[:8]} state={state}")
            await asyncio.sleep(poll_interval)

        return await self.get_task(endpoint_url, task_id)

    # -- JSON-RPC transport --

    async def _jsonrpc_call(
        self,
        client: httpx.AsyncClient,
        url: str,
        request: dict[str, Any],
        *,
        auth_token: str | None = None,
    ) -> dict[str, Any]:
        """Make a JSON-RPC 2.0 call with retry."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        last_error = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = await client.post(
                    url,
                    json=request,
                    headers=headers,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if "error" in data:
                        raise A2ARemoteError(
                            f"JSON-RPC error: {data['error'].get('message', data['error'])}"
                        )
                    return data
                elif resp.status_code >= 500 and attempt < self._max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                else:
                    raise A2ATransportError(
                        f"HTTP {resp.status_code}: {resp.text[:500]}"
                    )
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_error = exc
                if attempt < self._max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise A2ATransportError(f"Connection failed after {self._max_retries + 1} attempts: {exc}")

        raise A2ATransportError(f"Request failed: {last_error}")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class A2AClientError(Exception):
    """Base class for A2A client errors."""

class A2ADiscoveryError(A2AClientError):
    """Agent card discovery failed."""

class A2ATransportError(A2AClientError):
    """HTTP transport error."""

class A2ARemoteError(A2AClientError):
    """Remote agent returned a JSON-RPC error."""


# ---------------------------------------------------------------------------
# Module singleton
# ---------------------------------------------------------------------------

_client: A2AClient | None = None


def get_client(timeout: float = 60.0) -> A2AClient:
    """Get or create the global A2A client."""
    global _client
    if _client is None:
        _client = A2AClient(timeout=timeout)
    return _client


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

async def tool_a2a_discover(args: dict[str, Any]) -> dict[str, Any]:
    """Discover an external A2A agent by URL.

    Args:
        url: Base URL of the agent (required).
        timeout: Discovery timeout in seconds (default 30).
    """
    from ..failure_classification import classified_error, VALIDATION_ERROR

    url = args.get("url", "")
    if not url:
        return classified_error("url is required", code="missing_url", category=VALIDATION_ERROR)

    timeout = args.get("timeout", 30.0)
    try:
        client = get_client(timeout=timeout)
        card = await client.discover(url)
        return {
            "discovered": True,
            "url": url,
            "name": card.get("name", ""),
            "description": card.get("description", ""),
            "skills": card.get("skills", []),
            "capabilities": card.get("capabilities", {}),
        }
    except A2AClientError as exc:
        return {
            "discovered": False,
            "url": url,
            "error": str(exc),
        }


async def tool_a2a_send_task(args: dict[str, Any]) -> dict[str, Any]:
    """Send a task to an external A2A agent.

    Args:
        url: A2A endpoint URL (required).
        message: Task message text (required).
        skill_id: Optional skill to route to.
        wait: Whether to poll until complete (default false).
        timeout: Request timeout in seconds (default 60).
    """
    from ..failure_classification import classified_error, VALIDATION_ERROR

    url = args.get("url", "")
    message = args.get("message", "")
    skill_id = args.get("skill_id")
    wait = args.get("wait", False)
    timeout = args.get("timeout", 60.0)

    if not url:
        return classified_error("url is required", code="missing_url", category=VALIDATION_ERROR)
    if not message:
        return classified_error("message is required", code="missing_message", category=VALIDATION_ERROR)

    try:
        client = get_client(timeout=timeout)
        task = await client.send_task(url, message=message, skill_id=skill_id)

        task_id = task.get("id", "")
        status = task.get("status", {})
        state = status.get("state", "unknown")

        if wait and state not in TERMINAL_STATES:
            task = await client.poll_until_complete(url, task_id)
            status = task.get("status", {})
            state = status.get("state", "unknown")

        # Extract text from artifacts
        output_text = ""
        for artifact in task.get("artifacts", []):
            for part in artifact.get("parts", []):
                if part.get("type") == "text":
                    output_text += part.get("text", "") + "\n"

        return {
            "task_id": task_id,
            "status": state,
            "output": output_text[:6000] if output_text else "",
            "artifacts_count": len(task.get("artifacts", [])),
            "full_response": task,
        }
    except A2AClientError as exc:
        return {
            "task_id": "",
            "status": "error",
            "error": str(exc),
        }


async def tool_a2a_get_task(args: dict[str, Any]) -> dict[str, Any]:
    """Check status of a task on an external A2A agent.

    Args:
        url: A2A endpoint URL (required).
        task_id: Task ID to check (required).
    """
    from ..failure_classification import classified_error, VALIDATION_ERROR

    url = args.get("url", "")
    task_id = args.get("task_id", "")

    if not url:
        return classified_error("url is required", code="missing_url", category=VALIDATION_ERROR)
    if not task_id:
        return classified_error("task_id is required", code="missing_task_id", category=VALIDATION_ERROR)

    try:
        client = get_client()
        task = await client.get_task(url, task_id)
        status = task.get("status", {})

        output_text = ""
        for artifact in task.get("artifacts", []):
            for part in artifact.get("parts", []):
                if part.get("type") == "text":
                    output_text += part.get("text", "") + "\n"

        return {
            "task_id": task_id,
            "status": status.get("state", "unknown"),
            "message": status.get("message", ""),
            "output": output_text[:6000],
            "artifacts_count": len(task.get("artifacts", [])),
        }
    except A2AClientError as exc:
        return {
            "task_id": task_id,
            "status": "error",
            "error": str(exc),
        }
