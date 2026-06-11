"""ADK coder agent — implements GitHub issue fixes and opens PRs.

Runs on Cloud Run, called over A2A by the Revka workflow engine.
Gemini is consumed through Vertex AI with Application Default Credentials
(no API keys). The Cloud Run service account needs roles/aiplatform.user.
"""
from __future__ import annotations

import json
import os
import subprocess

# Vertex AI via ADC — must be set before google.adk/google.genai imports.
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "construct-498201")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "us-central1")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")

import httpx
from google.adk.agents import Agent
from google.adk.models import Gemini

APP_NAME = "coder-agent"
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-2.5-pro")
GITHUB_API = "https://api.github.com"

# Maximum bytes of tool output returned to the model.
MAX_TOOL_OUTPUT = 16_000

# Per-task workspace. The A2A server sets this before each run via
# set_workspace() and serializes task execution, so a plain module global is
# safe (and, unlike a ContextVar, survives ADK running sync tools in threads).
_WORKSPACE = "/tmp"


def set_workspace(path: str) -> None:
    """Bind the workspace directory for the current task (called by a2a_server)."""
    global _WORKSPACE
    _WORKSPACE = path


def _workspace() -> str:
    return _WORKSPACE


def _redact(text: str) -> str:
    """Never echo the GitHub token back to the model or logs."""
    token = os.getenv("GITHUB_TOKEN", "")
    if token:
        text = text.replace(token, "***GITHUB_TOKEN***")
    return text


def _clip(text: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


def run_shell(command: str) -> str:
    """Run a shell command inside the task workspace and return its output.

    The GITHUB_TOKEN environment variable is available, e.g. for
    `git clone https://x-access-token:$GITHUB_TOKEN@github.com/owner/repo.git repo`.

    Args:
        command: The shell command to execute (bash).

    Returns:
        JSON string: {"exit_code": int, "stdout": str, "stderr": str}.
    """
    try:
        proc = subprocess.run(
            ["bash", "-c", command],
            cwd=_workspace(),
            capture_output=True,
            text=True,
            timeout=600,
        )
        result = {
            "exit_code": proc.returncode,
            "stdout": _clip(_redact(proc.stdout)),
            "stderr": _clip(_redact(proc.stderr)),
        }
    except subprocess.TimeoutExpired:
        result = {"exit_code": -1, "stdout": "", "stderr": "command timed out after 600s"}
    except Exception as exc:  # noqa: BLE001 - surface tool failure to the model
        result = {"exit_code": -1, "stdout": "", "stderr": _redact(str(exc))}
    return json.dumps(result)


def read_file(path: str) -> str:
    """Read a text file relative to the task workspace.

    Args:
        path: File path relative to the workspace (e.g. "repo/src/main.py").

    Returns:
        The file content, or an error message starting with "ERROR:".
    """
    full = os.path.normpath(os.path.join(_workspace(), path))
    if not full.startswith(os.path.normpath(_workspace())):
        return "ERROR: path escapes the workspace"
    try:
        with open(full, encoding="utf-8", errors="replace") as fh:
            return _clip(fh.read())
    except OSError as exc:
        return f"ERROR: {exc}"


def write_file(path: str, content: str) -> str:
    """Write a text file relative to the task workspace, creating parent dirs.

    Args:
        path: File path relative to the workspace (e.g. "repo/src/fix.py").
        content: Full file content to write.

    Returns:
        "ok" on success, or an error message starting with "ERROR:".
    """
    full = os.path.normpath(os.path.join(_workspace(), path))
    if not full.startswith(os.path.normpath(_workspace())):
        return "ERROR: path escapes the workspace"
    try:
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)
        return "ok"
    except OSError as exc:
        return f"ERROR: {exc}"


