# CanonWorks

CanonWorks is the Construct-native story automation tool for long-form serial
fiction. It lives inside the Operator MCP surface and uses Kumiho as the canon
store: Project, Space, Item.kind, Revision, Artifact, Bundle, and revision
edges.

CanonWorks is not a separate app and it does not require the operator to hand
write a project config first. The operator-facing entrypoint is
`canonworks_start`: once a project/title is known, it creates the Kumiho Project
and canonical Space scaffold, then collects story seed answers, reports
readiness, asks the next questions, and previews the graph. `canonworks_commit`
then calls `canonworks_init`, creates the canon items, revisions, artifacts,
bundles, relationship edges, and stores the generated config path so
`canonworks_run_episode` and `canonworks_sync_state` can run without exposing
config plumbing to the operator.

`canonworks_init` remains the lower-level bootstrap tool. It creates the Kumiho
canon graph from story seed data, writes initial artifacts, creates relationship
edges, and returns the generated `project_config_yaml` plus the local config
artifact path.

## What It Creates

`canonworks_init` creates or reuses these Kumiho spaces under the target
project:

```text
<Project>/Config
<Project>/Series
<Project>/Characters
<Project>/Relationships
<Project>/Timeline
<Project>/Roadmaps
<Project>/Episodes
<Project>/Patches
<Project>/ContextPacks
<Project>/State
<Project>/Progress
<Project>/Reports
<Project>/Personas
<Project>/Bundles
```

It creates core bundles such as:

```text
<story_slug>-main-canon
<story_slug>-production-style
<story_slug>-production-episodes
<story_slug>-canon-patch-candidates
<story_slug>-current-character-states
<story_slug>-current-relationship-states
<story_slug>-current-timeline-progress
<story_slug>-current-storyline-progress
<story_slug>-current-foreshadow-progress
<story_slug>-state-sync-snapshots
<story_slug>-canon-state-sync-reports
<story_slug>-active-storylines
<story_slug>-active-foreshadow
<story_slug>-context-packs
<story_slug>-blocked-episodes
```

It writes initial canon items, revisions, and artifacts:

```text
series bible
canon synopsis
character index
relationship map
timeline
long-arc roadmap
production style guide
one character item per provided character
current character state snapshot
current relationship state snapshot
current timeline progress snapshot
current storyline progress snapshot
current foreshadow progress snapshot
canonworks project config
```

Relationship entries whose endpoints match character ids are also written as
Kumiho revision edges. If an endpoint does not match a known character id after
slug normalization, the tool returns a `relationship_edge_skipped` warning
instead of silently pretending an edge was created.

## Operator Flow

Use this flow when the operator says "start CanonWorks":

```text
canonworks_start
→ answer next_questions with canonworks_start
→ canonworks_preview
→ canonworks_commit
→ canonworks_run_episode
→ canonworks_sync_state
```

`canonworks_start` returns `session_id`, `draft`, `project_scaffold`,
`readiness`, `next_questions`, and `preview`. `project_scaffold.status` is
`ready` after the Kumiho project and base spaces are ensured. Keep calling it
with `session_id` and `answers` until `readiness.ready_to_commit` is true.

After `canonworks_commit`, the project config path is stored in CanonWorks
state. Prefer `canonworks_run_episode` and `canonworks_sync_state` over direct
workflow calls unless you are debugging the workflow itself.

## Low-Level Step 1: Initialize The Canon Graph

Call the Operator MCP tool `canonworks_init` with story seed data. This is the
only required manual setup step.

Minimum payload:

```json
{
  "title": "Glass City",
  "project": "GlassCity",
  "story_slug": "glass-city",
  "premise": "A serial about a city built from archived memories."
}
```

Recommended payload:

