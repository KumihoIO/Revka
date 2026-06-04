"""get_workflow_metadata tool — return all primitives the Architect can use
when proposing a workflow revision: step types, agents, auth profiles,
skills, channels.

Step types are introspected from the ``StepType`` enum + per-type Config
classes in ``operator_mcp.workflow.schema``. Live data (agents, auth
profiles, skills, channels) is fetched through the gateway client; if a
gateway endpoint is unreachable, that section returns an empty list and a
warning is appended to the response — the tool never raises.
"""
from __future__ import annotations

from typing import Any, get_args, get_origin
from types import UnionType
from typing import Union

from pydantic import BaseModel

from .._log import _log
from ..workflow.schema import (
    A2AStepConfig,
    AgentStepConfig,
    ComputeStepConfig,
    ConditionalStepConfig,
    DeprecateStepConfig,
    EmailStepConfig,
    ForEachStepConfig,
    GotoStepConfig,
    GroupChatStepConfig,
    HandoffStepConfig,
    HumanApprovalConfig,
    HumanInputConfig,
    ImageStepConfig,
    ManusStepConfig,
    MapReduceStepConfig,
    NotifyStepConfig,
    OutputStepConfig,
    ParallelStepConfig,
    PythonStepConfig,
    ResolveStepConfig,
    ShellStepConfig,
    StepType,
    SupervisorStepConfig,
    TagStepConfig,
)


# ---------------------------------------------------------------------------
# StepType → config class / human description / example YAML
# ---------------------------------------------------------------------------

_STEP_CONFIG_CLASS: dict[StepType, type[BaseModel]] = {
    StepType.AGENT: AgentStepConfig,
    StepType.SHELL: ShellStepConfig,
    StepType.PYTHON: PythonStepConfig,
    StepType.COMPUTE: ComputeStepConfig,
    StepType.EMAIL: EmailStepConfig,
    StepType.IMAGE: ImageStepConfig,
    StepType.CONDITIONAL: ConditionalStepConfig,
    StepType.PARALLEL: ParallelStepConfig,
    StepType.GOTO: GotoStepConfig,
    StepType.HUMAN_APPROVAL: HumanApprovalConfig,
    StepType.HUMAN_INPUT: HumanInputConfig,
    StepType.NOTIFY: NotifyStepConfig,
    StepType.OUTPUT: OutputStepConfig,
    StepType.A2A: A2AStepConfig,
    StepType.MAP_REDUCE: MapReduceStepConfig,
    StepType.SUPERVISOR: SupervisorStepConfig,
    StepType.GROUP_CHAT: GroupChatStepConfig,
    StepType.HANDOFF: HandoffStepConfig,
    StepType.RESOLVE: ResolveStepConfig,
    StepType.FOR_EACH: ForEachStepConfig,
    StepType.TAG: TagStepConfig,
    StepType.DEPRECATE: DeprecateStepConfig,
    StepType.MANUS: ManusStepConfig,
}

_STEP_LABELS: dict[StepType, str] = {
    StepType.AGENT: "Agent",
    StepType.SHELL: "Shell",
    StepType.PYTHON: "Python",
    StepType.COMPUTE: "Compute",
    StepType.EMAIL: "Email",
    StepType.IMAGE: "Image",
    StepType.CONDITIONAL: "Conditional",
    StepType.PARALLEL: "Parallel",
    StepType.GOTO: "Goto",
    StepType.HUMAN_APPROVAL: "Human Approval",
    StepType.HUMAN_INPUT: "Human Input",
    StepType.NOTIFY: "Notify",
    StepType.OUTPUT: "Output",
    StepType.A2A: "A2A",
    StepType.MAP_REDUCE: "Map / Reduce",
    StepType.SUPERVISOR: "Supervisor",
    StepType.GROUP_CHAT: "Group Chat",
    StepType.HANDOFF: "Handoff",
    StepType.RESOLVE: "Resolve",
    StepType.FOR_EACH: "For Each",
    StepType.TAG: "Tag",
    StepType.DEPRECATE: "Deprecate",
    StepType.MANUS: "Manus",
}

