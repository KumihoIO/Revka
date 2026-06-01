import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _script() -> Path:
    return _repo_root() / "scripts" / "demo" / "google_agents_cli_pre_recording_gate.py"


def _load_gate_module():
    spec = importlib.util.spec_from_file_location(
        "google_agents_cli_pre_recording_gate_for_test",
        _script(),
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, text: str = "captured demo artifact") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _complete_manifest() -> dict:
    return {
        "schema_version": 1,
        "scenario": {
            "name": "Facility Energy Optimization",
            "b2b_persona": "Commercial property operations manager",
            "business_workflow": "Balance occupant comfort against peak energy pricing",
            "measurable_outcome": "Eval success rate improves from 0.42 to 0.86",
        },
        "claims": {
            "existing_agent_baseline": {
                "agent_name": "Facility Energy Agent",
                "normal_case": "normal occupancy and pricing day",
                "edge_case": "heat wave plus peak pricing",
                "normal_case_evidence": "baseline/normal-case.json",
                "edge_case_evidence": "baseline/edge-case.json",
                "evidence_files": ["baseline/existing-agent.md"],
            },
            "optimization_improvement": {
                "metric_name": "eval_success_rate",
                "before": 0.42,
                "after": 0.86,
                "higher_is_better": True,
                "evidence_files": ["eval/baseline.json", "eval/optimized.json"],
            },
            "agent_simulation": {
                "scenario_count": 3,
                "edge_cases": ["heat wave plus peak pricing"],
                "evidence_files": ["simulation/run-output.json"],
            },
            "agent_observability": {
                "trace_ids": ["trace-heat-wave-001"],
                "evidence_files": ["observability/trace.jsonl"],
            },
            "agent_optimizer": {
                "original_instructions_file": "optimizer/original-instructions.md",
                "optimized_instructions_file": "optimizer/optimized-instructions.md",
                "measured_delta": 0.44,
                "evidence_files": ["optimizer/result.json"],
            },
            "live_google_cloud_deployment": {
                "project_id": "demo-project",
                "region": "us-central1",
                "resource": "projects/demo/locations/us-central1/agents/facility-energy",
                "rollback_plan_file": "deploy/rollback-plan.md",
                "evidence_files": ["deploy/deploy-output.txt"],
            },
            "mandatory_google_platform": {
                "intelligence": "Gemini API",
                "orchestration": "Agent Development Kit",
                "infrastructure": "Google Cloud Agent Runtime",
                "evidence_files": ["platform/architecture.md"],
            },
            "b2b_value": {
                "persona": "Commercial property operations manager",
                "workflow": "Peak-demand incident response",
                "inputs": ["occupancy", "weather", "grid price"],
                "actions": ["adjust setpoints", "notify facilities team"],
                "measurable_outcome": "Comfort maintained while reducing peak cost risk",
                "evidence_files": ["business/use-case.md"],
            },
        },
    }


def _write_complete_evidence(evidence_dir: Path) -> None:
    files = {
        "baseline/existing-agent.md": (
            "Facility Energy Optimization uses Facility Energy Agent as the existing "
            "sandbox agent. It already handles the normal occupancy and pricing day "
            "workflow, but before optimization it fails the heat wave plus peak pricing "
            "edge case."
        ),
        "baseline/normal-case.json": (
            '{"agent_name": "Facility Energy Agent", '
            '"scenario": "normal occupancy and pricing day", "passed": true}'
        ),
        "baseline/edge-case.json": (
            '{"agent_name": "Facility Energy Agent", '
            '"scenario": "heat wave plus peak pricing", "passed": false, "failed": true}'
        ),
        "eval/baseline.json": '{"eval_success_rate": 0.42, "scenario": "heat wave"}',
        "eval/optimized.json": '{"eval_success_rate": 0.86, "scenario": "heat wave"}',
        "simulation/run-output.json": (
            '{"scenario_count": 3, "generator": "Agent Simulation synthetic scenario run", '
            '"edge_cases": ["heat wave plus peak pricing"]}'
        ),
        "observability/trace.jsonl": (
            '{"trace_id": "trace-heat-wave-001", "source": "Agent Observability", '
            '"tool_calls": 4, '
            '"reasoning": "resolved comfort and cost conflict"}'
        ),
        "optimizer/result.json": (
            '{"measured_delta": 0.44, "changed": true, '
            '"command": ["agents-cli", "eval", "optimize"]}'
        ),
        "deploy/deploy-output.txt": (
            "deployed agent runtime for project demo-project in us-central1 at "
            "projects/demo/locations/us-central1/agents/facility-energy"
        ),
        "deploy/rollback-plan.md": "Rollback by redeploying the previous Agent Runtime revision.",
        "platform/architecture.md": (
            "The demo agent uses Gemini API intelligence, Agent Development Kit (ADK) "
            "orchestration, and Google Cloud Agent Runtime infrastructure for the "
            "Track 2 optimization workflow."
        ),
        "business/use-case.md": (
            "Facility Energy Optimization helps the Commercial property operations manager "
            "Balance occupant comfort against peak energy pricing. In the Peak-demand incident "
            "response workflow, the agent combines occupancy, weather, and grid price inputs, "
            "then can adjust setpoints and notify facilities team. Eval success rate improves "
            "from 0.42 to 0.86, and the B2B outcome is Comfort maintained while reducing peak "
            "cost risk."
        ),
    }
    for rel, text in files.items():
        _write(evidence_dir / rel, text)
    _write(evidence_dir / "optimizer/original-instructions.md", "Prioritize lowest cost.")
    _write(
        evidence_dir / "optimizer/optimized-instructions.md",
        "Prioritize comfort first, then cap peak-demand cost.",
    )
    (evidence_dir / "manifest.json").write_text(
        json.dumps(_complete_manifest()),
        encoding="utf-8",
    )


