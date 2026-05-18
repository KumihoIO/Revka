"""Tests for WorkflowEventListener defensive guards.

Covers the "Event loop is closed" failure path: when operator-mcp's main
asyncio loop closes but the synchronous Kumiho stream thread keeps firing,
``_handle_run_request`` and ``_launch_workflow`` must skip-and-log instead
of permanently claiming a run_id we can't actually schedule.
"""
from __future__ import annotations

import asyncio
import sys
import types

import pytest

from operator_mcp.workflow.event_listener import (
    _LAST_LIMITED_LOG_AT,
    _log_limited,
    TriggerRegistry,
    TriggerRule,
    WorkflowEventListener,
)
from operator_mcp.workflow.schema import TriggerDef


def _make_listener() -> WorkflowEventListener:
    return WorkflowEventListener(TriggerRegistry(), cwd="/tmp")


@pytest.fixture(autouse=True)
def _isolate_claimed_runs_persistence(tmp_path, monkeypatch):
    """`_handle_run_request` now writes to `_CLAIMED_RUNS_PATH` whenever
    a run is claimed. That path defaults to `~/.construct/event_listener_claimed_runs.json`
    — running the test suite would silently overwrite production dedup
    state. Redirect to tmp_path for every test, and reset the class-level
    in-memory state so tests don't bleed into each other."""
    monkeypatch.setattr(
        WorkflowEventListener,
        "_CLAIMED_RUNS_PATH",
        str(tmp_path / "claimed_runs.json"),
    )
    monkeypatch.setattr(
        WorkflowEventListener,
        "_ENTITY_SEEN_PATH",
        str(tmp_path / "entity_trigger_seen.json"),
    )
    WorkflowEventListener._claimed_runs.clear()
    WorkflowEventListener._claimed_runs_loaded = False
    _LAST_LIMITED_LOG_AT.clear()
    yield
    WorkflowEventListener._claimed_runs.clear()
    WorkflowEventListener._claimed_runs_loaded = False
    _LAST_LIMITED_LOG_AT.clear()


def test_log_limited_throttles_repeated_messages(monkeypatch):
    messages: list[str] = []
    monkeypatch.setattr("operator_mcp.workflow.event_listener._log", messages.append)

    _log_limited("same", "first", interval=300)
    _log_limited("same", "second", interval=300)
    _log_limited("other", "third", interval=300)

    assert messages == ["first", "third"]


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


def test_process_event_matches_domain_specific_output_space(tmp_path, monkeypatch):
    """Entity triggers must not be limited to Construct/WorkflowOutputs.

    Workflows can publish output entities to domain spaces such as
    CrossChronicle/ArcBlueprints, and trigger rules should match those spaces
    directly.
    """
    registry = TriggerRegistry()
    registry.register(
        "cross-chronicle-episode-room",
        TriggerDef(
            on_kind="arc-blueprint",
            on_tag="ready",
            on_name_pattern="cc-vol-*",
            on_space="CrossChronicle/ArcBlueprints",
            input_map={"arc_kref": "${trigger.entity_kref}"},
        ),
    )
    listener = WorkflowEventListener(
        registry,
        cwd="/tmp",
        cursor_path=str(tmp_path / "event_listener_cursor.txt"),
    )

    revision = types.SimpleNamespace(
        item_kref="kref://Construct/CrossChronicle/ArcBlueprints/cc-vol-1-arc-3.arc-blueprint",
    )
    item = types.SimpleNamespace(
        kind="arc-blueprint",
        item_name="cc-vol-1-arc-3",
        space="CrossChronicle/ArcBlueprints",
        metadata={"arc_number": "3"},
    )
    fake_kumiho = types.SimpleNamespace(
        get_revision=lambda _kref: revision,
        get_item=lambda _kref: item,
    )
    monkeypatch.setitem(sys.modules, "kumiho", fake_kumiho)

    launches: list[tuple[TriggerRule, dict[str, str]]] = []
    monkeypatch.setattr(
        listener,
        "_launch_workflow",
        lambda rule, ctx: launches.append((rule, ctx)),
    )

    event = types.SimpleNamespace(
        kref="kref://Construct/CrossChronicle/ArcBlueprints/cc-vol-1-arc-3.arc-blueprint?r=9",
        details={"tag": "ready"},
        cursor="cursor-1",
    )
    listener._process_event(event)

    assert len(launches) == 1
    rule, trigger_ctx = launches[0]
    assert rule.workflow_name == "cross-chronicle-episode-room"
    assert trigger_ctx["entity_kind"] == "arc-blueprint"
    assert trigger_ctx["entity_name"] == "cc-vol-1-arc-3"
    assert trigger_ctx["metadata.arc_number"] == "3"


