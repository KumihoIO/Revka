#!/usr/bin/env python3
"""Run the Google Agents CLI pre-recording readiness gate.

This is the umbrella gate for demo rehearsals. It composes the deterministic
local code probe with the strict Track 2 evidence gate, and can optionally check
PR health through GitHub CLI.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
REQUIRED_PR_CHECKS = (
    "CI Required Gate",
    "Security Required Gate",
)


def _check(name: str, status: str, failures: list[str] | None = None, **details: Any) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "failures": failures or [],
        **details,
    }


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _run_direct(cmd: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _snippet(text: str, limit: int = 1200) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... [truncated]"


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _limited(values: list[str], limit: int = 5) -> list[str]:
    return values[:limit]


def _load_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, f"missing JSON artifact: {path}"
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON artifact {path}: {exc}"
    if not isinstance(data, dict):
        return None, f"JSON artifact must be an object: {path}"
    return data, None


def _run_local_probe(output_dir: Path) -> dict[str, Any]:
    artifact = output_dir / "google_agents_cli_demo_probe.json"
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "demo" / "google_agents_cli_demo_probe.py"),
        "--quiet",
        "--output",
        str(artifact),
    ]
    result = _run(cmd)
    report, load_error = _load_json(artifact)
    failures: list[str] = []
    if result.returncode != 0:
        failures.append(f"local probe exited {result.returncode}")
    if load_error:
        failures.append(load_error)
    if report is not None and report.get("passed") is not True:
        failures.append("local probe report did not pass")
    summary = report.get("summary") if isinstance(report, dict) else None
    outcome_matrix = report.get("outcome_matrix") if isinstance(report, dict) else None
    outcome_summary = outcome_matrix.get("summary") if isinstance(outcome_matrix, dict) else None
    failed_outcomes = []
    if isinstance(outcome_matrix, dict):
        outcomes = outcome_matrix.get("outcomes")
        if isinstance(outcomes, list):
            failed_outcomes = [
                item
                for item in outcomes
                if isinstance(item, dict) and item.get("status") != "pass"
            ]
    if isinstance(outcome_summary, dict) and outcome_summary.get("failed"):
        failures.append(f"local outcome matrix failures: {outcome_summary.get('failed')}")
    if isinstance(summary, dict) and summary.get("failed") != 0:
        failures.append(f"local probe failures: {summary.get('failed')}")
    return _check(
        "local_code_probe",
        "fail" if failures else "pass",
        failures,
        artifact=str(artifact),
        summary=summary,
        outcome_matrix_summary=outcome_summary,
        failed_outcomes=failed_outcomes,
        stderr=result.stderr.strip(),
    )


def _run_track2_gate(evidence_dir: Path, output_dir: Path) -> dict[str, Any]:
    artifact = output_dir / "google_agents_cli_track2_evidence_gate.json"
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "demo" / "google_agents_cli_track2_evidence_gate.py"),
        "--evidence-dir",
        str(evidence_dir),
        "--output",
        str(artifact),
    ]
    result = _run(cmd)
    report, load_error = _load_json(artifact)
    failures: list[str] = []
    if result.returncode != 0:
        failures.append(f"Track 2 evidence gate exited {result.returncode}")
    if load_error:
        failures.append(load_error)
    if report is not None and report.get("passed") is not True:
        failures.append("Track 2 evidence report did not pass")
    summary = report.get("summary") if isinstance(report, dict) else None
    global_failures = _string_list(report.get("global_failures")) if report else []
    failed_claims: list[str] = []
    failure_details: list[dict[str, Any]] = []
    checks = report.get("checks") if isinstance(report, dict) else []
    if isinstance(checks, list):
        for item in checks:
            if not isinstance(item, dict) or item.get("status") != "fail":
                continue
            claim = item.get("claim")
            if not isinstance(claim, str):
                continue
            claim_failures = _string_list(item.get("failures"))
            failed_claims.append(claim)
            failure_details.append(
                {
                    "claim": claim,
                    "failure_count": len(claim_failures),
                    "failures": _limited(claim_failures),
                }
            )
    return _check(
        "track2_evidence_gate",
        "fail" if failures else "pass",
        failures,
        artifact=str(artifact),
        evidence_dir=str(evidence_dir),
        summary=summary,
        global_failures=global_failures,
        failed_claims=failed_claims,
        failure_details=failure_details,
        remediation=[
            "replace the Track 2 manifest placeholders and capture the required evidence artifacts",
            "rerun scripts/demo/google_agents_cli_track2_evidence_gate.py before recording",
        ]
        if failures
        else [],
        stderr=result.stderr.strip(),
    )


def _surface_command(
    binary: str,
    name: str,
    args: list[str],
    required_terms: list[str],
) -> tuple[dict[str, Any], list[str]]:
    result = _run_direct([binary, *args])
    if result is None:
        return {
            "name": name,
            "command": ["agents-cli", *args],
            "returncode": None,
            "stdout": "",
            "stderr": "command failed to start or timed out",
        }, [f"{name} failed to start or timed out"]
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    combined = f"{stdout}\n{stderr}".lower()
    failures = []
    if result.returncode != 0:
        failures.append(f"{name} exited {result.returncode}")
    for term in required_terms:
        if term.lower() not in combined:
            failures.append(f"{name} missing expected term: {term}")
    return {
        "name": name,
        "command": ["agents-cli", *args],
        "returncode": result.returncode,
        "stdout": _snippet(stdout),
        "stderr": _snippet(stderr),
    }, failures


def _login_status_authenticated(stdout: str, stderr: str) -> bool:
    text = f"{stdout}\n{stderr}".lower()
    normalized = " ".join(text.replace("_", " ").replace("-", " ").split())
    negative_terms = (
        "not authenticated",
        "unauthenticated",
        "authentication=false",
        "authentication false",
        "authenticated=false",
        "authenticated false",
        "logged in false",
        "logged_in false",
        "logged in=false",
        "logged_in=false",
    )
    if any(term in text for term in negative_terms) or any(
        term in normalized for term in negative_terms
    ):
        return False
    positive_terms = (
        "authenticated as",
        "authentication=true",
        "authentication true",
        "authenticated=true",
        "authenticated true",
        "logged in true",
        "logged_in true",
        "logged in=true",
        "logged_in=true",
    )
    return any(term in text for term in positive_terms) or any(
        term in normalized for term in positive_terms
    )


def _run_real_agents_cli_gate(require_auth: bool) -> dict[str, Any]:
    binary = shutil.which("agents-cli")
    failures: list[str] = []
    command_reports: list[dict[str, Any]] = []
    if not binary:
        return _check(
            "real_agents_cli",
            "fail",
            ["agents-cli was not found in PATH"],
            require_auth=require_auth,
            remediation=["install agents-cli and ensure it is on PATH before recording"],
        )

    surfaces = [
        (
            "top_level_help",
            ["--help"],
            [
                "setup",
                "create",
                "scaffold",
                "install",
                "lint",
                "run",
                "eval",
                "deploy",
                "publish",
                "infra",
                "data-ingestion",
                "playground",
                "update",
                "info",
                "login",
                "Agent Development Lifecycle",
            ],
        ),
        ("eval_help", ["eval", "--help"], ["run", "compare", "optimize"]),
        ("eval_optimize_help", ["eval", "optimize", "--help"], ["GEPA", "adk optimize"]),
        ("deploy_help", ["deploy", "--help"], ["Agent Runtime", "Cloud Run", "GKE", "--dry-run", "--status"]),
        ("publish_help", ["publish", "--help"], ["gemini-enterprise"]),
        ("info", ["info"], ["CLI version"]),
        ("login_status", ["login", "--status"], ["Authentication"]),
    ]
    for name, args, required_terms in surfaces:
        report, command_failures = _surface_command(binary, name, args, required_terms)
        command_reports.append(report)
        failures.extend(command_failures)

    login_report = next((item for item in command_reports if item["name"] == "login_status"), {})
    authenticated = _login_status_authenticated(
        str(login_report.get("stdout", "")),
        str(login_report.get("stderr", "")),
    )
    remediation: list[str] = []
    if require_auth and not authenticated:
        failures.append("agents-cli login --status did not report an authenticated session")
        remediation.append("run agents-cli login -i outside Construct, then rerun the strict gate")

    return _check(
        "real_agents_cli",
        "fail" if failures else "pass",
        failures,
        binary=binary,
        require_auth=require_auth,
        authenticated=authenticated,
        commands=command_reports,
        remediation=remediation,
    )


def _repo_parts(repo: str) -> tuple[str, str]:
    owner, sep, name = repo.partition("/")
    if not sep or not owner or not name:
        raise ValueError("repo must be in OWNER/NAME form")
    return owner, name


def _run_json_command(name: str, cmd: list[str]) -> tuple[Any | None, list[str], str]:
    result = _run(cmd)
    if result.returncode != 0:
        return None, [f"{name} exited {result.returncode}", result.stderr.strip()], result.stderr.strip()
    try:
        return json.loads(result.stdout), [], result.stderr.strip()
    except json.JSONDecodeError as exc:
        return None, [f"{name} returned invalid JSON: {exc}"], result.stderr.strip()


def _local_head() -> str | None:
    result = _run(["git", "rev-parse", "HEAD"])
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _git_stdout(name: str, args: list[str]) -> tuple[str | None, list[str], str]:
    result = _run(["git", *args])
    stderr = result.stderr.strip()
    if result.returncode != 0:
        failure = f"{name} exited {result.returncode}"
        if stderr:
            failure = f"{failure}: {_snippet(stderr, 400)}"
        return None, [failure], stderr
    return result.stdout.strip(), [], stderr


def _parse_ahead_behind(value: str) -> tuple[int | None, int | None]:
    parts = value.replace("\t", " ").split()
    if len(parts) != 2:
        return None, None
    try:
        behind, ahead = int(parts[0]), int(parts[1])
    except ValueError:
        return None, None
    return behind, ahead


def _run_local_git_gate(base_ref: str) -> dict[str, Any]:
    failures: list[str] = []
    branch = None
    base_oid = None
    upstream = None
    upstream_behind = None
    upstream_ahead = None
    dirty_entries: list[str] = []

    branch, command_failures, _stderr = _git_stdout(
        "git rev-parse --abbrev-ref HEAD",
        ["rev-parse", "--abbrev-ref", "HEAD"],
    )
    failures.extend(command_failures)
    if branch == "main":
        failures.append("local branch is main; use a non-main PR branch")
    elif branch == "HEAD":
        failures.append("local checkout is detached; use the PR branch before recording")

    status = _run(["git", "status", "--porcelain"])
    if status.returncode != 0:
        failure = f"git status --porcelain exited {status.returncode}"
        if status.stderr.strip():
            failure = f"{failure}: {_snippet(status.stderr, 400)}"
        failures.append(failure)
    else:
        dirty_entries = status.stdout.splitlines()
        if dirty_entries:
            failures.append(f"working tree has uncommitted changes: {len(dirty_entries)}")

    base_oid, command_failures, _stderr = _git_stdout(
        f"git rev-parse --verify {base_ref}",
        ["rev-parse", "--verify", base_ref],
    )
    if command_failures:
        failures.append(f"base ref is not available locally: {base_ref}")
        failures.extend(command_failures)
    else:
        base_contains = _run(["git", "merge-base", "--is-ancestor", base_ref, "HEAD"])
        if base_contains.returncode == 1:
            failures.append(f"HEAD does not contain base ref {base_ref}")
        elif base_contains.returncode != 0:
            failure = f"git merge-base --is-ancestor {base_ref} HEAD exited {base_contains.returncode}"
            if base_contains.stderr.strip():
                failure = f"{failure}: {_snippet(base_contains.stderr, 400)}"
            failures.append(failure)

    upstream, command_failures, _stderr = _git_stdout(
        "git rev-parse --abbrev-ref --symbolic-full-name @{u}",
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
    )
    if command_failures:
        failures.append("local branch has no upstream; push it and set upstream before recording")
        failures.extend(command_failures)
    else:
        counts, command_failures, _stderr = _git_stdout(
            f"git rev-list --left-right --count {upstream}...HEAD",
            ["rev-list", "--left-right", "--count", f"{upstream}...HEAD"],
        )
        if command_failures:
            failures.extend(command_failures)
        elif counts is not None:
            upstream_behind, upstream_ahead = _parse_ahead_behind(counts)
            if upstream_behind is None or upstream_ahead is None:
                failures.append(f"could not parse upstream divergence counts: {counts!r}")
            else:
                if upstream_behind:
                    failures.append(
                        f"local branch is behind upstream {upstream} by {upstream_behind} commit(s)"
                    )
                if upstream_ahead:
                    failures.append(
                        f"local branch is ahead upstream {upstream} by {upstream_ahead} commit(s); push before recording"
                    )

    return _check(
        "local_git_state",
        "fail" if failures else "pass",
        failures,
        branch=branch,
        base_ref=base_ref,
        base_oid=base_oid,
        upstream=upstream,
        upstream_behind=upstream_behind,
        upstream_ahead=upstream_ahead,
        dirty_count=len(dirty_entries),
        dirty_entries=_limited(dirty_entries, 20),
    )


def _run_pr_gate(pr_number: int, repo: str, expected_base_ref: str) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    owner, repo_name = _repo_parts(repo)

    pr_checks, failures, stderr = _run_json_command(
        "gh pr checks",
        [
            "gh",
            "pr",
            "checks",
            str(pr_number),
            "--repo",
            repo,
            "--json",
            "name,state,workflow",
        ],
    )
    if isinstance(pr_checks, list):
        if not pr_checks:
            failures.append("GitHub checks list is empty")
        non_success = [
            item
            for item in pr_checks
            if item.get("state") not in {"SUCCESS", "SKIPPED", "NEUTRAL"}
        ]
        if non_success:
            failures.append(f"non-success GitHub checks: {len(non_success)}")
        required_check_states: dict[str, list[str]] = {}
        for required_check in REQUIRED_PR_CHECKS:
            matches = [
                item
                for item in pr_checks
                if item.get("name") == required_check
            ]
            states = sorted(
                {
                    str(item.get("state"))
                    for item in matches
                    if item.get("state") is not None
                }
            )
            required_check_states[required_check] = states
            if not matches:
                failures.append(f"required GitHub check is missing: {required_check}")
            elif "SUCCESS" not in states:
                failures.append(
                    f"required GitHub check is not successful: "
                    f"{required_check} ({', '.join(states)})"
                )
        checks.append(
            _check(
                "github_pr_checks",
                "fail" if failures else "pass",
                failures,
                total=len(pr_checks),
                non_success=non_success,
                required_checks=list(REQUIRED_PR_CHECKS),
                required_check_states=required_check_states,
                stderr=stderr,
            )
        )
    else:
        checks.append(_check("github_pr_checks", "fail", failures or ["gh pr checks did not return a list"]))

    pr_view, failures, stderr = _run_json_command(
        "gh pr view",
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--repo",
            repo,
            "--json",
            "url,headRefOid,baseRefName,reviewDecision,mergeStateStatus,isDraft,state",
        ],
    )
    if isinstance(pr_view, dict):
        if pr_view.get("state") != "OPEN":
            failures.append(f"PR state is {pr_view.get('state')}, expected OPEN")
        if pr_view.get("baseRefName") != expected_base_ref:
            failures.append(
                f"PR baseRefName is {pr_view.get('baseRefName')}, expected {expected_base_ref}"
            )
        if pr_view.get("isDraft") is True:
            failures.append("PR is still draft")
        if pr_view.get("reviewDecision") == "CHANGES_REQUESTED":
            failures.append("PR has requested changes")
        head = _local_head()
        if head and pr_view.get("headRefOid") != head:
            failures.append("local HEAD does not match PR headRefOid")
        checks.append(
            _check(
                "github_pr_state",
                "fail" if failures else "pass",
                failures,
                pr=pr_view,
                local_head=head,
                stderr=stderr,
            )
        )
    else:
        checks.append(_check("github_pr_state", "fail", failures or ["gh pr view did not return an object"]))

    query = (
        "query($owner:String!, $repo:String!, $number:Int!){ "
        "repository(owner:$owner,name:$repo){ pullRequest(number:$number){ "
        "reviewThreads(first:100){ nodes{ isResolved } } } } }"
    )
    threads, failures, stderr = _run_json_command(
        "gh api graphql",
        [
            "gh",
            "api",
            "graphql",
            "-f",
            f"owner={owner}",
            "-f",
            f"repo={repo_name}",
            "-F",
            f"number={pr_number}",
            "-f",
            f"query={query}",
        ],
    )
    unresolved = None
    if isinstance(threads, dict):
        nodes = (
            threads.get("data", {})
            .get("repository", {})
            .get("pullRequest", {})
            .get("reviewThreads", {})
            .get("nodes", [])
        )
        if isinstance(nodes, list):
            unresolved = sum(1 for item in nodes if isinstance(item, dict) and not item.get("isResolved"))
            if unresolved:
                failures.append(f"unresolved review threads: {unresolved}")
        else:
            failures.append("reviewThreads.nodes did not return a list")
    else:
        failures.append("gh api graphql did not return an object")
    checks.append(
        _check(
            "github_review_threads",
            "fail" if failures else "pass",
            failures,
            unresolved=unresolved,
            stderr=stderr,
        )
    )
    return checks


def _strict_blocker_lines(item: dict[str, Any]) -> list[str]:
    name = item["name"]
    failures = _string_list(item.get("failures"))
    lines = [f"{name}: {failure}" for failure in failures] or [f"{name} failed"]
    global_failures = _string_list(item.get("global_failures"))
    if global_failures:
        lines.append(f"{name} global failures: {'; '.join(_limited(global_failures))}")
    failed_claims = _string_list(item.get("failed_claims"))
    if failed_claims:
        lines.append(f"{name} failed claims: {', '.join(failed_claims)}")
    for remediation in _string_list(item.get("remediation")):
        lines.append(f"{name} remediation: {remediation}")
    return lines


def _strict_blocker_detail(item: dict[str, Any]) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "check": item["name"],
        "failures": _string_list(item.get("failures")),
    }
    for key in (
        "artifact",
        "summary",
        "outcome_matrix_summary",
        "failed_outcomes",
        "global_failures",
        "failed_claims",
        "failure_details",
        "remediation",
        "branch",
        "base_ref",
        "base_oid",
        "upstream",
        "upstream_behind",
        "upstream_ahead",
        "dirty_count",
        "dirty_entries",
    ):
        value = item.get(key)
        if value not in (None, [], {}):
            detail[key] = value
    return detail


def _build_report(args: argparse.Namespace, output_dir: Path) -> dict[str, Any]:
    checks = [_run_local_probe(output_dir)]
    if args.require_real_agents_cli or args.require_real_agents_cli_auth:
        checks.append(_run_real_agents_cli_gate(args.require_real_agents_cli_auth))
    if args.skip_track2_evidence:
        checks.append(
            _check(
                "track2_evidence_gate",
                "skip",
                [],
                reason="--skip-track2-evidence was provided; do not use this for final recording readiness",
            )
        )
    else:
        checks.append(_run_track2_gate(Path(args.evidence_dir).resolve(), output_dir))

    if args.pr_number is not None:
        if args.skip_local_git_state:
            checks.append(
                _check(
                    "local_git_state",
                    "skip",
                    [],
                    reason="--skip-local-git-state was provided; do not use this for final recording readiness",
                    base_ref=args.base_ref,
                )
            )
        else:
            checks.append(_run_local_git_gate(args.base_ref))
        try:
            checks.extend(_run_pr_gate(args.pr_number, args.repo, args.pr_base_ref))
        except ValueError as exc:
            checks.append(_check("github_pr_state", "fail", [str(exc)]))

    passed = all(item["status"] in {"pass", "skip"} for item in checks)
    strict_blockers: list[str] = []
    strict_blocker_details: list[dict[str, Any]] = []
    if args.skip_track2_evidence:
        strict_blockers.append("Track 2 evidence validation was skipped")
    if args.skip_local_git_state and args.pr_number is not None:
        strict_blockers.append("Local git state validation was skipped")
    if not args.require_real_agents_cli_auth:
        strict_blockers.append("real agents-cli authentication was not required")
    for item in checks:
        if item["status"] == "fail":
            strict_blockers.extend(_strict_blocker_lines(item))
            strict_blocker_details.append(_strict_blocker_detail(item))
    return {
        "gate": "google_agents_cli_pre_recording",
        "passed": passed,
        "strict_final_recording_ready": passed and not strict_blockers,
        "strict_final_blockers": strict_blockers,
        "strict_final_blocker_details": strict_blocker_details,
        "repo": str(REPO_ROOT),
        "checks": checks,
        "summary": {
            "total": len(checks),
            "passed": sum(1 for item in checks if item["status"] == "pass"),
            "failed": sum(1 for item in checks if item["status"] == "fail"),
            "skipped": sum(1 for item in checks if item["status"] == "skip"),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence-dir",
        default=".demo/google-agents-cli-track2",
        help="Track 2 evidence directory containing manifest.json and artifacts",
    )
    parser.add_argument("--output", help="Write combined JSON report to this path")
    parser.add_argument(
        "--output-dir",
        help="Directory for child gate JSON artifacts; defaults to <output-stem>-artifacts when --output is set",
    )
    parser.add_argument(
        "--skip-track2-evidence",
        action="store_true",
        help="Skip live Track 2 evidence validation; only for code-only smoke checks",
    )
    parser.add_argument(
        "--require-real-agents-cli",
        action="store_true",
        help="Verify the installed agents-cli command surface with non-mutating commands",
    )
    parser.add_argument(
        "--require-real-agents-cli-auth",
        action="store_true",
        help="Also require agents-cli login --status to report an authenticated session",
    )
    parser.add_argument("--pr-number", type=int, help="Optional PR number to verify with gh")
    parser.add_argument("--repo", default="KumihoIO/construct-os", help="GitHub repo for PR checks")
    parser.add_argument(
        "--pr-base-ref",
        default="main",
        help="Expected GitHub PR base branch when --pr-number is set",
    )
    parser.add_argument(
        "--base-ref",
        default="origin/main",
        help="Local base ref that PR-backed demo branches must contain",
    )
    parser.add_argument(
        "--skip-local-git-state",
        action="store_true",
        help="Skip local clean/base/upstream validation for PR-backed demos; only for smoke checks",
    )
    parser.add_argument(
        "--require-strict-final-ready",
        action="store_true",
        help="Exit nonzero unless strict_final_recording_ready is true; use for final rehearsals",
    )
    args = parser.parse_args(argv)

    if args.output_dir:
        output_dir = Path(args.output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        report = _build_report(args, output_dir)
    elif args.output:
        output_path = Path(args.output).resolve()
        output_dir = output_path.with_name(f"{output_path.stem}-artifacts")
        output_dir.mkdir(parents=True, exist_ok=True)
        report = _build_report(args, output_dir)
    else:
        with tempfile.TemporaryDirectory(prefix="google-agents-cli-demo-gate-") as temp:
            output_dir = Path(temp)
            report = _build_report(args, output_dir)

    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text + "\n", encoding="utf-8")
    print(text)
    if args.require_strict_final_ready and not report["strict_final_recording_ready"]:
        return 1
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
