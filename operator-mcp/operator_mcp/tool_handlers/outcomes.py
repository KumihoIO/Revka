"""Agent outcome recording — A2A learning propagation through Kumiho.

Step 2 of the self-improving agent plan. Each agent run can record structured
"outcomes" (discoveries, decisions, lessons, insights, warnings) into a
session-scoped space at ``<harness_project>/Sessions/<session_id>/Outcomes/``,
where ``<harness_project>`` comes from `[kumiho].harness_project` in
``~/.revka/config.toml`` (default ``Revka``). Downstream agents in
the same workflow / chat / handoff chain can then inherit those outcomes via
``recall_session_outcomes`` and start with the team's accumulated knowledge
instead of re-discovering everything.

Storage layout (with default harness)::

    Revka/                       ← <harness_project> from config.toml
        Sessions/
            <session_id>/
                Outcomes/
                    <outcome-name>.outcome  ← memory_item_kind="outcome"

The outcome's ``memory_type`` doubles as its semantic kind (one of:
``discovery | decision | lesson | insight | warning | fact``). Edges of type
``INFORMS`` link outcomes back to ``related_krefs`` so the graph captures
which prior memories led to a given conclusion.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .._log import _log
from ..revka_config import harness_project

try:
    from kumiho.mcp_server import (
        tool_memory_store,
        tool_memory_retrieve,
        tool_search_items,
    )

    _HAS_KUMIHO = True
except ImportError:
    _HAS_KUMIHO = False


# Allowed outcome kinds. We accept anything but normalize unknown values to
# 'discovery' so the graph stays queryable.
_OUTCOME_KINDS = {
    "discovery",
    "decision",
    "lesson",
    "insight",
    "warning",
    "fact",
    "outcome",
}


def _unavailable() -> dict[str, Any]:
    return {
        "error": "kumiho package not available — install via `revka sidecars install`",
    }


def _normalize_kind(kind: str | None) -> str:
    if not kind:
        return "discovery"
    k = kind.strip().lower()
    if k in _OUTCOME_KINDS:
        return k
    return "discovery"


def _outcomes_space(session_id: str) -> str:
    """Resolve the storage space path for a session's outcomes.

    Uses the configured harness_project (default 'Revka') so deployments
    that rebrand the harness project keep all session data under the right
    namespace.
    """
    safe_session = (session_id or "unknown").replace("/", "-")
    return f"/{harness_project()}/Sessions/{safe_session}/Outcomes"


# ---------------------------------------------------------------------------
# record_agent_outcome
# ---------------------------------------------------------------------------


_TRUST_STATUS_VALUES = {"success", "partial", "failed"}


async def tool_record_agent_outcome_op(
    args: dict[str, Any],
    pool_client: Any | None = None,
) -> dict[str, Any]:
    """Record an agent outcome to ``<harness>/Sessions/<session_id>/Outcomes/``.

    Outcomes are append-only memories that downstream agents can inherit. Each
    outcome has a ``kind`` (discovery / decision / lesson / insight / warning /
    fact) and optional ``related_krefs`` that get DERIVED_FROM-style edges.

    Optional trust scoring: when ``template_name`` and ``status`` (one of
    success / partial / failed) are both present, ALSO update the agent
    template's rolling trust score in ``/<harness>/AgentTrust/<template>``.
    This used to be a separate tool that clashed with this one's name —
    folding it in keeps the LLM-facing surface to a single tool. Trust
    update failures are non-fatal; the outcome record still succeeds.

    Required: ``session_id``, ``title``.
    """
    if not _HAS_KUMIHO:
        return _unavailable()

    session_id = args.get("session_id")
    title = args.get("title")
    if not session_id:
        return {"error": "session_id is required"}
    if not title:
        return {"error": "title is required"}

    kind = _normalize_kind(args.get("kind"))
    content = args.get("content", "") or ""
    tags = list(args.get("tags") or [])
    related_files = args.get("related_files") or []
    related_krefs = args.get("related_krefs") or []
    agent_id = args.get("agent_id", "")
    agent_kref = args.get("agent_kref", "")
    template_name = args.get("template_name", "") or ""
    status = (args.get("status") or "").strip().lower()

    # Index tags so future engage / search calls can filter by kind / session /
    # agent without scanning content. De-duplicate.
    auto_tags = {kind, f"session:{session_id}"}
    if agent_id:
        auto_tags.add(f"agent:{agent_id}")
    if status in _TRUST_STATUS_VALUES:
        auto_tags.add(f"status:{status}")
    if template_name:
        auto_tags.add(f"template:{template_name}")
    for t in auto_tags:
        if t not in tags:
            tags.append(t)

    metadata: dict[str, Any] = {
        "session_id": session_id,
        "kind": kind,
    }
    if agent_id:
        metadata["agent_id"] = agent_id
    if agent_kref:
        metadata["agent_kref"] = agent_kref
    if template_name:
        metadata["template_name"] = template_name
    if status in _TRUST_STATUS_VALUES:
        metadata["status"] = status
    if related_files:
        # Kumiho metadata values are coerced to str — join with comma.
        metadata["related_files"] = ",".join(str(f) for f in related_files)

    space_path = _outcomes_space(session_id)

    try:
        result = await asyncio.to_thread(
            tool_memory_store,
            project=harness_project(),
            space_path=space_path,
            memory_type=kind,
            memory_item_kind="outcome",
            title=title,
            summary=content,
            assistant_text=content,
            tags=tags,
            source_revision_krefs=list(related_krefs),
            metadata=metadata,
            edge_type="INFORMS",
            stack_revisions=False,  # outcomes are append-only, never overwrite
        )
    except Exception as e:  # noqa: BLE001
        _log(f"record_agent_outcome failed: {e}")
        return {"error": f"store failed: {e}"}

    if isinstance(result, dict) and "error" in result:
        return result

    kref = (
        result.get("revision_kref")
        or result.get("item_kref")
        or result.get("kref")
        if isinstance(result, dict)
        else None
    )

    response: dict[str, Any] = {
        "kref": kref,
        "session_id": session_id,
        "kind": kind,
        "space_path": space_path,
        "raw": result,
    }

    # Optional trust-score update — only fires when the caller passed both
    # template_name and a recognized status. Soft-fails so outcome storage
    # remains the load-bearing path.
    if template_name and status in _TRUST_STATUS_VALUES and pool_client is not None:
        try:
            from .trust import update_agent_trust

            trust_result = await update_agent_trust(
                template_name=template_name,
                outcome=status,
                task_summary=title,
                agent_id=agent_id,
                pool_client=pool_client,
            )
            response["trust"] = trust_result
        except Exception as e:  # noqa: BLE001
            _log(f"trust update failed (outcome still recorded): {e}")
            response["trust"] = {"error": f"trust update failed: {e}"}

    return response


# ---------------------------------------------------------------------------
# recall_session_outcomes
# ---------------------------------------------------------------------------


async def tool_recall_session_outcomes_op(args: dict[str, Any]) -> dict[str, Any]:
    """Recall outcomes from a session (or list of sessions).

    Use this when a new agent / handoff target / next workflow step starts up
    so it can inherit what siblings already learned. Pass ``query`` for
    semantic ranking, or omit it for a chronological list.

    Either ``session_id``, ``sibling_sessions``, or ``query`` is required.
    """
    if not _HAS_KUMIHO:
        return _unavailable()

    session_id = args.get("session_id")
    sibling_sessions = args.get("sibling_sessions") or []
    query = (args.get("query") or "").strip()
    limit = args.get("limit", 10)
    kinds = args.get("kinds") or []

    space_paths: list[str] = []
    if session_id:
        space_paths.append(_outcomes_space(session_id))
    for sid in sibling_sessions:
        space_paths.append(_outcomes_space(sid))

    if not space_paths and not query:
        return {
            "error": (
                "specify session_id, sibling_sessions, or query — at least one "
                "must be provided to scope the search"
            )
        }

    if query:
        try:
            raw = await asyncio.to_thread(
                tool_memory_retrieve,
                project=harness_project(),
                query=query,
                space_paths=space_paths or None,
                memory_item_kind="outcome",
                memory_types=list(kinds) if kinds else None,
                limit=limit,
            )
        except Exception as e:  # noqa: BLE001
            _log(f"recall_session_outcomes (query) failed: {e}")
            return {"error": f"retrieve failed: {e}"}
        items = []
        if isinstance(raw, dict):
            items = raw.get("items") or raw.get("results") or []
        return {
            "outcomes": items,
            "count": len(items) if isinstance(items, list) else 0,
            "mode": "semantic",
            "space_paths": space_paths,
        }

    # No query — list all outcomes in the requested sessions.
    all_items: list[dict[str, Any]] = []
    for sp in space_paths:
        try:
            r = await asyncio.to_thread(
                tool_search_items,
                context_filter=sp.lstrip("/"),
                kind_filter="outcome",
            )
        except Exception as e:  # noqa: BLE001
            _log(f"recall_session_outcomes (list {sp}) failed: {e}")
            continue
        if isinstance(r, dict):
            items = r.get("items") or []
            if kinds:
                kinds_set = {k.lower() for k in kinds}
                items = [
                    it
                    for it in items
                    if (it.get("memory_type") or "").lower() in kinds_set
                    or any(t in kinds_set for t in (it.get("tags") or []))
                ]
            all_items.extend(items)

    # Cap on caller-requested limit — newest first if created_at is present.
    try:
        all_items.sort(key=lambda it: it.get("created_at") or "", reverse=True)
    except Exception:  # noqa: BLE001
        pass
    if isinstance(limit, int) and limit > 0:
        all_items = all_items[:limit]

    return {
        "outcomes": all_items,
        "count": len(all_items),
        "mode": "list",
        "space_paths": space_paths,
    }
