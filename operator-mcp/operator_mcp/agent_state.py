"""Agent state management — ManagedAgent, AgentTemplate, AgentPool."""
from __future__ import annotations

import asyncio
import json as _json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from ._log import _log


# ---------------------------------------------------------------------------
# Agent dataclass — represents a running/completed agent subprocess
# ---------------------------------------------------------------------------

@dataclass
class CacheSafeParams:
    """Frozen snapshot of parent's system prompt + MCP servers.

    Captured after the parent's first successful sidecar turn so that
    children reuse the exact same (cache-friendly) prompt bytes instead
    of rebuilding from scratch each time.
    """
    system_prompt: str
    mcp_servers: dict[str, Any] | None = None


@dataclass
class ManagedAgent:
    id: str
    agent_type: str          # "claude" or "codex"
    title: str
    cwd: str
    status: str              # "running", "idle", "error", "closed"
    process: asyncio.subprocess.Process | None = None
    stdout_buffer: str = ""
    stderr_buffer: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    _reader_task: asyncio.Task | None = field(default=None, repr=False)
    _sidecar_id: str | None = field(default=None, repr=False)
    _cached_params: CacheSafeParams | None = field(default=None, repr=False)
    # First prompt sent to this agent. Stashed so subprocess-mode follow-ups
    # can stitch back the original task context — subprocess CLIs don't
    # preserve sessions across invocations and would otherwise receive bare
    # follow-up text like "execute the task now" with no task to execute.
    _original_prompt: str = field(default="", repr=False)
    # MCP server config used at create-time for subprocess agents — re-used
    # on follow-up turns so each spawn gets the same kumiho-memory /
    # operator-tools surface as the first turn. Sidecar agents don't need
    # this (the SDK handles MCP within the persistent session).
    _subprocess_mcp_servers: dict[str, Any] | None = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# In-memory agent registry
# ---------------------------------------------------------------------------

AGENTS: dict[str, ManagedAgent] = {}
AGENTS_LOCK = asyncio.Lock()

MAX_CONCURRENT_AGENTS = 10


# ---------------------------------------------------------------------------
# Agent template dataclass (pool)
# ---------------------------------------------------------------------------

@dataclass
class AgentTemplate:
    name: str              # Unique template name, e.g. "rust-coder", "react-reviewer"
    agent_type: str        # "claude" or "codex"
    role: str              # "coder", "reviewer", "researcher"
    capabilities: list[str]  # ["rust", "typescript", "testing", "security-audit"]
    description: str       # What this agent is good at
    identity: str | None = None      # Rich identity statement
    soul: str | None = None          # Personality and values
    tone: str | None = None          # Communication style
    model: str | None = None         # Preferred model, e.g. "claude-opus-4-6", "claude-sonnet-4-6"
    default_cwd: str | None = None   # Optional default working directory
    system_hint: str | None = None   # Optional extra prompt context for this agent type
    allowed_tools: list[str] | None = None  # Tool allowlist (None = all tools allowed)
    max_turns: int = 200             # Max conversation turns before auto-stop
    created_at: str = ""             # ISO timestamp
    last_used: str | None = None     # ISO timestamp of last spawn
    use_count: int = 0               # How many times spawned


# ---------------------------------------------------------------------------
# Agent pool — persisted to JSON
# ---------------------------------------------------------------------------

