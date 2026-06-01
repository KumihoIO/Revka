#!/usr/bin/env python3
"""Run deterministic Google Agents CLI demo-readiness probes.

The default mode installs a temporary fake ``agents-cli`` binary so the probe
can exercise Construct's Operator MCP handler without touching Google Cloud.
Use ``--output`` to keep the JSON evidence bundle for a demo checklist.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
OPERATOR_MCP = REPO_ROOT / "operator-mcp"
if str(OPERATOR_MCP) not in sys.path:
    sys.path.insert(0, str(OPERATOR_MCP))

from operator_mcp import construct_config  # noqa: E402
from operator_mcp.tool_handlers import google_agents_cli as google_agents_cli_handler  # noqa: E402


ProbeFn = Callable[[], Awaitable[dict[str, Any]]]


PUBLIC_LIFECYCLE_COMMANDS: tuple[str, ...] = (
    "create",
    "data-ingestion",
    "deploy",
    "eval",
    "info",
    "infra",
    "install",
    "lint",
    "login",
    "playground",
    "publish",
    "run",
    "scaffold",
    "setup",
    "update",
)

COMPATIBILITY_COMMANDS: tuple[str, ...] = ("cmd-info",)


@dataclass(frozen=True)
class Probe:
    name: str
    description: str
    run: ProbeFn


DEMO_OUTCOMES: tuple[dict[str, Any], ...] = (
    {
        "id": "existing_agent_tool_capability",
        "title": "Existing agent uses Google lifecycle tooling",
        "required_probes": ["architecture_guardrails"],
    },
    {
        "id": "cli_project_tooling_inspection",
        "title": "Current CLI project/tooling inspection",
        "required_probes": ["info"],
    },
    {
        "id": "public_lifecycle_command_surface",
        "title": "Public lifecycle command surface",
        "required_probes": ["lifecycle_command_surface"],
    },
    {
        "id": "prompt_only_run",
        "title": "Prompt-only run",
        "required_probes": ["prompt_run"],
    },
    {
        "id": "successful_lifecycle_command",
        "title": "Successful lifecycle command",
        "required_probes": ["successful_lifecycle"],
    },
    {
        "id": "cli_failure",
        "title": "CLI failure",
        "required_probes": ["eval_failure"],
    },
    {
        "id": "missing_agents_cli_binary",
        "title": "Missing agents-cli binary",
        "required_probes": ["missing_binary"],
    },
    {
        "id": "malformed_command_input",
        "title": "Malformed command input",
        "required_probes": ["invalid_command"],
    },
    {
        "id": "interactive_login_attempt",
        "title": "Interactive login attempt",
        "required_probes": ["interactive_login"],
    },
    {
        "id": "bad_working_directory",
        "title": "Bad working directory",
        "required_probes": ["bad_working_directory"],
    },
    {
        "id": "timeout",
        "title": "Timeout",
        "required_probes": ["timeout"],
    },
    {
        "id": "large_output",
        "title": "Large output",
        "required_probes": ["truncation"],
    },
    {
        "id": "spawn_failure",
        "title": "Spawn failure",
        "required_probes": ["spawn_failure"],
    },
    {
        "id": "gemini_enterprise_publish_context",
        "title": "Gemini Enterprise publish context",
        "required_probes": ["enterprise_env"],
    },
    {
        "id": "runtime_safety_policy",
        "title": "Runtime safety policy",
        "required_probes": ["runtime_safety_policy"],
    },
    {
        "id": "deploy_command_acceptance",
        "title": "Deploy command acceptance",
        "required_probes": ["deploy_acceptance"],
    },
)


def _write_fake_agents_cli(bin_dir: Path) -> Path:
    script = bin_dir / ("agents-cli.exe" if os.name == "nt" else "agents-cli")
    script.write_text(
        """#!/usr/bin/env python3
import os
import sys
import time

args = sys.argv[1:]
cmd = args[0] if args else ""

