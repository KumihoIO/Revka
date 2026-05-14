# Construct Documentation Inventory

This inventory classifies docs by intent so readers can distinguish runtime-contract docs from proposals, process docs, and time-bound reports.

Last reviewed: **May 8, 2026**.

## Classification Legend

- **Current Guide/Reference**: intended to describe current runtime behavior or the active docs structure
- **Policy/Process**: collaboration, governance, or review rules
- **Proposal/Roadmap**: design exploration; may describe planned or hypothetical behavior
- **Snapshot/Audit**: time-bound report, audit, or review artifact

## Documentation Entry Points

| Doc | Type | Audience |
|---|---|---|
| `README.md` | Current Guide | all readers |
| `README.ko.md` | Current Guide (localized) | Korean readers |
| `docs/README.md` | Current Guide (hub) | all readers |
| `docs/SUMMARY.md` | Current Guide (unified TOC) | all readers |
| `docs/i18n/README.md` | Current Guide (i18n landing) | all readers |
| `docs/i18n/ko/README.md` | Current Guide (localized hub) | Korean readers |
| `docs/i18n/vi/README.md` | Current Guide (localized hub) | Vietnamese readers |
| `docs/i18n/zh-CN/README.md` | Current Guide (localized hub) | Simplified Chinese readers |
| `docs/maintainers/structure-README.md` | Current Guide (structure map) | maintainers |

## Collection Index Docs

| Doc | Type | Audience |
|---|---|---|
| `docs/setup-guides/README.md` | Current Guide | new users |
| `docs/reference/README.md` | Current Guide | users/operators |
| `docs/reference/sop/README.md` | Current Guide | operators |
| `docs/ops/README.md` | Current Guide | operators |
| `docs/security/README.md` | Current Guide | operators/contributors |
| `docs/hardware/README.md` | Current Guide | hardware builders |
| `docs/contributing/README.md` | Current Guide | contributors/reviewers |
| `docs/maintainers/README.md` | Current Guide | maintainers |

## Current Guides & References

| Doc | Type | Audience |
|---|---|---|
| `docs/setup-guides/one-click-bootstrap.md` | Current Guide | users/operators |
| `docs/setup-guides/kumiho-operator-setup.md` | Current Guide | users/operators |
| `docs/setup-guides/macos-update-uninstall.md` | Current Guide | users/operators |
| `docs/setup-guides/windows-setup.md` | Current Guide | users/operators |
| `docs/setup-guides/dashboard-dev.md` | Current Guide | contributors/operators |
| `docs/setup-guides/nextcloud-talk-setup.md` | Current Guide | operators |
| `docs/setup-guides/mattermost-setup.md` | Current Guide | operators |
| `docs/setup-guides/zai-glm-setup.md` | Current Guide | users/operators |
| `docs/browser-setup.md` | Current Guide | operators |
| `docs/aardvark-integration.md` | Current Guide | hardware builders |
| `docs/reference/cli/commands-reference.md` | Current Reference | users/operators |
| `docs/reference/api/providers-reference.md` | Current Reference | users/operators |
| `docs/reference/api/channels-reference.md` | Current Reference | users/operators |
| `docs/reference/api/config-reference.md` | Current Reference | operators |
| `docs/reference/ui-skins.md` | Current Reference | contributors/operators |
| `docs/reference/sop/connectivity.md` | Current SOP | operators |
| `docs/reference/sop/observability.md` | Current SOP | operators |
| `docs/reference/sop/cookbook.md` | Current SOP | operators |
| `docs/openai-temperature-compatibility.md` | Current Reference | users/operators |
| `docs/contributing/kumiho-memory-integration.md` | Current Integration Guide | integration developers |
| `docs/contributing/custom-providers.md` | Current Integration Guide | integration developers |
| `docs/contributing/extension-examples.md` | Current Integration Guide | contributors |
| `docs/contributing/adding-boards-and-tools.md` | Current Guide | hardware contributors |
| `docs/ops/operations-runbook.md` | Current Guide | operators |
| `docs/ops/troubleshooting.md` | Current Guide | users/operators |
| `docs/ops/network-deployment.md` | Current Guide | operators |
| `docs/ops/proxy-agent-playbook.md` | Current Guide | operators |
| `docs/hardware/hardware-peripherals-design.md` | Current Design Spec | hardware contributors |
| `docs/hardware/nucleo-setup.md` | Current Guide | hardware builders |
| `docs/hardware/arduino-uno-q-setup.md` | Current Guide | hardware builders |
| `docs/hardware/android-setup.md` | Current Guide | hardware builders |
| `docs/hardware/datasheets/nucleo-f401re.md` | Current Hardware Reference | hardware builders |
| `docs/hardware/datasheets/arduino-uno.md` | Current Hardware Reference | hardware builders |
| `docs/hardware/datasheets/esp32.md` | Current Hardware Reference | hardware builders |

