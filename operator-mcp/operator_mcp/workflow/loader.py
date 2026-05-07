"""Workflow loader — discover and parse YAML workflow definitions.

Loads from:
  1. Built-in workflows shipped with Construct (operator/workflow/builtins/)
  2. User workflows in ~/.construct/workflows/
  3. Project-local workflows in <cwd>/.construct/workflows/

Later sources override earlier ones (project > user > builtin).
"""
from __future__ import annotations

import os
import re
import sys
from typing import Any

try:
    import yaml
except ImportError:
    _repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
    import yaml
from pydantic import ValidationError as PydanticValidationError

from .._log import _log
from ..construct_config import harness_project
from .schema import WorkflowDef
from .validator import validate_workflow, ValidationResult
from operator_mcp.workflow.event_listener import get_trigger_registry


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_BUILTIN_DIR = os.path.join(os.path.dirname(__file__), "builtins")
_USER_DIR = os.path.expanduser("~/.construct/workflows")


# ---------------------------------------------------------------------------
# Single workflow loading
# ---------------------------------------------------------------------------

def _read_workflow_text(path: str) -> str:
    """Read workflow YAML with encoding fallback.

    utf-8 → cp949 → utf-8 with errors='replace'. CP949 covers Korean
    Windows files saved by older editors. The replace-fallback ensures
    we never hard-skip a workflow over a single bad byte.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        try:
            with open(path, "r", encoding="cp949") as f:
                text = f.read()
            _log(
                f"workflow_loader: '{os.path.basename(path)}' decoded as cp949 "
                "(not utf-8) — consider re-saving as utf-8"
            )
            return text
        except UnicodeDecodeError:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            _log(
                f"workflow_loader: '{os.path.basename(path)}' had undecodable "
                "bytes — using utf-8 with errors='replace'"
            )
            return text


def load_workflow_from_yaml(path: str) -> WorkflowDef:
    """Parse a YAML file into a WorkflowDef. Raises on parse errors."""
    # Pin UTF-8 explicitly so loading on Windows (default cp949 on Korean
    # locales) doesn't blow up on non-ASCII workflow YAML. Falls back to
    # cp949 (and ultimately utf-8 with errors='replace') for files saved
    # by older Korean Windows editors that emit legacy bytes.
    data = yaml.safe_load(_read_workflow_text(path))

    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML dict at root, got {type(data).__name__}")

    return WorkflowDef(**data)


def load_workflow_from_dict(data: dict[str, Any]) -> WorkflowDef:
    """Parse a dict (from JSON/YAML) into a WorkflowDef."""
    return WorkflowDef(**data)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

_REVISION_FILE_RE = re.compile(r"\.r\d+\.ya?ml$")


def _scan_directory(directory: str) -> dict[str, str]:
    """Scan a directory for .yaml/.yml files. Returns name → path.

    Skips revision artifact files (e.g. workflow.r3.yaml) — those are
    managed by Kumiho artifact persistence, not standalone workflows.
    """
    found: dict[str, str] = {}
    if not os.path.isdir(directory):
        return found

    for entry in os.listdir(directory):
        if entry.startswith(".") or entry.startswith("_"):
            continue
        if not entry.endswith((".yaml", ".yml")):
            continue
        if _REVISION_FILE_RE.search(entry):
            continue
        name = entry.rsplit(".", 1)[0]
        found[name] = os.path.join(directory, entry)

    return found


def discover_workflows(project_dir: str | None = None) -> dict[str, str]:
    """Discover all workflow files across builtin, user, and project dirs.

    Returns a dict of workflow_name → file_path.
    Later sources override earlier ones.
    """
    workflows: dict[str, str] = {}

    # 1. Built-ins
    workflows.update(_scan_directory(_BUILTIN_DIR))

    # 2. User directory
    workflows.update(_scan_directory(_USER_DIR))

    # 3. Project-local
    if project_dir:
        local_dir = os.path.join(project_dir, ".construct", "workflows")
        workflows.update(_scan_directory(local_dir))

    return workflows


def load_all_workflows(project_dir: str | None = None) -> dict[str, WorkflowDef]:
    """Load and parse all discovered workflows.

    Skips files with parse errors (logs warnings).
    """
    paths = discover_workflows(project_dir)
    loaded: dict[str, WorkflowDef] = {}

    for name, path in paths.items():
        try:
            wf = load_workflow_from_yaml(path)
            loaded[wf.name] = wf
        except (PydanticValidationError, ValueError, yaml.YAMLError) as exc:
            _log(f"workflow_loader: skipping '{name}' ({path}): {exc}")
        except Exception as exc:
            _log(f"workflow_loader: unexpected error loading '{name}': {exc}")

    # Rebuild trigger registry with freshly loaded workflows
    try:
        registry = get_trigger_registry()
        registry.rebuild(loaded)
    except Exception:
        pass  # Non-fatal — listener may not be active

    return loaded


def build_trigger_registry(workflows: dict[str, "WorkflowDef"] | None = None) -> int:
    """Build/rebuild the trigger registry from loaded workflows.

    Args:
        workflows: Pre-loaded workflows dict. If None, loads all workflows.

    Returns:
        Number of trigger rules registered.
    """
    if workflows is None:
        workflows = load_all_workflows()
    registry = get_trigger_registry()
    registry.rebuild(workflows)
    return registry.rule_count


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_workflow(name: str, project_dir: str | None = None) -> WorkflowDef | None:
    """Load a specific workflow by name."""
    paths = discover_workflows(project_dir)
    path = paths.get(name)
    if not path:
        return None
    try:
        return load_workflow_from_yaml(path)
    except Exception as exc:
        _log(f"workflow_loader: error loading '{name}': {exc}")
        return None


async def resolve_workflow(
    name: str,
    project_dir: str | None = None,
) -> tuple[WorkflowDef, str, str] | None:
    """Resolve a workflow by name. Kumiho is the source of truth.

    Returns (workflow_def, item_kref, revision_kref) on success. Both kref
    strings are empty ("") for built-in disk fallbacks — the caller treats
    empty kref as "no pinned revision" and renders runs by name-matching
    the current published workflow.

    Resolution order:
      1. Kumiho (published tag → latest tag fallback) — canonical source.
      2. Built-in disk fallback (operator/workflow/builtins/) — for testing
         ship-with-Construct workflows when Kumiho entry is absent.

    Fails hard if Kumiho is unavailable (no silent disk substitution for
    user/project workflows). Returns None only when the workflow does not
    exist in Kumiho and is not a built-in.
    """
    result = await _get_workflow_from_kumiho(name)
    if result:
        return result

    # Disk fallbacks — user workflows first (where the UI saves YAML), then
    # project-local, then built-ins. Returns empty krefs because these aren't
    # pinned to a Kumiho revision; the caller renders runs by name match.
    for source_name, directory in (
        ("user", _USER_DIR),
        ("project", os.path.join(project_dir, ".construct", "workflows") if project_dir else None),
        ("builtin", _BUILTIN_DIR),
    ):
        if not directory:
            continue
        path = _scan_directory(directory).get(name)
        if path:
            _log(f"workflow_loader: '{name}' not in Kumiho — using {source_name} disk copy {path}")
            return (load_workflow_from_yaml(path), "", "")

    return None


async def _get_workflow_from_kumiho(name: str) -> tuple[WorkflowDef, str, str] | None:
    """Load a workflow from Kumiho by revision + artifact.

    Picks the revision tagged 'published' (falls back to 'latest'), fetches
    its artifacts, and loads the YAML file directly from the artifact
    location. Hard-fails on Kumiho errors — the caller decides how to handle
    a missing SDK vs. a Kumiho lookup failure.
    """
    from ..operator_mcp import KUMIHO_SDK
    if not KUMIHO_SDK._available:
        raise RuntimeError(
            "workflow_loader: Kumiho SDK unavailable — cannot resolve workflow. "
            "Kumiho is the source of truth for workflows; nothing should run without it."
        )

    slug = name.lower().replace(" ", "-")
    items = await KUMIHO_SDK.list_items(f"{harness_project()}/Workflows")

    item_kref = None
    for item in items:
        item_name = item.get("item_name", item.get("name", ""))
        if item_name == slug or item_name == name:
            item_kref = item.get("kref", "")
            break

    if not item_kref:
        _log(f"workflow_loader: '{name}' not found in Kumiho Construct/Workflows")
        return None

    # Published tag wins; fall back to 'latest' if nothing is published.
    # get_latest_revision already implements this fallback.
    revision = await KUMIHO_SDK.get_latest_revision(item_kref, tag="published")
    if not revision:
        raise RuntimeError(
            f"workflow_loader: '{name}' has no published/latest revision in Kumiho "
            f"(item_kref={item_kref})"
        )

    revision_kref = revision.get("kref", "")
    if not revision_kref:
        raise RuntimeError(
            f"workflow_loader: Kumiho revision for '{name}' has no kref: {revision!r}"
        )

    artifacts = await KUMIHO_SDK.get_artifacts(revision_kref)
    if not artifacts:
        raise RuntimeError(
            f"workflow_loader: revision '{revision_kref}' for '{name}' has no artifacts"
        )

    # Pick the first YAML artifact. A workflow revision should carry exactly
    # one YAML file — if someone attaches multiple, take the first.
    yaml_location = None
    for art in artifacts:
        location = art.get("location", "")
        if location.endswith((".yaml", ".yml")):
            yaml_location = location
            break
    if not yaml_location:
        yaml_location = artifacts[0].get("location", "")

    if not yaml_location:
        raise RuntimeError(
            f"workflow_loader: no artifact location for revision '{revision_kref}'"
        )

    # Artifact location may be a file:// URL — strip the scheme.
    if yaml_location.startswith("file://"):
        yaml_location = yaml_location[len("file://"):]
    yaml_path = os.path.expanduser(yaml_location)
    if not os.path.isfile(yaml_path):
        raise RuntimeError(
            f"workflow_loader: Kumiho artifact path does not exist on disk: {yaml_path} "
            f"(revision {revision_kref})"
        )

    wf = load_workflow_from_yaml(yaml_path)
    tag_info = revision.get("tags") or revision.get("tag") or "?"
    _log(f"workflow_loader: loaded '{name}' from Kumiho rev={revision_kref} tags={tag_info} → {yaml_path}")
    return (wf, item_kref, revision_kref)


async def resolve_all_workflows(project_dir: str | None = None) -> dict[str, dict[str, Any]]:
    """Discover workflows from disk AND Kumiho.

    Returns {name: {"source": "disk"|"kumiho", ...}}.
    Disk workflows take precedence over Kumiho entries with the same name.
    """
    result: dict[str, dict[str, Any]] = {}

    # Disk workflows
    disk = discover_workflows(project_dir)
    for name, path in disk.items():
        result[name] = {"source": "disk", "path": path, "kref": None}

    # Kumiho workflows
    try:
        from ..operator_mcp import KUMIHO_SDK
        if KUMIHO_SDK._available:
            items = await KUMIHO_SDK.list_items(f"{harness_project()}/Workflows")
            for item in items:
                item_name = item.get("item_name", item.get("name", ""))
                if item_name and item_name not in result:
                    result[item_name] = {
                        "source": "kumiho",
                        "path": None,
                        "kref": item.get("kref", ""),
                    }
    except Exception as exc:
        _log(f"workflow_loader: Kumiho discovery failed: {exc}")

    return result


def save_workflow_yaml(wf: WorkflowDef, directory: str | None = None) -> str:
    """Save a WorkflowDef as YAML. Returns the file path.

    Defaults to user workflow directory (~/.construct/workflows/).
    """
    target_dir = directory or _USER_DIR
    os.makedirs(target_dir, exist_ok=True)

    filename = f"{wf.name}.yaml"
    path = os.path.join(target_dir, filename)

    data = wf.model_dump(mode="json", exclude_none=True)
    # Always include required fields
    data["name"] = wf.name
    data["steps"] = [s.model_dump(mode="json", exclude_none=True) for s in wf.steps]

    # Strip empty editor-format fields to reduce YAML noise
    _STRIP_IF_EMPTY = {"action", "agent_hints", "skills", "assign"}
    for step_data in data["steps"]:
        for key in _STRIP_IF_EMPTY:
            val = step_data.get(key)
            if val == "" or val == []:
                del step_data[key]

    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    _log(f"workflow_loader: saved '{wf.name}' → {path}")
    return path


def validate_workflow_file(path: str) -> dict[str, Any]:
    """Load and validate a workflow file. Returns validation result dict."""
    try:
        wf = load_workflow_from_yaml(path)
        vr = validate_workflow(wf)
        return {
            "file": path,
            "workflow_name": wf.name,
            **vr.to_dict(),
        }
    except (PydanticValidationError, ValueError, yaml.YAMLError) as exc:
        return {
            "file": path,
            "valid": False,
            "errors": [{"message": str(exc), "severity": "error"}],
            "warnings": [],
        }
