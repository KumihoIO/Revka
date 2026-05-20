"""Agent subprocess spawn/monitor — CLI subprocess model.

Workflow agents are spawned as `claude --print` subprocesses.  Prompts
are written to temp .md files and piped via stdin to avoid ARG_MAX and
shell-encoding issues with Korean/Unicode text.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
from typing import Any

from ._log import _log
from .agent_state import ManagedAgent
from .clean_env import build_agent_env, clean_build_caches
from .journal import SessionJournal
from .run_log import get_log, get_or_create_log

# Temp dir for agent prompt files — survives individual agent lifecycle
_PROMPT_DIR = os.path.expanduser("~/.construct/tmp/agent_prompts")
os.makedirs(_PROMPT_DIR, exist_ok=True)

# Stderr patterns that are harmless noise (gRPC fd warnings, telemetry, etc.)
_STDERR_NOISE_PATTERNS = re.compile(
    r"ev_poll_posix|"
    r"grpc_.*warning|"
    r"GrowthBook|"
    r"telemetry|"
    r"ExperimentalWarning|"
    r"^\s*$",
    re.IGNORECASE,
)

_TERSE_OUTPUT_CONTRACT = """\
## Output Contract
- Concise handoff only: summary, decisions, files, risks, next.
- Prefer paths/artifact refs over pasted logs or repeated context.
- Include exact errors only when they affect the next step.
- No filler, status diary, or tool narration."""


def _terse_internal_outputs_enabled() -> bool:
    raw = os.environ.get("CONSTRUCT_TERSE_INTERNAL_OUTPUTS", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _is_stderr_noise(line: str) -> bool:
    """Return True if a stderr line is harmless noise, not a real error."""
    return bool(_STDERR_NOISE_PATTERNS.search(line))


def _write_prompt_file(agent_id: str, prompt: str) -> str:
    """Write prompt to a temp .md file, return the path."""
    path = os.path.join(_PROMPT_DIR, f"{agent_id}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(prompt)
    return path


def _write_mcp_config_file(agent_id: str, mcp_servers: dict[str, Any]) -> str:
    """Write an MCP config JSON to a per-agent temp file. Returns the path.

    `claude --print --mcp-config <path-or-json>` accepts a JSON file
    matching the same `{"mcpServers": {...}}` shape that the Claude
    Agent SDK expects, so subprocess agents can register the operator
    + kumiho-memory MCP servers their sidecar siblings get for free.
    """
    path = os.path.join(_PROMPT_DIR, f"{agent_id}.mcp.json")
    payload = {"mcpServers": mcp_servers}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return path


def _codex_mcp_overrides(mcp_servers: dict[str, Any]) -> list[str]:
    """Translate the MCP server dict into codex `-c` flag pairs.

    `codex exec` doesn't accept a `--mcp-config` file flag; instead it
    takes `-c key=value` overrides parsed as TOML. Each leaf is emitted
    as its own `-c` so we can sidestep nested TOML escaping. JSON
    string syntax is a subset of TOML basic-string syntax, so
    `json.dumps()` produces values codex will parse correctly.
    """
    flags: list[str] = []
    for name, config in mcp_servers.items():
        prefix = f"mcp_servers.{name}"
        command = config.get("command")
        if command:
            flags.extend(["-c", f"{prefix}.command={json.dumps(command)}"])
        cmd_args = config.get("args")
        if cmd_args:
            flags.extend(["-c", f"{prefix}.args={json.dumps(cmd_args)}"])
        env = config.get("env") or {}
        for env_key, env_val in env.items():
            flags.extend([
                "-c",
                f"{prefix}.env.{env_key}={json.dumps(env_val)}",
            ])
    return flags


def _resolve_cli(name: str) -> str:
    """Resolve a CLI binary name to its full path on disk.

    asyncio.create_subprocess_exec on Windows calls CreateProcess with the
    bare name we hand it — and CreateProcess does NOT search PATH or apply
    PATHEXT the way cmd.exe / PowerShell do. Both `claude` and `codex` are
    installed by npm as `.cmd` shims (e.g. `claude.cmd`); spawning the
    bare name "claude" then fails with WinError 2 even though `claude` runs
    fine from a shell prompt.

    `shutil.which()` does the right thing on every OS — it walks PATH and
    on Windows applies PATHEXT, returning the full path including extension.
    On POSIX it just returns the bare path. Falling back to the original
    name lets the eventual subprocess error surface the missing binary
    rather than us swallowing it here.
    """
    return shutil.which(name) or name


def _build_command(
    agent_type: str, *,
    model: str | None = None,
    mcp_config_path: str | None = None,
    mcp_servers: dict[str, Any] | None = None,
) -> list[str]:
    if agent_type == "codex":
        cmd = [_resolve_cli("codex"), "exec", "--full-auto", "--skip-git-repo-check"]
        if mcp_servers:
            cmd.extend(_codex_mcp_overrides(mcp_servers))
        return cmd
    # Prompt is piped via stdin — no -p flag, no ARG_MAX issues,
    # no shell encoding problems with Korean/Unicode text.
    cmd = [_resolve_cli("claude"), "--print", "--dangerously-skip-permissions"]
    if model:
        cmd.extend(["--model", model])
    if mcp_config_path:
        cmd.extend(["--mcp-config", mcp_config_path])
    return cmd


async def _read_stream(stream: asyncio.StreamReader | None, agent: ManagedAgent, target: str) -> None:
    """Read from a stream until EOF, appending to the agent buffer."""
    if stream is None:
        return
    try:
        while True:
            line = await stream.readline()
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace")
            if target == "stdout":
                agent.stdout_buffer += decoded
            else:
                # Filter harmless noise from stderr
                if _is_stderr_noise(decoded):
                    continue
                agent.stderr_buffer += decoded
    except Exception as exc:
        _log(f"Stream reader error ({target}) for agent {agent.id}: {exc}")


async def _monitor_agent(
    agent: ManagedAgent, journal: SessionJournal, cmd: list[str]
) -> None:
    """Background task: read streams and update status when process exits."""
    proc = agent.process
    if proc is None:
        return

    stdout_task = asyncio.create_task(_read_stream(proc.stdout, agent, "stdout"))
    stderr_task = asyncio.create_task(_read_stream(proc.stderr, agent, "stderr"))

    await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
    try:
        await proc.wait()
    except ProcessLookupError:
        _log(f"Agent {agent.id}: process already exited (reaped externally)")
    except Exception as e:
        _log(f"Agent {agent.id}: proc.wait() failed: {e}")

    rc = proc.returncode
    if rc is None:
        # Process vanished without a return code
        agent.status = "error"
        _log(f"Agent {agent.id}: process exited with no return code")
    elif rc == 0:
        agent.status = "idle"
    else:
        agent.status = "error"

    # Pick the most useful summary: stderr for errors, stdout otherwise
    if agent.status == "error" and agent.stderr_buffer.strip():
        summary = agent.stderr_buffer.strip()[-500:]
    else:
        summary = agent.stdout_buffer[-500:] if agent.stdout_buffer else ""

    # Mirror subprocess execution into the agent's run log. The sidecar
    # path emits structured timeline events that EventConsumer translates
    # into run_log entries; in subprocess fallback mode (no session-manager
    # running) the run log was previously left at just `[header, prompt]`
    # forever — making the dashboard's RunLog drill-down look like the
    # agent did nothing, even when it produced real output. Recording the
    # captured stdout/stderr + exit code closes that visibility gap so
    # both backends produce equivalent runlogs.
    try:
        run_log = get_log(agent.id)
        if run_log is not None:
            run_log.record_subprocess(
                command=" ".join(cmd[:3]),
                exit_code=proc.returncode,
                stdout=agent.stdout_buffer,
                stderr=agent.stderr_buffer,
            )
    except Exception as e:
        _log(f"run_log.record_subprocess failed for {agent.id[:8]}: {e}")

    try:
        journal.record(
            agent.id, agent.status,
            exit_code=proc.returncode,
            summary=summary,
        )
    except Exception as e:
        _log(f"CRITICAL: Journal write failed in monitor for {agent.id}: {e}")
        # Don't crash the monitor — agent status is already set in-memory
    _log(f"Agent {agent.id} finished with rc={proc.returncode}, status={agent.status}")
    if agent.status == "error":
        _log(f"Agent {agent.id} stderr: {agent.stderr_buffer.strip()[-300:]}")


async def spawn_agent(
    agent: ManagedAgent,
    prompt: str,
    journal: SessionJournal,
    *,
    model: str | None = None,
    clean_build: bool = False,
    node_env: str = "development",
    env_extra: dict[str, str] | None = None,
    mcp_servers: dict[str, Any] | None = None,
) -> None:
    """Spawn the CLI subprocess and kick off the background monitor.

    Prompts are written to a temp .md file and piped via stdin to avoid
    ARG_MAX limits and shell-encoding issues with Korean/Unicode text.

    When `mcp_servers` is provided, the dict is injected into the spawned
    CLI so subprocess-mode agents see the same MCP servers (kumiho-memory,
    operator-tools) as sidecar-mode agents. The mechanism is per-CLI:
      - Claude: serialize to a temp JSON file and pass `--mcp-config <path>`
      - Codex: emit `-c mcp_servers.<name>.<field>=<toml-value>` overrides
    """
    # Claude consumes MCP config from a JSON file; codex doesn't read
    # files (only `-c` overrides) so we skip the write for codex agents.
    mcp_config_path: str | None = None
    if mcp_servers and agent.agent_type != "codex":
        mcp_config_path = _write_mcp_config_file(agent.id, mcp_servers)

    cmd = _build_command(
        agent.agent_type,
        model=model,
        mcp_config_path=mcp_config_path,
        mcp_servers=mcp_servers,
    )
    cwd = os.path.expanduser(agent.cwd)

    # Build sanitized environment
    env = build_agent_env(clean_build=clean_build, node_env=node_env, extra=env_extra)

    # Optionally clean build caches before spawning
    if clean_build:
        cleaned = clean_build_caches(cwd)
        if cleaned:
            _log(f"Agent {agent.id}: cleaned {len(cleaned)} cache dir(s) in {cwd}")

    # Write prompt to temp file for stdin pipe
    prompt_path = _write_prompt_file(agent.id, prompt)

    # Workflow/pattern agents can enter the subprocess backend without going
    # through tool_create_agent, so seed their RunLog here. Top-level agents
    # already have a log and prompt entry; avoid duplicating that prompt.
    run_log = get_log(agent.id)
    if run_log is None:
        run_log = get_or_create_log(
            agent.id,
            title=agent.title,
            agent_type=agent.agent_type,
            cwd=cwd,
        )
        run_log.record_prompt(prompt)

    _log(f"Spawning agent {agent.id}: {cmd[:3]}... ({len(prompt)} chars) in {cwd} [prompt={prompt_path}]")
    prompt_fh = None
    try:
        prompt_fh = open(prompt_path, "r", encoding="utf-8")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            env=env,
            stdin=prompt_fh,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as exc:
        agent.status = "error"
        agent.stderr_buffer += f"Failed to spawn: {exc}\n"
        _log(f"Spawn failed for agent {agent.id}: {exc}")
        return
    finally:
        if prompt_fh:
            prompt_fh.close()

    agent.process = proc
    agent.status = "running"
    try:
        journal.record(agent.id, "running", title=agent.title)
    except Exception as e:
        _log(f"CRITICAL: Journal write failed for spawn of {agent.id}: {e}")
        # Process is already running — continue, but state may diverge on restart
    agent._reader_task = asyncio.create_task(_monitor_agent(agent, journal, cmd))


# -- Spawn with retry (for team deployments) ---------------------------------

_TEAM_SPAWN_STAGGER_SECS = 3.0
_TEAM_MAX_CONCURRENT = 3


async def spawn_with_retry(agent: ManagedAgent, prompt: str, journal: SessionJournal, max_retries: int = 2) -> bool:
    """Spawn agent with retry on immediate failure. Returns True on success."""
    for attempt in range(max_retries + 1):
        await spawn_agent(agent, prompt, journal)

        if agent.status == "error" and agent.process is None:
            if attempt < max_retries:
                wait = _TEAM_SPAWN_STAGGER_SECS * (attempt + 1)
                _log(f"Agent {agent.id} spawn failed (attempt {attempt + 1}/{max_retries + 1}), retrying in {wait}s")
                await asyncio.sleep(wait)
                agent.status = "running"
                agent.stdout_buffer = ""
                agent.stderr_buffer = ""
                continue
            return False

        # Wait briefly to see if it dies immediately
        await asyncio.sleep(1.0)
        if agent.status == "error":
            if attempt < max_retries:
                wait = _TEAM_SPAWN_STAGGER_SECS * (attempt + 1)
                _log(f"Agent {agent.id} died immediately (attempt {attempt + 1}/{max_retries + 1}), retrying in {wait}s")
                await asyncio.sleep(wait)
                agent.status = "running"
                agent.stdout_buffer = ""
                agent.stderr_buffer = ""
                continue
            return False

        return True
    return False


def compose_agent_prompt(
    name: str, role: str, identity: str, expertise: list[str], task: str,
    upstream_deliverables: str = "",
) -> str:
    """Build a structured prompt for a team agent."""
    parts = [
        f"You are {name}, a {role} agent.",
    ]
    if identity:
        parts.append(f"\n## Identity\n{identity}")
    if expertise:
        parts.append(f"\n## Expertise\n{', '.join(expertise)}")
    if upstream_deliverables:
        parts.append(
            f"\n## Upstream Deliverables\n{upstream_deliverables}"
            "\n\n### How to use deliverables\n"
            "- Read the listed files directly to inspect upstream work\n"
            "- The outcome kref links to the Kumiho graph — you can use kumiho tools to query artifacts and provenance\n"
            "- Focus your work on building upon or reviewing these deliverables\n"
            "- If changes include a diff, review it carefully before proceeding"
        )
    parts.append(f"\n## Task\n{task}")
    if _terse_internal_outputs_enabled():
        parts.append(_TERSE_OUTPUT_CONTRACT)
    return "\n".join(parts)
