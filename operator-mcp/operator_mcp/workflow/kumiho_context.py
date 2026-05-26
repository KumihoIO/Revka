"""Kumiho-native context compiler for workflow kumiho_context steps."""
from __future__ import annotations

import os
from copy import deepcopy
from datetime import datetime
from typing import Any

from .._log import _log


DEFAULT_EDGE_TYPES = [
    "DEPENDS_ON",
    "REFERENCES",
    "ADVANCES",
    "UPDATES",
    "CONTRADICTS",
]
DEFAULT_TAG_PREFERENCE = [
    "current",
    "active",
    "production-ready",
    "ready",
    "published",
    "latest",
]
CRITICAL_EDGE_TYPES = {"CONTRADICTS", "BLOCKS"}
CRITICAL_KINDS = {"canon-rule", "character-state", "storyline"}
MULTI_REVISION_KINDS = {"timeline-event", "webnovel-episode", "canon-patch"}
MAX_BUNDLE_MEMBERS = 200
MAX_TRAVERSED_REVISIONS = 300

SECTION_BY_KIND = {
    "series-bible": "series_bible",
    "canon-rule": "hard_rules",
    "character": "character_bibles",
    "character-state": "active_character_states",
    "relationship-map": "relationship_constraints",
    "timeline": "timeline_window",
    "timeline-event": "timeline_window",
    "storyline": "active_storylines",
    "plot-thread": "active_storylines",
    "foreshadow-thread": "active_foreshadow_threads",
    "volume-plan": "recent_arc_context",
    "arc-blueprint": "recent_arc_context",
    "webnovel-episode": "recent_episode_context",
    "canon-patch": "pending_or_recent_patches",
    "canon-audit": "audit_constraints",
    "continuity-audit": "audit_constraints",
    "pacing-audit": "audit_constraints",
}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [value]


def _as_str_list(value: Any) -> list[str]:
    return [str(v).strip() for v in _as_list(value) if str(v).strip()]


def _is_kref(value: str) -> bool:
    return value.startswith("kref://")


def _is_revision_kref(value: str) -> bool:
    return _is_kref(value) and "?r=" in value


def _item_kref_from_revision_kref(value: str) -> str:
    return value.split("#", 1)[0].split("?", 1)[0]


def _revision_sort_key(rev: dict[str, Any]) -> tuple[str, int, str]:
    created = str(rev.get("created_at") or "")
    number_raw = rev.get("number", 0)
    try:
        number = int(number_raw)
    except (TypeError, ValueError):
        number = 0
    return created, number, str(rev.get("kref") or "")


def _tags_from_revision(rev: dict[str, Any]) -> set[str]:
    tags = rev.get("tags")
    if isinstance(tags, (list, tuple, set)):
        return {str(tag) for tag in tags if str(tag)}
    tag = rev.get("tag")
    return {str(tag)} if tag else set()


def _tags_from_item(item: dict[str, Any]) -> set[str]:
    meta = item.get("metadata", {}) if isinstance(item.get("metadata"), dict) else {}
    raw = meta.get("tags") or meta.get("tag")
    if isinstance(raw, str):
        return {p.strip() for p in raw.replace(";", ",").split(",") if p.strip()}
    if isinstance(raw, (list, tuple, set)):
        return {str(p).strip() for p in raw if str(p).strip()}
    return set()


