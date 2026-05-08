"""Agent lifecycle tool handlers: create, wait, send, activity, list.

Supports two backends:
  1. Sidecar (TS Session Manager) — preferred when available, provides
     structured timeline events and SSE streaming.
  2. Subprocess fallback — legacy mode via agent_subprocess.spawn_agent.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

from .._log import _log
from ..agent_state import AGENTS, MAX_CONCURRENT_AGENTS, AgentTemplate, CacheSafeParams, ManagedAgent, POOL
from ..failure_classification import (
    agent_not_found, agent_limit_exceeded, agent_busy,
    template_not_found, bad_directory, missing_cwd, invalid_param,
    classified_error, RUNTIME_ENV_ERROR,
)
from ..run_log import get_or_create_log, get_log, load_log_from_disk, list_run_logs
from ..agent_subprocess import spawn_agent
from ..journal import SessionJournal
from ..kumiho_clients import KumihoAgentPoolClient
from ..mcp_injection import build_mcp_servers, build_system_prompt
from ..workflow_context import WorkflowContext

# These are set by operator_mcp at startup when the sidecar is available
_sidecar_client = None  # SessionManagerClient | None
_event_consumer = None  # EventConsumer | None
_workflow_ctx: WorkflowContext | None = None  # Set by operator_mcp at startup


def set_sidecar(sidecar: Any, consumer: Any) -> None:
    """Called by operator_mcp to inject sidecar + event consumer."""
    global _sidecar_client, _event_consumer
    _sidecar_client = sidecar
    _event_consumer = consumer


def set_workflow_context(ctx: WorkflowContext) -> None:
    """Called by operator_mcp to inject workflow context."""
    global _workflow_ctx
    _workflow_ctx = ctx


async def _try_sidecar_create(
    agent_id: str,
    agent_type: str,
    title: str,
    cwd: str,
    prompt: str,
    *,
    parent_id: str | None = None,
    role_identity: str = "",
    template_hint: str = "",
    model: str | None = None,
    cached_params: CacheSafeParams | None = None,
    allowed_tools: list[str] | None = None,
    max_turns: int = 200,
    include_memory: bool = True,
    include_operator: bool = True,
    clean_build: bool = False,
    node_env: str = "development",
    env_extra: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Try to create an agent via the sidecar. Returns None if unavailable.

    Injects MCP servers (kumiho-memory, operator-tools) and builds a
    layered system prompt based on whether this is a top-level or sub-agent.

    If ``cached_params`` is provided, the frozen system prompt and MCP
    servers are reused directly instead of being rebuilt — this makes
    child agent creation cache-friendly and avoids per-spawn rebuilds.

    ``include_memory`` / ``include_operator``: control MCP injection.
    Set both to False for single-turn workflow agents that just need to
    read a prompt and write output — avoids tool-loop token waste.
    """
    if _sidecar_client is None:
        return None

    if not await _sidecar_client.ensure_running():
        return None

    is_top_level = parent_id is None

    if cached_params:
        # A1: Reuse frozen parent prompt + MCP servers
        system_prompt = cached_params.system_prompt
        mcp_servers = cached_params.mcp_servers or {}
    else:
        # Build MCP servers for injection — skip if not needed
        mcp_servers = build_mcp_servers(
            include_memory=include_memory,
            include_operator=include_operator,
            socket_path=_sidecar_client.socket_path if _sidecar_client else None,
        )

        # Build layered system prompt
        system_prompt = build_system_prompt(
            is_top_level=is_top_level,
            role_identity=role_identity,
            template_hint=template_hint,
            include_memory=include_memory,
            include_operator=include_operator,
        )

    config: dict[str, Any] = {
        "cwd": cwd,  # Already expanded+realpath'd by caller
        "agentType": agent_type,
        "prompt": prompt,
        "title": title,
        "parentId": parent_id or agent_id,
    }

    if mcp_servers:
        config["mcpServers"] = mcp_servers
    if system_prompt:
        config["systemPrompt"] = system_prompt
    if model:
        config["model"] = model

    # A2: Pass tool allowlist and max turns to sidecar
    if allowed_tools is not None:
        config["allowedTools"] = allowed_tools
    if max_turns != 200:
        config["maxTurns"] = max_turns

    # Inject clean env overrides for sidecar-spawned processes
    if clean_build or node_env != "development":
        from ..clean_env import build_sidecar_env_config
        config["env"] = build_sidecar_env_config(
            clean_build=clean_build, node_env=node_env,
        )

    # Layer in extra env vars (e.g. CONSTRUCT_AUTH_PROFILE_ID for workflow
    # auth-profile bindings) on top of any existing env config.
    if env_extra:
        existing = config.get("env") or {}
        existing.update({k: str(v) for k, v in env_extra.items()})
        config["env"] = existing

    result = await _sidecar_client.create_agent(config)

    if "error" in result:
        _log(f"Sidecar create_agent failed: {result['error']}")
        return None

    return result


