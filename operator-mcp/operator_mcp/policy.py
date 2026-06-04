"""Policy evaluation — pre-flight checks and structured permission handling.

Loads autonomy policy from ~/.revka/config.toml and provides:
  - Pre-flight validation before agent spawn (will this cwd be allowed?)
  - Tool permission classification (auto-approve, needs-approval, blocked)
  - Structured permission denial messages with policy context
  - Pending permission detection for agents in wait loops

Usage:
    policy = load_policy()
    result = policy.check_cwd("/some/path")
    result = policy.check_tool("shell", {"command": "rm -rf /"})
    result = policy.check_command("docker")
"""
from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from typing import Any

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

from ._log import _log
from .revka_config import workspace_dir as configured_workspace_dir


# ---------------------------------------------------------------------------
# Policy data
# ---------------------------------------------------------------------------

def _expand_path(path: str) -> str:
    """Resolve a policy path the same way for roots, forbidden paths, and cwd."""
    return os.path.realpath(os.path.expandvars(os.path.expanduser(path)))


def _path_is_under(path: str, root: str) -> bool:
    """Component-aware containment check for real paths."""
    if not root:
        return False
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


@dataclass
class PolicyCheckResult:
    """Result of a policy pre-flight check."""
    allowed: bool
    reason: str = ""
    policy_rule: str = ""  # Which config key triggered this
    suggestion: str = ""   # What the operator can do about it

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"allowed": self.allowed}
        if not self.allowed:
            d["reason"] = self.reason
            d["policy_rule"] = self.policy_rule
            if self.suggestion:
                d["suggestion"] = self.suggestion
        return d