```json
{
  "title": "Glass City",
  "project": "GlassCity",
  "story_slug": "glass-city",
  "premise": "A serial about a city built from archived memories.",
  "synopsis": "Mira investigates memory crimes in a city where the dead can still edit public records.",
  "language": "ko-KR",
  "cadence": "web_serial",
  "target_length": "6000",
  "genre_modules": [
    "serialized-mystery",
    "relationship-drama",
    "long-arc-payoff"
  ],
  "themes": [
    "memory as infrastructure",
    "truth under operational pressure"
  ],
  "canon_guardrails": [
    "No character learns a hidden truth unless an episode artifact records the reveal.",
    "Major relationship shifts require a canon patch candidate first."
  ],
  "characters": [
    {
      "id": "mira",
      "display_name": "Mira",
      "role": "lead",
      "summary": "An investigator who can read archived memory deltas.",
      "traits": ["precise", "withholding", "obsessive"]
    },
    {
      "id": "jun",
      "display_name": "Jun",
      "role": "rival",
      "summary": "A systems auditor hiding a private archive.",
      "traits": ["calm", "strategic", "untrusted"]
    }
  ],
  "relationships": [
    {
      "from": "mira",
      "to": "jun",
      "edge_type": "RIVAL_OF",
      "label": "professional rivalry",
      "summary": "They need each other but both suspect the other is editing evidence."
    }
  ],
  "timeline_events": [
    {
      "position": "prelude",
      "summary": "The city archive accepts its first human memory backup."
    }
  ],
  "storylines": [
    {
      "id": "archive-murder",
      "summary": "Mira traces a murder committed by editing a victim's remembered day.",
      "goal": "Expose the archive's write path."
    }
  ],
  "foreshadow_threads": [
    {
      "id": "jun-private-archive",
      "summary": "Jun knows more about the archive internals than he admits.",
      "payoff_target": "volume-01-finale"
    }
  ],
  "style_guide": "Close third POV, high continuity pressure, terse technical metaphors.",
  "external_reference_seed": "serialized mystery continuity, character state tracking, clue fairness"
}
```

Important output fields:

```text
project_config_yaml
project_config_item_kref
project_config_revision_kref
project_config_artifact_path
created.spaces
created.bundles
created.items
created.revisions
created.artifacts
created.bundle_members
created.edges
created.warnings
next_workflows
```

For normal operation, use `project_config_artifact_path` in workflow inputs. Use
`project_config_yaml` only when inline YAML is more convenient.

## Step 2: Produce One Episode

Run the built-in workflow `canonworks-serial-episode-factory`.

Operator MCP payload:

```json
{
  "workflow": "canonworks-serial-episode-factory",
  "cwd": "G:\\git\\KumihoIO\\construct-os",
  "inputs": {
    "project_config_yaml": "<project_config_artifact_path from canonworks_init>",
    "target_length": "6000자",
    "episode_goal": "Establish the first archive crime and end on a concrete memory discrepancy.",
    "must_include": "Mira and Jun must meet in a professional conflict scene.",
    "avoid": "Do not reveal Jun's private archive yet.",
    "continuity_context": "This is the first production episode.",
    "pacing_mode": "balanced",
    "initial_episode_number": 1,
    "initial_volume": 1
  }
}
```

The workflow returns immediately with a `run_id`. Poll it with:

```json
{
  "run_id": "<run_id>",
  "include_outputs": true
}
```

Expected episode-factory outputs:

```text
production-ready episode revision
locked context pack artifact
canon patch candidate revision
volume bundle update
production episode bundle update
blocked draft item if the final audit blocks publication
```

The workflow does not directly rewrite main canon. It emits a canon patch
candidate and records the produced episode so the sync workflow can account for
state changes.

## Step 3: Sync Canon State

After an episode is production-ready, run
`canonworks-serial-canon-state-sync`.

Operator MCP payload:

```json
{
  "workflow": "canonworks-serial-canon-state-sync",
  "cwd": "G:\\git\\KumihoIO\\construct-os",
  "inputs": {
    "project_config_yaml": "<project_config_artifact_path from canonworks_init>",
    "apply_mode": "propose_only",
    "continuity_context": "Apply only safe operational state updates. Keep risky relationship and timeline changes approval-gated.",
    "review_focus": "Mira, Jun, first archive crime, private archive foreshadowing"
  }
}
```

For targeted backfill or rewrite accounting, supply one or more of:

```json
{
  "target_episode_number": "12",
  "target_episode_kref": "kref://<Project>/Episodes/ep-012.webnovel-episode?r=3",
  "target_patch_kref": "kref://<Project>/Patches/ep-012-canon-patch.canon-patch?r=1",
  "bootstrap_mode": "sequential_backfill"
}
```

