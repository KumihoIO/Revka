"""MCP tool handlers for the declarative workflow engine.

Tools:
  - run_workflow: Execute a named workflow with inputs
  - get_workflow_status: Check status of a running/completed workflow
  - list_workflows: List available workflow definitions
  - cancel_workflow: Cancel a running workflow
  - validate_workflow: Validate a workflow definition
  - create_workflow: Create a new workflow from YAML or dict
"""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

from .._log import _log
from ..failure_classification import classified_error, VALIDATION_ERROR
from ..gateway_client import RevkaGatewayClient

_gateway: RevkaGatewayClient | None = None

# Background tasks for non-blocking workflow execution.
# Maps run_id -> asyncio.Task so we can monitor/cancel them.
_BACKGROUND_TASKS: dict[str, asyncio.Task] = {}


def set_gateway_client(gw: RevkaGatewayClient) -> None:
    global _gateway
    _gateway = gw


# ---------------------------------------------------------------------------
# run_workflow
# ---------------------------------------------------------------------------

async def tool_run_workflow(args: dict[str, Any]) -> dict[str, Any]:
    """Execute a declarative workflow.

    Args:
        workflow: Workflow name (resolved from disk dirs then Kumiho artifacts).
        inputs: Dict of input parameters for the workflow.
        cwd: Working directory for agent/shell steps (required).
        run_id: Optional run ID (generated if omitted).
        target_step_id: Optional step id for "run to here" — when set, only
            the transitive ancestor closure of this step (plus the step
            itself) is executed. Unknown ids return a classified
            `unknown_target_step` validation error.
    """
    from ..workflow.loader import load_workflow_from_dict
    from ..workflow.executor import execute_workflow

    workflow_name = args.get("workflow", "")
    workflow_def = args.get("workflow_def")  # Inline definition as dict
    inputs = args.get("inputs", {})
    cwd = args.get("cwd", "")
    run_id = args.get("run_id", str(uuid.uuid4()))
    target_step_id = args.get("target_step_id") or None

    # Empty cwd → fall back to the user's home directory. Mirrors what the
    # event listener does at `_async_run_request` (metadata.cwd or self._cwd).
    # The gateway's /api/workflows/run/{name} also leaves cwd empty when the
    # web client's Execute button doesn't supply one — without this fallback
    # the direct dispatch from gateway short-circuits with `missing_cwd` and
    # the run only starts after the 30s listener poll.
    if not cwd:
        cwd = os.path.expanduser("~")

    cwd = os.path.realpath(os.path.expanduser(cwd))
    if not os.path.isdir(cwd):
        return classified_error(f"Directory not found: {cwd}", code="bad_cwd", category=VALIDATION_ERROR)

    # Load workflow
    wf = None
    if workflow_def and isinstance(workflow_def, dict):
        try:
            wf = load_workflow_from_dict(workflow_def)
        except Exception as exc:
            return classified_error(f"Invalid workflow definition: {exc}", code="invalid_def", category=VALIDATION_ERROR)
    elif workflow_name:
        from ..workflow.loader import resolve_workflow
        resolved = await resolve_workflow(workflow_name, project_dir=cwd)
        if not resolved:
            from ..workflow.loader import discover_workflows
            available = list(discover_workflows(cwd).keys())
            return classified_error(
                f"Workflow '{workflow_name}' not found (checked disk and Kumiho)",
                code="not_found", category=VALIDATION_ERROR,
            )
        wf, workflow_item_kref, workflow_revision_kref = resolved
    else:
        return classified_error(
            "Either 'workflow' name or 'workflow_def' dict required",
            code="missing_workflow", category=VALIDATION_ERROR,
        )

    # workflow_def path (ad-hoc dict) carries no Kumiho pin.
    if workflow_def and isinstance(workflow_def, dict):
        workflow_item_kref = ""
        workflow_revision_kref = ""

    # Validate required inputs
    for inp in wf.inputs:
        if inp.required and inp.name not in inputs and inp.default is None:
            return classified_error(
                f"Missing required input: '{inp.name}'",
                code="missing_input", category=VALIDATION_ERROR,
            )
        if inp.name not in inputs and inp.default is not None:
            inputs[inp.name] = inp.default

    # Run-to-step: confirm the target id exists. Done after wf is loaded so
    # we can surface a clean classified error rather than letting the
    # executor silently no-op.
    if target_step_id and not wf.step_by_id(target_step_id):
        return classified_error(
            f"Unknown target step id: '{target_step_id}'",
            code="unknown_target_step", category=VALIDATION_ERROR,
        )

    # Cost guard
    max_cost_usd = args.get("max_cost_usd")

    # Execute as a background task — return immediately so the MCP call
    # does not block for the entire workflow duration (which can be minutes).
    _log(f"tool_run_workflow: starting '{wf.name}' run={run_id[:8]} (background)")

    async def _run_in_background() -> None:
        try:
            state = await execute_workflow(
                wf, inputs, cwd, run_id=run_id, max_cost_usd=max_cost_usd,
                workflow_item_kref=workflow_item_kref,
                workflow_revision_kref=workflow_revision_kref,
                target_step_id=target_step_id,
            )
            status = getattr(state.status, "value", str(state.status))
            if status == "failed":
                _log(
                    f"tool_run_workflow: background run={run_id[:8]} "
                    f"finished status=failed error={(state.error or '')[:500]}"
                )
            else:
                _log(
                    f"tool_run_workflow: background run={run_id[:8]} "
                    f"finished status={status}"
                )
        except Exception as exc:
            _log(f"tool_run_workflow: background run={run_id[:8]} FAILED: {exc}")
        finally:
            _BACKGROUND_TASKS.pop(run_id, None)

    task = asyncio.create_task(_run_in_background(), name=f"workflow-{run_id[:8]}")
    _BACKGROUND_TASKS[run_id] = task

    return {
        "run_id": run_id,
        "workflow": wf.name,
        "status": "started",
        "steps_total": len(wf.steps),
        "message": "Workflow started in background. Use get_workflow_status to poll for progress.",
    }


