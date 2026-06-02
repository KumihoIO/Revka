"""Kumiho mutation workflow helpers.

The workflow boundary is intentionally split:

- kumiho_bundle_update mutates only bundle membership.
- kumiho_patch_apply commits approved canon patch data.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote, urlparse

try:  # pragma: no cover - PyYAML is present in normal runtime/test envs.
    import yaml
except Exception:  # pragma: no cover
    yaml = None  # type: ignore[assignment]


DEFAULT_PROTECTED_BUNDLE_SUFFIXES = (
    "main-canon",
    "current-character-states",
    "active-storylines",
    "active-foreshadow",
)
ALLOWED_PATCH_STATUSES = {"candidate", "validated", "approved"}
PATCH_UPDATE_SECTIONS = (
    "character_states",
    "storylines",
    "foreshadow_threads",
    "timeline_events",
)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "approved"}
    return bool(value)


def _tags(value: dict[str, Any]) -> set[str]:
    raw = value.get("tags")
    if isinstance(raw, (list, tuple, set)):
        return {str(tag) for tag in raw if str(tag)}
    tag = value.get("tag")
    return {str(tag)} if tag else set()


def _is_kref(value: str) -> bool:
    return value.startswith("kref://")


def _is_revision_kref(value: str) -> bool:
    return _is_kref(value) and "?r=" in value


def _item_kref_from_revision_kref(value: str) -> str:
    return value.split("#", 1)[0].split("?", 1)[0]


def _artifact_location_to_path(location: str) -> str:
    if not location:
        return ""
    if not location.startswith("file://"):
        return location
    parsed = urlparse(location)
    if parsed.netloc and parsed.path:
        raw = f"//{parsed.netloc}{parsed.path}"
    elif parsed.netloc:
        raw = parsed.netloc
    else:
        raw = parsed.path or location[len("file://"):]
    raw = unquote(raw)
    if len(raw) >= 3 and raw[0] == "/" and raw[2] == ":":
        raw = raw[1:]
    return raw


def _member_item_kref(member: dict[str, Any]) -> str:
    return str(
        member.get("item_kref")
        or member.get("kref")
        or member.get("item", {}).get("kref")
        or ""
    )


def _name(value: dict[str, Any]) -> str:
    return str(value.get("item_name") or value.get("name") or "")


def _bundle_name_from_ref(ref: str) -> str:
    text = str(ref or "").strip()
    if not text:
        return ""
    if not _is_kref(text):
        return text.rstrip("/").split("/")[-1]
    base = text.split("?", 1)[0].rstrip("/").split("/")[-1]
    return base.removesuffix(".bundle")


def _bundle_names_from_config(value: Any) -> list[str]:
    names: list[str] = []
    for raw in _as_list(value):
        name = _bundle_name_from_ref(str(raw))
        if name:
            names.append(name)
    return names


def _bundle_name_matches_suffix(name: str, suffix: str) -> bool:
    return bool(name and suffix and (name == suffix or name.endswith(f"-{suffix}")))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jsonable_metadata(value: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, raw in value.items():
        if raw is None:
            continue
        if isinstance(raw, (str, int, float, bool)):
            out[str(key)] = str(raw)
        else:
            out[str(key)] = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
    return out


def _error(kind: str, message: str, **extra: Any) -> dict[str, Any]:
    out = {"type": kind, "message": message}
    out.update(extra)
    return out


def _render_bundle_report(result: dict[str, Any]) -> str:
    lines = [
        "# Kumiho Bundle Update Report",
        "",
        f"Project: {result.get('project', '')}",
        f"Mode: {result.get('mode', '')}",
        f"Changed: {str(bool(result.get('changed'))).lower()}",
        "",
        "## Bundles",
    ]
    for bundle in result.get("bundles", []):
        lines.append(
            "- "
            f"{bundle.get('bundle', '')}: "
            f"added {len(bundle.get('added', []))}, "
            f"removed {len(bundle.get('removed', []))}, "
            f"skipped {len(bundle.get('skipped_existing', [])) + len(bundle.get('skipped_missing', []))}, "
            f"errors {len(bundle.get('errors', []))}"
        )
    if result.get("errors"):
        lines.extend(["", "## Errors"])
        lines.extend(f"- {err.get('message', err)}" for err in result["errors"])
    return "\n".join(lines).strip() + "\n"


class KumihoBundleUpdater:
    """Apply idempotent Kumiho bundle membership updates."""

    def __init__(self, sdk: Any, config: dict[str, Any]) -> None:
        self.sdk = sdk
        self.cfg = deepcopy(config)
        self.project = str(self.cfg.get("project") or "").strip()
        self.mode = str(self.cfg.get("mode") or "add_members")
        self.create_if_missing = _as_bool(self.cfg.get("create_if_missing", False))
        self.idempotent = _as_bool(self.cfg.get("idempotent", True))
        missing_policy = self.cfg.get("fail_if_missing_bundle")
        self.fail_if_missing_bundle = (
            not self.create_if_missing if missing_policy is None else _as_bool(missing_policy)
        )
        self.fail_if_missing_item = _as_bool(self.cfg.get("fail_if_missing_item", True))
        self.allow_protected = _as_bool(self.cfg.get("allow_protected", False))
        self.protected_bundles = set(_bundle_names_from_config(self.cfg.get("protected_bundles")))
        suffixes_config = (
            self.cfg["protected_bundle_suffixes"]
            if "protected_bundle_suffixes" in self.cfg
            else DEFAULT_PROTECTED_BUNDLE_SUFFIXES
        )
        self.protected_bundle_suffixes = tuple(_bundle_names_from_config(suffixes_config))
        self.errors: list[dict[str, Any]] = []

    async def run(self) -> dict[str, Any]:
        if not self.project:
            return self._result(False, [], [_error("missing_project", "kumiho.project is required")])
        updates = [_as_dict(update) for update in _as_list(self.cfg.get("updates"))]
        if not updates:
            return self._result(False, [], [_error("missing_updates", "kumiho.updates is required")])

        bundles: list[dict[str, Any]] = []
        changed = False
        for update in updates:
            bundle_result = await self._apply_update(update)
            bundles.append(bundle_result)
            changed = changed or bool(bundle_result.get("added") or bundle_result.get("removed"))

        output = self._result(changed, bundles, self.errors)
        output["success"] = not self.errors
        output["artifact_content"] = _render_bundle_report(output)
        return output

    def _result(
        self,
        changed: bool,
        bundles: list[dict[str, Any]],
        errors: list[dict[str, Any]],
    ) -> dict[str, Any]:
        operations_count = 0
        for bundle in bundles:
            operations_count += len(bundle.get("added", [])) + len(bundle.get("removed", []))
        return {
            "success": not errors,
            "project": self.project,
            "mode": self.mode,
            "changed": changed,
            "bundles": bundles,
            "operations_count": operations_count,
            "errors": errors,
        }

    async def _apply_update(self, update: dict[str, Any]) -> dict[str, Any]:
        bundle_ref = str(update.get("bundle") or "").strip()
        result: dict[str, Any] = {
            "bundle": bundle_ref,
            "bundle_kref": "",
            "added": [],
            "removed": [],
            "replaced": [],
            "skipped_existing": [],
            "skipped_missing": [],
            "errors": [],
        }
        if not bundle_ref:
            self._record_error(result, "missing_bundle_ref", "bundle update requires bundle")
            return result
        if self._is_protected(bundle_ref) and not self.allow_protected:
            self._record_error(
                result,
                "protected_bundle",
                f"Bundle '{bundle_ref}' is protected; use kumiho_patch_apply or set allow_protected",
            )
            return result
        if not self._mode_allows(update):
            self._record_error(
                result,
                "mode_operation_mismatch",
                f"mode {self.mode!r} does not allow one or more requested operations",
            )
            return result

        bundle = await self._resolve_bundle(bundle_ref)
        if not bundle:
            if self.create_if_missing:
                try:
                    bundle = await self.sdk.create_bundle(
                        f"{self.project}/Bundles",
                        _bundle_name_from_ref(bundle_ref),
                        metadata={"source": "workflow", "created_by_step": "kumiho_bundle_update"},
                    )
                except Exception as exc:
                    self._record_error(result, "create_bundle_failed", str(exc))
                    return result
            elif self.fail_if_missing_bundle:
                self._record_error(result, "missing_bundle", f"Bundle '{bundle_ref}' not found")
                return result
            else:
                result["skipped_missing"].append(bundle_ref)
                return result

        bundle_kref = str(bundle.get("kref") or "")
        result["bundle_kref"] = bundle_kref
        existing = await self._member_set(bundle_kref)

        for member in [_as_dict(m) for m in _as_list(update.get("add"))]:
            await self._add_member(result, bundle_kref, existing, member)
        for member in [_as_dict(m) for m in _as_list(update.get("remove"))]:
            await self._remove_member(result, bundle_kref, existing, member)
        replace = _as_dict(update.get("replace"))
        if replace:
            await self._replace_members(result, bundle_kref, existing, replace)

        return result

    def _record_error(self, bundle_result: dict[str, Any], kind: str, message: str) -> None:
        err = _error(kind, message, bundle=bundle_result.get("bundle", ""))
        bundle_result["errors"].append(err)
        self.errors.append(err)

    def _is_protected(self, bundle_ref: str) -> bool:
        if self.allow_protected:
            return False
        name = _bundle_name_from_ref(bundle_ref)
        return name in self.protected_bundles or any(
            _bundle_name_matches_suffix(name, suffix)
            for suffix in self.protected_bundle_suffixes
        )

    def _mode_allows(self, update: dict[str, Any]) -> bool:
        has_add = bool(update.get("add"))
        has_remove = bool(update.get("remove"))
        has_replace = bool(update.get("replace"))
        if self.mode == "mixed":
            return has_add or has_remove or has_replace
        if self.mode == "add_members":
            return has_add and not has_remove and not has_replace
        if self.mode == "remove_members":
            return has_remove and not has_add and not has_replace
        if self.mode == "replace_members":
            return has_replace and not has_add and not has_remove
        return False

    async def _resolve_bundle(self, bundle_ref: str) -> dict[str, Any] | None:
        if _is_kref(bundle_ref):
            try:
                return await self.sdk.get_bundle_by_kref(bundle_ref)
            except Exception:
                return None
        items = await self.sdk.search_items(
            context=self.project,
            name=_bundle_name_from_ref(bundle_ref),
            kind="bundle",
            include_metadata=True,
        )
        expected = _bundle_name_from_ref(bundle_ref)
        for item in items:
            if _name(item) == expected or _name(item).removesuffix(".bundle") == expected:
                return item
        return items[0] if items else None

    async def _member_set(self, bundle_kref: str) -> set[str]:
        members = await self.sdk.get_bundle_members(bundle_kref)
        return {_member_item_kref(member) for member in members if _member_item_kref(member)}

    async def _resolve_item(self, item_kref: str) -> dict[str, Any] | None:
        clean = _item_kref_from_revision_kref(item_kref) if _is_revision_kref(item_kref) else item_kref
        if not clean:
            return None
        try:
            return await self.sdk.get_item(clean)
        except Exception:
            return None

    async def _add_member(
        self,
        result: dict[str, Any],
        bundle_kref: str,
        existing: set[str],
        member: dict[str, Any],
    ) -> None:
        item_kref = _item_kref_from_revision_kref(str(member.get("item_kref") or "").strip())
        if not item_kref:
            self._record_error(result, "missing_item_kref", "add member requires item_kref")
            return
        if item_kref in existing:
            if self.idempotent:
                result["skipped_existing"].append(item_kref)
            else:
                self._record_error(result, "duplicate_member", f"{item_kref} is already in bundle")
            return
        item = await self._resolve_item(item_kref)
        if not item and self.fail_if_missing_item:
            self._record_error(result, "missing_item", f"Item '{item_kref}' not found")
            return
        added = await self.sdk.add_bundle_member(bundle_kref, item_kref)
        if added:
            existing.add(item_kref)
            result["added"].append(item_kref)
        else:
            self._record_error(result, "add_member_failed", f"Failed to add {item_kref}")

    async def _remove_member(
        self,
        result: dict[str, Any],
        bundle_kref: str,
        existing: set[str],
        member: dict[str, Any],
    ) -> None:
        item_kref = _item_kref_from_revision_kref(str(member.get("item_kref") or "").strip())
        if not item_kref:
            self._record_error(result, "missing_item_kref", "remove member requires item_kref")
            return
        if item_kref not in existing:
            if self.idempotent:
                result["skipped_missing"].append(item_kref)
            else:
                self._record_error(result, "missing_member", f"{item_kref} is not in bundle")
            return
        removed = await self.sdk.remove_bundle_member(bundle_kref, item_kref)
        if removed:
            existing.discard(item_kref)
            result["removed"].append(item_kref)
        else:
            self._record_error(result, "remove_member_failed", f"Failed to remove {item_kref}")

    async def _replace_members(
        self,
        result: dict[str, Any],
        bundle_kref: str,
        existing: set[str],
        replace: dict[str, Any],
    ) -> None:
        match = _as_dict(replace.get("match"))
        kind = str(match.get("kind") or "").strip()
        name_pattern = str(match.get("name_pattern") or "*").strip() or "*"
        matched: list[str] = []
        for item_kref in sorted(existing):
            item = await self._resolve_item(item_kref)
            if not item:
                continue
            if kind and str(item.get("kind") or "") != kind:
                continue
            if not fnmatch.fnmatchcase(_name(item), name_pattern):
                continue
            matched.append(item_kref)
        for item_kref in matched:
            await self._remove_member(result, bundle_kref, existing, {"item_kref": item_kref})
        for member in [_as_dict(m) for m in _as_list(replace.get("with") or replace.get("with_items"))]:
            await self._add_member(result, bundle_kref, existing, member)
        if matched:
            result["replaced"].extend(matched)


def _render_patch_report(result: dict[str, Any]) -> str:
    lines = [
        "# Kumiho Patch Apply Report",
        "",
        f"Patch: {result.get('patch_kref', '')}",
        f"Status: {'applied' if result.get('applied') else 'dry_run' if result.get('dry_run') else 'blocked'}",
    ]
    if result.get("planned_operations"):
        lines.extend(["", "## Planned Operations"])
        for op in result["planned_operations"]:
            lines.append(f"- {op.get('op')}: {op.get('summary', '')}")
    if result.get("created_revisions"):
        lines.extend(["", "## Created Revisions"])
        for rev in result["created_revisions"]:
            lines.append(
                "- "
                f"{rev.get('item_kref', '')} "
                f"{rev.get('old_revision_kref', '')} -> {rev.get('new_revision_kref', '')}"
            )
    if result.get("created_items"):
        lines.extend(["", "## Created Items"])
        lines.extend(f"- {item.get('item_kref', '')}" for item in result["created_items"])
    if result.get("created_edges"):
        lines.extend(["", "## Created Edges"])
        lines.extend(
            f"- {edge.get('from', '')} {edge.get('edge_type', '')} {edge.get('to', '')}"
            for edge in result["created_edges"]
        )
    if result.get("bundle_updates"):
        lines.extend(["", "## Bundle Updates"])
        for bundle in result["bundle_updates"]:
            lines.append(f"- {bundle.get('bundle', '')}: {bundle.get('operation', '')} {bundle.get('item_kref', '')}")
    if result.get("errors"):
        lines.extend(["", "## Errors"])
        lines.extend(f"- {err.get('message', err)}" for err in result["errors"])
    return "\n".join(lines).strip() + "\n"


class KumihoPatchApplier:
    """Plan or apply a canon-patch item."""

    def __init__(self, sdk: Any, config: dict[str, Any], *, workflow: str, step_id: str) -> None:
        self.sdk = sdk
        self.cfg = deepcopy(config)
        self.workflow = workflow
        self.step_id = step_id
        self.project = str(self.cfg.get("project") or "").strip()
        self.patch_kref = str(self.cfg.get("patch_kref") or "").strip()
        self.dry_run = _as_bool(self.cfg.get("dry_run", True))
        self.allow_auto_apply = _as_bool(self.cfg.get("allow_auto_apply", False))
        self.approval = _as_dict(self.cfg.get("approval"))
        self.apply = _as_dict(self.cfg.get("apply"))
        self.tag_policy = _as_dict(self.cfg.get("tag_policy"))
        self.bundle_policy = _as_dict(self.cfg.get("bundle_policy"))
        self.evidence = _as_dict(self.cfg.get("evidence"))
        self.validation = _as_dict(self.cfg.get("validation"))
        self.errors: list[dict[str, Any]] = []
        self.warnings: list[dict[str, Any]] = []
        self.rollback_plan: list[dict[str, Any]] = []
        self.patch: dict[str, Any] = {}
        self.patch_revision: dict[str, Any] = {}

    async def run(self) -> dict[str, Any]:
        if not self.project:
            return self._blocked("missing_project", "kumiho.project is required")
        if not self.patch_kref:
            return self._blocked("missing_patch_kref", "kumiho.patch_kref is required")
        if not self.dry_run and self.approval.get("required", True):
            if not _as_bool(self.approval.get("approved", False)) and not self.allow_auto_apply:
                return self._blocked("approval_required", "Patch application requires approval")

        self.patch_revision = await self.sdk.get_revision(self.patch_kref) or {}
        if not self.patch_revision:
            return self._blocked("missing_patch", f"Patch revision '{self.patch_kref}' not found")
        self.patch = await self._load_patch_payload()
        if not self.patch:
            return self._blocked("invalid_patch", "Patch artifact or metadata did not contain canon_patch data")

        planned = await self._plan_operations()
        if self.errors and self._should_fail_on_validation_errors():
            return self._finish(False, False, planned, [], [], [], [], "")
        if self.dry_run:
            return self._finish(True, False, planned, [], [], [], [], "")

        created_revisions, created_items = await self._apply_revision_updates()
        created_edges = await self._apply_edges(created_revisions)
        await self._apply_patch_tags()
        bundle_updates = await self._apply_bundle_updates(created_revisions, created_items)
        apply_report_kref = await self._save_apply_report(created_revisions, created_items, created_edges, bundle_updates)
        success = not self.errors
        return self._finish(
            success,
            success,
            planned,
            created_revisions,
            created_items,
            created_edges,
            bundle_updates,
            apply_report_kref,
        )

    def _blocked(self, kind: str, message: str) -> dict[str, Any]:
        self.errors.append(_error(kind, message))
        return self._finish(False, False, [], [], [], [], [], "")

    def _finish(
        self,
        success: bool,
        applied: bool,
        planned: list[dict[str, Any]],
        created_revisions: list[dict[str, Any]],
        created_items: list[dict[str, Any]],
        created_edges: list[dict[str, Any]],
        bundle_updates: list[dict[str, Any]],
        apply_report_kref: str,
    ) -> dict[str, Any]:
        result = {
            "success": success,
            "dry_run": self.dry_run,
            "patch_kref": self.patch_kref,
            "applied": applied,
            "blocked": bool(self.errors and not applied and not self.dry_run),
            "planned_operations": planned,
            "operations_count": len(planned),
            "created_revisions": created_revisions,
            "created_items": created_items,
            "created_edges": created_edges,
            "bundle_updates": bundle_updates,
            "apply_report_kref": apply_report_kref,
            "rollback_plan": self.rollback_plan,
            "compensation_required": bool(self.errors and (created_revisions or created_items or created_edges)),
            "partial_apply": bool(self.errors and (created_revisions or created_items or created_edges)),
            "requires_manual_repair": bool(self.errors and (created_revisions or created_items or created_edges)),
            "errors": self.errors,
            "warnings": self.warnings,
        }
        result["artifact_content"] = _render_patch_report(result)
        return result

    def _should_fail_on_validation_errors(self) -> bool:
        return any(err.get("severity", "error") != "warning" for err in self.errors)

    async def _load_patch_payload(self) -> dict[str, Any]:
        metadata = _as_dict(self.patch_revision.get("metadata"))
        for key in ("canon_patch", "patch", "content"):
            raw = metadata.get(key)
            parsed = self._parse_patch_value(raw)
            if parsed:
                return parsed
        for artifact in await self.sdk.get_artifacts(self.patch_revision.get("kref", self.patch_kref)):
            parsed = self._parse_patch_value(artifact.get("content") or artifact.get("text"))
            if parsed:
                return parsed
            location = str(artifact.get("location") or artifact.get("path") or "")
            artifact_path = _artifact_location_to_path(location)
            if artifact_path and os.path.isfile(artifact_path):
                try:
                    with open(artifact_path, "r", encoding="utf-8") as fh:
                        parsed = self._parse_patch_value(fh.read())
                    if parsed:
                        return parsed
                except Exception as exc:
                    self.warnings.append(_error("artifact_read_failed", str(exc), severity="warning"))
        return {}

    def _parse_patch_value(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return _as_dict(raw.get("canon_patch") or raw)
        if not isinstance(raw, str) or not raw.strip():
            return {}
        text = raw.strip()
        candidates = []
        candidates.extend(match.group(1).strip() for match in re.finditer(r"```(?:ya?ml|json)?\s*(.*?)```", text, re.S))
        candidates.append(text)
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except Exception:
                parsed = None
            if not parsed and yaml is not None:
                try:
                    parsed = yaml.safe_load(candidate)
                except Exception:
                    parsed = None
            if isinstance(parsed, dict):
                return _as_dict(parsed.get("canon_patch") or parsed)
        return {}

    async def _plan_operations(self) -> list[dict[str, Any]]:
        planned: list[dict[str, Any]] = []
        status = str(self.patch.get("patch_status") or self.patch.get("status") or "candidate")
        if status not in ALLOWED_PATCH_STATUSES:
            self.errors.append(_error("invalid_patch_status", f"Unsupported patch_status {status!r}"))

        for update in self._revision_updates():
            summary = self._change_summary(update)
            evidence_locator = str(update.get("evidence_locator") or "")
            if self.evidence.get("require_evidence_locator", True) and not evidence_locator:
                self._validation_issue(
                    "missing_evidence",
                    f"Patch update for {update.get('item_name', '')} is missing evidence_locator",
                    policy_key="missing_evidence_policy",
                )
            previous = str(update.get("previous_revision_kref") or "")
            if previous:
                old_rev = await self.sdk.get_revision(previous)
                if not old_rev:
                    self.errors.append(_error("missing_previous_revision", f"{previous} not found"))
                elif (
                    not self.validation.get("allowed_stale_patch", False)
                    and not (_tags(old_rev) & {"current", "active", "production-ready"})
                ):
                    self._validation_issue(
                        "stale_previous_revision",
                        f"{previous} is not tagged current/active/production-ready",
                        policy_key="stale_patch_policy",
                    )
            planned.append({
                "op": "create_revision",
                "item_name": update.get("item_name", ""),
                "item_kind": update.get("item_kind", ""),
                "previous_revision_kref": previous,
                "summary": summary,
                "dry_run": self.dry_run,
            })

        for edge in self._proposed_edges():
            if self.evidence.get("require_evidence_locator", True) and not edge.get("evidence_locator"):
                self._validation_issue(
                    "missing_edge_evidence",
                    f"Proposed edge {edge.get('edge_type', '')} is missing evidence_locator",
                    policy_key="missing_evidence_policy",
                )
            planned.append({
                "op": "create_edge",
                "from": edge.get("from", ""),
                "edge_type": edge.get("edge_type", ""),
                "to": edge.get("to", ""),
                "summary": f"{edge.get('from', '')} {edge.get('edge_type', '')} {edge.get('to', '')}",
                "dry_run": self.dry_run,
            })

        if self.apply.get("update_bundles", True):
            for name in (
                "pending_patch_bundle",
                "applied_patch_bundle",
                "current_state_bundle",
                "active_storyline_bundle",
                "active_foreshadow_bundle",
                "timeline_bundle",
            ):
                if self.bundle_policy.get(name):
                    planned.append({
                        "op": "update_bundle",
                        "bundle": self.bundle_policy[name],
                        "summary": f"Update {self.bundle_policy[name]}",
                        "dry_run": self.dry_run,
                    })
        return planned

    def _validation_issue(self, kind: str, message: str, *, policy_key: str) -> None:
        policy = str(self.validation.get(policy_key) or "fail")
        issue = _error(kind, message, severity="warning" if policy == "warn" else "error")
        if policy == "warn":
            self.warnings.append(issue)
        else:
            self.errors.append(issue)

    def _revision_updates(self) -> list[dict[str, Any]]:
        updates_root = _as_dict(self.patch.get("proposed_revision_updates"))
        out: list[dict[str, Any]] = []
        for section in PATCH_UPDATE_SECTIONS:
            for update in [_as_dict(item) for item in _as_list(updates_root.get(section))]:
                if update:
                    update.setdefault("_section", section)
                    out.append(update)
        return out

    def _proposed_edges(self) -> list[dict[str, Any]]:
        return [_as_dict(edge) for edge in _as_list(self.patch.get("proposed_edges")) if _as_dict(edge)]

    def _change_summary(self, update: dict[str, Any]) -> str:
        for key in (
            "proposed_change_summary",
            "progress_update",
            "status_change",
            "update",
            "event_summary",
            "summary",
        ):
            if update.get(key):
                return str(update[key])
        return str(update.get("item_name") or update.get("item_kind") or "revision update")

    def _change_content(self, update: dict[str, Any]) -> str:
        for key in ("proposed_artifact_patch", "artifact_patch", "content", "body"):
            if update.get(key):
                return str(update[key])
        return self._change_summary(update)

    async def _apply_revision_updates(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if not self.apply.get("create_revisions", True):
            return [], []
        created_revisions: list[dict[str, Any]] = []
        created_items: list[dict[str, Any]] = []
        for update in self._revision_updates():
            try:
                item_kind = str(update.get("item_kind") or update.get("kind") or "")
                item_name = str(update.get("item_name") or update.get("name") or "")
                previous = str(update.get("previous_revision_kref") or "")
                old_rev = await self.sdk.get_revision(previous) if previous else None
                item_kref = str(update.get("item_kref") or "")
                if not item_kref and old_rev:
                    item_kref = str(old_rev.get("item_kref") or "")
                if not item_kref and item_kind == "timeline-event" and item_name:
                    item = await self._find_or_create_item(item_name, item_kind, "Timeline")
                    if item:
                        item_kref = str(item.get("kref") or "")
                        created_items.append({"item_kref": item_kref, "kind": item_kind, "name": item_name})
                if not item_kref:
                    self.errors.append(_error("missing_target_item", f"No target item for patch update {item_name!r}"))
                    continue

                metadata = _jsonable_metadata({
                    "source_patch": self.patch_kref,
                    "patch_id": self.patch.get("patch_id", ""),
                    "previous_revision_kref": previous,
                    "item_kind": item_kind,
                    "item_name": item_name,
                    "change_summary": self._change_summary(update),
                    "evidence_locator": update.get("evidence_locator", ""),
                    "workflow": self.workflow,
                    "step_id": self.step_id,
                    "applied_at": _now_iso(),
                })
                new_rev = await self.sdk.create_revision(item_kref, metadata, tag=None)
                new_rev_kref = str(new_rev.get("kref") or "")
                if not new_rev_kref:
                    self.errors.append(_error("create_revision_failed", f"create_revision returned no kref for {item_kref}"))
                    continue
                await self._attach_patch_artifact(new_rev_kref, item_name or "patch-update", self._change_content(update))
                tags_added, tags_removed = await self._apply_revision_tags(new_rev_kref, previous)
                created_revisions.append({
                    "item_kref": item_kref,
                    "item_kind": item_kind,
                    "item_name": item_name,
                    "old_revision_kref": previous,
                    "new_revision_kref": new_rev_kref,
                    "tags_added": tags_added,
                    "tags_removed_from_old": tags_removed,
                    "evidence_locator": update.get("evidence_locator", ""),
                    "summary": self._change_summary(update),
                })
                self.rollback_plan.append({
                    "manual_repair": "remove newly created revision or restore tags",
                    "new_revision_kref": new_rev_kref,
                    "old_revision_kref": previous,
                })
            except Exception as exc:
                self.errors.append(_error("revision_update_failed", str(exc)))
        return created_revisions, created_items

    async def _find_or_create_item(self, item_name: str, item_kind: str, space_leaf: str) -> dict[str, Any] | None:
        items = await self.sdk.search_items(
            context=self.project,
            name=item_name,
            kind=item_kind,
            include_metadata=True,
        )
        if items:
            return items[0]
        return await self.sdk.create_item(
            f"{self.project}/{space_leaf}",
            item_name,
            item_kind,
            _jsonable_metadata({
                "source_patch": self.patch_kref,
                "created_by": "kumiho_patch_apply",
                "created_at": _now_iso(),
            }),
        )

    async def _attach_patch_artifact(self, revision_kref: str, item_name: str, content: str) -> None:
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", item_name or "patch-update").strip("-") or "patch-update"
        artifact_dir = os.path.expanduser(
            f"~/.construct/artifacts/{self.workflow}/{self.step_id}/{safe_name}"
        )
        os.makedirs(artifact_dir, exist_ok=True)
        artifact_path = os.path.join(artifact_dir, "content.md")
        try:
            with open(artifact_path, "w", encoding="utf-8") as fh:
                fh.write(content or "")
            await self.sdk.create_artifact(
                revision_kref,
                "content.md",
                artifact_path,
                metadata={"summary": (content or "")[:500]},
            )
        except Exception as exc:
            self.warnings.append(_error("artifact_attach_failed", str(exc), severity="warning"))

    async def _apply_revision_tags(self, new_revision_kref: str, old_revision_kref: str) -> tuple[list[str], list[str]]:
        tags_added: list[str] = []
        tags_removed: list[str] = []
        if not self.apply.get("update_tags", True):
            return tags_added, tags_removed
        for tag in _as_list(self.tag_policy.get("new_revision_tags", ["current", "approved"])):
            tag_s = str(tag).strip()
            if not tag_s:
                continue
            await self.sdk.tag_revision(new_revision_kref, tag_s)
            tags_added.append(tag_s)
        if old_revision_kref and self.apply.get("untag_previous_current", True):
            for tag in _as_list(self.tag_policy.get("old_revision_tags_remove", ["current"])):
                tag_s = str(tag).strip()
                if not tag_s:
                    continue
                await self.sdk.untag_revision(old_revision_kref, tag_s)
                tags_removed.append(tag_s)
        return tags_added, tags_removed

    async def _apply_edges(self, created_revisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self.apply.get("create_edges", True):
            return []
        revision_remap = {
            str(rev.get("old_revision_kref")): str(rev.get("new_revision_kref"))
            for rev in created_revisions
            if rev.get("old_revision_kref") and rev.get("new_revision_kref")
        }
        created: list[dict[str, Any]] = []
        for edge in self._proposed_edges():
            source = str(edge.get("from") or edge.get("source") or "")
            target = str(edge.get("to") or edge.get("target") or "")
            source = revision_remap.get(source, source)
            target = revision_remap.get(target, target)
            edge_type = str(edge.get("edge_type") or "")
            if not source or not target or not edge_type:
                self.errors.append(_error("invalid_edge", "Proposed edge requires from, to, and edge_type"))
                continue
            metadata = _jsonable_metadata({
                "source_patch": self.patch_kref,
                "source_episode": self.evidence.get("source_episode_kref", ""),
                "source_context_pack": self.evidence.get("source_context_pack_kref", ""),
                "evidence_locator": edge.get("evidence_locator", ""),
                "reason": edge.get("reason", ""),
                "confidence": edge.get("confidence", ""),
                "approved_by": self.approval.get("approved_by", ""),
                "applied_at": _now_iso(),
            })
            try:
                await self.sdk.create_edge(source, target, edge_type, metadata=metadata)
                created.append({"from": source, "edge_type": edge_type, "to": target, "metadata": metadata})
                self.rollback_plan.append({
                    "manual_repair": "delete created edge",
                    "from": source,
                    "edge_type": edge_type,
                    "to": target,
                })
            except Exception as exc:
                self.errors.append(_error("create_edge_failed", str(exc)))
        return created

    async def _apply_patch_tags(self) -> None:
        if not self.apply.get("update_tags", True):
            return
        patch_tags = _as_dict(self.tag_policy.get("patch_tags"))
        patch_rev_kref = str(self.patch_revision.get("kref") or self.patch_kref)
        for tag in _as_list(patch_tags.get("remove", ["candidate"])):
            tag_s = str(tag).strip()
            if not tag_s:
                continue
            try:
                await self.sdk.untag_revision(patch_rev_kref, tag_s)
            except Exception as exc:
                self.warnings.append(_error("patch_untag_failed", str(exc), tag=tag_s, severity="warning"))
        for tag in _as_list(patch_tags.get("add", ["applied"])):
            tag_s = str(tag).strip()
            if not tag_s:
                continue
            try:
                await self.sdk.tag_revision(patch_rev_kref, tag_s)
            except Exception as exc:
                self.warnings.append(_error("patch_tag_failed", str(exc), tag=tag_s, severity="warning"))

    async def _apply_bundle_updates(
        self,
        created_revisions: list[dict[str, Any]],
        created_items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not self.apply.get("update_bundles", True):
            return []
        updates: list[dict[str, Any]] = []
        patch_item_kref = str(self.patch_revision.get("item_kref") or _item_kref_from_revision_kref(self.patch_kref))
        if self.bundle_policy.get("pending_patch_bundle"):
            updates.append(await self._bundle_remove(self.bundle_policy["pending_patch_bundle"], patch_item_kref))
        if self.bundle_policy.get("applied_patch_bundle"):
            updates.append(await self._bundle_add(self.bundle_policy["applied_patch_bundle"], patch_item_kref))

        for rev in created_revisions:
            item_kind = str(rev.get("item_kind") or "")
            target_bundle = ""
            if item_kind == "character-state":
                target_bundle = str(self.bundle_policy.get("current_state_bundle") or "")
            elif item_kind == "storyline":
                target_bundle = str(self.bundle_policy.get("active_storyline_bundle") or "")
            elif item_kind == "foreshadow-thread":
                target_bundle = str(self.bundle_policy.get("active_foreshadow_bundle") or "")
            elif item_kind == "timeline-event":
                target_bundle = str(self.bundle_policy.get("timeline_bundle") or "")
            if target_bundle:
                updates.append(await self._bundle_add(target_bundle, str(rev.get("item_kref") or "")))
        for item in created_items:
            if item.get("kind") == "timeline-event" and self.bundle_policy.get("timeline_bundle"):
                updates.append(await self._bundle_add(self.bundle_policy["timeline_bundle"], str(item.get("item_kref") or "")))
        return [update for update in updates if update]

    async def _bundle_add(self, bundle_ref: str, item_kref: str) -> dict[str, Any]:
        return await self._bundle_mutation(bundle_ref, item_kref, add=True)

    async def _bundle_remove(self, bundle_ref: str, item_kref: str) -> dict[str, Any]:
        return await self._bundle_mutation(bundle_ref, item_kref, add=False)

    async def _bundle_mutation(self, bundle_ref: str, item_kref: str, *, add: bool) -> dict[str, Any]:
        if not bundle_ref or not item_kref:
            return {}
        bundle = await KumihoBundleUpdater(self.sdk, {
            "project": self.project,
            "mode": "add_members" if add else "remove_members",
            "idempotent": True,
            "allow_protected": True,
            "fail_if_missing_item": False,
            "updates": [{
                "bundle": bundle_ref,
                "add" if add else "remove": [{"item_kref": item_kref}],
            }],
        }).run()
        if bundle.get("errors"):
            self.warnings.extend({**err, "severity": "warning"} for err in bundle["errors"])
        return {
            "bundle": bundle_ref,
            "operation": "add" if add else "remove",
            "item_kref": item_kref,
            "success": bool(bundle.get("success")),
            "details": bundle.get("bundles", []),
        }

    async def _save_apply_report(
        self,
        created_revisions: list[dict[str, Any]],
        created_items: list[dict[str, Any]],
        created_edges: list[dict[str, Any]],
        bundle_updates: list[dict[str, Any]],
    ) -> str:
        if not self.apply.get("save_apply_report", True):
            return ""
        patch_id = str(self.patch.get("patch_id") or "canon-patch")
        report_name = f"{patch_id}-apply"
        report_content = _render_patch_report({
            "patch_kref": self.patch_kref,
            "applied": True,
            "created_revisions": created_revisions,
            "created_items": created_items,
            "created_edges": created_edges,
            "bundle_updates": bundle_updates,
            "errors": self.errors,
        })
        try:
            item = await self.sdk.create_item(
                f"{self.project}/PatchApplyReports",
                report_name,
                "patch-apply-report",
                {"source_patch": self.patch_kref, "created_at": _now_iso()},
            )
            rev = await self.sdk.create_revision(
                str(item.get("kref") or ""),
                _jsonable_metadata({
                    "source_patch": self.patch_kref,
                    "created_revision_count": len(created_revisions),
                    "created_edge_count": len(created_edges),
                    "created_at": _now_iso(),
                }),
                tag=None,
            )
            rev_kref = str(rev.get("kref") or "")
            if rev_kref:
                await self._attach_patch_artifact(rev_kref, report_name, report_content)
                await self.sdk.tag_revision(rev_kref, "applied")
            return rev_kref
        except Exception as exc:
            self.warnings.append(_error("apply_report_failed", str(exc), severity="warning"))
            return ""


async def run_kumiho_bundle_update(config: dict[str, Any]) -> dict[str, Any]:
    """Execute a kumiho_bundle_update step."""
    from ..operator_mcp import KUMIHO_SDK

    if not getattr(KUMIHO_SDK, "_available", False):
        return {
            "success": False,
            "project": str(config.get("project") or ""),
            "mode": str(config.get("mode") or "add_members"),
            "changed": False,
            "bundles": [],
            "operations_count": 0,
            "errors": [_error("kumiho_unavailable", "Kumiho SDK not available")],
            "artifact_content": "# Kumiho Bundle Update Report\n\nKumiho SDK not available.\n",
        }
    return await KumihoBundleUpdater(KUMIHO_SDK, config).run()


async def run_kumiho_patch_apply(
    config: dict[str, Any],
    *,
    workflow: str,
    step_id: str,
) -> dict[str, Any]:
    """Execute a kumiho_patch_apply step."""
    from ..operator_mcp import KUMIHO_SDK

    if not getattr(KUMIHO_SDK, "_available", False):
        return {
            "success": False,
            "dry_run": bool(config.get("dry_run", True)),
            "patch_kref": str(config.get("patch_kref") or ""),
            "applied": False,
            "blocked": True,
            "planned_operations": [],
            "operations_count": 0,
            "created_revisions": [],
            "created_items": [],
            "created_edges": [],
            "bundle_updates": [],
            "apply_report_kref": "",
            "errors": [_error("kumiho_unavailable", "Kumiho SDK not available")],
            "warnings": [],
            "artifact_content": "# Kumiho Patch Apply Report\n\nKumiho SDK not available.\n",
        }
    return await KumihoPatchApplier(KUMIHO_SDK, config, workflow=workflow, step_id=step_id).run()
