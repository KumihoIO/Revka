"""SessionJournal — append-only JSONL journal for cross-session continuity."""
from __future__ import annotations

import json as _json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from ._log import _log


class JournalWriteError(Exception):
    """Raised when the journal cannot persist an event to disk."""


class SessionJournal:
    """Append-only JSONL journal that persists agent lifecycle events to disk.

    Each entry records a state transition (created, running, idle, error, closed)
    for an agent.  On operator startup the journal is loaded to provide full
    history of past sessions — even after the process restarts.
    """

    def __init__(self, journal_path: str | None = None) -> None:
        self.path = journal_path or os.path.expanduser(
            "~/.revka/operator_mcp/session_journal.jsonl"
        )
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._session_id = str(uuid.uuid4())[:12]
        _log(f"Session journal at {self.path} (session={self._session_id})")

    @property
    def session_id(self) -> str:
        return self._session_id

    # -- Write ----------------------------------------------------------------

    def record(
        self,
        agent_id: str,
        event: str,
        *,
        agent_type: str = "",
        title: str = "",
        cwd: str = "",
        template: str = "",
        exit_code: int | None = None,
        summary: str = "",
        sidecar_id: str = "",
        prompt_length: int | None = None,
    ) -> None:
        """Append a single event to the journal."""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session": self._session_id,
            "agent_id": agent_id,
            "event": event,
        }
        if agent_type:
            entry["agent_type"] = agent_type
        if title:
            entry["title"] = title
        if cwd:
            entry["cwd"] = cwd
        if template:
            entry["template"] = template
        if exit_code is not None:
            entry["exit_code"] = exit_code
        if summary:
            entry["summary"] = summary
        if sidecar_id:
            entry["sidecar_id"] = sidecar_id
        if prompt_length is not None:
            entry["prompt_length"] = prompt_length
        try:
            with open(self.path, "a") as f:
                f.write(_json.dumps(entry, default=str) + "\n")
        except Exception as e:
            _log(f"CRITICAL: Journal write failed: {e}")
            raise JournalWriteError(f"Cannot persist event: {e}") from e

    # -- Read -----------------------------------------------------------------

    def load_history(
        self,
        limit: int = 50,
        session_id: str | None = None,
        agent_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Load journal entries, newest first. Optionally filter by session or agent."""
        if not os.path.exists(self.path):
            return []
        entries: list[dict[str, Any]] = []
        try:
            with open(self.path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = _json.loads(line)
                    except _json.JSONDecodeError:
                        continue
                    if session_id and entry.get("session") != session_id:
                        continue
                    if agent_id and entry.get("agent_id") != agent_id:
                        continue
                    entries.append(entry)
        except Exception as e:
            _log(f"Journal read error: {e}")
        entries.reverse()
        return entries[:limit]

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return a summary of distinct sessions from the journal."""
        if not os.path.exists(self.path):
            return []
        sessions: dict[str, dict[str, Any]] = {}
        try:
            with open(self.path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = _json.loads(line)
                    except _json.JSONDecodeError:
                        continue
                    sid = entry.get("session", "")
                    if not sid:
                        continue
                    if sid not in sessions:
                        sessions[sid] = {
                            "session_id": sid,
                            "started_at": entry.get("ts", ""),
                            "agent_count": 0,
                            "agents": set(),
                            "last_event": entry.get("ts", ""),
                            "events": 0,
                        }
                    s = sessions[sid]
                    s["events"] += 1
                    s["last_event"] = entry.get("ts", "")
                    aid = entry.get("agent_id", "")
                    if aid and aid not in s["agents"]:
                        s["agents"].add(aid)
                        s["agent_count"] += 1
        except Exception as e:
            _log(f"Journal session list error: {e}")

        result = []
        for s in sessions.values():
            del s["agents"]
            result.append(s)
        result.sort(key=lambda x: x["last_event"], reverse=True)
        return result[:limit]
