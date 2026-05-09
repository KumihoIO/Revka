//! Register an installed skill with Kumiho.
//!
//! Step 6b of the self-improving skill plan.  This module exposes the
//! single canonical "this skill exists, give it a Kumiho identity"
//! operation that step 6c will route every install path through —
//! ClawHub, GitHub link, dashboard, `creator.rs`, daemon-startup scan,
//! the operator's `capture_skill` MCP tool.
//!
//! ## What `register_skill_with_kumiho` does
//!
//! Given a skill directory on disk (`~/.construct/workspace/skills/<slug>/`)
//! and a Kumiho client + memory_project name from config:
//!
//! 1. **No-op if already registered** — when the skill's `SKILL.toml`
//!    already has `[skill].kref` set, the existing kref is returned.
//!    This is what makes the function safe to call from every install
//!    path including a daemon-startup scan: re-registering a skill that
//!    was registered yesterday costs zero Kumiho calls.
//!
//! 2. **Auto-migrate legacy embedded prompts** to a content file.  The
//!    [`super::migrate_skill_toml_to_content_file`] helper added in step
//!    6a runs first when the manifest is still in legacy form, so the
//!    revision's artifact has a stable file path to point at.
//!
//! 3. **Resolve the absolute content file path** the artifact will
//!    reference.  Kumiho stores the kref → file location mapping; the
//!    file itself stays canonical on disk.
//!
//! 4. **Create item + revision + artifact + tag** in
//!    `<memory_project>/Skills/`:
//!      - `create_item(kind = SKILL_ITEM_KIND ("skill"), item_name = slug)`
//!      - `create_revision(item_kref, metadata)` with manifest fields
//!      - `create_artifact(revision_kref, name = "skill", location = "file://<abs>")`
//!      - `tag_revision(revision_kref, "published")`
//!
//!    Items created prior to the kind rename use `kind = "skilldef"` and
//!    remain readable via the lookup fallback (see
//!    [`LEGACY_SKILL_ITEM_KIND`] and the search helpers in
//!    `crate::gateway`).
//!
//! 5. **Rewrite `SKILL.toml`** with `[skill].kref =
//!    "kref://<memory_project>/Skills/<slug>.skill?t=published"`.
//!    The `?t=published` query is what makes the manifest write-once
//!    across revisions: subsequent improvements that retag `published`
//!    onto a new revision are picked up automatically without touching
//!    the manifest again.
//!
//! ## Error handling
//!
//! Any Kumiho-side failure leaves the file system in its starting state
//! — the manifest is only rewritten after every Kumiho call has
//! succeeded, so a partial registration cannot end up with a kref
//! pointing at a non-existent revision.

use anyhow::{Context, Result, anyhow};
use std::collections::HashMap;
use std::path::{Path, PathBuf};

use crate::gateway::kumiho_client::{KumihoClient, slugify};
use crate::skills::{SkillContentMigration, SkillManifest, migrate_skill_toml_to_content_file};

/// The kind used for skill items in Kumiho.  Matches the kref convention
/// `kref://<memory_project>/Skills/<slug>.skill`.
pub const SKILL_ITEM_KIND: &str = "skill";

/// Legacy kind name. New skills are written with [`SKILL_ITEM_KIND`]
/// (`"skill"`), but existing items may have been created under the prior
/// `"skilldef"` kind. Lookup paths try the new kind first and fall back
/// to this legacy value so older data stays reachable until an offline
/// migration script renames it.
pub const LEGACY_SKILL_ITEM_KIND: &str = "skilldef";

/// Default tag for the current revision of a skill.
pub const PUBLISHED_TAG: &str = "published";

/// Tag attached to the immediately-prior `published` revision before a
/// new one is promoted.  Used by [`rollback_skill_revision`] (step 6f)
/// as the canonical rollback target when an LLM-driven improvement
/// regresses against its predecessor.
///
/// Kumiho `tag_revision` is move-semantics, so re-applying this tag
/// each publish naturally moves it to the latest "outgoing" revision —
/// there's no manual cleanup required to keep it pointing at the
/// previous-but-one.
pub const PREVIOUS_PUBLISHED_TAG: &str = "previous_published";

/// Default artifact name for a skill revision.  Single artifact per
/// revision, named uniformly so consumers can locate it without a
/// listing call.
pub const SKILL_ARTIFACT_NAME: &str = "skill";

/// Build the published-kref pointer for a skill — the string written
/// into `SKILL.toml`'s `[skill].kref` field.  Always resolves to
/// whichever revision currently holds the `published` tag, so the
/// manifest never needs to be rewritten when a new revision lands.
pub fn build_published_kref(memory_project: &str, slug: &str) -> String {
    format!("kref://{memory_project}/Skills/{slug}.{SKILL_ITEM_KIND}?t={PUBLISHED_TAG}")
}

/// Outcome returned by [`register_skill_with_kumiho`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SkillRegistration {
    /// `SKILL.toml` already had `[skill].kref` set; no Kumiho calls
    /// were made.  The existing kref is returned so callers can use it
    /// without parsing the manifest themselves.
    AlreadyRegistered { kref: String },
    /// Created a fresh item + revision + artifact + tag and updated
    /// the manifest with the new kref.
    Registered {
        kref: String,
        item_kref: String,
        revision_kref: String,
    },
}

impl SkillRegistration {
    /// The published-kref pointer either way (existing or freshly
    /// minted).  Convenient for callers that don't need to distinguish.
    pub fn kref(&self) -> &str {
        match self {
            Self::AlreadyRegistered { kref } => kref,
            Self::Registered { kref, .. } => kref,
        }
    }
}

