#!/usr/bin/env python3
"""Run deterministic Track 3 demo-readiness probes.

This probe checks that the tracked Cloud Run A2A demo can support every
outcome the Track 3 recording runbook says the video may show. It is source and
documentation based, so it can run before a live Cloud Run rehearsal. The live
deployment and evidence artifacts are still validated by
google_agents_cli_track3_evidence_gate.py.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[2]
TRACK3_APP_DIR = REPO_ROOT / "examples" / "google-agents-track3" / "revka-agentops-a2a"
TRACK3_PRODUCTION_MANIFEST = TRACK3_APP_DIR / "cloudrun.production.yaml"
TRACK3_READINESS_DOC = REPO_ROOT / "docs" / "ops" / "google-agents-track3-enterprise-readiness.md"
TRACK3_EVIDENCE_GATE = REPO_ROOT / "scripts" / "demo" / "google_agents_cli_track3_evidence_gate.py"

ProbeFn = Callable[[], dict[str, Any]]


@dataclass(frozen=True)
class Probe:
    name: str
    description: str
    run: ProbeFn


TRACK3_DEMO_OUTCOMES: tuple[dict[str, Any], ...] = (
    {
        "id": "cloud_run_runtime_readiness",
        "title": "Cloud Run runtime readiness",
        "required_probes": ["runtime_surface", "deployment_dependencies"],
    },
    {
        "id": "registration_ready_a2a_discovery",
        "title": "Registration-ready A2A discovery",
        "required_probes": ["agent_card_registration_surface"],
    },
    {
        "id": "live_a2a_incident_plan",
        "title": "Live A2A incident plan",
        "required_probes": ["message_send_success_surface", "enterprise_reasoning_surface"],
    },
    {
        "id": "a2a_task_lifecycle_branches",
        "title": "A2A task lifecycle branches",
        "required_probes": ["task_lifecycle_surface"],
    },
    {
        "id": "demo_safe_error_branches",
        "title": "Demo-safe error branches",
        "required_probes": ["error_surface"],
    },
    {
        "id": "production_operating_controls",
        "title": "Production operating controls",
        "required_probes": ["production_controls_surface"],
    },
    {
        "id": "b2b_governance_story",
        "title": "B2B governance story",
        "required_probes": ["enterprise_reasoning_surface", "track3_evidence_gate_alignment"],
    },
    {
        "id": "final_rehearsal_gate_alignment",
        "title": "Final rehearsal gate alignment",
        "required_probes": [
            "documented_outcome_matrix_alignment",
            "track3_evidence_gate_alignment",
        ],
    },
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _plain_title(value: str) -> str:
    return value.replace("`", "")


def _read_outcome_doc_titles() -> list[str]:
    lines = _read(TRACK3_READINESS_DOC).splitlines()
    titles: list[str] = []
    in_table = False
    for line in lines:
        if line.startswith("| Outcome to show |"):
            in_table = True
            continue
        if not in_table:
            continue
        if line.startswith("|---"):
            continue
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells and cells[0]:
            titles.append(_plain_title(cells[0]))
    return titles


def _expect_documented_outcome_matrix_alignment() -> dict[str, Any]:
    documented_titles = _read_outcome_doc_titles()
    gated_titles = [_plain_title(str(item["title"])) for item in TRACK3_DEMO_OUTCOMES]
    _assert(documented_titles, "Track 3 readiness doc outcome matrix was not found")
    _assert(
        documented_titles == gated_titles,
        "documented Track 3 outcomes differ from gated outcomes: "
        f"documented={documented_titles!r}; gated={gated_titles!r}",
    )
    return {
        "track3_readiness_doc": str(TRACK3_READINESS_DOC),
        "documented_outcomes": documented_titles,
        "gated_outcomes": gated_titles,
    }


def _expect_runtime_surface() -> dict[str, Any]:
    main = _read(TRACK3_APP_DIR / "main.py")
    required = [
        '@app.get("/healthz")',
        '@app.get("/statusz")',
        '@app.get("/runtime")',
        '@app.get("/readyz")',
        '"track": "google-startups-ai-agents-track-3"',
        '"orchestration": "Google ADK"',
        '"intelligence": "Gemini via Vertex AI"',
    ]
    for fragment in required:
        _assert(fragment in main, f"runtime surface missing: {fragment}")
    return {"checked_source": str(TRACK3_APP_DIR / "main.py"), "required_fragments": required}


def _expect_agent_card_registration_surface() -> dict[str, Any]:
    main = _read(TRACK3_APP_DIR / "main.py")
    required = [
        '@app.get("/.well-known/agent-card.json")',
        '@app.get("/agent-card.json")',
        'payload.method == "agent/card"',
        '"protocolVersion": "0.3"',
        '"iconUrl": ICON_URL',
        '"defaultInputModes": ["text/plain", "application/json"]',
        '"defaultOutputModes": ["text/plain", "application/json"]',
        '"enterprise-agentops-incident-plan"',
    ]
    for fragment in required:
        _assert(fragment in main, f"A2A registration surface missing: {fragment}")
    return {"checked_source": str(TRACK3_APP_DIR / "main.py"), "required_fragments": required}


def _expect_message_send_success_surface() -> dict[str, Any]:
    main = _read(TRACK3_APP_DIR / "main.py")
    required = [
        'payload.method != "message/send"',
        "asyncio.wait_for(",
        "_adk_response(message, user_id=user_id, session_id=context_id)",
        'state="completed"',
        '"enterprise-agentops-plan"',
        '"platform": "Google Cloud Run"',
        '"orchestration": "Google ADK"',
        '"intelligence": "Gemini via Vertex AI"',
        '"b2bPackage": "Revka Enterprise AgentOps Control Plane"',
    ]
    for fragment in required:
        _assert(fragment in main, f"message/send success surface missing: {fragment}")
    return {"checked_source": str(TRACK3_APP_DIR / "main.py"), "required_fragments": required}


def _expect_task_lifecycle_surface() -> dict[str, Any]:
    main = _read(TRACK3_APP_DIR / "main.py")
    required = [
        'payload.method == "tasks/get"',
        'payload.method == "tasks/list"',
        'payload.method == "tasks/cancel"',
        "_store_task(task_id, task)",
        '"state": "canceled"',
        '"code": "TaskNotFoundError"',
    ]
    for fragment in required:
        _assert(fragment in main, f"task lifecycle surface missing: {fragment}")
    return {"checked_source": str(TRACK3_APP_DIR / "main.py"), "required_fragments": required}


def _expect_error_surface() -> dict[str, Any]:
    main = _read(TRACK3_APP_DIR / "main.py")
    required = [
        '"code": "UnsupportedOperationError"',
        'code="InvalidRequest"',
        '"No text content in message"',
        "except Exception as exc",
        'state="failed"',
        '"Runtime error captured for operator diagnosis."',
    ]
    for fragment in required:
        _assert(fragment in main, f"demo-safe error surface missing: {fragment}")
    return {"checked_source": str(TRACK3_APP_DIR / "main.py"), "required_fragments": required}


def _expect_enterprise_reasoning_surface() -> dict[str, Any]:
    agent = _read(TRACK3_APP_DIR / "agent.py")
    required = [
        "inspect_incident_context",
        "recommend_agentops_policy",
        "risk = \"critical\"",
        "security-review",
        "business impact",
        "specialized agents",
        "Google Cloud evidence",
        "approval boundary",
        "rollback path",
        "final operator recommendation",
        "tools=[inspect_incident_context, recommend_agentops_policy]",
    ]
    for fragment in required:
        _assert(fragment in agent, f"enterprise reasoning surface missing: {fragment}")
    return {"checked_source": str(TRACK3_APP_DIR / "agent.py"), "required_fragments": required}


def _expect_production_controls_surface() -> dict[str, Any]:
    main = _read(TRACK3_APP_DIR / "main.py")
    manifest = _read(TRACK3_PRODUCTION_MANIFEST)
    docs = "\n".join(
        [
            _read(TRACK3_APP_DIR / "README.md"),
            _read(TRACK3_READINESS_DOC),
        ]
    )
    required_main = [
        "A2A_BEARER_TOKEN = os.getenv",
        '"auth_mode": AUTH_MODE',
        "hmac.compare_digest",
        "MAX_MESSAGE_CHARS",
        "MAX_TASKS",
        "ADK_RESPONSE_TIMEOUT_SECONDS",
        "_store_task",
        "asyncio.wait_for(",
        '@app.get("/readyz")',
        "google.cloud.logging.Client().setup_logging()",
        'code="Unauthorized"',
    ]
    required_manifest = [
        "serviceAccountName:",
        "secretKeyRef:",
        "containerConcurrency:",
        "timeoutSeconds:",
        "MAX_MESSAGE_CHARS",
        "MAX_TASKS",
        "ADK_RESPONSE_TIMEOUT_SECONDS",
        "A2A_BEARER_TOKEN",
        "ENABLE_CLOUD_LOGGING",
    ]
    for fragment in required_main:
        _assert(fragment in main, f"production control source missing: {fragment}")
    for fragment in required_manifest:
        _assert(fragment in manifest, f"production Cloud Run manifest missing: {fragment}")
    for fragment in ("IAM", "service account", "A2A_BEARER_TOKEN", "/readyz", "Cloud Logging"):
        _assert(fragment in docs, f"production docs missing: {fragment}")
    return {
        "checked_source": str(TRACK3_APP_DIR / "main.py"),
        "production_manifest": str(TRACK3_PRODUCTION_MANIFEST),
        "required_main_fragments": required_main,
        "required_manifest_fragments": required_manifest,
    }


def _expect_deployment_dependencies() -> dict[str, Any]:
    requirements = _read(TRACK3_APP_DIR / "requirements.txt")
    runtime = _read(TRACK3_APP_DIR / "runtime.txt")
    required = ["fastapi", "uvicorn", "google-adk", "google-cloud-logging"]
    for fragment in required:
        _assert(fragment in requirements, f"deployment dependency missing: {fragment}")
    _assert(runtime.strip().startswith("python-"), "runtime.txt must declare a Python runtime")
    return {
        "requirements": str(TRACK3_APP_DIR / "requirements.txt"),
        "runtime": str(TRACK3_APP_DIR / "runtime.txt"),
        "required_dependencies": required,
    }


def _expect_track3_evidence_gate_alignment() -> dict[str, Any]:
    gate = _read(TRACK3_EVIDENCE_GATE)
    doc = _read(TRACK3_READINESS_DOC)
    match = re.search(r"REQUIRED_CLAIMS\s*=\s*\((.*?)\)", gate, re.DOTALL)
    _assert(match is not None, "Track 3 evidence gate REQUIRED_CLAIMS not found")
    claims = re.findall(r'"([^"]+)"', match.group(1))
    _assert(len(claims) == 8, f"expected 8 Track 3 required claims, found {len(claims)}")
    for claim in claims:
        _assert(claim in doc, f"Track 3 readiness doc does not mention claim: {claim}")
    for fragment in (
        "business impact",
        "specialized",
        "google cloud",
        "approval",
        "rollback",
        "operator",
    ):
        _assert(fragment in gate.lower(), f"Track 3 evidence gate missing response check: {fragment}")
    return {
        "track3_evidence_gate": str(TRACK3_EVIDENCE_GATE),
        "track3_readiness_doc": str(TRACK3_READINESS_DOC),
        "required_claims": claims,
    }


def _run_probes() -> list[dict[str, Any]]:
    probes = [
        Probe(
            "documented_outcome_matrix_alignment",
            "Track 3 readiness doc outcomes match the gated outcome matrix",
            _expect_documented_outcome_matrix_alignment,
        ),
        Probe("runtime_surface", "Cloud Run runtime endpoints prove ADK/Gemini metadata", _expect_runtime_surface),
        Probe(
            "agent_card_registration_surface",
            "A2A discovery and registration fields are exposed",
            _expect_agent_card_registration_surface,
        ),
        Probe(
            "message_send_success_surface",
            "message/send can return the executive incident plan artifact",
            _expect_message_send_success_surface,
        ),
        Probe(
            "task_lifecycle_surface",
            "tasks/get, tasks/list, and tasks/cancel branches are present",
            _expect_task_lifecycle_surface,
        ),
        Probe("error_surface", "Invalid and unsupported requests return demo-safe errors", _expect_error_surface),
        Probe(
            "enterprise_reasoning_surface",
            "ADK agent has B2B governance, risk routing, approval, and rollback instructions",
            _expect_enterprise_reasoning_surface,
        ),
        Probe(
            "production_controls_surface",
            "Production request, auth, readiness, retention, and Cloud Run controls are declared",
            _expect_production_controls_surface,
        ),
        Probe("deployment_dependencies", "Cloud Run deployment dependencies are declared", _expect_deployment_dependencies),
        Probe(
            "track3_evidence_gate_alignment",
            "Track 3 evidence gate covers the claims and response content used in the runbook",
            _expect_track3_evidence_gate_alignment,
        ),
    ]

    results: list[dict[str, Any]] = []
    for probe in probes:
        try:
            results.append(
                {
                    "name": probe.name,
                    "description": probe.description,
                    "status": "pass",
                    "result": probe.run(),
                }
            )
        except Exception as exc:  # noqa: BLE001 - diagnostic probe should report all gaps.
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
    for outcome in TRACK3_DEMO_OUTCOMES:
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


def run_probe() -> dict[str, Any]:
    results = _run_probes()
    failures = [item for item in results if item["status"] != "pass"]
    outcome_matrix = _build_outcome_matrix(results)
    failed_outcomes = outcome_matrix["summary"]["failed"]
    return {
        "probe": "google_agents_cli_track3_demo_readiness",
        "mode": "deterministic_source_probe",
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="Write JSON evidence bundle to this path")
    parser.add_argument("--quiet", action="store_true", help="Only set exit status and optional output file")
    args = parser.parse_args(argv)

    report = run_probe()
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    if not args.quiet:
        print(text)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
