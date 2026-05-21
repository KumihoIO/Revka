"""Regression tests for workflow orchestration step output visibility."""
from __future__ import annotations

import inspect
from typing import Any

import pytest

from operator_mcp.workflow.executor import (
    _exec_group_chat,
    _exec_handoff,
    _exec_map_reduce,
    _exec_supervisor,
)
from operator_mcp.workflow.schema import (
    GroupChatStepConfig,
    HandoffStepConfig,
    MapReduceStepConfig,
    StepDef,
    StepResult,
    StepType,
    SupervisorStepConfig,
    WorkflowDef,
    WorkflowState,
)


def _state() -> WorkflowState:
    return WorkflowState(
        workflow_name="coordination-test",
        run_id="run-coordination",
        started_at="2026-05-21T00:00:00Z",
        steps_total=1,
    )


@pytest.mark.asyncio
async def test_group_chat_turns_are_persisted_for_live_run_detail(monkeypatch, tmp_path):
    persisted: list[dict[str, Any]] = []
    checkpoints: list[list[dict[str, Any]]] = []

    async def fake_persist_workflow_run(**kwargs):
        persisted.append(kwargs)
        return "kref://Construct/WorkflowRuns/coordination-test.workflow_run"

    def fake_save_checkpoint(state: WorkflowState) -> str:
        checkpoints.append(state.step_results["chat"].output_data["transcript"])
        return "/tmp/checkpoint.json"

    async def fake_tool_group_chat(args, on_turn=None):
        turns = [
            {"speaker": "Moderator", "content": "Opening frame", "round": 1},
            {"speaker": "Reviewer", "content": "Trade-off analysis", "round": 1},
        ]
        for idx in range(1, len(turns) + 1):
            if on_turn:
                maybe = on_turn(turns[:idx])
                if inspect.isawaitable(maybe):
                    await maybe
        return {
            "topic": args["topic"],
            "participants": args["participants"],
            "transcript": turns,
            "summary": "The group aligned on the narrower fix.",
            "consensus": "YES",
            "conclusion": "Ship the scoped patch.",
        }

    monkeypatch.setattr("operator_mcp.workflow.memory.persist_workflow_run", fake_persist_workflow_run)
    monkeypatch.setattr("operator_mcp.workflow.executor._save_checkpoint", fake_save_checkpoint)
    monkeypatch.setattr("operator_mcp.patterns.group_chat.tool_group_chat", fake_tool_group_chat)

    step = StepDef(
        id="chat",
        type=StepType.GROUP_CHAT,
        group_chat=GroupChatStepConfig(
            topic="Choose workflow UI behavior",
            participants=["reviewer-template", "coder-template"],
            max_rounds=2,
        ),
    )
    state = _state()
    wf = WorkflowDef(name="coordination-test", steps=[step], checkpoint=True)

    result = await _exec_group_chat(step, state, str(tmp_path), wf)

    assert result.status == "completed"
    assert "Summary: The group aligned" in result.output
    assert result.output_data["chat_events"][1]["speaker"] == "Reviewer"
    assert len(persisted) == 2
    assert persisted[-1]["status"] == "running"
    assert persisted[-1]["step_results"]["chat"]["status"] == "running"
    transcript = persisted[-1]["step_results"]["chat"]["output_data"]["transcript"]
    assert transcript[1]["content"] == "Trade-off analysis"
    assert checkpoints[-1][1]["speaker"] == "Reviewer"


@pytest.mark.asyncio
async def test_supervisor_without_final_summary_still_has_output(monkeypatch, tmp_path):
    async def fake_supervisor_run(_args):
        return {
            "task": "Investigate workflow output",
            "status": "max_iterations_reached",
            "total_iterations": 2,
            "final_summary": "",
            "iterations": [
                {"iteration": 1, "action": "DELEGATE", "subtask": "Inspect executor"},
                {"iteration": 2, "action": "REQUEST_INFO", "question": "Need a fixture"},
            ],
            "work_history": [
                "Delegated to reviewer: inspected executor output handling.",
                "Supervisor requested info: need a fixture.",
            ],
        }

    monkeypatch.setattr("operator_mcp.patterns.supervisor.tool_supervisor_run", fake_supervisor_run)
    step = StepDef(
        id="supervise",
        type=StepType.SUPERVISOR,
        supervisor=SupervisorStepConfig(task="Investigate workflow output", max_iterations=2),
    )

    result = await _exec_supervisor(step, _state(), str(tmp_path))

    assert result.status == "failed"
    assert "Supervisor max_iterations_reached after 2 iteration" in result.output
    assert "inspected executor output handling" in result.output
    assert result.input_data["task"] == "Investigate workflow output"


