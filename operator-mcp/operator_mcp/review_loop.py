"""Reviewer/fix loop — automated review → fix cycle pattern.

Spawns a reviewer agent to inspect a coder's work, parses the verdict,
and if changes are needed, spawns a fixer agent with the feedback injected.
Repeats up to max_rounds.

Usage (via MCP tool):
    review_fix_loop(coder_agent_id="...", cwd="/path", max_rounds=2)

The loop is synchronous from the operator's perspective — it blocks until
the full cycle completes (or is halted).  Each round is logged for auditability.
"""
from __future__ import annotations

import asyncio
import os
import re
import uuid
from typing import Any

from ._log import _log
from .agent_state import AGENTS, ManagedAgent
from .agent_subprocess import compose_agent_prompt, spawn_agent
from .failure_classification import (
    agent_not_found,
    bad_directory,
    classified_error,
    policy_denied,
    RUNTIME_ENV_ERROR,
    VALIDATION_ERROR,
)
from .run_log import get_log


# ---------------------------------------------------------------------------
# Verdict parsing
# ---------------------------------------------------------------------------

_VERDICT_PATTERNS = [
    # Explicit structured verdicts (preferred — reviewer prompt asks for these)
    (re.compile(r"VERDICT:\s*APPROVED", re.IGNORECASE), "approved"),
    (re.compile(r"VERDICT:\s*NEEDS[_\s]?CHANGES", re.IGNORECASE), "needs_changes"),
    (re.compile(r"VERDICT:\s*BLOCKED", re.IGNORECASE), "blocked"),
    # Fallback heuristics
    (re.compile(r"\bLGTM\b", re.IGNORECASE), "approved"),
    (re.compile(r"\bapproved?\b", re.IGNORECASE), "approved"),
    (re.compile(r"\bneeds?\s+changes?\b", re.IGNORECASE), "needs_changes"),
    (re.compile(r"\brequest(?:ed|ing)?\s+changes?\b", re.IGNORECASE), "needs_changes"),
]


def parse_verdict(text: str) -> str:
    """Extract a verdict from reviewer output text.

    Returns one of: "approved", "needs_changes", "blocked", "unclear".
    Structured VERDICT: lines take priority over heuristic matches.
    """
    if not text:
        return "unclear"
    for pattern, verdict in _VERDICT_PATTERNS:
        if pattern.search(text):
            return verdict
    return "unclear"


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_REVIEWER_PROMPT = """\
You are a code reviewer. Review the work done by agent "{coder_title}" in {cwd}.

## Original task
{task}

## Coder output
{coder_output}

## Files touched
{files_touched}

{review_focus}

## Instructions
- Check for correctness, edge cases, security issues, style consistency.
- Be specific and actionable in your feedback.
- End your review with exactly one of these verdict lines:
  VERDICT: APPROVED
  VERDICT: NEEDS_CHANGES
  VERDICT: BLOCKED
- If NEEDS_CHANGES, list each required fix as a numbered item.
"""

_FIXER_PROMPT = """\
You are a fixer agent. A reviewer found issues in work for the following task.
Apply the reviewer's feedback precisely.

## Original task
{task}

## Reviewer feedback (round {round_num})
{reviewer_feedback}

## Files to fix
{files_touched}

## Instructions
- Address every numbered item from the reviewer.
- Do NOT add unrelated changes.
- After fixing, briefly summarize what you changed.
"""


# ---------------------------------------------------------------------------
# Core loop
# ---------------------------------------------------------------------------

