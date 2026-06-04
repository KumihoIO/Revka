//! Daemon-level effectiveness cache.
//!
//! Step 4 of the self-improving agent plan.  Backs the read side of
//! `record_skill_outcome` from PR #23 with a process-wide cache the
//! prompt builder can query synchronously while building each agent's
//! system prompt.
//!
//! ## Layout
//!
//! Per the operator-side handler in PR #23, each successful or failed
//! skill use is recorded under
//! `<memory_project>/Skills/<skill>/Outcomes/` with the outcome's slug
//! prefixed by `ok-` (success) or `fail-` (failure).  This cache lists
//! those items via [`KumihoClient::list_items`], classifies each by the
//! prefix, and stores `(rate, total)` per skill.
//!
//! ## Concurrency
//!
//! The cache is `Arc<EffectivenessCache>`-shareable and uses an internal
//! `RwLock` so the prompt builder can read scores without blocking the
//! background refresh task.  Refreshes are coalesced — multiple concurrent
//! callers of `refresh_for_skills` overlap their work but only the latest
//! snapshot is published.

use std::collections::HashMap;
use std::sync::{Arc, OnceLock};
use std::time::{Duration, Instant};

use parking_lot::RwLock;

use crate::gateway::kumiho_client::{ItemResponse, KumihoClient, KumihoError};
use crate::skills::effectiveness::{EffectivenessScore, SkillEffectivenessProvider};

/// Default refresh interval for the background task — 5 minutes is a
/// reasonable balance between freshness (so a recent regression in a
/// skill propagates to the rerank quickly) and load on Kumiho (one
/// list-items call per skill per interval).
pub const DEFAULT_REFRESH_INTERVAL: Duration = Duration::from_secs(5 * 60);

/// Default success-rate threshold below which a skill becomes a candidate
/// for improvement.  0.4 means "fails more than 60% of the time on the
/// recent window" — well below the 0.5 NEUTRAL line we use for cold-start
/// skills, so tagging a skill as a candidate is a strong signal that
/// something has actually regressed.
pub const DEFAULT_IMPROVEMENT_THRESHOLD: f64 = 0.4;

/// Minimum number of recorded outcomes before a skill is eligible to be
/// flagged as an improvement candidate.  Below this we treat the rate as
/// statistically noisy and skip the warning.  10 is a small enough sample
/// to act on in practice without firing on a single bad run.
pub const DEFAULT_IMPROVEMENT_MIN_SAMPLES: u32 = 10;

/// Minimum success-rate drop between the previous and current published
/// revision before [`EffectivenessCache::regression_candidates`] flags a
/// rollback candidate.  15 percentage points is large enough to dwarf
/// the natural per-revision sampling noise on a 10-sample window, but
/// small enough to catch a meaningfully worse rewrite.
pub const DEFAULT_REGRESSION_DROP: f64 = 0.15;

/// Minimum number of outcomes recorded against the *current* published
/// revision before regression detection will consider rolling it back.
/// Without this guard a skill with one bad outcome on a freshly-published
/// revision would be reverted before it had a chance to prove itself.
pub const DEFAULT_REGRESSION_MIN_SAMPLES: u32 = 10;

/// A skill whose recent rolling success rate has dropped below the
/// improvement threshold.  Future work (LLM-driven `SkillImprover`
/// integration) consumes this signal to actually rewrite the skill;
/// today the daemon emits a `tracing::warn!` per candidate so operators
/// can see which skills are regressing.
#[derive(Debug, Clone)]
pub struct SkillImprovementCandidate {
    pub skill_name: String,
    /// Most recent `successes / (successes + failures)` rate.
    pub rate: f64,
    /// Total resolved outcomes feeding the rate.
    pub total: u32,
}

/// A skill whose freshly-published revision is performing materially
/// worse than its predecessor.  Step 6f-B emits these so the daemon's
/// auto-rollback loop can call `rollback_skill_revision` and put the
/// previous revision back in front of agents.
#[derive(Debug, Clone)]
pub struct SkillRegressionCandidate {
    pub skill_name: String,
    /// Kref of the freshly published revision whose stats are bad.
    pub current_revision_kref: String,
    /// Success rate observed against the current revision.
    pub current_rate: f64,
    /// Total resolved outcomes against the current revision.
    pub current_total: u32,
    /// Kref of the prior `published` revision — the rollback target.
    pub previous_revision_kref: String,
    /// Success rate observed against the previous revision.  Useful for
    /// audit logging the size of the regression.
    pub previous_rate: f64,
    /// Total resolved outcomes against the previous revision.
    pub previous_total: u32,
}

// ── Process-wide cache handle ───────────────────────────────────────────────
//
// The daemon installs one [`EffectivenessCache`] at startup; everything that
// builds an agent prompt downstream reads it via [`global_provider`].  Tests
// revka local caches and bypass this entirely.