_STEP_DESCRIPTIONS: dict[StepType, str] = {
    StepType.AGENT: "Spawn a Revka agent (claude/codex) with a prompt.",
    StepType.SHELL: "Run a shell command.",
    StepType.PYTHON: "Invoke a Python script with JSON I/O.",
    StepType.COMPUTE: "Evaluate sandboxed expressions into typed output_data fields.",
    StepType.EMAIL: "Send an outbound email via SMTP.",
    StepType.IMAGE: (
        "Generate image(s) via the codex CLI's image_generation tool, "
        "push to the Live Canvas, and register a Kumiho artifact. "
        "ALWAYS prefer this over a generic `agent` step for image-generation "
        "tasks — agent steps cannot call generate_image_codex (it's an "
        "operator-tier MCP tool excluded from subagents) and will only "
        "describe what they would do without producing a canvas frame "
        "or asset."
    ),
    StepType.CONDITIONAL: "Branch based on expressions over prior step outputs.",
    StepType.PARALLEL: "Run multiple sub-steps concurrently with join strategies.",
    StepType.GOTO: "Jump to another step (loop support with max_iterations guard).",
    StepType.HUMAN_APPROVAL: "Pause for human confirmation (yes/no) before proceeding.",
    StepType.HUMAN_INPUT: "Pause and wait for freeform human text input.",
    StepType.NOTIFY: "Fire-and-forget notification to one or more channels.",
    StepType.OUTPUT: "Emit structured output from the workflow.",
    StepType.A2A: "Send a task to an external A2A agent.",
    StepType.MAP_REDUCE: (
        "Fan-out / fan-in across splits. Mapper and reducer may be built-in "
        "agent types or pool template names."
    ),
    StepType.SUPERVISOR: (
        "Dynamic delegation loop with a supervisor agent and optional "
        "specialist template allowlist."
    ),
    StepType.GROUP_CHAT: (
        "Moderated multi-agent discussion. Participants and moderator may "
        "be built-in agent types or pool template names."
    ),
    StepType.HANDOFF: (
        "Pass context from one agent to another. The receiving agent may be "
        "a built-in type or pool template name."
    ),
    StepType.RESOLVE: "Deterministic Kumiho entity lookup.",
    StepType.FOR_EACH: "Sequential iteration over a range or list of items.",
    StepType.TAG: "Re-tag an existing Kumiho entity revision.",
    StepType.DEPRECATE: "Deprecate a Kumiho item.",
    StepType.MANUS: (
        "Delegate a web-research task to a hosted Manus AI agent. "
        "Returns the final assistant message + optional structured-output "
        "value as the step's output_data."
    ),
}

