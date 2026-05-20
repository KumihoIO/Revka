"""Tests for operator.run_log — recording, querying, cleanup."""
from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from operator_mcp.run_log import (
    RunLog,
    get_or_create_log,
    get_log,
    cleanup_logs,
    load_log_from_disk,
    _LOGS,
)


@pytest.fixture(autouse=True)
def clean_logs():
    """Clear in-memory log registry before/after each test."""
    _LOGS.clear()
    yield
    _LOGS.clear()


@pytest.fixture
def log(tmp_path):
    """Create a RunLog writing to a temp directory."""
    with patch("operator_mcp.run_log._RUNLOGS_DIR", str(tmp_path)):
        return RunLog("test-agent-1", title="coder-test", agent_type="codex", cwd="/tmp/work")


class TestRunLogBasics:
    def test_creates_file(self, log, tmp_path):
        path = tmp_path / "test-agent-1.jsonl"
        assert path.exists()

    def test_header_written(self, log, tmp_path):
        path = tmp_path / "test-agent-1.jsonl"
        with open(path) as f:
            header = json.loads(f.readline())
        assert header["kind"] == "header"
        assert header["title"] == "coder-test"
        assert header["agent_type"] == "codex"

    def test_record_prompt(self, log):
        log.record_prompt("Do the task")
        summary = log.get_summary()
        assert summary["total_events"] == 2  # header + prompt

    def test_get_summary(self, log):
        s = log.get_summary()
        assert s["agent_id"] == "test-agent-1"
        assert s["title"] == "coder-test"
        assert s["tool_call_count"] == 0
        assert s["error_count"] == 0
        assert s["files_touched"] == []


class TestRunLogRecording:
    def test_record_tool_call(self, log):
        log.record_event({
            "event": {
                "type": "timeline",
                "item": {
                    "type": "tool_call",
                    "name": "Edit",
                    "args": json.dumps({"file_path": "/tmp/test.py"}),
                    "status": "success",
                    "result": "",
                },
            },
            "timestamp": "2025-01-01T00:00:00Z",
        })
        assert log.get_summary()["tool_call_count"] == 1
        assert "/tmp/test.py" in log.get_files_touched()

    def test_record_failed_tool(self, log):
        log.record_event({
            "event": {
                "type": "timeline",
                "item": {
                    "type": "tool_call",
                    "name": "Bash",
                    "args": json.dumps({"command": "npm test"}),
                    "status": "failed",
                    "error": "exit code 1",
                },
            },
            "timestamp": "2025-01-01T00:00:00Z",
        })
        assert log.get_summary()["error_count"] == 1
        assert log.get_summary()["last_failing_command"] is not None

    def test_record_assistant_message(self, log):
        log.record_event({
            "event": {
                "type": "timeline",
                "item": {"type": "assistant_message", "text": "Task complete."},
            },
            "timestamp": "2025-01-01T00:00:00Z",
        })
        assert log.get_summary()["last_message"] == "Task complete."

    def test_record_error_event(self, log):
        log.record_event({
            "event": {
                "type": "timeline",
                "item": {"type": "error", "message": "Something broke"},
            },
            "timestamp": "2025-01-01T00:00:00Z",
        })
        assert log.get_summary()["error_count"] == 1

    def test_record_status_changed(self, log):
        log.record_event({
            "event": {"type": "status_changed", "status": "idle"},
            "timestamp": "2025-01-01T00:00:00Z",
        })
        assert log.get_summary()["status"] == "idle"

    def test_record_turn_completed_usage(self, log):
        log.record_event({
            "event": {
                "type": "turn_completed",
                "turnId": "t1",
                "usage": {"inputTokens": 100, "outputTokens": 50, "totalCostUsd": 0.01},
            },
            "timestamp": "2025-01-01T00:00:00Z",
        })
        usage = log.get_summary()["usage"]
        assert usage["input_tokens"] == 100
        assert usage["output_tokens"] == 50

    def test_record_subprocess(self, log):
        log.record_subprocess("npm test", exit_code=1, stdout="FAIL", stderr="error")
        summary = log.get_summary()
        assert summary["status"] == "failed"
        assert summary["error_count"] == 1
        assert summary["last_failing_command"]["command"] == "npm test"
        assert summary["last_message"] == "FAIL"

    def test_subprocess_success(self, log):
        log.record_subprocess("npm build", exit_code=0, stdout="OK")
        summary = log.get_summary()
        assert summary["status"] == "completed"
        assert summary["error_count"] == 0
        assert summary["last_message"] == "OK"

    def test_subprocess_status_replays_from_disk(self, log, tmp_path):
        log.record_subprocess("npm build", exit_code=0, stdout="OK")
        _LOGS.clear()

        with patch("operator_mcp.run_log._RUNLOGS_DIR", str(tmp_path)):
            loaded = load_log_from_disk("test-agent-1")

        assert loaded is not None
        summary = loaded.get_summary()
        assert summary["status"] == "completed"
        assert summary["last_message"] == "OK"


class TestRunLogQueries:
    def test_get_tool_calls(self, log):
        for i in range(5):
            log.record_event({
                "event": {
                    "type": "timeline",
                    "item": {"type": "tool_call", "name": f"Tool{i}", "status": "ok"},
                },
                "timestamp": "2025-01-01T00:00:00Z",
            })
        calls = log.get_tool_calls(limit=3)
        assert len(calls) == 3
        # Most recent first
        assert calls[0]["name"] == "Tool4"

    def test_get_errors(self, log):
        log.record_event({
            "event": {"type": "timeline", "item": {"type": "error", "message": "oops"}},
            "timestamp": "2025-01-01T00:00:00Z",
        })
        errors = log.get_errors()
        assert len(errors) == 1

    def test_get_full_log(self, log, tmp_path):
        with patch("operator_mcp.run_log._RUNLOGS_DIR", str(tmp_path)):
            log.record_prompt("test")
            entries = log.get_full_log(limit=100)
            assert len(entries) >= 2  # header + prompt
            assert entries[0]["kind"] == "header"


class TestRunLogRegistry:
    def test_get_or_create(self, tmp_path):
        with patch("operator_mcp.run_log._RUNLOGS_DIR", str(tmp_path)):
            log = get_or_create_log("agent-1", title="test")
            assert log.agent_id == "agent-1"
            # Same id returns same instance
            assert get_or_create_log("agent-1") is log

    def test_get_log_not_found(self):
        assert get_log("nonexistent") is None


class TestCleanupLogs:
    def test_no_cleanup_below_limit(self, tmp_path):
        with patch("operator_mcp.run_log._RUNLOGS_DIR", str(tmp_path)):
            for i in range(5):
                get_or_create_log(f"a-{i}", title=f"test-{i}")
            removed = cleanup_logs(max_in_memory=10)
            assert removed == 0

    def test_cleanup_evicts_completed(self, tmp_path):
        with patch("operator_mcp.run_log._RUNLOGS_DIR", str(tmp_path)):
            for i in range(15):
                get_or_create_log(f"a-{i}", title=f"test-{i}")
            removed = cleanup_logs(max_in_memory=5)
            assert removed == 10
            assert len(_LOGS) == 5
