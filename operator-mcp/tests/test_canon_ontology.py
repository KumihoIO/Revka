from __future__ import annotations

from operator_mcp import canon_ontology as o


# ---------------------------------------------------------------------------
# Vocabulary integrity
# ---------------------------------------------------------------------------


def test_relationship_vocabulary_has_no_duplicates():
    names = o.relationship_type_names()
    assert len(names) == len(set(names))
    structural = o.structural_edge_names()
    assert len(structural) == len(set(structural))


def test_every_relationship_category_is_non_empty():
    for rt in o.RELATIONSHIP_TYPES.values():
        assert rt.category, rt.edge_type


def test_every_inverse_names_an_existing_type():
    for rt in o.RELATIONSHIP_TYPES.values():
        if rt.inverse is not None:
            assert rt.inverse in o.RELATIONSHIP_TYPES, rt.edge_type


def test_inverse_of_inverse_round_trips():
    for rt in o.RELATIONSHIP_TYPES.values():
        if rt.inverse is not None:
            back = o.inverse_of(rt.inverse)
            assert back == rt.edge_type


def test_symmetric_types_have_no_inverse_and_asymmetric_are_coherent():
    for rt in o.RELATIONSHIP_TYPES.values():
        # exactly one of symmetric / inverse semantics is coherent
        assert not (rt.symmetric and rt.inverse is not None), rt.edge_type
        if rt.symmetric:
            assert rt.inverse is None, rt.edge_type


def test_structural_edges_reference_known_entity_kinds():
    for se in o.STRUCTURAL_EDGES.values():
        assert se.source_kind in o.ENTITY_KINDS, se.edge_type
        assert se.target_kind in o.ENTITY_KINDS, se.edge_type
    # BELONGS_TO is a CanonWorks / Revka operator-mcp structural edge: it reuses
    # the kumiho *core SDK* generic grouping type but is NOT part of the
    # kumiho-memory ontology contract, so the (contract-scoped) flag is False.
    assert o.STRUCTURAL_EDGES["BELONGS_TO"].reused_kumiho_edge is False


def test_expected_core_types_present():
    for edge_type in ("RELATED_TO", "RIVAL_OF", "ALLY_OF", "MENTOR_OF", "MENTEE_OF"):
        assert edge_type in o.RELATIONSHIP_TYPES
    for edge_type in ("APPEARS_IN", "INVOLVES", "FORESHADOWS", "BELONGS_TO"):
        assert edge_type in o.STRUCTURAL_EDGES


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def test_normalize_exact_type_is_known():
    assert o.normalize_relationship_type("RIVAL_OF") == ("RIVAL_OF", True)


def test_normalize_case_and_hyphen_variants():
    assert o.normalize_relationship_type("rival-of") == ("RIVAL_OF", True)
    assert o.normalize_relationship_type("  Rival Of ") == ("RIVAL_OF", True)
    assert o.normalize_relationship_type("mentee-of") == ("MENTEE_OF", True)


def test_normalize_english_aliases():
    assert o.normalize_relationship_type("rival") == ("RIVAL_OF", True)
    assert o.normalize_relationship_type("rivalry") == ("RIVAL_OF", True)
    assert o.normalize_relationship_type("ally") == ("ALLY_OF", True)
    assert o.normalize_relationship_type("lover") == ("ROMANTIC_WITH", True)
    assert o.normalize_relationship_type("mentor") == ("MENTOR_OF", True)


def test_normalize_korean_aliases():
    assert o.normalize_relationship_type("라이벌") == ("RIVAL_OF", True)
    assert o.normalize_relationship_type("동맹") == ("ALLY_OF", True)
    assert o.normalize_relationship_type("적") == ("ENEMY_OF", True)
    assert o.normalize_relationship_type("친구") == ("FRIEND_OF", True)
    assert o.normalize_relationship_type("연인") == ("ROMANTIC_WITH", True)
    assert o.normalize_relationship_type("스승") == ("MENTOR_OF", True)
    assert o.normalize_relationship_type("제자") == ("MENTEE_OF", True)


def test_normalize_unknown_is_preserved_and_flagged():
    canonical, known = o.normalize_relationship_type("HAUNTS")
    assert canonical == "HAUNTS"
    assert known is False
    # Today's behavior: uppercased/underscored, never coerced.
    assert o.normalize_relationship_type("weird custom") == ("WEIRD_CUSTOM", False)


