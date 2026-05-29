"""Kumiho clients for Construct Operator.

Uses kumiho.mcp_server tool functions (shared venv) instead of raw gRPC.

Contains:
- KumihoSDKClient — thin wrapper around kumiho.mcp_server tool_* functions
- KumihoAgentPoolClient — manages Construct/AgentPool
- KumihoTeamClient — manages Construct/Teams bundles
- resolve_agent_krefs() — resolve LLM-provided names to actual Kumiho krefs
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from ._log import _log
from .agent_state import normalize_agent_type
from .construct_config import harness_project, kumiho_connection_config

_KUMIHO_IMPORT_ERROR: BaseException | None = None

# kumiho.mcp_server performs auto-configuration at import time when this env var
# is set. That can refresh network credentials before the Operator MCP server
# has answered `initialize`, which makes the whole sidecar unavailable. Keep
# auto-config lazy by suppressing it only while importing the tool functions.
_AUTO_CONFIGURE = os.environ.pop("KUMIHO_AUTO_CONFIGURE", None)
tool_batch_get_revisions = None  # type: ignore[assignment]
tool_get_item = None  # type: ignore[assignment]
tool_get_item_revisions = None  # type: ignore[assignment]
tool_get_revision = None  # type: ignore[assignment]
try:
    from kumiho.mcp_server import (
        _ensure_configured,
        tool_create_project,
        tool_create_space,
        tool_create_item,
        tool_create_revision,
        tool_tag_revision,
        tool_search_items,
        tool_fulltext_search,
        tool_create_bundle,
        tool_get_bundle,
        tool_get_bundle_members,
        tool_add_bundle_member,
        tool_remove_bundle_member,
        tool_create_edge,
        tool_get_edges,
        tool_deprecate_item,
        tool_get_revision_by_tag,
        tool_create_artifact,
        tool_get_artifacts,
        tool_get_artifacts_by_location,
        tool_resolve_kref,
        tool_get_dependencies,
        tool_get_dependents,
        tool_analyze_impact,
    )
    _HAS_KUMIHO = True

    # Optional — not present in all kumiho versions
    try:
        from kumiho.mcp_server import tool_batch_get_revisions
    except ImportError:
        tool_batch_get_revisions = None  # type: ignore[assignment]

    try:
        from kumiho.mcp_server import tool_get_item, tool_get_item_revisions, tool_get_revision
    except ImportError:
        tool_get_item = None  # type: ignore[assignment]
        tool_get_item_revisions = None  # type: ignore[assignment]
        tool_get_revision = None  # type: ignore[assignment]
except Exception as exc:
    _HAS_KUMIHO = False
    _KUMIHO_IMPORT_ERROR = exc
except SystemExit as exc:
    _HAS_KUMIHO = False
    _KUMIHO_IMPORT_ERROR = exc
finally:
    if _AUTO_CONFIGURE is not None:
        os.environ["KUMIHO_AUTO_CONFIGURE"] = _AUTO_CONFIGURE

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False


# ---------------------------------------------------------------------------
# KumihoSDKClient — wrapper around kumiho.mcp_server tool functions
# ---------------------------------------------------------------------------

class KumihoSDKClient:
    """Kumiho client using mcp_server tool functions (no raw gRPC).

    Initialization is lazy — _ensure_configured() is deferred until the
    first operation so it doesn't block the MCP initialize handshake.
    """

    def __init__(self) -> None:
        self._available = False
        self._initialized = False

    def _lazy_init(self) -> None:
        """Run the (potentially blocking) Kumiho configuration once."""
        if self._initialized:
            return
        self._initialized = True
        if not _HAS_KUMIHO:
            detail = f": {_KUMIHO_IMPORT_ERROR}" if _KUMIHO_IMPORT_ERROR else ""
            _log(
                "Kumiho not available "
                f"(kumiho package not installed or failed to import{detail})"
            )
            return
        try:
            ok = _ensure_configured()
            self._available = ok
            if ok:
                _log("Kumiho client connected (shared venv)")
        except SystemExit as e:
            self._available = False
            _log(f"Kumiho init exited during auto-configure: {e}")
        except Exception as e:
            self._available = False
            _log(f"Kumiho init failed: {e}")

    # -- Generic Kumiho operations (async wrappers around tool functions) ---

    async def ensure_space(self, project: str, space: str) -> None:
        """Ensure project + space exist (idempotent)."""
        def _call():
            r = tool_create_project(project)
            if "error" in r and "already exists" not in r["error"].lower():
                _log(f"create_project warning: {r['error']}")
            r = tool_create_space(project, space)
            if "error" in r and "already exists" not in r["error"].lower():
                _log(f"create_space warning: {r['error']}")
        await asyncio.to_thread(_call)

    async def ensure_space_path(self, space_path: str) -> None:
        """Ensure every level of a Kumiho space path exists."""
        parts = space_path.strip("/").split("/")
        if not parts or not parts[0]:
            return

        project = parts[0]
        if len(parts) == 1:
            def _call_project() -> None:
                r = tool_create_project(project)
                if "error" in r and "already exists" not in r["error"].lower():
                    _log(f"create_project warning: {r['error']}")

            await asyncio.to_thread(_call_project)
            return

        await self.ensure_space(project, parts[1])

        for i in range(2, len(parts)):
            parent_path = "/" + "/".join(parts[:i])
            segment = parts[i]

            def _call_nested(p=project, s=segment, pp=parent_path) -> None:
                r = tool_create_space(p, s, parent_path=pp)
                if "error" in r and "already exists" not in r["error"].lower():
                    _log(f"create_space({pp}/{s}) warning: {r['error']}")

            await asyncio.to_thread(_call_nested)

    async def list_items(self, space_path: str, include_deprecated: bool = False) -> list[dict[str, Any]]:
        def _call():
            clean_path = space_path.lstrip("/")
            r = tool_search_items(context_filter=clean_path, name_filter="", kind_filter="")
            if "error" in r:
                _log(f"list_items error: {r['error']}")
                return []
            items = r.get("items", [])
            if not include_deprecated:
                items = [i for i in items if not i.get("deprecated")]
            return items
        return await asyncio.to_thread(_call)

    async def search_items(
        self,
        context: str = "",
        name: str = "",
        kind: str = "",
        include_metadata: bool = False,
    ) -> list[dict[str, Any]]:
        """Search Kumiho items without creating or modifying graph state."""
        def _call():
            r = tool_search_items(
                context_filter=context,
                name_filter=name,
                kind_filter=kind,
                include_metadata=include_metadata,
            )
            if "error" in r:
                _log(f"search_items error: {r['error']}")
                return []
            return r.get("items", [])
        return await asyncio.to_thread(_call)

    async def get_item(self, item_kref: str) -> dict[str, Any] | None:
        """Fetch an item by kref."""
        if tool_get_item is None:
            return None
        def _call():
            r = tool_get_item(item_kref)
            if "error" in r:
                return None
            return r.get("item", r)
        return await asyncio.to_thread(_call)

    async def get_item_revisions(
        self,
        item_kref: str,
        include_metadata: bool = True,
    ) -> list[dict[str, Any]]:
        """Fetch all revisions for an item."""
        if tool_get_item_revisions is None:
            return []
        def _call():
            r = tool_get_item_revisions(item_kref, include_metadata=include_metadata)
            if "error" in r:
                return []
            return r.get("revisions", [])
        return await asyncio.to_thread(_call)

    async def get_revision(self, revision_kref: str) -> dict[str, Any] | None:
        """Fetch one exact revision by revision kref."""
        if tool_get_revision is None:
            return None
        def _call():
            r = tool_get_revision(revision_kref)
            if "error" in r:
                return None
            return r.get("revision", r)
        return await asyncio.to_thread(_call)

    async def get_revision_by_tag(self, item_kref: str, tag: str) -> dict[str, Any] | None:
        """Fetch a revision by tag without falling back to latest."""
        def _call():
            r = tool_get_revision_by_tag(item_kref, tag)
            if "error" in r:
                return None
            return r.get("revision", r)
        return await asyncio.to_thread(_call)

    async def create_item(
        self, space_path: str, name: str, kind: str, metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        def _call():
            str_meta = {str(k): str(v) for k, v in (metadata or {}).items()} if metadata else None
            r = tool_create_item(space_path.lstrip("/"), name, kind, str_meta)
            if "error" in r:
                raise RuntimeError(r["error"])
            return r.get("item", r)
        return await asyncio.to_thread(_call)

    async def create_revision(
        self, item_kref: str, metadata: dict[str, Any], tag: str | None = "published",
    ) -> dict[str, Any]:
        def _call():
            str_meta = {str(k): str(v) for k, v in metadata.items()}
            r = tool_create_revision(item_kref, str_meta)
            if "error" in r:
                raise RuntimeError(r["error"])
            rev = r.get("revision", r)
            if tag and rev.get("kref"):
                tool_tag_revision(rev["kref"], tag)
            return rev
        return await asyncio.to_thread(_call)

    async def get_latest_revision(self, item_kref: str, tag: str = "published") -> dict[str, Any] | None:
        def _call():
            r = tool_get_revision_by_tag(item_kref, tag)
            if "error" in r:
                r = tool_get_revision_by_tag(item_kref, "latest")
            if "error" in r:
                return None
            return r.get("revision", r)
        return await asyncio.to_thread(_call)

    async def batch_get_revisions(self, item_krefs: list[str], tag: str = "published") -> dict[str, dict[str, Any]]:
        """Batch fetch revisions by item krefs, returning a map of item_kref -> revision dict."""
        if tool_batch_get_revisions is None:
            # Fallback: fetch one by one
            result: dict[str, dict[str, Any]] = {}
            for ik in item_krefs:
                rev = await self.get_latest_revision(ik, tag)
                if rev:
                    result[ik] = rev
            return result

        def _call():
            r = tool_batch_get_revisions(item_krefs=item_krefs, tag=tag)
            if "error" in r:
                return {}
            result = {}
            for rev in r.get("revisions", []):
                ik = rev.get("item_kref", "")
                if ik:
                    result[ik] = rev
            return result
        return await asyncio.to_thread(_call)

    async def search(self, query: str, context: str = "", kind: str = "", include_revision_metadata: bool = False) -> list[dict[str, Any]]:
        def _call():
            r = tool_fulltext_search(query, context=context, kind=kind, include_revision_metadata=include_revision_metadata)
            if "error" in r:
                return []
            results = r.get("results", [])
            return [{"item": sr.get("item", sr), "score": sr.get("score", 0.0)} for sr in results]
        return await asyncio.to_thread(_call)

    # -- Bundle operations (for teams) -----------------------------------------

    async def create_bundle(self, space_path: str, name: str, metadata: dict[str, str] | None = None) -> dict[str, Any]:
        await self.ensure_space_path(space_path)

        def _call():
            clean_path = space_path.lstrip("/")
            _log(f"create_bundle: space={clean_path!r} name={name!r}")
            r = tool_create_bundle(clean_path, name, metadata=metadata)
            if "error" in r:
                _log(f"create_bundle error: {r['error']}")
                raise RuntimeError(r["error"])
            return r.get("bundle", r)
        return await asyncio.to_thread(_call)

    async def get_bundle_by_kref(self, kref: str) -> dict[str, Any]:
        def _call():
            r = tool_get_bundle(kref)
            if "error" in r:
                raise RuntimeError(r["error"])
            return r.get("bundle", r)
        return await asyncio.to_thread(_call)

    async def get_bundle_members(self, bundle_kref: str) -> list[dict[str, Any]]:
        def _call():
            r = tool_get_bundle_members(bundle_kref)
            if "error" in r:
                return []
            return r.get("members", [])
        return await asyncio.to_thread(_call)

    async def add_bundle_member(self, bundle_kref: str, item_kref: str) -> bool:
        def _call():
            _log(f"add_bundle_member: bundle={bundle_kref!r} item={item_kref!r}")
            r = tool_add_bundle_member(bundle_kref, item_kref)
            if "error" in r:
                _log(f"add_bundle_member error: {r['error']}")
                return False
            added = r.get("added", False)
            _log(f"add_bundle_member result: added={added}")
            return added
        return await asyncio.to_thread(_call)

    async def remove_bundle_member(self, bundle_kref: str, item_kref: str) -> bool:
        def _call():
            r = tool_remove_bundle_member(bundle_kref, item_kref)
            return r.get("removed", False)
        return await asyncio.to_thread(_call)

    # -- Artifact operations ---------------------------------------------------

    async def create_artifact(
        self,
        revision_kref: str,
        name: str,
        location: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Attach an artifact (file reference) to a revision."""
        def _call():
            str_meta = {str(k): str(v) for k, v in (metadata or {}).items()} if metadata else None
            if str_meta:
                # Older kumiho.mcp_server exposes artifact metadata on the
                # underlying SDK but not on tool_create_artifact. Use the same
                # configured singleton directly so workflow output metadata can
                # target artifacts without waiting for a tool wrapper bump.
                try:
                    from kumiho.mcp_server import _serialize_artifact, kumiho
                    revision = kumiho.get_revision(revision_kref)
                    artifact = revision.create_artifact(name, location, metadata=str_meta)
                    return _serialize_artifact(artifact)
                except Exception as exc:
                    raise RuntimeError(str(exc)) from exc
            r = tool_create_artifact(revision_kref, name, location)
            if "error" in r:
                raise RuntimeError(r["error"])
            return r.get("artifact", r)
        return await asyncio.to_thread(_call)

    async def get_artifacts(self, revision_kref: str) -> list[dict[str, Any]]:
        """Get all artifacts for a revision."""
        def _call():
            r = tool_get_artifacts(revision_kref)
            if "error" in r:
                return []
            return r.get("artifacts", [])
        return await asyncio.to_thread(_call)

    # -- Graph traversal operations --------------------------------------------

    async def get_artifacts_by_location(self, location: str) -> list[dict[str, Any]]:
        """Reverse lookup: find all artifacts referencing a file location."""
        def _call():
            r = tool_get_artifacts_by_location(location)
            if "error" in r:
                return []
            return r.get("artifacts", [])
        return await asyncio.to_thread(_call)

    async def resolve_kref(self, kref: str) -> str | None:
        """Resolve a kref URI to its file location on disk."""
        def _call():
            r = tool_resolve_kref(kref)
            if "error" in r or not r.get("resolved"):
                return None
            return r.get("location")
        return await asyncio.to_thread(_call)

    async def get_dependencies(self, revision_kref: str, edge_types: list[str] | None = None) -> list[dict[str, Any]]:
        """Get upstream dependencies of a revision (what it was DERIVED_FROM, DEPENDS_ON)."""
        def _call():
            kwargs: dict[str, Any] = {"revision_kref": revision_kref}
            if edge_types:
                kwargs["edge_types"] = edge_types
            r = tool_get_dependencies(**kwargs)
            if "error" in r:
                return []
            return r.get("dependencies", [])
        return await asyncio.to_thread(_call)

    async def get_dependents(self, revision_kref: str, edge_types: list[str] | None = None) -> list[dict[str, Any]]:
        """Get downstream dependents of a revision (what DERIVED_FROM or DEPENDS_ON this)."""
        def _call():
            kwargs: dict[str, Any] = {"revision_kref": revision_kref}
            if edge_types:
                kwargs["edge_types"] = edge_types
            r = tool_get_dependents(**kwargs)
            if "error" in r:
                return []
            return r.get("dependents", [])
        return await asyncio.to_thread(_call)

    async def analyze_impact(self, revision_kref: str) -> dict[str, Any]:
        """Analyze what downstream work depends on a revision."""
        def _call():
            r = tool_analyze_impact(revision_kref)
            if "error" in r:
                return {"error": r["error"]}
            return r
        return await asyncio.to_thread(_call)

    # -- Edge operations -------------------------------------------------------

    async def create_edge(
        self, source_rev_kref: str, target_rev_kref: str, edge_type: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        def _call():
            r = tool_create_edge(source_rev_kref, target_rev_kref, edge_type, metadata=metadata)
            if isinstance(r, dict) and "error" in r:
                raise RuntimeError(r["error"])
        await asyncio.to_thread(_call)

    async def get_edges(self, rev_kref: str, direction: int = 0) -> list[dict[str, str]]:
        def _call():
            dir_str = {0: "both", 1: "outgoing", 2: "incoming"}.get(direction, "both")
            r = tool_get_edges(rev_kref, direction=dir_str)
            if "error" in r:
                return []
            return r.get("edges", [])
        return await asyncio.to_thread(_call)

    async def delete_edge(self, source_kref: str, target_kref: str, edge_type: str) -> None:
        def _call():
            from kumiho.mcp_server import tool_delete_edge
            tool_delete_edge(source_kref, target_kref, edge_type)
        await asyncio.to_thread(_call)

    async def tag_revision(self, revision_kref: str, tag: str) -> None:
        """Apply a tag to a specific revision."""
        def _call():
            r = tool_tag_revision(revision_kref, tag)
            if isinstance(r, dict) and "error" in r:
                raise RuntimeError(r["error"])
        await asyncio.to_thread(_call)

    async def untag_revision(self, revision_kref: str, tag: str) -> None:
        """Remove a tag from a specific revision."""
        from kumiho.mcp_server import tool_untag_revision
        def _call():
            tool_untag_revision(revision_kref, tag)
        await asyncio.to_thread(_call)

    async def set_deprecated(self, kref: str, deprecated: bool) -> None:
        def _call():
            tool_deprecate_item(kref, deprecated)
        await asyncio.to_thread(_call)


# -- SDK singleton ----------------------------------------------------------

_SDK: KumihoSDKClient | None = None

def _get_sdk() -> KumihoSDKClient | None:
    """Lazy-init the SDK client singleton."""
    global _SDK
    if _SDK is None and _HAS_KUMIHO:
        _SDK = KumihoSDKClient()
    if _SDK is not None:
        _SDK._lazy_init()
    return _SDK if (_SDK and _SDK._available) else None


# ---------------------------------------------------------------------------
# KumihoAgentPoolClient — manages Construct/AgentPool
# ---------------------------------------------------------------------------

class KumihoAgentPoolClient:
    """Manages <harness_project>/AgentPool — prefers SDK, falls back to HTTP."""

    @property
    def SPACE_PATH(self) -> str:
        return f"/{harness_project()}/AgentPool"

    def __init__(self) -> None:
        config = kumiho_connection_config()
        self.api_url = (os.environ.get("KUMIHO_API_URL") or config.get("api_url", "")).rstrip("/")
        self.auth_token = os.environ.get("KUMIHO_AUTH_TOKEN") or config.get("auth_token", "")
        self._http_available = bool(self.api_url and self.auth_token and _HAS_HTTPX)
        self._available: bool | None = None  # deferred until first use

    def _ensure_available(self) -> bool:
        if self._available is not None:
            return self._available
        sdk = _get_sdk()
        if sdk:
            self._available = True
            _log("Kumiho Agent Pool: using shared client")
        elif self._http_available:
            self._available = True
            _log(f"Kumiho Agent Pool: using HTTP fallback ({self.api_url})")
        else:
            self._available = False
            _log("Kumiho Agent Pool: disabled (no SDK, no HTTP)")
        return self._available

    def _headers(self) -> dict[str, str]:
        return {"X-Kumiho-Token": self.auth_token, "Accept": "application/json"}

    # -- Generic helpers (delegate to SDK or HTTP) -----------------------------

    async def ensure_space(self, project: str, space: str) -> None:
        sdk = _get_sdk()
        if sdk:
            return await sdk.ensure_space(project, space)
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{self.api_url}/api/v1/projects", json={"name": project}, headers=self._headers())
            await client.post(f"{self.api_url}/api/v1/spaces", json={"parent_path": f"/{project}", "name": space}, headers=self._headers())

    async def list_items(self, space_path: str) -> list[dict[str, Any]]:
        sdk = _get_sdk()
        if sdk:
            return await sdk.list_items(space_path)
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{self.api_url}/api/v1/items", params={"space_path": space_path}, headers=self._headers())
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            items = resp.json()
            return items if isinstance(items, list) else []

    async def create_item(self, space_path: str, name: str, kind: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        sdk = _get_sdk()
        if sdk:
            return await sdk.create_item(space_path, name, kind, metadata)
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{self.api_url}/api/v1/items", json={"space_path": space_path, "item_name": name, "kind": kind, "metadata": metadata or {}}, headers=self._headers())
            resp.raise_for_status()
            return resp.json()

    async def create_revision(self, item_kref: str, metadata: dict[str, Any], tag: str | None = "published") -> dict[str, Any]:
        sdk = _get_sdk()
        if sdk:
            return await sdk.create_revision(item_kref, metadata, tag)
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{self.api_url}/api/v1/revisions", json={"item_kref": item_kref, "metadata": metadata}, headers=self._headers())
            resp.raise_for_status()
            rev = resp.json()
            if tag and rev.get("kref"):
                await client.post(f"{self.api_url}/api/v1/revisions/tags", params={"kref": rev["kref"]}, json={"tag": tag}, headers=self._headers())
            return rev

    async def get_latest_revision(self, item_kref: str, tag: str = "published") -> dict[str, Any] | None:
        sdk = _get_sdk()
        if sdk:
            return await sdk.get_latest_revision(item_kref, tag)
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{self.api_url}/api/v1/revisions/by-kref", params={"kref": item_kref, "t": tag}, headers=self._headers())
            if resp.status_code == 200:
                return resp.json()
            resp = await client.get(f"{self.api_url}/api/v1/revisions/latest", params={"item_kref": item_kref}, headers=self._headers())
            return resp.json() if resp.status_code == 200 else None

    async def batch_get_revisions(self, item_krefs: list[str], tag: str = "published") -> dict[str, dict[str, Any]]:
        sdk = _get_sdk()
        if sdk:
            return await sdk.batch_get_revisions(item_krefs, tag)
        revisions: dict[str, dict[str, Any]] = {}
        for item_kref in item_krefs:
            revision = await self.get_latest_revision(item_kref, tag)
            if revision:
                revisions[item_kref] = revision
        return revisions

    async def create_artifact(
        self,
        revision_kref: str,
        name: str,
        location: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        sdk = _get_sdk()
        if sdk:
            return await sdk.create_artifact(revision_kref, name, location, metadata)
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{self.api_url}/api/v1/artifacts",
                json={
                    "revision_kref": revision_kref,
                    "name": name,
                    "location": location,
                    "metadata": metadata or {},
                },
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def get_artifacts(self, revision_kref: str) -> list[dict[str, Any]]:
        sdk = _get_sdk()
        if sdk:
            return await sdk.get_artifacts(revision_kref)
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{self.api_url}/api/v1/artifacts",
                params={"revision_kref": revision_kref},
                headers=self._headers(),
            )
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            result = resp.json()
            return result if isinstance(result, list) else result.get("artifacts", [])

    async def tag_revision(self, revision_kref: str, tag: str) -> None:
        sdk = _get_sdk()
        if sdk:
            await sdk.tag_revision(revision_kref, tag)
            return
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{self.api_url}/api/v1/revisions/tags",
                params={"kref": revision_kref},
                json={"tag": tag},
                headers=self._headers(),
            )
            resp.raise_for_status()

    # -- Agent-specific methods ------------------------------------------------

    async def list_agents(self) -> list[dict[str, Any]]:
        """List all agent items enriched with published revision metadata."""
        if not self._ensure_available():
            return []
        sdk = _get_sdk()
        if sdk:
            try:
                items = await sdk.list_items(self.SPACE_PATH)
                if not items:
                    return []
                krefs = [i["kref"] for i in items if i.get("kref")]
                rev_map = await sdk.batch_get_revisions(krefs, "published")
                missing = [k for k in krefs if k not in rev_map]
                if missing:
                    latest_map = await sdk.batch_get_revisions(missing, "latest")
                    rev_map.update(latest_map)
                for item in items:
                    rev = rev_map.get(item.get("kref", ""))
                    if rev:
                        item["metadata"] = {**item.get("metadata", {}), **rev.get("metadata", {})}
                return items
            except Exception as e:
                _log(f"Kumiho Agent Pool list failed: {e}")
                return []
        # HTTP fallback
        try:
            items = await self.list_items(self.SPACE_PATH)
            if not items:
                return []
            async with httpx.AsyncClient(timeout=15) as client:
                async def _enrich(item: dict[str, Any]) -> dict[str, Any]:
                    kref = item.get("kref", "")
                    if not kref:
                        return item
                    try:
                        rev_resp = await client.get(f"{self.api_url}/api/v1/revisions/by-kref", params={"kref": kref, "t": "published"}, headers=self._headers())
                        if rev_resp.status_code == 200:
                            item["metadata"] = {**item.get("metadata", {}), **rev_resp.json().get("metadata", {})}
                        else:
                            rev_resp = await client.get(f"{self.api_url}/api/v1/revisions/latest", params={"item_kref": kref}, headers=self._headers())
                            if rev_resp.status_code == 200:
                                item["metadata"] = {**item.get("metadata", {}), **rev_resp.json().get("metadata", {})}
                    except Exception as e:
                        _log(f"Failed to fetch revision for {kref}: {e}")
                    return item
                return list(await asyncio.gather(*[_enrich(item) for item in items]))
        except Exception as e:
            _log(f"Kumiho Agent Pool list (HTTP) failed: {e}")
            return []

    async def search_agents(self, query: str) -> list[dict[str, Any]]:
        if not self._ensure_available():
            return []
        sdk = _get_sdk()
        if sdk:
            try:
                results = await sdk.search(query, context=harness_project(), include_revision_metadata=True)
                return [r["item"] for r in results]
            except Exception as e:
                _log(f"Kumiho Agent Pool search failed: {e}")
                return []
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.api_url}/api/v1/items/fulltext-search", params={"query": query, "context": harness_project(), "include_revision_metadata": True}, headers=self._headers())
                if resp.status_code == 404:
                    return []
                resp.raise_for_status()
                results = resp.json()
                return results if isinstance(results, list) else []
        except Exception as e:
            _log(f"Kumiho Agent Pool search (HTTP) failed: {e}")
            return []

    async def save_agent(
        self, name: str, agent_type: str, role: str, capabilities: list[str],
        description: str, identity: str | None = None, soul: str | None = None,
        tone: str | None = None, system_hint: str | None = None,
        model: str | None = None,
    ) -> bool:
        """Create or update an agent. Returns True on success."""
        if not self._ensure_available():
            return False
        try:
            await self.ensure_space(harness_project(), "AgentPool")
            metadata: dict[str, str] = {
                "agent_type": agent_type, "role": role,
                "expertise": ",".join(capabilities), "description": description,
                "identity": identity or f"{name} — {description}",
                "soul": soul or f"A {role} agent specialized in {', '.join(capabilities)}.",
            }
            if tone:
                metadata["tone"] = tone
            if system_hint:
                metadata["system_hint"] = system_hint
            if model:
                metadata["model"] = model

            existing_items = await self.list_agents()
            existing = next((i for i in existing_items if i.get("item_name", i.get("name")) == name), None)

            if existing:
                kref = existing.get("kref", "")
                if kref:
                    await self.create_revision(kref, metadata)
                    _log(f"Updated agent '{name}' in Kumiho (new revision, tagged published)")
            else:
                item = await self.create_item(self.SPACE_PATH, name, "agent", metadata)
                await self.create_revision(item["kref"], metadata)
                _log(f"Created agent '{name}' in Kumiho (tagged published)")
            return True
        except Exception as e:
            _log(f"Kumiho Agent Pool save failed: {e}")
            return False

    @staticmethod
    def item_to_template_dict(item: dict[str, Any]) -> dict[str, Any]:
        """Convert a Kumiho item to a template-like dict for the operator."""
        meta = item.get("metadata", {})
        expertise = meta.get("expertise", meta.get("capabilities", []))
        if isinstance(expertise, str):
            expertise = [e.strip() for e in expertise.split(",") if e.strip()]
        description = meta.get("identity", meta.get("description", ""))
        result = {
            "kref": item.get("kref", ""),
            "name": item.get("item_name", item.get("name", "unknown")),
            "agent_type": normalize_agent_type(meta.get("agent_type", "codex")),
            "role": meta.get("role", "coder"),
            "capabilities": expertise,
            "description": description,
            "soul": meta.get("soul", ""),
            "tone": meta.get("tone", ""),
            "system_hint": meta.get("system_hint", ""),
            "source": "kumiho",
            "use_count": 0,
        }
        if meta.get("model"):
            result["model"] = meta["model"]
        return result


