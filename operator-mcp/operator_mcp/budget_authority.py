"""Unified budget authority helpers for Operator agent work."""
from __future__ import annotations

from typing import Any

from .gateway_client import ConstructGatewayClient


class BudgetGateError(RuntimeError):
    """Raised when gateway budget policy refuses agent work."""

    def __init__(self, response: dict[str, Any]) -> None:
        super().__init__(str(response.get("error", "Budget gate refused agent work")))
        self.response = response


async def check_agent_budget(
    gateway: ConstructGatewayClient | None = None,
) -> dict[str, Any] | None:
    """Return a structured error if gateway budget policy blocks agent work."""
    client = gateway or ConstructGatewayClient()
    try:
        summary = await client.get_cost_summary()
    except Exception as exc:
        return {
            "error": f"Budget check failed: {exc}",
            "code": "budget_check_failed",
            "retryable": True,
            "source": "gateway",
        }

    if summary is None:
        return {
            "error": "Gateway budget authority unavailable; refusing agent work without unified budget check",
            "code": "budget_authority_unavailable",
            "retryable": True,
            "source": "gateway",
        }

    budget = summary.get("budget", {}) or {}
    if budget.get("enabled") and budget.get("state") == "exceeded":
        return {
            "error": "Budget exceeded; refusing agent work",
            "code": "budget_exceeded",
            "retryable": False,
            "source": "gateway",
            "budget": budget,
            "daily_cost_usd": summary.get("daily_cost_usd", 0.0),
            "monthly_cost_usd": summary.get("monthly_cost_usd", 0.0),
        }

    return None


async def require_agent_budget(
    gateway: ConstructGatewayClient | None = None,
) -> None:
    """Raise BudgetGateError if gateway budget policy blocks agent work."""
    error = await check_agent_budget(gateway)
    if error:
        raise BudgetGateError(error)
