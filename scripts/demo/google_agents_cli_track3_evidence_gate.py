#!/usr/bin/env python3
"""Validate Track 3 enterprise deployment evidence before recording."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


REQUIRED_CLAIMS = (
    "google_cloud_deployment",
    "a2a_interoperability",
    "gemini_powered_intelligence",
    "adk_orchestration",
    "b2b_enterprise_package",
    "enterprise_governance",
    "production_operating_controls",
    "gemini_enterprise_readiness",
)

PLACEHOLDER_TOKENS = {
    "",
    "todo",
    "todo:",
    "placeholder",
    "replace",
    "replace-me",
    "sample",
    "tbd",
}
SCAN_BYTES = 131_072


def _template() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "scenario": {
            "name": "Revka Enterprise AgentOps Control Plane",
            "b2b_persona": "Platform engineering leader",
            "business_workflow": "Governed production incident response",
            "measurable_outcome": "A2A incident plan returned from Cloud Run",
        },
        "claims": {
            "google_cloud_deployment": {
                "project_id": "your-google-cloud-project",
                "region": "us-central1",
                "service_name": "revka-agentops-a2a",
                "service_url": "https://SERVICE_URL",
                "evidence_files": ["deploy/cloud-run-service.json", "deploy/deploy-output.txt"],
            },
            "a2a_interoperability": {
                "agent_card_url": "https://SERVICE_URL/.well-known/agent-card.json",
                "rpc_url": "https://SERVICE_URL/",
                "skill_id": "enterprise-agentops-incident-plan",
                "evidence_files": ["a2a/agent-card.json", "a2a/message-send-response.json"],
            },
            "gemini_powered_intelligence": {
                "model_family": "Gemini",
                "runtime": "Vertex AI",
                "evidence_files": ["runtime/healthz.json", "a2a/message-send-response.json"],
            },
            "adk_orchestration": {
                "framework": "Google ADK",
                "source_files": [
                    "examples/google-agents-track3/revka-agentops-a2a/agent.py",
                    "examples/google-agents-track3/revka-agentops-a2a/main.py",
                ],
                "evidence_files": ["runtime/source-manifest.json"],
            },
            "b2b_enterprise_package": {
                "package_name": "Revka Enterprise AgentOps Control Plane",
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
            "production_operating_controls": {
                "auth": "Cloud Run IAM plus A2A bearer token",
                "service_account": "Dedicated least-privilege Cloud Run service account",
                "request_limits": "MAX_MESSAGE_CHARS and Cloud Run containerConcurrency",
                "timeout": "ADK_RESPONSE_TIMEOUT_SECONDS and Cloud Run timeoutSeconds",
                "retention": "MAX_TASKS bounded task retention",
                "evidence_files": [
                    "operations/production-controls.md",
                    "deploy/cloudrun-production.yaml",
                    "runtime/readyz.json",
                ],
            },
            "gemini_enterprise_readiness": {
                "status": "registration-ready",
                "requires_admin_access": True,
                "evidence_files": ["enterprise/gemini-enterprise-registration.md"],
            },
        },
    }


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")[:SCAN_BYTES]


def _load_json(path: Path) -> tuple[dict[str, Any] | list[Any] | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except Exception as exc:
        return None, str(exc)


def _is_placeholder(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        stripped = value.strip().lower()
        if stripped in PLACEHOLDER_TOKENS:
            return True
        return bool(re.search(r"\b(todo|placeholder|replace me|tbd)\b", stripped))
    if isinstance(value, list):
        return not value or any(_is_placeholder(item) for item in value)
    if isinstance(value, dict):
        return not value or any(_is_placeholder(item) for item in value.values())
    return False


def _claim_files(evidence_dir: Path, claim: dict[str, Any]) -> tuple[list[Path], list[str]]:
    failures: list[str] = []
    files: list[Path] = []
    for rel in claim.get("evidence_files", []):
        path = evidence_dir / rel
        if not path.exists():
            failures.append(f"missing evidence file: {rel}")
            continue
        if path.stat().st_size == 0:
            failures.append(f"empty evidence file: {rel}")
            continue
        files.append(path)
    return files, failures


def _corpus(paths: list[Path]) -> str:
    parts = []
    for path in paths:
        parts.append(path.name)
        parts.append(_read_text(path))
    return "\n".join(parts)


def _check_google_cloud_deployment(claim: dict[str, Any], files: list[Path]) -> list[str]:
    failures: list[str] = []
    service_url = str(claim.get("service_url", ""))
    if not service_url.startswith("https://"):
        failures.append("service_url must be an https Cloud Run URL")
    if "run.app" not in service_url:
        failures.append("service_url must point to a Cloud Run run.app service")

    service_json = next((path for path in files if path.name == "cloud-run-service.json"), None)
    if service_json:
        parsed, error = _load_json(service_json)
        if error:
            failures.append(f"cloud-run-service.json is not valid JSON: {error}")
        elif isinstance(parsed, dict):
            text = json.dumps(parsed)
            for expected in (claim.get("project_id"), claim.get("region"), claim.get("service_name")):
                if expected and str(expected) not in text:
                    failures.append(f"cloud run service evidence does not mention {expected}")
            status = parsed.get("status", {})
            if "url" in status and status.get("url") != service_url:
                failures.append("manifest service_url does not match Cloud Run status.url")
    corpus = _corpus(files).lower()
    for token in (
        "cloud run",
        str(claim.get("service_name", "")).lower(),
        str(claim.get("project_id", "")).lower(),
    ):
        if token and token not in corpus:
            failures.append(f"deployment evidence must mention {token}")
    return failures


def _check_a2a_interoperability(claim: dict[str, Any], files: list[Path]) -> list[str]:
    failures: list[str] = []
    card_path = next((path for path in files if path.name == "agent-card.json"), None)
    response_path = next((path for path in files if path.name == "message-send-response.json"), None)

    if card_path:
        card, error = _load_json(card_path)
        if error:
            failures.append(f"agent-card.json is not valid JSON: {error}")
        elif isinstance(card, dict):
            skills = card.get("skills", [])
            skill_ids = [skill.get("id") for skill in skills if isinstance(skill, dict)]
            if claim.get("skill_id") not in skill_ids:
                failures.append("agent card does not expose the manifest skill_id")
            for key in (
                "protocolVersion",
                "name",
                "description",
                "url",
                "iconUrl",
                "version",
                "capabilities",
                "skills",
                "defaultInputModes",
                "defaultOutputModes",
            ):
                if key not in card:
                    failures.append(f"agent card missing {key}")
            if card.get("protocolVersion") != "0.3":
                failures.append("agent card protocolVersion must be 0.3")
            if not str(card.get("iconUrl", "")).startswith("data:image/"):
                failures.append("agent card iconUrl must be an image data URL")

    if response_path:
        response, error = _load_json(response_path)
        if error:
            failures.append(f"message-send-response.json is not valid JSON: {error}")
        elif isinstance(response, dict):
            task = response.get("result", response)
            state = ((task.get("status") or {}).get("state") if isinstance(task, dict) else None)
            if state != "completed":
                failures.append("A2A message/send response must be completed")
            artifacts = task.get("artifacts", []) if isinstance(task, dict) else []
            artifact_text = json.dumps(artifacts).lower()
            required_response_terms = {
                "incident": ("incident",),
                "business impact": ("business impact",),
                "specialized agents": ("specialized", "agents"),
                "A2A handoff": ("a2a",),
                "Google Cloud evidence": ("google cloud", "cloud logging", "cloud"),
                "approval boundary": ("approval",),
                "rollback path": ("rollback",),
                "operator recommendation": ("operator", "recommendation"),
            }
            for label, alternatives in required_response_terms.items():
                if not any(token in artifact_text for token in alternatives):
                    failures.append(f"A2A response artifact must mention {label}")
    return failures


def _check_gemini_powered_intelligence(_claim: dict[str, Any], files: list[Path]) -> list[str]:
    corpus = _corpus(files).lower()
    failures = []
    for token in ("gemini", "vertex ai"):
        if token not in corpus:
            failures.append(f"runtime evidence must mention {token}")
    return failures


def _check_adk_orchestration(claim: dict[str, Any], files: list[Path], repo_root: Path) -> list[str]:
    failures: list[str] = []
    for rel in claim.get("source_files", []):
        path = repo_root / rel
        if not path.exists():
            failures.append(f"missing source file: {rel}")
            continue
        text = _read_text(path)
        if "google.adk" not in text and "Google ADK" not in text:
            failures.append(f"source file does not prove ADK use: {rel}")
    corpus = _corpus(files).lower()
    if "google adk" not in corpus and "google.adk" not in corpus:
        failures.append("runtime source manifest must mention Google ADK")
    return failures


def _check_text_claim(claim_name: str, files: list[Path]) -> list[str]:
    corpus = _corpus(files).lower()
    failures: list[str] = []
    required = {
        "b2b_enterprise_package": ("b2b", "buyer", "workflow", "revka enterprise agentops"),
        "enterprise_governance": ("identity", "rollback", "observability", "cloud logging"),
        "gemini_enterprise_readiness": ("gemini enterprise", "a2a", "agent card", "registration"),
    }.get(claim_name, ())
    for token in required:
        if token not in corpus:
            failures.append(f"{claim_name} evidence must mention {token}")
    return failures


def _check_production_operating_controls(files: list[Path]) -> list[str]:
    failures: list[str] = []
    corpus = _corpus(files).lower()
    for token in (
        "iam",
        "service account",
        "a2a_bearer_token",
        "request",
        "timeout",
        "retention",
        "cloud logging",
    ):
        if token not in corpus:
            failures.append(f"production controls evidence must mention {token}")

    manifest_path = next((path for path in files if path.name == "cloudrun-production.yaml"), None)
    if manifest_path:
        manifest = _read_text(manifest_path)
        for token in (
            "serviceAccountName:",
            "secretKeyRef:",
            "containerConcurrency:",
            "timeoutSeconds:",
            "MAX_MESSAGE_CHARS",
            "MAX_TASKS",
            "ADK_RESPONSE_TIMEOUT_SECONDS",
            "A2A_BEARER_TOKEN",
            "ENABLE_CLOUD_LOGGING",
        ):
            if token not in manifest:
                failures.append(f"production Cloud Run manifest must include {token}")

    readyz_path = next((path for path in files if path.name == "readyz.json"), None)
    if readyz_path:
        readyz, error = _load_json(readyz_path)
        if error:
            failures.append(f"readyz.json is not valid JSON: {error}")
        elif isinstance(readyz, dict):
            if readyz.get("ready") is not True:
                failures.append("readyz.json must report ready: true")
            for key in (
                "auth_mode",
                "max_message_chars",
                "max_tasks",
                "adk_response_timeout_seconds",
                "platform",
                "orchestration",
                "intelligence",
            ):
                if key not in readyz:
                    failures.append(f"readyz.json missing {key}")
            if readyz.get("auth_mode") not in {"bearer-token", "iam-secured", "public-demo"}:
                failures.append("readyz.json auth_mode must describe the invocation auth posture")
        else:
            failures.append("readyz.json must be a JSON object")
    return failures


def _check_claim(
    *,
    claim_name: str,
    claim: dict[str, Any],
    evidence_dir: Path,
    repo_root: Path,
) -> dict[str, Any]:
    failures: list[str] = []
    for key, value in claim.items():
        if key == "requires_admin_access":
            continue
        if _is_placeholder(value):
            failures.append(f"{claim_name}.{key} is missing or placeholder")

    files, file_failures = _claim_files(evidence_dir, claim)
    failures.extend(file_failures)
    if not file_failures:
        if claim_name == "google_cloud_deployment":
            failures.extend(_check_google_cloud_deployment(claim, files))
        elif claim_name == "a2a_interoperability":
            failures.extend(_check_a2a_interoperability(claim, files))
        elif claim_name == "gemini_powered_intelligence":
            failures.extend(_check_gemini_powered_intelligence(claim, files))
        elif claim_name == "adk_orchestration":
            failures.extend(_check_adk_orchestration(claim, files, repo_root))
        elif claim_name == "production_operating_controls":
            failures.extend(_check_production_operating_controls(files))
        elif claim_name in {
            "b2b_enterprise_package",
            "enterprise_governance",
            "gemini_enterprise_readiness",
        }:
            failures.extend(_check_text_claim(claim_name, files))

    return {
        "claim": claim_name,
        "status": "pass" if not failures else "fail",
        "failures": failures,
        "evidence_files": [str(path.relative_to(evidence_dir)) for path in files],
    }


def run_gate(evidence_dir: Path, repo_root: Path) -> dict[str, Any]:
    manifest_path = evidence_dir / "manifest.json"
    if not manifest_path.exists():
        return {
            "gate": "google_agents_cli_track3_evidence",
            "passed": False,
            "global_failures": [f"missing manifest: {manifest_path}"],
            "checks": [],
            "summary": {"passed": 0, "failed": len(REQUIRED_CLAIMS), "total": len(REQUIRED_CLAIMS)},
            "template": _template(),
        }

    manifest, error = _load_json(manifest_path)
    if error or not isinstance(manifest, dict):
        return {
            "gate": "google_agents_cli_track3_evidence",
            "passed": False,
            "global_failures": [f"manifest is not valid JSON object: {error}"],
            "checks": [],
            "summary": {"passed": 0, "failed": len(REQUIRED_CLAIMS), "total": len(REQUIRED_CLAIMS)},
            "template": _template(),
        }

    global_failures: list[str] = []
    scenario = manifest.get("scenario", {})
    for key in ("name", "b2b_persona", "business_workflow", "measurable_outcome"):
        if _is_placeholder(scenario.get(key)):
            global_failures.append(f"scenario.{key} is missing or placeholder")

    claims = manifest.get("claims", {})
    checks = []
    for claim_name in REQUIRED_CLAIMS:
        claim = claims.get(claim_name)
        if not isinstance(claim, dict):
            checks.append(
                {
                    "claim": claim_name,
                    "status": "fail",
                    "failures": [f"missing claim: {claim_name}"],
                    "evidence_files": [],
                }
            )
            continue
        checks.append(
            _check_claim(
                claim_name=claim_name,
                claim=claim,
                evidence_dir=evidence_dir,
                repo_root=repo_root,
            )
        )

    passed_count = sum(1 for check in checks if check["status"] == "pass")
    failed_count = len(checks) - passed_count
    passed = failed_count == 0 and not global_failures
    return {
        "gate": "google_agents_cli_track3_evidence",
        "passed": passed,
        "global_failures": global_failures,
        "checks": checks,
        "summary": {"passed": passed_count, "failed": failed_count, "total": len(checks)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence-dir",
        default=".demo/google-agents-cli-track3",
        help="Directory containing Track 3 manifest and captured evidence",
    )
    parser.add_argument("--output", help="Optional path to write the JSON report")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    report = run_gate(Path(args.evidence_dir), repo_root)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
