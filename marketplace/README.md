# Marketplace Templates for Revka

This directory contains draft templates and CI/CD workflows for listing Revka
on self-hosted PaaS platforms.

## Platforms

### Coolify (coollabsio/coolify)
- Template: `coolify/revka.yaml` -> goes to `templates/compose/revka.yaml` in their repo
- Logo: needs `revka.svg` in their `svgs/` directory
- PR target branch: `next` (CRITICAL — they close PRs to other branches)

### Dokploy (Dokploy/templates)
- Blueprint: `dokploy/blueprints/revka/` -> goes to `blueprints/revka/` in their repo
- Meta entry: `dokploy/meta-entry.json` -> merge into root `meta.json`
- Logo: needs `revka.svg` in the blueprint folder
- PR target branch: `main`
- IMPORTANT: Dokploy requires pinned image versions (no `latest` tag)

### EasyPanel (easypanel-io/templates)
- Template: `easypanel/` -> goes to `templates/revka/` in their repo
- Files: `meta.yaml` (metadata + schema), `index.ts` (generator logic), `assets/logo.svg`
- PR target branch: `main`
- IMPORTANT: EasyPanel requires pinned versions (no `latest`) and TypeScript generator
- Must run `npm run build` and `npm run prettier` before submitting

## Setup Checklist

### 1. Prerequisites

- [ ] **Copy the SVG logo** from `apps/tauri/icons/icon.svg` to `.github/assets/revka.svg`:
      ```bash
      cp apps/tauri/icons/icon.svg .github/assets/revka.svg
      git add .github/assets/revka.svg && git commit -m "chore: add SVG logo for marketplace templates"
      ```
- [ ] **Fork all three upstream repos** into the `KumihoIO` org:
      - Fork `coollabsio/coolify` -> `KumihoIO/coolify`
      - Fork `Dokploy/templates` -> `KumihoIO/templates`
      - Fork `easypanel-io/templates` -> `KumihoIO/easypanel-templates`
- [ ] **Create a GitHub PAT** (`MARKETPLACE_PAT`) with `repo` + `workflow` scopes
      that can push to the forks and create PRs on the upstream repos
- [ ] **Add the secret** `MARKETPLACE_PAT` to the `KumihoIO/Revka` repo secrets

### 2. Install the Workflow

Copy `sync-marketplace-templates.yml` to `.github/workflows/` in the revka repo.

### 3. Hook into Release Pipeline

Add this job to `release-stable-manual.yml` (after the `docker` job):

```yaml
  marketplace:
    name: Sync Marketplace Templates
    needs: [validate, docker]
    if: ${{ !cancelled() && needs.docker.result == 'success' }}
    uses: ./.github/workflows/sync-marketplace-templates.yml
    with:
      release_tag: ${{ needs.validate.outputs.tag }}
    secrets: inherit
```

And this to `release-beta-on-push.yml` (optional — only if you want beta syncs):

```yaml
  marketplace:
    name: Sync Marketplace Templates
    needs: [version, docker]
    if: ${{ !cancelled() && needs.docker.result == 'success' }}
    uses: ./.github/workflows/sync-marketplace-templates.yml
    with:
      release_tag: ${{ needs.version.outputs.tag }}
    secrets: inherit
```

### 4. Submit Initial PRs Manually

For the first listing, submit PRs manually:

**Coolify:**
1. Fork coollabsio/coolify (branch off `next`)
2. Add `templates/compose/revka.yaml` and `svgs/revka.svg`
3. Test using Docker Compose Empty deploy in your Coolify instance
4. Open PR to `coollabsio/coolify` targeting `next`

**Dokploy:**
1. Fork Dokploy/templates (branch off `main`)
2. Add `blueprints/revka/` with all 3 files
3. Add entry to root `meta.json`
4. Run `node dedupe-and-sort-meta.js`
5. Test via the PR preview URL (auto-generated)
6. Open PR to `Dokploy/templates` targeting `main`

**EasyPanel:**
1. Fork easypanel-io/templates (branch off `main`)
2. Add `templates/revka/` with `meta.yaml`, `index.ts`, and `assets/logo.svg`
3. Run `npm ci && npm run build && npm run prettier`
4. Test via `npm run dev` (opens a templates playground)
5. Open PR to `easypanel-io/templates` targeting `main`
6. Include a screenshot showing the deployed service with actual content

### 5. How Auto-Sync Works After Merge

Once the initial PRs are merged:

1. You cut a stable release (tag push or manual dispatch)
2. Docker images get built and pushed to GHCR
3. `sync-marketplace-templates.yml` fires
4. It auto-creates PRs to all three platform repos with the new version
5. Their maintainers review and merge (or you maintain the forks)

**Coolify** uses `:latest` tag so users get updates automatically on redeploy.
**Dokploy** requires pinned versions — workflow updates the image tag + meta.json each release.
**EasyPanel** requires pinned versions — workflow updates `meta.yaml` default image + changelog each release.

## File Structure

```
marketplace/
├── README.md                           # This file
├── sync-marketplace-templates.yml      # CI/CD workflow -> .github/workflows/
├── coolify/
│   └── revka.yaml                   # -> coollabsio/coolify templates/compose/
├── dokploy/
│   ├── meta-entry.json                 # -> merge into Dokploy/templates meta.json
│   └── blueprints/revka/
│       ├── docker-compose.yml          # -> Dokploy/templates blueprints/revka/
│       └── template.toml              # -> Dokploy/templates blueprints/revka/
└── easypanel/
    ├── meta.yaml                       # -> easypanel-io/templates templates/revka/
    ├── index.ts                        # -> easypanel-io/templates templates/revka/
    └── assets/                         # -> easypanel-io/templates templates/revka/assets/
        └── (logo.svg goes here)
```
