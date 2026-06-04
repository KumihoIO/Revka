"""Live Canvas tool handlers: render, clear."""
from __future__ import annotations

from typing import Any

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore

from .._log import _log
from ..gateway_client import RevkaGatewayClient


async def tool_render_canvas(args: dict[str, Any], gw: RevkaGatewayClient) -> dict[str, Any]:
    if not gw._available:
        return {"error": "Revka gateway not available."}

    content = args["content"]
    content_type = args.get("content_type", "html")
    canvas_id = args.get("canvas_id", "default")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{gw.gateway_url}/api/canvas/{canvas_id}",
                json={"content": content, "content_type": content_type},
                headers=gw._headers(),
            )
            if resp.status_code not in (200, 201):
                body = resp.text
                return {"error": f"Canvas render failed ({resp.status_code}): {body}"}
            data = resp.json()
            _log(f"Canvas '{canvas_id}' updated with {content_type} ({len(content)} chars)")
            return {"success": True, "canvas_id": canvas_id, "frame_id": data.get("frame_id", "")}
    except Exception as e:
        _log(f"Canvas render failed: {e}")
        return {"error": f"Canvas render failed: {e}"}


async def tool_clear_canvas(args: dict[str, Any], gw: RevkaGatewayClient) -> dict[str, Any]:
    if not gw._available:
        return {"error": "Revka gateway not available."}

    canvas_id = args.get("canvas_id", "default")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.delete(
                f"{gw.gateway_url}/api/canvas/{canvas_id}",
                headers=gw._headers(),
            )
            if resp.status_code not in (200, 204):
                return {"error": f"Canvas clear failed ({resp.status_code})"}
            _log(f"Canvas '{canvas_id}' cleared")
            return {"success": True, "canvas_id": canvas_id}
    except Exception as e:
        _log(f"Canvas clear failed: {e}")
        return {"error": f"Canvas clear failed: {e}"}
