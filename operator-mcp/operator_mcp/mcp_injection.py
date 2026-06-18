"""MCP injection — builds MCP server configs for agent sessions.

Provides two MCP servers for injection into agent sessions:
  1. kumiho-memory — full Kumiho memory graph access
  2. operator-tools — subset of operator tools for hierarchical spawning

Also handles system prompt layering:
  - Top-level agents: operator prompt + memory bootstrap + user task
  - Sub-agents: memory bootstrap + role identity + parent task
"""
from __future__ import annotations

import os
from typing import Any

from .revka_config import kumiho_connection_config

from ._log import _log

# -- Paths -------------------------------------------------------------------

_HOME = os.path.expanduser("~")
# Canonical location for Revka's own kumiho MCP sidecar — materialized by
# `revka install --sidecars-only` from resources/sidecars/run_kumiho_mcp.py.
# The launcher self-execs into ~/.revka/kumiho/venv/bin/python3.
#
# This is intentionally NOT the Claude Code plugin path (~/.revka/workspace/
# kumiho-plugins/claude/...) — that layout exists for users running Kumiho as a
# Claude Code plugin directly. When Revka injects MCP via `--mcp-config`,
# it ships its own sidecar and shouldn't depend on the user having the Claude
# Code plugin installed. Mirrors src/agent/kumiho.rs::DEFAULT_MCP_PATH_SUFFIX.
_KUMIHO_SIDECAR_ROOT = os.path.join(_HOME, ".revka/kumiho")
_KUMIHO_MCP_SCRIPT = os.path.join(_KUMIHO_SIDECAR_ROOT, "run_kumiho_mcp.py")
_OPERATOR_DIR = os.path.dirname(os.path.abspath(__file__))
_OPERATOR_SUBAGENT_MCP = os.path.join(_OPERATOR_DIR, "subagent_mcp.py")
_GOOGLE_AGENTOPS_MCP = os.path.join(_OPERATOR_DIR, "google_agentops_mcp.py")
_WORKFLOW_MEMORY_ALIAS_MCP = os.path.join(_OPERATOR_DIR, "workflow_memory_alias_mcp.py")


def _venv_python(venv_root: str) -> str:
    """Return the path to the venv's Python interpreter, or a system fallback.

    Mirrors the platform detection in resources/sidecars/run_kumiho_mcp.py:
    Windows venvs put their interpreter at `Scripts\\python.exe`, POSIX at
    `bin/python3` (with `bin/python` as a secondary). The system fallback
    must also be platform-correct — `python3` is the convention on POSIX
    but typically isn't on PATH on Windows, where `python.exe` (or `py.exe`
    via the launcher) is the convention.
    """
    if os.name == "nt":
        candidate = os.path.join(venv_root, "Scripts", "python.exe")
        if os.path.exists(candidate):
            return candidate
        return "python"
    candidate = os.path.join(venv_root, "bin", "python3")
    if os.path.exists(candidate):
        return candidate
    candidate = os.path.join(venv_root, "bin", "python")
    if os.path.exists(candidate):
        return candidate
    return "python3"


# -- MCP server configs ------------------------------------------------------

