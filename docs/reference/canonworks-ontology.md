# CanonWorks Canon Ontology Reference

CanonWorks bootstraps a Kumiho canon graph of series, characters, relationships,
timeline, and roadmap items. The **canon ontology** is the controlled-vocabulary
layer on top of that graph: it names the Item kinds CanonWorks creates, types
the character-relationship edges with category / symmetry / inverse semantics,
and defines the structural edges that tie narrative entities together.

The ontology lives in `operator-mcp/operator_mcp/canon_ontology.py` (pure
standard library, no validation framework — it is a vocabulary the CanonWorks
tools and downstream workflows reason against). `canonworks_init` publishes it
into the canon graph as a `canon-ontology` item with a `CANON_ONTOLOGY.md`
artifact, and records a names-only summary in the generated project config.

The current ontology version is `1` (`ONTOLOGY_VERSION = "1"`).

This layer builds directly on the kumiho-memory edge semantics documented in
[`../contributing/kumiho-memory-integration.md`](../contributing/kumiho-memory-integration.md)
(`DERIVED_FROM`, `DEPENDS_ON`, `REFERENCED`, `CONTAINS`, `CREATED_FROM`,
`BELONGS_TO`). CanonWorks does not replace those provenance edges; it adds a
narrative-domain vocabulary beside them and reuses `BELONGS_TO` rather than
inventing a synonym for scope.

## Entity Kinds

Every Item CanonWorks creates carries a canonical `kind`. The registry records
each kind's home space and the structural edges it may source or target.

| Kind | Label | Space | Description |
| --- | --- | --- | --- |
| `series-bible` | Series Bible | series | Top-level canon promise, themes, and guardrails for the serial. |
| `series-synopsis` | Series Synopsis | series | Canonical rolling synopsis of the series. |
| `character-index` | Character Index | characters | Roster index of all canonical characters. |
| `relationship-map` | Relationship Map | relationships | Human-readable rollup of character relationship edges. |
| `timeline` | Timeline | timeline | Canonical timeline of the series. |
| `series-roadmap` | Series Roadmap | roadmaps | Long-arc roadmap rollup of storylines and foreshadow threads. |
| `style-guide` | Production Style Guide | series | Prose, POV, pacing, and platform style rules. |
| `character` | Character | characters | A canonical character entity. |
| `storyline` | Storyline | roadmaps | A long-arc storyline entity. |
| `foreshadow-thread` | Foreshadow Thread | roadmaps | A planted foreshadow thread with a payoff target. |
| `timeline-event` | Timeline Event | timeline | A canonical timeline event anchored to the series timeline. |
| `canon-ontology` | Canon Ontology | canon_rules | The published CanonWorks ontology reference for this project. |
| `character-state` | Character State Snapshot | state | Current per-character state snapshot. |
| `relationship-state` | Relationship State Snapshot | state | Current relationship state snapshot. |
| `timeline-progress` | Timeline Progress Snapshot | progress | Current timeline progress snapshot. |
| `storyline-progress` | Storyline Progress Snapshot | progress | Current storyline progress snapshot. |
| `foreshadow-progress` | Foreshadow Progress Snapshot | progress | Current foreshadow progress snapshot. |
| `canonworks-config` | CanonWorks Project Config | config | Generated project config routing the CanonWorks workflows. |
| `webnovel-episode` | Webnovel Episode | episodes | A produced serial episode revision (episode factory output). |
| `canon-patch` | Canon Patch Candidate | patches | A propose-only canon patch candidate (workflow output). |
| `context-pack` | Context Pack | context_packs | A locked context pack assembled for an episode (workflow output). |

The `storyline`, `foreshadow-thread`, and `timeline-event` kinds are first-class
items as of ontology version `1`, so other items can point at them with edges.
`canonworks_init` creates the storyline and foreshadow-thread items in the
Roadmaps space and adds them to the `active_storylines` / `active_foreshadow`
bundles, and creates the timeline-event items in the Timeline space. `ROADMAP.md`
/ `TIMELINE.md` remain the human-readable rollups; the items are the graph-native
representation.

## Character Relationship Vocabulary

Character-to-character relationship edges are typed against this controlled
vocabulary. Each type carries a `category`, a `symmetric` flag, and an optional
`inverse`:

- **Symmetric** types read the same in both directions (`RIVAL_OF`,
  `SIBLING_OF`), so they never carry an inverse and never get a duplicated
  reverse edge.