@dataclass
class Policy:
    """Loaded autonomy policy from config.toml."""
    level: str = "supervised"  # supervised, autonomous, locked
    workspace_dir: str = "~/.revka/workspace"
    workspace_only: bool = True
    allowed_commands: list[str] = field(default_factory=list)
    forbidden_paths: list[str] = field(default_factory=list)
    allowed_roots: list[str] = field(default_factory=list)
    auto_approve: list[str] = field(default_factory=list)
    always_ask: list[str] = field(default_factory=list)
    require_approval_for_medium_risk: bool = True
    block_high_risk_commands: bool = True
    max_actions_per_hour: int = 20
    max_cost_per_day_cents: int = 500

    # Expanded paths (resolved ~ and env vars)
    _workspace_expanded: str = field(default="", repr=False)
    _forbidden_expanded: list[str] = field(default_factory=list, repr=False)
    _roots_expanded: list[str] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._workspace_expanded = _expand_path(self.workspace_dir) if self.workspace_dir else ""
        self._forbidden_expanded = [
            _expand_path(p) for p in self.forbidden_paths if p
        ]
        self._roots_expanded = [
            _expand_path(p) for p in self.allowed_roots if p
        ]

    def check_cwd(self, cwd: str) -> PolicyCheckResult:
        """Check if a working directory is allowed by policy."""
        if not cwd:
            return PolicyCheckResult(allowed=False, reason="No cwd specified",
                                     policy_rule="workspace_only")

        # Resolve symlinks to prevent bypass via symlinked paths
        expanded = _expand_path(cwd)

        roots = [r for r in [self._workspace_expanded, *self._roots_expanded] if r]
        if any(_path_is_under(expanded, root) for root in roots):
            return PolicyCheckResult(allowed=True)

        # Check forbidden paths
        for forbidden in self._forbidden_expanded:
            if _path_is_under(expanded, forbidden):
                return PolicyCheckResult(
                    allowed=False,
                    reason=f"Path {cwd} is under forbidden path {forbidden}",
                    policy_rule="forbidden_paths",
                    suggestion=f"Use a path under one of: {', '.join(self.allowed_roots)}",
                )

        # Check allowed roots (if workspace_only)
        if self.workspace_only:
            display_roots = [self.workspace_dir, *self.allowed_roots]
            return PolicyCheckResult(
                allowed=False,
                reason=f"Path {cwd} is not under any allowed root",
                policy_rule="allowed_roots",
                suggestion=f"Allowed roots: {', '.join(r for r in display_roots if r)}",
            )

        return PolicyCheckResult(allowed=True)

    def check_command(self, command: str) -> PolicyCheckResult:
        """Check if a shell command is allowed by policy."""
        if not command:
            return PolicyCheckResult(allowed=True)

        # Extract base command (first word)
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.strip().split()
        base = tokens[0] if tokens else ""
        base = os.path.basename(base)  # handle /usr/bin/git -> git
        args = tokens[1:]

        # High-risk command patterns — always checked regardless of allowlist
        if self.block_high_risk_commands:
            high_risk = ["rm -rf", "chmod 777", "curl | sh", "wget | sh",
                         "dd if=", "mkfs", "> /dev/"]
            for pattern in high_risk:
                if pattern in command:
                    return PolicyCheckResult(
                        allowed=False,
                        reason=f"Command matches high-risk pattern: '{pattern}'",
                        policy_rule="block_high_risk_commands",
                        suggestion="Rewrite the command to avoid destructive patterns",
                    )

        if not self.allowed_commands:
            arg_check = self._check_dangerous_args(base, args)
            if arg_check is not None:
                return arg_check
            return PolicyCheckResult(allowed=True)  # No allowlist = allow all

        if base not in self.allowed_commands:
            return PolicyCheckResult(
                allowed=False,
                reason=f"Command '{base}' is not in the allowed commands list",
                policy_rule="allowed_commands",
                suggestion=f"Allowed commands: {', '.join(c for c in self.allowed_commands if c)}",
            )

        arg_check = self._check_dangerous_args(base, args)
        if arg_check is not None:
            return arg_check

        return PolicyCheckResult(allowed=True)

    def _check_dangerous_args(self, base: str, args: list[str]) -> PolicyCheckResult | None:
        """Mirror Rust's command argument safety checks for pre-flight."""
        base = base.lower()
        if base == "find":
            for arg in args:
                lower = arg.lower()
                if lower in {"-exec", "-ok"}:
                    return PolicyCheckResult(
                        allowed=False,
                        reason=f"Command argument '{arg}' can execute subcommands",
                        policy_rule="command_arguments",
                        suggestion="Avoid find arguments that execute commands",
                    )
        if base == "git":
            for arg in args:
                lower = arg.lower()
                if (
                    lower == "config"
                    or lower.startswith("config.")
                    or lower == "alias"
                    or lower.startswith("alias.")
                    or arg == "-c"
                    or arg.startswith("-c=")
                ):
                    return PolicyCheckResult(
                        allowed=False,
                        reason=f"Git argument '{arg}' is not allowed by policy",
                        policy_rule="command_arguments",
                        suggestion="Avoid git config, alias, or -c injection arguments",
                    )
        return None

    def check_tool(self, tool_name: str) -> PolicyCheckResult:
        """Check tool permission level."""
        if tool_name in self.auto_approve:
            return PolicyCheckResult(allowed=True)

        if tool_name in self.always_ask:
            return PolicyCheckResult(
                allowed=False,
                reason=f"Tool '{tool_name}' requires explicit approval",
                policy_rule="always_ask",
                suggestion="Use respond_to_permission to approve",
            )

        if self.require_approval_for_medium_risk:
            return PolicyCheckResult(
                allowed=True,  # Allowed but may trigger approval
                reason=f"Tool '{tool_name}' may require approval (medium risk policy active)",
                policy_rule="require_approval_for_medium_risk",
            )

        return PolicyCheckResult(allowed=True)

    def preflight_spawn(self, cwd: str, agent_type: str = "") -> list[PolicyCheckResult]:
        """Run all pre-flight checks for an agent spawn. Returns only failures."""
        checks = [self.check_cwd(cwd)]
        return [c for c in checks if not c.allowed]

    def to_dict(self) -> dict[str, Any]:
        """Return policy summary for tool responses."""
        return {
            "level": self.level,
            "workspace_only": self.workspace_only,
            "allowed_roots": [r for r in self.allowed_roots if r],
            "allowed_commands_count": len([c for c in self.allowed_commands if c]),
            "forbidden_paths_count": len([p for p in self.forbidden_paths if p]),
            "auto_approve_count": len([t for t in self.auto_approve if t]),
            "require_approval_for_medium_risk": self.require_approval_for_medium_risk,
            "block_high_risk_commands": self.block_high_risk_commands,
        }


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

