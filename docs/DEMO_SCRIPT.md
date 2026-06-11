# Demo Recording Script — Revka Track 3 (≈ 4–5 min)

A screen-recording timeline that runs the **same cloud-native pipeline on a
fresh issue**, live. Total agent runtime is ~5–8 min; the script below shows
which parts to record live and which to time-lapse so the final cut is ~4–5 min.

## Before you hit record (prep, off-camera)

1. **Pair / get a token** (durable; reuse for the whole demo):
   ```bash
   REVKA_ADMIN_TOKEN=<existing token> DEVICE=demo bash scripts/cloud-paircode.sh
   # or first-time: DEVICE=demo bash scripts/cloud-paircode.sh
   ```
   Export it: `export BT=rk_...` and `export URL=https://revka-orchestrator-n22ujw2j2a-uc.a.run.app`
2. **Open three browser tabs:** (a) the demo repo Issues page, (b) the Revka
   dashboard at `$URL/` (token pasted, on the Workflow Runs view), (c) the
   Google Cloud console → Cloud Run services list (shows the 4 services green).
3. **Pick the fresh issue.** Suggested new feature (additive, testable, distinct
   from the tax one): *"Add a `apply_percentage_discount(items, percent_off)`
   helper to `cart.py` that returns the discounted subtotal in cents."*
4. Have `scripts/cloud-paircode.sh` and `docs/TRACK3_SUBMISSION.md` handy to
   show on screen.

---

## Timeline

### 0:00 – 0:30 — Hook + architecture
- **Show:** `docs/TRACK3_SUBMISSION.md` mermaid diagram (rendered on GitHub).
- **Say:** "Revka resolves GitHub issues autonomously — but with human approval
  gates — using a multi-agent system running entirely on Google Cloud. Reasoning
  is Gemini on Vertex AI, the agents are ADK on Cloud Run, and they coordinate
  over the A2A protocol."

### 0:30 – 0:50 — It's really on Google Cloud
- **Show:** Cloud Run console — `revka-orchestrator`, `coder-agent`,
  `reviewer-agent`, `construct-agentops-a2a`, all green; click one to show it
  runs as its own service account with no keys.
- **Say:** "Four services, four service identities, deployed keylessly from
  GitHub Actions via Workload Identity Federation."

### 0:50 – 1:20 — Create the issue (live)
- **Show:** the demo repo Issues page. Create the new discount-feature issue
  (paste a prepared title/body).
  ```bash
  gh issue create -R KumihoIO/google-agentops-demo \
    --title "Feature: percentage discount on cart subtotal" \
    --body "Add apply_percentage_discount(items, percent_off) to src/agentops_demo/cart.py returning the discounted subtotal in cents (round to nearest cent), with a regression test."
  ```
- **Say:** "Here's a brand-new feature request. No human will touch the code."

### 1:20 – 1:40 — Trigger the pipeline (live)
- **Show:** terminal.
  ```bash
  ISSUE_JSON=$(curl -s https://api.github.com/repos/KumihoIO/google-agentops-demo/issues/<N>)
  curl -s -X POST "$URL/api/workflows/run/github-issue-resolver" \
    -H "Authorization: Bearer $BT" -H "Content-Type: application/json" \
    -d "$(python3 -c 'import json,sys; print(json.dumps({"inputs":{"repo_name":"KumihoIO/google-agentops-demo","github_payload":sys.argv[1],"track3_a2a_url":"https://construct-agentops-a2a-1091585228963.us-central1.run.app"}}))' "$ISSUE_JSON")"
  ```
- **Cut to:** the dashboard Workflow Runs view — the run appears, steps light up.

### 1:40 – 2:10 — Assess + AgentOps preflight
- **Show:** dashboard step list: `assess_issue` ✓ then `agentops_preflight` ✓.
  Click the preflight step to show `a2a_discovery_status: discovered`.
- **Say:** "It plans the fix on Gemini, then proves the Google AgentOps
  integration by discovering the control plane over A2A — using a Cloud Run
  identity token minted from the metadata server."

### 2:10 – 2:30 — Gate 1 (the governance moment)
- **Show:** the run pauses at `human_approval_gate_1`; the dashboard shows the
  approval prompt. Click **Approve**.
- **Say:** "Before any code changes, a human approves — this is the governance
  layer enterprises actually need."

### 2:30 – 3:30 — Coder agent works (time-lapse this part)
- **Show:** `deploy_coder_agent` running. Switch to the demo repo PRs tab; the
  new PR appears (branch `fix/issue-<N>`). Open it, scroll the diff —
  `apply_percentage_discount` plus a test.
- **Say:** "The ADK coder agent on Cloud Run — reasoning on Gemini — cloned the
  repo, implemented the feature, ran the tests, and opened this PR. All via a
  single A2A task call."

### 3:30 – 3:55 — Review + Gate 2
- **Show:** `review_pr` ✓ (click to show the verdict), then the pause at
  `human_approval_gate_2`. Click **Approve**.
- **Say:** "A separate reviewer agent — its own identity — reviews the diff over
  A2A. Then the second human gate before merge."

### 3:55 – 4:25 — Merge + close (the payoff)
- **Show:** `merge_and_close` ✓, run status **completed**. Switch to GitHub:
  PR shows **Merged**, issue shows **Closed**, and `cart.py` on `main` now has
  the new function.
- **Say:** "The coder merges the PR and closes the issue. A real feature,
  shipped autonomously, governed by humans, entirely on Google Cloud."

### 4:25 – 4:45 — Close
- **Show:** the mermaid diagram again, or the four-green-services Cloud Run view.
- **Say:** "Cloud-native runtime, Gemini intelligence, A2A interoperability,
  per-agent identity — Track 3, end to end."

---

## Auto-approve option (if you don't want to click gates on camera)

Run this in a side terminal after triggering; it approves both gates the moment
they appear, so the run flows continuously while you narrate:

```bash
RUN=<run_id>
while true; do
  S=$(curl -s -H "Authorization: Bearer $BT" "$URL/api/workflows/runs/$RUN" \
       | python3 -c 'import json,sys; r=json.load(sys.stdin).get("run",{}); print(r.get("status"))')
  if [ "$S" = "paused" ]; then
    curl -s -X POST -H "Authorization: Bearer $BT" -H "Content-Type: application/json" \
      -d '{"approved":true,"feedback":"demo"}' "$URL/api/workflows/runs/$RUN/approve" >/dev/null
  fi
  case "$S" in completed|failed|cancelled) break;; esac
  sleep 10
done
```

## Reset between takes
- Close/recreate the issue, or pick a different fresh feature each take.
- If the coder finds the feature already implemented (from a prior take), use a
  new feature name so there's a genuine diff to show.
- Tokens are durable across takes; only re-pair if you redeploy the orchestrator.

## Gotchas seen in testing
- After **any orchestrator redeploy**, paired tokens reset — re-pair via
  `scripts/cloud-paircode.sh` (bootstrap mode reads the fresh code from logs).
- The coder runs one heavyweight task at a time (single instance) — don't fire
  two runs at once on camera.
- `gcloud` auth can expire; run `gcloud auth login` before recording if the
  pairing script's log-read step fails.
