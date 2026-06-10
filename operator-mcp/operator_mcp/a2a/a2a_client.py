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
import contextlib
import json
import shutil
import uuid
from typing import Any
from urllib.parse import urlsplit

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


def _token(value: Any) -> str | None:
    """Return a non-empty token string without accepting structured secrets."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _auth_headers(
    *,
    auth_token: str | None = None,
    cloud_run_identity_token: str | None = None,
    content_type: str | None = None,
) -> dict[str, str]:
    """Build outbound auth headers for A2A and Cloud Run IAM.

    Cloud Run IAM consumes ``X-Serverless-Authorization``. The A2A application
    can independently consume ``Authorization`` for its bearer-token policy, so
    keep the two tokens separate instead of overloading one header.
    """
    headers: dict[str, str] = {}
    if content_type:
        headers["Content-Type"] = content_type
    if token := _token(cloud_run_identity_token):
        headers["X-Serverless-Authorization"] = f"Bearer {token}"
    if token := _token(auth_token):
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _origin_url(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return value.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}"


_METADATA_IDENTITY_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/"
    "service-accounts/default/identity"
)


async def _metadata_identity_token(audience: str | None, *, timeout: float) -> str | None:
    """Mint an identity token from the GCP metadata server (Cloud Run / GCE).

    Returns None when not on a GCP runtime so callers can fall back to the
    gcloud CLI for local development.
    """
    if not audience:
        return None
    try:
        import httpx

        async with httpx.AsyncClient(timeout=min(timeout, 5.0), trust_env=False) as client:
            resp = await client.get(
                _METADATA_IDENTITY_URL,
                params={"audience": audience, "format": "full"},
                headers={"Metadata-Flavor": "Google"},
            )
        if resp.status_code == 200 and resp.text.strip():
            return resp.text.strip()
    except Exception:  # noqa: BLE001 — metadata server absent off-GCP
        return None
    return None


async def _gcloud_identity_token(
    audience: str | None,
    *,
    timeout: float = 20.0,
    configuration: str | None = None,
) -> str:
    # On a GCP runtime (Cloud Run / GCE) the metadata server can mint an
    # audience-scoped identity token without the gcloud CLI being installed in
    # the image. Prefer it; only fall back to the gcloud binary for local dev.
    # A non-default `configuration` is a gcloud-CLI concept, so skip the
    # metadata path when one is requested.
    if not _token(configuration):
        metadata_token = await _metadata_identity_token(audience, timeout=timeout)
        if metadata_token:
            return metadata_token

    binary = shutil.which("gcloud")
    if not binary:
        raise A2ATransportError(
            "Cloud Run auth requires either the GCP metadata server (Cloud Run / "
            "GCE) or the gcloud CLI; neither was available (metadata mint "
            "returned no token and gcloud was not found in PATH)"
        )

    async def _run(command: list[str]) -> tuple[int | None, str, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise A2ATransportError(f"failed to execute gcloud for Cloud Run auth: {exc}") from exc
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            proc.kill()
            with contextlib.suppress(ProcessLookupError):
                await proc.wait()
            raise A2ATransportError(
                f"gcloud identity-token mint timed out after {timeout:.0f}s"
            ) from exc
        return (
            proc.returncode,
            stdout_b.decode("utf-8", errors="replace").strip(),
            stderr_b.decode("utf-8", errors="replace").strip(),
        )

    command = [binary]
    config_name = _token(configuration)
    if config_name:
        command.append(f"--configuration={config_name}")
    command.extend(["auth", "print-identity-token"])
    if audience:
        command.append(f"--audiences={audience}")

    returncode, token, stderr = await _run(command)
    if returncode != 0 and audience and "Invalid account type for `--audiences`" in stderr:
        fallback = [binary]
        if config_name:
            fallback.append(f"--configuration={config_name}")
        fallback.extend(["auth", "print-identity-token"])
        returncode, token, stderr = await _run(fallback)

    if returncode != 0:
        raise A2ATransportError(
            "gcloud identity-token mint failed: " + (stderr[-500:] or f"exit {returncode}")
        )
    if not token:
        raise A2ATransportError("gcloud identity-token mint returned an empty token")
    return token


async def _resolve_cloud_run_identity_token(args: dict[str, Any], *, url: str) -> str | None:
    explicit = _token(
        args.get("cloud_run_identity_token")
        or args.get("serverless_identity_token")
        or args.get("identity_token")
    )
    if explicit:
        return explicit

    mode = args.get("cloud_run_auth")
    enabled = mode is True or (isinstance(mode, str) and mode.strip().lower() in {"gcloud", "google", "auto"})
    if not enabled:
        return None

    audience = _token(args.get("cloud_run_audience")) or _origin_url(url)
    timeout = float(args.get("cloud_run_auth_timeout") or 20.0)
    configuration = _token(args.get("cloud_run_config") or args.get("gcloud_config"))
    # On GCP runtimes (Cloud Run, GCE) the metadata server is the native
    # token source and no gcloud CLI exists in the image; off-GCP it is
    # unreachable and we fall back to gcloud for local development.
    metadata_token = await _metadata_identity_token(audience, timeout=timeout)
    if metadata_token:
        return metadata_token
    return await _gcloud_identity_token(
        audience,
        timeout=timeout,
        configuration=configuration,
    )


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

    async def discover(
        self,
        base_url: str,
        *,
        auth_token: str | None = None,
        cloud_run_identity_token: str | None = None,
    ) -> dict[str, Any]:
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
                resp = await client.get(
                    url,
                    headers=_auth_headers(
                        auth_token=auth_token,
                        cloud_run_identity_token=cloud_run_identity_token,
                    ),
                )
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
        cloud_run_identity_token: str | None = None,
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

        resp = await self._jsonrpc_call(
            client,
            endpoint_url,
            request,
            auth_token=auth_token,
            cloud_run_identity_token=cloud_run_identity_token,
        )
        return resp.get("result", resp)

    async def get_task(
        self,
        endpoint_url: str,
        task_id: str,
        *,
        auth_token: str | None = None,
        cloud_run_identity_token: str | None = None,
    ) -> dict[str, Any]:
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
        resp = await self._jsonrpc_call(
            client,
            endpoint_url,
            request,
            auth_token=auth_token,
            cloud_run_identity_token=cloud_run_identity_token,
        )
        return resp.get("result", resp)

    async def cancel_task(
        self,
        endpoint_url: str,
        task_id: str,
        *,
        auth_token: str | None = None,
        cloud_run_identity_token: str | None = None,
    ) -> dict[str, Any]:
        """Cancel a task on a remote A2A agent."""
        client = await self._get_client()
        request = {
            "jsonrpc": "2.0",
            "method": "tasks/cancel",
            "id": str(uuid.uuid4()),
            "params": {"id": task_id},
        }
        resp = await self._jsonrpc_call(
            client,
            endpoint_url,
            request,
            auth_token=auth_token,
            cloud_run_identity_token=cloud_run_identity_token,
        )
        return resp.get("result", resp)

    async def poll_until_complete(
        self,
        endpoint_url: str,
        task_id: str,
        *,
        poll_interval: float = 5.0,
        max_polls: int = 60,
        auth_token: str | None = None,
        cloud_run_identity_token: str | None = None,
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
            task = await self.get_task(
                endpoint_url,
                task_id,
                auth_token=auth_token,
                cloud_run_identity_token=cloud_run_identity_token,
            )
            status = task.get("status", {})
            state = status.get("state", "unknown")

            if state in TERMINAL_STATES:
                return task

            _log(f"a2a_client: poll {i+1}/{max_polls} task={task_id[:8]} state={state}")
            await asyncio.sleep(poll_interval)

        return await self.get_task(
            endpoint_url,
            task_id,
            auth_token=auth_token,
            cloud_run_identity_token=cloud_run_identity_token,
        )

    # -- JSON-RPC transport --

    async def _jsonrpc_call(
        self,
        client: httpx.AsyncClient,
        url: str,
        request: dict[str, Any],
        *,
        auth_token: str | None = None,
        cloud_run_identity_token: str | None = None,
    ) -> dict[str, Any]:
        """Make a JSON-RPC 2.0 call with retry."""
        headers = _auth_headers(
            auth_token=auth_token,
            cloud_run_identity_token=cloud_run_identity_token,
            content_type="application/json",
        )
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
    auth_token = _token(
        args.get("auth_token")
        or args.get("a2a_bearer_token")
        or args.get("app_bearer_token")
    )
    try:
        cloud_run_identity_token = await _resolve_cloud_run_identity_token(args, url=url)
        client = get_client(timeout=timeout)
        card = await client.discover(
            url,
            auth_token=auth_token,
            cloud_run_identity_token=cloud_run_identity_token,
        )
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
    auth_token = _token(
        args.get("auth_token")
        or args.get("a2a_bearer_token")
        or args.get("app_bearer_token")
    )

    if not url:
        return classified_error("url is required", code="missing_url", category=VALIDATION_ERROR)
    if not message:
        return classified_error("message is required", code="missing_message", category=VALIDATION_ERROR)

    try:
        cloud_run_identity_token = await _resolve_cloud_run_identity_token(args, url=url)
        client = get_client(timeout=timeout)
        task = await client.send_task(
            url,
            message=message,
            skill_id=skill_id,
            auth_token=auth_token,
            cloud_run_identity_token=cloud_run_identity_token,
        )

        task_id = task.get("id", "")
        status = task.get("status", {})
        state = status.get("state", "unknown")

        if wait and state not in TERMINAL_STATES:
            task = await client.poll_until_complete(
                url,
                task_id,
                auth_token=auth_token,
                cloud_run_identity_token=cloud_run_identity_token,
            )
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
    auth_token = _token(
        args.get("auth_token")
        or args.get("a2a_bearer_token")
        or args.get("app_bearer_token")
    )

    if not url:
        return classified_error("url is required", code="missing_url", category=VALIDATION_ERROR)
    if not task_id:
        return classified_error("task_id is required", code="missing_task_id", category=VALIDATION_ERROR)

    try:
        cloud_run_identity_token = await _resolve_cloud_run_identity_token(args, url=url)
        client = get_client()
        task = await client.get_task(
            url,
            task_id,
            auth_token=auth_token,
            cloud_run_identity_token=cloud_run_identity_token,
        )
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
