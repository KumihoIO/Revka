"""CanonWorks operator tools.

CanonWorks bootstraps the Kumiho canon graph that the serial writing workflows
consume: projects, spaces, bundles, canonical items, revisions, artifacts, and
relationship edges.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import uuid
from pathlib import Path
from typing import Any

try:  # pragma: no cover - PyYAML is present in normal runtime/test envs.
    import yaml
except Exception:  # pragma: no cover
    yaml = None  # type: ignore[assignment]


CORE_BUNDLES = {
    "main_canon": "main-canon",
    "production_style": "production-style",
    "production_episodes": "production-episodes",
    "canon_patch_candidates": "canon-patch-candidates",
    "current_character_states": "current-character-states",
    "current_relationship_states": "current-relationship-states",
    "current_timeline_progress": "current-timeline-progress",
    "current_storyline_progress": "current-storyline-progress",
    "current_foreshadow_progress": "current-foreshadow-progress",
    "state_sync_snapshots": "state-sync-snapshots",
    "canon_state_sync_reports": "canon-state-sync-reports",
    "active_storylines": "active-storylines",
    "active_foreshadow": "active-foreshadow",
    "context_packs": "context-packs",
    "blocked_episodes": "blocked-episodes",
}


SPACE_KEYS = {
    "config": "Config",
    "series": "Series",
    "canon_rules": "CanonRules",
    "characters": "Characters",
    "relationships": "Relationships",
    "timeline": "Timeline",
    "roadmaps": "Roadmaps",
    "style_guides": "StyleGuides",
    "volumes": "Volumes",
    "episodes": "Episodes",
    "patches": "Patches",
    "context_packs": "ContextPacks",
    "state": "State",
    "progress": "Progress",
    "reports": "Reports",
    "personas": "Personas",
    "bundles": "Bundles",
}


TOP_LEVEL_SEED_FIELDS = {
    "title",
    "series_title",
    "project",
    "project_name",
    "project_id",
    "kumiho_project",
    "story_slug",
    "slug",
    "id",
    "premise",
    "logline",
    "synopsis",
    "language",
    "cadence",
    "target_length",
    "default_episode_length_chars",
    "genre_modules",
    "themes",
    "canon_guardrails",
    "guardrails",
    "characters",
    "relationships",
    "timeline_events",
    "storylines",
    "foreshadow_threads",
    "style_guide",
    "external_reference_seed",
    "reference_seed",
    "agent_personas",
    "priority_rules",
    "audit_rules",
    "episode_name_prefix",
    "episode_id_prefix",
    "volume_bundle_prefix",
    "artifact_root",
    "workspace_dir",
    "spaces",
    "bundles",
}


def _as_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [value]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first(*values: Any) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _slugify(value: str, fallback: str = "canonworks-project") -> str:
    slug = re.sub(r"[^A-Za-z0-9가-힣_-]+", "-", str(value).strip()).strip("-").lower()
    return slug or fallback


def _path_segment(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._") or "artifact"


def _yaml_dump(value: dict[str, Any]) -> str:
    if yaml is not None:
        return yaml.safe_dump(value, allow_unicode=True, sort_keys=False)
    return json.dumps(value, ensure_ascii=False, indent=2)


def _jsonable_metadata(value: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, raw in value.items():
        if raw is None:
            continue
        if isinstance(raw, (str, int, float, bool)):
            out[str(key)] = raw
        else:
            out[str(key)] = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
    return out


def _workspace_root(args: dict[str, Any], story_slug: str) -> Path:
    base = _first(args.get("artifact_root"), args.get("workspace_dir"))
    if base:
        return Path(base).expanduser() / story_slug
    return Path.home() / ".revka" / "canonworks" / story_slug


def _state_root(args: dict[str, Any] | None = None) -> Path:
    raw = _first((args or {}).get("state_root"), (args or {}).get("canonworks_state_root"))
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".revka" / "canonworks"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _session_path(args: dict[str, Any], session_id: str) -> Path:
    return _state_root(args) / "sessions" / f"{_path_segment(session_id)}.json"


def _project_state_path(args: dict[str, Any], project: str, story_slug: str) -> Path:
    name = f"{_path_segment(project)}__{_path_segment(story_slug)}.json"
    return _state_root(args) / "projects" / name


class CanonWorksStateError(Exception):
    def __init__(self, path: Path, message: str) -> None:
        super().__init__(message)
        self.path = path


def _state_error_response(exc: CanonWorksStateError) -> dict[str, Any]:
    return {
        "success": False,
        "error": str(exc),
        "error_code": "canonworks_state_error",
        "state_path": str(exc.path),
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CanonWorksStateError(path, f"CanonWorks state file is not valid JSON: {path}") from exc
    except OSError as exc:
        raise CanonWorksStateError(path, f"Cannot read CanonWorks state file: {path}") from exc
    if not isinstance(data, dict):
        raise CanonWorksStateError(path, f"CanonWorks state file must contain a JSON object: {path}")
    return data


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _seed_from_args(args: dict[str, Any]) -> dict[str, Any]:
    seed: dict[str, Any] = {}
    for key in ("seed", "answers", "draft", "canon_seed"):
        value = args.get(key)
        if isinstance(value, dict):
            seed.update(value)
    for key in TOP_LEVEL_SEED_FIELDS:
        if key in args:
            seed[key] = args[key]
    return {key: value for key, value in seed.items() if value is not None}


def _merge_seed(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in incoming.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            nested = dict(merged[key])
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value
    return merged


def _load_session(args: dict[str, Any]) -> dict[str, Any]:
    session_id = _first(args.get("session_id"))
    if not session_id:
        return {}
    return _read_json(_session_path(args, session_id))


def _save_session(args: dict[str, Any], session: dict[str, Any]) -> None:
    _write_json(_session_path(args, session["session_id"]), session)


def _derive_project_shape(args: dict[str, Any]) -> dict[str, Any]:
    title = _first(args.get("title"), args.get("series_title"))
    story_slug = _first(args.get("story_slug"), args.get("slug"), args.get("id"), _slugify(title))
    project = _first(
        args.get("project"),
        args.get("project_name"),
        args.get("project_id"),
        args.get("kumiho_project"),
        _slugify(title, "CanonWorksProject"),
    )
    spaces_config = _as_dict(args.get("spaces"))
    spaces = {key: _space_path(project, spaces_config, key) for key in SPACE_KEYS}
    bundles = {
        key: _first(_as_dict(args.get("bundles")).get(key), f"{story_slug}-{suffix}")
        for key, suffix in CORE_BUNDLES.items()
    }
    return {"title": title, "project": project, "story_slug": story_slug, "spaces": spaces, "bundles": bundles}


async def _ensure_project_scaffold(args: dict[str, Any], sdk: Any | None) -> dict[str, Any]:
    """Create the Kumiho project and canonical spaces once a project name exists."""
    has_project_signal = _first(
        args.get("project"),
        args.get("project_name"),
        args.get("project_id"),
        args.get("kumiho_project"),
        args.get("title"),
        args.get("series_title"),
    )
    if not has_project_signal:
        return {"status": "waiting_for_project", "created": False}
    shape = _derive_project_shape(args)
    project = shape["project"]
    if sdk is None:
        return {"status": "not_requested", "created": False, "project": project}
    if hasattr(sdk, "_lazy_init"):
        sdk._lazy_init()
    if not getattr(sdk, "_available", True):
        return {
            "status": "failed",
            "created": False,
            "project": project,
            "error": "Kumiho SDK unavailable",
        }
    spaces = shape["spaces"]
    ensured = [project]
    try:
        await sdk.ensure_space_path(project)
        for path in spaces.values():
            await sdk.ensure_space_path(path)
            ensured.append(path)
    except Exception as exc:
        return {
            "status": "failed",
            "created": False,
            "project": project,
            "error": f"Failed to create CanonWorks project scaffold: {exc}",
        }
    return {
        "status": "ready",
        "created": True,
        "project": project,
        "story_slug": shape["story_slug"],
        "spaces": [{"key": key, "path": path} for key, path in spaces.items()],
        "ensured_paths": ensured,
    }


def _readiness_report(args: dict[str, Any]) -> dict[str, Any]:
    title = _first(args.get("title"), args.get("series_title"))
    premise = _first(args.get("premise"), args.get("logline"))
    characters = [_as_dict(c) for c in _as_list(args.get("characters"))]
    relationships = [_as_dict(r) for r in _as_list(args.get("relationships"))]
    timeline_events = [_as_dict(e) for e in _as_list(args.get("timeline_events"))]
    storylines = [_as_dict(s) for s in _as_list(args.get("storylines"))]
    foreshadow_threads = [_as_dict(f) for f in _as_list(args.get("foreshadow_threads"))]
    style_guide = _first(args.get("style_guide"))

    blocking: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    if not title:
        blocking.append({"field": "title", "message": "작품 제목이 필요합니다."})
    if not premise:
        blocking.append({"field": "premise", "message": "작품의 핵심 premise/logline이 필요합니다."})
    if not characters:
        blocking.append({"field": "characters", "message": "최소 1명 이상의 핵심 캐릭터가 필요합니다."})
    if not style_guide:
        warnings.append({"field": "style_guide", "message": "문체/POV/플랫폼 규칙이 없으면 회차 품질이 흔들릴 수 있습니다."})
    if not storylines:
        warnings.append({"field": "storylines", "message": "장기 storyline이 없으면 episode goal 자동화가 약해집니다."})
    if not timeline_events:
        warnings.append({"field": "timeline_events", "message": "초기 timeline anchor가 없으면 backfill/state sync 기준점이 약해집니다."})
    if not foreshadow_threads:
        warnings.append({"field": "foreshadow_threads", "message": "초기 foreshadow thread가 없으면 장기 회수 추적이 약해집니다."})

    character_ids = {_character_name(character, index) for index, character in enumerate(characters, start=1)}
    for rel in relationships:
        source_raw = _first(rel.get("from"), rel.get("source"), rel.get("source_id"))
        target_raw = _first(rel.get("to"), rel.get("target"), rel.get("target_id"))
        source = _slugify(source_raw, "")
        target = _slugify(target_raw, "")
        if not source or not target:
            blocking.append({"field": "relationships", "message": "관계에는 from/to 캐릭터 id가 필요합니다."})
        elif source not in character_ids or target not in character_ids:
            blocking.append({
                "field": "relationships",
                "message": f"관계 endpoint가 캐릭터 id와 맞지 않습니다: {source_raw} -> {target_raw}",
            })

    completion_fields = {
        "title": bool(title),
        "premise": bool(premise),
        "characters": bool(characters),
        "relationships": bool(relationships),
        "timeline_events": bool(timeline_events),
        "storylines": bool(storylines),
        "foreshadow_threads": bool(foreshadow_threads),
        "style_guide": bool(style_guide),
    }
    score = sum(1 for value in completion_fields.values() if value)
    return {
        "ready_to_commit": not blocking,
        "blocking": blocking,
        "warnings": warnings,
        "completion": completion_fields,
        "score": score,
        "score_max": len(completion_fields),
    }


def _next_questions(args: dict[str, Any], limit: int = 3) -> list[dict[str, str]]:
    readiness = _readiness_report(args)
    questions: list[dict[str, str]] = []
    for item in readiness["blocking"]:
        field = item["field"]
        if field == "title":
            questions.append({"field": "title", "question": "작품 제목은 무엇인가요?"})
        elif field == "premise":
            questions.append({"field": "premise", "question": "이 장편 연재의 핵심 premise/logline을 한두 문장으로 알려주세요."})
        elif field == "characters":
            questions.append({"field": "characters", "question": "핵심 캐릭터를 id, 이름, 역할, 요약으로 알려주세요."})
        elif field == "relationships":
            questions.append({"field": "relationships", "question": "관계의 from/to 값을 위 캐릭터 id와 정확히 맞춰 다시 알려주세요."})
    if len(questions) < limit and not args.get("relationships") and len(_as_list(args.get("characters"))) >= 2:
        questions.append({"field": "relationships", "question": "핵심 캐릭터 사이의 중요한 관계를 from/to/edge_type/summary로 알려주세요."})
    if len(questions) < limit and not args.get("storylines"):
        questions.append({"field": "storylines", "question": "초기 장기 storyline이나 1권 목표를 알려주세요."})
    if len(questions) < limit and not args.get("foreshadow_threads"):
        questions.append({"field": "foreshadow_threads", "question": "초반에 심을 떡밥과 예상 회수 지점을 알려주세요."})
    if len(questions) < limit and not args.get("style_guide"):
        questions.append({"field": "style_guide", "question": "문체, POV, 분량감, 플랫폼 톤 규칙을 알려주세요."})
    return questions[:limit]


def _preview_graph(args: dict[str, Any]) -> dict[str, Any]:
    shape = _derive_project_shape(args)
    spaces = shape["spaces"]
    bundles = shape["bundles"]
    characters = [_as_dict(c) for c in _as_list(args.get("characters"))]
    relationships = [_as_dict(r) for r in _as_list(args.get("relationships"))]
    character_ids = {
        _character_name(character, index): character
        for index, character in enumerate(characters, start=1)
    }
    base_items = [
        ("series_bible", spaces["series"], "main", "series-bible", "SERIES_BIBLE.md"),
        ("series_synopsis", spaces["series"], "canon-synopsis", "series-synopsis", "CANON_SYNOPSIS.md"),
        ("character_index", spaces["characters"], "index", "character-index", "CHARACTER_INDEX.md"),
        ("relationship_map", spaces["relationships"], "main", "relationship-map", "RELATIONSHIP_MAP.md"),
        ("timeline", spaces["timeline"], "main", "timeline", "TIMELINE.md"),
        ("roadmap", spaces["roadmaps"], "long-arc", "series-roadmap", "ROADMAP.md"),
        ("production_style", spaces["series"], "production-style", "style-guide", "PRODUCTION_STYLE.md"),
        ("current_character_state", spaces["state"], "current-character-state-snapshot", "character-state", "CURRENT_CHARACTER_STATE.md"),
        ("current_relationship_state", spaces["state"], "current-relationship-state-snapshot", "relationship-state", "CURRENT_RELATIONSHIP_STATE.md"),
        ("current_timeline_progress", spaces["progress"], "current-timeline-progress-snapshot", "timeline-progress", "CURRENT_TIMELINE_PROGRESS.md"),
        ("current_storyline_progress", spaces["progress"], "current-storyline-progress-snapshot", "storyline-progress", "CURRENT_STORYLINE_PROGRESS.md"),
        ("current_foreshadow_progress", spaces["progress"], "current-foreshadow-progress-snapshot", "foreshadow-progress", "CURRENT_FORESHADOW_PROGRESS.md"),
        ("canonworks_config", spaces["config"], "canonworks-project-config", "canonworks-config", "canonworks-project-config.yaml"),
    ]
    items = [
        {
            "role": role,
            "space": space,
            "name": name,
            "kind": kind,
            "kref": f"kref://{space}/{name}.{kind}",
            "artifact": artifact,
        }
        for role, space, name, kind, artifact in base_items
    ]
    for index, character in enumerate(characters, start=1):
        char_id = _character_name(character, index)
        items.append({
            "role": f"character:{char_id}",
            "space": spaces["characters"],
            "name": char_id,
            "kind": "character",
            "kref": f"kref://{spaces['characters']}/{char_id}.character",
            "artifact": "CHARACTER.md",
        })

    edges: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    for rel in relationships:
        source_raw = _first(rel.get("from"), rel.get("source"), rel.get("source_id"))
        target_raw = _first(rel.get("to"), rel.get("target"), rel.get("target_id"))
        source = _slugify(source_raw, "")
        target = _slugify(target_raw, "")
        edge_type = _first(rel.get("edge_type"), rel.get("type"), "RELATED_TO").upper().replace("-", "_")
        if source in character_ids and target in character_ids:
            edges.append({"from": source, "to": target, "edge_type": edge_type})
        else:
            warnings.append({
                "type": "relationship_edge_skipped",
                "from": source_raw,
                "to": target_raw,
                "reason": "relationship endpoints must match character ids after slug normalization",
            })

    return {
        "project": shape["project"],
        "story_slug": shape["story_slug"],
        "title": shape["title"],
        "spaces": [{"key": key, "path": path} for key, path in spaces.items()],
        "bundles": [{"key": key, "name": name, "space": spaces["bundles"]} for key, name in bundles.items()],
        "items": items,
        "relationship_edges": edges,
        "warnings": warnings,
    }


def _space_path(project: str, spaces: dict[str, str], key: str) -> str:
    configured = _first(spaces.get(key), SPACE_KEYS[key])
    if configured.startswith(project) or "/" in configured:
        return configured.strip("/")
    return f"{project}/{configured.strip('/')}"


async def _ensure_item(
    sdk: Any,
    space_path: str,
    name: str,
    kind: str,
    metadata: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    existing = await sdk.search_items(context=space_path, name=f"{name}.{kind}", kind=kind, include_metadata=True)
    if not existing:
        existing = await sdk.search_items(context=space_path, name=name, kind=kind, include_metadata=True)
    if existing:
        return existing[0], False
    item = await sdk.create_item(space_path, name, kind, _jsonable_metadata(metadata or {}))
    return item, True


async def _ensure_bundle(
    sdk: Any,
    space_path: str,
    name: str,
    metadata: dict[str, str] | None = None,
) -> tuple[dict[str, Any], bool]:
    existing = await sdk.search_items(context=space_path, name=name, kind="bundle", include_metadata=True)
    if existing:
        return existing[0], False
    bundle = await sdk.create_bundle(space_path, name, metadata or {})
    return bundle, True


async def _create_revision_with_artifact(
    sdk: Any,
    item_kref: str,
    tag: str,
    artifact_dir: Path,
    artifact_name: str,
    content: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / artifact_name
    artifact_path.write_text(content, encoding="utf-8")
    revision = await sdk.create_revision(item_kref, _jsonable_metadata(metadata), tag=tag)
    artifact = await sdk.create_artifact(
        revision["kref"],
        artifact_name,
        artifact_path.as_uri(),
        metadata={"mime": "text/markdown" if artifact_name.endswith(".md") else "application/x-yaml"},
    )
    return {"revision": revision, "artifact": artifact, "path": str(artifact_path)}


def _character_name(character: dict[str, Any], index: int) -> str:
    raw = _first(character.get("id"), character.get("name"), character.get("display_name"), f"character-{index}")
    return _slugify(raw, f"character-{index}")


def _render_series_bible(args: dict[str, Any]) -> str:
    title = _first(args.get("title"), "Untitled Serial")
    premise = _first(args.get("premise"), args.get("logline"), "")
    themes = _as_list(args.get("themes"))
    guardrails = _as_list(args.get("canon_guardrails") or args.get("guardrails"))
    return "\n".join([
        f"# {title}",
        "",
        "## Premise",
        premise or "- Define the core promise of the serial.",
        "",
        "## Themes",
        *(f"- {theme}" for theme in themes),
        "",
        "## Canon Guardrails",
        *(f"- {rule}" for rule in guardrails),
        "",
    ]).strip() + "\n"


def _render_character_index(characters: list[dict[str, Any]]) -> str:
    lines = ["# Character Index", ""]
    if not characters:
        lines.extend(["- Add canonical character profiles before episode generation.", ""])
    for index, character in enumerate(characters, start=1):
        display = _first(character.get("display_name"), character.get("name"), f"Character {index}")
        lines.extend([
            f"## {display}",
            f"- id: {_character_name(character, index)}",
            f"- role: {_first(character.get('role'), 'unknown')}",
            f"- summary: {_first(character.get('summary'), character.get('description'), '')}",
            f"- traits: {', '.join(str(x) for x in _as_list(character.get('traits')))}",
            "",
        ])
    return "\n".join(lines)


def _render_relationship_map(relationships: list[dict[str, Any]]) -> str:
    lines = ["# Relationship Map", ""]
    if not relationships:
        lines.extend(["- Add relationship edges as characters become known.", ""])
    for rel in relationships:
        source = _first(rel.get("from"), rel.get("source"), rel.get("source_id"))
        target = _first(rel.get("to"), rel.get("target"), rel.get("target_id"))
        label = _first(rel.get("label"), rel.get("relationship"), rel.get("type"), "RELATED_TO")
        lines.append(f"- {source} --{label}--> {target}: {_first(rel.get('summary'), rel.get('notes'))}")
    return "\n".join(lines).strip() + "\n"


def _render_timeline(events: list[dict[str, Any]]) -> str:
    lines = ["# Timeline", ""]
    if not events:
        lines.extend(["- Add canonical timeline events and episode anchors.", ""])
    for event in events:
        when = _first(event.get("time"), event.get("date"), event.get("position"), "unplaced")
        lines.append(f"- {when}: {_first(event.get('summary'), event.get('event'), event.get('title'))}")
    return "\n".join(lines).strip() + "\n"


def _render_roadmap(storylines: list[dict[str, Any]], foreshadow_threads: list[dict[str, Any]]) -> str:
    lines = ["# Long-Arc Roadmap", "", "## Storylines"]
    if not storylines:
        lines.append("- Define active storylines before production.")
    for storyline in storylines:
        lines.append(f"- {_first(storyline.get('id'), storyline.get('title'), 'storyline')}: {_first(storyline.get('summary'), storyline.get('goal'))}")
    lines.extend(["", "## Foreshadow Threads"])
    if not foreshadow_threads:
        lines.append("- Define foreshadow threads and payoff targets.")
    for thread in foreshadow_threads:
        lines.append(f"- {_first(thread.get('id'), thread.get('title'), 'thread')}: {_first(thread.get('summary'), thread.get('payoff_target'))}")
    return "\n".join(lines).strip() + "\n"


def _build_config(
    args: dict[str, Any],
    project: str,
    story_slug: str,
    spaces: dict[str, str],
    bundles: dict[str, str],
    actual_krefs: dict[str, str] | None = None,
) -> dict[str, Any]:
    krefs = {
        "series_bible": f"kref://{spaces['series']}/main.series-bible",
        "series_synopsis": f"kref://{spaces['series']}/canon-synopsis.series-synopsis",
        "character_index": f"kref://{spaces['characters']}/index.character-index",
        "relationship_map": f"kref://{spaces['relationships']}/main.relationship-map",
        "relationship_map_artifact": f"kref://{spaces['relationships']}/main.relationship-map?r=1&a=RELATIONSHIP_MAP.md",
        "timeline": f"kref://{spaces['timeline']}/main.timeline",
        "roadmap": f"kref://{spaces['roadmaps']}/long-arc.series-roadmap",
    }
    krefs.update({key: value for key, value in (actual_krefs or {}).items() if value})
    return {
        "canon_project": {
            "id": story_slug,
            "project": project,
            "title": _first(args.get("title"), "Untitled Serial"),
            "language": _first(args.get("language"), "ko-KR"),
            "cadence": _first(args.get("cadence"), "web_serial"),
            "default_episode_length_chars": _first(args.get("default_episode_length_chars"), args.get("target_length"), "6000"),
            "genre_modules": _as_list(args.get("genre_modules")),
            "spaces": spaces,
            "bundles": bundles,
            "naming": {
                "episode_name_prefix": _first(args.get("episode_name_prefix"), "ep"),
                "episode_id_prefix": _first(args.get("episode_id_prefix"), "ep"),
                "volume_bundle_prefix": _first(args.get("volume_bundle_prefix"), f"{story_slug}-vol"),
                "context_pack_suffix": "context",
                "patch_name_suffix": "canon-patch",
                "blocked_name_suffix": "blocked",
            },
            "krefs": krefs,
            "agent_personas": _as_dict(args.get("agent_personas")),
            "priority_rules": _as_list(args.get("priority_rules") or [
                "canon_integrity_over_hook",
                "reveal_locks_over_dramatic_convenience",
                "relationship_stage_over_romance_payoff",
            ]),
            "audit_rules": _as_list(args.get("audit_rules") or [
                "main canon and current snapshots are highest authority",
                "canon patch candidates are propose-only unless explicitly approved",
                "relationship and major timeline moves require human approval by default",
            ]),
            "external_reference_seed": _first(
                args.get("external_reference_seed"),
                args.get("reference_seed"),
                "serialized fiction continuity references for this project",
            ),
        }
    }


async def tool_canonworks_init(args: dict[str, Any], sdk: Any) -> dict[str, Any]:
    """Create a CanonWorks Kumiho project scaffold and initial canon graph."""
    if hasattr(sdk, "_lazy_init"):
        sdk._lazy_init()
    if not getattr(sdk, "_available", True):
        return {"success": False, "error": "Kumiho SDK unavailable", "created": {}}

    title = _first(args.get("title"), args.get("series_title"))
    if not title:
        return {"success": False, "error": "title is required", "created": {}}

    story_slug = _first(args.get("story_slug"), args.get("slug"), args.get("id"), _slugify(title))
    project = _first(args.get("project"), args.get("project_id"), args.get("kumiho_project"), _slugify(title, "CanonWorksProject"))
    artifact_root = _workspace_root(args, story_slug)
    spaces_config = _as_dict(args.get("spaces"))
    spaces = {key: _space_path(project, spaces_config, key) for key in SPACE_KEYS}
    bundles = {
        key: _first(_as_dict(args.get("bundles")).get(key), f"{story_slug}-{suffix}")
        for key, suffix in CORE_BUNDLES.items()
    }

    created: dict[str, list[dict[str, Any]]] = {
        "spaces": [],
        "bundles": [],
        "items": [],
        "revisions": [],
        "artifacts": [],
        "bundle_members": [],
        "edges": [],
        "warnings": [],
    }

    for path in spaces.values():
        await sdk.ensure_space_path(path)
        created["spaces"].append({"path": path})

    bundle_krefs: dict[str, str] = {}
    for key, name in bundles.items():
        bundle, is_new = await _ensure_bundle(
            sdk,
            spaces["bundles"],
            name,
            metadata={"canonworks": "true", "bundle_key": key, "story_slug": story_slug},
        )
        bundle_krefs[key] = str(bundle.get("kref") or "")
        created["bundles"].append({"key": key, "name": name, "kref": bundle_krefs[key], "created": is_new})

    def record_created_item(item: dict[str, Any], created_new: bool, role: str) -> None:
        created["items"].append({
            "role": role,
            "kref": item.get("kref", ""),
            "name": item.get("name") or item.get("item_name", ""),
            "created": created_new,
        })

    async def create_doc(space_key: str, name: str, kind: str, role: str, artifact_name: str, content: str, metadata: dict[str, Any], tag: str = "current") -> dict[str, Any]:
        item, is_new = await _ensure_item(sdk, spaces[space_key], name, kind, {**metadata, "canonworks_role": role})
        record_created_item(item, is_new, role)
        revision_artifact = await _create_revision_with_artifact(
            sdk,
            item["kref"],
            tag,
            artifact_root / _path_segment(role),
            artifact_name,
            content,
            {**metadata, "canonworks": "true", "canonworks_role": role, "story_slug": story_slug, "project_id": project},
        )
        created["revisions"].append({"role": role, "kref": revision_artifact["revision"].get("kref", ""), "tag": tag})
        created["artifacts"].append({"role": role, "kref": revision_artifact["artifact"].get("kref", ""), "path": revision_artifact["path"]})
        return {"item": item, **revision_artifact}

    characters = [_as_dict(c) for c in _as_list(args.get("characters"))]
    relationships = [_as_dict(r) for r in _as_list(args.get("relationships"))]
    timeline_events = [_as_dict(e) for e in _as_list(args.get("timeline_events"))]
    storylines = [_as_dict(s) for s in _as_list(args.get("storylines"))]
    foreshadow_threads = [_as_dict(f) for f in _as_list(args.get("foreshadow_threads"))]

    series_bible = await create_doc(
        "series", "main", "series-bible", "series_bible", "SERIES_BIBLE.md",
        _render_series_bible(args),
        {"title": title, "premise": _first(args.get("premise"), args.get("logline"))},
    )
    synopsis = await create_doc(
        "series", "canon-synopsis", "series-synopsis", "series_synopsis", "CANON_SYNOPSIS.md",
        f"# Canon Synopsis\n\n{_first(args.get('synopsis'), args.get('premise'), 'Add the canonical synopsis.')}\n",
        {"title": title},
    )
    character_index = await create_doc(
        "characters", "index", "character-index", "character_index", "CHARACTER_INDEX.md",
        _render_character_index(characters),
        {"character_count": len(characters)},
    )
    relationship_map = await create_doc(
        "relationships", "main", "relationship-map", "relationship_map", "RELATIONSHIP_MAP.md",
        _render_relationship_map(relationships),
        {"relationship_count": len(relationships)},
    )
    timeline = await create_doc(
        "timeline", "main", "timeline", "timeline", "TIMELINE.md",
        _render_timeline(timeline_events),
        {"event_count": len(timeline_events)},
    )
    roadmap = await create_doc(
        "roadmaps", "long-arc", "series-roadmap", "roadmap", "ROADMAP.md",
        _render_roadmap(storylines, foreshadow_threads),
        {"storyline_count": len(storylines), "foreshadow_count": len(foreshadow_threads)},
    )
    style_guide = await create_doc(
        "series", "production-style", "style-guide", "production_style", "PRODUCTION_STYLE.md",
        f"# Production Style\n\n{_first(args.get('style_guide'), 'Define prose, POV, pacing, and platform rules.')}\n",
        {"title": title},
    )

    character_revisions: dict[str, str] = {}
    for index, character in enumerate(characters, start=1):
        char_id = _character_name(character, index)
        display = _first(character.get("display_name"), character.get("name"), char_id)
        content = "\n".join([
            f"# {display}",
            "",
            f"- id: {char_id}",
            f"- role: {_first(character.get('role'), 'unknown')}",
            f"- summary: {_first(character.get('summary'), character.get('description'))}",
            f"- traits: {', '.join(str(x) for x in _as_list(character.get('traits')))}",
            "",
        ])
        doc = await create_doc(
            "characters", char_id, "character", f"character:{char_id}", "CHARACTER.md",
            content,
            {"character_id": char_id, "display_name": display, "role": _first(character.get("role"), "unknown")},
        )
        character_revisions[char_id] = doc["revision"]["kref"]

    current_docs = [
        await create_doc("state", "current-character-state-snapshot", "character-state", "current_character_state", "CURRENT_CHARACTER_STATE.md", "# Current Character State\n\nSeeded by CanonWorks.\n", {}),
        await create_doc("state", "current-relationship-state-snapshot", "relationship-state", "current_relationship_state", "CURRENT_RELATIONSHIP_STATE.md", "# Current Relationship State\n\nSeeded by CanonWorks.\n", {}),
        await create_doc("progress", "current-timeline-progress-snapshot", "timeline-progress", "current_timeline_progress", "CURRENT_TIMELINE_PROGRESS.md", "# Current Timeline Progress\n\nSeeded by CanonWorks.\n", {}),
        await create_doc("progress", "current-storyline-progress-snapshot", "storyline-progress", "current_storyline_progress", "CURRENT_STORYLINE_PROGRESS.md", "# Current Storyline Progress\n\nSeeded by CanonWorks.\n", {}),
        await create_doc("progress", "current-foreshadow-progress-snapshot", "foreshadow-progress", "current_foreshadow_progress", "CURRENT_FORESHADOW_PROGRESS.md", "# Current Foreshadow Progress\n\nSeeded by CanonWorks.\n", {}),
    ]

    config = _build_config(
        args,
        project,
        story_slug,
        spaces,
        bundles,
        actual_krefs={
            "series_bible": str(series_bible["item"].get("kref", "")),
            "series_synopsis": str(synopsis["item"].get("kref", "")),
            "character_index": str(character_index["item"].get("kref", "")),
            "relationship_map": str(relationship_map["item"].get("kref", "")),
            "relationship_map_artifact": str(relationship_map["artifact"].get("kref", "")),
            "timeline": str(timeline["item"].get("kref", "")),
            "roadmap": str(roadmap["item"].get("kref", "")),
        },
    )
    config_yaml = _yaml_dump(config)
    config_doc = await create_doc(
        "config",
        "canonworks-project-config",
        "canonworks-config",
        "canonworks_config",
        "canonworks-project-config.yaml",
        config_yaml,
        {"title": title, "config_kind": "canonworks-project-config"},
        tag="published",
    )

    main_members = [
        series_bible["item"]["kref"],
        synopsis["item"]["kref"],
        character_index["item"]["kref"],
        relationship_map["item"]["kref"],
        timeline["item"]["kref"],
        roadmap["item"]["kref"],
    ]
    for item_kref in main_members:
        if await sdk.add_bundle_member(bundle_krefs["main_canon"], item_kref):
            created["bundle_members"].append({"bundle": bundles["main_canon"], "item_kref": item_kref})
    if await sdk.add_bundle_member(bundle_krefs["production_style"], style_guide["item"]["kref"]):
        created["bundle_members"].append({"bundle": bundles["production_style"], "item_kref": style_guide["item"]["kref"]})
    state_bundle_pairs = [
        ("current_character_states", current_docs[0]),
        ("current_relationship_states", current_docs[1]),
        ("current_timeline_progress", current_docs[2]),
        ("current_storyline_progress", current_docs[3]),
        ("current_foreshadow_progress", current_docs[4]),
    ]
    for bundle_key, doc in state_bundle_pairs:
        if await sdk.add_bundle_member(bundle_krefs[bundle_key], doc["item"]["kref"]):
            created["bundle_members"].append({"bundle": bundles[bundle_key], "item_kref": doc["item"]["kref"]})
        if await sdk.add_bundle_member(bundle_krefs["state_sync_snapshots"], doc["item"]["kref"]):
            created["bundle_members"].append({"bundle": bundles["state_sync_snapshots"], "item_kref": doc["item"]["kref"]})

    for rel in relationships:
        source_raw = _first(rel.get("from"), rel.get("source"), rel.get("source_id"))
        target_raw = _first(rel.get("to"), rel.get("target"), rel.get("target_id"))
        source = _slugify(source_raw, "")
        target = _slugify(target_raw, "")
        if source in character_revisions and target in character_revisions:
            edge_type = _first(rel.get("edge_type"), rel.get("type"), "RELATED_TO").upper().replace("-", "_")
            metadata = {
                "relationship": _first(rel.get("label"), rel.get("relationship"), edge_type),
                "summary": _first(rel.get("summary"), rel.get("notes")),
                "canonworks": "true",
            }
            await sdk.create_edge(character_revisions[source], character_revisions[target], edge_type, metadata)
            created["edges"].append({"from": source, "to": target, "edge_type": edge_type})
        else:
            created["warnings"].append({
                "type": "relationship_edge_skipped",
                "from": source_raw,
                "to": target_raw,
                "reason": "relationship endpoints must match character ids after slug normalization",
            })

    return {
        "success": True,
        "project": project,
        "story_slug": story_slug,
        "title": title,
        "project_config_yaml": config_yaml,
        "project_config_item_kref": config_doc["item"]["kref"],
        "project_config_revision_kref": config_doc["revision"]["kref"],
        "project_config_artifact_path": config_doc["path"],
        "created": created,
        "next_workflows": [
            "canonworks-serial-episode-factory",
            "canonworks-serial-canon-state-sync",
        ],
    }


async def tool_canonworks_start(args: dict[str, Any], sdk: Any | None = None) -> dict[str, Any]:
    """Start or continue an operator-friendly CanonWorks setup interview."""
    try:
        session = _load_session(args)
    except CanonWorksStateError as exc:
        return _state_error_response(exc)
    now = _utc_now()
    if not session:
        session = {
            "session_id": _first(args.get("session_id"), str(uuid.uuid4())),
            "status": "draft",
            "created_at": now,
            "updated_at": now,
            "draft": {},
        }
    draft = _merge_seed(_as_dict(session.get("draft")), _seed_from_args(args))
    session["draft"] = draft
    session["updated_at"] = now
    readiness = _readiness_report(draft)
    session["readiness"] = readiness
    if not bool(args.get("defer_kumiho_scaffold")):
        scaffold = await _ensure_project_scaffold(draft, sdk)
        session["project_scaffold"] = scaffold
    else:
        scaffold = {"status": "deferred", "created": False}
        session["project_scaffold"] = scaffold
    _save_session(args, session)
    if scaffold.get("status") == "failed":
        return {
            "success": False,
            "error": scaffold.get("error", "CanonWorks project scaffold failed"),
            "session_id": session["session_id"],
            "status": session["status"],
            "draft": draft,
            "readiness": readiness,
            "project_scaffold": scaffold,
            "next_questions": _next_questions(draft),
            "preview": _preview_graph(draft),
        }
    return {
        "success": True,
        "session_id": session["session_id"],
        "status": session["status"],
        "draft": draft,
        "readiness": readiness,
        "project_scaffold": scaffold,
        "next_questions": _next_questions(draft),
        "preview": _preview_graph(draft),
        "next_actions": [
            "Choose or confirm a Kumiho project name",
            "Answer next_questions with canonworks_start",
            "Call canonworks_preview before committing",
            "Call canonworks_commit when readiness.ready_to_commit is true",
        ],
    }


async def tool_canonworks_preview(args: dict[str, Any]) -> dict[str, Any]:
    """Preview the Kumiho graph CanonWorks would create without mutating Kumiho."""
    try:
        session = _load_session(args)
    except CanonWorksStateError as exc:
        return _state_error_response(exc)
    draft = _merge_seed(_as_dict(session.get("draft")), _seed_from_args(args))
    readiness = _readiness_report(draft)
    return {
        "success": True,
        "session_id": session.get("session_id") or args.get("session_id", ""),
        "draft": draft,
        "readiness": readiness,
        "next_questions": _next_questions(draft),
        "preview": _preview_graph(draft),
    }


async def tool_canonworks_commit(args: dict[str, Any], sdk: Any) -> dict[str, Any]:
    """Commit an interviewed CanonWorks draft into Kumiho via canonworks_init."""
    try:
        session = _load_session(args)
    except CanonWorksStateError as exc:
        return _state_error_response(exc)
    draft = _merge_seed(_as_dict(session.get("draft")), _seed_from_args(args))
    readiness = _readiness_report(draft)
    if not readiness["ready_to_commit"] and not bool(args.get("allow_incomplete")):
        return {
            "success": False,
            "error": "CanonWorks draft is not ready to commit",
            "session_id": session.get("session_id") or args.get("session_id", ""),
            "readiness": readiness,
            "next_questions": _next_questions(draft),
            "preview": _preview_graph(draft),
        }

    result = await tool_canonworks_init(draft, sdk)
    if not result.get("success"):
        return {**result, "session_id": session.get("session_id") or args.get("session_id", ""), "readiness": readiness}

    session_id = _first(session.get("session_id"), args.get("session_id"), str(uuid.uuid4()))
    session = {
        **session,
        "session_id": session_id,
        "status": "committed",
        "updated_at": _utc_now(),
        "draft": draft,
        "readiness": readiness,
        "commit": {
            "project": result.get("project", ""),
            "story_slug": result.get("story_slug", ""),
            "title": result.get("title", ""),
            "project_config_item_kref": result.get("project_config_item_kref", ""),
            "project_config_revision_kref": result.get("project_config_revision_kref", ""),
            "project_config_artifact_path": result.get("project_config_artifact_path", ""),
        },
    }
    session.setdefault("created_at", _utc_now())
    _save_session(args, session)

    project_state = {
        "session_id": session_id,
        "updated_at": session["updated_at"],
        "project": result.get("project", ""),
        "story_slug": result.get("story_slug", ""),
        "title": result.get("title", ""),
        "project_config_artifact_path": result.get("project_config_artifact_path", ""),
        "project_config_item_kref": result.get("project_config_item_kref", ""),
        "project_config_revision_kref": result.get("project_config_revision_kref", ""),
        "draft": draft,
    }
    _write_json(_project_state_path(args, str(result.get("project", "")), str(result.get("story_slug", ""))), project_state)
    return {
        **result,
        "session_id": session_id,
        "readiness": readiness,
        "project_state_path": str(_project_state_path(args, str(result.get("project", "")), str(result.get("story_slug", "")))),
        "next_actions": [
            "Call canonworks_run_episode to produce the next episode",
            "Call canonworks_sync_state after a production-ready episode exists",
        ],
    }


def _load_project_state(args: dict[str, Any]) -> dict[str, Any]:
    config_path = _first(args.get("project_config_artifact_path"), args.get("project_config_yaml"))
    if config_path:
        return {"project_config_artifact_path": config_path}
    session = _load_session(args)
    commit = _as_dict(session.get("commit"))
    if commit.get("project_config_artifact_path"):
        return {**commit, "session_id": session.get("session_id", "")}
    project = _first(args.get("project"), args.get("project_id"), args.get("kumiho_project"))
    story_slug = _first(args.get("story_slug"), args.get("slug"), args.get("id"))
    if project and story_slug:
        return _read_json(_project_state_path(args, project, story_slug))
    return {}


def _workflow_cwd(args: dict[str, Any]) -> str:
    return _first(args.get("cwd"), str(Path.home()))


def _copy_present(args: dict[str, Any], names: list[str]) -> dict[str, Any]:
    return {name: args[name] for name in names if name in args and args[name] is not None}


async def tool_canonworks_run_episode(args: dict[str, Any]) -> dict[str, Any]:
    """Run the CanonWorks episode factory without exposing config-path plumbing to the operator."""
    try:
        state = _load_project_state(args)
    except CanonWorksStateError as exc:
        return _state_error_response(exc)
    config_path = _first(state.get("project_config_artifact_path"))
    if not config_path:
        return {
            "success": False,
            "error": "project_config_artifact_path not found; call canonworks_commit first or pass project/session_id",
        }
    from .workflows import tool_run_workflow

    inputs = {
        "project_config_yaml": config_path,
        **_copy_present(args, [
            "target_length",
            "episode_goal",
            "must_include",
            "avoid",
            "continuity_context",
            "pacing_mode",
            "opencrab_query",
            "initial_episode_number",
            "initial_volume",
            "include_relationship_layers",
            "min_relationship_strength",
        ]),
    }
    result = await tool_run_workflow({
        "workflow": "canonworks-serial-episode-factory",
        "cwd": _workflow_cwd(args),
        "inputs": inputs,
        **_copy_present(args, ["run_id", "max_cost_usd"]),
    })
    return {**result, "success": "error" not in result, "project_config_artifact_path": config_path, "inputs": inputs}


async def tool_canonworks_sync_state(args: dict[str, Any]) -> dict[str, Any]:
    """Run the CanonWorks post-episode state sync using the stored project config."""
    try:
        state = _load_project_state(args)
    except CanonWorksStateError as exc:
        return _state_error_response(exc)
    config_path = _first(state.get("project_config_artifact_path"))
    if not config_path:
        return {
            "success": False,
            "error": "project_config_artifact_path not found; call canonworks_commit first or pass project/session_id",
        }
    from .workflows import tool_run_workflow

    inputs = {
        "project_config_yaml": config_path,
        **_copy_present(args, [
            "target_episode_number",
            "target_episode_kref",
            "target_patch_kref",
            "bootstrap_mode",
            "apply_mode",
            "continuity_context",
            "review_focus",
        ]),
    }
    result = await tool_run_workflow({
        "workflow": "canonworks-serial-canon-state-sync",
        "cwd": _workflow_cwd(args),
        "inputs": inputs,
        **_copy_present(args, ["run_id", "max_cost_usd"]),
    })
    return {**result, "success": "error" not in result, "project_config_artifact_path": config_path, "inputs": inputs}