# ---------------------------------------------------------------------------
# get_workflow_status
# ---------------------------------------------------------------------------

async def tool_get_workflow_status(args: dict[str, Any]) -> dict[str, Any]:
    """Get status of a workflow run.

    Args:
        run_id: The workflow run ID (required).
        include_outputs: Whether to include step outputs (default False).
    """
    from ..workflow.executor import ACTIVE_WORKFLOWS, load_checkpoint, workflow_progress_snapshot

    run_id = args.get("run_id", "")
    include_outputs = args.get("include_outputs", False)

    if not run_id:
        return classified_error("run_id is required", code="missing_run_id", category=VALIDATION_ERROR)

    # Check active workflows first
    state = ACTIVE_WORKFLOWS.get(run_id)
    if not state:
        # Try loading from checkpoint
        state = load_checkpoint(run_id)

    if not state:
        return classified_error(
            f"Workflow run '{run_id}' not found",
            code="not_found", category=VALIDATION_ERROR,
        )

    progress = workflow_progress_snapshot(state)
    result: dict[str, Any] = {
        "run_id": state.run_id,
        "workflow": state.workflow_name,
        "status": state.status.value,
        "current_step": state.current_step,
        "started_at": state.started_at,
        "completed_at": state.completed_at,
        "error": state.error or None,
        "workflow_item_kref": state.workflow_item_kref,
        "workflow_revision_kref": state.workflow_revision_kref,
        "steps_completed": progress["top_level_steps_completed"],
        "steps_total": progress["top_level_steps_total"],
        "top_level_steps_completed": progress["top_level_steps_completed"],
        "top_level_steps_total": progress["top_level_steps_total"],
        "expanded_steps_completed": progress["expanded_steps_completed"],
        "steps": {},
    }
    for key in ("current_loop", "current_iteration", "current_loop_total", "current_step_instance"):
        if key in progress:
            result[key] = progress[key]

    for sid, sr in state.step_results.items():
        entry: dict[str, Any] = {
            "status": sr.status,
            "duration_s": sr.duration_s,
            "files": sr.files_touched,
            "agent_id": sr.agent_id,
            "agent_type": sr.agent_type,
            "role": sr.role,
            "action": sr.action,
        }
        if isinstance(sr.output_data, dict) and sr.output_data.get("template_name"):
            entry["template_name"] = sr.output_data.get("template_name")
        if include_outputs:
            entry["output"] = sr.output[:2000]
            entry["error"] = sr.error
            entry["input_data"] = sr.input_data
            entry["output_data"] = sr.output_data
            if isinstance(sr.output_data, dict) and sr.output_data.get("artifact_path"):
                entry["artifact_path"] = sr.output_data.get("artifact_path")
        result["steps"][sid] = entry

    return result