_EXAMPLE_YAML: dict[StepType, str] = {
    StepType.AGENT: (
        "id: do_thing\n"
        "type: agent\n"
        "agent:\n"
        "  prompt: \"Do the thing\"\n"
        "  agent_type: claude\n"
    ),
    StepType.SHELL: (
        "id: run_cmd\n"
        "type: shell\n"
        "shell:\n"
        "  command: \"echo hi\"\n"
    ),
    StepType.PYTHON: (
        "id: transform\n"
        "type: python\n"
        "python:\n"
        "  code: |\n"
        "    import json, sys\n"
        "    print(json.dumps({\"ok\": True}))\n"
    ),
    StepType.COMPUTE: (
        "id: next_arc_context\n"
        "type: compute\n"
        "compute:\n"
        "  outputs:\n"
        "    arc_number: \"${{ int(arc_loader.output_data.metadata.arc_number) + 1 }}\"\n"
        "    start: \"${{ int(arc_loader.output_data.metadata.end) + 1 }}\"\n"
        "    end: \"${{ outputs.start + 5 }}\"\n"
        "    episode_range: \"${{ outputs.start }}..${{ outputs.end }}\"\n"
    ),
    StepType.EMAIL: (
        "id: send\n"
        "type: email\n"
        "email:\n"
        "  to: \"a@b.com\"\n"
        "  subject: \"hi\"\n"
        "  body: \"hello\"\n"
    ),
    StepType.IMAGE: (
        "id: hero_shot\n"
        "type: image\n"
        "image:\n"
        "  prompt: |\n"
        "    Architectural panel of Seoul Station 2040.\n"
        "    Wide aerial shot, golden hour.\n"
        "  count: 1\n"
        "  canvas: true\n"
        "  register_artifact: true\n"
        "depends_on: [panel_design_plan]\n"
    ),
    StepType.CONDITIONAL: (
        "id: gate\n"
        "type: conditional\n"
        "conditional:\n"
        "  branches:\n"
        "    - condition: \"${prior.status} == 'completed'\"\n"
        "      goto: next_step\n"
        "    - condition: default\n"
        "      goto: fallback\n"
    ),
    StepType.PARALLEL: (
        "id: fanout\n"
        "type: parallel\n"
        "parallel:\n"
        "  steps: [step_a, step_b]\n"
        "  join: all\n"
    ),
    StepType.GOTO: (
        "id: loop_back\n"
        "type: goto\n"
        "goto:\n"
        "  target: earlier_step\n"
        "  max_iterations: 3\n"
    ),
    StepType.HUMAN_APPROVAL: (
        "id: approve\n"
        "type: human_approval\n"
        "human_approval:\n"
        "  message: \"Approve to continue\"\n"
        "  channel: dashboard\n"
    ),
    StepType.HUMAN_INPUT: (
        "id: ask\n"
        "type: human_input\n"
        "human_input:\n"
        "  message: \"What should I do next?\"\n"
        "  channel: dashboard\n"
    ),
    StepType.NOTIFY: (
        "id: ping\n"
        "type: notify\n"
        "notify:\n"
        "  channels: [dashboard]\n"
        "  message: \"Heads up\"\n"
    ),
    StepType.OUTPUT: (
        "id: emit\n"
        "type: output\n"
        "output:\n"
        "  format: json\n"
        "  template: \"{\\\"result\\\": \\\"${earlier.output}\\\"}\"\n"
    ),
    StepType.A2A: (
        "id: external\n"
        "type: a2a\n"
        "a2a:\n"
        "  url: \"https://agent.example.com\"\n"
        "  message: \"do the thing\"\n"
    ),
    StepType.MAP_REDUCE: (
        "id: mr\n"
        "type: map_reduce\n"
        "map_reduce:\n"
        "  task: \"summarize chapters\"\n"
        "  splits: [\"ch1\", \"ch2\"]\n"
        "  mapper: research-template\n"
        "  reducer: lead-reviewer\n"
    ),
    StepType.SUPERVISOR: (
        "id: sup\n"
        "type: supervisor\n"
        "supervisor:\n"
        "  task: \"plan and execute migration\"\n"
        "  supervisor_type: lead-architect\n"
        "  templates: [research-template, implementer-template]\n"
        "  max_iterations: 5\n"
    ),
    StepType.GROUP_CHAT: (
        "id: chat\n"
        "type: group_chat\n"
        "group_chat:\n"
        "  topic: \"design review\"\n"
        "  participants: [reviewer, coder]\n"
        "  moderator: lead-architect\n"
    ),
    StepType.HANDOFF: (
        "id: pass\n"
        "type: handoff\n"
        "handoff:\n"
        "  from_step: research\n"
        "  to_agent_type: implementer-template\n"
    ),
    StepType.RESOLVE: (
        "id: lookup\n"
        "type: resolve\n"
        "resolve:\n"
        "  kind: analysis-report\n"
        "  tag: published\n"
    ),
    StepType.FOR_EACH: (
        "id: each_episode\n"
        "type: for_each\n"
        "for_each:\n"
        "  range: \"1..3\"\n"
        "  variable: episode\n"
        "  steps: [process_episode]\n"
    ),
    StepType.TAG: (
        "id: mark\n"
        "type: tag\n"
        "tag_step:\n"
        "  item_kref: \"${earlier.output_data.kref}\"\n"
        "  tag: published\n"
    ),
    StepType.DEPRECATE: (
        "id: retire\n"
        "type: deprecate\n"
        "deprecate_step:\n"
        "  item_kref: \"${earlier.output_data.kref}\"\n"
        "  reason: \"superseded\"\n"
    ),
    StepType.MANUS: (
        "id: research\n"
        "type: manus\n"
        "manus:\n"
        "  prompt: \"Research the top 5 robotics startups in Seoul\"\n"
        "  timeout_seconds: 600\n"
    ),
}


