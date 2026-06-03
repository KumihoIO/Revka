"""Clean environment builder for agent subprocesses.

Prevents environment contamination that causes false build failures:
  - Stale NODE_ENV values (e.g. "production" from operator's shell)
  - Inherited debug flags, coverage tools, profilers
  - Build cache from previous runs (.next, turbopack, etc.)

Usage:
    env = build_agent_env(clean_build=True)
    proc = subprocess.Popen(cmd, env=env, cwd=cwd)
"""
from __future__ import annotations

import os
import shutil

try:
    from ._log import _log
except ImportError:
    import sys
    _log = lambda msg: sys.stderr.write(f"[clean_env] {msg}\n")


# Variables that MUST be forwarded for agents to authenticate and connect
_AUTH_KEYS = frozenset({
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "ANTHROPIC_BASE_URL",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "GEMINI_ENTERPRISE_APP_ID",
})

# Variables needed for network access
_NETWORK_KEYS = frozenset({
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "https_proxy",
    "http_proxy",
    "no_proxy",
})

# System variables needed for process execution
_SYSTEM_KEYS = frozenset({
    "PATH",
    "HOME",
    "USER",
    "SHELL",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TMPDIR",
    "XDG_RUNTIME_DIR",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
})

# Variables that are safe/useful for Node.js builds
_NODE_KEYS = frozenset({
    "NODE_PATH",
    "NVM_DIR",
    "NVM_BIN",
    "NPM_CONFIG_PREFIX",
})

# All allowed keys
_ALLOWED_KEYS = _AUTH_KEYS | _NETWORK_KEYS | _SYSTEM_KEYS | _NODE_KEYS

# Variables to NEVER forward (common contamination sources)
_BLOCKED_KEYS = frozenset({
    "NODE_ENV",          # Set explicitly per agent
    "DEBUG",             # Noisy debug output
    "VERBOSE",
    "CI",                # May trigger CI-specific behavior
    "CONTINUOUS_INTEGRATION",
    "NODE_OPTIONS",      # Can inject unwanted flags
    "ELECTRON_RUN_AS_NODE",
    "TS_NODE_PROJECT",   # Wrong tsconfig
    "FORCE_COLOR",       # Terminal artifacts in logs
    "NO_COLOR",
    "npm_config_loglevel",
})

# Variables that must NEVER be set via env_extra (security-sensitive)
_DANGEROUS_KEYS = frozenset({
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH",
    "DYLD_FRAMEWORK_PATH",
    "PYTHONPATH",
    "RUBYLIB",
    "PERL5LIB",
    "NODE_OPTIONS",
    "BASH_ENV",
    "ENV",
    "CDPATH",
    "PROMPT_COMMAND",
})

# Build cache directories that can be cleaned
_BUILD_CACHE_DIRS = [
    ".next",
    ".turbo",
    "node_modules/.cache",
    ".parcel-cache",
    ".nuxt",
    ".output",
    "dist",
    ".svelte-kit",
]


def build_agent_env(
    *,
    clean_build: bool = False,
    node_env: str = "development",
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build a sanitized environment dict for agent subprocesses.

    Args:
        clean_build: If True, use strict allowlist. If False, forward
                     all env vars except blocked ones (more permissive).
        node_env: Value for NODE_ENV. Defaults to "development".
        extra: Additional env vars to set (overrides everything).

    Returns:
        Environment dict suitable for subprocess.Popen(env=...).
    """
    if clean_build:
        # Strict allowlist mode — only forward known-safe vars
        env: dict[str, str] = {}
        for key in _ALLOWED_KEYS:
            val = os.environ.get(key)
            if val:
                env[key] = val
    else:
        # Permissive mode — forward everything except blocked keys
        env = {
            k: v for k, v in os.environ.items()
            if k not in _BLOCKED_KEYS
        }

    # Always set NODE_ENV explicitly
    env["NODE_ENV"] = node_env

    # Apply caller overrides (strip dangerous keys)
    if extra:
        for k, v in extra.items():
            if k in _DANGEROUS_KEYS:
                _log(f"clean_env: blocked dangerous env override: {k}")
            else:
                env[k] = v

    return env


def build_sidecar_env_config(
    *,
    clean_build: bool = False,
    node_env: str = "development",
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build env overrides to pass in sidecar agent config.

    Unlike build_agent_env(), this returns only the OVERRIDES
    to merge with process.env on the sidecar side — not a full env.
    The sidecar already inherits process.env, so we just need to
    set/override specific vars.
    """
    overrides: dict[str, str] = {
        "NODE_ENV": node_env,
    }

    if clean_build:
        # In clean mode, explicitly unset contamination sources
        for key in _BLOCKED_KEYS:
            if key != "NODE_ENV":  # Already set above
                overrides[key] = ""  # Empty string = unset

    if extra:
        for k, v in extra.items():
            if k in _DANGEROUS_KEYS:
                _log(f"clean_env: blocked dangerous sidecar env override: {k}")
            else:
                overrides[k] = v

    return overrides


def clean_build_caches(cwd: str, *, dry_run: bool = False) -> list[str]:
    """Remove build cache directories in the given working directory.

    Args:
        cwd: Project root to clean (normalized by caller, but we guard defensively).
        dry_run: If True, list what would be removed without removing.

    Returns:
        List of paths that were (or would be) removed.
    """
    # Normalize defensively — callers should already expand, but guard here too
    cwd = os.path.realpath(os.path.expanduser(cwd))
    if not os.path.isdir(cwd):
        _log(f"clean_build_caches: not a directory: {cwd}")
        return []

    removed: list[str] = []
    for cache_dir in _BUILD_CACHE_DIRS:
        full_path = os.path.join(cwd, cache_dir)
        if os.path.exists(full_path):
            if dry_run:
                removed.append(full_path)
            else:
                try:
                    if os.path.isdir(full_path):
                        shutil.rmtree(full_path)
                    else:
                        os.remove(full_path)
                    removed.append(full_path)
                    _log(f"Cleaned build cache: {full_path}")
                except Exception as e:
                    _log(f"Failed to clean {full_path}: {e}")
    return removed
