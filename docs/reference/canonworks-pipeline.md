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
- first-class storyline, foreshadow-thread, and timeline-event items (populating
  the active storylines and active foreshadow bundles) plus their structural
  edges
- a published `canon-ontology` item and `CANON_ONTOLOGY.md` artifact
- a generated `project_config_yaml` artifact that the two workflows consume

`canonworks_init` remains available for debugging and advanced automation, but
normal operators should not need to hand-write or pass project config paths.

## Canon Ontology

CanonWorks types the canon graph with a controlled vocabulary. Character
relationship `edge_type` values are normalized against it (English and Korean
aliases, e.g. `rival` / `라이벌` → `RIVAL_OF`) and enriched with category /
symmetry / inverse metadata; unknown types are preserved but flagged
out-of-vocabulary rather than coerced. `canonworks_init` also emits structural
edges — `APPEARS_IN` (character → series bible), `INVOLVES` (storyline →
character), `FORESHADOWS` (foreshadow-thread → storyline), and `BELONGS_TO`
(timeline-event → timeline, reusing the Kumiho scope edge) — and publishes the
ontology as a `canon-ontology` item. Pass `create_inverse_edges: true` (through
`canonworks_start` / `canonworks_commit` / `canonworks_init`) to also create the
reverse edge for asymmetric types with a defined inverse. See
[`canonworks-ontology.md`](./canonworks-ontology.md) for the full vocabulary and
edge semantics.

Both builtin workflows traverse this vocabulary when they assemble context
packs. Their `kumiho_context` steps list every relationship and structural edge
type in `traversal.edge_types` (an exact-match filter), seed the `canon-ontology`
item on the canon-facing context step, and pass the vocabulary into the
relationship-bearing prompts so proposed edges name canonical types and flag
out-of-vocabulary ones. See
[Consumed by the Workflows](./canonworks-ontology.md#consumed-by-the-workflows)
for the per-step wiring and the drift-guard test. This work bumped
`canonworks-serial-episode-factory` to `workflow_version` `2.6` and
`canonworks-serial-canon-state-sync` to `workflow_version` `1.1`; the edits are
additive apart from the version strings — step ids, order, and entity kinds are
unchanged.

## Workflows

- `canonworks-serial-episode-factory`
  - Produces one production-ready episode per run.
  - Reads canon, style, volume, current state/progress, prior production
    episodes, relationship graph, storyline, and foreshadow bundles from
    Kumiho.
  - Traverses the full canon-ontology edge vocabulary (character relationships
    plus structural edges) when assembling its context pack.
  - Emits a production-ready episode, locked context pack, canon patch
    candidate, or blocked draft.

- `canonworks-serial-canon-state-sync`
  - Runs after the episode factory.
  - Reads the production-ready episode plus its canon patch candidate.
  - Traverses the same ontology edge vocabulary across both of its context
    steps.
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
  timeline, roadmap, current snapshots, and the canon ontology item
- a names-only `ontology` block (version, ontology item kref, character edge
  types, structural edge types)
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
