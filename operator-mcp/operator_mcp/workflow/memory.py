"""Workflow memory — persist workflow runs to Kumiho graph.

Stores workflow executions in /Revka/WorkflowRuns space so that:
  - Future workflows can recall what prior runs produced
  - Agents can query workflow history for context
  - Cross-workflow variable sharing works via krefs

Structure:
  /Revka/WorkflowRuns/<workflow_name>-<run_id>
    revision metadata: status, inputs, step_results, timestamps
    edges: PRODUCED_BY (step → agent), DEPENDS_ON (run → prior run)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Callable

from .._log import _log
from ..artifact_summary import summarize_artifact_metadata
from ..revka_config import harness_project


# ---------------------------------------------------------------------------
# Persistence sanitization
# ---------------------------------------------------------------------------

# Per-step persistence caps. The Kumiho metadata budget is the binding
# constraint; we trade per-step detail for the ability to persist the whole
# run. Values were picked to keep a 20-step run under ~300KB total metadata.
_PER_STRING_CAP = 4000        # any single string field in input_data/output_data
_PER_STEP_JSON_CAP = 16_000   # full serialized step entry, post-truncation
_PERSIST_OP_TIMEOUT_SECS = 15.0
_PERSIST_ARTIFACT_TIMEOUT_SECS = 5.0
_TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}

# Patterns we mask before persistence. Conservative — better to over-mask
# than leak. We match `key=value` and `key: value` where key looks like a
# secret name, plus the special-case `Bearer <token>` form used in HTTP
# Authorization headers. The actual secret value is replaced with "***"
# so length inspection still works for debugging.
_REDACT_KEY_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
    r"authorization|auth[_-]?token|client[_-]?secret|private[_-]?key)"
    r"\s*[:=]\s*([^\s,&;'\"]+)"
)
# `Bearer <token>` — Authorization header convention. Also catches a few
# variants that happen to use the same prefix syntax.
_REDACT_BEARER_RE = re.compile(r"(?i)\b(bearer)\s+([A-Za-z0-9._\-+/=]+)")


def _timeout_from_env(name: str, default: float) -> float:
    raw = os.getenv(name, "")
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(0.0, value)


async def _kumiho_with_timeout(
    awaitable: Any,
    operation: str,
    *,
    run_id: str = "",
    timeout: float | None = None,
) -> Any:
    """Bound a Kumiho persistence call so best-effort persistence stays best-effort."""
    timeout_s = (
        _timeout_from_env("REVKA_WORKFLOW_MEMORY_TIMEOUT_SECS", _PERSIST_OP_TIMEOUT_SECS)
        if timeout is None
        else timeout
    )
    if timeout_s <= 0:
        return await awaitable
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout_s)
    except asyncio.TimeoutError as exc:
        prefix = f"run={run_id[:8]} " if run_id else ""
        raise TimeoutError(f"{prefix}{operation} timed out after {timeout_s:g}s") from exc


def _redact_for_persistence(value: Any) -> Any:
    """Walk a JSON-able value and mask obvious secret patterns in strings.

    Scope: defends against secrets that get interpolated into `command:`,
    email bodies, or python args. Does NOT replace explicit auth-profile
    binding (those tokens never enter input_data/output_data — they go
    through env vars).
    """
    if isinstance(value, str):
        # Apply Bearer first so the `authorization: Bearer xyz` form gets
        # the token masked (not just the literal "Bearer" word). The
        # subsequent key=value pass then redacts any leftover key:value
        # pairs that didn't fit the Bearer shape.
        out = _REDACT_BEARER_RE.sub(lambda m: f"{m.group(1)} ***", value)
        out = _REDACT_KEY_RE.sub(lambda m: f"{m.group(1)}=***", out)
        return out
    if isinstance(value, dict):
        return {k: _redact_for_persistence(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_for_persistence(v) for v in value]
    return value


def _coerce_jsonable(value: Any) -> Any:
    """Replace non-JSON-serializable values with their repr.

    Belt-and-suspenders: input_data/output_data should already be plain
    dict/list/str/int/float/bool/None, but external tool responses
    occasionally leak in (datetime, bytes, custom classes).
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _coerce_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_coerce_jsonable(v) for v in value]
    return repr(value)


def _truncate_strings_in_place(value: Any, cap: int = _PER_STRING_CAP) -> tuple[Any, bool]:
    """Recursively truncate any string longer than ``cap``.

    Returns ``(new_value, truncated)`` — the second element is True if any
    string was shortened, so the caller can mark the step entry as
    truncated for downstream UI consumers.
    """
    truncated = False
    if isinstance(value, str):
        if len(value) > cap:
            return value[:cap], True
        return value, False
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            new_v, t = _truncate_strings_in_place(v, cap)
            truncated = truncated or t
            out[k] = new_v
        return out, truncated
    if isinstance(value, list):
        out_list = []
        for v in value:
            new_v, t = _truncate_strings_in_place(v, cap)
            truncated = truncated or t
            out_list.append(new_v)
        return out_list, truncated
    return value, False


def _prepare_for_persistence(value: Any) -> tuple[Any, bool]:
    """Run the full persistence pipeline: coerce → redact → truncate.

    Returns ``(prepared_value, truncated_flag)``.
    """
    coerced = _coerce_jsonable(value)
    redacted = _redact_for_persistence(coerced)
    return _truncate_strings_in_place(redacted, _PER_STRING_CAP)


# ---------------------------------------------------------------------------
# Space path
# ---------------------------------------------------------------------------

_SPACE = "WorkflowRuns"


def _project() -> str:
    return harness_project()


def _space_path() -> str:
    return f"/{_project()}/{_SPACE}"


def _canonical_space(
    path: str | None,
    default: Callable[[], str] | None = None,
) -> str:
    """Normalize a user-supplied space path so write and read sides agree.

    Output and resolve steps both accept space paths from YAML. The strings
    looked identical to the user but reached Kumiho with different surface
    forms (leading slash vs not, trailing slash, doubled separators), so
    ``_exec_output`` would publish to one path and ``_exec_resolve`` would
    miss it on lookup. This helper produces ONE canonical form used on both
    sides.

    Rules:
      - Empty / None → ``default()`` if provided, else ``""``
      - Strip leading / trailing ``/``
      - Collapse repeated ``/`` to a single ``/``

    The output is always slash-free at both ends — callers that need a
    leading slash (e.g. for ``parent_path`` arguments to Kumiho) prepend it
    themselves so the normalization point stays a single, predictable place.
    """
    if not path or not path.strip():
        return default() if default is not None else ""
    parts = [p for p in path.split("/") if p]
    return "/".join(parts)


# ---------------------------------------------------------------------------
# Persist a completed workflow run
# ---------------------------------------------------------------------------