# ---------------------------------------------------------------------------
# list_workflows
# ---------------------------------------------------------------------------

async def tool_list_workflows(args: dict[str, Any]) -> dict[str, Any]:
    """List available workflow definitions.

    Args:
        cwd: Optional project directory to include project-local workflows.
        tag: Optional tag filter.
    """
    from ..workflow.loader import load_all_workflows, resolve_all_workflows, resolve_workflow

    cwd = args.get("cwd")
    tag = args.get("tag")

    # Disk workflows (always loaded with full details)
    disk_workflows = load_all_workflows(cwd)

    result = []
    seen_names: set[str] = set()
    for name, wf in sorted(disk_workflows.items()):
        if tag and tag not in wf.tags:
            continue
        result.append({
            "name": wf.name,
            "version": wf.version,
            "description": wf.description,
            "tags": wf.tags,
            "steps": len(wf.steps),
            "inputs": [i.name for i in wf.inputs],
            "source": "disk",
        })
        seen_names.add(wf.name)

    # Kumiho-registered workflows not already found on disk
    try:
        all_wfs = await resolve_all_workflows(cwd)
        for wf_name, info in sorted(all_wfs.items()):
            if wf_name in seen_names or info.get("source") != "kumiho":
                continue
            # Resolve full definition via artifact to get details
            resolved = await resolve_workflow(wf_name, project_dir=cwd)
            if resolved:
                wf, _item_kref, _rev_kref = resolved
                if tag and tag not in wf.tags:
                    continue
                result.append({
                    "name": wf.name,
                    "version": wf.version,
                    "description": wf.description,
                    "tags": wf.tags,
                    "steps": len(wf.steps),
                    "inputs": [i.name for i in wf.inputs],
                    "source": "kumiho",
                })
            else:
                # Artifact not loadable — still list the name
                result.append({
                    "name": wf_name,
                    "version": "?",
                    "description": "(registered in Kumiho, file not loadable)",
                    "tags": [],
                    "steps": 0,
                    "inputs": [],
                    "source": "kumiho",
                })
    except Exception as exc:
        _log(f"list_workflows: Kumiho augmentation failed (non-fatal): {exc}")

    return {"workflows": result, "count": len(result)}


# ---------------------------------------------------------------------------
# cancel_workflow
# ---------------------------------------------------------------------------

async def tool_cancel_workflow(args: dict[str, Any]) -> dict[str, Any]:
    """Cancel a running workflow.

    Sets ``state.cancel_requested = True`` — the executor's main loop and
    long-running step handlers (shell/python polls) read this flag at the
    next checkpoint, kill any owned subprocesses, and transition the run
    to ``WorkflowStatus.CANCELLED`` cleanly.

    Idempotent: calling on an already-cancelled or finished run returns a
    success-shaped response with ``cancelled=false`` and a reason, never
    an error.

    Args:
        run_id: The workflow run ID (required).
    """
    from ..workflow.executor import ACTIVE_WORKFLOWS, workflow_progress_snapshot
    from ..workflow.schema import WorkflowStatus

    run_id = args.get("run_id", "")
    if not run_id:
        return classified_error("run_id is required", code="missing_run_id", category=VALIDATION_ERROR)

    state = ACTIVE_WORKFLOWS.get(run_id)
    if not state:
        # Idempotent: not in active registry → either never existed or already
        # finished. Return a success-shaped response so the gateway can map
        # this to a 404 without classifying as an error.
        return {
            "cancelled": False,
            "run_id": run_id,
            "reason": "not_found_or_already_finished",
        }

    if state.status in (
        WorkflowStatus.COMPLETED,
        WorkflowStatus.FAILED,
        WorkflowStatus.CANCELLED,
    ):
        return {
            "cancelled": False,
            "run_id": run_id,
            "status": state.status.value,
            "reason": "already_terminal",
        }

    # Idempotent: if cancel was already requested but the executor hasn't
    # processed it yet, just confirm.
    state.cancel_requested = True
    _log(f"tool_cancel_workflow: cancel_requested set for run={run_id[:8]} (status={state.status.value})")

    progress = workflow_progress_snapshot(state)
    return {
        "cancelled": True,
        "run_id": run_id,
        "status": state.status.value,
        "steps_completed": progress["top_level_steps_completed"],
        "expanded_steps_completed": progress["expanded_steps_completed"],
    }


