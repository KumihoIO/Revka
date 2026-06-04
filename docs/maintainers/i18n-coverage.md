# Revka i18n Coverage and Structure

This document defines the localization structure for Revka docs and tracks current coverage.

Last refreshed: **April 27, 2026**.

## Canonical Layout

Use these i18n paths:

- Root language landing: `README.<locale>.md` when that locale has a root README
- Full localized docs tree: `docs/i18n/<locale>/...`
- Canonical localized collection indexes inside `docs/i18n/<locale>/...` rather than docs-root shim files

## Locale Coverage Matrix

| Locale | Root README | Canonical Docs Hub | Commands Ref | Config Ref | Troubleshooting | Status |
|---|---|---|---|---|---|---|
| `en` | `README.md` | `docs/README.md` | `docs/reference/cli/commands-reference.md` | `docs/reference/api/config-reference.md` | `docs/ops/troubleshooting.md` | Source of truth |
| `ko` | `README.ko.md` | `docs/i18n/ko/README.md` | `docs/i18n/ko/reference/cli/commands-reference.md` | `docs/i18n/ko/reference/api/config-reference.md` | `docs/i18n/ko/ops/troubleshooting.md` | Partial tree localized |
| `vi` | — | `docs/i18n/vi/README.md` | `docs/i18n/vi/commands-reference.md` | `docs/i18n/vi/config-reference.md` | `docs/i18n/vi/ops/troubleshooting.md` | Full tree localized |
| `zh-CN` | — | `docs/i18n/zh-CN/README.md` | `docs/i18n/zh-CN/reference/cli/commands-reference.zh-CN.md` | `docs/i18n/zh-CN/reference/api/config-reference.zh-CN.md` | `docs/i18n/zh-CN/ops/troubleshooting.zh-CN.md` | Partial tree localized (nested mirror) |

## Root README Completeness

Not all root READMEs are full translations of `README.md`:

| Locale | Style | Approximate Coverage |
|---|---|---|
| `en` | Full source | 100% |
| `ko` | Hub-style entry point plus localized core references | ~45% |
| `vi` | Near-complete translation (flat tree) | ~85% |
| `zh-CN` | Hub-style entry point with nested mirror | ~55% |

Hub-style entry points provide quick-start orientation and language navigation but do not replicate the full English README content. This is an accurate status record, not a gap to be immediately resolved.

## Collection Index i18n

Localized collection indexes currently exist for:

- English across the main `docs/` collections
- Korean under `docs/i18n/ko/{reference,ops,security,hardware,contributing,setup-guides}/README.md`
- Vietnamese across a broad mixed tree, including both nested collection indexes and several flat compatibility pages
- Simplified Chinese under `docs/i18n/zh-CN/{reference,ops,security,hardware,contributing,setup-guides,maintainers}/README*.md`

## Localization Rules

- Keep technical identifiers in English:
  - CLI command names
  - config keys
  - API paths
  - trait/type identifiers
- Prefer concise, operator-oriented localization over literal translation.
- Update "Last refreshed" / "Last synchronized" dates when localized pages change.
- Ensure every localized hub has an "Other languages" section.

## Adding a New Locale

1. Create `README.<locale>.md`.
2. Create canonical docs tree under `docs/i18n/<locale>/` with at least:
   - `README.md`
   - `reference/cli/commands-reference*.md`
   - `reference/api/config-reference*.md`
   - `ops/troubleshooting*.md`
3. Add locale links to:
   - root language nav in every `README*.md`
   - localized hubs line in `docs/README.md`
   - "Other languages" section in every `docs/README*.md`
   - language entry section in `docs/SUMMARY.md`
4. Add docs-root shim files only if there is a concrete backward-compatibility need.
5. Update this file (`docs/i18n-coverage.md`) and run link validation.

## Review Checklist

- Links resolve for all localized entry files.
- No locale references stale filenames or nonexistent docs-root shims.
- TOC (`docs/SUMMARY.md`) and docs hub (`docs/README.md`) include the locale.
