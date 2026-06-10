# syntax=docker/dockerfile:1.7

# ── Stage 0: Frontend build ─────────────────────────────────────
FROM node:22-alpine AS web-builder
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm ci --ignore-scripts 2>/dev/null || npm install --ignore-scripts
COPY web/ .
RUN npm run build

# ── Stage 1: Build ────────────────────────────────────────────
FROM rust:1.95-slim@sha256:81099830a1e1d244607b9a7a30f3ff6ecadc52134a933b4635faba24f52840c9 AS builder

WORKDIR /app
ARG REVKA_CARGO_FEATURES="channel-lark,whatsapp-web"

# Install build dependencies
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

# 1. Copy manifests to cache dependencies
COPY Cargo.toml Cargo.lock ./
# Include every workspace member: Cargo.lock is generated for the full workspace.
# Previously we used sed to drop `crates/robot-kit`, which made the manifest disagree
# with the lockfile and caused `cargo --locked` to fail (Cargo refused to rewrite the lock).
COPY crates/robot-kit/ crates/robot-kit/
COPY crates/aardvark-sys/ crates/aardvark-sys/
# Include tauri workspace member manifest (desktop app, but needed for workspace resolution).
# .dockerignore whitelists only Cargo.toml; src and build.rs are stubbed below.
COPY apps/tauri/Cargo.toml apps/tauri/Cargo.toml
# Vendored security patches referenced by [patch] entries in Cargo.toml —
# required even for the dependency-caching build.
COPY vendor/ vendor/
# Create dummy targets declared in Cargo.toml so manifest parsing succeeds.
RUN mkdir -p src benches apps/tauri/src \
    && echo "fn main() {}" > src/main.rs \
    && echo "" > src/lib.rs \
    && echo "fn main() {}" > benches/agent_benchmarks.rs \
    && echo "fn main() {}" > apps/tauri/src/main.rs \
    && echo "fn main() {}" > apps/tauri/build.rs
RUN --mount=type=cache,id=revka-cargo-registry,target=/usr/local/cargo/registry,sharing=locked \
    --mount=type=cache,id=revka-cargo-git,target=/usr/local/cargo/git,sharing=locked \
    --mount=type=cache,id=revka-target,target=/app/target,sharing=locked \
    if [ -n "$REVKA_CARGO_FEATURES" ]; then \
      cargo build --release --locked --features "$REVKA_CARGO_FEATURES"; \
    else \
      cargo build --release --locked; \
    fi
RUN rm -rf src benches

# 2. Copy only build-relevant source paths (avoid cache-busting on docs/tests/scripts)
COPY src/ src/
COPY benches/ benches/
COPY --from=web-builder /web/dist web/dist
COPY *.rs .
RUN touch src/main.rs
RUN --mount=type=cache,id=revka-cargo-registry,target=/usr/local/cargo/registry,sharing=locked \
    --mount=type=cache,id=revka-cargo-git,target=/usr/local/cargo/git,sharing=locked \
    --mount=type=cache,id=revka-target,target=/app/target,sharing=locked \
    rm -rf target/release/.fingerprint/kumiho-revka-* \
           target/release/.fingerprint/revka-* \
           target/release/deps/revka-* \
           target/release/deps/kumihoio_revka-* \
           target/release/incremental/revka-* \
           target/release/incremental/kumihoio_revka-* && \
    if [ -n "$REVKA_CARGO_FEATURES" ]; then \
      cargo build --release --locked --features "$REVKA_CARGO_FEATURES"; \
    else \
      cargo build --release --locked; \
    fi && \
    cp target/release/revka /app/revka && \
    strip /app/revka
RUN size=$(stat -c%s /app/revka) && \
    if [ "$size" -lt 1000000 ]; then echo "ERROR: binary too small (${size} bytes), likely dummy build artifact" && exit 1; fi

# Prepare runtime directory structure and default config inline (no extra stage)
RUN mkdir -p /revka-data/.revka /revka-data/workspace && \
    printf '%s\n' \
        'workspace_dir = "/revka-data/workspace"' \
        'config_path = "/revka-data/.revka/config.toml"' \
        'api_key = ""' \
        'default_provider = "openrouter"' \
        'default_model = "anthropic/claude-sonnet-4-20250514"' \
        'default_temperature = 0.7' \
        '' \
        '[gateway]' \
        'port = 42617' \
        'host = "[::]"' \
        'allow_public_bind = true' \
        'require_pairing = false' \
        '' \
        '[autonomy]' \
        'level = "supervised"' \
        'auto_approve = ["file_read", "file_write", "file_edit", "web_search_tool", "web_fetch", "calculator", "glob_search", "content_search", "image_info", "weather", "git_operations"]' \
        > /revka-data/.revka/config.toml && \
    chown -R 65534:65534 /revka-data