# ---------------------------------------------------------------------------
# validate_workflow
# ---------------------------------------------------------------------------

def _format_pydantic_errors(exc: Exception) -> list[dict[str, str]]:
    """Turn a Pydantic ValidationError (or other parse error) into a list
    of structured {message, severity, field} entries for the UI."""
    errors: list[dict[str, str]] = []
    pydantic_errors = getattr(exc, "errors", None)
    if callable(pydantic_errors):
        try:
            for e in pydantic_errors():
                loc = ".".join(str(p) for p in e.get("loc", ()))
                msg = e.get("msg", "validation error")
                entry: dict[str, str] = {
                    "message": f"{msg} at '{loc}'" if loc else msg,
                    "severity": "error",
                }
                if loc:
                    entry["field"] = loc
                errors.append(entry)
        except Exception:
            pass
    if not errors:
        errors.append({"message": str(exc), "severity": "error"})
    return errors


async def tool_validate_workflow(args: dict[str, Any]) -> dict[str, Any]:
    """Validate a workflow definition without executing it.

    Always returns a structured {valid, errors, warnings} result — never
    raises — so callers (Rust gateway dispatch/save handlers) can surface
    issues to the UI without swallowed exceptions.

    Args:
        workflow: Workflow name to validate (resolves from Kumiho/disk).
        workflow_def: Inline workflow definition dict.
        workflow_yaml: Raw YAML text to parse and validate.
        cwd: Optional project directory for discovery.
    """
    import yaml as _yaml

    from ..workflow.loader import load_workflow_from_dict, resolve_workflow
    from ..workflow.validator import validate_workflow as _validate

    workflow_name = args.get("workflow", "")
    workflow_def = args.get("workflow_def")
    workflow_yaml = args.get("workflow_yaml")
    cwd = args.get("cwd")

    wf = None

    if workflow_yaml and isinstance(workflow_yaml, str):
        try:
            data = _yaml.safe_load(workflow_yaml)
        except _yaml.YAMLError as exc:
            return {
                "valid": False,
                "errors": [{"message": f"YAML parse error: {exc}", "severity": "error"}],
                "warnings": [],
            }
        if not isinstance(data, dict):
            return {
                "valid": False,
                "errors": [{
                    "message": f"Expected YAML mapping at root, got {type(data).__name__}",
                    "severity": "error",
                }],
                "warnings": [],
            }
        try:
            wf = load_workflow_from_dict(data)
        except Exception as exc:
            return {
                "valid": False,
                "errors": _format_pydantic_errors(exc),
                "warnings": [],
            }
    elif workflow_def and isinstance(workflow_def, dict):
        try:
            wf = load_workflow_from_dict(workflow_def)
        except Exception as exc:
            return {
                "valid": False,
                "errors": _format_pydantic_errors(exc),
                "warnings": [],
            }
    elif workflow_name:
        try:
            resolved = await resolve_workflow(workflow_name, project_dir=cwd)
        except Exception as exc:
            # Stored YAML is corrupt (e.g. Pydantic schema violation). Surface
            # as structured errors instead of propagating — the dispatch path
            # depends on this never raising.
            return {
                "workflow": workflow_name,
                "valid": False,
                "errors": _format_pydantic_errors(exc),
                "warnings": [],
            }
        if not resolved:
            return {
                "workflow": workflow_name,
                "valid": False,
                "errors": [{"message": f"Workflow '{workflow_name}' not found (checked disk and Kumiho)", "severity": "error"}],
                "warnings": [],
            }
        wf, _item_kref, _rev_kref = resolved

    if not wf:
        return classified_error(
            "Either 'workflow', 'workflow_def', or 'workflow_yaml' required",
            code="missing_workflow", category=VALIDATION_ERROR,
        )

    vr = _validate(wf)
    return {
        "workflow": wf.name,
        **vr.to_dict(),
    }


# ---------------------------------------------------------------------------
# create_workflow
# ---------------------------------------------------------------------------