def github_merge_pr(repo_name: str, pr_number: int, commit_title: str = "", merge_method: str = "squash") -> str:
    """Merge a GitHub pull request via the REST API using GITHUB_TOKEN.

    Args:
        repo_name: "owner/repo".
        pr_number: Pull request number to merge.
        commit_title: Optional merge commit title.
        merge_method: "squash" (default), "merge", or "rebase".

    Returns:
        JSON string: {"merged": bool, "sha": str} or {"error": str}.
    """
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        return json.dumps({"error": "GITHUB_TOKEN is not set"})
    try:
        body: dict[str, str] = {"merge_method": merge_method}
        if commit_title:
            body["commit_title"] = commit_title
        resp = httpx.put(
            f"{GITHUB_API}/repos/{repo_name}/pulls/{pr_number}/merge",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json=body,
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            return json.dumps({"merged": bool(data.get("merged")), "sha": data.get("sha", "")})
        return json.dumps({"error": _redact(f"HTTP {resp.status_code}: {resp.text[:500]}")})
    except Exception as exc:  # noqa: BLE001 - surface tool failure to the model
        return json.dumps({"error": _redact(str(exc))})


def github_comment_and_close_issue(repo_name: str, issue_number: int, comment: str) -> str:
    """Comment on a GitHub issue and close it via the REST API using GITHUB_TOKEN.

    Args:
        repo_name: "owner/repo".
        issue_number: Issue number to comment on and close.
        comment: Markdown comment body to post before closing.

    Returns:
        JSON string: {"comment_url": str, "closed": bool} or {"error": str}.
    """
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        return json.dumps({"error": "GITHUB_TOKEN is not set"})
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        comment_url = ""
        if comment:
            c_resp = httpx.post(
                f"{GITHUB_API}/repos/{repo_name}/issues/{issue_number}/comments",
                headers=headers,
                json={"body": comment},
                timeout=30,
            )
            if c_resp.status_code in (200, 201):
                comment_url = c_resp.json().get("html_url", "")
            else:
                return json.dumps({"error": _redact(f"comment HTTP {c_resp.status_code}: {c_resp.text[:500]}")})
        x_resp = httpx.patch(
            f"{GITHUB_API}/repos/{repo_name}/issues/{issue_number}",
            headers=headers,
            json={"state": "closed"},
            timeout=30,
        )
        if x_resp.status_code == 200:
            return json.dumps({"comment_url": comment_url, "closed": x_resp.json().get("state") == "closed"})
        return json.dumps({"error": _redact(f"close HTTP {x_resp.status_code}: {x_resp.text[:500]}")})
    except Exception as exc:  # noqa: BLE001 - surface tool failure to the model
        return json.dumps({"error": _redact(str(exc))})


def github_open_pr(repo_name: str, head_branch: str, base_branch: str, title: str, body: str) -> str:
    """Open a GitHub pull request via the REST API using GITHUB_TOKEN.

    Args:
        repo_name: "owner/repo".
        head_branch: Branch containing the fix (already pushed).
        base_branch: Target branch (usually "main").
        title: PR title.
        body: PR description (markdown).

    Returns:
        JSON string: {"pr_url": str, "number": int} or {"error": str}.
    """
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        return json.dumps({"error": "GITHUB_TOKEN is not set"})
    try:
        resp = httpx.post(
            f"{GITHUB_API}/repos/{repo_name}/pulls",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"title": title, "head": head_branch, "base": base_branch, "body": body},
            timeout=30,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            return json.dumps({"pr_url": data.get("html_url", ""), "number": data.get("number")})
        return json.dumps({"error": _redact(f"HTTP {resp.status_code}: {resp.text[:500]}")})
    except Exception as exc:  # noqa: BLE001 - surface tool failure to the model
        return json.dumps({"error": _redact(str(exc))})


INSTRUCTION = """\
You are an autonomous coding agent. Each task message is a JSON object.

If the JSON has "action": "merge", you are in MERGE mode:
{"action": "merge", "repo_name": "owner/repo", "pr_number": 7,
 "issue_number": 123, "run_id": "..."}
In MERGE mode do exactly this and nothing else:
1. Call github_merge_pr(repo_name, pr_number, "<short title>", "squash").
2. If the merge succeeded, call github_comment_and_close_issue(repo_name,
   issue_number, "Resolved by Revka cloud workflow run <run_id>. Merged PR
   #<pr_number>. Reviewed and merged by the Google Cloud A2A executors.").
3. Respond with ONLY this JSON (no markdown, no prose):
   {"merge_status": "merged|failed", "issue_closed": true|false,
    "merged_pr_url": "https://github.com/<repo_name>/pull/<pr_number>",
    "audit_summary": "<one line>"}
Do NOT clone, edit, or open a PR in MERGE mode.

Otherwise you are in FIX mode. The task JSON is:
{"repo_name": "owner/repo", "issue_number": 123, "issue_title": "...",
 "issue_body": "...", "strategy": "how to implement the fix"}

Work entirely inside your workspace using your tools. Steps:

1. Clone the repository into a subdirectory named "repo":
   run_shell('git clone --depth 50 "https://x-access-token:$GITHUB_TOKEN@github.com/<repo_name>.git" repo')
2. Configure git identity:
   run_shell('cd repo && git config user.name "revka-coder-agent" && git config user.email "coder-agent@construct-498201.iam.gserviceaccount.com"')
3. Detect the default branch (run_shell('cd repo && git rev-parse --abbrev-ref HEAD')).
4. Create a branch named fix/issue-<issue_number>.
5. Explore the code (run_shell with ls/grep, read_file), then implement the fix
   following the provided strategy. Use write_file for edits. Keep the change minimal.
6. If the repo has Python tests (pytest.ini, pyproject with pytest, or a tests/ dir),
   run them: run_shell('cd repo && python3 -m pytest -x -q'). Record the result.
   If no test command is detectable, record test_status as "skipped".
7. Commit and push:
   run_shell('cd repo && git add -A && git commit -m "fix: <short description> (#<issue_number>)" && git push origin fix/issue-<issue_number>')
8. Open a PR with github_open_pr(repo_name, "fix/issue-<issue_number>", "<default branch>",
   "fix: <issue_title> (#<issue_number>)", "<summary of the change>\\n\\nFixes #<issue_number>").

When finished, respond with ONLY a JSON object (no markdown fences, no prose):
{"pr_url": "<url or empty>", "branch": "fix/issue-<n>", "summary": "<what was changed>",
 "test_status": "passed|failed|skipped"}

If a step fails irrecoverably, still respond with that JSON, an empty pr_url,
and a summary explaining the failure.
"""

root_agent = Agent(
    name="coder_agent",
    model=Gemini(model=MODEL_NAME),
    description="Implements GitHub issue fixes and opens pull requests.",
    instruction=INSTRUCTION,
    tools=[run_shell, read_file, write_file, github_open_pr, github_merge_pr, github_comment_and_close_issue],
)
