"""revise_workflow tool — apply structured operations to a workflow definition,
emit a new Kumiho revision, return structured repair feedback for any ops the
LLM can't apply cleanly.

Kumiho is revision-native, not mutation-native: every change produces a NEW
revision via the existing register_workflow path (gateway POST /api/workflows
which creates a revision and tags it 'published'). We never edit in place.
"""
from __future__ import annotations

import os
import re
from enum import Enum
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover — yaml is a hard dep of operator-mcp
    yaml = None  # type: ignore[assignment]

from pydantic import BaseModel, Field, ValidationError as PydanticValidationError

from .._log import _log
from ..gateway_client import ConstructGatewayClient
from ..workflow.loader import load_workflow_from_dict
from ..workflow.schema import StepType, WorkflowDef
from ..workflow.validator import validate_workflow


# ---------------------------------------------------------------------------
# Op + result models
# ---------------------------------------------------------------------------

class RevisionOpType(str, Enum):
    ADD_STEP = "add_step"
    EDIT_STEP = "edit_step"
    DELETE_STEP = "delete_step"
    REORDER = "reorder"
    WIRE = "wire"            # add a depends_on between two steps
    UNWIRE = "unwire"        # remove one
    INSERT_INTO_PARALLEL = "insert_into_parallel"
    EXTRACT_FROM_PARALLEL = "extract_from_parallel"
    RENAME_STEP = "rename_step"


class RevisionOp(BaseModel):
    op: RevisionOpType
    step_id: str | None = None
    new_id: str | None = None                  # rename_step
    step_def: dict[str, Any] | None = None     # add_step, edit_step
    target_step_id: str | None = None          # wire, unwire
    parallel_id: str | None = None             # insert_into_parallel, extract_from_parallel
    position: int | None = None                # reorder
    position_after: str | None = None          # add_step


class SkippedReason(str, Enum):
    STEP_NOT_FOUND = "step_not_found"
    DUPLICATE_ID = "duplicate_id"
    INVALID_YAML = "invalid_yaml"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    CYCLE_DETECTED = "cycle_detected"
    UNKNOWN_STEP_TYPE = "unknown_step_type"
    REFERENCE_BROKEN = "reference_broken"
    INVALID_POSITION = "invalid_position"
    PARALLEL_NOT_FOUND = "parallel_not_found"
    VALIDATION_FAILED = "validation_failed"


class SkippedItem(BaseModel):
    op_index: int
    op: RevisionOpType
    reason: SkippedReason
    details: str
    target_step_id: str | None = None


class _OpError(Exception):
    """Internal — raised by helpers to abort a single op with a typed reason."""
    def __init__(self, reason: SkippedReason, details: str, target_step_id: str | None = None):
        self.reason = reason
        self.details = details
        self.target_step_id = target_step_id
        super().__init__(details)


# ---------------------------------------------------------------------------
# Field bodies that may contain ${...} references (for reference-broken scan
# and rename rewrite). Mirrors schema.py — keep in sync if new step types add
# text fields that take interpolation.
# ---------------------------------------------------------------------------

# Map step type → list of (config_block, field_path) tuples to scan.
# Each tuple: (top-level step key, field name within that block).
_INTERPOLATED_FIELDS: dict[str, list[tuple[str, str]]] = {
    "agent":          [("agent", "prompt")],
    "shell":          [("shell", "command"), ("shell", "shell")],
    "python":         [("python", "code")],
    "email":          [("email", "body"), ("email", "subject")],
    "conditional":    [],  # branch conditions handled separately below
    "output":         [("output", "template")],
    "notify":         [("notify", "message"), ("notify", "title")],
    "a2a":            [("a2a", "message")],
    "group_chat":     [("group_chat", "topic")],
    "supervisor":     [("supervisor", "task")],
}


# ${id.field} or ${id} — capture the leading id only.
_VAR_REF_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_-]*)(?:\.[^}]*)?\}")


def _exact_id_pattern(step_id: str) -> re.Pattern[str]:
    """Pattern that matches ${<step_id>} or ${<step_id>.field…} but NOT
    ${<step_id>-suffix.…}. Word-boundary guard via explicit char class."""
    return re.compile(r"\$\{" + re.escape(step_id) + r"(\.[^}]*)?\}")


# ---------------------------------------------------------------------------
# Helpers — operate on dict state (raw YAML dict) for ergonomic mutation
# ---------------------------------------------------------------------------

