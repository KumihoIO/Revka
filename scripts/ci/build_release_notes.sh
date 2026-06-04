#!/usr/bin/env bash
# Generate release notes via git-cliff + contributor block.
#
# Usage: build_release_notes.sh <prev_tag_or_empty> <current_ref>
#   prev_tag_or_empty  Previous release tag, or "" to auto-detect previous stable
#   current_ref        Current ref (HEAD or a concrete tag like v2026.4.21)
#
# Writes three multiline values to $GITHUB_OUTPUT:
#   body         — markdown release notes
#   features     — features bullet list (for downstream consumers)
#   contributors — contributor bullet list

set -euo pipefail

PREV_TAG="${1:-}"
CURRENT_REF="${2:-HEAD}"

if [ -z "$PREV_TAG" ]; then
  PREV_TAG=$(git tag --sort=-creatordate \
    | grep -vE '\-(beta|rc|alpha)\.' \
    | head -1 || echo "")
fi

if [ -z "$PREV_TAG" ]; then
  RANGE="$CURRENT_REF"
else
  RANGE="${PREV_TAG}..${CURRENT_REF}"
fi

echo "Generating release notes for range: $RANGE"

# Install git-cliff if not available (cached in the runner).
if ! command -v git-cliff >/dev/null 2>&1; then
  echo "Installing git-cliff..."
  cargo install --locked git-cliff
fi

# Body via git-cliff. Uses cliff.toml at repo root.
if [ -z "$PREV_TAG" ]; then
  CHANGELOG=$(git-cliff --unreleased --strip header --strip footer 2>/dev/null || echo "")
else
  CHANGELOG=$(git-cliff "${PREV_TAG}..${CURRENT_REF}" --strip header --strip footer 2>/dev/null || echo "")
fi

if [ -z "$CHANGELOG" ]; then
  CHANGELOG="- Incremental improvements and polish"
fi

# Extract features section for downstream workflows that need it separately.
FEATURES=$(echo "$CHANGELOG" \
  | awk '/^### Features/{flag=1;next} /^### /{flag=0} flag' \
  | grep -E '^- ' || echo "- Incremental improvements and polish")

# Contributors: git authors + Co-Authored-By, deduplicated, bots filtered.
GIT_AUTHORS=$(git log "$RANGE" --pretty=format:"%an" --no-merges | sort -uf || true)
CO_AUTHORS=$(git log "$RANGE" --pretty=format:"%b" --no-merges \
  | grep -ioE 'Co-Authored-By: *[^<]+' \
  | sed 's/Co-Authored-By: *//i' \
  | sed 's/ *$//' \
  | sort -uf || true)

CONTRIBUTORS=$(printf "%s\n%s" "$GIT_AUTHORS" "$CO_AUTHORS" \
  | sort -uf \
  | grep -v '^$' \
  | grep -viE '\[bot\]$|^dependabot|^github-actions|^copilot|^Revka Bot|^Revka Runner|^Revka Agent|^blacksmith' \
  | while IFS= read -r name; do echo "- ${name}"; done || true)

if [ -z "$CONTRIBUTORS" ]; then
  CONTRIBUTORS="- The Revka team"
fi

BODY=$(cat <<NOTES_EOF
${CHANGELOG}

## Contributors

${CONTRIBUTORS}

---
*Full changelog: ${PREV_TAG:-first-release}...${CURRENT_REF}*
NOTES_EOF
)

# Emit outputs for GitHub Actions.
if [ -n "${GITHUB_OUTPUT:-}" ]; then
  {
    echo "body<<BODY_EOF"
    echo "$BODY"
    echo "BODY_EOF"
  } >> "$GITHUB_OUTPUT"

  {
    echo "features<<FEAT_EOF"
    echo "$FEATURES"
    echo "FEAT_EOF"
  } >> "$GITHUB_OUTPUT"

  {
    echo "contributors<<CONTRIB_EOF"
    echo "$CONTRIBUTORS"
    echo "CONTRIB_EOF"
  } >> "$GITHUB_OUTPUT"
else
  # Local invocation: print to stdout.
  echo "$BODY"
fi