# ── Stage 2: Development Runtime (Debian) ────────────────────
FROM debian:trixie-slim@sha256:f6e2cfac5cf956ea044b4bd75e6397b4372ad88fe00908045e9a0d21712ae3ba AS dev

# Install essential runtime dependencies only (use docker-compose.override.yml for dev tools)
RUN apt-get update && apt-get install -y \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /revka-data /revka-data
COPY --from=builder /app/revka /usr/local/bin/revka

# Overwrite minimal config with DEV template (Ollama defaults)
COPY dev/config.template.toml /revka-data/.revka/config.toml
RUN chown 65534:65534 /revka-data/.revka/config.toml

# Environment setup
# Ensure UTF-8 locale so CJK / multibyte input is handled correctly
ENV LANG=C.UTF-8
# Use consistent workspace path
ENV REVKA_WORKSPACE=/revka-data/workspace
ENV HOME=/revka-data
# Defaults for local dev (Ollama) - matches config.template.toml
ENV PROVIDER="ollama"
ENV REVKA_MODEL="llama3.2"
ENV REVKA_GATEWAY_PORT=42617

# Note: API_KEY is intentionally NOT set here to avoid confusion.
# It is set in config.toml as the Ollama URL.

WORKDIR /revka-data
USER 65534:65534
EXPOSE 42617
HEALTHCHECK --interval=60s --timeout=10s --retries=3 --start-period=10s \
    CMD ["revka", "status", "--format=exit-code"]
ENTRYPOINT ["revka"]
CMD ["daemon"]

# ── Stage 3: Production Runtime (Distroless) ─────────────────
FROM gcr.io/distroless/cc-debian13:nonroot@sha256:84fcd3c223b144b0cb6edc5ecc75641819842a9679a3a58fd6294bec47532bf7 AS release

COPY --from=builder /app/revka /usr/local/bin/revka
COPY --from=builder /revka-data /revka-data

# Environment setup
# Ensure UTF-8 locale so CJK / multibyte input is handled correctly
ENV LANG=C.UTF-8
ENV REVKA_WORKSPACE=/revka-data/workspace
ENV HOME=/revka-data
# Default provider and model are set in config.toml, not here,
# so config file edits are not silently overridden
#ENV PROVIDER=
ENV REVKA_GATEWAY_PORT=42617

# API_KEY must be provided at runtime!

WORKDIR /revka-data
USER 65534:65534
EXPOSE 42617
HEALTHCHECK --interval=60s --timeout=10s --retries=3 --start-period=10s \
    CMD ["revka", "status", "--format=exit-code"]
ENTRYPOINT ["revka"]
CMD ["daemon"]

# ── Stage 4: Cloud Run Runtime ────────────────────────────────
# Full agentic runtime for Google Cloud Run: daemon + Operator MCP and
# Kumiho memory sidecars (Python venvs) + session-manager (Node). Reasoning
# runs on Gemini; no CLI-agent logins exist in this image by design —
# workflow executors are reached over A2A.
FROM debian:trixie-slim AS cloudrun

RUN apt-get update && apt-get install -y \
    ca-certificates \
    curl \
    git \
    python3 \
    python3-venv \
    rsync \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/revka /usr/local/bin/revka
COPY --from=builder /revka-data /revka-data

# Sidecars: install Operator MCP + Kumiho memory venvs under /revka-data/.revka
# using the same installer `revka install --sidecars-only` wraps locally.
COPY operator-mcp/ /opt/revka-src/operator-mcp/
COPY scripts/install-sidecars.sh /opt/revka-src/scripts/install-sidecars.sh
RUN HOME=/revka-data bash /opt/revka-src/scripts/install-sidecars.sh

# Session manager (Node sidecar): committed dist + production deps.
RUN mkdir -p /revka-data/.revka/operator_mcp/session-manager
COPY operator-mcp/session-manager/dist/ /revka-data/.revka/operator_mcp/session-manager/dist/
COPY operator-mcp/session-manager/package.json /revka-data/.revka/operator_mcp/session-manager/package.json
RUN cd /revka-data/.revka/operator_mcp/session-manager \
    && npm install --omit=dev --no-audit --no-fund

# Cloud Run config: Gemini provider, public bind (Cloud Run fronts TLS/ingress),
# PORT env from Cloud Run overrides gateway.port at load time.
COPY dev/config.cloudrun.toml /revka-data/.revka/config.toml

RUN chown -R 65534:65534 /revka-data

ENV LANG=C.UTF-8
ENV REVKA_WORKSPACE=/revka-data/workspace
ENV HOME=/revka-data

WORKDIR /revka-data
USER 65534:65534
EXPOSE 42617
ENTRYPOINT ["revka"]
CMD ["daemon"]
