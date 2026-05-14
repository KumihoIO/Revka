"""Event consumer — bridges sidecar SSE streams to gateway channel events.

Subscribes to per-agent SSE event streams from the TS Session Manager sidecar,
translates raw timeline events into structured channel events, and pushes them
to the Construct gateway for broadcast to connected channels (dashboard, Slack,
Discord).

Channel event types:
  agent.started     — agent spawned with task summary
  agent.tool_use    — significant tool calls (file writes, not reads)
  agent.completed   — final result with usage stats
  agent.error       — failure with stderr excerpt
  agent.permission  — permission request needing user approval
"""
from __future__ import annotations

import asyncio
from typing import Any

import json as _json

from ._log import _log
from .gateway_client import ConstructGatewayClient
from .run_log import get_or_create_log
from .session_manager_client import SessionManagerClient

# Tool calls worth broadcasting (skip noisy read-only tools)
_SIGNIFICANT_TOOLS = frozenset({
    "Edit", "Write", "Bash", "NotebookEdit",
    "edit_file", "create_file", "execute_command",
    "write_file", "delete_file", "move_file",
})


def _tool_detail(tool_name: str, args_raw: str) -> str:
    """Extract a short human-readable detail from tool args."""
    try:
        args = _json.loads(args_raw) if isinstance(args_raw, str) and args_raw else {}
    except (ValueError, TypeError):
        args = {}
    if not isinstance(args, dict):
        return ""
    if tool_name in ("Bash", "execute_command"):
        cmd = args.get("command", "")
        # Show first line, truncated
        first_line = cmd.split("\n")[0].strip() if cmd else ""
        return first_line[:120] if first_line else ""
    if tool_name in ("Edit", "Write", "edit_file", "create_file", "write_file"):
        fp = args.get("file_path", args.get("path", ""))
        if fp:
            # Show just the filename or last two path components
            parts = fp.rstrip("/").split("/")
            return "/".join(parts[-2:]) if len(parts) > 1 else fp
    if tool_name in ("delete_file", "move_file"):
        return args.get("path", args.get("file_path", ""))[:80]
    return ""

# Max events to buffer per agent before dropping old ones
_MAX_BUFFER = 200


class ChannelEvent:
    """Structured channel event for gateway broadcast."""

    __slots__ = ("event_type", "agent_id", "agent_title", "content", "timestamp")

    def __init__(
        self,
        event_type: str,
        agent_id: str,
        agent_title: str,
        content: dict[str, Any],
        timestamp: str = "",
    ) -> None:
        self.event_type = event_type
        self.agent_id = agent_id
        self.agent_title = agent_title
        self.content = content
        self.timestamp = timestamp

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.event_type,
            "agentId": self.agent_id,
            "agentTitle": self.agent_title,
            "content": self.content,
            "timestamp": self.timestamp,
        }


