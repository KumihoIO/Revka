#!/usr/bin/env bash
# Pair a client with the Revka orchestrator running on Cloud Run, and print a
# reusable bearer token.
#
# The /admin/paircode endpoints are loopback-only by design, and Cloud Run has
# no `docker exec`, so the one-time pairing code is read from the service's
# startup logs (requires `gcloud` auth with Logs Viewer on the project). The
# returned bearer token is durable — share THAT with judges, not the code.
#
# Usage:
#   scripts/cloud-paircode.sh                       # uses defaults below
#   DEVICE=judges scripts/cloud-paircode.sh         # name the paired device
#   SERVICE=revka-orchestrator REGION=us-central1 PROJECT=construct-498201 \
#     scripts/cloud-paircode.sh
set -euo pipefail

PROJECT="${PROJECT:-construct-498201}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-revka-orchestrator}"
DEVICE="${DEVICE:-judge-device}"

echo "==> Resolving service URL ($SERVICE / $REGION)"
URL=$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" \
        --format='value(status.url)')
[ -n "$URL" ] || { echo "ERROR: could not resolve $SERVICE URL" >&2; exit 1; }
echo "    $URL"

echo "==> Reading the latest one-time pairing code from startup logs"
CODE=$(gcloud logging read \
  "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"$SERVICE\" AND textPayload:\"X-Pairing-Code:\"" \
  --project "$PROJECT" --limit=1 --order=desc --format='value(textPayload)' \
  | grep -oE 'X-Pairing-Code: [0-9]+' | grep -oE '[0-9]+' | head -1)
[ -n "$CODE" ] || { echo "ERROR: no pairing code in logs (has the service started? is pairing enabled?)" >&2; exit 1; }
echo "    code: $CODE"

echo "==> Exchanging the code for a bearer token (device: $DEVICE)"
RESP=$(curl -fsS -X POST "$URL/pair" \
  -H "X-Pairing-Code: $CODE" \
  -H "Content-Type: application/json" \
  -d "{\"device_name\":\"$DEVICE\"}")

TOKEN=$(printf '%s' "$RESP" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("token",""))' 2>/dev/null || true)
if [ -z "$TOKEN" ]; then
  echo "ERROR: pairing failed — response was:" >&2
  printf '%s\n' "$RESP" >&2
  echo "(The code is one-time; if it was already used, redeploy or restart the service to mint a new one.)" >&2
  exit 1
fi

cat <<EOF

==================================================================
  Revka orchestrator paired.

  Service URL : $URL
  Bearer token: $TOKEN

  Use it as:
    Authorization: Bearer $TOKEN

  Dashboard (paste the token when prompted):
    $URL/

  Quick check:
    curl -s -H "Authorization: Bearer $TOKEN" "$URL/api/health"
==================================================================
EOF
