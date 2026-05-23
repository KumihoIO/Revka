"""Artifact summary metadata helpers."""
from __future__ import annotations

import asyncio
import os
import re
from typing import Any

from ._log import _log

_DEFAULT_INPUT_CHARS = 12000
_DEFAULT_SUMMARY_CHARS = 1200
_DEFAULT_TIMEOUT_SECS = 8.0


def _clean_summary(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip()


def _extractive_summary(content: str, limit: int) -> str:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        return _clean_summary(content, limit)

    heading = next((line.lstrip("# ").strip() for line in lines if line.startswith("#")), "")
    body = " ".join(line for line in lines if not line.startswith("#"))
    if heading and body:
        return _clean_summary(f"{heading}: {body}", limit)
    if heading:
        return _clean_summary(heading, limit)
    return _clean_summary(" ".join(lines), limit)


def _split_provider_model(model: str) -> tuple[str, str]:
    clean = model.strip()
    provider = ""
    if "/" in clean:
        provider, clean = clean.split("/", 1)
        provider = provider.strip().lower()
    if provider:
        return provider, clean.strip()
    if clean.startswith("claude-"):
        return "anthropic", clean
    return "openai", clean


def _summary_prompt(content: str, *, artifact_name: str, content_format: str) -> str:
    clipped = content[:_DEFAULT_INPUT_CHARS]
    return (
        "Summarize this artifact for a downstream agent that should avoid "
        "loading the full raw artifact unless necessary.\n"
        "Return only a concise reusable summary, with key facts, decisions, "
        "outputs, and caveats. Do not mention that you are summarizing.\n\n"
        f"Artifact name: {artifact_name or 'artifact'}\n"
        f"Format: {content_format or 'text'}\n\n"
        f"{clipped}"
    )


async def _summarize_with_anthropic(prompt: str, model: str) -> str:
    import anthropic

    client = anthropic.AsyncAnthropic()
    response = await client.messages.create(
        model=model,
        max_tokens=400,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    return str(response.content[0].text)


async def _summarize_with_openai(prompt: str, model: str) -> str:
    import httpx

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return ""
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECS) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 400,
                "temperature": 0,
            },
        )
        resp.raise_for_status()
        data = resp.json()
    choices = data.get("choices") if isinstance(data, dict) else None
    if not choices:
        return ""
    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    return str(message.get("content") or "")


async def _llm_summary(
    content: str,
    *,
    artifact_name: str,
    content_format: str,
    summary_model: str,
) -> tuple[str, str]:
    requested_model = summary_model.strip()
    provider, model = _split_provider_model(summary_model)
    if not model:
        return "", ""
    prompt = _summary_prompt(content, artifact_name=artifact_name, content_format=content_format)
    try:
        if provider in {"anthropic", "claude"}:
            text = await asyncio.wait_for(_summarize_with_anthropic(prompt, model), timeout=_DEFAULT_TIMEOUT_SECS)
        else:
            text = await asyncio.wait_for(_summarize_with_openai(prompt, model), timeout=_DEFAULT_TIMEOUT_SECS)
    except Exception as exc:
        _log(f"artifact_summary: model summary failed for {artifact_name or 'artifact'}: {exc}")
        return "", requested_model
    return text, requested_model


async def summarize_artifact_metadata(
    content: str,
    *,
    artifact_name: str = "",
    content_format: str = "",
    summary_model: str = "",
    existing_metadata: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Return artifact metadata containing a compact ``summary``.

    Explicit caller-provided ``summary`` metadata wins. LLM summarization is
    best-effort and opt-in via the caller-provided ``summary_model``; an
    extractive fallback keeps the metadata useful without making artifact
    creation depend on model availability.
    """
    existing_summary = str((existing_metadata or {}).get("summary") or "").strip()
    limit = _DEFAULT_SUMMARY_CHARS
    if existing_summary:
        return {"summary": _clean_summary(existing_summary, limit)}

    if not content.strip():
        return {}

    if summary_model.strip():
        summary, model = await _llm_summary(
            content,
            artifact_name=artifact_name,
            content_format=content_format,
            summary_model=summary_model,
        )
        if summary:
            return {
                "summary": _clean_summary(summary, limit),
                "summary_source": "llm",
                "summary_model": model,
            }

    return {
        "summary": _extractive_summary(content, limit),
        "summary_source": "extractive",
    }
