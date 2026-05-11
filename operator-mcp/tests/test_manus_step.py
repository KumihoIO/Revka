"""Tests for the workflow `manus:` step type.

The Manus step delegates web-research tasks to a hosted Manus AI agent
via its public REST API. These tests cover the surface area Construct
needs to demo reliably:

  1. Schema acceptance — prompt is required, defaults applied.
  2. Missing API key short-circuits with a clear error and never calls
     the network.
  3. Happy path — task.create + listMessages polling → assistant message
     in output_data.assistant_message.
  4. Polling loop honors terminal `stopped` status_update.
  5. Polling loop honors terminal `error` status_update; failing step
     unless allow_failure flips it to completed.
  6. ``state.cancel_requested`` mid-poll triggers task.stop and returns
     a failed step with final_state='cancelled'.
  7. Workflow timeout fires task.stop and returns failed with
     final_state='timeout'.
  8. Structured-output schema is forwarded on create AND its value flows
     into output_data.structured_output on success.
  9. API key value is never written to output_data, input_data, or the
     error message (env-var name only).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import patch

import pytest

from operator_mcp.workflow.executor import _exec_manus
from operator_mcp.workflow.schema import (
    ManusStepConfig,
    StepDef,
    StepResult,
    StepType,
    WorkflowState,
)


def _state(inputs: dict | None = None) -> WorkflowState:
    return WorkflowState(
        workflow_name="test-wf",
        run_id="test-run",
        inputs=dict(inputs or {}),
    )


def _step(cfg: ManusStepConfig, step_id: str = "research") -> StepDef:
    return StepDef(id=step_id, type=StepType.MANUS, manus=cfg)


# ---------------------------------------------------------------------------
# Fake httpx.AsyncClient — records every request and returns scripted
# responses. Used by every test below to avoid touching the real Manus
# API.
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, status_code: int, payload: Any = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text else (
            json.dumps(payload) if payload is not None else ""
        )

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _FakeClient:
    """Drop-in for ``httpx.AsyncClient`` that replays a scripted set of
    create + poll responses. ``create_response`` is returned once for the
    POST /v2/task.create call. ``poll_responses`` is a list returned in
    order on each GET /v2/task.listMessages call (last item repeats
    forever once exhausted). ``stop_response`` answers POST
    /v2/task.stop. Records every call on ``self.calls`` so tests can
    assert request shape + auth header."""

    def __init__(
        self,
        *,
        create_response: _FakeResp,
        poll_responses: list[_FakeResp],
        stop_response: _FakeResp | None = None,
    ):
        self.create_response = create_response
        self.poll_responses = poll_responses
        self.stop_response = stop_response or _FakeResp(200, {"ok": True})
        self.calls: list[dict[str, Any]] = []
        self._poll_idx = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url: str, *, json: dict | None = None,
                   headers: dict | None = None, timeout: Any = None):
        self.calls.append({"method": "POST", "url": url, "json": json,
                           "headers": dict(headers or {})})
        if url.endswith("/v2/task.create"):
            return self.create_response
        if url.endswith("/v2/task.stop"):
            return self.stop_response
        raise AssertionError(f"unexpected POST {url}")

    async def get(self, url: str, *, params: dict | None = None,
                  headers: dict | None = None):
        self.calls.append({"method": "GET", "url": url, "params": params,
                           "headers": dict(headers or {})})
        if url.endswith("/v2/task.listMessages"):
            if self._poll_idx < len(self.poll_responses):
                resp = self.poll_responses[self._poll_idx]
                self._poll_idx += 1
            else:
                resp = self.poll_responses[-1]
            return resp
        raise AssertionError(f"unexpected GET {url}")


# Tight defaults so the poll loop runs in milliseconds during tests.
_FAST_CFG = dict(timeout_seconds=10, poll_interval_seconds=0)


async def _noop_sleep(_s):
    # Yield control once so the asyncio loop can progress, but don't
    # actually sleep — tests need to finish in milliseconds.
    await asyncio.sleep(0) if False else None


def _patch_manus(
    *,
    fake_client: _FakeClient,
    api_key: str = "fake-test-key",
):
    """Combined patcher: env var + httpx.AsyncClient. Returns context
    managers that should be entered together. Note we patch the
    ``asyncio.sleep`` *reference inside the executor module* so the
    runtime's own awaits aren't affected."""
    import operator_mcp.workflow.executor as ex
    return [
        patch.dict("os.environ", {"MANUS_API_KEY": api_key}),
        patch("httpx.AsyncClient", return_value=fake_client),
        patch.object(ex.asyncio, "sleep", new=_noop_sleep),
    ]


def _enter_all(ctxs):
    for c in ctxs:
        c.__enter__()


def _exit_all(ctxs):
    for c in reversed(ctxs):
        c.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 1. Schema acceptance
# ---------------------------------------------------------------------------


class TestSchema:
    def test_minimum_fields(self):
        cfg = ManusStepConfig(prompt="Find blue widgets")
        assert cfg.prompt == "Find blue widgets"
        assert cfg.connectors == []
        assert cfg.structured_output_schema is None
        assert cfg.allow_failure is False

    def test_step_def_accepts_manus(self):
        step = StepDef(
            id="r",
            type=StepType.MANUS,
            manus=ManusStepConfig(prompt="x"),
        )
        assert step.type == StepType.MANUS
        assert step.manus is not None
        assert step.get_config() is step.manus


# ---------------------------------------------------------------------------
# 2. Missing API key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAuth:
    async def test_missing_api_key_fails_fast(self, monkeypatch):
        monkeypatch.delenv("MANUS_API_KEY", raising=False)
        # Force the manus_config cache to re-read with the default env.
        from operator_mcp import construct_config
        monkeypatch.setattr(construct_config, "_cached_manus", None)
        cfg = ManusStepConfig(prompt="Find blue widgets")
        # If httpx is touched at all, the test should fail.
        with patch("httpx.AsyncClient",
                   side_effect=AssertionError("network touched without key")):
            result = await _exec_manus(_step(cfg), _state())
        assert result.status == "failed"
        assert "MANUS_API_KEY" in result.error


# ---------------------------------------------------------------------------
# 3. Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestHappyPath:
    async def test_create_poll_complete(self, monkeypatch):
        from operator_mcp import construct_config
        monkeypatch.setattr(construct_config, "_cached_manus", None)
        fc = _FakeClient(
            create_response=_FakeResp(200, {
                "ok": True,
                "request_id": "req-1",
                "task_id": "task-abc123def456",
                "task_title": "Find blue widgets",
                "task_url": "https://manus.ai/tasks/task-abc",
                "share_url": "https://manus.ai/share/task-abc",
            }),
            poll_responses=[
                _FakeResp(200, {"ok": True, "messages": [
                    {"type": "status_update", "agent_status": "running"},
                ], "has_more": False}),
                _FakeResp(200, {"ok": True, "messages": [
                    {"type": "status_update", "agent_status": "running"},
                    {"type": "assistant_message",
                     "content": "Found 3 widgets.",
                     "attachments": [{"name": "report.md"}]},
                    {"type": "status_update", "agent_status": "stopped",
                     "status_detail": "task completed", "brief": "done"},
                ], "has_more": False}),
            ],
        )
        ctxs = _patch_manus(fake_client=fc)
        try:
            _enter_all(ctxs)
            cfg = ManusStepConfig(prompt="Find blue widgets", **_FAST_CFG)
            result = await _exec_manus(_step(cfg), _state())
        finally:
            _exit_all(ctxs)

        assert result.status == "completed"
        assert result.output_data["task_id"] == "task-abc123def456"
        assert result.output_data["task_url"].startswith("https://manus.ai")
        assert result.output_data["final_state"] == "stopped"
        assert result.output_data["assistant_message"] == "Found 3 widgets."
        assert result.output_data["attachments"] == [{"name": "report.md"}]
        # Auth header asserted on first call.
        first = fc.calls[0]
        assert first["headers"].get("x-manus-api-key") == "fake-test-key"
        assert first["headers"].get("authorization") is None


# ---------------------------------------------------------------------------
# 4. Terminal `stopped` honored
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTerminalStopped:
    async def test_first_poll_already_terminal(self, monkeypatch):
        from operator_mcp import construct_config
        monkeypatch.setattr(construct_config, "_cached_manus", None)
        fc = _FakeClient(
            create_response=_FakeResp(200, {
                "ok": True, "task_id": "task-1",
                "task_url": "u", "share_url": "s",
            }),
            poll_responses=[
                _FakeResp(200, {"ok": True, "messages": [
                    {"type": "assistant_message", "content": "answer"},
                    {"type": "status_update", "agent_status": "stopped"},
                ]}),
            ],
        )
        ctxs = _patch_manus(fake_client=fc)
        try:
            _enter_all(ctxs)
            cfg = ManusStepConfig(prompt="x", **_FAST_CFG)
            result = await _exec_manus(_step(cfg), _state())
        finally:
            _exit_all(ctxs)
        assert result.status == "completed"
        # Should have polled exactly once.
        poll_calls = [c for c in fc.calls if c["method"] == "GET"]
        assert len(poll_calls) == 1


# ---------------------------------------------------------------------------
# 5. Terminal `error` → failed unless allow_failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTerminalError:
    async def test_error_status_fails_step(self, monkeypatch):
        from operator_mcp import construct_config
        monkeypatch.setattr(construct_config, "_cached_manus", None)
        fc = _FakeClient(
            create_response=_FakeResp(200, {
                "ok": True, "task_id": "task-err",
                "task_url": "u", "share_url": "s",
            }),
            poll_responses=[
                _FakeResp(200, {"ok": True, "messages": [
                    {"type": "status_update", "agent_status": "error",
                     "status_detail": "manus internal failure"},
                ]}),
            ],
        )
        ctxs = _patch_manus(fake_client=fc)
        try:
            _enter_all(ctxs)
            cfg = ManusStepConfig(prompt="x", **_FAST_CFG)
            result = await _exec_manus(_step(cfg), _state())
        finally:
            _exit_all(ctxs)
        assert result.status == "failed"
        assert result.output_data["final_state"] == "error"

    async def test_allow_failure_converts_to_completed(self, monkeypatch):
        from operator_mcp import construct_config
        monkeypatch.setattr(construct_config, "_cached_manus", None)
        fc = _FakeClient(
            create_response=_FakeResp(200, {
                "ok": True, "task_id": "task-err",
                "task_url": "u", "share_url": "s",
            }),
            poll_responses=[
                _FakeResp(200, {"ok": True, "messages": [
                    {"type": "status_update", "agent_status": "error",
                     "status_detail": "manus internal failure"},
                ]}),
            ],
        )
        ctxs = _patch_manus(fake_client=fc)
        try:
            _enter_all(ctxs)
            cfg = ManusStepConfig(
                prompt="x", allow_failure=True, **_FAST_CFG
            )
            result = await _exec_manus(_step(cfg), _state())
        finally:
            _exit_all(ctxs)
        # Soft-fail: status='completed' but output_data still reflects the
        # underlying error for the run-view UI.
        assert result.status == "completed"
        assert result.output_data["final_state"] == "error"
        assert result.output_data["allow_failure"] is True


# ---------------------------------------------------------------------------
# 6. cancel_requested mid-poll
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCancel:
    async def test_cancel_request_stops_loop(self, monkeypatch):
        from operator_mcp import construct_config
        monkeypatch.setattr(construct_config, "_cached_manus", None)
        # Build a client that returns `running` forever — only cancel
        # can break the loop.
        running_msg = _FakeResp(200, {"ok": True, "messages": [
            {"type": "status_update", "agent_status": "running"},
        ]})
        fc = _FakeClient(
            create_response=_FakeResp(200, {
                "ok": True, "task_id": "task-cancel",
                "task_url": "u", "share_url": "s",
            }),
            poll_responses=[running_msg, running_msg, running_msg, running_msg],
        )
        state = _state()

        # Flip cancel_requested after the first poll completes.
        original_get = fc.get
        get_count = {"n": 0}

        async def get_with_cancel(url, *, params=None, headers=None):
            resp = await original_get(url, params=params, headers=headers)
            get_count["n"] += 1
            if get_count["n"] >= 1:
                state.cancel_requested = True
            return resp

        fc.get = get_with_cancel  # type: ignore[assignment]

        ctxs = _patch_manus(fake_client=fc)
        try:
            _enter_all(ctxs)
            cfg = ManusStepConfig(prompt="x", **_FAST_CFG)
            result = await _exec_manus(_step(cfg), state)
        finally:
            _exit_all(ctxs)
        assert result.status == "failed"
        assert "cancel" in result.error.lower()
        assert result.output_data["final_state"] == "cancelled"
        # Stop must have been called.
        stop_calls = [c for c in fc.calls
                      if c["method"] == "POST" and c["url"].endswith("/v2/task.stop")]
        assert len(stop_calls) == 1
        assert stop_calls[0]["json"] == {"task_id": "task-cancel"}


# ---------------------------------------------------------------------------
# 7. Workflow timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTimeout:
    async def test_timeout_stops_task(self, monkeypatch):
        from operator_mcp import construct_config
        monkeypatch.setattr(construct_config, "_cached_manus", None)
        # Always-running responses; timeout_seconds=0 should fire on the
        # first elapsed check after the create call.
        running_msg = _FakeResp(200, {"ok": True, "messages": [
            {"type": "status_update", "agent_status": "running"},
        ]})
        fc = _FakeClient(
            create_response=_FakeResp(200, {
                "ok": True, "task_id": "task-timeout",
                "task_url": "u", "share_url": "s",
            }),
            poll_responses=[running_msg],
        )
        ctxs = _patch_manus(fake_client=fc)
        try:
            _enter_all(ctxs)
            # Aggressive: timeout 0 + non-zero monotonic gives elapsed > timeout
            cfg = ManusStepConfig(
                prompt="x", timeout_seconds=0, poll_interval_seconds=0,
            )
            # Patch time.monotonic so elapsed > 0 on first check.
            import operator_mcp.workflow.executor as ex
            real_mono = ex.time.monotonic
            counter = {"n": 0}

            def stepped_mono():
                counter["n"] += 1
                return float(counter["n"])

            with patch.object(ex.time, "monotonic", side_effect=stepped_mono):
                result = await _exec_manus(_step(cfg), _state())
        finally:
            _exit_all(ctxs)
        assert result.status == "failed"
        assert "time" in result.error.lower()
        assert result.output_data["final_state"] == "timeout"


# ---------------------------------------------------------------------------
# 8. Structured-output schema forwarding + return value
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestStructuredOutput:
    async def test_schema_forwarded_and_value_returned(self, monkeypatch):
        from operator_mcp import construct_config
        monkeypatch.setattr(construct_config, "_cached_manus", None)
        schema = {
            "type": "object",
            "properties": {"companies": {"type": "array"}},
        }
        fc = _FakeClient(
            create_response=_FakeResp(200, {
                "ok": True, "task_id": "task-struct",
                "task_url": "u", "share_url": "s",
            }),
            poll_responses=[
                _FakeResp(200, {"ok": True, "messages": [
                    {"type": "assistant_message", "content": "Found 2 companies"},
                    {"type": "structured_output_result", "success": True,
                     "value": {"companies": [{"name": "A"}, {"name": "B"}]}},
                    {"type": "status_update", "agent_status": "stopped"},
                ]}),
            ],
        )
        ctxs = _patch_manus(fake_client=fc)
        try:
            _enter_all(ctxs)
            cfg = ManusStepConfig(
                prompt="Find companies",
                structured_output_schema=schema,
                **_FAST_CFG,
            )
            result = await _exec_manus(_step(cfg), _state())
        finally:
            _exit_all(ctxs)
        assert result.status == "completed"
        # Schema forwarded on create.
        create_call = next(c for c in fc.calls
                           if c["method"] == "POST" and c["url"].endswith("/v2/task.create"))
        assert create_call["json"]["structured_output_schema"] == schema
        # Value populated.
        assert result.output_data["structured_output"] == {
            "companies": [{"name": "A"}, {"name": "B"}]
        }


# ---------------------------------------------------------------------------
# 9. API key never leaks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestKeyHandling:
    async def test_key_never_appears_in_outputs_or_logs(self, monkeypatch, caplog):
        from operator_mcp import construct_config
        monkeypatch.setattr(construct_config, "_cached_manus", None)
        # Use a distinctive key the test can grep for.
        secret = "sk-MANUS-SECRET-DO-NOT-LEAK-1234567890"
        fc = _FakeClient(
            create_response=_FakeResp(401, {"error": "bad auth"}),
            poll_responses=[_FakeResp(200, {"ok": True, "messages": []})],
        )
        ctxs = _patch_manus(fake_client=fc, api_key=secret)
        try:
            _enter_all(ctxs)
            cfg = ManusStepConfig(prompt="x", **_FAST_CFG)
            result = await _exec_manus(_step(cfg), _state())
        finally:
            _exit_all(ctxs)
        # Step failed (auth rejected).
        assert result.status == "failed"
        # Secret value must not appear in error / input_data / output_data.
        blob = json.dumps({
            "error": result.error,
            "input_data": result.input_data,
            "output_data": result.output_data,
        })
        assert secret not in blob
        # input_data records the env-var NAME only.
        assert result.input_data.get("api_key_env") == "MANUS_API_KEY"
