"""Operator memory tools — expose Kumiho memory operations as first-class MCP tools.

The kumiho-memory MCP server is injected into agent sessions, but the Operator
itself only used Kumiho indirectly via KUMIHO_SDK for team/pool management.
That meant the orchestrator could not query or write the shared memory graph
directly — and ``list_tools`` reported an incomplete view of what's available.

This module re-exposes the most useful kumiho.mcp_server tool functions under
operator-namespaced names (``memory_*``) so the orchestrator can engage memory
the same way agents do.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from .._log import _log
from ..construct_config import memory_retrieval_limit

# Strip question framing from queries before forwarding to engage. The
# graph engine scores documents against the query terms; question framing
# words ("do you recall", "tell me about", trailing "?", etc.) match
# unrelated memories and crowd out the actual subject. The LLM should
# normally reformulate via the tool description guidance, but this serves
# as a belt-and-suspenders normaliser when the LLM passes the question
# verbatim.
_QUESTION_PREFIXES = re.compile(
    r"^\s*(do\s+you\s+(recall|know|remember)|"
    r"tell\s+me\s+(about|something\s+about)|"
    r"can\s+you\s+(recall|find|tell\s+me)|"
    r"what(?:'s|\s+is|\s+do\s+you\s+know\s+about|\s+about)|"
    r"i\s+(want\s+to\s+know|need)\s+(about\s+)?)\s*",
    re.IGNORECASE,
)


def _normalize_query(q: str) -> str:
    """Strip common question-framing prefixes and trailing punctuation."""
    if not q:
        return q
    out = _QUESTION_PREFIXES.sub("", q).strip()
    out = re.sub(r"[?!.]+\s*$", "", out).strip()
    return out or q

try:
    from kumiho.mcp_server import (
        tool_memory_store,
        tool_memory_retrieve,
        tool_get_item,
        tool_search_items,
        tool_fulltext_search,
        tool_resolve_kref,
        tool_get_revision_by_tag,
    )

    _HAS_KUMIHO = True
except ImportError:
    _HAS_KUMIHO = False

# kumiho-memory provides the smart engage/reflect path (graph-augmented
# recall + sibling enrichment + post-consolidation edge discovery). The
# operator wrappers below delegate to these instead of re-implementing
# the same flow on top of the lower-level kumiho.mcp_server primitives.
try:
    from kumiho_memory.mcp_tools import (
        tool_memory_engage as _km_tool_memory_engage,
        tool_memory_reflect as _km_tool_memory_reflect,
    )

    _HAS_KUMIHO_MEMORY = True
except ImportError:
    _HAS_KUMIHO_MEMORY = False


def _unavailable() -> dict[str, Any]:
    return {
        "error": "kumiho package not available — install via `construct sidecars install`",
    }


def _unavailable_kumiho_memory() -> dict[str, Any]:
    return {
        "error": (
            "kumiho-memory package not available — install via "
            "`construct sidecars install` or `pip install kumiho-memory`"
        ),
    }


# ---------------------------------------------------------------------------
# Memory retrieval (read-side)
# ---------------------------------------------------------------------------


async def tool_memory_retrieve_op(args: dict[str, Any]) -> dict[str, Any]:
    """Fuzzy memory retrieval — Google-like semantic search across memory items.

    Returns a list of matching items with revision metadata. Best for natural
    language queries like "what did we decide about gRPC vs REST".
    """
    if not _HAS_KUMIHO:
        return _unavailable()
    return await asyncio.to_thread(
        tool_memory_retrieve,
        project=args.get("project", "CognitiveMemory"),
        query=args.get("query", ""),
        keywords=args.get("keywords"),
        topics=args.get("topics"),
        space_paths=args.get("space_paths"),
        memory_item_kind=args.get("memory_item_kind", "conversation"),
        limit=args.get("limit", memory_retrieval_limit()),
        mode=args.get("mode", "search"),
        memory_types=args.get("memory_types"),
    )


async def tool_memory_search_op(args: dict[str, Any]) -> dict[str, Any]:
    """Structured search by name/kind/context. Use for exact-match lookups."""
    if not _HAS_KUMIHO:
        return _unavailable()
    return await asyncio.to_thread(
        tool_search_items,
        context_filter=args.get("context_filter", ""),
        name_filter=args.get("name_filter", ""),
        kind_filter=args.get("kind_filter", ""),
        include_metadata=args.get("include_metadata", False),
    )


async def tool_memory_fulltext_op(args: dict[str, Any]) -> dict[str, Any]:
    """Full-text fuzzy search across items (Google-like)."""
    if not _HAS_KUMIHO:
        return _unavailable()
    query = args.get("query")
    if not query:
        return {"error": "query is required"}
    return await asyncio.to_thread(
        tool_fulltext_search,
        query=query,
        context=args.get("context", ""),
        kind=args.get("kind", ""),
        include_deprecated=args.get("include_deprecated", False),
        include_revision_metadata=args.get("include_revision_metadata", False),
        limit=args.get("limit", 20),
    )


async def tool_memory_get_item_op(args: dict[str, Any]) -> dict[str, Any]:
    """Get an item by its kref URI."""
    if not _HAS_KUMIHO:
        return _unavailable()
    kref = args.get("kref")
    if not kref:
        return {"error": "kref is required"}
    return await asyncio.to_thread(tool_get_item, kref=kref)


async def tool_memory_resolve_kref_op(args: dict[str, Any]) -> dict[str, Any]:
    """Resolve a kref URI into its concrete item / revision identifiers."""
    if not _HAS_KUMIHO:
        return _unavailable()
    kref = args.get("kref")
    if not kref:
        return {"error": "kref is required"}
    return await asyncio.to_thread(tool_resolve_kref, kref=kref)


async def tool_memory_get_revision_by_tag_op(args: dict[str, Any]) -> dict[str, Any]:
    """Get a specific revision by its tag (e.g. 'published', 'stable')."""
    if not _HAS_KUMIHO:
        return _unavailable()
    item_kref = args.get("item_kref")
    tag = args.get("tag", "published")
    if not item_kref:
        return {"error": "item_kref is required"}
    return await asyncio.to_thread(tool_get_revision_by_tag, item_kref=item_kref, tag=tag)


# ---------------------------------------------------------------------------
# Memory writes
# ---------------------------------------------------------------------------


async def tool_memory_store_op(args: dict[str, Any]) -> dict[str, Any]:
    """Store a memory bundle (decision, fact, preference, summary, etc.).

    Auto-stacks revisions on similar items by default to avoid duplicates.
    """
    if not _HAS_KUMIHO:
        return _unavailable()
    return await asyncio.to_thread(
        tool_memory_store,
        project=args.get("project", "CognitiveMemory"),
        space_path=args.get("space_path", ""),
        space_hint=args.get("space_hint", ""),
        memory_item_kind=args.get("memory_item_kind", "conversation"),
        bundle_name=args.get("bundle_name", ""),
        memory_type=args.get("memory_type", "summary"),
        title=args.get("title", ""),
        summary=args.get("summary", ""),
        user_text=args.get("user_text", ""),
        assistant_text=args.get("assistant_text", ""),
        tags=args.get("tags"),
        source_revision_krefs=args.get("source_revision_krefs"),
        metadata=args.get("metadata"),
        edge_type=args.get("edge_type", "DERIVED_FROM"),
        stack_revisions=args.get("stack_revisions", True),
    )


# ---------------------------------------------------------------------------
# Composite skill-behaviors (engage / reflect)
#
# These mirror the agent-side kumiho_memory_engage / kumiho_memory_reflect
# tools so the Operator uses the exact same recall + write paths the agents
# do (graph-augmented recall, sibling enrichment, post-consolidation edge
# discovery). Both delegate to kumiho_memory.mcp_tools so there's a single
# upstream-maintained implementation; older versions of this file
# re-implemented retrieve+stitch on the lower-level kumiho.mcp_server
# primitives, which silently dropped results when the SDK's return shape
# evolved.
# ---------------------------------------------------------------------------


async def tool_memory_engage_op(args: dict[str, Any]) -> dict[str, Any]:
    """Recall + context-build in one call. Operator-side equivalent of
    ``kumiho_memory_engage``.

    Delegates to ``kumiho_memory.tool_memory_engage`` so the orchestrator
    uses the same recall path agents use:

      - graph-augmented recall (multi-query reformulation + edge traversal)
      - sibling enrichment for connected memories
      - LLM-based reranking when an adapter is available
      - server-side context building via ``build_recalled_context``
      - 5-second recall deduplication

    Returns ``{context, results, source_krefs, count, recall_mode}`` — pass
    ``source_krefs`` to ``memory_reflect`` to create DERIVED_FROM provenance
    edges from new captures back to the recalled items.

    Defaults ``graph_augmented`` to True so chain-of-decision questions
    surface connected memories vector search alone misses. Caller can
    pass ``graph_augmented=False`` for fast single-query recall.
    """
    if not _HAS_KUMIHO_MEMORY:
        return _unavailable_kumiho_memory()
    if not args.get("query"):
        return {"error": "query is required"}

    forwarded = dict(args)
    forwarded["query"] = _normalize_query(forwarded["query"])
    forwarded.setdefault("limit", memory_retrieval_limit())
    forwarded.setdefault("graph_augmented", True)

    started = time.monotonic()
    result = await asyncio.to_thread(_km_tool_memory_engage, forwarded)
    elapsed = time.monotonic() - started
    results = result.get("results") if isinstance(result, dict) else None
    count = len(results) if isinstance(results, list) else result.get("count") if isinstance(result, dict) else None
    titles: list[str] = []
    if isinstance(results, list):
        for item in results[:5]:
            if not isinstance(item, dict):
                continue
            title = (
                item.get("title")
                or item.get("item_name")
                or item.get("name")
                or item.get("kref")
            )
            if title:
                titles.append(str(title)[:80])
    _log(
        "memory_engage "
        f"query={forwarded.get('query')!r} "
        f"limit={forwarded.get('limit')} "
        f"graph_augmented={forwarded.get('graph_augmented')} "
        f"elapsed={elapsed:.2f}s count={count} titles={titles}"
    )
    return result


async def tool_memory_reflect_op(args: dict[str, Any]) -> dict[str, Any]:
    """Buffer response + store captures with provenance edges. Operator-side
    equivalent of ``kumiho_memory_reflect``.

    Delegates to ``kumiho_memory.tool_memory_reflect`` so the orchestrator
    writes via the same path agents use:

      - response buffering into Redis working memory
      - capture storage with stack-revisions semantics
      - DERIVED_FROM edges to engage source_krefs
      - post-consolidation edge discovery for durable capture types
        (decision, architecture, implementation, synthesis, reflection)

    Each capture is ``{type, title, content, tags?, space_hint?}``.
    Returns ``{buffered, captures_stored, edges_discovered, stored_krefs}``.
    """
    if not _HAS_KUMIHO_MEMORY:
        return _unavailable_kumiho_memory()
    if not args.get("session_id"):
        return {"error": "session_id is required"}
    if not isinstance(args.get("captures") or [], list):
        return {"error": "captures must be a list of {type, title, content, ...}"}

    return await asyncio.to_thread(_km_tool_memory_reflect, args)