static GLOBAL_CACHE: OnceLock<Arc<EffectivenessCache>> = OnceLock::new();

/// Install a process-wide [`EffectivenessCache`].  Returns `Err` if a cache
/// has already been installed — the daemon is intended to call this exactly
/// once during startup.
pub fn set_global(cache: Arc<EffectivenessCache>) -> Result<(), &'static str> {
    GLOBAL_CACHE
        .set(cache)
        .map_err(|_| "effectiveness_cache global already installed")
}

/// Borrow the installed cache, or `None` if the daemon hasn't installed one
/// yet (CLI tools, tests, early startup).
pub fn global() -> Option<&'static Arc<EffectivenessCache>> {
    GLOBAL_CACHE.get()
}

/// Borrow the installed cache as a [`SkillEffectivenessProvider`] reference,
/// suitable for passing into the prompt builder's reranked path.
pub fn global_provider() -> Option<&'static dyn SkillEffectivenessProvider> {
    GLOBAL_CACHE
        .get()
        .map(|arc| arc.as_ref() as &dyn SkillEffectivenessProvider)
}

/// Per-revision regression state captured during refresh.  Step 6f-B
/// stores both the currently-published revision kref and the
/// immediately-prior one (resolved via Kumiho's `published` /
/// `previous_published` tags) along with the outcome scores bucketed by
/// `skill_kref` metadata.  The `regression_candidates` filter consumes
/// this synchronously without any further Kumiho calls.
#[derive(Default)]
struct RevisionState {
    /// Kref currently tagged `published` for this skill.
    current_kref: Option<String>,
    /// Kref currently tagged `previous_published` — the rollback target.
    previous_kref: Option<String>,
    /// Outcome scores bucketed by the `skill_kref` metadata field.
    /// Outcomes recorded without a `skill_kref` tag (legacy / pre-step-6e
    /// runs) are dropped from this map and only contribute to the
    /// aggregate `scores` map above.
    per_revision: HashMap<String, EffectivenessScore>,
}

/// Process-wide cache of recency-weighted skill effectiveness scores.
///
/// Revka this once at daemon startup, share via `Arc`, and call
/// [`Self::refresh_for_skills`] periodically (or use
/// [`Self::spawn_refresh_task`] for the standard background loop).
pub struct EffectivenessCache {
    scores: RwLock<HashMap<String, EffectivenessScore>>,
    /// Step 6f-B: per-skill state needed to detect regressions across a
    /// fresh `publish_skill_revision` and decide whether to roll back
    /// to the prior `previous_published` revision.  Empty for skills
    /// the refresh task hasn't been able to resolve tags for (e.g.
    /// brand-new skills with only one published revision so far).
    revision_state: RwLock<HashMap<String, RevisionState>>,
    /// Wall-clock time of the most recent successful refresh.  Used by
    /// callers that want to know whether the cache has any usable data
    /// before consulting it.
    last_refresh: RwLock<Option<Instant>>,
}

impl EffectivenessCache {
    /// Empty cache — every skill returns `None` until the first refresh
    /// completes.
    pub fn new() -> Arc<Self> {
        Arc::new(Self {
            scores: RwLock::new(HashMap::new()),
            revision_state: RwLock::new(HashMap::new()),
            last_refresh: RwLock::new(None),
        })
    }

    /// Number of skills currently in the cache.  Mostly useful for tests.
    pub fn len(&self) -> usize {
        self.scores.read().len()
    }

    /// Returns true when no scores have been loaded yet.
    pub fn is_empty(&self) -> bool {
        self.scores.read().is_empty()
    }

    /// Snapshot of the current scores — convenient for diagnostics or
    /// constructing an [`InMemoryProvider`] in tests.
    ///
    /// [`InMemoryProvider`]: crate::skills::effectiveness::InMemoryProvider
    pub fn snapshot(&self) -> HashMap<String, EffectivenessScore> {
        self.scores.read().clone()
    }

    /// `Some(elapsed)` since the last successful refresh, or `None` if
    /// the cache has never been refreshed.
    pub fn age(&self) -> Option<Duration> {
        self.last_refresh.read().map(|t| t.elapsed())
    }

    /// Replace the per-skill score map atomically.  Used internally by
    /// `refresh_for_skills` after computing fresh scores; exposed `pub`
    /// so callers (mostly tests + future daemon hooks) can publish their
    /// own snapshots without going through Kumiho.
    pub fn replace_scores(&self, scores: HashMap<String, EffectivenessScore>) {
        *self.scores.write() = scores;
        *self.last_refresh.write() = Some(Instant::now());
    }

