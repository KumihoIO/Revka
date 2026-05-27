# Kumiho SDK

Use this skill when working with Kumiho Python SDK, REST API, MCP tools, krefs,
projects, spaces, items, revisions, artifacts, edges, bundles, graph traversal,
memory tools, or Construct/FoxClaw Kumiho integration.

## Operating Rule

Kumiho is a graph-native source of truth, not a fuzzy scratchpad.

- Use semantic recall to find candidates.
- Use exact krefs to load or mutate the selected item/revision.
- Preserve provenance with source krefs, revision krefs, artifact krefs, and edges.
- Keep revision metadata small. Store large content in artifacts.
- For workflow/runtime behavior, verify the installed Operator MCP sidecar under
  `~/.construct/operator_mcp`, not only the repository source.

## Core Model

```text
Project
  Space
    Item
      Revision
        Artifact
        Edge
Bundle
```

- Project: top-level namespace.
- Space: hierarchical folder inside a project.
- Item: versioned asset identity with a kind.
- Revision: immutable numbered snapshot of an item.
- Artifact: reference to external storage. Kumiho stores the location, not bytes.
- Edge: directed relation between revisions.
- Bundle: audited item set.

## Krefs

```text
kref://project/space/item.kind
kref://project/space/item.kind?r=3
kref://project/space/item.kind?r=3&a=SKILL.md
kref://project/space/item.kind?t=published
```

Rules:

- Item name is composed as `{item_name}.{kind}`.
- Revisions are 1-indexed per item.
- `latest` is automatic. Tags such as `published`, `current`, or `active` are
  conventions and must be applied explicitly.
- If a workflow needs reproducibility, lock exact revision krefs.

## Python SDK Basics

```python
import kumiho

kumiho.auto_configure_from_discovery()

project = kumiho.create_project("MyProject", "Description")
space = project.create_space("Assets")
item = space.create_item("hero", "model")
rev = item.create_revision(metadata={"artist": "neo"})
artifact = rev.create_artifact("mesh.fbx", "file:///path/to/mesh.fbx")
rev.tag("published")
```

The safe publication order is:

```text
create_revision -> create_artifact -> tag_revision("published")
```

Creating artifacts after publication can fail in some deployments.

## REST Surface

Use REST when the SDK wrapper is unavailable or when an integration already has
HTTP plumbing.

- Projects: `GET /projects`, `POST /projects`
- Spaces: `GET /spaces`, `POST /spaces`, `GET /spaces/by-path`
- Items: `GET /items`, `POST /items`, `GET /items/by-kref`
- Revisions: `POST /revisions`, `GET /revisions/by-kref`,
  `GET /revisions/latest`, `POST /revisions/batch`
- Tags: `POST /revisions/tags?kref=...`, `DELETE /revisions/tags?kref=...&tag=...`
- Artifacts: `POST /artifacts`, `GET /artifacts?revision_kref=...`,
  `GET /artifacts/by-kref`
- Edges: `POST /edges`, `GET /edges?kref=...&direction=0|1|2&edge_type=...`
- Graph: `GET /graph/dependencies`, `GET /graph/impact`, `GET /graph/path`
- Bundles: `POST /bundles`, `GET /bundles/by-kref`,
  `GET /bundles/members`, `POST /bundles/members/add`,
  `POST /bundles/members/remove`

REST direction encoding:

```text
0 = outgoing
1 = incoming
2 = both
```

## MCP Tool Selection

For memory lifecycle:

- `kumiho_memory_engage`: recall relevant context before responding. Use at
  most once per response.
- `kumiho_memory_reflect`: capture durable facts, decisions, preferences, or
  summaries after responding.
- `kumiho_memory_store`: one-shot persist.
- `kumiho_memory_consolidate`: end-of-session summary.

For exact Kumiho graph operations:

- Search: `kumiho_search_items`, `kumiho_fulltext_search`
- Item/revision: `kumiho_get_item`, `kumiho_get_revision`,
  `kumiho_get_revision_by_tag`, `kumiho_batch_get_revisions`
- Artifacts: `kumiho_get_artifacts`, `kumiho_get_artifact`,
  `kumiho_get_artifacts_by_location`
- Edges/graph: `kumiho_get_edges`, `kumiho_get_dependencies`,
  `kumiho_get_dependents`, `kumiho_find_path`, `kumiho_analyze_impact`
- Mutation: `kumiho_create_item`, `kumiho_create_revision`,
  `kumiho_create_artifact`, `kumiho_create_edge`, `kumiho_tag_revision`,
  `kumiho_untag_revision`

Prefer high-level memory tools for conversation memory. Use low-level graph
tools only when exact entity loading, provenance, revision locking, or mutation
is required.

## Construct Workflow Runtime Note

Construct workflow Kumiho steps run through the installed Operator MCP sidecar.
Check the installed path when validating local behavior:

```powershell
$py = "$env:USERPROFILE\.construct\operator_mcp\venv\Scripts\python.exe"
$env:PYTHONPATH = "$env:USERPROFILE\.construct"
@'
from operator_mcp.operator_mcp import KUMIHO_SDK
if hasattr(KUMIHO_SDK, "_lazy_init"):
    KUMIHO_SDK._lazy_init()
print(type(KUMIHO_SDK).__module__ + "." + type(KUMIHO_SDK).__name__)
print(getattr(KUMIHO_SDK, "_available", None))
'@ | & $py -
```

If `_available` is false, do not fabricate workflow context. Fail clearly.

## Bundle Usage

Bundle membership is item-level. Reproducibility comes from a revision manifest.

For a context compiler or canon workflow:

1. Resolve bundle members.
2. Select each member's revision by exact kref, tag preference, or latest.
3. Traverse revision edges.
4. Rank/filter candidates.
5. Return exact revision krefs in a locked manifest.

## Safe Mutation Pattern

Read-only steps should not mutate canon.

- `kumiho_context`: reads graph and returns a locked context pack.
- `kumiho_bundle_update`: mutates bundle membership only.
- `kumiho_patch_apply`: applies approved canon changes, creates revisions,
  updates tags, creates edges, and writes apply reports.

For high-risk canon mutation:

- Require approval unless policy explicitly permits auto-apply.
- Support dry-run.
- Validate previous revision krefs are still current.
- Save provenance metadata: source patch, source episode, evidence locator,
  approved_by, applied_at.
- Prefer transactions. If unavailable, emit a compensation/rollback plan.

## Operational Gotchas

- Metadata values must be strings.
- Keep metadata small; large payloads can hit transport limits.
- Artifacts are references, not storage.
- `published` is not automatic.
- Batch operations may be partial. Handle `not_found`.
- Force delete may leave orphan graph nodes in some deployments. Prefer
  deprecation unless a hard delete is required.
- Do not mix `CognitiveMemory/` user memories with `FoxClaw/` or Construct
  operational data.
