"""Tests for output → resolve round-trip space-path normalization.

Bug B: ``_exec_output`` (publish path) and ``_exec_resolve`` (lookup path)
each used the user-supplied ``entity_space`` / ``space`` strings as-is.
The strings *looked* identical in YAML but reached Kumiho with different
forms — leading-slash vs not, trailing slash, doubled separators — so the
write side published to one path and the read side missed it.

These tests use a minimal in-memory Kumiho fake to verify the SDK sees
canonical paths from BOTH sides regardless of the user-supplied form.
"""
from __future__ import annotations

from typing import Any

import pytest

import operator_mcp.workflow.memory as memory_mod
from operator_mcp.workflow.memory import (
    _canonical_space,
    publish_workflow_entity,
    resolve_entity,
)


# ---------------------------------------------------------------------------
# Pure unit tests — _canonical_space
# ---------------------------------------------------------------------------

class TestCanonicalSpace:
    def test_empty_uses_default(self):
        assert _canonical_space("", default=lambda: "P/W") == "P/W"
        assert _canonical_space(None, default=lambda: "P/W") == "P/W"

    def test_empty_no_default(self):
        assert _canonical_space("") == ""
        assert _canonical_space(None) == ""

    def test_strips_leading_slash(self):
        assert _canonical_space("/A/B/C") == "A/B/C"

    def test_strips_trailing_slash(self):
        assert _canonical_space("A/B/C/") == "A/B/C"

    def test_collapses_double_slash(self):
        assert _canonical_space("A//B///C") == "A/B/C"

    def test_full_normalization(self):
        assert _canonical_space("//A//B/C/") == "A/B/C"


# ---------------------------------------------------------------------------
# Round-trip tests with a fake Kumiho SDK
# ---------------------------------------------------------------------------

class _FakeItem(dict):
    pass


