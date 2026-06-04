"""Tests for the Operator MCP tool ``manus_create_task``.

The tool exposes Manus to the Operator agent for ad-hoc, mid-chat
research. It reuses the same ``manus_run_task`` helper as the workflow
``manus:`` step so coverage of the HTTP create + poll path lives in
``test_manus_step.py``. This file focuses on the surface area unique to
the MCP tool:

  - happy-path returns the canonical fields (task_id, final_state,
    assistant message, attachments)
  - credentials_ref triggers the auth-profile resolve flow
  - no credentials_ref → falls back to MANUS_API_KEY env var
  - missing api key → tool returns ``{"error": ...}`` instead of raising
  - timeout → tool returns ``{"final_state: "timeout"}`` instead of raising
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import patch

import pytest

from operator_mcp.tool_handlers.manus import tool_manus_create_task


# ---------------------------------------------------------------------------
# Fake httpx.AsyncClient — copy-paste of the workflow step's pattern so
# these tests don't touch the real Manus API.
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


async def _noop_sleep(_s):
    if False:
        await asyncio.sleep(0)


def _patch_manus(*, fake_client: _FakeClient, api_key: str = "fake-test-key"):
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


_FAST_ARGS = {"timeout_seconds": 10, "poll_interval_seconds": 0}


# ---------------------------------------------------------------------------
# 1. Happy path — basic task runs and returns the canonical fields.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_basic_task_runs(monkeypatch):
    from operator_mcp import revka_config
    monkeypatch.setattr(revka_config, "_cached_manus", None)
    fc = _FakeClient(
        create_response=_FakeResp(200, {
            "ok": True,
            "task_id": "task-mcp-1",
            "task_url": "https://manus.ai/tasks/task-mcp-1",
            "share_url": "https://manus.ai/share/task-mcp-1",
        }),
        poll_responses=[
            _FakeResp(200, {"ok": True, "data": [
                {"id": "e1", "type": "assistant_message",
                 "assistant_message": {
                     "content": "Found 3 widgets.",
                     "attachments": [
                         {"file_name": "report.md",
                          "url": "https://manus.ai/files/report.md",
                          "size_bytes": 2048},
                     ],
                 }},
                {"id": "e2", "type": "status_update",
                 "status_update": {"agent_status": "stopped"}},
            ]}),
        ],
    )
    ctxs = _patch_manus(fake_client=fc)
    try:
        _enter_all(ctxs)
        result = await tool_manus_create_task({
            "prompt": "Find blue widgets",
            **_FAST_ARGS,
        })
    finally:
        _exit_all(ctxs)

    # Tool must return a dict, never raise.
    assert isinstance(result, dict)
    assert "error" not in result
    assert result["task_id"] == "task-mcp-1"
    assert result["task_url"].startswith("https://manus.ai")
    assert result["final_state"] == "stopped"
    assert result["assistant_message"] == "Found 3 widgets."
    assert result["attachments"] == [
        {"file_name": "report.md",
         "url": "https://manus.ai/files/report.md",
         "size_bytes": 2048},
    ]


# ---------------------------------------------------------------------------
# 2. credentials_ref path — resolver is consulted instead of env var.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_credentials_ref_path(monkeypatch):
    from operator_mcp import revka_config
    monkeypatch.setattr(revka_config, "_cached_manus", None)
    resolved_token = "resolved-from-profile-tool-1"
    env_value = "env-value-should-be-ignored-1"

    fc = _FakeClient(
        create_response=_FakeResp(200, {
            "ok": True, "task_id": "task-cred-mcp",
            "task_url": "u", "share_url": "s",
        }),
        poll_responses=[_FakeResp(200, {"ok": True, "data": [
            {"id": "e1", "type": "assistant_message",
             "assistant_message": {"content": "done"}},
            {"id": "e2", "type": "status_update",
             "status_update": {"agent_status": "stopped"}},
        ]})],
    )

    resolve_calls: list[str] = []

    async def fake_resolver(profile_id):
        resolve_calls.append(profile_id)
        return {
            "token": resolved_token,
            "kind": "token",
            "provider": "manus",
            "profile_name": "work",
            "expires_at": None,
        }

    ctxs = _patch_manus(fake_client=fc, api_key=env_value)
    import operator_mcp.workflow.executor as ex
    try:
        _enter_all(ctxs)
        with patch.object(ex, "resolve_auth_profile", new=fake_resolver):
            result = await tool_manus_create_task({
                "prompt": "x",
                "credentials_ref": "manus:work",
                **_FAST_ARGS,
            })
    finally:
        _exit_all(ctxs)

    assert "error" not in result
    assert result["final_state"] == "stopped"
    # Resolver was called with the profile id.
    assert resolve_calls == ["manus:work"]
    # Auth header carries the resolved token, NOT the env value.
    first = fc.calls[0]
    assert first["headers"].get("x-manus-api-key") == resolved_token
    assert first["headers"].get("x-manus-api-key") != env_value
    # credentials_ref echoed in result so callers can audit which
    # profile was used. The token itself never appears.
    assert result.get("credentials_ref") == "manus:work"
    blob = json.dumps(result)
    assert resolved_token not in blob


# ---------------------------------------------------------------------------
# 3. Env-fallback path — no credentials_ref → MANUS_API_KEY is used and
#    the resolver is never consulted.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_env_fallback_path(monkeypatch):
    from operator_mcp import revka_config
    monkeypatch.setattr(revka_config, "_cached_manus", None)
    env_key = "env-fallback-mcp-key-xyz"

    fc = _FakeClient(
        create_response=_FakeResp(200, {
            "ok": True, "task_id": "task-env-mcp",
            "task_url": "u", "share_url": "s",
        }),
        poll_responses=[_FakeResp(200, {"ok": True, "data": [
            {"id": "e1", "type": "assistant_message",
             "assistant_message": {"content": "done"}},
            {"id": "e2", "type": "status_update",
             "status_update": {"agent_status": "stopped"}},
        ]})],
    )

    async def must_not_resolve(_profile_id):
        raise AssertionError("resolver must not be called when credentials_ref is absent")

    ctxs = _patch_manus(fake_client=fc, api_key=env_key)
    import operator_mcp.workflow.executor as ex
    try:
        _enter_all(ctxs)
        with patch.object(ex, "resolve_auth_profile", new=must_not_resolve):
            result = await tool_manus_create_task({
                "prompt": "x",
                **_FAST_ARGS,
            })
    finally:
        _exit_all(ctxs)

    assert "error" not in result
    assert result["final_state"] == "stopped"
    first = fc.calls[0]
    assert first["headers"].get("x-manus-api-key") == env_key
    assert result.get("credentials_ref") == ""
    assert result.get("api_key_env") == "MANUS_API_KEY"


# ---------------------------------------------------------------------------
# 4. Missing api key — neither credentials_ref nor MANUS_API_KEY set →
#    tool returns {"error": ...} (does NOT raise).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_api_key_returns_error(monkeypatch):
    from operator_mcp import revka_config
    monkeypatch.setattr(revka_config, "_cached_manus", None)
    monkeypatch.delenv("MANUS_API_KEY", raising=False)

    # If httpx is touched at all, this test should fail — auth gate
    # must short-circuit before any network call.
    with patch("httpx.AsyncClient",
               side_effect=AssertionError("network touched without key")):
        result = await tool_manus_create_task({"prompt": "x", **_FAST_ARGS})

    assert isinstance(result, dict)
    assert "error" in result
    assert "MANUS_API_KEY" in result["error"]
    # No task_id was created.
    assert "task_id" not in result


# ---------------------------------------------------------------------------
# 5. Timeout — the poll loop fires task.stop and returns
#    final_state='timeout' rather than raising.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_returns_error_dict(monkeypatch):
    from operator_mcp import revka_config
    monkeypatch.setattr(revka_config, "_cached_manus", None)
    running_msg = _FakeResp(200, {"ok": True, "data": [
        {"id": "e1", "type": "status_update",
         "status_update": {"agent_status": "running"}},
    ]})
    fc = _FakeClient(
        create_response=_FakeResp(200, {
            "ok": True, "task_id": "task-mcp-timeout",
            "task_url": "u", "share_url": "s",
        }),
        poll_responses=[running_msg],
    )
    ctxs = _patch_manus(fake_client=fc)
    try:
        _enter_all(ctxs)
        import operator_mcp.workflow.executor as ex
        counter = {"n": 0}

        def stepped_mono():
            counter["n"] += 1
            return float(counter["n"])

        with patch.object(ex.time, "monotonic", side_effect=stepped_mono):
            result = await tool_manus_create_task({
                "prompt": "x",
                "timeout_seconds": 0,
                "poll_interval_seconds": 0,
            })
    finally:
        _exit_all(ctxs)

    assert isinstance(result, dict)
    assert result["final_state"] == "timeout"
    assert "error" in result
    assert "time" in result["error"].lower()
    # task.stop was called best-effort even though the timeout fired.
    stop_calls = [c for c in fc.calls
                  if c["method"] == "POST" and c["url"].endswith("/v2/task.stop")]
    assert len(stop_calls) == 1


# ---------------------------------------------------------------------------
# 6. Schema / arg validation — empty prompt returns an error dict.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_prompt_returns_error():
    # No env-var / network setup needed — the prompt gate short-circuits
    # before we touch revka_config or httpx.
    result = await tool_manus_create_task({"prompt": ""})
    assert isinstance(result, dict)
    assert result.get("error")
    assert "prompt" in result["error"].lower()
