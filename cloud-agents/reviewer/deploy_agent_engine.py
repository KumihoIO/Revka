"""Deploy the ADK reviewer agent to Vertex AI Agent Engine.

The reviewer is a pure reasoning + grounding agent (Gemini via Vertex AI +
Vertex AI Search), which is exactly what Agent Engine is built to host — so it
runs there, while the coder agent (which needs a real git/shell sandbox) stays
on Cloud Run. This is the "right runtime per agent" half of Revka's hybrid
deployment.

Idempotent: updates the existing Agent Engine with the same display name if one
exists, otherwise creates it. Prints the reasoning-engine resource name, which
Revka's workflow uses to call the agent over Agent Engine's query API.

Run locally (gcloud ADC) or from CI (Workload Identity Federation):

    cd cloud-agents/reviewer && python deploy_agent_engine.py

Config via env (sensible defaults for project construct-498201):
    PROJECT, LOCATION, STAGING_BUCKET, RUNTIME_SERVICE_ACCOUNT,
    REVIEWER_DATASTORE_ID, GITHUB_TOKEN_SECRET, DISPLAY_NAME
"""
from __future__ import annotations

import os
import sys

import vertexai
from vertexai import agent_engines
from vertexai.preview import reasoning_engines

from agent import root_agent, REVIEWER_DATASTORE_ID  # local module

PROJECT = os.getenv("PROJECT", "construct-498201")
LOCATION = os.getenv("LOCATION", "us-central1")
STAGING_BUCKET = os.getenv("STAGING_BUCKET", f"gs://{PROJECT}-agent-engine")
# Reuse the reviewer Cloud Run SA: it already holds aiplatform.user,
# discoveryengine.viewer, and secretAccessor on revka-GITHUB_TOKEN.
RUNTIME_SA = os.getenv(
    "RUNTIME_SERVICE_ACCOUNT",
    f"reviewer-agent@{PROJECT}.iam.gserviceaccount.com",
)
GITHUB_TOKEN_SECRET = os.getenv("GITHUB_TOKEN_SECRET", "revka-GITHUB_TOKEN")
DISPLAY_NAME = os.getenv("DISPLAY_NAME", "revka-reviewer")

REQUIREMENTS = [
    "google-adk==1.15.0",
    "httpx>=0.27.0,<1.0.0",
    "google-auth>=2.28",
    "google-cloud-aiplatform[agent_engines]>=1.95.1",
]

# Both files travel with the agent so the bundled-conventions corpus fallback
# (retrieve_conventions) keeps working even if the search index is still settling.
EXTRA_PACKAGES = ["agent.py", "grounding/CONVENTIONS.md"]

ENV_VARS = {
    # GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION are reserved on Agent Engine
    # (provided automatically); GOOGLE_GENAI_USE_VERTEXAI is set in agent.py.
    "REVIEWER_DATASTORE_ID": REVIEWER_DATASTORE_ID,
}

# The reviewer only *reads* PRs, so against a public repo it needs no GitHub
# token (unauthenticated REST is fine). Only attach the Secret Manager token for
# a private repo — and then the Agent Engine runtime service agent must have
# secretAccessor on it, or instances fail readiness ("no running instances").
if os.getenv("ATTACH_GITHUB_SECRET", "").lower() in ("1", "true", "yes"):
    # A dict is converted to a SecretRef proto by the SDK (version-agnostic).
    ENV_VARS["GITHUB_TOKEN"] = {"secret": GITHUB_TOKEN_SECRET, "version": "latest"}


def main() -> None:
    vertexai.init(project=PROJECT, location=LOCATION, staging_bucket=STAGING_BUCKET)

    app = reasoning_engines.AdkApp(agent=root_agent, enable_tracing=True)

    # Run the engine as the reviewer SA (it already holds aiplatform.user +
    # discoveryengine.viewer), so Vertex AI Search grounding is live rather than
    # falling back to the bundled CONVENTIONS.md corpus. Requires the Agent
    # Engine service agent to have roles/iam.serviceAccountTokenCreator on this
    # SA. Set via the SDK's spec.service_account (honored on create and update).
    common = dict(
        requirements=REQUIREMENTS,
        extra_packages=EXTRA_PACKAGES,
        env_vars=ENV_VARS,
        service_account=RUNTIME_SA,
        display_name=DISPLAY_NAME,
        description=(
            "Revka reviewer agent (ADK/Gemini via Vertex AI) — reviews GitHub "
            "PRs grounded in repo coding conventions via Vertex AI Search."
        ),
    )

    existing = [
        a for a in agent_engines.list()
        if getattr(a, "display_name", None) == DISPLAY_NAME
    ]
    if existing:
        target = existing[0]
        print(f"==> Updating existing Agent Engine: {target.resource_name}", file=sys.stderr)
        remote = target.update(agent_engine=app, **common)
    else:
        print("==> Creating new Agent Engine", file=sys.stderr)
        remote = agent_engines.create(agent_engine=app, **common)

    # stdout = just the resource name, so CI / callers can capture it cleanly.
    print(remote.resource_name)


if __name__ == "__main__":
    main()
