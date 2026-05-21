"""System health dashboard — unified view of Construct operator state.

Combines: active workflows, recent runs, agent pool, cost, and system health
into a single tool response for situational awareness.
"""
from __future__ import annotations

from typing import Any

from .._log import _log


async def tool_system_dashboard(args: dict[str, Any]) -> dict[str, Any]:
    """Unified system health dashboard.

    Args:
        include_costs: Include cost breakdown (default true).
        include_agents: Include agent pool summary (default true).
        include_workflows: Include active/recent workflows (default true).
        include_health: Include system health checks (default true).
    """
    include_costs = args.get("include_costs", True)
    include_agents = args.get("include_agents", True)
    include_workflows = args.get("include_workflows", True)
    include_health = args.get("include_health", True)

    result: dict[str, Any] = {}

    # -- Active agents --
    if include_agents:
        from ..agent_state import AGENTS, POOL
        from .agents import agent_is_active
        active = [
            {
                "id": a.id,
                "title": a.title,
                "type": a.agent_type,
                "status": a.status,
                "alive": True,
            }
            for a in AGENTS.values()
            if agent_is_active(a)
        ]
        pool_templates = [
            {"name": t.name, "type": t.agent_type, "role": t.role}
            for t in POOL.list_all()
        ]

        # Kumiho pool agents (best-effort)
        kumiho_agent_count = 0
        try:
            from ..operator_mcp import KUMIHO_POOL
            if KUMIHO_POOL._available:
                kumiho_agents = await KUMIHO_POOL.list_agents()
                kumiho_agent_count = len(kumiho_agents)
        except Exception:
            pass

        result["agents"] = {
            "active": active,
            "active_count": len(active),
            "total_managed": len(AGENTS),
            "pool_templates": len(pool_templates),
            "kumiho_agents": kumiho_agent_count,
        }

    # -- Active and recent workflows --
    if include_workflows:
        from ..workflow.executor import ACTIVE_WORKFLOWS, workflow_progress_snapshot
        from ..workflow.schema import WorkflowStatus

        active_wfs = []
        for rid, state in list(ACTIVE_WORKFLOWS.items()):
            # Only report truly active workflows (defense-in-depth).
            # Terminal states should already be removed by executor cleanup,
            # but guard against any stragglers.
            if state.status in (
                WorkflowStatus.COMPLETED,
                WorkflowStatus.FAILED,
                WorkflowStatus.CANCELLED,
            ):
                continue
            progress = workflow_progress_snapshot(state)
            active_wfs.append({
                "run_id": rid,
                "workflow": state.workflow_name,
                "status": state.status.value,
                "current_step": state.current_step,
                "steps_completed": progress["top_level_steps_completed"],
                "steps_total": progress["top_level_steps_total"],
                "top_level_steps_completed": progress["top_level_steps_completed"],
                "top_level_steps_total": progress["top_level_steps_total"],
                "expanded_steps_completed": progress["expanded_steps_completed"],
                "started_at": state.started_at,
            })
            for key in ("current_loop", "current_iteration", "current_loop_total", "current_step_instance"):
                if key in progress:
                    active_wfs[-1][key] = progress[key]

        # Recent runs from Kumiho (best-effort)
        recent_runs: list[dict[str, Any]] = []
        try:
            from ..workflow.memory import recall_workflow_runs
            recent_runs = await recall_workflow_runs(limit=5)
        except Exception:
            pass

        # Available workflow definitions
        workflow_count = 0
        try:
            from ..workflow.loader import discover_workflows
            workflow_count = len(discover_workflows())
        except Exception:
            pass

        result["workflows"] = {
            "active": active_wfs,
            "active_count": len(active_wfs),
            "recent_runs": recent_runs,
            "available_definitions": workflow_count,
        }

    # -- Cost summary --
    if include_costs:
        try:
            from ..operator_mcp import CONSTRUCT_GW
            summary = await CONSTRUCT_GW.get_cost_summary()
            if summary is None:
                raise RuntimeError("gateway budget authority unavailable")
            result["costs"] = {
                "session_cost_usd": summary.get("session_cost_usd", 0),
                "daily_cost_usd": summary.get("daily_cost_usd", 0),
                "monthly_cost_usd": summary.get("monthly_cost_usd", 0),
                "total_tokens": summary.get("total_tokens", 0),
                "request_count": summary.get("request_count", 0),
                "by_model": summary.get("by_model", {}),
                "by_agent": summary.get("by_agent", {}),
                "by_source": summary.get("by_source", {}),
                "budget": summary.get("budget", {}),
                "source": "gateway",
            }
        except Exception:
            result["costs"] = {"error": "Gateway budget authority unavailable"}

    # -- System health --
    if include_health:
        health: dict[str, Any] = {}

        # Kumiho connectivity
        try:
            from ..operator_mcp import KUMIHO_SDK, KUMIHO_POOL, KUMIHO_TEAMS
            health["kumiho_sdk"] = "available" if KUMIHO_SDK._available else "unavailable"
            health["kumiho_pool"] = "available" if KUMIHO_POOL._available else "unavailable"
            health["kumiho_teams"] = "available" if KUMIHO_TEAMS._available else "unavailable"
        except Exception:
            health["kumiho"] = "import_error"

        # Sidecar connectivity
        try:
            from ..operator_mcp import SIDECAR
            health["sidecar"] = "available" if SIDECAR._available else "unavailable"
        except Exception:
            health["sidecar"] = "unknown"

        # Gateway connectivity
        try:
            from ..operator_mcp import CONSTRUCT_GW
            gw_status = await CONSTRUCT_GW.get_status()
            health["gateway"] = "connected" if gw_status else "unavailable"
            if gw_status:
                health["gateway_provider"] = gw_status.get("provider", "")
                health["gateway_model"] = gw_status.get("model", "")
        except Exception:
            health["gateway"] = "unknown"

        # Teams count
        try:
            teams = await KUMIHO_TEAMS.list_teams()
            health["teams_count"] = len(teams)
        except Exception:
            health["teams_count"] = 0

        # Workflow event listener
        try:
            from ..workflow.event_listener import get_event_listener
            listener = get_event_listener()
            if listener is not None:
                health["event_listener"] = listener.health()
            else:
                health["event_listener"] = "not_started"
        except Exception:
            health["event_listener"] = "unknown"

        result["health"] = health

    return result