- **Asymmetric** types are directional. Some name their `inverse`
  (`MENTOR_OF` ↔ `MENTEE_OF`); others are directional with no defined inverse
  (`LOVES`, `PROTECTS`, `BETRAYED`, `OWES_DEBT_TO`, `KNOWS_SECRET_OF`).

`RELATED_TO` is the fallback used when a relationship declares no type.

| Edge Type | Category | Symmetric | Inverse | Description |
| --- | --- | --- | --- | --- |
| `RELATED_TO` | social | yes | — | Generic fallback relationship when no type is declared. |
| `ALLY_OF` | social | yes | — | Mutual allies pursuing aligned goals. |
| `FRIEND_OF` | social | yes | — | Personal friendship. |
| `CONFIDANT_OF` | social | yes | — | Trusted confidant who shares private matters. |
| `RIVAL_OF` | conflict | yes | — | Competitive rivalry between near-equals. |
| `ENEMY_OF` | conflict | yes | — | Declared hostility or opposition. |
| `ROMANTIC_WITH` | romance | yes | — | Mutual romantic involvement. |
| `LOVES` | romance | no | — | One-directional love, possibly unrequited. |
| `MENTOR_OF` | knowledge | no | `MENTEE_OF` | Teaches or guides the target. |
| `MENTEE_OF` | knowledge | no | `MENTOR_OF` | Is taught or guided by the target. |
| `PARENT_OF` | family | no | `CHILD_OF` | Parent of the target. |
| `CHILD_OF` | family | no | `PARENT_OF` | Child of the target. |
| `SIBLING_OF` | family | yes | — | Shares a sibling bond with the target. |
| `SPOUSE_OF` | family | yes | — | Married to the target. |
| `FAMILY_OF` | family | yes | — | Belongs to the same family as the target. |
| `GUARDIAN_OF` | family | no | `WARD_OF` | Legal or protective guardian of the target. |
| `WARD_OF` | family | no | `GUARDIAN_OF` | Is under the guardianship of the target. |
| `COMMANDS` | organization | no | `SERVES` | Holds command authority over the target. |
| `SERVES` | organization | no | `COMMANDS` | Serves under the authority of the target. |
| `PROTECTS` | social | no | — | Actively protects the target. |
| `BETRAYED` | conflict | no | — | Has betrayed the target. |
| `OWES_DEBT_TO` | social | no | — | Owes a debt or obligation to the target. |
| `KNOWS_SECRET_OF` | knowledge | no | — | Holds a secret about the target. |

Categories in use: `social`, `conflict`, `romance`, `knowledge`, `family`,
`organization`.

## Structural Edges

