"""Planning and goal tool handlers: save_plan, recall_plans, goals."""
from __future__ import annotations

import json as _json
from datetime import datetime, timezone
from typing import Any

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore

from .._log import _log
from ..revka_config import harness_project
from ..kumiho_clients import KumihoAgentPoolClient


async def tool_save_plan(args: dict[str, Any], pool_client: KumihoAgentPoolClient) -> dict[str, Any]:
    if not pool_client._available:
        return {"error": "Kumiho not available. Plan not saved."}

    name = args["name"]
    task_description = args["task_description"]
    steps = args.get("steps", [])
    agents_used = args.get("agents_used", [])
    outcome = args["outcome"]
    lessons = args.get("lessons", "")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            headers = pool_client._headers()
            api = pool_client.api_url

            _project = harness_project()
            await client.post(f"{api}/api/v1/projects", json={"name": _project}, headers=headers)
            await client.post(f"{api}/api/v1/spaces", json={"parent_path": f"/{_project}", "name": "Plans"}, headers=headers)

            metadata = {
                "task_description": task_description,
                "steps": ",".join(steps),
                "agents_used": ",".join(agents_used),
                "outcome": outcome,
                "lessons": lessons,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

            resp = await client.post(
                f"{api}/api/v1/items",
                json={
                    "space_path": f"/{_project}/Plans",
                    "item_name": name,
                    "kind": "plan",
                    "metadata": metadata,
                },
                headers=headers,
            )
            resp.raise_for_status()
            item = resp.json()
            kref = item.get("kref", "")

            if kref:
                await client.post(
                    f"{api}/api/v1/revisions",
                    json={"kref": kref, "metadata": metadata},
                    headers=headers,
                )

            _log(f"Saved plan '{name}' (outcome: {outcome})")
            return {"saved": True, "name": name, "kref": kref, "outcome": outcome}
    except Exception as e:
        _log(f"Plan save failed: {e}")
        return {"error": f"Failed to save plan: {e}"}


async def tool_recall_plans(args: dict[str, Any], pool_client: KumihoAgentPoolClient) -> dict[str, Any]:
    if not pool_client._available:
        return {"error": "Kumiho not available.", "plans": []}

    query = args["query"]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            headers = pool_client._headers()
            api = pool_client.api_url

            resp = await client.get(
                f"{api}/api/v1/items/fulltext-search",
                params={
                    "query": query,
                    "context": harness_project(),
                    "space_path": f"/{harness_project()}/Plans",
                    "include_revision_metadata": True,
                },
                headers=headers,
            )
            if resp.status_code == 404:
                return {"plans": [], "count": 0}
            resp.raise_for_status()
            results = resp.json()
            if not isinstance(results, list):
                results = []

            plans = []
            for item in results[:10]:
                meta = item.get("metadata", {})
                steps = meta.get("steps", "")
                if isinstance(steps, str):
                    steps = [s.strip() for s in steps.split(",") if s.strip()]
                agents = meta.get("agents_used", "")
                if isinstance(agents, str):
                    agents = [a.strip() for a in agents.split(",") if a.strip()]
                plans.append({
                    "name": item.get("item_name", item.get("name", "unknown")),
                    "task_description": meta.get("task_description", ""),
                    "steps": steps,
                    "agents_used": agents,
                    "outcome": meta.get("outcome", "unknown"),
                    "lessons": meta.get("lessons", ""),
                })

            return {"plans": plans, "count": len(plans)}
    except Exception as e:
        _log(f"Plan recall failed: {e}")
        return {"error": f"Plan recall failed: {e}", "plans": []}


async def tool_create_goal(args: dict[str, Any], pool_client: KumihoAgentPoolClient) -> dict[str, Any]:
    if not pool_client._available:
        return {"error": "Kumiho not available. Goal not created."}

    name = args["name"]
    description = args["description"]
    status = args.get("status", "active")
    priority = args.get("priority", "p1")
    parent_kref = args.get("parent_kref")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            headers = pool_client._headers()
            api = pool_client.api_url

            _project = harness_project()
            await client.post(f"{api}/api/v1/projects", json={"name": _project}, headers=headers)
            await client.post(f"{api}/api/v1/spaces", json={"parent_path": f"/{_project}", "name": "Goals"}, headers=headers)

            metadata = {
                "description": description,
                "status": status,
                "priority": priority,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

            resp = await client.post(
                f"{api}/api/v1/items",
                json={
                    "space_path": f"/{_project}/Goals",
                    "item_name": name,
                    "kind": "goal",
                    "metadata": metadata,
                },
                headers=headers,
            )
            resp.raise_for_status()
            item = resp.json()
            kref = item.get("kref", "")

            if kref:
                await client.post(
                    f"{api}/api/v1/revisions",
                    json={"kref": kref, "metadata": metadata},
                    headers=headers,
                )

            if parent_kref and kref:
                try:
                    await client.post(
                        f"{api}/api/v1/edges",
                        json={
                            "source_kref": parent_kref,
                            "target_kref": kref,
                            "kind": "PARENT_OF",
                            "metadata": {},
                        },
                        headers=headers,
                    )
                except Exception as edge_err:
                    _log(f"Warning: could not link goal to parent: {edge_err}")

            _log(f"Created goal '{name}' [{priority}/{status}]")
            return {"created": True, "name": name, "kref": kref, "status": status, "priority": priority}
    except Exception as e:
        _log(f"Goal creation failed: {e}")
        return {"error": f"Failed to create goal: {e}"}


async def tool_get_goals(args: dict[str, Any], pool_client: KumihoAgentPoolClient) -> dict[str, Any]:
    if not pool_client._available:
        return {"error": "Kumiho not available.", "goals": []}

    status_filter = args.get("status")
    priority_filter = args.get("priority")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            headers = pool_client._headers()
            api = pool_client.api_url

            resp = await client.get(
                f"{api}/api/v1/items",
                params={"space_path": f"/{harness_project()}/Goals", "include_metadata": True},
                headers=headers,
            )
            if resp.status_code == 404:
                return {"goals": [], "count": 0}
            resp.raise_for_status()
            items = resp.json()
            if not isinstance(items, list):
                items = []

            goals = []
            for item in items:
                meta = item.get("metadata", {})
                goal_status = meta.get("status", "active")
                goal_priority = meta.get("priority", "p1")

                if status_filter and goal_status != status_filter:
                    continue
                if priority_filter and goal_priority != priority_filter:
                    continue

                goals.append({
                    "name": item.get("item_name", item.get("name", "unknown")),
                    "kref": item.get("kref", ""),
                    "description": meta.get("description", ""),
                    "status": goal_status,
                    "priority": goal_priority,
                    "created_at": meta.get("created_at", ""),
                })

            priority_order = {"p0": 0, "p1": 1, "p2": 2, "p3": 3}
            goals.sort(key=lambda g: (priority_order.get(g["priority"], 9), g.get("created_at", "")))

            return {"goals": goals, "count": len(goals)}
    except Exception as e:
        _log(f"Goal listing failed: {e}")
        return {"error": f"Goal listing failed: {e}", "goals": []}


async def tool_update_goal(args: dict[str, Any], pool_client: KumihoAgentPoolClient) -> dict[str, Any]:
    if not pool_client._available:
        return {"error": "Kumiho not available."}

    kref = args["kref"]
    new_status = args.get("status")
    new_priority = args.get("priority")
    new_description = args.get("description")

    if not any([new_status, new_priority, new_description]):
        return {"error": "Nothing to update. Provide at least one of: status, priority, description."}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            headers = pool_client._headers()
            api = pool_client.api_url

            resp = await client.get(f"{api}/api/v1/items/{kref}", headers=headers)
            resp.raise_for_status()
            item = resp.json()
            meta = item.get("metadata", {})

            if new_status:
                meta["status"] = new_status
            if new_priority:
                meta["priority"] = new_priority
            if new_description:
                meta["description"] = new_description
            meta["updated_at"] = datetime.now(timezone.utc).isoformat()

            resp = await client.post(
                f"{api}/api/v1/revisions",
                json={"kref": kref, "metadata": meta},
                headers=headers,
            )
            resp.raise_for_status()

            name = item.get("item_name", item.get("name", "unknown"))
            _log(f"Updated goal '{name}': status={meta.get('status')}, priority={meta.get('priority')}")
            return {
                "updated": True,
                "name": name,
                "kref": kref,
                "status": meta.get("status"),
                "priority": meta.get("priority"),
            }
    except Exception as e:
        _log(f"Goal update failed: {e}")
        return {"error": f"Failed to update goal: {e}"}