# ---------------------------------------------------------------------------
# Team-edge normalization helpers (shared by SDK + HTTP paths)
# ---------------------------------------------------------------------------

def _normalize_to_item_kref(
    endpoint: str, rev_to_item: dict[str, str]
) -> str:
    """Turn a revision kref into its item kref.

    Checks *rev_to_item* first (exact match on the normalized key),
    then falls back to stripping the ``?r=…`` query suffix.
    Returns the raw string unchanged if neither strategy applies.
    """
    if not endpoint:
        return ""
    # exact lookup (key is already query-stripped)
    stripped = endpoint.split("?")[0] if "?" in endpoint else endpoint
    if stripped in rev_to_item:
        return rev_to_item[stripped]
    # if the full endpoint was in the map
    if endpoint in rev_to_item:
        return rev_to_item[endpoint]
    # fallback: strip query → treat as item kref
    return stripped


def _normalize_team_edge(
    raw_edge: dict[str, Any],
    member_item_krefs: set[str],
    rev_to_item: dict[str, str],
    expected_source_item: str | None = None,
) -> dict[str, str] | None:
    """Normalize a raw Kumiho edge into a team edge dict, or *None*.

    Rejects edges whose source or target is not a current team member,
    and optionally enforces that the source matches *expected_source_item*.
    """
    src_raw = raw_edge.get("source_kref", "")
    tgt_raw = raw_edge.get("target_kref", "")
    if not src_raw or not tgt_raw:
        return None

    src_item = _normalize_to_item_kref(src_raw, rev_to_item)
    tgt_item = _normalize_to_item_kref(tgt_raw, rev_to_item)

    if src_item not in member_item_krefs or tgt_item not in member_item_krefs:
        return None
    if src_item == tgt_item:
        return None  # reject self-edges
    if expected_source_item and src_item != expected_source_item:
        return None  # source doesn't match current iteration member

    return {
        "from_kref": src_item,
        "to_kref": tgt_item,
        "edge_type": raw_edge.get("edge_type", ""),
    }


