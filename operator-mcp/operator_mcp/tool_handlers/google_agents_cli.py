"""Google Agents CLI operator tool handler."""
from __future__ import annotations

import asyncio
import os
import shutil
from typing import Any

from ..construct_config import workspace_dir
from ..failure_classification import classified_error, VALIDATION_ERROR, RUNTIME_ENV_ERROR

SAFE_ENV_VARS = {
    "PATH",
    "HOME",
    "TERM",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "USER",
    "SHELL",
    "TMPDIR",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "GEMINI_ENTERPRISE_APP_ID",
}

ALLOWED_TOP_LEVEL_COMMANDS = {
    "cmd-info",
    "create",
    "data-ingestion",
    "deploy",
    "eval",
    "infra",
    "install",
    "lint",
    "login",
    "playground",
    "publish",
    "run",
    "scaffold",
    "setup",
    "update",
}


def _normalize_command(args: dict[str, Any]) -> list[str] | None:
    raw = args.get("command")
    if isinstance(raw, str):
        command = [raw]
    elif isinstance(raw, list):
        command = [str(part) for part in raw]
    else:
        command = []

    prompt = args.get("prompt")
    if not command and isinstance(prompt, str) and prompt:
        command = ["run"]
    if not command:
        return None
    if isinstance(prompt, str) and prompt:
        if command[0] != "run":
            raise ValueError("'prompt' is only valid with command ['run', ...]")
        command.append(prompt)
    return command


def _validate_command(command: list[str], allow_interactive: bool) -> str | None:
    first = command[0].strip() if command else ""
    if not first:
        return "agents-cli command must not be empty"
    if first not in ALLOWED_TOP_LEVEL_COMMANDS:
        return (
            f"Unsupported agents-cli command '{first}'. Allowed commands: "
            + ", ".join(sorted(ALLOWED_TOP_LEVEL_COMMANDS))
        )
    for arg in command:
        if arg == "":
            return "agents-cli command contains an empty token"
        if "\0" in arg:
            return "agents-cli command contains a NUL byte"
        if not allow_interactive and arg in {"-i", "--interactive"}:
            return "Interactive agents-cli flags are disabled by default"
    if first == "login" and not any(arg in {"--status", "status"} for arg in command) and not allow_interactive:
        return "Use `agents-cli login --status`; interactive login must be done outside Construct"
    return None


def _resolve_cwd(value: Any) -> str:
    root = os.path.realpath(workspace_dir())
    raw = value if isinstance(value, str) and value.strip() else root
    expanded = os.path.expanduser(raw)
    candidate = expanded if os.path.isabs(expanded) else os.path.join(root, expanded)
    resolved = os.path.realpath(candidate)
    if resolved != root and not resolved.startswith(root + os.sep):
        raise ValueError(f"working_directory is outside the workspace: {resolved}")
    return resolved


def _safe_env(extra: Any) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in SAFE_ENV_VARS:
        val = os.environ.get(key)
        if val:
            env[key] = val
    if isinstance(extra, list):
        for key in extra:
            if isinstance(key, str) and key.strip():
                val = os.environ.get(key.strip())
                if val:
                    env[key.strip()] = val
    return env


def _command_preview(command: list[str]) -> list[str]:
    if command and command[0] == "run":
        return ["run", "..."] if len(command) > 1 else ["run"]
    return command


async def tool_google_agents_cli(args: dict[str, Any]) -> dict[str, Any]:
    """Run a bounded Google Agents CLI command without shell expansion."""
    try:
        command = _normalize_command(args)
    except ValueError as exc:
        return classified_error(str(exc), code="invalid_command", category=VALIDATION_ERROR)
    if command is None:
        return classified_error(
            "command is required, or provide prompt to default to agents-cli run",
            code="missing_command",
            category=VALIDATION_ERROR,
        )

    allow_interactive = bool(args.get("allow_interactive", False))
    validation_error = _validate_command(command, allow_interactive)
    if validation_error:
        return classified_error(validation_error, code="invalid_command", category=VALIDATION_ERROR)

    try:
        cwd = _resolve_cwd(args.get("working_directory") or args.get("cwd"))
    except ValueError as exc:
        return classified_error(str(exc), code="bad_working_directory", category=VALIDATION_ERROR)
    if not os.path.isdir(cwd):
        return classified_error(
            f"working_directory is not a directory: {cwd}",
            code="bad_working_directory",
            category=VALIDATION_ERROR,
        )

    binary = shutil.which("agents-cli")
    if not binary:
        return classified_error(
            "Google Agents CLI ('agents-cli') not found in PATH. Install with: uvx google-agents-cli setup",
            code="agents_cli_missing",
            category=RUNTIME_ENV_ERROR,
        )

    timeout = float(args.get("timeout") or 600.0)
    max_output_bytes = int(args.get("max_output_bytes") or 2_097_152)
    if timeout <= 0:
        return classified_error("timeout must be greater than zero", code="bad_timeout", category=VALIDATION_ERROR)
    if max_output_bytes <= 0:
        return classified_error(
            "max_output_bytes must be greater than zero",
            code="bad_max_output_bytes",
            category=VALIDATION_ERROR,
        )
    proc = await asyncio.create_subprocess_exec(
        binary,
        *command,
        cwd=cwd,
        env=_safe_env(args.get("env_passthrough")),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        try:
            await proc.wait()
        except ProcessLookupError:
            # The process may exit between timeout detection and cleanup.
            pass
        return {
            "status": "timeout",
            "success": False,
            "exit_code": None,
            "cwd": cwd,
            "command": _command_preview(command),
            "error": f"agents-cli timed out after {timeout:.0f}s",
        }

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    if len(stdout.encode("utf-8")) > max_output_bytes:
        stdout = stdout.encode("utf-8")[:max_output_bytes].decode("utf-8", errors="ignore")
        stdout += "\n... [output truncated]"

    success = proc.returncode == 0
    return {
        "status": "completed" if success else "failed",
        "success": success,
        "exit_code": proc.returncode,
        "cwd": cwd,
        "command": _command_preview(command),
        "output": stdout,
        "stderr": stderr[-4000:] if stderr else "",
        "error": "" if success else (stderr[-2000:] or f"agents-cli exited with {proc.returncode}"),
    }