    /// Replace the per-skill revision state.  Pure helper exposed for
    /// tests and the refresh task; production callers go through
    /// `refresh_for_skills`.
    fn replace_revision_state(&self, state: HashMap<String, RevisionState>) {
        *self.revision_state.write() = state;
    }

    /// Test-only: install a synthetic revision state so unit tests can
    /// exercise `regression_candidates` without spinning up Kumiho.
    #[cfg(test)]
    fn install_test_revision_state(
        &self,
        skill_name: &str,
        current_kref: &str,
        previous_kref: &str,
        per_revision: HashMap<String, EffectivenessScore>,
    ) {
        let mut map = self.revision_state.write();
        map.insert(
            skill_name.to_string(),
            RevisionState {
                current_kref: Some(current_kref.to_string()),
                previous_kref: Some(previous_kref.to_string()),
                per_revision,
            },
        );
    }

    /// Refresh scores for the given skill names by listing each skill's
    /// outcomes space and tallying success / failure items.
    ///
    /// Failures for individual skills are logged and counted as zero —
    /// they don't poison the whole refresh.  The cache is replaced
    /// atomically once all skills have been processed.
    ///
    /// Step 6f-B: alongside the legacy aggregate score, this also
    /// resolves the skill's `published` and `previous_published`
    /// revision krefs and buckets outcomes by their `skill_kref`
    /// metadata field.  That state powers `regression_candidates()`.
    /// Skills that fail any individual Kumiho call (404, network) are
    /// silently dropped from the per-revision map for this cycle — the
    /// aggregate score still publishes so improvement detection isn't
    /// affected.
    pub async fn refresh_for_skills(
        &self,
        client: &KumihoClient,
        memory_project: &str,
        skill_names: &[String],
    ) -> Result<(), KumihoError> {
        let mut new_scores: HashMap<String, EffectivenessScore> = HashMap::new();
        let mut new_revision_state: HashMap<String, RevisionState> = HashMap::new();

        for name in skill_names {
            let safe = sanitize_skill_name(name);
            let space_path = format!("/{memory_project}/Skills/{safe}/Outcomes");
            // `include_deprecated=false` — deprecated outcomes are typically
            // ones the user explicitly retracted, so excluding them from the
            // success-rate calculation is the right default.
            let items = match client.list_items(&space_path, false).await {
                Ok(items) => items,
                Err(KumihoError::Api { status: 404, .. }) => {
                    // Space doesn't exist yet — skill has never been used.
                    Vec::new()
                }
                Err(e) => {
                    tracing::warn!(
                        skill = %name,
                        space = %space_path,
                        error = %e,
                        "skill effectiveness refresh failed for one skill; \
                         excluding from this snapshot",
                    );
                    continue;
                }
            };

            new_scores.insert(name.clone(), classify_outcomes(&items));

            // Per-revision bucketing.  Resolves both the live `published`
            // and the rollback target `previous_published` so the
            // regression detector can compare them synchronously.
            let item_kref = format!(
                "kref://{memory_project}/Skills/{safe}.{kind}",
                kind = crate::skills::registration::SKILL_ITEM_KIND,
            );
            let mut state = RevisionState {
                per_revision: classify_outcomes_per_revision(&items),
                ..Default::default()
            };
            // Best-effort tag resolution.  Either lookup failing leaves
            // the kref as None, which the regression detector treats as
            // "no candidate" and skips silently.
            match client
                .get_revision_by_tag(&item_kref, crate::skills::registration::PUBLISHED_TAG)
                .await
            {
                Ok(rev) => state.current_kref = Some(rev.kref),
                Err(KumihoError::Api { status: 404, .. }) => {
                    // Skill not registered yet — leave empty.
                }
                Err(e) => {
                    tracing::debug!(
                        skill = %name,
                        item_kref = %item_kref,
                        error = %e,
                        "regression refresh: published tag lookup failed; \
                         per-revision bucketing skipped this cycle",
                    );
                }
            }
            match client
                .get_revision_by_tag(
                    &item_kref,
                    crate::skills::registration::PREVIOUS_PUBLISHED_TAG,
                )
                .await
            {
                Ok(rev) => state.previous_kref = Some(rev.kref),
                Err(KumihoError::Api { status: 404, .. }) => {
                    // First publish — no rollback target yet.  Common
                    // and silent.
                }
                Err(e) => {
                    tracing::debug!(
                        skill = %name,
                        item_kref = %item_kref,
                        error = %e,
                        "regression refresh: previous_published tag lookup failed",
                    );
                }
            }
            new_revision_state.insert(name.clone(), state);
        }

        self.replace_scores(new_scores);
        self.replace_revision_state(new_revision_state);

        // Emit a tracing warning per candidate.  This is the daemon-side
        // signal that something has regressed and is the hook a future
        // LLM-driven SkillImprover integration will consume.  We log per-
        // skill (not aggregated) so operators can correlate with workflow
        // run logs.
        for cand in self.improvement_candidates() {
            tracing::warn!(
                skill = %cand.skill_name,
                rate = cand.rate,
                total = cand.total,
                "skill effectiveness: rolling success rate below threshold; \
                 candidate for SkillImprover",
            );
        }

        // Step 6f-B: regression candidates get their own warning log so
        // operators can trace each auto-rollback back to the data that
        // triggered it.
        for cand in self.regression_candidates() {
            tracing::warn!(
                skill = %cand.skill_name,
                current_revision = %cand.current_revision_kref,
                current_rate = cand.current_rate,
                current_total = cand.current_total,
                previous_revision = %cand.previous_revision_kref,
                previous_rate = cand.previous_rate,
                previous_total = cand.previous_total,
                "skill effectiveness: revision regressed against predecessor; \
                 candidate for auto-rollback",
            );
        }

        Ok(())
    }

