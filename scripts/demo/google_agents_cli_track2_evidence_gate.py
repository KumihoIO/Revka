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
    "existing_agent_baseline",
    "optimization_improvement",
    "agent_simulation",
    "agent_observability",
    "agent_optimizer",
    "live_google_cloud_deployment",
    "mandatory_google_platform",
    "b2b_value",
)

PLACEHOLDER_EVIDENCE_TEXT = {
    "",
    "evidence",
    "placeholder",
    "todo",
    "todo:",
    "replace me",
    "replace-me",
    "sample",
}


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
            "existing_agent_baseline": {
                "agent_name": "TODO: existing sandbox agent name",
                "normal_case": "TODO: normal scenario the existing agent already handles",
                "edge_case": "TODO: pre-optimization edge case that fails",
                "normal_case_evidence": "baseline/normal-case.json",
                "edge_case_evidence": "baseline/edge-case.json",
                "evidence_files": [
                    "baseline/existing-agent.md",
                ],
            },
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
            "mandatory_google_platform": {
                "intelligence": "TODO: Gemini API or third-party LLM through Agent Platform",
                "orchestration": "TODO: ADK, LangChain, or CrewAI managed on Agent Platform",
                "infrastructure": "TODO: Google Cloud runtime such as Agent Runtime, Cloud Run, or GKE",
                "evidence_files": [
                    "platform/architecture.md",
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


def _capture_plan_text() -> str:
    return """# Google Agents CLI Track 2 Evidence Capture Plan

This file is a working checklist for the real demo rehearsal. Do not treat it
as evidence. Replace the scaffold manifest values and capture the artifacts
listed below before running the strict pre-recording gate.

## Final Gate

```bash
agents-cli login -i
python3 scripts/demo/google_agents_cli_pre_recording_gate.py \\
  --evidence-dir .demo/google-agents-cli-track2 \\
  --require-real-agents-cli-auth \\
  --require-strict-final-ready \\
  --pr-number 324 \\
  --output /tmp/google_agents_cli_pre_recording_gate.json
```

The final report must say `strict_final_recording_ready: true`.
When `--pr-number` is set, the umbrella gate also verifies the local branch is
clean, contains the configured base ref, and has no upstream divergence. Do not
use `--skip-local-git-state` for final recording readiness.

## Scenario

Fill these fields in `manifest.json` with the exact business story shown in the
recording:

- `scenario.name`
- `scenario.b2b_persona`
- `scenario.business_workflow`
- `scenario.measurable_outcome`
- Gate invariant: the evidence corpus must mention each scenario field, so the
  manifest cannot describe a different story than the artifacts prove.

## Required Claims And Artifacts

### existing_agent_baseline

- Manifest: set `agent_name`, `normal_case`, `edge_case`,
  `normal_case_evidence`, and `edge_case_evidence`.
- Files:
  - `baseline/existing-agent.md` must explain the existing agent before
    optimization.
  - `baseline/normal-case.json` must contain boolean `passed: true` for the
    normal sandbox case.
  - `baseline/edge-case.json` must contain boolean `passed: false` or
    `failed: true` for the pre-optimization edge case.
- Gate invariant: evidence must mention the manifest agent name, normal case,
  and edge case so the demo cannot pass as a net-new agent build.

### optimization_improvement

- Manifest: set `metric_name`, numeric `before`, numeric `after`, and
  `higher_is_better`.
- Files:
  - `eval/baseline.json` must contain the metric named by `metric_name` with
    the same value as `before`.
  - `eval/optimized.json` must contain the same metric with the same value as
    `after`.
- Gate invariant: `after` must improve over `before` according to
  `higher_is_better`.

### agent_simulation

- Manifest: set `scenario_count` to the number of synthetic scenarios and list
  every edge case shown in the video.
- File: `simulation/run-output.json` must contain numeric `scenario_count` and
  mention each edge-case string from the manifest.
- Gate invariant: artifact `scenario_count` must be at least the manifest
  `scenario_count`, and the artifact must mention simulation or synthetic
  scenarios.

### agent_observability

- Manifest: list concrete trace IDs captured from Agent Observability or the
  runtime trace.
- File: `observability/trace.jsonl` must be valid non-empty JSONL and contain
  every trace ID from the manifest.
- Gate invariant: trace evidence must show details such as tool calls,
  reasoning, retries, decisions, or conflict resolution.

### agent_optimizer

- Manifest: set `original_instructions_file`,
  `optimized_instructions_file`, and non-zero numeric `measured_delta`.
- Files:
  - `optimizer/original-instructions.md`
  - `optimizer/optimized-instructions.md`
  - `optimizer/result.json` with numeric `measured_delta` and evidence of
    `agents-cli eval optimize`, `adk optimize`, or Agent Optimizer
- Gate invariant: optimized instructions must differ from original
  instructions, and result `measured_delta` must match the manifest.

### live_google_cloud_deployment

- Manifest: set `project_id`, `region`, `resource`, and
  `rollback_plan_file`.
- Files:
  - `deploy/deploy-output.txt` must mention the manifest project, region, and
    resource.
  - `deploy/rollback-plan.md` must describe rollback.

### mandatory_google_platform

- Manifest: set `intelligence`, `orchestration`, and `infrastructure`.
- File: `platform/architecture.md` must mention the manifest values and:
  - Gemini, or a third-party LLM deployed through Agent Platform
  - ADK, LangChain, or CrewAI orchestration
  - Google Cloud
  - Agent Runtime, Cloud Run, or GKE

### b2b_value

- Manifest: set `persona`, `workflow`, `inputs`, `actions`, and
  `measurable_outcome`.
- File: `business/use-case.md` must be a concrete narrative of at least 25
  words and mention the persona, workflow, every input, every action, and the
  measurable outcome from the manifest.

## Notes

- Evidence files must stay under `.demo/google-agents-cli-track2`.
- JSON files must parse as JSON; JSONL files must contain at least one valid
  JSON line.
- Placeholder-only text such as `TODO`, `placeholder`, `sample`, or
  `replace me` fails the gate.
"""


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


def _placeholder_file_failure(full: Path, rel: str) -> str | None:
    if full.stat().st_size > 4096:
        return None
    try:
        text = full.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    normalized = " ".join(text.strip().lower().split())
    if normalized in PLACEHOLDER_EVIDENCE_TEXT or normalized.startswith("todo:"):
        return f"placeholder evidence file: {rel}"
    return None


def _structured_file_failure(full: Path, rel: str) -> str | None:
    if full.suffix == ".json":
        try:
            json.loads(full.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return f"invalid JSON evidence file {rel}: {exc}"
    if full.suffix == ".jsonl":
        try:
            lines = [
                line
                for line in full.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            for line in lines:
                json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return f"invalid JSONL evidence file {rel}: {exc}"
        if not lines:
            return f"empty JSONL evidence file: {rel}"
    return None


def _safe_text(base: Path, rel: Any) -> str:
    path = _safe_evidence_path(base, rel)
    if not path or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _safe_json(base: Path, rel: Any) -> Any | None:
    path = _safe_evidence_path(base, rel)
    if not path or not path.is_file() or path.suffix != ".json":
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _numeric_by_key(value: Any, key: str) -> float | None:
    if isinstance(value, dict):
        for dict_key, dict_value in value.items():
            if dict_key == key:
                number = _number(dict_value)
                if number is not None:
                    return number
            nested = _numeric_by_key(dict_value, key)
            if nested is not None:
                return nested
    elif isinstance(value, list):
        for item in value:
            nested = _numeric_by_key(item, key)
            if nested is not None:
                return nested
    return None


def _bool_by_key(value: Any, key: str) -> bool | None:
    if isinstance(value, dict):
        for dict_key, dict_value in value.items():
            if dict_key == key and isinstance(dict_value, bool):
                return dict_value
            nested = _bool_by_key(dict_value, key)
            if nested is not None:
                return nested
    elif isinstance(value, list):
        for item in value:
            nested = _bool_by_key(item, key)
            if nested is not None:
                return nested
    return None


def _numbers_close(left: float, right: float) -> bool:
    return abs(left - right) <= 0.000_001


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _contains_all(text: str, terms: tuple[str, ...]) -> bool:
    return all(term in text for term in terms)


def _claim_files(claim: dict[str, Any]) -> list[str]:
    files = claim.get("evidence_files")
    if isinstance(files, list) and all(isinstance(item, str) for item in files):
        return files
    return []


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
            continue
        placeholder_failure = _placeholder_file_failure(full, rel)
        if placeholder_failure:
            failures.append(placeholder_failure)
            continue
        structured_failure = _structured_file_failure(full, rel)
        if structured_failure:
            failures.append(structured_failure)
    return failures


def _safe_evidence_path(base: Path, rel: Any) -> Path | None:
    if not _nonempty_string(rel):
        return None
    path = Path(rel)
    if path.is_absolute() or ".." in path.parts:
        return None
    return base / path


def _manifest_evidence_files(claims: dict[str, Any]) -> list[str]:
    files: list[str] = []
    for claim in claims.values():
        if not isinstance(claim, dict):
            continue
        for rel in _claim_files(claim):
            if rel not in files:
                files.append(rel)
        for key in (
            "normal_case_evidence",
            "edge_case_evidence",
            "original_instructions_file",
            "optimized_instructions_file",
            "rollback_plan_file",
        ):
            rel = claim.get(key)
            if isinstance(rel, str) and rel not in files:
                files.append(rel)
    return files


def _check_scenario_alignment(
    scenario: dict[str, Any],
    claims: dict[str, Any],
    base: Path,
) -> list[str]:
    failures = []
    evidence_text = "\n".join(
        _safe_text(base, rel) for rel in _manifest_evidence_files(claims)
    ).lower()
    for key in ("name", "b2b_persona", "business_workflow", "measurable_outcome"):
        value = scenario.get(key)
        if _nonempty_string(value) and value.lower() not in evidence_text:
            failures.append(f"scenario evidence does not mention scenario.{key}: {value}")
    return failures


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


def _check_existing_agent_baseline(claim: dict[str, Any], base: Path) -> list[str]:
    failures = []
    for key in ("agent_name", "normal_case", "edge_case"):
        if not _nonempty_string(claim.get(key)):
            failures.append(f"{key} is required")

    normal_case_evidence = claim.get("normal_case_evidence")
    edge_case_evidence = claim.get("edge_case_evidence")
    files = []
    if _nonempty_string(normal_case_evidence):
        files.append(normal_case_evidence)
    else:
        failures.append("normal_case_evidence is required")
    if _nonempty_string(edge_case_evidence):
        files.append(edge_case_evidence)
    else:
        failures.append("edge_case_evidence is required")

    failures.extend(_failures_for_files(base, claim, files))

    if _nonempty_string(normal_case_evidence):
        normal_passed = _bool_by_key(_safe_json(base, normal_case_evidence), "passed")
        if normal_passed is not True:
            failures.append(
                f"{normal_case_evidence} must contain boolean passed=true for the sandbox case"
            )
    if _nonempty_string(edge_case_evidence):
        edge_passed = _bool_by_key(_safe_json(base, edge_case_evidence), "passed")
        edge_failed = _bool_by_key(_safe_json(base, edge_case_evidence), "failed")
        if edge_passed is not False and edge_failed is not True:
            failures.append(
                f"{edge_case_evidence} must contain boolean passed=false or failed=true "
                "for the pre-optimization edge case"
            )

    evidence_text = "\n".join(
        _safe_text(base, rel) for rel in [*files, *_claim_files(claim)]
    ).lower()
    for key in ("agent_name", "normal_case", "edge_case"):
        value = claim.get(key)
        if _nonempty_string(value) and value.lower() not in evidence_text:
            failures.append(f"existing-agent evidence does not mention {key}: {value}")
    return failures


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
    evidence_files = _claim_files(claim)
    if len(evidence_files) < 2:
        failures.append("optimization evidence_files must include baseline and optimized JSON files")
    elif _nonempty_string(metric) and before is not None and after is not None:
        baseline_metric = _numeric_by_key(_safe_json(base, evidence_files[0]), metric)
        optimized_metric = _numeric_by_key(_safe_json(base, evidence_files[1]), metric)
        if baseline_metric is None:
            failures.append(f"{evidence_files[0]} must contain numeric metric '{metric}'")
        elif not _numbers_close(baseline_metric, before):
            failures.append(f"{evidence_files[0]} metric '{metric}' does not match before")
        if optimized_metric is None:
            failures.append(f"{evidence_files[1]} must contain numeric metric '{metric}'")
        elif not _numbers_close(optimized_metric, after):
            failures.append(f"{evidence_files[1]} metric '{metric}' does not match after")
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
    evidence_files = _claim_files(claim)
    if evidence_files:
        artifact_count = _numeric_by_key(_safe_json(base, evidence_files[0]), "scenario_count")
        if artifact_count is None:
            failures.append(f"{evidence_files[0]} must contain numeric scenario_count")
        elif isinstance(count, int) and artifact_count < count:
            failures.append(f"{evidence_files[0]} scenario_count is lower than manifest claim")
        artifact_text = "\n".join(_safe_text(base, rel) for rel in evidence_files).lower()
        if artifact_text and not _contains_any(artifact_text, ("simulation", "synthetic")):
            failures.append("simulation evidence must mention simulation or synthetic scenarios")
        if isinstance(edge_cases, list):
            for edge_case in edge_cases:
                if _nonempty_string(edge_case) and edge_case.lower() not in artifact_text:
                    failures.append(f"simulation evidence does not mention edge case: {edge_case}")
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
    trace_text = "\n".join(_safe_text(base, rel) for rel in _claim_files(claim))
    if isinstance(trace_ids, list):
        for trace_id in trace_ids:
            if _nonempty_string(trace_id) and trace_id not in trace_text:
                failures.append(f"observability evidence does not contain trace id: {trace_id}")
    trace_text_lower = trace_text.lower()
    if trace_text_lower and not _contains_any(
        trace_text_lower,
        ("tool", "reasoning", "decision", "conflict", "retry"),
    ):
        failures.append(
            "observability evidence must show trace details such as tool calls, "
            "reasoning, retries, or conflict resolution"
        )
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
    for rel in _claim_files(claim):
        result_delta = _numeric_by_key(_safe_json(base, rel), "measured_delta")
        if result_delta is None:
            failures.append(f"{rel} must contain numeric measured_delta")
        elif delta is not None and not _numbers_close(result_delta, delta):
            failures.append(f"{rel} measured_delta does not match manifest claim")
    optimizer_text = "\n".join(_safe_text(base, rel) for rel in _claim_files(claim)).lower()
    if optimizer_text and not (
        _contains_all(optimizer_text, ("agents-cli", "eval", "optimize"))
        or _contains_all(optimizer_text, ("adk", "optimize"))
        or "agent optimizer" in optimizer_text
    ):
        failures.append(
            "optimizer evidence must mention agents-cli eval optimize, adk optimize, "
            "or Agent Optimizer"
        )
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
    deploy_text = "\n".join(_safe_text(base, rel) for rel in _claim_files(claim)).lower()
    for key in ("project_id", "region", "resource"):
        value = claim.get(key)
        if _nonempty_string(value) and value.lower() not in deploy_text:
            failures.append(f"deployment evidence does not mention {key}: {value}")
    rollback_text = _safe_text(base, rollback).lower()
    if _nonempty_string(rollback) and "rollback" not in rollback_text:
        failures.append("rollback_plan_file must describe rollback")
    return failures


def _check_platform(claim: dict[str, Any], base: Path) -> list[str]:
    failures = []
    for key in ("intelligence", "orchestration", "infrastructure"):
        if not _nonempty_string(claim.get(key)):
            failures.append(f"{key} is required")
    failures.extend(_failures_for_files(base, claim, []))

    evidence_text = "\n".join(_safe_text(base, rel) for rel in _claim_files(claim)).lower()
    if evidence_text:
        intelligence_ok = "gemini" in evidence_text or (
            "agent platform" in evidence_text
            and "llm" in evidence_text
            and _contains_any(evidence_text, ("third-party", "third party"))
        )
        if not intelligence_ok:
            failures.append(
                "platform evidence must mention Gemini or a third-party LLM deployed through Agent Platform"
            )
        if not _contains_any(
            evidence_text,
            ("agent development kit", "adk", "langchain", "crewai"),
        ):
            failures.append(
                "platform evidence must mention ADK, LangChain, or CrewAI orchestration"
            )
        if "google cloud" not in evidence_text:
            failures.append("platform evidence must mention Google Cloud")
        if not _contains_any(evidence_text, ("agent runtime", "cloud run", "gke")):
            failures.append(
                "platform evidence must mention Agent Runtime, Cloud Run, or GKE"
            )
        for key in ("intelligence", "orchestration", "infrastructure"):
            value = claim.get(key)
            if _nonempty_string(value) and value.lower() not in evidence_text:
                failures.append(f"platform evidence does not mention {key}: {value}")
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
    b2b_text = "\n".join(_safe_text(base, rel) for rel in _claim_files(claim)).lower()
    if b2b_text and len(b2b_text.split()) < 25:
        failures.append("b2b evidence must be a concrete narrative, not a one-line stub")
    for key in ("persona", "workflow", "measurable_outcome"):
        value = claim.get(key)
        if _nonempty_string(value) and value.lower() not in b2b_text:
            failures.append(f"b2b evidence does not mention {key}: {value}")
    inputs = claim.get("inputs")
    if isinstance(inputs, list):
        for input_name in inputs:
            if _nonempty_string(input_name) and input_name.lower() not in b2b_text:
                failures.append(f"b2b evidence does not mention input: {input_name}")
    actions = claim.get("actions")
    if isinstance(actions, list):
        for action in actions:
            if _nonempty_string(action) and action.lower() not in b2b_text:
                failures.append(f"b2b evidence does not mention action: {action}")
    return failures


CHECKERS = {
    "existing_agent_baseline": _check_existing_agent_baseline,
    "optimization_improvement": _check_optimization,
    "agent_simulation": _check_simulation,
    "agent_observability": _check_observability,
    "agent_optimizer": _check_optimizer,
    "live_google_cloud_deployment": _check_deployment,
    "mandatory_google_platform": _check_platform,
    "b2b_value": _check_b2b,
}


def validate(manifest: dict[str, Any], evidence_dir: Path) -> dict[str, Any]:
    checks = []
    scenario = manifest.get("scenario")
    claims = manifest.get("claims")
    global_failures = []
    if manifest.get("schema_version") != 1:
        global_failures.append("schema_version must be 1")
    if not isinstance(scenario, dict):
        global_failures.append("scenario must be an object")
    else:
        for key in ("name", "b2b_persona", "business_workflow", "measurable_outcome"):
            if not _nonempty_string(scenario.get(key)):
                global_failures.append(f"scenario.{key} is required")
    if not isinstance(claims, dict):
        global_failures.append("claims must be an object")
        claims = {}
    elif isinstance(scenario, dict):
        global_failures.extend(_check_scenario_alignment(scenario, claims, evidence_dir))

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
    for dirname in (
        "baseline",
        "eval",
        "simulation",
        "observability",
        "optimizer",
        "deploy",
        "platform",
        "business",
    ):
        (path.parent / dirname).mkdir(exist_ok=True)


def _write_capture_plan(path: Path, force: bool) -> None:
    if path.exists() and not force:
        raise SystemExit(f"capture plan already exists: {path}; use --force to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_capture_plan_text(), encoding="utf-8")


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
    parser.add_argument(
        "--write-capture-plan",
        action="store_true",
        help="Create a markdown checklist for collecting real Track 2 artifacts and exit",
    )
    parser.add_argument(
        "--capture-plan",
        help="Capture-plan path; defaults to <evidence-dir>/capture-plan.md",
    )
    parser.add_argument("--force", action="store_true", help="Replace an existing template manifest")
    args = parser.parse_args(argv)

    evidence_dir = Path(args.evidence_dir).resolve()
    manifest_path = Path(args.manifest).resolve() if args.manifest else evidence_dir / "manifest.json"
    capture_plan_path = (
        Path(args.capture_plan).resolve()
        if args.capture_plan
        else evidence_dir / "capture-plan.md"
    )
    if args.write_template:
        _write_template(manifest_path, args.force)
        print(f"Wrote Track 2 evidence template: {manifest_path}")
    if args.write_capture_plan:
        _write_capture_plan(capture_plan_path, args.force)
        print(f"Wrote Track 2 evidence capture plan: {capture_plan_path}")
    if args.write_template or args.write_capture_plan:
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
