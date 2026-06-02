# Construct AgentOps A2A Demo

Track 3 deployable demo for the Google for Startups AI Agents Challenge.

This example packages Construct as a B2B AgentOps control-plane agent. It
exposes a public A2A-compatible HTTP surface for Cloud Run, uses Google ADK for
Gemini-backed reasoning, and returns enterprise operations recommendations that
can be discovered by another A2A client or registered in Gemini Enterprise when
an app/admin surface is available.

## Business Scenario

- **Buyer:** platform engineering or IT operations leader at a mid-market SaaS
  company.
- **Workflow:** triage a production incident, choose the right specialized
  agent path, and produce an auditable remediation plan.
- **Outcome:** reduce incident coordination time while keeping deployment,
  rollback, and approval boundaries explicit.

## Deploy

```bash
export PROJECT_ID=your-google-cloud-project
export REGION=us-central1

gcloud config set project "$PROJECT_ID"
gcloud config set run/region "$REGION"

gcloud run deploy construct-agentops-a2a \
  --source examples/google-agents-track3/construct-agentops-a2a \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --allow-unauthenticated \
  --set-env-vars GOOGLE_CLOUD_PROJECT="$PROJECT_ID",GOOGLE_CLOUD_LOCATION="$REGION",GOOGLE_GENAI_USE_VERTEXAI=true
```

After deployment:

```bash
SERVICE_URL="$(gcloud run services describe construct-agentops-a2a \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --format='value(status.url)')"

curl "$SERVICE_URL/runtime"
curl "$SERVICE_URL/.well-known/agent-card.json"
curl -X POST "$SERVICE_URL/" \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": "demo-1",
    "method": "message/send",
    "params": {
      "message": {
        "role": "user",
        "parts": [{
          "type": "text",
          "text": "A payments deploy is failing after a config change. Build an enterprise incident plan with owner, rollback, evidence, and A2A handoff."
        }]
      }
    }
  }'
```

## Evidence

The Track 3 evidence gate expects proof under
`.demo/google-agents-cli-track3`. Capture Cloud Run URL output, the agent card,
an A2A JSON-RPC invocation response, Gemini/ADK runtime evidence, a rollback
plan, and the B2B package.

Run:

```bash
python3 scripts/demo/google_agents_cli_track3_evidence_gate.py \
  --evidence-dir .demo/google-agents-cli-track3 \
  --output /tmp/google_agents_cli_track3_evidence_gate.json
```
