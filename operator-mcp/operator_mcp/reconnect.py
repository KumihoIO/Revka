"""Agent reconnection on startup — reattach to sidecar agents that survived a restart.

On operator restart, the in-memory AGENTS dict is empty but the sidecar may still
have running agents from the previous session.  This module reconciles the two by:

1. Querying the sidecar for all active agents
2. Cross-referencing with journal entries (matching sidecar_id)
3. Reconstructing ManagedAgent instances in the AGENTS dict
4. Re-subscribing the EventConsumer so logs resume

Usage (called once in _run() during startup):
    recovered = await reconnect_agents(sidecar, journal, event_consumer)
"""
from __future__ import annotations

from typing import Any

from ._log import _log
from .agent_state import AGENTS, ManagedAgent

_LAST_LOG_AT: dict[str, float] = {}
_LOG_THROTTLE_SECS = 300.0


def _log_limited(key: str, msg: str, *, interval: float = _LOG_THROTTLE_SECS) -> None:
    import time

    now = time.monotonic()
    last = _LAST_LOG_AT.get(key, 0.0)
    if now - last >= interval:
        _LAST_LOG_AT[key] = now
        _log(msg)


async def reconnect_agents(
    sidecar: Any,
    journal: Any,
    event_consumer: Any,
) -> list[dict[str, Any]]:
    """Attempt to reconnect to agents still alive in the sidecar.

    Returns a list of dicts describing each recovered agent (or an empty list).
    """
    if sidecar is None:
        return []

    # Step 1: ask sidecar what's still alive
    try:
        sidecar_agents = await sidecar.list_agents()
    except Exception as e:
        _log_limited("list_agents_failed", f"reconnect: sidecar.list_agents() failed: {e}")
        return []

    if not sidecar_agents:
        _log_limited("no_active_agents", "reconnect: no active sidecar agents found")
        return []

    _log(f"reconnect: sidecar reports {len(sidecar_agents)} active agent(s)")

    # Step 2: build a lookup from sidecar_id -> journal entry
    journal_by_sidecar_id = _build_journal_index(journal)

    recovered: list[dict[str, Any]] = []

    for sa in sidecar_agents:
        sidecar_id = sa.get("id", "")
        sidecar_status = sa.get("status", "")
        if not sidecar_id:
            continue

        # Skip agents that are already done
        if sidecar_status in ("closed", "error"):
            continue

        # Step 3: find the matching journal entry
        je = journal_by_sidecar_id.get(sidecar_id)

        if je:
            agent_id = je.get("agent_id", sidecar_id)
            agent_type = je.get("agent_type", "claude")
            title = je.get("title", sa.get("title", sidecar_id[:8]))
            cwd = je.get("cwd", sa.get("cwd", ""))
        else:
            # No journal match — use sidecar data directly
            agent_id = sidecar_id
            agent_type = sa.get("agentType", "claude")
            title = sa.get("title", sidecar_id[:8])
            cwd = sa.get("cwd", "")

        # Don't double-register
        if agent_id in AGENTS:
            continue

        # Step 4: reconstruct ManagedAgent
        status = _map_sidecar_status(sidecar_status)
        agent = ManagedAgent(
            id=agent_id,
            agent_type=agent_type,
            title=title,
            cwd=cwd,
            status=status,
        )
        agent._sidecar_id = sidecar_id
        AGENTS[agent_id] = agent

        # Step 5: re-subscribe event consumer
        if event_consumer is not None:
            await event_consumer.subscribe(sidecar_id, title)

        info = {
            "agent_id": agent_id,
            "sidecar_id": sidecar_id,
            "title": title,
            "status": status,
            "from_journal": je is not None,
        }
        recovered.append(info)
        _log(f"reconnect: recovered {title} ({agent_id[:8]}) status={status}")

    _log(f"reconnect: {len(recovered)} agent(s) recovered")
    return recovered


def _build_journal_index(journal: Any) -> dict[str, dict[str, Any]]:
    """Build sidecar_id -> journal entry mapping from recent journal history."""
    index: dict[str, dict[str, Any]] = {}
    try:
        entries = journal.load_history(limit=200)
    except Exception:
        return index

    for entry in entries:
        sid = entry.get("sidecar_id", "")
        if sid and sid not in index:
            index[sid] = entry
    return index


def _map_sidecar_status(sidecar_status: str) -> str:
    """Map sidecar status string to operator's ManagedAgent status."""
    mapping = {
        "running": "running",
        "idle": "idle",
        "waiting": "running",
        "active": "running",
        "error": "error",
        "closed": "idle",
    }
    return mapping.get(sidecar_status, "running")
