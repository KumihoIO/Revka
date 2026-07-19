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
    # BELONGS_TO reuses the existing Kumiho edge instead of a synonym.
    assert o.STRUCTURAL_EDGES["BELONGS_TO"].reused_kumiho_edge is True


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
