"""Unit tests for the cloud agents' A2A surface.

Covers the two things Revka's client depends on without needing google-adk:
  - parse_task_input: JSON task payload extraction + required-field checks.
  - build_agent_card: card shape consumed by a2a_discover.

The ADK imports inside a2a_server are lazy, so importing the modules here only
requires fastapi.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

CLOUD_AGENTS = Path(__file__).resolve().parent.parent


def _load(agent_dir: str):
    path = CLOUD_AGENTS / agent_dir / "a2a_server.py"
    name = f"{agent_dir}_a2a_server"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module", params=["coder", "reviewer"])
def server(request):
    return _load(request.param)


# -- agent card shape (what tool_a2a_discover reads) --


def test_agent_card_required_fields(server):
    card = server.build_agent_card("https://example.run.app")
    assert card["name"]  # discover() rejects cards without a name
    assert card["description"]
    assert card["url"] == "https://example.run.app"
    assert isinstance(card["skills"], list) and card["skills"]
    assert isinstance(card["capabilities"], dict)


def test_agent_card_skill_shape(server):
    skill = server.build_agent_card("https://example.run.app")["skills"][0]
    for key in ("id", "name", "description", "tags"):
        assert skill[key]


# -- task input parsing --


def test_parse_valid_json_coder():
    coder = _load("coder")
    data = coder.parse_task_input(
        '{"repo_name": "acme/api", "issue_number": 42, "issue_title": "t", '
        '"issue_body": "b", "strategy": "s"}'
    )
    assert data["repo_name"] == "acme/api"
    assert data["issue_number"] == 42


def test_parse_valid_json_reviewer():
    reviewer = _load("reviewer")
    data = reviewer.parse_task_input('{"repo_name": "acme/api", "pr_number": 7}')
    assert data["pr_number"] == 7


def test_parse_json_embedded_in_text(server):
    payload = (
        'Please handle this task: {"repo_name": "acme/api", '
        '"issue_number": 1, "pr_number": 1} thanks'
    )
    data = server.parse_task_input(payload)
    assert data["repo_name"] == "acme/api"


def test_parse_missing_required_field(server):
    with pytest.raises(ValueError, match="missing required fields"):
        server.parse_task_input('{"strategy": "x"}')


def test_parse_not_json(server):
    with pytest.raises(ValueError):
        server.parse_task_input("just some prose, no json here")


def test_parse_non_object_json(server):
    with pytest.raises(ValueError):
        server.parse_task_input('["a", "list"]')


# -- message text extraction (Revka sends parts keyed by "type") --


def test_message_text_accepts_type_keyed_parts(server):
    params = {
        "message": {
            "role": "user",
            "parts": [{"type": "text", "text": "hello"}],
        }
    }
    assert server._message_text(params) == "hello"


def test_message_text_accepts_kind_keyed_parts(server):
    params = {"message": {"parts": [{"kind": "text", "text": "hi"}]}}
    assert server._message_text(params) == "hi"


# -- task shape (what tool_a2a_send_task / tool_a2a_get_task read) --


def test_task_shape_matches_revka_client(server):
    task = server._make_task("task-1", "ctx-1", "submitted", "msg")
    assert task["id"] == "task-1"
    assert task["status"]["state"] == "submitted"
    server._store_task(task)
    server._finish_task("task-1", "completed", '{"ok": true}', artifact_name="result")
    stored = server.TASKS["task-1"]
    assert stored["status"]["state"] == "completed"
    part = stored["artifacts"][0]["parts"][0]
    # Revka's client extracts output only from parts with type == "text".
    assert part["type"] == "text"
    assert part["text"] == '{"ok": true}'
