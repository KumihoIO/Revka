"""Durable workflow recovery — resume interrupted runs after operator restart.

On operator process restart, any in-flight workflow execution is lost because
`execute_workflow()` runs as an asyncio coroutine in-memory.  However:

  - Step results are persisted to Kumiho after each step completes.
  - Sidecar agents may still be alive (they outlive the operator process).
  - `execute_workflow()` accepts `resume_state` to skip completed steps.

This module bridges the gap:

  1. Finds runs stuck in "running" state via Kumiho.
  2. Probes the sidecar for surviving agents (matched by title convention
     `wf-{run_id[:8]}-{step_id}`).
  3. Harvests output from completed/idle agents; waits briefly for still-running
     agents; marks dead agents' steps as failed.
  4. Builds a WorkflowState with pre-populated step_results and calls
     execute_workflow(resume_state=state) as a background task.

Uses a file lock (~/.construct/recovery.lock) so only one operator process
performs recovery even when multiple processes start simultaneously.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

from .._log import _log


_LOCK_PATH = os.path.expanduser("~/.construct/recovery.lock")
_RUN_LOCK_DIR = os.path.expanduser("~/.construct/workflow_locks")
_CHECKPOINT_DIR = os.path.expanduser("~/.construct/workflow_checkpoints")

# Statuses that mean the run is finished — don't resurrect them.
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


def _local_checkpoint_status(run_id: str) -> str | None:
    """Return the on-disk checkpoint's status for a run, or None if absent.

    The local checkpoint is authoritative for terminal status: the Kumiho
    persist on workflow finalize is best-effort, so a failed run can leave
    Kumiho metadata at "running" even though the checkpoint correctly says
    "failed". Recovery uses this as the trump card.
    """
    path = os.path.join(_CHECKPOINT_DIR, f"{run_id}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f).get("status")
    except Exception:
        return None

# How long to wait for a still-running agent before giving up (seconds)
_AGENT_HARVEST_TIMEOUT = 30.0

# How long to poll a running agent before declaring it unharvestable
_AGENT_POLL_INTERVAL = 2.0


async def recover_interrupted_runs(sidecar: Any) -> list[str]:
    """Attempt to recover all interrupted workflow runs.

    Called once during operator startup, after reconnect_agents() has
    re-populated the AGENTS dict.

    Args:
        sidecar: SessionManagerClient instance for querying live agents.

    Returns:
        List of run_ids that were submitted for recovery.
    """
    from .. import _fcntl_compat as fcntl

    # ---- Singleton lock ----
    try:
        lock_fd = open(_LOCK_PATH, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        _log("recovery: lock held by another process, skipping")
        return []

    try:
        return await _do_recovery(sidecar)
    except Exception as exc:
        _log(f"recovery: unexpected error: {exc}")
        import traceback
        _log(traceback.format_exc())
        return []
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
        except Exception:
            pass


async def _do_recovery(sidecar: Any) -> list[str]:
    """Core recovery logic."""
    from .memory import find_running_runs, reconstruct_step_results

    # Phase 1: Find interrupted runs in Kumiho
    running_runs = await find_running_runs()
    if not running_runs:
        _log("recovery: no interrupted runs found")
        return []

    _log(f"recovery: found {len(running_runs)} interrupted run(s)")

    # Phase 2: Get all live sidecar agents (one query for all runs)
    sidecar_agents = await _list_sidecar_agents(sidecar)
    _log(f"recovery: sidecar reports {len(sidecar_agents)} live agent(s)")

    recovered_ids: list[str] = []

    for run_info in running_runs:
        run_id = run_info["run_id"]
        workflow_name = run_info["workflow_name"]
        metadata = run_info["metadata"]

        if not run_id or not workflow_name:
            _log(f"recovery: skipping run with missing id/name: {run_info.get('kref')}")
            continue

        _log(f"recovery: processing run={run_id[:8]} workflow={workflow_name}")

        try:
            ok = await _recover_one_run(
                run_id=run_id,
                workflow_name=workflow_name,
                metadata=metadata,
                sidecar=sidecar,
                sidecar_agents=sidecar_agents,
            )
            if ok:
                recovered_ids.append(run_id)
        except Exception as exc:
            _log(f"recovery: failed to recover run={run_id[:8]}: {exc}")
            # Mark run as failed so we don't retry it indefinitely
            await _mark_run_failed(run_info["kref"], metadata, str(exc))

    if recovered_ids:
        _log(f"recovery: submitted {len(recovered_ids)} run(s) for resumption")
    return recovered_ids


def _acquire_run_lock(run_id: str) -> Any:
    """Acquire a per-run lock file. Returns the fd if acquired, None if already held.

    The lock is held for the lifetime of the resumed execution so that
    other operator processes (or subsequent restarts) don't try to
    recover the same run concurrently.
    """
    from .. import _fcntl_compat as fcntl
    os.makedirs(_RUN_LOCK_DIR, exist_ok=True)
    lock_path = os.path.join(_RUN_LOCK_DIR, f"{run_id[:12]}.lock")
    try:
        fd = open(lock_path, "w")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(f"{os.getpid()}\n")
        fd.flush()
        return fd
    except (OSError, BlockingIOError):
        return None


def _release_run_lock(fd: Any, run_id: str) -> None:
    """Release a per-run lock file."""
    from .. import _fcntl_compat as fcntl
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
        lock_path = os.path.join(_RUN_LOCK_DIR, f"{run_id[:12]}.lock")
        os.unlink(lock_path)
    except Exception:
        pass


async def _recover_one_run(
    *,
    run_id: str,
    workflow_name: str,
    metadata: dict[str, str],
    sidecar: Any,
    sidecar_agents: list[dict[str, Any]],
) -> bool:
    """Recover a single interrupted run. Returns True if resumption was launched."""
    from .memory import reconstruct_step_results
    from .loader import resolve_workflow
    from .schema import WorkflowState, WorkflowStatus, StepResult

    # If the on-disk checkpoint already records a terminal status, the run
    # is done — Kumiho metadata may still say "running" because the persist
    # on finalize is best-effort, but we trust the local checkpoint.
    local_status = _local_checkpoint_status(run_id)
    if local_status in _TERMINAL_STATUSES:
        _log(
            f"recovery: run={run_id[:8]} already {local_status} in checkpoint, skipping"
        )
        return False

    # Per-run lock: prevents duplicate recovery across operator processes
    run_lock_fd = _acquire_run_lock(run_id)
    if run_lock_fd is None:
        _log(f"recovery: run={run_id[:8]} already claimed by another process, skipping")
        return False

    # Also check if this run is already being driven in this process
    from .executor import ACTIVE_WORKFLOWS
    if run_id in ACTIVE_WORKFLOWS:
        _log(f"recovery: run={run_id[:8]} already active in this process, skipping")
        _release_run_lock(run_lock_fd, run_id)
        return False

    # Phase A: Reconstruct step results from Kumiho metadata
    step_results = reconstruct_step_results(metadata)
    completed_steps = {sid for sid, sr in step_results.items() if sr.status == "completed"}
    _log(f"recovery: run={run_id[:8]} has {len(completed_steps)} completed step(s) in Kumiho")

    # Phase B: Resolve the workflow definition.
    # Prefer the pinned revision from run metadata (so resumed runs stay bound
    # to the exact YAML they originally executed). Fall back to fresh resolve
    # only for legacy runs persisted before kref-pinning was introduced.
    stored_item_kref = metadata.get("workflow_item_kref", "") or ""
    stored_rev_kref = metadata.get("workflow_revision_kref", "") or ""
    resolved = await resolve_workflow(workflow_name)
    if resolved is None:
        _log(f"recovery: workflow '{workflow_name}' not found, marking run={run_id[:8]} as failed")
        _release_run_lock(run_lock_fd, run_id)
        return False
    wf, current_item_kref, current_rev_kref = resolved
    # Authoritative kref = what the original run recorded, if any.
    resume_item_kref = stored_item_kref or current_item_kref
    resume_rev_kref = stored_rev_kref or current_rev_kref

    # Phase C: Match sidecar agents to workflow steps
    prefix = f"wf-{run_id[:8]}-"
    matched_agents: dict[str, dict[str, Any]] = {}  # step_id -> sidecar agent info
    for sa in sidecar_agents:
        title = sa.get("title", "")
        if title.startswith(prefix):
            step_id = title[len(prefix):]
            if step_id:
                matched_agents[step_id] = sa
                _log(f"recovery: run={run_id[:8]} found live agent for step={step_id} "
                     f"status={sa.get('status', '?')}")

    # Phase D: Harvest outputs from matched agents
    for step_id, sa in matched_agents.items():
        # Skip steps already completed in Kumiho
        if step_id in completed_steps:
            continue

        agent_status = sa.get("status", "")
        sidecar_id = sa.get("id", "")

        if agent_status in ("idle", "completed", "closed"):
            # Agent finished — harvest its output
            sr = await _harvest_agent_output(step_id, sidecar_id, sa)
            step_results[step_id] = sr
            _log(f"recovery: harvested output from step={step_id} (was {agent_status})")

        elif agent_status in ("running", "active", "waiting"):
            # Agent still running — wait briefly, then harvest or mark failed
            sr = await _wait_and_harvest(step_id, sidecar_id, sa, sidecar)
            step_results[step_id] = sr
            _log(f"recovery: waited for step={step_id}, got status={sr.status}")

        elif agent_status in ("error",):
            # Agent errored — mark step as failed
            step_results[step_id] = StepResult(
                step_id=step_id,
                status="failed",
                error=f"Agent errored during interruption (sidecar status: {agent_status})",
                agent_id=sidecar_id,
            )

    # Phase E: Mark steps with dead agents as failed (agent not in sidecar)
    all_step_ids = {s.id for s in wf.steps}
    for step_id in all_step_ids:
        if step_id in step_results:
            continue
        # This step hasn't been started or its agent is dead — leave as pending
        # so the executor will pick it up on resume

    # Phase F: Reconstruct inputs
    inputs: dict[str, Any] = {}
    try:
        inputs = json.loads(metadata.get("inputs", "{}"))
    except (json.JSONDecodeError, TypeError):
        pass

    # Phase F2: Persist harvested results to Kumiho BEFORE launching executor.
    # This is critical — if the executor gets killed before its first persist,
    # the next recovery pass will still see the harvested results.
    newly_completed = {sid for sid, sr in step_results.items()
                       if sr.status == "completed" and sid not in completed_steps}
    if newly_completed:
        try:
            from .memory import persist_workflow_run
            step_dicts = {sid: sr.model_dump() for sid, sr in step_results.items()
                          if sr.status in ("completed", "failed", "skipped")}
            await persist_workflow_run(
                workflow_name=workflow_name,
                run_id=run_id,
                status="running",
                inputs=inputs,
                step_results=step_dicts,
                started_at=metadata.get("started_at", ""),
                steps_total=len(wf.steps),
                workflow_item_kref=resume_item_kref,
                workflow_revision_kref=resume_rev_kref,
            )
            _log(f"recovery: persisted {len(newly_completed)} harvested step(s) to Kumiho "
                 f"for run={run_id[:8]}")
        except Exception as exc:
            _log(f"recovery: pre-resume persist failed (non-fatal): {exc}")

    # Phase G: Build WorkflowState and launch resume
    state = WorkflowState(
        workflow_name=workflow_name,
        run_id=run_id,
        status=WorkflowStatus.RUNNING,
        inputs=inputs,
        step_results=step_results,
        started_at=metadata.get("started_at", ""),
        workflow_item_kref=resume_item_kref,
        workflow_revision_kref=resume_rev_kref,
    )

    # Launch execute_workflow as a background task.
    # The run_lock_fd is held for the entire execution so other processes
    # don't attempt duplicate recovery.
    from .executor import execute_workflow
    cwd = os.path.expanduser("~")

    async def _resume():
        try:
            _log(f"recovery: resuming run={run_id[:8]} with {len(step_results)} pre-populated step(s)")
            final_state = await execute_workflow(
                wf, inputs, cwd, run_id=run_id, resume_state=state,
                workflow_item_kref=resume_item_kref,
                workflow_revision_kref=resume_rev_kref,
            )
            _log(f"recovery: run={run_id[:8]} finished with status={final_state.status.value}")
        except Exception as exc:
            _log(f"recovery: resumed run={run_id[:8]} failed: {exc}")
        finally:
            _release_run_lock(run_lock_fd, run_id)

    asyncio.ensure_future(_resume())
    return True


# ---------------------------------------------------------------------------
# Agent harvesting helpers
# ---------------------------------------------------------------------------

async def _list_sidecar_agents(sidecar: Any) -> list[dict[str, Any]]:
    """Query sidecar for all live agents. Returns [] on failure."""
    if sidecar is None:
        return []
    try:
        agents = await sidecar.list_agents()
        return agents or []
    except Exception as exc:
        _log(f"recovery: sidecar.list_agents() failed: {exc}")
        return []


async def _harvest_agent_output(
    step_id: str,
    sidecar_id: str,
    sa: dict[str, Any],
) -> "StepResult":
    """Build a StepResult from a completed sidecar agent."""
    from .schema import StepResult
    from ..agent_state import AGENTS
    from ..patterns.refinement import _get_agent_output

    # Try to get output from RunLog (if agent was reconnected)
    agent = None
    for a in AGENTS.values():
        if getattr(a, "_sidecar_id", None) == sidecar_id:
            agent = a
            break

    output = ""
    files: list[str] = []
    if agent:
        output, files = _get_agent_output(agent.id)

    return StepResult(
        step_id=step_id,
        status="completed",
        output=output[:50000] if output else "",
        agent_id=sidecar_id,
        files_touched=files,
    )


async def _wait_and_harvest(
    step_id: str,
    sidecar_id: str,
    sa: dict[str, Any],
    sidecar: Any,
) -> "StepResult":
    """Wait briefly for a still-running agent, then harvest or give up.

    Includes early zombie exit: if the agent has zero events at the start
    of recovery, it's already dead — skip the full wait.
    """
    from .schema import StepResult

    # Snapshot event count at entry for zombie detection
    initial_event_count = 0
    try:
        events = await sidecar.get_events(sidecar_id, since=0)
        initial_event_count = len(events) if events else 0
    except Exception:
        pass

    # Early exit: zero events means agent never started or is long dead
    if initial_event_count == 0:
        _log(f"recovery: agent for step={step_id} has 0 events — zombie, skipping wait")
        return StepResult(
            step_id=step_id,
            status="failed",
            error="Agent has zero events at recovery — zombie",
            agent_id=sidecar_id,
        )

    deadline = time.monotonic() + _AGENT_HARVEST_TIMEOUT
    last_status = sa.get("status", "running")

    while time.monotonic() < deadline:
        await asyncio.sleep(_AGENT_POLL_INTERVAL)
        try:
            info = await sidecar.get_agent(sidecar_id)
            if info is None:
                break
            last_status = info.get("status", last_status)
            if last_status in ("idle", "completed", "closed"):
                return await _harvest_agent_output(step_id, sidecar_id, info)
            if last_status in ("error",):
                return StepResult(
                    step_id=step_id,
                    status="failed",
                    error="Agent errored while waiting for harvest",
                    agent_id=sidecar_id,
                )
        except Exception:
            break

    # Check if any progress was made during the wait
    final_event_count = 0
    try:
        events = await sidecar.get_events(sidecar_id, since=0)
        final_event_count = len(events) if events else 0
    except Exception:
        pass

    is_zombie = final_event_count == initial_event_count
    zombie_note = " (no new events — zombie)" if is_zombie else ""

    _log(f"recovery: agent for step={step_id} still {last_status} after "
         f"{_AGENT_HARVEST_TIMEOUT}s{zombie_note}, marking as failed")
    return StepResult(
        step_id=step_id,
        status="failed",
        error=f"Agent still {last_status} after recovery timeout ({_AGENT_HARVEST_TIMEOUT}s){zombie_note}",
        agent_id=sidecar_id,
    )


# ---------------------------------------------------------------------------
# Failure marking
# ---------------------------------------------------------------------------

async def _mark_run_failed(kref: str, metadata: dict[str, str], error: str) -> None:
    """Mark a run as failed in Kumiho so we don't retry it on next restart."""
    try:
        from ..operator_mcp import KUMIHO_SDK
        if not KUMIHO_SDK._available:
            return

        from datetime import datetime, timezone

        updated: dict[str, str] = {}
        for k, v in metadata.items():
            updated[k] = str(v) if not isinstance(v, str) else v
        updated["status"] = "failed"
        updated["error"] = f"Recovery failed: {error}"[:500]
        updated["completed_at"] = datetime.now(timezone.utc).isoformat()

        await KUMIHO_SDK.create_revision(kref, updated, tag="latest")
        _log(f"recovery: marked run {kref} as failed")
    except Exception as exc:
        _log(f"recovery: failed to mark run as failed: {exc}")
