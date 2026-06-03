"""Supervisor Delegation Pattern — dynamic task decomposition and routing.

The operator acts as a supervisor, iteratively analyzing the task,
delegating subtasks to the best available specialists, integrating
results, and repeating until the task is complete.

Differs from spawn_team (static DAG) in that the next agent is chosen
dynamically based on results so far.

Usage:
    supervisor_run(task="Fix the auth bug in /src/auth",
                   cwd="/project", max_iterations=5)
"""
from __future__ import annotations

import os
from typing import Any

from .._log import _log
from ..agent_state import POOL
from ..construct_config import harness_project
from ..failure_classification import (
    bad_directory,
    classified_error,
    policy_denied,
    VALIDATION_ERROR,
)
from .refinement import _spawn_and_wait, _get_agent_output


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SUPERVISOR_ANALYZE_PROMPT = """\
You are a task supervisor. Analyze the current state and decide the next step.

## Original task
{task}

## Available specialist agents
{available_agents}

## Work done so far
{work_history}

## Instructions
Decide ONE of these actions:
1. DELEGATE to a specialist — write:
   ACTION: DELEGATE
   AGENT: <agent_type or template_name>
   SUBTASK: <specific subtask description>

2. Declare the task COMPLETE — write:
   ACTION: COMPLETE
   SUMMARY: <final summary of what was accomplished>

3. REQUEST more information — write:
   ACTION: REQUEST_INFO
   QUESTION: <what you need to know>

Be decisive. Each iteration costs time and resources.
"""

_SPECIALIST_PROMPT = """\
You are a specialist agent working on a subtask.

## Overall task context
{task}

## Your specific subtask
{subtask}

## Prior work (from other specialists)
{prior_work}

## Instructions
- Focus on your specific subtask.
- Be thorough but concise.
- Report what you did and what you found.
"""


# ---------------------------------------------------------------------------
# Action parsing
# ---------------------------------------------------------------------------

def _parse_supervisor_action(text: str) -> dict[str, str]:
    """Parse supervisor's decision from output text."""
    import re

    action = "unknown"
    agent = ""
    subtask = ""
    summary = ""
    question = ""

    a_match = re.search(r"ACTION:\s*(DELEGATE|COMPLETE|REQUEST_INFO)", text, re.IGNORECASE)
    if a_match:
        action = a_match.group(1).upper()

    if action == "DELEGATE":
        ag_match = re.search(r"AGENT:\s*(.+?)(?=\n|SUBTASK:|$)", text, re.IGNORECASE)
        if ag_match:
            agent = ag_match.group(1).strip()
        st_match = re.search(r"SUBTASK:\s*(.+?)(?=\nACTION:|\nAGENT:|$)", text, re.DOTALL | re.IGNORECASE)
        if st_match:
            subtask = st_match.group(1).strip()[:2000]
    elif action == "COMPLETE":
        s_match = re.search(r"SUMMARY:\s*(.+)", text, re.DOTALL | re.IGNORECASE)
        if s_match:
            summary = s_match.group(1).strip()[:4000]
    elif action == "REQUEST_INFO":
        q_match = re.search(r"QUESTION:\s*(.+)", text, re.DOTALL | re.IGNORECASE)
        if q_match:
            question = q_match.group(1).strip()[:2000]

    return {
        "action": action,
        "agent": agent,
        "subtask": subtask,
        "summary": summary,
        "question": question,
    }


def _resolve_agent_type(agent_hint: str) -> str:
    """Resolve an agent hint to a concrete agent_type (claude/codex).

    Checks pool templates first, falls back to literal if it's a valid type.
    """
    # Check pool
    for tmpl in POOL.list_all():
        if tmpl.name.lower() == agent_hint.lower():
            return tmpl.agent_type
    # Check if it's a direct type
    if agent_hint.lower() in ("claude", "codex"):
        return agent_hint.lower()
    # Default: use claude for research-like, codex for coding-like
    coding_hints = ("cod", "fix", "implement", "build", "write", "refactor")
    if any(h in agent_hint.lower() for h in coding_hints):
        return "codex"
    return "claude"