Expected sync outputs:

```text
current character state snapshot
current relationship state snapshot
current timeline progress snapshot
current storyline progress snapshot
current foreshadow progress snapshot
canon state sync report
state snapshot bundle updates
```

After sync, the next episode-factory run reads the updated current bundles.

## Operating Loop

```text
canonworks_init
→ canonworks-serial-episode-factory
→ production-ready episode + canon patch candidate
→ canonworks-serial-canon-state-sync
→ current state/progress snapshots
→ next canonworks-serial-episode-factory run
```

## Project Config Contract

The generated config uses this top-level shape:

```yaml
canon_project:
  id: glass-city
  project: GlassCity
  title: Glass City
  language: ko-KR
  cadence: web_serial
  default_episode_length_chars: "6000"
  spaces:
    episodes: GlassCity/Episodes
    patches: GlassCity/Patches
    context_packs: GlassCity/ContextPacks
    state: GlassCity/State
    progress: GlassCity/Progress
    reports: GlassCity/Reports
    personas: GlassCity/Personas
  bundles:
    main_canon: glass-city-main-canon
    production_episodes: glass-city-production-episodes
    canon_patch_candidates: glass-city-canon-patch-candidates
    current_character_states: glass-city-current-character-states
  krefs:
    series_bible: kref://GlassCity/Series/main.series-bible
    relationship_map_artifact: kref://GlassCity/Relationships/main.relationship-map?r=1&a=RELATIONSHIP_MAP.md
```

`story_project` is still accepted by the workflows for compatibility, but new
CanonWorks configs should use `canon_project`.

## Re-Run Behavior

`canonworks_init` is conservative about existing Kumiho structure:

```text
existing spaces are reused
existing bundles are reused
existing items are reused
new revisions and artifacts are created for the seed docs/config
bundle membership calls are idempotent when Kumiho reports an existing member
relationship edges are created between the current character revisions from this run
```

This means a second `canonworks_init` run is a new bootstrap revision pass, not
a destructive reset. It does not delete old canon data and it does not rewrite
main canon in place.

The returned `created.items[*].created` and `created.bundles[*].created` flags
show whether each item or bundle was newly created or reused.

## Validation Commands

From `G:\git\KumihoIO\construct-os`:

```powershell
python -m pytest operator-mcp\tests\test_canonworks_tool.py operator-mcp\tests\test_builtin_workflows.py -q
python -m py_compile operator-mcp\operator_mcp\operator_mcp.py operator-mcp\operator_mcp\tool_handlers\canonworks.py
```

Validate installed workflows through the Operator MCP `validate_workflow` tool:

```json
{
  "workflow": "canonworks-serial-episode-factory",
  "cwd": "G:\\git\\KumihoIO\\construct-os"
}
```

```json
{
  "workflow": "canonworks-serial-canon-state-sync",
  "cwd": "G:\\git\\KumihoIO\\construct-os"
}
```

Check the installed Operator tool catalog:

```powershell
$py = "$env:USERPROFILE\.construct\operator_mcp\venv\Scripts\python.exe"
$env:PYTHONPATH = "$env:USERPROFILE\.construct"
@'
import asyncio
from operator_mcp.operator_mcp import list_tools

async def main():
    tools = await list_tools()
    print(any(t.name == "canonworks_init" for t in tools))

asyncio.run(main())
'@ | & $py -
```

Check the Construct service:

```powershell
$construct = "$env:USERPROFILE\.construct\bin\construct.exe"
& $construct service status
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:42617/health
```

## Design Boundary

CanonWorks owns the story-production structure, not the story itself. The
operator provides seed canon facts; CanonWorks records those facts into Kumiho,
then the workflows produce and reconcile episodes through revisions, artifacts,
bundles, and edges.

That boundary is important:

```text
Operator input creates initial canon data.
Project config routes workflows to the right Kumiho graph.
Episode Factory creates episode output and patch candidates.
Canon State Sync records state transitions for future runs.
Main canon remains conservative and approval-gated.
```
