# Google Agents Track 3 Enterprise Readiness

## Purpose

This runbook is the recording checklist for the Track 3 pivot. It proves that
Construct is not only invoking `agents-cli`; it is packaging an enterprise-ready
B2B agent surface on Google Cloud with A2A interoperability.

## Target Story

- **Product:** Construct Enterprise AgentOps Control Plane.
- **Buyer:** platform engineering or IT operations leader.
- **Workflow:** governed production incident response.
- **Google runtime:** Cloud Run.
- **Intelligence:** Gemini through Vertex AI.
- **Orchestration:** Google ADK.
- **Interoperability:** A2A agent card and JSON-RPC `message/send`.

## Deployable Artifact

The tracked demo app is:

```text
examples/google-agents-track3/construct-agentops-a2a
```

It exposes:

- `GET /runtime`
- `GET /.well-known/agent-card.json`
- `GET /agent-card.json`
- `POST /` and `POST /a2a` for A2A JSON-RPC methods

## Deployment

The recording deployment is public so judges can inspect the A2A card and invoke
the demo without organization-specific IAM setup. Production enterprise
deployment should remove `--allow-unauthenticated`, use a dedicated Cloud Run
service account, and grant `roles/run.invoker` only to approved callers or the
Gemini Enterprise integration identity.

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

For a production IAM-secured deployment, use the same command without
`--allow-unauthenticated`, then grant invoker access explicitly:

```bash
gcloud run services add-iam-policy-binding construct-agentops-a2a \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --member "$APPROVED_CALLER" \
  --role roles/run.invoker
```

## Capture Evidence

Use `.demo/google-agents-cli-track3` for local evidence. It is ignored by git
because it may contain project IDs, service URLs, trace IDs, and screenshots.

Required files:

```text
manifest.json
deploy/cloud-run-service.json
deploy/deploy-output.txt
deploy/rollback-plan.md
a2a/agent-card.json
a2a/message-send-response.json
runtime/healthz.json
runtime/source-manifest.json
business/package.md
governance/controls.md
enterprise/gemini-enterprise-registration.md
```

Helpful capture commands:

```bash
SERVICE_URL="$(gcloud run services describe construct-agentops-a2a \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --format='value(status.url)')"

gcloud run services describe construct-agentops-a2a \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --format=json > .demo/google-agents-cli-track3/deploy/cloud-run-service.json

curl -fsS "$SERVICE_URL/runtime" \
  > .demo/google-agents-cli-track3/runtime/healthz.json

curl -fsS "$SERVICE_URL/.well-known/agent-card.json" \
  > .demo/google-agents-cli-track3/a2a/agent-card.json

curl -fsS -X POST "$SERVICE_URL/" \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": "track3-demo",
    "method": "message/send",
    "params": {
      "message": {
        "role": "user",
        "parts": [{
          "type": "text",
          "text": "A payments deploy is failing after a config change. Build an enterprise incident plan with owner, rollback, evidence, approval boundary, and A2A handoff."
        }]
      }
    }
  }' > .demo/google-agents-cli-track3/a2a/message-send-response.json
```

## Gate

```bash
python3 scripts/demo/google_agents_cli_track3_evidence_gate.py \
  --evidence-dir .demo/google-agents-cli-track3 \
  --output /tmp/google_agents_cli_track3_evidence_gate.json
```

The report must have:

```json
{
  "passed": true,
  "summary": {
    "failed": 0,
    "passed": 7,
    "total": 7
  }
}
```

## Gemini Enterprise

If Gemini Enterprise app/admin access is available, use the captured A2A agent
card to register the Cloud Run service. If it is not available before
recording, the evidence must still include the registration-ready plan in
`enterprise/gemini-enterprise-registration.md` and the demo narration should
state that app-admin registration is the only environment-specific step.

The demo agent card includes the Gemini Enterprise registration fields used by
the Google Cloud A2A registration flow, including `protocolVersion`, `name`,
`description`, `url`, `iconUrl`, `version`, `capabilities`, `skills`,
`defaultInputModes`, and `defaultOutputModes`.

## Rollback

For the demo service, rollback by redeploying the previous Cloud Run revision
or deleting the service:

```bash
gcloud run services delete construct-agentops-a2a \
  --project "$PROJECT_ID" \
  --region "$REGION"
```