async def persist_workflow_run(
    workflow_name: str,
    run_id: str,
    status: str,
    inputs: dict[str, Any],
    step_results: dict[str, dict[str, Any]],
    started_at: str | None = None,
    completed_at: str | None = None,
    error: str = "",
    steps_total: int = 0,
    workflow_item_kref: str = "",
    workflow_revision_kref: str = "",
) -> str | None:
    """Persist a workflow run to Kumiho. Returns the item kref or None.

    Best-effort: returns None if Kumiho is unavailable, but logs errors
    so persistence failures are diagnosable.
    """
    try:
        from ..operator_mcp import KUMIHO_SDK
        if not KUMIHO_SDK._available:
            _log(f"workflow_memory: Kumiho SDK not available, skipping persist for run={run_id[:8]}")
            return None

        # Ensure space exists
        await _kumiho_with_timeout(
            KUMIHO_SDK.ensure_space(_project(), _SPACE),
            "ensure workflow run space",
            run_id=run_id,
        )

        # Get-or-create item for this run
        item_name = f"{workflow_name}-{run_id[:12]}"
        item_kref = ""
        _log(f"workflow_memory: persisting run={run_id[:8]} item_name={item_name}")

        expanded_completed_count = sum(
            1 for sr in step_results.values()
            if sr.get("status") in ("completed", "skipped")
        )
        completed_count = sum(
            1 for sid, sr in step_results.items()
            if "__iter_" not in sid and sr.get("status") in ("completed", "skipped")
        )
        effective_steps_total = steps_total or completed_count
        if effective_steps_total:
            completed_count = min(completed_count, effective_steps_total)

        # Check if item already exists (e.g. "running" entry created at start)
        try:
            existing = await _kumiho_with_timeout(
                KUMIHO_SDK.list_items(_space_path()),
                "list workflow run items",
                run_id=run_id,
            )
            for it in existing:
                if it.get("item_name", it.get("name", "")) == item_name:
                    item_kref = it.get("kref", "")
                    break
        except Exception:
            pass

        if not item_kref:
            item = await _kumiho_with_timeout(
                KUMIHO_SDK.create_item(
                    _space_path(),
                    item_name,
                    kind="workflow_run",
                    metadata={
                        "workflow": workflow_name,
                        "workflow_name": workflow_name,
                        "run_id": run_id,
                        "status": status,
                        "started_at": started_at or "",
                        "completed_at": completed_at or "",
                        "error": error[:500],
                        "step_count": str(len(step_results)),
                        "steps_completed": str(completed_count),
                        "steps_total": str(effective_steps_total),
                        "expanded_steps_completed": str(expanded_completed_count),
                        "workflow_item_kref": workflow_item_kref,
                        "workflow_revision_kref": workflow_revision_kref,
                    },
                ),
                "create workflow run item",
                run_id=run_id,
            )
            item_kref = item.get("kref", "")

        if not item_kref:
            _log(f"workflow_memory: failed to obtain item kref for run={run_id[:8]}, aborting persist")
            return None

        # Build revision metadata (compact summary)
        step_summary: dict[str, str] = {}
        all_files: list[str] = []
        for sid, sr in step_results.items():
            entry: dict[str, Any] = {
                "status": sr.get("status", "unknown"),
            }
            if sr.get("agent_id"):
                entry["agent_id"] = sr["agent_id"]
            if sr.get("agent_type"):
                entry["agent_type"] = sr["agent_type"]
            if sr.get("role"):
                entry["role"] = sr["role"]
            # Include template name and skills from output_data
            od = sr.get("output_data", {}) or {}
            if od.get("template_name"):
                entry["template_name"] = od["template_name"]
            if od.get("skills"):
                entry["skills"] = json.dumps(od["skills"])
            # Include group_chat transcript (truncated for Kumiho metadata budget)
            if od.get("transcript"):
                compact = []
                for turn in od["transcript"][:20]:
                    compact.append({
                        "speaker": turn.get("speaker", "?"),
                        "content": turn.get("content", "")[:800],
                        "round": turn.get("round", 0),
                    })
                entry["transcript"] = json.dumps(compact)
            # Truncate output_preview BEFORE serialization to keep JSON valid.
            # Budget: ~400 chars for preview, ~100 for other fields + JSON overhead.
            output = sr.get("output", "")
            if output:
                entry["output_preview"] = _redact_for_persistence(str(output))[:400]
            if sr.get("error"):
                entry["error"] = _redact_for_persistence(str(sr["error"]))[:1000]
            # Include artifact path so recovery can read full output from disk
            if od.get("artifact_path"):
                entry["artifact_path"] = od["artifact_path"]
            files = sr.get("files_touched", sr.get("files", []))
            if files:
                entry["files"] = json.dumps(files[:10])
                all_files.extend(files[:20])

            # ALWAYS persist input_data + output_data for the run-view UI.
            # Pre-process: coerce non-JSON values, redact obvious secrets,
            # cap each individual string field. Then enforce a per-step
            # JSON cap so a single rogue step can't blow the whole run's
            # metadata budget.
            id_data, id_trunc = _prepare_for_persistence(sr.get("input_data") or {})
            od_data, od_trunc = _prepare_for_persistence(od)
            entry["input_data"] = id_data
            entry["output_data"] = od_data

            entry_truncated = id_trunc or od_trunc
            entry_json = json.dumps(entry, default=str)
            if len(entry_json) > _PER_STEP_JSON_CAP:
                # Step JSON still too large after per-string truncation.
                # Drop the heaviest fields (artifact_content, transcript,
                # rendered email body, raw entity dump) progressively until
                # we fit. Mark _truncated so the UI shows a warning instead
                # of pretending the data is complete.
                entry_truncated = True
                for heavy_key in (
                    "artifact_content",
                    "rendered",
                    "entities",
                    "metadata",
                ):
                    for blob in (entry["input_data"], entry["output_data"]):
                        if isinstance(blob, dict) and heavy_key in blob:
                            blob[heavy_key] = "[truncated]"
                    entry_json = json.dumps(entry, default=str)
                    if len(entry_json) <= _PER_STEP_JSON_CAP:
                        break
                # Last resort: hard-truncate the JSON. Drop a marker; the
                # Rust gateway treats unparseable values as legacy entries
                # so we keep the parseable shape by trimming the heaviest
                # nested blobs to empty objects.
                if len(entry_json) > _PER_STEP_JSON_CAP:
                    entry["input_data"] = {"_truncated": True}
                    entry["output_data"] = {"_truncated": True}
                    entry_json = json.dumps(entry, default=str)
            if entry_truncated:
                entry["_truncated"] = True
                entry_json = json.dumps(entry, default=str)
            step_summary[sid] = entry_json

        rev_metadata: dict[str, str] = {
            "workflow": workflow_name,
            "workflow_name": workflow_name,  # Rust gateway reads this key
            "run_id": run_id,
            "status": status,
            "inputs": json.dumps(inputs)[:2000],
            "started_at": started_at or "",
            "completed_at": completed_at or "",
            "error": error[:500],
            "step_count": str(len(step_results)),
            "steps_completed": str(completed_count),
            "steps_total": str(effective_steps_total),
            "expanded_steps_completed": str(expanded_completed_count),
            "files_touched": json.dumps(list(set(all_files))[:50]),
            "persisted_at": datetime.now(timezone.utc).isoformat(),
            # Kumiho pin for the workflow revision this run executed against —
            # lets the dashboard fetch the exact YAML the run used, instead of
            # whatever is currently tagged `published`. Empty for built-ins.
            "workflow_item_kref": workflow_item_kref,
            "workflow_revision_kref": workflow_revision_kref,
        }
        # Add step summaries (flattened for Kumiho metadata).
        # Do NOT truncate the JSON string — that corrupts it.
        for sid, summary_json in step_summary.items():
            key = f"step_{sid}"[:50]  # Kumiho key length limit
            rev_metadata[key] = summary_json

        rev = await _kumiho_with_timeout(
            KUMIHO_SDK.create_revision(item_kref, rev_metadata, tag="latest"),
            "create workflow run revision",
            run_id=run_id,
        )
        rev_kref = rev.get("kref", "") if isinstance(rev, dict) else getattr(rev, "kref", "")

        # Attach disk artifacts to the Kumiho revision so they're discoverable
        # via the graph (not just via the metadata artifact_path field).
        attach_artifacts = status.lower() in _TERMINAL_RUN_STATUSES
        if rev_kref and attach_artifacts:
            attached_artifact_paths: set[str] = set()
            for sid, sr in step_results.items():
                art = sr.get("output_data", {}).get("artifact_path", "")
                if art and os.path.exists(art):
                    canonical_art = os.path.realpath(art)
                    if canonical_art in attached_artifact_paths:
                        continue
                    attached_artifact_paths.add(canonical_art)
                    artifact_name = os.path.basename(art) or f"{sid}.md"
                    try:
                        await _kumiho_with_timeout(
                            KUMIHO_SDK.create_artifact(
                                rev_kref,
                                artifact_name,
                                art,
                            ),
                            f"attach workflow run artifact {sid}",
                            run_id=run_id,
                            timeout=_timeout_from_env(
                                "REVKA_WORKFLOW_MEMORY_ARTIFACT_TIMEOUT_SECS",
                                _PERSIST_ARTIFACT_TIMEOUT_SECS,
                            ),
                        )
                    except Exception as e:
                        _log(f"workflow_memory: artifact attach failed for step={sid}: {e}")

        _log(f"workflow_memory: persisted run={run_id[:8]} workflow={workflow_name} kref={item_kref}")
        return item_kref

    except Exception as exc:
        import traceback
        _log(f"workflow_memory: persist failed for run={run_id[:8]}: {exc}\n{traceback.format_exc()}")
        return None


