"""Read Revka-level config values from ~/.revka/config.toml.

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

_CONFIG_PATH = os.path.expanduser("~/.revka/config.toml")
_DEFAULT_HARNESS = "Revka"
_DEFAULT_MEMORY = "CognitiveMemory"
_DEFAULT_WORKSPACE_DIR = "~/.revka/workspace"
_DEFAULT_KUMIHO_API_URL = "https://api.kumiho.cloud"
_DEFAULT_MEMORY_RETRIEVAL_LIMIT = 3
_DEFAULT_MEMORY_MIN_RELEVANCE_SCORE = 0.4

# Manus step defaults. Overridable per-step via ManusStepConfig and at the
# user level via [manus] in ~/.revka/config.toml. The api_key value
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
_cached_memory_retrieval_limit: int | None = None
_cached_memory_min_relevance_score: float | None = None
_cached_manus: dict | None = None
_cached_workspace_dir: str | None = None


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
        _log(f"revka_config: error reading config: {exc}")
        return {}
    sec = config.get(section, {})
    return sec if isinstance(sec, dict) else {}


def manus_config(*, force_reload: bool = False) -> dict:
    """Return Manus step defaults from [manus] in ~/.revka/config.toml.

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
        _log(f"revka_config: error reading config: {exc}")
        return {}
    return config.get("kumiho", {}) or {}


def _nonempty_str(value: object) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _read_workspace_env() -> dict[str, str]:
    """Read simple KEY=VALUE entries from the workspace .env file."""
    path = os.path.join(workspace_dir(), ".env")
    values: dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip("\"'")
                if key and value:
                    values[key] = value
    except FileNotFoundError:
        return {}
    except Exception as exc:
        _log(f"revka_config: error reading workspace .env: {exc}")
    return values


def kumiho_connection_config() -> dict[str, str]:
    """Return Kumiho connection values from Revka config.

    Onboarding stores the user-entered Kumiho token in
    the workspace ``.env`` as ``KUMIHO_SERVICE_TOKEN``. Some deployments use
    ``KUMIHO_AUTH_TOKEN`` or a config value instead. Workflow child agents run
    through the Python SDK/MCP bridge, so they should not depend on a separate
    ``kumiho_authentication.json`` having been created.
    """
    kumiho = _read_kumiho_section()
    workspace_env = _read_workspace_env()
    service_token = _nonempty_str(workspace_env.get("KUMIHO_SERVICE_TOKEN"))
    auth_token = (
        _nonempty_str(kumiho.get("auth_token"))
        or _nonempty_str(workspace_env.get("KUMIHO_AUTH_TOKEN"))
        or service_token
    )
    return {
        "api_url": _nonempty_str(kumiho.get("api_url")) or _DEFAULT_KUMIHO_API_URL,
        "auth_token": auth_token,
        "service_token": service_token or auth_token,
        "space_prefix": _nonempty_str(kumiho.get("space_prefix")),
        "memory_project": _nonempty_str(kumiho.get("memory_project")) or _DEFAULT_MEMORY,
        "harness_project": _nonempty_str(kumiho.get("harness_project")) or _DEFAULT_HARNESS,
    }


def harness_project(*, force_reload: bool = False) -> str:
    """Return the Kumiho harness project name from config (default 'Revka').

    Reads `[kumiho].harness_project` from ~/.revka/config.toml. Cached
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

    Reads `[kumiho].memory_project` from ~/.revka/config.toml. Falls back
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
    """Return the configured Kumiho memory recall limit.

    Rust passes this to the Operator sidecar as
    ``KUMIHO_MEMORY_RETRIEVAL_LIMIT``. When the sidecar is run directly,
    fall back to ``[kumiho].memory_retrieval_limit`` in config.toml and then
    to Revka's shipped default.
    """
    global _cached_memory_retrieval_limit
    if _cached_memory_retrieval_limit is not None and not force_reload:
        return _cached_memory_retrieval_limit

    env_value = os.environ.get("KUMIHO_MEMORY_RETRIEVAL_LIMIT")
    if isinstance(env_value, str) and env_value.strip():
        try:
            parsed = int(env_value.strip())
            if parsed > 0:
                _cached_memory_retrieval_limit = parsed
                return _cached_memory_retrieval_limit
        except ValueError:
            pass

    kumiho = _read_kumiho_section()
    value = kumiho.get("memory_retrieval_limit")
    try:
        parsed = int(value)
        if parsed > 0:
            _cached_memory_retrieval_limit = parsed
            return _cached_memory_retrieval_limit
    except (TypeError, ValueError):
        pass

    _cached_memory_retrieval_limit = _DEFAULT_MEMORY_RETRIEVAL_LIMIT
    return _cached_memory_retrieval_limit


def memory_min_relevance_score(*, force_reload: bool = False) -> float:
    """Return the configured minimum score for memory context injection."""
    global _cached_memory_min_relevance_score
    if _cached_memory_min_relevance_score is not None and not force_reload:
        return _cached_memory_min_relevance_score

    env_value = os.environ.get("REVKA_MEMORY_MIN_RELEVANCE_SCORE")
    if isinstance(env_value, str) and env_value.strip():
        try:
            parsed = float(env_value.strip())
            if 0.0 <= parsed <= 1.0:
                _cached_memory_min_relevance_score = parsed
                return _cached_memory_min_relevance_score
        except ValueError:
            pass

    memory = _read_section("memory")
    value = memory.get("min_relevance_score")
    try:
        parsed = float(value)
        if 0.0 <= parsed <= 1.0:
            _cached_memory_min_relevance_score = parsed
            return _cached_memory_min_relevance_score
    except (TypeError, ValueError):
        pass

    _cached_memory_min_relevance_score = _DEFAULT_MEMORY_MIN_RELEVANCE_SCORE
    return _cached_memory_min_relevance_score


def workspace_dir(*, force_reload: bool = False) -> str:
    """Return Revka's resolved workspace directory.

    Rust resolves this at runtime and stores generated assets below it. The
    Operator sidecar mirrors the same default path and honors
    ``REVKA_WORKSPACE`` when present so artifacts are browser-viewable via
    the gateway workspace asset endpoints.
    """
    global _cached_workspace_dir
    if _cached_workspace_dir is not None and not force_reload:
        return _cached_workspace_dir

    env_value = os.environ.get("REVKA_WORKSPACE")
    if isinstance(env_value, str) and env_value.strip():
        path = os.path.expanduser(env_value.strip())
        # When REVKA_WORKSPACE points at a profile/config directory, Rust
        # uses its nested workspace/ directory. If it already points at a data
        # directory without config.toml, use it directly.
        if os.path.exists(os.path.join(path, "config.toml")):
            path = os.path.join(path, "workspace")
        _cached_workspace_dir = os.path.abspath(path)
        return _cached_workspace_dir

    _cached_workspace_dir = os.path.abspath(os.path.expanduser(_DEFAULT_WORKSPACE_DIR))
    return _cached_workspace_dir
