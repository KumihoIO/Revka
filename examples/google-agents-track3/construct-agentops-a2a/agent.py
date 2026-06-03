"""ADK agent used by the Construct Track 3 A2A Cloud Run demo."""
from __future__ import annotations

import json
import os

from google.adk.agents import Agent
from google.adk.models import Gemini
from google.genai import types


APP_NAME = "construct-agentops-a2a"
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-2.5-flash")


def inspect_incident_context(summary: str) -> str:
    """Classify the incident and return enterprise routing context."""
    lowered = summary.lower()
    risk = "medium"
    if any(token in lowered for token in ("payments", "production", "customer", "outage")):
        risk = "high"
    if any(token in lowered for token in ("credential", "secret", "iam", "token")):
        risk = "critical"

    return json.dumps(
        {
            "risk": risk,
            "business_service": "payments" if "payment" in lowered else "application platform",
            "recommended_agents": [
                "incident-triage",
                "release-engineering",
                "security-review" if risk == "critical" else "sre-review",
            ],
            "required_evidence": [
                "deployment diff",
                "Cloud Logging trace",
                "rollback target",
                "approver",
            ],
        }
    )


def recommend_agentops_policy(risk: str, requested_action: str) -> str:
    """Return a governance policy for the requested remediation."""
    approval_required = risk.lower() in {"high", "critical"} or "rollback" in requested_action.lower()
    return json.dumps(
        {
            "approval_required": approval_required,
            "identity_boundary": "per-agent service identity before production action",
            "rollback_policy": "redeploy last known-good Cloud Run revision before forward fix",
            "observability": [
                "Cloud Logging incident timeline",
                "A2A task id",
                "Gemini reasoning summary",
            ],
        }
    )


root_agent = Agent(
    name="construct_agentops_control_plane",
    model=Gemini(
        model=MODEL_NAME,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=(
        "You are Construct Enterprise AgentOps Control Plane, a B2B operations "
        "agent for platform engineering teams. For every incident request, call "
        "inspect_incident_context and recommend_agentops_policy before answering. "
        "Return an actionable plan with: business impact, specialized agents to "
        "coordinate over A2A, Google Cloud evidence to inspect, approval boundary, "
        "rollback path, and final operator recommendation. Keep responses concise "
        "and suitable for an executive demo."
    ),
    tools=[inspect_incident_context, recommend_agentops_policy],
)
