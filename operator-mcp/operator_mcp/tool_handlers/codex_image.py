"""Codex CLI image generation tool.

Uses ``codex exec`` with the built-in ``image_generation`` tool to generate
one or more PNG images. Up to 5 images can be generated in parallel via
``asyncio.gather``; for more than 5, the caller invokes the tool repeatedly
in batches.

Optional integrations:

  * **Live Canvas** — when ``canvas`` is truthy, the generated images are
    pushed to the Construct Live Canvas as a single HTML frame so the user
    sees them appear in the dashboard.
  * **Kumiho artifact registration** — when ``register_artifact`` is true
    (default), each generated PNG is attached as an artifact under the
    active harness project's ``Images`` space, with a revision tagged
    ``latest``. This makes generated images discoverable via graph search
    and persistent across sessions.

The skill template originates from the open-source ``codex-image`` skill
in the ``revfactory/skills`` repo (no LICENSE on that repo at the time of
porting); this implementation is an independent rewrite in Python rather
than a derived copy.
"""
from __future__ import annotations

import asyncio
import base64
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from .._log import _log
from ..construct_config import harness_project
from ..gateway_client import ConstructGatewayClient

# `KUMIHO_SDK` lives on `..operator_mcp` and importing it eagerly creates a
# circular dependency. The handler imports it inside `_register_artifacts`
# instead — same pattern used by workflow/memory.py and patterns/handoff.py.


# Cached availability check — codex executable resolved + authenticated.
# Stores the absolute path to codex so subprocess_exec doesn't have to
# re-resolve PATHEXT on every call. Restart the operator MCP if codex auth
# or install location changes.
_CODEX_AVAILABILITY: dict[str, Any] | None = None


def _resolve_codex_executable() -> str | None:
    """Find the codex executable, returning an absolute path or None.

    `asyncio.create_subprocess_exec` calls CreateProcess on Windows which
    does NOT apply PATHEXT — handing it bare "codex" fails with WinError 2
    even when `codex` runs fine from a shell, because npm installs it as
    `codex.CMD`. `shutil.which()` does the right thing (walks PATH +
    PATHEXT on Windows), so we use it as the primary resolver.

    On Windows, if PATH doesn't include the npm global bin dir, we also
    check the conventional install locations (`%APPDATA%\\npm\\codex.CMD`
    and `%LOCALAPPDATA%\\npm\\codex.CMD`) as a fallback.
    """
    found = shutil.which("codex")
    if found:
        return found

    if sys.platform == "win32":
        candidates: list[str] = []
        appdata = os.environ.get("APPDATA")
        local_appdata = os.environ.get("LOCALAPPDATA")
        for root in (appdata, local_appdata):
            if not root:
                continue
            for ext in ("CMD", "cmd", "EXE", "exe"):
                candidates.append(os.path.join(root, "npm", f"codex.{ext}"))
        for c in candidates:
            if os.path.isfile(c):
                return c

    return None


