# CanonWorks Serial Pipeline

CanonWorks starts from the two working serial-production workflows and makes the
project-specific pieces explicit in Kumiho.

## Operator Flow

Use the Operator-facing CanonWorks tools first:

```text
canonworks_start
-> answer next_questions with canonworks_start
-> canonworks_preview
-> canonworks_commit
-> canonworks_run_episode
-> canonworks_sync_state
```

Once a project name or title is known, `canonworks_start` ensures the Kumiho
Project and canonical Space scaffold exist. It then collects story seed data
such as `title`, `premise`, `characters`, `relationships`, `timeline_events`,
`storylines`, `foreshadow_threads`, and `style_guide`. `canonworks_commit` then
calls the lower-level `canonworks_init` tool and creates:

- Kumiho project spaces for series, characters, relationships, timeline,
  roadmap, state, progress, episodes, patches, context packs, reports, and
  bundles
- core canon bundles such as main canon, production style, current snapshots,
  active storylines, active foreshadow, context packs, blocked episodes, and
  patch candidates
- initial canon items, revisions, markdown artifacts, and relationship edges
- a generated `project_config_yaml` artifact that the two workflows consume

`canonworks_init` remains available for debugging and advanced automation, but
normal operators should not need to hand-write or pass project config paths.

## Workflows

- `canonworks-serial-episode-factory`
  - Produces one production-ready episode per run.
  - Reads canon, style, volume, current state/progress, prior production
    episodes, relationship graph, storyline, and foreshadow bundles from
    Kumiho.
  - Emits a production-ready episode, locked context pack, canon patch
    candidate, or blocked draft.

- `canonworks-serial-canon-state-sync`
  - Runs after the episode factory.
  - Reads the production-ready episode plus its canon patch candidate.
  - Emits current character, relationship, timeline, storyline, and foreshadow
    snapshots for the next episode run.
  - Supports `target_episode_number`, `target_episode_kref`,
    `target_patch_kref`, and `bootstrap_mode` for backfill/rewrite/bootstrap
    runs.

## Project Config

Normally, `canonworks_run_episode` and `canonworks_sync_state` use the
`project_config_artifact_path` stored by `canonworks_commit`. Advanced users can
still pass that path as `project_config_yaml`, or pass inline YAML/JSON, when
calling workflows directly. See
`docs/reference/canonworks-project-config.example.yaml` for the generated shape.
Use `canon_project` as the top-level key; existing `story_project` configs are
still accepted for compatibility.

The config supplies:

- Kumiho project id, story slug, title, language, cadence
- episode/patch/context/state/progress/report spaces
- append-only and current snapshot bundle names
- canonical krefs for series bible, synopsis, characters, relationship map,
  timeline, roadmap, and current snapshots
- naming prefixes for episode item names, patch names, context packs, volume
  bundles, and blocked drafts
- genre modules, persona bindings, priority rules, audit rules, and external
  reference seed text

## Operating Loop

```text
canonworks-serial-episode-factory
→ production-ready episode + canon patch candidate + context pack
→ canonworks-serial-canon-state-sync
→ current snapshots and sync report
→ next canonworks-serial-episode-factory run
```

The workflows keep main canon conservative: episode generation writes a patch
candidate, while state sync writes current operational snapshots. Risky
relationship or major timeline deltas stay approval-gated unless the project
config and run inputs explicitly allow them.
