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

## Get access

The dashboard and API require a **bearer token**. The simplest hand-off: the
team runs the pairing script once and sends you the printed token — it's
durable and reusable, and can be revoked independently after judging. You do
not need any Google Cloud access for that.

`scripts/cloud-paircode.sh` has two modes:

**Admin mode (repeatable, preferred)** — if you already hold a valid token,
mint additional device tokens with no Cloud access at all. It calls the
authenticated `/api/pairing/initiate` endpoint to generate a fresh one-time
code and exchanges it:

```bash
REVKA_ADMIN_TOKEN=rk_xxx DEVICE=judges bash scripts/cloud-paircode.sh
```

**Bootstrap mode (first device only)** — with no token yet, the one-time code
is read from the service's startup logs (requires `gcloud` with Logs Viewer;
the admin code endpoints are loopback-only by design):

```bash
DEVICE=first-device bash scripts/cloud-paircode.sh
```

Either way it prints the **Service URL** and a **Bearer token**. A one-time
code is consumed on first successful pair; the token is what you keep.

> Note: the `/pair` endpoint has brute-force rate limiting. If you see
> "Too many failed attempts", wait the stated seconds and retry.

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
