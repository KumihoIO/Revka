from __future__ import annotations

import pytest


def test_memory_retrieval_limit_prefers_env(monkeypatch):
    from operator_mcp import revka_config

    monkeypatch.setenv("KUMIHO_MEMORY_RETRIEVAL_LIMIT", "3")
    monkeypatch.setattr(revka_config, "_cached_memory_retrieval_limit", None)

    assert revka_config.memory_retrieval_limit() == 3


def test_memory_min_relevance_score_prefers_env(monkeypatch):
    from operator_mcp import revka_config

    monkeypatch.setenv("REVKA_MEMORY_MIN_RELEVANCE_SCORE", "0.7")
    monkeypatch.setattr(revka_config, "_cached_memory_min_relevance_score", None)

    assert revka_config.memory_min_relevance_score() == 0.7


@pytest.mark.asyncio
async def test_memory_engage_applies_configured_limit_and_min_score(monkeypatch):
    from operator_mcp import revka_config
    from operator_mcp.tool_handlers import memory

    captured: dict = {}

    def fake_engage(args):
        captured.update(args)
        return {
            "context": "stale context",
            "results": [
                {"kref": "kref://memory/low", "summary": "low", "score": 0.2},
                {"kref": "kref://memory/high", "summary": "high", "score": 0.9},
            ],
            "source_krefs": ["kref://memory/low", "kref://memory/high"],
            "count": 2,
            "recall_mode": "summarized",
        }

    class FakeManager:
        def build_recalled_context(self, memories, query, recall_mode):
            assert query == "Cross Chronicle cc-full-synopsis character bible"
            assert recall_mode == "summarized"
            return "\n\n".join(mem["summary"] for mem in memories)

    monkeypatch.setenv("KUMIHO_MEMORY_RETRIEVAL_LIMIT", "3")
    monkeypatch.setenv("REVKA_MEMORY_MIN_RELEVANCE_SCORE", "0.7")
    monkeypatch.setattr(revka_config, "_cached_memory_retrieval_limit", None)
    monkeypatch.setattr(revka_config, "_cached_memory_min_relevance_score", None)
    monkeypatch.setattr(memory, "_HAS_KUMIHO_MEMORY", True)
    monkeypatch.setattr(memory, "_km_tool_memory_engage", fake_engage)
    monkeypatch.setattr(memory, "_km_get_manager", lambda: FakeManager())

    result = await memory.tool_memory_engage_op({
        "query": "Cross Chronicle cc-full-synopsis character bible",
        "limit": 5,
        "min_score": 0.1,
    })

    assert result["count"] == 1
    assert result["context"] == "high"
    assert result["source_krefs"] == ["kref://memory/high"]
    assert captured["limit"] == 3
    assert captured["min_score"] == 0.7
    assert captured["graph_augmented"] is True


@pytest.mark.asyncio
async def test_memory_engage_schema_uses_configured_limit_and_min_score(monkeypatch):
    from operator_mcp import revka_config
    from operator_mcp.operator_mcp import list_tools

    monkeypatch.setenv("KUMIHO_MEMORY_RETRIEVAL_LIMIT", "3")
    monkeypatch.setenv("REVKA_MEMORY_MIN_RELEVANCE_SCORE", "0.7")
    monkeypatch.setattr(revka_config, "_cached_memory_retrieval_limit", None)
    monkeypatch.setattr(revka_config, "_cached_memory_min_relevance_score", None)

    tools = await list_tools()
    memory_engage = next(tool for tool in tools if tool.name == "memory_engage")
    limit_schema = memory_engage.inputSchema["properties"]["limit"]
    min_score_schema = memory_engage.inputSchema["properties"]["min_score"]

    assert limit_schema["default"] == 3
    assert limit_schema["maximum"] == 3
    assert min_score_schema["default"] == 0.7
    assert min_score_schema["minimum"] == 0.7