/// Register an installed skill with Kumiho — see module docs.
pub async fn register_skill_with_kumiho(
    skill_dir: &Path,
    client: &KumihoClient,
    memory_project: &str,
) -> Result<SkillRegistration> {
    let manifest_path = skill_dir.join("SKILL.toml");
    if !manifest_path.exists() {
        return Err(anyhow!("no SKILL.toml at {}", manifest_path.display()));
    }

    // Phase 1: read the manifest and short-circuit if already registered.
    let manifest = read_manifest(&manifest_path)?;
    if let Some(kref) = manifest.skill.kref.as_ref() {
        return Ok(SkillRegistration::AlreadyRegistered { kref: kref.clone() });
    }

    // Phase 2: ensure the skill has an external content file.  Legacy
    // skills with embedded prompts get migrated here so step 4's
    // artifact has a stable file path to point at.
    if manifest.skill.content_file.is_none() && !manifest.prompts.is_empty() {
        let result = migrate_skill_toml_to_content_file(&manifest_path)
            .context("auto-migrating legacy embedded prompts to content file")?;
        if !matches!(result, SkillContentMigration::Migrated { .. }) {
            // The earlier check guaranteed prompts were non-empty, so
            // anything other than Migrated here is unexpected.
            return Err(anyhow!(
                "unexpected migrate_skill_toml_to_content_file result: {result:?}"
            ));
        }
    }

    // Reload after potential migration — the manifest now has
    // content_file set if it didn't before.
    let manifest = read_manifest(&manifest_path)?;
    let slug = slugify(&manifest.skill.name);
    if slug.is_empty() {
        return Err(anyhow!(
            "skill name {:?} slugified to empty; cannot register",
            manifest.skill.name
        ));
    }

    // Phase 3: resolve the absolute content file path the artifact will
    // reference.  Kumiho stores the kref → file mapping; the file stays
    // canonical on disk.
    let content_path = resolve_content_file(skill_dir, &manifest)?;
    let content_uri = format_file_uri(&content_path);

    // Phase 4: create item + revision + artifact + tag.  Each step
    // mutates Kumiho but not the manifest, so the file system stays
    // consistent with the partial-registration invariant: SKILL.toml is
    // only rewritten after EVERY Kumiho call has succeeded.
    client
        .ensure_project(memory_project)
        .await
        .with_context(|| format!("ensure_project({memory_project})"))?;
    client
        .ensure_space(memory_project, "Skills")
        .await
        .with_context(|| format!("ensure_space({memory_project}/Skills)"))?;

    let space_path = format!("/{memory_project}/Skills");
    let item = client
        .create_item(&space_path, &slug, SKILL_ITEM_KIND, HashMap::new())
        .await
        .with_context(|| format!("create_item({space_path}/{slug}.{SKILL_ITEM_KIND})"))?;

    let revision_metadata = revision_metadata_from_manifest(&manifest);
    let revision = client
        .create_revision(&item.kref, revision_metadata)
        .await
        .with_context(|| format!("create_revision({})", item.kref))?;

    client
        .create_artifact(
            &revision.kref,
            SKILL_ARTIFACT_NAME,
            &content_uri,
            HashMap::new(),
        )
        .await
        .with_context(|| format!("create_artifact({} -> {content_uri})", revision.kref))?;

    client
        .tag_revision(&revision.kref, PUBLISHED_TAG)
        .await
        .with_context(|| format!("tag_revision({}, {PUBLISHED_TAG})", revision.kref))?;

    // Phase 5: rewrite the manifest with the published kref pointer.
    let kref = build_published_kref(memory_project, &slug);
    write_kref_to_manifest(&manifest_path, &kref)?;

    Ok(SkillRegistration::Registered {
        kref,
        item_kref: item.kref,
        revision_kref: revision.kref,
    })
}

/// Outcome returned by [`publish_skill_revision`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PublishedSkillRevision {
    /// kref of the freshly created revision.  Useful for callers that
    /// want to record the per-revision outcome in step 6f.
    pub revision_kref: String,
    /// Relative path (under the skill directory) of the new content
    /// file that the published kref now resolves to.  Returned from
    /// the embedded sync so callers don't need to re-read SKILL.toml.
    pub new_content_file: String,
}

