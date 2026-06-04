"""Tests for operator_mcp.tracking — kref codec + email link rewriter.

The codec mirrors the standalone kref_encode.py builtin (test_python_step
covers the script path); these tests focus on the importable module
that the email step and future click handlers use directly.
"""
from __future__ import annotations

import pytest

from operator_mcp.tracking import (
    decode_kref,
    encode_kref,
    rewrite_links_with_tracker,
)


# ── codec round-trips ───────────────────────────────────────────────


class TestCodec:
    def test_roundtrip_no_secret(self):
        kref = "kref://Revka/Sessions/sess-1/Outcomes/x.outcome?r=1"
        token = encode_kref(kref)
        decoded, verified = decode_kref(token)
        assert decoded == kref
        # No secret → no integrity claim, even on a fresh-minted token.
        assert verified is False

    def test_roundtrip_with_secret_verifies(self):
        kref = "kref://Revka/Outreach/contacts/acme.contact?r=2"
        token = encode_kref(kref, secret="s3cret")
        decoded, verified = decode_kref(token, secret="s3cret")
        assert decoded == kref
        assert verified is True

    def test_wrong_secret_fails_verification(self):
        # Tamper detection: same token decoded with a different secret
        # MUST surface verified=False so a click handler can drop the
        # event rather than counting forged clicks.
        kref = "kref://Revka/Outreach/contacts/acme.contact?r=2"
        token = encode_kref(kref, secret="right")
        _decoded, verified = decode_kref(token, secret="wrong")
        assert verified is False

    def test_token_is_url_safe(self):
        # No padding, no slashes, no plus signs — safe to drop into a
        # URL path segment without further escaping.
        token = encode_kref("kref://Revka/x.item")
        assert "=" not in token
        assert "/" not in token
        assert "+" not in token

    def test_kref_with_query_string_roundtrips(self):
        # krefs themselves contain colons (kref://) and may contain ?r=N
        # — make sure the colon-as-separator on signed tokens doesn't
        # truncate the kref body.
        kref = "kref://CognitiveMemory/Skills/foo.skilldef?r=5&t=published"
        token = encode_kref(kref, secret="x")
        decoded, verified = decode_kref(token, secret="x")
        assert decoded == kref
        assert verified is True


# ── link rewriter ───────────────────────────────────────────────────


class TestRewrite:
    """The link rewriter wraps every plain http(s) URL in an email body
    with a redirect through the gateway click handler. This is the
    auto-tracking workflow authors get when they set track_clicks=true
    on the email step."""

    def test_wraps_single_url(self):
        out = rewrite_links_with_tracker(
            "Check out https://revka.example.com today.",
            encoded_kref="ABC123",
            base_url="https://gw.example.com",
        )
        # The original URL becomes the `u=` query param of the wrapper.
        assert "https://gw.example.com/track/c/ABC123?u=" in out
        # Trailing period stays OUTSIDE the wrapped URL — otherwise it
        # gets URL-encoded into the destination and breaks the redirect.
        assert out.endswith("today.")

    def test_wraps_multiple_urls(self):
        out = rewrite_links_with_tracker(
            "First https://a.com then https://b.com end.",
            encoded_kref="REF",
            base_url="https://gw",
        )
        assert out.count("/track/c/REF?u=") == 2

    def test_strips_base_url_trailing_slash(self):
        # base_url="https://gw/" or "https://gw" should produce the same
        # output (no double slash before /track/c/).
        a = rewrite_links_with_tracker(
            "https://x.com", encoded_kref="K", base_url="https://gw"
        )
        b = rewrite_links_with_tracker(
            "https://x.com", encoded_kref="K", base_url="https://gw/"
        )
        assert a == b

    def test_url_with_query_string_preserves_query(self):
        # https://x.com/foo?a=1&b=2 must round-trip — losing the query
        # would break the user's actual destination.
        out = rewrite_links_with_tracker(
            "Visit https://x.com/foo?a=1&b=2 here.",
            encoded_kref="K",
            base_url="https://gw",
        )
        # The original query string is URL-encoded into the u= param,
        # so &b=2 doesn't accidentally chain onto our wrapper's URL.
        assert "u=https%3A%2F%2Fx.com%2Ffoo%3Fa%3D1%26b%3D2" in out

    def test_no_urls_no_change(self):
        body = "No links here, just words."
        out = rewrite_links_with_tracker(
            body, encoded_kref="K", base_url="https://gw"
        )
        assert out == body

    def test_missing_kref_raises(self):
        # Empty kref means tracking is misconfigured — fail loudly so
        # the workflow doesn't silently send "tracked" emails that
        # actually log nothing.
        with pytest.raises(ValueError, match="encoded_kref"):
            rewrite_links_with_tracker(
                "https://x.com", encoded_kref="", base_url="https://gw"
            )

    def test_missing_base_url_raises(self):
        with pytest.raises(ValueError, match="base_url"):
            rewrite_links_with_tracker(
                "https://x.com", encoded_kref="K", base_url=""
            )

    def test_url_inside_parens_not_consumed(self):
        # "Visit (https://x.com) for more" — closing paren should stay
        # outside the wrapped URL.
        out = rewrite_links_with_tracker(
            "Visit (https://x.com) for more",
            encoded_kref="K",
            base_url="https://gw",
        )
        assert ") for more" in out