@pytest.mark.asyncio
async def test_map_reduce_all_mapper_failure_gets_human_readable_output(monkeypatch, tmp_path):
    async def fake_map_reduce(_args):
        return {
            "task": "Compare candidates",
            "status": "all_mappers_failed",
            "total_splits": 2,
            "successful_mappers": 0,
            "failed_mappers": 2,
            "skipped_mappers": 0,
            "mapper_results": [
                {"index": 0, "segment": "A", "status": "error", "output": "rate limit"},
                {"index": 1, "segment": "B", "status": "error", "output": "timeout"},
            ],
            "reducer_output": None,
        }

    monkeypatch.setattr("operator_mcp.patterns.map_reduce.tool_map_reduce", fake_map_reduce)
    step = StepDef(
        id="reduce",
        type=StepType.MAP_REDUCE,
        map_reduce=MapReduceStepConfig(
            task="Compare candidates",
            splits=["A", "B"],
        ),
    )

    result = await _exec_map_reduce(step, _state(), str(tmp_path))

    assert result.status == "failed"
    assert "Map-reduce all_mappers_failed: 0/2 mapper" in result.output
    assert "rate limit" in result.output
    assert result.input_data["splits"] == ["A", "B"]


@pytest.mark.asyncio
async def test_map_reduce_marks_failed_when_reducer_errors(monkeypatch, tmp_path):
    async def fake_map_reduce(_args):
        return {
            "task": "Synthesize candidates",
            "status": "completed",
            "total_splits": 2,
            "successful_mappers": 2,
            "failed_mappers": 0,
            "skipped_mappers": 0,
            "mapper_results": [
                {"index": 0, "segment": "A", "status": "completed", "output": "A ok"},
                {"index": 1, "segment": "B", "status": "completed", "output": "B ok"},
            ],
            "reducer": {"agent_id": "agent-r", "status": "error", "output": "", "files": []},
        }

    monkeypatch.setattr("operator_mcp.patterns.map_reduce.tool_map_reduce", fake_map_reduce)
    step = StepDef(
        id="reduce",
        type=StepType.MAP_REDUCE,
        map_reduce=MapReduceStepConfig(
            task="Synthesize candidates",
            splits=["A", "B"],
        ),
    )

    result = await _exec_map_reduce(step, _state(), str(tmp_path))

    assert result.status == "failed"
    assert result.error == "Reducer failed with status error"
    assert "Reducer ended with status error" in result.output


@pytest.mark.asyncio
async def test_handoff_empty_receiver_output_still_describes_result(monkeypatch, tmp_path):
    async def fake_handoff(_args):
        return {
            "from_agent_id": "agent-source",
            "to_agent_id": "agent-receiver",
            "to_agent_title": "receiver",
            "to_agent_status": "completed",
            "reason": "Continue implementation",
            "to_agent_output": "",
            "to_agent_files": ["web/src/file.tsx"],
        }

    monkeypatch.setattr("operator_mcp.patterns.handoff.tool_handoff_agent", fake_handoff)
    state = _state()
    state.step_results["source"] = StepResult(
        step_id="source",
        status="completed",
        agent_id="agent-source",
    )
    step = StepDef(
        id="handoff",
        type=StepType.HANDOFF,
        handoff=HandoffStepConfig(
            from_step="source",
            to_agent_type="coder-template",
            reason="Continue implementation",
        ),
    )

    result = await _exec_handoff(step, state, str(tmp_path))

    assert result.status == "completed"
    assert result.output == "Handoff to receiver ended with status completed.\nReason: Continue implementation"
    assert result.agent_id == "agent-receiver"
    assert result.files_touched == ["web/src/file.tsx"]