# ---------------------------------------------------------------------------
# Type stringification — Pydantic model_fields → friendly labels
# ---------------------------------------------------------------------------

_PRIMITIVE_NAMES: dict[type, str] = {
    str: "string",
    int: "int",
    float: "float",
    bool: "bool",
    dict: "dict",
    list: "list",
    type(None): "null",
}


def _annotation_to_string(ann: Any) -> str:
    """Render a Python type annotation as a friendly string for the LLM.

    Handles primitives, ``list[X]``, ``dict[K, V]``, ``X | None`` (and the
    older ``Optional[X]`` / ``Union[X, None]`` forms), ``Literal[...]``, and
    Pydantic submodels (rendered as the class name).
    """
    if ann is None or ann is type(None):
        return "null"

    # Literal[...] → "literal[a|b|c]"
    origin = get_origin(ann)
    if origin is not None:
        # Literal
        if getattr(ann, "__class__", None).__name__ == "_LiteralGenericAlias" or (
            origin is type(None) is False and str(origin).endswith("Literal")
        ):
            try:
                vals = "|".join(repr(v) for v in get_args(ann))
                return f"literal[{vals}]"
            except Exception:
                pass

        # Union / X | None
        if origin in (Union, UnionType):
            args = [a for a in get_args(ann) if a is not type(None)]
            none_present = any(a is type(None) for a in get_args(ann))
            if len(args) == 1:
                inner = _annotation_to_string(args[0])
                return f"{inner} | null" if none_present else inner
            rendered = " | ".join(_annotation_to_string(a) for a in args)
            return f"{rendered} | null" if none_present else rendered

        # list[X] / dict[K, V]
        if origin in (list, tuple, set, frozenset):
            inner = ", ".join(_annotation_to_string(a) for a in get_args(ann))
            name = origin.__name__
            return f"{name}[{inner}]" if inner else name
        if origin is dict:
            args = get_args(ann)
            if len(args) == 2:
                return f"dict[{_annotation_to_string(args[0])}, {_annotation_to_string(args[1])}]"
            return "dict"

        # Generic fallback
        return str(ann)

    # Bare types
    if isinstance(ann, type):
        if ann in _PRIMITIVE_NAMES:
            return _PRIMITIVE_NAMES[ann]
        if issubclass(ann, BaseModel):
            return ann.__name__
        return ann.__name__

    return str(ann)


# ---------------------------------------------------------------------------
# step_types section — cached after first compute (static data)
# ---------------------------------------------------------------------------

_STEP_TYPES_CACHE: list[dict[str, Any]] | None = None


def _compute_step_types() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for st in StepType:
        cfg_cls = _STEP_CONFIG_CLASS.get(st)
        config_fields: list[dict[str, Any]] = []
        if cfg_cls is not None:
            for fname, finfo in cfg_cls.model_fields.items():
                config_fields.append({
                    "name": fname,
                    "type": _annotation_to_string(finfo.annotation),
                    "required": finfo.is_required(),
                    "description": (finfo.description or "").strip(),
                })
        out.append({
            "type": st.value,
            "label": _STEP_LABELS.get(st, st.value),
            "description": _STEP_DESCRIPTIONS.get(st, ""),
            "config_fields": config_fields,
            "example_yaml": _EXAMPLE_YAML.get(st, "").rstrip("\n"),
        })
    return out


