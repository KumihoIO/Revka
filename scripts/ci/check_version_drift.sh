#!/usr/bin/env bash
# Gate that workspace version, Tauri app config, and web package.json
# all match. Prevents release artifacts shipping with divergent versions.
#
# Exits non-zero if any mismatch is detected.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

workspace_version=$(awk '
  /^\[package\]/ { in_block = 1; next }
  /^\[/ && in_block { in_block = 0 }
  in_block && /^version[[:space:]]*=/ {
    match($0, /"[^"]+"/)
    print substr($0, RSTART + 1, RLENGTH - 2)
    exit
  }
' "$ROOT/Cargo.toml")

tauri_version=$(python3 -c "import json,sys; print(json.load(open('$ROOT/apps/tauri/tauri.conf.json'))['version'])")
web_version=$(python3 -c "import json,sys; print(json.load(open('$ROOT/web/package.json')).get('version',''))")

echo "workspace: ${workspace_version}"
echo "tauri:     ${tauri_version}"
echo "web:       ${web_version}"

fail=0
if [ -z "$workspace_version" ]; then
  echo "::error::could not parse workspace version from Cargo.toml"
  fail=1
fi
if [ "$tauri_version" != "$workspace_version" ]; then
  echo "::error::tauri.conf.json version ($tauri_version) != workspace ($workspace_version)"
  fail=1
fi
if [ "$web_version" != "$workspace_version" ]; then
  echo "::error::web/package.json version ($web_version) != workspace ($workspace_version)"
  fail=1
fi

# ── Rust MSRV drift (#431) ────────────────────────────────────────────────
# The advertised Rust prerequisite in user-facing docs must match Cargo.toml's
# `rust-version` (the real build floor). The setup scripts derive the floor from
# Cargo.toml at runtime so they cannot drift; the docs are static, so guard them.
rust_version=$(awk '
  /^\[package\]/ { in_block = 1; next }
  /^\[/ && in_block { in_block = 0 }
  in_block && /^rust-version[[:space:]]*=/ {
    match($0, /"[^"]+"/)
    print substr($0, RSTART + 1, RLENGTH - 2)
    exit
  }
' "$ROOT/Cargo.toml")

echo "rust-version: ${rust_version}"

if [ -z "$rust_version" ]; then
  echo "::error::could not parse rust-version from Cargo.toml"
  fail=1
else
  rust_version_re=${rust_version//./\\.}
  for doc in \
    "README.md" \
    "README.ko.md" \
    "docs/setup-guides/windows-setup.md" \
    "docs/i18n/ko/setup-guides/windows-setup.md"; do
    if [ -f "$ROOT/$doc" ] && ! grep -qE "Rust[^0-9]*${rust_version_re}" "$ROOT/$doc"; then
      echo "::error::$doc does not advertise Rust ${rust_version} (rust-version drift?)"
      fail=1
    fi
  done
fi

if [ "$fail" -ne 0 ]; then
  exit 1
fi

echo "Versions aligned."
