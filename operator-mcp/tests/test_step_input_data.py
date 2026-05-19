"""Tests for the run-view step-detail backend (Issue 1A).

Covers:
  - StepResult.input_data field round-trips through Pydantic
  - Each non-agent _exec_* handler captures input_data with the documented keys
  - output_data gains the new fields (matched_kref, exit_code, etc.)
  - Truncation flags fire correctly on capped stdout/stderr
  - persist_workflow_run sanitization (redact secrets, truncate, JSON cap)
  - extract_steps_from_metadata (via raw memory.py round-trip) preserves
    input_data + output_data on every step

The agent / pattern step types (agent, map_reduce, supervisor, group_chat,
handoff) are intentionally not covered — they have their own RunLog detail
and will be revisited in a follow-up PR.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import patch

import pytest

from operator_mcp.workflow.executor import (
    _exec_conditional,
    _exec_deprecate,
    _exec_for_each,
    _exec_goto,
    _exec_notify,
    _exec_output,
    _exec_python,
    _exec_resolve,
    _exec_shell,
    _exec_tag,
)
from operator_mcp.workflow.memory import (
    _PER_STEP_JSON_CAP,
    _PER_STRING_CAP,
    _prepare_for_persistence,
    _redact_for_persistence,
)
from operator_mcp.workflow.schema import (
    AgentStepConfig,
    ConditionalBranch,
    ConditionalStepConfig,
    DeprecateStepConfig,
    ForEachStepConfig,
    GotoStepConfig,
    NotifyStepConfig,
    OutputStepConfig,
    PythonStepConfig,
    ResolveStepConfig,
    ShellStepConfig,
    StepDef,
    StepResult,
    StepType,
    TagStepConfig,
    WorkflowDef,
    WorkflowState,
)


# ── helpers ─────────────────────────────────────────────────────────


def _state(inputs: dict | None = None, results: dict | None = None) -> WorkflowState:
    return WorkflowState(
        workflow_name="t",
        run_id="r",
        inputs=dict(inputs or {}),
        step_results=dict(results or {}),
    )


# ── schema round-trip ──────────────────────────────────────────────


class TestSchemaRoundTrip:
    def test_input_data_default_empty(self):
        r = StepResult(step_id="x")
        assert r.input_data == {}

    def test_input_data_populated(self):
        r = StepResult(step_id="x", input_data={"k": "v"}, output_data={"o": 1})
        assert r.input_data == {"k": "v"}
        assert r.output_data == {"o": 1}

    def test_round_trip_preserves_input_data(self):
        r = StepResult(
            step_id="x",
            input_data={"command": "echo hi", "timeout_secs": 60},
            output_data={"exit_code": 0, "stdout_truncated": False},
        )
        dumped = r.model_dump()
        r2 = StepResult.model_validate(dumped)
        assert r2.input_data == r.input_data
        assert r2.output_data == r.output_data

    def test_round_trip_via_json(self):
        r = StepResult(
            step_id="x",
            input_data={"a": "b", "n": 1, "list": ["x"]},
        )
        s = r.model_dump_json()
        r2 = StepResult.model_validate_json(s)
        assert r2.input_data == r.input_data


# ── _exec_shell ────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestShell:
    async def test_captures_command_and_exit_code(self, tmp_path):
        cfg = ShellStepConfig(command="echo hello", timeout=10)
        step = StepDef(id="s", type=StepType.SHELL, shell=cfg)
        result = await _exec_shell(step, _state(), str(tmp_path))
        assert result.status == "completed"
        assert result.input_data["command"] == "echo hello"
        assert result.input_data["timeout_secs"] == 10
        assert result.input_data["allow_failure"] is False
        assert result.input_data["cwd"] == str(tmp_path)
        assert result.output_data["exit_code"] == 0
        assert result.output_data["stdout_truncated"] is False
        assert result.output_data["stderr_truncated"] is False

    async def test_interpolated_command_in_input_data(self, tmp_path):
        cfg = ShellStepConfig(command="echo ${inputs.name}", timeout=10)
        step = StepDef(id="s", type=StepType.SHELL, shell=cfg)
        result = await _exec_shell(step, _state(inputs={"name": "world"}), str(tmp_path))
        assert result.input_data["command"] == "echo world"

    async def test_stdout_truncation_flag(self, tmp_path):
        # Print 5000 chars — exceeds the 4000 cap.
        cfg = ShellStepConfig(command="python3 -c \"print('x'*5000)\"", timeout=10)
        step = StepDef(id="s", type=StepType.SHELL, shell=cfg)
        result = await _exec_shell(step, _state(), str(tmp_path))
        assert result.output_data["stdout_truncated"] is True


# ── _exec_python ───────────────────────────────────────────────────


@pytest.mark.asyncio
class TestPython:
    async def test_captures_inline_code_preview(self, tmp_path):
        code = "import json,sys; json.dump({'ok': True}, sys.stdout)"
        cfg = PythonStepConfig(code=code, timeout=10)
        step = StepDef(id="p", type=StepType.PYTHON, python=cfg)
        result = await _exec_python(step, _state(), str(tmp_path))
        assert result.status == "completed"
        assert result.input_data["code_preview"] == code
        assert result.input_data["script_path"] == ""
        assert result.input_data["timeout_secs"] == 10
        assert result.input_data["args"] == {}
        assert result.output_data["exit_code"] == 0
        assert result.output_data["stdout_truncated"] is False

    async def test_captures_args_with_interpolation(self, tmp_path):
        cfg = PythonStepConfig(
            code="import json,sys; json.dump({'k': 1}, sys.stdout)",
            args={"who": "${inputs.name}"},
            timeout=10,
        )
        step = StepDef(id="p", type=StepType.PYTHON, python=cfg)
        result = await _exec_python(step, _state(inputs={"name": "ada"}), str(tmp_path))
        assert result.input_data["args"] == {"who": "ada"}

    async def test_truncates_huge_inline_code_preview(self, tmp_path):
        # Huge inline code should be capped to 500 chars in code_preview but
        # code_length carries the original size for diagnostics.
        big_code = "x = 1\n" * 200  # ~1200 chars
        cfg = PythonStepConfig(code=big_code, timeout=10, allow_failure=True)
        step = StepDef(id="p", type=StepType.PYTHON, python=cfg)
        result = await _exec_python(step, _state(), str(tmp_path))
        assert len(result.input_data["code_preview"]) <= 500
        assert result.input_data["code_length"] == len(big_code)


# ── _exec_resolve ──────────────────────────────────────────────────


@pytest.mark.asyncio
class TestResolve:
    async def test_captures_query_and_matched_fields_on_hit(self):
        async def fake_resolve(**kwargs):
            return {
                "kref": "kref:rev:abc",
                "item_kref": "kref:item:def",
                "name": "MyEntity",
                "metadata": {"foo": "bar"},
            }

        cfg = ResolveStepConfig(kind="report", tag="published", mode="latest")
        step = StepDef(id="r", type=StepType.RESOLVE, resolve=cfg)
        with patch("operator_mcp.workflow.memory.resolve_entity", fake_resolve):
            result = await _exec_resolve(step, _state())
        assert result.input_data == {
            "kind": "report",
            "tag": "published",
            "name_pattern": "",
            "space": "",
            "mode": "latest",
            "metadata_source": "revision",
            "fail_if_missing": True,
        }
        assert result.output_data["found"] is True
        assert result.output_data["matched_kref"] == "kref:item:def"
        assert result.output_data["matched_name"] == "MyEntity"
        assert result.output_data["metadata_source"] == "revision"

    async def test_passes_metadata_source_to_resolver(self):
        calls: dict[str, Any] = {}

        async def fake_resolve(**kwargs):
            calls.update(kwargs)
            return {
                "kref": "kref:rev:abc",
                "item_kref": "kref:item:def",
                "name": "MyEntity",
                "metadata": {"foo": "bar"},
                "metadata_source": kwargs["metadata_source"],
            }

        cfg = ResolveStepConfig(kind="report", metadata_source="item")
        step = StepDef(id="r", type=StepType.RESOLVE, resolve=cfg)
        with patch("operator_mcp.workflow.memory.resolve_entity", fake_resolve):
            result = await _exec_resolve(step, _state())

        assert calls["metadata_source"] == "item"
        assert result.input_data["metadata_source"] == "item"
        assert result.output_data["metadata_source"] == "item"

    async def test_captures_query_on_miss(self):
        async def fake_resolve(**kwargs):
            return None

        cfg = ResolveStepConfig(kind="missing", tag="x", fail_if_missing=False)
        step = StepDef(id="r", type=StepType.RESOLVE, resolve=cfg)
        with patch("operator_mcp.workflow.memory.resolve_entity", fake_resolve):
            result = await _exec_resolve(step, _state())
        assert result.status == "completed"
        assert result.input_data["kind"] == "missing"
        assert result.output_data["found"] is False
        assert "matched_kref" not in result.output_data

    async def test_blank_kind_is_optional_when_missing_allowed(self):
        cfg = ResolveStepConfig(kind="", tag="x", fail_if_missing=False)
        step = StepDef(id="r", type=StepType.RESOLVE, resolve=cfg)
        result = await _exec_resolve(step, _state())
        assert result.status == "completed"
        assert result.input_data["kind"] == ""
        assert result.output_data == {"found": False}

    async def test_blank_kind_still_fails_when_required(self):
        cfg = ResolveStepConfig(kind="", tag="x", fail_if_missing=True)
        step = StepDef(id="r", type=StepType.RESOLVE, resolve=cfg)
        result = await _exec_resolve(step, _state())
        assert result.status == "failed"
        assert result.input_data["kind"] == ""
        assert result.error == "resolve step requires 'kind'"

    async def test_interpolates_space_expression(self):
        calls: dict[str, Any] = {}

        async def fake_resolve(**kwargs):
            calls.update(kwargs)
            return None

        cfg = ResolveStepConfig(
            kind="report",
            tag="published",
            space="Construct/${{ lower(inputs.team) }}/${inputs.suffix}",
            fail_if_missing=False,
        )
        step = StepDef(id="r", type=StepType.RESOLVE, resolve=cfg)
        with patch("operator_mcp.workflow.memory.resolve_entity", fake_resolve):
            result = await _exec_resolve(step, _state(inputs={"team": "OPS", "suffix": "Inbox"}))

        assert result.status == "completed"
        assert result.input_data["space"] == "Construct/ops/Inbox"
        assert calls["space"] == "Construct/ops/Inbox"


# ── _exec_output ───────────────────────────────────────────────────


@pytest.mark.asyncio
class TestOutput:
    async def test_captures_template_preview_no_entity(self):
        cfg = OutputStepConfig(format="markdown", template="hello ${inputs.name}")
        step = StepDef(id="o", type=StepType.OUTPUT, output=cfg)
        result = await _exec_output(step, _state(inputs={"name": "world"}))
        assert result.status == "completed"
        assert "hello world" in result.output
        assert result.input_data["format"] == "markdown"
        assert result.input_data["template_preview"] == "hello ${inputs.name}"
        assert result.input_data["entity_kind"] == ""
        assert result.input_data["metadata_target"] == "item"
        assert result.output_data["entity_registered"] is False
        assert result.output_data["entity_metadata_target"] == "item"

    async def test_caps_template_preview_at_500_chars(self):
        big = "X" * 1000
        cfg = OutputStepConfig(format="text", template=big)
        step = StepDef(id="o", type=StepType.OUTPUT, output=cfg)
        result = await _exec_output(step, _state())
        assert len(result.input_data["template_preview"]) == 500
        assert result.input_data["template_length"] == 1000

    async def test_entity_output_surfaces_attached_artifact(self):
        cfg = OutputStepConfig(
            format="markdown",
            template="# ${inputs.title}",
            entity_name="report-${inputs.title}",
            entity_kind="report",
        )
        step = StepDef(id="publish", type=StepType.OUTPUT, output=cfg)

        async def fake_publish_workflow_entity(**kwargs):
            assert kwargs["content"] == "# Q1"
            assert kwargs["content_format"] == "markdown"
            assert kwargs["metadata_target"] == "item"
            return {
                "item_kref": "kref://Construct/WorkflowOutputs/report.report",
                "revision_kref": "kref://Construct/WorkflowOutputs/report.report?r=7",
                "artifact_path": "/tmp/publish.md",
                "artifact_kref": "kref://Construct/WorkflowOutputs/report.report?r=7#a1",
                "artifact_attached": True,
                "artifact_error": "",
                "tag_applied": True,
                "tag_error": "",
                "metadata_target": "item",
            }

        with patch(
            "operator_mcp.workflow.memory.publish_workflow_entity",
            fake_publish_workflow_entity,
        ):
            result = await _exec_output(step, _state(inputs={"title": "Q1"}))

        assert result.status == "completed"
        assert result.output_data["entity_registered"] is True
        assert result.output_data["entity_artifact_attached"] is True
        assert result.output_data["entity_metadata_target"] == "item"
        assert result.output_data["artifact_path"] == "/tmp/publish.md"
        assert result.output_data["entity_artifact_kref"].endswith("#a1")

    async def test_entity_output_passes_metadata_target(self):
        cfg = OutputStepConfig(
            format="markdown",
            template="# report",
            entity_name="report",
            entity_kind="report",
            metadata_target="revision",
            entity_metadata={"topic": "Q1"},
        )
        step = StepDef(id="publish", type=StepType.OUTPUT, output=cfg)

        async def fake_publish_workflow_entity(**kwargs):
            assert kwargs["metadata_target"] == "revision"
            return {
                "item_kref": "kref://Construct/WorkflowOutputs/report.report",
                "revision_kref": "kref://Construct/WorkflowOutputs/report.report?r=7",
                "artifact_path": "/tmp/publish.md",
                "artifact_kref": "kref://Construct/WorkflowOutputs/report.report?r=7#a1",
                "artifact_attached": True,
                "artifact_error": "",
                "tag_applied": True,
                "tag_error": "",
                "metadata_target": "revision",
            }

        with patch(
            "operator_mcp.workflow.memory.publish_workflow_entity",
            fake_publish_workflow_entity,
        ):
            result = await _exec_output(step, _state())

        assert result.status == "completed"
        assert result.input_data["metadata_target"] == "revision"
        assert result.output_data["entity_metadata_target"] == "revision"

    async def test_entity_output_fails_when_artifact_attach_fails(self):
        cfg = OutputStepConfig(
            format="markdown",
            template="# report",
            entity_name="report",
            entity_kind="report",
        )
        step = StepDef(id="publish", type=StepType.OUTPUT, output=cfg)

        async def fake_publish_workflow_entity(**_kwargs):
            return {
                "item_kref": "kref://Construct/WorkflowOutputs/report.report",
                "revision_kref": "kref://Construct/WorkflowOutputs/report.report?r=7",
                "artifact_path": "/tmp/publish.md",
                "artifact_kref": "",
                "artifact_attached": False,
                "artifact_error": "revision already published",
                "tag_applied": False,
                "tag_error": "artifact attach failed; refusing to tag revision",
            }

        with patch(
            "operator_mcp.workflow.memory.publish_workflow_entity",
            fake_publish_workflow_entity,
        ):
            result = await _exec_output(step, _state())

        assert result.status == "failed"
        assert result.error == "revision already published"
        assert result.output_data["entity_registered"] is True
        assert result.output_data["entity_artifact_attached"] is False
        assert result.output_data["entity_tag_applied"] is False
        assert result.output_data["entity_tag_error"].startswith("artifact attach failed")


# ── _exec_notify ───────────────────────────────────────────────────


@pytest.mark.asyncio
class TestNotify:
    async def test_captures_title_message_channels(self):
        cfg = NotifyStepConfig(title="Hi", message="${inputs.body}", channels=["dashboard"])
        step = StepDef(id="n", type=StepType.NOTIFY, notify=cfg)
        # Don't actually push to gateway — patch the client to be unavailable.
        with patch("operator_mcp.gateway_client.ConstructGatewayClient") as gw:
            gw.return_value._available = False
            result = await _exec_notify(step, _state(inputs={"body": "msg!"}))
        assert result.status == "completed"
        assert result.input_data == {
            "title": "Hi",
            "message": "msg!",
            "channels": ["dashboard"],
        }


# ── _exec_conditional ──────────────────────────────────────────────


class TestConditional:
    def test_captures_matched_branch_index_and_condition(self):
        cfg = ConditionalStepConfig(branches=[
            ConditionalBranch(condition="default", goto="end", value="'matched'"),
        ])
        step = StepDef(id="g", type=StepType.CONDITIONAL, conditional=cfg)
        result = _exec_conditional(step, _state())
        assert result.input_data["branch_count"] == 1
        assert result.input_data["matched_branch_index"] == 0
        assert result.input_data["matched_condition"] == "default"
        assert result.input_data["matched_value_expr"] == "'matched'"
        assert result.input_data["matched_branch_label"] == "default"
        assert result.output_data["matched_branch_index"] == 0
        assert result.output_data["matched_branch_label"] == "default"
        assert result.output_data["matched_condition"] == "default"
        assert result.output_data["matched_value_expr"] == "'matched'"
        assert result.output_data["matched_output"] == "matched"
        assert result.output_data["matched_goto"] == "end"


# ── _exec_for_each ─────────────────────────────────────────────────


@pytest.mark.asyncio
class TestForEach:
    async def test_single_item_captures_input_data(self):
        # Use a single explicit item — for_each rejects fully empty configs
        # (range and items both empty) at the executor level.
        cfg = ForEachStepConfig(items=["only"], variable="ep", steps=["sub"], max_iterations=20)
        step = StepDef(id="fe", type=StepType.FOR_EACH, for_each=cfg)
        sub = StepDef(id="sub", type=StepType.OUTPUT, output=OutputStepConfig(template="x"))
        wf = WorkflowDef(name="t", steps=[step, sub], checkpoint=False)
        result = await _exec_for_each(step, _state(), "/tmp", wf)
        assert result.input_data["items_count"] == 1
        assert result.input_data["variable"] == "ep"
        assert result.input_data["items_preview"] == ["only"]
        assert result.output_data["iterations_completed"] == 1

    async def test_captures_items_preview_capped_at_5(self):
        cfg = ForEachStepConfig(
            range="1..10",
            variable="i",
            steps=["sub"],
            max_iterations=20,
        )
        step = StepDef(id="fe", type=StepType.FOR_EACH, for_each=cfg)
        sub = StepDef(id="sub", type=StepType.OUTPUT, output=OutputStepConfig(template="${for_each.i}"))
        wf = WorkflowDef(name="t", steps=[step, sub], checkpoint=False)
        result = await _exec_for_each(step, _state(), "/tmp", wf)
        assert result.input_data["items_count"] == 10
        assert len(result.input_data["items_preview"]) == 5
        assert result.input_data["items_preview"] == ["1", "2", "3", "4", "5"]

    async def test_agent_artifacts_are_iteration_qualified(self, tmp_path, monkeypatch):
        """Agent steps inside for_each must not overwrite the same markdown file."""
        from operator_mcp.agent_state import ManagedAgent
        from operator_mcp.patterns import refinement

        monkeypatch.setenv("HOME", str(tmp_path))

        calls: list[tuple[str, str]] = []

        async def fake_spawn_and_wait(agent_type, title, cwd, prompt, **_kwargs):
            iter_num = len(calls) + 1
            calls.append((title, prompt))
            agent = ManagedAgent(
                id=f"agent-{iter_num}",
                agent_type=agent_type,
                title=title,
                cwd=cwd,
                status="completed",
            )
            return agent, f"# draft {iter_num}\n"

        monkeypatch.setattr(refinement, "_spawn_and_wait", fake_spawn_and_wait)
        monkeypatch.setattr(refinement, "_get_agent_output", lambda _agent_id: ("", []))

        cfg = ForEachStepConfig(items=["a", "b"], variable="item", steps=["draft"])
        loop = StepDef(id="loop", type=StepType.FOR_EACH, for_each=cfg)
        draft = StepDef(
            id="draft",
            type=StepType.AGENT,
            agent=AgentStepConfig(
                agent_type="codex",
                role="writer",
                prompt="write ${for_each.item}",
                max_turns=1,
                tools="none",
            ),
        )
        wf = WorkflowDef(name="t", steps=[loop, draft], checkpoint=False)
        state = _state()

        result = await _exec_for_each(loop, state, str(tmp_path), wf)

        assert result.status == "completed"
        art_dir = tmp_path / ".construct" / "artifacts" / "t" / "r"
        iter_1 = art_dir / "draft__iter_1.md"
        iter_2 = art_dir / "draft__iter_2.md"
        assert iter_1.read_text(encoding="utf-8") == "# draft 1\n"
        assert iter_2.read_text(encoding="utf-8") == "# draft 2\n"
        assert not (art_dir / "draft.md").exists()
        assert state.step_results["draft__iter_1"].output_data["artifact_path"] == str(iter_1)
        assert state.step_results["draft__iter_2"].output_data["artifact_path"] == str(iter_2)
        assert state.step_results["draft__iter_1"].step_id == "draft__iter_1"
        assert state.step_results["draft__iter_2"].step_id == "draft__iter_2"
        # The base alias intentionally points at the latest iteration for
        # existing `${draft.output}` workflows, but its artifact path must be
        # the latest isolated file rather than a shared `draft.md`.
        assert state.step_results["draft"].step_id == "draft"
        assert state.step_results["draft"].agent_id == "agent-2"
        assert state.step_results["draft"].output_data["artifact_path"] == str(iter_2)

    async def test_output_entity_artifacts_are_iteration_qualified(self, tmp_path):
        """Output steps inside for_each must publish distinct files per iteration."""
        calls: list[dict[str, Any]] = []

        async def fake_publish_workflow_entity(**kwargs):
            calls.append(kwargs)
            artifact_dir = tmp_path / ".construct" / "artifacts" / kwargs["workflow_name"] / kwargs["run_id"]
            artifact_dir.mkdir(parents=True, exist_ok=True)
            artifact_path = artifact_dir / f"{kwargs['step_id']}.md"
            artifact_path.write_text(kwargs["content"], encoding="utf-8")
            return {
                "item_kref": f"kref://item/{kwargs['step_id']}",
                "revision_kref": f"kref://rev/{kwargs['step_id']}",
                "artifact_path": str(artifact_path),
                "artifact_kref": f"kref://rev/{kwargs['step_id']}#artifact",
                "artifact_attached": True,
                "artifact_error": "",
                "tag_applied": True,
                "tag_error": "",
                "metadata_target": "item",
            }

        cfg = ForEachStepConfig(items=["alpha", "beta"], variable="episode", steps=["emit"])
        loop = StepDef(id="loop", type=StepType.FOR_EACH, for_each=cfg)
        emit = StepDef(
            id="emit",
            type=StepType.OUTPUT,
            output=OutputStepConfig(
                format="markdown",
                template="# ${for_each.episode}\n",
                entity_name="episode-${for_each.iteration}",
                entity_kind="episode",
                entity_tag="ready",
            ),
        )
        wf = WorkflowDef(name="t", steps=[loop, emit], checkpoint=False)
        state = _state()

        with patch(
            "operator_mcp.workflow.memory.publish_workflow_entity",
            fake_publish_workflow_entity,
        ):
            result = await _exec_for_each(loop, state, str(tmp_path), wf)

        assert result.status == "completed"
        assert [call["step_id"] for call in calls] == ["emit__iter_1", "emit__iter_2"]
        art_dir = tmp_path / ".construct" / "artifacts" / "t" / "r"
        iter_1 = art_dir / "emit__iter_1.md"
        iter_2 = art_dir / "emit__iter_2.md"
        assert iter_1.read_text(encoding="utf-8") == "# alpha\n"
        assert iter_2.read_text(encoding="utf-8") == "# beta\n"
        assert not (art_dir / "emit.md").exists()
        assert state.step_results["emit__iter_1"].output_data["artifact_path"] == str(iter_1)
        assert state.step_results["emit__iter_2"].output_data["artifact_path"] == str(iter_2)
        assert state.step_results["emit"].output_data["artifact_path"] == str(iter_2)


# ── _exec_goto ─────────────────────────────────────────────────────


class TestGoto:
    def test_captures_target_max_and_iteration(self):
        cfg = GotoStepConfig(target="refine", max_iterations=3, condition="True")
        step = StepDef(id="g", type=StepType.GOTO, goto=cfg)
        s = _state()
        s.iteration_counts["g"] = 1
        result = _exec_goto(step, s)
        assert result.status == "completed"
        assert result.input_data["target"] == "refine"
        assert result.input_data["max_iterations"] == 3
        assert result.input_data["current_iteration"] == 1
        assert result.input_data["condition"] == "True"


# ── _exec_tag / _exec_deprecate ────────────────────────────────────


@pytest.mark.asyncio
class TestTagDeprecate:
    async def test_tag_captures_kref_and_marks_tagged(self):
        async def fake_tag(item_kref, tag, untag=""):
            return {"revision_kref": "kref:rev:1", "new_tag": tag}

        cfg = TagStepConfig(item_kref="kref:item:1", tag="released", untag="staging")
        step = StepDef(id="t", type=StepType.TAG, tag_step=cfg)
        with patch("operator_mcp.workflow.memory.tag_entity", fake_tag):
            result = await _exec_tag(step, _state())
        assert result.input_data["kref"] == "kref:item:1"
        assert result.input_data["tag"] == "released"
        assert result.input_data["previous_tag"] == "staging"
        assert result.output_data["tagged"] is True
        assert result.output_data["previous_tag"] == "staging"

    async def test_deprecate_records_timestamp(self):
        async def fake_dep(item_kref, reason=""):
            return {"item_kref": item_kref, "deprecated": "true"}

        cfg = DeprecateStepConfig(item_kref="kref:item:1", reason="superseded")
        step = StepDef(id="d", type=StepType.DEPRECATE, deprecate_step=cfg)
        with patch("operator_mcp.workflow.memory.deprecate_entity", fake_dep):
            result = await _exec_deprecate(step, _state())
        assert result.input_data == {"kref": "kref:item:1", "reason": "superseded"}
        assert "deprecated_at" in result.output_data
        # ISO 8601 with timezone — sanity check
        assert "T" in result.output_data["deprecated_at"]


# ── persistence sanitization ───────────────────────────────────────


class TestPersistenceSanitization:
    def test_redacts_password_in_string(self):
        s = "curl -X POST -H 'authorization: Bearer abc123' https://x"
        out = _redact_for_persistence(s)
        assert "Bearer abc123" not in out
        assert "***" in out

    def test_redacts_password_assignment(self):
        s = "DATABASE_URL=postgres://u:password=hunter2@host/db"
        out = _redact_for_persistence(s)
        assert "hunter2" not in out

    def test_redacts_token_in_dict(self):
        d = {"command": "deploy --token=secret_xyz prod"}
        out = _redact_for_persistence(d)
        assert "secret_xyz" not in out["command"]

    def test_truncates_long_strings(self):
        big = {"body": "X" * 10_000}
        prepared, truncated = _prepare_for_persistence(big)
        assert truncated is True
        assert len(prepared["body"]) == _PER_STRING_CAP

    def test_preserves_short_strings(self):
        d = {"k": "v"}
        prepared, truncated = _prepare_for_persistence(d)
        assert truncated is False
        assert prepared == {"k": "v"}

    def test_coerces_non_jsonable(self):
        class Custom:
            def __repr__(self) -> str:
                return "Custom()"

        d = {"obj": Custom()}
        prepared, _ = _prepare_for_persistence(d)
        # Repr fallback
        assert prepared["obj"] == "Custom()"


# ── persist_workflow_run round-trip ────────────────────────────────


class TestPersistRoundTrip:
    """Verify that a step entry built by persist_workflow_run carries
    input_data + output_data and parses back through the same JSON the
    Rust gateway reads.

    We exercise the inner build loop directly (not the full
    persist_workflow_run) so the test doesn't require a Kumiho SDK.
    """

    def _build_step_entry(self, sr: dict) -> str:
        """Re-implement the persist_workflow_run inner loop for one step.

        Kept in sync with operator_mcp/workflow/memory.py:persist_workflow_run.
        If the production code changes, this test will drift — that's the
        point: a forced touch when the persistence shape changes.
        """
        from operator_mcp.workflow.memory import (
            _PER_STEP_JSON_CAP,
            _prepare_for_persistence,
            _redact_for_persistence,
        )
        entry: dict[str, Any] = {"status": sr.get("status", "unknown")}
        od = sr.get("output_data", {}) or {}
        output = sr.get("output", "")
        if output:
            entry["output_preview"] = _redact_for_persistence(str(output))[:400]
        if sr.get("error"):
            entry["error"] = _redact_for_persistence(str(sr["error"]))[:1000]
        id_data, _ = _prepare_for_persistence(sr.get("input_data") or {})
        od_data, _ = _prepare_for_persistence(od)
        entry["input_data"] = id_data
        entry["output_data"] = od_data
        out = json.dumps(entry, default=str)
        assert len(out) <= _PER_STEP_JSON_CAP
        return out

    def test_shell_step_round_trip(self):
        sr = {
            "status": "completed",
            "output": "hello\n",
            "input_data": {"command": "echo hello", "timeout_secs": 60},
            "output_data": {"exit_code": 0, "stdout_truncated": False},
        }
        s = self._build_step_entry(sr)
        parsed = json.loads(s)
        assert parsed["input_data"]["command"] == "echo hello"
        assert parsed["output_data"]["exit_code"] == 0
        assert parsed["status"] == "completed"

    def test_secret_redacted_in_persisted_command(self):
        sr = {
            "status": "completed",
            "input_data": {"command": "curl -H 'authorization: Bearer SECRET123' x"},
            "output_data": {"exit_code": 0},
        }
        s = self._build_step_entry(sr)
        assert "SECRET123" not in s
        assert "***" in s

    def test_secret_redacted_in_persisted_error(self):
        sr = {
            "status": "failed",
            "error": "Failed: --api-key=secret123",
            "input_data": {},
            "output_data": {},
        }
        s = self._build_step_entry(sr)
        parsed = json.loads(s)
        assert "secret123" not in parsed["error"]
        assert "***" in parsed["error"]

    def test_secret_redacted_in_persisted_output_preview(self):
        sr = {
            "status": "completed",
            "output": "ok, token=hunter2 returned",
            "input_data": {},
            "output_data": {},
        }
        s = self._build_step_entry(sr)
        parsed = json.loads(s)
        assert "hunter2" not in parsed["output_preview"]
        assert "***" in parsed["output_preview"]

    def test_huge_string_truncated_in_persisted_output(self):
        sr = {
            "status": "completed",
            "input_data": {},
            "output_data": {"rendered": "X" * 50_000},
        }
        s = self._build_step_entry(sr)
        parsed = json.loads(s)
        # The rendered field got hard-capped at _PER_STRING_CAP
        assert len(parsed["output_data"]["rendered"]) <= _PER_STRING_CAP

    def test_diverse_step_types_preserve_input_data(self):
        # Simulate one of each non-agent step type's input_data shape.
        steps = {
            "shell": {"command": "ls"},
            "python": {"script_path": "kref_encode.py", "args": {"k": "v"}},
            "resolve": {"kind": "report", "tag": "published"},
            "output": {"format": "text", "template_preview": "x"},
            "notify": {"title": "T", "message": "M", "channels": ["dashboard"]},
            "email": {"to": ["a@b.c"], "subject": "S", "dry_run": True},
            "image": {"prompt": "a cat", "count": 1},
            "conditional": {"branch_count": 2, "matched_branch_index": 0},
            "for_each": {"variable": "i", "items_count": 3, "items_preview": [1, 2, 3]},
            "goto": {"target": "refine", "current_iteration": 1},
            "tag": {"kref": "k", "tag": "v"},
            "deprecate": {"kref": "k", "reason": "old"},
        }
        for sid, idata in steps.items():
            sr = {"status": "completed", "input_data": idata, "output_data": {}}
            s = self._build_step_entry(sr)
            parsed = json.loads(s)
            for key in idata:
                assert key in parsed["input_data"], f"step={sid} key={key} dropped"

    @pytest.mark.asyncio
    async def test_workflow_run_artifact_capture_dedupes_for_each_aliases(
        self,
        tmp_path,
        monkeypatch,
    ):
        from operator_mcp.workflow.memory import persist_workflow_run

        class FakeSDK:
            _available = True

            def __init__(self) -> None:
                self.create_artifact_calls: list[tuple[str, str, str]] = []

            async def ensure_space(self, _project: str, _space: str) -> None:
                return None

            async def list_items(self, _space_path: str) -> list[dict[str, Any]]:
                return []

            async def create_item(
                self,
                _space_path: str,
                name: str,
                kind: str,
                metadata: dict[str, Any] | None = None,
            ) -> dict[str, str]:
                return {"kref": f"kref://item/{name}", "kind": kind}

            async def create_revision(
                self,
                _item_kref: str,
                _metadata: dict[str, Any],
                tag: str | None = "latest",
            ) -> dict[str, str]:
                return {"kref": f"kref://rev/{tag or 'untagged'}"}

            async def create_artifact(
                self,
                revision_kref: str,
                name: str,
                location: str,
            ) -> dict[str, str]:
                self.create_artifact_calls.append((revision_kref, name, location))
                return {"kref": f"{revision_kref}#{name}"}

        iter_1 = tmp_path / "emit-episode__iter_1.md"
        iter_2 = tmp_path / "emit-episode__iter_2.md"
        iter_1.write_text("# one\n", encoding="utf-8")
        iter_2.write_text("# two\n", encoding="utf-8")

        sdk = FakeSDK()
        import operator_mcp.operator_mcp as op_mod
        monkeypatch.setattr(op_mod, "KUMIHO_SDK", sdk, raising=False)

        # Mirrors for_each state: iteration-qualified history entries plus
        # a base alias that points at the latest iteration for interpolation.
        step_results = {
            "emit-episode__iter_1": {
                "status": "completed",
                "output_data": {"artifact_path": str(iter_1)},
            },
            "emit-episode": {
                "status": "completed",
                "output_data": {"artifact_path": str(iter_2)},
            },
            "emit-episode__iter_2": {
                "status": "completed",
                "output_data": {"artifact_path": str(iter_2)},
            },
        }

        item_kref = await persist_workflow_run(
            workflow_name="room",
            run_id="run-1234567890",
            status="completed",
            inputs={},
            step_results=step_results,
        )

        assert item_kref is not None
        assert sdk.create_artifact_calls == [
            ("kref://rev/latest", "emit-episode__iter_1.md", str(iter_1)),
            ("kref://rev/latest", "emit-episode__iter_2.md", str(iter_2)),
        ]

    @pytest.mark.asyncio
    async def test_workflow_run_persist_times_out_hung_revision(
        self,
        monkeypatch,
    ):
        from operator_mcp.workflow.memory import persist_workflow_run

        class FakeSDK:
            _available = True

            async def ensure_space(self, _project: str, _space: str) -> None:
                return None

            async def list_items(self, _space_path: str) -> list[dict[str, Any]]:
                return []

            async def create_item(
                self,
                _space_path: str,
                name: str,
                kind: str,
                metadata: dict[str, Any] | None = None,
            ) -> dict[str, str]:
                return {"kref": f"kref://item/{name}", "kind": kind}

            async def create_revision(
                self,
                _item_kref: str,
                _metadata: dict[str, Any],
                tag: str | None = "latest",
            ) -> dict[str, str]:
                await asyncio.Event().wait()
                return {"kref": f"kref://rev/{tag or 'untagged'}"}

        import operator_mcp.operator_mcp as op_mod
        monkeypatch.setattr(op_mod, "KUMIHO_SDK", FakeSDK(), raising=False)
        monkeypatch.setenv("CONSTRUCT_WORKFLOW_MEMORY_TIMEOUT_SECS", "0.01")

        item_kref = await persist_workflow_run(
            workflow_name="room",
            run_id="run-1234567890",
            status="completed",
            inputs={},
            step_results={"draft": {"status": "completed"}},
        )

        assert item_kref is None

    @pytest.mark.asyncio
    async def test_workflow_run_artifact_attach_only_for_terminal_runs(
        self,
        tmp_path,
        monkeypatch,
    ):
        from operator_mcp.workflow.memory import persist_workflow_run

        class FakeSDK:
            _available = True

            def __init__(self) -> None:
                self.create_artifact_calls: list[tuple[str, str, str]] = []

            async def ensure_space(self, _project: str, _space: str) -> None:
                return None

            async def list_items(self, _space_path: str) -> list[dict[str, Any]]:
                return []

            async def create_item(
                self,
                _space_path: str,
                name: str,
                kind: str,
                metadata: dict[str, Any] | None = None,
            ) -> dict[str, str]:
                return {"kref": f"kref://item/{name}", "kind": kind}

            async def create_revision(
                self,
                _item_kref: str,
                _metadata: dict[str, Any],
                tag: str | None = "latest",
            ) -> dict[str, str]:
                return {"kref": f"kref://rev/{tag or 'untagged'}"}

            async def create_artifact(
                self,
                revision_kref: str,
                name: str,
                location: str,
            ) -> dict[str, str]:
                self.create_artifact_calls.append((revision_kref, name, location))
                return {"kref": f"{revision_kref}#{name}"}

        artifact = tmp_path / "draft.md"
        artifact.write_text("# draft\n", encoding="utf-8")

        sdk = FakeSDK()
        import operator_mcp.operator_mcp as op_mod
        monkeypatch.setattr(op_mod, "KUMIHO_SDK", sdk, raising=False)

        item_kref = await persist_workflow_run(
            workflow_name="room",
            run_id="run-1234567890",
            status="running",
            inputs={},
            step_results={
                "draft": {
                    "status": "completed",
                    "output_data": {"artifact_path": str(artifact)},
                },
            },
        )

        assert item_kref is not None
        assert sdk.create_artifact_calls == []
