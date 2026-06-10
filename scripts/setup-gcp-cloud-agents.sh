#!/usr/bin/env bash
# setup-gcp-deploy.sh — one-time GCP setup for Revka deploy targets
# (project construct-498201). Safe to re-run: every step is idempotent.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-construct-498201}"
REGION="${REGION:-us-central1}"
ORCHESTRATOR_SA="revka-orchestrator@${PROJECT_ID}.iam.gserviceaccount.com"

echo "==> Project: ${PROJECT_ID} (region ${REGION})"

# ---------------------------------------------------------------------------
# Cloud agents (Track 3): coder-agent + reviewer-agent A2A executors
# ---------------------------------------------------------------------------

for AGENT in coder-agent reviewer-agent; do
  SA_EMAIL="${AGENT}@${PROJECT_ID}.iam.gserviceaccount.com"

  if ! gcloud iam service-accounts describe "${SA_EMAIL}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    echo "==> Creating service account ${SA_EMAIL}"
    gcloud iam service-accounts create "${AGENT}" \
      --project "${PROJECT_ID}" \
      --display-name "Revka ${AGENT} (ADK A2A executor)"
  else
    echo "==> Service account ${SA_EMAIL} already exists"
  fi

  echo "==> Granting roles/aiplatform.user to ${SA_EMAIL} (Vertex AI / Gemini via ADC)"
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member "serviceAccount:${SA_EMAIL}" \
    --role roles/aiplatform.user \
    --condition None \
    --quiet >/dev/null

  echo "==> Granting deployer act-as on ${SA_EMAIL} (required for gcloud run deploy)"
  gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
    --member "serviceAccount:revka-deployer@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role roles/iam.serviceAccountUser \
    --quiet >/dev/null

  echo "==> Granting roles/run.invoker on service ${AGENT} to ${ORCHESTRATOR_SA}"
  if ! gcloud run services add-iam-policy-binding "${AGENT}" \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --member "serviceAccount:${ORCHESTRATOR_SA}" \
    --role roles/run.invoker \
    --quiet >/dev/null 2>&1; then
    echo "    (service ${AGENT} not deployed yet — re-run this script after the first deploy)"
  fi
done

# Secret used by both agents for GitHub clone/PR operations.
if gcloud secrets describe revka-GITHUB_TOKEN --project "${PROJECT_ID}" >/dev/null 2>&1; then
  echo "==> Secret revka-GITHUB_TOKEN already exists"
elif [ -n "${GITHUB_PAT:-}" ]; then
  echo "==> Creating secret revka-GITHUB_TOKEN from \$GITHUB_PAT"
  printf '%s' "${GITHUB_PAT}" | gcloud secrets create revka-GITHUB_TOKEN \
    --project "${PROJECT_ID}" \
    --replication-policy automatic \
    --data-file -
else
  cat <<'EOF'
==> Secret revka-GITHUB_TOKEN not created (set GITHUB_PAT and re-run, or run):
    printf '%s' "$YOUR_GITHUB_PAT" | gcloud secrets create revka-GITHUB_TOKEN \
      --project construct-498201 --replication-policy automatic --data-file -
EOF
fi

# Allow the agent runtime SAs to read the GitHub token secret.
if gcloud secrets describe revka-GITHUB_TOKEN --project "${PROJECT_ID}" >/dev/null 2>&1; then
  for AGENT in coder-agent reviewer-agent; do
    SA_EMAIL="${AGENT}@${PROJECT_ID}.iam.gserviceaccount.com"
    echo "==> Granting roles/secretmanager.secretAccessor on revka-GITHUB_TOKEN to ${SA_EMAIL}"
    gcloud secrets add-iam-policy-binding revka-GITHUB_TOKEN \
      --project "${PROJECT_ID}" \
      --member "serviceAccount:${SA_EMAIL}" \
      --role roles/secretmanager.secretAccessor \
      --quiet >/dev/null
  done
fi

echo "==> Done."