if cmd == "info":
    print("project=demo-agent-platform")
    print("runtime=adk")
    sys.exit(0)
if cmd == "lint":
    print("lint ok")
    sys.exit(0)
if cmd == "deploy":
    print("deploy accepted --dry-run")
    sys.exit(0)
if cmd == "publish":
    print("publish accepted " + " ".join(args[1:]))
    print("enterprise_app=" + os.environ.get("GEMINI_ENTERPRISE_APP_ID", ""))
    sys.exit(0)
if cmd == "eval":
    print("baseline_score=0.42")
    print("surge pricing conflict reproduced", file=sys.stderr)
    sys.exit(7)
if cmd == "run":
    prompt = args[-1] if len(args) > 1 else ""
    if prompt == "sleep":
        time.sleep(5)
    elif prompt == "large-output":
        print("x" * 4096)
    else:
        print("optimized_response=comfort-first-with-cost-cap")
        print("prompt=" + prompt)
    sys.exit(0)
if cmd == "login" and "--status" in args:
    print("logged_in=false")
    sys.exit(0)

print("unsupported fake command: " + " ".join(args), file=sys.stderr)
sys.exit(64)
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _scrub(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, str):
        home = str(Path.home())
        return value.replace(home, "~")
    return value


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def _call(args: dict[str, Any]) -> dict[str, Any]:
    return await google_agents_cli_handler.tool_google_agents_cli(args)


def _dict_value(node: ast.Dict, key: str) -> ast.AST | None:
    for dict_key, value in zip(node.keys, node.values):
        if isinstance(dict_key, ast.Constant) and dict_key.value == key:
            return value
    return None


def _string_list(node: ast.AST | None) -> list[str] | None:
    if not isinstance(node, ast.List):
        return None
    values: list[str] = []
    for item in node.elts:
        if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
            return None
        values.append(item.value)
    return values