# ---------------------------------------------------------------------------
# Publish a workflow output as a Kumiho entity (triggers event listeners)
# ---------------------------------------------------------------------------

async def _ensure_space_path(space_path: str) -> None:
    """Ensure every level of a (possibly nested) Kumiho space path exists.

    The SDK's ``ensure_space(project, space)`` only handles a single space
    directly under a project. For deeper paths like ``/A/B/C/D`` we have to
    walk the path and create each segment with the correct ``parent_path``
    so that ``create_item`` doesn't 404 with "Space not found".
    """
    parts = space_path.strip("/").split("/")
    if not parts or not parts[0]:
        return

    from ..operator_mcp import KUMIHO_SDK
    project = parts[0]

    if len(parts) == 1:
        # Path is just "/project" — nothing to create beyond the project.
        # ensure_space requires a space name; default to WorkflowOutputs to
        # match the legacy behaviour of this function.
        await _kumiho_with_timeout(
            KUMIHO_SDK.ensure_space(project, "WorkflowOutputs"),
            f"ensure space {project}/WorkflowOutputs",
        )
        return

    # First segment under the project: ensure_space already creates
    # both the project and the root-level space idempotently.
    await _kumiho_with_timeout(
        KUMIHO_SDK.ensure_space(project, parts[1]),
        f"ensure space {project}/{parts[1]}",
    )

    if len(parts) == 2:
        return

    # Deeper segments need parent_path plumbed through tool_create_space,
    # which the SDK's ensure_space wrapper does not expose.
    try:
        from kumiho.mcp_server import tool_create_space
    except ImportError:
        _log(
            "workflow_memory: kumiho.mcp_server.tool_create_space unavailable; "
            f"cannot ensure nested segments of {space_path!r}"
        )
        return

    for i in range(2, len(parts)):
        parent_path = "/" + "/".join(parts[:i])
        segment = parts[i]

        def _create(p=project, s=segment, pp=parent_path) -> None:
            r = tool_create_space(p, s, parent_path=pp)
            if "error" in r and "already exists" not in r["error"].lower():
                _log(f"workflow_memory: create_space({pp}/{s}) warning: {r['error']}")

        await asyncio.wait_for(
            asyncio.to_thread(_create),
            timeout=_timeout_from_env(
                "REVKA_WORKFLOW_MEMORY_TIMEOUT_SECS",
                _PERSIST_OP_TIMEOUT_SECS,
            ),
        )


# ---------------------------------------------------------------------------
# Attachment filename helpers (used by Manus register_output)
# ---------------------------------------------------------------------------

def _sanitize_attachment_filename(name: str) -> str:
    """Strip path traversal + separators from an attachment filename.

    Manus attachments arrive with whatever filename the agent produced;
    that may include ``../`` segments or absolute paths. Defensively
    flatten the name to a single component, drop dangerous characters,
    and cap length so it cannot blow up the artifact directory layout.

    Returns "" when nothing remains after sanitization — callers should
    fall back to a positional ``attachment_<index>`` name.
    """
    if not isinstance(name, str):
        return ""
    # Drop null bytes outright — they confuse os.path on every platform.
    cleaned = name.replace("\x00", "")
    # Reject path traversal — replace any ``..`` segment with the rest of
    # the basename so an attacker can't escape the attachments/ dir.
    cleaned = cleaned.replace("..", "")
    # Take only the basename — strip leading directories from BOTH unix
    # and windows path separators since Manus is platform-agnostic.
    cleaned = cleaned.replace("\\", "/").rsplit("/", 1)[-1]
    # Strip leading dots so we never write hidden files into the artifact dir.
    cleaned = cleaned.lstrip(".")
    # Trim whitespace + cap length. 200 chars is well under every common
    # filesystem limit (255 bytes on ext4/APFS, 260 on NTFS without long-path).
    cleaned = cleaned.strip()[:200]
    return cleaned


def _unique_attachment_path(base_dir: str, subdir: str, file_name: str) -> str:
    """Return a unique path under ``base_dir/subdir``.

    If ``base_dir/subdir/file_name`` already exists, append ``-1``, ``-2``,
    ... before the extension until the path is free. Used so two attachments
    that share a filename within the same step don't overwrite each other.
    """
    target_dir = os.path.join(base_dir, subdir)
    candidate = os.path.join(target_dir, file_name)
    if not os.path.exists(candidate):
        return candidate
    stem, ext = os.path.splitext(file_name)
    i = 1
    while True:
        alt = os.path.join(target_dir, f"{stem}-{i}{ext}")
        if not os.path.exists(alt):
            return alt
        i += 1