def _find_step(state: dict[str, Any], step_id: str) -> dict[str, Any] | None:
    for s in state.get("steps", []):
        if s.get("id") == step_id:
            return s
    return None


def _find_step_index(state: dict[str, Any], step_id: str) -> int:
    for i, s in enumerate(state.get("steps", [])):
        if s.get("id") == step_id:
            return i
    return -1


def _all_step_ids(state: dict[str, Any]) -> set[str]:
    return {s.get("id", "") for s in state.get("steps", []) if s.get("id")}


def _iter_text_field_holders(step: dict[str, Any]):
    """Yield (step_id, label, current_value, setter) for each text field in a
    step that supports ${...} interpolation. setter(new_val) writes back."""
    sid = step.get("id", "")
    # Conditional branches
    cond = step.get("conditional")
    if isinstance(cond, dict):
        branches = cond.get("branches") or []
        for bi, br in enumerate(branches):
            if isinstance(br, dict) and isinstance(br.get("condition"), str):
                def _set(new_val: str, _br=br):
                    _br["condition"] = new_val
                yield sid, f"conditional.branches[{bi}].condition", br["condition"], _set
    # Generic per-type fields
    for type_key, fields in _INTERPOLATED_FIELDS.items():
        cfg = step.get(type_key)
        if not isinstance(cfg, dict):
            continue
        for _block, field_name in fields:
            val = cfg.get(field_name)
            if isinstance(val, str):
                def _set(new_val: str, _cfg=cfg, _fn=field_name):
                    _cfg[_fn] = new_val
                yield sid, f"{type_key}.{field_name}", val, _set


# ---------------------------------------------------------------------------
# Per-op apply functions — each mutates `state` in place or raises _OpError
# ---------------------------------------------------------------------------

def _apply_add_step(state: dict[str, Any], op: RevisionOp) -> None:
    if not isinstance(op.step_def, dict):
        raise _OpError(SkippedReason.MISSING_REQUIRED_FIELD,
                       "add_step requires step_def")
    new_step = dict(op.step_def)  # shallow copy
    new_id = new_step.get("id", "")
    if not new_id:
        raise _OpError(SkippedReason.MISSING_REQUIRED_FIELD,
                       "step_def missing 'id'")
    if new_id in _all_step_ids(state):
        raise _OpError(SkippedReason.DUPLICATE_ID,
                       f"step id '{new_id}' already exists",
                       target_step_id=new_id)
    steps = state.setdefault("steps", [])
    if op.position_after:
        idx = _find_step_index(state, op.position_after)
        if idx < 0:
            # position_after not found — append rather than fail (instructions
            # say "if known, insert after; otherwise append")
            steps.append(new_step)
        else:
            steps.insert(idx + 1, new_step)
    else:
        steps.append(new_step)


def _apply_edit_step(state: dict[str, Any], op: RevisionOp) -> None:
    if not op.step_id:
        raise _OpError(SkippedReason.MISSING_REQUIRED_FIELD,
                       "edit_step requires step_id")
    if not isinstance(op.step_def, dict):
        raise _OpError(SkippedReason.MISSING_REQUIRED_FIELD,
                       "edit_step requires step_def")
    step = _find_step(state, op.step_id)
    if step is None:
        raise _OpError(SkippedReason.STEP_NOT_FOUND,
                       f"step '{op.step_id}' not found",
                       target_step_id=op.step_id)
    # Disallow type mutation here.
    new_def = op.step_def
    if "type" in new_def and new_def["type"] != step.get("type") and step.get("type"):
        raise _OpError(SkippedReason.UNKNOWN_STEP_TYPE,
                       "edit_step cannot change a step's 'type' — use add+delete or rename instead",
                       target_step_id=op.step_id)
    # Merge — caller-provided fields override, but never change id
    for k, v in new_def.items():
        if k == "id":
            continue
        step[k] = v


