"""Iterative Refinement Pattern — draft/critique loop with quality scoring.

Replaces the original review_loop.py with:
  - Structured quality scoring (0-100) instead of text-only verdicts
  - Fallback ladder: same creator → dedicated fixer → escalate
  - Trust-informed critic selection (auto-switch if codex trust < 0.7)
  - Backwards-compatible with review_fix_loop tool calls

Usage (via MCP tool):
    refinement_loop(task="...", cwd="/path", creator="coder-codex", critic="reviewer-claude")
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from collections.abc import Callable
from typing import Any

from .._log import _log
from ..agent_state import AGENTS, ManagedAgent
from ..construct_config import harness_project
from ..agent_subprocess import compose_agent_prompt, spawn_agent
from ..failure_classification import (
    agent_not_found,
    bad_directory,
    classified_error,
    policy_denied,
    RUNTIME_ENV_ERROR,
    VALIDATION_ERROR,
)
from ..run_log import get_log, get_or_create_log


# ---------------------------------------------------------------------------
# Quality scoring — parse structured JSON from critic output
# ---------------------------------------------------------------------------

_SCORE_JSON_RE = re.compile(
    r'\{\s*"score"\s*:\s*(\d+)',
    re.IGNORECASE,
)

_VERDICT_PATTERNS = [
    (re.compile(r"VERDICT:\s*APPROVED", re.IGNORECASE), "approved"),
    (re.compile(r"VERDICT:\s*NEEDS[_\s]?CHANGES", re.IGNORECASE), "needs_changes"),
    (re.compile(r"VERDICT:\s*BLOCKED", re.IGNORECASE), "blocked"),
    (re.compile(r"\bLGTM\b", re.IGNORECASE), "approved"),
    (re.compile(r"\bapproved?\b", re.IGNORECASE), "approved"),
    (re.compile(r"\bneeds?\s+changes?\b", re.IGNORECASE), "needs_changes"),
    (re.compile(r"\brequest(?:ed|ing)?\s+changes?\b", re.IGNORECASE), "needs_changes"),
]


def parse_quality(text: str) -> dict[str, Any]:
    """Extract structured quality assessment from critic output.

    Tries to parse JSON-format quality response first, falls back to
    verdict pattern matching from the original review_loop.

    Returns: {"score": int|None, "verdict": str, "feedback": list[str]}
    """
    if not text:
        return {"score": None, "verdict": "unclear", "feedback": []}

    score: int | None = None
    verdict = "unclear"
    feedback: list[str] = []

    # Try JSON extraction: {"score": 85, "verdict": "APPROVED", "feedback": [...]}
    try:
        # Find JSON block in output (may be wrapped in markdown code fences)
        json_match = re.search(r'```(?:json)?\s*(\{[^}]+\})\s*```', text, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group(1))
        else:
            # Try to find bare JSON object with score field
            score_match = re.search(r'\{[^{}]*"score"\s*:[^{}]*\}', text, re.DOTALL)
            if score_match:
                parsed = json.loads(score_match.group(0))
            else:
                parsed = None

        if parsed and isinstance(parsed, dict):
            if "score" in parsed:
                score = int(parsed["score"])
            if "verdict" in parsed:
                v = str(parsed["verdict"]).lower().replace(" ", "_")
                if v in ("approved", "needs_changes", "blocked"):
                    verdict = v
            if "feedback" in parsed:
                fb = parsed["feedback"]
                if isinstance(fb, list):
                    feedback = [str(f) for f in fb]
                elif isinstance(fb, str):
                    feedback = [fb]
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    # Fallback: score from simple pattern
    if score is None:
        m = _SCORE_JSON_RE.search(text)
        if m:
            score = int(m.group(1))

    # Fallback: verdict from patterns
    if verdict == "unclear":
        for pattern, v in _VERDICT_PATTERNS:
            if pattern.search(text):
                verdict = v
                break

    # Infer verdict from score if still unclear
    if verdict == "unclear" and score is not None:
        verdict = "approved" if score >= 70 else "needs_changes"

    # Extract numbered feedback items if not already parsed
    if not feedback and verdict == "needs_changes":
        feedback = re.findall(r'^\s*\d+[.)]\s+(.+)$', text, re.MULTILINE)

    return {"score": score, "verdict": verdict, "feedback": feedback}


# ---------------------------------------------------------------------------
# Agent spawn + wait helpers (shared with review_loop.py)
# ---------------------------------------------------------------------------

def _get_runlog_size(sidecar_id: str, runlog_dir: str) -> int:
    """Get the size in bytes of an agent's runlog file. Returns -1 if missing."""
    try:
        path = os.path.join(runlog_dir, f"{sidecar_id}.jsonl")
        return os.path.getsize(path)
    except OSError:
        return -1


