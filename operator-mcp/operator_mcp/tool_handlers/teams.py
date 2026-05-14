"""Team tool handlers: list, get, spawn, create, search."""
from __future__ import annotations

import asyncio
import json as _json
import os
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from .._log import _log
from ..construct_config import harness_project
from ..agent_state import AGENTS, ManagedAgent
from ..agent_subprocess import compose_agent_prompt, spawn_with_retry, _TEAM_SPAWN_STAGGER_SECS
from ..budget_authority import BudgetGateError
from ..failure_classification import (
    team_not_found, bad_directory, missing_cwd, graph_invalid, spawn_failed,
    upstream_stage_failed, classified_error, policy_denied,
    VALIDATION_ERROR, RUNTIME_ENV_ERROR,
)
from .agents import _try_sidecar_create, _event_consumer
from ..journal import SessionJournal
from ..kumiho_clients import KumihoTeamClient, KumihoAgentPoolClient, resolve_agent_krefs, _get_sdk
from ..spawn_tracker import get_or_create_tracker, get_tracker, list_trackers
from ..team_validation import validate_team_edges, lint_team


# ---------------------------------------------------------------------------
# Role-aware task decomposition
# ---------------------------------------------------------------------------

# Maps role names to task decomposition templates.
# {task} is replaced with the original user task.
_ROLE_TASK_TEMPLATES: dict[str, str] = {
    "coder": (
        "IMPLEMENT the following task. Write the code changes directly.\n\n"
        "Task: {task}\n\n"
        "Focus on correct, clean implementation. Run tests if available."
    ),
    "reviewer": (
        "REVIEW the code changes made for the following task. Do NOT implement — only review.\n\n"
        "Task: {task}\n\n"
        "Check for: correctness, edge cases, security issues, style consistency, "
        "and test coverage. Report findings as actionable feedback."
    ),
    "researcher": (
        "RESEARCH and DOCUMENT the following task. Do NOT implement code changes.\n\n"
        "Task: {task}\n\n"
        "Investigate the codebase, understand the current state, identify relevant files "
        "and patterns, and produce a clear summary of findings and recommendations."
    ),
    "tester": (
        "WRITE TESTS for the following task. Do NOT implement the feature — only write tests.\n\n"
        "Task: {task}\n\n"
        "Write comprehensive test cases covering happy paths, edge cases, and error conditions."
    ),
    "architect": (
        "DESIGN the architecture for the following task. Do NOT implement — produce a design document.\n\n"
        "Task: {task}\n\n"
        "Identify affected components, propose the approach, and note trade-offs."
    ),
    "planner": (
        "PLAN the execution strategy for the following task. Do NOT implement — produce a structured plan.\n\n"
        "Task: {task}\n\n"
        "Decompose into discrete subtasks with clear acceptance criteria, dependencies, "
        "and suggested agent roles. Output a plan that a operator can execute mechanically."
    ),
}

# Fallback priority when no edges are defined on the team.
_ROLE_PRIORITY: dict[str, int] = {
    "planner": 0,
    "architect": 0,
    "researcher": 0,
    "coder": 1,
    "tester": 2,
    "reviewer": 2,
}


def _decompose_task_for_role(role: str, task: str, team_context: list[dict[str, str]]) -> str:
    """Generate a role-specific subtask from the original task.

    Args:
        role: The member's role (coder, reviewer, researcher, etc.)
        task: The original user task.
        team_context: List of dicts with 'name' and 'role' for all team members,
                      so each agent knows who else is on the team.
    """
    template = _ROLE_TASK_TEMPLATES.get(role, _ROLE_TASK_TEMPLATES.get("coder", "{task}"))
    subtask = template.format(task=task)

    # Add team awareness so agents know who else is working
    if len(team_context) > 1:
        others = [f"  - {m['name']} ({m['role']})" for m in team_context]
        subtask += "\n\n## Team Members\nYou are part of a team:\n" + "\n".join(others)
        subtask += (
            "\n\nStay in your lane — focus on YOUR role's responsibility. "
            "Do not duplicate work that belongs to another role."
        )

    return subtask


# ---------------------------------------------------------------------------
# Edge-driven topological sort
# ---------------------------------------------------------------------------

def _toposort_members(
    members: list[dict[str, Any]],
    edges: list[dict[str, str]],
) -> list[list[int]]:
    """Topologically sort members into execution waves using Kumiho edges.

    Each edge has {from_kref, to_kref, edge_type}.  A DEPENDS_ON edge from A→B
    means A depends on B, so B must run first.  A SUPPORTS edge from A→B means
    A supports B, so A should run first (B depends on A's output).

    Returns a list of waves — each wave is a list of member indices that can
    run in parallel.  Waves are ordered so dependencies are satisfied.

    Falls back to role-priority tiers if no edges exist.
    """
    n = len(members)
    kref_to_idx: dict[str, int] = {}
    for i, m in enumerate(members):
        kref = m.get("kref", "")
        if kref:
            kref_to_idx[kref] = i

    # Build adjacency: dep_graph[i] = set of indices that i depends on (must run before i)
    dep_graph: dict[int, set[int]] = defaultdict(set)
    has_edges = False

    for edge in edges:
        from_kref = edge.get("from_kref", "")
        to_kref = edge.get("to_kref", "")
        edge_type = edge.get("edge_type", "").upper()
        from_idx = kref_to_idx.get(from_kref)
        to_idx = kref_to_idx.get(to_kref)

        if from_idx is None or to_idx is None:
            continue

        has_edges = True

        if edge_type == "DEPENDS_ON":
            # from depends on to → to must run first
            dep_graph[from_idx].add(to_idx)
        elif edge_type in ("SUPPORTS", "FEEDS_INTO"):
            # from supports to → from must run first → to depends on from
            dep_graph[to_idx].add(from_idx)
        # REPORTS_TO and other non-execution edges are ignored for ordering

    if not has_edges:
        # Fallback: role-priority tiers
        tiered: dict[int, list[int]] = defaultdict(list)
        for i, m in enumerate(members):
            role = m.get("role", "coder")
            tier = _ROLE_PRIORITY.get(role, 1)
            tiered[tier].append(i)
        return [tiered[t] for t in sorted(tiered.keys())]

    # Kahn's algorithm for topological sort into waves
    in_degree = [0] * n
    for node, deps in dep_graph.items():
        in_degree[node] = len(deps)

    # Start with nodes that have no dependencies
    queue = deque(i for i in range(n) if in_degree[i] == 0)
    waves: list[list[int]] = []

    visited = 0
    while queue:
        wave = list(queue)
        waves.append(wave)
        next_queue: deque[int] = deque()
        for node in wave:
            visited += 1
            # For each node that depends on this one, decrement in-degree
            for dependent in range(n):
                if node in dep_graph.get(dependent, set()):
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        next_queue.append(dependent)
        queue = next_queue

    # If we didn't visit all nodes, there's a cycle — hard fail
    if visited < n:
        remaining = [i for i in range(n) if in_degree[i] > 0]
        if remaining:
            cycle_names = [members[i].get("name", f"member-{i}") for i in remaining]
            raise ValueError(
                f"Dependency cycle detected among: {', '.join(cycle_names)}. "
                f"Cannot build execution waves with cyclic edges."
            )

    return waves