def _dedupe_and_sort_edges(edges: list[dict[str, str]]) -> list[dict[str, str]]:
    """Deduplicate and deterministically sort team edges."""
    seen: set[tuple[str, str, str]] = set()
    unique: list[dict[str, str]] = []
    for e in edges:
        key = (e["from_kref"], e["to_kref"], e["edge_type"])
        if key not in seen:
            seen.add(key)
            unique.append(e)
    unique.sort(key=lambda e: (e["from_kref"], e["to_kref"], e["edge_type"]))
    return unique


# ---------------------------------------------------------------------------
# KumihoTeamClient — manages Construct/Teams bundles
# ---------------------------------------------------------------------------

class KumihoTeamClient:
    """Manages <harness_project>/Teams bundles — prefers SDK, falls back to HTTP."""

    @property
    def SPACE_PATH(self) -> str:
        return f"/{harness_project()}/Teams"

    def __init__(self) -> None:
        config = kumiho_connection_config()
        self.api_url = (os.environ.get("KUMIHO_API_URL") or config.get("api_url", "")).rstrip("/")
        self.auth_token = os.environ.get("KUMIHO_AUTH_TOKEN") or config.get("auth_token", "")
        self._http_available = bool(self.api_url and self.auth_token and _HAS_HTTPX)
        self._available: bool | None = None  # deferred until first use

    def _ensure_available(self) -> bool:
        if self._available is not None:
            return self._available
        sdk = _get_sdk()
        if sdk:
            self._available = True
            _log("Kumiho Team Client: using shared client")
        elif self._http_available:
            self._available = True
            _log("Kumiho Team Client: using HTTP fallback")
        else:
            self._available = False
        return self._available

    def _headers(self) -> dict[str, str]:
        return {"X-Kumiho-Token": self.auth_token, "Accept": "application/json"}

    async def list_teams(self) -> list[dict[str, Any]]:
        """List all team bundles from Construct/Teams."""
        if not self._ensure_available():
            return []
        sdk = _get_sdk()
        if sdk:
            try:
                items = await sdk.list_items(self.SPACE_PATH)
                return [i for i in items if not i.get("deprecated")]
            except Exception as e:
                _log(f"Kumiho Team list failed: {e}")
                return []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{self.api_url}/api/v1/items", params={"space_path": self.SPACE_PATH}, headers=self._headers())
                if resp.status_code == 404:
                    return []
                resp.raise_for_status()
                items = resp.json()
                if not isinstance(items, list):
                    return []
                return [item for item in items if not item.get("deprecated")]
        except Exception as e:
            _log(f"Kumiho Team list (HTTP) failed: {e}")
            return []

    async def resolve_team_kref(self, raw: str) -> str | None:
        """Resolve a team name/kref to an actual bundle kref."""
        teams = await self.list_teams()
        if not teams:
            return None

        kref_map: dict[str, str] = {}
        name_map: dict[str, str] = {}
        for team in teams:
            actual_kref = team.get("kref", "")
            team_name = team.get("item_name", team.get("name", ""))
            if actual_kref:
                kref_map[actual_kref.lower()] = actual_kref
                if team_name:
                    name_map[team_name.lower()] = actual_kref
                    bare = team_name.rsplit(".", 1)[0] if "." in team_name else team_name
                    name_map[bare.lower()] = actual_kref

        if raw.lower() in kref_map:
            return kref_map[raw.lower()]

        name_part = raw
        if "://" in raw:
            name_part = raw.rsplit("/", 1)[-1]
        if "." in name_part:
            name_part = name_part.rsplit(".", 1)[0]

        matched = name_map.get(name_part.lower())
        if matched:
            _log(f"resolve_team_kref: '{raw}' -> '{matched}'")
            return matched

        _log(f"resolve_team_kref: '{raw}' could not be resolved")
        return None

    async def get_team(self, kref: str) -> dict[str, Any] | None:
        """Get a team bundle with its members and edges."""
        if not self._ensure_available():
            return None
        sdk = _get_sdk()

        result = None
        if sdk:
            result = await self._get_team_sdk(sdk, kref)
        else:
            result = await self._get_team_http(kref)

        if result is not None:
            return result

        resolved = await self.resolve_team_kref(kref)
        if resolved and resolved != kref:
            _log(f"get_team: retrying with resolved kref '{resolved}'")
            if sdk:
                return await self._get_team_sdk(sdk, resolved)
            return await self._get_team_http(resolved)

        return None

    async def _get_team_sdk(self, sdk: KumihoSDKClient, kref: str) -> dict[str, Any] | None:
        try:
            bundle = await sdk.get_bundle_by_kref(kref)
            members_raw = await sdk.get_bundle_members(kref)

            item_krefs = [m["item_kref"] for m in members_raw if m.get("item_kref")]
            rev_map = await sdk.batch_get_revisions(item_krefs, "published") if item_krefs else {}
            missing = [k for k in item_krefs if k not in rev_map]
            if missing:
                latest_map = await sdk.batch_get_revisions(missing, "latest")
                rev_map.update(latest_map)

            members = []
            for m in members_raw:
                ik = m.get("item_kref", "")
                rev = rev_map.get(ik)
                meta = rev.get("metadata", {}) if rev else {}
                expertise = meta.get("expertise", "")
                if isinstance(expertise, str):
                    expertise = [e.strip() for e in expertise.split(",") if e.strip()]
                name_part = ik.rsplit("/", 1)[-1] if "/" in ik else ik
                if "." in name_part:
                    name_part = name_part.rsplit(".", 1)[0]
                member_dict: dict[str, Any] = {
                    "kref": ik, "name": name_part,
                    "role": meta.get("role", ""), "agent_type": meta.get("agent_type", "codex"),
                    "expertise": expertise, "identity": meta.get("identity", ""),
                    "rev_kref": rev.get("kref", "") if rev else "",
                }
                if meta.get("model"):
                    member_dict["model"] = meta["model"]
                members.append(member_dict)

            member_item_krefs = {m["kref"] for m in members if m["kref"]}

            # Build revision→item mapping for edge normalization
            rev_to_item: dict[str, str] = {}
            for m in members:
                ik = m["kref"]
                rk = m.get("rev_kref", "")
                if rk and ik:
                    # Store with query stripped so lookups match
                    rev_to_item[rk.split("?")[0] if "?" in rk else rk] = ik

            edges: list[dict[str, str]] = []
            for m in members:
                rev_kref = m.get("rev_kref", "")
                if not rev_kref:
                    continue
                try:
                    raw_edges = await sdk.get_edges(rev_kref, direction=1)  # outgoing only
                    for raw_edge in raw_edges:
                        normalized = _normalize_team_edge(
                            raw_edge, member_item_krefs, rev_to_item,
                            expected_source_item=m["kref"],
                        )
                        if normalized:
                            edges.append(normalized)
                except Exception:
                    pass

            edges = _dedupe_and_sort_edges(edges)

            for m in members:
                m.pop("rev_kref", None)
            bundle["members"] = members
            bundle["edges"] = edges
            return bundle
        except Exception as e:
            _log(f"Kumiho Team get failed: {e}")
            return None

    async def _get_team_http(self, kref: str) -> dict[str, Any] | None:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(f"{self.api_url}/api/v1/bundles/by-kref", params={"kref": kref}, headers=self._headers())
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                bundle = resp.json()
                mem_resp = await client.get(f"{self.api_url}/api/v1/bundles/members", params={"bundle_kref": kref}, headers=self._headers())
                members_raw = []
                if mem_resp.status_code == 200:
                    mem_data = mem_resp.json()
                    members_raw = mem_data.get("members", []) if isinstance(mem_data, dict) else mem_data

                async def _enrich_member(m: dict[str, Any]) -> dict[str, Any]:
                    item_kref = m.get("item_kref", "")
                    info: dict[str, Any] = {"kref": item_kref, "name": "unknown", "role": "", "agent_type": "codex", "expertise": []}
                    if not item_kref:
                        return info
                    try:
                        rev_resp = await client.get(f"{self.api_url}/api/v1/revisions/by-kref", params={"kref": item_kref, "t": "published"}, headers=self._headers())
                        if rev_resp.status_code != 200:
                            rev_resp = await client.get(f"{self.api_url}/api/v1/revisions/latest", params={"item_kref": item_kref}, headers=self._headers())
                        if rev_resp.status_code == 200:
                            rev_data = rev_resp.json()
                            meta = rev_data.get("metadata", {})
                            expertise = meta.get("expertise", "")
                            if isinstance(expertise, str):
                                expertise = [e.strip() for e in expertise.split(",") if e.strip()]
                            name_part = item_kref.rsplit("/", 1)[-1] if "/" in item_kref else item_kref
                            if "." in name_part:
                                name_part = name_part.rsplit(".", 1)[0]
                            info.update({"name": name_part, "role": meta.get("role", ""), "agent_type": meta.get("agent_type", "codex"), "expertise": expertise, "identity": meta.get("identity", ""), "rev_kref": rev_data.get("kref", "")})
                    except Exception as e:
                        _log(f"Failed to enrich member {item_kref}: {e}")
                    return info

                members = list(await asyncio.gather(*[_enrich_member(m) for m in members_raw]))
                member_item_krefs = {m["kref"] for m in members if m["kref"]}

                # Build revision→item mapping for edge normalization
                rev_to_item: dict[str, str] = {}
                for m in members:
                    ik = m["kref"]
                    rk = m.get("rev_kref", "")
                    if rk and ik:
                        rev_to_item[rk.split("?")[0] if "?" in rk else rk] = ik

                async def _get_edges_for(m: dict[str, Any]) -> list[dict[str, str]]:
                    rev_kref = m.get("rev_kref", "")
                    if not rev_kref:
                        return []
                    try:
                        edge_resp = await client.get(f"{self.api_url}/api/v1/edges", params={"kref": rev_kref, "direction": "outgoing"}, headers=self._headers())
                        if edge_resp.status_code != 200:
                            return []
                        result: list[dict[str, str]] = []
                        for raw_edge in edge_resp.json():
                            normalized = _normalize_team_edge(
                                raw_edge, member_item_krefs, rev_to_item,
                                expected_source_item=m["kref"],
                            )
                            if normalized:
                                result.append(normalized)
                        return result
                    except Exception:
                        return []

                edge_lists = await asyncio.gather(*[_get_edges_for(m) for m in members])
                edges = _dedupe_and_sort_edges(
                    [e for sublist in edge_lists for e in sublist]
                )
                for m in members:
                    m.pop("rev_kref", None)
                bundle["members"] = members
                bundle["edges"] = edges
                return bundle
        except Exception as e:
            _log(f"Kumiho Team get (HTTP) failed: {e}")
            return None

    async def create_team(
        self, name: str, description: str, member_krefs: list[str],
        edges: list[dict[str, str]] | None = None,
    ) -> dict[str, Any] | None:
        """Create a new team bundle with members and optional edges."""
        if not self._ensure_available():
            return None
        sdk = _get_sdk()
        if sdk:
            return await self._create_team_sdk(sdk, name, description, member_krefs, edges)
        return await self._create_team_http(name, description, member_krefs, edges)

    async def _create_team_sdk(
        self, sdk: KumihoSDKClient, name: str, description: str,
        member_krefs: list[str], edges: list[dict[str, str]] | None = None,
    ) -> dict[str, Any] | None:
        try:
            await sdk.ensure_space(harness_project(), "Teams")

            bundle_kref = ""
            try:
                bundle = await sdk.create_bundle(self.SPACE_PATH, name, {"description": description})
                bundle_kref = bundle.get("kref", "")
            except RuntimeError as e:
                if "already exists" in str(e).lower():
                    existing = await self.list_teams()
                    found = next((t for t in existing if t.get("item_name", t.get("name", "")) == name), None)
                    if not found:
                        return None
                    bundle_kref = found.get("kref", "")
                    old_members = await sdk.get_bundle_members(bundle_kref)
                    old_item_krefs = [old_m.get("item_kref", "") for old_m in old_members if old_m.get("item_kref")]

                    # Clean up stale inter-member edges before removing members
                    if old_item_krefs:
                        old_rev_map = await sdk.batch_get_revisions(old_item_krefs, "published") if old_item_krefs else {}
                        old_missing = [k for k in old_item_krefs if k not in old_rev_map]
                        if old_missing:
                            old_latest = await sdk.batch_get_revisions(old_missing, "latest")
                            old_rev_map.update(old_latest)
                        old_item_set = set(old_item_krefs)
                        old_rev_to_item: dict[str, str] = {}
                        for oik in old_item_krefs:
                            orev = old_rev_map.get(oik, {}).get("kref", "")
                            if orev:
                                old_rev_to_item[orev.split("?")[0] if "?" in orev else orev] = oik
                        for oik in old_item_krefs:
                            orev = old_rev_map.get(oik, {}).get("kref", "")
                            if not orev:
                                continue
                            try:
                                old_edges = await sdk.get_edges(orev, direction=1)
                                for oe in old_edges:
                                    tgt_raw = oe.get("target_kref", "")
                                    src_raw = oe.get("source_kref", "")
                                    tgt_item = _normalize_to_item_kref(tgt_raw, old_rev_to_item)
                                    if tgt_item in old_item_set:
                                        try:
                                            await sdk.delete_edge(src_raw, tgt_raw, oe.get("edge_type", ""))
                                        except Exception:
                                            pass
                            except Exception:
                                pass

                    for old_m in old_members:
                        ik = old_m.get("item_kref", "")
                        if ik:
                            await sdk.remove_bundle_member(bundle_kref, ik)
                    _log(f"Updating existing team '{name}'")
                else:
                    raise

            # Add members sequentially — concurrent adds race on bundle revision numbers.
            added_count = 0
            failed_members = []
            for mk in member_krefs:
                ok = await sdk.add_bundle_member(bundle_kref, mk)
                if ok:
                    added_count += 1
                else:
                    failed_members.append(mk)
            if failed_members:
                _log(f"Team '{name}': failed to add members: {failed_members}")

            if edges:
                # Defense-in-depth: strip self-edges and dangling refs before persisting
                member_set = set(member_krefs)
                clean_edges = []
                for e in edges:
                    fk, tk = e.get("from_kref", ""), e.get("to_kref", "")
                    if fk == tk:
                        _log(f"Team '{name}': dropping self-edge {fk}")
                        continue
                    if fk not in member_set or tk not in member_set:
                        _log(f"Team '{name}': dropping dangling edge {fk} -> {tk}")
                        continue
                    clean_edges.append(e)
                edges = clean_edges

                # Cycle check — reject entire edge set if cycles detected
                from operator_mcp.team_validation import validate_team_edges as _validate
                member_stubs = [{"kref": k, "name": k} for k in member_krefs]
                vr = _validate(member_stubs, [{"from_kref": e.get("from_kref", ""), "to_kref": e.get("to_kref", ""), "edge_type": e.get("edge_type", "SUPPORTS")} for e in edges])
                if not vr.valid:
                    _log(f"Team '{name}': edge validation failed at persistence layer — {[err.message for err in vr.errors]}")
                    edges = []  # Drop all edges rather than persist a broken graph

                if edges:
                    all_item_krefs = list({e.get("from_kref", "") for e in edges} | {e.get("to_kref", "") for e in edges} - {""})
                    rev_map = await sdk.batch_get_revisions(all_item_krefs, "published") if all_item_krefs else {}
                    missing = [k for k in all_item_krefs if k not in rev_map]
                    if missing:
                        latest_map = await sdk.batch_get_revisions(missing, "latest")
                        rev_map.update(latest_map)

                    async def _create_edge(edge: dict[str, str]) -> None:
                        src_rev = rev_map.get(edge.get("from_kref", ""), {}).get("kref", "")
                        tgt_rev = rev_map.get(edge.get("to_kref", ""), {}).get("kref", "")
                        if not src_rev or not tgt_rev:
                            return
                        try:
                            await sdk.create_edge(src_rev, tgt_rev, edge.get("edge_type", "SUPPORTS"))
                        except Exception as e:
                            _log(f"Edge creation failed: {e}")

                    await asyncio.gather(*[_create_edge(e) for e in edges])

            result_dict: dict[str, Any] = {"kref": bundle_kref, "name": name, "members_added": added_count}
            if failed_members:
                result_dict["failed_members"] = failed_members
                result_dict["error_hint"] = (
                    f"{len(failed_members)} member(s) could not be added. "
                    "Ensure agent krefs exist in the pool (use search_agent_pool to verify)."
                )
            return result_dict
        except Exception as e:
            _log(f"Kumiho Team create failed: {e}")
            return None

    async def _create_team_http(
        self, name: str, description: str, member_krefs: list[str],
        edges: list[dict[str, str]] | None = None,
    ) -> dict[str, Any] | None:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                _project = harness_project()
                await client.post(f"{self.api_url}/api/v1/projects", json={"name": _project}, headers=self._headers())
                await client.post(f"{self.api_url}/api/v1/spaces", json={"parent_path": f"/{_project}", "name": "Teams"}, headers=self._headers())

                resp = await client.post(f"{self.api_url}/api/v1/bundles", json={"space_path": self.SPACE_PATH, "bundle_name": name, "metadata": {"description": description}}, headers=self._headers())
                is_update = False
                if resp.status_code == 409:
                    is_update = True
                    existing_teams = await self.list_teams()
                    existing = next((t for t in existing_teams if t.get("item_name", t.get("name", "")) == name), None)
                    if not existing:
                        return None
                    bundle_kref = existing.get("kref", "")
                    try:
                        old_resp = await client.get(f"{self.api_url}/api/v1/bundles/members", params={"bundle_kref": bundle_kref}, headers=self._headers())
                        if old_resp.status_code == 200:
                            old_data = old_resp.json()
                            old_members = old_data.get("members", old_data) if isinstance(old_data, dict) else old_data
                            old_item_krefs = [om.get("item_kref", "") for om in old_members if om.get("item_kref")]

                            # Clean up stale inter-member edges before removing members
                            old_item_set = set(old_item_krefs)
                            for oik in old_item_krefs:
                                try:
                                    rev_resp = await client.get(f"{self.api_url}/api/v1/revisions/by-kref", params={"kref": oik, "t": "published"}, headers=self._headers())
                                    if rev_resp.status_code != 200:
                                        rev_resp = await client.get(f"{self.api_url}/api/v1/revisions/latest", params={"item_kref": oik}, headers=self._headers())
                                    if rev_resp.status_code != 200:
                                        continue
                                    orev_kref = rev_resp.json().get("kref", "")
                                    if not orev_kref:
                                        continue
                                    edge_resp = await client.get(f"{self.api_url}/api/v1/edges", params={"kref": orev_kref, "direction": "outgoing"}, headers=self._headers())
                                    if edge_resp.status_code == 200:
                                        for oe in edge_resp.json():
                                            tgt_raw = oe.get("target_kref", "")
                                            tgt_item = tgt_raw.split("?")[0] if "?" in tgt_raw else tgt_raw
                                            if tgt_item in old_item_set:
                                                src_raw = oe.get("source_kref", "")
                                                try:
                                                    await client.post(f"{self.api_url}/api/v1/edges/delete", json={"source_kref": src_raw, "target_kref": tgt_raw, "edge_type": oe.get("edge_type", "")}, headers=self._headers())
                                                except Exception:
                                                    pass
                                except Exception:
                                    pass

                            for old_m in old_members:
                                ik = old_m.get("item_kref", "")
                                if ik:
                                    await client.post(f"{self.api_url}/api/v1/bundles/members/remove", json={"bundle_kref": bundle_kref, "item_kref": ik}, headers=self._headers())
                    except Exception as e:
                        _log(f"Failed to clean old members: {e}")
                    _log(f"Updating existing team '{name}'")
                else:
                    resp.raise_for_status()
                    bundle = resp.json()
                    bundle_kref = bundle.get("kref", "")

                async def _add_member(mk: str) -> bool:
                    try:
                        r = await client.post(
                            f"{self.api_url}/api/v1/bundles/members/add",
                            json={"bundle_kref": bundle_kref, "item_kref": mk},
                            headers=self._headers(),
                        )
                        r.raise_for_status()
                        return True
                    except Exception as e:
                        _log(f"Failed to add member {mk} to team: {e}")
                        return False

                member_results = await asyncio.gather(*[_add_member(mk) for mk in member_krefs])
                added_count = sum(1 for ok in member_results if ok)

                _log(f"Team edges to create: {len(edges) if edges else 0}")
                if edges:
                    # Defense-in-depth: strip self-edges and dangling refs before persisting
                    member_set = set(member_krefs)
                    clean_edges = []
                    for e in edges:
                        fk, tk = e.get("from_kref", ""), e.get("to_kref", "")
                        if fk == tk:
                            _log(f"Team '{name}': dropping self-edge {fk}")
                            continue
                        if fk not in member_set or tk not in member_set:
                            _log(f"Team '{name}': dropping dangling edge {fk} -> {tk}")
                            continue
                        clean_edges.append(e)
                    edges = clean_edges

                    # Cycle check — reject entire edge set if cycles detected
                    from operator_mcp.team_validation import validate_team_edges as _validate
                    member_stubs = [{"kref": k, "name": k} for k in member_krefs]
                    vr = _validate(member_stubs, [{"from_kref": e.get("from_kref", ""), "to_kref": e.get("to_kref", ""), "edge_type": e.get("edge_type", "SUPPORTS")} for e in edges])
                    if not vr.valid:
                        _log(f"Team '{name}': edge validation failed at persistence layer — {[err.message for err in vr.errors]}")
                        edges = []  # Drop all edges rather than persist a broken graph

                if edges:
                    _rev_cache: dict[str, str] = {}

                    async def _resolve_rev(item_kref: str) -> str:
                        if item_kref in _rev_cache:
                            return _rev_cache[item_kref]
                        resp = await client.get(
                            f"{self.api_url}/api/v1/revisions/by-kref",
                            params={"kref": item_kref, "t": "published"},
                            headers=self._headers(),
                        )
                        if resp.status_code == 200:
                            rev_kref = resp.json().get("kref", "")
                            _rev_cache[item_kref] = rev_kref
                            return rev_kref
                        fallback = await client.get(
                            f"{self.api_url}/api/v1/revisions/latest",
                            params={"item_kref": item_kref},
                            headers=self._headers(),
                        )
                        if fallback.status_code == 200:
                            rev_kref = fallback.json().get("kref", "")
                            _rev_cache[item_kref] = rev_kref
                            return rev_kref
                        _rev_cache[item_kref] = ""
                        return ""

                    all_krefs = set()
                    for edge in edges:
                        for key in ("from_kref", "to_kref"):
                            v = edge.get(key, "")
                            if v:
                                all_krefs.add(v)
                    await asyncio.gather(*[_resolve_rev(k) for k in all_krefs])

                    async def _create_edge(edge: dict[str, str]) -> None:
                        from_kref = edge.get("from_kref", "")
                        to_kref = edge.get("to_kref", "")
                        if not from_kref or not to_kref:
                            return
                        src = _rev_cache.get(from_kref, "")
                        tgt = _rev_cache.get(to_kref, "")
                        if not src or not tgt:
                            _log(f"Edge skipped — no revision for {from_kref} or {to_kref}")
                            return
                        try:
                            r = await client.post(
                                f"{self.api_url}/api/v1/edges",
                                json={
                                    "source_revision_kref": src,
                                    "target_revision_kref": tgt,
                                    "edge_type": edge.get("edge_type", "SUPPORTS"),
                                    "metadata": {},
                                },
                                headers=self._headers(),
                            )
                        except Exception as e:
                            _log(f"Edge creation failed: {e}")

                    await asyncio.gather(*[_create_edge(e) for e in edges])

                action = "Updated" if is_update else "Created"
                _log(f"{action} team '{name}' with {added_count}/{len(member_krefs)} members")
                return {"kref": bundle_kref, "name": name, "description": description, "member_count": added_count, "requested_members": len(member_krefs), "is_update": is_update}
        except Exception as e:
            _log(f"Kumiho Team create failed: {e}")
            return None

    async def search_teams(self, query: str) -> list[dict[str, Any]]:
        """Search for teams by name/description."""
        teams = await self.list_teams()
        query_lower = query.lower()
        matched = []
        for team in teams:
            name = team.get("item_name", team.get("name", "")).lower()
            desc = team.get("metadata", {}).get("description", "").lower()
            if query_lower in name or query_lower in desc:
                matched.append(team)
        return matched


