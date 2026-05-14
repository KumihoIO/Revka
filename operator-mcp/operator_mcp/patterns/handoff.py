"""Handoff Pattern — agent-to-agent context transfer mid-workflow.

Agent A completes or reaches a decision point → hands off to Agent B with
full context (findings, files touched, task state). The handoff chain is
tracked in Kumiho with HANDED_OFF_TO edges.

Usage:
    handoff_agent(from_agent_id="...", to_template="coder-codex",
                  reason="Bug identified, needs implementation", cwd="/path")
"""
from __future__ import annotations

import os
import uuid
from typing import Any

from .._log import _log
from ..agent_state import AGENTS, ManagedAgent, POOL
from ..agent_subprocess import compose_agent_prompt, spawn_agent
from ..failure_classification import (
    agent_not_found,
    bad_directory,
    classified_error,
    policy_denied,
    VALIDATION_ERROR,
)
from ..run_log import get_log
from .refinement import _spawn_and_wait, _get_agent_output


# ---------------------------------------------------------------------------
# Context extraction
# ---------------------------------------------------------------------------

def _extract_handoff_context(agent_id: str) -> dict[str, Any]:
    """Extract context from an agent for handoff to the next agent.

    Returns:
        Dict with: last_message, files_touched, tool_calls_summary, status.
    """
    agent = AGENTS.get(agent_id)
    if not agent:
        return {"error": f"Agent {agent_id} not found"}

    last_message, files_touched = _get_agent_output(agent_id)

    # Get tool call summary from run log
    run_log = get_log(agent_id)
    sidecar_id = getattr(agent, "_sidecar_id", None)
    if run_log is None and sidecar_id:
        run_log = get_log(sidecar_id)

    tool_calls: list[str] = []
    if run_log:
        summary = run_log.get_summary()
        tool_calls = summary.get("tool_calls_summary", [])
        if not tool_calls:
            tc_count = summary.get("tool_call_count", 0)
            tool_calls = [f"{tc_count} tool calls executed"]

    return {
        "last_message": last_message[:6000] if last_message else "(no output)",
        "files_touched": files_touched,
        "tool_calls_summary": tool_calls[:20],
        "agent_title": agent.title,
        "agent_type": agent.agent_type,
        "status": agent.status,
    }


# ---------------------------------------------------------------------------
# Handoff prompt
# ---------------------------------------------------------------------------

_HANDOFF_PROMPT = """\
You are receiving a handoff from agent "{from_title}" ({from_type}).

## Reason for handoff
{reason}

## Context from previous agent
{context_summary}

## Files already touched
{files_touched}

## Your task
{task}

## Instructions
- Build on the previous agent's work — do NOT start from scratch.
- The files listed above already contain changes from the previous agent.
- Focus on the specific reason for this handoff.
"""


def _resolve_template(name: str):
    for tmpl in POOL.list_all():
        if tmpl.name.lower() == name.lower():
            return tmpl
    return None


# ---------------------------------------------------------------------------
# Tool handler
# ---------------------------------------------------------------------------

async def tool_handoff_agent(args: dict[str, Any]) -> dict[str, Any]:
    """Hand off work from one agent to another with full context transfer.

    Args:
        from_agent_id: Agent whose work to hand off (must exist).
        to_template: Agent type or template name for the receiving agent.
        to_agent_type: CLI type for receiver (default "claude"). Overridden by template.
        reason: Why the handoff is happening.
        task: Specific task for the receiving agent. If omitted, uses "Continue the work".
        cwd: Working directory (defaults to from_agent's cwd).
        model: Optional model override.
        timeout: Per-agent timeout (default 300s).
    """
    from_agent_id = args.get("from_agent_id", "")
    to_agent_type = args.get("to_agent_type", args.get("to_template", "claude"))
    reason = args.get("reason", "Continuing the task")
    task = args.get("task", "")
    cwd = args.get("cwd", "")
    model = args.get("model")
    timeout = args.get("timeout", 300.0)

    if not from_agent_id:
        return classified_error(
            "from_agent_id is required",
            code="missing_from_agent", category=VALIDATION_ERROR,
        )

    from_agent = AGENTS.get(from_agent_id)
    if not from_agent:
        return agent_not_found(from_agent_id)

    # Default cwd from source agent
    if not cwd:
        cwd = from_agent.cwd
    cwd = os.path.realpath(os.path.expanduser(cwd))
    if not os.path.isdir(cwd):
        return bad_directory(cwd)

    from ..policy import load_policy
    policy = load_policy()
    receiver_template = _resolve_template(to_agent_type)
    # Normalize agent_type for policy check. Template names resolve through the
    # pool so dashboard-selected handoff receivers are not collapsed to claude.
    effective_type = (
        receiver_template.agent_type if receiver_template
        else to_agent_type if to_agent_type in ("claude", "codex")
        else "claude"
    )
    policy_failures = policy.preflight_spawn(cwd, effective_type)
    if policy_failures:
        fail = policy_failures[0]
        return policy_denied("cwd", cwd, fail.reason,
                             policy_rule=fail.policy_rule, suggestion=fail.suggestion)

    # Extract context from source agent
    context = _extract_handoff_context(from_agent_id)
    if "error" in context:
        return context

    files_str = ", ".join(context["files_touched"]) if context["files_touched"] else "(none)"
    effective_task = task or f"Continue the work started by {context['agent_title']}"

    prompt = _HANDOFF_PROMPT.format(
        from_title=context["agent_title"],
        from_type=context["agent_type"],
        reason=reason,
        context_summary=context["last_message"],
        files_touched=files_str,
        task=effective_task,
    )

    title = f"handoff-from-{from_agent.title[:20]}"
    receiver_identity = ""
    receiver_role = "coder"
    model_override = model
    if receiver_template:
        receiver_role = receiver_template.role or "coder"
        model_override = model or receiver_template.model
        receiver_identity = "\n".join(
            part for part in (
                receiver_template.description,
                receiver_template.identity,
                receiver_template.soul,
                f"Capabilities: {', '.join(receiver_template.capabilities)}" if receiver_template.capabilities else "",
            ) if part
        )

    full_prompt = compose_agent_prompt(
        receiver_template.name if receiver_template else title,
        receiver_role,
        receiver_identity,
        [],
        prompt,
    )

    to_agent, output = await _spawn_and_wait(
        effective_type, title, cwd, full_prompt,
        model=model_override, timeout=timeout,
    )

    to_output, to_files = _get_agent_output(to_agent.id)

    # Record handoff in Kumiho (best-effort)
    handoff_kref = None
    try:
        from ..operator_mcp import KUMIHO_SDK
        if KUMIHO_SDK._available:
            handoff_kref = await KUMIHO_SDK.create_edge(
                from_kref=f"agent:{from_agent_id}",
                to_kref=f"agent:{to_agent.id}",
                edge_type="HANDED_OFF_TO",
                metadata={"reason": reason[:200]},
            )
    except Exception as exc:
        _log(f"handoff: Kumiho edge creation failed: {exc}")

    result: dict[str, Any] = {
        "from_agent_id": from_agent_id,
        "from_agent_title": from_agent.title,
        "to_agent_id": to_agent.id,
        "to_agent_title": to_agent.title,
        "to_agent_status": to_agent.status,
        "reason": reason,
        "context_transferred": {
            "files_from_source": context["files_touched"],
            "message_length": len(context["last_message"]),
        },
        "to_agent_output": (to_output or output)[:4000],
        "to_agent_files": to_files,
        "handoff_kref": handoff_kref,
    }

    _log(f"handoff: {from_agent.title} → {to_agent.title} ({reason[:50]})")
    return result