async def publish_workflow_entity(
    *,
    entity_name: str,
    entity_kind: str,
    entity_tag: str = "ready",
    entity_space: str | None = None,
    entity_metadata: dict[str, str] | None = None,
    metadata_target: str = "item",
    content: str,
    content_format: str = "markdown",
    workflow_name: str,
    run_id: str,
    step_id: str,
    artifact_path_override: str | None = None,
    artifact_summary_model: str = "",
) -> dict[str, Any] | None:
    """Register a workflow output as a Kumiho entity and tag it.

    This creates an item + revision in Kumiho, then tags the revision.
    The tag event will be picked up by the WorkflowEventListener to trigger
    downstream workflows.

    Args:
        entity_metadata: User-defined key-value pairs. Stored on the Kumiho
            item, revision, or artifact according to metadata_target.
        metadata_target: One of "item", "revision", or "artifact". Item is
            the legacy default and keeps downstream trigger auto-mapping.

    Returns {"item_kref": ..., "revision_kref": ...} or None on failure.
    """
    try:
        from ..operator_mcp import KUMIHO_SDK
        if not KUMIHO_SDK._available:
            _log(f"workflow_memory: Kumiho SDK not available, skipping entity publish for {entity_name}")
            return None

        # Canonicalize the user-supplied space path so the write side here
        # matches the read side in `resolve_entity`. Without this, an output
        # step writing ``Revka/WorkflowOutputs/Github`` and a resolve
        # step reading ``/Revka/WorkflowOutputs/Github`` would publish
        # and lookup at different paths.
        space_path = _canonical_space(
            entity_space,
            default=lambda: f"{_project()}/WorkflowOutputs",
        )
        # Walk the full space path and ensure every segment exists. The SDK's
        # ensure_space only creates a single space directly under a project,
        # so deeper paths used to fail at create_item below with NOT_FOUND.
        # This is best-effort: output publishing should not fail just because
        # idempotent space creation is slow for an already-existing space. If
        # the space truly is missing, create_item below returns NOT_FOUND and
        # triggers one more ensure+retry with a clearer failure point.
        _log(f"workflow_memory: ensuring space {space_path} for entity {entity_name}")
        try:
            await _ensure_space_path(space_path)
        except TimeoutError as exc:
            _log(
                f"workflow_memory: ensure space {space_path} timed out; "
                f"continuing to create_item for entity {entity_name}: {exc}"
            )

        target = metadata_target if metadata_target in {"item", "revision", "artifact"} else "item"
        user_meta = dict(entity_metadata or {})

        # Merge source tracking with user-defined metadata only when the user
        # targets the item. Source tracking remains on the item for operational
        # lookup regardless of target.
        item_meta: dict[str, str] = {
            "source_workflow": workflow_name,
            "source_run_id": run_id,
            "source_step": step_id,
        }
        if target == "item":
            item_meta.update(user_meta)

        # Create the item. Retry once on NOT_FOUND to absorb the rare race
        # where the gRPC backend hasn't yet replicated the space we just
        # ensured above.
        async def _do_create() -> dict[str, Any]:
            return await _kumiho_with_timeout(
                KUMIHO_SDK.create_item(
                    space_path,
                    entity_name,
                    kind=entity_kind,
                    metadata=item_meta,
                ),
                "create workflow entity item",
                run_id=run_id,
            )

        try:
            item = await _do_create()
        except RuntimeError as exc:
            msg = str(exc).lower()
            if "not found" in msg or "not_found" in msg:
                _log(
                    f"workflow_memory: create_item NOT_FOUND for {space_path}; "
                    "re-ensuring space and retrying once"
                )
                await _ensure_space_path(space_path)
                item = await _do_create()
            else:
                raise
        item_kref = item.get("kref", "") if isinstance(item, dict) else getattr(item, "kref", "")
        if not item_kref:
            _log(f"workflow_memory: entity creation returned no kref for {entity_name}")
            return None

        # Write content to disk as a hard copy artifact — unless the caller
        # supplied an override path (Manus register_output uses an
        # entity-anchored path that lives outside the per-run tree, and
        # writes the file itself). In override mode we trust the file to
        # exist; the rest of the publish flow is unchanged.
        artifact_write_error = ""
        if artifact_path_override:
            artifact_path = artifact_path_override
            ext = os.path.splitext(artifact_path)[1] or ".md"
            if not os.path.exists(artifact_path):
                # The override path is supposed to be written by the
                # caller before invoking publish. If it isn't, surface
                # a clear error rather than silently producing a tagless
                # entity (which the output-step path also refuses to do).
                artifact_write_error = (
                    f"artifact_path_override does not exist on disk: {artifact_path}"
                )
                _log(
                    "workflow_memory: artifact_path_override missing — "
                    f"{artifact_path}"
                )
                artifact_path = ""
        else:
            artifact_dir = os.path.expanduser(f"~/.revka/artifacts/{workflow_name}/{run_id}")
            os.makedirs(artifact_dir, exist_ok=True)
            ext = {"json": ".json", "markdown": ".md", "text": ".txt"}.get(
                content_format, ".md"
            )
            artifact_path = os.path.join(artifact_dir, f"{step_id}{ext}")
            try:
                with open(artifact_path, "w", encoding="utf-8") as f:
                    f.write(content)
            except Exception as e:
                artifact_write_error = str(e)
                _log(f"workflow_memory: failed to write artifact to {artifact_path}: {e}")
                artifact_path = ""

        # Create a revision with the content and tag it in one call
        metadata = {
            "workflow": workflow_name,
            "run_id": run_id,
            "step_id": step_id,
            "content_preview": content[:2000] if content else "",
            "content_length": str(len(content)) if content else "0",
        }
        if target == "revision":
            metadata.update(user_meta)
        if artifact_path:
            metadata["artifact_path"] = artifact_path
        # Create the revision untagged first. Kumiho revisions can become
        # immutable once tagged/published, so artifacts must be attached before
        # applying the workflow-visible entity tag.
        rev = await _kumiho_with_timeout(
            KUMIHO_SDK.create_revision(item_kref, metadata, tag=None),
            "create workflow entity revision",
            run_id=run_id,
        )
        rev_kref = rev.get("kref", "") if isinstance(rev, dict) else getattr(rev, "kref", "")
        if not rev_kref:
            _log(f"workflow_memory: revision creation returned no kref for entity {entity_name}")
            return None

        # Attach the disk artifact to the revision before tagging it.
        artifact_kref = ""
        artifact_attached = False
        artifact_error = artifact_write_error
        if artifact_path and rev_kref:
            try:
                # In override mode, use the override path's basename so the
                # artifact's stored file_name matches what's actually on disk
                # (e.g. "content.md" for Manus, not "<step_id>.md").
                artifact_file_name = (
                    os.path.basename(artifact_path)
                    if artifact_path_override
                    else f"{step_id}{ext}"
                )
                artifact_metadata: dict[str, Any] = dict(user_meta) if target == "artifact" else {}
                artifact_metadata.update(
                    await summarize_artifact_metadata(
                        content,
                        artifact_name=artifact_file_name,
                        content_format=content_format,
                        summary_model=artifact_summary_model,
                        existing_metadata=artifact_metadata,
                    )
                )
                artifact = await _kumiho_with_timeout(
                    KUMIHO_SDK.create_artifact(
                        rev_kref,
                        artifact_file_name,
                        artifact_path,
                        artifact_metadata or None,
                    ),
                    "attach workflow entity artifact",
                    run_id=run_id,
                    timeout=_timeout_from_env(
                        "REVKA_WORKFLOW_MEMORY_ARTIFACT_TIMEOUT_SECS",
                        _PERSIST_ARTIFACT_TIMEOUT_SECS,
                    ),
                )
                artifact_kref = (
                    artifact.get("kref", "")
                    if isinstance(artifact, dict)
                    else getattr(artifact, "kref", "")
                )
                artifact_attached = bool(artifact_kref)
                if not artifact_attached:
                    artifact_error = "create_artifact returned no artifact kref"
            except Exception as e:
                artifact_error = str(e)
                _log(f"workflow_memory: failed to attach artifact to revision {rev_kref}: {e}")

        tag_applied = False
        tag_error = ""
        if not artifact_path:
            tag_error = f"artifact write failed; refusing to tag revision '{rev_kref}' as '{entity_tag}'"
            _log(f"workflow_memory: {tag_error}")
        elif not artifact_attached:
            tag_error = f"artifact attach failed; refusing to tag revision '{rev_kref}' as '{entity_tag}'"
            _log(f"workflow_memory: {tag_error}")
        elif entity_tag:
            try:
                tag_result = await _kumiho_with_timeout(
                    KUMIHO_SDK.tag_revision(rev_kref, entity_tag),
                    "tag workflow entity revision",
                    run_id=run_id,
                )
                if isinstance(tag_result, dict) and tag_result.get("error"):
                    raise RuntimeError(str(tag_result["error"]))
                tag_applied = True
            except Exception as e:
                tag_error = str(e)
                _log(f"workflow_memory: failed to tag revision {rev_kref} with '{entity_tag}': {e}")

        _log(f"workflow_memory: published entity: {entity_name} (kind={entity_kind}, tag={entity_tag}, artifact={artifact_path or 'none'})")
        return {
            "item_kref": item_kref,
            "revision_kref": rev_kref,
            "artifact_path": artifact_path,
            "artifact_kref": artifact_kref,
            "artifact_attached": artifact_attached,
            "artifact_error": artifact_error,
            "artifact_summary": artifact_metadata.get("summary", "") if artifact_path else "",
            "tag_applied": tag_applied,
            "tag_error": tag_error,
            "metadata_target": target,
        }

    except Exception as e:
        import traceback
        _log(f"workflow_memory: failed to publish entity {entity_name}: {e}\n{traceback.format_exc()}")
        return None