/// Publish a freshly-improved content file as a new Kumiho revision and
/// retag `published` onto it.
///
/// Step 6e of the kumiho-versioned skill plan.  The improver wrote the
/// new markdown to disk; this function makes it the new authoritative
/// revision so the next agent that resolves
/// `kref://…?t=published` reads the improved body.
///
/// Steps:
///   1. Read SKILL.toml; bail if `[skill].kref` isn't set (the skill
///      hasn't been registered yet — caller must run
///      [`register_skill_with_kumiho`] first).
///   2. Create a new revision under the same item as the existing
///      published revision.  Carries forward the manifest's metadata
///      (name, description, version, …) plus an `improvement_reason`
///      and `improved_at` timestamp so the revision is queryable on
///      its own.
///   3. Create the canonical `skill` artifact pointing at the new
///      content file (`file://<absolute path>`).
///   4. Retag `published` onto the new revision.  Kumiho's
///      [`tag_revision`] is move-semantics for tags, so the previous
///      revision automatically loses the tag.
///   5. Run [`sync_published_content_path`] to update SKILL.toml's
///      `content_file` pointer to the new file.  This is what makes
///      the loader pick up the new body without restart.
///
/// Idempotent on the SKILL.toml side: the manifest's `content_file`
/// gets rewritten by step 5 only when it changed.  The Kumiho side
/// always creates a new revision; there is no "no-op" path because
/// step 6f will rely on every improvement having a distinct revision
/// kref it can record outcomes against.
pub async fn publish_skill_revision(
    skill_dir: &Path,
    new_content_file: &Path,
    improvement_reason: &str,
    client: &KumihoClient,
    memory_project: &str,
) -> Result<PublishedSkillRevision> {
    let manifest_path = skill_dir.join("SKILL.toml");
    if !manifest_path.exists() {
        return Err(anyhow!("no SKILL.toml at {}", manifest_path.display()));
    }
    let manifest = read_manifest(&manifest_path)?;
    let kref = manifest.skill.kref.as_deref().ok_or_else(|| {
        anyhow!("skill not registered yet (no [skill].kref); run register_skill_with_kumiho first")
    })?;

    // Sanity check: the configured memory_project should match the one
    // baked into the manifest's kref.  Catches the case where someone
    // edits config.kumiho.memory_project after a skill is registered
    // and we'd otherwise create a revision in the wrong project.
    let expected_prefix = format!("kref://{memory_project}/");
    if !kref.starts_with(&expected_prefix) {
        return Err(anyhow!(
            "skill kref {kref:?} does not match configured memory_project {memory_project:?}; \
             re-register the skill or update config.kumiho.memory_project"
        ));
    }

    // Strip the `?t=published` suffix to get the bare item kref.  The
    // new revision is created under the same item.
    let (item_kref, _) = parse_kref_tag(kref);
    let item_kref = item_kref.to_string();

    // Resolve the content file to an absolute, canonical path so the
    // artifact location is portable across daemon working directories.
    let abs = std::fs::canonicalize(new_content_file).with_context(|| {
        format!(
            "canonicalising new content file: {}",
            new_content_file.display()
        )
    })?;
    let content_uri = format_file_uri(&abs);

    let mut revision_metadata = revision_metadata_from_manifest(&manifest);
    revision_metadata.insert("improvement_reason".into(), improvement_reason.to_string());
    revision_metadata.insert("improved_at".into(), chrono::Utc::now().to_rfc3339());

    let revision = client
        .create_revision(&item_kref, revision_metadata)
        .await
        .with_context(|| format!("create_revision({item_kref})"))?;

    client
        .create_artifact(
            &revision.kref,
            SKILL_ARTIFACT_NAME,
            &content_uri,
            HashMap::new(),
        )
        .await
        .with_context(|| format!("create_artifact({} -> {content_uri})", revision.kref))?;

    // Step 6f-A: preserve the outgoing `published` revision as
    // `previous_published` so [`rollback_skill_revision`] has a target
    // when the new revision regresses.  Best-effort — failure here
    // does NOT block the publish (the rollback path simply has nothing
    // to roll back to until the next improvement re-arms it).
    match client.get_revision_by_tag(&item_kref, PUBLISHED_TAG).await {
        Ok(outgoing) if outgoing.kref != revision.kref => {
            if let Err(e) = client
                .tag_revision(&outgoing.kref, PREVIOUS_PUBLISHED_TAG)
                .await
            {
                tracing::warn!(
                    outgoing = %outgoing.kref,
                    new_revision = %revision.kref,
                    error = ?e,
                    "publish_skill_revision: failed to mark outgoing as previous_published; \
                     rollback target may be unavailable until next publish",
                );
            }
        }
        Ok(_) => {
            // The new revision is somehow already tagged published —
            // shouldn't happen because we created it moments ago.
            // Treat as a no-op; the next tag_revision call below will
            // be a no-op too.
        }
        Err(e) => {
            tracing::warn!(
                item_kref = %item_kref,
                error = ?e,
                "publish_skill_revision: could not fetch current published revision \
                 to mark as previous_published; rollback target may be unavailable",
            );
        }
    }

    client
        .tag_revision(&revision.kref, PUBLISHED_TAG)
        .await
        .with_context(|| format!("tag_revision({}, {PUBLISHED_TAG})", revision.kref))?;

    // Update SKILL.toml.content_file to point at the new file.  We use
    // the same load-time projection that runs at daemon startup, so
    // there's a single canonical path that knows how to translate a
    // published revision into a content_file pointer.
    let new_content_file_rel = match sync_published_content_path(skill_dir, client).await? {
        SkillContentSync::Updated {
            new_content_file, ..
        } => new_content_file,
        SkillContentSync::AlreadyCurrent => manifest
            .skill
            .content_file
            .clone()
            .unwrap_or_else(|| abs.to_string_lossy().into_owned()),
        SkillContentSync::NotRegistered => {
            // Shouldn't happen — we just confirmed [skill].kref is set
            // a few lines up.  Surface as an error so callers don't
            // silently end up with stale content_file pointers.
            return Err(anyhow!(
                "publish_skill_revision: SKILL.toml lost its kref between read and sync"
            ));
        }
    };

    Ok(PublishedSkillRevision {
        revision_kref: revision.kref,
        new_content_file: new_content_file_rel,
    })
}

/// Outcome returned by [`rollback_skill_revision`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SkillRollback {
    /// kref of the revision now tagged `published` (the rollback target).
    pub restored_revision_kref: String,
    /// kref of the revision that WAS published before the rollback —
    /// the one whose regression triggered this call.  Useful for
    /// audit logging.
    pub demoted_revision_kref: String,
    /// Relative `content_file` path SKILL.toml now points at.
    pub new_content_file: String,
}