def _kumiho_forward_env() -> dict[str, str]:
    """Environment shared by Kumiho-backed MCP servers."""
    env: dict[str, str] = {}
    for key in (
        "KUMIHO_AUTH_TOKEN",
        "KUMIHO_SERVICE_TOKEN",
        "KUMIHO_API_URL",
        "KUMIHO_CONTROL_PLANE_URL",
        "KUMIHO_LOCAL_SERVER_ENDPOINT",
        "KUMIHO_UPSTASH_REDIS_URL",
        "UPSTASH_REDIS_URL",
        "KUMIHO_SPACE_PREFIX",
        "KUMIHO_MEMORY_PROJECT",
        "KUMIHO_HARNESS_PROJECT",
        "KUMIHO_MCP_LOG_LEVEL",
        "KUMIHO_AUTO_ASSESS",
        "KUMIHO_LLM_API_KEY",
        "KUMIHO_LLM_PROVIDER",
        "KUMIHO_LLM_MODEL",
        "KUMIHO_LLM_LIGHT_MODEL",
        "KUMIHO_LLM_BASE_URL",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        val = os.environ.get(key)
        if val:
            env[key] = val
    config = kumiho_connection_config()
    fallback_map = {
        "KUMIHO_AUTH_TOKEN": "auth_token",
        "KUMIHO_SERVICE_TOKEN": "service_token",
        "KUMIHO_API_URL": "api_url",
        "KUMIHO_SPACE_PREFIX": "space_prefix",
        "KUMIHO_MEMORY_PROJECT": "memory_project",
        "KUMIHO_HARNESS_PROJECT": "harness_project",
    }
    for env_key, config_key in fallback_map.items():
        if env_key not in env and config.get(config_key):
            env[env_key] = config[config_key]
    # Local self-hosted CE is tokenless: when an endpoint is configured, shadow
    # any inherited cloud credentials to empty so the kumiho SDK takes the
    # loopback CE probe (`/api/_live` -> gRPC) instead of cloud discovery (which
    # falls back to its default gRPC target 127.0.0.1:8080). Mirrors the Rust
    # daemon's CE wiring in src/agent/kumiho.rs::kumiho_mcp_server_config.
    if env.get("KUMIHO_LOCAL_SERVER_ENDPOINT"):
        for tok in ("KUMIHO_AUTH_TOKEN", "KUMIHO_SERVICE_TOKEN", "KUMIHO_CONTROL_PLANE_URL"):
            env[tok] = ""
        # kumiho_memory buffers sessions in Redis; CE has no control plane to
        # discover an Upstash URL, so default to the local loopback Redis the CE
        # onboarding provisions (unless the user already supplied one). Without
        # it, reflect/write falls back to the cloud memory proxy and fails with
        # "No credentials available for memory proxy".
        if not env.get("KUMIHO_UPSTASH_REDIS_URL") and not env.get("UPSTASH_REDIS_URL"):
            env["KUMIHO_UPSTASH_REDIS_URL"] = "redis://127.0.0.1:6379"
    return env


def kumiho_memory_config() -> dict[str, Any] | None:
    """Build kumiho-memory MCP stdio config for agent injection.

    Points at Revka's own kumiho sidecar (provisioned by `revka
    install --sidecars-only`). Returns None when the sidecar isn't installed.
    """
    if not os.path.exists(_KUMIHO_MCP_SCRIPT):
        _log(
            f"Kumiho sidecar not installed at {_KUMIHO_MCP_SCRIPT} — "
            "subprocess agents will run without memory access. "
            "Run `revka install --sidecars-only` to provision it."
        )
        return None

    # Prefer the kumiho venv interpreter — the launcher self-execs into it
    # anyway, so calling it directly skips one fork.
    python = _venv_python(_KUMIHO_SIDECAR_ROOT)

    # Forward the same env the Rust daemon forwards when it spawns kumiho —
    # see src/agent/kumiho.rs::kumiho_mcp_server_config for the canonical set.
    # Auto-configure is intentionally not enabled by default: it can perform
    # network credential refresh before the MCP initialize handshake.
    env = _kumiho_forward_env()

    return {
        "type": "stdio",
        "command": python,
        "args": [_KUMIHO_MCP_SCRIPT],
        "env": env,
    }


def workflow_memory_alias_config() -> dict[str, Any]:
    """Build the workflow-memory MCP for generic Kumiho aliases.

    The full kumiho-memory server exposes the upstream Kumiho MCP surface,
    whose tool names are package-version dependent. Workflow child agents need
    a stable tiny surface for capture/publish handoffs regardless of those
    upstream names.
    """
    python = _venv_python(os.path.join(_HOME, ".revka", "operator_mcp", "venv"))
    return {
        "type": "stdio",
        "command": python,
        "args": [_WORKFLOW_MEMORY_ALIAS_MCP],
        "env": _kumiho_forward_env(),
    }


def operator_tools_config(socket_path: str | None = None) -> dict[str, Any]:
    """Build operator-tools MCP stdio config for sub-agent injection.

    Exposes a subset of operator tools so sub-agents can spawn children,
    check siblings, post to chat rooms, run Google Agents CLI lifecycle
    commands, and call outbound A2A agents.
    """
    python = _venv_python(os.path.join(_HOME, ".revka", "operator_mcp", "venv"))

    env: dict[str, str] = {}
    if socket_path:
        env["REVKA_SIDECAR_SOCKET"] = socket_path

    return {
        "type": "stdio",
        "command": python,
        "args": [_OPERATOR_SUBAGENT_MCP],
        "env": env,
    }


def google_agentops_tools_config() -> dict[str, Any]:
    """Build the reduced Google AgentOps MCP config for workflow agents."""
    python = _venv_python(os.path.join(_HOME, ".revka", "operator_mcp", "venv"))
    return {
        "type": "stdio",
        "command": python,
        "args": [_GOOGLE_AGENTOPS_MCP],
        "env": {},
    }


def build_mcp_servers(
    include_memory: bool = True,
    include_operator: bool = True,
    include_google_agentops: bool = False,
    socket_path: str | None = None,
) -> dict[str, Any]:
    """Build the full MCP servers dict for agent session injection."""
    servers: dict[str, Any] = {}

    if include_memory:
        mem = kumiho_memory_config()
        if mem:
            servers["kumiho-memory"] = mem
        servers["workflow-memory"] = workflow_memory_alias_config()

    if include_operator:
        servers["operator-tools"] = operator_tools_config(socket_path)
    elif include_google_agentops:
        servers["google-agentops-tools"] = google_agentops_tools_config()

    return servers


# -- System prompt layering ---------------------------------------------------

_OPERATOR_PROMPT = """\
You are a sub-agent managed by the Revka Operator. You have access to \
operator-tools MCP which lets you spawn child agents, check their status, \
coordinate work, run Google Agents CLI lifecycle commands, and call external \
A2A agents.

Guidelines:
- Focus on your assigned task. Be thorough but efficient.
- Use create_agent to delegate subtasks when the work is too large or spans \
different domains.
- Use get_agent_activity and wait_for_agent to monitor children.
- Use google_agents_cli for Google ADK / Agent Platform lifecycle commands; \
agents-cli is a tool, not an agent_type.
- Use a2a_discover, a2a_send_task, and a2a_get_remote_task when the task \
requires external A2A interoperability.
- Report results clearly — your parent agent reads your output.
- If context grows large, call compact_conversation to trigger structured \
compaction. The summary is stored in Kumiho for cross-session recall."""

_MEMORY_BOOTSTRAP = """\
You have access to kumiho-memory MCP for persistent memory. Use \
kumiho_memory_engage before responding to topics that might have history. \
Use kumiho_memory_reflect after substantive responses to capture decisions, \
preferences, and facts. Workflow agents also have stable workflow-memory \
aliases: capture_skill stores reusable procedures and returns revision_kref; \
tag_revision applies a tag to a Kumiho revision or item. When capture_skill \
stores a long artifact, choose a cheap summary_model or ask which model to use; \
the summary is stored on artifact metadata."""

_GOOGLE_AGENTOPS_PROMPT = """\
You have access to google-agentops-tools MCP for Google Agent Platform work.

Guidelines:
- Use google_agents_cli for Google ADK / Agent Platform lifecycle commands; \
agents-cli is a tool, not an agent_type.
- Use a2a_discover, a2a_send_task, and a2a_get_remote_task when the task \
requires external A2A interoperability.
- Use get_auth_token only when this workflow step has a bound auth profile \
and an external API call needs that credential."""

_SUB_AGENT_PREAMBLE = """\
You are a worker agent spawned by a parent operator agent. Focus entirely \
on the task you've been given. Be thorough, verify your work, and report \
results clearly."""

_TERSE_OUTPUT_CONTRACT = """\
Output contract for operator handoff:
- Be concise: no filler, no tool narration, no step-by-step diary.
- Use short sections only when useful: Summary, Decisions, Files, Risks, Next.
- Prefer file paths, artifact refs, kref/ctx refs, and exact error lines over pasted logs.
- Do not paste large raw output; summarize it and cite where to inspect it."""


def _terse_internal_outputs_enabled() -> bool:
    raw = os.environ.get("REVKA_TERSE_INTERNAL_OUTPUTS", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def build_system_prompt(
    *,
    is_top_level: bool = False,
    role_identity: str = "",
    template_hint: str = "",
    include_memory: bool = True,
    include_operator: bool = True,
    include_google_agentops: bool = False,
    skill_pattern: str = "",
) -> str:
    """Build a layered system prompt for an agent session.

    Top-level agents get the operator prompt + memory bootstrap + skills.
    Sub-agents get a simpler preamble + role identity.

    skill_pattern: orchestration pattern name (team, loop, committee, handoff, chat)
                   to inject relevant skill instructions.
    """
    from .skill_loader import load_skills_for_pattern

    parts: list[str] = []

    # Lean mode: no MCP tools → minimal preamble, no tool instructions
    no_tools = not include_memory and not include_operator and not include_google_agentops

    if is_top_level:
        if include_operator:
            parts.append(_OPERATOR_PROMPT)
        elif include_google_agentops:
            parts.append(_GOOGLE_AGENTOPS_PROMPT)
        if include_memory:
            parts.append(_MEMORY_BOOTSTRAP)
    elif no_tools:
        # Single-turn worker: skip tool instructions entirely
        parts.append(
            "You are a specialist worker agent. Focus entirely on the task. "
            "Produce your output directly — do not search, do not use tools, "
            "do not ask clarifying questions. Write your complete response."
        )
    else:
        parts.append(_SUB_AGENT_PREAMBLE)
        if include_google_agentops and not include_operator:
            parts.append(_GOOGLE_AGENTOPS_PROMPT)
        if include_memory:
            parts.append(_MEMORY_BOOTSTRAP)

    if role_identity:
        parts.append(f"\n## Your Role\n{role_identity}")

    if template_hint:
        parts.append(f"\n## Context\n{template_hint}")

    if _terse_internal_outputs_enabled():
        parts.append(_TERSE_OUTPUT_CONTRACT)

    # Inject orchestration skills if pattern specified
    if skill_pattern:
        skill_content = load_skills_for_pattern(skill_pattern)
        if skill_content:
            parts.append(f"\n## Orchestration Skills\n{skill_content}")

    return "\n\n".join(parts)
