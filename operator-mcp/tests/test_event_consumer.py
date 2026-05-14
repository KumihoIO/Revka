"""Tests for operator.event_consumer — ChannelEvent, EventConsumer."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from operator_mcp.event_consumer import ChannelEvent, EventConsumer, _SIGNIFICANT_TOOLS


# ---------------------------------------------------------------------------
# ChannelEvent
# ---------------------------------------------------------------------------

class TestChannelEvent:
    def test_create(self):
        ev = ChannelEvent("agent.started", "a1", "Test Agent", {"provider": "claude"}, "2025-01-01T00:00:00Z")
        assert ev.event_type == "agent.started"
        assert ev.agent_id == "a1"
        assert ev.agent_title == "Test Agent"
        assert ev.content == {"provider": "claude"}
        assert ev.timestamp == "2025-01-01T00:00:00Z"

    def test_to_dict(self):
        ev = ChannelEvent("agent.completed", "a2", "Coder", {"usage": {"tokens": 100}}, "ts")
        d = ev.to_dict()
        assert d == {
            "type": "agent.completed",
            "agentId": "a2",
            "agentTitle": "Coder",
            "content": {"usage": {"tokens": 100}},
            "timestamp": "ts",
        }

    def test_default_timestamp(self):
        ev = ChannelEvent("agent.error", "a3", "T", {})
        assert ev.timestamp == ""


# ---------------------------------------------------------------------------
# EventConsumer._translate
# ---------------------------------------------------------------------------

class TestEventConsumerTranslate:
    pytestmark = pytest.mark.asyncio
    @pytest.fixture
    def consumer(self, mock_sidecar, mock_gateway):
        return EventConsumer(mock_sidecar, mock_gateway)

    async def test_session_started(self, consumer):
        raw = {"event": {"type": "session_started", "provider": "claude"}, "timestamp": "ts1"}
        events = await consumer._translate("a1", "Agent", raw)
        assert len(events) == 1
        assert events[0].event_type == "agent.started"
        assert events[0].content["provider"] == "claude"

    async def test_status_changed_error(self, consumer):
        raw = {"event": {"type": "status_changed", "status": "error"}, "timestamp": "ts2"}
        events = await consumer._translate("a1", "Agent", raw)
        assert len(events) == 1
        assert events[0].event_type == "agent.error"

    async def test_status_changed_non_error_ignored(self, consumer):
        raw = {"event": {"type": "status_changed", "status": "running"}}
        events = await consumer._translate("a1", "Agent", raw)
        assert len(events) == 0

    async def test_turn_completed(self, consumer):
        raw = {"event": {"type": "turn_completed", "usage": {"input_tokens": 50}}, "timestamp": "ts3"}
        events = await consumer._translate("a1", "Agent", raw)
        assert len(events) == 1
        assert events[0].event_type == "agent.completed"
        consumer._gateway.record_usage.assert_awaited_once()
        kwargs = consumer._gateway.record_usage.await_args.kwargs
        assert kwargs["input_tokens"] == 50
        assert kwargs["source"] == "sidecar"

    async def test_turn_failed(self, consumer):
        raw = {"event": {"type": "turn_failed", "error": "out of memory"}, "timestamp": "ts4"}
        events = await consumer._translate("a1", "Agent", raw)
        assert len(events) == 1
        assert events[0].event_type == "agent.error"
        assert "out of memory" in events[0].content["error"]

    async def test_timeline_significant_tool(self, consumer):
        raw = {"event": {"type": "timeline", "item": {"type": "tool_call", "name": "Edit", "status": "ok"}}}
        events = await consumer._translate("a1", "Agent", raw)
        assert len(events) == 1
        assert events[0].event_type == "agent.tool_use"
        assert events[0].content["tool"] == "Edit"

    async def test_timeline_insignificant_tool_ignored(self, consumer):
        raw = {"event": {"type": "timeline", "item": {"type": "tool_call", "name": "Read", "status": "ok"}}}
        events = await consumer._translate("a1", "Agent", raw)
        assert len(events) == 0

    async def test_timeline_permission_tool(self, consumer):
        raw = {"event": {"type": "timeline", "item": {"type": "tool_call", "name": "permission:Bash", "args": "rm -rf /"}}}
        events = await consumer._translate("a1", "Agent", raw)
        assert len(events) == 1
        assert events[0].event_type == "agent.permission"
        assert events[0].content["tool"] == "Bash"

    async def test_timeline_assistant_message_ignored(self, consumer):
        raw = {"event": {"type": "timeline", "item": {"type": "assistant_message", "text": "hello"}}}
        events = await consumer._translate("a1", "Agent", raw)
        assert len(events) == 0

    async def test_unknown_event_ignored(self, consumer):
        raw = {"event": {"type": "unknown_thing"}}
        events = await consumer._translate("a1", "Agent", raw)
        assert len(events) == 0


# ---------------------------------------------------------------------------
# EventConsumer state management
# ---------------------------------------------------------------------------

class TestEventConsumerState:
    @pytest.fixture
    def consumer(self, mock_sidecar, mock_gateway):
        return EventConsumer(mock_sidecar, mock_gateway)

    @pytest.mark.asyncio
    async def test_subscribe_registers(self, consumer):
        # Mark global stream active so subscribe doesn't create asyncio tasks
        consumer._tasks["_global"] = AsyncMock()
        await consumer.subscribe("a1", "Agent One")
        assert "a1" in consumer._agent_titles
        assert "a1" in consumer._agent_events

    @pytest.mark.asyncio
    async def test_unsubscribe_cleans_up(self, consumer):
        consumer._tasks["_global"] = AsyncMock()
        await consumer.subscribe("a1", "Agent One")
        await consumer.unsubscribe("a1")
        assert "a1" not in consumer._agent_titles
        assert "a1" not in consumer._agent_events

    def test_get_events_empty(self, consumer):
        assert consumer.get_events("nonexistent") == []

    def test_get_events_with_data(self, consumer):
        consumer._agent_titles["a1"] = "A1"
        consumer._agent_events["a1"] = [{"a": 1}, {"b": 2}, {"c": 3}]
        assert len(consumer.get_events("a1")) == 3

    def test_get_events_since(self, consumer):
        consumer._agent_titles["a1"] = "A1"
        consumer._agent_events["a1"] = [{"a": 1}, {"b": 2}, {"c": 3}]
        assert len(consumer.get_events("a1", since=2)) == 1

    def test_on_channel_event_callback(self, consumer):
        received = []
        consumer.on_channel_event(lambda ev: received.append(ev))
        assert len(consumer._callbacks) == 1


# ---------------------------------------------------------------------------
# EventConsumer.get_curated_activity
# ---------------------------------------------------------------------------

class TestGetCuratedActivity:
    @pytest.fixture
    def consumer(self, mock_sidecar, mock_gateway):
        return EventConsumer(mock_sidecar, mock_gateway)

    def test_empty(self, consumer):
        result = consumer.get_curated_activity("nonexistent")
        assert result["event_count"] == 0
        assert result["significant_events"] == []
        assert result["last_message"] == ""

    def test_tool_calls(self, consumer):
        consumer._agent_titles["a1"] = "A1"
        consumer._agent_events["a1"] = [
            {"event": {"type": "timeline", "item": {"type": "tool_call", "name": "Edit", "status": "ok"}}},
            {"event": {"type": "timeline", "item": {"type": "tool_call", "name": "Read", "status": "ok"}}},
        ]
        result = consumer.get_curated_activity("a1")
        assert result["event_count"] == 2
        assert len(result["significant_events"]) == 2
        assert result["significant_events"][0]["name"] == "Edit"

    def test_last_message(self, consumer):
        consumer._agent_titles["a1"] = "A1"
        consumer._agent_events["a1"] = [
            {"event": {"type": "timeline", "item": {"type": "assistant_message", "text": "First message"}}},
            {"event": {"type": "timeline", "item": {"type": "assistant_message", "text": "Second message"}}},
        ]
        result = consumer.get_curated_activity("a1")
        assert result["last_message"] == "Second message"

    def test_completed_event(self, consumer):
        consumer._agent_titles["a1"] = "A1"
        consumer._agent_events["a1"] = [
            {"event": {"type": "turn_completed", "usage": {"tokens": 500}}},
        ]
        result = consumer.get_curated_activity("a1")
        assert result["significant_events"][0]["type"] == "completed"

    def test_failed_event(self, consumer):
        consumer._agent_titles["a1"] = "A1"
        consumer._agent_events["a1"] = [
            {"event": {"type": "turn_failed", "error": "timeout"}},
        ]
        result = consumer.get_curated_activity("a1")
        assert result["significant_events"][0]["type"] == "failed"


# ---------------------------------------------------------------------------
# Significant tools set
# ---------------------------------------------------------------------------

def test_significant_tools_set():
    assert "Edit" in _SIGNIFICANT_TOOLS
    assert "Write" in _SIGNIFICANT_TOOLS
    assert "Bash" in _SIGNIFICANT_TOOLS
    assert "Read" not in _SIGNIFICANT_TOOLS
    assert "Glob" not in _SIGNIFICANT_TOOLS
