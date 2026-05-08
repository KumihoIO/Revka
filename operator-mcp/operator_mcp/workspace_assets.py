"""Mint HMAC-signed workspace-asset URLs.

Mirrors the Rust gateway's `src/gateway/workspace_assets.rs`. The shared
HMAC key is the gateway's service-token at ``~/.construct/service-token``,
which both the gateway and operator-mcp already read for their own auth
flows. Tools call ``sign_workspace_url(rel_path)`` to mint URLs they
embed in canvas/HTML output; the gateway verifies sig + exp on each
request.

URLs are returned as relative paths (``/workspace/<rel>?exp=…&sig=…``)
so the browser resolves against whatever origin the dashboard is on —
same URL works locally and through tunnels.
"""
from __future__ import annotations

import functools
import hashlib
import hmac
import os
import time
from pathlib import Path

DEFAULT_TTL_SECS = 3600


@functools.lru_cache(maxsize=1)
def _service_token_bytes() -> bytes:
    """Read the gateway's service-token. Cached for the operator-mcp
    process lifetime — the file rarely changes and reading it on every
    sign call would add unnecessary I/O.
    """
    token_path = Path(
        os.environ.get(
            "CONSTRUCT_SERVICE_TOKEN_PATH",
            str(Path.home() / ".construct" / "service-token"),
        )
    )
    return token_path.read_text(encoding="utf-8").strip().encode("utf-8")


def _hmac_hex(rel_path: str, exp: int, secret: bytes) -> str:
    msg = rel_path.encode("utf-8") + b"\n" + str(exp).encode("ascii")
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


def sign_workspace_url(rel_path: str, ttl_secs: int = DEFAULT_TTL_SECS) -> str:
    """Return ``/workspace/<rel_path>?exp=…&sig=…`` valid for ``ttl_secs``.

    ``rel_path`` is relative to ``config.workspace_dir`` (typically
    ``~/.construct/workspace``). Use forward slashes; the gateway
    normalizes both forward and backslash separators on Windows.
    """
    rel = rel_path.replace("\\", "/").lstrip("/")
    secret = _service_token_bytes()
    exp = int(time.time()) + int(ttl_secs)
    sig = _hmac_hex(rel, exp, secret)
    return f"/workspace/{rel}?exp={exp}&sig={sig}"


def workspace_url_for_path(absolute_path: str | Path, workspace_dir: str | Path) -> str | None:
    """Compute a signed URL for an absolute path under ``workspace_dir``.

    Returns ``None`` if the path is outside the workspace (we never sign
    URLs that would let the gateway serve files outside its configured
    workspace root).
    """
    abs_path = Path(absolute_path).resolve()
    root = Path(workspace_dir).resolve()
    try:
        rel = abs_path.relative_to(root)
    except ValueError:
        return None
    return sign_workspace_url(str(rel).replace("\\", "/"))