Structural edges tie the narrative-structure entities together. They are emitted
by `canonworks_init` from the seed data — not declared by the operator — and are
recorded separately from character-relationship edges (see
[Created report](#created-report-shape) below).

| Edge Type | Source Kind | Target Kind | Description |
| --- | --- | --- | --- |
| `APPEARS_IN` | character | series-bible | A character appears in the series canon. |
| `INVOLVES` | storyline | character | A storyline involves a character in its cast. |
| `FORESHADOWS` | foreshadow-thread | storyline | A foreshadow thread points forward to a storyline payoff. |
| `BELONGS_TO` | timeline-event | timeline | A timeline event belongs to the series timeline. |

`BELONGS_TO` is the existing Kumiho scope edge, reused here rather than
duplicated under a new name (it is flagged `reused_kumiho_edge` in the
manifest). This keeps CanonWorks aligned with the kumiho-memory edge philosophy:
one scope/ownership edge across the graph.

`INVOLVES` edges are created from a storyline's cast, read from the first present
of the `characters`, `cast`, or `involves` keys. `FORESHADOWS` edges resolve a
thread's `payoff_target` (or `storyline`) against known storyline ids after slug
normalization; when the target does not match a storyline id, the raw target is
kept in item metadata only and no edge is emitted.

## Normalization and Aliases

Free-form relationship input is normalized to the controlled vocabulary by
`normalize_relationship_type(raw) -> (canonical, known)`:

1. **Case / whitespace / hyphen folding** to the canonical `UPPER_SNAKE` form —
   `rival-of` and `Rival Of` both resolve to `RIVAL_OF`.
2. **Alias lookup** (English and Korean) runs before the canonical check, so
   both a bare noun and a canonical variant resolve — `rival` and `라이벌` both
   map to `RIVAL_OF`.
3. **Unknown preservation.** A type that matches neither an alias nor a
   vocabulary entry is preserved (never coerced to a fallback) in the canonical
   `UPPER_SNAKE` form and flagged `known=False`. Letters from any script survive
   the fold — a single-token or hyphenated type is byte-identical to the
   pre-ontology `.upper()` / hyphen→underscore output (`blood-oath` →
   `BLOOD_OATH`, Cyrillic `враг` → `ВРАГ`, CJK `宿敌` → `宿敌`), while
   whitespace and other separators fold to a single underscore (`blood pact` →
   `BLOOD_PACT`, `foo@bar` → `FOO_BAR`). Backward compatibility is a hard
   requirement: any type that produced an edge before the ontology still
   produces an edge, with the same type name for single-token/hyphenated
   inputs and its canonical `UPPER_SNAKE` folding for multi-word/special-char
   inputs.

Empty / missing input resolves to the `RELATED_TO` fallback (`known=True`).

### English aliases

| Aliases | Resolves to |
| --- | --- |
| `rival`, `rivalry`, `rivals` | `RIVAL_OF` |
| `ally`, `allies`, `alliance` | `ALLY_OF` |
| `enemy`, `enemies`, `foe`, `nemesis` | `ENEMY_OF` |
| `friend`, `friends`, `friendship` | `FRIEND_OF` |
| `confidant`, `confidante` | `CONFIDANT_OF` |
| `lover`, `lovers`, `romance`, `romantic`, `partner` | `ROMANTIC_WITH` |
| `crush`, `unrequited`, `loves` | `LOVES` |
| `mentor`, `teacher`, `master` | `MENTOR_OF` |
| `mentee`, `student`, `disciple`, `apprentice`, `pupil` | `MENTEE_OF` |
| `parent`, `father`, `mother`, `mom`, `dad` | `PARENT_OF` |
| `child`, `son`, `daughter` | `CHILD_OF` |
| `sibling`, `brother`, `sister` | `SIBLING_OF` |
| `spouse`, `husband`, `wife`, `married` | `SPOUSE_OF` |
| `family`, `kin`, `relative` | `FAMILY_OF` |
| `guardian` | `GUARDIAN_OF` |
| `ward` | `WARD_OF` |
| `commander`, `boss`, `superior` | `COMMANDS` |
| `servant`, `subordinate`, `retainer` | `SERVES` |
| `protector`, `guard` | `PROTECTS` |
| `betrayal`, `betrayer`, `traitor` | `BETRAYED` |
| `debtor`, `debt` | `OWES_DEBT_TO` |

### Korean aliases

| Aliases | Resolves to |
| --- | --- |
| `라이벌`, `경쟁자`, `경쟁` | `RIVAL_OF` |
| `동맹`, `아군`, `동료` | `ALLY_OF` |
| `적`, `원수`, `적수` | `ENEMY_OF` |
| `친구`, `벗` | `FRIEND_OF` |
| `연인`, `애인` | `ROMANTIC_WITH` |
| `짝사랑` | `LOVES` |
| `스승`, `멘토`, `사부` | `MENTOR_OF` |
| `제자`, `문하생` | `MENTEE_OF` |
| `부모`, `아버지`, `어머니` | `PARENT_OF` |
| `자식`, `자녀`, `아들`, `딸` | `CHILD_OF` |
| `형제`, `자매`, `남매` | `SIBLING_OF` |
| `부부`, `배우자` | `SPOUSE_OF` |
| `가족`, `혈육` | `FAMILY_OF` |
| `보호자`, `후견인` | `GUARDIAN_OF` |
| `배신`, `배신자` | `BETRAYED` |

## Edge Enrichment

When `canonworks_init` creates a character-relationship edge, it merges ontology
metadata into the existing edge metadata (`relationship`, `summary`,
`canonworks` are kept). The enrichment keys are:

```text
ontology_version    the ontology version that typed the edge
edge_category       the relationship category (social, family, ...)
edge_symmetric      whether the type is symmetric
inverse_edge_type   the inverse type name, or dropped when null
in_vocabulary       false for a preserved unknown type
```

`None`-valued keys (such as `inverse_edge_type` for a directional type) are
dropped by the same `_jsonable_metadata` sanitizer used for all Kumiho item and
revision metadata, so observable string/bool values are unchanged.

### Inverse edges

`canonworks_init`, `canonworks_commit`, and `canonworks_start` accept a
`create_inverse_edges` boolean (default `false`). When `true`, each created
**asymmetric** edge with a defined inverse also gets its reverse edge — e.g. a
`MENTOR_OF` edge from A→B additionally creates a `MENTEE_OF` edge B→A. The
derived edge is marked `derived: "inverse"` and `inverse_of_edge_type` in its
metadata. Symmetric types never get a duplicate reverse edge, and directional
types with no defined inverse are skipped.

## Warnings

The ontology adds non-blocking warnings; readiness/blocking rules are unchanged.

| Warning `type` | When | Notes |
| --- | --- | --- |
| `relationship_edge_skipped` | A relationship endpoint does not match a character id after slug normalization. | Existing behavior, unchanged. A skipped edge never also emits a type warning. |
| `relationship_edge_type_unknown` | A created edge uses an out-of-vocabulary type. | Includes `declared_type` and the preserved `edge_type`; adds a `suggestion` when the alias table has a near match. The type is preserved, not coerced. |
| `storyline_character_skipped` | A storyline lists a character id that does not match a known character. | Mirrors the relationship-skip pattern; the `INVOLVES` edge is not created. |

## Preview Parity

`canonworks_preview` surfaces the ontology before commit without touching
Kumiho. In addition to today's fields it returns:

- the new `storyline` / `foreshadow-thread` / `timeline-event` / `canon-ontology`
  items in `items`,
- a `structural_edges` list (`APPEARS_IN` / `INVOLVES` / `FORESHADOWS` /
  `BELONGS_TO`),
- an `in_vocabulary` flag on each entry in `relationship_edges`,
- an `ontology` block: `version`, `character_edge_types`,
  `structural_edge_types`.

## Created Report Shape

Structural edges are recorded in `created.structural_edges`; `created.edges`
stays character-relationship edges only. Keeping the two lists separate means
the character-relationship list is exactly the set of typed relationship edges
(plus any derived inverse edges), while structural graph edges are reported on
their own channel.

```text
created.edges              character-relationship edges (+ inverse when enabled)
created.structural_edges   APPEARS_IN / INVOLVES / FORESHADOWS / BELONGS_TO
created.warnings           includes the ontology warnings above
```

## Config Ontology Section

The generated `canon_project` config carries a names-only ontology summary so
the workflows and downstream agents can reason about the vocabulary without
parsing the artifact:

```yaml
canon_project:
  krefs:
    canon_ontology: kref://<Project>/CanonRules/canon-ontology.canon-ontology
  ontology:
    version: '1'
    kref: kref://<Project>/CanonRules/canon-ontology.canon-ontology
    character_edge_types: [RELATED_TO, ALLY_OF, FRIEND_OF, ...]
    structural_edge_types: [APPEARS_IN, INVOLVES, FORESHADOWS, BELONGS_TO]
```

Full semantics (category, symmetry, inverse, descriptions) live in the
`CANON_ONTOLOGY.md` artifact and the machine-readable manifest, not in the
config. See
[`canonworks-project-config.example.yaml`](./canonworks-project-config.example.yaml)
for the full generated shape.

## Consumed by the Workflows

The two builtin CanonWorks workflows —
`canonworks-serial-episode-factory` and `canonworks-serial-canon-state-sync` —
traverse this vocabulary when they assemble context packs. Their
`kumiho_context` steps run in `graph_augmented_context` mode, where the
traversal filter (`traversal.edge_types`) is an **exact string match**: an edge
is followed only if its type is listed literally. So every character-relationship
type (23) and structural edge (4) in the ontology is enumerated in each step's
`traversal.edge_types`, appended after the legacy narrative / provenance edges
those steps already carried (`ADVANCES`, `PAYOFF_TARGET`, `CONTRADICTS`, ...).
Before the workflows listed the vocabulary, every ontology edge except the
`RELATED_TO` fallback was invisible to context assembly.

- **Episode factory** — the `episode-context` step traverses the full ontology
  edge set, seeds the `canon-ontology` item kref (default
  `kref://<Project>/CanonRules/canon-ontology.canon-ontology`, honoring
  `krefs.canon_ontology` then `ontology.kref`), adds `canon-ontology` to
  `include_kinds` (kind boost `1.4`), and boosts the structural edges that
  actually surface as `via_edges` in the pack's `edge_map` (`INVOLVES` `1.8`,
  `APPEARS_IN` `1.4`) so the graph edges an episode rests on outrank generic
  ones. Character-to-character relationship edges are traversed (they are listed
  in `traversal.edge_types`) but are **not** given a via-edge boost: in the
  graph `canonworks_init` builds, every character is already reached via
  `APPEARS_IN` from the depth-0 series-bible seed before its own relationship
  edges are examined, so those edges never enter `edge_map` and a boost on them
  would be inert. Relationship-kind context is conveyed through the selected
  relationship-map artifact instead.
- **State sync** — the canon-facing `state-sync-context` step gets the same
  ontology edge set, the seeded `canon-ontology` kref, the `include_kinds`
  entry, and matching boosts (`INVOLVES` `1.8`, `APPEARS_IN` `1.4`); the
  snapshot-focused `state-delta-context-lite` step traverses the ontology edges
  too but stays lean — no ontology kref seed and no `include_kinds` entry — to
  keep the delta pass cheap.

Each workflow's `project-config` step parses the config's `ontology` block and
emits `ontology_version`, `canon_ontology_kref`, and comma-joined
`ontology_character_edge_types_text` / `ontology_structural_edge_types_text`.
Because that step runs as an isolated subprocess and cannot import the ontology
module, it hardcodes the fallback vocabulary so a pre-ontology config (no
`ontology` block) still traverses and names the full edge set. The
relationship-bearing agent prompts carry that vocabulary as context, name
`relation_kind` / `proposed_relation_kind` values from it when the edge is
in-vocabulary, and flag out-of-vocabulary edges (`out_of_vocabulary: true`) as
canon-patch candidates rather than coercing or silently canonizing them. When a
typed graph edge does surface in the pack's `edge_map`, it takes precedence over
`RELATIONSHIP_MAP.md` prose if the two disagree; but because character-
relationship edges typically do not surface (only structural edges such as
`APPEARS_IN` / `INVOLVES` do), the relationship-map artifact remains the
authoritative source for relationship kinds, which the prompts normalize to the
vocabulary. In state sync, out-of-vocabulary relationship deltas stay
`pending_human_approval` (no approval gate is weakened).

A **drift-guard test** (`operator-mcp/tests/test_builtin_workflows.py`) keeps
this wiring honest. It imports `operator_mcp.canon_ontology` and asserts that
every `relationship_type_names()` and `structural_edge_names()` entry appears in
every `kumiho_context` step's `traversal.edge_types` in both YAMLs — so adding a
vocabulary type without teaching the workflows to traverse it fails the test. A
companion test executes each `project-config` step and sync-asserts that its
hardcoded fallback lists equal the ontology module's names.

## Manifest and Document

- `ontology_manifest()` returns a machine-readable summary: `version`,
  `entity_kinds`, `relationship_types` (with category / symmetric / inverse), and
  `structural_edges`. Stable ordering.
- `render_ontology_doc()` renders that manifest as the `CANON_ONTOLOGY.md`
  artifact — the same tables shown above, published into the `canon-ontology`
  item at init time.

## Versioning and Backward Compatibility

- `ONTOLOGY_VERSION` is a single string (`"1"`). Every enriched edge records the
  `ontology_version` it was typed under, so a later vocabulary revision can be
  told apart from an earlier one on the graph.
- Unknown relationship types are always preserved (never coerced to a fallback)
  and flagged `in_vocabulary=false`. Any relationship input that produced an edge
  before the ontology still produces an edge: single-token and hyphenated types
  keep the same name (`blood-oath` → `BLOOD_OATH`), and non-Latin letters are
  kept (`враг` → `ВРАГ`, `宿敌` → `宿敌`), while multi-word and special-character
  types fold their separators to the canonical `UPPER_SNAKE` form (`blood pact`
  → `BLOOD_PACT`). Existing projects re-init cleanly; only unknown types that
  carried spaces or non-hyphen separators change name (to their `UPPER_SNAKE`
  folding), never to a different or empty edge.
- The vocabulary is a controlled list, not a schema validator: out-of-vocabulary
  types warn but never block, keeping the operator in control of domain terms the
  starter vocabulary does not cover.
