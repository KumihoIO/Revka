"""#459: the operator must forward an untrusted marker to the sidecar.

The session-manager spawn gate (codexSpawnRefusal) refuses an untrusted
permission-bypassing CLI only when it receives ``trusted: false``. Since the
sidecar is the *primary* spawn path (tried before the subprocess fallback that
holds the Python gate), ``_try_sidecar_create`` must put ``trusted=False`` into
the config it POSTs for an untrusted spawn — and must leave it absent otherwise,
so the Claude SDK gate (claude.ts gates on ``trusted !== true``) and the
non-breaking trusted-by-default behavior are preserved.
"""
from __future__ import annotations

import pytest

import operator_mcp.tool_handlers.agents as agents
from operator_mcp.agent_state import CacheSafeParams
from operator_mcp.tool_handlers.agents import _coerce_trusted


class CapturingSidecar:
    def __init__(self) -> None:
        self.captured_config: dict | None = None
        self.socket_path = "/tmp/fake.sock"

    async def ensure_running(self) -> bool:
        return True

    async def create_agent(self, config: dict) -> dict:
        self.captured_config = config
        return {"id": "sidecar-123"}


@pytest.mark.asyncio
async def test_untrusted_codex_forwards_trusted_false(tmp_path, monkeypatch):
    sidecar = CapturingSidecar()
    monkeypatch.setattr(agents, "_sidecar_client", sidecar)
    params = CacheSafeParams(system_prompt="sp", mcp_servers={})

    await agents._try_sidecar_create(
        "agent-codex", "codex", "Coder", str(tmp_path), "do things",
        trusted=False,
        cached_params=params,
        skip_budget_check=True,
    )

    assert sidecar.captured_config is not None
    assert sidecar.captured_config["trusted"] is False


@pytest.mark.asyncio
async def test_trusted_codex_omits_trusted(tmp_path, monkeypatch):
    sidecar = CapturingSidecar()
    monkeypatch.setattr(agents, "_sidecar_client", sidecar)
    params = CacheSafeParams(system_prompt="sp", mcp_servers={})

    await agents._try_sidecar_create(
        "agent-codex", "codex", "Coder", str(tmp_path), "do things",
        cached_params=params,  # trusted defaults to True
        skip_budget_check=True,
    )

    assert sidecar.captured_config is not None
    # Absent (not True) so claude.ts stays gated and codex stays non-breaking.
    assert "trusted" not in sidecar.captured_config


class TestCoerceTrusted:
    def test_booleans_pass_through(self):
        assert _coerce_trusted(True) is True
        assert _coerce_trusted(False) is False

    def test_missing_defaults_trusted(self):
        assert _coerce_trusted(None) is True

    def test_stringified_false_is_untrusted(self):
        # A security gate must not fail open on bool("false") == True.
        for v in ("false", "False", "0", "no", "  FALSE  ", ""):
            assert _coerce_trusted(v) is False

    def test_other_strings_are_trusted(self):
        assert _coerce_trusted("true") is True
        assert _coerce_trusted("1") is True