def _apply_delete_step(state: dict[str, Any], op: RevisionOp) -> list[str]:
    """Delete a step. Returns the ID that was deleted (for downstream
    reference-broken scanning). Auto-removes depends_on refs and sub-step
    membership in parallel/for_each."""
    if not op.step_id:
        raise _OpError(SkippedReason.MISSING_REQUIRED_FIELD,
                       "delete_step requires step_id")
    idx = _find_step_index(state, op.step_id)
    if idx < 0:
        raise _OpError(SkippedReason.STEP_NOT_FOUND,
                       f"step '{op.step_id}' not found",
                       target_step_id=op.step_id)
    deleted_id = op.step_id
    state["steps"].pop(idx)
    # Auto-clean refs in remaining steps
    for s in state.get("steps", []):
        deps = s.get("depends_on")
        if isinstance(deps, list) and deleted_id in deps:
            s["depends_on"] = [d for d in deps if d != deleted_id]
        # Drop from parallel.steps
        par = s.get("parallel")
        if isinstance(par, dict):
            sub = par.get("steps")
            if isinstance(sub, list) and deleted_id in sub:
                par["steps"] = [x for x in sub if x != deleted_id]
        # Drop from for_each.steps
        fe = s.get("for_each")
        if isinstance(fe, dict):
            sub = fe.get("steps")
            if isinstance(sub, list) and deleted_id in sub:
                fe["steps"] = [x for x in sub if x != deleted_id]
    return [deleted_id]


def _apply_reorder(state: dict[str, Any], op: RevisionOp) -> None:
    if not op.step_id:
        raise _OpError(SkippedReason.MISSING_REQUIRED_FIELD,
                       "reorder requires step_id")
    if op.position is None:
        raise _OpError(SkippedReason.MISSING_REQUIRED_FIELD,
                       "reorder requires position")
    steps = state.get("steps", [])
    idx = _find_step_index(state, op.step_id)
    if idx < 0:
        raise _OpError(SkippedReason.STEP_NOT_FOUND,
                       f"step '{op.step_id}' not found",
                       target_step_id=op.step_id)
    pos = op.position
    if pos < 0 or pos >= len(steps):
        raise _OpError(SkippedReason.INVALID_POSITION,
                       f"position {pos} out of range [0, {len(steps) - 1}]",
                       target_step_id=op.step_id)
    s = steps.pop(idx)
    steps.insert(pos, s)


def _apply_wire(state: dict[str, Any], op: RevisionOp) -> None:
    if not op.step_id or not op.target_step_id:
        raise _OpError(SkippedReason.MISSING_REQUIRED_FIELD,
                       "wire requires step_id and target_step_id")
    step = _find_step(state, op.step_id)
    target = _find_step(state, op.target_step_id)
    if step is None:
        raise _OpError(SkippedReason.STEP_NOT_FOUND,
                       f"step '{op.step_id}' not found",
                       target_step_id=op.step_id)
    if target is None:
        raise _OpError(SkippedReason.STEP_NOT_FOUND,
                       f"target step '{op.target_step_id}' not found",
                       target_step_id=op.target_step_id)
    deps = step.setdefault("depends_on", [])
    if not isinstance(deps, list):
        raise _OpError(SkippedReason.MISSING_REQUIRED_FIELD,
                       f"step '{op.step_id}' has malformed depends_on")
    if op.target_step_id in deps:
        return  # idempotent — already wired
    # Cycle detection: would adding step → target create a cycle?
    deps_with = list(deps) + [op.target_step_id]
    if _has_cycle(state, op.step_id, deps_with):
        raise _OpError(SkippedReason.CYCLE_DETECTED,
                       f"adding depends_on '{op.target_step_id}' to '{op.step_id}' creates a cycle",
                       target_step_id=op.target_step_id)
    deps.append(op.target_step_id)


def _apply_unwire(state: dict[str, Any], op: RevisionOp) -> None:
    if not op.step_id or not op.target_step_id:
        raise _OpError(SkippedReason.MISSING_REQUIRED_FIELD,
                       "unwire requires step_id and target_step_id")
    step = _find_step(state, op.step_id)
    if step is None:
        raise _OpError(SkippedReason.STEP_NOT_FOUND,
                       f"step '{op.step_id}' not found",
                       target_step_id=op.step_id)
    deps = step.get("depends_on") or []
    if not isinstance(deps, list):
        return
    step["depends_on"] = [d for d in deps if d != op.target_step_id]


