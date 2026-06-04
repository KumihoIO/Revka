"""Event-driven workflow chaining for Revka.

Listens for Kumiho ``revision.tagged`` events and triggers matching workflows
based on registered trigger rules.  Each workflow can declare one or more
:class:`TriggerDef` entries (in its YAML ``triggers:`` block) that specify
which entity kind + tag combinations should auto-launch the workflow.

Architecture
------------
* :class:`TriggerRegistry` — pure-data index mapping ``(kind, tag)`` pairs to
  :class:`TriggerRule` objects.  Rebuilt whenever the workflow catalogue changes.
* :class:`WorkflowEventListener` — long-running background task that consumes
  ``kumiho.event_stream(routing_key_filter="revision.tagged")`` in a thread
  executor (the Kumiho SDK stream is synchronous) and schedules workflow
  launches on the async event loop.
* Module-level singleton accessors (:func:`get_trigger_registry`,
  :func:`get_event_listener`, :func:`set_event_listener`) follow the same
  pattern used by the heartbeat monitor and event consumer elsewhere in
  Operator.
"""
from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from operator_mcp.revka_config import harness_project
from operator_mcp.workflow.schema import TriggerDef, WorkflowDef, WorkflowStatus

try:
    from kumiho.mcp_server import tool_tag_revision  # noqa: F401
except ImportError:
    tool_tag_revision = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_logger = logging.getLogger("revka.event_listener")
_LAST_LIMITED_LOG_AT: dict[str, float] = {}
_LIMITED_LOG_INTERVAL_SECS = 300.0


def _log(msg: str) -> None:
    _logger.info(msg)


def _log_limited(
    key: str,
    msg: str,
    *,
    interval: float = _LIMITED_LOG_INTERVAL_SECS,
) -> None:
    now = time.monotonic()
    last = _LAST_LIMITED_LOG_AT.get(key, 0.0)
    if now - last >= interval:
        _LAST_LIMITED_LOG_AT[key] = now
        _log(msg)


def _debug(msg: str) -> None:
    _logger.debug(msg)


# ---------------------------------------------------------------------------
# TriggerRule (lightweight dataclass — one per TriggerDef registration)
# ---------------------------------------------------------------------------

@dataclass
class TriggerRule:
    """A single trigger rule extracted from a workflow's TriggerDef."""

    workflow_name: str
    input_map: dict[str, str] = field(default_factory=dict)
    name_pattern: str = ""
    space_filter: str = ""


# ---------------------------------------------------------------------------
# TriggerRegistry
# ---------------------------------------------------------------------------

class TriggerRegistry:
    """Maps ``(kind, tag)`` pairs to workflow launch rules.

    Call :meth:`rebuild` after loading/reloading workflows so the registry
    stays in sync with the on-disk definitions.
    """

    def __init__(self) -> None:
        self._rules: dict[tuple[str, str], list[TriggerRule]] = {}
        self._workflow_count: int = 0

    # -- Mutation -----------------------------------------------------------

    def register(self, workflow_name: str, trigger: TriggerDef) -> None:
        """Register a single trigger rule for *workflow_name*."""
        key = (trigger.on_kind, trigger.on_tag)
        rule = TriggerRule(
            workflow_name=workflow_name,
            input_map=dict(trigger.input_map),
            name_pattern=trigger.on_name_pattern,
            space_filter=trigger.on_space,
        )
        self._rules.setdefault(key, []).append(rule)

    def rebuild(self, workflows: dict[str, WorkflowDef]) -> None:
        """Rebuild the full registry from a workflow catalogue."""
        self._rules.clear()
        self._workflow_count = 0
        for wf_name, wf_def in workflows.items():
            for trigger in wf_def.triggers:
                self.register(wf_name, trigger)
                self._workflow_count += 1

    def iter_rules(self) -> list[tuple[str, str, TriggerRule]]:
        """Return all registered entity trigger rules with their kind/tag key."""
        out: list[tuple[str, str, TriggerRule]] = []
        for (kind, tag), rules in self._rules.items():
            for rule in rules:
                out.append((kind, tag, rule))
        return out

    # -- Query --------------------------------------------------------------

    def match(self, kind: str, tag: str, name: str = "", space: str = "") -> list[TriggerRule]:
        """Return all rules matching the given entity kind, tag, name, and space.

        Rules without a ``name_pattern`` match any name.  Rules *with* a
        pattern use :func:`fnmatch.fnmatch` for glob matching.
        Rules without a ``space_filter`` match any space.  Rules *with* a
        filter use prefix matching (entity space must start with filter path).
        """
        rules = self._rules.get((kind, tag), [])
        matched = []
        for r in rules:
            # Name filter
            if r.name_pattern and name and not fnmatch.fnmatch(name, r.name_pattern):
                continue
            if r.name_pattern and not name:
                continue
            # Space filter (prefix match)
            if r.space_filter and (not space or not space.startswith(r.space_filter)):
                continue
            matched.append(r)
        return matched

    # -- Introspection ------------------------------------------------------

    @property
    def rule_count(self) -> int:
        """Total number of individual trigger rules registered."""
        return sum(len(v) for v in self._rules.values())

    @property
    def workflow_count(self) -> int:
        """Number of trigger registrations (one per workflow + trigger pair)."""
        return self._workflow_count


