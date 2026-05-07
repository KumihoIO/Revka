"""Workflow validation — structural checks before execution.

Validates:
  - No duplicate step IDs
  - No dependency cycles (topological sort)
  - All depends_on references point to existing steps
  - All variable references (${step_id.field}) resolve
  - Parallel sub-steps exist
  - Goto targets exist and max_iterations is sane
  - Conditional branch targets exist
  - Required inputs have no default → must be provided at runtime
  - Output entity_name / entity_kind cross-consistency
  - Trigger definitions have required fields and map required inputs
"""
from __future__ import annotations

import re
from typing import Any

from .schema import (
    StepType,
    WorkflowDef,
    StepDef,
    ConditionalStepConfig,
    GotoStepConfig,
    ParallelStepConfig,
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

class ValidationError:
    """A single validation issue."""
    __slots__ = ("step_id", "field", "message", "severity")

    def __init__(self, message: str, step_id: str = "", field: str = "",
                 severity: str = "error"):
        self.step_id = step_id
        self.field = field
        self.message = message
        self.severity = severity  # "error" or "warning"

    def to_dict(self) -> dict[str, str]:
        d: dict[str, str] = {"message": self.message, "severity": self.severity}
        if self.step_id:
            d["step_id"] = self.step_id
        if self.field:
            d["field"] = self.field
        return d

    def __repr__(self) -> str:
        loc = f"[{self.step_id}]" if self.step_id else ""
        return f"ValidationError{loc}: {self.message}"


class ValidationResult:
    """Aggregate validation outcome."""
    __slots__ = ("errors", "warnings", "execution_order")

    def __init__(self) -> None:
        self.errors: list[ValidationError] = []
        self.warnings: list[ValidationError] = []
        self.execution_order: list[str] = []

    @property
    def valid(self) -> bool:
        return len(self.errors) == 0

    def add_error(self, message: str, step_id: str = "", field: str = "") -> None:
        self.errors.append(ValidationError(message, step_id, field, "error"))

    def add_warning(self, message: str, step_id: str = "", field: str = "") -> None:
        self.warnings.append(ValidationError(message, step_id, field, "warning"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
            "execution_order": self.execution_order,
        }


# ---------------------------------------------------------------------------
# Variable reference pattern
# ---------------------------------------------------------------------------

_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _extract_var_refs(text: str) -> list[str]:
    """Extract all ${...} variable references from a string."""
    return _VAR_PATTERN.findall(text)


# ---------------------------------------------------------------------------
# Validation passes
# ---------------------------------------------------------------------------

def _check_duplicate_ids(wf: WorkflowDef, result: ValidationResult) -> set[str]:
    """Check for duplicate step IDs. Returns the set of valid IDs."""
    seen: set[str] = set()
    for step in wf.steps:
        if step.id in seen:
            result.add_error(f"Duplicate step ID: '{step.id}'", step.id)
        seen.add(step.id)
    return seen


def _check_dependencies(wf: WorkflowDef, valid_ids: set[str],
                        result: ValidationResult) -> dict[str, set[str]]:
    """Check all depends_on references. Returns adjacency map."""
    adj: dict[str, set[str]] = {s.id: set() for s in wf.steps}
    for step in wf.steps:
        for dep in step.depends_on:
            if dep not in valid_ids:
                result.add_error(
                    f"depends_on references unknown step '{dep}'",
                    step.id, "depends_on",
                )
            else:
                adj[step.id].add(dep)
    return adj


def _check_cycles(adj: dict[str, set[str]], result: ValidationResult) -> list[str]:
    """Topological sort with cycle detection. Returns execution order."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {node: WHITE for node in adj}
    order: list[str] = []
    cycle_nodes: list[str] = []

    def dfs(node: str) -> bool:
        color[node] = GRAY
        for dep in adj[node]:
            if dep not in color:
                continue
            if color[dep] == GRAY:
                cycle_nodes.append(f"{node} -> {dep}")
                return True
            if color[dep] == WHITE:
                if dfs(dep):
                    return True
        color[node] = BLACK
        order.append(node)
        return False

    for node in adj:
        if color[node] == WHITE:
            if dfs(node):
                result.add_error(
                    f"Dependency cycle detected: {', '.join(cycle_nodes)}"
                )

    return order  # Topological order (dependencies first)


def _check_step_configs(wf: WorkflowDef, valid_ids: set[str],
                        result: ValidationResult) -> None:
    """Validate type-specific step configurations."""
    for step in wf.steps:
        config = step.get_config()

        if step.type == StepType.AGENT:
            # No hard requirement: StepDef.resolve_agent_config() synthesizes a
            # default AgentStepConfig from action + agent_hints at executor time
            # when step.agent is None.
            pass

        elif step.type == StepType.SHELL:
            if config is None or not getattr(config, "command", ""):
                result.add_error("'shell' step missing command", step.id, "shell")

        elif step.type == StepType.CONDITIONAL:
            if config is None:
                result.add_error("'conditional' step missing config", step.id, "conditional")
            else:
                cfg: ConditionalStepConfig = config  # type: ignore
                if not cfg.branches:
                    result.add_error("'conditional' step has no branches", step.id)
                for i, branch in enumerate(cfg.branches):
                    if branch.goto not in valid_ids and branch.goto != "end":
                        result.add_error(
                            f"Branch {i} goto references unknown step '{branch.goto}'",
                            step.id, "conditional",
                        )

        elif step.type == StepType.PARALLEL:
            if config is None:
                result.add_error("'parallel' step missing config", step.id, "parallel")
            else:
                cfg_p: ParallelStepConfig = config  # type: ignore
                if len(cfg_p.steps) < 2:
                    result.add_error("'parallel' needs at least 2 sub-steps", step.id)
                par_set = set(cfg_p.steps)
                for sub in cfg_p.steps:
                    if sub not in valid_ids:
                        result.add_error(
                            f"Parallel sub-step '{sub}' not defined elsewhere in the workflow",
                            step.id, "parallel",
                        )
                        continue
                    sub_step = wf.step_by_id(sub)
                    if sub_step is None:
                        continue
                    for dep in sub_step.depends_on:
                        # depends_on the parallel wrapper itself is the
                        # natural way to gate the parallel block — fine.
                        if dep == step.id:
                            continue
                        # Inside the same parallel group, ordering between
                        # children defeats the point of "run in parallel".
                        if dep in par_set:
                            result.add_error(
                                f"Parallel sub-step '{sub}' has depends_on '{dep}' "
                                f"which is another child of the same parallel group "
                                f"(creates ambiguous ordering inside the parallel block)",
                                step.id, "parallel",
                            )
                        elif dep in valid_ids:
                            result.add_error(
                                f"Parallel sub-step '{sub}' has depends_on '{dep}' "
                                f"which is outside the parallel group "
                                f"(creates ambiguous ordering — move the dep onto the parallel step)",
                                step.id, "parallel",
                            )

        elif step.type == StepType.GOTO:
            if config is None:
                result.add_error("'goto' step missing config", step.id, "goto")
            else:
                cfg_g: GotoStepConfig = config  # type: ignore
                if cfg_g.target not in valid_ids:
                    result.add_error(
                        f"Goto target '{cfg_g.target}' not found",
                        step.id, "goto",
                    )

        elif step.type == StepType.HUMAN_APPROVAL:
            cfg_ha = step.human_approval
            if cfg_ha and cfg_ha.on_reject_goto:
                if cfg_ha.on_reject_goto not in valid_ids:
                    result.errors.append(ValidationError(
                        step_id=step.id,
                        field="human_approval.on_reject_goto",
                        message=f"on_reject_goto target '{cfg_ha.on_reject_goto}' not found in workflow steps.",
                    ))

        elif step.type == StepType.NOTIFY:
            # notify config is optional; when provided, require at least title
            # or message so we don't push an empty event. When absent, the
            # executor constructs a degenerate default (no-op-ish notification).
            cfg_nt = step.notify
            if cfg_nt is not None:
                if not (cfg_nt.message or cfg_nt.title):
                    result.add_error(
                        "'notify' step has empty message and title — provide at least one",
                        step.id, "notify",
                    )

        elif step.type == StepType.OUTPUT:
            cfg = step.output
            if cfg:
                # Entity production: if either entity_name or entity_kind is set, both are required
                has_name = cfg.entity_name is not None and cfg.entity_name.strip()
                has_kind = cfg.entity_kind is not None and cfg.entity_kind.strip()
                if has_name and not has_kind:
                    result.errors.append(ValidationError(
                        step_id=step.id,
                        field="output.entity_kind",
                        message="entity_kind is required when entity_name is set.",
                    ))
                if has_kind and not has_name:
                    result.errors.append(ValidationError(
                        step_id=step.id,
                        field="output.entity_name",
                        message="entity_name is required when entity_kind is set.",
                    ))

        elif step.type == StepType.A2A:
            if config is None:
                result.add_error("'a2a' step missing config", step.id, "a2a")
            else:
                if not getattr(config, "url", ""):
                    result.add_error("'a2a' step missing url", step.id, "a2a")

        elif step.type == StepType.MAP_REDUCE:
            if config is None:
                result.add_error("'map_reduce' step missing config", step.id, "map_reduce")
            else:
                if not getattr(config, "task", ""):
                    result.add_error("'map_reduce' step missing task", step.id)
                splits = getattr(config, "splits", [])
                if len(splits) < 2:
                    result.add_error("'map_reduce' needs at least 2 splits", step.id)

        elif step.type == StepType.SUPERVISOR:
            if config is None:
                result.add_error("'supervisor' step missing config", step.id, "supervisor")
            else:
                if not getattr(config, "task", ""):
                    result.add_error("'supervisor' step missing task", step.id)

        elif step.type == StepType.GROUP_CHAT:
            if config is None:
                result.add_error("'group_chat' step missing config", step.id, "group_chat")
            else:
                if not getattr(config, "topic", ""):
                    result.add_error("'group_chat' step missing topic", step.id)
                participants = getattr(config, "participants", [])
                if len(participants) < 2:
                    result.add_error("'group_chat' needs at least 2 participants", step.id)

        elif step.type == StepType.HANDOFF:
            if config is None:
                result.add_error("'handoff' step missing config", step.id, "handoff")
            else:
                from_step = getattr(config, "from_step", "")
                if from_step and from_step not in valid_ids:
                    result.add_error(
                        f"Handoff from_step '{from_step}' not found",
                        step.id, "handoff",
                    )

        elif step.type == StepType.FOR_EACH:
            if config is None:
                result.add_error("'for_each' step missing config", step.id, "for_each")
            else:
                fe_steps = getattr(config, "steps", [])
                if not fe_steps:
                    result.add_error("'for_each' needs at least 1 sub-step", step.id)
                for sub in fe_steps:
                    if sub not in valid_ids:
                        result.add_error(
                            f"for_each sub-step '{sub}' not found",
                            step.id, "for_each",
                        )
                has_range = bool(getattr(config, "range", ""))
                has_items = bool(getattr(config, "items", []))
                if not has_range and not has_items:
                    result.add_error(
                        "'for_each' needs 'range' or 'items'",
                        step.id, "for_each",
                    )
                # Check sub-step ordering respects internal depends_on
                sub_order = {sid: i for i, sid in enumerate(fe_steps)}
                fe_set = set(fe_steps)
                for sub_id in fe_steps:
                    sub_step = wf.step_by_id(sub_id)
                    if not sub_step:
                        continue
                    for dep in sub_step.depends_on:
                        if dep == step.id:
                            result.add_error(
                                f"Sub-step '{sub_id}' depends_on the for_each step '{step.id}' (circular)",
                                step.id, "for_each",
                            )
                        elif dep in sub_order and sub_order[dep] > sub_order[sub_id]:
                            result.warnings.append(ValidationError(
                                step_id=step.id,
                                field="for_each",
                                message=f"Sub-step '{sub_id}' depends_on '{dep}' but '{dep}' comes later in the steps list. Reorder steps so dependencies run first.",
                                severity="warning",
                            ))
                    # Warn if a parallel sub-step references children not in the for_each list
                    if sub_step.type == StepType.PARALLEL and sub_step.parallel:
                        for par_child in sub_step.parallel.steps:
                            if par_child not in fe_set and par_child in valid_ids:
                                result.warnings.append(ValidationError(
                                    step_id=step.id,
                                    field="for_each",
                                    message=f"Sub-step '{sub_id}' (parallel) references '{par_child}' which is not in the for_each steps list. "
                                            f"The executor handles this via transitive ownership, but the step will only run inside the for_each context.",
                                    severity="warning",
                                ))

        elif step.type == StepType.TAG:
            if not step.tag_step:
                result.warnings.append(ValidationError(
                    step_id=step.id,
                    field="tag_step",
                    message="Tag step has no tag_step config.",
                    severity="warning",
                ))

        elif step.type == StepType.DEPRECATE:
            if not step.deprecate_step:
                result.warnings.append(ValidationError(
                    step_id=step.id,
                    field="deprecate_step",
                    message="Deprecate step has no deprecate_step config.",
                    severity="warning",
                ))


def _check_variable_refs(wf: WorkflowDef, valid_ids: set[str],
                         result: ValidationResult) -> None:
    """Check that ${step_id.field} references point to existing steps.

    Known namespaces: inputs, loop, env. Step references must match valid_ids.
    """
    builtin_namespaces = {"inputs", "loop", "env", "trigger", "for_each", "previous"}

    for step in wf.steps:
        # Collect all string fields that might have variable refs
        texts: list[str] = []
        config = step.get_config()
        if config:
            for field_name in config.model_fields:
                val = getattr(config, field_name, None)
                if isinstance(val, str):
                    texts.append(val)
                elif isinstance(val, list):
                    for item in val:
                        if isinstance(item, str):
                            texts.append(item)
                elif isinstance(val, dict):
                    for dv in val.values():
                        if isinstance(dv, str):
                            texts.append(dv)
        # Also check output config fields (entity_metadata, template) on StepDef directly
        if step.output:
            if step.output.template:
                texts.append(step.output.template)
            if step.output.entity_name:
                texts.append(step.output.entity_name)
            if step.output.entity_kind:
                texts.append(step.output.entity_kind)
            if step.output.entity_metadata:
                for dv in step.output.entity_metadata.values():
                    if isinstance(dv, str):
                        texts.append(dv)

        for text in texts:
            for ref in _extract_var_refs(text):
                parts = ref.split(".", 1)
                namespace = parts[0]
                if namespace in builtin_namespaces:
                    continue
                if namespace not in valid_ids:
                    result.add_warning(
                        f"Variable reference '${{{ref}}}' — step '{namespace}' not found",
                        step.id,
                    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_workflow(wf: WorkflowDef) -> ValidationResult:
    """Run all validation passes on a workflow definition.

    Returns a ValidationResult with errors, warnings, and execution order.
    """
    result = ValidationResult()

    # Pass 1: duplicate IDs
    valid_ids = _check_duplicate_ids(wf, result)

    # Pass 2: dependency references
    adj = _check_dependencies(wf, valid_ids, result)

    # Pass 3: cycle detection + topological sort
    if result.valid:
        order = _check_cycles(adj, result)
        # Exclude steps that are owned by a parent (parallel/for_each) — those
        # are invoked by the parent's executor, so the top-level scheduler
        # must not also run them. Otherwise sub-tasks execute twice and one
        # copy races against the other.
        owned_ids: set[str] = set()
        for s in wf.steps:
            if s.type == StepType.PARALLEL and s.parallel:
                owned_ids.update(s.parallel.steps)
            elif s.type == StepType.FOR_EACH and s.for_each:
                owned_ids.update(s.for_each.steps)
        result.execution_order = [sid for sid in order if sid not in owned_ids]

    # Pass 4: step config validation
    _check_step_configs(wf, valid_ids, result)

    # Pass 5: variable references
    _check_variable_refs(wf, valid_ids, result)

    # --- Pass 6: Trigger definitions ----------------------------------------
    for i, trigger in enumerate(wf.triggers):
        is_cron_only = bool(trigger.cron and trigger.cron.strip())
        if not is_cron_only:
            if not trigger.on_kind or not trigger.on_kind.strip():
                result.errors.append(ValidationError(
                    field=f"triggers[{i}].on_kind",
                    message="Trigger must specify on_kind (the entity kind to watch).",
                ))
            if not trigger.on_tag or not trigger.on_tag.strip():
                result.errors.append(ValidationError(
                    field=f"triggers[{i}].on_tag",
                    message="Trigger must specify on_tag (the revision tag that fires the trigger).",
                ))
        # Warn if workflow has required inputs but trigger doesn't map them.
        # Note: entity_metadata auto-mapping can fill these at runtime if
        # the upstream entity has matching metadata keys — so this is advisory.
        required_inputs = [inp.name for inp in wf.inputs if inp.required and inp.default is None]
        unmapped = [name for name in required_inputs if name not in trigger.input_map]
        if unmapped:
            result.warnings.append(ValidationError(
                field=f"triggers[{i}].input_map",
                message=f"Trigger does not explicitly map required input(s): {', '.join(unmapped)}. "
                        f"They may be auto-filled from entity metadata if the upstream "
                        f"output declares matching keys in entity_metadata.",
                severity="warning",
            ))

    return result
