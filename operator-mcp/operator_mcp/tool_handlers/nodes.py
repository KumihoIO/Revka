"""Multi-node tool handlers: list_nodes, invoke_node."""
from __future__ import annotations

from typing import Any

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore

from .._log import _log
from ..gateway_client import RevkaGatewayClient


async def tool_list_nodes(gw: RevkaGatewayClient) -> dict[str, Any]:
    if not gw._available:
        return {"error": "Gateway not available. Cannot list nodes.", "nodes": []}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{gw.gateway_url}/api/nodes",
                headers=gw._headers(),
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        _log(f"list_nodes failed: {e}")
        return {"error": f"Failed to list nodes: {e}", "nodes": []}


async def tool_invoke_node(args: dict[str, Any], gw: RevkaGatewayClient) -> dict[str, Any]:
    if not gw._available:
        return {"error": "Gateway not available. Cannot invoke node."}

    node_id = args["node_id"]
    capability = args["capability"]
    invoke_args = args.get("args", {})

    try:
        async with httpx.AsyncClient(timeout=35) as client:
            resp = await client.post(
                f"{gw.gateway_url}/api/nodes/{node_id}/invoke",
                json={"capability": capability, "args": invoke_args},
                headers=gw._headers(),
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        _log(f"invoke_node failed: {e}")
        return {"error": f"Node invocation failed: {e}"}
