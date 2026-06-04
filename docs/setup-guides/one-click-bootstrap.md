# One-Click Bootstrap

This page defines the fastest supported path to install and initialize Revka.

Last verified: **April 21, 2026**.

## Option 0: Homebrew (macOS/Linuxbrew)

```bash
brew install revka
```

## Option A (Recommended): Clone + local script

```bash
git clone https://github.com/KumihoIO/Revka.git
cd revka
./install.sh
```

What it does by default:

1. `cargo build --release --locked`
2. `cargo install --path . --force --locked`

### Resource preflight and pre-built flow

Source builds typically require at least:

- **2 GB RAM + swap**
- **6 GB free disk**

When resources are constrained, bootstrap now attempts a pre-built binary first.

```bash
./install.sh --prefer-prebuilt
```

To require binary-only installation and fail if no compatible release asset exists:

```bash
./install.sh --prebuilt-only
```

To bypass pre-built flow and force source compilation:

```bash
./install.sh --force-source-build
```

## Dual-mode bootstrap

Default behavior is **app-only** (build/install Revka) and expects existing Rust toolchain.

For fresh machines, enable environment bootstrap explicitly:

```bash
./install.sh --install-system-deps --install-rust
```

Notes:

- `--install-system-deps` installs compiler/build prerequisites (may require `sudo`).
- `--install-rust` installs Rust via `rustup` when missing.
- `--prefer-prebuilt` tries release binary download first, then falls back to source build.
- `--prebuilt-only` disables source fallback.
- `--force-source-build` disables pre-built flow entirely.

## Option B: Remote one-liner

```bash
curl -fsSL https://raw.githubusercontent.com/KumihoIO/Revka/main/install.sh | bash
```

For high-security environments, prefer Option A so you can review the script before execution.

If you run Option B outside a repository checkout, the install script automatically clones a temporary workspace, builds, installs, and then cleans it up.

## Optional onboarding modes

<!-- TODO screenshot: Docker container running Revka showing the onboarding UI in a browser -->
![Docker container running Revka showing the onboarding UI in a browser](../assets/setup/one-click-bootstrap-02-docker-onboarding.png)

### Containerized onboarding (Docker)

```bash
./install.sh --docker
```

This builds a local Revka image and launches onboarding inside a container while
persisting config/workspace to `./.revka-docker`.

Container CLI defaults to `docker`. If Docker CLI is unavailable and `podman` exists,
the installer auto-falls back to `podman`. You can also set `REVKA_CONTAINER_CLI`
explicitly (for example: `REVKA_CONTAINER_CLI=podman ./install.sh --docker`).

For Podman, the installer runs with `--userns keep-id` and `:Z` volume labels so
workspace/config mounts remain writable inside the container.

If you add `--skip-build`, the installer skips local image build. It first tries the local
Docker tag (`REVKA_DOCKER_IMAGE`, default: `revka-bootstrap:local`); if missing,
it pulls `ghcr.io/kumihoio/revka:latest` and tags it locally before running.

### Stopping and restarting a Docker/Podman container

After `./install.sh --docker` finishes, the container exits. Your config and workspace
are persisted in the data directory (default: `./.revka-docker`, or `~/.revka-docker`
when bootstrapping via `curl | bash`). You can override this path with `REVKA_DOCKER_DATA_DIR`.

**Do not re-run `install.sh`** to restart -- it will rebuild the image and re-run onboarding.
Instead, start a new container from the existing image and mount the persisted data directory.

#### Using the repository docker-compose.yml

The simplest way to run Revka long-term in Docker/Podman is with the provided
`docker-compose.yml` at the repository root. It uses a named volume (`revka-data`)
and sets `restart: unless-stopped` so the container survives reboots.

```bash
# Start (detached)
docker compose up -d

# Stop
docker compose down

# Restart after stopping
docker compose up -d
```

Replace `docker` with `podman` if you use Podman.

#### Manual container run (using install.sh data directory)

If you installed via `./install.sh --docker` and want to reuse the `.revka-docker`
data directory without compose:

