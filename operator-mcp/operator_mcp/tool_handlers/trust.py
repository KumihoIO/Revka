"""Agent trust/reputation helpers.

Trust scoring is the rolling success-rate signal we accumulate per agent
template across runs. Storage lives at ``/<harness>/AgentTrust/<template>``
where each item's metadata carries ``trust_score`` (recency-weighted
success ratio), ``total_runs``, and a ``recent_outcomes`` ring buffer.

Trust *recording* is no longer its own MCP tool — it folded into
``record_agent_outcome`` (see tool_handlers.outcomes). The previous
top-level ``record_agent_outcome`` tool here clashed by name with the
session-outcome tool, leading to schema/dispatch mismatches where the
LLM client saw one schema while the dispatcher fired the other handler.
This module now exposes ``update_agent_trust`` as a plain helper that
the outcomes handler calls inline when it gets ``template_name`` +
``status`` fields.

Trust *retrieval* (``get_agent_trust``) is still its own tool — that
read path was never duplicated and downstream code consumes it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .._log import _log
from ..revka_config import harness_project
from ..kumiho_clients import KumihoAgentPoolClient


# Maps the outcome label that callers pass in to the score increment we
# fold into the running average. Anything outside the table treats as
# "partial" (0.5) so unexpected values don't crash the trust update.
_TRUST_SCORE_WEIGHTS = {"success": 1.0, "partial": 0.5, "failed": 0.0}


async def update_agent_trust(
    *,
    template_name: str,
    outcome: str,
    task_summary: str,
    agent_id: str,
    pool_client: KumihoAgentPoolClient,
) -> dict[str, Any]:
    """Update the rolling trust score for an agent template.

    Reads the latest revision in ``/<harness>/AgentTrust/<template>``,
    increments ``total_runs`` + ``total_score``, recomputes
    ``trust_score`` (mean), appends to the recent-outcomes ring buffer
    (last 10), and writes a new revision. Creates the item if it
    doesn't exist yet.

    Returns ``{recorded, template_name, trust_score, total_runs}`` on
    success or ``{error}`` on any failure. Failures are non-fatal —
    callers (the outcomes handler) treat trust-update errors as a soft
    warning, not a reason to fail the underlying outcome record.
    """
    if not pool_client._available:
        return {"error": "Kumiho not available."}

    if not template_name:
        return {"error": "template_name is required for trust update"}

    project = harness_project()
    trust_space = f"/{project}/AgentTrust"
    score_val = _TRUST_SCORE_WEIGHTS.get(outcome, 0.5)
    now = datetime.now(timezone.utc).isoformat()

    try:
        await pool_client.ensure_space(project, "AgentTrust")

        items = await pool_client.list_items(trust_space)
        existing_kref = None
        existing_meta: dict[str, Any] = {}
        for item in items:
            if item.get("item_name") == template_name:
                existing_kref = item.get("kref")
                rev = await pool_client.get_latest_revision(existing_kref)
                if rev:
                    existing_meta = rev.get("metadata", {})
                else:
                    existing_meta = item.get("metadata", {})
                break

        if existing_kref:
            total_runs = int(existing_meta.get("total_runs", 0)) + 1
            total_score = float(existing_meta.get("total_score", 0.0)) + score_val
            trust_score = round(total_score / total_runs, 3)

            recent = existing_meta.get("recent_outcomes", "")
            recent_list = [r for r in recent.split("|") if r] if recent else []
            recent_list.append(f"{outcome}:{task_summary[:50]}:{now}")
            recent_list = recent_list[-10:]

            meta = {
                "total_runs": total_runs,
                "total_score": round(total_score, 3),
                "trust_score": trust_score,
                "recent_outcomes": "|".join(recent_list),
                "last_run": now,
                "template_name": template_name,
            }
            if agent_id:
                meta["last_agent_id"] = agent_id
            await pool_client.create_revision(existing_kref, meta)
        else:
            meta = {
                "total_runs": 1,
                "total_score": score_val,
                "trust_score": score_val,
                "recent_outcomes": f"{outcome}:{task_summary[:50]}:{now}",
                "last_run": now,
                "template_name": template_name,
            }
            if agent_id:
                meta["last_agent_id"] = agent_id
            item = await pool_client.create_item(trust_space, template_name, "trust_record", meta)
            await pool_client.create_revision(item["kref"], meta)

        _log(f"Recorded {outcome} for '{template_name}' (trust: {meta['trust_score']})")
        return {
            "recorded": True,
            "template_name": template_name,
            "trust_score": meta["trust_score"],
            "total_runs": meta["total_runs"],
        }
    except Exception as e:
        _log(f"Trust recording failed: {e}")
        return {"error": f"Failed to record trust outcome: {e}"}


async def tool_get_agent_trust(args: dict[str, Any], pool_client: KumihoAgentPoolClient) -> dict[str, Any]:
    if not pool_client._available:
        return {"error": "Kumiho not available.", "agents": []}

    template_name = args.get("template_name")
    trust_space = f"/{harness_project()}/AgentTrust"

    try:
        items = await pool_client.list_items(trust_space)

        agents = []
        for item in items:
            name = item.get("item_name", "")
            if template_name and name != template_name:
                continue
            rev = await pool_client.get_latest_revision(item.get("kref", ""))
            meta = rev.get("metadata", {}) if rev else item.get("metadata", {})
            recent_raw = meta.get("recent_outcomes", "")
            recent = []
            for entry in recent_raw.split("|"):
                if entry:
                    parts = entry.split(":", 2)
                    if len(parts) >= 2:
                        recent.append({"outcome": parts[0], "task": parts[1], "when": parts[2] if len(parts) > 2 else ""})

            agents.append({
                "template_name": name,
                "trust_score": float(meta.get("trust_score", 0.0)),
                "total_runs": int(meta.get("total_runs", 0)),
                "last_run": meta.get("last_run", ""),
                "recent_outcomes": recent,
            })

        agents.sort(key=lambda a: -a["trust_score"])
        return {"agents": agents, "count": len(agents)}
    except Exception as e:
        _log(f"Trust retrieval failed: {e}")
        return {"error": f"Trust retrieval failed: {e}", "agents": []}