# ---------------------------------------------------------------------------
# Resolve a Kumiho entity by kind + tag (used by resolve step type)
# ---------------------------------------------------------------------------

def _deprecated_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _revision_skip_reason(rev: dict[str, Any], requested_tag: str) -> str:
    if _deprecated_flag(rev.get("deprecated")):
        return "revision is deprecated"
    tag = (requested_tag or "").strip()
    if not tag:
        return ""
    tags = rev.get("tags")
    if isinstance(tags, (list, tuple, set)):
        normalized = {str(t) for t in tags}
        if tag not in normalized:
            return f"revision is not tagged {tag!r}"
    elif "tag" in rev and rev.get("tag") is not None:
        if str(rev.get("tag")) != tag:
            return f"revision is not tagged {tag!r}"
    return ""


async def resolve_entity(
    kind: str,
    tag: str = "published",
    name_pattern: str = "",
    space: str = "",
    artifact_name: str = "",
    mode: str = "latest",
    metadata_source: str = "revision",
) -> dict[str, Any] | list[dict[str, Any]] | None:
    """Resolve a Kumiho entity by kind + tag. Returns revision dict(s) or None."""
    from ..operator_mcp import KUMIHO_SDK
    if not KUMIHO_SDK._available:
        raise RuntimeError("Kumiho SDK not available")

    # Search for items matching the kind. Canonicalize the lookup path so
    # ``space="/Revka/WorkflowOutputs/Github"`` and
    # ``space="Revka/WorkflowOutputs/Github"`` resolve to the same
    # context as the path used by `publish_workflow_entity`.
    context = _canonical_space(
        space,
        default=lambda: f"{_project()}/WorkflowOutputs",
    )
    items = await KUMIHO_SDK.list_items(context)
    _log(f"resolve_entity: list_items({context}) → {len(items)} items")

    # Filter by kind and ignore deprecated items even if an SDK/backend
    # accidentally returned them in a default list call.
    matched = [
        it for it in items
        if it.get("kind") == kind and not _deprecated_flag(it.get("deprecated"))
    ]
    _log(f"resolve_entity: kind={kind} → {len(matched)} items")

    # Filter by name pattern if provided. Kumiho stores item names as
    # ``<base>.<kind>`` internally, so a user pattern like
    # ``zeroclaw-repo`` (kind ``research``) wouldn't match the stored
    # ``zeroclaw-repo.research`` under raw fnmatch. We strip the
    # ``.<kind>`` suffix (only when it matches the item's own kind, to
    # avoid eating arbitrary trailing-dot segments) before matching, and
    # also accept the raw stored name for users who include the suffix.
    if name_pattern:
        import fnmatch

        def _base_name(it: dict[str, Any]) -> str:
            name = it.get("name", "")
            it_kind = it.get("kind", "")
            suffix = f".{it_kind}"
            if it_kind and name.endswith(suffix):
                return name[: -len(suffix)]
            return name

        matched = [
            it for it in matched
            if fnmatch.fnmatch(_base_name(it), name_pattern)
            or fnmatch.fnmatch(it.get("name", ""), name_pattern)
        ]
        _log(f"resolve_entity: name_pattern={name_pattern!r} → {len(matched)} items")

    if not matched:
        _log(
            f"resolve_entity: NO MATCH — kind={kind} tag={tag} "
            f"name_pattern={name_pattern!r} space={context}"
        )
        return None

    source = metadata_source if metadata_source in {"revision", "item", "artifact"} else "revision"

    async def _decorate_revision(item: dict[str, Any], rev: dict[str, Any]) -> dict[str, Any]:
        item_kref = item.get("kref", "")
        item_meta = dict(item.get("metadata", {}) or {})
        rev_meta = dict(rev.get("metadata", {}) or {})
        artifact_meta: dict[str, Any] = {}
        artifact_info: dict[str, Any] | None = None
        try:
            artifacts = await KUMIHO_SDK.get_artifacts(rev.get("kref", ""))
        except Exception as exc:
            _log(f"resolve_entity: failed to fetch artifacts for {rev.get('kref', '')}: {exc}")
            artifacts = []
        if artifacts:
            default_name = rev.get("default_artifact", "")
            if artifact_name:
                artifact_info = next((a for a in artifacts if a.get("name") == artifact_name), None)
                if artifact_info is None:
                    _log(
                        f"resolve_entity: artifact_name={artifact_name!r} not found "
                        f"on revision {rev.get('kref', '')}"
                    )
            else:
                artifact_info = next(
                    (a for a in artifacts if default_name and a.get("name") == default_name),
                    artifacts[0],
                )
            if artifact_info is not None:
                artifact_meta = dict(artifact_info.get("metadata", {}) or {})

        selected_meta = (
            item_meta if source == "item"
            else artifact_meta if source == "artifact"
            else rev_meta
        )
        rev = dict(rev)
        rev.setdefault("item_kref", item_kref)
        rev.setdefault("name", item.get("name", ""))
        rev["metadata"] = selected_meta
        rev["metadata_source"] = source
        rev["item_metadata"] = item_meta
        rev["revision_metadata"] = rev_meta
        if artifact_info is not None:
            rev["artifact"] = artifact_info
            rev["artifact_metadata"] = artifact_meta
            if artifact_meta.get("summary"):
                rev["artifact_summary"] = str(artifact_meta["summary"])
        return rev

    if mode == "latest":
        # Get the most recent item (by created_at or just take last)
        # Try to get revision with the specified tag
        for item in reversed(matched):
            item_kref = item.get("kref", "")
            if not item_kref:
                continue
            rev = await KUMIHO_SDK.get_latest_revision(item_kref, tag=tag)
            if rev:
                skip_reason = _revision_skip_reason(rev, tag)
                if skip_reason:
                    _log(
                        f"resolve_entity: item {item.get('name')} kref={item_kref} "
                        f"skipped — {skip_reason}"
                    )
                    continue
                rev = await _decorate_revision(item, rev)
                _log(
                    f"resolve_entity: matched {item.get('name')} "
                    f"kref={item_kref} (rev tag={tag!r}, metadata_source={source!r})"
                )
                return rev
            else:
                _log(
                    f"resolve_entity: item {item.get('name')} kref={item_kref} "
                    f"has no revision tagged {tag!r}"
                )
        _log(
            f"resolve_entity: NO MATCH — kind={kind} tag={tag} "
            f"name_pattern={name_pattern!r} space={context}"
        )
        return None
    else:  # all
        results = []
        for item in matched:
            item_kref = item.get("kref", "")
            if not item_kref:
                continue
            rev = await KUMIHO_SDK.get_latest_revision(item_kref, tag=tag)
            if rev:
                skip_reason = _revision_skip_reason(rev, tag)
                if skip_reason:
                    _log(
                        f"resolve_entity: item {item.get('name')} kref={item_kref} "
                        f"skipped — {skip_reason}"
                    )
                    continue
                results.append(await _decorate_revision(item, rev))
        return results if results else None