/// Roll back to the immediately-prior published revision (step 6f-A).
///
/// Symmetric to [`publish_skill_revision`]: where publish moves
/// `published` forward and stamps the outgoing revision with
/// `previous_published`, this function moves `published` BACK onto
/// whatever currently holds `previous_published`, and re-syncs
/// `SKILL.toml.content_file` so the next agent that resolves
/// `?t=published` reads the restored body.
///
/// Only the tag movement and the manifest pointer are rewritten —
/// the demoted revision and its artifact stay intact in Kumiho so
/// step 6f-B can record per-revision regression history without
/// losing the file.
///
/// Errors when:
///   - SKILL.toml is missing or has no `[skill].kref`.
///   - The configured `memory_project` doesn't match the manifest's
///     kref project (same guard as `publish_skill_revision`).
///   - Kumiho has no `previous_published` revision (e.g. this is the
///     skill's first published version, or the prior publish failed
///     to stamp the outgoing tag).
///   - The `previous_published` revision IS the current `published`
///     revision (defensive — would be a no-op rollback).
pub async fn rollback_skill_revision(
    skill_dir: &Path,
    client: &KumihoClient,
    memory_project: &str,
) -> Result<SkillRollback> {
    let manifest_path = skill_dir.join("SKILL.toml");
    if !manifest_path.exists() {
        return Err(anyhow!("no SKILL.toml at {}", manifest_path.display()));
    }
    let manifest = read_manifest(&manifest_path)?;
    let kref = manifest.skill.kref.as_deref().ok_or_else(|| {
        anyhow!("skill not registered yet (no [skill].kref); nothing to roll back")
    })?;

    let expected_prefix = format!("kref://{memory_project}/");
    if !kref.starts_with(&expected_prefix) {
        return Err(anyhow!(
            "skill kref {kref:?} does not match configured memory_project {memory_project:?}; \
             re-register the skill or update config.kumiho.memory_project"
        ));
    }

    let (item_kref, _) = parse_kref_tag(kref);
    let item_kref = item_kref.to_string();

    // Locate both endpoints up front so we can fail before touching
    // the tag if either side is missing.
    let target = client
        .get_revision_by_tag(&item_kref, PREVIOUS_PUBLISHED_TAG)
        .await
        .with_context(|| format!("get_revision_by_tag({item_kref}, {PREVIOUS_PUBLISHED_TAG})"))?;
    let current = client
        .get_revision_by_tag(&item_kref, PUBLISHED_TAG)
        .await
        .with_context(|| format!("get_revision_by_tag({item_kref}, {PUBLISHED_TAG})"))?;

    if target.kref == current.kref {
        return Err(anyhow!(
            "rollback target {target} is already the current published revision; nothing to roll back",
            target = target.kref,
        ));
    }

    // Move the `published` tag back onto the rollback target.
    // tag_revision is move-semantics so this implicitly demotes the
    // current published revision.
    client
        .tag_revision(&target.kref, PUBLISHED_TAG)
        .await
        .with_context(|| format!("tag_revision({}, {PUBLISHED_TAG})", target.kref))?;

    // Re-sync SKILL.toml.content_file so the loader picks up the
    // restored body.
    let new_content_file_rel = match sync_published_content_path(skill_dir, client).await? {
        SkillContentSync::Updated {
            new_content_file, ..
        } => new_content_file,
        SkillContentSync::AlreadyCurrent => manifest.skill.content_file.clone().unwrap_or_default(),
        SkillContentSync::NotRegistered => {
            return Err(anyhow!(
                "rollback_skill_revision: SKILL.toml lost its kref between read and sync"
            ));
        }
    };

    Ok(SkillRollback {
        restored_revision_kref: target.kref,
        demoted_revision_kref: current.kref,
        new_content_file: new_content_file_rel,
    })
}

/// Outcome returned by [`sync_published_content_path`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SkillContentSync {
    /// Skill has no `[skill].kref` set — registration hasn't run yet,
    /// nothing to sync.
    NotRegistered,
    /// `content_file` already matches the published revision's
    /// artifact — no rewrite needed.
    AlreadyCurrent,
    /// `content_file` was out of date and has been rewritten in place
    /// to point at the published revision's artifact path.
    Updated {
        manifest: PathBuf,
        new_content_file: String,
    },
}