async def tool_create_workflow(args: dict[str, Any]) -> dict[str, Any]:
    """Create a new workflow definition and save it as YAML.

    Args:
        workflow_def: Workflow definition as dict (required).
        directory: Optional save directory (defaults to ~/.revka/workflows/).
    """
    from ..workflow.loader import load_workflow_from_dict, save_workflow_yaml
    from ..workflow.validator import validate_workflow as _validate

    workflow_def = args.get("workflow_def")
    directory = args.get("directory")

    if not workflow_def or not isinstance(workflow_def, dict):
        return classified_error(
            "workflow_def dict is required",
            code="missing_def", category=VALIDATION_ERROR,
        )

    try:
        wf = load_workflow_from_dict(workflow_def)
    except Exception as exc:
        return classified_error(
            f"Invalid workflow definition: {exc}",
            code="invalid_def", category=VALIDATION_ERROR,
        )

    # Validate
    vr = _validate(wf)
    if not vr.valid:
        return {
            "saved": False,
            "workflow": wf.name,
            **vr.to_dict(),
        }

    # Save to disk
    path = save_workflow_yaml(wf, directory)

    # Sync to gateway REST API so the dashboard can see it
    registered = False
    if _gateway:
        try:
            with open(path) as f:
                yaml_text = f.read()
            kref = await _gateway.register_workflow(
                name=wf.name,
                description=wf.description,
                definition_yaml=yaml_text,
                version=wf.version,
                tags=wf.tags or None,
            )
            registered = bool(kref)
        except Exception as exc:
            _log(f"workflow_create: gateway sync failed (non-fatal): {exc}")

    return {
        "saved": True,
        "workflow": wf.name,
        "path": path,
        "steps": len(wf.steps),
        "valid": True,
        "registered": registered,
    }


# ---------------------------------------------------------------------------
# resume_workflow (for human_approval pauses)
# ---------------------------------------------------------------------------