# ---------------------------------------------------------------------------
# Tag / deprecate existing entities
# ---------------------------------------------------------------------------

async def tag_entity(
    item_kref: str,
    tag: str,
    untag: str = "",
) -> dict[str, str]:
    """Re-tag an existing entity's latest revision.

    Optionally removes an old tag first (e.g. 'planted' -> 'referenced').
    Returns {"revision_kref": ..., "new_tag": ...}.
    """
    from ..operator_mcp import KUMIHO_SDK
    if not KUMIHO_SDK._available:
        raise RuntimeError("Kumiho SDK not available")

    # Get the latest revision for this item
    rev = await KUMIHO_SDK.get_latest_revision(item_kref, tag=untag if untag else "latest")
    if not rev:
        raise RuntimeError(f"No revision found for item {item_kref}")

    rev_kref = rev.get("kref", "")
    if not rev_kref:
        raise RuntimeError(f"Revision has no kref for item {item_kref}")

    # Remove old tag if specified
    if untag:
        try:
            await KUMIHO_SDK.untag_revision(rev_kref, untag)
        except Exception:
            pass  # Tag may not exist — that's fine

    # Apply new tag
    await KUMIHO_SDK.tag_revision(rev_kref, tag)

    _log(f"workflow_memory: tagged {item_kref} revision {rev_kref}: {untag + ' → ' if untag else ''}{tag}")
    return {"revision_kref": rev_kref, "new_tag": tag}


async def deprecate_entity(
    item_kref: str,
    reason: str = "",
) -> dict[str, str]:
    """Deprecate a Kumiho item.

    Returns {"item_kref": ..., "deprecated": "true"}.
    """
    from ..operator_mcp import KUMIHO_SDK
    if not KUMIHO_SDK._available:
        raise RuntimeError("Kumiho SDK not available")

    await KUMIHO_SDK.set_deprecated(item_kref, True)

    _log(f"workflow_memory: deprecated {item_kref}" + (f" reason={reason}" if reason else ""))
    return {"item_kref": item_kref, "deprecated": "true"}


# ---------------------------------------------------------------------------
# Create edges linking workflow runs to Kumiho-stored agents and teams
# ---------------------------------------------------------------------------

async def _resolve_pool_kref(template_name: str) -> str | None:
    """Resolve an agent template name to its real kref in /Revka/AgentPool."""
    try:
        from ..operator_mcp import KUMIHO_POOL
        if not KUMIHO_POOL._available:
            return None
        agents = await KUMIHO_POOL.list_agents()
        for agent in agents:
            name = agent.get("item_name", agent.get("name", ""))
            if name == template_name:
                return agent.get("kref")
        return None
    except Exception:
        return None


async def link_agents_to_run(
    run_kref: str,
    step_results: dict[str, dict[str, Any]],
) -> int:
    """Create edges from the workflow run to Kumiho-stored agents.

    For each step that used an agent:
      1. Try to resolve the agent's template to a real /Revka/AgentPool kref
         and create a USED_TEMPLATE edge (workflow run → pool agent) with
         step context metadata (step_id, role, action, skills).
      2. Fall back to a PRODUCED_BY edge with the runtime agent ID.

    Returns total edge count created.
    """
    try:
        from ..operator_mcp import KUMIHO_SDK
        if not KUMIHO_SDK._available or not run_kref:
            return 0

        count = 0
        for sid, sr in step_results.items():
            agent_id = sr.get("agent_id")
            if not agent_id:
                continue

            # Try to find the pool template kref for this agent
            # template_name is stored in output_data by the executor
            output_data = sr.get("output_data", {})
            template_name = output_data.get("template_name", sr.get("template_name", ""))
            pool_kref = None
            if template_name:
                pool_kref = await _resolve_pool_kref(template_name)

            # Build edge metadata with step context
            edge_meta: dict[str, str] = {
                "step_id": sid,
                "agent_id": agent_id,
                "status": sr.get("status", ""),
            }
            if sr.get("role"):
                edge_meta["role"] = sr["role"]
            if sr.get("action"):
                edge_meta["action"] = sr["action"]
            if sr.get("agent_type"):
                edge_meta["agent_type"] = sr["agent_type"]
            # Include skills and template name
            skills = output_data.get("skills", [])
            if skills:
                edge_meta["skills"] = json.dumps(skills)
            if template_name:
                edge_meta["template"] = template_name

            try:
                if pool_kref:
                    # Link to real pool agent with step context
                    await KUMIHO_SDK.create_edge(
                        run_kref, pool_kref, "USED_TEMPLATE",
                        metadata=edge_meta,
                    )
                    _log(f"workflow_memory: linked step={sid} → pool agent '{template_name}' ({pool_kref})")
                    count += 1
                else:
                    # Fall back to runtime agent reference
                    await KUMIHO_SDK.create_edge(
                        run_kref, f"agent:{agent_id}", "PRODUCED_BY",
                        metadata=edge_meta,
                    )
                    count += 1
            except Exception as exc:
                _log(f"workflow_memory: edge creation failed for step={sid}: {exc}")
        return count
    except Exception:
        return 0