def _operator_agent_type_enums() -> list[list[str]]:
    source = (OPERATOR_MCP / "operator_mcp" / "operator_mcp.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    enums: list[list[str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        agent_type_schema = _dict_value(node, "agent_type")
        if not isinstance(agent_type_schema, ast.Dict):
            continue
        enum = _string_list(_dict_value(agent_type_schema, "enum"))
        if enum is not None:
            enums.append(enum)
    return enums


def _rust_allowed_commands() -> list[str]:
    source = (REPO_ROOT / "src" / "tools" / "google_agents_cli.rs").read_text(encoding="utf-8")
    match = re.search(
        r"const\s+ALLOWED_TOP_LEVEL_COMMANDS\s*:\s*&\s*\[\s*&str\s*\]\s*=\s*&\[(.*?)\];",
        source,
        re.DOTALL,
    )
    _assert(match is not None, "Rust google_agents_cli allowed command list not found")
    return re.findall(r'"([^"]+)"', match.group(1))


async def _expect_lifecycle_command_surface() -> dict[str, Any]:
    expected = set(PUBLIC_LIFECYCLE_COMMANDS) | set(COMPATIBILITY_COMMANDS)
    python_allowed = set(google_agents_cli_handler.ALLOWED_TOP_LEVEL_COMMANDS)
    rust_allowed = set(_rust_allowed_commands())

    _assert(expected <= python_allowed, "Operator MCP missing allowed agents-cli commands")
    _assert(expected <= rust_allowed, "Rust tool missing allowed agents-cli commands")
    _assert(python_allowed == rust_allowed, "Rust and Operator MCP allowed command sets differ")

    checked_sources = {
        "operator_mcp": OPERATOR_MCP / "operator_mcp" / "operator_mcp.py",
        "rust_tool": REPO_ROOT / "src" / "tools" / "google_agents_cli.rs",
        "config_reference": REPO_ROOT / "docs" / "reference" / "api" / "config-reference.md",
        "demo_readiness": REPO_ROOT / "docs" / "ops" / "google-agents-cli-demo-readiness.md",
    }
    missing: dict[str, list[str]] = {}
    for name, path in checked_sources.items():
        text = path.read_text(encoding="utf-8")
        source_missing = [command for command in PUBLIC_LIFECYCLE_COMMANDS if command not in text]
        if source_missing:
            missing[name] = source_missing
    _assert(not missing, f"documented/tool command surface is incomplete: {missing}")

    return {
        "public_lifecycle_commands": list(PUBLIC_LIFECYCLE_COMMANDS),
        "compatibility_commands": list(COMPATIBILITY_COMMANDS),
        "python_allowed_commands": sorted(python_allowed),
        "rust_allowed_commands": sorted(rust_allowed),
        "checked_sources": {name: str(path) for name, path in checked_sources.items()},
    }


async def _expect_architecture_guardrails() -> dict[str, Any]:
    enums = _operator_agent_type_enums()
    _assert(enums, "operator agent_type schemas should declare enum values")
    for enum in enums:
        _assert(enum == ["claude", "codex"], f"agent_type enum should be claude/codex only: {enum}")

    source_checks = {
        "operator_mcp": OPERATOR_MCP / "operator_mcp" / "operator_mcp.py",
        "operator_core": REPO_ROOT / "src" / "agent" / "operator" / "core.rs",
        "gateway_ws": REPO_ROOT / "src" / "gateway" / "ws.rs",
        "rust_tool": REPO_ROOT / "src" / "tools" / "google_agents_cli.rs",
    }
    required_phrases = {
        "operator_mcp": [
            "spawn claude/codex and let it call google_agents_cli",
            "Run Google Agents CLI (agents-cli) lifecycle commands",
        ],
        "operator_core": [
            "with the google_agents_cli tool; agents-cli is not a peer coding agent",
        ],
        "gateway_ws": [
            "agents-cli is not an agent_type",
        ],
        "rust_tool": [
            "`agents-cli` is not a coding-agent replacement",
            "never a shell",
        ],
    }
    checked: list[str] = []
    for name, path in source_checks.items():
        text = path.read_text(encoding="utf-8")
        for phrase in required_phrases[name]:
            _assert(phrase in text, f"{name} missing guardrail phrase: {phrase}")
        checked.append(name)

    return {
        "agent_type_enums": enums,
        "checked_sources": checked,
    }


async def _expect_success_info() -> dict[str, Any]:
    result = await _call({"command": ["info"]})
    _assert(result.get("success") is True, "info command should succeed")
    _assert(result.get("command") == ["info"], "info command preview should be exact")
    _assert("project=demo-agent-platform" in result.get("output", ""), "info output missing project")
    return result


async def _expect_successful_lifecycle(workspace: Path) -> dict[str, Any]:
    result = await _call({"command": ["lint"], "working_directory": "adk-project"})
    expected_cwd = os.path.realpath(workspace / "adk-project")
    _assert(result.get("success") is True, "successful lifecycle command should pass")
    _assert(result.get("status") == "completed", "successful lifecycle status should be completed")
    _assert(result.get("exit_code") == 0, "successful lifecycle should preserve exit code 0")
    _assert(result.get("command") == ["lint"], "successful lifecycle command preview should be exact")
    _assert(result.get("cwd") == expected_cwd, "successful lifecycle cwd should be resolved")
    _assert("lint ok" in result.get("output", ""), "successful lifecycle stdout missing")
    return result


async def _expect_prompt_run_redaction() -> dict[str, Any]:
    result = await _call({"prompt": "simulate peak pricing and heat wave"})
    _assert(result.get("success") is True, "prompt-only run should succeed")
    _assert(result.get("command") == ["run", "..."], "prompt should be redacted in preview")
    _assert("optimized_response=" in result.get("output", ""), "run output missing optimized response")
    return result


async def _expect_eval_failure_diagnostics() -> dict[str, Any]:
    result = await _call({"command": ["eval", "run"]})
    _assert(result.get("success") is False, "failing eval should report failure")
    _assert(result.get("exit_code") == 7, "failing eval should preserve exit code")
    _assert("baseline_score=0.42" in result.get("output", ""), "failing eval should preserve stdout")
    _assert("surge pricing" in result.get("stderr", ""), "failing eval should preserve stderr")
    return result


async def _expect_invalid_command_shape() -> dict[str, Any]:
    cases = [
        ({"command": ["run", 1]}, "strings"),
        ({"command": {"name": "info"}}, "string or list"),
        ({"command": [""]}, "empty"),
        ({"command": [" run"]}, "whitespace"),
        ({"command": ["not-a-google-agents-command"]}, "Unsupported"),
        ({"command": ["run", "bad\0prompt"]}, "NUL"),
    ]
    results = []
    for args, expected_error in cases:
        result = await _call(args)
        _assert(result.get("error_code") == "invalid_command", f"{args!r} should be invalid")
        _assert(
            expected_error in result.get("error", ""),
            f"{args!r} error should mention {expected_error!r}",
        )
        results.append({"args": _scrub(args), "error": result.get("error")})
    return {"cases": results}


async def _expect_interactive_login_block() -> dict[str, Any]:
    result = await _call({"command": ["login", "--interactive"]})
    _assert(result.get("error_code") == "invalid_command", "interactive login should be blocked")
    _assert("Interactive" in result.get("error", ""), "interactive login error should be explicit")
    return result


async def _expect_workspace_escape_block(workspace: Path) -> dict[str, Any]:
    outside = workspace.parent / "outside"
    outside.mkdir(exist_ok=True)
    result = await _call({"command": ["login", "--status"], "working_directory": str(outside)})
    _assert(result.get("error_code") == "bad_working_directory", "cwd escape should be blocked")
    _assert("outside the workspace" in result.get("error", ""), "cwd error should mention workspace")
    return result


async def _expect_timeout() -> dict[str, Any]:
    result = await _call({"prompt": "sleep", "timeout": 0.05})
    _assert(result.get("status") == "timeout", "sleeping command should time out")
    _assert(result.get("success") is False, "timeout should fail safely")
    return result


async def _expect_truncation() -> dict[str, Any]:
    result = await _call({"prompt": "large-output", "max_output_bytes": 32})
    _assert(result.get("success") is True, "large output command should succeed")
    _assert("[output truncated]" in result.get("output", ""), "large output should be truncated")
    return result


async def _expect_enterprise_env() -> dict[str, Any]:
    result = await _call({"command": ["publish", "gemini-enterprise", "--list"]})
    _assert(result.get("success") is True, "publish list should succeed in fake probe")
    _assert("enterprise_app=demo-enterprise-app" in result.get("output", ""), "enterprise env not passed")
    return result


async def _expect_deploy_acceptance() -> dict[str, Any]:
    result = await _call({"command": ["deploy"]})
    _assert(result.get("success") is True, "deploy command should be accepted")
    _assert("deploy accepted" in result.get("output", ""), "deploy output missing acceptance")
    return result


async def _expect_missing_binary(fake_path: str) -> dict[str, Any]:
    empty_bin = Path(tempfile.mkdtemp(prefix="agents-cli-empty-bin-"))
    try:
        os.environ["PATH"] = str(empty_bin)
        result = await _call({"command": ["login", "--status"]})
    finally:
        os.environ["PATH"] = fake_path
        shutil.rmtree(empty_bin, ignore_errors=True)
    _assert(result.get("error_code") == "agents_cli_missing", "missing binary should be structured")
    _assert(result.get("error_category") == "runtime_env_error", "missing binary category should be runtime")
    return result


async def _expect_spawn_failure() -> dict[str, Any]:
    temp_dir = Path(tempfile.mkdtemp(prefix="agents-cli-spawn-failure-"))
    fake_binary = temp_dir / "agents-cli"
    fake_binary.write_text("#!/bin/sh\n", encoding="utf-8")
    original_which = google_agents_cli_handler.shutil.which
    original_create_subprocess_exec = google_agents_cli_handler.asyncio.create_subprocess_exec

    async def raise_os_error(*_args: Any, **_kwargs: Any) -> Any:
        raise PermissionError("permission denied")

    try:
        google_agents_cli_handler.shutil.which = lambda _name: str(fake_binary)
        google_agents_cli_handler.asyncio.create_subprocess_exec = raise_os_error
        result = await _call({"command": ["login", "--status"]})
    finally:
        google_agents_cli_handler.shutil.which = original_which
        google_agents_cli_handler.asyncio.create_subprocess_exec = original_create_subprocess_exec
        shutil.rmtree(temp_dir, ignore_errors=True)

    _assert(result.get("error_code") == "agents_cli_spawn_failed", "spawn error should be structured")
    _assert(result.get("error_category") == "runtime_env_error", "spawn error category should be runtime")
    _assert(result.get("retryable") is True, "spawn error should be retryable")
    return result


async def _expect_runtime_safety_policy() -> dict[str, Any]:
    rust_tool = REPO_ROOT / "src" / "tools" / "google_agents_cli.rs"
    source = rust_tool.read_text(encoding="utf-8")
    required_fragments = [
        "self.security.is_rate_limited()",
        "enforce_tool_operation",
        "ToolOperation::Act",
        "record_action()",
        "google_agents_cli_blocks_rate_limited",
        "google_agents_cli_blocks_readonly",
    ]
    for fragment in required_fragments:
        _assert(fragment in source, f"runtime safety source missing: {fragment}")
    return {"checked_source": str(rust_tool), "required_fragments": required_fragments}


async def _run_probes(workspace: Path, fake_path: str) -> list[dict[str, Any]]:
    probes: list[Probe] = [
        Probe(
            "architecture_guardrails",
            "agents-cli remains a tool capability for claude/codex agents, not an agent_type",
            _expect_architecture_guardrails,
        ),
        Probe("info", "Current project/tooling inspection", _expect_success_info),
        Probe(
            "lifecycle_command_surface",
            "Rust, Operator MCP, and docs cover the current public agents-cli lifecycle surface",
            _expect_lifecycle_command_surface,
        ),
        Probe(
            "successful_lifecycle",
            "Successful lifecycle command reports status, exit code, cwd, preview, and stdout",
            lambda: _expect_successful_lifecycle(workspace),
        ),
        Probe("prompt_run", "Prompt-only agents-cli run with redacted preview", _expect_prompt_run_redaction),
        Probe("eval_failure", "Eval failure preserves stdout/stderr/exit code", _expect_eval_failure_diagnostics),
        Probe("invalid_command", "Malformed command input is rejected before spawn", _expect_invalid_command_shape),
        Probe("interactive_login", "Interactive login is blocked by default", _expect_interactive_login_block),
        Probe(
            "bad_working_directory",
            "Working directory outside workspace is blocked",
            lambda: _expect_workspace_escape_block(workspace),
        ),
        Probe("timeout", "Long-running commands return timeout status", _expect_timeout),
        Probe("truncation", "Large output is truncated with explicit marker", _expect_truncation),
        Probe("enterprise_env", "Gemini Enterprise app id passes through safe env", _expect_enterprise_env),
        Probe("deploy_acceptance", "Deploy command shape is accepted", _expect_deploy_acceptance),
        Probe(
            "missing_binary",
            "Missing agents-cli binary returns structured error",
            lambda: _expect_missing_binary(fake_path),
        ),
        Probe("spawn_failure", "OS-level spawn errors return structured retryable errors", _expect_spawn_failure),
        Probe(
            "runtime_safety_policy",
            "Rust tool enforces read-only mode and rate/action limits before execution",
            _expect_runtime_safety_policy,
        ),
    ]

    results: list[dict[str, Any]] = []
    for probe in probes:
        try:
            result = await probe.run()
            results.append(
                {
                    "name": probe.name,
                    "description": probe.description,
                    "status": "pass",
                    "result": _scrub(result),
                }
            )
        except Exception as exc:  # noqa: BLE001 - this is a diagnostic probe.
            results.append(
                {
                    "name": probe.name,
                    "description": probe.description,
                    "status": "fail",
                    "error": str(exc),
                }
            )
    return results


def _build_outcome_matrix(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_name = {item.get("name"): item for item in results}
    outcomes: list[dict[str, Any]] = []
    for outcome in DEMO_OUTCOMES:
        required_probes = outcome["required_probes"]
        missing = [name for name in required_probes if name not in by_name]
        failed = [
            name
            for name in required_probes
            if name in by_name and by_name[name].get("status") != "pass"
        ]
        failures = []
        if missing:
            failures.append("missing required probes: " + ", ".join(missing))
        if failed:
            failures.append("failed required probes: " + ", ".join(failed))
        outcomes.append(
            {
                "id": outcome["id"],
                "title": outcome["title"],
                "required_probes": required_probes,
                "status": "fail" if failures else "pass",
                "failures": failures,
            }
        )

    failed_outcomes = [item for item in outcomes if item["status"] != "pass"]
    return {
        "summary": {
            "total": len(outcomes),
            "passed": len(outcomes) - len(failed_outcomes),
            "failed": len(failed_outcomes),
        },
        "outcomes": outcomes,
    }


async def _main_async(args: argparse.Namespace) -> int:
    original_path = os.environ.get("PATH", "")
    original_workspace = os.environ.get("CONSTRUCT_WORKSPACE")
    original_enterprise_app = os.environ.get("GEMINI_ENTERPRISE_APP_ID")
    with tempfile.TemporaryDirectory(prefix="construct-google-agents-probe-") as temp:
        root = Path(temp)
        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / "adk-project").mkdir()
        bin_dir = root / "bin"
        bin_dir.mkdir()
        _write_fake_agents_cli(bin_dir)

        fake_path = str(bin_dir) + os.pathsep + original_path
        os.environ["PATH"] = fake_path
        os.environ["CONSTRUCT_WORKSPACE"] = str(workspace)
        os.environ["GEMINI_ENTERPRISE_APP_ID"] = "demo-enterprise-app"
        construct_config._cached_workspace_dir = None

        results = await _run_probes(workspace, fake_path)

    os.environ["PATH"] = original_path
    if original_workspace is None:
        os.environ.pop("CONSTRUCT_WORKSPACE", None)
    else:
        os.environ["CONSTRUCT_WORKSPACE"] = original_workspace
    if original_enterprise_app is None:
        os.environ.pop("GEMINI_ENTERPRISE_APP_ID", None)
    else:
        os.environ["GEMINI_ENTERPRISE_APP_ID"] = original_enterprise_app
    construct_config._cached_workspace_dir = None
    failures = [item for item in results if item["status"] != "pass"]
    outcome_matrix = _build_outcome_matrix(results)
    failed_outcomes = outcome_matrix["summary"]["failed"]
    bundle = {
        "probe": "google_agents_cli_demo_readiness",
        "mode": "deterministic_fake_agents_cli",
        "repo": str(REPO_ROOT),
        "passed": len(failures) == 0 and failed_outcomes == 0,
        "summary": {
            "total": len(results),
            "passed": len(results) - len(failures),
            "failed": len(failures),
        },
        "outcome_matrix": outcome_matrix,
        "results": results,
    }
    text = json.dumps(bundle, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    if not args.quiet:
        print(text)
    return 0 if not failures and failed_outcomes == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="Write JSON evidence bundle to this path")
    parser.add_argument("--quiet", action="store_true", help="Only set exit status and optional output file")
    args = parser.parse_args(argv)
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
