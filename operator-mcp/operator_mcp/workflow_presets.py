"""Workflow presets — reusable orchestration patterns.

Built-in presets for common multi-agent workflows:
  - code-review: coder → reviewer (with optional fix loop)
  - spec-to-impl: architect → coder → reviewer
  - bug-fix: researcher → coder → tester
  - refactor: researcher → architect → coder → reviewer

Custom presets can be saved/loaded from ~/.construct/operator_mcp/workflow_presets.json.

Usage (via MCP tools):
    list_workflow_presets()               # see all available presets
    spawn_workflow(preset="code-review", task="...", cwd="...")
"""
from __future__ import annotations

import json as _json
import os
from dataclasses import asdict, dataclass, field
from typing import Any

from ._log import _log


# ---------------------------------------------------------------------------
# Preset schema
# ---------------------------------------------------------------------------

@dataclass
class WorkflowStep:
    """A single step in a workflow preset."""
    role: str                          # coder, reviewer, researcher, etc.
    agent_type: str = "codex"          # claude or codex
    name_suffix: str = ""              # appended to step title, e.g. "alice"
    depends_on: list[int] = field(default_factory=list)  # indices of prior steps
    review_loop: bool = False          # if True, run review_fix_loop after this step
    max_review_rounds: int = 2

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorkflowPreset:
    """A reusable workflow blueprint."""
    name: str
    description: str
    steps: list[WorkflowStep]
    builtin: bool = True
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "steps": [s.to_dict() for s in self.steps],
            "builtin": self.builtin,
            "tags": self.tags,
            "step_count": len(self.steps),
        }


# ---------------------------------------------------------------------------
# Built-in presets
# ---------------------------------------------------------------------------

_BUILTIN_PRESETS: dict[str, WorkflowPreset] = {
    "code-review": WorkflowPreset(
        name="code-review",
        description="Coder implements, reviewer checks. Optional fix loop on rejection.",
        steps=[
            WorkflowStep(role="coder", agent_type="codex"),
            WorkflowStep(role="reviewer", agent_type="codex", depends_on=[0], review_loop=True),
        ],
        tags=["review", "quality"],
    ),
    "spec-to-impl": WorkflowPreset(
        name="spec-to-impl",
        description="Architect designs, coder implements, reviewer verifies.",
        steps=[
            WorkflowStep(role="architect", agent_type="claude"),
            WorkflowStep(role="coder", agent_type="codex", depends_on=[0]),
            WorkflowStep(role="reviewer", agent_type="codex", depends_on=[1], review_loop=True),
        ],
        tags=["architecture", "review"],
    ),
    "bug-fix": WorkflowPreset(
        name="bug-fix",
        description="Researcher investigates, coder fixes, tester verifies.",
        steps=[
            WorkflowStep(role="researcher", agent_type="claude"),
            WorkflowStep(role="coder", agent_type="codex", depends_on=[0]),
            WorkflowStep(role="tester", agent_type="codex", depends_on=[1]),
        ],
        tags=["bugfix", "testing"],
    ),
    "refactor": WorkflowPreset(
        name="refactor",
        description="Researcher analyzes, architect plans, coder implements, reviewer approves.",
        steps=[
            WorkflowStep(role="researcher", agent_type="claude"),
            WorkflowStep(role="architect", agent_type="claude", depends_on=[0]),
            WorkflowStep(role="coder", agent_type="codex", depends_on=[1]),
            WorkflowStep(role="reviewer", agent_type="codex", depends_on=[2], review_loop=True),
        ],
        tags=["refactor", "architecture", "review"],
    ),
    "research-only": WorkflowPreset(
        name="research-only",
        description="Two parallel researchers investigate different angles.",
        steps=[
            WorkflowStep(role="researcher", agent_type="claude", name_suffix="alpha"),
            WorkflowStep(role="researcher", agent_type="claude", name_suffix="beta"),
        ],
        tags=["research", "parallel"],
    ),
}


# ---------------------------------------------------------------------------
# Custom preset storage
# ---------------------------------------------------------------------------

_PRESETS_PATH = os.path.expanduser("~/.construct/operator_mcp/workflow_presets.json")
_custom_presets: dict[str, WorkflowPreset] | None = None


def _load_custom_presets() -> dict[str, WorkflowPreset]:
    global _custom_presets
    if _custom_presets is not None:
        return _custom_presets

    _custom_presets = {}
    try:
        if os.path.exists(_PRESETS_PATH):
            with open(_PRESETS_PATH, "r") as f:
                data = _json.load(f)
            for name, entry in data.items():
                steps = [WorkflowStep(**s) for s in entry.get("steps", [])]
                _custom_presets[name] = WorkflowPreset(
                    name=name,
                    description=entry.get("description", ""),
                    steps=steps,
                    builtin=False,
                    tags=entry.get("tags", []),
                )
    except Exception as exc:
        _log(f"workflow_presets: error loading custom presets: {exc}")

    return _custom_presets


