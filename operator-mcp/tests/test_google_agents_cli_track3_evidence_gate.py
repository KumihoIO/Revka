import json
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _script() -> Path:
    return _repo_root() / "scripts" / "demo" / "google_agents_cli_track3_evidence_gate.py"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _manifest(service_url: str = "https://construct-agentops-a2a-abc-uc.a.run.app") -> dict:
    return {
        "schema_version": 1,
        "scenario": {
            "name": "Construct Enterprise AgentOps Control Plane",
            "b2b_persona": "Platform engineering leader",
            "business_workflow": "Governed production incident response",
            "measurable_outcome": "A2A incident plan returned from Cloud Run",
        },
        "claims": {
            "google_cloud_deployment": {
                "project_id": "demo-track3-project",
                "region": "us-central1",
                "service_name": "construct-agentops-a2a",
                "service_url": service_url,
                "evidence_files": [
                    "deploy/cloud-run-service.json",
                    "deploy/deploy-output.txt",
                ],
            },
            "a2a_interoperability": {
                "agent_card_url": f"{service_url}/.well-known/agent-card.json",
                "rpc_url": f"{service_url}/",
                "skill_id": "enterprise-agentops-incident-plan",
                "evidence_files": [
                    "a2a/agent-card.json",
                    "a2a/message-send-response.json",
                ],
            },
            "gemini_powered_intelligence": {
                "model_family": "Gemini",
                "runtime": "Vertex AI",
                "evidence_files": [
                    "runtime/healthz.json",
                    "a2a/message-send-response.json",
                ],
            },
            "adk_orchestration": {
                "framework": "Google ADK",
                "source_files": [
                    "examples/google-agents-track3/construct-agentops-a2a/agent.py",
                    "examples/google-agents-track3/construct-agentops-a2a/main.py",
                ],
                "evidence_files": ["runtime/source-manifest.json"],
            },
            "b2b_enterprise_package": {
                "package_name": "Construct Enterprise AgentOps Control Plane",
                "buyer": "Platform engineering or IT operations leader",
                "workflow": "Production incident response",
                "evidence_files": ["business/package.md"],
            },
            "enterprise_governance": {
                "identity": "Cloud Run service account or agent identity",
                "rollback": "Cloud Run revision rollback",
                "observability": "Cloud Logging",
                "evidence_files": ["governance/controls.md", "deploy/rollback-plan.md"],
            },
            "gemini_enterprise_readiness": {
                "status": "registration-ready",
                "requires_admin_access": True,
                "evidence_files": ["enterprise/gemini-enterprise-registration.md"],
            },
        },
    }


def _write_complete_evidence(evidence_dir: Path, service_url: str) -> None:
    _write(
        evidence_dir / "deploy/cloud-run-service.json",
        json.dumps(
            {
                "metadata": {
                    "name": "construct-agentops-a2a",
                    "labels": {"project": "demo-track3-project"},
                },
                "status": {"url": service_url},
                "spec": {"template": {"metadata": {"annotations": {"region": "us-central1"}}}},
            }
        ),
    )
    _write(
        evidence_dir / "deploy/deploy-output.txt",
        (
            "Cloud Run service construct-agentops-a2a deployed in project "
            "demo-track3-project region us-central1 at Cloud Run URL "
            f"{service_url}"
        ),
    )
    _write(
        evidence_dir / "a2a/agent-card.json",
        json.dumps(
            {
                "protocolVersion": "0.3",
                "name": "Construct Enterprise AgentOps Control Plane",
                "description": "B2B A2A agent",
                "url": service_url,
                "iconUrl": "data:image/svg+xml;base64,PHN2Zw==",
                "version": "1.0.0",
                "capabilities": {"streaming": False, "pushNotifications": False},
                "defaultInputModes": ["text/plain", "application/json"],
                "defaultOutputModes": ["text/plain", "application/json"],
                "skills": [{"id": "enterprise-agentops-incident-plan"}],
            }
        ),
    )
    _write(
        evidence_dir / "a2a/message-send-response.json",
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "demo",
                "result": {
                    "id": "task-demo",
                    "status": {"state": "completed"},
                    "artifacts": [
                        {
                            "parts": [
                                {
                                    "type": "text",
                                    "text": (
                                        "Gemini Vertex AI incident plan: business impact is high. "
                                        "Coordinate specialized agents via A2A, inspect Google Cloud "
                                        "evidence and Cloud Logging, enforce approval boundary, "
                                        "then rollback. Final operator recommendation: pause rollout."
                                    ),
                                }
                            ]
                        }
                    ],
                },
            }
        ),
    )
    _write(
        evidence_dir / "runtime/healthz.json",
        json.dumps(
            {
                "ok": True,
                "orchestration": "Google ADK",
                "intelligence": "Gemini via Vertex AI",
            }
        ),
    )
    _write(
        evidence_dir / "runtime/source-manifest.json",
        json.dumps(
            {
                "framework": "Google ADK",
                "imports": ["google.adk.agents.Agent", "google.adk.runners.Runner"],
            }
        ),
    )
    _write(
        evidence_dir / "business/package.md",
        (
            "Construct Enterprise AgentOps Control Plane is a B2B package for the buyer: "
            "platform engineering or IT operations leader. The workflow is production "
            "incident response using Construct Enterprise AgentOps."
        ),
    )
    _write(
        evidence_dir / "governance/controls.md",
        (
            "Enterprise governance uses per-agent service identity, approval gates, "
            "rollback controls, and observability through Cloud Logging."
        ),
    )
    _write(
        evidence_dir / "deploy/rollback-plan.md",
        "Rollback by redeploying the previous Cloud Run revision and recording approval.",
    )
    _write(
        evidence_dir / "enterprise/gemini-enterprise-registration.md",
        (
            "Gemini Enterprise registration-ready path: register the A2A agent card, "
            "verify the skill, and share through Gemini Enterprise app administration."
        ),
    )