class AgentPool:
    def __init__(self, pool_path: str):
        self.pool_path = pool_path
        self.templates: dict[str, AgentTemplate] = {}
        self._load()

    def _load(self) -> None:
        try:
            if os.path.exists(self.pool_path):
                with open(self.pool_path, "r") as f:
                    data = _json.load(f)
                for name, entry in data.items():
                    # Backward compat: strip unknown fields, default new ones
                    valid_fields = {f.name for f in AgentTemplate.__dataclass_fields__.values()}
                    filtered = {k: v for k, v in entry.items() if k in valid_fields}
                    self.templates[name] = AgentTemplate(**filtered)
            else:
                os.makedirs(os.path.dirname(self.pool_path), exist_ok=True)
                with open(self.pool_path, "w") as f:
                    _json.dump({}, f)
        except Exception as exc:
            _log(f"AgentPool._load error: {exc}")

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.pool_path), exist_ok=True)
            # Atomic write: write to temp file, then rename
            tmp_path = self.pool_path + ".tmp"
            with open(tmp_path, "w") as f:
                _json.dump(
                    {name: asdict(t) for name, t in self.templates.items()},
                    f,
                    indent=2,
                )
            os.replace(tmp_path, self.pool_path)
        except Exception as exc:
            _log(f"AgentPool._save error: {exc}")
            # Clean up temp file on failure
            try:
                os.remove(self.pool_path + ".tmp")
            except OSError:
                pass

    def add(self, template: AgentTemplate) -> None:
        self.templates[template.name] = template
        self._save()

    def remove(self, name: str) -> None:
        self.templates.pop(name, None)
        self._save()

    def list_all(self) -> list[AgentTemplate]:
        return sorted(self.templates.values(), key=lambda t: t.use_count, reverse=True)

    def record_use(self, name: str) -> None:
        t = self.templates.get(name)
        if t is not None:
            t.use_count += 1
            t.last_used = datetime.now(timezone.utc).isoformat()
            self._save()

    def validate_template(self, name: str) -> "TemplateValidation":
        """Run quality gates on a template before use."""
        t = self.templates.get(name)
        if t is None:
            v = TemplateValidation()
            v.errors.append(f"Template '{name}' not found")
            return v
        return validate_template(t)

    def search(self, query: str) -> list[AgentTemplate]:
        """Keyword search against name, role, capabilities, description."""
        keywords = [kw.lower() for kw in query.split() if kw]
        if not keywords:
            return self.list_all()

        scored: list[tuple[int, AgentTemplate]] = []
        for t in self.templates.values():
            haystack = " ".join([
                t.name,
                t.role,
                " ".join(t.capabilities),
                t.description,
            ]).lower()
            hits = sum(1 for kw in keywords if kw in haystack)
            if hits > 0:
                scored.append((hits, t))

        scored.sort(key=lambda pair: (pair[0], pair[1].use_count), reverse=True)
        return [t for _, t in scored]


# ---------------------------------------------------------------------------
# Template quality gates
# ---------------------------------------------------------------------------

_VALID_ROLES = {"coder", "reviewer", "researcher", "tester", "architect", "planner"}
_VALID_AGENT_TYPES = {"claude", "codex"}


@dataclass
class TemplateValidation:
    """Result of template quality gate checks."""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    score: int = 0  # 0-100 quality score

    @property
    def valid(self) -> bool:
        return len(self.errors) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "score": self.score,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def validate_template(t: AgentTemplate) -> TemplateValidation:
    """Run quality gates on an agent template.

    Checks:
      - Required fields (name, role, agent_type, description)
      - Role validity
      - Agent type validity
      - Capability list non-empty
      - Description quality (min length)
      - Identity/soul completeness (for higher scores)
      - Model validity (if specified)
    """
    v = TemplateValidation()
    score = 100

    # Required fields
    if not t.name or not t.name.strip():
        v.errors.append("Template name is required")
        score -= 25
    if not t.description or not t.description.strip():
        v.errors.append("Template description is required")
        score -= 20

    # Role validity
    if t.role not in _VALID_ROLES:
        v.errors.append(f"Invalid role '{t.role}'. Must be one of: {', '.join(sorted(_VALID_ROLES))}")
        score -= 25

    # Agent type validity
    if t.agent_type not in _VALID_AGENT_TYPES:
        v.errors.append(f"Invalid agent_type '{t.agent_type}'. Must be 'claude' or 'codex'")
        score -= 25

    # Capability list
    if not t.capabilities:
        v.warnings.append("No capabilities listed — agents may receive poorly targeted tasks")
        score -= 10
    elif len(t.capabilities) < 2:
        v.warnings.append("Only one capability listed — consider adding more for better task matching")
        score -= 5

    # Description quality
    if t.description and len(t.description.strip()) < 20:
        v.warnings.append("Description is very short — longer descriptions improve task decomposition")
        score -= 5

    # Identity / soul (not required, but improves quality)
    if not t.identity:
        v.warnings.append("No identity statement — agents will use generic behavior")
        score -= 5
    if not t.system_hint:
        v.warnings.append("No system_hint — consider adding domain-specific context")
        score -= 5

    # Model validity (if specified)
    if t.model and not any(prefix in t.model for prefix in ("claude-", "gpt-", "o1", "o3", "codex")):
        v.warnings.append(f"Unusual model '{t.model}' — verify it's supported by your provider")

    # Max turns sanity
    if t.max_turns < 1:
        v.errors.append(f"max_turns must be >= 1, got {t.max_turns}")
        score -= 10
    elif t.max_turns > 500:
        v.warnings.append(f"max_turns={t.max_turns} is very high — consider a lower limit for cost control")

    v.score = max(0, score)
    return v


# ---------------------------------------------------------------------------
# Global pool instance
# ---------------------------------------------------------------------------

_POOL_PATH = os.path.expanduser("~/.construct/operator_mcp/agent_pool.json")
POOL = AgentPool(_POOL_PATH)
