# Demo Recording Script — Revka Track 3 (2:00, three-window)

A 2-minute cut built around a **three-window layout**:

- **`[DASH]` — Revka dashboard (main window):** the governed orchestration —
  steps lighting up, the human approval gates. The *product*.
- **`[LOGS]` — Cloud Run monitor:** the agents actually executing on Google
  Cloud — A2A task calls, ADK/Vertex reasoning, clone/test/PR. The *proof it's
  on GCP*.
- **`[GH]` — GitHub:** the demo repo — the issue, then the PR appearing, the
  diff, the merge, the close. The *real-world outcome*.

The real run takes ~6 min; the final video is 2:00 via continuous narration,
auto-approved gates (no clicking dead air), and time-lapsing the coder segment.

## Before you hit record (prep, off-camera)

1. **Token + env** (durable; reuse across takes):
   ```bash
   REVKA_ADMIN_TOKEN=<existing token> DEVICE=demo bash scripts/cloud-paircode.sh
   export BT=rk_...   URL=https://revka-orchestrator-n22ujw2j2a-uc.a.run.app
   ```
2. **`[DASH]`** open `$URL/`, paste the token, go to Workflow Runs.
3. **`[LOGS]`** Cloud Run monitor. Pick one:
   - **Console (best visual):** Logs Explorer → paste the query → **Stream logs** on:
     ```
     resource.type="cloud_run_revision"
     resource.labels.service_name=("revka-orchestrator" OR "coder-agent" OR "reviewer-agent")
     ```
   - **Terminal (fallback):** `bash scripts/demo-logs.sh` — colored, service-tagged
     (cyan=orchestrator, green=coder, magenta=reviewer).
4. **`[GH]`** open the demo repo: `github.com/KumihoIO/google-agentops-demo` —
   start on the Issues tab.
5. **Pre-write the fresh issue** (additive, testable, distinct each take), e.g.
   *"Add `apply_percentage_discount(items, percent_off)` to `cart.py`."*
6. **Auto-approver** ready in a hidden terminal (script at the bottom).

> Editing tip: record the full ~6-min session once; in your editor speed the
> coder segment to fit 0:48→1:16. Narration runs continuously over the cut.

---

## Final-cut timeline (2:00)

### 0:00 – 0:12 · Hook
- **[DASH]** dashboard idle (main). **[LOGS]** and **[GH]** visible alongside.
- **VO:** "Revka resolves GitHub issues autonomously — with human approval gates
  — using a multi-agent system running entirely on Google Cloud. Dashboard's the
  orchestrator, the logs are the agents on Cloud Run, and GitHub is where the
  work lands."

### 0:12 – 0:26 · New issue → auto-trigger (live)
- **[GH]** open the fresh feature issue, then add the **`revka`** label.
  The repo's `revka-issue-trigger.yml` GitHub Action fires automatically and
  POSTs to the Cloud Run orchestrator (bearer token in repo secrets) — no manual
  command.
- **[DASH]** the new run appears in Workflow Runs on its own.
- **[LOGS]** first orchestrator lines scroll in.
- **VO:** "I open a feature request and label it for Revka. A GitHub Action
  triggers the pipeline on Cloud Run automatically — no human touches the code."

> The label trigger is the headline flow (issue → GitHub Action → Cloud Run). A
> manual `POST /api/workflows/run/...` (commands below) is the fallback if you'd
> rather not depend on the Action timing on camera.

### 0:26 – 0:42 · Assess + AgentOps preflight + Gate 1
- **[DASH]** `assess_issue` ✓ → `agentops_preflight` ✓ (click it: flash
  `a2a_discovery_status: discovered`) → pause at gate 1, auto-approves.
- **[LOGS]** orchestrator: A2A discovery + identity-token mint.
- **VO:** "It plans the fix on Gemini, proves the Google AgentOps integration by
  discovering the control plane over A2A, then pauses for human approval."

### 0:42 – 1:16 · Coder agent works → PR (TIME-LAPSE)
- **[DASH]** `deploy_coder_agent` running (sped up).
- **[LOGS]** **green** coder lines — *the money shot, point at it:* A2A task
  received → git clone → Vertex/Gemini → pytest → "PR opened".
- **[GH]** the new PR appears (`fix/issue-<N>`); open the diff briefly.
- **VO:** "Watch the logs: the ADK coder agent on Cloud Run takes the A2A task,
  clones the repo, reasons on Gemini via Vertex, writes the code and a test, runs
  pytest, and opens this pull request — all on Google Cloud."