def _save_custom_presets() -> None:
    custom = _load_custom_presets()
    try:
        os.makedirs(os.path.dirname(_PRESETS_PATH), exist_ok=True)
        with open(_PRESETS_PATH, "w") as f:
            _json.dump(
                {name: p.to_dict() for name, p in custom.items()},
                f,
                indent=2,
            )
    except Exception as exc:
        _log(f"workflow_presets: error saving: {exc}")


def get_all_presets() -> dict[str, WorkflowPreset]:
    """Get all presets (builtin + custom)."""
    all_p = dict(_BUILTIN_PRESETS)
    all_p.update(_load_custom_presets())
    return all_p


def get_preset(name: str) -> WorkflowPreset | None:
    """Get a preset by name."""
    return get_all_presets().get(name)


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

async def tool_list_workflow_presets(args: dict[str, Any]) -> dict[str, Any]:
    """List all available workflow presets."""
    tag = args.get("tag")
    presets = get_all_presets()

    result = []
    for p in presets.values():
        if tag and tag not in p.tags:
            continue
        result.append(p.to_dict())

    return {"presets": result, "count": len(result)}


async def tool_save_workflow_preset(args: dict[str, Any]) -> dict[str, Any]:
    """Save a custom workflow preset."""
    name = args.get("name", "")
    description = args.get("description", "")
    steps_raw = args.get("steps", [])
    tags = args.get("tags", [])

    if not name:
        return {"error": "Preset name is required"}
    if name in _BUILTIN_PRESETS:
        return {"error": f"Cannot overwrite builtin preset '{name}'"}
    if not steps_raw:
        return {"error": "At least one step is required"}

    steps = []
    for i, s in enumerate(steps_raw):
        if isinstance(s, str):
            import json as _j
            s = _j.loads(s)
        steps.append(WorkflowStep(
            role=s.get("role", "coder"),
            agent_type=s.get("agent_type", "codex"),
            name_suffix=s.get("name_suffix", ""),
            depends_on=s.get("depends_on", []),
            review_loop=s.get("review_loop", False),
            max_review_rounds=s.get("max_review_rounds", 2),
        ))

    preset = WorkflowPreset(
        name=name,
        description=description,
        steps=steps,
        builtin=False,
        tags=tags,
    )

    custom = _load_custom_presets()
    custom[name] = preset
    _save_custom_presets()

    return {"saved": name, "preset": preset.to_dict()}


async def tool_get_workflow_plan(args: dict[str, Any]) -> dict[str, Any]:
    """Preview what a workflow preset would do without executing it.

    Returns the execution plan: steps, dependencies, agent types.
    """
    preset_name = args.get("preset", "")
    preset = get_preset(preset_name)
    if not preset:
        available = list(get_all_presets().keys())
        return {"error": f"Unknown preset '{preset_name}'", "available": available}

    # Build execution plan
    plan_steps = []
    for i, step in enumerate(preset.steps):
        plan_step: dict[str, Any] = {
            "step": i,
            "role": step.role,
            "agent_type": step.agent_type,
            "wave": _compute_wave(i, step.depends_on, preset.steps),
        }
        if step.name_suffix:
            plan_step["name"] = f"{step.role}-{step.name_suffix}"
        if step.depends_on:
            plan_step["depends_on"] = [
                f"step {d} ({preset.steps[d].role})" for d in step.depends_on if d < len(preset.steps)
            ]
        if step.review_loop:
            plan_step["review_loop"] = True
            plan_step["max_review_rounds"] = step.max_review_rounds
        plan_steps.append(plan_step)

    # Compute waves
    waves: dict[int, list[int]] = {}
    for ps in plan_steps:
        w = ps["wave"]
        waves.setdefault(w, []).append(ps["step"])

    return {
        "preset": preset_name,
        "description": preset.description,
        "steps": plan_steps,
        "waves": {str(k): v for k, v in sorted(waves.items())},
        "total_waves": len(waves),
    }


def _compute_wave(
    step_idx: int,
    depends_on: list[int],
    steps: list[WorkflowStep],
    _visiting: set[int] | None = None,
    _cache: dict[int, int] | None = None,
) -> int:
    """Compute which wave a step belongs to based on dependencies.

    Uses memoization and cycle detection to prevent infinite recursion.
    """
    if _cache is None:
        _cache = {}
    if step_idx in _cache:
        return _cache[step_idx]

    if _visiting is None:
        _visiting = set()

    if not depends_on:
        _cache[step_idx] = 0
        return 0

    if step_idx in _visiting:
        # Circular dependency — break the cycle
        return 0

    _visiting.add(step_idx)
    max_dep_wave = 0
    for dep in depends_on:
        if dep < len(steps):
            dep_wave = _compute_wave(dep, steps[dep].depends_on, steps, _visiting, _cache)
            max_dep_wave = max(max_dep_wave, dep_wave + 1)
    _visiting.discard(step_idx)

    _cache[step_idx] = max_dep_wave
    return max_dep_wave