def _build_relationship_context(
    member_idx: int,
    members: list[dict[str, Any]],
    edges: list[dict[str, str]],
    spawned_map: dict[str, dict[str, str]],
) -> str:
    """Build a ## Relationships section for a member based on Kumiho edges.

    spawned_map: kref -> {"agent_id": ..., "name": ..., "role": ...} for already-spawned agents.
    """
    kref = members[member_idx].get("kref", "")
    if not kref or not edges:
        return ""

    upstream: list[str] = []
    downstream: list[str] = []

    for edge in edges:
        from_kref = edge.get("from_kref", "")
        to_kref = edge.get("to_kref", "")
        edge_type = edge.get("edge_type", "").upper()

        if edge_type == "DEPENDS_ON":
            if from_kref == kref and to_kref in spawned_map:
                info = spawned_map[to_kref]
                upstream.append(
                    f"  - **{info['name']}** ({info['role']}) — agent_id: `{info['agent_id']}`"
                )
            elif to_kref == kref and from_kref in spawned_map:
                info = spawned_map[from_kref]
                downstream.append(
                    f"  - **{info['name']}** ({info['role']}) — agent_id: `{info['agent_id']}`"
                )
        elif edge_type in ("SUPPORTS", "FEEDS_INTO"):
            if from_kref == kref and to_kref in spawned_map:
                info = spawned_map[to_kref]
                downstream.append(
                    f"  - **{info['name']}** ({info['role']}) — agent_id: `{info['agent_id']}`"
                )
            elif to_kref == kref and from_kref in spawned_map:
                info = spawned_map[from_kref]
                upstream.append(
                    f"  - **{info['name']}** ({info['role']}) — agent_id: `{info['agent_id']}`"
                )

    if not upstream and not downstream:
        return ""

    parts = ["\n## Relationships"]
    if upstream:
        parts.append(
            "**Upstream (your inputs)** — read their output before starting. "
            "Use `get_agent_activity(agent_id)` to retrieve their work.\n"
            + "\n".join(upstream)
        )
    if downstream:
        parts.append(
            "**Downstream (your consumers)** — your output feeds into their work. "
            "Be explicit and structured so they can use it directly.\n"
            + "\n".join(downstream)
        )
    return "\n".join(parts)


def _collect_upstream_output(
    member_idx: int,
    members: list[dict[str, Any]],
    edges: list[dict[str, str]],
    spawned_map: dict[str, dict[str, str]],
) -> str:
    """Collect and format stdout from upstream agents for injection into prompt."""
    kref = members[member_idx].get("kref", "")
    if not kref or not edges:
        return ""

    upstream_krefs: set[str] = set()
    for edge in edges:
        from_kref = edge.get("from_kref", "")
        to_kref = edge.get("to_kref", "")
        edge_type = edge.get("edge_type", "").upper()

        if edge_type == "DEPENDS_ON" and from_kref == kref:
            upstream_krefs.add(to_kref)
        elif edge_type in ("SUPPORTS", "FEEDS_INTO") and to_kref == kref:
            upstream_krefs.add(from_kref)

    if not upstream_krefs:
        return ""

    sections: list[str] = []
    for uk in upstream_krefs:
        info = spawned_map.get(uk)
        if not info:
            continue
        agent = AGENTS.get(info["agent_id"])
        if not agent or not agent.stdout_buffer.strip():
            continue
        # Truncate to last 3000 chars to avoid blowing up the prompt
        output = agent.stdout_buffer.strip()[-3000:]
        sections.append(
            f"### Output from {info['name']} ({info['role']})\n"
            f"```\n{output}\n```"
        )

    if not sections:
        return ""

    return "\n## Upstream Output\n" + "\n\n".join(sections)


async def _wait_for_single_agent(
    aid: str, *, timeout: float, sidecar_client: Any,
) -> tuple[str, str]:
    """Wait for one agent to complete. Returns (agent_id, outcome).

    outcome is one of: "completed", "error", "timeout", "unreachable", "missing"
    """
    a = AGENTS.get(aid)
    if not a:
        _log(f"_wait_for_wave: {aid[:8]} not in AGENTS (missing)")
        return aid, "missing"

    sidecar_id = getattr(a, "_sidecar_id", None)

    if sidecar_id and sidecar_client:
        # Poll sidecar for completion with failure tracking
        deadline = asyncio.get_event_loop().time() + timeout
        poll_interval = 1.0
        consecutive_failures = 0
        remaining = timeout
        while remaining > 0:
            if a.status in ("completed", "error", "closed"):
                break
            try:
                info = await sidecar_client.get_agent(sidecar_id)
            except Exception as e:
                info = None
                consecutive_failures += 1
                _log(f"_wait_for_wave: sidecar poll error for {aid[:8]} "
                     f"(attempt {consecutive_failures}): {e}")

            if info is None:
                consecutive_failures += 1
                if consecutive_failures >= 8:
                    _log(f"_wait_for_wave: {aid[:8]} backend unreachable "
                         f"after {consecutive_failures} failures")
                    return aid, "unreachable"
            else:
                consecutive_failures = 0
                status = info.get("status", "")
                if status in ("idle", "error", "closed"):
                    a.status = "completed" if status == "idle" else status
                    break

            await asyncio.sleep(min(poll_interval, remaining))
            remaining = max(0, deadline - asyncio.get_event_loop().time())
            poll_interval = min(poll_interval * 1.2, 5.0)
        else:
            _log(f"_wait_for_wave: {aid[:8]} timed out ({timeout}s)")
            return aid, "timeout"

        return aid, "error" if a.status in ("error", "closed") else "completed"

    elif a._reader_task:
        # Subprocess: wait on reader task
        try:
            await asyncio.wait_for(a._reader_task, timeout=timeout)
        except asyncio.TimeoutError:
            _log(f"_wait_for_wave: {aid[:8]} subprocess timed out ({timeout:.1f}s)")
            return aid, "timeout"
        except asyncio.CancelledError:
            _log(f"_wait_for_wave: {aid[:8]} reader task cancelled")
            a.status = "error"
            return aid, "error"
        except Exception as e:
            _log(f"_wait_for_wave: {aid[:8]} reader task exception: {e}")
            a.status = "error"
            return aid, "error"

        return aid, "error" if a.status in ("error", "closed") else "completed"

    else:
        # No sidecar, no reader task — check current status
        if a.status in ("completed", "error", "closed", "idle"):
            return aid, "error" if a.status in ("error", "closed") else "completed"
        _log(f"_wait_for_wave: {aid[:8]} running but no reader_task or sidecar")
        return aid, "error"


