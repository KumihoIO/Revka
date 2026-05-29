from __future__ import annotations

from types import SimpleNamespace

import pytest

import operator_mcp.patterns.refinement as refinement
import operator_mcp.workflow.executor as executor
from operator_mcp.workflow.schema import (
    AgentStepConfig,
    StepDef,
    StepResult,
    StepType,
    WorkflowState,
)


@pytest.mark.asyncio
async def test_exec_agent_extracts_final_output_yaml(monkeypatch, tmp_path):
    output = """Canon audit complete.

FINAL_OUTPUT:
verdict: ready
production_ready: true
notes:
  - canon consistent
"""

    async def fake_spawn_and_wait(*_args, **_kwargs):
        agent = SimpleNamespace(id="agent-1", status="completed")
        return agent, output

    def fake_get_agent_output(agent_id):
        assert agent_id == "agent-1"
        return output, []

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(executor, "workspace_dir", lambda: str(tmp_path / "workspace"))
    monkeypatch.setattr(refinement, "_spawn_and_wait", fake_spawn_and_wait)
    monkeypatch.setattr(refinement, "_get_agent_output", fake_get_agent_output)

    step = StepDef(
        id="final_canon_auditor",
        type=StepType.AGENT,
        agent=AgentStepConfig(prompt="audit canon"),
    )
    state = WorkflowState(
        workflow_name="manghan-developer-episode-factory",
        run_id="6094f99f-c1be-4896-bd50-b9374e243e69",
    )

    result = await executor._exec_agent(step, state, cwd=str(tmp_path))

    assert result.status == "completed"
    assert result.output_data["verdict"] == "ready"
    assert result.output_data["production_ready"] is True
    assert result.output_data["notes"] == ["canon consistent"]
    assert "FINAL_OUTPUT" not in result.output_data


def test_extract_final_output_yaml_ignores_codex_token_footer():
    output = """FINAL_OUTPUT:
  verdict: "NEEDS_CHANGES"
  production_ready: false
  final_gate:
    volume_bundle_violations:
      - "투자자 축 조기 호출"
tokens used
28,088
"""

    parsed = executor._extract_structured_agent_output(output)

    assert parsed is not None
    assert parsed["verdict"] == "NEEDS_CHANGES"
    assert parsed["production_ready"] is False
    assert parsed["final_gate"]["volume_bundle_violations"] == ["투자자 축 조기 호출"]


def test_eval_names_recovers_final_output_fields_from_step_output():
    state = WorkflowState(workflow_name="manghan", run_id="r")
    state.step_results["final-canon-auditor"] = StepResult(
        step_id="final-canon-auditor",
        status="completed",
        output="""FINAL_OUTPUT:
  verdict: "NEEDS_CHANGES"
  production_ready: false
""",
        output_data={"template_name": "continuity-lorekeeper"},
    )

    names, aliases = executor._build_eval_names(state)

    assert aliases["final-canon-auditor"] == "final_canon_auditor"
    assert names["final_canon_auditor"]["output_data"]["production_ready"] is False
    assert names["final_canon_auditor"]["output_data"]["verdict"] == "NEEDS_CHANGES"
