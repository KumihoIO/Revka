"""Regression tests for workflow failures that happen before step execution."""
from __future__ import annotations

import pytest

from operator_mcp.tool_handlers.workflows import tool_get_workflow_status
from operator_mcp.workflow import executor, memory, recovery
from operator_mcp.workflow.schema import (
    AgentStepConfig,
    ShellStepConfig,
    StepDef,
    StepResult,
    StepType,
    WorkflowDef,
    WorkflowState,
    WorkflowStatus,
)


@pytest.fixture(autouse=True)
def _isolate_executor_state(monkeypatch, tmp_path):
    lock_dir = tmp_path / "workflow_locks"
    ckpt_dir = tmp_path / "workflow_checkpoints"
    lock_dir.mkdir(parents=True, exist_ok=True)
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    executor.ACTIVE_WORKFLOWS.clear()
    monkeypatch.setattr(executor, "_CHECKPOINT_DIR", str(ckpt_dir))
    monkeypatch.setattr(recovery, "_RUN_LOCK_DIR", str(lock_dir))
    yield
    executor.ACTIVE_WORKFLOWS.clear()


def _shell_step(step_id: str, command: str = "echo ok") -> StepDef:
    return StepDef(
        id=step_id,
        type=StepType.SHELL,
        shell=ShellStepConfig(command=command, timeout=10),
    )


@pytest.mark.asyncio
async def test_cost_guard_preflight_failure_is_persisted(monkeypatch, tmp_path):
    persisted: list[dict] = []

    async def fake_check_cost_guard(_max_cost_usd=None):
        return "Budget exceeded: daily configured limit reached"

    async def fake_persist_workflow_run(**kwargs):
        persisted.append(kwargs)
        return "kref://Revka/WorkflowRuns/cost-blocked.workflow_run"

    monkeypatch.setattr(executor, "_check_cost_guard", fake_check_cost_guard)
    monkeypatch.setattr(memory, "persist_workflow_run", fake_persist_workflow_run)

    sentinel = tmp_path / "should-not-run"
    wf = WorkflowDef(
        name="cost-blocked",
        steps=[_shell_step("write", f"touch {sentinel}")],
        checkpoint=False,
    )

    state = await executor.execute_workflow(
        wf,
        inputs={"topic": "ux"},
        cwd=str(tmp_path),
        run_id="cost-preflight-run",
        workflow_item_kref="kref://Revka/Workflows/cost-blocked.workflow",
        workflow_revision_kref="kref://Revka/Workflows/cost-blocked.workflow?r=7",
    )

    assert state.status == WorkflowStatus.FAILED
    assert state.error.startswith("Cost guard: Budget exceeded")
    assert not sentinel.exists()
    assert len(persisted) == 1
    assert persisted[0]["run_id"] == "cost-preflight-run"
    assert persisted[0]["status"] == "failed"
    assert persisted[0]["error"].startswith("Cost guard: Budget exceeded")
    assert persisted[0]["steps_total"] == 1
    assert (
        persisted[0]["workflow_item_kref"]
        == "kref://Revka/Workflows/cost-blocked.workflow"
    )
    assert (
        persisted[0]["workflow_revision_kref"]
        == "kref://Revka/Workflows/cost-blocked.workflow?r=7"
    )


@pytest.mark.asyncio
async def test_validation_preflight_failure_is_persisted(monkeypatch, tmp_path):
    persisted: list[dict] = []

    async def fake_persist_workflow_run(**kwargs):
        persisted.append(kwargs)
        return "kref://Revka/WorkflowRuns/invalid.workflow_run"

    monkeypatch.setattr(memory, "persist_workflow_run", fake_persist_workflow_run)

    wf = WorkflowDef(
        name="invalid-preflight",
        steps=[
            StepDef(
                id="blocked",
                type=StepType.SHELL,
                depends_on=["missing"],
                shell=ShellStepConfig(command="echo should-not-run", timeout=10),
            )
        ],
        checkpoint=False,
    )

    state = await executor.execute_workflow(
        wf,
        inputs={},
        cwd=str(tmp_path),
        run_id="validation-preflight-run",
    )

    assert state.status == WorkflowStatus.FAILED
    assert state.error.startswith("Validation failed:")
    assert len(persisted) == 1
    assert persisted[0]["run_id"] == "validation-preflight-run"
    assert persisted[0]["status"] == "failed"
    assert persisted[0]["steps_total"] == 1


