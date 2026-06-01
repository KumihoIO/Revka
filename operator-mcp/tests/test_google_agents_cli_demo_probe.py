import json
import subprocess
import sys
from pathlib import Path


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
    assert {
        item["name"]
        for item in bundle["results"]
    } >= {
        "architecture_guardrails",
        "info",
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
    }
