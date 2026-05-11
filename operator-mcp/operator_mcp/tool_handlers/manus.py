"""MCP tool handler exposing Manus as an Operator-callable tool.

The Operator agent (the conversational chat agent) calls this when the
user asks for ad-hoc research that benefits from real browser / web
access. Mirrors the workflow ``manus:`` step's auth + create + poll
mechanics by delegating to :func:`operator_mcp.workflow.executor.manus_run_task`,
the shared executor, so the two paths can't drift.

Differences from the workflow step:
  - No ``state.cancel_requested`` plumbing — MCP tools have no
    workflow-level cancel; the only cancel surface is the configured
    ``timeout_seconds``.
  - No ``register_output`` — that's a workflow-step concept. The tool
    returns the result dict; the Operator agent can decide to call
    other tools (``kumiho_create_item`` etc.) if it wants to persist.
  - Auth precedence is identical: ``credentials_ref`` (auth-profile
    resolve) → ``MANUS_API_KEY`` env var → tool-level error.
"""
from __future__ import annotations

from typing import Any

from .._log import _log


async def tool_manus_create_task(args: dict[str, Any]) -> dict[str, Any]:
    """Create a Manus task, poll until terminal, return result.

    Args:
        prompt (str, required): The research prompt for Manus.
        structured_output_schema (dict, optional): JSON schema for
            Manus to populate. When provided, ``structured_output`` will
            be present in the result.
        connectors (list[str], optional): Manus connector ids to attach.
        enable_skills (list[str], optional): Manus skill ids to enable.
        force_skills (list[str], optional): Manus skill ids to force-invoke.
        agent_profile (str, optional): Manus agent profile id. Defaults
            to the cached ``[manus].default_agent_profile`` or
            ``"manus-1.6"``.
        locale (str, optional): Locale hint passed through to Manus.
        project_id (str, optional): Manus project binding.
        title (str, optional): Display title for the Manus task.
        timeout_seconds (int, optional): Max seconds to poll. Default 600.
        poll_interval_seconds (int, optional): Seconds between polls.
            Default ``[manus].default_poll_interval_seconds`` or 5.
        credentials_ref (str, optional): Auth-profile id (e.g.
            ``"manus:work"``). When set, the gateway resolves the
            encrypted token at execution time. When unset, falls back to
            the env var named in ``[manus].api_key_env``.

    Returns:
        dict with at minimum ``task_id`` / ``task_url`` / ``final_state``
        on success, or ``{"error": "..."}`` when the call short-circuits
        (missing api key, auth resolve failure, transport failure). The
        full result schema is documented on ``manus_run_task``.
    """
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        return {"error": "prompt is required"}

    # Forward each field through to manus_run_task. Missing optional
    # fields are simply left as None / their defaults so the shared
    # helper can apply config.toml fallbacks consistently.
    structured = args.get("structured_output_schema")
    if structured is not None and not isinstance(structured, dict):
        return {"error": "structured_output_schema must be an object"}

    def _str_list(key: str) -> list[str] | None:
        v = args.get(key)
        if v is None:
            return None
        if not isinstance(v, list):
            return None
        return [str(x) for x in v if isinstance(x, (str, int))]

    timeout_arg = args.get("timeout_seconds")
    poll_arg = args.get("poll_interval_seconds")
    try:
        timeout_s = int(timeout_arg) if timeout_arg is not None else 600
    except (TypeError, ValueError):
        return {"error": "timeout_seconds must be an integer"}
    try:
        poll_s = int(poll_arg) if poll_arg is not None else None
    except (TypeError, ValueError):
        return {"error": "poll_interval_seconds must be an integer"}

    # Lazy import to keep the module-level import light + avoid cycles.
    from ..workflow.executor import manus_run_task

    _log(
        f"manus_create_task: starting "
        f"prompt_len={len(prompt)} timeout={timeout_s}s "
        f"credentials_ref={args.get('credentials_ref') or '(env)'}"
    )

    result = await manus_run_task(
        prompt=prompt,
        structured_output_schema=structured,
        connectors=_str_list("connectors"),
        enable_skills=_str_list("enable_skills"),
        force_skills=_str_list("force_skills"),
        agent_profile=(args.get("agent_profile") or None),
        locale=(args.get("locale") or None),
        project_id=(args.get("project_id") or None),
        title=(args.get("title") or None),
        timeout_seconds=timeout_s,
        poll_interval_seconds=poll_s,
        credentials_ref=(args.get("credentials_ref") or None),
        cancel_check=None,
    )
    return result
