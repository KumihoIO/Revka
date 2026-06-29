"""Tests for rejecting the non-canonical ``config:`` step block.

WORKFLOWS.md is the source of truth: per-step settings live under a typed
block named after the step ``type`` (``agent:``, ``human_approval:``, …). A
generic ``config:`` key is not a Revka format — unknown keys are dropped by
default, so a ``config:`` block silently loses every prompt/role/message and
the step runs empty while validation still reports the workflow as valid.

``StepDef.reject_config_block`` now fails loudly instead, pointing at the
canonical block name. This keeps the executor, the validator, and the
dashboard editor all aligned with the manual.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from operator_mcp.workflow.schema import StepDef, WorkflowDef


class TestConfigBlockRejection:
    def test_agent_config_block_rejected_suggests_agent(self):
        with pytest.raises(ValidationError) as exc:
            StepDef(id="research", type="agent", config={"prompt": "hi"})
        msg = str(exc.value)
        assert "'config:'" in msg
        assert "'agent:'" in msg
        assert "WORKFLOWS.md" in msg

    def test_human_approval_config_block_suggests_human_approval(self):
        with pytest.raises(ValidationError) as exc:
            StepDef(id="gate", type="human_approval", config={"message": "ok?"})
        assert "'human_approval:'" in str(exc.value)

    def test_action_alias_resolves_to_canonical_block(self):
        # `type: approve` is an action alias for human_approval.
        with pytest.raises(ValidationError) as exc:
            StepDef(id="gate", type="approve", config={"message": "ok?"})
        assert "'human_approval:'" in str(exc.value)

    def test_kumiho_config_block_suggests_kumiho(self):
        with pytest.raises(ValidationError) as exc:
            StepDef(id="ctx", type="kumiho_context", config={"project": "demo"})
        assert "'kumiho:'" in str(exc.value)

    def test_canonical_agent_block_still_loads(self):
        step = StepDef(id="research", type="agent", agent={"prompt": "do it"})
        assert step.agent is not None
        assert step.agent.prompt == "do it"

    def test_step_without_config_is_unaffected(self):
        # No config key, no typed block — executor synthesizes a default.
        step = StepDef(id="x", type="agent")
        assert step.agent is None

    def test_workflow_with_config_block_step_is_rejected(self):
        with pytest.raises(ValidationError) as exc:
            WorkflowDef(
                name="bad",
                steps=[{"id": "research", "type": "agent",
                        "config": {"prompt": "hi"}}],
            )
        assert "'config:'" in str(exc.value)