def _apply_insert_into_parallel(state: dict[str, Any], op: RevisionOp) -> None:
    if not op.step_id or not op.parallel_id:
        raise _OpError(SkippedReason.MISSING_REQUIRED_FIELD,
                       "insert_into_parallel requires step_id and parallel_id")
    if _find_step(state, op.step_id) is None:
        raise _OpError(SkippedReason.STEP_NOT_FOUND,
                       f"step '{op.step_id}' not found",
                       target_step_id=op.step_id)
    parent = _find_step(state, op.parallel_id)
    if parent is None:
        raise _OpError(SkippedReason.PARALLEL_NOT_FOUND,
                       f"parallel step '{op.parallel_id}' not found",
                       target_step_id=op.parallel_id)
    if parent.get("type") != StepType.PARALLEL.value:
        raise _OpError(SkippedReason.PARALLEL_NOT_FOUND,
                       f"step '{op.parallel_id}' is not a parallel step (type={parent.get('type')!r})",
                       target_step_id=op.parallel_id)
    par_cfg = parent.setdefault("parallel", {})
    sub = par_cfg.setdefault("steps", [])
    if not isinstance(sub, list):
        raise _OpError(SkippedReason.MISSING_REQUIRED_FIELD,
                       f"parallel '{op.parallel_id}' has malformed steps list")
    if op.step_id not in sub:
        sub.append(op.step_id)


def _apply_extract_from_parallel(state: dict[str, Any], op: RevisionOp) -> None:
    if not op.step_id:
        raise _OpError(SkippedReason.MISSING_REQUIRED_FIELD,
                       "extract_from_parallel requires step_id")
    # Find the parallel that contains this step (use parallel_id if given, else search)
    parents: list[dict[str, Any]] = []
    for s in state.get("steps", []):
        if s.get("type") == StepType.PARALLEL.value:
            par = s.get("parallel") or {}
            sub = par.get("steps") or []
            if op.step_id in sub:
                if op.parallel_id and s.get("id") != op.parallel_id:
                    continue
                parents.append(s)
    if not parents:
        raise _OpError(SkippedReason.PARALLEL_NOT_FOUND,
                       f"step '{op.step_id}' not found inside any parallel"
                       + (f" (parallel_id={op.parallel_id})" if op.parallel_id else ""),
                       target_step_id=op.step_id)
    for p in parents:
        par = p.get("parallel") or {}
        sub = par.get("steps") or []
        par["steps"] = [x for x in sub if x != op.step_id]


def _apply_rename_step(state: dict[str, Any], op: RevisionOp) -> None:
    if not op.step_id or not op.new_id:
        raise _OpError(SkippedReason.MISSING_REQUIRED_FIELD,
                       "rename_step requires step_id and new_id")
    if op.step_id == op.new_id:
        return
    step = _find_step(state, op.step_id)
    if step is None:
        raise _OpError(SkippedReason.STEP_NOT_FOUND,
                       f"step '{op.step_id}' not found",
                       target_step_id=op.step_id)
    if op.new_id in _all_step_ids(state):
        raise _OpError(SkippedReason.DUPLICATE_ID,
                       f"new_id '{op.new_id}' already exists",
                       target_step_id=op.new_id)
    old_id = op.step_id
    new_id = op.new_id
    step["id"] = new_id
    pat = _exact_id_pattern(old_id)
    # Update depends_on, parallel.steps, for_each.steps, and text fields
    for s in state.get("steps", []):
        deps = s.get("depends_on")
        if isinstance(deps, list):
            s["depends_on"] = [new_id if d == old_id else d for d in deps]
        par = s.get("parallel")
        if isinstance(par, dict):
            sub = par.get("steps")
            if isinstance(sub, list):
                par["steps"] = [new_id if x == old_id else x for x in sub]
        fe = s.get("for_each")
        if isinstance(fe, dict):
            sub = fe.get("steps")
            if isinstance(sub, list):
                fe["steps"] = [new_id if x == old_id else x for x in sub]
        # Rewrite ${old_id.…} → ${new_id.…} in interpolated text fields
        for _sid, _label, val, setter in _iter_text_field_holders(s):
            new_val = pat.sub(lambda m: "${" + new_id + (m.group(1) or "") + "}", val)
            if new_val != val:
                setter(new_val)