/// Resolve a registered skill's `?t=published` kref via Kumiho and
/// rewrite SKILL.toml's `[skill].content_file` to match the artifact
/// the published revision points at.
///
/// This is the load-time projection of the kref — without it the
/// loader would still read whatever `content_file` was set when the
/// skill was first registered, even after [`SkillImprover`] (step 6e)
/// creates a new revision and retags `published`.  Running this at
/// daemon startup keeps the disk-side cache consistent with the graph.
///
/// Idempotent on already-current skills; safe to call from the
/// daemon-startup scan in `src/gateway/mod.rs::run_gateway`.
///
/// [`SkillImprover`]: crate::skills::improver::SkillImprover
pub async fn sync_published_content_path(
    skill_dir: &Path,
    client: &KumihoClient,
) -> Result<SkillContentSync> {
    let manifest_path = skill_dir.join("SKILL.toml");
    if !manifest_path.exists() {
        return Err(anyhow!("no SKILL.toml at {}", manifest_path.display()));
    }
    let manifest = read_manifest(&manifest_path)?;

    let Some(kref) = manifest.skill.kref.as_ref() else {
        return Ok(SkillContentSync::NotRegistered);
    };

    // Parse the kref into the item_kref portion + the tag.  Manifests
    // always use `?t=published` (build_published_kref) but we tolerate
    // either query-string ordering / extra params in case a future
    // step writes `?t=stable` or similar.
    let (item_kref, tag) = parse_kref_tag(kref);
    let item_kref = item_kref.to_string();
    let tag = tag.unwrap_or(PUBLISHED_TAG).to_string();

    // Resolve via Kumiho.  Network failures are caller's problem to
    // log — we propagate them so the daemon-startup scan can record
    // which skills failed to sync.
    let revision = client
        .get_revision_by_tag(&item_kref, &tag)
        .await
        .with_context(|| format!("get_revision_by_tag({item_kref}, {tag})"))?;

    let artifacts = client
        .get_artifacts(&revision.kref)
        .await
        .with_context(|| format!("get_artifacts({})", revision.kref))?;

    // Prefer the canonical `skill` artifact; fall back to the first
    // artifact if a future revision uses a different name.
    let artifact = artifacts
        .iter()
        .find(|a| a.name == SKILL_ARTIFACT_NAME)
        .or_else(|| artifacts.first())
        .ok_or_else(|| anyhow!("revision {} has no artifacts", revision.kref))?;

    // Convert the artifact's `file://` location into a relative path
    // inside the skill directory.  Falls back to the absolute string
    // when the artifact is outside the skill dir (shouldn't happen for
    // skills we registered, but stays correct for unusual layouts).
    let abs = parse_file_uri(&artifact.location).ok_or_else(|| {
        anyhow!(
            "artifact location {:?} is not a file:// URI; cannot sync content_file",
            artifact.location
        )
    })?;
    let rel = match abs.strip_prefix(skill_dir) {
        Ok(stripped) => stripped.to_string_lossy().into_owned(),
        Err(_) => abs.to_string_lossy().into_owned(),
    };

    if manifest.skill.content_file.as_deref() == Some(rel.as_str()) {
        return Ok(SkillContentSync::AlreadyCurrent);
    }

    // Rewrite the manifest with the new content_file.  Other fields
    // (kref, name, description, version, tools, ...) round-trip
    // unchanged thanks to skip_serializing_if + the toml round-trip.
    let mut updated = manifest;
    updated.skill.content_file = Some(rel.clone());
    let serialized =
        toml::to_string_pretty(&updated).context("serializing SKILL.toml after sync")?;
    std::fs::write(&manifest_path, serialized.as_bytes())
        .with_context(|| format!("writing {}", manifest_path.display()))?;

    Ok(SkillContentSync::Updated {
        manifest: manifest_path,
        new_content_file: rel,
    })
}

/// Split a kref of the form `kref://…?t=published` into its (item_kref,
/// tag) pair.  Returns the bare item_kref + None when no `?t=` query
/// is present.  Tolerates additional query params (`?r=N`, `?as_of=`)
/// by scanning for the `t=` segment.
fn parse_kref_tag(kref: &str) -> (&str, Option<&str>) {
    let Some((base, query)) = kref.split_once('?') else {
        return (kref, None);
    };
    for part in query.split('&') {
        if let Some(value) = part.strip_prefix("t=") {
            return (base, Some(value));
        }
    }
    (base, None)
}

/// Parse a `file://` URI into the local path it references.  Reverses
/// the percent-escaping applied by [`format_file_uri`].
fn parse_file_uri(uri: &str) -> Option<PathBuf> {
    let path_str = uri.strip_prefix("file://")?;
    Some(PathBuf::from(path_str.replace("%25", "%")))
}

/// Read and parse the manifest TOML.  Kept as a small helper so
/// callers + tests can exercise the parsing path consistently.
fn read_manifest(path: &Path) -> Result<SkillManifest> {
    let raw = std::fs::read_to_string(path)
        .with_context(|| format!("reading SKILL.toml: {}", path.display()))?;
    toml::from_str(&raw).with_context(|| format!("parsing SKILL.toml: {}", path.display()))
}

/// Resolve the absolute path of the file the artifact will reference.
/// Prefers `[skill].content_file`; falls back to a synthesized
/// `contents/r1.md` only when the migrator hasn't run yet (which the
/// caller guarantees won't happen — but we error cleanly if it does).
fn resolve_content_file(skill_dir: &Path, manifest: &SkillManifest) -> Result<PathBuf> {
    let rel = manifest.skill.content_file.as_deref().ok_or_else(|| {
        anyhow!(
            "skill {:?} has no content_file after migration; cannot register",
            manifest.skill.name
        )
    })?;
    let path = skill_dir.join(rel);
    if !path.exists() {
        return Err(anyhow!(
            "skill content_file does not exist: {}",
            path.display()
        ));
    }
    // Resolve to an absolute path so the artifact location is portable
    // across daemon working directories.
    std::fs::canonicalize(&path).with_context(|| format!("canonicalising {}", path.display()))
}

/// Build the metadata HashMap stored on the Kumiho revision.  Kept
/// minimal — name / description / version / author / tags — so the
/// revision is queryable without fetching the artifact.  Tags are
/// joined with `,` because Kumiho metadata values are strings.
fn revision_metadata_from_manifest(manifest: &SkillManifest) -> HashMap<String, String> {
    let mut meta = HashMap::with_capacity(5);
    meta.insert("name".into(), manifest.skill.name.clone());
    meta.insert("description".into(), manifest.skill.description.clone());
    meta.insert("version".into(), manifest.skill.version.clone());
    if let Some(a) = &manifest.skill.author {
        meta.insert("author".into(), a.clone());
    }
    if !manifest.skill.tags.is_empty() {
        meta.insert("tags".into(), manifest.skill.tags.join(","));
    }
    meta
}

/// Format an absolute path as a `file://` URI suitable for a Kumiho
/// artifact location.  Uses simple URL-escaping of percent characters
/// so paths with `%` round-trip cleanly.
fn format_file_uri(path: &Path) -> String {
    let s = path.to_string_lossy();
    format!("file://{}", s.replace('%', "%25"))
}

