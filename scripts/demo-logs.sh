#!/usr/bin/env bash
# Live-ish Cloud Run log tail for the Track 3 demo split pane.
#
# Streams recent logs from the three Revka services with a colored service tag,
# so a screen-recording split window shows the agents actually executing on
# Google Cloud (A2A calls, ADK/Vertex reasoning, clone/test/PR) while the Revka
# dashboard drives the run in the other pane.
#
#   bash scripts/demo-logs.sh            # all three services
#   SERVICES="coder-agent" bash scripts/demo-logs.sh   # focus one
#
# Prefers the real-time `gcloud alpha logging tail`; falls back to a 3s poll
# loop (no extra components needed) if tail is unavailable.
set -euo pipefail

PROJECT="${PROJECT:-construct-498201}"
SERVICES="${SERVICES:-revka-orchestrator coder-agent reviewer-agent}"

# Build a Logging filter over the selected services.
names=$(printf '"%s" OR ' $SERVICES); names="(${names% OR })"
FILTER="resource.type=\"cloud_run_revision\" AND resource.labels.service_name=${names}"

color() { case "$1" in
  revka-orchestrator) printf '\033[36m';;   # cyan
  coder-agent)        printf '\033[32m';;    # green
  reviewer-agent)     printf '\033[35m';;    # magenta
  *)                  printf '\033[37m';;
esac; }

emit() { # service \t text
  local svc="${1%%$'\t'*}" txt="${1#*$'\t'}"
  [ -z "$txt" ] && return
  printf '%b%-18s\033[0m │ %s\n' "$(color "$svc")" "$svc" "$txt"
}

echo "── Cloud Run logs · $SERVICES · project $PROJECT ──"

if gcloud alpha logging tail --help >/dev/null 2>&1; then
  gcloud alpha logging tail "$FILTER" --project "$PROJECT" \
    --format='value(resource.labels.service_name, textPayload)' 2>/dev/null \
  | while IFS= read -r line; do emit "$line"; done
else
  echo "(alpha tail unavailable — polling every 3s)"
  seen="/tmp/.demo-logs-seen.$$"; : > "$seen"
  trap 'rm -f "$seen"' EXIT
  while true; do
    gcloud logging read "$FILTER" --project "$PROJECT" --freshness=30s --order=asc \
      --format='value(timestamp, resource.labels.service_name, textPayload)' 2>/dev/null \
    | while IFS= read -r row; do
        key=$(printf '%s' "$row" | cksum | cut -d' ' -f1)
        grep -q "^$key\$" "$seen" 2>/dev/null && continue
        echo "$key" >> "$seen"
        svc=$(printf '%s' "$row" | awk '{print $2}')
        txt=$(printf '%s' "$row" | cut -d$'\t' -f3-)
        emit "$svc"$'\t'"$txt"
      done
    sleep 3
  done
fi
