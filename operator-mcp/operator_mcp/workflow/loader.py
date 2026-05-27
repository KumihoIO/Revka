"""Workflow loader — discover and parse YAML workflow definitions.

Loads from:
  1. Built-in workflows shipped with Construct (operator/workflow/builtins/)
  2. User workflows in ~/.construct/workflows/
  3. Project-local workflows in <cwd>/.construct/workflows/

Later sources override earlier ones (project > user > builtin).
"""
from __future__ import annotations

import ast
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
from .schema import StepDef, StepType, WorkflowDef
from .validator import validate_workflow, ValidationResult
from operator_mcp.workflow.event_listener import get_trigger_registry


# ---------------------------------------------------------------------------
# depends_on inference from ${step.field} interpolations
# ---------------------------------------------------------------------------
#
# Mirror of the frontend edge inference in
# web/src/construct/components/workflows/yamlSync.ts (search "Inferred
# dependency edges from `${step_id.<field>}`"). The DAG visualizer already
# infers edges from interpolation references; the runtime wave scheduler
# (executor.py wave loop) reads only step.depends_on, so without this pass
# a YAML that expresses deps purely via `${X.output}` interpolation fans
# every step out into Wave 1 and downstream steps see empty inputs.
#
# Cheap and pure: scans declared text-bearing fields once at load time,
# adds inferred deps in place. No-ops for refs to non-existent steps
# (validator catches those separately) and self-references.

_STEP_REF_RE = re.compile(r"\$\{(?!\{)([a-zA-Z_][a-zA-Z0-9_-]*)(?:\.[a-zA-Z_][a-zA-Z0-9_.-]*)?\}")
_EXPR_TEMPLATE_RE = re.compile(r"\$\{\{\s*(.*?)\s*\}\}", re.DOTALL)
_EXPR_ROOT_FALLBACK_RE = re.compile(r"(?<![A-Za-z0-9_.])([A-Za-z_][A-Za-z0-9_-]*)\s*\.")

# Namespaces that are NOT step references — workflow-scope, runtime-scope,
# or loop-iteration-scope. Mirrors the executor's interpolate() resolver
# (executor.py ~L83) and the frontend's NON_STEP_REF_IDS.
_NON_STEP_NAMESPACES = frozenset({
    "inputs", "input",          # workflow input parameters
    "trigger",                  # event trigger context
    "env",                      # environment variables
    "context",                  # workflow context (frontend convention)
    "loop",                     # goto loop iteration
    "for_each",                 # for_each iteration scope
    "previous",                 # for_each previous-iteration step results
    "rejection",                # human_approval rejection feedback
    "run_id",                   # workflow run id
    "outputs",                  # workflow-level outputs (frontend convention)
})


def _extract_expr_ref_namespaces(expr: str) -> set[str]:
    """Return root namespaces from attribute chains in a ${{ ... }} expression.

    Regex token scans see every dotted segment (``output_data.metadata``) as a
    possible step id. Parsing the expression lets us keep only the root object
    (``arc_loader`` in ``arc_loader.output_data.metadata.end``).
    """
    if not isinstance(expr, str) or not expr:
        return set()
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return {m.group(1) for m in _EXPR_ROOT_FALLBACK_RE.finditer(expr)}

    refs: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        root: ast.AST = node
        while isinstance(root, ast.Attribute):
            root = root.value
        if isinstance(root, ast.Name):
            refs.add(root.id)
    return refs