def _deprecated(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _name(item: dict[str, Any]) -> str:
    return str(item.get("item_name") or item.get("name") or "")


def _base_name(item: dict[str, Any]) -> str:
    name = _name(item)
    kind = str(item.get("kind") or "")
    suffix = f".{kind}"
    if kind and name.endswith(suffix):
        return name[: -len(suffix)]
    return name


def _space_matches(item_kref: str, spaces: list[str]) -> bool:
    if not spaces:
        return True
    normalized = item_kref.strip("/")
    for space in spaces:
        s = space.strip("/")
        if not s:
            continue
        if normalized.startswith(f"kref://{s}") or f"/{s}/" in normalized:
            return True
    return False


def _edge_direction_code(direction: str) -> int:
    return {"both": 0, "out": 1, "in": 2}.get(direction, 0)


def _edge_neighbor(edge: dict[str, Any], current: str) -> str:
    source = str(edge.get("source_kref") or "")
    target = str(edge.get("target_kref") or "")
    if source == current:
        return target
    if target == current:
        return source
    current_item = _item_kref_from_revision_kref(current)
    if _item_kref_from_revision_kref(source) == current_item:
        return target
    if _item_kref_from_revision_kref(target) == current_item:
        return source
    return ""


def _parse_dt(value: Any) -> float:
    if not value:
        return 0.0
    try:
        text = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return 0.0


def _short_text(value: str, cap: int) -> str:
    if cap <= 0:
        return ""
    text = value.strip()
    if len(text) <= cap:
        return text
    return text[:cap].rstrip() + "\n...[truncated]"


class KumihoContextCompiler:
    """Compile a locked, structured context pack from Kumiho graph data."""

    def __init__(self, sdk: Any, config: dict[str, Any], *, workflow: str, step_id: str) -> None:
        self.sdk = sdk
        self.cfg = deepcopy(config)
        self.workflow = workflow
        self.step_id = step_id
        self.item_cache: dict[str, dict[str, Any]] = {}
        self.revision_cache: dict[str, dict[str, Any]] = {}
        self.candidates: dict[str, dict[str, Any]] = {}
        self.edge_map: list[dict[str, Any]] = []
        self.missing_context: list[dict[str, Any]] = []
        self.conflict_warnings: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []
        self.stats: dict[str, Any] = {
            "seed_bundle_count": 0,
            "seed_kref_count": 0,
            "seed_item_count": 0,
            "seed_query_count": 0,
            "traversed_revision_count": 0,
            "selected_item_count": 0,
            "locked_revision_count": 0,
            "truncated": False,
        }

    @property
    def project(self) -> str:
        return str(self.cfg.get("project") or "").strip()

    @property
    def mode(self) -> str:
        return str(self.cfg.get("mode") or "graph_augmented_context")

    async def compile(self) -> dict[str, Any]:
        if not self.project:
            return self._empty_result(
                {"type": "missing_project", "message": "kumiho.project is required"}
            )

        await self._resolve_seed_bundles()
        if self.errors:
            return self._empty_result(self.errors[0])

        await self._resolve_seed_krefs()
        await self._resolve_seed_items()

        if self.mode in {"graph_augmented_context", "semantic_context"}:
            await self._run_semantic_search()

        if self.mode in {"graph_augmented_context", "bundle_context"}:
            await self._traverse_graph()

        filtered = self._filter_candidates()
        ranked = self._rank_candidates(filtered)
        selected = self._select_candidates(ranked)
        summaries = await self._load_artifact_summaries(selected)
        await self._detect_conflicts(selected)

        context_pack = self._build_context_pack(selected, summaries)
        artifact_content = self._render_context_pack(context_pack)
        source_krefs = [m["revision_kref"] for m in context_pack["locked_manifest"]]
        return {
            "found": bool(context_pack["locked_manifest"]),
            "context_pack": context_pack,
            "artifact_content": artifact_content,
            "source_krefs": source_krefs,
            "locked_manifest": context_pack["locked_manifest"],
            "edge_map": context_pack["edge_map"],
            "conflict_warnings": context_pack["conflict_warnings"],
            "missing_context": context_pack["missing_context"],
            "stats": context_pack["stats"],
        }

    def _empty_result(self, error: dict[str, Any]) -> dict[str, Any]:
        context_pack = {
            "name": f"{self.project or 'kumiho'}-{self.step_id}-context-pack",
            "project": self.project,
            "mode": self.mode,
            "task": {"workflow": self.workflow, "step_id": self.step_id},
            "locked_manifest": [],
            "source_krefs": [],
            "relevant_items": [],
            "relevant_canon": {},
            "edge_map": [],
            "conflict_warnings": [],
            "missing_context": [error],
            "assumptions": [],
            "stats": self.stats,
        }
        return {
            "found": False,
            "error": error,
            "context_pack": context_pack,
            "artifact_content": self._render_context_pack(context_pack),
            "source_krefs": [],
            "locked_manifest": [],
            "edge_map": [],
            "conflict_warnings": [],
            "missing_context": context_pack["missing_context"],
            "stats": self.stats,
        }

    async def _resolve_seed_bundles(self) -> None:
        seed = self.cfg.get("seed", {}) or {}
        bundles = _as_list(seed.get("bundles"))
        self.stats["seed_bundle_count"] = len(bundles)
        for bundle_ref in bundles:
            bundle_name, optional = self._bundle_ref_parts(bundle_ref)
            if not bundle_name:
                continue
            try:
                bundle = await self._find_bundle(bundle_name)
            except Exception as exc:
                bundle = None
                _log(f"kumiho_context: bundle lookup failed for {bundle_name}: {exc}")
            if not bundle:
                entry = {
                    "type": "missing_bundle",
                    "bundle": bundle_name,
                    "message": f"Bundle {bundle_name} not found",
                }
                if optional:
                    self.missing_context.append(entry)
                    continue
                self.errors.append(entry)
                return
            members = await self.sdk.get_bundle_members(bundle.get("kref", ""))
            if len(members) > MAX_BUNDLE_MEMBERS:
                self.stats["truncated"] = True
                self.stats["truncation_reason"] = "max bundle members exceeded"
                members = members[:MAX_BUNDLE_MEMBERS]
            for member in members:
                item_kref = (
                    member.get("item_kref")
                    or member.get("kref")
                    or member.get("member_kref")
                    or ""
                )
                if not item_kref:
                    continue
                item = await self._get_item(item_kref, fallback=member)
                rev, selected_by = await self._select_revision_for_item(item, None)
                if rev:
                    self._add_candidate(
                        rev,
                        item,
                        reason=f"bundle:{bundle_name}",
                        selected_by=selected_by,
                        depth=0,
                    )
                else:
                    self.missing_context.append({
                        "kind": item.get("kind", ""),
                        "name": _name(item),
                        "item_kref": item_kref,
                        "reason": "No revision matched lock.tag_preference",
                        "severity": "medium",
                    })

    def _bundle_ref_parts(self, bundle_ref: Any) -> tuple[str, bool]:
        if isinstance(bundle_ref, dict):
            name = str(bundle_ref.get("kref") or bundle_ref.get("name") or "").strip()
            return name, bool(bundle_ref.get("optional", False))
        return str(bundle_ref).strip(), False

    async def _find_bundle(self, bundle_ref: str) -> dict[str, Any] | None:
        if _is_kref(bundle_ref):
            return await self.sdk.get_bundle_by_kref(bundle_ref)

        async def _search(kind: str) -> list[dict[str, Any]]:
            if hasattr(self.sdk, "search_items"):
                return await self.sdk.search_items(
                    context=self.project,
                    name=bundle_ref,
                    kind=kind,
                    include_metadata=True,
                )
            return []

        found = await _search("bundle")
        if not found:
            found = await _search("")
        if not found:
            return None
        for item in found:
            if bundle_ref in {_name(item), _base_name(item)}:
                return item
        return found[0]

    async def _resolve_seed_krefs(self) -> None:
        seed = self.cfg.get("seed", {}) or {}
        krefs = _as_str_list(seed.get("krefs"))
        self.stats["seed_kref_count"] = len(krefs)
        for kref in krefs:
            if not kref:
                continue
            if _is_revision_kref(kref):
                rev = await self._get_revision(kref)
                if not rev:
                    self.missing_context.append({
                        "kref": kref,
                        "reason": "Revision kref could not be resolved",
                        "severity": "medium",
                    })
                    continue
                item = await self._get_item(rev.get("item_kref") or _item_kref_from_revision_kref(kref))
                self._add_candidate(
                    rev,
                    item,
                    reason="seed:kref",
                    selected_by="input_revision",
                    depth=0,
                )
            else:
                item = await self._get_item(kref)
                rev, selected_by = await self._select_revision_for_item(item, None)
                if rev:
                    self._add_candidate(
                        rev,
                        item,
                        reason="seed:kref",
                        selected_by=selected_by,
                        depth=0,
                    )
                else:
                    self.missing_context.append({
                        "item_kref": kref,
                        "reason": "No revision matched lock.tag_preference",
                        "severity": "medium",
                    })

    async def _resolve_seed_items(self) -> None:
        seed = self.cfg.get("seed", {}) or {}
        items = _as_list(seed.get("items"))
        self.stats["seed_item_count"] = len(items)
        for spec in items:
            if not isinstance(spec, dict):
                continue
            kind = str(spec.get("kind") or "")
            name_pattern = str(spec.get("name_pattern") or "")
            tag = str(spec.get("tag") or "")
            optional = bool(spec.get("optional", False))
            mode = str(spec.get("mode") or "latest")
            matches = await self._search_items(kind=kind, name=name_pattern)
            if not matches:
                entry = {
                    "kind": kind,
                    "name": name_pattern,
                    "reason": "No item matched seed item search",
                    "severity": "medium",
                }
                if optional:
                    self.missing_context.append(entry)
                    continue
                self.missing_context.append(entry)
                continue
            chosen = matches if mode == "all" else [matches[-1]]
            for item in chosen:
                rev, selected_by = await self._select_revision_for_item(item, tag or None)
                if rev:
                    self._add_candidate(
                        rev,
                        item,
                        reason="seed:item",
                        selected_by=selected_by,
                        depth=0,
                    )
                elif not optional:
                    self.missing_context.append({
                        "kind": item.get("kind", kind),
                        "name": _name(item) or name_pattern,
                        "reason": f"No revision matched tag {tag!r}" if tag else "No lockable revision",
                        "severity": "medium",
                    })

    async def _search_items(self, *, kind: str = "", name: str = "") -> list[dict[str, Any]]:
        if hasattr(self.sdk, "search_items"):
            return await self.sdk.search_items(
                context=self.project,
                name=name,
                kind=kind,
                include_metadata=True,
            )
        return []

    async def _run_semantic_search(self) -> None:
        seed = self.cfg.get("seed", {}) or {}
        ranking = self.cfg.get("ranking", {}) or {}
        queries = _as_str_list(seed.get("queries"))
        semantic_query = str(ranking.get("semantic_query") or "").strip()
        if semantic_query:
            queries.append(semantic_query)
        queries = list(dict.fromkeys(q for q in queries if q.strip()))
        self.stats["seed_query_count"] = len(queries)
        for query in queries:
            try:
                results = await self.sdk.search(
                    query,
                    context=self.project,
                    kind="",
                    include_revision_metadata=True,
                )
            except TypeError:
                results = await self.sdk.search(query, context=self.project, kind="")
            except Exception as exc:
                _log(f"kumiho_context: semantic search failed for query={query!r}: {exc}")
                continue
            for result in results:
                item = result.get("item", result)
                if not isinstance(item, dict):
                    continue
                rev, selected_by = await self._select_revision_for_item(item, None)
                if not rev:
                    continue
                score = float(result.get("score") or 0.0)
                self._add_candidate(
                    rev,
                    item,
                    reason=f"semantic:{query}",
                    selected_by=selected_by,
                    depth=1,
                    semantic_score=score,
                )

    async def _traverse_graph(self) -> None:
        traversal = self.cfg.get("traversal", {}) or {}
        max_depth = min(int(traversal.get("max_depth") or 0), 3)
        if max_depth <= 0:
            return
        direction = str(traversal.get("direction") or "both")
        edge_types = {str(t) for t in _as_str_list(traversal.get("edge_types"))}
        queue: list[tuple[str, int]] = [
            (rev_kref, 0) for rev_kref in sorted(self.candidates.keys())
        ]
        visited = {rev_kref for rev_kref, _ in queue}
        while queue and self.stats["traversed_revision_count"] < MAX_TRAVERSED_REVISIONS:
            rev_kref, depth = queue.pop(0)
            if depth >= max_depth:
                continue
            try:
                edges = await self.sdk.get_edges(rev_kref, direction=_edge_direction_code(direction))
            except Exception as exc:
                _log(f"kumiho_context: get_edges failed for {rev_kref}: {exc}")
                continue
            for edge in edges:
                edge_type = str(edge.get("edge_type") or "")
                if edge_types and edge_type not in edge_types:
                    continue
                neighbor = _edge_neighbor(edge, rev_kref)
                if not neighbor or neighbor in visited:
                    continue
                next_depth = depth + 1
                edge_entry = {
                    "from": edge.get("source_kref", ""),
                    "edge_type": edge_type,
                    "to": edge.get("target_kref", ""),
                    "depth": next_depth,
                    "score": self._graph_score(next_depth),
                }
                self.edge_map.append(edge_entry)
                rev = await self._get_revision(neighbor)
                if not rev:
                    continue
                item = await self._get_item(rev.get("item_kref") or _item_kref_from_revision_kref(neighbor))
                self._add_candidate(
                    rev,
                    item,
                    reason=f"edge:{edge_type} from {rev_kref}",
                    selected_by="edge_traversal",
                    depth=next_depth,
                    via_edge=edge_entry,
                )
                visited.add(neighbor)
                queue.append((neighbor, next_depth))
                self.stats["traversed_revision_count"] += 1
                if self.stats["traversed_revision_count"] >= MAX_TRAVERSED_REVISIONS:
                    self.stats["truncated"] = True
                    self.stats["truncation_reason"] = "max traversed revisions exceeded"
                    break

    async def _get_item(self, item_kref: str, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
        if not item_kref:
            return dict(fallback or {})
        if item_kref in self.item_cache:
            return self.item_cache[item_kref]
        item: dict[str, Any] | None = None
        if hasattr(self.sdk, "get_item"):
            try:
                item = await self.sdk.get_item(item_kref)
            except Exception:
                item = None
        if not item:
            item = dict(fallback or {})
            item.setdefault("kref", item_kref)
        self.item_cache[item_kref] = item
        return item

    async def _get_revision(self, revision_kref: str) -> dict[str, Any] | None:
        if not revision_kref:
            return None
        if revision_kref in self.revision_cache:
            return self.revision_cache[revision_kref]
        rev: dict[str, Any] | None = None
        if hasattr(self.sdk, "get_revision"):
            try:
                rev = await self.sdk.get_revision(revision_kref)
            except Exception:
                rev = None
        if rev:
            self.revision_cache[revision_kref] = rev
        return rev

    async def _select_revision_for_item(
        self,
        item: dict[str, Any],
        preferred_tag: str | None,
    ) -> tuple[dict[str, Any] | None, str]:
        item_kref = str(item.get("kref") or item.get("item_kref") or "")
        if not item_kref:
            return None, ""
        tag_preferences = list(DEFAULT_TAG_PREFERENCE)
        lock = self.cfg.get("lock", {}) or {}
        configured = _as_str_list(lock.get("tag_preference"))
        if configured:
            tag_preferences = configured
        if preferred_tag:
            tag_preferences = [preferred_tag] + [t for t in tag_preferences if t != preferred_tag]
        for tag in tag_preferences:
            rev = None
            if hasattr(self.sdk, "get_revision_by_tag"):
                try:
                    rev = await self.sdk.get_revision_by_tag(item_kref, tag)
                except Exception:
                    rev = None
            else:
                try:
                    rev = await self.sdk.get_latest_revision(item_kref, tag=tag)
                except Exception:
                    rev = None
            if rev and not _deprecated(rev.get("deprecated")):
                return rev, f"tag:{tag}"
        return None, ""

    def _add_candidate(
        self,
        rev: dict[str, Any],
        item: dict[str, Any],
        *,
        reason: str,
        selected_by: str,
        depth: int,
        semantic_score: float = 0.0,
        via_edge: dict[str, Any] | None = None,
    ) -> None:
        rev_kref = str(rev.get("kref") or "")
        if not rev_kref:
            return
        existing = self.candidates.get(rev_kref)
        if existing:
            existing["why_loaded"].append(reason)
            existing["depth"] = min(existing["depth"], depth)
            existing["semantic_score"] = max(existing["semantic_score"], semantic_score)
            if via_edge:
                existing.setdefault("via_edges", []).append(via_edge)
            return
        self.candidates[rev_kref] = {
            "revision": rev,
            "item": item,
            "depth": depth,
            "semantic_score": semantic_score,
            "selected_by": selected_by,
            "why_loaded": [reason],
            "via_edges": [via_edge] if via_edge else [],
        }

    def _filter_candidates(self) -> list[dict[str, Any]]:
        filters = self.cfg.get("filters", {}) or {}
        include_kinds = set(_as_str_list(filters.get("include_kinds")))
        exclude_kinds = set(_as_str_list(filters.get("exclude_kinds")))
        include_tags = set(_as_str_list(filters.get("include_tags")))
        exclude_tags = set(_as_str_list(filters.get("exclude_tags")))
        spaces = _as_str_list(filters.get("spaces"))
        out: list[dict[str, Any]] = []
        for candidate in self.candidates.values():
            item = candidate["item"]
            rev = candidate["revision"]
            kind = str(item.get("kind") or "")
            tags = _tags_from_revision(rev) | _tags_from_item(item)
            item_kref = str(item.get("kref") or rev.get("item_kref") or "")
            if _deprecated(item.get("deprecated")) or _deprecated(rev.get("deprecated")):
                continue
            if include_kinds and kind not in include_kinds:
                continue
            if exclude_kinds and kind in exclude_kinds:
                continue
            if include_tags and not (tags & include_tags):
                continue
            if exclude_tags and (tags & exclude_tags):
                continue
            if not _space_matches(item_kref, spaces):
                continue
            out.append(candidate)
        return out

    def _rank_candidates(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ranking = self.cfg.get("ranking", {}) or {}
        method = str(ranking.get("method") or "hybrid")
        boost_kinds = {
            str(k): float(v)
            for k, v in (ranking.get("boost_kinds") or {}).items()
            if isinstance(v, (int, float))
        }
        boost_edge_types = {
            str(k): float(v)
            for k, v in (ranking.get("boost_edge_types") or {}).items()
            if isinstance(v, (int, float))
        }
        max_created = max((_parse_dt(c["revision"].get("created_at")) for c in candidates), default=0.0)
        for c in candidates:
            item = c["item"]
            rev = c["revision"]
            kind = str(item.get("kind") or "")
            edge_boost = 0.0
            critical = kind in CRITICAL_KINDS
            for edge in c.get("via_edges") or []:
                edge_type = str(edge.get("edge_type") or "")
                if edge_type in CRITICAL_EDGE_TYPES:
                    critical = True
                edge_boost = max(edge_boost, boost_edge_types.get(edge_type, 0.0))
            graph_score = self._graph_score(int(c.get("depth") or 0))
            semantic_score = min(max(float(c.get("semantic_score") or 0.0), 0.0), 1.0)
            tags = _tags_from_revision(rev) | _tags_from_item(item)
            tag_score = 1.0 if tags & {"current", "active", "production-ready", "ready", "published"} else 0.0
            created = _parse_dt(rev.get("created_at"))
            recency_score = (created / max_created) if max_created and created else 0.0
            kind_boost = boost_kinds.get(kind, 0.0)
            if method == "none":
                score = 1.0
            elif method == "graph":
                score = graph_score + edge_boost * 0.05
            elif method == "semantic":
                score = semantic_score
            else:
                score = (
                    semantic_score * 0.45
                    + graph_score * 0.35
                    + tag_score * 0.10
                    + recency_score * 0.05
                    + min(kind_boost, 5.0) * 0.05
                    + min(edge_boost, 5.0) * 0.03
                )
            c["score"] = round(score, 6)
            c["critical"] = critical
        return sorted(
            candidates,
            key=lambda c: (
                0 if c.get("critical") else 1,
                -float(c.get("score") or 0.0),
                int(c.get("depth") or 0),
                str(c["item"].get("kind") or ""),
                _name(c["item"]),
                str(c["revision"].get("kref") or ""),
            ),
        )

    def _graph_score(self, depth: int) -> float:
        if depth <= 0:
            return 1.0
        if depth == 1:
            return 0.75
        if depth == 2:
            return 0.50
        return 0.25

    def _select_candidates(self, ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
        filters = self.cfg.get("filters", {}) or {}
        max_items = min(int(filters.get("max_items") or 50), 100)
        selected: list[dict[str, Any]] = []
        seen_items: set[str] = set()
        for c in ranked:
            item = c["item"]
            item_kref = str(item.get("kref") or c["revision"].get("item_kref") or "")
            kind = str(item.get("kind") or "")
            if kind not in MULTI_REVISION_KINDS and item_kref in seen_items:
                continue
            if len(selected) >= max_items and not c.get("critical"):
                self.stats["truncated"] = True
                self.stats["truncation_reason"] = "max_items exceeded"
                continue
            selected.append(c)
            if item_kref:
                seen_items.add(item_kref)
        self.stats["selected_item_count"] = len(seen_items)
        self.stats["locked_revision_count"] = len(selected)
        return selected

    async def _load_artifact_summaries(self, selected: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        output_cfg = self.cfg.get("output", {}) or {}
        include_summaries = bool(output_cfg.get("include_artifact_summaries", True))
        include_content = bool(output_cfg.get("include_artifact_content", False))
        per_item_cap = int(output_cfg.get("max_artifact_chars_per_item") or 3000)
        total_cap = int(output_cfg.get("max_total_artifact_chars") or 60000)
        total_used = 0
        summaries: dict[str, dict[str, Any]] = {}
        for candidate in selected:
            rev = candidate["revision"]
            rev_kref = str(rev.get("kref") or "")
            artifact = await self._default_artifact(rev)
            artifact_meta = dict(artifact.get("metadata") or {}) if artifact else {}
            rev_meta = dict(rev.get("metadata") or {})
            summary = ""
            content = ""
            if include_summaries:
                summary = (
                    str(artifact_meta.get("summary") or "")
                    or str(rev_meta.get("summary") or "")
                    or str(rev_meta.get("description") or "")
                    or str(rev_meta.get("content_preview") or "")
                )
            location = str(artifact.get("location") or "") if artifact else ""
            if (include_content or (include_summaries and not summary)) and location and os.path.isfile(location):
                try:
                    with open(location, "r", encoding="utf-8") as fh:
                        raw = fh.read()
                    remaining = max(total_cap - total_used, 0)
                    limit = min(per_item_cap, remaining)
                    content = _short_text(raw, limit)
                    total_used += len(content)
                    if include_summaries and not summary:
                        summary = content
                except Exception as exc:
                    _log(f"kumiho_context: failed to read artifact {location}: {exc}")
            summaries[rev_kref] = {
                "summary": summary,
                "artifact": artifact,
                "artifact_metadata": artifact_meta,
                "content": content if include_content else "",
            }
        return summaries

    async def _default_artifact(self, rev: dict[str, Any]) -> dict[str, Any]:
        rev_kref = str(rev.get("kref") or "")
        if not rev_kref:
            return {}
        try:
            artifacts = await self.sdk.get_artifacts(rev_kref)
        except Exception:
            return {}
        if not artifacts:
            return {}
        default_name = str(rev.get("default_artifact") or "")
        if default_name:
            for artifact in artifacts:
                if artifact.get("name") == default_name:
                    return artifact
        return artifacts[0]

    async def _detect_conflicts(self, selected: list[dict[str, Any]]) -> None:
        output_cfg = self.cfg.get("output", {}) or {}
        if not bool(output_cfg.get("include_conflict_warnings", True)):
            return
        selected_revs = {str(c["revision"].get("kref") or "") for c in selected}
        for edge in self.edge_map:
            edge_type = str(edge.get("edge_type") or "")
            if edge_type not in CRITICAL_EDGE_TYPES:
                continue
            source = str(edge.get("from") or "")
            target = str(edge.get("to") or "")
            if source not in selected_revs and target not in selected_revs:
                continue
            self.conflict_warnings.append({
                "severity": "high" if edge_type == "CONTRADICTS" else "medium",
                "type": f"{edge_type.lower()}_edge",
                "source": source,
                "target": target,
                "warning": f"Selected context includes a {edge_type} edge.",
            })
        for candidate in selected:
            item = candidate["item"]
            rev = candidate["revision"]
            tags = _tags_from_revision(rev) | _tags_from_item(item)
            if "spoiler-locked" in tags or "spoiler_locked" in tags:
                self.conflict_warnings.append({
                    "severity": "medium",
                    "type": "spoiler_lock",
                    "source": rev.get("kref", ""),
                    "warning": "Item is spoiler-locked. Use only as restricted context.",
                })
            if "current" in tags and hasattr(self.sdk, "get_item_revisions"):
                try:
                    revisions = await self.sdk.get_item_revisions(item.get("kref", ""), include_metadata=False)
                except Exception:
                    revisions = []
                current_count = sum(1 for r in revisions if "current" in _tags_from_revision(r))
                if current_count > 1:
                    self.conflict_warnings.append({
                        "severity": "high",
                        "type": "multiple_current_revisions",
                        "item": item.get("kref", ""),
                        "warning": "Multiple revisions are tagged current for this item.",
                    })

    def _build_context_pack(
        self,
        selected: list[dict[str, Any]],
        summaries: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        locked_manifest: list[dict[str, Any]] = []
        relevant_items: list[dict[str, Any]] = []
        relevant_canon: dict[str, list[dict[str, Any]]] = {}
        for candidate in selected:
            item = candidate["item"]
            rev = candidate["revision"]
            rev_kref = str(rev.get("kref") or "")
            item_kref = str(item.get("kref") or rev.get("item_kref") or _item_kref_from_revision_kref(rev_kref))
            kind = str(item.get("kind") or "")
            summary_info = summaries.get(rev_kref, {})
            summary = str(summary_info.get("summary") or "")
            locked_manifest.append({
                "item_kref": item_kref,
                "revision_kref": rev_kref,
                "kind": kind,
                "name": _name(item),
                "selected_by": candidate.get("selected_by", ""),
                "reason": "; ".join(dict.fromkeys(candidate.get("why_loaded", []))),
                "score": round(float(candidate.get("score") or 0.0), 4),
            })
            item_entry = {
                "kind": kind,
                "name": _name(item),
                "item_kref": item_kref,
                "revision_kref": rev_kref,
                "summary": summary,
                "why_loaded": list(dict.fromkeys(candidate.get("why_loaded", []))),
            }
            artifact = summary_info.get("artifact") or {}
            if artifact:
                item_entry["artifact"] = {
                    "name": artifact.get("name", ""),
                    "kref": artifact.get("kref", ""),
                    "location": artifact.get("location", ""),
                }
            if summary_info.get("content"):
                item_entry["content"] = summary_info["content"]
            relevant_items.append(item_entry)
            section = SECTION_BY_KIND.get(kind, "other_context")
            relevant_canon.setdefault(section, []).append({
                "id": _base_name(item) or _name(item),
                "source": rev_kref,
                "kind": kind,
                "summary": summary,
            })

        source_krefs = [m["revision_kref"] for m in locked_manifest]
        edge_map = self.edge_map if (self.cfg.get("output", {}) or {}).get("include_edge_map", True) else []
        return {
            "name": f"{self.project}-{self.step_id}-context-pack",
            "project": self.project,
            "mode": self.mode,
            "task": {"workflow": self.workflow, "step_id": self.step_id},
            "locked_manifest": locked_manifest,
            "source_krefs": source_krefs,
            "relevant_items": relevant_items,
            "relevant_canon": relevant_canon,
            "edge_map": edge_map,
            "conflict_warnings": self.conflict_warnings,
            "missing_context": self.missing_context,
            "assumptions": self._assumptions(),
            "stats": self.stats,
        }

    def _assumptions(self) -> list[str]:
        assumptions = []
        for missing in self.missing_context:
            name = missing.get("name") or missing.get("kind") or missing.get("kref") or "context"
            reason = missing.get("reason") or missing.get("message") or "missing"
            assumptions.append(f"{name}: {reason}")
        return assumptions

    def _render_context_pack(self, pack: dict[str, Any]) -> str:
        lines = [
            f"# Kumiho Context Pack: {pack.get('name', '')}",
            "",
            f"- project: {pack.get('project', '')}",
            f"- mode: {pack.get('mode', '')}",
            f"- workflow: {pack.get('task', {}).get('workflow', '')}",
            "",
            "## Locked Manifest",
        ]
        manifest = pack.get("locked_manifest") or []
        if manifest:
            for entry in manifest:
                lines.append(
                    f"- {entry.get('revision_kref', '')} "
                    f"({entry.get('kind', '')}/{entry.get('name', '')}; "
                    f"selected_by={entry.get('selected_by', '')}; "
                    f"score={entry.get('score', 0)})"
                )
        else:
            lines.append("- none")

        lines.extend(["", "## Relevant Context"])
        for section, entries in (pack.get("relevant_canon") or {}).items():
            title = section.replace("_", " ").title()
            lines.extend(["", f"### {title}"])
            for entry in entries:
                lines.append(f"- {entry.get('id', '')} ({entry.get('source', '')})")
                summary = str(entry.get("summary") or "").strip()
                if summary:
                    lines.append(f"  {summary}")

        lines.extend(["", "## Edge Map"])
        edge_map = pack.get("edge_map") or []
        if edge_map:
            for edge in edge_map:
                lines.append(
                    f"- {edge.get('from', '')} --{edge.get('edge_type', '')}--> "
                    f"{edge.get('to', '')} (depth={edge.get('depth', '')})"
                )
        else:
            lines.append("- none")

        lines.extend(["", "## Conflict Warnings"])
        warnings = pack.get("conflict_warnings") or []
        if warnings:
            for warning in warnings:
                lines.append(
                    f"- [{warning.get('severity', 'info')}] "
                    f"{warning.get('warning', warning.get('type', 'warning'))}"
                )
        else:
            lines.append("- none")

        lines.extend(["", "## Missing Context"])
        missing = pack.get("missing_context") or []
        if missing:
            for entry in missing:
                label = entry.get("name") or entry.get("bundle") or entry.get("kref") or entry.get("kind") or "context"
                lines.append(f"- {label}: {entry.get('reason') or entry.get('message') or 'missing'}")
        else:
            lines.append("- none")

        lines.extend(["", "## Source Krefs"])
        source_krefs = pack.get("source_krefs") or []
        if source_krefs:
            for kref in source_krefs:
                lines.append(f"- {kref}")
        else:
            lines.append("- none")

        stats = pack.get("stats") or {}
        lines.extend(["", "## Stats"])
        for key in sorted(stats):
            lines.append(f"- {key}: {stats[key]}")
        return "\n".join(lines).rstrip() + "\n"


async def compile_kumiho_context(config: dict[str, Any], *, workflow: str, step_id: str) -> dict[str, Any]:
    """Compile a kumiho_context result using the configured Operator Kumiho SDK."""
    from ..operator_mcp import KUMIHO_SDK

    if not getattr(KUMIHO_SDK, "_available", False):
        if hasattr(KUMIHO_SDK, "_lazy_init"):
            KUMIHO_SDK._lazy_init()
    if not getattr(KUMIHO_SDK, "_available", False):
        raise RuntimeError("Kumiho SDK not available")

    compiler = KumihoContextCompiler(KUMIHO_SDK, config, workflow=workflow, step_id=step_id)
    return await compiler.compile()