async def tool_resume_workflow(args: dict[str, Any]) -> dict[str, Any]:
    """Resume a paused workflow (e.g. after human approval or human input).

    Args:
        run_id: The paused workflow run ID (required).
        approved: Whether the human approved (default True). For human_input steps, True means the response is accepted.
        response: Freeform text response for human_input steps. Becomes the step's output for downstream interpolation.
        cwd: Working directory (required if resuming).
    """
    from ..workflow.executor import ACTIVE_WORKFLOWS, execute_workflow, load_checkpoint
    from ..workflow.loader import resolve_workflow
    from ..workflow.schema import WorkflowStatus

    run_id = args.get("run_id", "")
    approved = args.get("approved", True)
    response_text = args.get("response", "")
    cwd = args.get("cwd", "")

    if not run_id:
        return classified_error("run_id is required", code="missing_run_id", category=VALIDATION_ERROR)

    state = ACTIVE_WORKFLOWS.get(run_id) or load_checkpoint(run_id)
    if not state:
        return classified_error(f"Run '{run_id}' not found", code="not_found", category=VALIDATION_ERROR)

    if state.status != WorkflowStatus.PAUSED:
        return {"run_id": run_id, "status": state.status.value, "message": "Workflow not paused"}

    # Re-load the workflow definition (needed for both approval and rejection paths).
    # Keep the resumed run pinned to its original revision if we have one in state —
    # otherwise fall back to whatever resolve returns now.
    resolved = await resolve_workflow(state.workflow_name, project_dir=cwd or None)
    if not resolved:
        return classified_error(f"Cannot reload workflow '{state.workflow_name}' (checked disk and Kumiho)", code="reload_failed", category=VALIDATION_ERROR)
    wf, _item_kref, _rev_kref = resolved

    if not approved:
        # Find the pending human_approval step to check for on_reject_goto
        from ..workflow.schema import HumanApprovalConfig, StepType
        reject_goto = ""
        reject_max = 3
        approval_step_id = ""
        for sid, sr in state.step_results.items():
            if sr.status == "pending" and sr.output_data.get("awaiting_approval"):
                approval_step_id = sid
                step_def = wf.step_by_id(sid)
                if step_def and step_def.type == StepType.HUMAN_APPROVAL:
                    cfg = step_def.human_approval or HumanApprovalConfig()
                    reject_goto = cfg.on_reject_goto
                    reject_max = cfg.on_reject_max
                break

        if not reject_goto:
            # No revision loop configured — cancel as before
            state.status = WorkflowStatus.CANCELLED
            ACTIVE_WORKFLOWS.pop(run_id, None)
            return {"run_id": run_id, "status": "cancelled", "message": "Human rejected"}

        # Validate that the reject target exists in the workflow
        execution_order = wf.step_ids()
        if reject_goto not in execution_order:
            _log(f"tool_resume_workflow: on_reject_goto='{reject_goto}' not found in workflow steps, cancelling")
            state.status = WorkflowStatus.CANCELLED
            ACTIVE_WORKFLOWS.pop(run_id, None)
            return {
                "run_id": run_id,
                "status": "cancelled",
                "message": f"Rejection target step '{reject_goto}' not found in workflow. Workflow cancelled.",
            }

        # Check rejection count
        reject_key = f"__reject_count__{approval_step_id}"
        reject_count = state.inputs.get(reject_key, 0) + 1
        if reject_count > reject_max:
            state.status = WorkflowStatus.CANCELLED
            ACTIVE_WORKFLOWS.pop(run_id, None)
            return {
                "run_id": run_id,
                "status": "cancelled",
                "message": f"Rejection limit reached ({reject_max}). Workflow cancelled.",
            }
        state.inputs[reject_key] = reject_count

        feedback = response_text or "Rejected without specific feedback."

        # Clear step results between reject_goto target and the approval step
        # so they re-execute with the feedback applied.
        if approval_step_id in execution_order:
            target_idx = execution_order.index(reject_goto)
            approval_idx = execution_order.index(approval_step_id)
            for clear_idx in range(target_idx, approval_idx + 1):
                clear_sid = execution_order[clear_idx]
                state.step_results.pop(clear_sid, None)
                # Also clear iteration-keyed results (for_each sub-steps)
                iter_keys = [k for k in state.step_results if k.startswith(f"{clear_sid}__iter_")]
                for ik in iter_keys:
                    state.step_results.pop(ik, None)

        # Store feedback so the target step's prompt can interpolate it
        # via ${rejection.feedback} and ${rejection.count}
        state.inputs["__rejection_feedback__"] = feedback
        state.inputs["__rejection_count__"] = reject_count

        _log(
            f"tool_resume_workflow: rejection loop run={run_id[:8]} "
            f"goto='{reject_goto}' attempt={reject_count}/{reject_max} "
            f"feedback={feedback[:100]}"
        )

        # Resume the workflow — cleared steps will re-execute
        state.status = WorkflowStatus.RUNNING
        state.error = ""
        # Fall through to the resume logic below

    else:
        # Approved — mark pending steps as completed.
        # When a human_approval step is inside a for_each loop, multiple copies
        # of the pending result exist (original key, iteration key, and the
        # for_each step itself).  Mark ALL pending approval/input steps so the
        # for_each can resume cleanly.
        for sid, sr in state.step_results.items():
            if sr.status != "pending":
                continue
            if sr.output_data.get("awaiting_approval"):
                # Don't mark the for_each wrapper as completed — it needs to
                # re-execute to continue its remaining iterations.  Only mark
                # the actual human_approval sub-step copies.
                if sr.output_data.get("paused_iteration"):
                    # This is the for_each wrapper's pending result — leave
                    # it as pending so the main loop re-enters for_each.
                    continue
                sr.status = "completed"
                sr.output = "Approved by human"
            elif sr.output_data.get("awaiting_input"):
                sr.status = "completed"
                sr.output = response_text or "No response provided"
                sr.output_data["human_response"] = response_text

    effective_cwd = cwd or "/tmp"

    # Resume as a background task — same non-blocking pattern as run_workflow.
    _log(f"tool_resume_workflow: resuming run={run_id[:8]} (background)")

    async def _resume_in_background() -> None:
        try:
            await execute_workflow(wf, state.inputs, effective_cwd, resume_state=state)
            _log(f"tool_resume_workflow: background run={run_id[:8]} finished")
        except Exception as exc:
            _log(f"tool_resume_workflow: background run={run_id[:8]} FAILED: {exc}")
        finally:
            _BACKGROUND_TASKS.pop(run_id, None)

    task = asyncio.create_task(_resume_in_background(), name=f"workflow-resume-{run_id[:8]}")
    _BACKGROUND_TASKS[run_id] = task

    return {
        "run_id": run_id,
        "status": "resumed",
        "message": "Workflow resumed in background. Use get_workflow_status to poll for progress.",
    }


# ---------------------------------------------------------------------------
# retry_workflow (for failed runs — resume from the failed step, keeping
# prior successful step outputs so only the failed step + downstream re-run)
# ---------------------------------------------------------------------------

