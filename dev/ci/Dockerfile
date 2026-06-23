# syntax=docker/dockerfile:1.7

FROM rust:1.92-slim@sha256:bf3368a992915f128293ac76917ab6e561e4dda883273c8f5c9f6f8ea37a378e

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    pkg-config \
    libssl-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN rustup toolchain install 1.92.0 --profile minimal --component rustfmt --component clippy

# cargo-deny >= 0.19 is required to parse the current RustSec advisory DB:
# 0.18.x's bundled cvss crate cannot parse CVSS v4.0 advisories (e.g.
# RUSTSEC-2026-0146), and cargo-deny loads the whole DB up front, so a single
# v4.0 entry makes `cargo deny check advisories`/`all` abort. 0.19.x also flags
# the duplicate-version set that deny.toml's [[bans.skip]] allowlist is tuned to.
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    cargo install --locked cargo-deny --version 0.19.9

WORKDIR /workspace

CMD ["bash"]
