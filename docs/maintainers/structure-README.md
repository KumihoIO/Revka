# Construct Docs Structure Map

This page describes the current documentation structure across three axes:

1. Language
2. Collection
3. Function

Last refreshed: **May 8, 2026**.

## 1) By Language

| Language | Entry point | Canonical tree | Notes |
|---|---|---|---|
| English | `docs/README.md` | `docs/` | Source-of-truth runtime behavior docs are authored in English first. |
| Korean (`ko`) | `docs/i18n/ko/README.md` | `docs/i18n/ko/` | Partial localized tree aligned to the English hub and major guides. |
| Vietnamese (`vi`) | `docs/i18n/vi/README.md` | `docs/i18n/vi/` | Broadest localized tree; many pages are direct localized copies of earlier English docs. |
| Simplified Chinese (`zh-CN`) | `docs/i18n/zh-CN/README.md` | `docs/i18n/zh-CN/` | Localized hub plus selected translated collections. |

## 2) By Collection

These directories are the primary navigation modules by product area.

- `docs/setup-guides/` for install, onboarding, and local setup flows
- `docs/reference/` for CLI, API, config, and SOP reference material
- `docs/ops/` for day-2 operations, deployment, and troubleshooting
- `docs/security/` for security guidance and security-oriented navigation
- `docs/hardware/` for board/peripheral implementation and hardware workflows
- `docs/contributing/` for contribution and CI/review processes
- `docs/maintainers/` for repo maps, inventory, and maintenance-facing docs

## 3) By Function

Use this grouping to decide where new docs belong.

### Runtime Contract

- `docs/reference/cli/commands-reference.md`
- `docs/reference/api/providers-reference.md`
- `docs/reference/api/channels-reference.md`
- `docs/reference/api/config-reference.md`
- `docs/ops/operations-runbook.md`
- `docs/ops/troubleshooting.md`
- `docs/setup-guides/one-click-bootstrap.md`
- `README.md`
- `docs/README.md`

### Setup / Integration Guides

- `docs/setup-guides/*.md`
- `docs/browser-setup.md`
- `docs/aardvark-integration.md`
- `docs/contributing/kumiho-memory-integration.md`
- `docs/contributing/custom-providers.md`
- `docs/contributing/extension-examples.md`
- `docs/contributing/adding-boards-and-tools.md`

### Policy / Process

- `CONTRIBUTING.md`
- `docs/contributing/pr-workflow.md`
- `docs/contributing/reviewer-playbook.md`
- `docs/contributing/pr-discipline.md`
- `docs/contributing/ci-map.md`
- `docs/contributing/actions-source-policy.md`
- `docs/contributing/change-playbooks.md`
- `docs/contributing/docs-contract.md`

### Proposal / Roadmap

- `docs/security/sandboxing.md`
- `docs/security/audit-logging.md`
- `docs/security/agnostic-security.md`
- `docs/security/frictionless-security.md`
- `docs/security/security-roadmap.md`
- `docs/ops/resource-limits.md`
- `docs/architecture/adr-005-operator-liveness-and-rust-migration.md`
- `docs/superpowers/specs/*.md`

### Snapshot / Audit

- `docs/coherence-audit-2026-05.md`
- `docs/p0-2-row1-13-review.md`
- `docs/p0-2-row6-review.md`
- `docs/audit-row-*.md`
- `docs/maintainers/project-triage-snapshot-2026-02-18.md`

### Assets / Templates

- `docs/assets/`
- `docs/contributing/doc-template.md`
- `docs/hardware/datasheets/`

## Placement Rules

- New runtime-behavior docs must be linked from the relevant collection index and `docs/SUMMARY.md`.
- When docs move between collections, update `docs/README.md`, `docs/SUMMARY.md`, and `docs/maintainers/docs-inventory.md` in the same change.
- Localized hubs should keep their entry-point links aligned with English, but localized deep-page coverage can lag and should be treated as best-effort unless explicitly marked current.
