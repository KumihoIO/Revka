"""Read Construct-level config values from ~/.construct/config.toml.

Exposes the two Kumiho project names used across the operator + gateway:

  * harness_project — operational namespace (Workflows, AgentPool, Teams,
    Sessions, ...). Must match the gateway's `[kumiho].harness_project`.
  * memory_project  — user / cognitive namespace (Skills, personal memory,
    cross-session recall). Must match the gateway's `[kumiho].memory_project`.

Both helpers cache after first read; pass `force_reload=True` to re-read.
"""
from __future__ import annotations

import os

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

from ._log import _log

_CONFIG_PATH = os.path.expanduser("~/.construct/config.toml")
_DEFAULT_HARNESS = "Construct"
_DEFAULT_MEMORY = "CognitiveMemory"
_DEFAULT_WORKSPACE_DIR = "~/.construct/workspace"
_DEFAULT_MEMORY_RETRIEVAL_LIMIT = 3

# Manus step defaults. Overridable per-step via ManusStepConfig and at the
# user level via [manus] in ~/.construct/config.toml. The api_key value
# itself never lives in config.toml — only the env-var NAME does.
_DEFAULT_MANUS = {
    "api_key_env": "MANUS_API_KEY",
    "base_url": "https://api.manus.ai",
    "default_agent_profile": "manus-1.6",
    "default_timeout_seconds": 600,
    "default_poll_interval_seconds": 5,
}

_cached_harness: str | None = None
_cached_memory: str | None = None
_cached_manus: dict | None = None
_cached_workspace_dir: str | None = None
_cached_memory_retrieval_limit: int | None = None


def _read_section(section: str) -> dict:
    """Return a top-level section from config.toml as a dict.

    Returns an empty dict on any read / parse error so callers can fall
    back to defaults.
    """
    if tomllib is None:
        return {}
    try:
        with open(_CONFIG_PATH, "rb") as f:
            config = tomllib.load(f)
    except FileNotFoundError:
        return {}
    except Exception as exc:
        _log(f"construct_config: error reading config: {exc}")
        return {}
    sec = config.get(section, {})
    return sec if isinstance(sec, dict) else {}


def manus_config(*, force_reload: bool = False) -> dict:
    """Return Manus step defaults from [manus] in ~/.construct/config.toml.

    Falls back to built-in defaults for any missing keys. Cached after
    first read; pass ``force_reload=True`` to re-read from disk.

    NEVER reads or returns the actual API key — only the env-var NAME
    holding it. Callers do ``os.environ.get(cfg['api_key_env'], '')``.
    """
    global _cached_manus
    if _cached_manus is not None and not force_reload:
        return _cached_manus

    on_disk = _read_section("manus")
    merged = dict(_DEFAULT_MANUS)
    for k, v in on_disk.items():
        if v is None or v == "":
            continue
        # Only accept values whose shape matches the default so a malformed
        # config can't break the dispatch path (str expected → reject ints).
        if isinstance(_DEFAULT_MANUS.get(k), int):
            try:
                merged[k] = int(v)
            except (TypeError, ValueError):
                continue
        else:
            if isinstance(v, str):
                merged[k] = v.strip() or _DEFAULT_MANUS.get(k, "")
    _cached_manus = merged
    return _cached_manus


def _read_kumiho_section() -> dict:
    """Return the [kumiho] section from config.toml as a dict.

    Returns an empty dict on any read / parse error so callers can fall
    back to defaults.
    """
    if tomllib is None:
        return {}
    try:
        with open(_CONFIG_PATH, "rb") as f:
            config = tomllib.load(f)
    except FileNotFoundError:
        return {}
    except Exception as exc:
        _log(f"construct_config: error reading config: {exc}")
        return {}
    return config.get("kumiho", {}) or {}


def harness_project(*, force_reload: bool = False) -> str:
    """Return the Kumiho harness project name from config (default 'Construct').

    Reads `[kumiho].harness_project` from ~/.construct/config.toml. Cached
    after first read; pass `force_reload=True` to re-read from disk.
    """
    global _cached_harness
    if _cached_harness is not None and not force_reload:
        return _cached_harness

    kumiho = _read_kumiho_section()
    value = kumiho.get("harness_project")
    if isinstance(value, str) and value.strip():
        _cached_harness = value.strip()
    else:
        _cached_harness = _DEFAULT_HARNESS
    return _cached_harness


def memory_project(*, force_reload: bool = False) -> str:
    """Return the Kumiho memory project name from config (default 'CognitiveMemory').

    Reads `[kumiho].memory_project` from ~/.construct/config.toml. Falls back
    to the `KUMIHO_MEMORY_PROJECT` environment variable, then to the
    'CognitiveMemory' default.

    Cached after first read; pass `force_reload=True` to re-read from disk.
    """
    global _cached_memory
    if _cached_memory is not None and not force_reload:
        return _cached_memory

    kumiho = _read_kumiho_section()
    value = kumiho.get("memory_project")
    if isinstance(value, str) and value.strip():
        _cached_memory = value.strip()
        return _cached_memory

    env_value = os.environ.get("KUMIHO_MEMORY_PROJECT")
    if isinstance(env_value, str) and env_value.strip():
        _cached_memory = env_value.strip()
        return _cached_memory

    _cached_memory = _DEFAULT_MEMORY
    return _cached_memory


def memory_retrieval_limit(*, force_reload: bool = False) -> int:
    """Return [memory].retrieval_limit with a conservative default of 3."""
    global _cached_memory_retrieval_limit
    if _cached_memory_retrieval_limit is not None and not force_reload:
        return _cached_memory_retrieval_limit

    memory = _read_section("memory")
    try:
        value = int(memory.get("retrieval_limit", _DEFAULT_MEMORY_RETRIEVAL_LIMIT))
    except (TypeError, ValueError):
        value = _DEFAULT_MEMORY_RETRIEVAL_LIMIT
    _cached_memory_retrieval_limit = max(1, value)
    return _cached_memory_retrieval_limit


def workspace_dir(*, force_reload: bool = False) -> str:
    """Return Construct's resolved workspace directory.

    Rust resolves this at runtime and stores generated assets below it. The
    Operator sidecar mirrors the same default path and honors
    ``CONSTRUCT_WORKSPACE`` when present so artifacts are browser-viewable via
    the gateway workspace asset endpoints.
    """
    global _cached_workspace_dir
    if _cached_workspace_dir is not None and not force_reload:
        return _cached_workspace_dir

    env_value = os.environ.get("CONSTRUCT_WORKSPACE")
    if isinstance(env_value, str) and env_value.strip():
        path = os.path.expanduser(env_value.strip())
        # When CONSTRUCT_WORKSPACE points at a profile/config directory, Rust
        # uses its nested workspace/ directory. If it already points at a data
        # directory without config.toml, use it directly.
        if os.path.exists(os.path.join(path, "config.toml")):
            path = os.path.join(path, "workspace")
        _cached_workspace_dir = os.path.abspath(path)
        return _cached_workspace_dir

    _cached_workspace_dir = os.path.abspath(os.path.expanduser(_DEFAULT_WORKSPACE_DIR))
    return _cached_workspace_dir
