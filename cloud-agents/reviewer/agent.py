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
import google.auth
import google.auth.transport.requests
from google.adk.agents import Agent
from google.adk.models import Gemini

APP_NAME = "reviewer-agent"
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-2.5-pro")
GITHUB_API = "https://api.github.com"

MAX_DIFF_CHARS = 60_000

# Vertex AI Search (Discovery Engine) data store holding the repo's coding
# conventions (CONVENTIONS.md). Used to ground the review in named standards.
# Configurable via env so the data store can be swapped without a code change.
DEFAULT_DATASTORE_ID = (
    "projects/construct-498201/locations/us/collections/"
    "default_collection/dataStores/reviewer-conventions"
)
REVIEWER_DATASTORE_ID = os.getenv("REVIEWER_DATASTORE_ID", DEFAULT_DATASTORE_ID)


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
3. Ground the review in the repository's coding conventions. Call
   retrieve_conventions(query) — which searches the conventions via Vertex AI
   Search — for what the diff touches (e.g. query "money cents", "frozen
   dataclass validation", "docstring", "regression test", "Iterable LineItem
   pure function"). The conventions are numbered rules (Rule 1 .. Rule 10).
4. Check the PR diff against the retrieved standards. For every violation, cite
   the rule number and a short quote of its text in the finding (e.g.
   "violates Rule 1: monetary values must be integer cents — adds a float
   discount in cart.py").
5. Also review for general quality not covered by a rule:
   - Correctness: logic errors, edge cases, broken behavior, API misuse.
   - Safety: security issues (injection, secrets in code, unsafe shell/file
     operations), data loss risks, missing input validation.
   - Test coverage: are the changes covered by new or existing tests?

IMPORTANT — grounding is additive, never required. If retrieve_conventions
returns nothing, errors, or is unavailable, DO NOT fail. Review the diff on its
own merits using your general code-review judgment and the rules you remember,
and set standards_checked to an empty list. Always produce a verdict.

Decide "approved" only when there are no convention violations, no correctness
or safety problems, and test coverage is acceptable for the size of the change;
otherwise "needs_changes".

When finished, respond with ONLY a JSON object (no markdown fences, no prose):
{"review_status": "approved" or "needs_changes",
 "findings": ["<finding 1>", "<finding 2>", ...],
 "standards_checked": ["Rule 1: monetary values are integer cents", ...],
 "summary": "<one-paragraph review summary>"}

Each finding should name the file/area and the concrete problem or risk, and
cite the violated rule number where one applies. standards_checked lists the
conventions you retrieved and checked against (empty if grounding was
unavailable). If the PR cannot be fetched, return review_status "needs_changes"
with a finding explaining the fetch failure.
"""

def retrieve_conventions(query: str) -> str:
    """Retrieve the repo's coding conventions relevant to `query` from the
    Vertex AI Search (Discovery Engine) data store, to ground the review.

    This is a function tool (a direct Discovery Engine query) rather than the
    built-in VertexAiSearchTool, because Gemini rejects mixing a built-in
    search/grounding tool with function tools ("Multiple tools are supported
    only when they are all search tools").

    Args:
        query: What conventions to look up, e.g. "monetary values rounding tests".

    Returns: matched convention text (numbered rules), or a note that none were
    retrieved — in which case review using the PR diff alone.
    """
    if not REVIEWER_DATASTORE_ID:
        return "No conventions data store configured; review using the diff alone."
    try:
        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        creds.refresh(google.auth.transport.requests.Request())
        serving = f"{REVIEWER_DATASTORE_ID}/servingConfigs/default_search"
        url = f"https://us-discoveryengine.googleapis.com/v1/{serving}:search"
        body = {
            "query": query,
            "pageSize": 5,
            "contentSearchSpec": {
                "snippetSpec": {"returnSnippet": True},
                "extractiveContentSpec": {"maxExtractiveAnswerCount": 3},
            },
        }
        resp = httpx.post(
            url,
            json=body,
            headers={
                "Authorization": f"Bearer {creds.token}",
                "X-Goog-User-Project": os.getenv("GOOGLE_CLOUD_PROJECT", "construct-498201"),
                "Content-Type": "application/json",
            },
            timeout=20.0,
        )
        resp.raise_for_status()
        data = resp.json()
        chunks: list[str] = []
        for result in data.get("results", []):
            dsd = (result.get("document", {}) or {}).get("derivedStructData", {}) or {}
            for ans in dsd.get("extractive_answers", []) or []:
                if ans.get("content"):
                    chunks.append(ans["content"])
            for snip in dsd.get("snippets", []) or []:
                if snip.get("snippet"):
                    chunks.append(snip["snippet"])
        text = "\n".join(c.strip() for c in chunks if c and c.strip())
        if not text:
            return (
                "No conventions retrieved from Vertex AI Search (the index may "
                "still be building); review using the PR diff alone."
            )
        return text[:6000]
    except Exception as exc:  # noqa: BLE001 — grounding is additive, never fatal
        return (
            f"Conventions retrieval unavailable ({type(exc).__name__}); "
            "review using the PR diff alone."
        )


# All tools are function tools (Gemini forbids mixing a built-in search tool
# with function tools). Grounding is additive — retrieve_conventions degrades
# gracefully and the instruction falls back to diff-only review.
root_agent = Agent(
    name="reviewer_agent",
    model=Gemini(model=MODEL_NAME),
    description="Reviews GitHub pull requests for correctness, safety, and test coverage, grounded in the repo's coding conventions via Vertex AI Search.",
    instruction=INSTRUCTION,
    tools=[github_get_pr, github_get_pr_diff, retrieve_conventions],
)