def _has_cycle(state: dict[str, Any], head_id: str, head_deps: list[str]) -> bool:
    """Simulate adding head_deps to head_id and detect a cycle."""
    adj: dict[str, set[str]] = {}
    for s in state.get("steps", []):
        sid = s.get("id", "")
        if not sid:
            continue
        if sid == head_id:
            adj[sid] = set(head_deps)
        else:
            deps = s.get("depends_on") or []
            adj[sid] = set(d for d in deps if isinstance(d, str))
    # DFS coloring
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {n: WHITE for n in adj}

    def dfs(node: str) -> bool:
        color[node] = GRAY
        for nxt in adj.get(node, set()):
            if nxt not in color:
                continue
            if color[nxt] == GRAY:
                return True
            if color[nxt] == WHITE and dfs(nxt):
                return True
        color[node] = BLACK
        return False

    for n in list(adj.keys()):
        if color[n] == WHITE and dfs(n):
            return True
    return False


_DISPATCH = {
    RevisionOpType.ADD_STEP: _apply_add_step,
    RevisionOpType.EDIT_STEP: _apply_edit_step,
    RevisionOpType.DELETE_STEP: _apply_delete_step,
    RevisionOpType.REORDER: _apply_reorder,
    RevisionOpType.WIRE: _apply_wire,
    RevisionOpType.UNWIRE: _apply_unwire,
    RevisionOpType.INSERT_INTO_PARALLEL: _apply_insert_into_parallel,
    RevisionOpType.EXTRACT_FROM_PARALLEL: _apply_extract_from_parallel,
    RevisionOpType.RENAME_STEP: _apply_rename_step,
}


# ---------------------------------------------------------------------------
# Reference-broken scan
# ---------------------------------------------------------------------------

