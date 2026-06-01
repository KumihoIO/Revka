#!/usr/bin/env python3
"""Validate Track 2 demo evidence before recording.

This gate intentionally does not fabricate Agent Platform results. It checks a
local evidence bundle produced during the real demo rehearsal and fails closed
when a Track 2 claim lacks concrete artifacts.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REQUIRED_CLAIMS = (
    "optimization_improvement",
    "agent_simulation",
    "agent_observability",
    "agent_optimizer",
    "live_google_cloud_deployment",
    "b2b_value",
)


def _template() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "scenario": {
            "name": "TODO: concrete B2B agent demo name",
            "b2b_persona": "TODO: buyer/operator persona",
            "business_workflow": "TODO: workflow the agent improves",
            "measurable_outcome": "TODO: metric visible in the demo",
        },
        "claims": {
            "optimization_improvement": {
                "metric_name": "eval_success_rate",
                "before": 0.0,
                "after": 0.0,
                "higher_is_better": True,
                "evidence_files": [
                    "eval/baseline.json",
                    "eval/optimized.json",
                ],
            },
            "agent_simulation": {
                "scenario_count": 0,
                "edge_cases": [
                    "TODO: synthetic rare or multi-variable scenario",
                ],
                "evidence_files": [
                    "simulation/run-output.json",
                ],
            },
            "agent_observability": {
                "trace_ids": [
                    "TODO: trace id from Agent Observability or runtime trace",
                ],
                "evidence_files": [
                    "observability/trace.jsonl",
                ],
            },
            "agent_optimizer": {
                "original_instructions_file": "optimizer/original-instructions.md",
                "optimized_instructions_file": "optimizer/optimized-instructions.md",
                "measured_delta": 0.0,
                "evidence_files": [
                    "optimizer/result.json",
                ],
            },
            "live_google_cloud_deployment": {
                "project_id": "TODO: google cloud project id",
                "region": "TODO: deployment region",
                "resource": "TODO: Agent Platform resource or service URL",
                "rollback_plan_file": "deploy/rollback-plan.md",
                "evidence_files": [
                    "deploy/deploy-output.txt",
                ],
            },
            "b2b_value": {
                "persona": "TODO: B2B user persona",
                "workflow": "TODO: business workflow",
                "inputs": [
                    "TODO: business input",
                ],
                "actions": [
                    "TODO: action taken by agent",
                ],
                "measurable_outcome": "TODO: business metric or outcome",
                "evidence_files": [
                    "business/use-case.md",
                ],
            },
        },
    }


def _is_todo(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower().startswith("todo:")


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not _is_todo(value)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _file_failures(base: Path, files: list[Any]) -> list[str]:
    failures: list[str] = []
    for rel in files:
        if not _nonempty_string(rel):
            failures.append("evidence file path is empty or TODO")
            continue
        path = Path(rel)
        if path.is_absolute() or ".." in path.parts:
            failures.append(f"evidence file must stay inside evidence dir: {rel}")
            continue
        full = base / path
        if not full.is_file():
            failures.append(f"missing evidence file: {rel}")
            continue
        if full.stat().st_size == 0:
            failures.append(f"empty evidence file: {rel}")
    return failures


def _safe_evidence_path(base: Path, rel: Any) -> Path | None:
    if not _nonempty_string(rel):
        return None
    path = Path(rel)
    if path.is_absolute() or ".." in path.parts:
        return None
    return base / path


def _failures_for_files(base: Path, claim: dict[str, Any], files: list[Any]) -> list[str]:
    failures: list[str] = []
    extra = claim.get("evidence_files", [])
    if not isinstance(extra, list) or not extra:
        failures.append("evidence_files must list at least one file")
        return failures
    if not all(isinstance(item, str) for item in extra):
        failures.append("evidence_files entries must be strings")
        return failures

    all_files = list(files)
    for item in extra:
        if item not in all_files:
            all_files.append(item)
    return failures + _file_failures(base, all_files)


def _check_common(claim_name: str, claim: Any) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(claim, dict):
        return {}, [f"{claim_name} must be an object"]
    return claim, []


def _check_optimization(claim: dict[str, Any], base: Path) -> list[str]:
    failures = []
    metric = claim.get("metric_name")
    before = _number(claim.get("before"))
    after = _number(claim.get("after"))
    higher_is_better = claim.get("higher_is_better", True)
    if not _nonempty_string(metric):
        failures.append("metric_name is required")
    if before is None or after is None:
        failures.append("before and after must be numeric")
    elif not isinstance(higher_is_better, bool):
        failures.append("higher_is_better must be a boolean")
    elif higher_is_better is False:
        if not after < before:
            failures.append("after must be lower than before when higher_is_better is false")
    elif not after > before:
        failures.append("after must be higher than before")
    failures.extend(_failures_for_files(base, claim, []))
    return failures


def _check_simulation(claim: dict[str, Any], base: Path) -> list[str]:
    failures = []
    count = claim.get("scenario_count")
    edge_cases = claim.get("edge_cases")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        failures.append("scenario_count must be at least 1")
    if (
        not isinstance(edge_cases, list)
        or not edge_cases
        or not all(_nonempty_string(item) for item in edge_cases)
    ):
        failures.append("edge_cases must list at least one concrete scenario")
    failures.extend(_failures_for_files(base, claim, []))
    return failures


def _check_observability(claim: dict[str, Any], base: Path) -> list[str]:
    failures = []
    trace_ids = claim.get("trace_ids")
    if (
        not isinstance(trace_ids, list)
        or not trace_ids
        or not all(_nonempty_string(item) for item in trace_ids)
    ):
        failures.append("trace_ids must list at least one concrete trace id")
    failures.extend(_failures_for_files(base, claim, []))
    return failures


def _check_optimizer(claim: dict[str, Any], base: Path) -> list[str]:
    failures = []
    original = claim.get("original_instructions_file")
    optimized = claim.get("optimized_instructions_file")
    delta = _number(claim.get("measured_delta"))
    files = []
    if _nonempty_string(original):
        files.append(original)
    else:
        failures.append("original_instructions_file is required")
    if _nonempty_string(optimized):
        files.append(optimized)
    else:
        failures.append("optimized_instructions_file is required")
    if delta is None or delta == 0:
        failures.append("measured_delta must be a non-zero number")
    failures.extend(_failures_for_files(base, claim, files))
    original_path = _safe_evidence_path(base, original)
    optimized_path = _safe_evidence_path(base, optimized)
    if original_path and optimized_path and original_path.is_file() and optimized_path.is_file():
        original_text = original_path.read_text(encoding="utf-8", errors="replace")
        optimized_text = optimized_path.read_text(encoding="utf-8", errors="replace")
        if original_text == optimized_text:
            failures.append("optimized instructions must differ from original instructions")
    return failures


def _check_deployment(claim: dict[str, Any], base: Path) -> list[str]:
    failures = []
    for key in ("project_id", "region", "resource"):
        if not _nonempty_string(claim.get(key)):
            failures.append(f"{key} is required")
    rollback = claim.get("rollback_plan_file")
    files = [rollback] if _nonempty_string(rollback) else []
    if not files:
        failures.append("rollback_plan_file is required")
    failures.extend(_failures_for_files(base, claim, files))
    return failures


def _check_b2b(claim: dict[str, Any], base: Path) -> list[str]:
    failures = []
    for key in ("persona", "workflow", "measurable_outcome"):
        if not _nonempty_string(claim.get(key)):
            failures.append(f"{key} is required")
    for key in ("inputs", "actions"):
        values = claim.get(key)
        if (
            not isinstance(values, list)
            or not values
            or not all(_nonempty_string(item) for item in values)
        ):
            failures.append(f"{key} must list at least one concrete item")
    failures.extend(_failures_for_files(base, claim, []))
    return failures


CHECKERS = {
    "optimization_improvement": _check_optimization,
    "agent_simulation": _check_simulation,
    "agent_observability": _check_observability,
    "agent_optimizer": _check_optimizer,
    "live_google_cloud_deployment": _check_deployment,
    "b2b_value": _check_b2b,
}


def validate(manifest: dict[str, Any], evidence_dir: Path) -> dict[str, Any]:
    checks = []
    scenario = manifest.get("scenario")
    claims = manifest.get("claims")
    global_failures = []
    if not isinstance(scenario, dict):
        global_failures.append("scenario must be an object")
    else:
        for key in ("name", "b2b_persona", "business_workflow", "measurable_outcome"):
            if not _nonempty_string(scenario.get(key)):
                global_failures.append(f"scenario.{key} is required")
    if not isinstance(claims, dict):
        global_failures.append("claims must be an object")
        claims = {}

    for claim_name in REQUIRED_CLAIMS:
        claim, failures = _check_common(claim_name, claims.get(claim_name))
        if not failures:
            failures.extend(CHECKERS[claim_name](claim, evidence_dir))
        checks.append(
            {
                "claim": claim_name,
                "status": "fail" if failures else "pass",
                "failures": failures,
            }
        )

    passed = not global_failures and all(item["status"] == "pass" for item in checks)
    return {
        "gate": "google_agents_cli_track2_evidence",
        "passed": passed,
        "summary": {
            "total": len(checks),
            "passed": sum(1 for item in checks if item["status"] == "pass"),
            "failed": sum(1 for item in checks if item["status"] == "fail"),
        },
        "global_failures": global_failures,
        "checks": checks,
    }


def _error_report(message: str) -> dict[str, Any]:
    return {
        "gate": "google_agents_cli_track2_evidence",
        "passed": False,
        "summary": {
            "total": 0,
            "passed": 0,
            "failed": 1,
        },
        "global_failures": [message],
        "checks": [],
    }


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"manifest not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"manifest is not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise SystemExit("manifest must be a JSON object")
    return data


def _write_template(path: Path, force: bool) -> None:
    if path.exists() and not force:
        raise SystemExit(f"manifest already exists: {path}; use --force to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_template(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for dirname in ("eval", "simulation", "observability", "optimizer", "deploy", "business"):
        (path.parent / dirname).mkdir(exist_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence-dir",
        default=".demo/google-agents-cli-track2",
        help="Directory containing manifest.json and evidence artifacts",
    )
    parser.add_argument("--manifest", help="Manifest path; defaults to <evidence-dir>/manifest.json")
    parser.add_argument("--output", help="Write JSON gate report to this path")
    parser.add_argument("--write-template", action="store_true", help="Create a manifest template and exit")
    parser.add_argument("--force", action="store_true", help="Replace an existing template manifest")
    args = parser.parse_args(argv)

    evidence_dir = Path(args.evidence_dir).resolve()
    manifest_path = Path(args.manifest).resolve() if args.manifest else evidence_dir / "manifest.json"
    if args.write_template:
        _write_template(manifest_path, args.force)
        print(f"Wrote Track 2 evidence template: {manifest_path}")
        return 0

    try:
        report = validate(_load_json(manifest_path), evidence_dir)
    except SystemExit as exc:
        report = _error_report(str(exc))
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