## Policy / Process Docs

| Doc | Type |
|---|---|
| `CONTRIBUTING.md` | Policy |
| `docs/contributing/pr-workflow.md` | Policy |
| `docs/contributing/reviewer-playbook.md` | Process |
| `docs/contributing/pr-discipline.md` | Process |
| `docs/contributing/ci-map.md` | Process |
| `docs/contributing/actions-source-policy.md` | Policy |
| `docs/contributing/change-playbooks.md` | Process |
| `docs/contributing/label-registry.md` | Process |
| `docs/contributing/testing.md` | Process |
| `docs/contributing/testing-telegram.md` | Process |
| `docs/contributing/release-process.md` | Process |
| `docs/contributing/docs-contract.md` | Policy |
| `docs/contributing/doc-template.md` | Process |
| `docs/contributing/cla.md` | Policy |
| `docs/maintainers/trademark.md` | Policy |

## Proposal / Roadmap Docs

These are useful context, but should not be treated as strict runtime contracts.

| Doc | Type |
|---|---|
| `docs/security/sandboxing.md` | Proposal |
| `docs/security/audit-logging.md` | Proposal |
| `docs/security/agnostic-security.md` | Proposal |
| `docs/security/frictionless-security.md` | Proposal |
| `docs/security/security-roadmap.md` | Roadmap |
| `docs/ops/resource-limits.md` | Proposal |
| `docs/architecture/adr-005-operator-liveness-and-rust-migration.md` | Proposal |
| `docs/superpowers/specs/2026-03-13-linkedin-tool-design.md` | Proposal |
| `docs/superpowers/specs/2026-03-19-google-workspace-operation-allowlist.md` | Proposal |

## Snapshot / Audit Docs

| Doc | Type |
|---|---|
| `docs/coherence-audit-2026-05.md` | Audit |
| `docs/p0-2-row1-13-review.md` | Audit |
| `docs/p0-2-row6-review.md` | Audit |
| `docs/audit-row-8-review.md` | Audit |
| `docs/audit-row-5-10-11-12-scrub-inventory.md` | Audit |
| `docs/audit-row-5-10-11-12-scrub-review.md` | Audit |
| `docs/maintainers/project-triage-snapshot-2026-02-18.md` | Snapshot |

## Maintenance Recommendations

1. Update `docs/reference/cli/commands-reference.md` whenever the CLI surface changes.
2. Update `docs/reference/api/providers-reference.md` when provider IDs, aliases, auth env vars, or onboarding notes change.
3. Update `docs/reference/api/channels-reference.md` when channel support or setup semantics change.
4. Keep `README.md`, `docs/README.md`, and `docs/SUMMARY.md` aligned on the top-level product surface.
5. Keep proposal docs explicitly labeled so they are not mistaken for runtime contracts.
6. When moving docs between collections, update this inventory in the same change.
7. Keep localized hubs aligned with the English entry points, but treat localized deep pages as best-effort rather than contract-complete.
