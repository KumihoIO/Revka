# Revka Operations Runbook

This runbook is for operators who maintain availability, security posture, and incident response.

Last verified: **April 21, 2026**.

## Scope

Use this document for day-2 operations:

- starting and supervising runtime
- health checks and diagnostics
- safe rollout and rollback
- incident triage and recovery

For first-time installation, start from [one-click-bootstrap.md](../setup-guides/one-click-bootstrap.md).

## Runtime Modes

| Mode | Command | When to use |
|---|---|---|
| Foreground runtime | `revka daemon` | local debugging, short-lived sessions |
| Foreground gateway only | `revka gateway` | webhook endpoint testing |
| User service | `revka service install && revka service start` | persistent operator-managed runtime |
| Docker / Podman | `docker compose up -d` | containerized deployment |

## Docker / Podman Runtime

If you installed via `./install.sh --docker`, the container exits after onboarding. To run
Revka as a long-lived container, use the repository `docker-compose.yml` or start a
container manually against the persisted data directory.

### Recommended: docker-compose

```bash
# Start (detached, auto-restarts on reboot)
docker compose up -d

# Stop
docker compose down

# Restart
docker compose up -d
```

Replace `docker` with `podman` if using Podman.

### Manual container lifecycle

```bash
# Start a new container from the bootstrap image
docker run -d --name revka \
  --restart unless-stopped \
  -v "$PWD/.revka-docker/.revka:/revka-data/.revka" \
  -v "$PWD/.revka-docker/workspace:/revka-data/workspace" \
  -e HOME=/revka-data \
  -e REVKA_WORKSPACE=/revka-data/workspace \
  -p 42617:42617 \
  revka-bootstrap:local \
  gateway

# Stop (preserves config and workspace)
docker stop revka

# Restart a stopped container
docker start revka

# View logs
docker logs -f revka

# Health check
docker exec revka revka status
```

For Podman, add `--userns keep-id --user "$(id -u):$(id -g)"` and append `:Z` to volume mounts.

### Key detail: do not re-run install.sh to restart

Re-running `install.sh --docker` rebuilds the image and re-runs onboarding. To simply
restart, use `docker start`, `docker compose up -d`, or `podman start`.

For full setup instructions, see [one-click-bootstrap.md](../setup-guides/one-click-bootstrap.md#stopping-and-restarting-a-dockerpodman-container).

## Baseline Operator Checklist

1. Validate configuration:

```bash
revka status
```

2. Verify diagnostics:

```bash
revka doctor
revka channel doctor
```

3. Start runtime:

```bash
revka daemon
```

4. For persistent user session service:

```bash
revka service install
revka service start
revka service status
```

<!-- TODO screenshot: Revka dashboard Audit view displaying the signed audit chain -->
![Revka dashboard Audit view displaying the signed audit chain](../assets/ops/operations-runbook-01-dashboard-audit.png)

<!-- TODO screenshot: dashboard showing Revka health status indicators for runtime subsystems -->
![Dashboard showing Revka health status indicators for runtime subsystems](../assets/ops/operations-runbook-03-dashboard-health.png)

## Health and State Signals

| Signal | Command / File | Expected |
|---|---|---|
| Config validity | `revka doctor` | no critical errors |
| Channel connectivity | `revka channel doctor` | configured channels healthy |
| Runtime summary | `revka status` | expected provider/model/channels |
| Daemon heartbeat/state | `~/.revka/daemon_state.json` | file updates periodically |
| Gateway/dashboard | `GET http://127.0.0.1:42617/health` | `200 OK` |
| Audit chain | `GET /api/audit/verify` (or `Audit` view on dashboard) | chain verifies clean |
| Kumiho proxy | `GET /api/kumiho/health` (via gateway) | upstream Kumiho reachable |
| Operator checkpoints | `~/.revka/workflow_checkpoints/` | recent workflow runs present |
| Operator RunLogs | `~/.revka/operator_mcp/runlogs/` | per-agent JSONL trails present |

<!-- TODO screenshot: terminal showing the tail of ~/.revka/logs/daemon.log -->
![Terminal showing the tail of ~/.revka/logs/daemon.log](../assets/ops/operations-runbook-02-daemon-logs.png)

## Logs and Diagnostics

### macOS / Windows (service wrapper logs)

- `~/.revka/logs/daemon.stdout.log`
- `~/.revka/logs/daemon.stderr.log`

### Linux (systemd user service)

```bash
journalctl --user -u revka.service -f
```

## Incident Triage Flow (Fast Path)

1. Snapshot system state:

```bash
revka status
revka doctor
revka channel doctor
```

2. Check service state:

```bash
revka service status
```

3. If service is unhealthy, restart cleanly:

```bash
revka service stop
revka service start
```

4. If channels still fail, verify allowlists and credentials in `~/.revka/config.toml`.

5. If gateway is involved, verify bind/auth settings (`[gateway]`) and local reachability.

## Safe Change Procedure

Before applying config changes:

1. backup `~/.revka/config.toml`
2. apply one logical change at a time
3. run `revka doctor`
4. restart daemon/service
5. verify with `status` + `channel doctor`

## Rollback Procedure

If a rollout regresses behavior:

1. restore previous `config.toml`
2. restart runtime (`daemon` or `service`)
3. confirm recovery via `doctor` and channel health checks
4. document incident root cause and mitigation

## Related Docs

- [one-click-bootstrap.md](../setup-guides/one-click-bootstrap.md)
- [troubleshooting.md](./troubleshooting.md)
- [config-reference.md](../reference/api/config-reference.md)
- [commands-reference.md](../reference/cli/commands-reference.md)
