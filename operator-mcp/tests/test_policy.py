"""Tests for operator.policy — path checks, command checks, symlink handling."""
from __future__ import annotations

import os
import tempfile

import pytest

from operator_mcp.policy import Policy, PolicyCheckResult, load_policy


class TestPolicyCheckResult:
    def test_allowed(self):
        r = PolicyCheckResult(allowed=True)
        assert r.allowed is True
        d = r.to_dict()
        assert d == {"allowed": True}

    def test_denied(self):
        r = PolicyCheckResult(
            allowed=False,
            reason="blocked",
            policy_rule="test_rule",
            suggestion="try this",
        )
        d = r.to_dict()
        assert d["allowed"] is False
        assert d["reason"] == "blocked"
        assert d["policy_rule"] == "test_rule"
        assert d["suggestion"] == "try this"


class TestCheckCwd:
    def test_empty_cwd_denied(self):
        p = Policy()
        r = p.check_cwd("")
        assert r.allowed is False

    def test_allowed_with_no_restrictions(self):
        p = Policy(workspace_only=False, forbidden_paths=[], allowed_roots=[])
        r = p.check_cwd("/tmp/safe")
        assert r.allowed is True

    def test_forbidden_path_blocked(self):
        p = Policy(forbidden_paths=["/etc", "/var/secrets"])
        r = p.check_cwd("/etc/nginx")
        assert r.allowed is False
        assert "forbidden" in r.reason.lower()

    def test_allowed_roots_enforced(self):
        p = Policy(workspace_only=True, allowed_roots=["~/projects"])
        r = p.check_cwd("/opt/elsewhere")
        assert r.allowed is False
        assert "allowed root" in r.reason.lower()

    def test_under_allowed_root_ok(self, tmp_path):
        p = Policy(workspace_only=True, allowed_roots=[str(tmp_path)])
        sub = tmp_path / "sub" / "dir"
        sub.mkdir(parents=True)
        r = p.check_cwd(str(sub))
        assert r.allowed is True

    def test_workspace_dir_enforced_without_allowed_roots(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        sub = workspace / "repo"
        sub.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()

        p = Policy(workspace_only=True, workspace_dir=str(workspace), allowed_roots=[])
        assert p.check_cwd(str(sub)).allowed is True
        r = p.check_cwd(str(outside))
        assert r.allowed is False
        assert r.policy_rule == "allowed_roots"

    def test_allowed_root_uses_component_boundary(self, tmp_path):
        allowed = tmp_path / "allowed"
        sibling = tmp_path / "allowed-sibling"
        allowed.mkdir()
        sibling.mkdir()

        p = Policy(workspace_only=True, allowed_roots=[str(allowed)])
        r = p.check_cwd(str(sibling))
        assert r.allowed is False

    def test_symlink_resolved(self, tmp_path):
        """Symlink bypass: /tmp/safe -> /etc/secrets should be caught."""
        forbidden_dir = tmp_path / "forbidden"
        forbidden_dir.mkdir()
        link = tmp_path / "innocent_link"
        link.symlink_to(str(forbidden_dir))

        p = Policy(forbidden_paths=[str(forbidden_dir)])
        # Direct access blocked
        r_direct = p.check_cwd(str(forbidden_dir))
        assert r_direct.allowed is False
        # Symlink access also blocked (resolved via realpath)
        r_link = p.check_cwd(str(link))
        assert r_link.allowed is False

    def test_symlink_under_allowed_root(self, tmp_path):
        """Symlink pointing outside allowed root should be denied."""
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        link = allowed / "sneaky"
        link.symlink_to(str(outside))

        p = Policy(workspace_only=True, allowed_roots=[str(allowed)])
        r = p.check_cwd(str(link))
        assert r.allowed is False


class TestCheckCommand:
    def test_empty_command_allowed(self):
        p = Policy()
        r = p.check_command("")
        assert r.allowed is True

    def test_no_allowlist_allows_all(self):
        p = Policy(allowed_commands=[])
        r = p.check_command("git status")
        assert r.allowed is True

    def test_high_risk_blocked_even_without_allowlist(self):
        """High-risk patterns must be caught even when allowed_commands is empty."""
        p = Policy(allowed_commands=[], block_high_risk_commands=True)
        r = p.check_command("rm -rf /")
        assert r.allowed is False
        assert "high-risk" in r.reason.lower()

    def test_high_risk_patterns(self):
        p = Policy(block_high_risk_commands=True)
        patterns = ["rm -rf /tmp", "chmod 777 /etc", "curl | sh", "dd if=/dev/zero"]
        for cmd in patterns:
            r = p.check_command(cmd)
            assert r.allowed is False, f"Should block: {cmd}"

    def test_high_risk_disabled(self):
        p = Policy(block_high_risk_commands=False)
        r = p.check_command("rm -rf /tmp")
        assert r.allowed is True

    def test_allowed_commands_whitelist(self):
        p = Policy(allowed_commands=["git", "npm", "node"], block_high_risk_commands=False)
        assert p.check_command("git status").allowed is True
        assert p.check_command("npm install").allowed is True
        assert p.check_command("docker run").allowed is False

    def test_git_lowercase_c_blocked_but_uppercase_c_allowed(self):
        p = Policy(allowed_commands=["git"], block_high_risk_commands=False)
        assert p.check_command("git -c core.editor=calc.exe commit").allowed is False
        assert p.check_command("git -c=core.editor=calc.exe commit").allowed is False
        assert p.check_command("git config core.editor calc.exe").allowed is False
        assert p.check_command("git -C ./repo status").allowed is True

    def test_find_exec_blocked(self):
        p = Policy(allowed_commands=["find"], block_high_risk_commands=False)
        r = p.check_command("find . -exec rm -rf {} +")
        assert r.allowed is False
        assert r.policy_rule == "command_arguments"

    def test_full_path_resolved(self):
        p = Policy(allowed_commands=["git"])
        r = p.check_command("/usr/bin/git status")
        assert r.allowed is True


class TestCheckTool:
    def test_auto_approve(self):
        p = Policy(auto_approve=["Read", "Glob"])
        assert p.check_tool("Read").allowed is True

    def test_always_ask(self):
        p = Policy(always_ask=["Bash"])
        r = p.check_tool("Bash")
        assert r.allowed is False
        assert "approval" in r.reason.lower()

    def test_unknown_tool_with_medium_risk(self):
        p = Policy(require_approval_for_medium_risk=True)
        r = p.check_tool("SomeNewTool")
        assert r.allowed is True  # Allowed but flagged


class TestPreflightSpawn:
    def test_returns_failures_only(self, tmp_path):
        p = Policy(workspace_only=False)
        failures = p.preflight_spawn(str(tmp_path))
        assert failures == []

    def test_catches_bad_cwd(self):
        p = Policy(forbidden_paths=["/forbidden"])
        failures = p.preflight_spawn("/forbidden/sub")
        assert len(failures) == 1
        assert failures[0].allowed is False


class TestPolicyToDict:
    def test_summary_fields(self):
        p = Policy(
            level="autonomous",
            workspace_only=True,
            allowed_roots=["~/project"],
            allowed_commands=["git", "npm"],
            forbidden_paths=["/etc"],
            auto_approve=["Read"],
        )
        d = p.to_dict()
        assert d["level"] == "autonomous"
        assert d["workspace_only"] is True
        assert d["allowed_roots"] == ["~/project"]
        assert d["allowed_commands_count"] == 2
        assert d["forbidden_paths_count"] == 1
        assert d["auto_approve_count"] == 1