class _FakeKumihoSDK:
    """Minimal stand-in for KUMIHO_SDK that records the space_path the
    workflow layer hands to it. The fake key for the items registry is the
    raw space_path argument received — which is what we want to assert is
    identical between publish and resolve."""

    def __init__(self) -> None:
        self._available = True
        self.items_by_space: dict[str, list[dict[str, Any]]] = {}
        self.revisions_by_kref: dict[str, dict[str, Any]] = {}
        self._kref_counter = 0
        # Calls captured for assertions.
        self.create_item_calls: list[str] = []
        self.list_items_calls: list[str] = []

    async def ensure_space(self, project: str, space: str) -> None:
        return None

    async def list_items(self, space_path: str) -> list[dict[str, Any]]:
        self.list_items_calls.append(space_path)
        return list(self.items_by_space.get(space_path, []))

    async def create_item(
        self,
        space_path: str,
        name: str,
        kind: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.create_item_calls.append(space_path)
        self._kref_counter += 1
        kref = f"kref://item/{self._kref_counter}"
        item = {"kref": kref, "name": name, "kind": kind, "metadata": dict(metadata or {})}
        self.items_by_space.setdefault(space_path, []).append(item)
        return item

    async def create_revision(
        self,
        item_kref: str,
        metadata: dict[str, Any],
        tag: str | None = "published",
    ) -> dict[str, Any]:
        self._kref_counter += 1
        rev_kref = f"kref://rev/{self._kref_counter}"
        rev = {"kref": rev_kref, "metadata": dict(metadata), "tag": tag}
        self.revisions_by_kref[item_kref] = rev
        return rev

    async def get_latest_revision(self, item_kref: str, tag: str = "published") -> dict[str, Any] | None:
        return self.revisions_by_kref.get(item_kref)

    async def create_artifact(self, *args: Any, **kwargs: Any) -> Any:
        return None


@pytest.fixture
def fake_sdk(monkeypatch, tmp_path) -> _FakeKumihoSDK:
    """Patch the SDK lookup used by both publish_workflow_entity and
    resolve_entity. Both functions do ``from ..operator_mcp import KUMIHO_SDK``
    inside the call, so we install the fake on that module."""
    sdk = _FakeKumihoSDK()
    import operator_mcp.operator_mcp as op_mod
    monkeypatch.setattr(op_mod, "KUMIHO_SDK", sdk, raising=False)

    # Stub _ensure_space_path — it tries to import kumiho.mcp_server which
    # isn't available in the test env. The fake SDK doesn't care about
    # space pre-creation.
    async def _noop_ensure(_path: str) -> None:
        return None
    monkeypatch.setattr(memory_mod, "_ensure_space_path", _noop_ensure)

    # Redirect artifact directory into the test's tmpdir so we don't
    # litter the user's home.
    monkeypatch.setattr(
        "os.path.expanduser",
        lambda p: str(tmp_path) if p.startswith("~/") else p,
    )
    return sdk


async def _publish_then_resolve(
    sdk: _FakeKumihoSDK,
    *,
    write_space: str,
    read_space: str,
    kind: str = "BlogPost",
    tag: str = "ready",
    name: str = "post-1",
) -> dict[str, Any] | list[dict[str, Any]] | None:
    await publish_workflow_entity(
        entity_name=name,
        entity_kind=kind,
        entity_tag=tag,
        entity_space=write_space,
        entity_metadata={"k": "v"},
        content="hello",
        content_format="markdown",
        workflow_name="wf",
        run_id="r1",
        step_id="s1",
    )
    return await resolve_entity(kind=kind, tag=tag, space=read_space, mode="latest")


@pytest.mark.asyncio
class TestRoundTripNormalization:
    async def test_register_then_resolve_round_trip(self, fake_sdk):
        # Identical strings on both sides — baseline.
        result = await _publish_then_resolve(
            fake_sdk,
            write_space="Construct/WorkflowOutputs/Github",
            read_space="Construct/WorkflowOutputs/Github",
        )
        assert result is not None
        # And both sides hit Kumiho with the SAME canonical path.
        assert fake_sdk.create_item_calls == ["Construct/WorkflowOutputs/Github"]
        assert "Construct/WorkflowOutputs/Github" in fake_sdk.list_items_calls

    async def test_resolve_with_leading_slash(self, fake_sdk):
        # Write without slash, read with leading slash — must still find.
        result = await _publish_then_resolve(
            fake_sdk,
            write_space="Construct/WorkflowOutputs/Github",
            read_space="/Construct/WorkflowOutputs/Github",
        )
        assert result is not None
        assert fake_sdk.create_item_calls[-1] == fake_sdk.list_items_calls[-1]

    async def test_output_with_leading_slash(self, fake_sdk):
        # Write with leading slash, read without — must still find.
        result = await _publish_then_resolve(
            fake_sdk,
            write_space="/Construct/WorkflowOutputs/Github",
            read_space="Construct/WorkflowOutputs/Github",
        )
        assert result is not None
        assert fake_sdk.create_item_calls[-1] == fake_sdk.list_items_calls[-1]

    async def test_trailing_slash_normalized(self, fake_sdk):
        # Trailing slash on either side normalizes away.
        result = await _publish_then_resolve(
            fake_sdk,
            write_space="Construct/WorkflowOutputs/Github/",
            read_space="Construct/WorkflowOutputs/Github",
        )
        assert result is not None
        assert fake_sdk.create_item_calls[-1] == fake_sdk.list_items_calls[-1]

    async def test_double_slash_collapsed(self, fake_sdk):
        # Double / triple slashes collapse to single.
        result = await _publish_then_resolve(
            fake_sdk,
            write_space="//Construct//WorkflowOutputs//Github",
            read_space="Construct/WorkflowOutputs/Github",
        )
        assert result is not None
        assert fake_sdk.create_item_calls[-1] == "Construct/WorkflowOutputs/Github"
        assert fake_sdk.list_items_calls[-1] == "Construct/WorkflowOutputs/Github"


# ---------------------------------------------------------------------------
# name_pattern matching against base name (kind suffix tolerance)
# ---------------------------------------------------------------------------

def _seed_item(
    sdk: _FakeKumihoSDK,
    *,
    space: str,
    name: str,
    kind: str,
    tag: str = "published",
) -> str:
    """Insert an item with a Kumiho-stored ``<base>.<kind>`` name and a
    revision tagged ``tag``. Returns the item kref."""
    sdk._kref_counter += 1
    item_kref = f"kref://item/{sdk._kref_counter}"
    item = {"kref": item_kref, "name": name, "kind": kind, "metadata": {}}
    sdk.items_by_space.setdefault(space, []).append(item)
    sdk._kref_counter += 1
    rev_kref = f"kref://rev/{sdk._kref_counter}"
    sdk.revisions_by_kref[item_kref] = {"kref": rev_kref, "metadata": {}, "tag": tag}
    return item_kref


@pytest.mark.asyncio
class TestNamePatternBaseNameMatching:
    SPACE = "Construct/WorkflowOutputs/Github"

    async def test_name_pattern_matches_base_name(self, fake_sdk):
        # Kumiho stores names as <base>.<kind>. User passes the bare base
        # name as name_pattern — must resolve.
        _seed_item(
            fake_sdk,
            space=self.SPACE,
            name="zeroclaw-repo.research",
            kind="research",
        )
        result = await resolve_entity(
            kind="research",
            tag="published",
            name_pattern="zeroclaw-repo",
            space=self.SPACE,
            mode="latest",
        )
        assert result is not None
        assert result.get("name") == "zeroclaw-repo.research"

    async def test_name_pattern_matches_full_name(self, fake_sdk):
        # Backward compat: user who already knows the suffix and passes
        # the full ``<base>.<kind>`` form must still resolve.
        _seed_item(
            fake_sdk,
            space=self.SPACE,
            name="zeroclaw-repo.research",
            kind="research",
        )
        result = await resolve_entity(
            kind="research",
            tag="published",
            name_pattern="zeroclaw-repo.research",
            space=self.SPACE,
            mode="latest",
        )
        assert result is not None
        assert result.get("name") == "zeroclaw-repo.research"

    async def test_name_pattern_glob_still_works(self, fake_sdk):
        # A glob like ``zeroclaw-*`` against the base name must still match.
        _seed_item(
            fake_sdk,
            space=self.SPACE,
            name="zeroclaw-repo.research",
            kind="research",
        )
        result = await resolve_entity(
            kind="research",
            tag="published",
            name_pattern="zeroclaw-*",
            space=self.SPACE,
            mode="latest",
        )
        assert result is not None
        assert result.get("name") == "zeroclaw-repo.research"

    async def test_name_pattern_doesnt_overstrip(self, fake_sdk):
        # An item whose name happens to end in ``.foo`` but whose kind is
        # ``research`` (not ``foo``) must NOT have ``.foo`` stripped.
        # The kind filter rejects it first; even if a user queried with
        # the dotted form, the suffix-strip is conditional on the suffix
        # equalling the item's own kind.
        _seed_item(
            fake_sdk,
            space=self.SPACE,
            name="something.notthekind",
            kind="research",
        )
        # Querying with kind=research, name_pattern=something — would
        # ONLY match if we wrongly stripped ``.notthekind`` off. We don't.
        result = await resolve_entity(
            kind="research",
            tag="published",
            name_pattern="something",
            space=self.SPACE,
            mode="latest",
        )
        assert result is None
        # And querying for an item whose kind doesn't match is rejected
        # by the kind filter before name matching even runs.
        _seed_item(
            fake_sdk,
            space=self.SPACE,
            name="zeroclaw-repo.foo",
            kind="foo",
        )
        result = await resolve_entity(
            kind="research",
            tag="published",
            name_pattern="zeroclaw-repo.foo",
            space=self.SPACE,
            mode="latest",
        )
        assert result is None

    async def test_resolve_logs_diagnostics(self, fake_sdk, capsys):
        _seed_item(
            fake_sdk,
            space=self.SPACE,
            name="zeroclaw-repo.research",
            kind="research",
        )
        await resolve_entity(
            kind="research",
            tag="published",
            name_pattern="zeroclaw-repo",
            space=self.SPACE,
            mode="latest",
        )
        err = capsys.readouterr().err
        assert "resolve_entity: list_items(" in err
        assert "resolve_entity: kind=research" in err
        assert "resolve_entity: name_pattern=" in err
        assert "resolve_entity: matched zeroclaw-repo.research" in err

        # And the NO MATCH path logs a clearly identifiable line.
        result = await resolve_entity(
            kind="research",
            tag="published",
            name_pattern="does-not-exist",
            space=self.SPACE,
            mode="latest",
        )
        assert result is None
        err = capsys.readouterr().err
        assert "resolve_entity: NO MATCH" in err
