# Revka Reviewer Agent (ADK + A2A)

ADK agent (Gemini 2.5 Pro via Vertex AI, ADC — no API keys) that fetches a
GitHub pull request diff and reviews it for correctness, safety, and test
coverage. Exposed over the A2A protocol in the dialect spoken by Revka's
`a2a_discover` / `a2a_send_task` tools: agent card at
`/.well-known/agent-card.json`, JSON-RPC `message/send` / `tasks/get` /
`tasks/cancel` at `/`, task artifacts with `{"type": "text"}` parts.

**Task input** (A2A message text, JSON):

```json
{"repo_name": "owner/repo", "pr_number": 57}
```

**Output artifact** (JSON text):
`{"review_status": "approved"|"needs_changes", "findings": [...], "summary"}`.

`message/send` returns immediately with state `working`; callers poll
`tasks/get` (Revka's `a2a_send_task` with `wait=true` does this). Requires env
`GITHUB_TOKEN` (Secret Manager) for private repos and a service account with
`roles/aiplatform.user`.

## Deploy

```bash
gcloud run deploy reviewer-agent \
  --project construct-498201 --region us-central1 \
  --source cloud-agents/reviewer \
  --no-allow-unauthenticated \
  --service-account reviewer-agent@construct-498201.iam.gserviceaccount.com \
  --set-secrets GITHUB_TOKEN=revka-GITHUB_TOKEN:latest \
  --set-env-vars GOOGLE_CLOUD_PROJECT=construct-498201,GOOGLE_CLOUD_LOCATION=us-central1,GOOGLE_GENAI_USE_VERTEXAI=True \
  --memory 1Gi --timeout 900 --max-instances 1 --no-cpu-throttling
```

(`--max-instances 1` + `--no-cpu-throttling` because tasks run in-memory and
continue after `message/send` returns.) CI: `.github/workflows/deploy-cloud-agents.yml`.
