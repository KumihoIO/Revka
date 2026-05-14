from operator_mcp.token_compression import (
    build_skill_pointer_manifest,
    compress_agent_result,
    compress_skill_content,
    compress_text,
)
from operator_mcp.agent_subprocess import compose_agent_prompt
from operator_mcp.mcp_injection import build_system_prompt


def test_compress_text_preserves_error_and_tail():
    text = "\n".join(
        ["noise line"] * 500
        + ["ERROR: build failed in src/main.rs"]
        + [f"tail {i}" for i in range(80)]
    )

    compressed, stats = compress_text(text, max_chars=1000)

    assert stats is not None
    assert len(compressed) <= 1000
    assert "build failed" in compressed
    assert "tail 79" in compressed


def test_compress_agent_result_keeps_schema():
    result = {
        "agent_id": "a1",
        "status": "completed",
        "last_message": "x" * 5000,
        "files_touched": ["src/main.rs"],
    }

    out = compress_agent_result(result, max_chars=800)

    assert out["agent_id"] == "a1"
    assert out["status"] == "completed"
    assert out["files_touched"] == ["src/main.rs"]
    assert len(out["last_message"]) <= 800
    assert out["token_compression"]["last_message"]["axis"] == "workflow_data"


def test_small_agent_result_is_unchanged_object():
    result = {"agent_id": "a1", "last_message": "done"}
    assert compress_agent_result(result, max_chars=800) is result


def test_operator_prompts_include_terse_handoff_contract():
    prompt = compose_agent_prompt("Alice", "coder", "", [], "Build feature")
    system_prompt = build_system_prompt(include_memory=False, include_operator=False)

    assert "Output Contract" in prompt
    assert "Concise handoff" in prompt
    assert "Output contract for operator handoff" in system_prompt


def test_compress_skill_content_keeps_manifest_signals():
    content = "\n".join(
        [
            "# Research Skill",
            "Description: use when researching external sources.",
            "Must cite sources.",
            "Never paste raw logs.",
        ]
        + [f"detail {i} " + ("x" * 100) for i in range(100)]
    )

    compressed, stats = compress_skill_content(
        "kref://skill",
        content,
        resolved_path="/tmp/skills/research.md",
        max_chars=700,
    )

    assert stats is not None
    assert len(compressed) <= 700
    assert "Research Skill" in compressed
    assert "kref://skill" in compressed
    assert "resolved_path: /tmp/skills/research.md" in compressed
    assert "memory_resolve_kref" in compressed
    assert "Must cite sources" in compressed
    assert "compact-manifest" in compressed


def test_build_skill_pointer_manifest_keeps_hydration_pointer_only():
    manifest, stats = build_skill_pointer_manifest(
        "kref://skill/research",
        resolved_path="/tmp/skills/research.md",
        max_chars=500,
    )

    assert len(manifest) <= 500
    assert "kref://skill/research" in manifest
    assert "resolved_path: /tmp/skills/research.md" in manifest
    assert "pointer-manifest" in manifest
    assert "memory_resolve_kref" in manifest
    assert stats["axis"] == "skill_context"