# ---------------------------------------------------------------------------
# resolve_agent_krefs — LLM name resolution
# ---------------------------------------------------------------------------

async def resolve_agent_krefs(raw_krefs: list[str], pool_client: KumihoAgentPoolClient) -> list[str]:
    """Resolve LLM-provided agent names/krefs to actual Kumiho item krefs."""
    agents = await pool_client.list_agents()
    if not agents:
        _log("resolve_agent_krefs: no agents found in pool, returning raw krefs")
        return raw_krefs

    name_to_kref: dict[str, str] = {}
    kref_to_kref: dict[str, str] = {}
    for agent in agents:
        actual_kref = agent.get("kref", "")
        agent_name = agent.get("item_name", agent.get("name", ""))
        if actual_kref:
            kref_to_kref[actual_kref.lower()] = actual_kref
            if agent_name:
                name_to_kref[agent_name.lower()] = actual_kref
                bare_name = agent_name.rsplit(".", 1)[0] if "." in agent_name else agent_name
                name_to_kref[bare_name.lower()] = actual_kref

    resolved = []
    for raw in raw_krefs:
        if raw.lower() in kref_to_kref:
            resolved.append(kref_to_kref[raw.lower()])
            continue

        name_part = raw
        if "://" in raw:
            name_part = raw.rsplit("/", 1)[-1]
        if "." in name_part:
            name_part = name_part.rsplit(".", 1)[0]

        matched = name_to_kref.get(name_part.lower())
        if matched:
            _log(f"resolve_agent_krefs: '{raw}' -> '{matched}'")
            resolved.append(matched)
        else:
            _log(
                f"resolve_agent_krefs: '{raw}' NOT FOUND in agent pool "
                f"(tried name_part='{name_part.lower()}'). "
                f"Available agents: {list(name_to_kref.keys())[:10]}"
            )
            resolved.append(raw)

    return resolved