/// Read SKILL.toml, set `[skill].kref`, write it back.  Preserves
/// every other field through the toml round-trip.
fn write_kref_to_manifest(path: &Path, kref: &str) -> Result<()> {
    let mut manifest = read_manifest(path)?;
    manifest.skill.kref = Some(kref.to_string());
    let serialized =
        toml::to_string_pretty(&manifest).context("serializing SKILL.toml with kref")?;
    std::fs::write(path, serialized.as_bytes())
        .with_context(|| format!("writing SKILL.toml: {}", path.display()))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn build_published_kref_uses_memory_project_and_slug() {
        let kref = build_published_kref("CognitiveMemory", "code-review");
        assert_eq!(
            kref,
            "kref://CognitiveMemory/Skills/code-review.skill?t=published"
        );
    }

    #[test]
    fn build_published_kref_respects_custom_memory_project() {
        // `memory_project` comes from config — never hardcode CognitiveMemory.
        let kref = build_published_kref("MyOrgMemory", "deploy-runner");
        assert_eq!(
            kref,
            "kref://MyOrgMemory/Skills/deploy-runner.skill?t=published"
        );
    }

    #[test]
    fn format_file_uri_basic() {
        let p = PathBuf::from("/Users/neo/.construct/workspace/skills/foo/contents/r1.md");
        assert_eq!(
            format_file_uri(&p),
            "file:///Users/neo/.construct/workspace/skills/foo/contents/r1.md"
        );
    }

    #[test]
    fn format_file_uri_escapes_percent() {
        let p = PathBuf::from("/tmp/has%percent");
        assert_eq!(format_file_uri(&p), "file:///tmp/has%25percent");
    }

    #[test]
    fn revision_metadata_from_manifest_includes_required_fields() {
        let manifest = SkillManifest {
            skill: super::super::SkillMeta {
                name: "demo".into(),
                description: "demo skill".into(),
                version: "0.1.0".into(),
                author: Some("alice".into()),
                tags: vec!["safety".into(), "ops".into()],
                content_file: Some("contents/r1.md".into()),
                kref: None,
            },
            tools: vec![],
            prompts: vec![],
        };
        let meta = revision_metadata_from_manifest(&manifest);
        assert_eq!(meta.get("name").map(String::as_str), Some("demo"));
        assert_eq!(
            meta.get("description").map(String::as_str),
            Some("demo skill")
        );
        assert_eq!(meta.get("version").map(String::as_str), Some("0.1.0"));
        assert_eq!(meta.get("author").map(String::as_str), Some("alice"));
        assert_eq!(meta.get("tags").map(String::as_str), Some("safety,ops"));
    }

    #[test]
    fn revision_metadata_omits_optional_when_absent() {
        let manifest = SkillManifest {
            skill: super::super::SkillMeta {
                name: "minimal".into(),
                description: "x".into(),
                version: "0.1.0".into(),
                author: None,
                tags: vec![],
                content_file: Some("contents/r1.md".into()),
                kref: None,
            },
            tools: vec![],
            prompts: vec![],
        };
        let meta = revision_metadata_from_manifest(&manifest);
        assert!(!meta.contains_key("author"));
        assert!(!meta.contains_key("tags"));
    }

    #[test]
    fn write_kref_to_manifest_round_trips_other_fields() {
        let dir = tempfile::tempdir().unwrap();
        let manifest_path = dir.path().join("SKILL.toml");
        std::fs::write(
            &manifest_path,
            r#"[skill]
name = "round-trip"
description = "preserves other fields"
version = "0.4.2"
content_file = "contents/r1.md"
"#,
        )
        .unwrap();

        write_kref_to_manifest(
            &manifest_path,
            "kref://CognitiveMemory/Skills/round-trip.skill?t=published",
        )
        .unwrap();

        let raw = std::fs::read_to_string(&manifest_path).unwrap();
        assert!(raw.contains("name = \"round-trip\""));
        assert!(raw.contains("version = \"0.4.2\""));
        assert!(raw.contains("content_file = \"contents/r1.md\""));
        assert!(
            raw.contains(
                "kref = \"kref://CognitiveMemory/Skills/round-trip.skill?t=published\""
            )
        );
    }

    #[test]
    fn resolve_content_file_errors_when_path_missing() {
        let dir = tempfile::tempdir().unwrap();
        let manifest = SkillManifest {
            skill: super::super::SkillMeta {
                name: "missing".into(),
                description: "x".into(),
                version: "0.1.0".into(),
                author: None,
                tags: vec![],
                content_file: Some("contents/does-not-exist.md".into()),
                kref: None,
            },
            tools: vec![],
            prompts: vec![],
        };
        let err = resolve_content_file(dir.path(), &manifest).unwrap_err();
        assert!(err.to_string().contains("does not exist"));
    }

    #[test]
    fn resolve_content_file_errors_when_not_set() {
        let dir = tempfile::tempdir().unwrap();
        let manifest = SkillManifest {
            skill: super::super::SkillMeta {
                name: "no-pointer".into(),
                description: "x".into(),
                version: "0.1.0".into(),
                author: None,
                tags: vec![],
                content_file: None,
                kref: None,
            },
            tools: vec![],
            prompts: vec![],
        };
        let err = resolve_content_file(dir.path(), &manifest).unwrap_err();
        assert!(err.to_string().contains("no content_file"));
    }

    // ── kref/tag parsing + file URI round-trip (step 6d) ──────────────

    #[test]
    fn parse_kref_tag_extracts_published() {
        let (base, tag) = parse_kref_tag("kref://CognitiveMemory/Skills/foo.skill?t=published");
        assert_eq!(base, "kref://CognitiveMemory/Skills/foo.skill");
        assert_eq!(tag, Some("published"));
    }

    #[test]
    fn parse_kref_tag_extracts_arbitrary_tag() {
        let (base, tag) = parse_kref_tag("kref://CognitiveMemory/Skills/foo.skill?t=stable");
        assert_eq!(base, "kref://CognitiveMemory/Skills/foo.skill");
        assert_eq!(tag, Some("stable"));
    }

    #[test]
    fn parse_kref_tag_returns_none_when_query_absent() {
        let (base, tag) = parse_kref_tag("kref://CognitiveMemory/Skills/foo.skill");
        assert_eq!(base, "kref://CognitiveMemory/Skills/foo.skill");
        assert_eq!(tag, None);
    }

    #[test]
    fn parse_kref_tag_skips_unrelated_query_params() {
        // Future-compatible: an `?r=3` revision selector with no `t=`
        // means no published tag — return None so the caller can pick
        // a sensible default rather than picking up `r=3` as the tag.
        let (base, tag) = parse_kref_tag("kref://CognitiveMemory/Skills/foo.skill?r=3");
        assert_eq!(base, "kref://CognitiveMemory/Skills/foo.skill");
        assert_eq!(tag, None);
    }

    #[test]
    fn parse_kref_tag_finds_t_among_multiple_params() {
        let (base, tag) = parse_kref_tag("kref://CognitiveMemory/Skills/foo.skill?r=3&t=stable");
        assert_eq!(base, "kref://CognitiveMemory/Skills/foo.skill");
        assert_eq!(tag, Some("stable"));
    }

    #[test]
    fn parse_kref_tag_handles_legacy_skilldef_suffix() {
        // Legacy items created under the old `kind = "skilldef"` carry
        // krefs with `.skilldef` in their item path.  parse_kref_tag is
        // suffix-agnostic: the lookup fallback in
        // `crate::gateway::kumiho_client::search_skills_with_legacy`
        // relies on this so both old and new krefs round-trip cleanly.
        let (base, tag) =
            parse_kref_tag("kref://CognitiveMemory/Skills/foo.skilldef?t=published");
        assert_eq!(base, "kref://CognitiveMemory/Skills/foo.skilldef");
        assert_eq!(tag, Some("published"));
    }

    #[test]
    fn parse_file_uri_strips_scheme_and_unescapes() {
        let p = parse_file_uri("file:///tmp/x").unwrap();
        assert_eq!(p, PathBuf::from("/tmp/x"));

        let p = parse_file_uri("file:///tmp/has%25percent").unwrap();
        assert_eq!(p, PathBuf::from("/tmp/has%percent"));
    }

    #[test]
    fn parse_file_uri_returns_none_for_non_file_scheme() {
        assert!(parse_file_uri("http://example.com").is_none());
        assert!(parse_file_uri("/no/scheme").is_none());
    }

    #[test]
    fn file_uri_round_trips_through_parse() {
        let original = PathBuf::from("/Users/neo/.construct/workspace/skills/foo/contents/r1.md");
        let uri = format_file_uri(&original);
        let parsed = parse_file_uri(&uri).expect("round trip");
        assert_eq!(parsed, original);

        // With a percent in the path:
        let weird = PathBuf::from("/tmp/has%percent");
        let uri = format_file_uri(&weird);
        let parsed = parse_file_uri(&uri).expect("round trip");
        assert_eq!(parsed, weird);
    }

    // ── publish_skill_revision pre-flight checks (step 6e) ────────────

    #[tokio::test]
    async fn publish_skill_revision_errors_when_no_skill_toml() {
        let dir = tempfile::tempdir().unwrap();
        let client = KumihoClient::new("http://127.0.0.1:1".into(), "test".into());
        let err = publish_skill_revision(
            dir.path(),
            &dir.path().join("contents/r2.md"),
            "test",
            &client,
            "CognitiveMemory",
        )
        .await
        .unwrap_err();
        assert!(err.to_string().contains("no SKILL.toml"), "got: {err}");
    }

    #[tokio::test]
    async fn publish_skill_revision_errors_when_no_kref_in_manifest() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(
            dir.path().join("SKILL.toml"),
            r#"[skill]
name = "unregistered"
description = "no kref yet"
version = "0.1.0"
content_file = "contents/r1.md"
"#,
        )
        .unwrap();
        std::fs::create_dir_all(dir.path().join("contents")).unwrap();
        std::fs::write(dir.path().join("contents/r1.md"), "body").unwrap();
        let new_file = dir.path().join("contents/r2.md");
        std::fs::write(&new_file, "improved").unwrap();

        let client = KumihoClient::new("http://127.0.0.1:1".into(), "test".into());
        let err = publish_skill_revision(dir.path(), &new_file, "test", &client, "CognitiveMemory")
            .await
            .unwrap_err();
        assert!(
            err.to_string().contains("skill not registered yet"),
            "got: {err}"
        );
    }

    #[tokio::test]
    async fn publish_skill_revision_errors_on_project_mismatch() {
        // Skill was registered against `OldProject` but config now
        // points at `NewProject` — surface this as a hard error rather
        // than silently writing a revision under the wrong project.
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(
            dir.path().join("SKILL.toml"),
            r#"[skill]
name = "moved"
description = "x"
version = "0.1.0"
content_file = "contents/r1.md"
kref = "kref://OldProject/Skills/moved.skill?t=published"
"#,
        )
        .unwrap();
        std::fs::create_dir_all(dir.path().join("contents")).unwrap();
        std::fs::write(dir.path().join("contents/r1.md"), "body").unwrap();
        let new_file = dir.path().join("contents/r2.md");
        std::fs::write(&new_file, "improved").unwrap();

        let client = KumihoClient::new("http://127.0.0.1:1".into(), "test".into());
        let err = publish_skill_revision(dir.path(), &new_file, "test", &client, "NewProject")
            .await
            .unwrap_err();
        let msg = err.to_string();
        assert!(
            msg.contains("does not match configured memory_project"),
            "got: {msg}"
        );
        // Should mention both projects so the operator can debug.
        assert!(msg.contains("OldProject"), "got: {msg}");
        assert!(msg.contains("NewProject"), "got: {msg}");
    }

    #[tokio::test]
    async fn publish_skill_revision_errors_when_new_file_missing() {
        // The pre-flight checks pass but the content file path doesn't
        // exist on disk — `canonicalize` should fail cleanly without
        // touching Kumiho.
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(
            dir.path().join("SKILL.toml"),
            r#"[skill]
name = "ghost"
description = "x"
version = "0.1.0"
content_file = "contents/r1.md"
kref = "kref://CognitiveMemory/Skills/ghost.skill?t=published"
"#,
        )
        .unwrap();
        std::fs::create_dir_all(dir.path().join("contents")).unwrap();
        std::fs::write(dir.path().join("contents/r1.md"), "body").unwrap();
        // Note: r2.md is intentionally not written.
        let phantom = dir.path().join("contents/r2-does-not-exist.md");

        let client = KumihoClient::new("http://127.0.0.1:1".into(), "test".into());
        let err = publish_skill_revision(dir.path(), &phantom, "test", &client, "CognitiveMemory")
            .await
            .unwrap_err();
        assert!(
            err.to_string().contains("canonicalising new content file"),
            "got: {err}"
        );
    }

    // ── rollback_skill_revision pre-flight checks (step 6f-A) ────────

    #[tokio::test]
    async fn rollback_skill_revision_errors_when_no_skill_toml() {
        let dir = tempfile::tempdir().unwrap();
        let client = KumihoClient::new("http://127.0.0.1:1".into(), "test".into());
        let err = rollback_skill_revision(dir.path(), &client, "CognitiveMemory")
            .await
            .unwrap_err();
        assert!(err.to_string().contains("no SKILL.toml"), "got: {err}");
    }

    #[tokio::test]
    async fn rollback_skill_revision_errors_when_no_kref_in_manifest() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(
            dir.path().join("SKILL.toml"),
            r#"[skill]
name = "unregistered"
description = "no kref yet"
version = "0.1.0"
content_file = "contents/r1.md"
"#,
        )
        .unwrap();

        let client = KumihoClient::new("http://127.0.0.1:1".into(), "test".into());
        let err = rollback_skill_revision(dir.path(), &client, "CognitiveMemory")
            .await
            .unwrap_err();
        assert!(
            err.to_string().contains("nothing to roll back"),
            "got: {err}"
        );
    }

    #[tokio::test]
    async fn rollback_skill_revision_errors_on_project_mismatch() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(
            dir.path().join("SKILL.toml"),
            r#"[skill]
name = "moved"
description = "x"
version = "0.1.0"
content_file = "contents/r1.md"
kref = "kref://OldProject/Skills/moved.skill?t=published"
"#,
        )
        .unwrap();

        let client = KumihoClient::new("http://127.0.0.1:1".into(), "test".into());
        let err = rollback_skill_revision(dir.path(), &client, "NewProject")
            .await
            .unwrap_err();
        let msg = err.to_string();
        assert!(
            msg.contains("does not match configured memory_project"),
            "got: {msg}"
        );
        assert!(msg.contains("OldProject"), "got: {msg}");
        assert!(msg.contains("NewProject"), "got: {msg}");
    }

    #[test]
    fn previous_published_tag_is_distinct_from_published() {
        // Sanity check: the rollback target tag must not collide with
        // the live tag, otherwise tag_revision's move-semantics would
        // make a publish + rollback indistinguishable.
        assert_ne!(PUBLISHED_TAG, PREVIOUS_PUBLISHED_TAG);
        assert_eq!(PREVIOUS_PUBLISHED_TAG, "previous_published");
    }

    #[test]
    fn skill_item_kind_is_skill_with_legacy_skilldef_distinct() {
        // The kind rename invariant: writes go through `SKILL_ITEM_KIND`
        // ("skill"), and lookup fallbacks key off `LEGACY_SKILL_ITEM_KIND`
        // ("skilldef").  Asserting both values + their distinctness here
        // guards the gateway's `search_items_with_legacy` helper from a
        // refactor that accidentally collapses them back to a single
        // value (which would silently lose the legacy-data fallback).
        assert_eq!(SKILL_ITEM_KIND, "skill");
        assert_eq!(LEGACY_SKILL_ITEM_KIND, "skilldef");
        assert_ne!(SKILL_ITEM_KIND, LEGACY_SKILL_ITEM_KIND);
    }

    #[test]
    fn build_published_kref_uses_new_skill_suffix() {
        // The kref-format flip from `.skilldef` to `.skill`: writes must
        // emit the new suffix.  Legacy data carrying the `.skilldef`
        // suffix is reachable via the search fallback in
        // `kumiho_client::search_skills` — see
        // `parse_kref_tag_handles_legacy_skilldef_suffix` for the read
        // side of that contract.
        let kref = build_published_kref("CognitiveMemory", "demo");
        assert!(kref.contains(".skill?"), "expected .skill suffix in {kref}");
        assert!(
            !kref.contains(".skilldef"),
            "fresh kref must not include legacy suffix: {kref}"
        );
    }
}