def _scan_step_for_refs(step: StepDef) -> set[str]:
    """Return the set of step-namespace identifiers referenced via
    ``${X}`` / ``${X.field}`` in this step's text-bearing config fields.

    Does not filter against the workflow's known step IDs — caller is
    responsible for intersecting with valid step IDs. Filters out the
    workflow-scope / runtime-scope namespaces in ``_NON_STEP_NAMESPACES``.
    """
    refs: set[str] = set()

    def _scan_text(text: Any) -> None:
        if not isinstance(text, str) or not text:
            return
        for m in _STEP_REF_RE.finditer(text):
            ns = m.group(1)
            if ns in _NON_STEP_NAMESPACES:
                continue
            refs.add(ns)
        for m in _EXPR_TEMPLATE_RE.finditer(text):
            for ns in _extract_expr_ref_namespaces(m.group(1)):
                if ns in _NON_STEP_NAMESPACES:
                    continue
                refs.add(ns)

    def _scan_value(value: Any) -> None:
        # Recurse into dict values and list items so dict/list-shaped
        # interpolatable fields (python.args, output.entity_metadata,
        # email.cc/bcc, map_reduce.splits) get the same treatment as
        # plain strings.
        if isinstance(value, str):
            _scan_text(value)
        elif isinstance(value, dict):
            for v in value.values():
                _scan_value(v)
        elif isinstance(value, list):
            for v in value:
                _scan_value(v)

    # agent
    if step.agent is not None:
        _scan_text(step.agent.prompt)
    # shell
    if step.shell is not None:
        _scan_text(step.shell.command)
    # python — code, script (path may include ${...}), and args (dict)
    if step.python is not None:
        _scan_text(step.python.code)
        _scan_text(step.python.script)
        _scan_value(step.python.args)
    # compute — output expressions can reference upstream step data.
    if step.compute is not None:
        _scan_value(step.compute.outputs)
    # email
    if step.email is not None:
        _scan_value(step.email.to)
        _scan_text(step.email.subject)
        _scan_text(step.email.body)
        _scan_text(step.email.body_html)
        _scan_text(step.email.from_address)
        _scan_value(step.email.cc)
        _scan_value(step.email.bcc)
        _scan_text(step.email.reply_to)
        _scan_text(step.email.track_kref)
    # conditional — branch conditions and (optional) value expressions are
    # interpolated. Both surfaces can reference upstream step results, so
    # both feed the depends_on inference pass.
    if step.conditional is not None:
        for branch in step.conditional.branches:
            _scan_text(branch.condition)
            _scan_text(branch.value)
    # goto
    if step.goto is not None:
        _scan_text(step.goto.condition)
    # human_approval / human_input / notify — channel_id may be templated
    # off a prior step (e.g. dynamic Discord channel pick).
    if step.human_approval is not None:
        _scan_text(step.human_approval.message)
        _scan_text(step.human_approval.channel_id)
    if step.human_input is not None:
        _scan_text(step.human_input.message)
    if step.notify is not None:
        _scan_text(step.notify.title)
        _scan_text(step.notify.message)
        _scan_text(step.notify.channel_id)
    # for_each — range expression and explicit items list both interpolate
    # at executor.py:1241 / :1272. Without scanning these, a dynamic range
    # like "1..${count.output}" fans count + for_each into the same wave.
    if step.for_each is not None:
        _scan_text(step.for_each.range)
        _scan_value(step.for_each.items)
    # output — template + entity_name + entity_metadata values
    if step.output is not None:
        _scan_text(step.output.template)
        _scan_text(step.output.entity_name)
        _scan_value(step.output.entity_metadata)
    # a2a
    if step.a2a is not None:
        _scan_text(step.a2a.url)
        _scan_text(step.a2a.message)
    # resolve — name_pattern / space may template off prior steps
    if step.resolve is not None:
        _scan_text(step.resolve.name_pattern)
        _scan_text(step.resolve.space)
    # kumiho_context — seed krefs/queries, ranking query, and filters may
    # template off upstream resolve/compute outputs.
    if step.kumiho is not None:
        _scan_value(step.kumiho.model_dump(mode="python"))
    # handoff
    if step.handoff is not None:
        _scan_text(step.handoff.reason)
        _scan_text(step.handoff.task)
    # map_reduce — task + splits list
    if step.map_reduce is not None:
        _scan_text(step.map_reduce.task)
        _scan_value(step.map_reduce.splits)
    # supervisor
    if step.supervisor is not None:
        _scan_text(step.supervisor.task)
    # group_chat
    if step.group_chat is not None:
        _scan_text(step.group_chat.topic)
    # tag / deprecate — item_kref typically templated from a prior step
    if step.tag_step is not None:
        _scan_text(step.tag_step.item_kref)
    if step.deprecate_step is not None:
        _scan_text(step.deprecate_step.item_kref)

    return refs