async def tool_retry_workflow(args: dict[str, Any]) -> dict[str, Any]:
    """Retry a failed workflow from the first failed step.

    Successful step outputs are preserved so only the failed step and
    downstream steps re-execute.

    Args:
        run_id: The failed workflow run ID (required).
        cwd: Working directory for the retry (optional, falls back to /tmp).
    """
    from ..workflow.executor import ACTIVE_WORKFLOWS, execute_workflow, load_checkpoint, _save_checkpoint
    from ..workflow.loader import resolve_workflow
    from ..workflow.schema import WorkflowStatus

    run_id = args.get("run_id", "")
    cwd = args.get("cwd", "")

    if not run_id:
        return classified_error("run_id is required", code="missing_run_id", category=VALIDATION_ERROR)

    state = ACTIVE_WORKFLOWS.get(run_id) or load_checkpoint(run_id)
    if not state:
        return classified_error(f"Run '{run_id}' not found", code="not_found", category=VALIDATION_ERROR)

    if state.status != WorkflowStatus.FAILED:
        return {
            "run_id": run_id,
            "status": state.status.value,
            "message": f"Workflow is not failed (status={state.status.value}); retry only applies to failed runs",
        }

    resolved = await resolve_workflow(state.workflow_name, project_dir=cwd or None)
    if not resolved:
        return classified_error(
            f"Cannot reload workflow '{state.workflow_name}' (checked disk and Kumiho)",
            code="reload_failed",
            category=VALIDATION_ERROR,
        )
    wf, _item_kref, _rev_kref = resolved

    # Find the first failed step in execution order and clear its result +
    # everything downstream so the executor re-plans from there.
    execution_order = wf.step_ids()
    first_failed_idx: int | None = None
    for idx, sid in enumerate(execution_order):
        sr = state.step_results.get(sid)
        if sr and sr.status == "failed":
            first_failed_idx = idx
            break

    if first_failed_idx is None:
        # Defensive — status was FAILED but no step result reports failed.
        # Clear nothing; let the executor figure it out.
        _log(f"tool_retry_workflow: run={run_id[:8]} no failed step_result found, resuming as-is")
    else:
        for clear_idx in range(first_failed_idx, len(execution_order)):
            clear_sid = execution_order[clear_idx]
            state.step_results.pop(clear_sid, None)
            iter_keys = [k for k in state.step_results if k.startswith(f"{clear_sid}__iter_")]
            for ik in iter_keys:
                state.step_results.pop(ik, None)

    state.status = WorkflowStatus.RUNNING
    state.error = ""
    state.completed_at = None
    state.current_step = execution_order[first_failed_idx] if first_failed_idx is not None else None
    state.cancel_requested = False
    state.running_processes.clear()
    state.steps_total = len(wf.steps)
    ACTIVE_WORKFLOWS[run_id] = state

    if wf.checkpoint:
        try:
            _save_checkpoint(state)
        except Exception as exc:  # noqa: BLE001
            _log(f"tool_retry_workflow: checkpoint update failed for run={run_id[:8]}: {exc}")

    try:
        from ..workflow.memory import persist_workflow_run
        step_dicts = {sid: sr.model_dump() for sid, sr in state.step_results.items()}
        await persist_workflow_run(
            workflow_name=state.workflow_name,
            run_id=state.run_id,
            status=state.status.value,
            inputs=state.inputs,
            step_results=step_dicts,
            started_at=state.started_at,
            steps_total=len(wf.steps),
            workflow_item_kref=state.workflow_item_kref,
            workflow_revision_kref=state.workflow_revision_kref,
        )
    except Exception as exc:  # noqa: BLE001
        _log(f"tool_retry_workflow: running-state persist failed for run={run_id[:8]}: {exc}")

    effective_cwd = cwd or "/tmp"
    _log(f"tool_retry_workflow: retrying run={run_id[:8]} (background)")

    async def _retry_in_background() -> None:
        try:
            await execute_workflow(wf, state.inputs, effective_cwd, resume_state=state)
            _log(f"tool_retry_workflow: background run={run_id[:8]} finished")
        except Exception as exc:
            _log(f"tool_retry_workflow: background run={run_id[:8]} FAILED: {exc}")
        finally:
            _BACKGROUND_TASKS.pop(run_id, None)

    task = asyncio.create_task(_retry_in_background(), name=f"workflow-retry-{run_id[:8]}")
    _BACKGROUND_TASKS[run_id] = task

    return {
        "run_id": run_id,
        "status": "retrying",
        "message": "Workflow retry started in background. Use get_workflow_status to poll for progress.",
    }
