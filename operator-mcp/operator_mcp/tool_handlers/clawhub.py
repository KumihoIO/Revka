"""ClawHub marketplace tool handlers: search, install, browse."""
from __future__ import annotations

from typing import Any

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore

from .._log import _log
from ..gateway_client import RevkaGatewayClient


async def tool_search_clawhub(args: dict[str, Any], gw: RevkaGatewayClient) -> dict[str, Any]:
    if not gw._available:
        return {"error": "Revka gateway not available.", "items": []}

    query = args["query"]
    limit = args.get("limit", 20)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{gw.gateway_url}/api/clawhub/search",
                params={"q": query, "limit": limit},
                headers=gw._headers(),
            )
            if resp.status_code != 200:
                return {"error": f"ClawHub search failed ({resp.status_code})", "items": []}
            data = resp.json()
            items = data if isinstance(data, list) else data.get("items", data.get("results", []))
            _log(f"ClawHub search '{query}' returned {len(items)} results")
            return {"items": items, "count": len(items)}
    except Exception as e:
        _log(f"ClawHub search failed: {e}")
        return {"error": f"Search failed: {e}", "items": []}


async def tool_install_from_clawhub(args: dict[str, Any], gw: RevkaGatewayClient) -> dict[str, Any]:
    if not gw._available:
        return {"error": "Revka gateway not available."}

    slug = args["slug"]

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{gw.gateway_url}/api/clawhub/install/{slug}",
                headers=gw._headers(),
            )
            if resp.status_code not in (200, 201):
                body = resp.text
                return {"error": f"Install failed ({resp.status_code}): {body}"}
            data = resp.json()
            _log(f"Installed '{slug}' from ClawHub: {data.get('kref', '')}")
            return data
    except Exception as e:
        _log(f"ClawHub install failed: {e}")
        return {"error": f"Failed to install: {e}"}


async def tool_browse_clawhub(args: dict[str, Any], gw: RevkaGatewayClient) -> dict[str, Any]:
    if not gw._available:
        return {"error": "Revka gateway not available.", "items": []}

    limit = args.get("limit", 20)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{gw.gateway_url}/api/clawhub/trending",
                params={"limit": limit},
                headers=gw._headers(),
            )
            if resp.status_code != 200:
                return {"error": f"ClawHub browse failed ({resp.status_code})", "items": []}
            data = resp.json()
            items = data if isinstance(data, list) else data.get("items", data.get("skills", []))
            _log(f"ClawHub trending returned {len(items)} skills")
            return {"items": items, "count": len(items)}
    except Exception as e:
        _log(f"ClawHub browse failed: {e}")
        return {"error": f"Browse failed: {e}", "items": []}