def test_normalize_empty_falls_back_to_related_to():
    assert o.normalize_relationship_type("") == ("RELATED_TO", True)
    assert o.normalize_relationship_type(None) == ("RELATED_TO", True)


def test_normalize_non_latin_unknown_is_preserved_not_collapsed():
    # Unknown CJK / kana / Cyrillic types must survive the fold verbatim
    # (uppercased, matching the pre-ontology .upper() behavior), never coerced
    # to the empty string, and never collide two distinct types onto one edge.
    assert o.normalize_relationship_type("宿敌") == ("宿敌", False)
    assert o.normalize_relationship_type("恋人") == ("恋人", False)
    assert o.normalize_relationship_type("враг") == ("ВРАГ", False)
    assert o.normalize_relationship_type("ライバル") == ("ライバル", False)
    # Distinct non-Latin inputs stay distinct (no empty-string collision).
    assert o.normalize_relationship_type("宿敌")[0] != o.normalize_relationship_type("恋人")[0]
    # Mixed non-Latin + separators still fold separators only.
    assert o.normalize_relationship_type("宿 敌") == ("宿_敌", False)
    # Pathological all-punctuation input is preserved, never empty.
    canonical, known = o.normalize_relationship_type("---")
    assert canonical != ""
    assert known is False


def test_edge_metadata_and_helpers():
    meta = o.edge_metadata_for("MENTOR_OF")
    assert meta["in_vocabulary"] is True
    assert meta["symmetric"] is False
    assert meta["inverse_of"] == "MENTEE_OF"
    assert meta["ontology_version"] == o.ONTOLOGY_VERSION
    unknown = o.edge_metadata_for("HAUNTS")
    assert unknown["in_vocabulary"] is False
    assert unknown["inverse_of"] is None
    assert o.is_symmetric("RIVAL_OF") is True
    assert o.is_symmetric("MENTOR_OF") is False


# ---------------------------------------------------------------------------
# Manifest + document
# ---------------------------------------------------------------------------


def test_manifest_contains_version_and_both_vocabularies():
    manifest = o.ontology_manifest()
    assert manifest["version"] == o.ONTOLOGY_VERSION
    rel_types = {r["edge_type"] for r in manifest["relationship_types"]}
    assert "RIVAL_OF" in rel_types and "MENTOR_OF" in rel_types
    struct_types = {s["edge_type"] for s in manifest["structural_edges"]}
    assert struct_types == set(o.structural_edge_names())
    kinds = {k["kind"] for k in manifest["entity_kinds"]}
    for expected in ("character", "storyline", "foreshadow-thread", "timeline-event", "canon-ontology"):
        assert expected in kinds


def test_render_doc_mentions_every_edge_type():
    doc = o.render_ontology_doc()
    for edge_type in o.relationship_type_names():
        assert edge_type in doc, edge_type
    for edge_type in o.structural_edge_names():
        assert edge_type in doc, edge_type
    assert f"ontology_version: {o.ONTOLOGY_VERSION}" in doc


# ---------------------------------------------------------------------------
# Kumiho node-kind mapping (Deliverable A)
# ---------------------------------------------------------------------------


def test_kumiho_node_kinds_are_the_six_real_kinds():
    # Whether sourced from kumiho-memory or the local fallback, the set is the
    # six canonical Kumiho node kinds.
    assert set(o.kumiho_node_kinds()) == {
        "entity", "fact", "decision", "event", "action", "question"
    }


def test_node_kind_mapping_only_uses_real_kumiho_kinds():
    mapping = o.node_kind_mapping()
    # Every canon kind is present in the mapping.
    assert set(mapping) == set(o.entity_kind_names())
    valid = set(o.kumiho_node_kinds())
    for kind, node_kind in mapping.items():
        # Unmapped kinds are None (no natural fit forced); mapped values are real.
        assert node_kind is None or node_kind in valid, (kind, node_kind)


def test_node_kind_mapping_expected_fits():
    mapping = o.node_kind_mapping()
    assert mapping["character"] == "entity"
    assert mapping["series-bible"] == "entity"
    assert mapping["storyline"] == "entity"
    assert mapping["foreshadow-thread"] == "entity"
    assert mapping["canon-ontology"] == "entity"
    assert mapping["timeline-event"] == "event"
    # A kind with no natural fit is left unmapped, not forced.
    assert mapping["canonworks-config"] is None
    assert o.kumiho_node_kind_for("character") == "entity"
    assert o.kumiho_node_kind_for("timeline-event") == "event"
    assert o.kumiho_node_kind_for("canonworks-config") is None


