"""Tests for ``manus.register_output`` — auto-publish a Manus step's result
as a Kumiho entity and download attachments to an entity-anchored disk
path.

These tests reuse the same in-process fake httpx + monkey-patched Kumiho
SDK pattern as ``test_manus_step.py``. They assert:

  1. With register_output configured + terminal "stopped" status, the
     publish helper is invoked with the right args and content.md lands
     at the entity-anchored path.
  2. Multiple attachments are downloaded to ``<entity_dir>/attachments/``
     and recorded in output_data.attachments_downloaded.
  3. A failing download is best-effort — the step still completes, the
     other attachment downloads, and the failure appears in
     attachments_failed.
  4. Path-traversal-style filenames get sanitized to a single safe
     basename.
  5. Two attachments sharing a filename collide — the second one gets a
     ``-1`` suffix before the extension.
  6. ``content_source: "structured"`` writes JSON-serialized structured
     output to content.md.
  7. When the Manus task ends in error, no registration / disk writes
     happen.
  8. ``KUMIHO_SDK.create_artifact`` is invoked once per successful
     download to attach the file to the revision.
  9. ``register_attachments: false`` keeps the raw attachments listed in
     output_data but skips all downloads + disk writes.
 10. ``entity_space`` is canonicalized — doubled slashes / trailing
     slashes are normalized in BOTH the disk path and the publish call.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from operator_mcp.workflow.executor import _exec_manus
from operator_mcp.workflow.schema import (
    ManusRegisterOutputConfig,
    ManusStepConfig,
    StepDef,
    StepResult,
    StepType,
    WorkflowState,
)


# Reuse the fakes + helpers from test_manus_step (they live in the same
# tests/ directory so a sibling import is fine).
from tests.test_manus_step import (
    _FakeClient,
    _FakeResp,
    _FAST_CFG,
    _patch_manus,
    _enter_all,
    _exit_all,
    _step,
    _state,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_home(monkeypatch):
    """Redirect ``~`` so register_output writes land in a tempdir."""
    d = tempfile.mkdtemp(prefix="manus-ro-test-")
    monkeypatch.setenv("HOME", d)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def fake_kumiho_sdk():
    """Replace ``operator_mcp.operator_mcp.KUMIHO_SDK`` with a stub that
    captures every publish / artifact call. Returns the stub so tests can
    assert on it.

    The stub is plugged into BOTH the executor's late-import path
    (``operator_mcp.operator_mcp.KUMIHO_SDK``) AND ``memory.publish_workflow_entity``
    which late-imports the same symbol. We patch the underlying module
    attribute so both views see the stub.
    """
    sdk = MagicMock()
    sdk._available = True
    # Async methods used by the publish + attach path.
    sdk.create_item = AsyncMock(return_value={"kref": "kref://item/test"})
    sdk.create_revision = AsyncMock(return_value={"kref": "kref://rev/test"})
    sdk.create_artifact = AsyncMock(return_value={"kref": "kref://artifact/test"})
    sdk.tag_revision = AsyncMock(return_value={"ok": True})
    sdk.ensure_space = AsyncMock(return_value=None)
    sdk.list_items = AsyncMock(return_value=[])

    # `_ensure_space_path` calls `kumiho.mcp_server.tool_create_space`
    # via to_thread — mock it so deeper paths don't try to hit the gRPC
    # backend in tests.
    with patch("operator_mcp.operator_mcp.KUMIHO_SDK", sdk):
        yield sdk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_attachment_url_client(
    *,
    poll_responses: list[_FakeResp],
    attachment_responses: dict[str, _FakeResp] | None = None,
):
    """Build a _FakeClient that also serves attachment GETs.

    The Manus executor opens a fresh ``httpx.AsyncClient`` for each
    attachment download. We can't reuse the single-client fixture because
    those clients are entered/exited per-attachment. Instead, we let
    ``httpx.AsyncClient`` return the same fake for every call — the fake
    routes by URL.
    """
    fc = _FakeClient(
        create_response=_FakeResp(200, {
            "ok": True,
            "task_id": "task-ro-1",
            "task_url": "https://manus.ai/tasks/task-ro-1",
            "share_url": "https://manus.ai/share/task-ro-1",
        }),
        poll_responses=poll_responses,
    )

    # Augment GET to handle attachment URLs (anything that's not the
    # listMessages endpoint).
    original_get = fc.get
    att_map = attachment_responses or {}

    async def get_router(url, *, params=None, headers=None):
        if url.endswith("/v2/task.listMessages"):
            return await original_get(url, params=params, headers=headers)
        # Attachment GET: look up by url.
        fc.calls.append({"method": "GET", "url": url, "params": params,
                         "headers": dict(headers or {})})
        if url in att_map:
            return att_map[url]
        # Default: 404
        return _FakeResp(404, None, text="not found")

    fc.get = get_router  # type: ignore[assignment]
    _install_stream_router(fc, att_map)
    return fc


class _BytesResp:
    """Like _FakeResp but exposes ``content`` (bytes) + raise_for_status
    so the executor's attachment-download code path works.

    Supports both the old blocking ``client.get(url)`` interface and the
    streaming ``client.stream("GET", url)`` + ``aiter_bytes`` interface.
    For streaming, chunks are derived from ``content`` (single chunk) or
    from ``chunks`` if explicitly provided — useful for tests that need
    the executor to see multiple chunks (e.g. size-cap enforcement)."""

    def __init__(
        self,
        status_code: int,
        content: bytes = b"",
        *,
        chunks: list[bytes] | None = None,
    ):
        self.status_code = status_code
        self.content = content
        self._chunks = chunks if chunks is not None else (
            [content] if content else []
        )

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):  # for safety — never called on attachment path
        return {}

    # Streaming-context support: ``async with client.stream(...) as resp``.
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_bytes(self, chunk_size: int = 64 * 1024):
        for c in self._chunks:
            yield c


def _install_stream_router(fc, att_map: dict):
    """Attach a ``stream("GET", url)`` method to the fake client that
    routes by URL to ``att_map``. Returns a non-async-context that yields
    the matching _BytesResp (or a 404 _BytesResp). Matches the executor's
    `async with client.stream(...) as resp` usage."""

    class _StreamCtx:
        def __init__(self, resp):
            self._resp = resp

        async def __aenter__(self):
            return self._resp

        async def __aexit__(self, *exc):
            return False

    def stream(method, url):
        fc.calls.append({"method": "STREAM", "url": url, "params": None,
                         "headers": {}})
        resp = att_map.get(url) or _BytesResp(404, b"")
        return _StreamCtx(resp)

    fc.stream = stream  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# 1. register_output publishes entity + writes content.md at entity path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_output_publishes_entity(
    monkeypatch, tmp_home, fake_kumiho_sdk,
):
    from operator_mcp import revka_config
    monkeypatch.setattr(revka_config, "_cached_manus", None)
    fc = _make_attachment_url_client(poll_responses=[
        _FakeResp(200, {"ok": True, "data": [
            {"id": "e1", "type": "assistant_message",
             "assistant_message": {"content": "research summary"}},
            {"id": "e2", "type": "status_update",
             "status_update": {"agent_status": "stopped"}},
        ]}),
    ])

    # Track publish_workflow_entity calls.
    publish_calls = []
    from operator_mcp.workflow import memory as wm

    original_publish = wm.publish_workflow_entity

    async def spy_publish(**kwargs):
        publish_calls.append(kwargs)
        return await original_publish(**kwargs)

    monkeypatch.setattr(wm, "publish_workflow_entity", spy_publish)
    # Also patch `_ensure_space_path` to a no-op — it would try gRPC otherwise.
    monkeypatch.setattr(wm, "_ensure_space_path", AsyncMock(return_value=None))

    ctxs = _patch_manus(fake_client=fc)
    try:
        _enter_all(ctxs)
        cfg = ManusStepConfig(
            prompt="x",
            register_output=ManusRegisterOutputConfig(
                entity_name="my-report",
                entity_kind="research-report",
                entity_tag="published",
                entity_space="Revka/WorkflowOutputs/Research",
            ),
            **_FAST_CFG,
        )
        result = await _exec_manus(_step(cfg), _state())
    finally:
        _exit_all(ctxs)

    assert result.status == "completed"
    assert len(publish_calls) == 1
    call = publish_calls[0]
    assert call["entity_name"] == "my-report"
    assert call["entity_kind"] == "research-report"
    assert call["entity_tag"] == "published"
    # Space canonicalized — same string in publish + on disk.
    assert call["entity_space"] == "Revka/WorkflowOutputs/Research"
    assert call["artifact_path_override"] is not None

    expected_dir = os.path.join(
        tmp_home,
        ".revka/artifacts/Revka/WorkflowOutputs/Research/research-report/my-report",
    )
    content_path = os.path.join(expected_dir, "content.md")
    assert os.path.exists(content_path), f"expected {content_path} on disk"
    with open(content_path) as f:
        assert f.read() == "research summary"

    od = result.output_data
    assert od["content_path"] == content_path
    assert od["registered_entity"]["name"] == "my-report"
    assert od["registered_entity"]["kind"] == "research-report"
    assert od["registered_entity"]["space"] == "Revka/WorkflowOutputs/Research"
    assert od["registered_entity"]["item_kref"] == "kref://item/test"
    assert od["registered_entity"]["revision_kref"] == "kref://rev/test"


# ---------------------------------------------------------------------------
# 2. attachments downloaded to entity path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attachments_downloaded_to_entity_path(
    monkeypatch, tmp_home, fake_kumiho_sdk,
):
    from operator_mcp import revka_config
    monkeypatch.setattr(revka_config, "_cached_manus", None)
    att1_url = "https://manus.ai/files/a.pdf"
    att2_url = "https://manus.ai/files/b.csv"
    fc = _make_attachment_url_client(
        poll_responses=[
            _FakeResp(200, {"ok": True, "data": [
                {"id": "e1", "type": "assistant_message",
                 "assistant_message": {
                    "content": "done",
                    "attachments": [
                        {"file_name": "a.pdf", "url": att1_url, "size_bytes": 10},
                        {"file_name": "b.csv", "url": att2_url, "size_bytes": 20},
                    ],
                 }},
                {"id": "e2", "type": "status_update",
                 "status_update": {"agent_status": "stopped"}},
            ]}),
        ],
        attachment_responses={
            att1_url: _BytesResp(200, b"PDF-BYTES"),
            att2_url: _BytesResp(200, b"col1,col2\n1,2\n"),
        },
    )

    # Route every httpx.AsyncClient(...) call to our single fake.
    from operator_mcp.workflow import memory as wm
    monkeypatch.setattr(wm, "_ensure_space_path", AsyncMock(return_value=None))

    ctxs = _patch_manus(fake_client=fc)
    try:
        _enter_all(ctxs)
        cfg = ManusStepConfig(
            prompt="x",
            register_output=ManusRegisterOutputConfig(
                entity_name="report",
                entity_kind="r-kind",
                entity_space="Revka/WorkflowOutputs/Research",
            ),
            **_FAST_CFG,
        )
        result = await _exec_manus(_step(cfg), _state())
    finally:
        _exit_all(ctxs)

    assert result.status == "completed"
    expected_dir = os.path.join(
        tmp_home,
        ".revka/artifacts/Revka/WorkflowOutputs/Research/r-kind/report/attachments",
    )
    assert os.path.exists(os.path.join(expected_dir, "a.pdf"))
    assert os.path.exists(os.path.join(expected_dir, "b.csv"))
    with open(os.path.join(expected_dir, "a.pdf"), "rb") as f:
        assert f.read() == b"PDF-BYTES"

    od = result.output_data
    assert len(od["attachments_downloaded"]) == 2
    names = sorted(d["file_name"] for d in od["attachments_downloaded"])
    assert names == ["a.pdf", "b.csv"]
    assert od["attachments_failed"] == []


# ---------------------------------------------------------------------------
# 3. attachment download failure is best-effort
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attachment_download_failure_is_best_effort(
    monkeypatch, tmp_home, fake_kumiho_sdk,
):
    from operator_mcp import revka_config
    monkeypatch.setattr(revka_config, "_cached_manus", None)
    good_url = "https://manus.ai/files/good.bin"
    bad_url = "https://manus.ai/files/bad.bin"
    fc = _make_attachment_url_client(
        poll_responses=[
            _FakeResp(200, {"ok": True, "data": [
                {"id": "e1", "type": "assistant_message",
                 "assistant_message": {
                    "content": "done",
                    "attachments": [
                        {"file_name": "good.bin", "url": good_url},
                        {"file_name": "bad.bin", "url": bad_url},
                    ],
                 }},
                {"id": "e2", "type": "status_update",
                 "status_update": {"agent_status": "stopped"}},
            ]}),
        ],
        attachment_responses={
            good_url: _BytesResp(200, b"OK"),
            bad_url: _BytesResp(500, b""),
        },
    )

    from operator_mcp.workflow import memory as wm
    monkeypatch.setattr(wm, "_ensure_space_path", AsyncMock(return_value=None))

    ctxs = _patch_manus(fake_client=fc)
    try:
        _enter_all(ctxs)
        cfg = ManusStepConfig(
            prompt="x",
            register_output=ManusRegisterOutputConfig(
                entity_name="report",
                entity_kind="r-kind",
            ),
            **_FAST_CFG,
        )
        result = await _exec_manus(_step(cfg), _state())
    finally:
        _exit_all(ctxs)

    # Step still completes — Manus task succeeded; one attachment fetch failed.
    assert result.status == "completed"
    od = result.output_data
    assert len(od["attachments_downloaded"]) == 1
    assert od["attachments_downloaded"][0]["file_name"] == "good.bin"
    assert len(od["attachments_failed"]) == 1
    assert od["attachments_failed"][0]["file_name"] == "bad.bin"
    assert "500" in od["attachments_failed"][0]["error"]


# ---------------------------------------------------------------------------
# 4. filename sanitization (path traversal)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_filename_sanitization(
    monkeypatch, tmp_home, fake_kumiho_sdk,
):
    from operator_mcp import revka_config
    monkeypatch.setattr(revka_config, "_cached_manus", None)
    evil_url = "https://manus.ai/files/evil"
    fc = _make_attachment_url_client(
        poll_responses=[
            _FakeResp(200, {"ok": True, "data": [
                {"id": "e1", "type": "assistant_message",
                 "assistant_message": {
                    "content": "done",
                    "attachments": [
                        {"file_name": "../../etc/passwd", "url": evil_url},
                    ],
                 }},
                {"id": "e2", "type": "status_update",
                 "status_update": {"agent_status": "stopped"}},
            ]}),
        ],
        attachment_responses={evil_url: _BytesResp(200, b"x")},
    )

    from operator_mcp.workflow import memory as wm
    monkeypatch.setattr(wm, "_ensure_space_path", AsyncMock(return_value=None))

    ctxs = _patch_manus(fake_client=fc)
    try:
        _enter_all(ctxs)
        cfg = ManusStepConfig(
            prompt="x",
            register_output=ManusRegisterOutputConfig(
                entity_name="report",
                entity_kind="r-kind",
            ),
            **_FAST_CFG,
        )
        result = await _exec_manus(_step(cfg), _state())
    finally:
        _exit_all(ctxs)

    assert result.status == "completed"
    od = result.output_data
    assert len(od["attachments_downloaded"]) == 1
    safe = od["attachments_downloaded"][0]["file_name"]
    # No path separators, no leading dots, no `..` left.
    assert "/" not in safe
    assert "\\" not in safe
    assert ".." not in safe
    assert not safe.startswith(".")
    # The file must be inside the entity's attachments/ dir — never outside it.
    local = od["attachments_downloaded"][0]["local_path"]
    expected_prefix = os.path.join(
        tmp_home, ".revka/artifacts",
    )
    assert local.startswith(expected_prefix)
    # And the basename only contained the leftover characters after stripping
    # `..` and `/` — "etcpasswd" is the expected sanitized form.
    assert os.path.basename(local) == safe


# ---------------------------------------------------------------------------
# 5. filename collision gets suffix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_filename_collision_gets_suffix(
    monkeypatch, tmp_home, fake_kumiho_sdk,
):
    from operator_mcp import revka_config
    monkeypatch.setattr(revka_config, "_cached_manus", None)
    url_a = "https://manus.ai/files/dup-a"
    url_b = "https://manus.ai/files/dup-b"
    fc = _make_attachment_url_client(
        poll_responses=[
            _FakeResp(200, {"ok": True, "data": [
                {"id": "e1", "type": "assistant_message",
                 "assistant_message": {
                    "content": "done",
                    "attachments": [
                        {"file_name": "report.pdf", "url": url_a},
                        {"file_name": "report.pdf", "url": url_b},
                    ],
                 }},
                {"id": "e2", "type": "status_update",
                 "status_update": {"agent_status": "stopped"}},
            ]}),
        ],
        attachment_responses={
            url_a: _BytesResp(200, b"A"),
            url_b: _BytesResp(200, b"B"),
        },
    )

    from operator_mcp.workflow import memory as wm
    monkeypatch.setattr(wm, "_ensure_space_path", AsyncMock(return_value=None))

    ctxs = _patch_manus(fake_client=fc)
    try:
        _enter_all(ctxs)
        cfg = ManusStepConfig(
            prompt="x",
            register_output=ManusRegisterOutputConfig(
                entity_name="report",
                entity_kind="r-kind",
            ),
            **_FAST_CFG,
        )
        result = await _exec_manus(_step(cfg), _state())
    finally:
        _exit_all(ctxs)

    od = result.output_data
    names = sorted(d["file_name"] for d in od["attachments_downloaded"])
    assert names == ["report-1.pdf", "report.pdf"]


# ---------------------------------------------------------------------------
# 6. content_source: structured writes JSON
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_content_source_structured(
    monkeypatch, tmp_home, fake_kumiho_sdk,
):
    from operator_mcp import revka_config
    monkeypatch.setattr(revka_config, "_cached_manus", None)
    fc = _make_attachment_url_client(poll_responses=[
        _FakeResp(200, {"ok": True, "data": [
            {"id": "e1", "type": "assistant_message",
             "assistant_message": {"content": "narrative"}},
            {"id": "e2", "type": "structured_output_result",
             "structured_output_result": {
                "success": True,
                "value": {"summary": "S", "score": 0.9},
             }},
            {"id": "e3", "type": "status_update",
             "status_update": {"agent_status": "stopped"}},
        ]}),
    ])

    from operator_mcp.workflow import memory as wm
    monkeypatch.setattr(wm, "_ensure_space_path", AsyncMock(return_value=None))

    ctxs = _patch_manus(fake_client=fc)
    try:
        _enter_all(ctxs)
        cfg = ManusStepConfig(
            prompt="x",
            register_output=ManusRegisterOutputConfig(
                entity_name="report",
                entity_kind="r-kind",
                content_source="structured",
            ),
            **_FAST_CFG,
        )
        result = await _exec_manus(_step(cfg), _state())
    finally:
        _exit_all(ctxs)

    assert result.status == "completed"
    content_path = result.output_data["content_path"]
    with open(content_path) as f:
        body = f.read()
    parsed = json.loads(body)
    assert parsed == {"summary": "S", "score": 0.9}


# ---------------------------------------------------------------------------
# 7. step fails → no registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_output_skipped_when_step_fails(
    monkeypatch, tmp_home, fake_kumiho_sdk,
):
    from operator_mcp import revka_config
    monkeypatch.setattr(revka_config, "_cached_manus", None)
    fc = _make_attachment_url_client(poll_responses=[
        _FakeResp(200, {"ok": True, "data": [
            {"id": "e1", "type": "status_update",
             "status_update": {"agent_status": "error",
                               "status_detail": "boom"}},
        ]}),
    ])

    publish_calls = []
    from operator_mcp.workflow import memory as wm
    original_publish = wm.publish_workflow_entity

    async def spy_publish(**kwargs):
        publish_calls.append(kwargs)
        return await original_publish(**kwargs)

    monkeypatch.setattr(wm, "publish_workflow_entity", spy_publish)
    monkeypatch.setattr(wm, "_ensure_space_path", AsyncMock(return_value=None))

    ctxs = _patch_manus(fake_client=fc)
    try:
        _enter_all(ctxs)
        cfg = ManusStepConfig(
            prompt="x",
            register_output=ManusRegisterOutputConfig(
                entity_name="report",
                entity_kind="r-kind",
            ),
            **_FAST_CFG,
        )
        result = await _exec_manus(_step(cfg), _state())
    finally:
        _exit_all(ctxs)

    assert result.status == "failed"
    assert publish_calls == []
    # No entity dir created.
    entity_dir = os.path.join(
        tmp_home,
        ".revka/artifacts",
    )
    # The artifacts root might exist for other tests in the same tmp_home,
    # but the per-entity content.md must NOT.
    if os.path.exists(entity_dir):
        # Walk and verify content.md isn't somewhere under it.
        for root, _dirs, files in os.walk(entity_dir):
            assert "content.md" not in files, f"unexpected content.md under {root}"


# ---------------------------------------------------------------------------
# 8. create_artifact invoked per successful download
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_artifact_called_per_attachment(
    monkeypatch, tmp_home, fake_kumiho_sdk,
):
    from operator_mcp import revka_config
    monkeypatch.setattr(revka_config, "_cached_manus", None)
    u1 = "https://manus.ai/files/x.txt"
    u2 = "https://manus.ai/files/y.txt"
    fc = _make_attachment_url_client(
        poll_responses=[
            _FakeResp(200, {"ok": True, "data": [
                {"id": "e1", "type": "assistant_message",
                 "assistant_message": {
                    "content": "done",
                    "attachments": [
                        {"file_name": "x.txt", "url": u1},
                        {"file_name": "y.txt", "url": u2},
                    ],
                 }},
                {"id": "e2", "type": "status_update",
                 "status_update": {"agent_status": "stopped"}},
            ]}),
        ],
        attachment_responses={
            u1: _BytesResp(200, b"x"),
            u2: _BytesResp(200, b"y"),
        },
    )

    from operator_mcp.workflow import memory as wm
    monkeypatch.setattr(wm, "_ensure_space_path", AsyncMock(return_value=None))

    ctxs = _patch_manus(fake_client=fc)
    try:
        _enter_all(ctxs)
        cfg = ManusStepConfig(
            prompt="x",
            register_output=ManusRegisterOutputConfig(
                entity_name="report",
                entity_kind="r-kind",
            ),
            **_FAST_CFG,
        )
        result = await _exec_manus(_step(cfg), _state())
    finally:
        _exit_all(ctxs)

    assert result.status == "completed"
    # 1 call for the content.md publish + 2 for the attachments = 3 total.
    assert fake_kumiho_sdk.create_artifact.await_count == 3
    # Inspect the two attachment calls (skip the first which is content.md).
    attachment_call_args = [
        c for c in fake_kumiho_sdk.create_artifact.call_args_list
        if c.args[1] in ("x.txt", "y.txt")
    ]
    assert len(attachment_call_args) == 2
    for call in attachment_call_args:
        rev_kref, file_name, local_path = call.args
        assert rev_kref == "kref://rev/test"
        assert os.path.exists(local_path)


# ---------------------------------------------------------------------------
# 9. register_attachments: false skips downloads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_attachments_false_skips_downloads(
    monkeypatch, tmp_home, fake_kumiho_sdk,
):
    from operator_mcp import revka_config
    monkeypatch.setattr(revka_config, "_cached_manus", None)
    # If the executor tries to hit this URL the test will fail (no
    # attachment_responses entry → 404, which would land in attachments_failed).
    att_url = "https://manus.ai/files/forbidden.bin"
    fc = _make_attachment_url_client(
        poll_responses=[
            _FakeResp(200, {"ok": True, "data": [
                {"id": "e1", "type": "assistant_message",
                 "assistant_message": {
                    "content": "done",
                    "attachments": [
                        {"file_name": "forbidden.bin", "url": att_url},
                    ],
                 }},
                {"id": "e2", "type": "status_update",
                 "status_update": {"agent_status": "stopped"}},
            ]}),
        ],
        # Deliberately empty: any attempt to GET the attachment becomes
        # a 404 we can assert against (failed downloads end up in
        # output_data.attachments_failed, which must be empty here).
        attachment_responses={},
    )

    from operator_mcp.workflow import memory as wm
    monkeypatch.setattr(wm, "_ensure_space_path", AsyncMock(return_value=None))

    ctxs = _patch_manus(fake_client=fc)
    try:
        _enter_all(ctxs)
        cfg = ManusStepConfig(
            prompt="x",
            register_output=ManusRegisterOutputConfig(
                entity_name="report",
                entity_kind="r-kind",
                register_attachments=False,
            ),
            **_FAST_CFG,
        )
        result = await _exec_manus(_step(cfg), _state())
    finally:
        _exit_all(ctxs)

    assert result.status == "completed"
    od = result.output_data
    # Raw attachments still present in output_data (existing pre-PR behavior).
    assert len(od["attachments"]) == 1
    assert od["attachments"][0]["file_name"] == "forbidden.bin"
    # But no downloads + no failures (we never tried).
    assert od["attachments_downloaded"] == []
    assert od["attachments_failed"] == []
    # No attachments/ subdir created.
    attachments_dir = os.path.join(
        tmp_home,
        ".revka/artifacts/Revka/WorkflowOutputs/r-kind/report/attachments",
    )
    assert not os.path.exists(attachments_dir)


# ---------------------------------------------------------------------------
# 10. canonical space path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_path_uses_canonical_space(
    monkeypatch, tmp_home, fake_kumiho_sdk,
):
    from operator_mcp import revka_config
    monkeypatch.setattr(revka_config, "_cached_manus", None)
    fc = _make_attachment_url_client(poll_responses=[
        _FakeResp(200, {"ok": True, "data": [
            {"id": "e1", "type": "assistant_message",
             "assistant_message": {"content": "x"}},
            {"id": "e2", "type": "status_update",
             "status_update": {"agent_status": "stopped"}},
        ]}),
    ])

    publish_calls = []
    from operator_mcp.workflow import memory as wm
    original_publish = wm.publish_workflow_entity

    async def spy_publish(**kwargs):
        publish_calls.append(kwargs)
        return await original_publish(**kwargs)

    monkeypatch.setattr(wm, "publish_workflow_entity", spy_publish)
    monkeypatch.setattr(wm, "_ensure_space_path", AsyncMock(return_value=None))

    ctxs = _patch_manus(fake_client=fc)
    try:
        _enter_all(ctxs)
        cfg = ManusStepConfig(
            prompt="x",
            register_output=ManusRegisterOutputConfig(
                entity_name="report",
                entity_kind="r-kind",
                # Doubled slashes + trailing slash — must be canonicalized.
                entity_space="Revka//WorkflowOutputs/Github/",
            ),
            **_FAST_CFG,
        )
        result = await _exec_manus(_step(cfg), _state())
    finally:
        _exit_all(ctxs)

    assert result.status == "completed"
    # Disk path uses the normalized form.
    expected_dir = os.path.join(
        tmp_home,
        ".revka/artifacts/Revka/WorkflowOutputs/Github/r-kind/report",
    )
    assert os.path.exists(os.path.join(expected_dir, "content.md"))
    # Publish call sees the canonicalized space too.
    assert publish_calls[0]["entity_space"] == "Revka/WorkflowOutputs/Github"


# ---------------------------------------------------------------------------
# 11. entity_name path traversal rejected (Fix 1: sanitizer + containment)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entity_name_traversal_rejected(
    monkeypatch, tmp_home, fake_kumiho_sdk,
):
    """A malicious entity_name like ``../../escape`` must not write outside
    the artifacts root. Either the sanitizer strips it to empty (skip,
    error recorded) or the containment check refuses. Either way: step
    still completes, no Kumiho publish call, no escape on disk."""
    from operator_mcp import revka_config
    monkeypatch.setattr(revka_config, "_cached_manus", None)
    fc = _make_attachment_url_client(poll_responses=[
        _FakeResp(200, {"ok": True, "data": [
            {"id": "e1", "type": "assistant_message",
             "assistant_message": {"content": "evil"}},
            {"id": "e2", "type": "status_update",
             "status_update": {"agent_status": "stopped"}},
        ]}),
    ])

    publish_calls = []
    from operator_mcp.workflow import memory as wm
    original_publish = wm.publish_workflow_entity

    async def spy_publish(**kwargs):
        publish_calls.append(kwargs)
        return await original_publish(**kwargs)

    monkeypatch.setattr(wm, "publish_workflow_entity", spy_publish)
    monkeypatch.setattr(wm, "_ensure_space_path", AsyncMock(return_value=None))

    ctxs = _patch_manus(fake_client=fc)
    try:
        _enter_all(ctxs)
        cfg = ManusStepConfig(
            prompt="x",
            register_output=ManusRegisterOutputConfig(
                entity_name="../../escape",
                entity_kind="r-kind",
            ),
            **_FAST_CFG,
        )
        result = await _exec_manus(_step(cfg), _state())
    finally:
        _exit_all(ctxs)

    # Step still completes — Manus task succeeded. Whatever happened next,
    # the security invariant is: nothing written outside ~/.revka/artifacts/.
    assert result.status == "completed"
    home_real = os.path.realpath(tmp_home)
    artifacts_root = os.path.join(home_real, ".revka/artifacts")
    for root, _dirs, files in os.walk(home_real):
        for f in files:
            full = os.path.join(root, f)
            assert full.startswith(artifacts_root), (
                f"file escaped artifacts root: {full}"
            )
    # Either: (a) sanitizer salvaged a safe name and registration proceeded
    # normally inside the artifacts root, OR (b) sanitization/containment
    # rejected outright. Both satisfy the security goal. We assert ONE
    # holds.
    err = result.output_data.get("register_output_error", "")
    rejected = ("invalid after sanitization" in err
                or "would escape artifacts root" in err)
    salvaged = result.output_data.get("registered_entity") is not None
    assert rejected or salvaged, (
        f"expected either rejection or salvaged registration; "
        f"err={err!r}, registered={result.output_data.get('registered_entity')!r}"
    )
    if rejected:
        assert publish_calls == []


# ---------------------------------------------------------------------------
# 12. attachment size cap aborts streaming download (Fix 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attachment_size_cap_enforced(
    monkeypatch, tmp_home, fake_kumiho_sdk,
):
    """Stream that yields more bytes than MAX_ATTACHMENT_BYTES is aborted —
    partial file removed, entry recorded in attachments_failed. We patch
    MAX_ATTACHMENT_BYTES to 1KB and have the fake yield 2KB so the test
    finishes in milliseconds without allocating real megabytes."""
    from operator_mcp import revka_config
    monkeypatch.setattr(revka_config, "_cached_manus", None)
    import operator_mcp.workflow.executor as ex
    monkeypatch.setattr(ex, "MAX_ATTACHMENT_BYTES", 1024)

    big_url = "https://manus.ai/files/big.bin"
    # Two 1KB chunks = 2KB total; executor must abort after the second
    # chunk pushes ``written`` past 1KB.
    fc = _make_attachment_url_client(
        poll_responses=[
            _FakeResp(200, {"ok": True, "data": [
                {"id": "e1", "type": "assistant_message",
                 "assistant_message": {
                    "content": "done",
                    "attachments": [
                        {"file_name": "big.bin", "url": big_url},
                    ],
                 }},
                {"id": "e2", "type": "status_update",
                 "status_update": {"agent_status": "stopped"}},
            ]}),
        ],
        attachment_responses={
            big_url: _BytesResp(200, chunks=[b"a" * 1024, b"b" * 1024]),
        },
    )

    from operator_mcp.workflow import memory as wm
    monkeypatch.setattr(wm, "_ensure_space_path", AsyncMock(return_value=None))

    ctxs = _patch_manus(fake_client=fc)
    try:
        _enter_all(ctxs)
        cfg = ManusStepConfig(
            prompt="x",
            register_output=ManusRegisterOutputConfig(
                entity_name="report",
                entity_kind="r-kind",
            ),
            **_FAST_CFG,
        )
        result = await _exec_manus(_step(cfg), _state())
    finally:
        _exit_all(ctxs)

    assert result.status == "completed"
    od = result.output_data
    assert od["attachments_downloaded"] == []
    assert len(od["attachments_failed"]) == 1
    err = od["attachments_failed"][0]["error"]
    assert "exceeds" in err and "1024" in err, f"unexpected error: {err!r}"
    # Partial file must have been removed.
    attachments_dir = os.path.join(
        tmp_home,
        ".revka/artifacts/Revka/WorkflowOutputs/r-kind/report/attachments",
    )
    if os.path.exists(attachments_dir):
        leftover = os.listdir(attachments_dir)
        assert leftover == [], f"partial file not cleaned up: {leftover}"


# ---------------------------------------------------------------------------
# 13. follow_redirects=True is set on the attachment download client (Fix 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redirect_followed(
    monkeypatch, tmp_home, fake_kumiho_sdk,
):
    """The attachment-download httpx client must be constructed with
    ``follow_redirects=True`` — Manus CDN URLs often 302. We capture the
    constructor kwargs and assert the flag is set."""
    from operator_mcp import revka_config
    monkeypatch.setattr(revka_config, "_cached_manus", None)
    blob_url = "https://manus.ai/files/redirected.bin"
    fc = _make_attachment_url_client(
        poll_responses=[
            _FakeResp(200, {"ok": True, "data": [
                {"id": "e1", "type": "assistant_message",
                 "assistant_message": {
                    "content": "done",
                    "attachments": [
                        {"file_name": "blob.bin", "url": blob_url},
                    ],
                 }},
                {"id": "e2", "type": "status_update",
                 "status_update": {"agent_status": "stopped"}},
            ]}),
        ],
        attachment_responses={blob_url: _BytesResp(200, b"FINAL-BYTES")},
    )

    # Spy the AsyncClient ctor (after _patch_manus's own patch returns the
    # fc). Capture each kwargs dict.
    import httpx
    ctor_calls: list[dict] = []
    original_async_client = httpx.AsyncClient

    def spy_ctor(*args, **kwargs):
        ctor_calls.append(dict(kwargs))
        return fc

    from operator_mcp.workflow import memory as wm
    monkeypatch.setattr(wm, "_ensure_space_path", AsyncMock(return_value=None))

    with patch("httpx.AsyncClient", side_effect=spy_ctor), \
         patch.dict("os.environ", {"MANUS_API_KEY": "fake-test-key"}):
        import operator_mcp.workflow.executor as ex
        with patch.object(ex.asyncio, "sleep", new=lambda _s: None):
            cfg = ManusStepConfig(
                prompt="x",
                register_output=ManusRegisterOutputConfig(
                    entity_name="report",
                    entity_kind="r-kind",
                ),
                **_FAST_CFG,
            )
            result = await _exec_manus(_step(cfg), _state())

    assert result.status == "completed"
    # The attachment download succeeded — confirms the stream path worked.
    assert len(result.output_data["attachments_downloaded"]) == 1
    # And at least one AsyncClient was built with follow_redirects=True
    # (the attachment-download ctor; the poll ctor doesn't need it).
    assert any(c.get("follow_redirects") is True for c in ctor_calls), (
        f"no AsyncClient call had follow_redirects=True: {ctor_calls}"
    )


# ---------------------------------------------------------------------------
# 14. publish raise → step completes with register_output_error (Fix 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_raise_becomes_register_error(
    monkeypatch, tmp_home, fake_kumiho_sdk,
):
    """When publish_workflow_entity raises, the step must still complete
    and surface the error through output_data.register_output_error
    instead of crashing the executor."""
    from operator_mcp import revka_config
    monkeypatch.setattr(revka_config, "_cached_manus", None)
    fc = _make_attachment_url_client(poll_responses=[
        _FakeResp(200, {"ok": True, "data": [
            {"id": "e1", "type": "assistant_message",
             "assistant_message": {"content": "ok"}},
            {"id": "e2", "type": "status_update",
             "status_update": {"agent_status": "stopped"}},
        ]}),
    ])

    from operator_mcp.workflow import memory as wm

    async def boom_publish(**kwargs):
        raise RuntimeError("kumiho gateway down")

    monkeypatch.setattr(wm, "publish_workflow_entity", boom_publish)
    monkeypatch.setattr(wm, "_ensure_space_path", AsyncMock(return_value=None))

    ctxs = _patch_manus(fake_client=fc)
    try:
        _enter_all(ctxs)
        cfg = ManusStepConfig(
            prompt="x",
            register_output=ManusRegisterOutputConfig(
                entity_name="report",
                entity_kind="r-kind",
            ),
            **_FAST_CFG,
        )
        result = await _exec_manus(_step(cfg), _state())
    finally:
        _exit_all(ctxs)

    assert result.status == "completed"
    err = result.output_data.get("register_output_error", "")
    assert "kumiho gateway down" in err


# ---------------------------------------------------------------------------
# 15. empty sanitized entity_name skips registration (Fix 1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_sanitized_entity_name_skips_registration(
    monkeypatch, tmp_home, fake_kumiho_sdk,
):
    """An entity_name made entirely of traversal chars (``"...."``)
    sanitizes to empty. The executor must skip registration, record an
    error, and not crash."""
    from operator_mcp import revka_config
    monkeypatch.setattr(revka_config, "_cached_manus", None)
    fc = _make_attachment_url_client(poll_responses=[
        _FakeResp(200, {"ok": True, "data": [
            {"id": "e1", "type": "assistant_message",
             "assistant_message": {"content": "x"}},
            {"id": "e2", "type": "status_update",
             "status_update": {"agent_status": "stopped"}},
        ]}),
    ])

    publish_calls = []
    from operator_mcp.workflow import memory as wm
    original_publish = wm.publish_workflow_entity

    async def spy_publish(**kwargs):
        publish_calls.append(kwargs)
        return await original_publish(**kwargs)

    monkeypatch.setattr(wm, "publish_workflow_entity", spy_publish)
    monkeypatch.setattr(wm, "_ensure_space_path", AsyncMock(return_value=None))

    ctxs = _patch_manus(fake_client=fc)
    try:
        _enter_all(ctxs)
        cfg = ManusStepConfig(
            prompt="x",
            register_output=ManusRegisterOutputConfig(
                entity_name="....",
                entity_kind="r-kind",
            ),
            **_FAST_CFG,
        )
        result = await _exec_manus(_step(cfg), _state())
    finally:
        _exit_all(ctxs)

    assert result.status == "completed"
    err = result.output_data.get("register_output_error", "")
    assert "invalid after sanitization" in err
    assert publish_calls == []
