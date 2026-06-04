"""Deterministic token compression helpers for operator-MCP payloads.

The Rust runtime handles provider-facing chat/tool output. This module covers
the Python operator side where agent wait results and workflow handoffs can
carry large stdout tails. Compression is deliberately schema-preserving:
callers still receive the same JSON object, with oversized text fields reduced
and a ``token_compression`` metadata block added.
"""
from __future__ import annotations

import os
from typing import Any


DEFAULT_AGENT_MESSAGE_MAX_CHARS = int(
    os.environ.get("REVKA_AGENT_RESULT_MAX_CHARS", "2000")
)
DEFAULT_SKILL_CONTEXT_MAX_CHARS = int(
    os.environ.get("REVKA_WORKFLOW_SKILL_MAX_CHARS", "1600")
)

_ERROR_KEYWORDS = (
    "error",
    "failed",
    "failure",
    "panic",
    "exception",
    "traceback",
    "assertion",
    "denied",
    "timeout",
    "warning",
    "fatal",
    "not found",
    "cannot",
    "could not",
)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


def _limit_text(text: str, max_chars: int, marker: str) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    if max_chars <= len(marker) + 2:
        return text[:max_chars]
    available = max_chars - len(marker)
    head_len = available * 3 // 4
    tail_len = available - head_len
    return text[:head_len] + marker + text[-tail_len:]


def compress_text(text: str, max_chars: int = DEFAULT_AGENT_MESSAGE_MAX_CHARS) -> tuple[str, dict[str, Any] | None]:
    """Compress oversized text while preserving high-signal lines and tail."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text, None

    lines = text.splitlines()
    signal: list[str] = []
    for line in lines:
        lower = line.lower()
        if any(keyword in lower for keyword in _ERROR_KEYWORDS):
            signal.append(line[:500])
        if len(signal) >= 80:
            break

    tail = lines[-40:]
    body: list[str] = []
    body.append(
        "[Revka token compression: axis=workflow_data, "
        f"chars {len(text)}->{max_chars}, est_tokens {estimate_tokens(text)}->{max_chars // 4}]"
    )
    if signal:
        body.append("\nSignal lines:")
        body.extend(signal)
    body.append("\nTail:")
    body.extend(line[:500] for line in tail)

    compressed = "\n".join(body)
    compressed = _limit_text(
        compressed,
        max_chars,
        "\n\n[... compressed workflow output truncated ...]\n\n",
    )

    return compressed, {
        "axis": "workflow_data",
        "original_chars": len(text),
        "compressed_chars": len(compressed),
        "estimated_tokens_saved": max(0, estimate_tokens(text) - estimate_tokens(compressed)),
    }


def compress_agent_result(
    result: dict[str, Any],
    *,
    max_chars: int = DEFAULT_AGENT_MESSAGE_MAX_CHARS,
) -> dict[str, Any]:
    """Return a schema-compatible agent result with compressed large text fields."""
    last_message = result.get("last_message")
    if not isinstance(last_message, str):
        return result

    compressed, stats = compress_text(last_message, max_chars=max_chars)
    if stats is None:
        return result

    out = dict(result)
    out["last_message"] = compressed
    existing = out.get("token_compression")
    if isinstance(existing, dict):
        merged = dict(existing)
        merged["last_message"] = stats
        out["token_compression"] = merged
    else:
        out["token_compression"] = {"last_message": stats}
    return out


def compress_skill_content(
    ref: str,
    content: str,
    *,
    resolved_path: str | None = None,
    max_chars: int = DEFAULT_SKILL_CONTEXT_MAX_CHARS,
) -> tuple[str, dict[str, Any] | None]:
    """Build a compact workflow-step skill manifest from full skill text."""
    if max_chars <= 0:
        return content, None

    lines = [line.rstrip() for line in content.replace("\r\n", "\n").splitlines()]
    title = next((line.strip("# ").strip() for line in lines if line.startswith("#")), "")
    signal_keywords = (
        "description",
        "use when",
        "trigger",
        "workflow",
        "must",
        "never",
        "rules",
        "steps",
        "inputs",
        "outputs",
        "non-negotiable",
        "검증",
        "규칙",
        "절대",
        "출력",
    )

    signal: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if stripped.startswith(("#", "-", "*")) or any(k in lower for k in signal_keywords):
            signal.append(stripped[:240])
        if len(signal) >= 40:
            break

    body: list[str] = [
        f"[Revka skill context: ref={ref}, chars {len(content)}->{max_chars}]",
        f"skill_ref: {ref}",
    ]
    if resolved_path:
        body.append(f"resolved_path: {resolved_path}")
    if title:
        body.append(f"name: {title}")
    body.append("mode: compact-manifest; full markdown is external, not preloaded.")
    body.append(
        "hydrate: if this manifest is insufficient, resolve skill_ref with "
        "memory_resolve_kref or read resolved_path, then apply only the needed rules."
    )
    body.append("signals:")
    body.extend(f"- {line}" for line in signal[:32])

    compressed = "\n".join(body)
    compressed = _limit_text(
        compressed,
        max_chars,
        "\n[... compact skill manifest truncated ...]\n",
    )

    return compressed, {
        "axis": "skill_context",
        "ref": ref,
        "resolved_path": resolved_path,
        "original_chars": len(content),
        "compressed_chars": len(compressed),
        "estimated_tokens_saved": max(0, estimate_tokens(content) - estimate_tokens(compressed)),
    }


def build_skill_pointer_manifest(
    ref: str,
    *,
    resolved_path: str | None = None,
    max_chars: int = DEFAULT_SKILL_CONTEXT_MAX_CHARS,
) -> tuple[str, dict[str, Any]]:
    """Build a no-inline skill manifest when only a kref/path pointer is needed."""
    body = [
        f"[Revka skill context: ref={ref}, mode=pointer]",
        f"skill_ref: {ref}",
    ]
    if resolved_path:
        body.append(f"resolved_path: {resolved_path}")
    body.extend(
        [
            "mode: pointer-manifest; full markdown is external, not preloaded.",
            (
                "hydrate: resolve skill_ref with memory_resolve_kref or read "
                "resolved_path only when this step requires details missing here."
            ),
            "signals:",
            "- Skill body intentionally omitted to preserve workflow context.",
        ]
    )
    manifest = _limit_text(
        "\n".join(body),
        max_chars,
        "\n[... skill pointer manifest truncated ...]\n",
    )
    return manifest, {
        "axis": "skill_context",
        "ref": ref,
        "resolved_path": resolved_path,
        "original_chars": 0,
        "compressed_chars": len(manifest),
        "estimated_tokens_saved": 0,
    }