async def tool_create_agent(args: dict[str, Any], journal: SessionJournal, pool_client: KumihoAgentPoolClient) -> dict[str, Any]:
    running = sum(1 for a in AGENTS.values() if a.status == "running")
    if running >= MAX_CONCURRENT_AGENTS:
        return agent_limit_exceeded(running, MAX_CONCURRENT_AGENTS)

    title = args["title"][:60]
    initial_prompt = args.get("initial_prompt", "")

    template_name = args.get("template")
    tmpl: AgentTemplate | None = None
    template_warnings: list[str] = []
    if template_name:
        tmpl = POOL.templates.get(template_name)
        if tmpl is None:
            return template_not_found(template_name)
        # Quality gate check
        validation = POOL.validate_template(template_name)
        if not validation.valid:
            return {"error": f"Template '{template_name}' failed quality gates", "validation": validation.to_dict()}
        template_warnings = validation.warnings

    agent_type = args.get("agent_type")
    if agent_type is None:
        agent_type = tmpl.agent_type if tmpl else "claude"

    if agent_type not in ("claude", "codex"):
        return invalid_param("agent_type", agent_type, "'claude' or 'codex'")

    # Model selection: explicit arg > template > None (use sidecar default)
    model = args.get("model")
    if model is None and tmpl and tmpl.model:
        model = tmpl.model

    cwd = args.get("cwd")
    if cwd is None and tmpl and tmpl.default_cwd:
        cwd = tmpl.default_cwd
    if cwd is None:
        return missing_cwd()

    expanded_cwd = os.path.realpath(os.path.expanduser(cwd))
    if not os.path.isdir(expanded_cwd):
        return bad_directory(expanded_cwd)

    # Policy pre-flight check
    from ..policy import load_policy
    from ..failure_classification import policy_denied
    policy = load_policy()
    policy_failures = policy.preflight_spawn(expanded_cwd, agent_type)
    if policy_failures:
        fail = policy_failures[0]
        return policy_denied("cwd", expanded_cwd, fail.reason,
                             policy_rule=fail.policy_rule, suggestion=fail.suggestion)

    if tmpl and tmpl.system_hint and initial_prompt:
        initial_prompt = tmpl.system_hint + "\n\n" + initial_prompt
    elif tmpl and tmpl.system_hint and not initial_prompt:
        initial_prompt = tmpl.system_hint

    if tmpl:
        POOL.record_use(tmpl.name)

    agent_id = str(uuid.uuid4())

    # Journal first, then AGENTS — ensures persistent record exists before
    # in-memory registration.  If journal fails, agent is not registered.
    try:
        journal.record(
            agent_id, "created",
            agent_type=agent_type, title=title, cwd=cwd,
            template=template_name or "",
        )
    except Exception as e:
        _log(f"create_agent: journal write failed for {agent_id[:8]}: {e}")
        return classified_error(
            f"Cannot persist agent state: {e}",
            code="journal_write_failed",
            category=RUNTIME_ENV_ERROR,
            retryable=True,
        )

    agent = ManagedAgent(
        id=agent_id,
        agent_type=agent_type,
        title=title,
        cwd=cwd,
        status="idle",
    )
    AGENTS[agent_id] = agent

    # Initialize structured run log
    run_log = get_or_create_log(agent_id, title=title, agent_type=agent_type, cwd=expanded_cwd)
    if initial_prompt:
        run_log.record_prompt(initial_prompt)
        # Stash the original prompt on the agent so subprocess-mode follow-ups
        # can re-include it; sidecar path uses send_query and preserves context
        # internally, so this is only consumed by the subprocess fallback in
        # tool_send_agent_prompt below.
        agent._original_prompt = initial_prompt

    # Build role identity from template if available
    role_identity = ""
    template_hint = ""
    if tmpl:
        identity_parts = []
        if tmpl.identity:
            identity_parts.append(tmpl.identity)
        if tmpl.soul:
            identity_parts.append(tmpl.soul)
        if tmpl.tone:
            identity_parts.append(f"Communication style: {tmpl.tone}")
        role_identity = "\n".join(identity_parts)
        template_hint = tmpl.system_hint or ""

    # A2: Extract tool allowlist and max_turns from template
    allowed_tools: list[str] | None = None
    max_turns = 200
    if tmpl:
        allowed_tools = tmpl.allowed_tools
        max_turns = tmpl.max_turns

    # A1: Check if parent agent has cached params we can reuse
    parent_id = args.get("parent_id")
    cached_params: CacheSafeParams | None = None
    if parent_id:
        parent = AGENTS.get(parent_id)
        if parent and parent._cached_params:
            cached_params = parent._cached_params

    # Clean build options
    clean_build = args.get("clean_build", False)
    node_env = args.get("node_env", "development")

    # MCP injection level. `include_memory` and `include_operator` were
    # previously referenced inside the sidecar branch without being
    # defined in this function (latent NameError on the top-level path
    # where `cached_params` is None). Derive from args with sensible
    # defaults — both on by default, matching `_try_sidecar_create`.
    include_memory = args.get("include_memory", True)
    include_operator = args.get("include_operator", True)

    # Try sidecar first, fallback to subprocess
    sidecar_info = None
    if initial_prompt:
        sidecar_info = await _try_sidecar_create(
            agent_id, agent_type, title, cwd, initial_prompt,
            role_identity=role_identity,
            template_hint=template_hint,
            model=model,
            cached_params=cached_params,
            allowed_tools=allowed_tools,
            max_turns=max_turns,
            clean_build=clean_build,
            node_env=node_env,
        )
        if sidecar_info:
            agent.status = "running"
            agent._sidecar_id = sidecar_info.get("id", "")
            # A1: Capture cache-safe params for future children.
            # Use the actual MCP flags so children inherit the parent's
            # tool policy (e.g. tools: none → no MCP for children either).
            if not cached_params:
                mcp_servers = build_mcp_servers(
                    include_memory=include_memory,
                    include_operator=include_operator,
                    socket_path=_sidecar_client.socket_path if _sidecar_client else None,
                )
                sys_prompt = build_system_prompt(
                    is_top_level=parent_id is None,
                    role_identity=role_identity,
                    template_hint=template_hint,
                    include_memory=include_memory,
                    include_operator=include_operator,
                )
                agent._cached_params = CacheSafeParams(
                    system_prompt=sys_prompt,
                    mcp_servers=mcp_servers,
                )
            else:
                agent._cached_params = cached_params
            # Pre-register title and model so the global stream uses them
            # for events that arrive before subscribe() acquires the lock.
            if _event_consumer and agent._sidecar_id:
                _event_consumer._agent_titles[agent._sidecar_id] = title
                if model:
                    _event_consumer.set_agent_model(agent._sidecar_id, model)
                await _event_consumer.subscribe(agent._sidecar_id, title, model=model or "")
            try:
                journal.record(agent_id, "started_via_sidecar", sidecar_id=agent._sidecar_id)
            except Exception as e:
                _log(f"Journal write failed for sidecar start of {agent_id[:8]}: {e}")
        else:
            # Subprocess fallback. Build the same MCP server set the sidecar
            # path injects (kumiho-memory + operator-tools), so subprocess
            # agents can use memory recall / hierarchical spawning instead
            # of running tool-less. Stash on the agent so follow-up turns
            # via tool_send_agent_prompt can re-attach the same servers
            # without re-deriving them.
            sub_mcp_servers = build_mcp_servers(
                include_memory=include_memory,
                include_operator=include_operator,
                socket_path=_sidecar_client.socket_path if _sidecar_client else None,
            )
            agent._subprocess_mcp_servers = sub_mcp_servers
            await spawn_agent(
                agent,
                initial_prompt,
                journal,
                clean_build=clean_build,
                node_env=node_env,
                mcp_servers=sub_mcp_servers,
            )

    result: dict[str, Any] = {
        "agent_id": agent_id,
        "type": agent_type,
        "status": agent.status,
        "cwd": cwd,
        "title": title,
        "session_id": journal.session_id,
        "backend": "sidecar" if sidecar_info else "subprocess",
    }
    if model:
        result["model"] = model
    if template_name:
        result["template"] = template_name
    if template_warnings:
        result["template_warnings"] = template_warnings
    return result


