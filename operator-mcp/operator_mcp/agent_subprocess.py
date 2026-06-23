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
_PROMPT_DIR = os.path.expanduser("~/.revka/tmp/agent_prompts")
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
    raw = os.environ.get("REVKA_TERSE_INTERNAL_OUTPUTS", "1").strip().lower()
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


def _write_agent_home_configs(agent_id: str, agent_type: str, mcp_servers: dict[str, Any]) -> tuple[str, dict[str, str]]:
    """Write dynamic MCP configurations into a temporary isolated HOME directory
    for agent CLIs that do not accept --mcp-config directly on the command line
    (specifically opencode, agy, and agent).
    Returns the path to the temporary home directory and a dict of env overrides.
    """
    agent_home = os.path.join(_PROMPT_DIR, "homes", agent_id)
    os.makedirs(agent_home, exist_ok=True)

    extra_env = {
        "HOME": agent_home,
        "USERPROFILE": agent_home,
        "HOMEPATH": agent_home,
        "XDG_CONFIG_HOME": os.path.join(agent_home, ".config"),
    }

    # Symlink real gcloud config and .agents directory to the sandbox if they exist.
    # This allows private Cloud Run identity-token minting and update checking within the isolated HOME.
    real_home = os.path.expanduser("~")
    real_gcloud_dir = os.path.join(real_home, ".config", "gcloud")
    if os.path.exists(real_gcloud_dir):
        sandbox_config_dir = os.path.join(agent_home, ".config")
        os.makedirs(sandbox_config_dir, exist_ok=True)
        sandbox_gcloud_dir = os.path.join(sandbox_config_dir, "gcloud")
        if not os.path.exists(sandbox_gcloud_dir):
            try:
                os.symlink(real_gcloud_dir, sandbox_gcloud_dir)
            except Exception as e:
                _log(f"Warning: failed to symlink real gcloud config to sandbox: {e}")

    real_agents_dir = os.path.join(real_home, ".agents")
    if os.path.exists(real_agents_dir):
        sandbox_agents_dir = os.path.join(agent_home, ".agents")
        if not os.path.exists(sandbox_agents_dir):
            try:
                os.symlink(real_agents_dir, sandbox_agents_dir)
            except Exception as e:
                _log(f"Warning: failed to symlink real .agents directory: {e}")

    if agent_type == "opencode":
        # Write config at ~/.config/opencode/config.json and opencode.json
        # and translate to opencode local format
        opencode_dir = os.path.join(agent_home, ".config", "opencode")
        os.makedirs(opencode_dir, exist_ok=True)
        
        opencode_mcp = {}
        for name, cfg in mcp_servers.items():
            cmd_list = [cfg["command"]] + cfg.get("args", [])
            opencode_mcp[name] = {
                "type": "local",
                "command": cmd_list,
                "enabled": True,
                "environment": cfg.get("env", {})
            }
        
        payload = {
            "$schema": "https://opencode.ai/config.json",
            "mcp": opencode_mcp
        }
        
        for filename in ("config.json", "opencode.json"):
            with open(os.path.join(opencode_dir, filename), "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)

    elif agent_type == "agy":
        # Write config at ~/.gemini/antigravity-cli/mcp_config.json
        agy_dir = os.path.join(agent_home, ".gemini", "antigravity-cli")
        os.makedirs(agy_dir, exist_ok=True)
        
        # Copy real credentials/settings if they exist to prevent auth prompt in sandbox
        real_home = os.path.expanduser("~")
        real_agy_dir = os.path.join(real_home, ".gemini", "antigravity-cli")
        if os.path.exists(real_agy_dir):
            import shutil
            for name in ("antigravity-oauth-token", "settings.json", "installation_id"):
                src = os.path.join(real_agy_dir, name)
                if os.path.exists(src):
                    try:
                        shutil.copy2(src, os.path.join(agy_dir, name))
                    except Exception as e:
                        _log(f"Warning: failed to copy real agy credential file {name}: {e}")

        agy_mcp = {}
        for name, cfg in mcp_servers.items():
            agy_mcp[name] = {
                "command": cfg["command"],
                "args": cfg.get("args", []),
                "env": cfg.get("env", {})
            }
            
        payload = {"mcpServers": agy_mcp}
        with open(os.path.join(agy_dir, "mcp_config.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    elif agent_type == "agent":
        # Write config at ~/.cursor/mcp.json (Cursor CLI)
        cursor_dir = os.path.join(agent_home, ".cursor")
        os.makedirs(cursor_dir, exist_ok=True)
        
        # Format is exactly the standard mcpServers structure
        payload = {"mcpServers": mcp_servers}
        with open(os.path.join(cursor_dir, "mcp.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    return agent_home, extra_env


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
        # Approval flags are top-level Codex options, so they must precede `exec`.
        cmd = [
            _resolve_cli("codex"),
            "--ask-for-approval",
            "never",
            "--sandbox",
            "danger-full-access",
            "exec",
            "--skip-git-repo-check",
        ]
        if mcp_servers:
            cmd.extend(_codex_mcp_overrides(mcp_servers))
        return cmd
    binary_name = agent_type if agent_type in ("claude", "agy", "agent", "opencode") else "claude"

    if binary_name == "opencode":
        cmd = [_resolve_cli("opencode"), "run"]
    else:
        # Prompt is piped via stdin — no -p flag, no ARG_MAX issues,
        # no shell encoding problems with Korean/Unicode text.
        cmd = [_resolve_cli(binary_name), "--print", "--dangerously-skip-permissions"]
        if binary_name == "agy":
            # Antigravity's print mode defaults to a 5-minute wait and then
            # emits "Error: timed out waiting for response" as its entire
            # output (exit 0), failing any longer-running workflow step.
            # Give it headroom beyond the longest step budget instead.
            cmd.extend(["--print-timeout", "30m"])
        if model:
            cmd.extend(["--model", model])
        if mcp_config_path and agent_type == "claude":
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


def _claude_spawn_anthropic_key(
    agent_type: str,
    env: dict[str, str],
    spawn_key: str | None,
) -> str | None:
    """Value to set as ``ANTHROPIC_API_KEY`` for a spawned agent, or ``None``.

    The Rust daemon hands the operator the decrypted Anthropic key under the
    dedicated ``REVKA_SPAWN_ANTHROPIC_API_KEY`` name (never ``ANTHROPIC_API_KEY``)
    so it is never copied into an MCP-server config and serialized onto a codex
    command line (see ``_kumiho_forward_env`` / ``_codex_mcp_overrides``, which
    would expose it in process listings). We translate it back to
    ``ANTHROPIC_API_KEY`` only for ``claude`` agents — which authenticate against
    api.anthropic.com via that var — and only when the child has no explicit
    credential already (an inherited ``ANTHROPIC_API_KEY`` or a
    ``CLAUDE_CODE_OAUTH_TOKEN`` subscription token both take precedence).
    """
    if not spawn_key or agent_type != "claude":
        return None
    if env.get("ANTHROPIC_API_KEY") or env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return None
    return spawn_key


# Agent types whose CLI launches with permissions fully bypassed
# (codex: --sandbox danger-full-access; claude/agy/agent:
# --dangerously-skip-permissions). Those flags can't be revoked once the
# process is running, so an untrusted spawn of one is refused at spawn time
# (#459). opencode has no such flag and is never refused.
_BYPASS_PERMISSION_AGENTS = frozenset({"codex", "claude", "agy", "agent"})


def cli_spawn_refusal(agent_type: str, trusted: bool) -> str | None:
    """Reason to refuse an untrusted permission-bypassing CLI spawn, or None.

    Trusted spawns (the default) always proceed. An untrusted spawn is refused
    only for CLIs we would otherwise launch with permissions bypassed — there
    is no headless-safe sandbox/approval flag to downgrade to, so the spawn is
    refused rather than run ungated.
    """
    if trusted:
        return None
    if agent_type in _BYPASS_PERMISSION_AGENTS:
        return (
            f"Refusing to spawn untrusted '{agent_type}' agent: it would run with "
            "permissions bypassed and cannot be gated after launch. Mark the spawn "
            "trusted to allow it."
        )
    return None


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
    refusal = cli_spawn_refusal(agent.agent_type, agent.trusted)
    if refusal:
        agent.status = "error"
        agent.stderr_buffer += refusal + "\n"
        _log(f"Agent {agent.id}: {refusal}")
        return

    # Claude consumes MCP config from a JSON file; codex doesn't read
    # files (only `-c` overrides) so we skip the write for codex agents.
    mcp_config_path: str | None = None
    if mcp_servers and agent.agent_type != "codex":
        mcp_config_path = _write_mcp_config_file(agent.id, mcp_servers)

    # Write configs and prepare home redirection environment variables for opencode, agy, and agent
    home_env: dict[str, str] = {}
    if mcp_servers and agent.agent_type in ("opencode", "agy", "agent"):
        _, home_env = _write_agent_home_configs(agent.id, agent.agent_type, mcp_servers)

    cmd = _build_command(
        agent.agent_type,
        model=model,
        mcp_config_path=mcp_config_path,
        mcp_servers=mcp_servers,
    )
    cwd = os.path.expanduser(agent.cwd)

    # Build sanitized environment
    merged_env_extra = dict(env_extra) if env_extra else {}
    merged_env_extra.update(home_env)
    env = build_agent_env(clean_build=clean_build, node_env=node_env, extra=merged_env_extra)

    # The Rust daemon supplies revka's decrypted Anthropic key under a dedicated,
    # non-forwarded name so it never lands on a codex command line. Pop that
    # transport var (so no child carries it) and, for `claude` agents only,
    # expose it as ANTHROPIC_API_KEY in the child *process env* (never a CLI arg).
    _spawn_anthropic_key = os.environ.get("REVKA_SPAWN_ANTHROPIC_API_KEY")
    env.pop("REVKA_SPAWN_ANTHROPIC_API_KEY", None)
    _claude_key = _claude_spawn_anthropic_key(agent.agent_type, env, _spawn_anthropic_key)
    if _claude_key:
        env["ANTHROPIC_API_KEY"] = _claude_key

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