def _infer_depends_on(wf: WorkflowDef) -> None:
    """Augment ``step.depends_on`` for every step in ``wf`` based on
    ``${other_step.field}`` interpolation references. Mutates in place.

    Idempotent — running twice produces the same result.

    Safe for unknown-step references: ignored here, the validator catches
    them separately as missing-reference errors so the user still gets a
    clear failure mode.

    Parallel sub-steps: the validator forbids cross-group ``depends_on``
    on parallel children (ambiguous ordering — the parent should gate the
    block). When a child references an upstream the parallel parent
    already depends on, ordering is already satisfied transitively; emit
    no inferred edge rather than creating a validator-rejected one.
    """
    known_ids = {s.id for s in wf.steps}
    alias_to_id = {
        sid.replace("-", "_"): sid
        for sid in known_ids
        if "-" in sid and sid.replace("-", "_") not in known_ids
    }

    # Map child step id -> (parallel parent id, parent's depends_on set).
    # Used to suppress inferences that would violate the parallel-group rule.
    parent_of: dict[str, tuple[str, set[str]]] = {}
    for s in wf.steps:
        if s.type == StepType.PARALLEL and s.parallel:
            parent_deps = set(s.depends_on)
            for child_id in s.parallel.steps:
                parent_of[child_id] = (s.id, parent_deps)

    for step in wf.steps:
        refs = _scan_step_for_refs(step)
        if not refs:
            continue
        existing = list(step.depends_on)
        existing_set = set(existing)
        added: list[str] = []
        parent_info = parent_of.get(step.id)
        for raw_ref in refs:
            ref = alias_to_id.get(raw_ref, raw_ref)
            if ref == step.id:
                continue  # self-reference — skip silently
            if ref not in known_ids:
                continue  # validator surfaces this as a missing-step error
            if ref in existing_set:
                continue
            # Suppress inferences on parallel children that would create
            # cross-group depends_on (validator rejects these). Ordering is
            # already enforced via the parallel parent.
            if parent_info is not None:
                parent_id, parent_deps = parent_info
                if ref != parent_id and ref in parent_deps:
                    continue
            existing.append(ref)
            existing_set.add(ref)
            added.append(ref)
        if added:
            step.depends_on = existing
            _log(
                f"workflow_loader: inferred depends_on for '{step.id}' "
                f"from interpolation: +{added}"
            )


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

    wf = WorkflowDef(**data)
    _infer_depends_on(wf)
    return wf


def load_workflow_from_text(text: str, source: str = "<inline>") -> WorkflowDef:
    """Parse workflow YAML text into a WorkflowDef. Raises on parse errors."""
    data = yaml.safe_load(text)

    if not isinstance(data, dict):
        raise ValueError(
            f"Expected YAML dict at root in {source}, got {type(data).__name__}"
        )

    wf = WorkflowDef(**data)
    _infer_depends_on(wf)
    return wf


def load_workflow_from_dict(data: dict[str, Any]) -> WorkflowDef:
    """Parse a dict (from JSON/YAML) into a WorkflowDef."""
    wf = WorkflowDef(**data)
    _infer_depends_on(wf)
    return wf


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


def _is_workflow_definition_item(item: dict[str, Any]) -> bool:
    """Return true for Kumiho items that represent workflow definitions."""
    item_kref = item.get("kref", "")
    if not isinstance(item_kref, str) or not item_kref:
        return False
    return item_kref.split("?", 1)[0].endswith(".workflow")


async def load_all_workflows_with_kumiho(
    project_dir: str | None = None,
) -> dict[str, WorkflowDef]:
    """Load all disk workflows, then overlay current Kumiho revisions.

    Plain disk discovery intentionally skips ``*.rN.yaml`` revision artifact
    files. That is correct for editable local workflow files, but the event
    trigger registry must reflect the current workflow catalogue because a
    trigger can be introduced in a saved revision while the base disk copy
    remains stale.
    """
    loaded = load_all_workflows(project_dir)

    try:
        from ..operator_mcp import KUMIHO_SDK
        if not KUMIHO_SDK._available:
            return loaded

        items = await KUMIHO_SDK.list_items(f"{harness_project()}/Workflows")
        kumiho_loaded = 0
        for item in items:
            if not _is_workflow_definition_item(item):
                continue
            item_name = item.get("item_name", item.get("name", ""))
            try:
                result = await _load_workflow_item_from_kumiho(item)
            except Exception as exc:
                _log(f"workflow_loader: skipping Kumiho workflow '{item_name}': {exc}")
                continue
            if not result:
                continue
            wf, _item_kref, _revision_kref = result
            loaded[wf.name] = wf
            kumiho_loaded += 1

        if kumiho_loaded:
            _log(
                "workflow_loader: overlaid "
                f"{kumiho_loaded} current Kumiho workflow(s)"
            )
    except Exception as exc:
        _log(f"workflow_loader: Kumiho trigger registry overlay failed: {exc}")

    try:
        registry = get_trigger_registry()
        registry.rebuild(loaded)
    except Exception as exc:
        _log(f"workflow_loader: trigger registry rebuild failed: {exc}")

    return loaded


