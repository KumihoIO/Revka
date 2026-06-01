import json
import os
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _script() -> Path:
    return _repo_root() / "scripts" / "demo" / "google_agents_cli_pre_recording_gate.py"


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
        "eval/baseline.json": '{"score": 0.42, "scenario": "heat wave"}',
        "eval/optimized.json": '{"score": 0.86, "scenario": "heat wave"}',
        "simulation/run-output.json": '{"scenario_count": 3, "edge_cases": ["heat wave"]}',
        "observability/trace.jsonl": '{"trace_id": "trace-heat-wave-001", "tool_calls": 4}',
        "optimizer/result.json": '{"measured_delta": 0.44, "changed": true}',
        "deploy/deploy-output.txt": "deployed agent runtime projects/demo/locations/us-central1",
        "deploy/rollback-plan.md": "Rollback by redeploying the previous Agent Runtime revision.",
        "business/use-case.md": "Commercial property operator avoids peak pricing conflict.",
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
    assert report["summary"]["failed"] == 0
    statuses = {item["name"]: item["status"] for item in report["checks"]}
    assert statuses["local_code_probe"] == "pass"
    assert statuses["track2_evidence_gate"] == "pass"


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
    print(json.dumps([{"name": "CI", "state": "SUCCESS", "workflow": "CI"}]))
elif args[:2] == ["pr", "view"]:
    print(json.dumps({
        "url": "https://github.com/KumihoIO/construct-os/pull/324",
        "headRefOid": os.environ["FAKE_HEAD"],
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
    statuses = {item["name"]: item["status"] for item in report["checks"]}
    assert statuses["local_code_probe"] == "pass"
    assert statuses["track2_evidence_gate"] == "skip"
    assert statuses["github_pr_checks"] == "pass"
    assert statuses["github_pr_state"] == "pass"
    assert statuses["github_review_threads"] == "pass"
    local_probe = next(item for item in report["checks"] if item["name"] == "local_code_probe")
    assert Path(local_probe["artifact"]).is_file()