    /// Skills whose latest cached score is below
    /// [`DEFAULT_IMPROVEMENT_THRESHOLD`] with at least
    /// [`DEFAULT_IMPROVEMENT_MIN_SAMPLES`] resolved outcomes.  Sorted
    /// worst-first so callers iterating with a budget hit the most-broken
    /// skills first.
    pub fn improvement_candidates(&self) -> Vec<SkillImprovementCandidate> {
        let scores = self.scores.read();
        let mut candidates: Vec<SkillImprovementCandidate> = scores
            .iter()
            .filter_map(|(name, score)| {
                let rate = score.rate?;
                if score.total < DEFAULT_IMPROVEMENT_MIN_SAMPLES {
                    return None;
                }
                if rate >= DEFAULT_IMPROVEMENT_THRESHOLD {
                    return None;
                }
                Some(SkillImprovementCandidate {
                    skill_name: name.clone(),
                    rate,
                    total: score.total,
                })
            })
            .collect();
        candidates.sort_by(|a, b| {
            a.rate
                .partial_cmp(&b.rate)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        candidates
    }

    /// Skills whose freshly-published revision is performing materially
    /// worse than its predecessor.  Step 6f-B's auto-rollback loop
    /// consumes these to call `rollback_skill_revision`.
    ///
    /// A skill is a regression candidate when ALL of:
    ///   - Both `published` and `previous_published` revision krefs
    ///     resolved during the last refresh cycle.
    ///   - The current revision has at least
    ///     [`DEFAULT_REGRESSION_MIN_SAMPLES`] outcomes recorded against
    ///     its kref (via the outcome's `skill_kref` metadata field).
    ///   - Both revisions have a defined success rate.
    ///   - The current rate is at least
    ///     [`DEFAULT_REGRESSION_DROP`] below the previous rate —
    ///     i.e. demonstrably worse, not just within sampling noise.
    ///
    /// Sorted by drop magnitude (worst regression first) so callers
    /// iterating with a budget hit the most-broken skills first.
    pub fn regression_candidates(&self) -> Vec<SkillRegressionCandidate> {
        let revision_state = self.revision_state.read();
        let mut candidates: Vec<SkillRegressionCandidate> = revision_state
            .iter()
            .filter_map(|(name, state)| {
                let current_kref = state.current_kref.as_deref()?;
                let previous_kref = state.previous_kref.as_deref()?;
                if current_kref == previous_kref {
                    // Right after a rollback both tags point at the same
                    // revision until the next publish moves
                    // previous_published forward.  Skip — no candidate.
                    return None;
                }
                let current_score = state.per_revision.get(current_kref)?;
                let previous_score = state.per_revision.get(previous_kref)?;
                if current_score.total < DEFAULT_REGRESSION_MIN_SAMPLES {
                    return None;
                }
                let current_rate = current_score.rate?;
                let previous_rate = previous_score.rate?;
                if previous_rate - current_rate < DEFAULT_REGRESSION_DROP {
                    return None;
                }
                Some(SkillRegressionCandidate {
                    skill_name: name.clone(),
                    current_revision_kref: current_kref.to_string(),
                    current_rate,
                    current_total: current_score.total,
                    previous_revision_kref: previous_kref.to_string(),
                    previous_rate,
                    previous_total: previous_score.total,
                })
            })
            .collect();
        // Sort by drop magnitude descending (largest regression first).
        candidates.sort_by(|a, b| {
            let drop_a = a.previous_rate - a.current_rate;
            let drop_b = b.previous_rate - b.current_rate;
            drop_b
                .partial_cmp(&drop_a)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        candidates
    }

    /// Spawn a background tokio task that periodically calls
    /// `refresh_for_skills` until the daemon shuts down.  Returns the
    /// `JoinHandle` so callers can `abort()` on shutdown.
    ///
    /// The task runs immediately once on spawn, then on the supplied
    /// interval.  Errors from individual refresh cycles are logged and
    /// the task continues — only an explicit `abort()` stops it.
    pub fn spawn_refresh_task(
        self: Arc<Self>,
        client: Arc<KumihoClient>,
        memory_project: String,
        skill_names: Vec<String>,
        interval: Duration,
    ) -> tokio::task::JoinHandle<()> {
        tokio::spawn(async move {
            let mut ticker = tokio::time::interval(interval);
            // Don't fall behind if a refresh takes longer than `interval`.
            ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
            loop {
                ticker.tick().await;
                if let Err(e) = self
                    .refresh_for_skills(&client, &memory_project, &skill_names)
                    .await
                {
                    tracing::warn!(
                        error = %e,
                        "effectiveness cache refresh cycle failed; will retry",
                    );
                }
            }
        })
    }
}

impl SkillEffectivenessProvider for EffectivenessCache {
    fn score(&self, skill_name: &str) -> Option<EffectivenessScore> {
        self.scores.read().get(skill_name).copied()
    }
}

/// Sanitize a skill name into the slug used by `record_skill_outcome`
/// when building the storage space path.  Mirrors the Python helper
/// `_outcomes_space` in `skill_outcomes.py`: replace `/` with `-`.
fn sanitize_skill_name(name: &str) -> String {
    name.replace('/', "-")
}

/// Classify a list of outcome items into a single [`EffectivenessScore`].
///
/// Detection mirrors the Python `_outcome_is_success` logic: items
/// whose `item_name` starts with `ok-` count as successes, items whose
/// `item_name` starts with `fail-` count as failures, anything else is
/// ignored as undetermined.  This keeps detection single-round-trip:
/// no per-item revision fetches required because the `[OK]` / `[FAIL]`
/// title prefix is preserved through slugification.
pub(crate) fn classify_outcomes(items: &[ItemResponse]) -> EffectivenessScore {
    let mut successes: u32 = 0;
    let mut failures: u32 = 0;
    for it in items {
        if it.item_name.starts_with("ok-") {
            successes = successes.saturating_add(1);
        } else if it.item_name.starts_with("fail-") {
            failures = failures.saturating_add(1);
        }
    }
    let total = successes.saturating_add(failures);
    let rate = if total == 0 {
        None
    } else {
        Some(f64::from(successes) / f64::from(total))
    };
    EffectivenessScore { rate, total }
}

/// Step 6f-B: bucket outcomes by their `skill_kref` metadata so the
/// regression detector can compare a freshly-published revision
/// against its predecessor.
///
/// Outcomes recorded without a `skill_kref` (legacy / pre-step-6e
/// runs) are dropped — they only contribute to the aggregate score
/// `classify_outcomes` produces, not to per-revision regression
/// detection.  This keeps the per-revision view honest at the cost of
/// silently discarding pre-migration data.
///
/// Outcomes whose `skill_kref` is a tag-pointer (`?t=published`) are
/// also dropped — those identify "whichever revision was current at
/// some point" rather than a concrete revision, so bucketing by them
/// would conflate every revision into one bucket.  Step 6f-C upgrades
/// the operator-side `record_skill_outcome` to resolve tag-pointers
/// to concrete revision krefs at write time.
pub(crate) fn classify_outcomes_per_revision(
    items: &[ItemResponse],
) -> HashMap<String, EffectivenessScore> {
    // Tally successes / failures per kref in a single pass.
    let mut tallies: HashMap<String, (u32, u32)> = HashMap::new();
    for it in items {
        let Some(kref) = it.metadata.get("skill_kref") else {
            continue;
        };
        if is_tag_pointer_kref(kref) {
            continue;
        }
        let entry = tallies.entry(kref.clone()).or_insert((0, 0));
        if it.item_name.starts_with("ok-") {
            entry.0 = entry.0.saturating_add(1);
        } else if it.item_name.starts_with("fail-") {
            entry.1 = entry.1.saturating_add(1);
        }
    }
    tallies
        .into_iter()
        .map(|(kref, (successes, failures))| {
            let total = successes.saturating_add(failures);
            let rate = if total == 0 {
                None
            } else {
                Some(f64::from(successes) / f64::from(total))
            };
            (kref, EffectivenessScore { rate, total })
        })
        .collect()
}

/// Detect kref strings that name a *tag* (e.g. `?t=published`) rather
/// than a concrete revision (`?r=3` or no query string).  Used by
/// `classify_outcomes_per_revision` to drop outcomes that can't be
/// reliably attributed to a specific revision.
fn is_tag_pointer_kref(kref: &str) -> bool {
    let Some((_, query)) = kref.split_once('?') else {
        return false;
    };
    query.split('&').any(|part| part.starts_with("t="))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn item(item_name: &str) -> ItemResponse {
        ItemResponse {
            kref: format!("kref://test/{item_name}"),
            name: item_name.to_string(),
            item_name: item_name.to_string(),
            kind: "skill_outcome".to_string(),
            deprecated: false,
            created_at: None,
            author: None,
            username: None,
            author_display: None,
            metadata: HashMap::new(),
        }
    }

    #[test]
    fn classify_outcomes_counts_ok_and_fail_prefixes() {
        let items = vec![
            item("ok-skill-on-mar-1-aa"),
            item("ok-skill-on-mar-2-bb"),
            item("ok-skill-on-mar-3-cc"),
            item("fail-skill-on-mar-4-dd"),
            item("fail-skill-on-mar-5-ee"),
        ];
        let score = classify_outcomes(&items);
        assert_eq!(score.total, 5);
        assert_eq!(score.rate, Some(0.6));
    }

    #[test]
    fn classify_outcomes_ignores_unprefixed_items() {
        let items = vec![
            item("ok-skill-aa"),
            item("legacy-undated-bb"), // no ok-/fail- prefix
            item("fail-skill-cc"),
        ];
        let score = classify_outcomes(&items);
        assert_eq!(score.total, 2);
        assert_eq!(score.rate, Some(0.5));
    }

    #[test]
    fn classify_outcomes_returns_none_rate_for_empty() {
        let score = classify_outcomes(&[]);
        assert_eq!(score.total, 0);
        assert_eq!(score.rate, None);
    }

    #[test]
    fn cache_score_returns_none_before_refresh() {
        let cache = EffectivenessCache::new();
        assert!(cache.is_empty());
        assert!(cache.score("anything").is_none());
        assert!(cache.age().is_none());
    }

    #[test]
    fn cache_replace_scores_publishes_atomically() {
        let cache = EffectivenessCache::new();
        let mut scores = HashMap::new();
        scores.insert(
            "alpha".to_string(),
            EffectivenessScore {
                rate: Some(0.9),
                total: 10,
            },
        );
        scores.insert(
            "beta".to_string(),
            EffectivenessScore {
                rate: Some(0.1),
                total: 5,
            },
        );
        cache.replace_scores(scores);

        let alpha = cache.score("alpha").expect("alpha present");
        assert_eq!(alpha.rate, Some(0.9));
        assert_eq!(alpha.total, 10);
        let beta = cache.score("beta").expect("beta present");
        assert_eq!(beta.total, 5);
        assert_eq!(cache.len(), 2);
        assert!(cache.age().is_some());
    }

    #[test]
    fn cache_implements_skill_effectiveness_provider() {
        let cache = EffectivenessCache::new();
        let mut scores = HashMap::new();
        scores.insert(
            "foo".to_string(),
            EffectivenessScore {
                rate: Some(0.75),
                total: 4,
            },
        );
        cache.replace_scores(scores);

        // Borrow through the trait — what PromptContext consumers do.
        let provider: &dyn SkillEffectivenessProvider = &*cache;
        assert!(provider.score("foo").is_some());
        assert!(provider.score("missing").is_none());
    }

    // ── Improvement candidate detection ─────────────────────────────────

    #[test]
    fn improvement_candidates_flags_below_threshold_with_enough_samples() {
        let cache = EffectivenessCache::new();
        let mut scores = HashMap::new();
        // total=20, rate=0.3 → BELOW threshold and ABOVE min samples → candidate.
        scores.insert(
            "regressed".to_string(),
            EffectivenessScore {
                rate: Some(0.3),
                total: 20,
            },
        );
        // total=20, rate=0.6 → ABOVE threshold → not a candidate.
        scores.insert(
            "healthy".to_string(),
            EffectivenessScore {
                rate: Some(0.6),
                total: 20,
            },
        );
        cache.replace_scores(scores);

        let candidates = cache.improvement_candidates();
        assert_eq!(candidates.len(), 1);
        assert_eq!(candidates[0].skill_name, "regressed");
        assert!((candidates[0].rate - 0.3).abs() < 1e-9);
        assert_eq!(candidates[0].total, 20);
    }

    #[test]
    fn improvement_candidates_skips_low_sample_skills() {
        let cache = EffectivenessCache::new();
        let mut scores = HashMap::new();
        // total=5 < min_samples → skip even though rate is bad.
        scores.insert(
            "noisy".to_string(),
            EffectivenessScore {
                rate: Some(0.0),
                total: 5,
            },
        );
        cache.replace_scores(scores);
        assert!(cache.improvement_candidates().is_empty());
    }

    #[test]
    fn improvement_candidates_skips_no_data_skills() {
        let cache = EffectivenessCache::new();
        let mut scores = HashMap::new();
        // No data yet — `rate: None`.  Should not be flagged.
        scores.insert(
            "fresh".to_string(),
            EffectivenessScore {
                rate: None,
                total: 0,
            },
        );
        cache.replace_scores(scores);
        assert!(cache.improvement_candidates().is_empty());
    }

    #[test]
    fn improvement_candidates_sorted_worst_first() {
        let cache = EffectivenessCache::new();
        let mut scores = HashMap::new();
        scores.insert(
            "mid".to_string(),
            EffectivenessScore {
                rate: Some(0.3),
                total: 50,
            },
        );
        scores.insert(
            "worst".to_string(),
            EffectivenessScore {
                rate: Some(0.05),
                total: 30,
            },
        );
        scores.insert(
            "barely".to_string(),
            EffectivenessScore {
                rate: Some(0.39),
                total: 100,
            },
        );
        cache.replace_scores(scores);

        let candidates = cache.improvement_candidates();
        assert_eq!(candidates.len(), 3);
        assert_eq!(candidates[0].skill_name, "worst");
        assert_eq!(candidates[1].skill_name, "mid");
        assert_eq!(candidates[2].skill_name, "barely");
    }

    #[test]
    fn sanitize_skill_name_replaces_slashes() {
        assert_eq!(sanitize_skill_name("plain"), "plain");
        assert_eq!(sanitize_skill_name("with/slash"), "with-slash");
        assert_eq!(sanitize_skill_name("a/b/c"), "a-b-c");
    }

    // ── Per-revision classification (step 6f-B) ──────────────────────

    fn item_with_kref(item_name: &str, skill_kref: &str) -> ItemResponse {
        let mut metadata = HashMap::new();
        metadata.insert("skill_kref".to_string(), skill_kref.to_string());
        ItemResponse {
            kref: format!("kref://test/outcomes/{item_name}"),
            name: item_name.to_string(),
            item_name: item_name.to_string(),
            kind: "skill_outcome".to_string(),
            deprecated: false,
            created_at: None,
            author: None,
            username: None,
            author_display: None,
            metadata,
        }
    }

    #[test]
    fn classify_outcomes_per_revision_buckets_by_skill_kref() {
        let r1 = "kref://m/Skills/foo.skilldef?r=1";
        let r2 = "kref://m/Skills/foo.skilldef?r=2";
        let items = vec![
            item_with_kref("ok-a", r1),
            item_with_kref("ok-b", r1),
            item_with_kref("fail-c", r1),
            item_with_kref("ok-d", r2),
            item_with_kref("fail-e", r2),
            item_with_kref("fail-f", r2),
        ];
        let scores = classify_outcomes_per_revision(&items);
        assert_eq!(scores.len(), 2);
        let s1 = scores.get(r1).expect("r1 present");
        assert_eq!(s1.total, 3);
        assert!((s1.rate.unwrap() - (2.0 / 3.0)).abs() < 1e-9);
        let s2 = scores.get(r2).expect("r2 present");
        assert_eq!(s2.total, 3);
        assert!((s2.rate.unwrap() - (1.0 / 3.0)).abs() < 1e-9);
    }

    #[test]
    fn classify_outcomes_per_revision_drops_items_without_skill_kref() {
        let r1 = "kref://m/Skills/foo.skilldef?r=1";
        let items = vec![
            item_with_kref("ok-a", r1),
            // Legacy item with no skill_kref metadata — should be ignored.
            item("ok-legacy"),
        ];
        let scores = classify_outcomes_per_revision(&items);
        assert_eq!(scores.len(), 1);
        assert_eq!(scores.get(r1).unwrap().total, 1);
    }

    #[test]
    fn classify_outcomes_per_revision_drops_tag_pointer_krefs() {
        // Outcomes recorded with the manifest's `?t=published` pointer
        // (instead of a concrete revision_kref) cannot be attributed
        // to a specific revision and must be dropped.  Step 6f-C
        // upgrades the operator to record concrete krefs so this
        // bucket vanishes in production.
        let tag_pointer = "kref://m/Skills/foo.skilldef?t=published";
        let r1 = "kref://m/Skills/foo.skilldef?r=1";
        let items = vec![
            item_with_kref("ok-a", tag_pointer),
            item_with_kref("fail-b", tag_pointer),
            item_with_kref("ok-c", r1),
        ];
        let scores = classify_outcomes_per_revision(&items);
        assert_eq!(scores.len(), 1);
        assert!(scores.contains_key(r1));
        assert!(!scores.contains_key(tag_pointer));
    }

    #[test]
    fn is_tag_pointer_kref_recognises_t_query() {
        assert!(is_tag_pointer_kref(
            "kref://m/Skills/foo.skilldef?t=published"
        ));
        assert!(is_tag_pointer_kref(
            "kref://m/Skills/foo.skilldef?r=1&t=stable"
        ));
        assert!(!is_tag_pointer_kref("kref://m/Skills/foo.skilldef?r=1"));
        assert!(!is_tag_pointer_kref("kref://m/Skills/foo.skilldef"));
    }

    // ── Regression candidates (step 6f-B) ────────────────────────────

    fn rev_score(rate: f64, total: u32) -> EffectivenessScore {
        EffectivenessScore {
            rate: Some(rate),
            total,
        }
    }

    #[test]
    fn regression_candidates_flags_drop_above_threshold() {
        let cache = EffectivenessCache::new();
        let mut per_rev = HashMap::new();
        per_rev.insert("kref://r/2".to_string(), rev_score(0.30, 12));
        per_rev.insert("kref://r/1".to_string(), rev_score(0.80, 50));
        cache.install_test_revision_state("regressed", "kref://r/2", "kref://r/1", per_rev);

        let candidates = cache.regression_candidates();
        assert_eq!(candidates.len(), 1);
        let c = &candidates[0];
        assert_eq!(c.skill_name, "regressed");
        assert_eq!(c.current_revision_kref, "kref://r/2");
        assert!((c.current_rate - 0.30).abs() < 1e-9);
        assert_eq!(c.previous_revision_kref, "kref://r/1");
        assert!((c.previous_rate - 0.80).abs() < 1e-9);
    }

    #[test]
    fn regression_candidates_skips_when_drop_below_threshold() {
        let cache = EffectivenessCache::new();
        let mut per_rev = HashMap::new();
        // Drop is only 0.05 — below DEFAULT_REGRESSION_DROP (0.15).
        per_rev.insert("kref://r/2".to_string(), rev_score(0.55, 30));
        per_rev.insert("kref://r/1".to_string(), rev_score(0.60, 50));
        cache.install_test_revision_state("flat", "kref://r/2", "kref://r/1", per_rev);

        assert!(cache.regression_candidates().is_empty());
    }

    #[test]
    fn regression_candidates_skips_when_current_below_min_samples() {
        let cache = EffectivenessCache::new();
        let mut per_rev = HashMap::new();
        // Drop is huge but only 5 samples on the new revision — too noisy.
        per_rev.insert("kref://r/2".to_string(), rev_score(0.0, 5));
        per_rev.insert("kref://r/1".to_string(), rev_score(0.9, 100));
        cache.install_test_revision_state("noisy", "kref://r/2", "kref://r/1", per_rev);

        assert!(cache.regression_candidates().is_empty());
    }

    #[test]
    fn regression_candidates_skips_when_no_previous_published() {
        let cache = EffectivenessCache::new();
        // First publish — only current_kref is known.
        let mut per_rev = HashMap::new();
        per_rev.insert("kref://r/1".to_string(), rev_score(0.10, 50));
        let mut state_map = cache.revision_state.write();
        state_map.insert(
            "first-publish".to_string(),
            RevisionState {
                current_kref: Some("kref://r/1".to_string()),
                previous_kref: None,
                per_revision: per_rev,
            },
        );
        drop(state_map);

        assert!(cache.regression_candidates().is_empty());
    }

    #[test]
    fn regression_candidates_skips_when_current_equals_previous() {
        // Right after a rollback both tags can briefly point at the
        // same revision until the next publish moves previous_published
        // forward.  Detector must skip so we don't ping-pong.
        let cache = EffectivenessCache::new();
        let mut per_rev = HashMap::new();
        per_rev.insert("kref://r/1".to_string(), rev_score(0.10, 50));
        cache.install_test_revision_state("post-rollback", "kref://r/1", "kref://r/1", per_rev);

        assert!(cache.regression_candidates().is_empty());
    }

    #[test]
    fn regression_candidates_sorted_worst_drop_first() {
        let cache = EffectivenessCache::new();
        // Skill A: drop = 0.20.
        let mut per_rev_a = HashMap::new();
        per_rev_a.insert("kref://a/2".to_string(), rev_score(0.50, 20));
        per_rev_a.insert("kref://a/1".to_string(), rev_score(0.70, 50));
        cache.install_test_revision_state("a", "kref://a/2", "kref://a/1", per_rev_a);
        // Skill B: drop = 0.50.
        let mut per_rev_b = HashMap::new();
        per_rev_b.insert("kref://b/2".to_string(), rev_score(0.30, 20));
        per_rev_b.insert("kref://b/1".to_string(), rev_score(0.80, 50));
        cache.install_test_revision_state("b", "kref://b/2", "kref://b/1", per_rev_b);

        let candidates = cache.regression_candidates();
        assert_eq!(candidates.len(), 2);
        assert_eq!(candidates[0].skill_name, "b");
        assert_eq!(candidates[1].skill_name, "a");
    }
}
