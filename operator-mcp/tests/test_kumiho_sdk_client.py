from __future__ import annotations

import pytest

import operator_mcp.kumiho_clients as kumiho_clients
from operator_mcp.kumiho_clients import KumihoSDKClient


@pytest.mark.asyncio
async def test_create_bundle_ensures_parent_space(monkeypatch):
    calls: list[tuple] = []

    def create_project(project: str):
        calls.append(("create_project", project))
        return {"created": True}

    def create_space(project: str, space: str, parent_path: str | None = None):
        calls.append(("create_space", project, space, parent_path))
        return {"created": True}

    def create_bundle(space_path: str, name: str, metadata: dict[str, str] | None = None):
        calls.append(("create_bundle", space_path, name, metadata))
        return {
            "bundle": {
                "kref": f"kref://{space_path}/{name}.bundle",
                "name": name,
            }
        }

    monkeypatch.setattr(kumiho_clients, "tool_create_project", create_project)
    monkeypatch.setattr(kumiho_clients, "tool_create_space", create_space)
    monkeypatch.setattr(kumiho_clients, "tool_create_bundle", create_bundle)

    bundle = await KumihoSDKClient().create_bundle(
        "/ManghanDev/Bundles",
        "manghan-production-episodes",
        metadata={"source": "workflow"},
    )

    assert bundle["kref"] == "kref://ManghanDev/Bundles/manghan-production-episodes.bundle"
    assert calls == [
        ("create_project", "ManghanDev"),
        ("create_space", "ManghanDev", "Bundles", None),
        ("create_bundle", "ManghanDev/Bundles", "manghan-production-episodes", {"source": "workflow"}),
    ]


@pytest.mark.asyncio
async def test_ensure_space_path_creates_nested_segments(monkeypatch):
    calls: list[tuple] = []

    def create_project(project: str):
        calls.append(("create_project", project))
        return {"error": f"Project '{project}' already exists"}

    def create_space(project: str, space: str, parent_path: str | None = None):
        calls.append(("create_space", project, space, parent_path))
        return {"created": True}

    monkeypatch.setattr(kumiho_clients, "tool_create_project", create_project)
    monkeypatch.setattr(kumiho_clients, "tool_create_space", create_space)

    await KumihoSDKClient().ensure_space_path("ManghanDev/Bundles/Canon")

    assert calls == [
        ("create_project", "ManghanDev"),
        ("create_space", "ManghanDev", "Bundles", None),
        ("create_space", "ManghanDev", "Canon", "/ManghanDev/Bundles"),
    ]