async def _check_codex_available() -> dict[str, Any]:
    global _CODEX_AVAILABILITY
    if _CODEX_AVAILABILITY is not None:
        return _CODEX_AVAILABILITY

    codex_path = _resolve_codex_executable()
    if not codex_path:
        _CODEX_AVAILABILITY = {
            "ok": False,
            "error": (
                "codex CLI not found. Install from https://github.com/openai/codex "
                "and run `codex login`. On Windows, ensure your PATH includes the "
                "npm global bin (e.g. `%APPDATA%\\npm`)."
            ),
        }
        return _CODEX_AVAILABILITY

    try:
        proc = await asyncio.create_subprocess_exec(
            codex_path,
            "login",
            "status",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
    except Exception as exc:
        _CODEX_AVAILABILITY = {
            "ok": False,
            "error": f"codex login status failed (executable: {codex_path}): {exc}",
        }
        return _CODEX_AVAILABILITY

    out = (stdout or b"").decode("utf-8", errors="replace")
    err = (stderr or b"").decode("utf-8", errors="replace")
    # Some codex builds (notably the Windows `.CMD` shim) print the login
    # status to stderr instead of stdout, so check both streams.
    combined = f"{out}\n{err}"
    if "Logged in" not in combined:
        _CODEX_AVAILABILITY = {
            "ok": False,
            "error": (
                "codex CLI is not authenticated. Run `codex login`. "
                f"Status: {out.strip() or err.strip() or 'unknown'}"
            ),
        }
        return _CODEX_AVAILABILITY

    _CODEX_AVAILABILITY = {"ok": True, "executable": codex_path}
    return _CODEX_AVAILABILITY


def _resolve_output_paths(
    output_path: str,
    count: int,
    pattern: str | None,
    cwd: str,
) -> list[Path]:
    """Compute absolute output paths for ``count`` generated images."""
    base = Path(os.path.expanduser(cwd))
    if count == 1:
        out = Path(os.path.expanduser(output_path))
        return [out if out.is_absolute() else base / out]

    paths: list[Path] = []
    if pattern:
        for n in range(1, count + 1):
            name = pattern.replace("{n}", str(n))
            p = Path(os.path.expanduser(name))
            paths.append(p if p.is_absolute() else base / p)
        return paths

    out = Path(os.path.expanduser(output_path))
    suffix = out.suffix or ".png"
    stem = out.stem
    parent = out.parent
    for n in range(1, count + 1):
        derived = parent / f"{stem}-{n}{suffix}"
        paths.append(derived if derived.is_absolute() else base / derived)
    return paths


def _build_codex_prompt(prompt: str, output_path: Path) -> str:
    """Compose the natural-language prompt for ``codex exec``.

    The codex CLI invokes its built-in ``image_generation`` tool when the
    prompt asks for an image; we steer it to write the PNG to a specific
    relative path inside the workspace and to report only that path.
    """
    rel = output_path.name
    return (
        f"Use the image_generation tool to create an image for: {prompt!r}. "
        f"Save the result as ./{rel} in the current working directory. "
        f"Reply with only the absolute file path on a single line, no other text."
    )


async def _spawn_codex_image(
    prompt: str,
    output_path: Path,
    cwd: Path,
    codex_executable: str | None = None,
) -> dict[str, Any]:
    """Run a single ``codex exec`` and return success/failure with the path.

    ``codex_executable`` is the absolute path resolved by
    ``_resolve_codex_executable``; required on Windows because
    ``create_subprocess_exec`` does not apply PATHEXT to the bare name.
    """
    if codex_executable is None:
        codex_executable = _resolve_codex_executable() or "codex"
    # Use the OS temp dir so this works on Windows too.
    import tempfile

    log_path = os.path.join(
        tempfile.gettempdir(), f"codex-img-{os.getpid()}-{output_path.stem}.md"
    )
    cmd = [
        codex_executable,
        "exec",
        "--sandbox",
        "workspace-write",
        "--skip-git-repo-check",
        "--cd",
        str(cwd),
        "-o",
        log_path,
        _build_codex_prompt(prompt, output_path),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
    except Exception as exc:
        return {"ok": False, "path": str(output_path), "error": f"spawn failed: {exc}"}

    if proc.returncode != 0:
        tail = (stderr or b"").decode("utf-8", errors="replace")[:500]
        return {
            "ok": False,
            "path": str(output_path),
            "error": f"codex exec exited {proc.returncode}: {tail}",
        }
    if not output_path.exists() or output_path.stat().st_size == 0:
        return {
            "ok": False,
            "path": str(output_path),
            "error": (
                f"output PNG missing or empty at {output_path}. "
                "Check that `codex features list` reports image_generation as enabled."
            ),
        }
    return {
        "ok": True,
        "path": str(output_path),
        "size": output_path.stat().st_size,
    }


async def _push_to_canvas(
    paths: list[Path],
    canvas_id: str,
    gw: ConstructGatewayClient,
) -> dict[str, Any]:
    """Build an HTML gallery of the generated images and push to Live Canvas.

    Inlines each image as a base64 data URI so the canvas frame is
    self-contained and doesn't depend on the dashboard being able to
    read local files.
    """
    if not gw._available:
        return {"error": "Construct gateway not available — canvas push skipped"}

    try:
        import httpx
    except ImportError:
        return {"error": "httpx not installed — canvas push skipped"}

    figures: list[str] = []
    for p in paths:
        if not p.exists():
            continue
        try:
            data = base64.b64encode(p.read_bytes()).decode("ascii")
        except Exception as exc:
            _log(f"canvas: failed to read {p}: {exc}")
            continue
        figures.append(
            '<figure style="margin:0 0 1rem 0">'
            f'<img src="data:image/png;base64,{data}" '
            'style="max-width:100%;height:auto;border-radius:8px;'
            'box-shadow:0 4px 16px rgba(0,0,0,0.4)"/>'
            '<figcaption style="font-size:0.85rem;color:#888;margin-top:0.25rem">'
            f"{p.name}</figcaption></figure>"
        )

    if not figures:
        return {"error": "no readable images to push"}

    html = (
        "<!DOCTYPE html><html><body "
        'style="font-family:system-ui,-apple-system,sans-serif;'
        'padding:1.5rem;background:#0b0b0b;color:#eee;margin:0">'
        f'<h1 style="margin:0 0 1rem 0;font-size:1.05rem;font-weight:500;'
        f'color:#aaa">Codex image generation — {len(figures)} result'
        f'{"s" if len(figures) != 1 else ""}</h1>'
        f'{"".join(figures)}'
        "</body></html>"
    )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{gw.gateway_url}/api/canvas/{canvas_id}",
                json={"content": html, "content_type": "html"},
                headers=gw._headers(),
            )
            if resp.status_code not in (200, 201):
                return {
                    "error": f"canvas {resp.status_code}: {resp.text[:200]}",
                }
            data = resp.json()
            return {
                "canvas_id": canvas_id,
                "frame_id": data.get("frame_id", ""),
                "image_count": len(figures),
            }
    except Exception as exc:
        return {"error": f"canvas push failed: {exc}"}


async def _register_artifacts(
    paths: list[Path],
    prompt: str,
    item_name: str | None,
    space: str | None = None,
) -> dict[str, Any]:
    """Create a Kumiho item under ``<harness>/<space>`` with file artifacts.

    The ``space`` argument is a path relative to the harness project — e.g.
    ``"Images"`` (default) or ``"Marketing/Logos"``. Multi-segment paths
    are supported; the leftmost segment is the Kumiho space directly under
    the harness project, the rest is treated as a sub-namespace.

    Each generated PNG is attached to the same revision so they can be
    retrieved together. Returns the item kref, revision kref, and a list
    of artifact krefs.
    """
    from ..operator_mcp import KUMIHO_SDK  # lazy: avoids import cycle

    project = harness_project()
    space_relative = (space or "Images").strip().strip("/")
    if not space_relative:
        space_relative = "Images"
    space_path = f"{project}/{space_relative}"
    # The Kumiho `ensure_space` API takes (project, top_level_space). For
    # multi-segment paths like "Marketing/Logos" we ensure the top segment
    # exists; subspaces are created lazily by `create_item`.
    top_space = space_relative.split("/", 1)[0]

    # Derive item name from first PNG's stem unless caller supplied one.
    if not item_name and paths:
        first_stem = paths[0].stem
        # Drop the auto-appended -1/-2/... suffix when batch-generating.
        for sep in ("-",):
            if sep in first_stem:
                head, _, tail = first_stem.rpartition(sep)
                if tail.isdigit() and head:
                    first_stem = head
                    break
        item_name = first_stem or "image"

    if not item_name:
        return {"error": "no item name resolved"}

    try:
        await KUMIHO_SDK.ensure_space(project, top_space)
    except Exception as exc:
        _log(f"codex_image: ensure_space warning: {exc}")

    try:
        item = await KUMIHO_SDK.create_item(
            space_path=space_path,
            name=item_name,
            kind="image",
            metadata={
                "prompt": prompt[:500],
                "count": str(len(paths)),
                "source": "codex_image_generation",
            },
        )
    except Exception as exc:
        return {"error": f"create_item failed: {exc}"}

    item_kref = (
        item.get("kref") if isinstance(item, dict) else getattr(item, "kref", "")
    )
    if not item_kref:
        return {"error": "create_item returned no kref"}

    try:
        rev = await KUMIHO_SDK.create_revision(
            item_kref=item_kref,
            metadata={
                "prompt": prompt[:500],
                "files": ",".join(p.name for p in paths),
                "count": str(len(paths)),
            },
            tag="latest",
        )
    except Exception as exc:
        return {
            "item_kref": item_kref,
            "error": f"create_revision failed: {exc}",
        }

    rev_kref = rev.get("kref") if isinstance(rev, dict) else getattr(rev, "kref", "")
    if not rev_kref:
        return {"item_kref": item_kref, "error": "create_revision returned no kref"}

    artifact_krefs: list[str] = []
    for p in paths:
        if not p.exists():
            continue
        try:
            art = await KUMIHO_SDK.create_artifact(rev_kref, p.name, str(p))
        except Exception as exc:
            _log(f"codex_image: create_artifact failed for {p}: {exc}")
            continue
        ak = art.get("kref") if isinstance(art, dict) else getattr(art, "kref", "")
        if ak:
            artifact_krefs.append(ak)

    return {
        "item_kref": item_kref,
        "revision_kref": rev_kref,
        "artifact_krefs": artifact_krefs,
        "space_path": space_path,
    }


async def tool_generate_image_codex(
    args: dict[str, Any],
    gw: ConstructGatewayClient,
) -> dict[str, Any]:
    """Generate ``count`` PNGs by spawning ``codex exec`` subprocesses.

    Inputs (see registration in ``operator_mcp.py`` for the JSON schema):

    * ``prompt`` — required, image description
    * ``output_path`` — required, target PNG path (relative or absolute)
    * ``cwd`` — optional, defaults to ``~/.construct/workspace``
    * ``count`` — optional 1..5, default 1
    * ``output_pattern`` — optional template with ``{n}`` placeholder when
      ``count > 1``; if omitted, derived as ``<stem>-N.<ext>``
    * ``canvas`` — bool or canvas_id string; pushes a gallery frame
    * ``register_artifact`` — bool, default True; creates a Kumiho item
    * ``space`` — Kumiho space relative to the harness project, default
      ``Images``. Multi-segment paths like ``Marketing/Logos`` supported.
    * ``item_name`` — optional override for the Kumiho item name
    """
    prompt = (args.get("prompt") or "").strip()
    output_path = (args.get("output_path") or "").strip()
    cwd = args.get("cwd") or "~/.construct/workspace"
    try:
        count = int(args.get("count", 1))
    except (TypeError, ValueError):
        return {"error": "count must be an integer 1..5"}
    pattern = args.get("output_pattern")
    canvas_arg = args.get("canvas", False)
    register = args.get("register_artifact", True)
    if not isinstance(register, bool):
        register = bool(register)
    item_name = args.get("item_name")
    space = args.get("space")

    if not prompt:
        return {"error": "prompt is required"}
    if not output_path:
        return {"error": "output_path is required"}
    if not 1 <= count <= 5:
        return {
            "error": (
                f"count must be 1..5 (got {count}); for more images, "
                "call the tool repeatedly in batches"
            )
        }

    avail = await _check_codex_available()
    if not avail.get("ok"):
        return {"error": avail.get("error", "codex unavailable")}

    codex_exe = avail.get("executable")
    cwd_path = Path(os.path.expanduser(cwd))
    cwd_path.mkdir(parents=True, exist_ok=True)
    paths = _resolve_output_paths(output_path, count, pattern, cwd)
    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)

    results = await asyncio.gather(
        *(_spawn_codex_image(prompt, p, cwd_path, codex_exe) for p in paths),
        return_exceptions=True,
    )

    successes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for r in results:
        if isinstance(r, dict) and r.get("ok"):
            successes.append(r)
        elif isinstance(r, dict):
            failures.append(r)
        else:
            failures.append({"error": f"unexpected exception: {r!r}"})

    response: dict[str, Any] = {
        "generated": len(successes),
        "requested": count,
        "files": [r["path"] for r in successes],
    }
    if failures:
        response["failures"] = failures

    if not successes:
        response["error"] = "no images generated; see failures"
        return response

    success_paths = [Path(r["path"]) for r in successes]

    if canvas_arg:
        canvas_id = canvas_arg if isinstance(canvas_arg, str) else "default"
        response["canvas"] = await _push_to_canvas(success_paths, canvas_id, gw)

    if register:
        response["artifact"] = await _register_artifacts(
            success_paths, prompt, item_name, space=space
        )

    return response


def _reset_availability_cache() -> None:
    """Test helper: clear the cached codex availability probe."""
    global _CODEX_AVAILABILITY
    _CODEX_AVAILABILITY = None