async def build_trigger_registry_async(
    workflows: dict[str, "WorkflowDef"] | None = None,
    project_dir: str | None = None,
) -> int:
    """Build/rebuild the trigger registry, including current Kumiho workflows."""
    if workflows is None:
        workflows = await load_all_workflows_with_kumiho(project_dir)
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
    the current workflow.

    Resolution order:
      1. Kumiho latest revision — canonical source.
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

    Picks the latest revision, fetches its artifacts, and loads the YAML file
    directly from the artifact location. Hard-fails on Kumiho errors — the
    caller decides how to handle a missing SDK vs. a Kumiho lookup failure.
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
    workflow_item: dict[str, Any] | None = None
    for item in items:
        item_name = item.get("item_name", item.get("name", ""))
        if item_name == slug or item_name == name:
            item_kref = item.get("kref", "")
            workflow_item = item
            break

    if not item_kref:
        _log(f"workflow_loader: '{name}' not found in Kumiho Construct/Workflows")
        return None

    return await _load_workflow_item_from_kumiho(workflow_item, expected_name=name)


async def _load_workflow_item_from_kumiho(
    item: dict[str, Any],
    expected_name: str | None = None,
) -> tuple[WorkflowDef, str, str] | None:
    """Load one Kumiho workflow item from its latest YAML definition.

    Prefer the local YAML artifact when it exists because that preserves the
    exact authored file. If the artifact points at another machine, fall back
    to the revision metadata definition instead of dropping the workflow from
    runtime discovery.
    """
    from ..operator_mcp import KUMIHO_SDK

    item_name = item.get("item_name", item.get("name", ""))
    display_name = expected_name or item_name or "<unknown>"
    item_kref = item.get("kref", "")
    if not item_kref:
        _log(f"workflow_loader: Kumiho workflow '{display_name}' has no item kref")
        return None

    # Workflow edits create a new revision artifact before any publish-style
    # tag is applied. When a tag such as "published" is immutable or otherwise
    # fails to move, the newest usable workflow is still the "latest" revision.
    revision = await KUMIHO_SDK.get_latest_revision(item_kref, tag="latest")
    if not revision:
        raise RuntimeError(
            f"workflow_loader: '{display_name}' has no latest revision in Kumiho "
            f"(item_kref={item_kref})"
        )

    revision_kref = revision.get("kref", "")
    if not revision_kref:
        raise RuntimeError(
            f"workflow_loader: Kumiho revision for '{display_name}' has no kref: {revision!r}"
        )

    artifacts = await KUMIHO_SDK.get_artifacts(revision_kref)

    # Pick the first YAML artifact. A workflow revision should carry exactly
    # one YAML file. If someone attaches multiple, take the first. If the
    # artifact path points at another host, we still keep the workflow by using
    # the inline revision definition below.
    yaml_location = None
    for art in artifacts:
        location = art.get("location", "")
        if location.endswith((".yaml", ".yml")):
            yaml_location = location
            break
    if not yaml_location and artifacts:
        yaml_location = artifacts[0].get("location", "")

    tag_info = revision.get("tags") or revision.get("tag") or "?"

    if yaml_location:
        # Artifact location may be a file:// URL — strip the scheme.
        yaml_path = (
            yaml_location[len("file://"):]
            if yaml_location.startswith("file://")
            else yaml_location
        )
        yaml_path = os.path.expanduser(yaml_path)
        if os.path.isfile(yaml_path):
            wf = load_workflow_from_yaml(yaml_path)
            _log(
                f"workflow_loader: loaded '{wf.name}' from Kumiho rev={revision_kref} "
                f"tags={tag_info} → {yaml_path}"
            )
            return (wf, item_kref, revision_kref)

        _log(
            "workflow_loader: Kumiho workflow artifact is not local; "
            f"using revision metadata for '{display_name}' "
            f"(path={yaml_path}, revision={revision_kref})"
        )
    else:
        _log(
            f"workflow_loader: revision '{revision_kref}' for '{display_name}' "
            "has no artifact location; using revision metadata"
        )

    metadata = revision.get("metadata", {}) or {}
    for key in ("definition", "workflow_yaml", "content", "yaml", "body"):
        yaml_text = metadata.get(key)
        if isinstance(yaml_text, str) and yaml_text.strip():
            wf = load_workflow_from_text(
                yaml_text,
                source=f"Kumiho revision metadata {revision_kref}.{key}",
            )
            _log(
                f"workflow_loader: loaded '{wf.name}' from Kumiho rev={revision_kref} "
                f"tags={tag_info} via metadata.{key}"
            )
            return (wf, item_kref, revision_kref)

    detail = (
        f"artifact path does not exist on disk: {yaml_location}"
        if yaml_location
        else "revision has no artifacts"
    )
    raise RuntimeError(
        f"workflow_loader: cannot load Kumiho workflow '{display_name}': {detail} "
        "and revision metadata has no workflow definition"
    )


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
