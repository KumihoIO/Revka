"""Tests for propose_workflow_yaml — validate-only proposal flow.

This tool MUST NOT persist anywhere. The tests assert the response shape
and that the diff fields work. Persistence-side checks (no gateway/kumiho
imports) live in tests/test_architect_propose_imports.py is unnecessary —
we cover that with a one-line static grep in the body of test_no_persistence.
"""
from __future__ import annotations

import pytest

from operator_mcp.tool_handlers.architect_propose import tool_propose_workflow_yaml


_VALID_YAML = """
name: propose-test
version: "1.0"
description: propose_workflow_yaml unit-test fixture
steps:
  - id: first
    type: agent
    agent:
      agent_type: claude
      role: researcher
      prompt: Do step one
"""


_VALID_YAML_TWO_STEPS = """
name: propose-test
version: "1.0"
description: propose_workflow_yaml unit-test fixture
steps:
  - id: first
    type: agent
    agent:
      agent_type: claude
      role: researcher
      prompt: Do step one
  - id: second
    type: agent
    depends_on: [first]
    agent:
      agent_type: claude
      role: coder
      prompt: Use ${first.output}
"""


@pytest.mark.asyncio
async def test_happy_path_valid_yaml() -> None:
    result = await tool_propose_workflow_yaml({
        "proposed_yaml": _VALID_YAML,
        "intent_summary": "user asked for a one-step researcher",
    })
    assert result["valid"] is True
    assert result["errors"] == []
    assert result["summary"] == "user asked for a one-step researcher"
    # Re-serialized YAML preserves the workflow name
    assert "name: propose-test" in result["yaml"]
    # No base_yaml → diffs all empty
    assert result["added_step_ids"] == []
    assert result["modified_step_ids"] == []
    assert result["removed_step_ids"] == []


@pytest.mark.asyncio
async def test_invalid_yaml_syntax_returns_parse_error() -> None:
    bad_yaml = "name: x\n  steps: [unclosed"
    result = await tool_propose_workflow_yaml({
        "proposed_yaml": bad_yaml,
        "intent_summary": "broken",
    })
    assert result["valid"] is False
    assert result["errors"], "Expected at least one error"
    msg = result["errors"][0]["message"]
    assert "YAML" in msg or "parse" in msg.lower() or "mapping" in msg.lower()


@pytest.mark.asyncio
async def test_schema_invalid_missing_required_field() -> None:
    """A YAML missing the required `steps` field surfaces a structured
    Pydantic error."""
    bad = "name: missing-steps\nversion: \"1.0\"\ndescription: oh no\n"
    result = await tool_propose_workflow_yaml({
        "proposed_yaml": bad,
        "intent_summary": "missing steps",
    })
    assert result["valid"] is False
    # The Pydantic error mentions the missing `steps` field
    flat = " ".join(e["message"] for e in result["errors"])
    assert "steps" in flat.lower()


@pytest.mark.asyncio
async def test_diff_added_step_id() -> None:
    result = await tool_propose_workflow_yaml({
        "proposed_yaml": _VALID_YAML_TWO_STEPS,
        "base_yaml": _VALID_YAML,
        "intent_summary": "add a second step",
    })
    assert result["valid"] is True
    assert result["added_step_ids"] == ["second"]
    assert result["modified_step_ids"] == []
    assert result["removed_step_ids"] == []


@pytest.mark.asyncio
async def test_propose_workflow_yaml_rejects_orphan_parallel_step() -> None:
    """parallel step with no parallel.steps array should fail validation."""
    args = {
        "proposed_yaml": """
name: bad-parallel
version: '1.0'
steps:
  - id: orphan
    type: parallel
    parallel:
      join: all
""",
        "intent_summary": "test",
    }
    result = await tool_propose_workflow_yaml(args)
    assert result["valid"] is False
    assert any(
        "parallel.steps" in str(e).lower() for e in result.get("errors", [])
    )


@pytest.mark.asyncio
async def test_no_persistence_imports() -> None:
    """The tool module must never import gateway/kumiho — the whole point
    of propose_workflow_yaml is that it can't persist."""
    import operator_mcp.tool_handlers.architect_propose as mod

    src_path = mod.__file__
    assert src_path is not None
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    assert "gateway_client" not in src
    assert "kumiho_clients" not in src
    assert "save_workflow_yaml" not in src


_VALID_IMAGE_STEP_YAML = """
name: propose-image-test
version: "1.0"
description: image step Architect proposal
steps:
  - id: hero_shot
    type: image
    image:
      prompt: |
        Architectural panel of Seoul Station 2040.
        Wide aerial shot, golden hour.
      count: 1
      canvas: true
      register_artifact: true
"""


@pytest.mark.asyncio
async def test_image_step_validates() -> None:
    """Architect-proposed image-step YAML round-trips through validation.

    Pins the public shape so a backend rename (e.g. dropping `canvas` or
    renaming `register_artifact`) can't silently break Architect proposals.
    """
    result = await tool_propose_workflow_yaml({
        "proposed_yaml": _VALID_IMAGE_STEP_YAML,
        "intent_summary": "user asked for one image generated to canvas",
    })
    assert result["valid"] is True, result["errors"]
    assert "type: image" in result["yaml"]


@pytest.mark.asyncio
async def test_image_step_metadata_advertised() -> None:
    """get_workflow_metadata must list `image` so the Architect knows
    it exists. Without this, the Architect falls back to a prose-prompted
    `agent` step and produces no canvas frame / no artifact."""
    from operator_mcp.tool_handlers.workflow_discovery import (
        tool_get_workflow_metadata,
    )

    metadata = await tool_get_workflow_metadata({})
    step_types = [s["type"] for s in metadata["step_types"]]
    assert "image" in step_types, (
        f"image step type missing from metadata; got {step_types}"
    )
    image_meta = next(s for s in metadata["step_types"] if s["type"] == "image")
    # The description must steer the Architect toward `image` over `agent`
    # for image-generation tasks.
    assert "agent" in image_meta["description"].lower()
    field_names = {f["name"] for f in image_meta["config_fields"]}
    assert "prompt" in field_names
    assert "canvas" in field_names
    assert "register_artifact" in field_names
    assert "type: image" in image_meta["example_yaml"]
