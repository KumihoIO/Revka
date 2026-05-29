"""Map-Reduce Pattern — fan-out task to N parallel agents, aggregate results.

Split a task into N subtasks, spawn N mapper agents in parallel (respecting
concurrency limits), then spawn a reducer agent to synthesize all results.

Usage:
    map_reduce(task="Review security of all API endpoints",
               splits=["src/api/auth.ts", "src/api/users.ts", "src/api/orders.ts"],
               mapper="claude", reducer="claude", cwd="/project")
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from .._log import _log
from ..agent_state import POOL, normalize_agent_type, valid_agent_type
from ..agent_subprocess import compose_agent_prompt
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

_MAPPER_PROMPT = """\
You are a mapper agent processing one segment of a larger task.

## Overall task
{task}

## Your segment ({segment_index}/{total_segments})
{segment}

## Instructions
- Focus only on your segment.
- Be thorough and specific.
- Produce structured output that can be aggregated with other segments.
- Start with a brief summary of findings, then details.
"""

_REDUCER_PROMPT = """\
You are a reducer agent synthesizing results from {count} parallel workers.

## Overall task
{task}

## Results from all mapper agents
{mapper_results}

## Instructions
- Synthesize all results into a coherent summary.
- Identify common themes, conflicts, and gaps.
- Prioritize findings by severity/importance.
- Produce a single, actionable output that combines all mapper work.
"""


# ---------------------------------------------------------------------------
# Tool handler
# ---------------------------------------------------------------------------

def _resolve_template(agent_hint: str):
    """Return a pool template by name, case-insensitive."""
    for tmpl in POOL.list_all():
        if tmpl.name.lower() == agent_hint.lower():
            return tmpl
    return None


def _template_identity(tmpl) -> str:
    """Build prompt identity text from an agent pool template."""
    return "\n".join(
        part for part in (
            tmpl.description,
            tmpl.identity,
            tmpl.soul,
            f"Capabilities: {', '.join(tmpl.capabilities)}" if tmpl.capabilities else "",
        ) if part
    )


async def tool_map_reduce(args: dict[str, Any]) -> dict[str, Any]:
    """Fan out a task to N parallel agents, then aggregate results.

    Args:
        task: Overall task description (required).
        splits: List of segments to process in parallel (required, min 2).
            Each split is a string describing one segment (e.g. a file path,
            a section of text, a subtask description).
        mapper: Agent type or pool template name for mapper agents (default "claude").
        reducer: Agent type or pool template name for reducer agent (default "claude").
        cwd: Working directory (required).
        concurrency: Max simultaneous mapper agents (default 3, max 10).
        model: Optional model override.
        timeout: Per-agent timeout (default 300s).
        halt_on_failure: Stop all mappers if one fails (default False).
    """
    task = args.get("task", "")
    splits = args.get("splits", [])
    mapper_type = args.get("mapper", "claude")
    reducer_type = args.get("reducer", "claude")
    cwd = args.get("cwd", "")
    concurrency = min(args.get("concurrency", 3), 10)
    requested_model = args.get("model")
    timeout = args.get("timeout", 300.0)
    halt_on_failure = args.get("halt_on_failure", False)

    if not task:
        return classified_error("task is required", code="missing_task", category=VALIDATION_ERROR)
    if not splits or len(splits) < 2:
        return classified_error(
            "At least 2 splits required for map-reduce",
            code="insufficient_splits", category=VALIDATION_ERROR,
        )
    if not cwd:
        return classified_error("cwd is required", code="missing_cwd", category=VALIDATION_ERROR)

    cwd = os.path.realpath(os.path.expanduser(cwd))
    if not os.path.isdir(cwd):
        return bad_directory(cwd)

    from ..policy import load_policy
    policy = load_policy()
    mapper_template = _resolve_template(mapper_type)
    reducer_template = _resolve_template(reducer_type)
    effective_mapper = (
        mapper_template.agent_type if mapper_template
        else normalize_agent_type(str(mapper_type)) if valid_agent_type(str(mapper_type))
        else "claude"
    )
    effective_reducer = (
        reducer_template.agent_type if reducer_template
        else normalize_agent_type(str(reducer_type)) if valid_agent_type(str(reducer_type))
        else "claude"
    )
    mapper_name = mapper_template.name if mapper_template else "mapper"
    mapper_role = mapper_template.role if mapper_template else "researcher"
    mapper_identity = _template_identity(mapper_template) if mapper_template else ""
    mapper_model = requested_model or (mapper_template.model if mapper_template else None)
    reducer_name = reducer_template.name if reducer_template else "reducer"
    reducer_role = reducer_template.role if reducer_template else "researcher"
    reducer_identity = _template_identity(reducer_template) if reducer_template else ""
    reducer_model = requested_model or (reducer_template.model if reducer_template else None)

    policy_failures = policy.preflight_spawn(cwd, effective_mapper)
    if policy_failures:
        fail = policy_failures[0]
        return policy_denied("cwd", cwd, fail.reason,
                             policy_rule=fail.policy_rule, suggestion=fail.suggestion)
    policy_failures = policy.preflight_spawn(cwd, effective_reducer)
    if policy_failures:
        fail = policy_failures[0]
        return policy_denied("cwd", cwd, fail.reason,
                             policy_rule=fail.policy_rule, suggestion=fail.suggestion)

    total = len(splits)
    _log(f"map_reduce: {total} splits, concurrency={concurrency}, mapper={mapper_type}")

    # -- Map phase: run mappers with concurrency limit --
    semaphore = asyncio.Semaphore(concurrency)
    mapper_results: list[dict[str, Any]] = [None] * total  # type: ignore
    halt_event = asyncio.Event()

    async def run_mapper(idx: int, segment: str) -> None:
        if halt_event.is_set():
            mapper_results[idx] = {
                "index": idx,
                "segment": segment[:200],
                "status": "skipped",
                "output": "",
                "files": [],
            }
            return

        async with semaphore:
            if halt_event.is_set():
                mapper_results[idx] = {
                    "index": idx, "segment": segment[:200],
                    "status": "skipped", "output": "", "files": [],
                }
                return

            prompt = _MAPPER_PROMPT.format(
                task=task,
                segment_index=idx + 1,
                total_segments=total,
                segment=segment,
            )
            agent, output = await _spawn_and_wait(
                effective_mapper,
                f"mapper-{idx+1}-of-{total}",
                cwd,
                compose_agent_prompt(f"{mapper_name}-{idx+1}", mapper_role, mapper_identity, [], prompt),
                model=mapper_model,
                timeout=timeout,
            )
            agent_output, agent_files = _get_agent_output(agent.id)
            effective_output = agent_output or output

            result_entry = {
                "index": idx,
                "segment": segment[:200],
                "agent_id": agent.id,
                "status": agent.status,
                "output": effective_output[:4000],
                "files": agent_files,
            }
            mapper_results[idx] = result_entry

            if agent.status == "error" and halt_on_failure:
                _log(f"map_reduce: mapper {idx+1} failed, halting remaining")
                halt_event.set()

    # Launch all mappers
    tasks = [asyncio.create_task(run_mapper(i, s)) for i, s in enumerate(splits)]
    await asyncio.gather(*tasks, return_exceptions=True)

    # Filter results
    successful = [r for r in mapper_results if r and r.get("status") != "error"]
    failed = [r for r in mapper_results if r and r.get("status") == "error"]
    skipped = [r for r in mapper_results if r and r.get("status") == "skipped"]

    _log(f"map_reduce: map phase done — {len(successful)} ok, {len(failed)} failed, {len(skipped)} skipped")

    # -- Reduce phase: synthesize all results --
    if not successful:
        return {
            "task": task,
            "status": "all_mappers_failed",
            "total_splits": total,
            "mapper_results": mapper_results,
            "reducer_output": None,
        }

    # Build reducer input
    results_text = "\n\n".join(
        f"### Segment {r['index']+1}: {r['segment'][:100]}\n{r['output'][:3000]}"
        for r in successful
    )

    reducer_prompt = _REDUCER_PROMPT.format(
        task=task,
        count=len(successful),
        mapper_results=results_text[:10000],
    )

    reducer_agent, reducer_output = await _spawn_and_wait(
        effective_reducer,
        "reducer",
        cwd,
        compose_agent_prompt(reducer_name, reducer_role, reducer_identity, [], reducer_prompt),
        model=reducer_model,
        timeout=timeout,
    )
    reducer_text, reducer_files = _get_agent_output(reducer_agent.id)

    result: dict[str, Any] = {
        "task": task,
        "status": "completed",
        "total_splits": total,
        "successful_mappers": len(successful),
        "failed_mappers": len(failed),
        "skipped_mappers": len(skipped),
        "mapper_results": mapper_results,
        "reducer": {
            "agent_id": reducer_agent.id,
            "status": reducer_agent.status,
            "output": (reducer_text or reducer_output)[:6000],
            "files": reducer_files,
        },
    }

    _log(f"map_reduce: complete — {len(successful)}/{total} mappers, reducer={reducer_agent.status}")
    return result