def _get_step_types() -> list[dict[str, Any]]:
    global _STEP_TYPES_CACHE
    if _STEP_TYPES_CACHE is None:
        _STEP_TYPES_CACHE = _compute_step_types()
    return _STEP_TYPES_CACHE


# ---------------------------------------------------------------------------
# Live-data sections — agents, auth_profiles, skills, channels
# ---------------------------------------------------------------------------

def _gateway() -> Any:
    """Return the shared RevkaGatewayClient instance (operator_mcp module)."""
    from ..operator_mcp import REVKA_GW
    return REVKA_GW


def _slim_agent(a: dict[str, Any]) -> dict[str, Any]:
    return {
        "item_name": a.get("item_name", ""),
        "name": a.get("name", ""),
        "agent_type": a.get("agent_type", ""),
        "role": a.get("role", ""),
        "expertise": a.get("expertise", []) or [],
        "identity": a.get("identity", ""),
    }


def _slim_auth_profile(p: dict[str, Any]) -> dict[str, Any]:
    # Metadata only — no token bytes are echoed even if upstream changes.
    return {
        "id": p.get("id", ""),
        "provider": p.get("provider", ""),
        "profile_name": p.get("profile_name", ""),
        "kind": p.get("kind", ""),
    }


def _slim_skill(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": s.get("name") or s.get("item_name", ""),
        "description": s.get("description", ""),
    }


def _slim_channel(c: dict[str, Any]) -> dict[str, Any]:
    # Gateway returns "type"; expose as "kind" per the documented shape.
    return {
        "name": c.get("name", ""),
        "kind": c.get("kind") or c.get("type", ""),
    }


# ---------------------------------------------------------------------------
# Tool entrypoint
# ---------------------------------------------------------------------------

_DEFAULT_INCLUDE = ("step_types", "agents", "auth_profiles", "skills", "channels")


async def tool_get_workflow_metadata(args: dict[str, Any]) -> dict[str, Any]:
    """Return the editor-relevant primitives the Architect can compose.

    Args:
        include: optional list filtering which categories to return.
            Defaults to all of ``step_types``, ``agents``, ``auth_profiles``,
            ``skills``, ``channels``.
    """
    raw_include = args.get("include")
    if isinstance(raw_include, list) and raw_include:
        include = {str(x) for x in raw_include}
    else:
        include = set(_DEFAULT_INCLUDE)

    result: dict[str, Any] = {}
    warnings: list[str] = []

    if "step_types" in include:
        result["step_types"] = _get_step_types()

    if "agents" in include:
        try:
            data = await _gateway().get_agents(include_deprecated=False)
        except Exception as e:
            _log(f"get_workflow_metadata: agents fetch raised: {e}")
            data = None
        if data is None:
            result["agents"] = []
            warnings.append("agents: gateway unreachable")
        else:
            result["agents"] = [_slim_agent(a) for a in data]

    if "auth_profiles" in include:
        try:
            data = await _gateway().get_auth_profiles()
        except Exception as e:
            _log(f"get_workflow_metadata: auth_profiles fetch raised: {e}")
            data = None
        if data is None:
            result["auth_profiles"] = []
            warnings.append("auth_profiles: gateway unreachable")
        else:
            result["auth_profiles"] = [_slim_auth_profile(p) for p in data]

    if "skills" in include:
        try:
            data = await _gateway().get_skills(include_deprecated=False)
        except Exception as e:
            _log(f"get_workflow_metadata: skills fetch raised: {e}")
            data = None
        if data is None:
            result["skills"] = []
            warnings.append("skills: gateway unreachable")
        else:
            result["skills"] = [_slim_skill(s) for s in data]

    if "channels" in include:
        try:
            data = await _gateway().get_channels()
        except Exception as e:
            _log(f"get_workflow_metadata: channels fetch raised: {e}")
            data = None
        if data is None:
            result["channels"] = []
            warnings.append("channels: gateway unreachable")
        else:
            result["channels"] = [_slim_channel(c) for c in data]

    if warnings:
        result["_warnings"] = warnings
    return result
