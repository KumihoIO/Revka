"""Structured per-agent run logs — full audit trail for debugging and review.

Each agent gets a JSONL file at ~/.construct/operator_mcp/runlogs/{agent_id}.jsonl.
Every event is appended with full detail: timestamps, tool calls with args/results,
shell commands with exit codes and stdout/stderr, files touched, reasoning blocks,
and assistant messages.

Unlike EventConsumer's in-memory buffer (capped at 200, lost on restart), run logs
are persistent and queryable after agent completion.

Usage:
    log = RunLog(agent_id, title="coder-Pixel")
    log.record_event(raw_event)        # from sidecar SSE
    log.record_subprocess(cmd, rc, stdout, stderr)  # from subprocess backend

    # Query
    log.get_summary()           # overview with counts and last failing command
    log.get_tool_calls()        # all tool calls with full args/results
    log.get_errors()            # all errors and failures
    log.get_full_log(limit=50)  # last N entries
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

try:
    from ._log import _log
except ImportError:
    import sys
    _log = lambda msg: sys.stderr.write(f"[run_log] {msg}\n")

_RUNLOGS_DIR = os.path.expanduser("~/.construct/operator_mcp/runlogs")


def _ensure_dir() -> None:
    os.makedirs(_RUNLOGS_DIR, exist_ok=True)


class RunLog:
    """Append-only structured log for a single agent run."""

    def __init__(self, agent_id: str, *, title: str = "", agent_type: str = "", cwd: str = "") -> None:
        self.agent_id = agent_id
        self.title = title
        self.agent_type = agent_type
        self.cwd = cwd

        _ensure_dir()
        self._path = os.path.join(_RUNLOGS_DIR, f"{agent_id}.jsonl")

        # In-memory indexes for fast queries (rebuilt from disk on load)
        self._entry_count = 0
        self._tool_calls: list[dict[str, Any]] = []
        self._errors: list[dict[str, Any]] = []
        self._files_touched: set[str] = set()
        self._last_failing_command: dict[str, Any] | None = None
        self._last_message: str = ""
        self._usage: dict[str, Any] = {}
        self._status: str = "initializing"

        # Write header entry
        self._append({
            "kind": "header",
            "agent_id": agent_id,
            "title": title,
            "agent_type": agent_type,
            "cwd": cwd,
        })

    def _append(self, entry: dict[str, Any]) -> None:
        """Append a single entry to the JSONL file."""
        entry.setdefault("ts", datetime.now(timezone.utc).isoformat())
        entry.setdefault("seq", self._entry_count)
        self._entry_count += 1
        try:
            with open(self._path, "a") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as e:
            _log(f"RunLog: write error for {self.agent_id[:8]}: {e}")

    def record_event(self, raw_event: dict[str, Any]) -> None:
        """Record a raw sidecar SSE event with full detail."""
        inner = raw_event.get("event", raw_event)
        ev_type = inner.get("type", "")
        timestamp = raw_event.get("timestamp", "")

        if ev_type == "timeline":
            item = inner.get("item", {})
            item_type = item.get("type", "")

            if item_type == "tool_call":
                entry = {
                    "kind": "tool_call",
                    "ts": timestamp,
                    "name": item.get("name", ""),
                    "args": item.get("args", ""),
                    "status": item.get("status", ""),
                    "result": item.get("result", ""),
                    "error": item.get("error", ""),
                }
                self._append(entry)
                self._tool_calls.append(entry)

                # Track files touched
                name = item.get("name", "")
                args_str = item.get("args", "")
                if name in ("Edit", "Write", "Read", "edit_file", "create_file", "write_file", "read_file"):
                    self._extract_file_path(args_str)

                # Track shell commands
                if name in ("Bash", "execute_command"):
                    self._extract_command(entry)

                # Track failures
                if item.get("status") == "failed" or item.get("error"):
                    self._errors.append(entry)
                    if name in ("Bash", "execute_command"):
                        self._last_failing_command = entry

            elif item_type == "assistant_message":
                text = item.get("text", "")
                self._append({
                    "kind": "message",
                    "ts": timestamp,
                    "text": text,
                })
                if text:
                    self._last_message = text

            elif item_type == "reasoning":
                self._append({
                    "kind": "reasoning",
                    "ts": timestamp,
                    "text": item.get("text", ""),
                })

            elif item_type == "error":
                entry = {
                    "kind": "error",
                    "ts": timestamp,
                    "message": item.get("message", ""),
                }
                self._append(entry)
                self._errors.append(entry)

            elif item_type == "user_message":
                self._append({
                    "kind": "user_message",
                    "ts": timestamp,
                    "text": item.get("text", ""),
                })

        elif ev_type == "turn_started":
            self._append({
                "kind": "turn_started",
                "ts": timestamp,
                "turn_id": inner.get("turnId", ""),
            })

        elif ev_type == "turn_completed":
            usage = inner.get("usage", {})
            if usage:
                self._usage = {
                    "input_tokens": self._usage.get("input_tokens", 0) + (usage.get("inputTokens") or 0),
                    "output_tokens": self._usage.get("output_tokens", 0) + (usage.get("outputTokens") or 0),
                    "total_cost_usd": self._usage.get("total_cost_usd", 0) + (usage.get("totalCostUsd") or 0),
                }
            self._append({
                "kind": "turn_completed",
                "ts": timestamp,
                "turn_id": inner.get("turnId", ""),
                "usage": usage,
            })

        elif ev_type == "turn_failed":
            entry = {
                "kind": "turn_failed",
                "ts": timestamp,
                "turn_id": inner.get("turnId", ""),
                "error": inner.get("error", ""),
            }
            self._append(entry)
            self._errors.append(entry)

        elif ev_type == "status_changed":
            self._status = inner.get("status", "")
            self._append({
                "kind": "status_changed",
                "ts": timestamp,
                "status": self._status,
            })

        elif ev_type == "session_started":
            self._append({
                "kind": "session_started",
                "ts": timestamp,
                "session_id": inner.get("sessionId", ""),
                "provider": inner.get("provider", ""),
            })

        elif ev_type == "session_closed":
            self._append({
                "kind": "session_closed",
                "ts": timestamp,
            })

    def record_subprocess(
        self,
        command: str,
        exit_code: int | None,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        """Record a subprocess command execution (for subprocess backend)."""
        entry: dict[str, Any] = {
            "kind": "subprocess",
            "command": command,
            "exit_code": exit_code,
            "stdout": stdout[-5000:] if stdout else "",
            "stderr": stderr[-2000:] if stderr else "",
        }
        self._append(entry)
        if exit_code is None or exit_code != 0:
            self._status = "failed"
            self._errors.append(entry)
            self._last_failing_command = entry
        else:
            self._status = "completed"
        if stdout:
            self._last_message = stdout.strip()

    def record_prompt(self, prompt: str) -> None:
        """Record the initial prompt sent to the agent."""
        self._append({
            "kind": "prompt",
            "text": prompt,
            "length": len(prompt),
        })

    # -- Query methods ---------------------------------------------------------

    def get_summary(self) -> dict[str, Any]:
        """Return a structured summary of the agent run."""
        return {
            "agent_id": self.agent_id,
            "title": self.title,
            "agent_type": self.agent_type,
            "cwd": self.cwd,
            "status": self._status,
            "total_events": self._entry_count,
            "tool_call_count": len(self._tool_calls),
            "error_count": len(self._errors),
            "files_touched": sorted(self._files_touched),
            "last_failing_command": self._last_failing_command,
            "last_message": self._last_message[-2000:] if self._last_message else "",
            "usage": self._usage,
        }

    def get_tool_calls(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return tool calls, most recent first."""
        return list(reversed(self._tool_calls[-limit:]))

    def get_errors(self) -> list[dict[str, Any]]:
        """Return all errors and failures."""
        return list(self._errors)

    def get_full_log(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """Read entries from the JSONL file."""
        entries: list[dict[str, Any]] = []
        try:
            with open(self._path, "r") as f:
                for i, line in enumerate(f):
                    if i < offset:
                        continue
                    if len(entries) >= limit:
                        break
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except FileNotFoundError:
            pass
        return entries

    def get_files_touched(self) -> list[str]:
        """Return sorted list of files this agent read or modified."""
        return sorted(self._files_touched)

    # -- Internal helpers ------------------------------------------------------

    def _extract_file_path(self, args_str: str) -> None:
        """Extract file_path from tool call args JSON."""
        try:
            args = json.loads(args_str) if isinstance(args_str, str) else args_str
            path = args.get("file_path") or args.get("path") or args.get("file") or ""
            if path:
                self._files_touched.add(path)
        except (json.JSONDecodeError, AttributeError):
            pass

    def _extract_command(self, entry: dict[str, Any]) -> None:
        """Extract command string from Bash/execute_command args."""
        try:
            args = json.loads(entry.get("args", "{}")) if isinstance(entry.get("args"), str) else entry.get("args", {})
            cmd = args.get("command") or args.get("cmd") or ""
            if cmd:
                entry["command"] = cmd
        except (json.JSONDecodeError, AttributeError):
            pass


# -- Registry: maps agent_id → RunLog instance --------------------------------

_LOGS: dict[str, RunLog] = {}


def get_or_create_log(
    agent_id: str,
    *,
    title: str = "",
    agent_type: str = "",
    cwd: str = "",
) -> RunLog:
    """Get existing RunLog or create a new one."""
    if agent_id not in _LOGS:
        _LOGS[agent_id] = RunLog(agent_id, title=title, agent_type=agent_type, cwd=cwd)
    return _LOGS[agent_id]


def get_log(agent_id: str) -> RunLog | None:
    """Get RunLog for an agent, or None if not tracked."""
    return _LOGS.get(agent_id)


def cleanup_logs(*, max_in_memory: int = 100) -> int:
    """Evict oldest completed logs from in-memory registry. Returns count removed.

    Logs remain on disk — only the in-memory index is trimmed.
    """
    if len(_LOGS) <= max_in_memory:
        return 0
    # Keep running agents, evict completed ones by entry count (proxy for age)
    from .agent_state import AGENTS
    evictable = [
        (aid, log) for aid, log in _LOGS.items()
        if aid not in AGENTS or AGENTS[aid].status not in ("running", "idle")
    ]
    evictable.sort(key=lambda x: x[1]._entry_count)
    to_remove = len(_LOGS) - max_in_memory
    removed = 0
    for aid, _ in evictable[:to_remove]:
        del _LOGS[aid]
        removed += 1
    return removed


def load_log_from_disk(agent_id: str) -> RunLog | None:
    """Load a RunLog from disk for a completed agent (replay JSONL to rebuild indexes)."""
    path = os.path.join(_RUNLOGS_DIR, f"{agent_id}.jsonl")
    if not os.path.exists(path):
        return None

    log = RunLog.__new__(RunLog)
    log.agent_id = agent_id
    log._path = path
    log._entry_count = 0
    log._tool_calls = []
    log._errors = []
    log._files_touched = set()
    log._last_failing_command = None
    log._last_message = ""
    log._usage = {}
    log._status = "unknown"
    log.title = ""
    log.agent_type = ""
    log.cwd = ""

    try:
        with open(path, "r") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                log._entry_count += 1
                kind = entry.get("kind", "")

                if kind == "header":
                    log.title = entry.get("title", "")
                    log.agent_type = entry.get("agent_type", "")
                    log.cwd = entry.get("cwd", "")
                elif kind == "tool_call":
                    log._tool_calls.append(entry)
                    if entry.get("status") == "failed" or entry.get("error"):
                        log._errors.append(entry)
                        name = entry.get("name", "")
                        if name in ("Bash", "execute_command"):
                            log._last_failing_command = entry
                    name = entry.get("name", "")
                    if name in ("Edit", "Write", "Read", "edit_file", "create_file", "write_file", "read_file"):
                        log._extract_file_path(entry.get("args", ""))
                elif kind == "message":
                    text = entry.get("text", "")
                    if text:
                        log._last_message = text
                elif kind in ("error", "turn_failed"):
                    log._errors.append(entry)
                elif kind == "status_changed":
                    log._status = entry.get("status", "")
                elif kind == "turn_completed":
                    usage = entry.get("usage", {})
                    if usage:
                        log._usage = {
                            "input_tokens": log._usage.get("input_tokens", 0) + (usage.get("inputTokens") or 0),
                            "output_tokens": log._usage.get("output_tokens", 0) + (usage.get("outputTokens") or 0),
                            "total_cost_usd": log._usage.get("total_cost_usd", 0) + (usage.get("totalCostUsd") or 0),
                        }
                elif kind == "subprocess":
                    exit_code = entry.get("exit_code")
                    if exit_code is None or exit_code != 0:
                        log._status = "failed"
                        log._errors.append(entry)
                        log._last_failing_command = entry
                    else:
                        log._status = "completed"
                    stdout = entry.get("stdout", "")
                    if stdout:
                        log._last_message = stdout.strip()

    except Exception as e:
        _log(f"RunLog: failed to load {agent_id}: {e}")
        return None

    _LOGS[agent_id] = log
    return log


def list_run_logs() -> list[dict[str, Any]]:
    """List all run log files on disk with basic metadata."""
    _ensure_dir()
    logs: list[dict[str, Any]] = []
    try:
        for fname in os.listdir(_RUNLOGS_DIR):
            if not fname.endswith(".jsonl"):
                continue
            agent_id = fname[:-6]  # strip .jsonl
            path = os.path.join(_RUNLOGS_DIR, fname)
            size = os.path.getsize(path)

            # Read just the header line for metadata
            title = ""
            agent_type = ""
            try:
                with open(path, "r") as f:
                    first_line = f.readline()
                    header = json.loads(first_line)
                    title = header.get("title", "")
                    agent_type = header.get("agent_type", "")
            except Exception:
                pass

            logs.append({
                "agent_id": agent_id,
                "title": title,
                "agent_type": agent_type,
                "size_bytes": size,
                "path": path,
            })
    except Exception:
        pass

    return sorted(logs, key=lambda x: x.get("path", ""), reverse=True)
