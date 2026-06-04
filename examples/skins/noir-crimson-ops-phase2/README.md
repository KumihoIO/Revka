# Noir Crimson Ops Phase 2 Skin

This is a complete Phase 2 UI skin package template for Revka. It exercises
every supported skin asset slot and keeps agent/team profile images out of the
skin ZIP, because those are uploaded separately as Kumiho `profile-avatar`
artifacts.

Import `../noir-crimson-ops-phase2.zip` from the Revka Skins page.

The source directory mirrors the ZIP root:

```text
revka-skin.json
assets/
  brand-logo.png
  operator-avatar.png
  dashboard-hero.png
  ...
```

To rebuild the ZIP from this directory:

```powershell
Set-Location examples/skins/noir-crimson-ops-phase2
Compress-Archive -Path revka-skin.json, assets -DestinationPath ../noir-crimson-ops-phase2.zip -Force
```
