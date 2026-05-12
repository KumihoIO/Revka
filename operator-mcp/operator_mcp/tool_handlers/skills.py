"""Skill tool handlers: capture, list, load."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .._log import _log
from ..construct_config import memory_project, workspace_dir
from ..kumiho_clients import KumihoAgentPoolClient
from ..skill_loader import list_skills, load_skill

_SKILL_ARTIFACT_NAME = "SKILL.md"
_TRAILING_AGENT_ID = re.compile(
    r"[\s_-]*(?:agent[-_\s]*)?[0-9a-f]{8,}(?:-[0-9a-f]{4,}){0,4}\s*$",
    re.IGNORECASE,
)


def _slug(value: str, *, default: str = "skill") -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip(".-_").lower()
    return slug[:120] or default


def _clean_skill_name(name: str, agent_id: str = "") -> str:
    cleaned = name.strip()
    if agent_id:
        escaped = re.escape(agent_id.strip())
        cleaned = re.sub(rf"[\s_-]*(?:\({escaped}\)|{escaped})\s*$", "", cleaned).strip()
    cleaned = _TRAILING_AGENT_ID.sub("", cleaned).strip()
    return cleaned or name.strip()


def _artifact_path(project: str, space: str, item_name: str, item_kind: str, artifact_name: str) -> Path:
    root = Path(workspace_dir()).expanduser().resolve() / "artifact"
    parts = [
        _slug(project, default="project"),
        *[_slug(part, default="space") for part in space.split("/") if part.strip()],
        _slug(item_name, default="item"),
        _slug(item_kind, default="kind"),
    ]
    path = root.joinpath(*parts, artifact_name)
    real_root = root.resolve()
    real_parent = path.parent.resolve()
    if real_root != real_parent and real_root not in real_parent.parents:
        raise ValueError("skill artifact path would escape workspace artifact root")
    return path


def _metadata_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        import json

        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _metadata(values: dict[str, Any]) -> dict[str, str]:
    return {str(k): _metadata_value(v) for k, v in values.items() if v is not None}


def _item_name(item: dict[str, Any]) -> str:
    return str(item.get("item_name") or item.get("name") or "")


async def _find_existing_skill(
    pool_client: KumihoAgentPoolClient,
    space_path: str,
    name: str,
) -> dict[str, Any] | None:
    items = await pool_client.list_items(space_path)
    for item in items:
        if _item_name(item) == name and str(item.get("kind", item.get("item_kind", "skill"))) == "skill":
            return item
    for item in items:
        if _item_name(item) == name:
            return item
    return None


async def _load_revision_artifact(
    pool_client: KumihoAgentPoolClient,
    revision_kref: str,
) -> tuple[str, str, str]:
    get_artifacts = getattr(pool_client, "get_artifacts", None)
    if not get_artifacts:
        return "", "", ""
    artifacts = await get_artifacts(revision_kref)
    if not isinstance(artifacts, list):
        return "", "", ""
    skill_artifact = next(
        (art for art in artifacts if art.get("name") == _SKILL_ARTIFACT_NAME),
        artifacts[0] if artifacts else None,
    )
    if not isinstance(skill_artifact, dict):
        return "", "", ""
    location = str(skill_artifact.get("location") or "")
    if not location:
        return "", "", ""
    try:
        content = Path(location).read_text(encoding="utf-8")
    except OSError:
        return "", location, str(skill_artifact.get("kref") or "")
    return content, location, str(skill_artifact.get("kref") or "")


async def tool_capture_skill(args: dict[str, Any], pool_client: KumihoAgentPoolClient) -> dict[str, Any]:
    if hasattr(pool_client, "_ensure_available") and not pool_client._ensure_available():  # type: ignore[attr-defined]
        return {"error": "Kumiho is not available"}

    agent_id = str(args.get("agent_id") or args.get("source_agent_id") or "")
    name = _clean_skill_name(str(args["name"]), agent_id)
    domain = args["domain"]
    description = args["description"]
    procedure = args["procedure"]
    learned_from = args.get("learned_from", "")
    change_summary = args.get("change_summary", "")
    source_revision_krefs = args.get("source_revision_krefs") or []
    project = memory_project()
    space = "Skills"
    space_path = f"/{project}/{space}"
    item_kind = "skill"
    now = datetime.now(timezone.utc).isoformat()

    item_metadata = _metadata({
        "description": description,
        "domain": domain,
        "source": "operator-capture-skill",
    })

    revision_metadata: dict[str, Any] = {
        "description": description,
        "domain": domain,
        "learned_from": learned_from,
        "change_summary": change_summary,
        "source": "operator-capture-skill",
        "created_at": now,
        "artifact_name": _SKILL_ARTIFACT_NAME,
    }
    if agent_id:
        revision_metadata["agent_id"] = agent_id
    if source_revision_krefs:
        revision_metadata["source_revision_krefs"] = source_revision_krefs

    try:
        await pool_client.ensure_space(project, space)
        existing = await _find_existing_skill(pool_client, space_path, name)
        previous_revision_kref = ""
        previous_artifact_path = ""
        previous_artifact_kref = ""
        previous_procedure = ""

        if existing and existing.get("kref"):
            item = existing
            previous_revision = await pool_client.get_latest_revision(existing["kref"], tag="published")
            if previous_revision:
                previous_revision_kref = str(previous_revision.get("kref") or "")
                revision_metadata["previous_revision_kref"] = previous_revision_kref
                previous_procedure, previous_artifact_path, previous_artifact_kref = await _load_revision_artifact(
                    pool_client,
                    previous_revision_kref,
                )
                if previous_artifact_path:
                    revision_metadata["previous_artifact_path"] = previous_artifact_path
                if previous_artifact_kref:
                    revision_metadata["previous_artifact_kref"] = previous_artifact_kref
        else:
            item = await pool_client.create_item(space_path, name, item_kind, item_metadata)

        item_kref = str(item.get("kref") or "")
        if not item_kref:
            return {"error": "Failed to capture skill: item creation returned no kref"}

        artifact_path = _artifact_path(project, space, name, item_kind, _SKILL_ARTIFACT_NAME)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(procedure, encoding="utf-8")

        revision_metadata["artifact_path"] = str(artifact_path)
        revision_metadata["content_length"] = len(procedure)
        revision_metadata["previous_content_length"] = len(previous_procedure)

        rev = await pool_client.create_revision(item_kref, _metadata(revision_metadata), tag=None)
        rev_kref = str(rev.get("kref") or "")
        if not rev_kref:
            return {"error": "Failed to capture skill: revision creation returned no kref"}

        artifact = await pool_client.create_artifact(rev_kref, _SKILL_ARTIFACT_NAME, str(artifact_path))
        artifact_kref = str(artifact.get("kref") or "") if isinstance(artifact, dict) else ""
        if not artifact_kref:
            return {"error": "Failed to capture skill: create_artifact returned no kref"}

        await pool_client.tag_revision(rev_kref, "published")
    except Exception as e:
        _log(f"Skill capture failed: {e}")
        return {"error": f"Failed to capture skill: {e}"}

    _log(f"Captured skill '{name}' [{domain}] (kref={item_kref}, rev={rev_kref})")
    return {
        "captured": True,
        "name": name,
        "item_kref": item_kref,
        "revision_kref": rev_kref,
        "artifact_kref": artifact_kref,
        "artifact_path": str(artifact_path),
        "updated_existing": bool(previous_revision_kref),
        "previous_revision_kref": previous_revision_kref,
        "previous_artifact_path": previous_artifact_path,
        "previous_artifact_kref": previous_artifact_kref,
        "read_previous_artifact": bool(previous_procedure),
    }


async def tool_list_skills() -> dict[str, Any]:
    """List all available orchestration skills."""
    skills = list_skills()
    return {"skills": skills, "count": len(skills)}


async def tool_load_skill(args: dict[str, Any]) -> dict[str, Any]:
    """Load a specific skill's content."""
    name = args["name"]
    content = load_skill(name)
    if content is None:
        return {"error": f"Skill not found: {name}"}
    return {"name": name, "content": content}
