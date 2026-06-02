# Construct AgentOps A2A

Track 3 production-capable A2A package for the Google for Startups AI Agents
Challenge.

This example packages Construct as a B2B AgentOps control-plane agent. It
exposes an A2A-compatible HTTP surface for Cloud Run, uses Google ADK for
Gemini-backed reasoning, and returns enterprise operations recommendations that
can be discovered by another A2A client or registered in Gemini Enterprise.

The discovery endpoints are safe to expose publicly. Production invocation
should be protected with Cloud Run IAM, a dedicated service account, and
`A2A_BEARER_TOKEN` for JSON-RPC calls from approved A2A clients.

## Business Scenario

- **Buyer:** platform engineering or IT operations leader at a mid-market SaaS
  company.
- **Workflow:** triage a production incident, choose the right specialized
  agent path, and produce an auditable remediation plan.
- **Outcome:** reduce incident coordination time while keeping deployment,
  rollback, and approval boundaries explicit.

## Recording Deploy

This path is optimized for judge inspection. It allows unauthenticated access so
the A2A card and `message/send` flow can be inspected without organization
setup.

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
curl "$SERVICE_URL/readyz"
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

## Production Deploy

For a production enterprise deployment, use
`examples/google-agents-track3/construct-agentops-a2a/cloudrun.production.yaml`
as the starting manifest. Replace `PROJECT_ID`, image name, region, and service
account values, then deploy a built container image with Cloud Run IAM enforced.

```bash
export PROJECT_ID=your-google-cloud-project
export REGION=us-central1
export SERVICE_ACCOUNT="construct-agentops-a2a@$PROJECT_ID.iam.gserviceaccount.com"

gcloud iam service-accounts create construct-agentops-a2a \
  --project "$PROJECT_ID" \
  --display-name "Construct AgentOps A2A"

printf '%s' "$A2A_BEARER_TOKEN" | gcloud secrets create construct-a2a-bearer-token \
  --project "$PROJECT_ID" \
  --data-file=-

gcloud secrets add-iam-policy-binding construct-a2a-bearer-token \
  --project "$PROJECT_ID" \
  --member "serviceAccount:$SERVICE_ACCOUNT" \
  --role roles/secretmanager.secretAccessor

gcloud run services replace cloudrun.production.yaml \
  --project "$PROJECT_ID" \
  --region "$REGION"

gcloud run services add-iam-policy-binding construct-agentops-a2a \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --member "$APPROVED_CALLER" \
  --role roles/run.invoker
```

Production controls surfaced by the runtime:

- `/readyz` reports readiness without making a model call, including
  `auth_mode`, `max_message_chars`, `max_tasks`, and
  `adk_response_timeout_seconds`.
- `A2A_BEARER_TOKEN` protects JSON-RPC invocation when set; the agent card stays
  discoverable.
- `MAX_MESSAGE_CHARS`, `MAX_TASKS`, and `ADK_RESPONSE_TIMEOUT_SECONDS` bound
  request size, in-memory task retention, and ADK response latency.
- `ENABLE_CLOUD_LOGGING=true` wires standard logging into Cloud Logging.

## Evidence

The Track 3 evidence gate expects proof under
`.demo/google-agents-cli-track3`. Capture Cloud Run URL output, the agent card,
an A2A JSON-RPC invocation response, Gemini/ADK runtime evidence, a rollback
plan, production operating controls, and the B2B package.

Run:

```bash
python3 scripts/demo/google_agents_cli_track3_evidence_gate.py \
  --evidence-dir .demo/google-agents-cli-track3 \
  --output /tmp/google_agents_cli_track3_evidence_gate.json
```
