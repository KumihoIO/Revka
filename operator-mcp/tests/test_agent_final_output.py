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


async def _run_agent_step(
    monkeypatch,
    tmp_path,
    output: str,
    cfg: AgentStepConfig,
    agent_status: str = "completed",
):
    captured: dict[str, str] = {}

    async def fake_spawn_and_wait(*args, **_kwargs):
        captured["prompt"] = args[3]
        agent = SimpleNamespace(id="agent-1", status=agent_status)
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
        agent=cfg,
    )
    state = WorkflowState(
        workflow_name="manghan-developer-episode-factory",
        run_id="6094f99f-c1be-4896-bd50-b9374e243e69",
    )

    return await executor._exec_agent(step, state, cwd=str(tmp_path)), captured


@pytest.mark.asyncio
async def test_exec_agent_extracts_final_output_yaml(monkeypatch, tmp_path):
    output = """Canon audit complete.

FINAL_OUTPUT:
verdict: ready
production_ready: true
notes:
  - canon consistent
"""

    result, _captured = await _run_agent_step(
        monkeypatch,
        tmp_path,
        output,
        AgentStepConfig(prompt="audit canon"),
    )

    assert result.status == "completed"
    assert result.output_data["verdict"] == "ready"
    assert result.output_data["production_ready"] is True
    assert result.output_data["notes"] == ["canon consistent"]
    assert "FINAL_OUTPUT" not in result.output_data


@pytest.mark.asyncio
async def test_exec_agent_injects_structured_output_instruction(monkeypatch, tmp_path):
    output = """FINAL_OUTPUT:
  verdict: APPROVED
  production_ready: true
"""

    result, captured = await _run_agent_step(
        monkeypatch,
        tmp_path,
        output,
        AgentStepConfig(
            prompt="audit canon",
            output_fields=["verdict", "production_ready"],
        ),
    )

    assert result.status == "completed"
    assert "STRUCTURED OUTPUT REQUIRED" in captured["prompt"]
    assert "FINAL_OUTPUT:" in captured["prompt"]
    assert "  verdict: <value>" in captured["prompt"]
    assert "  production_ready: <value>" in captured["prompt"]
    assert captured["prompt"].index("audit canon") < captured["prompt"].index(
        "STRUCTURED OUTPUT REQUIRED"
    )
    assert "STRUCTURED OUTPUT REQUIRED" in result.input_data["prompt_preview"]
    assert "production_ready: <value>" in result.input_data["prompt_preview"]


@pytest.mark.asyncio
async def test_exec_agent_prompt_preview_keeps_contract_after_long_prompt(
    monkeypatch, tmp_path
):
    output = """FINAL_OUTPUT:
  verdict: APPROVED
  production_ready: true
"""

    result, _captured = await _run_agent_step(
        monkeypatch,
        tmp_path,
        output,
        AgentStepConfig(
            prompt="audit canon\n" + ("long context\n" * 200),
            output_fields=["verdict", "production_ready"],
        ),
    )

    assert result.status == "completed"
    assert "... [prompt truncated before structured output] ..." in result.input_data[
        "prompt_preview"
    ]
    assert "STRUCTURED OUTPUT REQUIRED" in result.input_data["prompt_preview"]
    assert "production_ready: <value>" in result.input_data["prompt_preview"]


@pytest.mark.asyncio
async def test_exec_agent_fails_when_required_structured_field_missing(
    monkeypatch, tmp_path
):
    output = """FINAL_OUTPUT:
  verdict: APPROVED
"""

    result, _captured = await _run_agent_step(
        monkeypatch,
        tmp_path,
        output,
        AgentStepConfig(
            prompt="audit canon",
            output_fields=["verdict", "production_ready"],
        ),
    )

    assert result.status == "failed"
    assert result.error == "structured_output_missing: production_ready"
    assert result.output_data["structured_output_missing"] == ["production_ready"]
    assert result.output_data["verdict"] == "APPROVED"


@pytest.mark.asyncio
async def test_exec_agent_failed_status_preserves_error_and_records_missing_fields(
    monkeypatch, tmp_path
):
    result, _captured = await _run_agent_step(
        monkeypatch,
        tmp_path,
        "agent provider failed",
        AgentStepConfig(
            prompt="audit canon",
            output_fields=["verdict", "production_ready"],
        ),
        agent_status="error",
    )

    assert result.status == "failed"
    assert result.error == "agent provider failed"
    assert result.output_data["structured_output_missing"] == [
        "verdict",
        "production_ready",
    ]


@pytest.mark.asyncio
async def test_exec_agent_without_output_fields_keeps_free_text_compatible(
    monkeypatch, tmp_path
):
    result, _captured = await _run_agent_step(
        monkeypatch,
        tmp_path,
        "Plain review summary.",
        AgentStepConfig(prompt="audit canon"),
    )

    assert result.status == "completed"
    assert "structured_output_missing" not in result.output_data


@pytest.mark.asyncio
async def test_exec_agent_accepts_fenced_json_for_required_fields(monkeypatch, tmp_path):
    output = """Review complete.

```json
{"verdict": "APPROVED", "production_ready": true}
```
"""

    result, _captured = await _run_agent_step(
        monkeypatch,
        tmp_path,
        output,
        AgentStepConfig(
            prompt="audit canon",
            output_fields=["verdict", "production_ready"],
        ),
    )

    assert result.status == "completed"
    assert result.output_data["verdict"] == "APPROVED"
    assert result.output_data["production_ready"] is True


@pytest.mark.asyncio
async def test_exec_agent_accepts_direct_json_for_required_fields(monkeypatch, tmp_path):
    result, _captured = await _run_agent_step(
        monkeypatch,
        tmp_path,
        '{"verdict": "APPROVED", "production_ready": true}',
        AgentStepConfig(
            prompt="audit canon",
            output_fields=["verdict", "production_ready"],
        ),
    )

    assert result.status == "completed"
    assert result.output_data["verdict"] == "APPROVED"
    assert result.output_data["production_ready"] is True


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
