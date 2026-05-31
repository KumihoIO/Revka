"""Agent pool tool handlers: search, save template, list templates."""
from __future__ import annotations

import json as _json
from datetime import datetime, timezone
from typing import Any

from ..agent_state import AgentTemplate, POOL
from ..kumiho_clients import KumihoAgentPoolClient


def _template_to_dict(t: AgentTemplate) -> dict[str, Any]:
    """Convert a template to the response dict format."""
    return {
        "name": t.name,
        "agent_type": t.agent_type,
        "role": t.role,
        "capabilities": t.capabilities,
        "description": t.description,
        "use_count": t.use_count,
    }


async def tool_search_agent_pool(args: dict[str, Any], pool_client: KumihoAgentPoolClient) -> dict[str, Any]:
    query = args["query"]
    local_matches = [_template_to_dict(t) for t in POOL.search(query)]
    kumiho_items = await pool_client.search_agents(query)
    kumiho_matches = [pool_client.item_to_template_dict(item) for item in kumiho_items]
    seen = {m["name"] for m in local_matches}
    for km in kumiho_matches:
        if km["name"] not in seen:
            local_matches.append(km)
            seen.add(km["name"])
    return {
        "matches": local_matches,
        "count": len(local_matches),
    }


async def tool_save_agent_template(args: dict[str, Any], pool_client: KumihoAgentPoolClient) -> dict[str, Any]:
    name = args["name"]
    agent_type = args["agent_type"]
    role = args["role"]
    capabilities = args["capabilities"]
    if isinstance(capabilities, str):
        try:
            capabilities = _json.loads(capabilities)
        except (ValueError, TypeError):
            capabilities = [c.strip() for c in capabilities.split(",") if c.strip()]
    description = args["description"]

    if agent_type not in ("claude", "codex"):
        return {"error": f"Invalid agent_type: {agent_type}. Must be 'claude' or 'codex'."}
    valid_roles = ("coder", "reviewer", "researcher", "tester", "architect", "planner")
    if role not in valid_roles:
        return {"error": f"Invalid role: {role}. Must be one of: {', '.join(valid_roles)}"}

    is_update = name in POOL.templates

    existing = POOL.templates.get(name)
    template = AgentTemplate(
        name=name,
        agent_type=agent_type,
        role=role,
        capabilities=capabilities,
        description=description,
        identity=args.get("identity"),
        soul=args.get("soul"),
        tone=args.get("tone"),
        model=args.get("model"),
        default_cwd=args.get("default_cwd"),
        system_hint=args.get("system_hint"),
        created_at=existing.created_at if existing else datetime.now(timezone.utc).isoformat(),
        last_used=existing.last_used if existing else None,
        use_count=existing.use_count if existing else 0,
    )
    POOL.add(template)

    kumiho_saved = await pool_client.save_agent(
        name=name,
        agent_type=agent_type,
        role=role,
        capabilities=capabilities,
        description=description,
        identity=args.get("identity"),
        soul=args.get("soul"),
        tone=args.get("tone"),
        system_hint=args.get("system_hint"),
        model=args.get("model"),
    )

    return {
        "saved": True,
        "name": name,
        "is_update": is_update,
        "kumiho_synced": kumiho_saved,
    }


async def tool_list_agent_templates(pool_client: KumihoAgentPoolClient) -> dict[str, Any]:
    local_templates = [_template_to_dict(t) for t in POOL.list_all()]
    kumiho_items = await pool_client.list_agents()
    kumiho_templates = [pool_client.item_to_template_dict(item) for item in kumiho_items]
    seen = {t["name"] for t in local_templates}
    for kt in kumiho_templates:
        if kt["name"] not in seen:
            local_templates.append(kt)
            seen.add(kt["name"])
    return {
        "templates": local_templates,
        "count": len(local_templates),
    }
