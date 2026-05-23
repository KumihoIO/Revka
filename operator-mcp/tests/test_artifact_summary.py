from __future__ import annotations

import pytest

from operator_mcp import artifact_summary


@pytest.mark.asyncio
async def test_extractive_summary_without_configured_model():
    metadata = await artifact_summary.summarize_artifact_metadata(
        "# Report\n\nKey finding one.\nKey finding two.",
        artifact_name="report.md",
        content_format="markdown",
    )

    assert metadata == {
        "summary": "Report: Key finding one. Key finding two.",
        "summary_source": "extractive",
    }


@pytest.mark.asyncio
async def test_configured_light_model_summary(monkeypatch):
    async def fake_openai(prompt: str, model: str) -> str:
        assert model == "cheap-model"
        assert "Artifact name: report.md" in prompt
        return "LLM summary"

    monkeypatch.setattr(artifact_summary, "_summarize_with_openai", fake_openai)

    metadata = await artifact_summary.summarize_artifact_metadata(
        "# Report\n\nLong content.",
        artifact_name="report.md",
        content_format="markdown",
        summary_model="cheap-model",
    )

    assert metadata == {
        "summary": "LLM summary",
        "summary_source": "llm",
        "summary_model": "cheap-model",
    }


@pytest.mark.asyncio
async def test_explicit_summary_metadata_wins(monkeypatch):
    async def fail_openai(_prompt: str, _model: str) -> str:
        raise AssertionError("model summarization should not run")

    monkeypatch.setattr(artifact_summary, "_summarize_with_openai", fail_openai)

    metadata = await artifact_summary.summarize_artifact_metadata(
        "Long content.",
        summary_model="cheap-model",
        existing_metadata={"summary": "Human summary"},
    )

    assert metadata == {"summary": "Human summary"}
