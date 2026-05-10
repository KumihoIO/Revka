"""Tests for the workflow `python:` step type.

Covers:
- Schema validation (must specify exactly one of script / code)
- Inline code path with JSON-on-stdout → output_data merge
- Script path resolution: cwd > builtins
- The bundled kref_encode.py builtin (round-trips encode/decode, with
  and without HMAC secret)
- Failure modes: missing script, timeout, non-zero exit, allow_failure
- Non-JSON stdout still completes (just empty output_data)
- Args interpolation (${inputs.x}) reaches the script
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

import pytest
from pydantic import ValidationError

from operator_mcp.workflow.executor import (
    _BUILTIN_PYTHON_STEPS_DIR,
    _exec_python,
    _interpolate_args,
)
from operator_mcp.workflow.schema import (
    PythonStepConfig,
    StepDef,
    StepResult,
    StepType,
    WorkflowState,
)


# ── helpers ─────────────────────────────────────────────────────────


def _make_state(inputs: dict | None = None, results: dict | None = None) -> WorkflowState:
    """Minimal WorkflowState for executor tests."""
    return WorkflowState(
        workflow_name="test-wf",
        run_id="test-run",
        inputs=dict(inputs or {}),
        step_results=dict(results or {}),
    )


def _step(cfg: PythonStepConfig, step_id: str = "py") -> StepDef:
    return StepDef(id=step_id, type=StepType.PYTHON, python=cfg)


# ── schema validation ──────────────────────────────────────────────


class TestSchema:
    def test_requires_script_or_code(self):
        with pytest.raises(ValidationError, match="exactly one"):
            PythonStepConfig()

    def test_rejects_both_script_and_code(self):
        with pytest.raises(ValidationError, match="exactly one"):
            PythonStepConfig(script="x.py", code="print(1)")

    def test_script_only_ok(self):
        cfg = PythonStepConfig(script="kref_encode.py")
        assert cfg.script == "kref_encode.py"
        assert cfg.code is None

    def test_code_only_ok(self):
        cfg = PythonStepConfig(code="import json,sys; json.dump({'ok': 1}, sys.stdout)")
        assert cfg.code is not None
        assert cfg.script is None


# ── _interpolate_args ──────────────────────────────────────────────


class TestInterpolateArgs:
    """Args dict gets ${...} expansion before being fed to the script.

    Without this, scripts couldn't reference workflow inputs / prior step
    outputs without doing the interpolation themselves on every site.
    """

    def test_string_value_interpolated(self):
        state = _make_state(inputs={"name": "Alice"})
        out = _interpolate_args({"greeting": "hello ${inputs.name}"}, state)
        assert out == {"greeting": "hello Alice"}

    def test_nested_dict_walked(self):
        state = _make_state(inputs={"x": "42"})
        out = _interpolate_args({"outer": {"inner": "${inputs.x}"}}, state)
        assert out == {"outer": {"inner": "42"}}

    def test_list_walked(self):
        state = _make_state(inputs={"a": "1"})
        out = _interpolate_args(["${inputs.a}", "static"], state)
        assert out == ["1", "static"]

    def test_non_string_passthrough(self):
        state = _make_state()
        out = _interpolate_args({"n": 7, "b": True, "x": None}, state)
        assert out == {"n": 7, "b": True, "x": None}


# ── inline code path ───────────────────────────────────────────────


@pytest.mark.asyncio
class TestInlineCode:
    """`code:` lets one-off transforms ride without a separate file."""

    async def test_simple_inline_emits_json(self, tmp_path):
        code = (
            "import json, sys\n"
            "payload = json.load(sys.stdin)\n"
            "json.dump({'echoed': payload['args']['v'], 'doubled': payload['args']['n']*2}, sys.stdout)\n"
        )
        cfg = PythonStepConfig(code=code, args={"v": "hi", "n": 5})
        result = await _exec_python(_step(cfg), _make_state(), str(tmp_path))

        assert result.status == "completed"
        assert result.output_data["echoed"] == "hi"
        assert result.output_data["doubled"] == 10
        assert result.output_data["exit_code"] == 0

    async def test_args_interpolation(self, tmp_path):
        code = "import json,sys; print(json.dumps(json.load(sys.stdin)['args']))"
        cfg = PythonStepConfig(code=code, args={"who": "${inputs.who}"})
        state = _make_state(inputs={"who": "world"})
        result = await _exec_python(_step(cfg), state, str(tmp_path))

        assert result.status == "completed"
        # The script echoed args back as JSON; output_data wraps non-dict
        # JSON under `result` (we got a dict though so it merges flat).
        # Either way, `who=world` should be reachable.
        assert "world" in result.output

    async def test_non_json_stdout_completes_with_empty_output_data(self, tmp_path):
        # Scripts that print plain text shouldn't fail — we just don't
        # populate output_data beyond exit_code.
        cfg = PythonStepConfig(code="print('plain text, not json')")
        result = await _exec_python(_step(cfg), _make_state(), str(tmp_path))

        assert result.status == "completed"
        assert "plain text" in result.output
        # Non-JSON stdout still gives us exit_code + truncation flags.
        # The flags now ride on every python step result so the run-view UI
        # can show "stdout truncated" when output gets capped.
        assert result.output_data["exit_code"] == 0
        assert result.output_data["stdout_truncated"] is False
        assert result.output_data["stderr_truncated"] is False
        assert "result" not in result.output_data
        # input_data should reflect the inline code path
        assert "print('plain text, not json')" in result.input_data["code_preview"]


# ── script path resolution ──────────────────────────────────────────


@pytest.mark.asyncio
class TestScriptResolution:
    async def test_resolves_from_cwd(self, tmp_path):
        script = tmp_path / "local.py"
        script.write_text(
            "import json,sys; json.dump({'from': 'cwd'}, sys.stdout)"
        )
        cfg = PythonStepConfig(script="local.py")
        result = await _exec_python(_step(cfg), _make_state(), str(tmp_path))
        assert result.status == "completed"
        assert result.output_data["from"] == "cwd"

    async def test_resolves_from_builtins(self, tmp_path):
        # kref_encode.py ships in the builtins dir — picked up by bare name
        # even when the workflow's cwd doesn't contain it.
        cfg = PythonStepConfig(
            script="kref_encode.py",
            args={"op": "encode", "kref": "kref://Test/x.item"},
        )
        result = await _exec_python(_step(cfg), _make_state(), str(tmp_path))
        assert result.status == "completed"
        assert "encoded" in result.output_data
        assert result.output_data["kref"] == "kref://Test/x.item"

    async def test_missing_script_fails_clearly(self, tmp_path):
        cfg = PythonStepConfig(script="does-not-exist.py")
        result = await _exec_python(_step(cfg), _make_state(), str(tmp_path))
        assert result.status == "failed"
        assert "not found" in result.error
        assert "does-not-exist.py" in result.error


# ── kref_encode.py — direct subprocess test ─────────────────────────


class TestKrefEncodeBuiltin:
    """The bundled script is a Python step's first dogfood — exercises
    encode/decode round-trip, HMAC verification, and error paths."""

    SCRIPT = os.path.join(_BUILTIN_PYTHON_STEPS_DIR, "kref_encode.py")

    def _run(self, args: dict) -> dict:
        # Standalone script — runs from any Python interpreter without
        # needing operator_mcp on sys.path.
        proc = subprocess.run(
            [sys.executable, self.SCRIPT],
            input=json.dumps({"args": args, "context": {}}),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0 and not proc.stdout.strip():
            raise AssertionError(
                f"kref_encode.py exited {proc.returncode}: {proc.stderr}"
            )
        return json.loads(proc.stdout)

    def test_encode_decode_roundtrip_no_secret(self):
        kref = "kref://Construct/Sessions/sess-1/Outcomes/x.outcome?r=1"
        enc = self._run({"op": "encode", "kref": kref})
        assert "encoded" in enc
        assert "/" not in enc["encoded"]  # url-safe, no padding
        assert "+" not in enc["encoded"]
        assert "=" not in enc["encoded"]

        dec = self._run({"op": "decode", "encoded": enc["encoded"]})
        assert dec["kref"] == kref
        # No secret → no integrity claim
        assert dec["verified"] is False

    def test_encode_decode_with_secret_verifies(self):
        kref = "kref://Construct/Outreach/contacts/acme.contact?r=2"
        enc = self._run({"op": "encode", "kref": kref, "secret": "s3cret"})
        dec = self._run(
            {"op": "decode", "encoded": enc["encoded"], "secret": "s3cret"}
        )
        assert dec["kref"] == kref
        assert dec["verified"] is True

    def test_decode_with_wrong_secret_does_not_verify(self):
        # Tamper resistance: a forged token under a different secret
        # decodes (we still return the bytes) but `verified` is False.
        kref = "kref://Construct/Outreach/contacts/acme.contact?r=2"
        enc = self._run({"op": "encode", "kref": kref, "secret": "right"})
        dec = self._run(
            {"op": "decode", "encoded": enc["encoded"], "secret": "wrong"}
        )
        assert dec["verified"] is False

    def test_encode_requires_kref(self):
        out = self._run({"op": "encode"})
        assert "error" in out
        assert "kref" in out["error"]

    def test_unknown_op_errors(self):
        out = self._run({"op": "frobnicate"})
        assert "error" in out


# ── failure modes ───────────────────────────────────────────────────


@pytest.mark.asyncio
class TestFailureModes:
    async def test_nonzero_exit_fails(self, tmp_path):
        cfg = PythonStepConfig(code="import sys; sys.exit(2)")
        result = await _exec_python(_step(cfg), _make_state(), str(tmp_path))
        assert result.status == "failed"
        assert result.output_data["exit_code"] == 2

    async def test_allow_failure_completes_on_nonzero_exit(self, tmp_path):
        cfg = PythonStepConfig(
            code="import sys; sys.stderr.write('boom'); sys.exit(3)",
            allow_failure=True,
        )
        result = await _exec_python(_step(cfg), _make_state(), str(tmp_path))
        # allow_failure flips status back to completed but still records
        # the actual exit code — downstream conditionals can read it.
        assert result.status == "completed"
        assert result.output_data["exit_code"] == 3

    async def test_timeout_kills_process(self, tmp_path):
        cfg = PythonStepConfig(
            code="import time; time.sleep(10)",
            timeout=0.5,
        )
        result = await _exec_python(_step(cfg), _make_state(), str(tmp_path))
        assert result.status == "failed"
        assert "timed out" in result.error