def _resolve_template(agent_hint: str):
    """Return a pool template by name, case-insensitive."""
    for tmpl in POOL.list_all():
        if tmpl.name.lower() == agent_hint.lower():
            return tmpl
    return None


# ---------------------------------------------------------------------------
# Trust-informed agent selection
# ---------------------------------------------------------------------------

async def _get_trust_ranking(agent_hint: str) -> float:
    """Get trust score for an agent template. Returns 1.0 if unavailable."""
    try:
        from ..operator_mcp import KUMIHO_POOL
        if not KUMIHO_POOL._available:
            return 1.0
        items = await KUMIHO_POOL.list_items(f"/{harness_project()}/AgentTrust")
        for item in items:
            if agent_hint.lower() in item.get("item_name", "").lower():
                rev = await KUMIHO_POOL.get_latest_revision(item.get("kref"))
                if rev:
                    return float(rev.get("metadata", {}).get("trust_score", 1.0))
        return 1.0
    except Exception:
        return 1.0


# ---------------------------------------------------------------------------
# Tool handler
# ---------------------------------------------------------------------------

async def tool_supervisor_run(args: dict[str, Any]) -> dict[str, Any]:
    """Run a supervisor delegation loop — dynamically decompose and delegate.

    Args:
        task: Task description (required).
        cwd: Working directory (required).
        templates: Optional list of available template names. Defaults to all in pool.
        max_iterations: Max delegate→integrate cycles (default 5, max 10).
        supervisor_type: Agent type for supervisor analysis (default "claude").
        model: Optional model override.
        timeout: Per-agent timeout (default 300s).
    """
    task = args.get("task", "")
    cwd = args.get("cwd", "")
    templates = args.get("templates", [])
    max_iterations = min(args.get("max_iterations", 5), 10)
    supervisor_type = args.get("supervisor_type", "claude")
    requested_model = args.get("model")
    timeout = args.get("timeout", 300.0)
    supervisor_template = _resolve_template(supervisor_type)
    supervisor_model = requested_model
    supervisor_role = "architect"
    supervisor_identity = ""
    supervisor_name = "supervisor"
    if supervisor_template:
        supervisor_type = supervisor_template.agent_type
        supervisor_model = requested_model or supervisor_template.model
        supervisor_role = supervisor_template.role or "architect"
        supervisor_name = supervisor_template.name
        supervisor_identity = "\n".join(
            part for part in (
                supervisor_template.description,
                supervisor_template.identity,
                supervisor_template.soul,
                (
                    f"Capabilities: {', '.join(supervisor_template.capabilities)}"
                    if supervisor_template.capabilities else ""
                ),
            ) if part
        )

    if not task:
        return classified_error("task is required", code="missing_task", category=VALIDATION_ERROR)
    if not cwd:
        return classified_error("cwd is required", code="missing_cwd", category=VALIDATION_ERROR)

    cwd = os.path.realpath(os.path.expanduser(cwd))
    if not os.path.isdir(cwd):
        return bad_directory(cwd)

    from ..policy import load_policy
    policy = load_policy()

    # Build available agents list
    if templates:
        available_desc = "\n".join(f"- {t}" for t in templates)
    else:
        pool_agents = POOL.list_all()
        available_desc = "\n".join(
            f"- {t.name} ({t.agent_type}, {t.role}): {t.description[:80]}"
            for t in pool_agents
        ) if pool_agents else "- claude (general purpose)\n- codex (code generation)"

    iterations: list[dict[str, Any]] = []
    work_history: list[str] = []
    final_summary = ""
    status = "max_iterations_reached"

    for iteration in range(1, max_iterations + 1):
        _log(f"supervisor: iteration {iteration}/{max_iterations}")

        work_str = (
            "\n".join(f"[Step {i+1}] {w}" for i, w in enumerate(work_history))
            if work_history else "(No work done yet)"
        )

        # Ask supervisor what to do next
        analyze_prompt = _SUPERVISOR_ANALYZE_PROMPT.format(
            task=task,
            available_agents=available_desc,
            work_history=work_str[:6000],
        )
        from ..agent_subprocess import compose_agent_prompt
        sup_agent, sup_output = await _spawn_and_wait(
            supervisor_type, f"supervisor-iter{iteration}", cwd,
            compose_agent_prompt(supervisor_name, supervisor_role, supervisor_identity, [], analyze_prompt),
            model=supervisor_model, timeout=timeout,
        )

        decision = _parse_supervisor_action(sup_output)
        _log(f"supervisor: iteration {iteration} action={decision['action']}")

        iter_info: dict[str, Any] = {
            "iteration": iteration,
            "supervisor_agent_id": sup_agent.id,
            "action": decision["action"],
        }

        if decision["action"] == "COMPLETE":
            final_summary = decision["summary"] or sup_output[:4000]
            iter_info["summary"] = final_summary
            iterations.append(iter_info)
            status = "completed"
            break

        if decision["action"] == "REQUEST_INFO":
            iter_info["question"] = decision["question"]
            iterations.append(iter_info)
            work_history.append(f"Supervisor requested info: {decision['question'][:200]}")
            # In automated mode, we can't answer — note it and continue
            continue

        if decision["action"] == "DELEGATE":
            agent_hint = decision["agent"]
            subtask = decision["subtask"]
            agent_type = _resolve_agent_type(agent_hint)
            spec_template = _resolve_template(agent_hint)
            spec_role = spec_template.role if spec_template else "coder"
            spec_identity = ""
            spec_model = requested_model
            if spec_template:
                spec_model = requested_model or spec_template.model
                spec_identity = "\n".join(
                    part for part in (
                        spec_template.description,
                        spec_template.identity,
                        spec_template.soul,
                        (
                            f"Capabilities: {', '.join(spec_template.capabilities)}"
                            if spec_template.capabilities else ""
                        ),
                    ) if part
                )

            # Policy check
            policy_failures = policy.preflight_spawn(cwd, agent_type)
            if policy_failures:
                fail = policy_failures[0]
                iter_info["error"] = f"Policy denied: {fail.reason}"
                iterations.append(iter_info)
                work_history.append(f"Delegation to {agent_hint} blocked by policy: {fail.reason}")
                continue

            # Spawn specialist
            prior_work = "\n".join(work_history[-3:]) if work_history else "(first task)"
            spec_prompt = _SPECIALIST_PROMPT.format(
                task=task,
                subtask=subtask,
                prior_work=prior_work[:4000],
            )
            spec_agent, spec_output = await _spawn_and_wait(
                agent_type, f"specialist-{agent_hint[:15]}-iter{iteration}", cwd,
                compose_agent_prompt(
                    spec_template.name if spec_template else f"specialist-{iteration}",
                    spec_role,
                    spec_identity,
                    [],
                    spec_prompt,
                ),
                model=spec_model, timeout=timeout,
            )

            spec_text, spec_files = _get_agent_output(spec_agent.id)
            effective_output = spec_text or spec_output

            iter_info["specialist_agent_id"] = spec_agent.id
            iter_info["specialist_type"] = agent_type
            iter_info["subtask"] = subtask
            iter_info["specialist_status"] = spec_agent.status
            iter_info["files_touched"] = spec_files
            iterations.append(iter_info)

            work_history.append(
                f"Delegated to {agent_hint} ({agent_type}): {subtask[:100]}. "
                f"Result: {effective_output[:500]}. Files: {', '.join(spec_files[:5]) if spec_files else 'none'}"
            )
        else:
            # Unknown action — log and continue
            iter_info["raw_output"] = sup_output[:2000]
            iterations.append(iter_info)
            work_history.append(f"Supervisor gave unclear action: {sup_output[:200]}")

    result: dict[str, Any] = {
        "task": task,
        "status": status,
        "total_iterations": len(iterations),
        "final_summary": final_summary,
        "iterations": iterations,
        "work_history": work_history,
    }

    _log(f"supervisor: {status} after {len(iterations)} iterations")
    return result