async def _wait_for_wave_agents(
    agent_ids: list[str], *, timeout: float = 300.0,
) -> dict[str, str]:
    """Wait for all agents in a wave to complete, in parallel.

    Each agent gets the full timeout independently — no starvation from
    slow siblings.  Uses asyncio.gather so a hung agent doesn't block
    checking others.

    Returns a dict mapping agent_id -> outcome string:
      "completed", "error", "timeout", "unreachable", "missing"
    Callers should inspect this to decide whether downstream waves proceed.
    """
    from .agents import _sidecar_client

    if not agent_ids:
        return {}

    tasks = [
        _wait_for_single_agent(aid, timeout=timeout, sidecar_client=_sidecar_client)
        for aid in agent_ids
    ]

    pairs = await asyncio.gather(*tasks, return_exceptions=True)

    results: dict[str, str] = {}
    for i, pair in enumerate(pairs):
        aid = agent_ids[i]
        if isinstance(pair, Exception):
            _log(f"_wait_for_wave: {aid[:8]} gather exception: {pair}")
            results[aid] = "error"
        else:
            results[pair[0]] = pair[1]

    return results


@dataclass
class AgentOutcome:
    """Structured outcome from a completed agent — the handoff unit."""
    agent_id: str
    title: str
    role: str
    status: str
    revision_kref: str
    summary: str
    files: list[str]
    tool_call_count: int
    error_count: int
    errors: list[str]
    diff_summary: str = ""


def _relativize(path: str, cwd: str) -> str:
    """Convert absolute path to relative from cwd for readable artifact names."""
    if cwd and path.startswith(cwd):
        rel = path[len(cwd):].lstrip("/")
        return rel if rel else path
    return path


# ---------------------------------------------------------------------------
# Wave checkpoint persistence — save/load state between waves so spawn_team
# can resume from a failed wave instead of re-running everything.
# ---------------------------------------------------------------------------

_CHECKPOINT_DIR = os.path.expanduser("~/.construct/operator_mcp/checkpoints")


@dataclass
class WaveCheckpoint:
    """Serializable snapshot of spawn_team state after a wave completes."""
    team_kref: str
    team_name: str
    task: str
    cwd: str
    halt_on_failure: bool
    completed_wave: int
    total_waves: int
    spawned: list[dict[str, Any]]
    failed: list[dict[str, Any]]
    spawned_map: dict[str, dict[str, str]]
    outcomes: dict[str, dict[str, Any]]  # serialized AgentOutcome
    checkpoint_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WaveCheckpoint":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def _outcome_to_dict(o: AgentOutcome) -> dict[str, Any]:
    """Serialize AgentOutcome for checkpoint."""
    return {
        "agent_id": o.agent_id, "title": o.title, "role": o.role,
        "status": o.status, "revision_kref": o.revision_kref,
        "summary": o.summary, "files": o.files,
        "tool_call_count": o.tool_call_count, "error_count": o.error_count,
        "errors": o.errors, "diff_summary": o.diff_summary,
    }


def _outcome_from_dict(d: dict[str, Any]) -> AgentOutcome:
    """Deserialize AgentOutcome from checkpoint."""
    return AgentOutcome(**{k: v for k, v in d.items() if k in AgentOutcome.__dataclass_fields__})


def _save_wave_checkpoint(
    checkpoint: WaveCheckpoint,
) -> str:
    """Persist checkpoint to disk. Returns the checkpoint_id."""
    os.makedirs(_CHECKPOINT_DIR, exist_ok=True)
    if not checkpoint.checkpoint_id:
        checkpoint.checkpoint_id = str(uuid.uuid4())[:12]
    path = os.path.join(
        _CHECKPOINT_DIR,
        f"{checkpoint.team_name}_{checkpoint.checkpoint_id}.json",
    )
    with open(path, "w") as f:
        _json.dump(checkpoint.to_dict(), f, default=str)
    _log(f"Checkpoint saved: wave {checkpoint.completed_wave}/{checkpoint.total_waves - 1} -> {path}")
    return checkpoint.checkpoint_id


def _load_wave_checkpoint(checkpoint_id: str) -> WaveCheckpoint | None:
    """Load checkpoint by ID (scans checkpoint dir)."""
    if not os.path.isdir(_CHECKPOINT_DIR):
        return None
    for fname in os.listdir(_CHECKPOINT_DIR):
        if checkpoint_id in fname and fname.endswith(".json"):
            path = os.path.join(_CHECKPOINT_DIR, fname)
            try:
                with open(path) as f:
                    data = _json.load(f)
                return WaveCheckpoint.from_dict(data)
            except Exception as e:
                _log(f"Checkpoint load error ({fname}): {e}")
                return None
    return None


def _load_latest_checkpoint(team_name: str) -> WaveCheckpoint | None:
    """Load the most recent checkpoint for a team."""
    if not os.path.isdir(_CHECKPOINT_DIR):
        return None
    prefix = f"{team_name}_"
    candidates = [
        f for f in os.listdir(_CHECKPOINT_DIR)
        if f.startswith(prefix) and f.endswith(".json")
    ]
    if not candidates:
        return None
    # Sort by mtime descending — most recent first
    candidates.sort(
        key=lambda f: os.path.getmtime(os.path.join(_CHECKPOINT_DIR, f)),
        reverse=True,
    )
    path = os.path.join(_CHECKPOINT_DIR, candidates[0])
    try:
        with open(path) as f:
            data = _json.load(f)
        return WaveCheckpoint.from_dict(data)
    except Exception as e:
        _log(f"Latest checkpoint load error ({candidates[0]}): {e}")
        return None


def _delete_checkpoint(checkpoint_id: str) -> bool:
    """Remove checkpoint file after successful completion."""
    if not os.path.isdir(_CHECKPOINT_DIR):
        return False
    for fname in os.listdir(_CHECKPOINT_DIR):
        if checkpoint_id in fname and fname.endswith(".json"):
            try:
                os.remove(os.path.join(_CHECKPOINT_DIR, fname))
                return True
            except OSError:
                return False
    return False