### 1:16 – 1:34 · Review + Gate 2
- **[DASH]** `review_pr` ✓ (flash verdict) → gate 2 auto-approves.
- **[LOGS]** **magenta** reviewer lines: diff fetched, verdict returned.
- **VO:** "A separate reviewer agent — its own identity — reviews the diff over
  A2A. Then the second human gate before merge."

### 1:34 – 1:52 · Merge + close (payoff)
- **[DASH]** `merge_and_close` ✓, run **completed**.
- **[LOGS]** green coder lines: merge + issue close.
- **[GH]** PR flips to **Merged**, issue to **Closed**; new function on `main`.
- **VO:** "The coder merges and closes the issue. A real feature, shipped
  autonomously, governed by humans, entirely on Google Cloud."

### 1:52 – 2:00 · Tagline
- **[DASH]** run all green, **[GH]** closed issue, **[LOGS]** final lines.
- **VO:** "Cloud-native runtime, Gemini intelligence, A2A interoperability.
  Track 3, end to end."

---

## Automatic trigger (headline flow)

The demo repo's `revka-issue-trigger.yml` Action posts to Cloud Run when an issue
is labeled `revka`. Repo secrets (set once):

- `REVKA_GATEWAY_URL` = `https://revka-orchestrator-n22ujw2j2a-uc.a.run.app`
- `REVKA_BEARER_TOKEN` = the stable admin token (`revka-GATEWAY_ADMIN_TOKEN`)

On camera, just open the issue and add the label:

```bash
gh issue create -R KumihoIO/google-agentops-demo \
  --title "Feature: percentage discount on cart subtotal" \
  --body "Add apply_percentage_discount(items, percent_off) to src/agentops_demo/cart.py returning the discounted subtotal in cents (round to nearest cent), with a regression test." \
  --label revka
```

## Manual trigger (fallback)

```bash
# [GH] create the fresh issue (no label)
gh issue create -R KumihoIO/google-agentops-demo \
  --title "Feature: percentage discount on cart subtotal" \
  --body "Add apply_percentage_discount(items, percent_off) to src/agentops_demo/cart.py returning the discounted subtotal in cents (round to nearest cent), with a regression test."

# [DASH] trigger (replace <N> with the new issue number)
ISSUE=$(curl -s https://api.github.com/repos/KumihoIO/google-agentops-demo/issues/<N>)
RUN=$(curl -s -X POST "$URL/api/workflows/run/github-issue-resolver" \
  -H "Authorization: Bearer $BT" -H "Content-Type: application/json" \
  -d "$(python3 -c 'import json,sys;print(json.dumps({"inputs":{"repo_name":"KumihoIO/google-agentops-demo","github_payload":sys.argv[1],"track3_a2a_url":"https://construct-agentops-a2a-1091585228963.us-central1.run.app"}}))' "$ISSUE")" \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["run_id"])')
echo "run: $RUN"

# [LOGS] Cloud Run monitor (terminal fallback; prefer Console Logs Explorer)
bash scripts/demo-logs.sh

# hidden terminal — hands-free gate approver (run right after triggering)
while true; do
  S=$(curl -s -H "Authorization: Bearer $BT" "$URL/api/workflows/runs/$RUN" \
       | python3 -c 'import json,sys;print(json.load(sys.stdin).get("run",{}).get("status"))')
  [ "$S" = "paused" ] && curl -s -X POST -H "Authorization: Bearer $BT" \
    -H "Content-Type: application/json" -d '{"approved":true,"feedback":"demo"}' \
    "$URL/api/workflows/runs/$RUN/approve" >/dev/null
  case "$S" in completed|failed|cancelled) break;; esac
  sleep 8
done
```

## Presenter narration (full pitch — recommended)

This is the **presenter's script**, not a voice-over. You're making a case:
*here's the problem enterprises have with autonomous agents, here's how Revka
solves it, here's why it's different, and here it is doing it for real.* The
demo is your evidence, not your subject. ~620 words, ~4:00 at a measured keynote
pace. Section headers are beats, not on-screen text. (For a tight 2:00 cut, use
the compressed voice-over below instead.)

### The problem — why enterprises can't ship autonomous agents *(~0:00–0:40)*

> Every enterprise wants the same thing from AI: take the multi-step work that
> consumes engineering, security, and operations teams — and let it run itself.
> Almost none of them have. And it isn't because the agents aren't capable.
>
> It's because a raw autonomous agent is a black box. It acts without oversight.
> It forgets everything the moment a task ends. And it leaves no trail you could
> ever show an auditor. You cannot put that in front of production — not in
> finance, not in security, not anywhere a wrong move is expensive and
> irreversible. The capability arrived years ago. The *trust* didn't.