def test_manifest_and_doc_surface_node_kinds():
    manifest = o.ontology_manifest()
    assert set(manifest["kumiho_node_kinds"]) == set(o.kumiho_node_kinds())
    by_kind = {e["kind"]: e for e in manifest["entity_kinds"]}
    assert by_kind["character"]["kumiho_node_kind"] == "entity"
    assert by_kind["timeline-event"]["kumiho_node_kind"] == "event"
    assert by_kind["canonworks-config"]["kumiho_node_kind"] is None
    doc = o.render_ontology_doc()
    assert "Kumiho Node Kind" in doc


# ---------------------------------------------------------------------------
# Typed-graph predicate projection via resolve_predicate (Deliverable C)
# ---------------------------------------------------------------------------


def test_project_predicate_folds_narrative_types_to_relates_to():
    # Narrative predicates are NOT among Kumiho's 10 canonical predicates, so
    # they fold onto the RELATES_TO fallback with the verbatim preserved. This
    # holds both when kumiho-memory is present (its registry folds) and when
    # absent (local RELATES_TO fallback).
    canonical, verbatim, fallback = o.project_predicate("RIVAL_OF")
    assert canonical == "RELATES_TO"
    assert verbatim == "RIVAL_OF"
    assert fallback is True
    # canon INVOLVES also folds to RELATES_TO (distinct from Kumiho's INVOLVES).
    canonical, verbatim, _ = o.project_predicate("INVOLVES")
    assert canonical == "RELATES_TO"
    assert verbatim == "INVOLVES"


def test_project_predicate_consults_resolve_predicate_when_available(monkeypatch):
    # When kumiho-memory is present, resolve_predicate is the authority for the
    # fold — canon must consult it, not a hand-maintained copy.
    calls: list[str] = []

    class _Resolution:
        def __init__(self, fallback: bool) -> None:
            self.normalized = ""
            self.folded = False
            self.fallback = fallback

    def fake_resolve(predicate: str):
        calls.append(predicate)
        # Pretend the registry maps this narrative type to a canonical edge.
        return "DEPENDS_ON", _Resolution(fallback=False)

    monkeypatch.setattr(o, "_km_resolve_predicate", fake_resolve)
    canonical, verbatim, fallback = o.project_predicate("USES_ARTIFACT")
    assert calls == ["USES_ARTIFACT"]
    assert canonical == "DEPENDS_ON"
    assert verbatim == "USES_ARTIFACT"
    assert fallback is False


def test_project_predicate_degrades_without_kumiho(monkeypatch):
    # With the registry unavailable, canon degrades to its local RELATES_TO
    # fallback for the typed projection (never raises).
    monkeypatch.setattr(o, "_km_resolve_predicate", None)
    canonical, verbatim, fallback = o.project_predicate("rival")
    assert canonical == o.KUMIHO_FALLBACK_PREDICATE == "RELATES_TO"
    assert verbatim == "RIVAL_OF"  # canon-local normalization still applies
    assert fallback is True


# ---------------------------------------------------------------------------
# Canon spec version + Kumiho spec reference (Deliverable D)
# ---------------------------------------------------------------------------


def test_canon_spec_version_is_distinct_and_stable():
    assert o.CANON_ONTOLOGY_SPEC_VERSION == f"canonworks.ontology.v{o.ONTOLOGY_VERSION}"
    # Canon's fallback edge is intentionally distinct from Kumiho's canonical.
    assert o.DEFAULT_RELATIONSHIP_TYPE == "RELATED_TO"
    assert o.KUMIHO_FALLBACK_PREDICATE == "RELATES_TO"
    assert o.DEFAULT_RELATIONSHIP_TYPE != o.KUMIHO_FALLBACK_PREDICATE


def test_kumiho_spec_reference_shape():
    ref = o.kumiho_spec_reference()
    # None when kumiho-memory is absent/too old; a {spec_version, spec_tag} dict
    # when present. Either way, never raises and never fabricates.
    if ref is not None:
        assert set(ref) == {"spec_version", "spec_tag"}
        assert ref["spec_tag"] == "ontology.spec"
