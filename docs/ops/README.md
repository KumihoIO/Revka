# Operations & Deployment Docs

For operators running Construct in persistent or production-like environments.

## Core Operations

- Day-2 runbook: [./operations-runbook.md](./operations-runbook.md)
- Release runbook: [../contributing/release-process.md](../contributing/release-process.md)
- Troubleshooting matrix: [./troubleshooting.md](./troubleshooting.md)
- Safe network/gateway deployment: [./network-deployment.md](./network-deployment.md)
- Google Agents CLI demo readiness: [./google-agents-cli-demo-readiness.md](./google-agents-cli-demo-readiness.md)
- Google Track 3 enterprise readiness: [./google-agents-track3-enterprise-readiness.md](./google-agents-track3-enterprise-readiness.md)
- Mattermost setup (channel-specific): [../setup-guides/mattermost-setup.md](../setup-guides/mattermost-setup.md)

## Common Flow

1. Validate runtime (`status`, `doctor`, `channel doctor`)
2. Apply one config change at a time
3. Restart service/daemon
4. Verify channel and gateway health
5. Roll back quickly if behavior regresses

## Related

- Config reference: [../reference/api/config-reference.md](../reference/api/config-reference.md)
- Security collection: [../security/README.md](../security/README.md)
