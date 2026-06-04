"""Session continuity tool handlers: history, archive."""
from __future__ import annotations

import json as _json
from datetime import datetime, timezone
from typing import Any

from .._log import _log
from ..revka_config import harness_project
from ..journal import SessionJournal
from ..kumiho_clients import KumihoAgentPoolClient
from .memory import tool_memory_store_op


async def tool_get_session_history(args: dict[str, Any], journal: SessionJournal) -> dict[str, Any]:
    if args.get("list_sessions"):
        sessions = journal.list_sessions(limit=args.get("limit", 20))
        return {
            "sessions": sessions,
            "count": len(sessions),
            "current_session": journal.session_id,
        }

    entries = journal.load_history(
        limit=args.get("limit", 30),
        session_id=args.get("session_id"),
        agent_id=args.get("agent_id"),
    )
    return {
        "entries": entries,
        "count": len(entries),
        "current_session": journal.session_id,
    }


async def tool_archive_session(args: dict[str, Any], journal: SessionJournal, pool_client: KumihoAgentPoolClient) -> dict[str, Any]:
    if not pool_client._available:
        return {"error": "Kumiho not available. Session not archived."}

    session_id = args.get("session_id", journal.session_id)
    title = args["title"]
    summary = args["summary"]
    outcome = args["outcome"]

    entries = journal.load_history(limit=200, session_id=session_id)

    agents_seen: dict[str, dict[str, Any]] = {}
    for entry in reversed(entries):
        aid = entry.get("agent_id", "")
        if aid not in agents_seen:
            agents_seen[aid] = {
                "agent_id": aid,
                "title": entry.get("title", ""),
                "agent_type": entry.get("agent_type", ""),
                "template": entry.get("template", ""),
                "final_status": entry.get("event", ""),
            }
        else:
            agents_seen[aid]["final_status"] = entry.get("event", agents_seen[aid]["final_status"])

    now = datetime.now(timezone.utc).isoformat()
    metadata = {
        "session_id": session_id,
        "title": title,
        "summary": summary,
        "outcome": outcome,
        "agent_count": len(agents_seen),
        "agents": _json.dumps(list(agents_seen.values())),
        "event_count": len(entries),
        "archived_at": now,
    }

    try:
        result = await tool_memory_store_op({
            "project": harness_project(),
            "space_path": "Sessions",
            "memory_item_kind": "session",
            "memory_type": "summary",
            "title": title,
            "summary": "",
            "assistant_text": summary,
            "user_text": "",
            "tags": [],
            "metadata": metadata,
        })

        if isinstance(result, dict) and result.get("error"):
            _log(f"Session archive failed: {result['error']}")
            return {"error": f"Failed to archive session: {result['error']}"}

        kref = ""
        if isinstance(result, dict):
            kref = (
                result.get("kref")
                or result.get("item_kref")
                or (result.get("item") or {}).get("kref")
                or ""
            )

        try:
            journal.record(session_id, "archived", summary=title)
        except Exception:
            pass  # Non-critical — archive already persisted

        _log(f"Archived session '{session_id}' (kref={kref})")
        return {
            "archived": True,
            "session_id": session_id,
            "kref": kref,
            "title": title,
            "outcome": outcome,
            "agent_count": len(agents_seen),
        }
    except Exception as e:
        _log(f"Session archive failed: {e}")
        return {"error": f"Failed to archive session: {e}"}
