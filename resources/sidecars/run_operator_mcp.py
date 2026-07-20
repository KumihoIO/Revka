#!/usr/bin/env python3
"""Launcher for the Operator MCP sidecar.

Materialized into ~/.revka/operator_mcp/run_operator_mcp.py by
`revka install`. Re-execs into the per-sidecar venv interpreter so
Revka itself does not depend on any particular Python on PATH at runtime.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    venv = Path.home() / ".revka" / "operator_mcp" / "venv"
    if os.name == "nt":
        interp = venv / "Scripts" / "python.exe"
    else:
        interp = venv / "bin" / "python3"
        if not interp.exists():
            interp = venv / "bin" / "python"

    if not interp.exists():
        sys.stderr.write(
            f"operator sidecar venv interpreter not found at {interp}.\n"
            "Run `revka install --sidecars-only` to (re)provision the sidecars.\n"
        )
        return 127

    # Older Revka binaries may still inject KUMIHO_AUTO_CONFIGURE at
    # sidecar launch. Keep endpoint discovery out of the pre-initialize path.
    os.environ.pop("KUMIHO_AUTO_CONFIGURE", None)
    # Keep gRPC C-core INFO fork diagnostics out of the user-facing log stream.
    os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
    os.environ.setdefault("GLOG_minloglevel", "2")

    argv = [str(interp), "-m", "operator_mcp", *sys.argv[1:]]
    if os.name == "nt":
        # os.execv on Windows is emulated as spawn-new-process + exit-parent,
        # so the MCP client's pipe to THIS process drops while the real server
        # is still importing — the handshake races and slow-importing sidecars
        # lose. Stay resident and pass stdio through instead.
        import subprocess

        return subprocess.call(argv)
    os.execv(str(interp), argv)


if __name__ == "__main__":
    raise SystemExit(main())