async def link_run_to_team(run_kref: str, team_name: str) -> bool:
    """Create an EXECUTED_BY edge from a workflow run to a Kumiho team bundle.

    Args:
        run_kref: The workflow run item kref.
        team_name: Team name or kref in /Revka/Teams.

    Returns:
        True if edge was created.
    """
    try:
        from ..operator_mcp import KUMIHO_SDK, KUMIHO_TEAMS
        if not KUMIHO_SDK._available:
            return False

        team_kref = await KUMIHO_TEAMS.resolve_team_kref(team_name)
        if not team_kref:
            return False

        await KUMIHO_SDK.create_edge(run_kref, team_kref, "EXECUTED_BY")
        _log(f"workflow_memory: linked run to team '{team_name}' ({team_kref})")
        return True
    except Exception as exc:
        _log(f"workflow_memory: team link failed: {exc}")
        return False


async def link_run_to_prior(
    run_kref: str,
    prior_run_id: str,
) -> bool:
    """Create a DEPENDS_ON edge from one workflow run to a prior run.

    Useful for chained workflows where run B builds on run A's output.
    """
    try:
        from ..operator_mcp import KUMIHO_SDK
        if not KUMIHO_SDK._available:
            return False

        # Find the prior run's kref
        results = await KUMIHO_SDK.search(
            prior_run_id, context=_space_path(), kind="workflow_run",
        )
        for r in results:
            item = r.get("item", {})
            if prior_run_id[:12] in item.get("item_name", ""):
                prior_kref = item.get("kref", "")
                if prior_kref:
                    await KUMIHO_SDK.create_edge(run_kref, prior_kref, "DEPENDS_ON")
                    _log(f"workflow_memory: linked run to prior {prior_run_id[:8]}")
                    return True
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Recall workflow runs
# ---------------------------------------------------------------------------

