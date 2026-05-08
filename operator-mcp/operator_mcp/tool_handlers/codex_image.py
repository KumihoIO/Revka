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
import subprocess
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


def _is_per_user_npm_install(executable: str) -> bool:
    """Heuristic: does ``executable`` look like a per-user npm Windows install?

    npm's per-user global install puts shims under ``%APPDATA%\\npm`` (or
    ``%LOCALAPPDATA%\\npm``). Codex's Windows sandbox helper relies on
    ``CreateProcessAsUserW`` which requires ``SE_ASSIGNPRIMARYTOKEN_NAME`` —
    a privilege that **per-user** installs don't get. As a result the
    sandbox helper fails on these installs (``CreateProcessAsUserW failed: 5``),
    blocking codex from loading its internal ``imagegen`` SKILL.md and
    silently making image generation impossible under the default sandbox.

    Admin-installed codex (``%ProgramFiles%\\nodejs\\``, system npm prefix)
    has the privilege and works correctly. The detection here lets us pick
    a safer default sandbox automatically — see ``_default_sandbox()``.
    """
    if sys.platform != "win32" or not executable:
        return False
    # Normalize to backslash form so comparison works regardless of how
    # the path got into the env (forward-slash mixed paths happen).
    exe_lower = executable.lower().replace("/", "\\")
    for env_var in ("APPDATA", "LOCALAPPDATA"):
        root = os.environ.get(env_var)
        if not root:
            continue
        npm_dir = (root + "\\npm").lower().replace("/", "\\")
        if exe_lower.startswith(npm_dir + "\\") or exe_lower == npm_dir:
            return True
    return False


def _default_sandbox(executable: str | None = None) -> str:
    """Pick the right ``--sandbox`` mode for the current install.

    macOS / Linux / Windows-with-admin-codex → ``workspace-write`` (safer:
    codex still operates in a confined workspace).

    Windows per-user npm install → ``danger-full-access``: the sandbox
    helper needs admin-only privileges and can't run there. Without this
    auto-fallback the tool would either pay a ~60 s probe-and-recover tax
    on every first call, or silently produce no PNG on the user's first
    attempt (codex hallucinates a "saved" reply when its imagegen skill
    can't load). Users with healthy installs can still pin
    ``workspace-write`` explicitly via the ``sandbox`` parameter.
    """
    if executable is None:
        executable = _resolve_codex_executable() or ""
    if _is_per_user_npm_install(executable):
        return "danger-full-access"
    return "workspace-write"


