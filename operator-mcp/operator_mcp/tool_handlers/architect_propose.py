"""propose_workflow_yaml — validate a YAML proposal without persisting.

The Architect (LLM) constructs the proposed YAML and calls this tool.
The tool validates against the schema and echoes back a structured response.
Persistence is the editor frontend's job (and ultimately the user's, via Save).

NEVER persists to disk or Kumiho. Pure proposal/validation.
"""
from __future__ import annotations

import io
from typing import Any, Optional

import yaml as _yaml
from pydantic import BaseModel

from ..workflow.loader import load_workflow_from_dict
from ..workflow.validator import validate_workflow as _validate


class ProposalRequest(BaseModel):
    """Schema for the `propose_workflow_yaml` tool input."""

    proposed_yaml: str
    intent_summary: str = ""
    base_yaml: Optional[str] = None


def _format_pydantic_errors(exc: Exception) -> list[dict[str, str]]:
    """Pydantic ValidationError → structured list. Mirrors workflows.py.

    Adds `path` (dot-notation) so the frontend can highlight the offending
    field; keeps `message` and `severity` for the existing error renderer.
    """
    out: list[dict[str, str]] = []
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
                    entry["path"] = loc
                out.append(entry)
        except Exception:
            pass
    if not out:
        out.append({"message": str(exc), "severity": "error"})
    return out


def _step_ids_from_yaml(text: str) -> list[str]:
    """Best-effort step-ID extraction from raw YAML.

    Used for the diff fields (added/modified/removed). Returns an empty
    list if the YAML can't be parsed — the diff is informational so we
    silently degrade rather than fail the proposal.
    """
    try:
        data = _yaml.safe_load(text)
    except _yaml.YAMLError:
        return []
    if not isinstance(data, dict):
        return []
    steps = data.get("steps")
    if not isinstance(steps, list):
        return []
    ids: list[str] = []
    for step in steps:
        if isinstance(step, dict):
            sid = step.get("id")
            if isinstance(sid, str) and sid:
                ids.append(sid)
    return ids


def _step_blobs_from_yaml(text: str) -> dict[str, Any]:
    """{step_id: step_dict} so we can detect modified steps via equality."""
    try:
        data = _yaml.safe_load(text)
    except _yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    steps = data.get("steps")
    if not isinstance(steps, list):
        return {}
    out: dict[str, Any] = {}
    for step in steps:
        if isinstance(step, dict):
            sid = step.get("id")
            if isinstance(sid, str) and sid:
                out[sid] = step
    return out


def _canonical_yaml(wf_dict: dict[str, Any]) -> str:
    """Re-serialize to canonical YAML.

    `allow_unicode=True` so Korean / non-ASCII round-trips correctly
    (matches the loader.py UTF-8 fix). `sort_keys=False` preserves the
    Architect's authoring order; `default_flow_style=False` keeps the
    block style users expect.
    """
    buf = io.StringIO()
    _yaml.safe_dump(
        wf_dict,
        buf,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
    return buf.getvalue()


async def tool_propose_workflow_yaml(args: dict[str, Any]) -> dict[str, Any]:
    """Validate a proposed workflow YAML. Never persists.

    Returns:
        {
          yaml: str,                 # echoed-back YAML, normalized
          summary: str,              # echo of intent_summary
          valid: bool,               # passed schema validation?
          errors: [{message, path?, severity}],
          warnings: [{message, path?}],
          added_step_ids:    [str],  # in proposed but not base
          modified_step_ids: [str],  # in both, with config differences
          removed_step_ids:  [str],  # in base but not proposed
        }
    """
    try:
        req = ProposalRequest(**args)
    except Exception as exc:
        return {
            "yaml": "",
            "summary": str(args.get("intent_summary", "")),
            "valid": False,
            "errors": _format_pydantic_errors(exc),
            "warnings": [],
            "added_step_ids": [],
            "modified_step_ids": [],
            "removed_step_ids": [],
        }

    summary = req.intent_summary
    proposed = req.proposed_yaml
    base = req.base_yaml or ""

    # 1. YAML parse
    try:
        parsed = _yaml.safe_load(proposed)
    except _yaml.YAMLError as exc:
        return {
            "yaml": proposed,
            "summary": summary,
            "valid": False,
            "errors": [{"message": f"YAML parse error: {exc}", "severity": "error"}],
            "warnings": [],
            "added_step_ids": [],
            "modified_step_ids": [],
            "removed_step_ids": [],
        }

    if not isinstance(parsed, dict):
        return {
            "yaml": proposed,
            "summary": summary,
            "valid": False,
            "errors": [{
                "message": f"Expected YAML mapping at root, got {type(parsed).__name__}",
                "severity": "error",
            }],
            "warnings": [],
            "added_step_ids": [],
            "modified_step_ids": [],
            "removed_step_ids": [],
        }

    # 2. Schema (Pydantic) validation
    try:
        wf = load_workflow_from_dict(parsed)
    except Exception as exc:
        return {
            "yaml": proposed,
            "summary": summary,
            "valid": False,
            "errors": _format_pydantic_errors(exc),
            "warnings": [],
            "added_step_ids": [],
            "modified_step_ids": [],
            "removed_step_ids": [],
        }

    # 3. Workflow-level validation (cycles, refs, etc.)
    vr = _validate(wf)

    # 4. Diff against base_yaml, if provided
    added_ids: list[str] = []
    modified_ids: list[str] = []
    removed_ids: list[str] = []
    if base.strip():
        base_blobs = _step_blobs_from_yaml(base)
        proposed_blobs = _step_blobs_from_yaml(proposed)
        base_ids = set(base_blobs.keys())
        proposed_ids = set(proposed_blobs.keys())
        added_ids = sorted(proposed_ids - base_ids)
        removed_ids = sorted(base_ids - proposed_ids)
        for sid in sorted(base_ids & proposed_ids):
            if base_blobs[sid] != proposed_blobs[sid]:
                modified_ids.append(sid)

    # 5. Re-serialize from the parsed dict (preserves authoring order, drops
    #    accidental whitespace). We do NOT serialize from the WorkflowDef —
    #    that would lose fields the Pydantic model dropped silently.
    try:
        canonical = _canonical_yaml(parsed)
    except Exception:
        canonical = proposed

    return {
        "yaml": canonical,
        "summary": summary,
        "valid": vr.valid,
        "errors": [e.to_dict() for e in vr.errors],
        "warnings": [w.to_dict() for w in vr.warnings],
        "added_step_ids": added_ids,
        "modified_step_ids": modified_ids,
        "removed_step_ids": removed_ids,
    }
