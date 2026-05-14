# Security patches

This directory contains narrow local patches for transitive crates where the
upstream dependency chain has not yet published a compatible patched release.

- `glib-0.18.5`: backports the `VariantStrIter` fix for
  `GHSA-wrw7-89jp-8q8g` by passing a mutable child pointer to
  `g_variant_get_child`.
- `phf_generator-0.8.0`: keeps the `0.8` API required by older
  `phf_codegen` users while moving `rand` to `0.8.6` for
  `GHSA-cq8v-f236-94qc`.

Remove these patches once upstream Tauri and its transitive dependencies publish
compatible fixed versions.
