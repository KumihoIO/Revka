# Accessing the Revka Orchestrator (Judges)

Revka runs natively on **Google Cloud Run** (project `construct-498201`,
region `us-central1`) as the service `revka-orchestrator`. It is the
governance + orchestration brain of the Track 3 multi-agent system: it
receives GitHub issues, runs the Google AgentOps preflight, enforces
human-in-the-loop approval gates, and coordinates the **ADK / Gemini coder
and reviewer agents** (also on Cloud Run) over the **A2A protocol**.

Reasoning runs on **Gemini via Vertex AI**, authenticated by the service's
own Google service account (no API keys). Each agent — orchestrator, coder,
reviewer, AgentOps control plane — has its own cryptographic service
identity, with least-privilege IAM between them.

## Get access in one command

The dashboard requires a bearer token. Pairing codes are minted on the
server and read from its logs (the admin endpoint is loopback-only by
design, so codes can't be fetched over the public URL). This script does
the whole exchange and prints a durable token:

```bash
DEVICE=judges bash scripts/cloud-paircode.sh
```

It prints the **Service URL** and a **Bearer token**. The token is durable
(the one-time code is consumed during pairing); reuse the token for every
request.

> Requires the Google Cloud SDK (`gcloud`) authenticated against the project
> with at least Logs Viewer. If you don't have project access, ask the team
> to run the script and send you the printed token directly — that's the
> intended hand-off and it can be revoked independently after judging.

## Use it

**Dashboard** — open the printed Service URL in a browser and paste the
token when prompted.

**Health check**

```bash
curl -s -H "Authorization: Bearer <TOKEN>" "<SERVICE_URL>/api/health"
```

**Trigger the end-to-end demo** (GitHub issue → assess → AgentOps preflight
→ approval gate → coder agent opens a PR → reviewer agent → approval gate →
merge → issue closed):

```bash
curl -s -X POST "<SERVICE_URL>/api/workflows/run/github-issue-resolver" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"inputs":{"repo_name":"KumihoIO/google-agentops-demo",
                 "github_payload":"<issue JSON>",
                 "track3_a2a_url":"https://construct-agentops-a2a-1091585228963.us-central1.run.app"}}'
```

Then open the dashboard's **Workflow Runs** page to watch the steps and
**approve the human gates** as they appear.

## Architecture at a glance

```
GitHub issue ──webhook──▶ Revka orchestrator (Cloud Run)
                              │  governance · gates · audit
                              ├──A2A (identity token)──▶ AgentOps control plane (Cloud Run)
                              ├──A2A (identity token)──▶ coder agent  (ADK · Gemini/Vertex · Cloud Run)
                              └──A2A (identity token)──▶ reviewer agent (ADK · Gemini/Vertex · Cloud Run)
                                                              │
                                                              ▼
                                                   PR opened → reviewed → merged
```
