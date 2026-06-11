#!/usr/bin/env bash
# One-time GCP setup for the Cloud Run deploy pipeline (run as a human with
# owner/editor on the project — Claude is intentionally not allowed to grant IAM).
#
#   bash scripts/setup-gcp-deploy.sh
#
# Creates:
#   - revka-deployer SA (used by GitHub Actions via Workload Identity Federation)
#   - revka-orchestrator SA (Cloud Run runtime identity)
#   - WIF pool/provider trusted for this GitHub repo
#   - Secret Manager secrets from your local ~/.revka/workspace/.env
set -euo pipefail

PROJECT=construct-498201
REGION=us-central1
REPO=KumihoIO/Revka
DEPLOY_SA=revka-deployer
RUNTIME_SA=revka-orchestrator
POOL=github-actions
PROVIDER=github-oidc

PROJECT_NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')

echo "==> Service accounts"
gcloud iam service-accounts create "$DEPLOY_SA" --project="$PROJECT" \
  --display-name="Revka CI deployer (GitHub Actions)" 2>/dev/null || true
gcloud iam service-accounts create "$RUNTIME_SA" --project="$PROJECT" \
  --display-name="Revka orchestrator runtime (Cloud Run)" 2>/dev/null || true

echo "==> Deployer roles (push images, deploy Cloud Run, act-as runtime SA)"
for role in roles/artifactregistry.writer roles/run.admin; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:${DEPLOY_SA}@${PROJECT}.iam.gserviceaccount.com" \
    --role="$role" --condition=None >/dev/null
done
gcloud iam service-accounts add-iam-policy-binding \
  "${RUNTIME_SA}@${PROJECT}.iam.gserviceaccount.com" \
  --member="serviceAccount:${DEPLOY_SA}@${PROJECT}.iam.gserviceaccount.com" \
  --role=roles/iam.serviceAccountUser >/dev/null

echo "==> Runtime roles (Vertex reasoning + secrets)"
for role in roles/aiplatform.user roles/secretmanager.secretAccessor roles/run.invoker; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:${RUNTIME_SA}@${PROJECT}.iam.gserviceaccount.com" \
    --role="$role" --condition=None >/dev/null
done

echo "==> Workload Identity Federation for GitHub Actions (keyless)"
gcloud iam workload-identity-pools create "$POOL" --project="$PROJECT" \
  --location=global --display-name="GitHub Actions" 2>/dev/null || true
gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
  --project="$PROJECT" --location=global --workload-identity-pool="$POOL" \
  --display-name="GitHub OIDC" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='${REPO}'" 2>/dev/null || true
gcloud iam service-accounts add-iam-policy-binding \
  "${DEPLOY_SA}@${PROJECT}.iam.gserviceaccount.com" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${REPO}" \
  --role=roles/iam.workloadIdentityUser >/dev/null

echo "==> Secret Manager secrets from ~/.revka/workspace/.env"
ENVFILE="$HOME/.revka/workspace/.env"
for key in KUMIHO_SERVICE_TOKEN GEMINI_OAUTH_CLIENT_ID GEMINI_OAUTH_CLIENT_SECRET GEMINI_API_KEY; do
  val=$(grep -E "^${key}=" "$ENVFILE" | head -1 | cut -d= -f2- || true)
  if [ -n "${val:-}" ]; then
    printf '%s' "$val" | gcloud secrets create "revka-${key}" --project="$PROJECT" \
      --data-file=- 2>/dev/null \
      || printf '%s' "$val" | gcloud secrets versions add "revka-${key}" \
           --project="$PROJECT" --data-file=- >/dev/null
    echo "    revka-${key}: ok"
  else
    echo "    revka-${key}: SKIPPED (not in .env)"
  fi
done

# Stable pre-shared admin bearer token for the dashboard/API. Survives
# redeploys (unlike runtime-paired tokens). Generated once; reused thereafter.
if gcloud secrets describe revka-GATEWAY_ADMIN_TOKEN --project="$PROJECT" >/dev/null 2>&1; then
  echo "    revka-GATEWAY_ADMIN_TOKEN: exists (keeping)"
else
  ADMIN_TOKEN="rk_$(head -c 32 /dev/urandom | xxd -p -c 64)"
  printf '%s' "$ADMIN_TOKEN" | gcloud secrets create revka-GATEWAY_ADMIN_TOKEN \
    --project="$PROJECT" --data-file=- >/dev/null
  echo "    revka-GATEWAY_ADMIN_TOKEN: created"
  echo "    >>> SAVE THIS DASHBOARD TOKEN (shown once): $ADMIN_TOKEN"
fi
echo "    (grant the runtime SA access:)"
gcloud secrets add-iam-policy-binding revka-GATEWAY_ADMIN_TOKEN --project="$PROJECT" \
  --member="serviceAccount:${RUNTIME_SA}@${PROJECT}.iam.gserviceaccount.com" \
  --role=roles/secretmanager.secretAccessor >/dev/null 2>&1 || true

echo
echo "Done. GitHub workflow values:"
echo "  workload_identity_provider: projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"
echo "  service_account: ${DEPLOY_SA}@${PROJECT}.iam.gserviceaccount.com"
