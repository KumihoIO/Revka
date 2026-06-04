# Revka Documentation

This page is the primary entry point for the Revka documentation system.

> Revka's core Rust runtime is a fork of [ZeroClaw](https://github.com/zeroclaw-labs/zeroclaw); see [`NOTICE`](../NOTICE) for full attribution and [`upstream/zeroclaw-attribution.md`](upstream/zeroclaw-attribution.md) for the relationship in plain language.

Last refreshed: **April 27, 2026**.

Localized hubs: [한국어](i18n/ko/README.md) · [Tiếng Việt](i18n/vi/README.md) · [简体中文](i18n/zh-CN/README.md). [What is "Revka"?](../README.md#what-is-revka) at the repository root.

---

## Start here by audience

- **New to Revka?** → [one-click-bootstrap.md](setup-guides/one-click-bootstrap.md), then [setup-guides/README.md](setup-guides/README.md)
- **Hardware / embedded target?** → [hardware/README.md](hardware/README.md)
- **Running Revka in production?** → [ops/README.md](ops/README.md)
- **Integrating via API / MCP?** → [reference/README.md](reference/README.md) and [contributing/README.md](contributing/README.md)
- **Reviewing or shipping code?** → [contributing/pr-workflow.md](contributing/pr-workflow.md) + [contributing/reviewer-playbook.md](contributing/reviewer-playbook.md)
- **Looking for something specific?** → [SUMMARY.md](SUMMARY.md) (the unified TOC)

## Quick task lookup

| I want to… | Read this |
|---|---|
| Install and run Revka quickly | [README.md (Quick Start)](../README.md#install) |
| Bootstrap in one command | [one-click-bootstrap.md](setup-guides/one-click-bootstrap.md) |
| Set up the Kumiho memory sidecar | [kumiho-operator-setup.md](setup-guides/kumiho-operator-setup.md) |
| Update or uninstall on macOS | [macos-update-uninstall.md](setup-guides/macos-update-uninstall.md) |
| Set up Revka on Windows | [windows-setup.md](setup-guides/windows-setup.md) |
| Find commands by task | [commands-reference.md](reference/cli/commands-reference.md) |
| Check config defaults and keys | [config-reference.md](reference/api/config-reference.md) |
| Configure custom providers / endpoints | [custom-providers.md](contributing/custom-providers.md) |
| Configure Z.AI / GLM provider | [zai-glm-setup.md](setup-guides/zai-glm-setup.md) |
| Wire Kumiho graph-native memory into an agent | [kumiho-memory-integration.md](contributing/kumiho-memory-integration.md) |
| Run the embedded React dashboard locally | [dashboard-dev.md](setup-guides/dashboard-dev.md) |
| Operate runtime (day-2 runbook) | [operations-runbook.md](ops/operations-runbook.md) |
| Troubleshoot install/runtime/channel issues | [troubleshooting.md](ops/troubleshooting.md) |
| Validate Google Agents CLI demo outcomes | [google-agents-cli-demo-readiness.md](ops/google-agents-cli-demo-readiness.md) |
| Package the Google Track 3 Cloud Run A2A demo | [google-agents-track3-enterprise-readiness.md](ops/google-agents-track3-enterprise-readiness.md) |
| Run Matrix encrypted-room setup and diagnostics | [matrix-e2ee-guide.md](security/matrix-e2ee-guide.md) |

## What makes Revka, Revka

- A **memory-native Rust agent runtime** — every session, plan, skill, and trust score lives in Kumiho's graph; nothing is forgotten.
- **Single Rust binary** — the gateway, daemon, embedded React dashboard, and CLI compile to one executable (statically linked on musl Linux); the Operator MCP (Python) installs as a managed sidecar alongside it, while the Session Manager (Node.js) remains opt-in via `revka install --sidecars-only --with-session-manager`.
- **Declarative orchestration** — Operator drives multi-agent workflows defined in YAML.
- **First-class hardware** — STM32, Arduino, ESP32, Pico, and Aardvark I²C/SPI peripherals all surface as agent tools.
- **18-route Web UI** — `http://127.0.0.1:42617` covers Orchestration, Operations, and Inspection without leaving the browser; 17 routes live in the main sidebar, with `Workflow Runs` reached contextually.
- **Trust scoring + ClawHub marketplace** — agents earn trust over runs and share skills via a content-addressed registry.

---

## Setup & onboarding

- [setup-guides/README.md](setup-guides/README.md) — setup index
- [one-click-bootstrap.md](setup-guides/one-click-bootstrap.md) — single-command install
- [kumiho-operator-setup.md](setup-guides/kumiho-operator-setup.md) — Kumiho memory sidecar install
- [macos-update-uninstall.md](setup-guides/macos-update-uninstall.md) — macOS lifecycle operations
- [windows-setup.md](setup-guides/windows-setup.md) — Windows setup notes
- [dashboard-dev.md](setup-guides/dashboard-dev.md) — run the `web/` dashboard locally
- [nextcloud-talk-setup.md](setup-guides/nextcloud-talk-setup.md) — Nextcloud Talk channel
- [mattermost-setup.md](setup-guides/mattermost-setup.md) — Mattermost channel
- [zai-glm-setup.md](setup-guides/zai-glm-setup.md) — Z.AI / GLM provider setup
- [browser-setup.md](browser-setup.md) — browser channel and headless / VNC modes
- [aardvark-integration.md](aardvark-integration.md) — Aardvark I²C/SPI host adapter

## Using Revka (day-to-day)

- [commands-reference.md](reference/cli/commands-reference.md) — CLI command lookup by workflow
- [config-reference.md](reference/api/config-reference.md) — config keys, defaults, and secure defaults
- [providers-reference.md](reference/api/providers-reference.md) — provider IDs, aliases, and credential env vars
- [channels-reference.md](reference/api/channels-reference.md) — channel capabilities and setup paths
- [reference/sop/observability.md](reference/sop/observability.md) — SOP run-state inspection and metrics
- [openai-temperature-compatibility.md](openai-temperature-compatibility.md) — temperature handling across providers

## Integrations

- [contributing/kumiho-memory-integration.md](contributing/kumiho-memory-integration.md) — the two-reflex pattern, capture types, provenance edges
- [contributing/custom-providers.md](contributing/custom-providers.md) — custom provider / base URL templates
- [contributing/extension-examples.md](contributing/extension-examples.md) — worked examples of provider/channel/tool extensions
- [contributing/adding-boards-and-tools.md](contributing/adding-boards-and-tools.md) — adding hardware boards and host-side tools
- [setup-guides/zai-glm-setup.md](setup-guides/zai-glm-setup.md) — Z.AI / GLM
- [setup-guides/nextcloud-talk-setup.md](setup-guides/nextcloud-talk-setup.md) — Nextcloud Talk
- [setup-guides/mattermost-setup.md](setup-guides/mattermost-setup.md) — Mattermost

## Operations

- [ops/README.md](ops/README.md) — operations index
- [ops/operations-runbook.md](ops/operations-runbook.md) — day-2 runtime operations and rollback
- [ops/troubleshooting.md](ops/troubleshooting.md) — failure signatures and recovery
- [ops/network-deployment.md](ops/network-deployment.md) — Raspberry Pi / LAN deployment
- [ops/proxy-agent-playbook.md](ops/proxy-agent-playbook.md) — proxy modes and tool-call routing
- [ops/resource-limits.md](ops/resource-limits.md) — runtime resource controls
- [ops/google-agents-cli-demo-readiness.md](ops/google-agents-cli-demo-readiness.md) — Google Agents CLI demo outcome checklist
- [ops/google-agents-track3-enterprise-readiness.md](ops/google-agents-track3-enterprise-readiness.md) — Track 3 Cloud Run A2A enterprise packaging checklist

## Security

> Some pages in this section are roadmap or proposal docs. For current behaviour, anchor on [config-reference.md](reference/api/config-reference.md), [operations-runbook.md](ops/operations-runbook.md), and [troubleshooting.md](ops/troubleshooting.md).

- [security/README.md](security/README.md) — security index
- [security/agnostic-security.md](security/agnostic-security.md) — provider-agnostic security model
- [security/frictionless-security.md](security/frictionless-security.md) — defaults that don't slow operators down
- [security/sandboxing.md](security/sandboxing.md) — Seatbelt / Landlock / Firejail / Bubblewrap layers
- [security/audit-logging.md](security/audit-logging.md) — Merkle-chained audit log
- [security/matrix-e2ee-guide.md](security/matrix-e2ee-guide.md) — Matrix E2EE channel setup
- [security/security-roadmap.md](security/security-roadmap.md) — proposed security work

## Hardware & peripherals

- [hardware/README.md](hardware/README.md) — hardware index
- [hardware/hardware-peripherals-design.md](hardware/hardware-peripherals-design.md) — peripheral architecture
- [hardware/nucleo-setup.md](hardware/nucleo-setup.md) — STM32 Nucleo-F401RE
- [hardware/arduino-uno-q-setup.md](hardware/arduino-uno-q-setup.md) — Arduino Uno Q
- [hardware/android-setup.md](hardware/android-setup.md) — Android / Termux target
- [hardware/datasheets/nucleo-f401re.md](hardware/datasheets/nucleo-f401re.md), [arduino-uno.md](hardware/datasheets/arduino-uno.md), [esp32.md](hardware/datasheets/esp32.md)

## Contributing

- [../CONTRIBUTING.md](../CONTRIBUTING.md) — top-level contributor entry
- [contributing/README.md](contributing/README.md) — contributor index
- [contributing/pr-workflow.md](contributing/pr-workflow.md) — PR governance and review lanes
- [contributing/reviewer-playbook.md](contributing/reviewer-playbook.md) — reviewer guide
- [contributing/pr-discipline.md](contributing/pr-discipline.md) — discipline norms
- [contributing/ci-map.md](contributing/ci-map.md) — CI workflow map
- [contributing/actions-source-policy.md](contributing/actions-source-policy.md) — GitHub Actions source policy
- [contributing/release-process.md](contributing/release-process.md) — release flow
- [contributing/testing.md](contributing/testing.md), [testing-telegram.md](contributing/testing-telegram.md), [cargo-slicer-speedup.md](contributing/cargo-slicer-speedup.md)
- [contributing/docs-contract.md](contributing/docs-contract.md) — docs-pairing contract
- [contributing/doc-template.md](contributing/doc-template.md) — doc starter template
- [contributing/label-registry.md](contributing/label-registry.md), [change-playbooks.md](contributing/change-playbooks.md)
- [contributing/cla.md](contributing/cla.md) — Contributor License Agreement

## Architecture & reference

- [architecture/adr-004-tool-shared-state-ownership.md](architecture/adr-004-tool-shared-state-ownership.md) — shared-state ownership for tools
- [architecture/adr-005-operator-liveness-and-rust-migration.md](architecture/adr-005-operator-liveness-and-rust-migration.md) — Operator → Rust migration (proposed)
- [reference/README.md](reference/README.md) — reference index
- [reference/sop/connectivity.md](reference/sop/connectivity.md) — connectivity SOPs
- [reference/sop/observability.md](reference/sop/observability.md) — observability SOPs

## Maintainer notes

- [maintainers/README.md](maintainers/README.md) — maintainer index
- [maintainers/repo-map.md](maintainers/repo-map.md) — repo top-level map
- [maintainers/structure-README.md](maintainers/structure-README.md) — docs structure map (language / part / function)
- [maintainers/docs-inventory.md](maintainers/docs-inventory.md) — docs inventory and classification
- [maintainers/i18n-coverage.md](maintainers/i18n-coverage.md) — translation coverage matrix
- [maintainers/refactor-candidates.md](maintainers/refactor-candidates.md) — files ripe for refactor
- [maintainers/trademark.md](maintainers/trademark.md) — naming and attribution norms
- [maintainers/project-triage-snapshot-2026-02-18.md](maintainers/project-triage-snapshot-2026-02-18.md) — archived triage snapshot

## License & upstream attribution

- [`../NOTICE`](../NOTICE) — root NOTICE with upstream ZeroClaw attribution preserved per Apache 2.0 §4(c)
- [`../LICENSE-MIT`](../LICENSE-MIT), [`../LICENSE-APACHE`](../LICENSE-APACHE) — dual-license texts
- [upstream/zeroclaw-attribution.md](upstream/zeroclaw-attribution.md) — what we inherited from ZeroClaw and the fork-compliance checklist
- [maintainers/trademark.md](maintainers/trademark.md) — Revka naming norms and the ZeroClaw trademark acknowledgement

## Other languages

- [한국어 (Korean)](i18n/ko/README.md)
- [Tiếng Việt (Vietnamese)](i18n/vi/README.md)
- [简体中文 (Simplified Chinese)](i18n/zh-CN/README.md)

Translations are best-effort. Pages without a translation fall back to the English source.