@pytest.mark.asyncio
async def test_entity_trigger_poller_picks_up_domain_output(monkeypatch):
    """Entity trigger polling catches tagged outputs missed by the event stream."""
    registry = TriggerRegistry()
    registry.register(
        "cross-chronicle-episode-room",
        TriggerDef(
            on_kind="arc-blueprint",
            on_tag="ready",
            on_name_pattern="cc-vol-*",
            on_space="CrossChronicle/ArcBlueprints",
            input_map={"arc_kref": "${trigger.entity_kref}"},
        ),
    )
    listener = WorkflowEventListener(registry, cwd="/tmp")

    class FakeSDK:
        _available = True

        async def list_items(self, space):
            assert space == "CrossChronicle/ArcBlueprints"
            return [
                {
                    "kref": "kref://CrossChronicle/ArcBlueprints/cc-vol-1-arc-6.arc-blueprint",
                    "kind": "arc-blueprint",
                    "item_name": "cc-vol-1-arc-6",
                    "space": "CrossChronicle/ArcBlueprints",
                    "metadata": {"episode_start": "16"},
                }
            ]

        async def get_latest_revision(self, item_kref, tag=None):
            assert tag == "ready"
            return {
                "kref": f"{item_kref}?r=1",
                "tags": ["latest", "ready"],
            }

    import operator_mcp.operator_mcp as op_mod
    monkeypatch.setattr(op_mod, "KUMIHO_SDK", FakeSDK(), raising=False)

    launches: list[tuple[TriggerRule, dict[str, str]]] = []
    monkeypatch.setattr(
        listener,
        "_launch_workflow",
        lambda rule, ctx: launches.append((rule, ctx)),
    )

    assert await listener._poll_entity_triggers_once() == 1
    assert len(launches) == 1
    rule, trigger_ctx = launches[0]
    assert rule.workflow_name == "cross-chronicle-episode-room"
    assert trigger_ctx["entity_name"] == "cc-vol-1-arc-6"
    assert trigger_ctx["metadata.episode_start"] == "16"

    # Persistent seen-set prevents duplicate launches for the same revision.
    assert await listener._poll_entity_triggers_once() == 0
    assert len(launches) == 1


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


# -- Ghost-run regression guards -----------------------------------------
#
# Real-world incident: same `run_id` got launched 60+ times because
#   1. Kumiho's gRPC event stream replays history on reconnect (every
#      macOS idle wakeup), so the same `tag=pending` event arrived
#      repeatedly,
#   2. `_claimed_runs` was in-memory only — daemon restart wiped it,
#   3. `_async_run_request` had no current-status pre-check, so a stale
#      pending event whose item already had `metadata.status="completed"`
#      still ran the workflow again.
# These tests pin the three fixes.


def test_claimed_runs_persist_across_simulated_restart(tmp_path):
    """First listener claims a run_id; a fresh listener instance with
    the dedup-loaded flag reset (simulating a restart) observes the
    same run_id as already-claimed."""
    first = _make_listener()

    loop = asyncio.new_event_loop()
    try:
        first._loop = loop
        first._handle_run_request(
            item_kref="kref://x",
            item_metadata={"run_id": "persistent-run", "workflow_name": "wf"},
        )
        assert "persistent-run" in WorkflowEventListener._claimed_runs

        # Simulate restart: clear in-memory state but keep the on-disk
        # file written by _save_claimed_runs above.
        WorkflowEventListener._claimed_runs.clear()
        WorkflowEventListener._claimed_runs_loaded = False

        second = _make_listener()
        second._load_claimed_runs()
        assert "persistent-run" in WorkflowEventListener._claimed_runs, (
            "claimed_runs file did not survive simulated restart — "
            "ghost-run dedup will fail across daemon restarts"
        )
    finally:
        loop.close()


