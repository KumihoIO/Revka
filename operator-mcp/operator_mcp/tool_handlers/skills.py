"""Skill tool handlers: capture, list, load."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

_MEMORY_PROJECT = os.environ.get("KUMIHO_MEMORY_PROJECT", "CognitiveMemory")

from .._log import _log
from ..kumiho_clients import KumihoAgentPoolClient
from ..skill_loader import list_skills, load_skill
from .memory import tool_memory_store_op


async def tool_capture_skill(args: dict[str, Any], pool_client: KumihoAgentPoolClient) -> dict[str, Any]:
    # NOTE: do not early-return on `pool_client._available`. That flag is set
    # lazily — `None` until `_ensure_available()` runs — so checking it raw
    # produces a false negative when capture_skill is the first pool-client
    # call in a session. tool_memory_store_op does its own availability check
    # internally (against `_HAS_KUMIHO`), and the SDK call surfaces its own
    # error if Kumiho is genuinely unreachable. Same fix pattern as PR #197
    # (archive_session). Routing through tool_memory_store_op also avoids
    # the raw `/api/v1/items` POST which can 422 on `kind: skill`.
    name = args["name"]
    domain = args["domain"]
    description = args["description"]
    procedure = args["procedure"]
    learned_from = args.get("learned_from", "")

    metadata = {
        "description": description,
        "domain": domain,
        "procedure": procedure,
        "learned_from": learned_from,
        "source": "operator-auto-capture",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        result = await tool_memory_store_op({
            "project": _MEMORY_PROJECT,
            "space_path": "Skills",
            "memory_item_kind": "skill",
            "memory_type": "summary",
            "title": name,
            "summary": "",
            "assistant_text": procedure,
            "user_text": description,
            "tags": [domain, "operator-auto-capture"],
            "metadata": metadata,
        })
    except Exception as e:
        _log(f"Skill capture failed: {e}")
        return {"error": f"Failed to capture skill: {e}"}

    if isinstance(result, dict) and result.get("error"):
        _log(f"Skill capture failed: {result['error']}")
        return {"error": f"Failed to capture skill: {result['error']}"}

    kref = ""
    if isinstance(result, dict):
        kref = (
            result.get("kref")
            or result.get("item_kref")
            or (result.get("item") or {}).get("kref")
            or ""
        )

    _log(f"Captured skill '{name}' [{domain}] (kref={kref})")
    return {"captured": True, "name": name, "kref": kref}


async def tool_list_skills() -> dict[str, Any]:
    """List all available orchestration skills."""
    skills = list_skills()
    return {"skills": skills, "count": len(skills)}


async def tool_load_skill(args: dict[str, Any]) -> dict[str, Any]:
    """Load a specific skill's content."""
    name = args["name"]
    content = load_skill(name)
    if content is None:
        return {"error": f"Skill not found: {name}"}
    return {"name": name, "content": content}
