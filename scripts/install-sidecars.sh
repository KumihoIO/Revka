#!/usr/bin/env bash
# install-sidecars.sh — install Construct's Python MCP sidecars (Kumiho + Operator)
#
# This script is idempotent: safe to re-run. It only creates scaffolding under
# ~/.construct/ and never overwrites an existing config.toml or .env.
#
# It installs:
#   1. Operator MCP at ~/.construct/operator_mcp/ (via operator-mcp/Makefile when
#      make is available; falls back to a minimal rsync + pip install).
#   2. Kumiho MCP at ~/.construct/kumiho/ — creates a venv, installs the
#      `kumiho` package from PyPI, and writes a launcher at
#      ~/.construct/kumiho/run_kumiho_mcp.py that invokes `python -m
#      kumiho.mcp_server`.
#
# Defaults match the paths in ~/.construct/config.toml:
#   kumiho.mcp_path   = ~/.construct/kumiho/run_kumiho_mcp.py
#   operator.mcp_path = ~/.construct/operator_mcp/run_operator_mcp.py
#
# Usage:
#   ./scripts/install-sidecars.sh [--skip-kumiho] [--skip-operator] [--dry-run]
#
set -euo pipefail

# ── Args ──────────────────────────────────────────────────────────
SKIP_KUMIHO=false
SKIP_OPERATOR=false
DRY_RUN=false
PYTHON_BIN="${PYTHON:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-kumiho)   SKIP_KUMIHO=true ;;
    --skip-operator) SKIP_OPERATOR=true ;;
    --dry-run)       DRY_RUN=true ;;
    --python)        shift; PYTHON_BIN="$1" ;;
    -h|--help)
      # Print leading comment block (stops at first non-comment line).
      awk 'NR==1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"
      exit 0
      ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
  shift
done

# ── Paths ─────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OPERATOR_SRC="$REPO_ROOT/operator-mcp"

CONSTRUCT_DIR="$HOME/.construct"
KUMIHO_DIR="$CONSTRUCT_DIR/kumiho"
KUMIHO_VENV="$KUMIHO_DIR/venv"
KUMIHO_LAUNCHER="$KUMIHO_DIR/run_kumiho_mcp.py"
OPERATOR_DIR="$CONSTRUCT_DIR/operator_mcp"
OPERATOR_LAUNCHER="$OPERATOR_DIR/run_operator_mcp.py"

info()    { printf '==> %s\n' "$*"; }
step_ok() { printf '    [ok] %s\n' "$*"; }
step_skip() { printf '    [skip] %s\n' "$*"; }
warn()    { printf '    [warn] %s\n' "$*" >&2; }
run()     { if [[ "$DRY_RUN" == true ]]; then echo "    + $*"; else eval "$@"; fi; }

# ── Preflight ────────────────────────────────────────────────────
# Pick the first working Python interpreter. On MINGW / Git Bash on Windows,
# `python3` often resolves to a Windows App Store stub that fails to execute;
# fall back to `python` in that case. User can override with --python or $PYTHON.
pick_python() {
  local candidate
  for candidate in "$PYTHON_BIN" python3 python; do
    [[ -z "$candidate" ]] && continue
    if command -v "$candidate" >/dev/null 2>&1; then
      # Execute a trivial probe — catches the Windows App Store stub which
      # is on PATH but fails to run.
      if "$candidate" -c 'import sys' >/dev/null 2>&1; then
        PYTHON_BIN="$candidate"
        return 0
      fi
    fi
  done
  return 1
}

if ! pick_python; then
  echo "error: no working Python interpreter found. Install Python 3.11+ and retry (or pass --python /path/to/python)." >&2
  exit 1
fi

PY_OK=$("$PYTHON_BIN" -c 'import sys; print(1 if sys.version_info >= (3,11) else 0)')
if [[ "$PY_OK" != "1" ]]; then
  warn "Python >= 3.11 is recommended. Detected: $($PYTHON_BIN --version 2>&1)"
fi

mkdir -p "$CONSTRUCT_DIR"

# ── Operator sidecar ──────────────────────────────────────────────
install_operator() {
  info "Installing Operator MCP → $OPERATOR_DIR"

  if [[ ! -d "$OPERATOR_SRC" ]]; then
    warn "operator-mcp/ not found at $OPERATOR_SRC — skipping Operator install"
    warn "Run this script from a Construct source checkout."
    return 0
  fi

  # Prefer the canonical Makefile install.
  if command -v make >/dev/null 2>&1; then
    step_ok "using operator-mcp/Makefile (make install)"
    run "cd '$OPERATOR_SRC' && make install"
  else
    step_ok "make not found — falling back to minimal pip + copy install"
    run "mkdir -p '$OPERATOR_DIR'"
    # Create venv if absent
    if [[ ! -x "$OPERATOR_DIR/venv/bin/python3" && ! -x "$OPERATOR_DIR/venv/Scripts/python.exe" ]]; then
      run "'$PYTHON_BIN' -m venv '$OPERATOR_DIR/venv'"
    fi
    local op_py
    if [[ -x "$OPERATOR_DIR/venv/bin/python3" ]]; then
      op_py="$OPERATOR_DIR/venv/bin/python3"
    else
      op_py="$OPERATOR_DIR/venv/Scripts/python.exe"
    fi
    run "'$op_py' -m pip install --quiet --upgrade pip"
    run "'$op_py' -m pip install --quiet '$OPERATOR_SRC'[all] || '$op_py' -m pip install --quiet '$OPERATOR_SRC'"
    # Copy package tree minus caches
    if command -v rsync >/dev/null 2>&1; then
      run "rsync -a --exclude='__pycache__' --exclude='*.pyc' --exclude='venv' --exclude='session-manager' '$OPERATOR_SRC/operator_mcp/' '$OPERATOR_DIR/'"
    else
      run "cp -R '$OPERATOR_SRC/operator_mcp/.' '$OPERATOR_DIR/'"
    fi
    run "cp '$OPERATOR_SRC/operator_mcp/run_operator_mcp.py' '$OPERATOR_LAUNCHER'"
    run "cp '$OPERATOR_SRC/requirements.txt' '$OPERATOR_DIR/requirements.txt' 2>/dev/null || true"
  fi

  if [[ -f "$OPERATOR_LAUNCHER" ]]; then
    step_ok "Operator launcher present: $OPERATOR_LAUNCHER"
  else
    warn "Operator launcher missing at $OPERATOR_LAUNCHER — install likely failed"
  fi
}