def _agent_base(agent_id: str, agent: ManagedAgent) -> dict[str, Any]:
    """Common identity/metadata fields — included in EVERY agent response."""
    sidecar_id = getattr(agent, "_sidecar_id", None)
    return {
        "agent_id": agent_id,
        "sidecar_id": sidecar_id or None,
        "title": agent.title,
        "status": agent.status,
        "backend": "sidecar" if sidecar_id else "subprocess",
        "agent_type": agent.agent_type,
        "cwd": agent.cwd,
        "created_at": agent.created_at.isoformat() if hasattr(agent.created_at, "isoformat") else "",
    }


async def _sync_sidecar_events(agent_id: str, sidecar_id: str) -> None:
    """Fetch events from sidecar REST API and feed them to the RunLog.

    The SSE-based EventConsumer may not have delivered all events by the time
    wait_for_agent detects completion (race condition).  This explicit fetch
    ensures the RunLog is fully populated before we build the result.

    Always fetches from REST — the authoritative source.  Rebuilds the
    in-memory indexes on the agent_id RunLog so tool counts and last_message
    are correct regardless of what SSE delivered.
    """
    if not _sidecar_client or not sidecar_id:
        return
    try:
        events = await _sidecar_client.get_events(sidecar_id)
        if not events:
            return
        run_log = get_or_create_log(agent_id)
        # Reset in-memory indexes and rebuild from authoritative REST events.
        # This avoids double-counting if SSE partially delivered some events.
        run_log._tool_calls.clear()
        run_log._errors.clear()
        run_log._files_touched.clear()
        run_log._last_failing_command = None
        run_log._last_message = ""
        run_log._usage = {}
        for ev in events:
            run_log.record_event(ev)
    except Exception as e:
        _log(f"_sync_sidecar_events: failed for {agent_id[:8]}: {e}")


