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
from ..run_log import get_log


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


async def _cancel_timed_out_agent(agent: ManagedAgent) -> None:
    """Cancel a timed-out agent to stop it from burning tokens."""
    try:
        from ..tool_handlers.agents import _cancel_one
        await _cancel_one(agent)
    except Exception as exc:
        _log(f"refinement: failed to cancel agent {agent.id[:8]}: {exc}")


async def _wait_for_agent(agent: ManagedAgent, *, timeout: float = 300.0) -> str:
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
    _RUNLOG_DIR = os.path.expanduser("~/.construct/operator_mcp/runlogs")

    zombie_window = max(_ZOMBIE_MIN, timeout * _ZOMBIE_RATIO)

    sidecar_id = getattr(agent, "_sidecar_id", None)

    if sidecar_id:
        from ..tool_handlers.agents import _sidecar_client
        if _sidecar_client:
            loop_time = asyncio.get_event_loop().time
            deadline = loop_time() + timeout
            poll_interval = 1.0

            # Zombie-detection state
            last_event_count = -1          # -1 = not yet checked
            last_progress_time = loop_time()
            next_liveness_check = loop_time() + _LIVENESS_CHECK_INTERVAL
            consecutive_empty = 0          # track repeated 0-event responses
            last_runlog_size = -1          # cross-verification via local file

            while loop_time() < deadline:
                if agent.status in ("completed", "error", "closed"):
                    break
                try:
                    info = await _sidecar_client.get_agent(sidecar_id)
                    if info is None:
                        _log(f"refinement: agent {agent.id[:8]} vanished from sidecar")
                        agent.status = "error"
                        return "[AGENT VANISHED]"

                    status = info.get("status", "")
                    if status in ("idle", "error", "closed"):
                        agent.status = "completed" if status == "idle" else status
                        break

                    # Periodic liveness probe for running agents
                    now = loop_time()
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
                                        agent.status = "error"
                                        await _cancel_timed_out_agent(agent)
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
                                    agent.status = "error"
                                    await _cancel_timed_out_agent(agent)
                                    return f"[ZOMBIE — no progress for {stale:.0f}s]"

                            # Track runlog size for next comparison
                            last_runlog_size = _get_runlog_size(sidecar_id, _RUNLOG_DIR)
                        except Exception as exc:
                            _log(f"refinement: agent {agent.id[:8]} liveness check error: {exc}")
                except Exception:
                    pass
                remaining = deadline - loop_time()
                await asyncio.sleep(min(poll_interval, max(0.1, remaining)))
                poll_interval = min(poll_interval * 1.2, 5.0)
            else:
                _log(f"refinement: agent {agent.id[:8]} timed out ({timeout}s), cancelling")
                await _cancel_timed_out_agent(agent)
                return f"[TIMEOUT after {timeout}s]"
    elif agent._reader_task:
        try:
            await asyncio.wait_for(agent._reader_task, timeout=timeout)
        except asyncio.TimeoutError:
            _log(f"refinement: agent {agent.id[:8]} timed out ({timeout}s), cancelling")
            await _cancel_timed_out_agent(agent)
            return f"[TIMEOUT after {timeout}s]"

    run_log = get_log(agent.id)
    if run_log is None and sidecar_id:
        run_log = get_log(sidecar_id)
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
    env_extra: dict[str, str] | None = None,
) -> tuple[ManagedAgent, str]:
    """Spawn an agent, wait for completion, return (agent, output_text).

    ``env_extra`` is forwarded to the agent subprocess (CLI mode) and to
    the sidecar create_agent config (sidecar mode). Used by workflow
    auth-profile bindings to expose CONSTRUCT_AUTH_PROFILE_ID without
    injecting it into the system prompt.
    """
    from ..tool_handlers.agents import _try_sidecar_create, _event_consumer

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
    use_cli = not include_memory and not include_operator

    sidecar_info = None
    if not use_cli:
        sidecar_info = await _try_sidecar_create(
            agent_id, agent_type, title, cwd, prompt, model=model,
            max_turns=max_turns,
            include_memory=include_memory,
            include_operator=include_operator,
            env_extra=env_extra,
        )
    if sidecar_info:
        agent.status = "running"
        agent._sidecar_id = sidecar_info.get("id", "")
        if _event_consumer and agent._sidecar_id:
            _event_consumer._agent_titles[agent._sidecar_id] = title
            if model:
                _event_consumer.set_agent_model(agent._sidecar_id, model)
            await _event_consumer.subscribe(agent._sidecar_id, title, model=model or "")
    else:
        from ..operator_mcp import JOURNAL
        try:
            await spawn_agent(agent, prompt, JOURNAL, model=model, env_extra=env_extra)
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