async def _wait_for_agent(agent: ManagedAgent, *, timeout: float = 300.0) -> str:
    """Wait for an agent to complete and return its last message.

    Handles both subprocess agents (_reader_task) and sidecar agents
    (poll sidecar status until terminal).
    """
    sidecar_id = getattr(agent, "_sidecar_id", None)

    if sidecar_id:
        # Sidecar path: poll for completion
        from .tool_handlers.agents import _sidecar_client
        if _sidecar_client:
            deadline = asyncio.get_event_loop().time() + timeout
            poll_interval = 1.0
            while asyncio.get_event_loop().time() < deadline:
                if agent.status in ("completed", "error", "closed"):
                    break
                try:
                    info = await _sidecar_client.get_agent(sidecar_id)
                    if info:
                        status = info.get("status", "")
                        if status in ("idle", "error", "closed"):
                            agent.status = "completed" if status == "idle" else status
                            break
                except Exception:
                    pass
                remaining = deadline - asyncio.get_event_loop().time()
                await asyncio.sleep(min(poll_interval, max(0.1, remaining)))
                poll_interval = min(poll_interval * 1.2, 5.0)
            else:
                _log(f"review_loop: agent {agent.id[:8]} timed out ({timeout}s)")
                return f"[TIMEOUT after {timeout}s]"
    elif agent._reader_task:
        # Subprocess path
        try:
            await asyncio.wait_for(agent._reader_task, timeout=timeout)
        except asyncio.TimeoutError:
            _log(f"review_loop: agent {agent.id[:8]} timed out ({timeout}s)")
            return f"[TIMEOUT after {timeout}s]"

    # Extract output from RunLog or stdout buffer
    run_log = get_log(agent.id)
    if run_log is None and sidecar_id:
        run_log = get_log(sidecar_id)

    if run_log:
        summary = run_log.get_summary()
        return summary.get("last_message", "")
    return agent.stdout_buffer[-4000:] if agent.stdout_buffer else ""


async def _spawn_and_wait(
    agent_type: str,
    title: str,
    cwd: str,
    prompt: str,
    *,
    model: str | None = None,
    timeout: float = 300.0,
) -> tuple[ManagedAgent, str]:
    """Spawn an agent, wait for completion, return (agent, output_text)."""
    from .budget_authority import BudgetGateError
    from .tool_handlers.agents import _try_sidecar_create, _event_consumer

    agent_id = str(uuid.uuid4())
    agent = ManagedAgent(
        id=agent_id,
        agent_type=agent_type,
        title=title,
        cwd=cwd,
        status="idle",
    )
    AGENTS[agent_id] = agent

    # Try sidecar, fallback to subprocess
    try:
        sidecar_info = await _try_sidecar_create(
            agent_id, agent_type, title, cwd, prompt, model=model,
        )
    except BudgetGateError as exc:
        agent.status = "error"
        return agent, str(exc.response.get("error", exc))
    if sidecar_info:
        agent.status = "running"
        agent._sidecar_id = sidecar_info.get("id", "")
        if _event_consumer and agent._sidecar_id:
            await _event_consumer.subscribe(agent._sidecar_id, title)
    else:
        from .operator_mcp import JOURNAL
        try:
            await spawn_agent(agent, prompt, JOURNAL)
        except Exception:
            agent.status = "error"
            return agent, agent.stderr_buffer[-2000:] if agent.stderr_buffer else "spawn failed"

    output = await _wait_for_agent(agent, timeout=timeout)
    return agent, output


def _get_agent_output(agent_id: str) -> tuple[str, list[str]]:
    """Get an agent's last message and files touched from RunLog."""
    agent = AGENTS.get(agent_id)
    if not agent:
        return "", []

    run_log = get_log(agent_id)
    sidecar_id = getattr(agent, "_sidecar_id", None)
    if run_log is None and sidecar_id:
        run_log = get_log(sidecar_id)

    if run_log:
        summary = run_log.get_summary()
        return (
            summary.get("last_message", ""),
            summary.get("files_touched", []),
        )
    return agent.stdout_buffer[-4000:] if agent.stdout_buffer else "", []


# ---------------------------------------------------------------------------
# Tool handler
# ---------------------------------------------------------------------------