def _run_subprocess_sync(
    cmd: list[str], timeout: float | None = None
) -> tuple[int, bytes, bytes]:
    """Synchronous subprocess.run wrapper for use with `asyncio.to_thread`.

    Why not `asyncio.create_subprocess_exec`? On Windows, when the operator
    MCP server's anyio-based event loop is busy with concurrent tasks
    (workflow_loader, event_listener, retry queue, …), an in-loop
    `create_subprocess_exec` call can hang indefinitely waiting for IOCP
    pump cycles that never come. The MCP context starves the subprocess
    machinery while standalone scripts work fine. Pushing the blocking
    `subprocess.run` into a thread pool worker via `asyncio.to_thread`
    sidesteps the proactor entirely and runs reliably across platforms.

    Why ``stdin=subprocess.DEVNULL``? When the operator MCP server is
    spawned via stdio transport (the normal case), its stdin is the
    JSON-RPC pipe from the MCP client. Without explicit redirection,
    child processes inherit that stdin — codex (or its ``codex.CMD``
    cmd-shell shim on Windows) blocks reading from the JSON-RPC stream
    waiting for input that's meant for MCP, and never finishes. Symptom:
    codex spawns, sits at <0.1s CPU, and the tool call hangs forever.
    DEVNULL guarantees the child sees EOF immediately and never blocks
    on a phantom stdin read.
    """
    proc = subprocess.run(
        cmd,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


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
        returncode, stdout, stderr = await asyncio.to_thread(
            _run_subprocess_sync,
            [codex_path, "login", "status"],
            30.0,
        )
    except subprocess.TimeoutExpired:
        _CODEX_AVAILABILITY = {
            "ok": False,
            "error": f"codex login status timed out after 30s (executable: {codex_path})",
        }
        return _CODEX_AVAILABILITY
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
    sandbox: str = "workspace-write",
) -> dict[str, Any]:
    """Run a single ``codex exec`` and return success/failure with the path.

    ``codex_executable`` is the absolute path resolved by
    ``_resolve_codex_executable``; required on Windows because
    ``create_subprocess_exec`` does not apply PATHEXT to the bare name.

    ``sandbox`` is forwarded to codex's ``--sandbox`` flag. Default
    ``workspace-write`` matches the original SKILL.md and works on macOS
    and Linux. On Windows installs where the codex sandbox helper isn't
    set up correctly (``CreateProcessAsUserW failed: 5``), codex can't
    spawn the helper that loads its internal ``imagegen`` skill, so the
    model hallucinates a "saved" reply without actually calling
    ``image_generation``. Passing ``danger-full-access`` bypasses the
    helper entirely. The proper fix is to repair the codex install
    (admin reinstall on Windows); the override exists as an escape hatch.
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
        sandbox,
        "--skip-git-repo-check",
        "--cd",
        str(cwd),
        "-o",
        log_path,
        _build_codex_prompt(prompt, output_path),
    ]
    # Run via `asyncio.to_thread(subprocess.run, ...)` rather than
    # `asyncio.create_subprocess_exec`. See `_run_subprocess_sync` for the
    # rationale (Windows + MCP anyio loop hang). Each parallel codex spawn
    # gets its own thread-pool worker, so `asyncio.gather` over N spawns
    # still gives genuine parallelism.
    try:
        returncode, stdout, stderr = await asyncio.to_thread(
            _run_subprocess_sync, cmd, None
        )
    except Exception as exc:
        return {"ok": False, "path": str(output_path), "error": f"spawn failed: {exc}"}

    if returncode != 0:
        tail = (stderr or b"").decode("utf-8", errors="replace")[:500]
        return {
            "ok": False,
            "path": str(output_path),
            "error": f"codex exec exited {returncode}: {tail}",
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


import re

# Default workspace root for kref-mirroring artifact storage. Match Construct's
# `~/.construct/workspace` convention so the on-disk layout is predictable.
_WORKSPACE_ROOT = Path(os.path.expanduser("~/.construct/workspace"))

# Parses the revision number from a kref like
# `kref://Construct/Images/foo.image?r=3` → `3`.
_KREF_REVISION_RE = re.compile(r"\?r=(\d+)")


def _derive_item_name(
    output_path: str, count: int, explicit: str | None = None
) -> str:
    """Compute the Kumiho item name used as the second-to-last path segment.

    Caller-supplied ``item_name`` wins. Otherwise we derive from the
    output filename stem; for batch mode (``count > 1``) the trailing
    ``-N`` numeric suffix that the resolver auto-appends is stripped.
    """
    if explicit:
        return explicit
    stem = Path(output_path).stem or "image"
    if count == 1:
        return stem
    # Batch mode: caller's `output_path` is e.g. "logo.png" and the
    # resolver expands to logo-1.png/logo-2.png/…; strip the suffix
    # so the item name is the bare stem.
    return stem


def _resolve_space(space: str | None) -> tuple[str, str, str]:
    """Return ``(project, space_relative, top_space)`` for the active harness.

    ``space_relative`` is the path under ``<harness>/`` (default ``Images``);
    multi-segment values like ``Marketing/Logos`` are supported. ``top_space``
    is the leftmost segment which Kumiho's ``ensure_space`` API requires.
    """
    project = harness_project()
    space_relative = (space or "Images").strip().strip("/") or "Images"
    top_space = space_relative.split("/", 1)[0]
    return project, space_relative, top_space


def _kref_artifact_dir(
    project: str, space_relative: str, item_name: str, kind: str, revision_number: int
) -> Path:
    """Compute the on-disk directory mirroring a Kumiho revision kref.

    Returns ``<workspace>/<project>/<space>/<item>.<kind>/r<N>/`` so that the
    file layout matches the kref hierarchy. Multi-segment spaces flow through
    as nested directories.
    """
    return (
        _WORKSPACE_ROOT
        / project
        / space_relative
        / f"{item_name}.{kind}"
        / f"r{revision_number}"
    )


async def _create_item_and_revision(
    prompt: str,
    space: str | None,
    item_name: str,
    file_count: int,
    kind: str = "image",
) -> dict[str, Any]:
    """Pre-create the Kumiho item + revision so we know the on-disk path.

    The revision number lets us derive ``r<N>`` for the artifact directory
    BEFORE codex writes anything, so the file lands at the kref-mirroring
    location directly (no post-hoc move). Returns a ``rev_meta`` dict
    that ``_attach_artifacts`` consumes, or ``{"error": ...}`` on failure.
    """
    from ..operator_mcp import KUMIHO_SDK  # lazy: avoids import cycle

    project, space_relative, top_space = _resolve_space(space)
    space_path = f"{project}/{space_relative}"

    try:
        await KUMIHO_SDK.ensure_space(project, top_space)
    except Exception as exc:
        _log(f"codex_image: ensure_space warning: {exc}")

    try:
        item = await KUMIHO_SDK.create_item(
            space_path=space_path,
            name=item_name,
            kind=kind,
            metadata={
                "prompt": prompt[:500],
                "count": str(file_count),
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
                "count": str(file_count),
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

    match = _KREF_REVISION_RE.search(rev_kref)
    revision_number = int(match.group(1)) if match else 1

    return {
        "item_kref": item_kref,
        "revision_kref": rev_kref,
        "revision_number": revision_number,
        "project": project,
        "space_relative": space_relative,
        "space_path": space_path,
        "item_name": item_name,
        "kind": kind,
    }


async def _attach_artifacts(
    rev_meta: dict[str, Any], paths: list[Path]
) -> list[str]:
    """Attach each PNG path as a Kumiho artifact under the existing revision."""
    from ..operator_mcp import KUMIHO_SDK  # lazy: avoids import cycle

    krefs: list[str] = []
    for p in paths:
        if not p.exists():
            continue
        try:
            art = await KUMIHO_SDK.create_artifact(
                rev_meta["revision_kref"], p.name, str(p)
            )
        except Exception as exc:
            _log(f"codex_image: create_artifact failed for {p}: {exc}")
            continue
        ak = art.get("kref") if isinstance(art, dict) else getattr(art, "kref", "")
        if ak:
            krefs.append(ak)
    return krefs


async def tool_generate_image_codex(
    args: dict[str, Any],
    gw: ConstructGatewayClient,
) -> dict[str, Any]:
    """Generate ``count`` PNGs by spawning ``codex exec`` subprocesses.

    Inputs (see registration in ``operator_mcp.py`` for the JSON schema):

    * ``prompt`` — required, image description
    * ``output_path`` — required. When ``register_artifact`` is true, only
      the **filename** is honored (the directory is auto-derived from the
      kref hierarchy). When ``register_artifact`` is false, treated as a
      file path relative to ``cwd``.
    * ``cwd`` — optional, defaults to ``~/.construct/workspace``. Only
      used when ``register_artifact`` is false.
    * ``count`` — optional 1..5, default 1
    * ``output_pattern`` — optional template with ``{n}`` placeholder when
      ``count > 1``; if omitted, derived as ``<stem>-N.<ext>``
    * ``canvas`` — bool or canvas_id string; pushes a gallery frame
    * ``register_artifact`` — bool, default True; creates a Kumiho item +
      revision and lays the PNG(s) out at
      ``<workspace>/<harness>/<space>/<item>.<kind>/r<N>/<filename>`` so
      the on-disk path mirrors the kref hierarchy.
    * ``space`` — Kumiho space relative to the harness project, default
      ``Images``. Multi-segment paths like ``Marketing/Logos`` supported.
    * ``item_name`` — optional override for the Kumiho item name; defaults
      to the bare filename stem of ``output_path``.
    * ``sandbox`` — codex ``--sandbox`` mode. When omitted, defaults to
      ``workspace-write`` on macOS/Linux/admin-installed Windows, and
      ``danger-full-access`` on Windows per-user npm installs whose
      sandbox helper can't spawn child processes (see ``_default_sandbox``).
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
    # If the caller pinned a sandbox, validate it now. If not, defer the
    # auto-default until after `_check_codex_available` so we can pass the
    # actually-resolved codex path to `_default_sandbox()`.
    sandbox_arg = args.get("sandbox")
    if sandbox_arg and sandbox_arg not in {
        "read-only",
        "workspace-write",
        "danger-full-access",
    }:
        return {
            "error": (
                f"sandbox must be one of read-only / workspace-write / "
                f"danger-full-access (got {sandbox_arg!r})"
            )
        }

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
    # Auto-default for the sandbox now that we know which codex install we're
    # talking to. Per-user npm Windows installs can't run codex's sandbox
    # helper and need `danger-full-access` to load the imagegen skill.
    sandbox = sandbox_arg if sandbox_arg else _default_sandbox(codex_exe)

    # Path resolution: when `register_artifact` is on, lay the file out at
    # the kref-mirroring path so the on-disk hierarchy matches Kumiho's
    # graph hierarchy. Pre-create the item + revision so we know the
    # revision number BEFORE codex writes — no post-hoc move required.
    rev_meta: dict[str, Any] | None = None
    response: dict[str, Any] = {"requested": count}
    if register:
        derived_item = _derive_item_name(output_path, count, item_name)
        rev_meta = await _create_item_and_revision(
            prompt=prompt,
            space=space,
            item_name=derived_item,
            file_count=count,
        )
        if "error" in rev_meta:
            # Fall back to the legacy cwd-rooted layout so the user still
            # gets the PNG; surface the registration error in the response.
            response["artifact"] = rev_meta
            register = False
            rev_meta = None

    if rev_meta is not None:
        # kref-mirroring layout: derive the directory and route paths there.
        rev_dir = _kref_artifact_dir(
            rev_meta["project"],
            rev_meta["space_relative"],
            rev_meta["item_name"],
            rev_meta["kind"],
            rev_meta["revision_number"],
        )
        rev_dir.mkdir(parents=True, exist_ok=True)
        cwd_path = rev_dir
        paths = _resolve_output_paths(output_path, count, pattern, str(rev_dir))
    else:
        cwd_path = Path(os.path.expanduser(cwd))
        cwd_path.mkdir(parents=True, exist_ok=True)
        paths = _resolve_output_paths(output_path, count, pattern, cwd)

    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)

    results = await asyncio.gather(
        *(
            _spawn_codex_image(prompt, p, cwd_path, codex_exe, sandbox=sandbox)
            for p in paths
        ),
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

    response["generated"] = len(successes)
    response["files"] = [r["path"] for r in successes]
    if failures:
        response["failures"] = failures

    if not successes:
        response["error"] = "no images generated; see failures"
        return response

    success_paths = [Path(r["path"]) for r in successes]

    if canvas_arg:
        canvas_id = canvas_arg if isinstance(canvas_arg, str) else "default"
        response["canvas"] = await _push_to_canvas(success_paths, canvas_id, gw)

    if rev_meta is not None:
        artifact_krefs = await _attach_artifacts(rev_meta, success_paths)
        response["artifact"] = {
            "item_kref": rev_meta["item_kref"],
            "revision_kref": rev_meta["revision_kref"],
            "revision_number": rev_meta["revision_number"],
            "artifact_krefs": artifact_krefs,
            "space_path": rev_meta["space_path"],
            "directory": str(_kref_artifact_dir(
                rev_meta["project"],
                rev_meta["space_relative"],
                rev_meta["item_name"],
                rev_meta["kind"],
                rev_meta["revision_number"],
            )),
        }

    return response


def _reset_availability_cache() -> None:
    """Test helper: clear the cached codex availability probe."""
    global _CODEX_AVAILABILITY
    _CODEX_AVAILABILITY = None