def test_load_claimed_runs_caps_at_10k(tmp_path):
    """File grows unbounded over months otherwise. Newest entries kept."""
    import json
    huge = [f"run-{i}" for i in range(10_500)]
    with open(WorkflowEventListener._CLAIMED_RUNS_PATH, "w") as f:
        json.dump(huge, f)

    listener = _make_listener()
    listener._load_claimed_runs()

    assert len(WorkflowEventListener._claimed_runs) == 10_000
    assert "run-10499" in WorkflowEventListener._claimed_runs
    assert "run-0" not in WorkflowEventListener._claimed_runs


@pytest.mark.asyncio
async def test_async_run_request_skips_when_latest_status_is_completed(
    tmp_path, monkeypatch
):
    """The must-fix guard: a stream-replayed `tag=pending` event whose
    item's latest revision already has `status=completed` must NOT
    re-execute the workflow."""
    listener = _make_listener()

    class FakeSDK:
        _available = True

        async def get_latest_revision(self, kref, tag=None):
            return {"metadata": {"status": "completed"}}

    import operator_mcp.operator_mcp as op_mod
    monkeypatch.setattr(op_mod, "KUMIHO_SDK", FakeSDK(), raising=False)

    # If the guard works, neither resolve_workflow nor execute_workflow
    # should be reached. Patch them to raise loudly if they are.
    from operator_mcp.workflow import loader, executor

    async def _explode_resolve(*_a, **_kw):
        raise AssertionError("resolve_workflow called for already-completed run")

    async def _explode_execute(*_a, **_kw):
        raise AssertionError("execute_workflow called for already-completed run")

    monkeypatch.setattr(loader, "resolve_workflow", _explode_resolve)
    monkeypatch.setattr(executor, "execute_workflow", _explode_execute)

    await listener._async_run_request(
        item_kref="kref://Construct/WorkflowRunRequests/run-x",
        metadata={
            "workflow_name": "blog-writer",
            "run_id": "ghost-run-1",
            "inputs": "{}",
        },
    )

    # Side benefit: the guard claims the run_id so subsequent stream
    # replays short-circuit before even hitting Kumiho again.
    assert "ghost-run-1" in WorkflowEventListener._claimed_runs


@pytest.mark.asyncio
async def test_async_run_request_proceeds_when_latest_status_is_pending(
    tmp_path, monkeypatch
):
    """Inverse guard — a legitimate pending request must still launch.
    Pinning this so a future over-eager guard doesn't break the run-
    request feature entirely."""
    listener = _make_listener()

    class FakeSDK:
        _available = True

        async def get_latest_revision(self, kref, tag=None):
            return {"metadata": {"status": "pending"}}

    import operator_mcp.operator_mcp as op_mod
    monkeypatch.setattr(op_mod, "KUMIHO_SDK", FakeSDK(), raising=False)

    resolve_called = {"hit": False}

    async def _capture_resolve(*_a, **_kw):
        resolve_called["hit"] = True
        # Return None → workflow_not_found path short-circuits the rest
        # without needing a full executor mock.
        return None

    from operator_mcp.workflow import loader
    monkeypatch.setattr(loader, "resolve_workflow", _capture_resolve)

    async def _noop_tag(*_a, **_kw):
        return None
    monkeypatch.setattr(listener, "_tag_run_request", _noop_tag)

    await listener._async_run_request(
        item_kref="kref://Construct/WorkflowRunRequests/run-y",
        metadata={
            "workflow_name": "blog-writer",
            "run_id": "live-run-1",
            "inputs": "{}",
        },
    )

    assert resolve_called["hit"], (
        "pending request should have proceeded past the status guard"
    )
