# Demo Recording Script — Revka Track 3 (2:00)

A tight 2-minute cut of the cloud-native pipeline resolving a **fresh issue**.
The real run takes ~6 min; the final video is 2:00 by keeping narration
continuous, auto-approving the gates (no clicking dead air), and time-lapsing
the one slow step (the coder). Capture everything, then cut to the beats below.

## Before you hit record (prep, off-camera)

1. **Token + env** (durable; reuse across takes):
   ```bash
   REVKA_ADMIN_TOKEN=<existing token> DEVICE=demo bash scripts/cloud-paircode.sh
   export BT=rk_...   URL=https://revka-orchestrator-n22ujw2j2a-uc.a.run.app
   ```
2. **Three tabs ready:** (a) demo repo Issues, (b) Revka dashboard `$URL/`
   (token pasted, Workflow Runs view), (c) Cloud Run console (4 services green).
3. **Pre-write the fresh issue** (additive, testable, distinct from prior takes),
   e.g. *"Add `apply_percentage_discount(items, percent_off)` to `cart.py`."*
4. **Start the auto-approver** in a hidden terminal right after you trigger
   (script at the bottom) so both gates clear instantly while you talk.

> Editing tip: record the full ~6-min session once, then in your editor speed
> the coder segment to fit 0:50→1:20. Narration runs continuously over the cut.

---

## Final-cut timeline (2:00)

### 0:00 – 0:12 · Hook + architecture
- **Screen:** the `TRACK3_SUBMISSION.md` mermaid diagram.
- **VO:** "Revka resolves GitHub issues autonomously — with human approval gates
  — using a multi-agent system running entirely on Google Cloud: Gemini on
  Vertex AI, ADK agents on Cloud Run, coordinating over A2A."

### 0:12 – 0:24 · It's really on Google Cloud
- **Screen:** Cloud Run console — `revka-orchestrator`, `coder-agent`,
  `reviewer-agent`, `construct-agentops-a2a`, all green.
- **VO:** "Four services, four service identities, deployed keylessly from
  GitHub Actions."

### 0:24 – 0:40 · New issue → trigger (live)
- **Screen:** create the issue, then the trigger command; cut to the dashboard
  as the run appears.
- **VO:** "A brand-new feature request. I trigger the pipeline — and no human
  will touch the code."

### 0:40 – 0:52 · Assess + AgentOps preflight + Gate 1
- **Screen:** `assess_issue` ✓ → `agentops_preflight` ✓ (flash
  `a2a_discovery_status: discovered`) → gate 1 auto-approves.
- **VO:** "It plans the fix on Gemini, proves the Google AgentOps integration by
  discovering the control plane over A2A, then pauses for human approval."

### 0:52 – 1:20 · Coder agent works → PR (TIME-LAPSE)
- **Screen:** `deploy_coder_agent` running (sped up), then cut to the demo repo —
  the new PR with the diff (`apply_percentage_discount` + a test).
- **VO:** "The ADK coder agent on Cloud Run — reasoning on Gemini — clones the
  repo, writes the code and a test, and opens this pull request. One A2A call."

### 1:20 – 1:38 · Review + Gate 2
- **Screen:** `review_pr` ✓ (flash the verdict) → gate 2 auto-approves.
- **VO:** "A separate reviewer agent, its own identity, reviews the diff over
  A2A — then the second human gate before merge."

### 1:38 – 1:54 · Merge + close (payoff)
- **Screen:** `merge_and_close` ✓, run **completed**; cut to GitHub: PR
  **Merged**, issue **Closed**, new function on `main`.
- **VO:** "The coder merges and closes the issue. A real feature, shipped
  autonomously, governed by humans — entirely on Google Cloud."

### 1:54 – 2:00 · Tagline
- **Screen:** the mermaid diagram (or 4 green services).
- **VO:** "Cloud-native runtime, Gemini intelligence, A2A interoperability.
  Track 3, end to end."

---

## Commands (paste-ready)

```bash
# create the fresh issue
gh issue create -R KumihoIO/google-agentops-demo \
  --title "Feature: percentage discount on cart subtotal" \
  --body "Add apply_percentage_discount(items, percent_off) to src/agentops_demo/cart.py returning the discounted subtotal in cents (round to nearest cent), with a regression test."

# trigger (replace <N> with the new issue number)
ISSUE=$(curl -s https://api.github.com/repos/KumihoIO/google-agentops-demo/issues/<N>)
RUN=$(curl -s -X POST "$URL/api/workflows/run/github-issue-resolver" \
  -H "Authorization: Bearer $BT" -H "Content-Type: application/json" \
  -d "$(python3 -c 'import json,sys;print(json.dumps({"inputs":{"repo_name":"KumihoIO/google-agentops-demo","github_payload":sys.argv[1],"track3_a2a_url":"https://construct-agentops-a2a-1091585228963.us-central1.run.app"}}))' "$ISSUE")" \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["run_id"])')
echo "run: $RUN"
```

```bash
# hands-free gate approver (run in a hidden terminal right after triggering)
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

## Notes
- Use a **different feature each take** so the coder always has a real diff.
- After any **orchestrator redeploy**, re-pair (`scripts/cloud-paircode.sh`); run
  `gcloud auth login` first if the log-read step fails.
- The coder runs one task at a time — don't fire two runs at once.
- For a fully scripted alternative, want the gates approved *on camera* instead?
  Skip the auto-approver and click **Approve** in the dashboard at 0:40 and 1:20.
