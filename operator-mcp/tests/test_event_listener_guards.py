"""Tests for WorkflowEventListener defensive guards.

Covers the "Event loop is closed" failure path: when operator-mcp's main
asyncio loop closes but the synchronous Kumiho stream thread keeps firing,
``_handle_run_request`` and ``_launch_workflow`` must skip-and-log instead
of permanently claiming a run_id we can't actually schedule.
"""
from __future__ import annotations

import asyncio

import pytest

from operator_mcp.workflow.event_listener import (
    TriggerRegistry,
    TriggerRule,
    WorkflowEventListener,
)


def _make_listener() -> WorkflowEventListener:
    return WorkflowEventListener(TriggerRegistry(), cwd="/tmp")


def test_handle_run_request_skips_when_loop_closed():
    """Closed loop ⇒ skip without claiming run_id."""
    listener = _make_listener()

    closed_loop = asyncio.new_event_loop()
    closed_loop.close()
    listener._loop = closed_loop

    listener._handle_run_request(
        item_kref="kref://Construct/WorkflowRunRequests/run-1",
        item_metadata={"run_id": "abc-123", "workflow_name": "wf"},
    )

    # The whole point: run_id must NOT be claimed, so the next operator-mcp
    # process (or the run-request poller) can still pick this up.
    assert "abc-123" not in listener._claimed_runs


def test_handle_run_request_skips_when_loop_none():
    """``_loop is None`` (start() never called) ⇒ skip without claiming."""
    listener = _make_listener()
    listener._loop = None

    listener._handle_run_request(
        item_kref="kref://x",
        item_metadata={"run_id": "no-loop-run", "workflow_name": "wf"},
    )

    assert "no-loop-run" not in listener._claimed_runs


def test_handle_run_request_claims_only_after_successful_schedule():
    """On a healthy loop, the run_id is claimed AFTER scheduling succeeds."""
    listener = _make_listener()

    loop = asyncio.new_event_loop()
    try:
        listener._loop = loop
        listener._handle_run_request(
            item_kref="kref://x",
            item_metadata={"run_id": "happy-run", "workflow_name": "wf"},
        )
        # call_soon_threadsafe scheduled the wrapper; we don't run it, just
        # confirm the claim happened on the success path.
        assert "happy-run" in listener._claimed_runs
    finally:
        loop.close()


def test_handle_run_request_does_not_reclaim_already_claimed():
    """Dedup short-circuit: an already-claimed run_id is a no-op."""
    listener = _make_listener()
    listener._claimed_runs.add("dup-run")

    # Even with a dead loop, the dedup check fires first → no exception.
    closed_loop = asyncio.new_event_loop()
    closed_loop.close()
    listener._loop = closed_loop

    listener._handle_run_request(
        item_kref="kref://x",
        item_metadata={"run_id": "dup-run", "workflow_name": "wf"},
    )
    # Still present, no crash.
    assert "dup-run" in listener._claimed_runs


def test_launch_workflow_skips_when_loop_closed():
    """Entity-trigger launch path also guards against closed loops."""
    listener = _make_listener()

    closed_loop = asyncio.new_event_loop()
    closed_loop.close()
    listener._loop = closed_loop

    rule = TriggerRule(workflow_name="some-wf", input_map={})
    # Should not raise — the guard takes the early-return path.
    listener._launch_workflow(rule, trigger_ctx={"entity_name": "e"})


def test_handle_run_request_does_not_claim_on_schedule_exception(monkeypatch):
    """If call_soon_threadsafe itself raises (race past the is_closed guard),
    the run_id must not end up claimed."""
    listener = _make_listener()

    class FakeLoop:
        def is_closed(self) -> bool:
            return False

        def call_soon_threadsafe(self, *_args, **_kwargs):
            raise RuntimeError("Event loop is closed")

    listener._loop = FakeLoop()  # type: ignore[assignment]

    listener._handle_run_request(
        item_kref="kref://x",
        item_metadata={"run_id": "race-run", "workflow_name": "wf"},
    )

    assert "race-run" not in listener._claimed_runs