# ── Kumiho sidecar ────────────────────────────────────────────────
install_kumiho() {
  info "Installing Kumiho MCP → $KUMIHO_DIR"

  run "mkdir -p '$KUMIHO_DIR'"

  # Create the Kumiho venv if absent (idempotent).
  local kumiho_py=""
  if [[ -x "$KUMIHO_VENV/bin/python3" ]]; then
    kumiho_py="$KUMIHO_VENV/bin/python3"
    step_skip "venv already exists at $KUMIHO_VENV"
  elif [[ -x "$KUMIHO_VENV/Scripts/python.exe" ]]; then
    kumiho_py="$KUMIHO_VENV/Scripts/python.exe"
    step_skip "venv already exists at $KUMIHO_VENV"
  else
    run "'$PYTHON_BIN' -m venv '$KUMIHO_VENV'"
    if [[ -x "$KUMIHO_VENV/bin/python3" ]]; then
      kumiho_py="$KUMIHO_VENV/bin/python3"
    else
      kumiho_py="$KUMIHO_VENV/Scripts/python.exe"
    fi
    step_ok "venv created"
  fi

  # Install/upgrade the Kumiho package with the MCP extra (pulls in mcp + httpx).
  run "'$kumiho_py' -m pip install --quiet --upgrade pip"
  # Version pin matches operator-mcp/requirements.txt (kumiho>=0.9.20).
  # The [mcp] extra installs `mcp>=1.0.0` + `httpx>=0.27.0` — required for the
  # stdio server in kumiho.mcp_server.
  #
  # `kumiho_memory` (separate package) is required for the high-level memory
  # tools mandated by the Construct session-bootstrap prompt:
  # kumiho_memory_engage / reflect / recall / consolidate / dream_state. The
  # bare `kumiho` package only exposes low-level CRUD + memory_store/retrieve;
  # kumiho.mcp_server auto-discovers and merges in kumiho_memory's tools when
  # the package is importable. Without this extra install, the prompt fires
  # tool calls that don't exist.
  run "'$kumiho_py' -m pip install --quiet 'kumiho[mcp]>=0.9.20' 'kumiho_memory>=0.5.0'"
  step_ok "kumiho[mcp] + kumiho_memory installed into venv"

  # Write the launcher if missing. We do NOT overwrite a user-authored launcher.
  if [[ -f "$KUMIHO_LAUNCHER" ]]; then
    step_skip "launcher already present: $KUMIHO_LAUNCHER"
  else
    if [[ "$DRY_RUN" == true ]]; then
      echo "    + write launcher: $KUMIHO_LAUNCHER"
    else
      cat > "$KUMIHO_LAUNCHER" <<PYEOF
#!/usr/bin/env python3
"""Kumiho MCP launcher installed by Construct's install-sidecars script.

This script is a thin shim: it re-executes the Kumiho MCP stdio server
module out of the dedicated Kumiho venv. It is referenced from
~/.construct/config.toml as kumiho.mcp_path.

Regenerate with: ./scripts/install-sidecars.sh
"""
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
VENV_PY = HERE / "venv" / "bin" / "python3"
if not VENV_PY.exists():
    # Windows venv layout
    VENV_PY = HERE / "venv" / "Scripts" / "python.exe"

if not VENV_PY.exists():
    sys.stderr.write(
        f"[kumiho-launcher] venv python not found at {VENV_PY}. "
        "Re-run scripts/install-sidecars.sh.\n"
    )
    sys.exit(127)

# Module path: kumiho.mcp_server is the canonical entry used by Construct's
# operator sidecar (see operator-mcp/operator_mcp/kumiho_clients.py).
os.execv(str(VENV_PY), [str(VENV_PY), "-m", "kumiho.mcp_server", *sys.argv[1:]])
PYEOF
      chmod +x "$KUMIHO_LAUNCHER"
      step_ok "launcher written: $KUMIHO_LAUNCHER"
    fi
  fi
}

# ── Run ───────────────────────────────────────────────────────────
if [[ "$SKIP_OPERATOR" == true ]]; then
  step_skip "Operator install skipped (--skip-operator)"
else
  install_operator
fi

if [[ "$SKIP_KUMIHO" == true ]]; then
  step_skip "Kumiho install skipped (--skip-kumiho)"
else
  install_kumiho
fi

info "Done."
echo
echo "Verify with:"
echo "  construct doctor"
echo "  ls -l '$KUMIHO_LAUNCHER' '$OPERATOR_LAUNCHER'"
# Pick the right venv python path for the current platform.
if [[ -x "$KUMIHO_VENV/bin/python3" ]]; then
  echo "  '$KUMIHO_VENV/bin/python3' -c 'import kumiho; print(kumiho.__version__)'"
else
  echo "  '$KUMIHO_VENV/Scripts/python.exe' -c 'import kumiho; print(kumiho.__version__)'"
fi
