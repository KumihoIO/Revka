from __future__ import annotations

import pytest

from operator_mcp.budget_authority import BudgetGateError, check_agent_budget, require_agent_budget


class FakeGateway:
    def __init__(self, summary):
        self.summary = summary

    async def get_cost_summary(self):
        return self.summary


@pytest.mark.asyncio
async def test_agent_budget_allows_ok_gateway_summary():
    error = await check_agent_budget(FakeGateway({
        "budget": {"enabled": True, "state": "ok"},
        "daily_cost_usd": 0.25,
        "monthly_cost_usd": 0.25,
    }))

    assert error is None


@pytest.mark.asyncio
async def test_agent_budget_fails_closed_when_gateway_unavailable():
    error = await check_agent_budget(FakeGateway(None))

    assert error is not None
    assert error["code"] == "budget_authority_unavailable"
    assert error["source"] == "gateway"


@pytest.mark.asyncio
async def test_agent_budget_blocks_exceeded_gateway_budget():
    error = await check_agent_budget(FakeGateway({
        "budget": {"enabled": True, "state": "exceeded"},
        "daily_cost_usd": 2.0,
        "monthly_cost_usd": 10.0,
    }))

    assert error is not None
    assert error["code"] == "budget_exceeded"
    assert error["retryable"] is False


@pytest.mark.asyncio
async def test_require_agent_budget_raises_structured_response():
    with pytest.raises(BudgetGateError) as excinfo:
        await require_agent_budget(FakeGateway(None))

    assert excinfo.value.response["code"] == "budget_authority_unavailable"
