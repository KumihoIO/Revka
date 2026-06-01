import json
import subprocess
import sys
from pathlib import Path


def _plain_title(value: str) -> str:
    return value.replace("`", "")


def _read_demo_outcome_doc_titles(repo_root: Path) -> list[str]:
    doc = repo_root / "docs" / "ops" / "google-agents-cli-demo-readiness.md"
    lines = doc.read_text(encoding="utf-8").splitlines()
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


def test_google_agents_cli_demo_probe_generates_passing_evidence_bundle(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "demo" / "google_agents_cli_demo_probe.py"
    output = tmp_path / "probe.json"

    result = subprocess.run(
        [sys.executable, str(script), "--quiet", "--output", str(output)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    bundle = json.loads(output.read_text(encoding="utf-8"))
    assert bundle["passed"] is True
    assert bundle["summary"]["failed"] == 0
    assert bundle["outcome_matrix"]["summary"] == {
        "failed": 0,
        "passed": 15,
        "total": 15,
    }
    assert {
        item["name"]
        for item in bundle["results"]
    } >= {
        "architecture_guardrails",
        "info",
        "successful_lifecycle",
        "prompt_run",
        "eval_failure",
        "invalid_command",
        "interactive_login",
        "bad_working_directory",
        "timeout",
        "truncation",
        "enterprise_env",
        "deploy_acceptance",
        "missing_binary",
        "spawn_failure",
        "runtime_safety_policy",
    }
    assert {
        item["id"]
        for item in bundle["outcome_matrix"]["outcomes"]
    } >= {
        "existing_agent_tool_capability",
        "cli_project_tooling_inspection",
        "prompt_only_run",
        "successful_lifecycle_command",
        "cli_failure",
        "missing_agents_cli_binary",
        "malformed_command_input",
        "interactive_login_attempt",
        "bad_working_directory",
        "timeout",
        "large_output",
        "spawn_failure",
        "gemini_enterprise_publish_context",
        "runtime_safety_policy",
        "deploy_command_acceptance",
    }
    assert [
        _plain_title(item["title"])
        for item in bundle["outcome_matrix"]["outcomes"]
    ] == _read_demo_outcome_doc_titles(repo_root)