class EventConsumer:
    """Consumes sidecar SSE streams and pushes channel events to gateway."""

    def __init__(
        self,
        sidecar: SessionManagerClient,
        gateway: ConstructGatewayClient,
    ) -> None:
        self._sidecar = sidecar
        self._gateway = gateway
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._agent_titles: dict[str, str] = {}
        self._agent_models: dict[str, str] = {}  # agent_id → model name for cost tracking
        self._agent_providers: dict[str, str] = {}
        self._agent_events: dict[str, list[dict[str, Any]]] = {}
        self._callbacks: list[Any] = []
        self._lock = asyncio.Lock()

    def on_channel_event(self, callback: Any) -> None:
        """Register a callback for channel events (for in-process consumers)."""
        self._callbacks.append(callback)

    def set_agent_model(self, agent_id: str, model: str) -> None:
        """Register the model used by an agent so cost events include it."""
        if model:
            self._agent_models[agent_id] = model

    async def subscribe(self, agent_id: str, title: str = "", model: str = "") -> None:
        """Start consuming events for an agent.

        If the global stream is active, only registers the agent for buffering.
        Otherwise starts a per-agent SSE connection.
        """
        async with self._lock:
            self._agent_titles[agent_id] = title or agent_id[:8]
            if model:
                self._agent_models[agent_id] = model
            if agent_id not in self._agent_events:
                self._agent_events[agent_id] = []

            # If global stream is running, no need for per-agent connection
            if "_global" in self._tasks:
                _log(f"EventConsumer: registered {agent_id} ({title}) [global stream]")
                return

            if agent_id in self._tasks:
                return
            task = asyncio.create_task(self._consume_loop(agent_id))
            self._tasks[agent_id] = task
            _log(f"EventConsumer: subscribed to {agent_id} ({title})")

    def _resolve_title(self, agent_id: str) -> str:
        """Resolve a human-readable title for an agent ID.

        Looks up the ManagedAgent registry first, then falls back to
        a short ID prefix. Prevents raw hex IDs from leaking to the UI.
        """
        try:
            from .agent_state import AGENTS
            # Direct match
            agent = AGENTS.get(agent_id)
            if agent and agent.title:
                return agent.title
            # Maybe agent_id is a sidecar_id — scan for matching agents
            for a in AGENTS.values():
                if getattr(a, "_sidecar_id", None) == agent_id and a.title:
                    return a.title
        except Exception:
            pass
        return agent_id[:8]

    async def unsubscribe(self, agent_id: str) -> None:
        """Stop consuming events for an agent."""
        async with self._lock:
            task = self._tasks.pop(agent_id, None)
            if task and not task.done():
                task.cancel()
            self._agent_titles.pop(agent_id, None)
            self._agent_models.pop(agent_id, None)
            self._agent_events.pop(agent_id, None)

    def cleanup_finished(self, active_agent_ids: set[str]) -> int:
        """Remove event buffers for agents no longer active. Returns count removed."""
        stale = [aid for aid in self._agent_events if aid != "_global" and aid not in active_agent_ids]
        for aid in stale:
            self._agent_events.pop(aid, None)
            self._agent_titles.pop(aid, None)
            task = self._tasks.pop(aid, None)
            if task and not task.done():
                task.cancel()
        return len(stale)

    def get_events(self, agent_id: str, since: int = 0) -> list[dict[str, Any]]:
        """Get buffered events for an agent (for activity queries)."""
        events = self._agent_events.get(agent_id, [])
        if since > 0:
            return events[since:]
        return list(events)

    def get_curated_activity(self, agent_id: str) -> dict[str, Any]:
        """Get a curated timeline summary for an agent.

        Returns significant events (tool calls, completions, errors) rather
        than the full raw stream. Used by get_agent_activity tool handler.
        """
        events = self._agent_events.get(agent_id, [])
        significant: list[dict[str, Any]] = []
        last_message = ""

        for ev in events:
            inner = ev.get("event", ev)
            ev_type = inner.get("type", "")

            if ev_type == "timeline":
                item = inner.get("item", {})
                item_type = item.get("type", "")
                if item_type == "assistant_message":
                    last_message = item.get("text", "")
                elif item_type == "tool_call":
                    significant.append({
                        "type": "tool_call",
                        "name": item.get("name", ""),
                        "status": item.get("status", ""),
                    })
                elif item_type == "error":
                    significant.append({
                        "type": "error",
                        "message": item.get("message", ""),
                    })
            elif ev_type == "turn_completed":
                significant.append({
                    "type": "completed",
                    "usage": inner.get("usage"),
                })
            elif ev_type == "turn_failed":
                significant.append({
                    "type": "failed",
                    "error": inner.get("error", ""),
                })

        return {
            "event_count": len(events),
            "significant_events": significant[-20:],
            "last_message": last_message[-2000:] if last_message else "",
        }

    async def start_global_stream(self) -> None:
        """Start consuming the global SSE stream (all agents).

        More efficient than per-agent streams — uses a single HTTP connection.
        Individual subscribe() calls still register agents for event buffering
        and title tracking but won't start per-agent SSE tasks.
        """
        if "_global" in self._tasks:
            return
        task = asyncio.create_task(self._global_consume_loop())
        self._tasks["_global"] = task
        _log("EventConsumer: global stream started")

    async def stop_all(self) -> None:
        """Cancel all consumer tasks."""
        for agent_id in list(self._tasks):
            await self.unsubscribe(agent_id)

    # -- Internal --------------------------------------------------------------

    async def _global_consume_loop(self) -> None:
        """Consume the global SSE stream and dispatch to per-agent buffers."""
        try:
            async for raw_event in self._sidecar.stream_all_events():
                agent_id = raw_event.get("agentId", "")
                if not agent_id:
                    continue

                # Auto-register unknown agents and update titles from headers
                async with self._lock:
                    if agent_id not in self._agent_events:
                        self._agent_events[agent_id] = []
                        if agent_id not in self._agent_titles:
                            self._agent_titles[agent_id] = self._resolve_title(agent_id)

                    # Header events from the session-manager carry the
                    # authoritative title.  Update whenever we see one so
                    # that early events with a fallback ID get corrected.
                    if raw_event.get("kind") == "header" or raw_event.get("event", {}).get("kind") == "header":
                        hdr = raw_event if "title" in raw_event else raw_event.get("event", {})
                        hdr_title = hdr.get("title", "")
                        if hdr_title and hdr_title != agent_id[:8] and hdr_title != agent_id:
                            self._agent_titles[agent_id] = hdr_title

                    # Buffer
                    buf = self._agent_events[agent_id]
                    buf.append(raw_event)
                    if len(buf) > _MAX_BUFFER:
                        del buf[:len(buf) - _MAX_BUFFER]

                # Persist to structured run log
                try:
                    title_for_log = self._agent_titles.get(agent_id, "")
                    run_log = get_or_create_log(agent_id, title=title_for_log)
                    run_log.record_event(raw_event)
                except Exception:
                    pass

                # Translate and dispatch
                title = self._agent_titles.get(agent_id, agent_id[:8])
                for ch_event in await self._translate(agent_id, title, raw_event):
                    await self._dispatch(ch_event)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            _log(f"EventConsumer: global stream error: {e}")

    async def _consume_loop(self, agent_id: str) -> None:
        """Main SSE consumption loop for a single agent."""
        title = self._agent_titles.get(agent_id, agent_id[:8])
        try:
            async for raw_event in self._sidecar.stream_events(agent_id):
                # Buffer the raw event
                buf = self._agent_events.get(agent_id)
                if buf is not None:
                    buf.append(raw_event)
                    if len(buf) > _MAX_BUFFER:
                        del buf[:len(buf) - _MAX_BUFFER]

                # Persist to structured run log
                try:
                    run_log = get_or_create_log(agent_id, title=title)
                    run_log.record_event(raw_event)
                except Exception:
                    pass

                # Translate to channel events
                channel_events = await self._translate(agent_id, title, raw_event)
                for ch_event in channel_events:
                    await self._dispatch(ch_event)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            _log(f"EventConsumer: stream error for {agent_id}: {e}")

    async def _translate(
        self,
        agent_id: str,
        title: str,
        raw: dict[str, Any],
    ) -> list[ChannelEvent]:
        """Translate a raw sidecar event into channel events."""
        inner = raw.get("event", raw)
        timestamp = raw.get("timestamp", "")
        ev_type = inner.get("type", "")
        events: list[ChannelEvent] = []

        if ev_type == "session_started":
            provider = inner.get("provider", "")
            if provider:
                self._agent_providers[agent_id] = provider
            events.append(ChannelEvent(
                "agent.started", agent_id, title,
                {"provider": provider},
                timestamp,
            ))

        elif ev_type == "status_changed" and inner.get("status") == "error":
            events.append(ChannelEvent(
                "agent.error", agent_id, title,
                {"status": "error"},
                timestamp,
            ))

        elif ev_type == "turn_completed":
            usage = inner.get("usage")
            events.append(ChannelEvent(
                "agent.completed", agent_id, title,
                {"usage": usage},
                timestamp,
            ))
            if usage:
                provider = (
                    usage.get("provider", "")
                    or self._agent_providers.get(agent_id, "")
                    or "sidecar"
                )
                # Model may come from the usage event itself, or from
                # registration (set_agent_model / subscribe). The latter is
                # more reliable because sidecars often omit the field.
                model = usage.get("model", "") or self._agent_models.get(agent_id, "")
                if not model:
                    model = f"{provider}/unknown"
                input_tokens = (
                    usage.get("inputTokens")
                    or usage.get("input_tokens")
                    or usage.get("prompt_tokens")
                    or 0
                )
                output_tokens = (
                    usage.get("outputTokens")
                    or usage.get("output_tokens")
                    or usage.get("completion_tokens")
                    or 0
                )
                await self._gateway.record_usage(
                    agent_id=agent_id,
                    model=model,
                    provider=provider,
                    input_tokens=int(input_tokens or 0),
                    output_tokens=int(output_tokens or 0),
                    source="sidecar",
                    agent_title=title,
                )

        elif ev_type == "turn_failed":
            events.append(ChannelEvent(
                "agent.error", agent_id, title,
                {"error": inner.get("error", "")[:500]},
                timestamp,
            ))

        elif ev_type == "timeline":
            item = inner.get("item", {})
            item_type = item.get("type", "")
            if item_type == "tool_call":
                tool_name = item.get("name", "")
                # Permission requests get their own channel event type
                if tool_name.startswith("permission:"):
                    events.append(ChannelEvent(
                        "agent.permission", agent_id, title,
                        {
                            "tool": tool_name.replace("permission:", ""),
                            "args": item.get("args", ""),
                        },
                        timestamp,
                    ))
                elif tool_name in _SIGNIFICANT_TOOLS:
                    content: dict[str, Any] = {
                        "tool": tool_name,
                        "status": item.get("status", ""),
                    }
                    detail = _tool_detail(tool_name, item.get("args", ""))
                    if detail:
                        content["detail"] = detail
                    events.append(ChannelEvent(
                        "agent.tool_use", agent_id, title,
                        content,
                        timestamp,
                    ))

        return events

    async def _dispatch(self, event: ChannelEvent) -> None:
        """Push a channel event to gateway and local callbacks."""
        event_dict = event.to_dict()

        # Local callbacks (in-process consumers like journal)
        for cb in self._callbacks:
            try:
                result = cb(event_dict)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass

        # Push to gateway
        await self._gateway.push_channel_event(event_dict)