def _runlog_is_growing(sidecar_id: str, last_size: int, runlog_dir: str) -> bool:
    """Check if the agent's runlog has grown since last_size.

    This is the cross-verification guardrail: the runlog is written by
    the SSE event consumer (separate connection), so if it's growing
    the agent is alive even if the REST get_events API returns stale data.
    """
    current = _get_runlog_size(sidecar_id, runlog_dir)
    if current < 0:
        return False  # no runlog at all
    if last_size < 0:
        return current > 0  # first check — any content means alive
    return current > last_size


def _subprocess_progress_marker(agent: ManagedAgent, runlog_dir: str) -> int:
    """Return a coarse monotonic marker for subprocess fallback progress."""
    runlog_id = getattr(agent, "_sidecar_id", None) or agent.id
    runlog_size = max(_get_runlog_size(runlog_id, runlog_dir), 0)
    return runlog_size + len(agent.stdout_buffer or "") + len(agent.stderr_buffer or "")


def _initializing_timeout_secs(timeout: float) -> float:
    try:
        configured = float(os.getenv("CONSTRUCT_AGENT_INITIALIZING_TIMEOUT_SECS", "180"))
    except (TypeError, ValueError):
        configured = 180.0
    return min(timeout, max(0.1, configured))


def _agent_run_log(agent: ManagedAgent):
    sidecar_id = getattr(agent, "_sidecar_id", None)
    run_log = get_log(agent.id)
    if run_log is None and sidecar_id:
        run_log = get_log(sidecar_id)
    return run_log


def _agent_run_summary(agent: ManagedAgent) -> dict[str, Any]:
    run_log = _agent_run_log(agent)
    if not run_log:
        return {}
    try:
        return run_log.get_summary()
    except Exception:
        return {}


def _prompt_only_runlog(summary: dict[str, Any], agent: ManagedAgent) -> bool:
    if (agent.stdout_buffer or "").strip() or (agent.stderr_buffer or "").strip():
        return False
    if str(summary.get("last_message") or "").strip():
        return False
    if int(summary.get("tool_call_count") or 0) > 0:
        return False
    if int(summary.get("error_count") or 0) > 0:
        return False
    total_events = int(summary.get("total_events") or 0)
    return total_events == 0 or total_events <= 2


def _failure_detail_from_summary(summary: dict[str, Any], agent: ManagedAgent) -> str:
    stderr_tail = str(summary.get("stderr_tail") or "").strip()
    if stderr_tail:
        return stderr_tail[-1000:]
    failing = summary.get("last_failing_command")
    if isinstance(failing, dict):
        for key in ("stderr_tail", "stderr", "error", "message"):
            value = str(failing.get(key) or "").strip()
            if value:
                return value[-1000:]
    if (agent.stderr_buffer or "").strip():
        return agent.stderr_buffer.strip()[-1000:]
    return ""


def _record_lifecycle_failure(
    agent: ManagedAgent,
    message: str,
    *,
    code: str,
    detail: dict[str, Any] | None = None,
) -> None:
    sidecar_id = getattr(agent, "_sidecar_id", None)
    runlog_id = sidecar_id or agent.id
    run_log = _agent_run_log(agent)
    if run_log is None:
        run_log = get_or_create_log(
            runlog_id,
            title=agent.title,
            agent_type=agent.agent_type,
            cwd=agent.cwd,
        )
    stderr_tail = (agent.stderr_buffer or "").strip()
    try:
        run_log.record_lifecycle_error(
            message,
            code=code,
            stderr_tail=stderr_tail[-2000:] if stderr_tail else "",
            detail=detail,
        )
    except Exception as exc:
        _log(f"refinement: failed to record lifecycle error for {agent.id[:8]}: {exc}")


def _dead_health(agent: ManagedAgent) -> dict[str, Any] | None:
    try:
        from ..heartbeat import get_heartbeat_monitor
        monitor = get_heartbeat_monitor()
        for aid in (agent.id, getattr(agent, "_sidecar_id", None)):
            if not aid:
                continue
            health = monitor.get_health(aid)
            if (
                health
                and health.get("health") == "dead"
                and health.get("status") == "running"
            ):
                return health
    except Exception:
        return None
    return None


async def _cancel_timed_out_agent(agent: ManagedAgent) -> None:
    """Cancel a timed-out agent to stop it from burning tokens."""
    try:
        from ..tool_handlers.agents import _cancel_one
        await _cancel_one(agent)
    except Exception as exc:
        _log(f"refinement: failed to cancel agent {agent.id[:8]}: {exc}")


