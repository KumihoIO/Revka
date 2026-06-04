"""Unified failure classification for operator tool responses.

Every error returned to the calling agent includes:
  - error:          human-readable message (unchanged, for backward compat)
  - error_code:     machine-readable slug (e.g. "agent_not_found")
  - error_category: one of the defined categories below
  - retryable:      whether the caller should retry

Categories:
  task_failed           — agent's task logic failed (test failure, bad output)
  tool_blocked          — permission denied or policy rejection
  runtime_env_error     — bad cwd, missing template, resource limit
  backend_transport     — sidecar unreachable, circuit breaker, timeout
  validation_error      — invalid graph, bad parameters, malformed input
  not_found             — agent/team/template doesn't exist
  internal_error        — unexpected exception in operator itself
"""
from __future__ import annotations

from typing import Any


# -- Categories ----------------------------------------------------------------

TASK_FAILED = "task_failed"
TOOL_BLOCKED = "tool_blocked"
RUNTIME_ENV_ERROR = "runtime_env_error"
BACKEND_TRANSPORT = "backend_transport"
VALIDATION_ERROR = "validation_error"
NOT_FOUND = "not_found"
INTERNAL_ERROR = "internal_error"


# -- Structured error builder --------------------------------------------------

def classified_error(
    message: str,
    *,
    code: str,
    category: str,
    retryable: bool = False,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a structured error response dict.

    Returns a dict compatible with existing {"error": "..."} responses
    but enriched with classification fields. Callers that only check
    for "error" key still work; callers that understand classification
    get richer info.
    """
    result: dict[str, Any] = {
        "error": message,
        "error_code": code,
        "error_category": category,
        "retryable": retryable,
    }
    if detail:
        result["detail"] = detail
    return result


# -- Pre-built errors for common cases ----------------------------------------

def agent_not_found(agent_id: str) -> dict[str, Any]:
    return classified_error(
        f"Agent not found: {agent_id}",
        code="agent_not_found",
        category=NOT_FOUND,
    )


def team_not_found(kref: str) -> dict[str, Any]:
    return classified_error(
        f"Team not found: {kref}",
        code="team_not_found",
        category=NOT_FOUND,
    )


def template_not_found(name: str) -> dict[str, Any]:
    return classified_error(
        f"Template not found in pool: {name}",
        code="template_not_found",
        category=NOT_FOUND,
    )


def bad_directory(path: str) -> dict[str, Any]:
    return classified_error(
        f"Directory does not exist: {path}",
        code="bad_directory",
        category=RUNTIME_ENV_ERROR,
    )


def missing_cwd() -> dict[str, Any]:
    return classified_error(
        "cwd is required. Pass an absolute path (e.g. ~/.revka/workspace) "
        "or use a template whose default_cwd is set.",
        code="missing_cwd",
        category=RUNTIME_ENV_ERROR,
    )


def agent_limit_exceeded(running: int, limit: int) -> dict[str, Any]:
    return classified_error(
        f"Agent limit reached ({running}/{limit}). Wait for running agents to finish or cancel some.",
        code="agent_limit_exceeded",
        category=RUNTIME_ENV_ERROR,
        retryable=True,
        detail={"running_agents": running, "max_agents": limit},
    )


def invalid_param(field: str, value: Any, allowed: str = "") -> dict[str, Any]:
    msg = f"Invalid {field}: {value}"
    if allowed:
        msg += f". Must be {allowed}"
    return classified_error(
        msg,
        code="invalid_param",
        category=VALIDATION_ERROR,
    )


def graph_invalid(validation_dict: dict[str, Any]) -> dict[str, Any]:
    return classified_error(
        "Team graph is invalid",
        code="graph_invalid",
        category=VALIDATION_ERROR,
        detail=validation_dict,
    )


def circuit_breaker_open(breaker_status: dict[str, Any]) -> dict[str, Any]:
    return classified_error(
        "Sidecar circuit breaker is OPEN",
        code="circuit_breaker_open",
        category=BACKEND_TRANSPORT,
        retryable=True,
        detail={"circuit_breaker": breaker_status},
    )


def sidecar_unavailable() -> dict[str, Any]:
    return classified_error(
        "Session manager not available",
        code="sidecar_unavailable",
        category=BACKEND_TRANSPORT,
        retryable=True,
    )


def backend_unreachable(last_known_status: str = "") -> dict[str, Any]:
    return classified_error(
        "Sidecar is unreachable. The agent may still be running.",
        code="backend_unreachable",
        category=BACKEND_TRANSPORT,
        retryable=True,
        detail={"last_known_status": last_known_status} if last_known_status else None,
    )


def agent_busy(agent_id: str) -> dict[str, Any]:
    return classified_error(
        "Agent is still running. Wait for it to finish first.",
        code="agent_busy",
        category=RUNTIME_ENV_ERROR,
        retryable=True,
        detail={"agent_id": agent_id},
    )


def spawn_failed(name: str, stderr: str = "") -> dict[str, Any]:
    return classified_error(
        f"Agent '{name}' failed to spawn",
        code="spawn_failed",
        category=TASK_FAILED,
        detail={"stderr": stderr} if stderr else None,
    )


def upstream_stage_failed(stage: int, failed_names: list[str]) -> dict[str, Any]:
    return classified_error(
        f"Skipped: upstream stage {stage} failed ({', '.join(failed_names)})",
        code="upstream_stage_failed",
        category=TASK_FAILED,
        detail={"failed_stage": stage, "failed_agents": failed_names},
    )


# -- Policy / permission denials -----------------------------------------------

def policy_denied(check_type: str, target: str, reason: str, *, policy_rule: str = "", suggestion: str = "") -> dict[str, Any]:
    """A policy pre-flight check denied an operation."""
    detail: dict[str, Any] = {"check_type": check_type, "target": target, "policy_rule": policy_rule}
    if suggestion:
        detail["suggestion"] = suggestion
    return classified_error(
        reason,
        code="policy_denied",
        category=TOOL_BLOCKED,
        detail=detail,
    )


def permission_pending(agent_id: str, pending_count: int, pending_tools: list[str]) -> dict[str, Any]:
    """Agent is blocked waiting for permission approval."""
    return classified_error(
        f"Agent {agent_id} is blocked on {pending_count} pending permission(s): {', '.join(pending_tools)}",
        code="permission_pending",
        category=TOOL_BLOCKED,
        detail={"agent_id": agent_id, "pending_count": pending_count, "pending_tools": pending_tools},
    )


# -- Exception classifier for MCP dispatch catch-all --------------------------

def classify_exception(exc: Exception) -> dict[str, Any]:
    """Classify an unexpected exception from tool dispatch.

    Used by the MCP catch-all handler to return structured errors
    instead of bare str(exc).
    """
    exc_type = type(exc).__name__
    msg = str(exc)

    # Circuit breaker
    if "CircuitBreakerOpen" in exc_type or "circuit" in msg.lower():
        return classified_error(
            msg,
            code="circuit_breaker_open",
            category=BACKEND_TRANSPORT,
            retryable=True,
        )

    # Connection/transport errors
    if any(t in exc_type for t in ("ConnectionError", "ConnectError", "TimeoutError", "httpx")):
        return classified_error(
            msg,
            code="transport_error",
            category=BACKEND_TRANSPORT,
            retryable=True,
        )

    # Permission/auth
    if any(t in exc_type for t in ("PermissionError", "AuthError")):
        return classified_error(
            msg,
            code="permission_denied",
            category=TOOL_BLOCKED,
        )

    # File/path errors
    if any(t in exc_type for t in ("FileNotFoundError", "NotADirectoryError")):
        return classified_error(
            msg,
            code="file_not_found",
            category=RUNTIME_ENV_ERROR,
        )

    # Validation
    if any(t in exc_type for t in ("ValueError", "ValidationError", "TypeError")):
        return classified_error(
            msg,
            code="invalid_input",
            category=VALIDATION_ERROR,
        )

    # Default: internal error
    return classified_error(
        msg,
        code="internal_error",
        category=INTERNAL_ERROR,
        detail={"exception_type": exc_type},
    )
