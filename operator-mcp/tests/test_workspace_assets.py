"""Tests for `operator_mcp.workspace_assets`.

Pins the URL format and HMAC scheme so the Python signer stays in sync
with the Rust verifier in `src/gateway/workspace_assets.rs`. If either
side changes the message format, expiry encoding, or hash algorithm,
these tests need to update along with the Rust verifier.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from pathlib import Path

import pytest

from operator_mcp import workspace_assets


@pytest.fixture(autouse=True)
def _reset_token_cache():
    """`_service_token_bytes` is `lru_cache`d; reset between tests so each
    can monkeypatch the env independently."""
    workspace_assets._service_token_bytes.cache_clear()
    yield
    workspace_assets._service_token_bytes.cache_clear()


@pytest.fixture
def fake_token(tmp_path, monkeypatch):
    token_file = tmp_path / "service-token"
    token_file.write_text("test-token-value-1234", encoding="utf-8")
    monkeypatch.setenv("CONSTRUCT_SERVICE_TOKEN_PATH", str(token_file))
    return b"test-token-value-1234"


def test_sign_workspace_url_format(fake_token):
    url = workspace_assets.sign_workspace_url("Construct/Images/foo.png", ttl_secs=3600)
    assert url.startswith("/workspace/Construct/Images/foo.png?")
    # exp + sig query params present.
    m = re.search(r"\?exp=(\d+)&sig=([0-9a-f]+)$", url)
    assert m, f"unexpected URL shape: {url}"


def test_sign_uses_hmac_sha256_over_path_newline_exp(fake_token):
    """Pin the exact HMAC scheme so the Rust verifier matches."""
    url = workspace_assets.sign_workspace_url("a/b.png", ttl_secs=10)
    m = re.search(r"\?exp=(\d+)&sig=([0-9a-f]+)$", url)
    exp, sig = m.group(1), m.group(2)
    msg = b"a/b.png\n" + exp.encode("ascii")
    expected = hmac.new(fake_token, msg, hashlib.sha256).hexdigest()
    assert sig == expected


def test_sign_strips_leading_slash_and_normalizes_separators(fake_token):
    url = workspace_assets.sign_workspace_url("\\Construct\\Images\\foo.png")
    assert url.startswith("/workspace/Construct/Images/foo.png?")


def test_workspace_url_for_path_inside_workspace(tmp_path, fake_token):
    workspace = tmp_path / "ws"
    nested = workspace / "Construct" / "Images"
    nested.mkdir(parents=True)
    target = nested / "foo.png"
    target.write_bytes(b"\x89PNG")
    url = workspace_assets.workspace_url_for_path(target, workspace)
    assert url is not None
    assert url.startswith("/workspace/Construct/Images/foo.png?")


def test_workspace_url_for_path_outside_workspace_returns_none(tmp_path, fake_token):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    elsewhere = tmp_path / "elsewhere" / "foo.png"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(b"x")
    assert workspace_assets.workspace_url_for_path(elsewhere, workspace) is None