def _enrich_from_log(result: dict[str, Any], agent_id: str, agent: ManagedAgent) -> None:
    """Add activity data from RunLog or EventConsumer fallback.

    Always populates the same field set so callers get a stable schema.
    """
    sidecar_id = getattr(agent, "_sidecar_id", None)

    run_log = get_log(agent_id)
    if run_log is None and sidecar_id:
        run_log = get_log(sidecar_id)

    # Defaults — always present regardless of source
    result.setdefault("last_message", "")
    result.setdefault("tool_call_count", 0)
    result.setdefault("error_count", 0)
    result.setdefault("files_touched", [])
    result.setdefault("last_failing_command", None)
    result.setdefault("usage", {})
    result.setdefault("last_event_at", "")

    if run_log:
        summary = run_log.get_summary()
        result["last_message"] = summary.get("last_message", "")
        result["tool_call_count"] = summary.get("tool_call_count", 0)
        result["error_count"] = summary.get("error_count", 0)
        result["files_touched"] = summary.get("files_touched", [])
        result["last_failing_command"] = summary.get("last_failing_command")
        result["usage"] = summary.get("usage", {})
        # Timestamp of last tool call
        tool_calls = run_log.get_tool_calls(limit=1)
        if tool_calls:
            result["last_event_at"] = tool_calls[0].get("ts", "")
    elif _event_consumer and sidecar_id:
        activity = _event_consumer.get_curated_activity(sidecar_id)
        result["last_message"] = activity.get("last_message", "")
        sig = activity.get("significant_events", [])
        result["tool_call_count"] = activity.get("event_count", len(sig))
        if sig:
            result["last_event_at"] = sig[0].get("ts", "")
    elif agent.stdout_buffer:
        result["last_message"] = agent.stdout_buffer[-2000:]


# Cache of terminal wait results keyed by agent_id.
# Once an agent reaches a terminal state, _build_wait_result stores a snapshot
# here so that subsequent waits are instant, idempotent, and immune to
# transient sidecar/transport failures.
_terminal_result_cache: dict[str, dict[str, Any]] = {}


