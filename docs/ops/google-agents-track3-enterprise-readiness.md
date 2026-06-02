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

## Demo Outcome Matrix

| Outcome to show | Expected Construct behavior | Evidence to check before recording |
|---|---|---|
| Cloud Run runtime readiness | Cloud Run service exposes health/runtime metadata that names Track 3, Google ADK orchestration, and Gemini through Vertex AI | `GET /runtime`; `runtime/healthz.json`; `track3_demo_probe` runtime surface |
| Registration-ready A2A discovery | The service exposes `/.well-known/agent-card.json`, `/agent-card.json`, and JSON-RPC `agent/card` with Gemini Enterprise registration-ready A2A fields | `a2a/agent-card.json`; `track3_demo_probe` agent-card registration surface |
| Live A2A incident plan | JSON-RPC `message/send` returns a completed task with an executive incident plan covering business impact, specialized agents, A2A handoff, Google Cloud evidence, approval, rollback, and operator recommendation | `a2a/message-send-response.json`; Track 3 evidence gate A2A interoperability check |
| A2A task lifecycle branches | The demo service can store tasks and expose `tasks/get`, `tasks/list`, and `tasks/cancel` branches for protocol completeness | Source probe for task lifecycle branches; optional rehearsal curl calls |
| Demo-safe error branches | Missing text, unsupported methods, missing tasks, and ADK runtime errors return structured JSON-RPC/task errors instead of crashing the recording | Source probe for invalid request, unsupported operation, task-not-found, and failed-task branches |
| B2B governance story | The ADK agent routes risk, calls governance tools, and returns identity, approval, observability, rollback, and recommendation details for enterprise buyers | `business/package.md`; `governance/controls.md`; `enterprise/gemini-enterprise-registration.md`; source probe for ADK instructions and tools |
| Final rehearsal gate alignment | The umbrella pre-recording gate can validate local code readiness, Track 3 source outcome coverage, Track 3 live evidence, and PR state in one report | `google_agents_cli_pre_recording_gate.py --track track3`; `strict_final_recording_ready: true` |

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

The manifest must prove these Track 3 claims:

```text
google_cloud_deployment
a2a_interoperability
gemini_powered_intelligence
adk_orchestration
b2b_enterprise_package
enterprise_governance
gemini_enterprise_readiness
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

For the final Track 3 recording rehearsal, run the umbrella gate:

```bash
python3 scripts/demo/google_agents_cli_pre_recording_gate.py \
  --track track3 \
  --evidence-dir .demo/google-agents-cli-track3 \
  --pr-number 324 \
  --require-strict-final-ready \
  --output /tmp/google_agents_cli_track3_pre_recording_gate.json
```

The report must have `strict_final_recording_ready: true`. If it does not, use
`strict_final_blockers` and `strict_final_blocker_details` as the recording
blocker list.

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
