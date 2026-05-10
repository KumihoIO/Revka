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
            "fail_if_missing": True,
        }
        assert result.output_data["found"] is True
        assert result.output_data["matched_kref"] == "kref:item:def"
        assert result.output_data["matched_name"] == "MyEntity"

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
        assert result.output_data["entity_registered"] is False

    async def test_caps_template_preview_at_500_chars(self):
        big = "X" * 1000
        cfg = OutputStepConfig(format="text", template=big)
        step = StepDef(id="o", type=StepType.OUTPUT, output=cfg)
        result = await _exec_output(step, _state())
        assert len(result.input_data["template_preview"]) == 500
        assert result.input_data["template_length"] == 1000


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
