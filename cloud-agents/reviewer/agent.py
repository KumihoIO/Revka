"""ADK reviewer agent — reviews GitHub pull requests.

Runs on Cloud Run, called over A2A by the Revka workflow engine.
Gemini is consumed through Vertex AI with Application Default Credentials
(no API keys). The Cloud Run service account needs roles/aiplatform.user.
"""
from __future__ import annotations

import json
import os

# Vertex AI via ADC — must be set before google.adk/google.genai imports.
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "construct-498201")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "us-central1")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")

import httpx
from google.adk.agents import Agent
from google.adk.models import Gemini

APP_NAME = "reviewer-agent"
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-2.5-pro")
GITHUB_API = "https://api.github.com"

MAX_DIFF_CHARS = 60_000


def _headers(accept: str = "application/vnd.github+json") -> dict[str, str]:
    headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
    token = os.getenv("GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def github_get_pr(repo_name: str, pr_number: int) -> str:
    """Fetch pull request metadata from the GitHub REST API.

    Args:
        repo_name: "owner/repo".
        pr_number: Pull request number.

    Returns:
        JSON string with title, body, state, base/head branches, changed_files,
        additions, deletions — or {"error": str}.
    """
    try:
        resp = httpx.get(
            f"{GITHUB_API}/repos/{repo_name}/pulls/{pr_number}",
            headers=_headers(),
            timeout=30,
        )
        if resp.status_code != 200:
            return json.dumps({"error": f"HTTP {resp.status_code}: {resp.text[:500]}"})
        data = resp.json()
        return json.dumps(
            {
                "title": data.get("title", ""),
                "body": (data.get("body") or "")[:4000],
                "state": data.get("state", ""),
                "base": data.get("base", {}).get("ref", ""),
                "head": data.get("head", {}).get("ref", ""),
                "changed_files": data.get("changed_files", 0),
                "additions": data.get("additions", 0),
                "deletions": data.get("deletions", 0),
                "html_url": data.get("html_url", ""),
            }
        )
    except Exception as exc:  # noqa: BLE001 - surface tool failure to the model
        return json.dumps({"error": str(exc)})


def github_get_pr_diff(repo_name: str, pr_number: int) -> str:
    """Fetch the unified diff of a pull request from the GitHub REST API.

    Args:
        repo_name: "owner/repo".
        pr_number: Pull request number.

    Returns:
        The unified diff text (truncated to 60k chars), or "ERROR: ...".
    """
    try:
        resp = httpx.get(
            f"{GITHUB_API}/repos/{repo_name}/pulls/{pr_number}",
            headers=_headers(accept="application/vnd.github.v3.diff"),
            timeout=30,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return f"ERROR: HTTP {resp.status_code}: {resp.text[:500]}"
        diff = resp.text
        if len(diff) > MAX_DIFF_CHARS:
            diff = diff[:MAX_DIFF_CHARS] + f"\n...[diff truncated at {MAX_DIFF_CHARS} chars]"
        return diff
    except Exception as exc:  # noqa: BLE001 - surface tool failure to the model
        return f"ERROR: {exc}"


INSTRUCTION = """\
You are a rigorous code review agent. Each task message is a JSON object:
{"repo_name": "owner/repo", "pr_number": 123}

Steps:
1. Call github_get_pr(repo_name, pr_number) for context (title, description, size).
2. Call github_get_pr_diff(repo_name, pr_number) and read the full diff.
3. Review the change for:
   - Correctness: logic errors, edge cases, broken behavior, API misuse.
   - Safety: security issues (injection, secrets in code, unsafe shell/file
     operations), data loss risks, missing input validation.
   - Test coverage: are the changes covered by new or existing tests?

Decide "approved" only when there are no correctness or safety problems and
test coverage is acceptable for the size of the change; otherwise "needs_changes".

When finished, respond with ONLY a JSON object (no markdown fences, no prose):
{"review_status": "approved" or "needs_changes",
 "findings": ["<finding 1>", "<finding 2>", ...],
 "summary": "<one-paragraph review summary>"}

Each finding should name the file/area and the concrete problem or risk.
If the PR cannot be fetched, return review_status "needs_changes" with a
finding explaining the fetch failure.
"""

root_agent = Agent(
    name="reviewer_agent",
    model=Gemini(model=MODEL_NAME),
    description="Reviews GitHub pull requests for correctness, safety, and test coverage.",
    instruction=INSTRUCTION,
    tools=[github_get_pr, github_get_pr_diff],
)