async def tool_review_fix_loop(args: dict[str, Any]) -> dict[str, Any]:
    """Run a review → fix cycle on a completed agent's work.

    Args:
        coder_agent_id: Agent whose work to review (must be completed).
        cwd: Working directory (required).
        task: Original task description (for context).
        reviewer_type: Agent type for reviewer (default "codex").
        fixer_type: Agent type for fixer (default "codex").
        model: Optional model override for reviewer and fixer.
        max_rounds: Max review→fix iterations (default 2, max 5).
        review_focus: Extra guidance for the reviewer (optional).
        timeout: Per-agent timeout in seconds (default 300).
    """
    coder_agent_id = args.get("coder_agent_id", "")
    cwd = args.get("cwd", "")
    task = args.get("task", "")
    reviewer_type = args.get("reviewer_type", "codex")
    fixer_type = args.get("fixer_type", "codex")
    model = args.get("model")
    max_rounds = min(args.get("max_rounds", 2), 5)
    review_focus = args.get("review_focus", "")
    timeout = args.get("timeout", 300.0)

    if not cwd:
        return classified_error(
            "cwd is required for review_fix_loop",
            code="missing_cwd", category=VALIDATION_ERROR,
        )

    # Normalize and validate path — same chain as create_agent
    cwd = os.path.realpath(os.path.expanduser(cwd))
    if not os.path.isdir(cwd):
        return bad_directory(cwd)

    from .policy import load_policy
    policy = load_policy()
    policy_failures = policy.preflight_spawn(cwd, reviewer_type)
    if policy_failures:
        fail = policy_failures[0]
        return policy_denied("cwd", cwd, fail.reason,
                             policy_rule=fail.policy_rule, suggestion=fail.suggestion)

    # Get coder output
    if coder_agent_id:
        agent = AGENTS.get(coder_agent_id)
        if not agent:
            return agent_not_found(coder_agent_id)
        coder_output, files_touched = _get_agent_output(coder_agent_id)
        coder_title = agent.title
    else:
        return classified_error(
            "coder_agent_id is required",
            code="missing_coder", category=VALIDATION_ERROR,
        )

    if not coder_output:
        coder_output = "(no output captured from coder agent)"

    rounds: list[dict[str, Any]] = []
    current_output = coder_output
    current_files = files_touched
    last_fixer_id: str | None = None

    for round_num in range(1, max_rounds + 1):
        _log(f"review_loop: round {round_num}/{max_rounds} for {coder_title}")

        # -- Spawn reviewer --
        focus_section = f"## Review focus\n{review_focus}" if review_focus else ""
        reviewer_prompt = _REVIEWER_PROMPT.format(
            coder_title=coder_title,
            cwd=cwd,
            task=task or "(not specified)",
            coder_output=current_output[:6000],
            files_touched=", ".join(current_files) if current_files else "(unknown)",
            review_focus=focus_section,
        )

        reviewer_agent, reviewer_output = await _spawn_and_wait(
            reviewer_type,
            f"reviewer-round{round_num}",
            cwd,
            compose_agent_prompt("reviewer", "reviewer", "", [], reviewer_prompt),
            model=model,
            timeout=timeout,
        )

        verdict = parse_verdict(reviewer_output)
        _log(f"review_loop: round {round_num} verdict={verdict}")

        round_info: dict[str, Any] = {
            "round": round_num,
            "reviewer_agent_id": reviewer_agent.id,
            "reviewer_status": reviewer_agent.status,
            "verdict": verdict,
            "reviewer_feedback": reviewer_output[:4000],
        }

        if verdict == "approved":
            round_info["action"] = "accepted"
            rounds.append(round_info)
            break

        if verdict == "blocked":
            round_info["action"] = "halted"
            rounds.append(round_info)
            break

        if round_num >= max_rounds:
            round_info["action"] = "max_rounds_reached"
            rounds.append(round_info)
            break

        # -- Spawn fixer --
        fixer_prompt = _FIXER_PROMPT.format(
            task=task or "(not specified)",
            round_num=round_num,
            reviewer_feedback=reviewer_output[:6000],
            files_touched=", ".join(current_files) if current_files else "(unknown)",
        )

        fixer_agent, fixer_output = await _spawn_and_wait(
            fixer_type,
            f"fixer-round{round_num}",
            cwd,
            compose_agent_prompt("fixer", "coder", "", [], fixer_prompt),
            model=model,
            timeout=timeout,
        )

        round_info["fixer_agent_id"] = fixer_agent.id
        round_info["fixer_status"] = fixer_agent.status
        round_info["action"] = "fix_applied"
        rounds.append(round_info)

        # Update state for next round
        fixer_output_text, fixer_files = _get_agent_output(fixer_agent.id)
        current_output = fixer_output_text or fixer_output
        current_files = fixer_files or current_files
        last_fixer_id = fixer_agent.id
        coder_title = fixer_agent.title

    # Build result
    final_verdict = rounds[-1]["verdict"] if rounds else "no_rounds"
    final_action = rounds[-1].get("action", "unknown") if rounds else "unknown"

    result: dict[str, Any] = {
        "coder_agent_id": coder_agent_id,
        "total_rounds": len(rounds),
        "final_verdict": final_verdict,
        "final_action": final_action,
        "rounds": rounds,
    }
    if last_fixer_id:
        result["last_fixer_agent_id"] = last_fixer_id

    _log(f"review_loop: complete — {len(rounds)} rounds, verdict={final_verdict}")
    return result
