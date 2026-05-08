#!/usr/bin/env python3
"""Bootstrap launcher for the Construct Operator MCP Server.

Locates the per-component venv that scripts/install-sidecars.{sh,bat}
provisions at ~/.construct/operator_mcp/venv and re-execs the package
under it. If the venv is missing or its python interpreter is missing,
exit with a clear instruction to run install-sidecars.

Earlier versions of this script tried to share a venv with a Claude Code
plugin under ~/.cache/kumiho-claude, and to install deps at runtime.
That path was wrong on every supported platform: install-sidecars
provisions a dedicated operator venv and installs all deps into it. The
runtime install logic (and its lock and marker) was redundant and
caused races + Windows DLL-lock failures. This file is now a thin
locator + exec.
"""
from __future__ import annotations

import os
import pathlib
import sys

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent

# Allow override via env var so embedded / non-standard installs can point
# at a different venv. Default matches install-sidecars layout.
_DEFAULT_OPERATOR_DIR = pathlib.Path.home() / ".construct" / "operator_mcp"
OPERATOR_DIR = pathlib.Path(
    os.environ.get("CONSTRUCT_OPERATOR_DIR") or str(_DEFAULT_OPERATOR_DIR)
)
VENV_DIR = OPERATOR_DIR / "venv"


def _venv_python(venv_dir: pathlib.Path) -> pathlib.Path:
    """Platform-aware path to the venv's Python interpreter."""
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python3"


def _bail(msg: str) -> "None":
    sys.stderr.write(msg.rstrip() + "\n")
    sys.stderr.write(
        "\n[operator] Operator MCP not installed. Run:\n"
        "    scripts\\install-sidecars.bat   (Windows)\n"
        "    scripts/install-sidecars.sh     (macOS / Linux)\n"
        "from the Construct repo root, then retry.\n"
    )
    sys.exit(1)


def main() -> "None":
    python = _venv_python(VENV_DIR)
    if not python.exists():
        _bail(
            f"[operator] Could not find Python interpreter at {python}. "
            f"The operator-mcp venv at {VENV_DIR} either doesn't exist "
            f"or is incomplete."
        )

    # The package source lives next to this launcher (install-sidecars
    # mirrors the source tree to OPERATOR_DIR). PYTHONPATH points at the
    # parent of `operator_mcp/` so `python -m operator_mcp` resolves.
    parent_dir = str(SCRIPT_DIR.parent)
    env = os.environ.copy()
    env["PYTHONPATH"] = parent_dir + (os.pathsep + env.get("PYTHONPATH", ""))

    # IMPORTANT: switch cwd to the parent before exec'ing. install-sidecars
    # flattens the package directly into OPERATOR_DIR (which contains both
    # __init__.py AND operator_mcp.py). If Python's sys.path[0] is OPERATOR_DIR
    # itself, `python -m operator_mcp` resolves to the inner module file
    # instead of the package, and its relative imports fail with
    # "attempted relative import with no known parent package". Setting
    # cwd to the parent forces Python to resolve operator_mcp as a package.
    os.chdir(parent_dir)

    os.execve(
        str(python),
        [str(python), "-m", "operator_mcp"] + sys.argv[1:],
        env,
    )


if __name__ == "__main__":
    main()
