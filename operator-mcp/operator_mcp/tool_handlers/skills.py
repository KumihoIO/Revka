"""Skill tool handlers: capture, list, load."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .._log import _log
from ..construct_config import memory_project, workspace_dir
from ..kumiho_clients import KumihoAgentPoolClient
from ..skill_loader import list_skills as list_local_skills
from ..skill_loader import load_skill as load_local_skill

_SKILL_ARTIFACT_NAME = "SKILL.md"
_SKILL_ITEM_KINDS = {"skill"}
_TRAILING_AGENT_ID = re.compile(
    r"[\s_-]*(?:agent[-_\s]*)?[0-9a-f]{8,}(?:-[0-9a-f]{4,}){0,4}\s*$",
    re.IGNORECASE,
)
_WINDOWS_DRIVE_PATH = re.compile(r"^/?[A-Za-z]:[\\/]")


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


def _item_kind(item: dict[str, Any]) -> str:
    kind = item.get("kind") or item.get("item_kind")
    if isinstance(kind, str) and kind.strip():
        return kind.strip()

    kref = str(item.get("kref") or "")
    match = re.search(r"\.([A-Za-z0-9_]+)(?:[?#]|$)", kref)
    return match.group(1) if match else ""


def _skill_match_name(item: dict[str, Any]) -> str:
    return _item_name(item).strip().removesuffix(".skill")


def _item_metadata(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _revision_metadata(revision: dict[str, Any] | None) -> dict[str, Any]:
    if not revision:
        return {}
    metadata = revision.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _artifact_path_from_location(location: str) -> Path:
    if location.startswith("file://"):
        parsed = urlparse(location)
        if parsed.netloc.lower() == "localhost":
            raw_path = parsed.path
        elif _WINDOWS_DRIVE_PATH.match(parsed.netloc):
            raw_path = f"{parsed.netloc}{parsed.path}"
        elif parsed.netloc and parsed.path:
            raw_path = f"//{parsed.netloc}{parsed.path}"
        else:
            raw_path = parsed.path or location[len("file://"):]
        decoded = unquote(raw_path)
        if _WINDOWS_DRIVE_PATH.match(decoded):
            decoded = decoded.lstrip("/")
        return Path(decoded)
    return Path(location)


async def _get_skill_artifact(
    pool_client: KumihoAgentPoolClient,
    revision_kref: str,
) -> dict[str, Any] | None:
    get_artifacts = getattr(pool_client, "get_artifacts", None)
    if not get_artifacts:
        return None
    artifacts = await get_artifacts(revision_kref)
    if not isinstance(artifacts, list):
        return None
    skill_artifact = next(
        (art for art in artifacts if isinstance(art, dict) and art.get("name") == _SKILL_ARTIFACT_NAME),
        artifacts[0] if artifacts else None,
    )
    return skill_artifact if isinstance(skill_artifact, dict) else None


async def _latest_skill_revision(
    pool_client: KumihoAgentPoolClient,
    item_kref: str,
    tag: str = "published",
) -> dict[str, Any] | None:
    try:
        return await pool_client.get_latest_revision(item_kref, tag=tag)
    except Exception as e:
        _log(f"Skill revision lookup failed for {item_kref}: {e}")
        return None


def _skill_summary(
    item: dict[str, Any],
    revision: dict[str, Any] | None = None,
    artifact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item_meta = _item_metadata(item)
    rev_meta = _revision_metadata(revision)
    metadata = {**item_meta, **rev_meta}
    name = _skill_match_name(item)
    title = str(metadata.get("title") or name)
    description = str(metadata.get("description") or "")
    result: dict[str, Any] = {
        "name": name,
        "title": title,
        "description": description,
        "domain": str(metadata.get("domain") or ""),
        "kref": str(item.get("kref") or ""),
        "kind": _item_kind(item),
        "source": "kumiho",
    }
    if revision:
        result["revision_kref"] = str(revision.get("kref") or "")
        if metadata.get("created_at"):
            result["created_at"] = str(metadata["created_at"])
        if metadata.get("change_summary"):
            result["change_summary"] = str(metadata["change_summary"])
    if artifact:
        result["artifact_kref"] = str(artifact.get("kref") or "")
        result["artifact_name"] = str(artifact.get("name") or _SKILL_ARTIFACT_NAME)
    return result


async def _find_skill_by_name_or_kref(
    pool_client: KumihoAgentPoolClient,
    space_path: str,
    name_or_kref: str,
) -> dict[str, Any] | None:
    items = await pool_client.list_items(space_path)
    for item in items:
        if _item_kind(item) not in _SKILL_ITEM_KINDS:
            continue
        if str(item.get("kref") or "") == name_or_kref:
            return item
        if _skill_match_name(item) == name_or_kref:
            return item
    return None


async def _find_existing_skill(
    pool_client: KumihoAgentPoolClient,
    space_path: str,
    name: str,
) -> dict[str, Any] | None:
    items = await pool_client.list_items(space_path)
    for item in items:
        if _item_kind(item) in _SKILL_ITEM_KINDS and _skill_match_name(item) == name:
            return item
    return None


async def _load_revision_artifact(
    pool_client: KumihoAgentPoolClient,
    revision_kref: str,
) -> tuple[str, str, str]:
    skill_artifact = await _get_skill_artifact(pool_client, revision_kref)
    if not skill_artifact:
        return "", "", ""
    location = str(skill_artifact.get("location") or "")
    if not location:
        return "", "", ""
    path = _artifact_path_from_location(location)
    try:
        content = path.read_text(encoding="utf-8")
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


async def tool_list_skills(args: dict[str, Any], pool_client: KumihoAgentPoolClient) -> dict[str, Any]:
    """List captured skills from Kumiho."""
    project = memory_project()
    space_path = f"/{project}/Skills"
    include_legacy_disk = bool(args.get("include_legacy_disk"))
    include_details = bool(args.get("include_details"))

    if hasattr(pool_client, "_ensure_available") and not pool_client._ensure_available():  # type: ignore[attr-defined]
        if not include_legacy_disk:
            return {"error": "Kumiho is not available", "skills": [], "count": 0, "source": "kumiho"}
        skills = [{**skill, "source": "local_legacy"} for skill in list_local_skills()]
        return {"skills": skills, "count": len(skills), "source": "local_legacy"}

    try:
        items = await pool_client.list_items(space_path)
        skill_items = [item for item in items if _item_kind(item) in _SKILL_ITEM_KINDS]
        if not include_details:
            results = [_skill_summary(item) for item in skill_items]
            results.sort(key=lambda skill: skill["name"])
            return {
                "skills": results,
                "count": len(results),
                "source": "kumiho",
                "space": space_path,
                "details_included": False,
            }

        revision_by_item: dict[str, dict[str, Any]] = {}
        batch_get_revisions = getattr(pool_client, "batch_get_revisions", None)
        item_krefs = [str(item.get("kref") or "") for item in skill_items if item.get("kref")]
        if batch_get_revisions and item_krefs:
            try:
                revision_by_item = await batch_get_revisions(item_krefs, "published")
            except Exception as e:
                _log(f"Skill batch revision lookup failed: {e}")

        results: list[dict[str, Any]] = []
        for item in skill_items:
            item_kref = str(item.get("kref") or "")
            revision = revision_by_item.get(item_kref)
            if revision is None and item_kref:
                revision = await _latest_skill_revision(pool_client, item_kref)
            revision_kref = str(revision.get("kref") or "") if revision else ""
            artifact = await _get_skill_artifact(pool_client, revision_kref) if revision_kref else None
            results.append(_skill_summary(item, revision, artifact))
        results.sort(key=lambda skill: skill["name"])
        return {
            "skills": results,
            "count": len(results),
            "source": "kumiho",
            "space": space_path,
            "details_included": True,
        }
    except Exception as e:
        _log(f"Skill list failed: {e}")
        return {"error": f"Failed to list skills from Kumiho: {e}", "skills": [], "count": 0, "source": "kumiho"}


async def tool_load_skill(args: dict[str, Any], pool_client: KumihoAgentPoolClient) -> dict[str, Any]:
    """Load a captured skill's published SKILL.md artifact from Kumiho."""
    name = args["name"]
    tag = str(args.get("tag") or "published")
    allow_legacy_disk = bool(args.get("allow_legacy_disk_fallback"))
    project = memory_project()
    space_path = f"/{project}/Skills"

    if hasattr(pool_client, "_ensure_available") and not pool_client._ensure_available():  # type: ignore[attr-defined]
        if allow_legacy_disk:
            content = load_local_skill(name)
            if content is not None:
                return {"name": name, "content": content, "source": "local_legacy"}
        return {"error": "Kumiho is not available", "source": "kumiho"}

    try:
        item = await _find_skill_by_name_or_kref(pool_client, space_path, name)
        if not item:
            if allow_legacy_disk:
                content = load_local_skill(name)
                if content is not None:
                    return {"name": name, "content": content, "source": "local_legacy"}
            return {"error": f"Skill not found in Kumiho: {name}", "source": "kumiho"}

        item_kref = str(item.get("kref") or "")
        revision = await _latest_skill_revision(pool_client, item_kref, tag=tag)
        if not revision:
            return {"error": f"Skill has no {tag} revision: {name}", "source": "kumiho", "item_kref": item_kref}

        revision_kref = str(revision.get("kref") or "")
        content, artifact_path, artifact_kref = await _load_revision_artifact(pool_client, revision_kref)
        if not content:
            return {
                "error": f"Skill revision has no readable {_SKILL_ARTIFACT_NAME} artifact: {name}",
                "source": "kumiho",
                "item_kref": item_kref,
                "revision_kref": revision_kref,
                "artifact_kref": artifact_kref,
            }

        return {
            "name": _skill_match_name(item),
            "content": content,
            "source": "kumiho",
            "item_kref": item_kref,
            "revision_kref": revision_kref,
            "artifact_kref": artifact_kref,
            "artifact_path": artifact_path,
            "tag": tag,
        }
    except Exception as e:
        _log(f"Skill load failed: {e}")
        return {"error": f"Failed to load skill from Kumiho: {e}", "source": "kumiho"}
