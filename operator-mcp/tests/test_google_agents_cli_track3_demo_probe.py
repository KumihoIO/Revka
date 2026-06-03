import json
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _script() -> Path:
    return _repo_root() / "scripts" / "demo" / "google_agents_cli_track3_demo_probe.py"


def test_track3_demo_probe_passes_all_source_outcomes(tmp_path):
    output = tmp_path / "track3-demo-probe.json"

    result = subprocess.run(
        [sys.executable, str(_script()), "--output", str(output)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["summary"] == {"failed": 0, "passed": 10, "total": 10}
    assert report["outcome_matrix"]["summary"] == {
        "failed": 0,
        "passed": 8,
        "total": 8,
    }
    titles = [item["title"] for item in report["outcome_matrix"]["outcomes"]]
    assert titles == [
        "Cloud Run runtime readiness",
        "Registration-ready A2A discovery",
        "Live A2A incident plan",
        "A2A task lifecycle branches",
        "Demo-safe error branches",
        "Production operating controls",
        "B2B governance story",
        "Final rehearsal gate alignment",
    ]
