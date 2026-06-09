"""Revka Operator — MCP server for agent orchestration."""

# Bootstrap: ensure the Kumiho venv site-packages are on sys.path so the
# operator can import kumiho regardless of which Python interpreter runs it.
# This is needed because the operator is spawned as an MCP subprocess by
# Claude Code using system Python, but kumiho is installed in its own venv.
import os as _os, sys as _sys

try:
    import kumiho
except ImportError:
    _KUMIHO_SITE = _os.path.expanduser("~/.kumiho/venv/lib/python3.11/site-packages")
    if _os.path.isdir(_KUMIHO_SITE) and _KUMIHO_SITE not in _sys.path:
        _sys.path.insert(0, _KUMIHO_SITE)