def test_local_git_gate_reports_dirty_stale_branch(monkeypatch):
    module = _load_gate_module()

    def fake_run(cmd):
        if cmd == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return subprocess.CompletedProcess(cmd, 0, "feature/google-agents-demo\n", "")
        if cmd == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                " M scripts/demo/google_agents_cli_pre_recording_gate.py\n?? scratch.json\n",
                "",
            )
        if cmd == ["git", "rev-parse", "--verify", "origin/main"]:
            return subprocess.CompletedProcess(cmd, 0, "abc123\n", "")
        if cmd == ["git", "merge-base", "--is-ancestor", "origin/main", "HEAD"]:
            return subprocess.CompletedProcess(cmd, 1, "", "")
        if cmd == [
            "git",
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{u}",
        ]:
            return subprocess.CompletedProcess(cmd, 0, "origin/feature/google-agents-demo\n", "")
        if cmd == [
            "git",
            "rev-list",
            "--left-right",
            "--count",
            "origin/feature/google-agents-demo...HEAD",
        ]:
            return subprocess.CompletedProcess(cmd, 0, "2\t1\n", "")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(module, "_run", fake_run)

    check = module._run_local_git_gate("origin/main")

    assert check["status"] == "fail"
    assert check["branch"] == "feature/google-agents-demo"
    assert check["base_ref"] == "origin/main"
    assert check["base_oid"] == "abc123"
    assert check["upstream"] == "origin/feature/google-agents-demo"
    assert check["upstream_behind"] == 2
    assert check["upstream_ahead"] == 1
    assert check["dirty_count"] == 2
    assert any("uncommitted changes" in item for item in check["failures"])
    assert any("HEAD does not contain base ref origin/main" in item for item in check["failures"])
    assert any("behind upstream" in item for item in check["failures"])
    assert any("ahead upstream" in item for item in check["failures"])


def test_login_status_auth_parser_rejects_negative_variants():
    module = _load_gate_module()

    negative_outputs = [
        "Authentication\n  Not authenticated",
        "Authentication\nUnauthenticated",
        "Authentication\nauthenticated=false",
        "Authentication\nauthentication=false",
        "Authentication\nlogged_in=false",
        "Authentication\nlogged in=false",
        "Authentication\nunknown",
    ]

    for output in negative_outputs:
        assert module._login_status_authenticated(output, "") is False, output


def test_login_status_auth_parser_accepts_positive_variants():
    module = _load_gate_module()

    positive_outputs = [
        "Authentication\nAuthenticated as demo@example.com",
        "Authentication\nauthenticated=true",
        "Authentication\nauthentication=true",
        "Authentication\nlogged_in=true",
        "Authentication\nlogged in=true",
    ]

    for output in positive_outputs:
        assert module._login_status_authenticated(output, "") is True, output


