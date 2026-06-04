"""Tests for revise_workflow MCP tool.

Exercises the core apply-loop and reference-broken scan in isolation. We
inject the YAML via the test-only ``workflow_yaml`` arg and a mocked
``RevkaGatewayClient`` so no live gateway/Kumiho is required.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
import yaml

from operator_mcp.tool_handlers.workflow_revisions import (
    SkippedReason,
    _apply_rename_step,
    _exact_id_pattern,
    _load_current_yaml,
    _scan_broken_refs,
    tool_revise_workflow,
)


# ---------------------------------------------------------------------------
# Minimal but valid workflow YAML used as the starting state.
# Two simple agent steps; second references first via ${first.output}.
# ---------------------------------------------------------------------------

_BASE_YAML = """
name: revise-test
version: "1.0"
description: revise_workflow unit-test fixture
steps:
  - id: first
    type: agent
    agent:
      agent_type: claude
      role: researcher
      prompt: "Do step one"
  - id: second
    type: agent
    depends_on: [first]
    agent:
      agent_type: claude
      role: coder
      prompt: "Use ${first.output} to do step two"
"""


@pytest.fixture
def fake_gateway():
    gw = AsyncMock()
    gw._available = True
    gw.register_workflow = AsyncMock(
        return_value="kref://Revka/Workflows/revise-test.workflow"
    )
    return gw


# ---------------------------------------------------------------------------
# Happy path: add_step + wire produces valid YAML, no skipped items
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_revise_happy_path_add_and_wire(fake_gateway, monkeypatch):
    # Stub get_latest_revision so the post-publish revision lookup returns a
    # fake revision_kref instead of going through Kumiho SDK.
    from operator_mcp import operator_mcp as op_mod

    class _StubSDK:
        _available = True
        async def get_latest_revision(self, item_kref: str, tag: str = "published"):
            return {"kref": "kref://Revka/Workflows/revise-test.r2"}

    monkeypatch.setattr(op_mod, "KUMIHO_SDK", _StubSDK())

    result = await tool_revise_workflow(
        {
            "workflow_kref": "kref://Revka/Workflows/revise-test.workflow",
            "workflow_yaml": _BASE_YAML,
            "operations": [
                {
                    "op": "add_step",
                    "position_after": "second",
                    "step_def": {
                        "id": "third",
                        "type": "agent",
                        "agent": {
                            "agent_type": "claude",
                            "role": "reviewer",
                            # Prompt must reference the step we wire to;
                            # the agent-unused-depends_on validator rule
                            # rejects depends_on entries that don't appear
                            # in any interpolatable text field.
                            "prompt": "Review ${second.output}",
                        },
                    },
                },
                {
                    "op": "wire",
                    "step_id": "third",
                    "target_step_id": "second",
                },
            ],
        },
        _gw=fake_gateway,
    )

    assert result["success"] is True, result
    assert result["applied_count"] == 2
    assert result["skipped_items"] == []
    assert result["new_revision_kref"] == "kref://Revka/Workflows/revise-test.r2"
    fake_gateway.register_workflow.assert_awaited_once()
    # Verify the YAML passed to the gateway round-trips and contains the new step
    kwargs = fake_gateway.register_workflow.await_args.kwargs
    new_yaml = kwargs["definition_yaml"]
    parsed = yaml.safe_load(new_yaml)
    ids = [s["id"] for s in parsed["steps"]]
    assert "third" in ids
    third = next(s for s in parsed["steps"] if s["id"] == "third")
    assert "second" in third.get("depends_on", [])


# ---------------------------------------------------------------------------
# Skip path: delete_step on a missing id reports STEP_NOT_FOUND, then
# subsequent ops still apply.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_revise_skip_path_continues_after_failure(fake_gateway, monkeypatch):
    from operator_mcp import operator_mcp as op_mod

    class _StubSDK:
        _available = True
        async def get_latest_revision(self, item_kref: str, tag: str = "published"):
            return {"kref": "kref://Revka/Workflows/revise-test.r2"}

    monkeypatch.setattr(op_mod, "KUMIHO_SDK", _StubSDK())

    result = await tool_revise_workflow(
        {
            "workflow_kref": "kref://Revka/Workflows/revise-test.workflow",
            "workflow_yaml": _BASE_YAML,
            "operations": [
                {
                    "op": "delete_step",
                    "step_id": "ghost",  # does not exist
                },
                {
                    "op": "edit_step",
                    "step_id": "first",
                    "step_def": {
                        "agent": {
                            "agent_type": "claude",
                            "role": "researcher",
                            "prompt": "Updated prompt",
                        },
                    },
                },
            ],
        },
        _gw=fake_gateway,
    )

    assert result["success"] is True, result
    assert result["applied_count"] == 1  # edit_step succeeded
    assert len(result["skipped_items"]) == 1
    sk = result["skipped_items"][0]
    assert sk["op_index"] == 0
    assert sk["op"] == "delete_step"
    assert sk["reason"] == SkippedReason.STEP_NOT_FOUND.value
    assert sk["target_step_id"] == "ghost"
    # Verify the edit landed in the persisted YAML
    kwargs = fake_gateway.register_workflow.await_args.kwargs
    parsed = yaml.safe_load(kwargs["definition_yaml"])
    first = next(s for s in parsed["steps"] if s["id"] == "first")
    assert first["agent"]["prompt"] == "Updated prompt"


@pytest.mark.asyncio
async def test_revise_reorder_accepts_before_position_keyword(fake_gateway, monkeypatch):
    from operator_mcp import operator_mcp as op_mod

    class _StubSDK:
        _available = True
        async def get_latest_revision(self, item_kref: str, tag: str = "published"):
            return {"kref": "kref://Revka/Workflows/revise-test.r2"}

    monkeypatch.setattr(op_mod, "KUMIHO_SDK", _StubSDK())

    result = await tool_revise_workflow(
        {
            "workflow_kref": "kref://Revka/Workflows/revise-test.workflow",
            "workflow_yaml": _BASE_YAML,
            "operations": [
                {
                    "op": "reorder",
                    "step_id": "second",
                    "position": "before",
                    "target_step_id": "first",
                },
            ],
        },
        _gw=fake_gateway,
    )

    assert result["success"] is True, result
    assert result["applied_count"] == 1
    assert result["skipped_items"] == []
    kwargs = fake_gateway.register_workflow.await_args.kwargs
    parsed = yaml.safe_load(kwargs["definition_yaml"])
    assert [s["id"] for s in parsed["steps"]] == ["second", "first"]


@pytest.mark.asyncio
async def test_revise_add_step_accepts_before_position_keyword(fake_gateway, monkeypatch):
    from operator_mcp import operator_mcp as op_mod

    class _StubSDK:
        _available = True
        async def get_latest_revision(self, item_kref: str, tag: str = "published"):
            return {"kref": "kref://Revka/Workflows/revise-test.r2"}

    monkeypatch.setattr(op_mod, "KUMIHO_SDK", _StubSDK())

    result = await tool_revise_workflow(
        {
            "workflow_kref": "kref://Revka/Workflows/revise-test.workflow",
            "workflow_yaml": _BASE_YAML,
            "operations": [
                {
                    "op": "add_step",
                    "position": "before",
                    "target_step_id": "second",
                    "step_def": {
                        "id": "middle",
                        "type": "agent",
                        "agent": {
                            "agent_type": "claude",
                            "role": "reviewer",
                            "prompt": "Review the first step",
                        },
                    },
                },
            ],
        },
        _gw=fake_gateway,
    )

    assert result["success"] is True, result
    assert result["applied_count"] == 1
    assert result["skipped_items"] == []
    kwargs = fake_gateway.register_workflow.await_args.kwargs
    parsed = yaml.safe_load(kwargs["definition_yaml"])
    assert [s["id"] for s in parsed["steps"]] == ["first", "middle", "second"]


@pytest.mark.asyncio
async def test_load_current_yaml_pins_utf8_for_korean_artifacts(tmp_path, monkeypatch):
    workflow_path = tmp_path / "workflow.yaml"
    workflow_path.write_text(
        'name: korean-test\nversion: "1.0"\ndescription: "한글 워크플로우"\nsteps: []\n',
        encoding="utf-8",
    )

    from operator_mcp import operator_mcp as op_mod

    class _StubSDK:
        _available = True

        async def get_latest_revision(self, item_kref: str, tag: str = "published"):
            return {"kref": "kref://Revka/Workflows/korean-test.r1"}

        async def get_artifacts(self, revision_kref: str):
            return [{"location": str(workflow_path)}]

    real_open = open

    def cp949_default_open(path, mode="r", *args, **kwargs):
        if (
            str(path) == str(workflow_path)
            and "r" in mode
            and "b" not in mode
            and kwargs.get("encoding") is None
        ):
            raise UnicodeDecodeError("cp949", b"\xec", 0, 1, "illegal multibyte sequence")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(op_mod, "KUMIHO_SDK", _StubSDK())
    monkeypatch.setattr("builtins.open", cp949_default_open)

    yaml_text, revision_kref = await _load_current_yaml(
        "kref://Revka/Workflows/korean-test.workflow"
    )

    assert revision_kref == "kref://Revka/Workflows/korean-test.r1"
    assert "한글 워크플로우" in yaml_text


# ---------------------------------------------------------------------------
# Reference-broken: deleting a step that's referenced in another step's
# prompt emits a REFERENCE_BROKEN SkippedItem.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_revise_delete_reports_reference_broken(fake_gateway, monkeypatch):
    from operator_mcp import operator_mcp as op_mod

    class _StubSDK:
        _available = True
        async def get_latest_revision(self, item_kref: str, tag: str = "published"):
            return {"kref": "kref://Revka/Workflows/revise-test.r2"}

    monkeypatch.setattr(op_mod, "KUMIHO_SDK", _StubSDK())

    # second.prompt references ${first.output}; deleting `first` leaves a
    # broken ref. depends_on auto-cleans, so the workflow still parses.
    result = await tool_revise_workflow(
        {
            "workflow_kref": "kref://Revka/Workflows/revise-test.workflow",
            "workflow_yaml": _BASE_YAML,
            "operations": [
                {"op": "delete_step", "step_id": "first"},
            ],
        },
        _gw=fake_gateway,
    )

    assert result["success"] is True, result
    assert result["applied_count"] == 1
    refs = [s for s in result["skipped_items"]
            if s["reason"] == SkippedReason.REFERENCE_BROKEN.value]
    assert len(refs) >= 1
    broken = refs[0]
    assert broken["target_step_id"] == "first"
    # The op_kind is delete_step because `first` is in deleted_set
    assert broken["op"] == "delete_step"
    assert "agent.prompt" in broken["details"]


# ---------------------------------------------------------------------------
# Reference-scan regex word-boundary behaviour.
# ${first.output} matches old_id="first"; ${first-2.output} must NOT match.
# ---------------------------------------------------------------------------

def test_exact_id_pattern_word_boundary():
    pat = _exact_id_pattern("first")
    assert pat.search("body has ${first.output} here") is not None
    assert pat.search("plain ${first}") is not None
    # Suffix-only collision must not match
    assert pat.search("body has ${first-2.output} here") is None
    assert pat.search("body has ${first_alt.output} here") is None
    # But an exact match elsewhere should still hit
    assert pat.search("a ${first-2.output} and ${first.output}") is not None


def test_rename_step_rewrites_only_exact_refs():
    """rename_step rewrites ${old.…} but leaves ${old-2.…} alone."""
    state: dict[str, Any] = {
        "name": "rename-test",
        "steps": [
            {
                "id": "old",
                "type": "agent",
                "agent": {"agent_type": "claude", "role": "x", "prompt": "p"},
            },
            {
                "id": "old-2",
                "type": "agent",
                "agent": {"agent_type": "claude", "role": "x", "prompt": "p"},
            },
            {
                "id": "consumer",
                "type": "agent",
                "depends_on": ["old", "old-2"],
                "agent": {
                    "agent_type": "claude",
                    "role": "x",
                    "prompt": "use ${old.output} and also ${old-2.output} together",
                },
            },
        ],
    }
    from operator_mcp.tool_handlers.workflow_revisions import RevisionOp, RevisionOpType
    op = RevisionOp(op=RevisionOpType.RENAME_STEP, step_id="old", new_id="renamed")
    _apply_rename_step(state, op)

    consumer = state["steps"][2]
    assert consumer["depends_on"] == ["renamed", "old-2"]
    # Exact ${old.…} got rewritten; ${old-2.…} did NOT.
    assert consumer["agent"]["prompt"] == "use ${renamed.output} and also ${old-2.output} together"


def test_rename_step_rewrites_compute_expression_alias_refs():
    state: dict[str, Any] = {
        "name": "rename-compute-test",
        "steps": [
            {"id": "arc-loader", "type": "resolve", "resolve": {"kind": "arc"}},
            {
                "id": "next-arc-context",
                "type": "compute",
                "depends_on": ["arc-loader"],
                "compute": {
                    "outputs": {
                        "start": "${{ int(arc_loader.output_data.metadata.end) + 1 }}",
                        "raw": "${{ arc-loader.output_data.end }}",
                        "literal": "${{ 'arc_loader.output_data.end' }}",
                    }
                },
            },
        ],
    }
    from operator_mcp.tool_handlers.workflow_revisions import RevisionOp, RevisionOpType

    op = RevisionOp(op=RevisionOpType.RENAME_STEP, step_id="arc-loader", new_id="arc-source")
    _apply_rename_step(state, op)

    compute = state["steps"][1]
    assert compute["depends_on"] == ["arc-source"]
    assert compute["compute"]["outputs"]["start"] == "${{ int(arc_source.output_data.metadata.end) + 1 }}"
    assert compute["compute"]["outputs"]["raw"] == "${{ arc_source.output_data.end }}"
    assert compute["compute"]["outputs"]["literal"] == "${{ 'arc_loader.output_data.end' }}"
    assert _scan_broken_refs(state) == []


def test_scan_broken_refs_ignores_nested_compute_expression_fields():
    state: dict[str, Any] = {
        "name": "compute-scan-test",
        "steps": [
            {"id": "arc-loader", "type": "resolve", "resolve": {"kind": "arc"}},
            {
                "id": "next-arc-context",
                "type": "compute",
                "compute": {
                    "outputs": {
                        "start": "${{ int(arc_loader.output_data.metadata.end) + 1 }}",
                    }
                },
            },
        ],
    }

    assert _scan_broken_refs(state) == []
