#!/usr/bin/env bash
set -euo pipefail

if [ -f "dev/docker-compose.ci.yml" ]; then
  COMPOSE_FILE="dev/docker-compose.ci.yml"
elif [ -f "docker-compose.ci.yml" ] && [ "$(basename "$(pwd)")" = "dev" ]; then
  COMPOSE_FILE="docker-compose.ci.yml"
else
  echo "❌ Run this script from repo root or dev/ directory."
  exit 1
fi

compose_cmd=(docker compose -f "$COMPOSE_FILE")
SMOKE_CACHE_DIR="${SMOKE_CACHE_DIR:-.cache/buildx-smoke}"

run_in_ci() {
  local cmd="$1"
  "${compose_cmd[@]}" run --rm local-ci bash -c "$cmd"
}

build_smoke_image() {
  if docker buildx version >/dev/null 2>&1; then
    mkdir -p "$SMOKE_CACHE_DIR"
    local build_args=(
      --load
      --target dev
      --cache-to "type=local,dest=$SMOKE_CACHE_DIR,mode=max"
      -t revka-local-smoke:latest
      .
    )
    if [ -f "$SMOKE_CACHE_DIR/index.json" ]; then
      build_args=(--cache-from "type=local,src=$SMOKE_CACHE_DIR" "${build_args[@]}")
    fi
    docker buildx build "${build_args[@]}"
  else
    DOCKER_BUILDKIT=1 docker build --target dev -t revka-local-smoke:latest .
  fi
}

print_help() {
  cat <<'EOF'
Revka Local CI in Docker

Usage: ./dev/ci.sh <command>

Commands:
  build-image   Build/update the local CI image
  shell         Open an interactive shell inside the CI container
  lint          Run rustfmt + clippy correctness gate (container only)
  lint-strict   Run rustfmt + full clippy warnings gate (container only)
  lint-delta    Run strict lint delta gate on changed Rust lines (container only)
  test          Run cargo test (container only)
  test-component  Run component tests only
  test-integration Run integration tests only
  test-system     Run system tests only
  test-live       Run live tests (requires credentials)
  test-features   Run feature-gated channel tests (matrix/lark/nostr/whatsapp-web)
  test-manual     Run manual test scripts (dockerignore, etc.)
  build         Run release build smoke check (container only)
  audit         Run cargo deny advisories check (container only)
  deny          Run cargo deny check (container only)
  security      Run cargo deny full policy check (container only)
  docker-smoke  Build and verify runtime image (host docker daemon)
  docker-nonroot Build the production image and assert it runs non-root (USER != 0)
  all           Run lint, test, build, security, docker-smoke
  clean         Remove local CI containers and volumes
EOF
}

if [ $# -lt 1 ]; then
  print_help
  exit 1
fi

case "$1" in
  build-image)
    "${compose_cmd[@]}" build local-ci
    ;;

  shell)
    "${compose_cmd[@]}" run --rm local-ci bash
    ;;

  lint)
    run_in_ci "./scripts/ci/rust_quality_gate.sh"
    ;;

  lint-strict)
    run_in_ci "./scripts/ci/rust_quality_gate.sh --strict"
    ;;

  lint-delta)
    run_in_ci "./scripts/ci/rust_strict_delta_gate.sh"
    ;;

  test)
    run_in_ci "cargo test --locked --verbose"
    ;;

  test-component)
    run_in_ci "cargo test --test component --locked --verbose"
    ;;

  test-integration)
    run_in_ci "cargo test --test integration --locked --verbose"
    ;;

  test-system)
    run_in_ci "cargo test --test system --locked --verbose"
    ;;

  test-live)
    run_in_ci "cargo test --test live -- --ignored --verbose"
    ;;

  test-manual)
    run_in_ci "bash tests/manual/test_dockerignore.sh"
    ;;

  test-features)
    # Feature-gated channel tests (#433). voice-wake is omitted because the
    # local CI image lacks libasound2-dev (cpal/ALSA); the GitHub `test-features`
    # job installs it and additionally covers voice-wake.
    run_in_ci "cargo test --locked --verbose --features channel-matrix,channel-lark,channel-nostr,whatsapp-web"
    ;;

  build)
    run_in_ci "cargo build --release --locked --verbose"
    ;;

  audit)
    run_in_ci "cargo deny check advisories"
    ;;

  deny)
    run_in_ci "cargo deny check all"
    ;;

  security)
    run_in_ci "cargo deny check all"
    ;;

  docker-smoke)
    build_smoke_image
    docker run --rm revka-local-smoke:latest --version
    ;;

  docker-nonroot)
    # Runtime assertion: build the production image and verify it ships non-root.
    # Complements the static, merge-blocking scripts/ci/check_docker_nonroot.py
    # gate with a real `docker inspect` against the built image.
    DOCKER_BUILDKIT=1 docker build --target release -t revka-nonroot-check:latest .
    user="$(docker inspect --format '{{.Config.User}}' revka-nonroot-check:latest)"
    uid="${user%%:*}"
    echo "production (release) image USER = '${user}'"
    if [ -z "$user" ] || [ "$uid" = "0" ] || [ "$uid" = "root" ]; then
      echo "❌ production image runs as root (USER='${user}')"
      exit 1
    fi
    echo "✅ production image is non-root (USER='${user}')"
    ;;

  all)
    run_in_ci "./scripts/ci/rust_quality_gate.sh"
    run_in_ci "cargo test --locked --verbose"
    run_in_ci "cargo test --locked --verbose --features channel-matrix,channel-lark,channel-nostr,whatsapp-web"
    run_in_ci "bash tests/manual/test_dockerignore.sh"
    run_in_ci "cargo build --release --locked --verbose"
    run_in_ci "cargo deny check all"
    build_smoke_image
    docker run --rm revka-local-smoke:latest --version
    ;;

  clean)
    "${compose_cmd[@]}" down -v --remove-orphans
    ;;

  *)
    print_help
    exit 1
    ;;
esac