_CONFIG_PATH = os.path.expanduser("~/.revka/config.toml")
_cached_policy: Policy | None = None


def load_policy(*, force_reload: bool = False) -> Policy:
    """Load policy from ~/.revka/config.toml. Caches after first load."""
    global _cached_policy
    if _cached_policy is not None and not force_reload:
        return _cached_policy

    if tomllib is None:
        _log("policy: tomllib not available, using defaults")
        _cached_policy = Policy()
        return _cached_policy

    try:
        with open(_CONFIG_PATH, "rb") as f:
            config = tomllib.load(f)
    except FileNotFoundError:
        _log(f"policy: {_CONFIG_PATH} not found, using defaults")
        _cached_policy = Policy()
        return _cached_policy
    except Exception as exc:
        _log(f"policy: error reading config: {exc}")
        _cached_policy = Policy()
        return _cached_policy

    autonomy = config.get("autonomy", {})
    _cached_policy = Policy(
        level=autonomy.get("level", "supervised"),
        workspace_dir=config.get("workspace_dir")
        or configured_workspace_dir(force_reload=force_reload),
        workspace_only=autonomy.get("workspace_only", True),
        allowed_commands=autonomy.get("allowed_commands", []),
        forbidden_paths=autonomy.get("forbidden_paths", []),
        allowed_roots=autonomy.get("allowed_roots", []),
        auto_approve=autonomy.get("auto_approve", []),
        always_ask=autonomy.get("always_ask", []),
        require_approval_for_medium_risk=autonomy.get("require_approval_for_medium_risk", True),
        block_high_risk_commands=autonomy.get("block_high_risk_commands", True),
        max_actions_per_hour=autonomy.get("max_actions_per_hour", 20),
        max_cost_per_day_cents=autonomy.get("max_cost_per_day_cents", 500),
    )
    _log(f"policy: loaded from {_CONFIG_PATH} (level={_cached_policy.level})")
    return _cached_policy


# ---------------------------------------------------------------------------
# Tool handler
# ---------------------------------------------------------------------------

async def tool_check_policy(args: dict[str, Any]) -> dict[str, Any]:
    """Check policy for a cwd, command, or tool before executing.

    Args:
        cwd: Working directory to check (optional).
        command: Shell command to check (optional).
        tool: Tool name to check (optional).
    """
    policy = load_policy()
    results: list[dict[str, Any]] = []

    cwd = args.get("cwd")
    if cwd:
        check = policy.check_cwd(cwd)
        results.append({"check": "cwd", "target": cwd, **check.to_dict()})

    command = args.get("command")
    if command:
        check = policy.check_command(command)
        results.append({"check": "command", "target": command, **check.to_dict()})

    tool = args.get("tool")
    if tool:
        check = policy.check_tool(tool)
        results.append({"check": "tool", "target": tool, **check.to_dict()})

    all_ok = all(r.get("allowed", False) for r in results)
    return {
        "policy_level": policy.level,
        "checks": results,
        "all_allowed": all_ok,
    }


async def tool_get_policy_summary(args: dict[str, Any]) -> dict[str, Any]:
    """Return the current autonomy policy summary."""
    policy = load_policy()
    return policy.to_dict()
