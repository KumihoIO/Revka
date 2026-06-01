import json
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _script() -> Path:
    return _repo_root() / "scripts" / "demo" / "google_agents_cli_track2_evidence_gate.py"


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
        "eval/baseline.json": '{"eval_success_rate": 0.42, "scenario": "heat wave"}',
        "eval/optimized.json": '{"eval_success_rate": 0.86, "scenario": "heat wave"}',
        "simulation/run-output.json": (
            '{"scenario_count": 3, "edge_cases": ["heat wave plus peak pricing"]}'
        ),
        "observability/trace.jsonl": '{"trace_id": "trace-heat-wave-001", "tool_calls": 4}',
        "optimizer/result.json": '{"measured_delta": 0.44, "changed": true}',
        "deploy/deploy-output.txt": (
            "deployed agent runtime for project demo-project in us-central1 at "
            "projects/demo/locations/us-central1/agents/facility-energy"
        ),
        "deploy/rollback-plan.md": "Rollback by redeploying the previous Agent Runtime revision.",
        "business/use-case.md": (
            "Commercial property operations manager handles the Peak-demand incident response "
            "workflow by combining occupancy, weather, and grid price inputs. The agent can "
            "adjust setpoints and notify facilities team while preserving comfort and reducing "
            "peak cost risk."
        ),
    }
    for rel, text in files.items():
        _write(evidence_dir / rel, text)
    _write(evidence_dir / "optimizer/original-instructions.md", "Prioritize lowest cost.")
    _write(
        evidence_dir / "optimizer/optimized-instructions.md",
        "Prioritize comfort first, then cap peak-demand cost.",
    )


def test_track2_evidence_gate_passes_complete_bundle(tmp_path):
    evidence_dir = tmp_path / "evidence"
    manifest = _complete_manifest()
    _write_complete_evidence(evidence_dir)
    (evidence_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(_script()), "--evidence-dir", str(evidence_dir)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    report = json.loads(result.stdout)
    assert report["passed"] is True
    assert report["summary"] == {"failed": 0, "passed": 6, "total": 6}


def test_track2_evidence_gate_fails_missing_required_artifact(tmp_path):
    evidence_dir = tmp_path / "evidence"
    manifest = _complete_manifest()
    _write_complete_evidence(evidence_dir)
    (evidence_dir / "eval" / "optimized.json").unlink()
    (evidence_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(_script()), "--evidence-dir", str(evidence_dir)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    report = json.loads(result.stdout)
    optimization = next(
        item for item in report["checks"] if item["claim"] == "optimization_improvement"
    )
    assert optimization["status"] == "fail"
    assert "missing evidence file: eval/optimized.json" in optimization["failures"]


def test_track2_evidence_gate_rejects_placeholder_artifact(tmp_path):
    evidence_dir = tmp_path / "evidence"
    manifest = _complete_manifest()
    _write_complete_evidence(evidence_dir)
    (evidence_dir / "business" / "use-case.md").write_text("TODO: replace me", encoding="utf-8")
    (evidence_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(_script()), "--evidence-dir", str(evidence_dir)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    report = json.loads(result.stdout)
    b2b = next(item for item in report["checks"] if item["claim"] == "b2b_value")
    assert b2b["status"] == "fail"
    assert "placeholder evidence file: business/use-case.md" in b2b["failures"]


def test_track2_evidence_gate_rejects_invalid_json_artifact(tmp_path):
    evidence_dir = tmp_path / "evidence"
    manifest = _complete_manifest()
    _write_complete_evidence(evidence_dir)
    (evidence_dir / "eval" / "baseline.json").write_text("not json", encoding="utf-8")
    (evidence_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(_script()), "--evidence-dir", str(evidence_dir)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    report = json.loads(result.stdout)
    optimization = next(
        item for item in report["checks"] if item["claim"] == "optimization_improvement"
    )
    assert optimization["status"] == "fail"
    assert any(
        item.startswith("invalid JSON evidence file eval/baseline.json")
        for item in optimization["failures"]
    )


def test_track2_evidence_gate_rejects_metric_mismatch(tmp_path):
    evidence_dir = tmp_path / "evidence"
    manifest = _complete_manifest()
    _write_complete_evidence(evidence_dir)
    (evidence_dir / "eval" / "optimized.json").write_text(
        '{"eval_success_rate": 0.43, "scenario": "heat wave"}',
        encoding="utf-8",
    )
    (evidence_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(_script()), "--evidence-dir", str(evidence_dir)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    report = json.loads(result.stdout)
    optimization = next(
        item for item in report["checks"] if item["claim"] == "optimization_improvement"
    )
    assert "eval/optimized.json metric 'eval_success_rate' does not match after" in optimization["failures"]


def test_track2_evidence_gate_rejects_b2b_stub(tmp_path):
    evidence_dir = tmp_path / "evidence"
    manifest = _complete_manifest()
    _write_complete_evidence(evidence_dir)
    (evidence_dir / "business" / "use-case.md").write_text(
        "Commercial property operations manager adjusts setpoints.",
        encoding="utf-8",
    )
    (evidence_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(_script()), "--evidence-dir", str(evidence_dir)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    report = json.loads(result.stdout)
    b2b = next(item for item in report["checks"] if item["claim"] == "b2b_value")
    assert "b2b evidence must be a concrete narrative, not a one-line stub" in b2b["failures"]


def test_track2_evidence_gate_rejects_wrong_schema_version(tmp_path):
    evidence_dir = tmp_path / "evidence"
    manifest = _complete_manifest()
    manifest["schema_version"] = 2
    _write_complete_evidence(evidence_dir)
    (evidence_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(_script()), "--evidence-dir", str(evidence_dir)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert "schema_version must be 1" in report["global_failures"]


def test_track2_evidence_gate_emits_json_when_manifest_is_missing(tmp_path):
    evidence_dir = tmp_path / "evidence"

    result = subprocess.run(
        [sys.executable, str(_script()), "--evidence-dir", str(evidence_dir)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["passed"] is False
    assert report["summary"]["failed"] == 1
    assert any("manifest not found" in item for item in report["global_failures"])


def test_track2_evidence_gate_rejects_paths_outside_evidence_dir(tmp_path):
    evidence_dir = tmp_path / "evidence"
    manifest = _complete_manifest()
    _write_complete_evidence(evidence_dir)
    manifest["claims"]["agent_optimizer"][
        "original_instructions_file"
    ] = "../outside-original.md"
    (tmp_path / "outside-original.md").write_text("outside", encoding="utf-8")
    (evidence_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(_script()), "--evidence-dir", str(evidence_dir)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    report = json.loads(result.stdout)
    optimizer = next(item for item in report["checks"] if item["claim"] == "agent_optimizer")
    assert optimizer["status"] == "fail"
    assert (
        "evidence file must stay inside evidence dir: ../outside-original.md"
        in optimizer["failures"]
    )


def test_track2_evidence_gate_writes_template(tmp_path):
    evidence_dir = tmp_path / "evidence"

    result = subprocess.run(
        [
            sys.executable,
            str(_script()),
            "--evidence-dir",
            str(evidence_dir),
            "--write-template",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    manifest = json.loads((evidence_dir / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["claims"]) >= {
        "optimization_improvement",
        "agent_simulation",
        "agent_observability",
        "agent_optimizer",
        "live_google_cloud_deployment",
        "b2b_value",
    }
