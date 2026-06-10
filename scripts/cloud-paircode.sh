#!/usr/bin/env bash
# Pair a client with the Revka orchestrator on Cloud Run and print a reusable
# bearer token.
#
# Two modes:
#   1. Admin mode (repeatable, preferred): if you already hold a valid bearer
#      token, set REVKA_ADMIN_TOKEN and this mints a FRESH one-time code via the
#      authenticated /api/pairing/initiate endpoint — no log access, no restart.
#   2. Bootstrap mode (first device): with no admin token, the one-time code is
#      read from the service's startup logs (requires gcloud + Logs Viewer; the
#      admin code endpoints are loopback-only by design).
#
# The printed bearer token is durable — share THAT with judges/teammates, not
# the one-time code. A code is consumed on first successful pair.
#
# Usage:
#   REVKA_ADMIN_TOKEN=rk_xxx DEVICE=judges scripts/cloud-paircode.sh   # admin mode
#   DEVICE=first-device scripts/cloud-paircode.sh                      # bootstrap mode
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

if [ -n "${REVKA_ADMIN_TOKEN:-}" ]; then
  echo "==> Minting a fresh pairing code via authenticated endpoint (admin mode)"
  CODE=$(curl -fsS -X POST "$URL/api/pairing/initiate" \
           -H "Authorization: Bearer $REVKA_ADMIN_TOKEN" \
         | python3 -c 'import sys,json; print(json.load(sys.stdin).get("pairing_code",""))')
  [ -n "$CODE" ] || { echo "ERROR: initiate returned no code (token valid? has the right scope?)" >&2; exit 1; }
else
  echo "==> Reading the latest one-time pairing code from startup logs (bootstrap mode)"
  echo "    (tip: set REVKA_ADMIN_TOKEN to an existing token to skip log access)"
  CODE=$(gcloud logging read \
    "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"$SERVICE\" AND textPayload:\"X-Pairing-Code:\"" \
    --project "$PROJECT" --limit=1 --order=desc --format='value(textPayload)' \
    | grep -oE 'X-Pairing-Code: [0-9]+' | grep -oE '[0-9]+' | head -1)
  [ -n "$CODE" ] || { echo "ERROR: no pairing code in logs" >&2; exit 1; }
fi
echo "    code: $CODE"

echo "==> Exchanging the code for a bearer token (device: $DEVICE)"
RESP=$(curl -sS -X POST "$URL/pair" \
  -H "X-Pairing-Code: $CODE" \
  -H "Content-Type: application/json" \
  -d "{\"device_name\":\"$DEVICE\"}")

TOKEN=$(printf '%s' "$RESP" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("token",""))' 2>/dev/null || true)
if [ -z "$TOKEN" ]; then
  echo "ERROR: pairing failed — response was:" >&2
  printf '%s\n' "$RESP" >&2
  echo >&2
  echo "If 'Too many failed attempts': the brute-force limiter is cooling down — wait the" >&2
  echo "stated seconds and retry. If 'Invalid pairing code': the code was already used or" >&2
  echo "superseded — re-run to mint another (admin mode) or read a fresh one from logs." >&2
  exit 1
fi

cat <<EOF

==================================================================
  Revka orchestrator paired (device: $DEVICE).

  Service URL : $URL
  Bearer token: $TOKEN

  Use it as:   Authorization: Bearer $TOKEN
  Dashboard :  open $URL/ and paste the token
  Health    :  curl -s -H "Authorization: Bearer $TOKEN" "$URL/api/health"
==================================================================
EOF