### What Revka is *(~0:40–1:15)*

> Revka closes that gap. It's a platform for building autonomous workflows an
> enterprise can actually turn on.
>
> You define the work **visually** — as a graph of steps, not a hidden prompt.
> Agents execute it. And at every decision that matters, a human approves. There
> is no black box: you see the workflow, you govern it, and every single step is
> recorded. It runs on your own cloud — here, entirely on Google Cloud — where
> each agent holds its own cryptographic identity and reasons on Gemini through
> Vertex AI, with no API keys anywhere in the system.

### Why Revka is different — memory and the record *(~1:15–2:05)*

> But governance isn't what sets Revka apart. Two things do.
>
> The first is **memory**. Most agents are amnesiac — or they bolt on a flat pile
> of vectors and call it memory. Revka's memory is **graph-native**: every
> workflow's outputs, decisions, and context are stored as a connected knowledge
> graph. The system doesn't just *act* — it remembers what was done, why, and how
> it relates to everything before it, and it grounds the next run in that
> accumulated understanding of your organization. Every workflow makes the next
> one smarter.
>
> The second is the **record**. Revka's infrastructure captures every workflow
> output — every agent action, every approval, every result — as a durable,
> attributable audit trail. Graph-native memory plus a complete record of every
> run: *that* is what turns "autonomous" from a liability your compliance team
> vetoes into an asset you can defend to a regulator.

### See it run — one workflow of many *(~2:05–3:35)*

> Let me show you one. This workflow resolves a software issue, end to end.
>
> I trigger it by labeling the issue — and it appears in the dashboard, running
> on its own. It assesses the work, verifies a partner agent over the A2A
> protocol, and then stops at a human gate before touching anything. I approve. ‖
> Now the coder agent — built with the Agent Development Kit, reasoning on Gemini
> — takes the task over A2A, does the work on Cloud Run, and opens this pull
> request. No human wrote that code. ‖ An independent reviewer agent checks it,
> grounded in our coding standards retrieved from Vertex AI Search, and cites the
> exact rule it applies. A second human gate. I approve. ‖ The agents merge the
> change and close the issue — agent to agent, on Google Cloud. And everything
> you just watched is now in the graph and in the audit trail.

### Close — the platform behind it *(~3:35–4:00)*

> That was one workflow. The same engine runs a security audit, a data pipeline,
> a compliance review, a research task — anything you can define as steps. Revka
> is the platform underneath: visual, governed, auditable, and grounded in memory
> that compounds. That's the difference between an agent that's impressive in a
> demo — and one an enterprise can actually deploy.

**Delivery notes:** the first two beats (*problem* and *what Revka is*) can run
over a slow pan of the dashboard or a title card — you don't need live action
until "See it run." Let the *differentiation* beat breathe; it's the argument
the judges remember. The four `‖` marks in the demo beat are your approval/cut
points. If you must trim to ~3:00, cut the close to one sentence and tighten the
problem to its first and last lines — never cut the memory/record beat.

## Voice-over transcript — tight 2:00 cut (alternative)

Continuous narration for the 2:00 cut — ~300 words, ~150 wpm. Read at a measured
demo pace; pause briefly at each `‖`. Timestamps are cut points, not hard cues.

> **[0:00]** This is Revka — a platform for building autonomous, auditable
> workflows for real enterprise work. You define them visually, as steps on a
> canvas; agents execute them; and a human governs the decisions that matter. ‖
> It all runs on Google Cloud.
>
> **[0:14]** What you're about to see is *one* workflow — resolving a software
> issue, end to end. But the same engine runs security audits, data pipelines,
> research, operations — any multi-step process you can define. ‖ Every agent
> here is a Cloud Run service with its own identity, reasoning on Gemini through
> Vertex AI. No API keys.
>
> **[0:36]** I start this workflow by labeling an issue — a GitHub Action triggers
> it, and it appears in the dashboard, running on its own. ‖ It assesses the
> work, verifies a partner agent over the A2A protocol, and then pauses for human
> approval before anything changes. I approve.
>
> **[0:58]** Now the coder agent — built with the Agent Development Kit, reasoning
> on Gemini — takes the task over A2A, does the work on Cloud Run, and opens a
> pull request. ‖ No human wrote that code.
>
> **[1:18]** Then an independent reviewer agent checks it — grounded in our coding
> standards, retrieved from Vertex AI Search — and cites the exact rule it
> applies. ‖ A second human gate, before merge. I approve.
>
> **[1:38]** The agents merge the change and close the issue — agent to agent,
> entirely on Google Cloud. The work was done autonomously, ‖ but every step was
> governed and recorded — a complete, auditable trail.
>
> **[1:52]** That's one workflow. Revka is the platform behind it — visual,
> governed, auditable, multi-agent — turning everyday enterprise problems into
> autonomous workflows on Google Cloud.

