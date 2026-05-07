"""Tool handler for structured context compaction (B1).

Exposes compact_conversation as an MCP tool that:
1. Sends the 9-section compact prompt to the active agent
2. Parses the summary from the response
3. Stores the compacted summary in Kumiho via the SDK client
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

_MEMORY_PROJECT = os.environ.get("KUMIHO_MEMORY_PROJECT", "CognitiveMemory")

from .._log import _log
from ..compaction import (
    COMPACT_PROMPT,
    build_compaction_capture,
    estimate_tokens,
    parse_compact_output,
)
from ..journal import SessionJournal


async def tool_compact_conversation(
    args: dict[str, Any],
    journal: SessionJournal,
    kumiho_sdk: Any | None = None,
) -> dict[str, Any]:
    """Compact the current conversation context.

    This tool is designed to be called by the operator LLM when context
    is getting large, or manually by the user.

    The LLM should:
    1. Call this tool to get the compact prompt template
    2. Process its own conversation through the template
    3. Return the structured summary

    Since we can't directly access the LLM's conversation from here,
    this tool returns the prompt template and instructions. The LLM
    applies it to its own context and calls store_compaction with the result.
    """
    session_id = args.get("session_id") or journal.session_id
    reason = args.get("reason", "manual")

    try:
        journal.record(
            "operator",
            "compaction_started",
            summary=f"reason={reason}",
        )
    except Exception:
        pass  # Non-critical — compaction proceeds even if journal fails

    return {
        "status": "prompt_ready",
        "session_id": session_id,
        "compact_prompt": COMPACT_PROMPT,
        "instructions": (
            "Apply this prompt to your full conversation context. "
            "Produce the <analysis> and <summary> blocks, then call "
            "store_compaction with the raw output."
        ),
    }


async def tool_store_compaction(
    args: dict[str, Any],
    journal: SessionJournal,
    kumiho_sdk: Any | None = None,
) -> dict[str, Any]:
    """Store a compacted summary in Kumiho.

    Called after the LLM has produced a compact summary via the
    9-section template.
    """
    raw_output = args.get("raw_output", "")
    session_id = args.get("session_id") or journal.session_id
    source_krefs = args.get("source_krefs", [])

    if not raw_output.strip():
        return {"error": "raw_output is required — provide the full compact output including <summary> tags."}

    # Parse out the summary
    summary = parse_compact_output(raw_output)
    token_estimate = estimate_tokens(summary)

    # Build Kumiho capture
    capture = build_compaction_capture(session_id, summary, source_krefs)

    # Store in Kumiho if SDK is available
    stored_kref = None
    if kumiho_sdk and getattr(kumiho_sdk, "_available", False):
        try:
            # Ensure every level of the nested compactions space exists.
            # ensure_space() only creates a single space directly under a
            # project — passing "compactions/<session>" as a name would create
            # a literal slashed-name space, not a nested one. Reuse the
            # walk-and-create helper from workflow.memory (added in PR #171).
            from ..workflow.memory import _ensure_space_path
            await _ensure_space_path(f"/{_MEMORY_PROJECT}/compactions/{session_id}")

            # Create item for this compaction
            now = datetime.now(timezone.utc)
            item_name = f"compact-{now.strftime('%Y%m%dT%H%M%S')}"
            item = await kumiho_sdk.create_item(
                f"/{_MEMORY_PROJECT}/compactions/{session_id}",
                item_name,
                "summary",
                metadata={
                    "session_id": session_id,
                    "compacted_at": now.isoformat(),
                    "token_estimate": str(token_estimate),
                },
            )

            # Create revision with the summary content
            rev = await kumiho_sdk.create_revision(
                item["kref"],
                metadata={
                    "content": summary[:8000],  # Kumiho metadata value limit
                    "tags": ",".join(capture["tags"]),
                },
                tag="published",
            )
            stored_kref = rev.get("kref")
            _log(f"Compaction stored in Kumiho: {stored_kref}")

        except Exception as e:
            _log(f"Kumiho compaction store failed (non-fatal): {e}")

    try:
        journal.record(
            "operator",
            "compaction_stored",
            summary=f"tokens={token_estimate}, kref={stored_kref or 'local-only'}",
        )
    except Exception:
        pass  # Non-critical

    result: dict[str, Any] = {
        "status": "stored",
        "session_id": session_id,
        "summary_tokens": token_estimate,
        "sections_found": summary.count("## "),
    }
    if stored_kref:
        result["kumiho_kref"] = stored_kref
    result["capture"] = capture

    return result