def _cancel_check_requested(cancel_check: Callable[[], bool] | None) -> bool:
    if cancel_check is None:
        return False
    try:
        return bool(cancel_check())
    except Exception as exc:
        _log(f"refinement: cancel_check failed: {exc}")
        return False


async def _wait_for_agent(
    agent: ManagedAgent,
    *,
    timeout: float = 300.0,
    cancel_check: Callable[[], bool] | None = None,
) -> str:
    """Wait for an agent to complete and return its last message.

    Includes zombie detection: if sidecar reports 'running' but no new
    events appear for a sustained period, the agent is declared dead.
    The zombie window scales with the step timeout (40%, min 180s) to
    avoid false positives when many agents start simultaneously.

    Before killing a suspected zombie, cross-verifies against the local
    runlog file — if the runlog is still growing, the agent is alive and
    the sidecar event API is unreliable (e.g. dual-process race).
    """
    _ZOMBIE_MIN = 180.0               # floor: 3 minutes
    _ZOMBIE_RATIO = 0.4               # 40% of step timeout
    _LIVENESS_CHECK_INTERVAL = 30.0   # how often to fetch event counts
    _INITIALIZING_TIMEOUT = _initializing_timeout_secs(timeout)
    _RUNLOG_DIR = os.path.expanduser("~/.construct/operator_mcp/runlogs")

    zombie_window = max(_ZOMBIE_MIN, timeout * _ZOMBIE_RATIO)

    sidecar_id = getattr(agent, "_sidecar_id", None)

    if sidecar_id:
        from ..tool_handlers.agents import _sidecar_client
        if _sidecar_client:
            loop_time = asyncio.get_event_loop().time
            deadline = loop_time() + timeout
            poll_start = loop_time()
            poll_interval = 1.0

            # Zombie-detection state
            last_event_count = -1          # -1 = not yet checked
            last_progress_time = loop_time()
            next_liveness_check = loop_time() + _LIVENESS_CHECK_INTERVAL
            consecutive_empty = 0          # track repeated 0-event responses
            last_runlog_size = -1          # cross-verification via local file
            last_seen_status = ""

            while loop_time() < deadline:
                if _cancel_check_requested(cancel_check):
                    _log(f"refinement: cancellation requested for agent {agent.id[:8]}; cancelling")
                    await _cancel_timed_out_agent(agent)
                    agent.status = "cancelled"
                    return "[CANCELLED]"
                if agent.status in ("completed", "error", "closed"):
                    break
                try:
                    info = await _sidecar_client.get_agent(sidecar_id)
                    if info is None:
                        _log(f"refinement: agent {agent.id[:8]} vanished from sidecar")
                        agent.status = "error"
                        return "[AGENT VANISHED]"

                    status = info.get("status", "")
                    last_seen_status = status
                    now = loop_time()
                    health = _dead_health(agent)
                    if health:
                        message = "Agent health is dead while sidecar status is running"
                        _log(f"refinement: agent {agent.id[:8]} {message}; marking failed")
                        _record_lifecycle_failure(
                            agent,
                            message,
                            code="agent_dead_while_running",
                            detail={"health": health},
                        )
                        await _cancel_timed_out_agent(agent)
                        agent.status = "error"
                        return f"[DEAD AGENT — {message}]"

                    if status in ("idle", "error", "closed"):
                        agent.status = "completed" if status == "idle" else status
                        if status == "error":
                            try:
                                from ..tool_handlers.agents import _sync_sidecar_events
                                await _sync_sidecar_events(agent.id, sidecar_id)
                            except Exception as exc:
                                _log(
                                    f"refinement: sidecar event sync failed "
                                    f"for {agent.id[:8]}: {exc}"
                                )
                            summary = _agent_run_summary(agent)
                            if _prompt_only_runlog(summary, agent):
                                detail = _failure_detail_from_summary(summary, agent)
                                message = "Agent died during initialization"
                                if detail:
                                    message = f"{message}: {detail}"
                                _record_lifecycle_failure(
                                    agent,
                                    message,
                                    code="agent_bootstrap_failed",
                                    detail={"sidecar_status": status, "summary": summary},
                                )
                                return f"[AGENT DIED DURING INITIALIZATION] {detail}".rstrip()
                        break

                    if now - poll_start >= _INITIALIZING_TIMEOUT:
                        summary = _agent_run_summary(agent)
                        if _prompt_only_runlog(summary, agent) and last_event_count <= 2:
                            message = (
                                f"Agent initialization timeout: no useful activity after "
                                f"{_INITIALIZING_TIMEOUT:.0f}s"
                            )
                            _log(f"refinement: agent {agent.id[:8]} {message}; marking failed")
                            _record_lifecycle_failure(
                                agent,
                                message,
                                code="agent_initialization_timeout",
                                detail={"summary": summary},
                            )
                            await _cancel_timed_out_agent(agent)
                            agent.status = "error"
                            return f"[INITIALIZATION TIMEOUT after {_INITIALIZING_TIMEOUT:.0f}s]"

                    if status == "initializing" and now - poll_start >= _INITIALIZING_TIMEOUT:
                        _log(
                            f"refinement: agent {agent.id[:8]} stayed initializing for "
                            f"{now - poll_start:.0f}s; marking failed"
                        )
                        _record_lifecycle_failure(
                            agent,
                            f"Agent stayed initializing for {_INITIALIZING_TIMEOUT:.0f}s",
                            code="agent_initializing_timeout",
                            detail={"sidecar_status": status},
                        )
                        await _cancel_timed_out_agent(agent)
                        agent.status = "error"
                        return f"[INITIALIZATION TIMEOUT after {_INITIALIZING_TIMEOUT:.0f}s]"

                    # Periodic liveness probe for running agents
                    if now >= next_liveness_check:
                        next_liveness_check = now + _LIVENESS_CHECK_INTERVAL
                        try:
                            events = await _sidecar_client.get_events(sidecar_id, since=0)
                            event_count = len(events) if events else 0

                            if event_count == 0:
                                consecutive_empty += 1
                                if consecutive_empty >= 8:  # ~4 min of 0 events
                                    # Cross-verify: is the runlog growing?
                                    if _runlog_is_growing(sidecar_id, last_runlog_size, _RUNLOG_DIR):
                                        _log(f"refinement: agent {agent.id[:8]} sidecar shows 0 events "
                                             f"but runlog is growing — NOT a zombie, resetting")
                                        consecutive_empty = 0
                                        last_progress_time = now
                                    else:
                                        _log(f"refinement: agent {agent.id[:8]} never produced "
                                             f"events after {consecutive_empty} checks — zombie")
                                        _record_lifecycle_failure(
                                            agent,
                                            "Agent died during initialization: never produced events",
                                            code="agent_bootstrap_no_events",
                                            detail={"consecutive_empty": consecutive_empty},
                                        )
                                        await _cancel_timed_out_agent(agent)
                                        agent.status = "error"
                                        return "[ZOMBIE — never produced events]"
                            elif last_event_count < 0 or event_count > last_event_count:
                                last_event_count = event_count
                                last_progress_time = now
                                consecutive_empty = 0
                            elif now - last_progress_time >= zombie_window:
                                # Cross-verify via runlog before killing
                                if _runlog_is_growing(sidecar_id, last_runlog_size, _RUNLOG_DIR):
                                    _log(f"refinement: agent {agent.id[:8]} sidecar events frozen at "
                                         f"{event_count} but runlog is growing — NOT a zombie, resetting")
                                    last_progress_time = now
                                else:
                                    stale = now - last_progress_time
                                    _log(f"refinement: agent {agent.id[:8]} no progress for "
                                         f"{stale:.0f}s (events frozen at {event_count}, "
                                         f"runlog static) — zombie confirmed")
                                    _record_lifecycle_failure(
                                        agent,
                                        f"Agent made no progress for {stale:.0f}s",
                                        code="agent_progress_stalled",
                                        detail={"event_count": event_count, "stale_seconds": stale},
                                    )
                                    await _cancel_timed_out_agent(agent)
                                    agent.status = "error"
                                    return f"[ZOMBIE — no progress for {stale:.0f}s]"

                            # Track runlog size for next comparison
                            last_runlog_size = _get_runlog_size(sidecar_id, _RUNLOG_DIR)
                        except Exception as exc:
                            _log(f"refinement: agent {agent.id[:8]} liveness check error: {exc}")
                except Exception:
                    pass
                remaining = deadline - loop_time()
                sleep_for = min(poll_interval, max(0.1, remaining))
                if _INITIALIZING_TIMEOUT < timeout:
                    init_remaining = (poll_start + _INITIALIZING_TIMEOUT) - loop_time()
                    sleep_for = min(sleep_for, max(0.1, init_remaining))
                await asyncio.sleep(sleep_for)
                poll_interval = min(poll_interval * 1.2, 5.0)
            else:
                summary = _agent_run_summary(agent)
                if last_seen_status == "initializing" or (
                    _prompt_only_runlog(summary, agent) and last_event_count <= 2
                ):
                    message = (
                        f"Agent initialization timeout: no useful activity after "
                        f"{_INITIALIZING_TIMEOUT:.0f}s"
                    )
                    _log(f"refinement: agent {agent.id[:8]} {message}; marking failed")
                    _record_lifecycle_failure(
                        agent,
                        message,
                        code="agent_initialization_timeout",
                        detail={"sidecar_status": last_seen_status, "summary": summary},
                    )
                    await _cancel_timed_out_agent(agent)
                    agent.status = "error"
                    return f"[INITIALIZATION TIMEOUT after {_INITIALIZING_TIMEOUT:.0f}s]"
                _log(f"refinement: agent {agent.id[:8]} timed out ({timeout}s), cancelling")
                await _cancel_timed_out_agent(agent)
                return f"[TIMEOUT after {timeout}s]"
    elif agent._reader_task:
        loop_time = asyncio.get_event_loop().time
        deadline = loop_time() + timeout
        last_progress_time = loop_time()
        last_marker = _subprocess_progress_marker(agent, _RUNLOG_DIR)

        while loop_time() < deadline:
            if _cancel_check_requested(cancel_check):
                _log(f"refinement: cancellation requested for subprocess agent {agent.id[:8]}; cancelling")
                await _cancel_timed_out_agent(agent)
                agent.status = "cancelled"
                return "[CANCELLED]"
            if agent._reader_task.done():
                break

            now = loop_time()
            marker = _subprocess_progress_marker(agent, _RUNLOG_DIR)
            if marker > last_marker:
                last_marker = marker
                last_progress_time = now

            has_output = bool((agent.stdout_buffer or "").strip() or (agent.stderr_buffer or "").strip())
            idle_for = now - last_progress_time
            if not has_output and idle_for >= _INITIALIZING_TIMEOUT:
                _log(
                    f"refinement: subprocess agent {agent.id[:8]} produced no output for "
                    f"{idle_for:.0f}s; marking failed"
                )
                _record_lifecycle_failure(
                    agent,
                    f"Agent initialization timeout: no subprocess output after {_INITIALIZING_TIMEOUT:.0f}s",
                    code="agent_initialization_timeout",
                    detail={"idle_seconds": idle_for},
                )
                await _cancel_timed_out_agent(agent)
                agent.status = "error"
                return f"[INITIALIZATION TIMEOUT after {_INITIALIZING_TIMEOUT:.0f}s]"

            remaining = deadline - now
            sleep_for = min(1.0, max(0.1, remaining))
            if _INITIALIZING_TIMEOUT < timeout:
                init_remaining = (last_progress_time + _INITIALIZING_TIMEOUT) - now
                sleep_for = min(sleep_for, max(0.1, init_remaining))
            await asyncio.sleep(sleep_for)
        else:
            _log(f"refinement: agent {agent.id[:8]} timed out ({timeout}s), cancelling")
            await _cancel_timed_out_agent(agent)
            return f"[TIMEOUT after {timeout}s]"

    if agent.status == "error":
        summary = _agent_run_summary(agent)
        detail = _failure_detail_from_summary(summary, agent)
        if not detail and (agent.stdout_buffer or "").strip():
            detail = agent.stdout_buffer.strip()[-1000:]
        if detail or _prompt_only_runlog(summary, agent) or not str(summary.get("last_message") or "").strip():
            message = "Agent died during initialization"
            if detail:
                message = f"{message}: {detail}"
            _record_lifecycle_failure(
                agent,
                message,
                code="agent_bootstrap_failed",
                detail={"summary": summary},
            )
            return f"[AGENT DIED DURING INITIALIZATION] {detail}".rstrip()

    if not sidecar_id and agent.stdout_buffer:
        return agent.stdout_buffer

    run_log = _agent_run_log(agent)
    if run_log:
        summary = run_log.get_summary()
        return summary.get("last_message", "")
    return agent.stdout_buffer if agent.stdout_buffer else ""


