//! Auto-rollback for regressed skill revisions (step 6f-B).
//!
//! [`crate::skills::EffectivenessCache::regression_candidates`] surfaces
//! skills whose freshly-published revision is performing materially
//! worse than its predecessor.  This module is the consumer of those
//! signals: it owns the per-skill cooldown so we don't ping-pong a
//! revision in and out of `published`, and it dispatches the actual
//! Kumiho call via [`crate::skills::registration::rollback_skill_revision`].
//!
//! Gated behind `#[cfg(feature = "skill-creation")]` to mirror the
//! improvement path.
//!
//! ## Cooldown semantics
//!
//! Every successful rollback arms a cooldown for the affected skill.
//! Subsequent regression candidates for the same skill are skipped
//! until the cooldown expires.  The cooldown is **separate** from
//! `SkillImprover`'s improvement cooldown: a rollback of skill A does
//! not delay a future improvement of skill A, and vice versa.  The
//! safety net against re-improving a known-bad skill comes from the
//! improvement cooldown that was already armed when the (now
//! regressed) revision was first published.
//!
//! ## Why the rollback target tag matters
//!
//! `rollback_skill_revision` requires the skill to have a
//! `previous_published` revision tagged in Kumiho.  That tag is set
//! by `publish_skill_revision` (step 6f-A).  A skill that has only
//! ever had one published revision can't be rolled back — the
//! tracker handles this by returning `Ok(None)` when the underlying
//! call fails for that reason, since it's an expected pre-condition
//! rather than a runtime error.

use anyhow::Result;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant};

use crate::gateway::kumiho_client::KumihoClient;
use crate::skills::effectiveness_cache::SkillRegressionCandidate;
use crate::skills::registration::{SkillRollback, rollback_skill_revision};

/// Default cooldown after a successful rollback.  One hour matches the
/// improvement cooldown — long enough for the rolled-back revision's
/// stats to accumulate so we don't immediately bounce again on the
/// next refresh cycle if a couple of late outcomes against the demoted
/// revision come in.
pub const DEFAULT_ROLLBACK_COOLDOWN: Duration = Duration::from_secs(60 * 60);

/// Auto-rollback tracker — manages per-skill cooldowns and dispatches
/// to [`rollback_skill_revision`].  Mirrors the role
/// [`crate::skills::improver::SkillImprover`] plays for the
/// improvement path.
pub struct SkillRollbackTracker {
    workspace_dir: PathBuf,
    cooldown: Duration,
    cooldowns: HashMap<String, Instant>,
}

impl SkillRollbackTracker {
    pub fn new(workspace_dir: PathBuf) -> Self {
        Self {
            workspace_dir,
            cooldown: DEFAULT_ROLLBACK_COOLDOWN,
            cooldowns: HashMap::new(),
        }
    }

    /// Override the rollback cooldown.  Mostly useful for tests.
    #[cfg(test)]
    pub fn with_cooldown(mut self, cooldown: Duration) -> Self {
        self.cooldown = cooldown;
        self
    }

    /// True when the skill is eligible for a rollback attempt right
    /// now (no active cooldown).
    pub fn should_rollback(&self, slug: &str) -> bool {
        match self.cooldowns.get(slug) {
            None => true,
            Some(last) => Instant::now().saturating_duration_since(*last) >= self.cooldown,
        }
    }
}

/// All the pieces an auto-rollback attempt needs from the daemon.
/// Constructed once at startup and threaded into the background task
/// that consumes [`SkillRegressionCandidate`]s.
pub struct AutoRollbackContext {
    /// Revka workspace root — `SKILL.toml` lives at
    /// `<workspace>/skills/<slug>/SKILL.toml`.
    pub workspace_dir: PathBuf,
    /// Kumiho client used to retag `published`.  Shared with the
    /// effectiveness refresh task and the improver so a single daemon
    /// talks to one Kumiho process.
    pub kumiho_client: Arc<KumihoClient>,
    /// Memory project name from `config.kumiho.memory_project`.
    pub memory_project: String,
}