@pytest.mark.asyncio
async def test_agent_required_tool_visibility_failure_is_persisted(monkeypatch, tmp_path):
    persisted: list[dict] = []

    async def fake_persist_workflow_run(**kwargs):
        persisted.append(kwargs)
        return "kref://Revka/WorkflowRuns/missing-tool.workflow_run"

    monkeypatch.setattr(memory, "persist_workflow_run", fake_persist_workflow_run)

    wf = WorkflowDef(
        name="missing-tool",
        steps=[
            StepDef(
                id="capture",
                type=StepType.AGENT,
                agent=AgentStepConfig(
                    agent_type="codex",
                    tools="none",
                    required_tools=["capture_skill"],
                    prompt="Use capture_skill to save the procedure.",
                ),
            )
        ],
        checkpoint=False,
    )

    state = await executor.execute_workflow(
        wf,
        inputs={},
        cwd=str(tmp_path),
        run_id="missing-tool-run",
    )

    assert state.status == WorkflowStatus.FAILED
    assert state.error.startswith("Required tool visibility failed")
    assert "capture_skill" in state.error
    assert len(persisted) == 1
    assert persisted[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_agent_google_agentops_tools_visible_with_operator_tools(monkeypatch, tmp_path):
    from operator_mcp.agent_state import ManagedAgent
    from operator_mcp.patterns import refinement

    captured: dict[str, object] = {}

    async def fake_check_cost_guard(_max_cost_usd=None):
        return None

    async def fake_persist_workflow_run(**_kwargs):
        return "kref://Revka/WorkflowRuns/google-agentops-tools.workflow_run"

    async def fake_spawn_and_wait(
        agent_type,
        title,
        cwd,
        prompt,
        **kwargs,
    ):
        captured["include_memory"] = kwargs["include_memory"]
        captured["include_operator"] = kwargs["include_operator"]
        captured["include_google_agentops"] = kwargs.get("include_google_agentops")
        return (
            ManagedAgent(
                id="agent-1",
                agent_type=agent_type,
                title=title,
                cwd=cwd,
                status="completed",
            ),
            "used google agentops tools",
        )

    def fake_get_agent_output(_agent_id):
        return "used google agentops tools", []

    monkeypatch.setattr(executor, "_check_cost_guard", fake_check_cost_guard)
    monkeypatch.setattr(memory, "persist_workflow_run", fake_persist_workflow_run)
    monkeypatch.setattr(executor, "workspace_dir", lambda: str(tmp_path / "workspace"))
    monkeypatch.setattr(refinement, "_spawn_and_wait", fake_spawn_and_wait)
    monkeypatch.setattr(refinement, "_get_agent_output", fake_get_agent_output)

    required_tools = [
        "google_agents_cli",
        "a2a_discover",
        "a2a_send_task",
        "a2a_get_remote_task",
    ]
    wf = WorkflowDef(
        name="google-agentops-tools",
        steps=[
            StepDef(
                id="agentops",
                type=StepType.AGENT,
                agent=AgentStepConfig(
                    agent_type="codex",
                    tools="all",
                    required_tools=required_tools,
                    prompt="Use google_agents_cli and A2A tools as needed.",
                ),
            )
        ],
        checkpoint=False,
    )

    state = await executor.execute_workflow(
        wf,
        inputs={},
        cwd=str(tmp_path),
        run_id="google-agentops-tools-run",
    )

    assert state.status == WorkflowStatus.COMPLETED
    assert captured["include_memory"] is True
    assert captured["include_operator"] is True
    assert captured["include_google_agentops"] is False
    assert state.step_results["agentops"].input_data["required_tools"] == required_tools


@pytest.mark.asyncio
async def test_agent_google_agentops_tools_visible_with_narrow_tool_mode(monkeypatch, tmp_path):
    from operator_mcp.agent_state import ManagedAgent
    from operator_mcp.patterns import refinement

    captured: dict[str, object] = {}

    async def fake_check_cost_guard(_max_cost_usd=None):
        return None

    async def fake_persist_workflow_run(**_kwargs):
        return "kref://Revka/WorkflowRuns/google-agentops-narrow.workflow_run"

    async def fake_spawn_and_wait(
        agent_type,
        title,
        cwd,
        prompt,
        **kwargs,
    ):
        captured["include_memory"] = kwargs["include_memory"]
        captured["include_operator"] = kwargs["include_operator"]
        captured["include_google_agentops"] = kwargs.get("include_google_agentops")
        return (
            ManagedAgent(
                id="agent-1",
                agent_type=agent_type,
                title=title,
                cwd=cwd,
                status="completed",
            ),
            "used narrow google agentops tools",
        )

    def fake_get_agent_output(_agent_id):
        return "used narrow google agentops tools", []

    monkeypatch.setattr(executor, "_check_cost_guard", fake_check_cost_guard)
    monkeypatch.setattr(memory, "persist_workflow_run", fake_persist_workflow_run)
    monkeypatch.setattr(executor, "workspace_dir", lambda: str(tmp_path / "workspace"))
    monkeypatch.setattr(refinement, "_spawn_and_wait", fake_spawn_and_wait)
    monkeypatch.setattr(refinement, "_get_agent_output", fake_get_agent_output)

    required_tools = [
        "google_agents_cli",
        "a2a_discover",
        "a2a_send_task",
        "a2a_get_remote_task",
    ]
    wf = WorkflowDef(
        name="google-agentops-narrow",
        steps=[
            StepDef(
                id="agentops",
                type=StepType.AGENT,
                agent=AgentStepConfig(
                    agent_type="codex",
                    tools="google_agentops",
                    required_tools=required_tools,
                    prompt="Use google_agents_cli and A2A tools as needed.",
                ),
            )
        ],
        checkpoint=False,
    )

    state = await executor.execute_workflow(
        wf,
        inputs={},
        cwd=str(tmp_path),
        run_id="google-agentops-narrow-run",
    )

    assert state.status == WorkflowStatus.COMPLETED
    assert captured["include_memory"] is False
    assert captured["include_operator"] is False
    assert captured["include_google_agentops"] is True
    assert state.step_results["agentops"].input_data["tools"] == "google_agentops"
    assert state.step_results["agentops"].input_data["required_tools"] == required_tools


@pytest.mark.asyncio
async def test_agent_google_agents_cli_requires_operator_tools(monkeypatch, tmp_path):
    persisted: list[dict] = []

    async def fake_persist_workflow_run(**kwargs):
        persisted.append(kwargs)
        return "kref://Revka/WorkflowRuns/missing-google-cli.workflow_run"

    monkeypatch.setattr(memory, "persist_workflow_run", fake_persist_workflow_run)

    wf = WorkflowDef(
        name="missing-google-cli",
        steps=[
            StepDef(
                id="agentops",
                type=StepType.AGENT,
                agent=AgentStepConfig(
                    agent_type="codex",
                    tools="memory",
                    required_tools=["google_agents_cli"],
                    prompt="Use google_agents_cli to inspect the ADK project.",
                ),
            )
        ],
        checkpoint=False,
    )

    state = await executor.execute_workflow(
        wf,
        inputs={},
        cwd=str(tmp_path),
        run_id="missing-google-cli-run",
    )

    assert state.status == WorkflowStatus.FAILED
    assert state.error.startswith("Required tool visibility failed")
    assert "google_agents_cli" in state.error
    assert "agent.tools=memory" in state.error
    assert len(persisted) == 1
    assert persisted[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_agent_step_uses_workspace_run_sandbox(monkeypatch, tmp_path):
    from operator_mcp.agent_state import ManagedAgent
    from operator_mcp.patterns import refinement

    captured: dict[str, str] = {}
    workspace = tmp_path / "workspace"

    async def fake_check_cost_guard(_max_cost_usd=None):
        return None

    async def fake_persist_workflow_run(**_kwargs):
        return "kref://Revka/WorkflowRuns/sandbox.workflow_run"

    async def fake_spawn_and_wait(
        agent_type,
        title,
        cwd,
        prompt,
        **_kwargs,
    ):
        captured["cwd"] = cwd
        captured["prompt"] = prompt
        return (
            ManagedAgent(
                id="agent-1",
                agent_type=agent_type,
                title=title,
                cwd=cwd,
                status="completed",
            ),
            "done",
        )

    def fake_get_agent_output(_agent_id):
        return "done", []

    monkeypatch.setattr(executor, "_check_cost_guard", fake_check_cost_guard)
    monkeypatch.setattr(memory, "persist_workflow_run", fake_persist_workflow_run)
    monkeypatch.setattr(executor, "workspace_dir", lambda: str(workspace))
    monkeypatch.setattr(refinement, "_spawn_and_wait", fake_spawn_and_wait)
    monkeypatch.setattr(refinement, "_get_agent_output", fake_get_agent_output)

    wf = WorkflowDef(
        name="sandboxed-agent",
        steps=[
            StepDef(
                id="agent-step",
                type=StepType.AGENT,
                agent=AgentStepConfig(
                    agent_type="codex",
                    tools="none",
                    prompt="Return done.",
                ),
            )
        ],
        checkpoint=False,
    )

    state = await executor.execute_workflow(
        wf,
        inputs={},
        cwd=str(tmp_path),
        run_id="sandbox-run",
    )

    assert state.status == WorkflowStatus.COMPLETED
    assert captured["cwd"].startswith(str(workspace))
    assert "sandboxed-agent" in captured["cwd"]
    assert "sandbox-run" in captured["cwd"]
    assert "agent-step" in captured["cwd"]
    step_result = state.step_results["agent-step"]
    assert step_result.input_data["cwd"] == captured["cwd"]
    assert step_result.input_data["source_cwd"] == str(tmp_path)


@pytest.mark.asyncio
async def test_capture_required_step_missing_revision_kref_fails(monkeypatch, tmp_path):
    from operator_mcp.agent_state import ManagedAgent
    from operator_mcp.patterns import refinement

    async def fake_check_cost_guard(_max_cost_usd=None):
        return None

    async def fake_persist_workflow_run(**_kwargs):
        return "kref://Revka/WorkflowRuns/capture-required.workflow_run"

    async def fake_spawn_and_wait(agent_type, title, cwd, prompt, **_kwargs):
        return (
            ManagedAgent(
                id="agent-1",
                agent_type=agent_type,
                title=title,
                cwd=cwd,
                status="completed",
            ),
            "capture attempted",
        )

    def fake_get_agent_output(_agent_id):
        return "capture attempted", []

    monkeypatch.setattr(executor, "_check_cost_guard", fake_check_cost_guard)
    monkeypatch.setattr(memory, "persist_workflow_run", fake_persist_workflow_run)
    monkeypatch.setattr(executor, "workspace_dir", lambda: str(tmp_path / "workspace"))
    monkeypatch.setattr(refinement, "_spawn_and_wait", fake_spawn_and_wait)
    monkeypatch.setattr(refinement, "_get_agent_output", fake_get_agent_output)

    wf = WorkflowDef(
        name="capture-required",
        steps=[
            StepDef(
                id="capture",
                type=StepType.AGENT,
                agent=AgentStepConfig(
                    agent_type="codex",
                    tools="memory",
                    required_tools=["capture_skill"],
                    prompt="Use capture_skill to save the procedure.",
                ),
            )
        ],
        checkpoint=False,
    )

    state = await executor.execute_workflow(
        wf,
        inputs={},
        cwd=str(tmp_path),
        run_id="capture-required-run",
    )

    assert state.status == WorkflowStatus.FAILED
    result = state.step_results["capture"]
    assert result.status == "failed"
    assert result.error == "capture_required: revision_kref missing"
    assert result.output_data["capture_required"] is True


@pytest.mark.asyncio
async def test_live_workflow_status_includes_artifact_output_data():
    state = WorkflowState(
        workflow_name="artifact-run",
        run_id="artifact-run-id",
        status=WorkflowStatus.RUNNING,
        steps_total=1,
    )
    state.step_results["draft"] = StepResult(
        step_id="draft",
        status="completed",
        output="preview",
        input_data={"prompt": "write"},
        output_data={"artifact_path": "C:/tmp/out.md", "summary": "done"},
    )
    executor.ACTIVE_WORKFLOWS[state.run_id] = state

    status = await tool_get_workflow_status({
        "run_id": state.run_id,
        "include_outputs": True,
    })

    step = status["steps"]["draft"]
    assert step["artifact_path"] == "C:/tmp/out.md"
    assert step["input_data"]["prompt"] == "write"
    assert step["output_data"]["summary"] == "done"