**Pacing notes:** the framing bookends ([0:00] and [1:52]) carry the message —
*Revka is the platform; issue resolution is one example.* If you run long, trim
the partner-agent clause in [0:36] and the "No human wrote that code" aside. If
short, hold on the PR diff (0:58–1:18) and the merged/closed GitHub state
(1:38–1:52). The two "I approve" beats land as you click (or as the auto-approver
fires).

## On-screen captions (lower-thirds)

Short overlays synced to the beats — keep each ≤ 6 words, sans-serif, lower
third, ~3s on screen. They carry the mandate keywords even with the sound off.

| In → Out | Caption |
| --- | --- |
| 0:00 → 0:06 | **Revka — autonomous workflow platform** |
| 0:07 → 0:13 | *Visual · governed · auditable · on Google Cloud* |
| 0:16 → 0:22 | *One workflow of many — issue resolution* |
| 0:13 → 0:18 | **New GitHub issue → triggered** |
| 0:27 → 0:33 | **Plans the fix on Gemini (Vertex AI)** |
| 0:34 → 0:40 | **A2A preflight: control plane discovered** |
| 0:40 → 0:44 | **⏸ Human approval gate 1** |
| 0:45 → 0:52 | **ADK coder agent · Cloud Run** |
| 0:53 → 1:00 | *clone → Gemini → test → open PR* |
| 1:05 → 1:12 | **Pull request opened — by the agent** |
| 1:17 → 1:24 | **ADK reviewer agent · A2A** |
| 1:25 → 1:30 | **⏸ Human approval gate 2** |
| 1:35 → 1:42 | **Merged ✓ · Issue closed ✓** |
| 1:43 → 1:50 | *Shipped autonomously, governed by humans* |
| 1:53 → 2:00 | **Cloud Run · Gemini · A2A — Track 3** |

Persistent corner tags (tiny, top-left, whole video) reinforce which window is
which: `DASHBOARD` / `CLOUD RUN LOGS` / `GITHUB`.

## Pinned description (paste under the video)

> **Revka — Google for Startups AI Agents Challenge, Track 3.**
> Revka is a platform for building **autonomous, auditable, visually-defined
> workflows** that run on Google Cloud — for software, security, data, research,
> operations, and more. Workflows are multi-agent and human-governed: agents
> reason on Gemini, coordinate over A2A, and pause at approval gates a human
> controls, with every step recorded as an audit trail.
>
> This video shows **one example workflow** — resolving a software issue end to
> end. Labeling the issue triggers it; an orchestrator on Cloud Run assesses the
> work and verifies the Google AgentOps control plane over A2A; after a human
> gate, an **ADK coder agent** (Gemini via Vertex AI, on Cloud Run) does the work
> and opens a pull request; an **ADK reviewer agent**, grounded in Vertex AI
> Search, reviews it; and after a second human gate, the change is merged and the
> issue closed — no human touches the code.
>
> **Left:** the Revka dashboard (visual workflow + human gates). **Top-right:**
> live Cloud Run logs (the agents executing on GCP). **Bottom-right:** the work
> target on GitHub.
>
> Stack: Cloud Run · Vertex AI (Gemini 2.5 Pro) · Agent Development Kit · A2A
> protocol · Vertex AI Search grounding · Workload Identity Federation (keyless) ·
> per-agent service identities. No API keys; reasoning authenticated by each
> service's own account.

## Window layout suggestion
- **Dashboard** large on the left (≈55% width).
- **Cloud Run logs** top-right; **GitHub** bottom-right (≈45% width, stacked).
- Camera/recording at 1080p+ so the log text is legible.

## Notes
- **Use a different feature each take** so the coder always has a real diff.
- Prefer the **Console Logs Explorer (streaming)** for `[LOGS]` — live and
  formatted; `demo-logs.sh` polls every 3s as a fallback.
- After any **orchestrator redeploy**, re-pair (`scripts/cloud-paircode.sh`); run
  `gcloud auth login` first if the log-read step fails.
- The coder runs one task at a time — don't fire two runs at once.
- Want the gates clicked **on camera** instead of auto-approved? Skip the
  approver and click **Approve** in `[DASH]` at ~0:38 and ~1:18.