```bash
# Docker
docker run -d --name revka \
  --restart unless-stopped \
  -v "$PWD/.revka-docker/.revka:/revka-data/.revka" \
  -v "$PWD/.revka-docker/workspace:/revka-data/workspace" \
  -e HOME=/revka-data \
  -e REVKA_WORKSPACE=/revka-data/workspace \
  -p 42617:42617 \
  revka-bootstrap:local \
  gateway

# Podman (add --userns keep-id and :Z volume labels)
podman run -d --name revka \
  --restart unless-stopped \
  --userns keep-id \
  --user "$(id -u):$(id -g)" \
  -v "$PWD/.revka-docker/.revka:/revka-data/.revka:Z" \
  -v "$PWD/.revka-docker/workspace:/revka-data/workspace:Z" \
  -e HOME=/revka-data \
  -e REVKA_WORKSPACE=/revka-data/workspace \
  -p 42617:42617 \
  revka-bootstrap:local \
  gateway
```

#### Common lifecycle commands

```bash
# Stop the container (preserves data)
docker stop revka

# Start a stopped container (config and workspace are intact)
docker start revka

# View logs
docker logs -f revka

# Remove the container (data in volumes/.revka-docker is preserved)
docker rm revka

# Check health
docker exec revka revka status
```

#### Environment variables

When running manually, pass provider configuration as environment variables
or ensure they are already saved in the persisted `config.toml`:

```bash
docker run -d --name revka \
  -e API_KEY="sk-..." \
  -e PROVIDER="openrouter" \
  -v "$PWD/.revka-docker/.revka:/revka-data/.revka" \
  -v "$PWD/.revka-docker/workspace:/revka-data/workspace" \
  -p 42617:42617 \
  revka-bootstrap:local \
  gateway
```

If you already ran `onboard` during the initial install, your API key and provider are
saved in `.revka-docker/.revka/config.toml` and do not need to be passed again.

### Quick onboarding (non-interactive)

```bash
./install.sh --api-key "sk-..." --provider openrouter
```

Or with environment variables:

```bash
REVKA_API_KEY="sk-..." REVKA_PROVIDER="openrouter" ./install.sh
```

## Useful flags

- `--install-system-deps`
- `--install-rust`
- `--skip-build` (in `--docker` mode: use local image if present, otherwise pull `ghcr.io/kumihoio/revka:latest`)
- `--skip-install`
- `--provider <id>`

See all options:

```bash
./install.sh --help
```

<!-- TODO screenshot: Revka dashboard initial state after successful one-click bootstrap -->
![Revka dashboard initial state after successful one-click bootstrap](../assets/setup/one-click-bootstrap-01-dashboard-initial.png)

## After Bootstrap

Once the installer finishes, the fastest path to a live Revka:

```bash
# Start the gateway (embedded React web dashboard + REST API + WebSocket)
revka gateway

# Or run the full supervised runtime (gateway + channels + heartbeat + cron)
revka daemon
```

The web dashboard is served at `http://127.0.0.1:42617/`. See the root
[README.md](../../README.md) for the full feature map (Kumiho graph memory,
Operator workflows, ClawHub, A2A, trust scoring) and
[dashboard-dev.md](dashboard-dev.md) if you plan to iterate on the frontend.

Kumiho (FastAPI + Neo4j) and the Operator MCP are optional at runtime but
enabled by default in `~/.revka/config.toml` under `[kumiho]` and
`[operator]` — disable them there if you are not running those sidecars.

`install.sh` (and `setup.bat`) now auto-install the **Kumiho** and **Operator**
Python MCP sidecars under `~/.revka/` when a source checkout is present
and the launchers are missing. See
[kumiho-operator-setup.md](kumiho-operator-setup.md) for the full walkthrough,
manual steps, and verification commands. Disable with `--skip-sidecars`.

## Related docs

- [README.md](../README.md)
- [commands-reference.md](../reference/cli/commands-reference.md)
- [providers-reference.md](../reference/api/providers-reference.md)
- [channels-reference.md](../reference/api/channels-reference.md)
- [dashboard-dev.md](dashboard-dev.md)