async def _capture_git_diff(files: list[str], cwd: str) -> str:
    """Capture git diff for specific files. Returns diff text or empty string."""
    if not files or not cwd:
        return ""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "--no-color", "--", *files,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        diff = stdout.decode("utf-8", errors="replace").strip()
        if not diff:
            # Try against HEAD for newly staged files
            proc2 = await asyncio.create_subprocess_exec(
                "git", "diff", "HEAD", "--no-color", "--", *files,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout2, _ = await asyncio.wait_for(proc2.communicate(), timeout=10.0)
            diff = stdout2.decode("utf-8", errors="replace").strip()
        return diff
    except Exception:
        return ""


async def _record_wave_outcomes(
    wave_agent_ids: list[str],
    spawned_map: dict[str, dict[str, str]],
    team_name: str,
    cwd: str = "",
    upstream_rev_krefs: list[str] | None = None,
) -> dict[str, AgentOutcome]:
    """Capture each agent's work as a Kumiho revision with file artifacts.

    For each completed agent:
      1. Extracts structured metadata from RunLog (files, errors, tool calls)
      2. Creates a Kumiho item under Construct/Outcomes
      3. Creates a revision with rich metadata (role, status, summary, counts)
      4. Attaches each file touched as a named artifact (relative path = name)
      5. Creates DERIVED_FROM edges to upstream outcome revisions (provenance chain)

    Returns a mapping of agent_id -> AgentOutcome with the revision kref and
    structured metadata for injection into downstream prompts.
    """
    from ..run_log import get_log

    sdk = _get_sdk()
    if not sdk:
        return {}

    outcomes: dict[str, AgentOutcome] = {}

    try:
        await sdk.ensure_space(harness_project(), "Outcomes")
    except Exception as e:
        _log(f"_record_wave_outcomes: failed to ensure space: {e}")
        return {}

    # Ensure diff output directory exists
    diff_dir = os.path.expanduser("~/.construct/operator_mcp/diffs")
    os.makedirs(diff_dir, exist_ok=True)

    for aid in wave_agent_ids:
        agent = AGENTS.get(aid)
        if not agent:
            continue

        # --- Extract structured data from RunLog ---
        sidecar_id = getattr(agent, "_sidecar_id", None)
        run_log = get_log(aid)
        if run_log is None and sidecar_id:
            run_log = get_log(sidecar_id)

        files: list[str] = []
        summary_text = ""
        tool_call_count = 0
        error_count = 0
        error_messages: list[str] = []

        if run_log:
            log_summary = run_log.get_summary()
            files = log_summary.get("files_touched", [])
            summary_text = log_summary.get("last_message", "")[:800]
            tool_call_count = log_summary.get("tool_call_count", 0)
            error_count = log_summary.get("error_count", 0)
            for err in run_log.get_errors()[-3:]:
                msg = err.get("error", err.get("message", ""))
                if msg:
                    error_messages.append(str(msg)[:200])
        elif agent.stdout_buffer:
            summary_text = agent.stdout_buffer.strip()[-800:]

        role = agent.title.split("-")[0] if "-" in agent.title else "agent"

        # --- Capture git diff for touched files ---
        diff_text = await _capture_git_diff(files, cwd)
        diff_summary = diff_text[:3000] if diff_text else ""

        # Write diff to persistent file for artifact reference
        diff_file_path = ""
        if diff_text:
            diff_file_path = os.path.join(diff_dir, f"{aid}.diff")
            try:
                with open(diff_file_path, "w", encoding="utf-8") as f:
                    f.write(diff_text)
            except Exception as e:
                _log(f"_record_wave_outcomes: failed to write diff file: {e}")
                diff_file_path = ""

        # --- SUPERSEDES: search for existing outcome item with same team+role ---
        item_kref = ""
        prev_published_kref = ""
        try:
            existing = await sdk.search(
                f"{team_name} {role}", context=f"{harness_project()}/Outcomes", kind="outcome",
            )
            for result in existing:
                item_data = result.get("item", {})
                item_meta = item_data.get("metadata", {})
                if item_meta.get("team") == team_name and item_meta.get("role") == role:
                    item_kref = item_data.get("kref", "")
                    if item_kref:
                        # Get the current published revision before we create a new one
                        prev_rev = await sdk.get_latest_revision(item_kref, tag="published")
                        if prev_rev:
                            prev_published_kref = prev_rev.get("kref", "")
                        _log(
                            f"_record_wave_outcomes: SUPERSEDES — reusing existing item "
                            f"{item_kref} (prev published: {prev_published_kref[:40]})"
                        )
                    break
        except Exception as e:
            _log(f"_record_wave_outcomes: SUPERSEDES search failed: {e}")

        # --- Create Kumiho item (or reuse existing) + revision + artifacts ---
        item_name = f"{team_name}-{agent.title}-{aid[:8]}"
        try:
            if not item_kref:
                item = await sdk.create_item(
                    f"/{harness_project()}/Outcomes", item_name, "outcome",
                    metadata={
                        "agent_id": aid,
                        "team": team_name,
                        "role": role,
                        "agent_type": agent.agent_type,
                    },
                )
                item_kref = item.get("kref", "")
                if not item_kref:
                    continue

            # Revision metadata — the structured handoff payload
            rev_metadata: dict[str, str] = {
                "status": agent.status,
                "role": role,
                "team": team_name,
                "tool_calls": str(tool_call_count),
                "errors": str(error_count),
                "files_count": str(len(files)),
                "summary": summary_text[:300],
                "agent_id": aid,
            }
            if error_messages:
                rev_metadata["last_errors"] = " | ".join(error_messages)[:500]
            if diff_text:
                rev_metadata["has_diff"] = "true"

            rev = await sdk.create_revision(
                item_kref, metadata=rev_metadata, tag="published",
            )
            rev_kref = rev.get("kref", "")
            if not rev_kref:
                continue

            # Attach files as named artifacts (relative path as name, absolute as location)
            for fpath in files:
                artifact_name = _relativize(fpath, cwd)
                budget_error = ""
                try:
                    await sdk.create_artifact(rev_kref, artifact_name, fpath)
                except Exception as e:
                    _log(f"_record_wave_outcomes: artifact {artifact_name}: {e}")

            # Attach diff as artifact if we have one
            if diff_file_path:
                try:
                    await sdk.create_artifact(rev_kref, "changes.diff", diff_file_path)
                except Exception as e:
                    _log(f"_record_wave_outcomes: diff artifact: {e}")

            # DERIVED_FROM edges to upstream outcomes (provenance chain)
            if upstream_rev_krefs:
                for upstream_kref in upstream_rev_krefs:
                    try:
                        await sdk.create_edge(rev_kref, upstream_kref, "DERIVED_FROM")
                    except Exception as e:
                        _log(f"_record_wave_outcomes: edge to {upstream_kref[:30]}: {e}")

            # SUPERSEDES edge to previous published revision (re-run provenance)
            if prev_published_kref:
                try:
                    await sdk.create_edge(rev_kref, prev_published_kref, "DERIVED_FROM")
                    _log(
                        f"_record_wave_outcomes: SUPERSEDES edge "
                        f"{rev_kref[:30]} -> {prev_published_kref[:30]}"
                    )
                except Exception as e:
                    _log(f"_record_wave_outcomes: SUPERSEDES edge failed: {e}")

            outcome = AgentOutcome(
                agent_id=aid,
                title=agent.title,
                role=role,
                status=agent.status,
                revision_kref=rev_kref,
                summary=summary_text[:300],
                files=files,
                tool_call_count=tool_call_count,
                error_count=error_count,
                errors=error_messages,
                diff_summary=diff_summary,
            )
            outcomes[aid] = outcome
            _log(
                f"_record_wave_outcomes: {agent.title} -> {rev_kref} "
                f"({len(files)} artifacts, {len(upstream_rev_krefs or [])} edges"
                f"{', diff=' + str(len(diff_text)) + 'b' if diff_text else ''}"
                f"{', supersedes=' + prev_published_kref[:30] if prev_published_kref else ''})"
            )

        except Exception as e:
            _log(f"_record_wave_outcomes: failed for {agent.title}: {e}")

    return outcomes


def _get_upstream_agent_ids(
    member_idx: int,
    members: list[dict[str, Any]],
    edges: list[dict[str, str]],
    spawned_map: dict[str, dict[str, str]],
) -> list[str]:
    """Resolve which agent_ids are upstream of a given member via edges."""
    kref = members[member_idx].get("kref", "")
    if not kref or not edges:
        return []

    upstream_member_krefs: list[str] = []
    for edge in edges:
        from_kref = edge.get("from_kref", "")
        to_kref = edge.get("to_kref", "")
        edge_type = edge.get("edge_type", "").upper()

        if edge_type == "DEPENDS_ON" and from_kref == kref:
            upstream_member_krefs.append(to_kref)
        elif edge_type in ("SUPPORTS", "FEEDS_INTO") and to_kref == kref:
            upstream_member_krefs.append(from_kref)

    agent_ids = []
    for uk in upstream_member_krefs:
        info = spawned_map.get(uk)
        if info:
            agent_ids.append(info["agent_id"])
    return agent_ids


def _build_upstream_handoff(
    member_idx: int,
    members: list[dict[str, Any]],
    edges: list[dict[str, str]],
    spawned_map: dict[str, dict[str, str]],
    all_outcomes: dict[str, AgentOutcome],
    cwd: str = "",
) -> str:
    """Build a structured ## Upstream Deliverables section for downstream agents.

    Instead of dumping raw stdout, this provides:
      - Each upstream agent's outcome kref (resolvable in Kumiho)
      - Status + role + structured summary
      - Explicit file listing with relative paths (directly readable)
      - Error info if the upstream agent hit issues

    This replaces both _collect_upstream_output and _build_upstream_krefs_context
    as the primary inter-agent handoff mechanism.
    """
    upstream_aids = _get_upstream_agent_ids(member_idx, members, edges, spawned_map)
    if not upstream_aids:
        return ""

    sections: list[str] = []
    for aid in upstream_aids:
        outcome = all_outcomes.get(aid)
        if not outcome:
            # Fallback: no outcome recorded, use raw stdout
            agent = AGENTS.get(aid)
            info_parts = [f"  - agent_id: `{aid}`"]
            if agent:
                info_parts.append(f"  - Status: {agent.status}")
                if agent.stdout_buffer.strip():
                    output = agent.stdout_buffer.strip()[-1500:]
                    info_parts.append(f"  - Raw output (truncated):\n    ```\n    {output}\n    ```")
            sections.append(f"### {aid[:8]} (no structured outcome)\n" + "\n".join(info_parts))
            continue

        parts: list[str] = [
            f"### {outcome.title} — {outcome.status}",
            f"- **Outcome kref**: `{outcome.revision_kref}`",
            f"- **Role**: {outcome.role}",
            f"- **Tool calls**: {outcome.tool_call_count}",
        ]

        if outcome.error_count > 0:
            parts.append(f"- **Errors**: {outcome.error_count}")
            if outcome.errors:
                parts.append("- **Error details**: " + "; ".join(outcome.errors[:2]))

        if outcome.summary:
            # Include the agent's final output as structured context
            parts.append(f"- **Summary**:\n  > {outcome.summary[:500]}")

        if outcome.files:
            parts.append(f"- **Files produced** ({len(outcome.files)}):")
            for fpath in outcome.files:
                rel = _relativize(fpath, cwd)
                parts.append(f"  - `{rel}` → `{fpath}`")

        if outcome.diff_summary:
            parts.append(f"- **Changes**:\n  ```diff\n  {outcome.diff_summary[:2000]}\n  ```")

        sections.append("\n".join(parts))

    if not sections:
        return ""

    return (
        "\n## Upstream Deliverables\n"
        "The following agents have completed their stage. Their work is tracked as "
        "Kumiho revisions with file artifacts. Read the listed files directly to "
        "inspect their work. The outcome kref links to the full revision in the "
        "Kumiho graph (artifacts, metadata, provenance edges).\n\n"
        + "\n\n".join(sections)
    )


async def tool_list_teams(team_client: KumihoTeamClient) -> dict[str, Any]:
    teams = await team_client.list_teams()
    team_list = []
    for team in teams:
        team_list.append({
            "kref": team.get("kref", ""),
            "name": team.get("item_name", team.get("name", "unknown")),
            "description": team.get("metadata", {}).get("description", ""),
        })
    return {"teams": team_list, "count": len(team_list)}


async def tool_get_team(args: dict[str, Any], team_client: KumihoTeamClient) -> dict[str, Any]:
    kref = args["kref"]
    team = await team_client.get_team(kref)
    if team is None:
        return team_not_found(kref)
    return {
        "kref": team.get("kref", kref),
        "name": team.get("item_name", team.get("name", "unknown")),
        "description": team.get("metadata", {}).get("description", ""),
        "members": team.get("members", []),
        "edges": team.get("edges", []),
    }


async def tool_spawn_team(args: dict[str, Any], team_client: KumihoTeamClient, journal: SessionJournal) -> dict[str, Any]:
    team_kref = args["team_kref"]
    task = args["task"]
    cwd = args.get("cwd")

    team = await team_client.get_team(team_kref)
    if team is None:
        return team_not_found(team_kref)

    members = team.get("members", [])
    if not members:
        return classified_error("Team has no members.", code="empty_team", category=VALIDATION_ERROR)

    if not cwd:
        return missing_cwd()
    expanded_cwd = os.path.realpath(os.path.expanduser(cwd))
    if not os.path.isdir(expanded_cwd):
        return bad_directory(expanded_cwd)

    # Policy pre-flight — same check as create_agent
    from ..policy import load_policy
    policy = load_policy()
    policy_failures = policy.preflight_spawn(expanded_cwd, "claude")
    if policy_failures:
        fail = policy_failures[0]
        return policy_denied("cwd", expanded_cwd, fail.reason,
                             policy_rule=fail.policy_rule, suggestion=fail.suggestion)

    edges = team.get("edges", [])
    dry_run = args.get("dry_run", False)
    team_name = team.get("item_name", team.get("name", "unknown"))

    # Sanitize edges — strip self-edges and dangling refs before validation
    member_krefs_set = {m.get("kref", "") for m in members if m.get("kref")}
    edges = [
        e for e in edges
        if e.get("from_kref") != e.get("to_kref")
        and e.get("from_kref") in member_krefs_set
        and e.get("to_kref") in member_krefs_set
    ]

    # Validate graph before spawning — mandatory, no bypass
    validation = validate_team_edges(members, edges, include_preview=True)
    if not validation.valid:
        _log(f"spawn_team: graph validation failed: {[e.message for e in validation.errors]}")
        return graph_invalid(validation.to_dict())

    if dry_run:
        return {
            "dry_run": True,
            "team": team_name,
            "members": len(members),
            "edges": len(edges),
            **validation.to_dict(),
        }

    # Build team context for cross-role awareness
    team_context = [{"name": m.get("name", "agent"), "role": m.get("role", "coder")} for m in members]

    # Topologically sort members into execution waves using Kumiho edges
    waves = _toposort_members(members, edges)
    _log(f"Execution waves: {[[members[i].get('name', '?') for i in wave] for wave in waves]}")

    spawned: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    # Maps member kref -> spawned info (for relationship injection into later waves)
    spawned_map: dict[str, dict[str, str]] = {}
    # Maps agent_id -> AgentOutcome (structured handoff for downstream agents)
    all_outcomes: dict[str, AgentOutcome] = {}

    # Resume from checkpoint if requested
    resume_from = args.get("resume_from")
    resume_wave = 0
    if resume_from:
        ckpt = _load_wave_checkpoint(resume_from) if len(resume_from) <= 12 else None
        if ckpt is None:
            ckpt = _load_latest_checkpoint(team_name)
        if ckpt is None:
            return classified_error(
                f"No checkpoint found for resume_from='{resume_from}'",
                code="checkpoint_not_found", category=VALIDATION_ERROR,
            )
        # Restore state from checkpoint
        spawned = ckpt.spawned
        failed = ckpt.failed
        spawned_map = ckpt.spawned_map
        all_outcomes = {
            aid: _outcome_from_dict(od) for aid, od in ckpt.outcomes.items()
        }
        resume_wave = ckpt.completed_wave + 1
        _log(f"spawn_team: resuming from checkpoint {ckpt.checkpoint_id}, wave {resume_wave}")

    # Initialize spawn progress tracker
    tracker = get_or_create_tracker(team_name)
    tracker.init_stages(len(waves))

    halt_on_failure = args.get("halt_on_failure", True)
    halted = False

    checkpoint_id = ""  # populated if we save checkpoints

    try:
        for wave_num, wave in enumerate(waves):
            # Skip waves already completed in a previous run
            if wave_num < resume_wave:
                _log(f"Skipping wave {wave_num} (already completed in checkpoint)")
                tracker.record_stage_start(wave_num, [])
                tracker.record_stage_complete(wave_num)
                continue

            _log(f"Spawning wave {wave_num}: {[members[i].get('name', '?') for i in wave]}")
            wave_agent_ids_for_tracker: list[str] = []

            for stagger_i, member_idx in enumerate(wave):
                member = members[member_idx]
                role = member.get("role", "coder")
                agent_type = member.get("agent_type", "codex")
                name = member.get("name", "agent")
                identity = member.get("identity", "")
                expertise = member.get("expertise", [])
                member_kref = member.get("kref", "")
                model = member.get("model")  # Model from Kumiho agent metadata

                # Role-specific subtask
                subtask = _decompose_task_for_role(role, task, team_context)

                # Inject relationship context from Kumiho edges
                rel_ctx = _build_relationship_context(member_idx, members, edges, spawned_map)
                if rel_ctx:
                    subtask += "\n" + rel_ctx

                # Inject structured upstream deliverables (outcomes + artifacts + metadata)
                handoff = _build_upstream_handoff(
                    member_idx, members, edges, spawned_map, all_outcomes,
                    cwd=expanded_cwd,
                )
                deliverables_text = ""
                if handoff:
                    deliverables_text = handoff
                else:
                    # No structured outcomes yet — fall back to raw stdout injection
                    upstream_output = _collect_upstream_output(
                        member_idx, members, edges, spawned_map,
                    )
                    if upstream_output:
                        deliverables_text = upstream_output

                adapted_prompt = compose_agent_prompt(
                    name, role, identity, expertise, subtask,
                    upstream_deliverables=deliverables_text,
                )
                title = f"{role}-{name}"[:60]

                agent_id = str(uuid.uuid4())
                agent = ManagedAgent(
                    id=agent_id,
                    agent_type=agent_type,
                    title=title,
                    cwd=expanded_cwd,
                    status="idle",
                )
                AGENTS[agent_id] = agent

                if stagger_i > 0:
                    _log(f"Staggering spawn ({_TEAM_SPAWN_STAGGER_SECS}s before next in wave)")
                    await asyncio.sleep(_TEAM_SPAWN_STAGGER_SECS)

                # Try sidecar first (supports model selection), fallback to subprocess
                budget_error = ""
                try:
                    sidecar_info = await _try_sidecar_create(
                        agent_id, agent_type, title, expanded_cwd, adapted_prompt,
                        model=model,
                    )
                except BudgetGateError as exc:
                    agent.status = "error"
                    sidecar_info = None
                    ok = False
                    budget_error = str(exc.response.get("error", exc))
                    _log(f"spawn_team: budget gate blocked {agent_id[:8]}: {budget_error}")
                if sidecar_info:
                    ok = True
                    agent.status = "running"
                    agent._sidecar_id = sidecar_info.get("id", "")
                    if _event_consumer and agent._sidecar_id:
                        await _event_consumer.subscribe(agent._sidecar_id, title)
                elif agent.status != "error":
                    ok = await spawn_with_retry(agent, adapted_prompt, journal)

                info: dict[str, Any] = {
                    "agent_id": agent_id,
                    "name": name,
                    "role": role,
                    "agent_type": agent_type,
                    "title": title,
                    "wave": wave_num,
                }
                if model:
                    info["model"] = model
                if ok:
                    spawned.append(info)
                    wave_agent_ids_for_tracker.append(agent_id)
                    tracker.record_agent_spawned(wave_num, agent_id, title=title)
                    if member_kref:
                        spawned_map[member_kref] = {
                            "agent_id": agent_id,
                            "name": name,
                            "role": role,
                        }
                else:
                    # Rollback: remove from AGENTS since agent never ran
                    AGENTS.pop(agent_id, None)
                    info["error"] = budget_error or (agent.stderr_buffer[-300:] if agent.stderr_buffer else "unknown")
                    failed.append(info)
                    tracker.record_agent_failed(wave_num, agent_id)

            tracker.record_stage_start(wave_num, wave_agent_ids_for_tracker)

            # Check if ALL agents in this wave failed to spawn — halt early
            wave_spawn_failures = [f for f in failed if f.get("wave") == wave_num]
            wave_agent_ids = [s["agent_id"] for s in spawned if s.get("wave") == wave_num]

            if not wave_agent_ids and wave_spawn_failures and halt_on_failure and wave_num < len(waves) - 1:
                fail_names = [f"{f.get('name', '?')}(spawn_failed)" for f in wave_spawn_failures]
                _log(
                    f"spawn_team: HALTING — wave {wave_num} had {len(wave_spawn_failures)} spawn failure(s) "
                    f"({fail_names}), skipping waves {wave_num + 1}..{len(waves) - 1}"
                )
                tracker.record_halt(wave_num, f"Wave {wave_num} spawn failed: {', '.join(fail_names)}")
                for skip_wave in range(wave_num + 1, len(waves)):
                    for member_idx in waves[skip_wave]:
                        m = members[member_idx]
                        err = upstream_stage_failed(wave_num, [f.get("agent_id", "") for f in wave_spawn_failures])
                        err["name"] = m.get("name", "agent")
                        err["role"] = m.get("role", "")
                        err["wave"] = skip_wave
                        failed.append(err)
                halted = True
                break  # exit the wave loop

            if wave_agent_ids:
                _log(f"Waiting for wave {wave_num} agents to complete...")
                wave_results = await _wait_for_wave_agents(wave_agent_ids, timeout=300.0)

                # Log wave wait results for observability
                for aid, outcome in wave_results.items():
                    if outcome != "completed":
                        _log(f"spawn_team: wave {wave_num} agent {aid[:8]} -> {outcome}")

                # Record agent completions for tracker
                for aid in wave_agent_ids:
                    a = AGENTS.get(aid)
                    if a:
                        tracker.record_agent_complete(aid, status=a.status)
                tracker.record_stage_complete(wave_num)

                # Capture outcomes as Kumiho revisions + artifacts + provenance edges
                # Collect upstream revision krefs so DERIVED_FROM edges can be created
                upstream_rev_krefs = [
                    o.revision_kref for o in all_outcomes.values()
                    if o.revision_kref
                ]
                wave_outcomes = await _record_wave_outcomes(
                    wave_agent_ids, spawned_map, team_name,
                    cwd=expanded_cwd,
                    upstream_rev_krefs=upstream_rev_krefs if wave_num > 0 else None,
                )
                all_outcomes.update(wave_outcomes)

                # Save checkpoint after each wave completes (enables resume on failure)
                try:
                    ckpt_data = WaveCheckpoint(
                        team_kref=team_kref,
                        team_name=team_name,
                        task=task,
                        cwd=expanded_cwd,
                        halt_on_failure=halt_on_failure,
                        completed_wave=wave_num,
                        total_waves=len(waves),
                        spawned=spawned,
                        failed=failed,
                        spawned_map=spawned_map,
                        outcomes={aid: _outcome_to_dict(o) for aid, o in all_outcomes.items()},
                        checkpoint_id=checkpoint_id or "",
                    )
                    checkpoint_id = _save_wave_checkpoint(ckpt_data)
                except Exception as e:
                    _log(f"spawn_team: checkpoint save failed (non-fatal): {e}")

                # Check for upstream failures before spawning next wave
                # Use wave_results for authoritative status (covers timeout + unreachable)
                if wave_num < len(waves) - 1:
                    wave_bad = {
                        aid: reason for aid, reason in wave_results.items()
                        if reason in ("error", "timeout", "unreachable", "missing")
                    }
                    if wave_bad and halt_on_failure:
                        failed_descriptions = []
                        for aid, reason in wave_bad.items():
                            a = AGENTS.get(aid)
                            name = a.title if a else aid[:8]
                            failed_descriptions.append(f"{name}({reason})")
                        _log(
                            f"spawn_team: HALTING — wave {wave_num} had {len(wave_bad)} failure(s) "
                            f"({failed_descriptions}), skipping waves {wave_num + 1}..{len(waves) - 1}"
                        )
                        tracker.record_halt(wave_num, f"Wave {wave_num} failed: {', '.join(failed_descriptions)}")
                        for skip_wave in range(wave_num + 1, len(waves)):
                            for member_idx in waves[skip_wave]:
                                m = members[member_idx]
                                err = upstream_stage_failed(wave_num, list(wave_bad.keys()))
                                err["name"] = m.get("name", "agent")
                                err["role"] = m.get("role", "")
                                err["wave"] = skip_wave
                                err["upstream_failures"] = wave_bad
                                failed.append(err)
                        halted = True
                        break  # exit the wave loop
    finally:
        tracker.record_complete()

    # Enrich spawned agents with their outcome krefs + summaries
    for s in spawned:
        outcome = all_outcomes.get(s["agent_id"])
        if outcome:
            s["outcome_kref"] = outcome.revision_kref
            s["files_produced"] = len(outcome.files)

    # Build outcome_krefs map for backward compat
    outcome_krefs_map = {
        aid: o.revision_kref for aid, o in all_outcomes.items() if o.revision_kref
    }

    # Clean up checkpoint on successful full completion (no halt)
    if checkpoint_id and not halted:
        _delete_checkpoint(checkpoint_id)

    result: dict[str, Any] = {
        "team": team.get("item_name", team.get("name", "unknown")),
        "spawned_agents": spawned,
        "count": len(spawned),
        "waves": len(waves),
        "edge_driven": bool(edges),
    }
    if checkpoint_id and halted:
        result["checkpoint_id"] = checkpoint_id
        result["resume_from_wave"] = (
            max(s.get("wave", 0) for s in spawned) + 1 if spawned else 0
        )
    if resume_from:
        result["resumed_from_wave"] = resume_wave
    if outcome_krefs_map:
        result["outcome_krefs"] = outcome_krefs_map
    if failed:
        result["failed_agents"] = failed
        result["failed_count"] = len(failed)
    return result


async def tool_create_team(args: dict[str, Any], team_client: KumihoTeamClient, pool_client: KumihoAgentPoolClient) -> dict[str, Any]:
    import json as _json

    name = args["name"]
    description = args["description"]
    member_krefs = args["member_krefs"]
    edges = args.get("edges", [])

    if isinstance(member_krefs, str):
        try:
            member_krefs = _json.loads(member_krefs)
        except (ValueError, TypeError):
            member_krefs = [k.strip() for k in member_krefs.split(",") if k.strip()]
    if isinstance(edges, str):
        try:
            edges = _json.loads(edges)
        except (ValueError, TypeError):
            edges = []

    _log(f"create_team: name={name}, members={member_krefs}, edges={edges}")

    member_krefs = await resolve_agent_krefs(member_krefs, pool_client)

    # Dedup members — same agent listed twice creates implicit self-connections
    seen_krefs: set[str] = set()
    deduped: list[str] = []
    for k in member_krefs:
        if k not in seen_krefs:
            seen_krefs.add(k)
            deduped.append(k)
    if len(deduped) < len(member_krefs):
        _log(f"create_team: removed {len(member_krefs) - len(deduped)} duplicate member(s)")
    member_krefs = deduped

    if edges:
        all_edge_krefs = set()
        for edge in edges:
            for key in ("from_kref", "to_kref"):
                v = edge.get(key, "")
                if v:
                    all_edge_krefs.add(v)
        if all_edge_krefs:
            resolved_list = await resolve_agent_krefs(list(all_edge_krefs), pool_client)
            edge_map = dict(zip(list(all_edge_krefs), resolved_list))
            for edge in edges:
                for key in ("from_kref", "to_kref"):
                    v = edge.get(key, "")
                    if v and v in edge_map:
                        edge[key] = edge_map[v]

        # Strip self-edges before validation — the LLM often creates A→A accidentally
        edges = [e for e in edges if e.get("from_kref") != e.get("to_kref")]

        # Strip edges referencing agents not in the member list
        member_set = set(member_krefs)
        edges = [e for e in edges if e.get("from_kref") in member_set and e.get("to_kref") in member_set]

    _log(f"create_team (resolved): members={member_krefs}, edges={edges}")

    # Validate graph — ALWAYS run, even with empty edges (catches member issues)
    member_stubs = [{"kref": k, "name": k.rsplit("/", 1)[-1].split(".")[0] if "/" in k else k[:12]} for k in member_krefs]
    validation = validate_team_edges(member_stubs, edges, include_preview=True)
    if not validation.valid:
        _log(f"create_team: validation failed: {[e.message for e in validation.errors]}")
        return graph_invalid(validation.to_dict())

    result = await team_client.create_team(name, description, member_krefs, edges)
    if result is None:
        return classified_error("Failed to create team. Ensure Kumiho is configured.", code="kumiho_unavailable", category=RUNTIME_ENV_ERROR, retryable=True)
    return result


async def tool_search_teams(args: dict[str, Any], team_client: KumihoTeamClient) -> dict[str, Any]:
    query = args["query"]
    teams = await team_client.search_teams(query)
    team_list = []
    for team in teams:
        team_list.append({
            "kref": team.get("kref", ""),
            "name": team.get("item_name", team.get("name", "unknown")),
            "description": team.get("metadata", {}).get("description", ""),
        })
    return {"teams": team_list, "count": len(team_list)}


async def tool_get_spawn_progress(args: dict[str, Any]) -> dict[str, Any]:
    """Get spawn progress for a team or all teams."""
    team_name = args.get("team_name")
    if team_name:
        tracker = get_tracker(team_name)
        if not tracker:
            return team_not_found(team_name)
        return tracker.get_progress()
    return {"trackers": list_trackers()}


async def tool_lint_team(args: dict[str, Any], team_client: KumihoTeamClient) -> dict[str, Any]:
    """Lint a team definition — comprehensive static analysis.

    Checks role balance, naming, capability coverage, edge completeness,
    team size, and graph validity. Returns structured report with issues
    and suggestions.
    """
    team_kref = args.get("team_kref", "")
    task = args.get("task", "")

    if not team_kref:
        return classified_error("team_kref is required", code="missing_team_kref", category=VALIDATION_ERROR)

    team = await team_client.get_team(team_kref)
    if team is None:
        return team_not_found(team_kref)

    members_raw = team.get("members", [])
    edges = team.get("edges", [])

    # Build member dicts with metadata
    members = []
    for m in members_raw:
        member: dict[str, Any] = {
            "kref": m.get("kref", ""),
            "name": m.get("item_name", m.get("name", "")),
            "role": m.get("metadata", {}).get("role", ""),
            "capabilities": m.get("metadata", {}).get("capabilities", []),
        }
        members.append(member)

    return lint_team(members, edges, task=task)


async def tool_resolve_outcome(args: dict[str, Any]) -> dict[str, Any]:
    """Resolve an outcome kref to its full revision with artifacts and metadata."""
    revision_kref = args.get("revision_kref", "")
    if not revision_kref:
        return classified_error("revision_kref is required", code="missing_kref", category=VALIDATION_ERROR)

    sdk = _get_sdk()
    if not sdk:
        return classified_error("Kumiho not available", code="kumiho_unavailable", category=RUNTIME_ENV_ERROR)

    try:
        artifacts = await sdk.get_artifacts(revision_kref)
        edges = await sdk.get_edges(revision_kref, direction=0)

        return {
            "revision_kref": revision_kref,
            "artifacts": [
                {"name": a.get("name", ""), "location": a.get("location", ""), "kref": a.get("kref", "")}
                for a in artifacts
            ],
            "artifact_count": len(artifacts),
            "edges": [
                {"source": e.get("source_kref", ""), "target": e.get("target_kref", ""), "type": e.get("edge_type", "")}
                for e in edges
            ],
        }
    except Exception as e:
        return classified_error(f"Failed to resolve outcome: {e}", code="resolve_failed", category=RUNTIME_ENV_ERROR)


async def tool_get_outcome_lineage(args: dict[str, Any]) -> dict[str, Any]:
    """Walk DERIVED_FROM edges to show the full provenance chain of an outcome."""
    revision_kref = args.get("revision_kref", "")
    if not revision_kref:
        return classified_error("revision_kref is required", code="missing_kref", category=VALIDATION_ERROR)

    sdk = _get_sdk()
    if not sdk:
        return classified_error("Kumiho not available", code="kumiho_unavailable", category=RUNTIME_ENV_ERROR)

    try:
        # Walk upstream: what was this derived from?
        upstream_edges = await sdk.get_edges(revision_kref, direction=1)  # outgoing
        upstream = []
        for e in upstream_edges:
            if e.get("edge_type", "").upper() in ("DERIVED_FROM", "DEPENDS_ON"):
                target = e.get("target_kref", "")
                if target:
                    target_artifacts = await sdk.get_artifacts(target)
                    upstream.append({
                        "revision_kref": target,
                        "edge_type": e.get("edge_type", ""),
                        "artifacts": [{"name": a.get("name", ""), "location": a.get("location", "")} for a in target_artifacts],
                    })

        # Walk downstream: what depends on this?
        downstream_edges = await sdk.get_edges(revision_kref, direction=2)  # incoming
        downstream = []
        for e in downstream_edges:
            if e.get("edge_type", "").upper() in ("DERIVED_FROM", "DEPENDS_ON"):
                source = e.get("source_kref", "")
                if source:
                    source_artifacts = await sdk.get_artifacts(source)
                    downstream.append({
                        "revision_kref": source,
                        "edge_type": e.get("edge_type", ""),
                        "artifacts": [{"name": a.get("name", ""), "location": a.get("location", "")} for a in source_artifacts],
                    })

        return {
            "revision_kref": revision_kref,
            "upstream": upstream,
            "downstream": downstream,
            "upstream_count": len(upstream),
            "downstream_count": len(downstream),
        }
    except Exception as e:
        return classified_error(f"Failed to get lineage: {e}", code="lineage_failed", category=RUNTIME_ENV_ERROR)