def test_pre_recording_gate_passes_with_complete_track2_bundle(tmp_path):
    evidence_dir = tmp_path / "evidence"
    output = tmp_path / "report.json"
    output_dir = tmp_path / "artifacts"
    _write_complete_evidence(evidence_dir)

    result = subprocess.run(
        [
            sys.executable,
            str(_script()),
            "--evidence-dir",
            str(evidence_dir),
            "--output",
            str(output),
            "--output-dir",
            str(output_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["strict_final_recording_ready"] is False
    assert "real agents-cli authentication was not required" in report["strict_final_blockers"]
    assert report["summary"]["failed"] == 0
    statuses = {item["name"]: item["status"] for item in report["checks"]}
    assert statuses["local_code_probe"] == "pass"
    assert statuses["track2_evidence_gate"] == "pass"
    local_probe = next(item for item in report["checks"] if item["name"] == "local_code_probe")
    assert local_probe["outcome_matrix_summary"] == {
        "failed": 0,
        "passed": 16,
        "total": 16,
    }


def test_pre_recording_gate_final_mode_fails_when_auth_is_not_required(tmp_path):
    evidence_dir = tmp_path / "evidence"
    output = tmp_path / "report.json"
    _write_complete_evidence(evidence_dir)

    result = subprocess.run(
        [
            sys.executable,
            str(_script()),
            "--evidence-dir",
            str(evidence_dir),
            "--require-strict-final-ready",
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["strict_final_recording_ready"] is False
    assert "real agents-cli authentication was not required" in report["strict_final_blockers"]


def test_pre_recording_gate_fails_when_track2_evidence_is_missing(tmp_path):
    output = tmp_path / "report.json"

    result = subprocess.run(
        [
            sys.executable,
            str(_script()),
            "--evidence-dir",
            str(tmp_path / "missing-evidence"),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    track2 = next(item for item in report["checks"] if item["name"] == "track2_evidence_gate")
    assert track2["status"] == "fail"
    assert any("Track 2 evidence gate exited 1" in item for item in track2["failures"])
    assert any("manifest not found" in item for item in track2["global_failures"])
    assert any("track2_evidence_gate global failures:" in item for item in report["strict_final_blockers"])


def test_pre_recording_gate_reports_track2_failed_claim_details(tmp_path):
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    (evidence_dir / "manifest.json").write_text(
        json.dumps(_complete_manifest()),
        encoding="utf-8",
    )
    output = tmp_path / "report.json"

    result = subprocess.run(
        [
            sys.executable,
            str(_script()),
            "--evidence-dir",
            str(evidence_dir),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    track2 = next(item for item in report["checks"] if item["name"] == "track2_evidence_gate")
    assert track2["status"] == "fail"
    assert "existing_agent_baseline" in track2["failed_claims"]
    assert "optimization_improvement" in track2["failed_claims"]
    assert any(item["claim"] == "agent_optimizer" for item in track2["failure_details"])
    assert any(
        "track2_evidence_gate failed claims:" in item
        and "existing_agent_baseline" in item
        and "optimization_improvement" in item
        for item in report["strict_final_blockers"]
    )
    detail = next(
        item
        for item in report["strict_final_blocker_details"]
        if item["check"] == "track2_evidence_gate"
    )
    assert "failed_claims" in detail
    assert "failure_details" in detail


def test_pre_recording_gate_can_verify_pr_state_with_fake_gh(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
if args[:2] == ["pr", "checks"]:
    print(json.dumps([
        {"name": "CI Required Gate", "state": "SUCCESS", "workflow": "CI"},
        {"name": "Security Required Gate", "state": "SUCCESS", "workflow": "CI"},
    ]))
elif args[:2] == ["pr", "view"]:
    print(json.dumps({
        "url": "https://github.com/KumihoIO/construct-os/pull/324",
        "headRefOid": os.environ["FAKE_HEAD"],
        "baseRefName": "main",
        "reviewDecision": "REVIEW_REQUIRED",
        "mergeStateStatus": "BLOCKED",
        "isDraft": False,
        "state": "OPEN",
    }))
elif args[:2] == ["api", "graphql"]:
    print(json.dumps({
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {"nodes": []}
                }
            }
        }
    }))
else:
    print("unexpected gh invocation: " + " ".join(args), file=sys.stderr)
    sys.exit(2)
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=_repo_root(),
        text=True,
    ).strip()
    env = {
        **os.environ,
        "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
        "FAKE_HEAD": head,
    }
    output = tmp_path / "report.json"

    result = subprocess.run(
        [
            sys.executable,
            str(_script()),
            "--skip-track2-evidence",
            "--pr-number",
            "324",
            "--skip-local-git-state",
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["strict_final_recording_ready"] is False
    assert "Track 2 evidence validation was skipped" in report["strict_final_blockers"]
    assert "Local git state validation was skipped" in report["strict_final_blockers"]
    statuses = {item["name"]: item["status"] for item in report["checks"]}
    assert statuses["local_code_probe"] == "pass"
    assert statuses["track2_evidence_gate"] == "skip"
    assert statuses["local_git_state"] == "skip"
    assert statuses["github_pr_checks"] == "pass"
    assert statuses["github_pr_state"] == "pass"
    assert statuses["github_review_threads"] == "pass"
    local_probe = next(item for item in report["checks"] if item["name"] == "local_code_probe")
    assert Path(local_probe["artifact"]).is_file()


def test_pre_recording_gate_rejects_pr_checks_missing_required_gates(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
if args[:2] == ["pr", "checks"]:
    print(json.dumps([{"name": "CI", "state": "SUCCESS", "workflow": "CI"}]))
elif args[:2] == ["pr", "view"]:
    print(json.dumps({
        "url": "https://github.com/KumihoIO/construct-os/pull/324",
        "headRefOid": os.environ["FAKE_HEAD"],
        "baseRefName": "main",
        "reviewDecision": "REVIEW_REQUIRED",
        "mergeStateStatus": "BLOCKED",
        "isDraft": False,
        "state": "OPEN",
    }))
elif args[:2] == ["api", "graphql"]:
    print(json.dumps({
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {"nodes": []}
                }
            }
        }
    }))
else:
    print("unexpected gh invocation: " + " ".join(args), file=sys.stderr)
    sys.exit(2)
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=_repo_root(),
        text=True,
    ).strip()
    env = {
        **os.environ,
        "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
        "FAKE_HEAD": head,
    }
    output = tmp_path / "report.json"

    result = subprocess.run(
        [
            sys.executable,
            str(_script()),
            "--skip-track2-evidence",
            "--pr-number",
            "324",
            "--skip-local-git-state",
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    pr_checks = next(item for item in report["checks"] if item["name"] == "github_pr_checks")
    assert pr_checks["status"] == "fail"
    assert "required GitHub check is missing: CI Required Gate" in pr_checks["failures"]
    assert "required GitHub check is missing: Security Required Gate" in pr_checks["failures"]


def test_pre_recording_gate_rejects_pr_with_wrong_base_branch(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
if args[:2] == ["pr", "checks"]:
    print(json.dumps([
        {"name": "CI Required Gate", "state": "SUCCESS", "workflow": "CI"},
        {"name": "Security Required Gate", "state": "SUCCESS", "workflow": "CI"},
    ]))
elif args[:2] == ["pr", "view"]:
    print(json.dumps({
        "url": "https://github.com/KumihoIO/construct-os/pull/324",
        "headRefOid": os.environ["FAKE_HEAD"],
        "baseRefName": "release-demo",
        "reviewDecision": "REVIEW_REQUIRED",
        "mergeStateStatus": "BLOCKED",
        "isDraft": False,
        "state": "OPEN",
    }))
elif args[:2] == ["api", "graphql"]:
    print(json.dumps({
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {"nodes": []}
                }
            }
        }
    }))
else:
    print("unexpected gh invocation: " + " ".join(args), file=sys.stderr)
    sys.exit(2)
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=_repo_root(),
        text=True,
    ).strip()
    env = {
        **os.environ,
        "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
        "FAKE_HEAD": head,
    }
    output = tmp_path / "report.json"

    result = subprocess.run(
        [
            sys.executable,
            str(_script()),
            "--skip-track2-evidence",
            "--pr-number",
            "324",
            "--skip-local-git-state",
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    pr_state = next(item for item in report["checks"] if item["name"] == "github_pr_state")
    assert pr_state["status"] == "fail"
    assert "PR baseRefName is release-demo, expected main" in pr_state["failures"]


def test_pre_recording_gate_final_mode_fails_when_smoke_checks_are_skipped(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
if args[:2] == ["pr", "checks"]:
    print(json.dumps([
        {"name": "CI Required Gate", "state": "SUCCESS", "workflow": "CI"},
        {"name": "Security Required Gate", "state": "SUCCESS", "workflow": "CI"},
    ]))
elif args[:2] == ["pr", "view"]:
    print(json.dumps({
        "url": "https://github.com/KumihoIO/construct-os/pull/324",
        "headRefOid": os.environ["FAKE_HEAD"],
        "baseRefName": "main",
        "reviewDecision": "REVIEW_REQUIRED",
        "mergeStateStatus": "BLOCKED",
        "isDraft": False,
        "state": "OPEN",
    }))
elif args[:2] == ["api", "graphql"]:
    print(json.dumps({
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {"nodes": []}
                }
            }
        }
    }))
else:
    print("unexpected gh invocation: " + " ".join(args), file=sys.stderr)
    sys.exit(2)
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=_repo_root(),
        text=True,
    ).strip()
    env = {
        **os.environ,
        "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
        "FAKE_HEAD": head,
    }
    output = tmp_path / "report.json"

    result = subprocess.run(
        [
            sys.executable,
            str(_script()),
            "--skip-track2-evidence",
            "--pr-number",
            "324",
            "--skip-local-git-state",
            "--require-strict-final-ready",
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["strict_final_recording_ready"] is False
    assert "Track 2 evidence validation was skipped" in report["strict_final_blockers"]
    assert "Local git state validation was skipped" in report["strict_final_blockers"]


def _write_fake_agents_cli(bin_dir: Path, authenticated: bool = False) -> None:
    fake_agents_cli = bin_dir / "agents-cli"
    login_status = "Authenticated as demo@example.com" if authenticated else "Not authenticated"
    fake_agents_cli.write_text(
        f"""#!/usr/bin/env python3
import sys

args = sys.argv[1:]
if args == ["--help"]:
    print("Agents CLI - Agent Development Lifecycle toolchain")
    print("Commands: setup create scaffold install lint run eval deploy publish infra")
    print("Commands: data-ingestion playground update info login")
elif args == ["eval", "--help"]:
    print("Subcommands: run compare optimize")
elif args == ["eval", "optimize", "--help"]:
    print("Optimize agent prompts using the GEPA framework")
    print("This command runs adk optimize under the hood")
elif args == ["deploy", "--help"]:
    print("Deploy the agent to Agent Runtime, Cloud Run, or GKE")
    print("--dry-run --status")
elif args == ["publish", "--help"]:
    print("Commands: gemini-enterprise")
elif args == ["info"]:
    print("CLI version: 0.2.1")
elif args == ["login", "--status"]:
    print("Authentication")
    print("{login_status}")
else:
    print("unexpected agents-cli invocation: " + " ".join(args), file=sys.stderr)
    sys.exit(2)
""",
        encoding="utf-8",
    )
    fake_agents_cli.chmod(0o755)


def test_pre_recording_gate_can_require_real_agents_cli_surface(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_agents_cli(fake_bin)
    output = tmp_path / "report.json"
    env = {
        **os.environ,
        "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
    }

    result = subprocess.run(
        [
            sys.executable,
            str(_script()),
            "--skip-track2-evidence",
            "--require-real-agents-cli",
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    report = json.loads(output.read_text(encoding="utf-8"))
    real_cli = next(item for item in report["checks"] if item["name"] == "real_agents_cli")
    assert real_cli["status"] == "pass"
    assert real_cli["authenticated"] is False
    assert report["strict_final_recording_ready"] is False


def test_pre_recording_gate_can_require_real_agents_cli_auth(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_agents_cli(fake_bin, authenticated=False)
    output = tmp_path / "report.json"
    env = {
        **os.environ,
        "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
    }

    result = subprocess.run(
        [
            sys.executable,
            str(_script()),
            "--skip-track2-evidence",
            "--require-real-agents-cli-auth",
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    real_cli = next(item for item in report["checks"] if item["name"] == "real_agents_cli")
    assert real_cli["status"] == "fail"
    assert any("authenticated session" in item for item in real_cli["failures"])
    assert any(
        "agents-cli login --status did not report an authenticated session" in item
        for item in report["strict_final_blockers"]
    )
    assert any("agents-cli login -i" in item for item in report["strict_final_blockers"])
    detail = next(
        item
        for item in report["strict_final_blocker_details"]
        if item["check"] == "real_agents_cli"
    )
    assert detail["remediation"] == [
        "run agents-cli login -i outside Construct, then rerun the strict gate"
    ]


def test_pre_recording_gate_reports_strict_final_ready_with_auth_and_evidence(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_agents_cli(fake_bin, authenticated=True)
    evidence_dir = tmp_path / "evidence"
    _write_complete_evidence(evidence_dir)
    output = tmp_path / "report.json"
    env = {
        **os.environ,
        "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
    }

    result = subprocess.run(
        [
            sys.executable,
            str(_script()),
            "--evidence-dir",
            str(evidence_dir),
            "--require-real-agents-cli-auth",
            "--require-strict-final-ready",
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["strict_final_recording_ready"] is True
    assert report["strict_final_blockers"] == []