async def _spawn_and_wait(
    agent_type: str,
    title: str,
    cwd: str,
    prompt: str,
    *,
    model: str | None = None,
    timeout: float = 300.0,
    max_turns: int = 200,
    include_memory: bool = True,
    include_operator: bool = True,
    include_google_agentops: bool = False,
    env_extra: dict[str, str] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[ManagedAgent, str]:
    """Spawn an agent, wait for completion, return (agent, output_text).

    ``env_extra`` is forwarded to the agent subprocess (CLI mode) and to
    the sidecar create_agent config (sidecar mode). Used by workflow
    auth-profile bindings to expose CONSTRUCT_AUTH_PROFILE_ID without
    injecting it into the system prompt.
    """
    from ..budget_authority import BudgetGateError, require_agent_budget
    from ..mcp_injection import build_mcp_servers, build_system_prompt
    from ..tool_handlers import agents as agent_tools

    agent_id = str(uuid.uuid4())
    agent = ManagedAgent(
        id=agent_id,
        agent_type=agent_type,
        title=title,
        cwd=cwd,
        status="idle",
    )
    AGENTS[agent_id] = agent

    # Single-turn workers (no MCP tools) go straight to CLI subprocess.
    # The sidecar's Agent SDK has separate rate limits from the CLI —
    # using `claude --print --bare` shares the user's CLI quota instead.
    use_cli = not include_memory and not include_operator and not include_google_agentops
    subprocess_mcp_servers: dict[str, Any] | None = None
    subprocess_prompt = prompt

    if not use_cli:
        socket_path = getattr(agent_tools._sidecar_client, "socket_path", None)
        subprocess_mcp_servers = build_mcp_servers(
            include_memory=include_memory,
            include_operator=include_operator,
            include_google_agentops=include_google_agentops,
            socket_path=socket_path,
        )
        system_prompt = build_system_prompt(
            is_top_level=False,
            include_memory=include_memory,
            include_operator=include_operator,
            include_google_agentops=include_google_agentops,
        )
        if system_prompt:
            subprocess_prompt = f"{system_prompt}\n\n{prompt}"

    if use_cli:
        try:
            from ..operator_mcp import CONSTRUCT_GW
            await require_agent_budget(CONSTRUCT_GW)
        except BudgetGateError as exc:
            agent.status = "error"
            return agent, str(exc.response.get("error", exc))

    sidecar_info = None
    if not use_cli:
        try:
            sidecar_info = await agent_tools._try_sidecar_create(
                agent_id, agent_type, title, cwd, prompt, model=model,
                max_turns=max_turns,
                include_memory=include_memory,
                include_operator=include_operator,
                include_google_agentops=include_google_agentops,
                env_extra=env_extra,
            )
        except BudgetGateError as exc:
            agent.status = "error"
            return agent, str(exc.response.get("error", exc))
    if sidecar_info:
        agent.status = "running"
        agent._sidecar_id = sidecar_info.get("id", "")
        if agent_tools._event_consumer and agent._sidecar_id:
            agent_tools._event_consumer._agent_titles[agent._sidecar_id] = title
            if model:
                agent_tools._event_consumer.set_agent_model(agent._sidecar_id, model)
            await agent_tools._event_consumer.subscribe(agent._sidecar_id, title, model=model or "")
    else:
        from ..operator_mcp import JOURNAL
        try:
            agent._subprocess_mcp_servers = subprocess_mcp_servers
            agent._original_prompt = subprocess_prompt
            await spawn_agent(
                agent,
                subprocess_prompt,
                JOURNAL,
                model=model,
                env_extra=env_extra,
                mcp_servers=subprocess_mcp_servers,
            )
        except Exception:
            agent.status = "error"
            return agent, agent.stderr_buffer[-2000:] if agent.stderr_buffer else "spawn failed"

    output = await _wait_for_agent(agent, timeout=timeout, cancel_check=cancel_check)
    return agent, output


def _get_agent_output(agent_id: str) -> tuple[str, list[str]]:
    """Get an agent's last message and files touched from RunLog."""
    agent = AGENTS.get(agent_id)
    if not agent:
        return "", []
    run_log = get_log(agent_id)
    sidecar_id = getattr(agent, "_sidecar_id", None)
    if not sidecar_id and agent.stdout_buffer:
        return agent.stdout_buffer, []
    if run_log is None and sidecar_id:
        run_log = get_log(sidecar_id)
    if run_log:
        entries = run_log.get_full_log(limit=1000)
        for entry in reversed(entries):
            if entry.get("kind") == "subprocess" and entry.get("stdout"):
                return (
                    str(entry.get("stdout", "")),
                    run_log.get_summary().get("files_touched", []),
                )
        summary = run_log.get_summary()
        return (summary.get("last_message", ""), summary.get("files_touched", []))
    return agent.stdout_buffer if agent.stdout_buffer else "", []


# ---------------------------------------------------------------------------
# Trust-informed critic selection
# ---------------------------------------------------------------------------

async def _get_trust_score(template_name: str) -> float:
    """Get trust score for a template. Returns 1.0 if unavailable."""
    try:
        from ..operator_mcp import KUMIHO_POOL
        if not KUMIHO_POOL._available:
            return 1.0
        items = await KUMIHO_POOL.list_items(f"/{harness_project()}/AgentTrust")
        for item in items:
            if item.get("item_name") == template_name:
                rev = await KUMIHO_POOL.get_latest_revision(item.get("kref"))
                if rev:
                    return float(rev.get("metadata", {}).get("trust_score", 1.0))
        return 1.0
    except Exception:
        return 1.0


async def _select_critic(
    requested: str,
    fallback: str = "claude",
    trust_threshold: float = 0.7,
) -> str:
    """Select critic agent type, auto-switching if trust is too low."""
    trust = await _get_trust_score(f"reviewer-{requested}")
    if trust < trust_threshold:
        _log(f"refinement: critic '{requested}' trust={trust:.2f} < {trust_threshold}, switching to '{fallback}'")
        return fallback
    return requested


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_CRITIC_PROMPT = """\
You are a code critic evaluating work quality. Review the implementation below.

## Original task
{task}

## Implementation output
{creator_output}

## Files touched
{files_touched}

{review_focus}

## Instructions
- Evaluate correctness, edge cases, security, style, and completeness.
- Provide a quality score (0-100) and structured feedback.
- Respond with a JSON block:

```json
{{"score": <0-100>, "verdict": "APPROVED|NEEDS_CHANGES|BLOCKED", "feedback": ["item 1", "item 2"]}}
```

- Score >= 70 = APPROVED, < 70 = NEEDS_CHANGES.
- If NEEDS_CHANGES, each feedback item should be specific and actionable.
- Also include a VERDICT: line after the JSON for backwards compatibility.
"""

_FIXER_PROMPT = """\
You are a fixer agent. A critic found issues in the implementation.

## Original task
{task}

## Critic feedback (round {round_num}, score: {score})
{feedback_items}

## Files to fix
{files_touched}

## Instructions
- Address every feedback item precisely.
- Do NOT add unrelated changes.
- After fixing, briefly summarize what you changed.
"""

_DEDICATED_FIXER_PROMPT = """\
You are a dedicated fixer agent. The original creator failed to address feedback.
Take a fresh look and fix the issues independently.

## Original task
{task}

## Unresolved feedback
{feedback_items}

## Files to fix
{files_touched}

## Instructions
- You have full authority to rewrite sections if needed.
- Address every feedback item.
- Summarize your changes.
"""


# ---------------------------------------------------------------------------
# Core refinement loop
# ---------------------------------------------------------------------------

async def tool_refinement_loop(args: dict[str, Any]) -> dict[str, Any]:
    """Run an iterative refinement loop: create → critique → refine → repeat.

    Supports both new creator-from-scratch and review of existing agent work.

    Args:
        cwd: Working directory (required).
        task: Task description.
        creator_agent_id: Existing agent whose work to refine (mutually exclusive with creator).
        creator: Agent type for creator (default "codex"). Used when creating fresh.
        critic: Agent type for critic (default "claude").
        model: Optional model override.
        max_rounds: Max critique→refine iterations (default 2, max 5).
        threshold: Quality score threshold for approval (default 70, range 0-100).
        review_focus: Extra guidance for the critic.
        timeout: Per-agent timeout in seconds (default 300).
    """
    cwd = args.get("cwd", "")
    task = args.get("task", "")
    creator_agent_id = args.get("creator_agent_id") or args.get("coder_agent_id")  # backwards compat
    creator_type = args.get("creator", args.get("fixer_type", "codex"))
    critic_type = args.get("critic", args.get("reviewer_type", "claude"))
    model = args.get("model")
    max_rounds = min(args.get("max_rounds", 2), 5)
    threshold = max(0, min(100, args.get("threshold", 70)))
    review_focus = args.get("review_focus", "")
    timeout = args.get("timeout", 300.0)

    if not cwd:
        return classified_error(
            "cwd is required for refinement_loop",
            code="missing_cwd", category=VALIDATION_ERROR,
        )

    cwd = os.path.realpath(os.path.expanduser(cwd))
    if not os.path.isdir(cwd):
        return bad_directory(cwd)

    from ..policy import load_policy
    policy = load_policy()
    policy_failures = policy.preflight_spawn(cwd, critic_type)
    if policy_failures:
        fail = policy_failures[0]
        return policy_denied("cwd", cwd, fail.reason,
                             policy_rule=fail.policy_rule, suggestion=fail.suggestion)

    # Get initial output from existing agent or error
    if creator_agent_id:
        agent = AGENTS.get(creator_agent_id)
        if not agent:
            return agent_not_found(creator_agent_id)
        current_output, current_files = _get_agent_output(creator_agent_id)
        creator_title = agent.title
    else:
        return classified_error(
            "creator_agent_id (or coder_agent_id) is required — pass the agent whose work to review",
            code="missing_creator", category=VALIDATION_ERROR,
        )

    if not current_output:
        current_output = "(no output captured from creator agent)"

    # Trust-informed critic selection
    effective_critic = await _select_critic(critic_type)

    rounds: list[dict[str, Any]] = []
    last_fixer_id: str | None = None

    for round_num in range(1, max_rounds + 1):
        _log(f"refinement: round {round_num}/{max_rounds} for {creator_title}")

        # -- Spawn critic --
        focus_section = f"## Review focus\n{review_focus}" if review_focus else ""
        critic_prompt = _CRITIC_PROMPT.format(
            task=task or "(not specified)",
            creator_output=current_output[:6000],
            files_touched=", ".join(current_files) if current_files else "(unknown)",
            review_focus=focus_section,
        )

        critic_agent, critic_output = await _spawn_and_wait(
            effective_critic,
            f"critic-round{round_num}",
            cwd,
            compose_agent_prompt("critic", "reviewer", "", [], critic_prompt),
            model=model,
            timeout=timeout,
        )

        quality = parse_quality(critic_output)
        _log(f"refinement: round {round_num} score={quality['score']} verdict={quality['verdict']}")

        round_info: dict[str, Any] = {
            "round": round_num,
            "critic_agent_id": critic_agent.id,
            "critic_status": critic_agent.status,
            "score": quality["score"],
            "verdict": quality["verdict"],
            "feedback": quality["feedback"],
            "critic_output": critic_output[:4000],
        }

        # Approved or meets threshold
        if quality["verdict"] == "approved" or (
            quality["score"] is not None and quality["score"] >= threshold
        ):
            round_info["action"] = "accepted"
            rounds.append(round_info)
            break

        if quality["verdict"] == "blocked":
            round_info["action"] = "halted"
            rounds.append(round_info)
            break

        if round_num >= max_rounds:
            round_info["action"] = "max_rounds_reached"
            rounds.append(round_info)
            break

        # -- Fallback ladder: try same creator first, then dedicated fixer --
        feedback_text = "\n".join(
            f"{i+1}. {f}" for i, f in enumerate(quality["feedback"])
        ) if quality["feedback"] else critic_output[:4000]

        fixer_prompt = _FIXER_PROMPT.format(
            task=task or "(not specified)",
            round_num=round_num,
            score=quality["score"] or "N/A",
            feedback_items=feedback_text,
            files_touched=", ".join(current_files) if current_files else "(unknown)",
        )

        fixer_agent, fixer_output = await _spawn_and_wait(
            creator_type,
            f"fixer-round{round_num}",
            cwd,
            compose_agent_prompt("fixer", "coder", "", [], fixer_prompt),
            model=model,
            timeout=timeout,
        )

        # Check if fixer actually did work
        fixer_output_text, fixer_files = _get_agent_output(fixer_agent.id)
        fixer_worked = bool(fixer_output_text and fixer_agent.status != "error")

        if not fixer_worked and round_num < max_rounds:
            # Fallback: spawn dedicated fixer
            _log(f"refinement: round {round_num} creator-fixer failed, trying dedicated fixer")
            dedicated_prompt = _DEDICATED_FIXER_PROMPT.format(
                task=task or "(not specified)",
                feedback_items=feedback_text,
                files_touched=", ".join(current_files) if current_files else "(unknown)",
            )
            ded_agent, ded_output = await _spawn_and_wait(
                creator_type,
                f"dedicated-fixer-round{round_num}",
                cwd,
                compose_agent_prompt("dedicated-fixer", "coder", "", [], dedicated_prompt),
                model=model,
                timeout=timeout,
            )
            ded_text, ded_files = _get_agent_output(ded_agent.id)
            if ded_text:
                fixer_output_text = ded_text
                fixer_files = ded_files
                fixer_agent = ded_agent
            round_info["dedicated_fixer_agent_id"] = ded_agent.id

        round_info["fixer_agent_id"] = fixer_agent.id
        round_info["fixer_status"] = fixer_agent.status
        round_info["action"] = "fix_applied"
        rounds.append(round_info)

        current_output = fixer_output_text or fixer_output
        current_files = fixer_files or current_files
        last_fixer_id = fixer_agent.id
        creator_title = fixer_agent.title

    # Build result
    final_round = rounds[-1] if rounds else {}
    final_verdict = final_round.get("verdict", "no_rounds")
    final_action = final_round.get("action", "unknown")
    final_score = final_round.get("score")

    result: dict[str, Any] = {
        "creator_agent_id": creator_agent_id,
        "total_rounds": len(rounds),
        "final_verdict": final_verdict,
        "final_action": final_action,
        "final_score": final_score,
        "threshold": threshold,
        "critic_type_used": effective_critic,
        "rounds": rounds,
    }
    if last_fixer_id:
        result["last_fixer_agent_id"] = last_fixer_id

    _log(f"refinement: complete — {len(rounds)} rounds, verdict={final_verdict}, score={final_score}")
    return result
