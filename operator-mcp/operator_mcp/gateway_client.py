"""Construct Gateway Client — queries cost, audit, and governance APIs."""
from __future__ import annotations

import os
from typing import Any

from ._log import _log

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False


def _read_service_token() -> str:
    """Read the gateway service token.

    Order: ``CONSTRUCT_SERVICE_TOKEN`` env override →
    ``~/.construct/service-token`` (written by the Rust gateway at startup).
    Returns empty string when neither source is available; the caller logs and
    proceeds without auth (gateway will then 401, which is the correct signal).

    Mirrors ``operator_mcp.workflow.auth_resolver._service_token``.
    """
    env_tok = os.environ.get("CONSTRUCT_SERVICE_TOKEN", "").strip()
    if env_tok:
        return env_tok
    path = os.path.expanduser("~/.construct/service-token")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""
    except OSError as exc:
        _log(f"gateway_client: failed to read service token at {path}: {exc}")
        return ""


class ConstructGatewayClient:
    """Queries the Construct gateway REST API for cost/audit/governance data."""

    def __init__(self) -> None:
        self.gateway_url = os.environ.get("CONSTRUCT_GATEWAY_URL", "").rstrip("/")
        self.service_token = _read_service_token()
        self._available = bool(self.gateway_url and _HAS_HTTPX)
        if self._available:
            auth_state = "service-token" if self.service_token else "no token (calls will 401)"
            _log(f"Construct Gateway client enabled: {self.gateway_url} ({auth_state})")
        else:
            missing = []
            if not _HAS_HTTPX:
                missing.append("httpx not installed")
            if not self.gateway_url:
                missing.append("CONSTRUCT_GATEWAY_URL not set")
            _log(f"Construct Gateway client disabled: {', '.join(missing)}")

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Accept": "application/json"}
        latest_token = _read_service_token()
        if latest_token:
            self.service_token = latest_token
        if self.service_token:
            h["X-Construct-Service-Token"] = self.service_token
        return h

    async def get_cost_summary(self) -> dict[str, Any] | None:
        """Get current cost summary from the Rust gateway budget authority."""
        if not self._available:
            return None
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.gateway_url}/api/cost",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("cost", data)
        except Exception as e:
            _log(f"Gateway cost query failed: {e}")
            return None

    async def record_usage(
        self,
        *,
        model: str,
        provider: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        source: str = "sidecar",
        agent_id: str = "",
        agent_title: str = "",
    ) -> dict[str, Any] | None:
        """Record token usage in the Rust gateway cost tracker."""
        if not self._available:
            return None
        clean_model = (model or "").strip()
        if not clean_model:
            _log("Gateway usage ingest skipped: missing model")
            return None
        payload: dict[str, Any] = {
            "model": clean_model,
            "provider": (provider or "sidecar").strip(),
            "input_tokens": max(0, int(input_tokens or 0)),
            "output_tokens": max(0, int(output_tokens or 0)),
            "source": source or "sidecar",
        }
        if agent_id:
            payload["agent_id"] = agent_id
        if agent_title:
            payload["agent_title"] = agent_title
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self.gateway_url}/api/cost/usage",
                    headers=self._headers(),
                    json=payload,
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            _log(f"Gateway usage ingest failed: {e}")
            return None

    async def get_status(self) -> dict[str, Any] | None:
        """Get system status including config info."""
        if not self._available:
            return None
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.gateway_url}/api/status",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            _log(f"Gateway status query failed: {e}")
            return None

    async def register_workflow(
        self,
        name: str,
        description: str,
        definition_yaml: str,
        *,
        version: str = "1.0",
        tags: list[str] | None = None,
    ) -> str | None:
        """Register a workflow definition with the gateway REST API.

        This syncs disk-saved workflows to Kumiho so the dashboard can see them.
        Returns the workflow item kref on success (e.g. ``kref://Construct/Workflows/foo.workflow``)
        and ``None`` on any failure. Callers that only care about success/failure
        can use ``bool(kref)`` — ``None`` is falsy, a non-empty kref is truthy.

        Note: the Rust gateway endpoint creates a NEW revision and tags it
        ``published`` on every call. To get the revision_kref the caller
        should query Kumiho for the latest published revision of the returned
        item kref (this client deliberately stays thin and avoids Kumiho SDK
        coupling).
        """
        if not self._available:
            return None
        try:
            body: dict[str, Any] = {
                "name": name,
                "description": description,
                "definition": definition_yaml,
                "version": version,
            }
            if tags:
                body["tags"] = tags
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{self.gateway_url}/api/workflows",
                    json=body,
                    headers=self._headers(),
                )
                if resp.status_code in (200, 201):
                    try:
                        data = resp.json()
                    except Exception:
                        data = {}
                    item_kref = ""
                    if isinstance(data, dict):
                        wf = data.get("workflow") or {}
                        if isinstance(wf, dict):
                            item_kref = str(wf.get("kref", "") or "")
                    _log(f"Workflow '{name}' registered with gateway (kref={item_kref or '?'})")
                    return item_kref or None
                _log(f"Gateway register_workflow {resp.status_code}: {resp.text[:200]}")
                return None
        except Exception as e:
            _log(f"Gateway register_workflow failed: {e}")
            return None

    async def get_agents(self, include_deprecated: bool = False) -> list[dict[str, Any]] | None:
        """List pool agents from the gateway.

        Returns the ``agents`` array from ``GET /api/agents`` (each entry has
        ``name``, ``item_name``, ``agent_type``, ``role``, ``expertise``,
        ``identity``, etc.) or ``None`` if the gateway is unreachable.
        """
        if not self._available:
            return None
        try:
            params = {
                "include_deprecated": "true" if include_deprecated else "false",
                "page": "1",
                "per_page": "50",  # gateway caps per_page at 50
            }
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.gateway_url}/api/agents",
                    params=params,
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("agents", []) if isinstance(data, dict) else []
        except Exception as e:
            _log(f"Gateway agents query failed: {e}")
            return None

    async def get_auth_profiles(self) -> list[dict[str, Any]] | None:
        """List auth profile metadata from the gateway.

        Returns metadata-only summaries (no token bytes) from
        ``GET /api/auth/profiles``, or ``None`` if the gateway is
        unreachable.
        """
        if not self._available:
            return None
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.gateway_url}/api/auth/profiles",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("profiles", []) if isinstance(data, dict) else []
        except Exception as e:
            _log(f"Gateway auth-profiles query failed: {e}")
            return None

    async def get_skills(self, include_deprecated: bool = False) -> list[dict[str, Any]] | None:
        """List skills from the gateway.

        Returns the ``skills`` array from ``GET /api/skills`` (each entry has
        ``name``, ``description``, etc.) or ``None`` if the gateway is
        unreachable.
        """
        if not self._available:
            return None
        try:
            params = {
                "include_deprecated": "true" if include_deprecated else "false",
                "page": "1",
                "per_page": "50",
            }
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.gateway_url}/api/skills",
                    params=params,
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("skills", []) if isinstance(data, dict) else []
        except Exception as e:
            _log(f"Gateway skills query failed: {e}")
            return None

    async def get_channels(self) -> list[dict[str, Any]] | None:
        """List configured channels from the gateway.

        Returns the ``channels`` array from ``GET /api/channels`` (each entry
        has ``name``, ``type``/``kind``, etc.) or ``None`` if the gateway is
        unreachable.
        """
        if not self._available:
            return None
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.gateway_url}/api/channels",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("channels", []) if isinstance(data, dict) else []
        except Exception as e:
            _log(f"Gateway channels query failed: {e}")
            return None

    async def push_channel_event(self, event: dict[str, Any]) -> bool:
        """Push a structured channel event to the gateway for broadcast.

        Channel events are forwarded to all connected channels (dashboard,
        Slack, Discord) via the gateway's WebSocket bridge.

        Returns True if the event was accepted.
        """
        if not self._available:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self.gateway_url}/api/channel-events",
                    json=event,
                    headers=self._headers(),
                )
                return resp.status_code in (200, 201, 202)
        except Exception as e:
            _log(f"Gateway channel event push failed: {e}")
            return False
