#!/usr/bin/env python3
"""Launcher for the Kumiho MCP sidecar.

Materialized into ~/.revka/kumiho/run_kumiho_mcp.py by `revka install`.
Re-execs into the per-sidecar venv interpreter so Revka itself does not
depend on any particular Python on PATH at runtime.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    venv = Path.home() / ".revka" / "kumiho" / "venv"
    if os.name == "nt":
        interp = venv / "Scripts" / "python.exe"
    else:
        interp = venv / "bin" / "python3"
        if not interp.exists():
            interp = venv / "bin" / "python"

    if not interp.exists():
        sys.stderr.write(
            f"kumiho sidecar venv interpreter not found at {interp}.\n"
            "Run `revka install --sidecars-only` to (re)provision the sidecars.\n"
        )
        return 127

    # Older Revka binaries may still inject KUMIHO_AUTO_CONFIGURE at
    # sidecar launch. Keep endpoint discovery out of the pre-initialize path.
    os.environ.pop("KUMIHO_AUTO_CONFIGURE", None)

    argv = [str(interp), "-m", "kumiho.mcp_server", *sys.argv[1:]]
    os.execv(str(interp), argv)


if __name__ == "__main__":
    raise SystemExit(main())