def test_track3_evidence_gate_passes_complete_bundle(tmp_path):
    evidence_dir = tmp_path / "evidence"
    service_url = "https://construct-agentops-a2a-abc-uc.a.run.app"
    _write_complete_evidence(evidence_dir, service_url)
    (evidence_dir / "manifest.json").write_text(
        json.dumps(_manifest(service_url)),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(_script()), "--evidence-dir", str(evidence_dir)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    report = json.loads(result.stdout)
    assert report["passed"] is True
    assert report["summary"] == {"failed": 0, "passed": 7, "total": 7}


def test_track3_evidence_gate_rejects_non_cloud_run_url(tmp_path):
    evidence_dir = tmp_path / "evidence"
    service_url = "https://example.com/agent"
    _write_complete_evidence(evidence_dir, service_url)
    (evidence_dir / "manifest.json").write_text(
        json.dumps(_manifest(service_url)),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(_script()), "--evidence-dir", str(evidence_dir)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    report = json.loads(result.stdout)
    deployment = next(
        item for item in report["checks"] if item["claim"] == "google_cloud_deployment"
    )
    assert deployment["status"] == "fail"
    assert any("run.app" in item for item in deployment["failures"])


def test_track3_evidence_gate_rejects_a2a_without_completed_task(tmp_path):
    evidence_dir = tmp_path / "evidence"
    service_url = "https://construct-agentops-a2a-abc-uc.a.run.app"
    _write_complete_evidence(evidence_dir, service_url)
    _write(
        evidence_dir / "a2a/message-send-response.json",
        json.dumps({"result": {"status": {"state": "failed"}, "artifacts": []}}),
    )
    (evidence_dir / "manifest.json").write_text(
        json.dumps(_manifest(service_url)),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(_script()), "--evidence-dir", str(evidence_dir)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    report = json.loads(result.stdout)
    a2a = next(item for item in report["checks"] if item["claim"] == "a2a_interoperability")
    assert a2a["status"] == "fail"
    assert "A2A message/send response must be completed" in a2a["failures"]


def test_track3_evidence_gate_rejects_incomplete_demo_story(tmp_path):
    evidence_dir = tmp_path / "evidence"
    service_url = "https://construct-agentops-a2a-abc-uc.a.run.app"
    _write_complete_evidence(evidence_dir, service_url)
    _write(
        evidence_dir / "a2a/message-send-response.json",
        json.dumps(
            {
                "result": {
                    "status": {"state": "completed"},
                    "artifacts": [
                        {
                            "parts": [
                                {
                                    "type": "text",
                                    "text": "Task completed with a generic incident note.",
                                }
                            ]
                        }
                    ],
                }
            }
        ),
    )
    (evidence_dir / "manifest.json").write_text(
        json.dumps(_manifest(service_url)),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(_script()), "--evidence-dir", str(evidence_dir)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    report = json.loads(result.stdout)
    a2a = next(item for item in report["checks"] if item["claim"] == "a2a_interoperability")
    assert a2a["status"] == "fail"
    assert "A2A response artifact must mention business impact" in a2a["failures"]
    assert "A2A response artifact must mention A2A handoff" in a2a["failures"]
    assert "A2A response artifact must mention operator recommendation" in a2a["failures"]


def test_track3_evidence_gate_rejects_non_registration_ready_agent_card(tmp_path):
    evidence_dir = tmp_path / "evidence"
    service_url = "https://construct-agentops-a2a-demo-uc.a.run.app"
    _write_complete_evidence(evidence_dir, service_url)
    _write(
        evidence_dir / "a2a/agent-card.json",
        json.dumps(
            {
                "name": "Construct Enterprise AgentOps Control Plane",
                "description": "B2B A2A agent",
                "url": service_url,
                "skills": [{"id": "enterprise-agentops-incident-plan"}],
            }
        ),
    )
    (evidence_dir / "manifest.json").write_text(
        json.dumps(_manifest(service_url)), encoding="utf-8"
    )

    result = subprocess.run(
        [sys.executable, str(_script()), "--evidence-dir", str(evidence_dir)],
        cwd=_repo_root(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    report = json.loads(result.stdout)
    a2a = next(
        check for check in report["checks"] if check["claim"] == "a2a_interoperability"
    )
    assert "agent card missing protocolVersion" in a2a["failures"]
    assert "agent card missing iconUrl" in a2a["failures"]


def test_track3_evidence_gate_rejects_missing_enterprise_package(tmp_path):
    evidence_dir = tmp_path / "evidence"
    service_url = "https://construct-agentops-a2a-abc-uc.a.run.app"
    _write_complete_evidence(evidence_dir, service_url)
    _write(evidence_dir / "business/package.md", "generic dev tool")
    (evidence_dir / "manifest.json").write_text(
        json.dumps(_manifest(service_url)),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(_script()), "--evidence-dir", str(evidence_dir)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    report = json.loads(result.stdout)
    package = next(
        item for item in report["checks"] if item["claim"] == "b2b_enterprise_package"
    )
    assert package["status"] == "fail"
    assert any("b2b" in item.lower() for item in package["failures"])