/// Outcome of [`attempt_skill_rollback`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SkillRollbackOutcome {
    /// Slug of the rolled-back skill.
    pub slug: String,
    /// Kref of the revision now tagged `published`.
    pub restored_revision_kref: String,
    /// Kref of the revision that was demoted.  Useful for audit logs.
    pub demoted_revision_kref: String,
    /// Relative `content_file` path SKILL.toml now points at.
    pub content_file: String,
}

/// Attempt to roll back a single regression candidate.
///
/// Returns:
/// - `Ok(Some(outcome))` — `published` was retagged onto the previous
///   revision and SKILL.toml's `content_file` re-synced.  The
///   tracker's cooldown is now armed for this skill.
/// - `Ok(None)` — skipped (cooldown not expired, manifest missing,
///   no `previous_published` revision in Kumiho).  No state changed.
/// - `Err(_)` — fatal error.  Caller should log and continue with
///   the next candidate.
pub async fn attempt_skill_rollback(
    ctx: &AutoRollbackContext,
    candidate: &SkillRegressionCandidate,
    tracker: &mut SkillRollbackTracker,
) -> Result<Option<SkillRollbackOutcome>> {
    if !tracker.should_rollback(&candidate.skill_name) {
        return Ok(None);
    }

    let skill_dir = ctx.workspace_dir.join("skills").join(&candidate.skill_name);
    if !skill_dir.join("SKILL.toml").exists() {
        // Skill on disk is gone — the cache is reflecting outcomes
        // for a skill the workspace no longer holds.  Skip silently.
        tracing::debug!(
            skill = %candidate.skill_name,
            workspace = %skill_dir.display(),
            "auto-rollback: SKILL.toml not found on disk; skipping",
        );
        return Ok(None);
    }

    let result = rollback_skill_revision(&skill_dir, &ctx.kumiho_client, &ctx.memory_project).await;

    match result {
        Ok(SkillRollback {
            restored_revision_kref,
            demoted_revision_kref,
            new_content_file,
        }) => {
            tracker
                .cooldowns
                .insert(candidate.skill_name.clone(), Instant::now());
            Ok(Some(SkillRollbackOutcome {
                slug: candidate.skill_name.clone(),
                restored_revision_kref,
                demoted_revision_kref,
                content_file: new_content_file,
            }))
        }
        Err(e) => {
            // Two recoverable conditions surface as errors today:
            //   1. The skill has no `previous_published` revision
            //      (first-publish skill that regressed).
            //   2. `previous_published == published` (race against a
            //      concurrent publish, or already-rolled-back state).
            // Both should NOT bubble up as fatal — log + skip so the
            // loop continues.  Any other failure is genuinely fatal
            // and we propagate it.
            let msg = format!("{e:#}");
            if msg.contains("nothing to roll back")
                || msg.contains("already the current published")
                || msg.contains(crate::skills::registration::PREVIOUS_PUBLISHED_TAG)
            {
                tracing::debug!(
                    skill = %candidate.skill_name,
                    error = %msg,
                    "auto-rollback: skill not eligible (no rollback target); skipping",
                );
                return Ok(None);
            }
            Err(e)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cooldown_allows_first_attempt() {
        let tracker = SkillRollbackTracker::new(PathBuf::from("/tmp/ws"));
        assert!(tracker.should_rollback("any-skill"));
    }

    #[test]
    fn cooldown_blocks_recent_attempt() {
        let mut tracker = SkillRollbackTracker::new(PathBuf::from("/tmp/ws"))
            .with_cooldown(Duration::from_secs(3600));
        tracker
            .cooldowns
            .insert("recent".to_string(), Instant::now());
        assert!(!tracker.should_rollback("recent"));
        // Other skills are unaffected — cooldown is per-skill.
        assert!(tracker.should_rollback("other"));
    }

    #[test]
    fn cooldown_expires_after_window() {
        let mut tracker = SkillRollbackTracker::new(PathBuf::from("/tmp/ws"))
            .with_cooldown(Duration::from_millis(1));
        tracker
            .cooldowns
            .insert("expired".to_string(), Instant::now());
        std::thread::sleep(Duration::from_millis(5));
        assert!(tracker.should_rollback("expired"));
    }
}