def _scan_broken_refs(state: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Walk every interpolated text field, find ${id.…} references, and return
    a list of (containing_step_id, field_label, missing_id) for refs whose
    leading id does not resolve to a known step or builtin namespace."""
    builtins = {"inputs", "loop", "env", "trigger", "for_each", "previous"}
    valid_ids = _all_step_ids(state)
    out: list[tuple[str, str, str]] = []
    for s in state.get("steps", []):
        for sid, label, val, _setter in _iter_text_field_holders(s):
            for m in _VAR_REF_PATTERN.finditer(val):
                ref_id = m.group(1)
                if ref_id in builtins or ref_id in valid_ids:
                    continue
                out.append((sid, label, ref_id))
    return out


# ---------------------------------------------------------------------------
# YAML round-trip
# ---------------------------------------------------------------------------

def _serialize_yaml(state: dict[str, Any]) -> str:
    if yaml is None:
        raise RuntimeError("PyYAML not installed — cannot serialize workflow")
    return yaml.safe_dump(state, default_flow_style=False, sort_keys=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# Loading the current revision YAML for a workflow_kref
# ---------------------------------------------------------------------------

async def _load_current_yaml(workflow_kref: str) -> tuple[str, str]:
    """Given an item_kref, fetch the latest published revision and return
    (yaml_text, revision_kref). Raises RuntimeError on failure."""
    from ..operator_mcp import KUMIHO_SDK
    if not KUMIHO_SDK._available:
        raise RuntimeError("Kumiho SDK unavailable — cannot load workflow")

    rev = await KUMIHO_SDK.get_latest_revision(workflow_kref, tag="published")
    if not rev:
        raise RuntimeError(f"no published/latest revision for {workflow_kref}")
    revision_kref = rev.get("kref", "")
    if not revision_kref:
        raise RuntimeError(f"revision has no kref: {rev!r}")

    artifacts = await KUMIHO_SDK.get_artifacts(revision_kref)
    if not artifacts:
        raise RuntimeError(f"revision {revision_kref} has no artifacts")
    yaml_loc = ""
    for art in artifacts:
        loc = art.get("location", "")
        if loc.endswith((".yaml", ".yml")):
            yaml_loc = loc
            break
    if not yaml_loc:
        yaml_loc = artifacts[0].get("location", "")
    if not yaml_loc:
        raise RuntimeError(f"no artifact location for revision {revision_kref}")
    if yaml_loc.startswith("file://"):
        yaml_loc = yaml_loc[len("file://"):]
    yaml_path = os.path.expanduser(yaml_loc)
    if not os.path.isfile(yaml_path):
        raise RuntimeError(f"artifact path missing on disk: {yaml_path}")
    with open(yaml_path, "r") as f:
        return f.read(), revision_kref


# ---------------------------------------------------------------------------
# Main tool entry
# ---------------------------------------------------------------------------

async def tool_revise_workflow(
    args: dict[str, Any],
    _gw: ConstructGatewayClient | None = None,
) -> dict[str, Any]:
    """Apply structured operations to a workflow's current revision and emit a
    new Kumiho revision.

    Args:
        workflow_kref: kref of the workflow item.
        operations: list of RevisionOp dicts.
        rationale: optional human-readable why.
        workflow_yaml: (test-only) raw YAML to use instead of fetching from
            Kumiho. Lets unit tests exercise the core logic without a live
            gateway.

    Returns:
        {
          success: bool,
          new_revision_kref: str | None,
          applied_count: int,
          skipped_items: [SkippedItem...],
          errors: [str...],
        }
    """
    workflow_kref = str(args.get("workflow_kref", "") or "")
    raw_ops = args.get("operations") or []
    rationale = str(args.get("rationale", "") or "")
    inline_yaml = args.get("workflow_yaml")

    if not workflow_kref and not inline_yaml:
        return {
            "success": False,
            "new_revision_kref": None,
            "applied_count": 0,
            "skipped_items": [],
            "errors": ["workflow_kref is required"],
        }
    if not isinstance(raw_ops, list):
        return {
            "success": False,
            "new_revision_kref": None,
            "applied_count": 0,
            "skipped_items": [],
            "errors": ["operations must be a list"],
        }

    # Parse operations up front so type errors surface as outright failures
    # (these aren't "skip and continue" — the LLM sent malformed JSON).
    ops: list[RevisionOp] = []
    parse_errors: list[str] = []
    for i, raw in enumerate(raw_ops):
        try:
            ops.append(RevisionOp.model_validate(raw))
        except PydanticValidationError as exc:
            parse_errors.append(f"operations[{i}]: {exc}")
    if parse_errors:
        return {
            "success": False,
            "new_revision_kref": None,
            "applied_count": 0,
            "skipped_items": [],
            "errors": parse_errors,
        }

    # 1. Load current YAML
    if inline_yaml is not None:
        yaml_text = str(inline_yaml)
    else:
        try:
            yaml_text, _current_rev = await _load_current_yaml(workflow_kref)
        except Exception as exc:
            return {
                "success": False,
                "new_revision_kref": None,
                "applied_count": 0,
                "skipped_items": [],
                "errors": [f"load failed: {exc}"],
            }

    # 2. Parse to dict + Pydantic (validate baseline parses)
    if yaml is None:
        return {
            "success": False,
            "new_revision_kref": None,
            "applied_count": 0,
            "skipped_items": [],
            "errors": ["yaml module unavailable"],
        }
    try:
        state = yaml.safe_load(yaml_text)
    except Exception as exc:
        return {
            "success": False,
            "new_revision_kref": None,
            "applied_count": 0,
            "skipped_items": [],
            "errors": [f"current YAML failed to parse: {exc}"],
        }
    if not isinstance(state, dict):
        return {
            "success": False,
            "new_revision_kref": None,
            "applied_count": 0,
            "skipped_items": [],
            "errors": ["current workflow YAML root is not a dict"],
        }
    try:
        load_workflow_from_dict(state)
    except Exception as exc:
        return {
            "success": False,
            "new_revision_kref": None,
            "applied_count": 0,
            "skipped_items": [],
            "errors": [f"current workflow does not parse as WorkflowDef: {exc}"],
        }

    # 3. Apply ops in sequence; collect SkippedItems
    skipped: list[SkippedItem] = []
    applied_count = 0
    deleted_ids: list[str] = []
    for i, op in enumerate(ops):
        handler = _DISPATCH.get(op.op)
        if handler is None:
            skipped.append(SkippedItem(
                op_index=i, op=op.op,
                reason=SkippedReason.UNKNOWN_STEP_TYPE,
                details=f"unsupported op type {op.op!r}",
            ))
            continue
        try:
            ret = handler(state, op)
            if op.op == RevisionOpType.DELETE_STEP and isinstance(ret, list):
                deleted_ids.extend(ret)
            applied_count += 1
        except _OpError as e:
            skipped.append(SkippedItem(
                op_index=i, op=op.op,
                reason=e.reason, details=e.details,
                target_step_id=e.target_step_id,
            ))
        except Exception as e:
            # Defensive — should not happen
            skipped.append(SkippedItem(
                op_index=i, op=op.op,
                reason=SkippedReason.MISSING_REQUIRED_FIELD,
                details=f"unexpected error: {e}",
            ))

    # 4. Validate the resulting WorkflowDef
    parsed: WorkflowDef | None = None
    try:
        parsed = load_workflow_from_dict(state)
    except Exception as exc:
        skipped.append(SkippedItem(
            op_index=-1, op=RevisionOpType.EDIT_STEP,
            reason=SkippedReason.VALIDATION_FAILED,
            details=f"resulting workflow failed schema parse: {exc}",
        ))
        return {
            "success": False,
            "new_revision_kref": None,
            "applied_count": applied_count,
            "skipped_items": [s.model_dump() for s in skipped],
            "errors": [],
        }

    vr = validate_workflow(parsed)
    if not vr.valid:
        msgs = "; ".join(e.message for e in vr.errors)
        skipped.append(SkippedItem(
            op_index=-1, op=RevisionOpType.EDIT_STEP,
            reason=SkippedReason.VALIDATION_FAILED,
            details=f"validate_workflow rejected: {msgs}",
        ))
        return {
            "success": False,
            "new_revision_kref": None,
            "applied_count": applied_count,
            "skipped_items": [s.model_dump() for s in skipped],
            "errors": [],
        }

    # 5. Reference-broken scan — scan the FINAL state for ${id.…} refs that
    # don't resolve. Anchored at deleted ids when available, but report any
    # broken ref so the LLM can repair them with edit_step in a follow-up call.
    deleted_set = set(deleted_ids)
    for sid, label, missing_id in _scan_broken_refs(state):
        # Prefer the originating delete_step op_index when relevant; otherwise
        # use -1 to signal "post-apply scan".
        op_index = -1
        op_kind = RevisionOpType.DELETE_STEP if missing_id in deleted_set else RevisionOpType.EDIT_STEP
        skipped.append(SkippedItem(
            op_index=op_index, op=op_kind,
            reason=SkippedReason.REFERENCE_BROKEN,
            details=f"step '{sid}' field {label} references unknown id '{missing_id}'",
            target_step_id=missing_id,
        ))

    # 6. Re-serialize and persist
    try:
        new_yaml = _serialize_yaml(state)
    except Exception as exc:
        return {
            "success": False,
            "new_revision_kref": None,
            "applied_count": applied_count,
            "skipped_items": [s.model_dump() for s in skipped],
            "errors": [f"serialize failed: {exc}"],
        }

    gw = _gw
    if gw is None:
        gw = ConstructGatewayClient()

    rationale_tags = list(parsed.tags or [])
    if rationale and "rationale:" not in " ".join(rationale_tags):
        # Tag list is the only metadata channel register_workflow exposes.
        # Keep it short — a single rationale tag with a truncated rationale.
        short = rationale[:80].replace("\n", " ").strip()
        if short:
            rationale_tags.append(f"rationale:{short}")

    item_kref: str | None = None
    try:
        item_kref = await gw.register_workflow(
            name=parsed.name,
            description=parsed.description or "",
            definition_yaml=new_yaml,
            version=parsed.version or "1.0",
            tags=rationale_tags or None,
        )
    except Exception as exc:
        _log(f"revise_workflow: register_workflow raised: {exc}")
        return {
            "success": False,
            "new_revision_kref": None,
            "applied_count": applied_count,
            "skipped_items": [s.model_dump() for s in skipped],
            "errors": [f"register_workflow raised: {exc}"],
        }
    if not item_kref:
        return {
            "success": False,
            "new_revision_kref": None,
            "applied_count": applied_count,
            "skipped_items": [s.model_dump() for s in skipped],
            "errors": ["gateway register_workflow returned no kref"],
        }

    # 7. Fetch the new revision_kref via Kumiho SDK (gateway response only
    # carries the item_kref).
    new_revision_kref: str | None = None
    try:
        from ..operator_mcp import KUMIHO_SDK
        if KUMIHO_SDK._available:
            rev = await KUMIHO_SDK.get_latest_revision(item_kref, tag="published")
            if rev:
                new_revision_kref = rev.get("kref") or None
    except Exception as exc:
        _log(f"revise_workflow: post-publish revision lookup failed: {exc}")

    return {
        "success": True,
        "new_revision_kref": new_revision_kref,
        "applied_count": applied_count,
        "skipped_items": [s.model_dump() for s in skipped],
        "errors": [],
    }