async def recall_workflow_runs(
    workflow_name: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Recall recent workflow runs from Kumiho.

    Args:
        workflow_name: Filter by workflow name (None = all).
        limit: Max results.

    Returns:
        List of run summary dicts.
    """
    try:
        from ..operator_mcp import KUMIHO_SDK
        if not KUMIHO_SDK._available:
            return []

        # Search for workflow_run items
        query = f"workflow_run {workflow_name}" if workflow_name else "workflow_run"
        results = await KUMIHO_SDK.search(
            query, context=_space_path(), kind="workflow_run",
            include_revision_metadata=True,
        )

        runs: list[dict[str, Any]] = []
        for r in results[:limit]:
            item = r.get("item", {})
            metadata = item.get("revision_metadata", item.get("metadata", {}))

            run_info: dict[str, Any] = {
                "kref": item.get("kref", ""),
                "workflow": metadata.get("workflow", ""),
                "run_id": metadata.get("run_id", ""),
                "status": metadata.get("status", ""),
                "started_at": metadata.get("started_at", ""),
                "completed_at": metadata.get("completed_at", ""),
                "step_count": metadata.get("step_count", "0"),
                "error": metadata.get("error", ""),
            }

            # Parse files_touched
            try:
                run_info["files_touched"] = json.loads(metadata.get("files_touched", "[]"))
            except (json.JSONDecodeError, TypeError):
                run_info["files_touched"] = []

            runs.append(run_info)

        return runs

    except Exception as exc:
        _log(f"workflow_memory: recall failed: {exc}")
        return []


async def get_workflow_run_detail(run_id: str) -> dict[str, Any] | None:
    """Get detailed info about a specific workflow run from Kumiho.

    Uses a multi-strategy approach:
      1. List items in the WorkflowRuns space and match by item_name
         (contains run_id[:12]).  This is the most reliable strategy
         because it avoids fulltext-search indexing delays.
      2. Fall back to fulltext search with revision metadata.
    """
    try:
        from ..operator_mcp import KUMIHO_SDK
        if not KUMIHO_SDK._available:
            return None

        run_prefix = run_id[:12].lower()
        item_kref: str = ""

        # Strategy 1: list items and match by item_name containing run_id[:12]
        try:
            items = await KUMIHO_SDK.list_items(_space_path())
            for it in items:
                name = it.get("item_name", it.get("name", "")).lower()
                kind = it.get("kind", "")
                if kind == "workflow_run" and run_prefix in name:
                    item_kref = it.get("kref", "")
                    break
        except Exception as exc:
            _log(f"workflow_memory: detail strategy 1 (list_items) failed: {exc}")

        # Strategy 2: fulltext search (may have indexing delay for new items)
        if not item_kref:
            try:
                results = await KUMIHO_SDK.search(
                    run_id, context=_space_path(), kind="workflow_run",
                    include_revision_metadata=True,
                )
                for r in results:
                    item = r.get("item", {})
                    metadata = item.get("revision_metadata", item.get("metadata", {}))
                    if metadata.get("run_id", "").startswith(run_prefix):
                        item_kref = item.get("kref", "")
                        break
            except Exception as exc:
                _log(f"workflow_memory: detail strategy 2 (search) failed: {exc}")

        if not item_kref:
            _log(f"workflow_memory: run {run_id[:8]} not found via any strategy")
            return None

        # Now fetch the latest revision which has the full step data
        rev = await KUMIHO_SDK.get_latest_revision(item_kref, tag="latest")
        if not rev:
            _log(f"workflow_memory: found item {item_kref} but no revision for run {run_id[:8]}")
            return None

        metadata = rev.get("metadata", {})

        # Parse step results from revision metadata
        steps: dict[str, Any] = {}
        for key, val in metadata.items():
            if key.startswith("step_") and key not in ("step_count", "steps_completed", "steps_total"):
                step_id = key[5:]
                try:
                    steps[step_id] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    steps[step_id] = {"raw": val}

        return {
            "kref": item_kref,
            "workflow": metadata.get("workflow", ""),
            "run_id": metadata.get("run_id", ""),
            "status": metadata.get("status", ""),
            "inputs": metadata.get("inputs", "{}"),
            "started_at": metadata.get("started_at", ""),
            "completed_at": metadata.get("completed_at", ""),
            "error": metadata.get("error", ""),
            "step_count": metadata.get("step_count", "0"),
            "files_touched": metadata.get("files_touched", "[]"),
            "steps": steps,
            "persisted_at": metadata.get("persisted_at", ""),
        }

    except Exception as exc:
        _log(f"workflow_memory: detail lookup failed for run {run_id[:8]}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Stale run cleanup (called on operator startup)
# ---------------------------------------------------------------------------

async def mark_stale_runs() -> int:
    """Find workflow runs stuck in 'running' state and mark them as failed.

    On daemon restart, any run that was 'running' is now orphaned — no
    executor is driving it.  This scans Kumiho for such runs and updates
    their status to 'failed' with a clear reason.

    Also marks matching local checkpoints failed so explicit Retry can load the
    interrupted state instead of losing it on startup.

    Returns the number of runs marked stale.
    """
    import os
    marked = 0
    _log("workflow_memory: scanning for stale runs...")

    # --- 1. Update Kumiho entries ---
    try:
        from ..operator_mcp import KUMIHO_SDK
        if not KUMIHO_SDK._available:
            _log("workflow_memory: Kumiho SDK not available, skipping stale scan")
        else:
            from kumiho.mcp_server import tool_get_revision_by_tag as _get_rev

            # List all items in the WorkflowRuns space, then check each
            items = await KUMIHO_SDK.list_items(_space_path())
            _log(f"workflow_memory: found {len(items)} workflow run(s) to check")

            for item in items:
                kref = item.get("kref", "")
                if not kref:
                    continue

                # Get latest revision metadata to check status
                try:
                    import asyncio
                    rev = await asyncio.to_thread(_get_rev, kref, "latest")
                    meta = rev.get("metadata", rev.get("revision", {}).get("metadata", {}))
                    status = meta.get("status", "")
                except Exception as exc:
                    _log(f"workflow_memory: could not read revision for {kref}: {exc}")
                    continue

                if status not in ("running", "paused"):
                    continue

                # This run is stuck — create a new revision marking it failed
                try:
                    updated_meta: dict[str, str] = {}
                    for k, v in meta.items():
                        updated_meta[k] = str(v) if not isinstance(v, str) else v
                    updated_meta["status"] = "failed"
                    updated_meta["error"] = (
                        "Run interrupted — daemon restarted while workflow was in progress"
                    )
                    updated_meta["completed_at"] = datetime.now(timezone.utc).isoformat()

                    await KUMIHO_SDK.create_revision(kref, updated_meta, tag="latest")
                    run_id = meta.get("run_id", kref)
                    _mark_checkpoint_failed(run_id, updated_meta["error"], updated_meta["completed_at"])
                    _log(f"workflow_memory: marked stale run={run_id[:8]} (was {status})")
                    marked += 1

                except Exception as exc:
                    _log(f"workflow_memory: failed to update stale run {kref}: {exc}")
                    continue

    except Exception as exc:
        _log(f"workflow_memory: stale run scan failed: {exc}")

    if marked:
        _log(f"workflow_memory: marked {marked} stale run(s) as failed on startup")

    return marked


def _mark_checkpoint_failed(run_id: str, error: str, completed_at: str) -> bool:
    """Update a local checkpoint to failed without deleting retry state."""
    checkpoint_dir = os.path.expanduser("~/.revka/workflow_checkpoints")
    path = os.path.join(checkpoint_dir, f"{run_id}.json")
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r") as f:
            data = json.load(f)
        data["status"] = "failed"
        data["error"] = error
        data["completed_at"] = completed_at
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        _log(f"workflow_memory: marked checkpoint {os.path.basename(path)} failed")
        return True
    except Exception as exc:
        _log(f"workflow_memory: failed to update checkpoint {path}: {exc}")
        return False


# ---------------------------------------------------------------------------
# Tool handlers for MCP
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Recovery helpers — find interrupted runs and reconstruct their state
# ---------------------------------------------------------------------------

async def find_running_runs() -> list[dict[str, Any]]:
    """Find workflow runs stuck in 'running' state (candidates for recovery).

    Returns a list of dicts with keys:
        kref, run_id, workflow_name, started_at, metadata (full revision metadata)
    """
    try:
        from ..operator_mcp import KUMIHO_SDK
        if not KUMIHO_SDK._available:
            return []

        from kumiho.mcp_server import tool_get_revision_by_tag as _get_rev
        import asyncio

        items = await KUMIHO_SDK.list_items(_space_path())
        running: list[dict[str, Any]] = []

        for item in items:
            kref = item.get("kref", "")
            if not kref or item.get("kind") != "workflow_run":
                continue

            try:
                rev = await asyncio.to_thread(_get_rev, kref, "latest")
                meta = rev.get("metadata", rev.get("revision", {}).get("metadata", {}))
            except Exception:
                continue

            if meta.get("status") == "running":
                running.append({
                    "kref": kref,
                    "run_id": meta.get("run_id", ""),
                    "workflow_name": meta.get("workflow", meta.get("workflow_name", "")),
                    "started_at": meta.get("started_at", ""),
                    "metadata": meta,
                })

        return running

    except Exception as exc:
        _log(f"workflow_memory: find_running_runs failed: {exc}")
        return []


def reconstruct_step_results(metadata: dict[str, str]) -> dict[str, "StepResult"]:
    """Reconstruct StepResult objects from Kumiho revision metadata.

    The executor persists step results as `step_{sid}` keys in revision
    metadata, each containing a JSON-encoded dict with status, agent_id,
    output_preview, files, etc.  This reverses that encoding.
    """
    from .schema import StepResult

    results: dict[str, StepResult] = {}
    for key, val in metadata.items():
        if not key.startswith("step_"):
            continue
        # Skip aggregate keys
        if key in ("step_count", "steps_completed", "steps_total"):
            continue
        step_id = key[5:]  # strip "step_" prefix
        try:
            data = json.loads(val)
        except (json.JSONDecodeError, TypeError):
            continue

        # Prefer full output from disk artifact over 400-char preview
        output_text = data.get("output_preview", "")
        art_path = data.get("artifact_path", "")
        if art_path and os.path.exists(art_path):
            try:
                with open(art_path, "r", encoding="utf-8") as af:
                    output_text = af.read()
            except Exception:
                pass  # fall back to preview

        sr = StepResult(
            step_id=step_id,
            status=data.get("status", "failed"),
            output=output_text,
            agent_id=data.get("agent_id"),
            agent_type=data.get("agent_type", ""),
            role=data.get("role", ""),
        )
        # Restore files_touched
        files_raw = data.get("files")
        if files_raw:
            try:
                sr.files_touched = json.loads(files_raw) if isinstance(files_raw, str) else files_raw
            except (json.JSONDecodeError, TypeError):
                pass
        # Restore template_name into output_data
        if data.get("template_name"):
            sr.output_data["template_name"] = data["template_name"]
        if data.get("skills"):
            try:
                sr.output_data["skills"] = json.loads(data["skills"]) if isinstance(data["skills"], str) else data["skills"]
            except (json.JSONDecodeError, TypeError):
                pass

        results[step_id] = sr

    return results


async def tool_recall_workflow_runs(args: dict[str, Any]) -> dict[str, Any]:
    """Recall recent workflow runs from Kumiho memory.

    Args:
        workflow: Optional workflow name filter.
        limit: Max results (default 10).
    """
    workflow_name = args.get("workflow")
    limit = min(args.get("limit", 10), 50)

    runs = await recall_workflow_runs(workflow_name, limit)
    return {
        "runs": runs,
        "count": len(runs),
        "filter": workflow_name or "(all)",
    }


async def tool_get_workflow_run_detail(args: dict[str, Any]) -> dict[str, Any]:
    """Get detailed info about a specific workflow run.

    Args:
        run_id: The workflow run ID (required).
    """
    from ..failure_classification import classified_error, VALIDATION_ERROR

    run_id = args.get("run_id", "")
    if not run_id:
        return classified_error("run_id is required", code="missing_run_id", category=VALIDATION_ERROR)

    detail = await get_workflow_run_detail(run_id)
    if not detail:
        return {"run_id": run_id, "found": False, "message": "Run not found in Kumiho"}

    return {"found": True, **detail}
