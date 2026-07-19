"""CanonWorks narrative ontology.

A formal, controlled vocabulary layer on top of the Kumiho memory graph (the
same substrate documented in ``docs/contributing/kumiho-memory-integration.md``,
whose provenance edges are ``DERIVED_FROM``, ``DEPENDS_ON``, ``REFERENCED``,
``CONTAINS``, ``CREATED_FROM``, ``BELONGS_TO``).

CanonWorks bootstraps a canon graph of series/character/relationship/timeline
items. This module turns that weakly-typed graph into a genuinely typed
ontology by defining:

* the canonical Item **kinds** CanonWorks creates (entity-kind registry),
* a controlled **character relationship** vocabulary with category / symmetry /
  inverse semantics,
* a **structural** edge vocabulary tying narrative entities together, reusing
  the existing Kumiho ``BELONGS_TO`` edge rather than inventing a synonym,
* **normalization** of free-form relationship types (case/hyphen folding plus an
  English + Korean alias table) that preserves unknown types for backward
  compatibility while flagging them as out-of-vocabulary.

Pure standard library. No new dependencies, no validation framework — this is a
vocabulary the CanonWorks tools and downstream agents can reason against.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


ONTOLOGY_VERSION = "1"

#: Fallback relationship type used when none is declared. Symmetric, in-vocab.
DEFAULT_RELATIONSHIP_TYPE = "RELATED_TO"


# ---------------------------------------------------------------------------
# Entity-kind registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntityKind:
    """A canonical Item kind CanonWorks creates in the canon graph."""

    kind: str
    label: str
    space: str  # SPACE_KEYS key in tool_handlers/canonworks.py
    description: str
    sources: tuple[str, ...] = ()  # edge types this kind may originate
    targets: tuple[str, ...] = ()  # edge types this kind may receive


_ENTITY_KINDS: tuple[EntityKind, ...] = (
    EntityKind("series-bible", "Series Bible", "series",
               "Top-level canon promise, themes, and guardrails for the serial.",
               targets=("APPEARS_IN",)),
    EntityKind("series-synopsis", "Series Synopsis", "series",
               "Canonical rolling synopsis of the series."),
    EntityKind("character-index", "Character Index", "characters",
               "Roster index of all canonical characters."),
    EntityKind("relationship-map", "Relationship Map", "relationships",
               "Human-readable rollup of character relationship edges."),
    EntityKind("timeline", "Timeline", "timeline",
               "Canonical timeline of the series.",
               targets=("BELONGS_TO",)),
    EntityKind("series-roadmap", "Series Roadmap", "roadmaps",
               "Long-arc roadmap rollup of storylines and foreshadow threads."),
    EntityKind("style-guide", "Production Style Guide", "series",
               "Prose, POV, pacing, and platform style rules."),
    EntityKind("character", "Character", "characters",
               "A canonical character entity.",
               sources=("APPEARS_IN",), targets=("INVOLVES",)),
    EntityKind("storyline", "Storyline", "roadmaps",
               "A long-arc storyline entity.",
               sources=("INVOLVES",), targets=("FORESHADOWS",)),
    EntityKind("foreshadow-thread", "Foreshadow Thread", "roadmaps",
               "A planted foreshadow thread with a payoff target.",
               sources=("FORESHADOWS",)),
    EntityKind("timeline-event", "Timeline Event", "timeline",
               "A canonical timeline event anchored to the series timeline.",
               sources=("BELONGS_TO",)),
    EntityKind("canon-ontology", "Canon Ontology", "canon_rules",
               "The published CanonWorks ontology reference for this project."),
    EntityKind("character-state", "Character State Snapshot", "state",
               "Current per-character state snapshot."),
    EntityKind("relationship-state", "Relationship State Snapshot", "state",
               "Current relationship state snapshot."),
    EntityKind("timeline-progress", "Timeline Progress Snapshot", "progress",
               "Current timeline progress snapshot."),
    EntityKind("storyline-progress", "Storyline Progress Snapshot", "progress",
               "Current storyline progress snapshot."),
    EntityKind("foreshadow-progress", "Foreshadow Progress Snapshot", "progress",
               "Current foreshadow progress snapshot."),
    EntityKind("canonworks-config", "CanonWorks Project Config", "config",
               "Generated project config routing the CanonWorks workflows."),
    EntityKind("webnovel-episode", "Webnovel Episode", "episodes",
               "A produced serial episode revision (episode factory output)."),
    EntityKind("canon-patch", "Canon Patch Candidate", "patches",
               "A propose-only canon patch candidate (workflow output)."),
    EntityKind("context-pack", "Context Pack", "context_packs",
               "A locked context pack assembled for an episode (workflow output)."),
)

ENTITY_KINDS: dict[str, EntityKind] = {ek.kind: ek for ek in _ENTITY_KINDS}


def entity_kind_names() -> list[str]:
    return [ek.kind for ek in _ENTITY_KINDS]


# ---------------------------------------------------------------------------
# Character relationship vocabulary
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RelationshipType:
    """A controlled character↔character relationship edge type.

    Exactly one of ``symmetric`` / ``inverse`` carries the direction semantics:
    a symmetric type has ``inverse is None``; an asymmetric type is either
    paired with an ``inverse`` type or left directional with ``inverse is None``.
    """

    edge_type: str
    category: str
    symmetric: bool
    inverse: Optional[str]
    description: str


_RELATIONSHIP_TYPES: tuple[RelationshipType, ...] = (
    RelationshipType("RELATED_TO", "social", True, None,
                     "Generic fallback relationship when no type is declared."),
    RelationshipType("ALLY_OF", "social", True, None,
                     "Mutual allies pursuing aligned goals."),
    RelationshipType("FRIEND_OF", "social", True, None,
                     "Personal friendship."),
    RelationshipType("CONFIDANT_OF", "social", True, None,
                     "Trusted confidant who shares private matters."),
    RelationshipType("RIVAL_OF", "conflict", True, None,
                     "Competitive rivalry between near-equals."),
    RelationshipType("ENEMY_OF", "conflict", True, None,
                     "Declared hostility or opposition."),
    RelationshipType("ROMANTIC_WITH", "romance", True, None,
                     "Mutual romantic involvement."),
    RelationshipType("LOVES", "romance", False, None,
                     "One-directional love, possibly unrequited."),
    RelationshipType("MENTOR_OF", "knowledge", False, "MENTEE_OF",
                     "Teaches or guides the target."),
    RelationshipType("MENTEE_OF", "knowledge", False, "MENTOR_OF",
                     "Is taught or guided by the target."),
    RelationshipType("PARENT_OF", "family", False, "CHILD_OF",
                     "Parent of the target."),
    RelationshipType("CHILD_OF", "family", False, "PARENT_OF",
                     "Child of the target."),
    RelationshipType("SIBLING_OF", "family", True, None,
                     "Shares a sibling bond with the target."),
    RelationshipType("SPOUSE_OF", "family", True, None,
                     "Married to the target."),
    RelationshipType("FAMILY_OF", "family", True, None,
                     "Belongs to the same family as the target."),
    RelationshipType("GUARDIAN_OF", "family", False, "WARD_OF",
                     "Legal or protective guardian of the target."),
    RelationshipType("WARD_OF", "family", False, "GUARDIAN_OF",
                     "Is under the guardianship of the target."),
    RelationshipType("COMMANDS", "organization", False, "SERVES",
                     "Holds command authority over the target."),
    RelationshipType("SERVES", "organization", False, "COMMANDS",
                     "Serves under the authority of the target."),
    RelationshipType("PROTECTS", "social", False, None,
                     "Actively protects the target."),
    RelationshipType("BETRAYED", "conflict", False, None,
                     "Has betrayed the target."),
    RelationshipType("OWES_DEBT_TO", "social", False, None,
                     "Owes a debt or obligation to the target."),
    RelationshipType("KNOWS_SECRET_OF", "knowledge", False, None,
                     "Holds a secret about the target."),
)

RELATIONSHIP_TYPES: dict[str, RelationshipType] = {
    rt.edge_type: rt for rt in _RELATIONSHIP_TYPES
}


def relationship_type_names() -> list[str]:
    return [rt.edge_type for rt in _RELATIONSHIP_TYPES]


def is_known_relationship_type(edge_type: str) -> bool:
    return str(edge_type).upper() in RELATIONSHIP_TYPES


# ---------------------------------------------------------------------------
# Structural edge vocabulary
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StructuralEdge:
    """An edge type tying narrative-structure entities together."""

    edge_type: str
    source_kind: str
    target_kind: str
    description: str
    reused_kumiho_edge: bool = False


_STRUCTURAL_EDGES: tuple[StructuralEdge, ...] = (
    StructuralEdge("APPEARS_IN", "character", "series-bible",
                   "A character appears in the series canon."),
    StructuralEdge("INVOLVES", "storyline", "character",
                   "A storyline involves a character in its cast."),
    StructuralEdge("FORESHADOWS", "foreshadow-thread", "storyline",
                   "A foreshadow thread points forward to a storyline payoff."),
    StructuralEdge("BELONGS_TO", "timeline-event", "timeline",
                   "A timeline event belongs to the series timeline "
                   "(reuses the Kumiho BELONGS_TO scope edge).",
                   reused_kumiho_edge=True),
)

STRUCTURAL_EDGES: dict[str, StructuralEdge] = {
    se.edge_type: se for se in _STRUCTURAL_EDGES
}


def structural_edge_names() -> list[str]:
    return [se.edge_type for se in _STRUCTURAL_EDGES]


def is_structural_edge(edge_type: str) -> bool:
    return str(edge_type).upper() in STRUCTURAL_EDGES


# ---------------------------------------------------------------------------
# Alias table (English + Korean) for relationship-type normalization
# ---------------------------------------------------------------------------


def _alias_key(text: str) -> str:
    """Lowercase and collapse separators for alias lookup.

    Separators are runs of non-word characters plus the underscore; word
    characters from *any* script (Latin, Hangul, CJK ideographs, kana, Cyrillic,
    …) are preserved, so non-Latin relationship types survive the fold and are
    matched against the alias table by their own characters.
    """
    key = re.sub(r"[\W_]+", " ", str(text).strip().lower(), flags=re.UNICODE)
    return re.sub(r"\s+", " ", key).strip()


def _canonical_token(text: str) -> str:
    """Uppercase, underscore-join tokens (today's behavior, superset-safe).

    Only runs of non-word characters and underscores fold to a single ``_``;
    word characters from any script are kept verbatim. This preserves unknown
    non-Latin types (e.g. ``宿敌`` → ``宿敌``, ``враг`` → ``ВРАГ``) instead of
    deleting them, matching the pre-ontology ``.upper().replace('-','_')``
    behavior for out-of-vocabulary input.
    """
    token = re.sub(r"[\W_]+", "_", str(text).strip(), flags=re.UNICODE).strip("_")
    return token.upper()


# Raw alias -> canonical relationship edge type. Keys are matched after
# ``_alias_key`` normalization, so any casing / separator variant resolves.
_RAW_ALIASES: dict[str, str] = {
    # English
    "rival": "RIVAL_OF", "rivalry": "RIVAL_OF", "rivals": "RIVAL_OF",
    "ally": "ALLY_OF", "allies": "ALLY_OF", "alliance": "ALLY_OF",
    "enemy": "ENEMY_OF", "enemies": "ENEMY_OF", "foe": "ENEMY_OF",
    "nemesis": "ENEMY_OF",
    "friend": "FRIEND_OF", "friends": "FRIEND_OF", "friendship": "FRIEND_OF",
    "confidant": "CONFIDANT_OF", "confidante": "CONFIDANT_OF",
    "lover": "ROMANTIC_WITH", "lovers": "ROMANTIC_WITH",
    "romance": "ROMANTIC_WITH", "romantic": "ROMANTIC_WITH",
    "partner": "ROMANTIC_WITH",
    "crush": "LOVES", "unrequited": "LOVES", "loves": "LOVES",
    "mentor": "MENTOR_OF", "teacher": "MENTOR_OF", "master": "MENTOR_OF",
    "mentee": "MENTEE_OF", "student": "MENTEE_OF", "disciple": "MENTEE_OF",
    "apprentice": "MENTEE_OF", "pupil": "MENTEE_OF",
    "parent": "PARENT_OF", "father": "PARENT_OF", "mother": "PARENT_OF",
    "mom": "PARENT_OF", "dad": "PARENT_OF",
    "child": "CHILD_OF", "son": "CHILD_OF", "daughter": "CHILD_OF",
    "sibling": "SIBLING_OF", "brother": "SIBLING_OF", "sister": "SIBLING_OF",
    "spouse": "SPOUSE_OF", "husband": "SPOUSE_OF", "wife": "SPOUSE_OF",
    "married": "SPOUSE_OF",
    "family": "FAMILY_OF", "kin": "FAMILY_OF", "relative": "FAMILY_OF",
    "guardian": "GUARDIAN_OF", "protector guardian": "GUARDIAN_OF",
    "ward": "WARD_OF",
    "commander": "COMMANDS", "boss": "COMMANDS", "superior": "COMMANDS",
    "servant": "SERVES", "subordinate": "SERVES", "retainer": "SERVES",
    "protector": "PROTECTS", "guard": "PROTECTS",
    "betrayal": "BETRAYED", "betrayer": "BETRAYED", "traitor": "BETRAYED",
    "debtor": "OWES_DEBT_TO", "debt": "OWES_DEBT_TO",
    # Korean
    "라이벌": "RIVAL_OF", "경쟁자": "RIVAL_OF", "경쟁": "RIVAL_OF",
    "동맹": "ALLY_OF", "아군": "ALLY_OF", "동료": "ALLY_OF",
    "적": "ENEMY_OF", "원수": "ENEMY_OF", "적수": "ENEMY_OF",
    "친구": "FRIEND_OF", "벗": "FRIEND_OF",
    "연인": "ROMANTIC_WITH", "애인": "ROMANTIC_WITH",
    "짝사랑": "LOVES",
    "스승": "MENTOR_OF", "멘토": "MENTOR_OF", "사부": "MENTOR_OF",
    "제자": "MENTEE_OF", "문하생": "MENTEE_OF",
    "부모": "PARENT_OF", "아버지": "PARENT_OF", "어머니": "PARENT_OF",
    "자식": "CHILD_OF", "자녀": "CHILD_OF", "아들": "CHILD_OF", "딸": "CHILD_OF",
    "형제": "SIBLING_OF", "자매": "SIBLING_OF", "남매": "SIBLING_OF",
    "부부": "SPOUSE_OF", "배우자": "SPOUSE_OF",
    "가족": "FAMILY_OF", "혈육": "FAMILY_OF",
    "보호자": "GUARDIAN_OF", "후견인": "GUARDIAN_OF",
    "배신": "BETRAYED", "배신자": "BETRAYED",
}

# Normalized alias key -> canonical type.
_ALIASES: dict[str, str] = {_alias_key(raw): canon for raw, canon in _RAW_ALIASES.items()}


def normalize_relationship_type(raw: object) -> tuple[str, bool]:
    """Normalize a free-form relationship type to the controlled vocabulary.

    Returns ``(canonical, known)``:

    * exact / case / hyphen variants of a vocabulary type resolve to it
      (``"rival-of"`` -> ``("RIVAL_OF", True)``),
    * English and Korean aliases resolve to their type
      (``"rival"`` / ``"라이벌"`` -> ``("RIVAL_OF", True)``),
    * unknown types are **preserved** in today's uppercased/underscored form and
      flagged ``known=False`` — never coerced (backward compatibility).
    """
    text = "" if raw is None else str(raw).strip()
    if not text:
        return DEFAULT_RELATIONSHIP_TYPE, True
    alias_hit = _ALIASES.get(_alias_key(text))
    if alias_hit is not None:
        return alias_hit, True
    canonical = _canonical_token(text)
    if not canonical:
        # Folding stripped the whole token (e.g. pure punctuation like "---").
        # Preserve the original uppercased rather than coercing to "" — never
        # collide distinct out-of-vocabulary inputs onto one empty edge type.
        canonical = text.upper()
    if canonical in RELATIONSHIP_TYPES:
        return canonical, True
    return canonical, False


def suggest_relationship_type(raw: object) -> Optional[str]:
    """Best-effort near-match for an out-of-vocabulary type, else ``None``."""
    key = _alias_key(raw if raw is not None else "")
    if not key:
        return None
    token = key.replace(" ", "_")
    for edge_type in RELATIONSHIP_TYPES:
        lowered = edge_type.lower()
        if token and (token in lowered or lowered.split("_", 1)[0] == token):
            return edge_type
    for alias_key, canonical in _ALIASES.items():
        if alias_key and (alias_key in key or key in alias_key):
            return canonical
    return None


# ---------------------------------------------------------------------------
# Edge semantics helpers
# ---------------------------------------------------------------------------


def inverse_of(edge_type: str) -> Optional[str]:
    """The inverse relationship type for ``edge_type``, or ``None``."""
    entry = RELATIONSHIP_TYPES.get(str(edge_type).upper())
    return entry.inverse if entry else None


def is_symmetric(edge_type: str) -> bool:
    """Whether ``edge_type`` is a symmetric relationship type."""
    entry = RELATIONSHIP_TYPES.get(str(edge_type).upper())
    return bool(entry.symmetric) if entry else False


def edge_metadata_for(edge_type: str) -> dict[str, object]:
    """Ontology enrichment for a (already-normalized) relationship edge type."""
    entry = RELATIONSHIP_TYPES.get(str(edge_type).upper())
    if entry is None:
        return {
            "category": "",
            "symmetric": False,
            "inverse_of": None,
            "ontology_version": ONTOLOGY_VERSION,
            "in_vocabulary": False,
        }
    return {
        "category": entry.category,
        "symmetric": entry.symmetric,
        "inverse_of": entry.inverse,
        "ontology_version": ONTOLOGY_VERSION,
        "in_vocabulary": True,
    }


def structural_edge_metadata(edge_type: str) -> dict[str, object]:
    """Ontology enrichment for a structural edge type."""
    entry = STRUCTURAL_EDGES.get(str(edge_type).upper())
    if entry is None:
        return {
            "category": "structural",
            "ontology_version": ONTOLOGY_VERSION,
            "in_vocabulary": False,
        }
    return {
        "category": "structural",
        "source_kind": entry.source_kind,
        "target_kind": entry.target_kind,
        "ontology_version": ONTOLOGY_VERSION,
        "in_vocabulary": True,
    }


# ---------------------------------------------------------------------------
# Manifest + document rendering
# ---------------------------------------------------------------------------


def ontology_manifest() -> dict[str, object]:
    """Machine-readable summary of the ontology (stable ordering)."""
    return {
        "version": ONTOLOGY_VERSION,
        "entity_kinds": [
            {
                "kind": ek.kind,
                "label": ek.label,
                "space": ek.space,
                "description": ek.description,
                "sources": list(ek.sources),
                "targets": list(ek.targets),
            }
            for ek in _ENTITY_KINDS
        ],
        "relationship_types": [
            {
                "edge_type": rt.edge_type,
                "category": rt.category,
                "symmetric": rt.symmetric,
                "inverse": rt.inverse,
                "description": rt.description,
            }
            for rt in _RELATIONSHIP_TYPES
        ],
        "structural_edges": [
            {
                "edge_type": se.edge_type,
                "source_kind": se.source_kind,
                "target_kind": se.target_kind,
                "description": se.description,
                "reused_kumiho_edge": se.reused_kumiho_edge,
            }
            for se in _STRUCTURAL_EDGES
        ],
    }


def render_ontology_doc() -> str:
    """Render the ontology manifest as stable ``CANON_ONTOLOGY.md`` markdown."""
    lines: list[str] = [
        "# Canon Ontology",
        "",
        f"- ontology_version: {ONTOLOGY_VERSION}",
        "",
        (
            "CanonWorks types the Kumiho canon graph on top of the kumiho-memory "
            + "edge semantics (`DERIVED_FROM`, `DEPENDS_ON`, `REFERENCED`, `CONTAINS`, "
            + "`CREATED_FROM`, `BELONGS_TO`). Unknown relationship types are preserved "
            + "as declared but flagged out-of-vocabulary."
        ),
        "",
        "## Entity Kinds",
        "",
        "| Kind | Label | Space | Description |",
        "| --- | --- | --- | --- |",
    ]
    for ek in _ENTITY_KINDS:
        lines.append(f"| `{ek.kind}` | {ek.label} | {ek.space} | {ek.description} |")
    lines.extend([
        "",
        "## Character Relationship Vocabulary",
        "",
        "| Edge Type | Category | Symmetric | Inverse | Description |",
        "| --- | --- | --- | --- | --- |",
    ])
    for rt in _RELATIONSHIP_TYPES:
        inverse = rt.inverse if rt.inverse else "—"
        symmetric = "yes" if rt.symmetric else "no"
        lines.append(
            f"| `{rt.edge_type}` | {rt.category} | {symmetric} | {inverse} | {rt.description} |"
        )
    lines.extend([
        "",
        "## Structural Edges",
        "",
        "| Edge Type | Source Kind | Target Kind | Description |",
        "| --- | --- | --- | --- |",
    ])
    for se in _STRUCTURAL_EDGES:
        lines.append(
            f"| `{se.edge_type}` | {se.source_kind} | {se.target_kind} | {se.description} |"
        )
    lines.extend([
        "",
        "## Normalization",
        "",
        (
            "- Case, whitespace, and hyphens fold to the canonical `UPPER_SNAKE` form "
            + "(`rival-of` → `RIVAL_OF`)."
        ),
        (
            "- English and Korean aliases resolve to a controlled type "
            + "(`rival` / `라이벌` → `RIVAL_OF`)."
        ),
        (
            "- Unknown types are preserved verbatim (uppercased/underscored) and "
            + "flagged `in_vocabulary=false` so callers can warn without data loss."
        ),
        "",
    ])
    return "\n".join(lines).rstrip() + "\n"
