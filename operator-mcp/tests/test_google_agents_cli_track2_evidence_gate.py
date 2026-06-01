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
    assert report["summary"] == {"failed": 0, "passed": 8, "total": 8}


def test_track2_evidence_gate_rejects_missing_existing_agent_failure(tmp_path):
    evidence_dir = tmp_path / "evidence"
    manifest = _complete_manifest()
    _write_complete_evidence(evidence_dir)
    (evidence_dir / "baseline" / "edge-case.json").write_text(
        '{"agent_name": "Facility Energy Agent", '
        '"scenario": "heat wave plus peak pricing", "passed": true}',
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
    baseline = next(
        item for item in report["checks"] if item["claim"] == "existing_agent_baseline"
    )
    assert baseline["status"] == "fail"
    assert any("pre-optimization edge case" in item for item in baseline["failures"])


def test_track2_evidence_gate_rejects_scenario_evidence_mismatch(tmp_path):
    evidence_dir = tmp_path / "evidence"
    manifest = _complete_manifest()
    manifest["scenario"]["business_workflow"] = "Different procurement workflow"
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
    assert any(
        "scenario.business_workflow" in item for item in report["global_failures"]
    )


def test_track2_evidence_gate_rejects_optimizer_without_invocation_proof(tmp_path):
    evidence_dir = tmp_path / "evidence"
    manifest = _complete_manifest()
    _write_complete_evidence(evidence_dir)
    (evidence_dir / "optimizer" / "result.json").write_text(
        '{"measured_delta": 0.44, "changed": true}',
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
    optimizer = next(item for item in report["checks"] if item["claim"] == "agent_optimizer")
    assert optimizer["status"] == "fail"
    assert any("optimizer evidence must mention" in item for item in optimizer["failures"])


def test_track2_evidence_gate_rejects_generic_simulation_proof(tmp_path):
    evidence_dir = tmp_path / "evidence"
    manifest = _complete_manifest()
    _write_complete_evidence(evidence_dir)
    (evidence_dir / "simulation" / "run-output.json").write_text(
        '{"scenario_count": 3, "generator": "generic synthetic scenario run", '
        '"edge_cases": ["heat wave plus peak pricing"]}',
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
    simulation = next(item for item in report["checks"] if item["claim"] == "agent_simulation")
    assert simulation["status"] == "fail"
    assert "simulation evidence must mention Agent Simulation" in simulation["failures"]


def test_track2_evidence_gate_rejects_generic_observability_trace(tmp_path):
    evidence_dir = tmp_path / "evidence"
    manifest = _complete_manifest()
    _write_complete_evidence(evidence_dir)
    (evidence_dir / "observability" / "trace.jsonl").write_text(
        '{"trace_id": "trace-heat-wave-001", "tool_calls": 4, '
        '"reasoning": "resolved comfort and cost conflict"}',
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
    observability = next(
        item for item in report["checks"] if item["claim"] == "agent_observability"
    )
    assert observability["status"] == "fail"
    assert "observability evidence must mention Agent Observability" in observability["failures"]


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


def test_track2_evidence_gate_rejects_large_padded_placeholder_artifact(tmp_path):
    evidence_dir = tmp_path / "evidence"
    manifest = _complete_manifest()
    _write_complete_evidence(evidence_dir)
    (evidence_dir / "business" / "use-case.md").write_text(
        ("TODO replace me\n" * 500),
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


def test_track2_evidence_gate_rejects_missing_platform_proof(tmp_path):
    evidence_dir = tmp_path / "evidence"
    manifest = _complete_manifest()
    _write_complete_evidence(evidence_dir)
    (evidence_dir / "platform" / "architecture.md").write_text(
        "The project uses a generic cloud runtime and a model.",
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
    platform = next(
        item for item in report["checks"] if item["claim"] == "mandatory_google_platform"
    )
    assert "platform evidence must mention Google Cloud" in platform["failures"]
    assert any("Gemini" in item for item in platform["failures"])


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


def test_track2_evidence_gate_rejects_symlink_outside_evidence_dir(tmp_path):
    evidence_dir = tmp_path / "evidence"
    manifest = _complete_manifest()
    _write_complete_evidence(evidence_dir)
    outside = tmp_path / "outside-optimized.json"
    outside.write_text('{"eval_success_rate": 0.86}', encoding="utf-8")
    (evidence_dir / "eval" / "optimized.json").unlink()
    (evidence_dir / "eval" / "optimized.json").symlink_to(outside)
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
    assert (
        "evidence file must stay inside evidence dir: eval/optimized.json"
        in optimization["failures"]
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
        "existing_agent_baseline",
        "optimization_improvement",
        "agent_simulation",
        "agent_observability",
        "agent_optimizer",
        "live_google_cloud_deployment",
        "mandatory_google_platform",
        "b2b_value",
    }


def test_track2_evidence_gate_writes_capture_plan(tmp_path):
    evidence_dir = tmp_path / "evidence"

    result = subprocess.run(
        [
            sys.executable,
            str(_script()),
            "--evidence-dir",
            str(evidence_dir),
            "--write-capture-plan",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    plan = (evidence_dir / "capture-plan.md").read_text(encoding="utf-8")
    assert "strict_final_recording_ready: true" in plan
    assert "existing_agent_baseline" in plan
    assert "baseline/edge-case.json" in plan
    assert "mandatory_google_platform" in plan
    assert "platform/architecture.md" in plan
    assert "agents-cli login -i" in plan
    assert "--require-strict-final-ready" in plan