async def _build_wait_result(agent_id: str, agent: ManagedAgent, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a consistent wait_for_agent / activity response.

    Every call returns the same top-level field set, regardless of which
    backend is available (RunLog, EventConsumer, or subprocess buffers).
    Terminal results are cached for durability.
    """
    sidecar_id = getattr(agent, "_sidecar_id", None)
    if sidecar_id:
        await _sync_sidecar_events(agent_id, sidecar_id)
    result = _agent_base(agent_id, agent)
    _enrich_from_log(result, agent_id, agent)
    if extra:
        result.update(extra)

    # Cache terminal results so future waits are instant and idempotent
    effective_status = extra.get("status", agent.status) if extra else agent.status
    if effective_status in ("completed", "error", "closed"):
        _terminal_result_cache[agent_id] = result
        # Auto-capture finding into workflow context
        if _workflow_ctx is not None:
            finding = _workflow_ctx.capture(result)
            if finding is not None:
                # Best-effort Kumiho persistence (fire-and-forget)
                import asyncio
                asyncio.ensure_future(_workflow_ctx.persist_finding(finding))

    return result


async def tool_wait_for_agent(args: dict[str, Any]) -> dict[str, Any]:
    agent_id = args["agent_id"]
    # No artificial cap.  The Rust transport's send_and_recv has no internal
    # deadline — it relies on the outer tool_timeout (default 180s, max 600s).
    # The old 30s cap existed to prevent response queue desync, but that's
    # fixed by JSON-RPC ID correlation in mcp_transport.rs.
    # Default 120s covers most agents; callers can request more.
    timeout = int(args.get("timeout", 120))
    agent = AGENTS.get(agent_id)
    if agent is None:
        return agent_not_found(agent_id)

    # If already completed, return immediately (idempotent).
    # Check both in-memory status AND terminal cache for durability.
    if agent.status in ("completed", "error", "closed"):
        return await _build_wait_result(agent_id, agent)
    cached = _terminal_result_cache.get(agent_id)
    if cached:
        _log(f"wait_for_agent: {agent_id[:8]} returning cached terminal result")
        return cached

    sidecar_id = getattr(agent, "_sidecar_id", None)

    # Sidecar path: poll with adaptive interval.
    # Fast at first (catch quick completions), then ease off to reduce overhead.
    # Budget: stay well under MCP client timeout (~60s).
    if sidecar_id and _sidecar_client:
        import time as _time
        deadline = _time.monotonic() + timeout
        consecutive_failures = 0
        error_backoff = 0.5
        last_known_status = agent.status
        poll_count = 0
        # Adaptive sleep: 0.3s for first 3s, 0.7s for next 7s, 1.5s after that.
        # This gives fast response for quick agents while reducing poll load for
        # long-running ones.  Total budget fits comfortably within 30s.
        poll_start = _time.monotonic()

        while _time.monotonic() < deadline:
            poll_count += 1
            try:
                info = await _sidecar_client.get_agent(sidecar_id)
            except Exception as e:
                info = None
                _log(f"wait_for_agent: sidecar error for {agent_id[:8]}: {e}")

            if info is None:
                consecutive_failures += 1
                if consecutive_failures >= 8:
                    # Backend is unreachable — return last known state and enqueue
                    # background retry to update status when connectivity recovers.
                    _log(f"wait_for_agent: backend unreachable after {consecutive_failures} failures for {agent_id[:8]}")

                    # Enqueue background status check via retry queue
                    from ..retry_queue import get_retry_queue
                    rq = get_retry_queue()
                    _captured_sidecar = _sidecar_client
                    _captured_sid = sidecar_id
                    _captured_aid = agent_id

                    async def _bg_status_check() -> dict[str, Any]:
                        """Background check: poll sidecar once to recover agent status."""
                        if _captured_sidecar is None:
                            return {"error": "no sidecar", "retryable": True}
                        info = await _captured_sidecar.get_agent(_captured_sid)
                        if info is None:
                            return {"error": "still unreachable", "retryable": True}
                        status = info.get("status", "")
                        if status in ("idle", "error", "closed"):
                            a = AGENTS.get(_captured_aid)
                            if a:
                                a.status = "completed" if status == "idle" else status
                                _log(f"RetryQueue: recovered {_captured_aid[:8]} status={status}")
                        return {"status": status}

                    rq.enqueue(
                        op_id=f"wait-recover-{agent_id[:8]}",
                        coro_factory=_bg_status_check,
                        max_retries=5,
                        initial_backoff=5.0,
                    )

                    return await _build_wait_result(agent_id, agent, extra={
                        "status": "backend_unreachable",
                        "last_known_status": last_known_status,
                        "retry_enqueued": True,
                        "hint": "Sidecar is unreachable. Background retry enqueued — agent status will update when connectivity recovers. Call wait_for_agent again or check get_agent_run_log.",
                    })
                # Exponential backoff: 0.5, 1, 2, 2, 2...
                await asyncio.sleep(min(error_backoff, 2.0))
                error_backoff = min(error_backoff * 2, 2.0)
                continue

            consecutive_failures = 0
            error_backoff = 0.5
            status = info.get("status", "")
            last_known_status = status

            if status in ("idle", "error", "closed"):
                agent.status = "completed" if status == "idle" else status
                _log(f"wait_for_agent: {agent_id[:8]} completed after {poll_count} polls ({_time.monotonic() - poll_start:.1f}s)")
                return await _build_wait_result(agent_id, agent, extra={
                    "usage": info.get("usage"),
                })

            # Check for pending permissions (agent may be blocked)
            # Throttle: only check every 10th poll to avoid hammering
            if status == "running" and _sidecar_client and poll_count % 10 == 0:
                try:
                    pending = await _sidecar_client.list_pending_permissions()
                    agent_pending = [p for p in pending if p.get("agent_id") == sidecar_id]
                    if agent_pending:
                        tools = [p.get("tool", "unknown") for p in agent_pending]
                        return await _build_wait_result(agent_id, agent, extra={
                            "status": "permission_blocked",
                            "pending_permissions": agent_pending,
                            "hint": f"Agent is blocked on {len(agent_pending)} permission(s): {', '.join(tools)}. Use respond_to_permission to approve or deny.",
                        })
                except Exception:
                    pass  # Permission check is best-effort

            # Adaptive sleep interval based on elapsed time
            elapsed = _time.monotonic() - poll_start
            if elapsed < 3:
                interval = 0.3   # Fast: catch quick completions
            elif elapsed < 10:
                interval = 0.7   # Medium: agent is working
            else:
                interval = 1.5   # Slow: long-running, reduce poll load

            await asyncio.sleep(interval)

        # Timeout — but before reporting "still running", do one final check.
        # The agent may have completed between our last poll and the timeout.
        # This eliminates the common race where the poll loop exits at ~30s
        # just as the agent finishes.
        _log(f"wait_for_agent: {agent_id[:8]} timed out after {poll_count} polls ({timeout}s), doing final check")
        try:
            final_info = await _sidecar_client.get_agent(sidecar_id)
            if final_info:
                final_status = final_info.get("status", "")
                if final_status in ("idle", "error", "closed"):
                    agent.status = "completed" if final_status == "idle" else final_status
                    _log(f"wait_for_agent: {agent_id[:8]} caught by final check (status={final_status})")
                    return await _build_wait_result(agent_id, agent, extra={
                        "usage": final_info.get("usage"),
                    })
        except Exception as e:
            _log(f"wait_for_agent: {agent_id[:8]} final check failed: {e}")

        # Truly still running — return progress
        _log(f"wait_for_agent: {agent_id[:8]} confirmed still running")
        return await _build_wait_result(agent_id, agent, extra={
            "status": "running",
            "timeout_seconds": timeout,
            "poll_count": poll_count,
            "hint": "Agent still running. Call wait_for_agent again to continue waiting.",
        })

    # Subprocess fallback
    if agent.status != "running":
        return await _build_wait_result(agent_id, agent)
    if agent._reader_task is None:
        _log(f"wait_for_agent: {agent_id[:8]} running but no reader_task")
        return await _build_wait_result(agent_id, agent, extra={
            "warning": "Agent is running but reader task not initialized.",
        })

    try:
        await asyncio.wait_for(agent._reader_task, timeout=float(timeout))
    except asyncio.TimeoutError:
        return await _build_wait_result(agent_id, agent, extra={
            "status": "running",
            "timeout_seconds": timeout,
            "hint": "Agent still running. Call wait_for_agent again to continue waiting.",
        })
    except asyncio.CancelledError:
        _log(f"wait_for_agent: reader task cancelled for {agent_id[:8]}")
        agent.status = "error"
        return await _build_wait_result(agent_id, agent, extra={
            "status": "error",
            "error": "Agent reader task was cancelled unexpectedly.",
        })
    except Exception as e:
        _log(f"wait_for_agent: reader task exception for {agent_id[:8]}: {e}")
        agent.status = "error"
        return await _build_wait_result(agent_id, agent, extra={
            "status": "error",
            "error": f"Agent reader task failed: {e}",
        })

    return await _build_wait_result(agent_id, agent)


async def tool_send_agent_prompt(args: dict[str, Any], journal: SessionJournal) -> dict[str, Any]:
    agent_id = args["agent_id"]
    prompt = args["prompt"]
    agent = AGENTS.get(agent_id)
    if agent is None:
        return agent_not_found(agent_id)

    if agent.status == "running":
        return agent_busy(agent_id)

    sidecar_id = getattr(agent, "_sidecar_id", None)

    # Sidecar path
    if sidecar_id and _sidecar_client:
        result = await _sidecar_client.send_query(sidecar_id, prompt)
        if "error" not in result:
            agent.status = "running"
            try:
                journal.record(agent_id, "query_sent_via_sidecar", prompt_length=len(prompt))
            except Exception as e:
                _log(f"Journal write failed for query send to {agent_id[:8]}: {e}")
            return {
                "agent_id": agent_id,
                "status": agent.status,
                "backend": "sidecar",
            }
        _log(f"Sidecar send_query failed, falling through: {result.get('error')}")

    # Subprocess fallback
    #
    # `claude --print` and `codex exec` are one-shot: each invocation is a
    # fresh process with no memory of prior turns. So a bare follow-up like
    # "execute the task now" arrives without the original task context and
    # the agent legitimately responds "I don't see a specific task in your
    # message." Stitch the original prompt + last response + new follow-up
    # so the agent resumes coherently. The previous response is truncated
    # to keep the composite within reasonable prompt-length limits.
    prev_response = agent.stdout_buffer.strip()
    original = agent._original_prompt
    if original or prev_response:
        parts: list[str] = []
        if original:
            parts.append(f"## Original task\n\n{original}")
        if prev_response:
            truncated = prev_response[-6000:]
            elision = "[…earlier output truncated…]\n\n" if len(prev_response) > 6000 else ""
            parts.append(f"## Your previous response\n\n{elision}{truncated}")
        parts.append(f"## Follow-up\n\n{prompt}")
        full_prompt = "\n\n".join(parts)
    else:
        full_prompt = prompt

    agent.stdout_buffer = ""
    agent.stderr_buffer = ""
    await spawn_agent(
        agent,
        full_prompt,
        journal,
        mcp_servers=agent._subprocess_mcp_servers,
    )

    return {
        "agent_id": agent_id,
        "status": agent.status,
    }


async def tool_get_agent_activity(args: dict[str, Any]) -> dict[str, Any]:
    agent_id = args["agent_id"]
    agent = AGENTS.get(agent_id)
    if agent is None:
        return agent_not_found(agent_id)

    sidecar_id = getattr(agent, "_sidecar_id", None)

    # Try to load from disk if not in memory
    run_log = get_log(agent_id)
    if run_log is None and sidecar_id:
        run_log = get_log(sidecar_id)
    if run_log is None:
        run_log = load_log_from_disk(agent_id)
    if run_log is None and sidecar_id:
        run_log = load_log_from_disk(sidecar_id)

    # Sync events from sidecar before enriching (same as wait_for_agent)
    if sidecar_id:
        await _sync_sidecar_events(agent_id, sidecar_id)

    # Same base + enrichment as wait_for_agent
    result = _agent_base(agent_id, agent)
    _enrich_from_log(result, agent_id, agent)

    # If RunLog available from disk, merge the full summary (may have extra detail)
    if run_log:
        summary = run_log.get_summary()
        result["total_events"] = summary.get("total_events", 0)

    return result


async def tool_get_agent_run_log(args: dict[str, Any]) -> dict[str, Any]:
    """Query detailed structured run log for an agent."""
    agent_id = args["agent_id"]
    view = args.get("view", "summary")
    limit = args.get("limit", 50)

    run_log = get_log(agent_id)
    if run_log is None:
        agent = AGENTS.get(agent_id)
        sidecar_id = getattr(agent, "_sidecar_id", None) if agent else None
        if sidecar_id:
            run_log = get_log(sidecar_id) or load_log_from_disk(sidecar_id)
    if run_log is None:
        run_log = load_log_from_disk(agent_id)

    if run_log is None:
        return {"error": f"No run log found for agent {agent_id}."}

    if view == "summary":
        return run_log.get_summary()
    elif view == "tool_calls":
        return {"agent_id": agent_id, "tool_calls": run_log.get_tool_calls(limit=limit)}
    elif view == "errors":
        return {"agent_id": agent_id, "errors": run_log.get_errors()}
    elif view == "files":
        return {"agent_id": agent_id, "files_touched": run_log.get_files_touched()}
    elif view == "full":
        return {"agent_id": agent_id, "entries": run_log.get_full_log(limit=limit)}
    else:
        return {"error": f"Unknown view: {view}. Use: summary, tool_calls, errors, files, full"}


async def tool_list_run_logs() -> dict[str, Any]:
    """List all available run logs on disk."""
    return {"run_logs": list_run_logs()}


async def tool_get_circuit_breaker_status() -> dict[str, Any]:
    """Return circuit breaker status for sidecar connection."""
    if _sidecar_client and hasattr(_sidecar_client, "breaker"):
        return {"circuit_breaker": _sidecar_client.breaker.status()}
    return {"circuit_breaker": {"state": "n/a", "reason": "no sidecar client"}}


async def tool_reset_circuit_breaker() -> dict[str, Any]:
    """Force-reset the sidecar circuit breaker to CLOSED."""
    if _sidecar_client and hasattr(_sidecar_client, "breaker"):
        await _sidecar_client.breaker.reset()
        return {"circuit_breaker": _sidecar_client.breaker.status(), "reset": True}
    return {"error": "no sidecar client"}


async def tool_list_agents() -> dict[str, Any]:
    # Snapshot to avoid iteration over a live dict across potential await points
    snapshot = list(AGENTS.values())
    agents_list = [_agent_base(a.id, a) for a in snapshot]
    return {"agents": agents_list, "count": len(agents_list)}


# -- Cancellation --------------------------------------------------------------

import signal as _signal


async def _cancel_one(agent: ManagedAgent) -> dict[str, Any]:
    """Cancel a single agent. Returns status dict."""
    agent_id = agent.id
    sidecar_id = getattr(agent, "_sidecar_id", None)

    # Sidecar path: interrupt then close
    if sidecar_id and _sidecar_client:
        try:
            await _sidecar_client.interrupt_agent(sidecar_id)
            await asyncio.sleep(1.0)
            await _sidecar_client.close_agent(sidecar_id)
        except Exception as e:
            _log(f"cancel_agent: sidecar cancel failed for {agent_id[:8]}: {e}")
        agent.status = "cancelled"
        if _event_consumer:
            await _event_consumer.unsubscribe(sidecar_id)
        return {"agent_id": agent_id, "status": "cancelled", "method": "sidecar"}

    # Subprocess path: signal escalation SIGINT → SIGTERM → SIGKILL
    proc = agent.process
    if proc is None or proc.returncode is not None:
        agent.status = "cancelled"
        return {"agent_id": agent_id, "status": "cancelled", "method": "already_stopped"}

    agent.status = "cancelling"

    # 1. SIGINT (graceful)
    try:
        proc.send_signal(_signal.SIGINT)
    except (ProcessLookupError, OSError):
        agent.status = "cancelled"
        return {"agent_id": agent_id, "status": "cancelled", "method": "already_exited"}

    # Wait up to 5s for graceful exit
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
        agent.status = "cancelled"
        return {"agent_id": agent_id, "status": "cancelled", "method": "sigint", "exit_code": proc.returncode}
    except asyncio.TimeoutError:
        pass

    # 2. SIGTERM
    try:
        proc.terminate()
    except (ProcessLookupError, OSError):
        agent.status = "cancelled"
        return {"agent_id": agent_id, "status": "cancelled", "method": "sigterm_race"}

    try:
        await asyncio.wait_for(proc.wait(), timeout=3.0)
        agent.status = "cancelled"
        return {"agent_id": agent_id, "status": "cancelled", "method": "sigterm", "exit_code": proc.returncode}
    except asyncio.TimeoutError:
        pass

    # 3. SIGKILL (force)
    try:
        proc.kill()
        await asyncio.wait_for(proc.wait(), timeout=2.0)
    except Exception:
        pass

    agent.status = "cancelled"
    _log(f"cancel_agent: force-killed {agent_id[:8]}")
    return {"agent_id": agent_id, "status": "cancelled", "method": "sigkill", "exit_code": proc.returncode}


async def tool_cancel_agent(args: dict[str, Any]) -> dict[str, Any]:
    """Cancel a running agent."""
    agent_id = args.get("agent_id", "")
    agent = AGENTS.get(agent_id)
    if agent is None:
        return agent_not_found(agent_id)

    if agent.status in ("cancelled", "completed", "closed"):
        return {"agent_id": agent_id, "status": agent.status, "already_stopped": True}

    result = await _cancel_one(agent)
    _log(f"cancel_agent: {agent_id[:8]} → {result.get('method')}")
    return result


async def tool_cancel_all_agents(args: dict[str, Any]) -> dict[str, Any]:
    """Cancel all running agents."""
    results = []
    running = [a for a in AGENTS.values() if a.status in ("running", "cancelling")]
    if not running:
        return {"cancelled": 0, "results": [], "message": "No running agents to cancel."}

    for agent in running:
        result = await _cancel_one(agent)
        results.append(result)
        _log(f"cancel_all: {agent.id[:8]} → {result.get('method')}")

    return {"cancelled": len(results), "results": results}
