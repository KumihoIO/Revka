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

### 0:12 – 0:26 · New issue → trigger (live)
- **[GH]** the fresh feature issue (create it or show it pre-made).
- **[DASH]** run the trigger command; the new run appears in Workflow Runs.
- **[LOGS]** first orchestrator lines scroll in.
- **VO:** "A brand-new feature request. I trigger the pipeline — no human will
  touch the code."

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

## Commands (paste-ready)

```bash
# [GH] create the fresh issue
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

## On-screen captions (lower-thirds)

Short overlays synced to the beats — keep each ≤ 6 words, sans-serif, lower
third, ~3s on screen. They carry the mandate keywords even with the sound off.

| In → Out | Caption |
| --- | --- |
| 0:00 → 0:05 | **Revka — autonomous issue resolution** |
| 0:06 → 0:11 | *Multi-agent · Google Cloud · human-gated* |
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
> A GitHub issue is resolved end-to-end by a multi-agent system running entirely
> on Google Cloud. A Gemini-powered orchestrator on Cloud Run plans the fix and
> verifies the Google AgentOps control plane over the A2A protocol; after a human
> approval gate, an **ADK coder agent** (Gemini via Vertex AI, on Cloud Run)
> clones the repo, implements the feature, runs the tests, and opens a pull
> request; an **ADK reviewer agent** reviews it over A2A; and after a second human
> gate, the PR is merged and the issue closed — no human touches the code.
>
> **Left:** the Revka dashboard (orchestration + human gates). **Top-right:**
> live Cloud Run logs (the agents executing on GCP). **Bottom-right:** GitHub
> (the issue → PR → merge → close).
>
> Stack: Cloud Run · Vertex AI (Gemini 2.5 Pro) · Agent Development Kit · A2A
> protocol · Workload Identity Federation (keyless) · per-agent service
> identities. No API keys; reasoning authenticated by each service's own account.

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