# ---------------------------------------------------------------------------
# WorkflowEventListener
# ---------------------------------------------------------------------------

class WorkflowEventListener:
    """Background task that listens for Kumiho ``revision.tagged`` events
    and triggers matching workflows.

    Uses ``kumiho.event_stream(routing_key_filter="revision.tagged")`` to
    receive events in real-time.  When an event matches a registered trigger
    rule, the corresponding workflow is launched with trigger context injected
    into the :class:`WorkflowState`.

    The Kumiho event stream is a **synchronous** iterator, so it runs inside
    :meth:`asyncio.loop.run_in_executor` to avoid blocking the event loop.
    """

    def __init__(
        self,
        registry: TriggerRegistry,
        cwd: str = "",
        cursor_path: str | None = None,
    ) -> None:
        self._registry = registry
        self._cwd = cwd or os.path.expanduser("~")
        self._cursor_path = cursor_path or os.path.expanduser(
            "~/.revka/event_listener_cursor.txt"
        )
        self._task: asyncio.Task[None] | None = None
        self._poll_task: asyncio.Task[None] | None = None
        self._entity_poll_task: asyncio.Task[None] | None = None
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._entity_seen: set[str] = set()
        self._entity_seen_loaded = False

        # Metrics
        self._last_event_at: str | None = None
        self._events_processed: int = 0
        self._workflows_triggered: int = 0
        self._errors: int = 0
        self._started_at: str | None = None

        # Dedup: (entity_name, workflow_name) → monotonic timestamp of last trigger.
        # Prevents the same entity from launching the same workflow multiple
        # times within the cooldown window (e.g. when multiple episode-room
        # runs publish revisions of the same item in quick succession).
        self._trigger_cooldowns: dict[tuple[str, str], float] = {}
        self._TRIGGER_COOLDOWN_SECS = 120.0  # 2-minute cooldown

    # -- Lifecycle ----------------------------------------------------------

    _LISTENER_LOCK_PATH = os.path.expanduser("~/.revka/event_listener.lock")
    _ENTITY_SEEN_PATH = os.path.expanduser("~/.revka/entity_trigger_seen.json")
    _ENTITY_POLL_INTERVAL_SECS = 30.0

    async def start(self) -> None:
        """Start the background listener task (idempotent).

        Uses a file lock so only ONE operator process runs the event
        listener and poller.  Other processes skip silently — they still
        serve MCP tools but don't execute workflows.
        """
        if self._task and not self._task.done():
            return

        # Singleton lock — only one event listener across all operator processes
        from .. import _fcntl_compat as fcntl
        try:
            self._listener_lock_fd = open(self._LISTENER_LOCK_PATH, "w")
            fcntl.flock(self._listener_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._listener_lock_fd.write(f"{os.getpid()}\n")
            self._listener_lock_fd.flush()
        except (OSError, BlockingIOError):
            _log_limited(
                "listener_lock_held",
                "WorkflowEventListener: lock held by another operator, skipping",
            )
            return

        self._running = True
        self._started_at = datetime.utcnow().isoformat() + "Z"
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(
            self._listen_loop(), name="workflow-event-listener"
        )
        self._poll_task = asyncio.create_task(
            self._poll_run_requests(), name="workflow-run-request-poll"
        )
        self._poll_task.add_done_callback(self._poll_done_cb)
        self._entity_poll_task = asyncio.create_task(
            self._poll_entity_triggers(), name="workflow-entity-trigger-poll"
        )
        _log("WorkflowEventListener started (locked)")

    @staticmethod
    def _poll_done_cb(task: asyncio.Task[None]) -> None:
        import sys
        if task.cancelled():
            print("[operator] poll task cancelled", file=sys.stderr, flush=True)
        elif task.exception():
            print(f"[operator] poll task crashed: {task.exception()}", file=sys.stderr, flush=True)
        else:
            print("[operator] poll task finished cleanly", file=sys.stderr, flush=True)

    async def stop(self) -> None:
        """Gracefully stop the background listener."""
        self._running = False
        for t in [
            self._task,
            getattr(self, "_poll_task", None),
            getattr(self, "_entity_poll_task", None),
        ]:
            if t and not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
        _log("WorkflowEventListener stopped")

    def health(self) -> dict[str, Any]:
        """Return health/status snapshot for diagnostics."""
        running = (
            self._running
            and self._task is not None
            and not self._task.done()
        )
        return {
            "status": "running" if running else "stopped",
            "started_at": self._started_at,
            "last_event_at": self._last_event_at,
            "events_processed": self._events_processed,
            "workflows_triggered": self._workflows_triggered,
            "errors": self._errors,
            "registered_triggers": self._registry.rule_count,
            "registered_workflows": self._registry.workflow_count,
        }

    # -- Core loop ----------------------------------------------------------

    async def _listen_loop(self) -> None:
        """Top-level loop with reconnection and exponential backoff."""
        backoff = 5
        while self._running:
            try:
                cursor = self._load_cursor()
                loop = asyncio.get_event_loop()
                # The Kumiho event_stream is synchronous — run in a thread.
                acquired = await loop.run_in_executor(
                    None, self._sync_listen, cursor
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._errors += 1
                _log(
                    f"Event listener error: {exc}, "
                    f"reconnecting in {backoff}s"
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
            else:
                if not acquired:
                    # Another process holds the stream lock — back off and
                    # re-check periodically in case the holder dies.
                    await asyncio.sleep(60)
                    continue
                # Clean exit from _sync_listen (e.g. self._running became
                # False).  Reset backoff for next potential restart.
                backoff = 5

    _STREAM_LOCK_PATH = os.path.expanduser("~/.revka/event_stream.lock")

    def _sync_listen(self, cursor: str | None) -> bool:
        """Synchronous event stream consumption (runs in thread executor).

        Iterates ``kumiho.event_stream`` and delegates each event to
        :meth:`_process_event`.  Exits when ``self._running`` is cleared.

        Uses a file lock so only one operator process consumes the stream,
        preventing duplicate workflow launches.

        Returns ``True`` if the lock was acquired (stream consumed),
        ``False`` if another process held the lock.
        """
        from .. import _fcntl_compat as fcntl
        import kumiho  # type: ignore[import-untyped]

        # Acquire exclusive lock — if another process holds it, exit quietly
        lock_fd = None
        try:
            os.makedirs(os.path.dirname(self._STREAM_LOCK_PATH), exist_ok=True)
            lock_fd = open(self._STREAM_LOCK_PATH, "w")
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_fd.write(str(os.getpid()))
            lock_fd.flush()
        except (IOError, OSError):
            if lock_fd:
                lock_fd.close()
            return False

        _log("event_listener: acquired stream lock, starting event consumption")
        try:
            kwargs: dict[str, Any] = {
                "routing_key_filter": "revision.tagged",
            }
            if cursor:
                kwargs["cursor"] = cursor

            for event in kumiho.event_stream(**kwargs):
                if not self._running:
                    break
                self._process_event(event)
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                lock_fd.close()
            except Exception:
                pass
        return True

    # -- Event processing ---------------------------------------------------

    def _process_event(self, event: Any) -> None:
        """Handle a single ``revision.tagged`` event.

        Steps:
        1. Extract the revision kref and tag from the event payload.
        2. Resolve the revision to its parent item (kind, name, space).
        3. Handle workflow run-request items specially.
        4. Match against the trigger registry.
        5. For each match, schedule a workflow launch on the event loop.
        6. Persist cursor every 10 events.
        """
        import kumiho  # type: ignore[import-untyped]

        self._events_processed += 1
        self._last_event_at = datetime.utcnow().isoformat() + "Z"

        try:
            # -- 1. Extract kref and tag -----------------------------------
            kref_str = str(event.kref) if hasattr(event, "kref") else ""

            tag = ""
            if hasattr(event, "details") and isinstance(event.details, dict):
                tag = event.details.get("tag", "")
            elif hasattr(event, "metadata") and isinstance(event.metadata, dict):
                tag = event.metadata.get("tag", "")

            if not kref_str or not tag:
                return

            # -- 2. Resolve revision -> item --------------------------------
            try:
                rev = kumiho.get_revision(kref_str)
                item_kref = getattr(rev, "item_kref", "")
                if not item_kref:
                    _log(f"Event skipped: revision {kref_str} has no item_kref")
                    return
                item = kumiho.get_item(str(item_kref))
                kind: str = getattr(item, "kind", "")
                name: str = (
                    getattr(item, "item_name", "")
                    or getattr(item, "name", "")
                )
                space: str = getattr(item, "space", "")
            except Exception as exc:
                _log(f"Event skipped: failed to resolve {kref_str}: {exc}")
                return

            # -- 3. Cron-triggered run requests -----------------------------
            # Items of kind 'workflow-run-request' tagged 'pending' represent
            # cron-scheduled workflow launches.  Handle them directly instead
            # of going through the trigger registry.
            if (
                kind == "workflow-run-request"
                and tag == "pending"
                and "WorkflowRunRequests" in space
            ):
                self._handle_run_request(
                    item_kref=str(item_kref),
                    item_metadata={
                        str(k): str(v)
                        for k, v in (
                            getattr(item, "metadata", None) or {}
                        ).items()
                    },
                )
                return

            _debug(f"Event received: kind={kind} tag={tag} name={name} space={space}")

            # -- 4. Match against registry ---------------------------------
            # Non-matching events are the common case. Do not pre-filter by
            # WorkflowOutputs here: workflow output steps can publish to
            # domain-specific spaces, and trigger rules carry their own
            # optional space filter.
            matches = self._registry.match(kind, tag, name, space)
            if not matches:
                _debug(f"Event skipped: no trigger match for kind={kind} tag={tag}")
                return

            # -- 5. Read entity metadata -----------------------------------
            # Item metadata contains user-defined fields from entity_metadata
            # plus source tracking fields (source_workflow, source_run_id, etc.)
            item_metadata: dict[str, str] = {}
            raw_meta = getattr(item, "metadata", None)
            if isinstance(raw_meta, dict):
                item_metadata = {str(k): str(v) for k, v in raw_meta.items()}

            # -- 6. Build trigger context and launch -----------------------
            trigger_ctx: dict[str, str] = {
                "entity_kref": str(item_kref),
                "entity_name": name,
                "entity_kind": kind,
                "tag": tag,
                "revision_kref": kref_str,
                "metadata": json.dumps(item_metadata),
            }
            # Flatten each metadata key as metadata.<key> for interpolation
            # e.g. ${trigger.metadata.part} → value of item_metadata["part"]
            for mk, mv in item_metadata.items():
                trigger_ctx[f"metadata.{mk}"] = mv

            now = time.monotonic()
            self._load_entity_seen()
            saw_new_entity_trigger = False
            for rule in matches:
                seen_key = self._entity_seen_key(kref_str, rule.workflow_name)
                if seen_key in self._entity_seen:
                    continue
                dedup_key = (name, rule.workflow_name)
                last_triggered = self._trigger_cooldowns.get(dedup_key, 0.0)
                if now - last_triggered < self._TRIGGER_COOLDOWN_SECS:
                    _log(f"Event deduped: '{rule.workflow_name}' already triggered "
                         f"for entity '{name}' {now - last_triggered:.0f}s ago "
                         f"(cooldown={self._TRIGGER_COOLDOWN_SECS:.0f}s)")
                    continue
                self._trigger_cooldowns[dedup_key] = now
                self._launch_workflow(rule, trigger_ctx)
                self._entity_seen.add(seen_key)
                saw_new_entity_trigger = True
            if saw_new_entity_trigger:
                self._save_entity_seen()

            # -- 7. Cursor persistence (every event) -------------------------
            event_cursor = getattr(event, "cursor", None)
            if event_cursor:
                self._save_cursor(str(event_cursor))

        except Exception as exc:
            self._errors += 1
            _log(f"Error processing event: {exc}")

    # -- Workflow launch ----------------------------------------------------

    def _launch_workflow(
        self,
        rule: TriggerRule,
        trigger_ctx: dict[str, str],
    ) -> None:
        """Schedule a workflow launch on the async event loop.

        Called from the synchronous thread executor, so we use
        :meth:`loop.call_soon_threadsafe` to bridge into async land.
        """
        # Defensive guard: the captured loop may have closed (e.g. operator-mcp
        # shutting down) while this thread is still iterating events.  Skip
        # rather than raising "Event loop is closed".
        if self._loop is None or self._loop.is_closed():
            _log(
                f"event_listener: loop closed, skipping launch of "
                f"'{rule.workflow_name}'"
            )
            return
        try:
            self._loop.call_soon_threadsafe(
                lambda r=rule, ctx=trigger_ctx: asyncio.ensure_future(
                    self._async_launch(r, ctx)
                )
            )
        except Exception as exc:
            self._errors += 1
            _log(f"Failed to schedule workflow launch: {exc}")

    # -- Cron run-request handling -----------------------------------------

    # Persistent run_id dedup. The Kumiho event stream replays history on
    # gRPC reconnect (every macOS idle, every operator restart, every
    # network blip), so pure in-memory dedup is not enough — a `pending`
    # tag from yesterday gets re-delivered today and we'd re-launch the
    # same run_id.  Persisted to disk so dedup survives restart and
    # cross-process (mirror of the poller's seen-set at `_SEEN_PATH`).
    _CLAIMED_RUNS_PATH = os.path.expanduser("~/.revka/event_listener_claimed_runs.json")
    _claimed_runs: set[str] = set()
    _claimed_runs_loaded: bool = False

    def _load_claimed_runs(self) -> None:
        """Lazy-load the persistent claimed-runs set on first access.

        Class-level mutable default ``set()`` is shared across instances by
        design — this lets unrelated callers within the same process all
        see the same dedup state without plumbing an instance through.
        """
        if self._claimed_runs_loaded:
            return
        try:
            if os.path.exists(self._CLAIMED_RUNS_PATH):
                with open(self._CLAIMED_RUNS_PATH, "r") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        # Cap at 10k to stop the file growing unbounded
                        # over months of usage; fresh entries always
                        # added back as new run-requests arrive.
                        WorkflowEventListener._claimed_runs.update(
                            str(r) for r in data[-10000:]
                        )
        except Exception as exc:
            _log(f"event_listener: failed to load claimed workflow runs: {exc}")
        WorkflowEventListener._claimed_runs_loaded = True

    def _save_claimed_runs(self) -> None:
        """Persist the claimed-runs set after every add. Best-effort —
        a write failure just means dedup falls back to in-memory until
        next successful save."""
        try:
            os.makedirs(os.path.dirname(self._CLAIMED_RUNS_PATH), exist_ok=True)
            with open(self._CLAIMED_RUNS_PATH, "w") as f:
                # Sorted for determinism; capped at 10k newest entries.
                runs = sorted(self._claimed_runs)
                json.dump(runs[-10000:], f)
        except Exception:
            pass

    def _handle_run_request(
        self,
        item_kref: str,
        item_metadata: dict[str, str],
    ) -> None:
        """Schedule a cron-triggered workflow launch on the async loop.

        Called from the synchronous thread executor, bridges to async via
        :meth:`loop.call_soon_threadsafe` — same pattern as
        :meth:`_launch_workflow`.
        """
        # Dedup: skip if this run was ever claimed (persistent across
        # restarts so Kumiho stream replays don't re-launch yesterday's
        # already-completed run_ids).
        self._load_claimed_runs()
        run_id = item_metadata.get("run_id", "")
        if run_id and run_id in self._claimed_runs:
            return

        # Defensive guard: if the captured loop is gone (e.g. operator-mcp
        # shutting down) the schedule will raise "Event loop is closed".
        # Skip without claiming so the durable Kumiho `pending` item is left
        # for the next operator-mcp's recovery / poller.
        if self._loop is None or self._loop.is_closed():
            _log(
                f"event_listener: loop closed, skipping schedule for "
                f"run_id {run_id[:8] if run_id else '?'} — "
                f"Kumiho pending item remains for recovery"
            )
            return

        try:
            self._loop.call_soon_threadsafe(  # type: ignore[union-attr]
                lambda kref=item_kref, meta=item_metadata: asyncio.ensure_future(
                    self._async_run_request(kref, meta)
                )
            )
        except Exception as exc:
            self._errors += 1
            _log(f"Failed to schedule cron run request: {exc}")
            return

        # Only claim the run AFTER we've successfully scheduled it.  If the
        # schedule raises, the pending Kumiho item remains visible to the
        # next operator-mcp via the run-request poller.
        if run_id:
            self._claimed_runs.add(run_id)
            self._save_claimed_runs()

    async def _async_run_request(
        self,
        item_kref: str,
        metadata: dict[str, str],
    ) -> None:
        """Execute a cron-triggered workflow run request.

        Extracts workflow name, inputs, and cwd from item metadata, tags the
        request as ``running``, executes the workflow, then tags it
        ``completed`` or ``failed``.
        """
        from operator_mcp.workflow.loader import resolve_workflow
        from operator_mcp.workflow.executor import execute_workflow

        workflow_name = metadata.get("workflow_name", "")
        inputs_str = metadata.get("inputs", "{}")
        cwd = metadata.get("cwd", "") or self._cwd
        run_id = metadata.get("run_id", "") or str(uuid.uuid4())
        target_step_id = metadata.get("target_step_id", "") or None

        if not workflow_name:
            _log("event_listener: run request missing workflow_name, skipping")
            return

        # Re-fetch the item's CURRENT status from Kumiho before launching.
        # The event stream replays old `tag=pending` events on every gRPC
        # reconnect (network blips, daemon restarts, macOS idle wakeups);
        # the in-memory `_claimed_runs` dedup catches that within a
        # process and the persistent disk file extends across restarts,
        # but a fresh install or wiped state directory still misses.
        # This guard is the durable backstop — same shape as the poller's
        # check at `_poll_run_requests` line ~757.
        try:
            from operator_mcp.operator_mcp import KUMIHO_SDK
            if KUMIHO_SDK._available and item_kref:
                latest_rev = await KUMIHO_SDK.get_latest_revision(
                    item_kref, tag="latest"
                )
                latest_meta = (latest_rev or {}).get("metadata", {}) or {}
                current_status = str(latest_meta.get("status", "")).lower()
                if current_status in ("running", "completed", "failed"):
                    _log(
                        f"event_listener: skipping run request for "
                        f"'{workflow_name}' (run_id={run_id[:8]}) — "
                        f"latest revision already in status='{current_status}' "
                        f"(stream replay or duplicate dispatch)"
                    )
                    # Mark it claimed so future replays of the same event
                    # short-circuit without another Kumiho roundtrip.
                    if run_id:
                        self._claimed_runs.add(run_id)
                        self._save_claimed_runs()
                    return
        except Exception as exc:
            # Best-effort — a Kumiho hiccup here just falls through to
            # the launch attempt, which is the pre-fix behavior.  We log
            # so it's visible without making the runtime worse.
            _log(
                f"event_listener: status pre-check failed for run_id="
                f"{run_id[:8]} ({exc}); proceeding with launch"
            )

        _log(
            f"event_listener: cron-triggered run request for "
            f"workflow '{workflow_name}' (run_id={run_id[:8]})"
        )

        try:
            inputs = json.loads(inputs_str) if inputs_str else {}
        except json.JSONDecodeError:
            _log(f"event_listener: malformed inputs JSON for '{workflow_name}', using empty dict")
            inputs = {}

        # Tag as running
        await self._tag_run_request(item_kref, "running")

        try:
            resolved = await resolve_workflow(workflow_name)
        except Exception as exc:
            # Malformed stored YAML (Pydantic schema violation, etc.) must
            # not escape as an unretrieved task exception — tag failed so
            # the UI can reflect it and move on.
            self._errors += 1
            import traceback
            _log(
                f"event_listener: failed to resolve '{workflow_name}': {exc}\n"
                f"{traceback.format_exc()}"
            )
            await self._tag_run_request(item_kref, "failed", status_detail=f"resolve_error: {str(exc)[:450]}")
            return

        if resolved is None:
            _log(f"event_listener: workflow '{workflow_name}' not found")
            await self._tag_run_request(item_kref, "failed", status_detail="workflow_not_found")
            return
        wf, wf_item_kref, wf_rev_kref = resolved

        try:
            _log(f"event_listener: starting cron-triggered workflow '{workflow_name}'")
            state = await execute_workflow(
                wf,
                inputs,
                cwd,
                run_id=run_id,
                trigger_context={"trigger_source": "cron", "run_request_kref": item_kref},
                workflow_item_kref=wf_item_kref,
                workflow_revision_kref=wf_rev_kref,
                target_step_id=target_step_id,
            )
            self._workflows_triggered += 1
            _log(
                f"event_listener: cron-triggered '{workflow_name}' "
                f"completed with status={state.status}"
            )
            if state.status == WorkflowStatus.COMPLETED:
                await self._tag_run_request(item_kref, "completed")
            elif (
                state.status == WorkflowStatus.CANCELLED
                and "Duplicate execution prevented by run lock" in (state.error or "")
            ):
                _log(
                    f"event_listener: run_id={run_id[:8]} is already owned by "
                    "another executor; leaving request status unchanged"
                )
            else:
                self._errors += 1
                detail = state.error or f"workflow_status:{state.status.value}"
                await self._tag_run_request(
                    item_kref,
                    "failed",
                    status_detail=detail[:500],
                )
        except Exception as exc:
            self._errors += 1
            import traceback
            _log(f"event_listener: cron-triggered '{workflow_name}' failed: {exc}\n{traceback.format_exc()}")
            await self._tag_run_request(item_kref, "failed", status_detail=str(exc)[:500])

    async def _tag_run_request(
        self,
        item_kref: str,
        tag: str,
        status_detail: str = "",
    ) -> None:
        """Create a new revision on the run-request item with a status tag.

        Also tags the revision as ``latest`` so the poller can look up
        current status via ``get_latest_revision(kref, tag="latest")``.

        Best-effort — failures are logged but never propagated.
        """
        try:
            from operator_mcp.operator_mcp import KUMIHO_SDK

            if not KUMIHO_SDK._available or not item_kref:
                return
            meta: dict[str, Any] = {"status": tag}
            if status_detail:
                meta["status_detail"] = status_detail
            rev = await KUMIHO_SDK.create_revision(item_kref, meta, tag=tag)
            # Also tag as "latest" so pollers can look up current status
            if rev and rev.get("kref"):
                try:
                    await asyncio.to_thread(tool_tag_revision, rev["kref"], "latest")
                except Exception:
                    pass
        except Exception as exc:
            _log(f"event_listener: failed to tag run request as '{tag}': {exc}")

    # -- Poll-based run request pickup ------------------------------------

    # -- Persistent seen-set helpers -----------------------------------------

    _SEEN_PATH = os.path.expanduser("~/.revka/poller_seen.json")
    _LOCK_PATH = os.path.expanduser("~/.revka/poller.lock")

    def _load_seen(self) -> set[str]:
        """Load persistent seen-set from disk."""
        try:
            if os.path.exists(self._SEEN_PATH):
                with open(self._SEEN_PATH, "r") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return set(data)
        except Exception:
            pass
        return set()

    def _save_seen(self, seen: set[str]) -> None:
        """Persist seen-set to disk."""
        try:
            os.makedirs(os.path.dirname(self._SEEN_PATH), exist_ok=True)
            with open(self._SEEN_PATH, "w") as f:
                json.dump(sorted(seen), f)
        except Exception:
            pass

    async def _poll_run_requests(self) -> None:
        """Poll Kumiho for unprocessed workflow-run-request items.

        The gateway creates run-request items but may not tag them (the
        revision.tagged event never fires).  This poller catches those
        orphaned requests.

        Uses a file lock so only one poller runs across all operator
        processes, and a persistent seen-set to survive restarts.
        """
        import sys as _sys
        from .. import _fcntl_compat as fcntl

        # Acquire exclusive lock — if another process holds it, exit quietly
        lock_fd = None
        try:
            os.makedirs(os.path.dirname(self._LOCK_PATH), exist_ok=True)
            lock_fd = open(self._LOCK_PATH, "w")
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_fd.write(str(os.getpid()))
            lock_fd.flush()
        except (IOError, OSError):
            _log_limited(
                "poller_lock_held",
                "event_listener: poller lock held by another process, skipping",
            )
            if lock_fd:
                lock_fd.close()
            return

        _seen = self._load_seen()
        print("[operator] event_listener: run-request poller started (locked)", file=_sys.stderr, flush=True)
        _log("event_listener: run-request poller started (locked)")

        try:
            while self._running:
                try:
                    await asyncio.sleep(30)
                    from operator_mcp.operator_mcp import KUMIHO_SDK
                    if not KUMIHO_SDK._available:
                        continue

                    items = await KUMIHO_SDK.list_items(
                        f"/{harness_project()}/WorkflowRunRequests"
                    )
                    for item in items:
                        kref = item.get("kref", "")
                        if not kref or kref in _seen:
                            continue
                        kind = item.get("kind", "")
                        if kind != "workflow-run-request":
                            continue
                        # Read latest revision to get actual metadata
                        try:
                            rev = await KUMIHO_SDK.get_latest_revision(kref, tag="latest")
                            meta = rev.get("metadata", {}) if rev else {}
                        except Exception:
                            meta = item.get("metadata", {})
                        status = meta.get("status", "")
                        if status in ("running", "completed", "failed"):
                            _seen.add(kref)
                            self._save_seen(_seen)
                            continue
                        # Unprocessed request — pick it up
                        _seen.add(kref)
                        self._save_seen(_seen)
                        item_metadata = {
                            str(k): str(v) for k, v in meta.items()
                        }
                        _log(f"event_listener: poll picked up run request wf={meta.get('workflow_name','')} run={meta.get('run_id','')[:8]}")
                        await self._async_run_request(kref, item_metadata)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    import traceback
                    _log(f"event_listener: poll error: {exc}\n{traceback.format_exc()}")
                    await asyncio.sleep(10)
        finally:
            try:
                from .. import _fcntl_compat as _fcntl
                _fcntl.flock(lock_fd, _fcntl.LOCK_UN)
                lock_fd.close()
            except Exception:
                pass

    # -- Poll-based entity trigger pickup ----------------------------------

    def _load_entity_seen(self) -> None:
        if self._entity_seen_loaded:
            return
        try:
            if os.path.exists(self._ENTITY_SEEN_PATH):
                with open(self._ENTITY_SEEN_PATH, "r") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self._entity_seen = {str(v) for v in data[-10_000:]}
        except Exception:
            self._entity_seen = set()
        self._entity_seen_loaded = True

    def _save_entity_seen(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._ENTITY_SEEN_PATH), exist_ok=True)
            data = sorted(self._entity_seen)[-10_000:]
            with open(self._ENTITY_SEEN_PATH, "w") as f:
                json.dump(data, f)
        except Exception as exc:
            _log(f"event_listener: failed to persist entity trigger seen-set: {exc}")

    @staticmethod
    def _entity_seen_key(revision_kref: str, workflow_name: str) -> str:
        return f"{revision_kref}|{workflow_name}"

    async def _poll_entity_triggers(self) -> None:
        """Poll registered entity trigger spaces as a durable event-stream fallback."""
        _log("event_listener: entity trigger poller started")
        while self._running:
            try:
                await asyncio.sleep(self._ENTITY_POLL_INTERVAL_SECS)
                triggered = await self._poll_entity_triggers_once()
                if triggered:
                    _log(f"event_listener: entity poll triggered {triggered} workflow(s)")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                import traceback
                _log(f"event_listener: entity poll error: {exc}\n{traceback.format_exc()}")
                await asyncio.sleep(10)

    async def _poll_entity_triggers_once(self) -> int:
        """Scan trigger spaces for tagged entity revisions missed by the stream."""
        from operator_mcp.operator_mcp import KUMIHO_SDK

        if not KUMIHO_SDK._available:
            return 0
        self._load_entity_seen()

        scan_specs = {
            (rule.space_filter, kind, tag)
            for kind, tag, rule in self._registry.iter_rules()
            if kind and tag and rule.space_filter
        }
        if not scan_specs:
            return 0

        triggered = 0
        for space_filter, kind, tag in sorted(scan_specs):
            items = await KUMIHO_SDK.list_items(space_filter)
            for item in items:
                item_kind = str(item.get("kind", ""))
                if item_kind != kind:
                    continue
                item_kref = str(item.get("kref", ""))
                if not item_kref:
                    continue
                name = str(item.get("item_name", item.get("name", "")))
                space = str(item.get("space", "") or space_filter)

                matches = self._registry.match(kind, tag, name, space)
                if not matches:
                    continue

                rev = await KUMIHO_SDK.get_latest_revision(item_kref, tag=tag)
                if not rev:
                    continue
                rev_tags = rev.get("tags")
                if isinstance(rev_tags, list) and tag not in {str(t) for t in rev_tags}:
                    continue
                rev_kref = str(rev.get("kref", ""))
                if not rev_kref:
                    continue

                raw_meta = item.get("metadata", None)
                item_metadata = (
                    {str(k): str(v) for k, v in raw_meta.items()}
                    if isinstance(raw_meta, dict)
                    else {}
                )
                trigger_ctx: dict[str, str] = {
                    "entity_kref": item_kref,
                    "entity_name": name,
                    "entity_kind": kind,
                    "tag": tag,
                    "revision_kref": rev_kref,
                    "metadata": json.dumps(item_metadata),
                }
                for mk, mv in item_metadata.items():
                    trigger_ctx[f"metadata.{mk}"] = mv

                now = time.monotonic()
                for rule in matches:
                    seen_key = self._entity_seen_key(rev_kref, rule.workflow_name)
                    if seen_key in self._entity_seen:
                        continue
                    dedup_key = (name, rule.workflow_name)
                    last_triggered = self._trigger_cooldowns.get(dedup_key, 0.0)
                    if now - last_triggered < self._TRIGGER_COOLDOWN_SECS:
                        continue
                    self._trigger_cooldowns[dedup_key] = now
                    _log(
                        f"Trigger poll: launching '{rule.workflow_name}' "
                        f"for {kind}:{name} tag={tag}"
                    )
                    self._launch_workflow(rule, trigger_ctx)
                    self._entity_seen.add(seen_key)
                    triggered += 1

        if triggered:
            self._save_entity_seen()
        return triggered

    # -- Entity-trigger workflow launch ------------------------------------

    async def _async_launch(self, rule: TriggerRule, trigger_ctx: dict[str, str]) -> None:
        """Actually launch the triggered workflow."""
        from operator_mcp.workflow.loader import resolve_workflow
        from operator_mcp.workflow.executor import execute_workflow

        resolved = await resolve_workflow(rule.workflow_name)
        if not resolved:
            _log(f"Trigger: workflow '{rule.workflow_name}' not found")
            return
        wf, wf_item_kref, wf_rev_kref = resolved

        # Build inputs from trigger context + input_map
        inputs: dict[str, Any] = {}
        for input_name, template in rule.input_map.items():
            val = template
            for key, value in trigger_ctx.items():
                val = val.replace(f"${{trigger.{key}}}", value)
            inputs[input_name] = val

        # Auto-map: for required inputs not explicitly mapped, check entity
        # metadata for a matching key.  This lets upstream workflows pass
        # values to downstream workflows by storing them in entity_metadata
        # with names that match the downstream input names.
        entity_meta: dict[str, str] = {}
        raw_meta = trigger_ctx.get("metadata", "{}")
        try:
            entity_meta = json.loads(raw_meta) if raw_meta else {}
        except (json.JSONDecodeError, TypeError):
            pass

        for inp_def in wf.inputs:
            if inp_def.name in inputs:
                continue  # Already mapped explicitly
            if inp_def.name in entity_meta:
                inputs[inp_def.name] = entity_meta[inp_def.name]
                _log(f"Trigger: auto-mapped input '{inp_def.name}' from entity metadata")

        run_id = str(uuid.uuid4())
        _log(f"Trigger: launching '{rule.workflow_name}' (run_id={run_id[:8]}) "
             f"triggered by {trigger_ctx.get('entity_kind', '?')}:{trigger_ctx.get('entity_name', '?')}")

        try:
            state = await execute_workflow(
                wf, inputs, self._cwd,
                run_id=run_id,
                trigger_context=trigger_ctx,
                workflow_item_kref=wf_item_kref,
                workflow_revision_kref=wf_rev_kref,
            )
            self._workflows_triggered += 1
            _log(f"Trigger: '{rule.workflow_name}' completed with status={state.status}")
        except Exception as e:
            self._errors += 1
            _log(f"Trigger: '{rule.workflow_name}' failed: {e}")

    # -- Cursor persistence -------------------------------------------------

    def _load_cursor(self) -> str | None:
        """Load the last saved event cursor from disk."""
        try:
            if os.path.exists(self._cursor_path):
                with open(self._cursor_path, "r") as fh:
                    return fh.read().strip() or None
        except Exception:
            pass
        return None

    def _save_cursor(self, cursor: str) -> None:
        """Persist the event cursor to disk for resumption after restart."""
        try:
            os.makedirs(os.path.dirname(self._cursor_path), exist_ok=True)
            with open(self._cursor_path, "w") as fh:
                fh.write(cursor)
        except Exception as exc:
            _log(f"Failed to save cursor: {exc}")


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_REGISTRY = TriggerRegistry()
_LISTENER: WorkflowEventListener | None = None


def get_trigger_registry() -> TriggerRegistry:
    """Return the module-level trigger registry singleton."""
    return _REGISTRY


def get_event_listener() -> WorkflowEventListener | None:
    """Return the current event listener (if set)."""
    return _LISTENER


def set_event_listener(listener: WorkflowEventListener) -> None:
    """Set the module-level event listener singleton."""
    global _LISTENER
    _LISTENER = listener
