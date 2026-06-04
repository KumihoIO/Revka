//! LLM-driven skill rewrite (kumiho-versioned, step 6e).
//!
//! Step 5 of the self-improving agent plan — closes the feedback loop.
//! [`crate::skills::EffectivenessCache::improvement_candidates`] surfaces
//! skills whose rolling success rate has regressed; this module takes a
//! candidate, asks an LLM to propose revised **markdown content** (not a
//! full SKILL.toml — see step 6e), validates the response, writes it to
//! a fresh `contents/r<N+1>-<timestamp>.md` via [`SkillImprover`], and
//! publishes it as a new Kumiho revision tagged `published` via
//! [`publish_skill_revision`].
//!
//! Gated behind `#[cfg(feature = "skill-creation")]` to mirror
//! [`SkillImprover`]'s own gate.
//!
//! ## Why we ask the LLM for markdown, not TOML
//!
//! From step 6e onwards, `SKILL.toml` is the **identity** of a skill
//! (name, version, kref pointer, tools).  The agent reads its prompts
//! from the markdown file referenced by `[skill].content_file`.  When
//! the rolling success rate regresses we want to evolve the *prompts*,
//! not the metadata — so the LLM is asked to revise the markdown body.
//! The new revision file lives next to its predecessors under
//! `contents/`, and the published kref retags onto it via
//! `publish_skill_revision`.  The previous file is preserved on disk
//! and in Kumiho so step 6f can roll back to it if the new revision
//! itself regresses.
//!
//! ## Flow
//!
//!   1. Cooldown check via `SkillImprover::should_improve_skill`.
//!   2. Load the manifest; bail if `[skill].content_file` is unset
//!      (legacy skill that hasn't been migrated yet — daemon-startup
//!      registration will fix this on next run).
//!   3. Read the current content file.  Skip silently if missing.
//!   4. Call `provider.chat` with a focused system + user prompt that
//!      includes the current markdown, the regression statistics, and
//!      a strict response format ("only the revised markdown inside a
//!      ```markdown fence").
//!   5. Extract the first ```markdown fence from the response.  Return
//!      early if not present.
//!   6. Hand the new content + reason to `SkillImprover::improve_skill`,
//!      which writes the new file under `contents/` and arms the
//!      cooldown.
//!   7. Hand the new file path to `publish_skill_revision`, which
//!      creates the Kumiho revision + artifact + retags `published`
//!      and updates SKILL.toml's `content_file`.
//!
//! Test coverage focuses on the pure helpers — fence extraction and
//! prompt composition.  The end-to-end async path is covered by the
//! daemon integration that consumes this module.

use anyhow::{Context, Result, anyhow};
use std::path::Path;
use std::sync::Arc;

use crate::gateway::kumiho_client::KumihoClient;
use crate::providers::traits::{ChatMessage, ChatRequest, Provider};
use crate::skills::SkillManifest;
use crate::skills::effectiveness_cache::SkillImprovementCandidate;
use crate::skills::improver::SkillImprover;
use crate::skills::registration::publish_skill_revision;

/// All the pieces an auto-improvement attempt needs from the daemon.  The
/// daemon constructs one of these once at startup and threads it into
/// the background task that consumes [`SkillImprovementCandidate`]s.
pub struct AutoImproveContext {
    /// Revka workspace root — `SKILL.toml` lives at
    /// `<workspace>/skills/<slug>/SKILL.toml`.
    pub workspace_dir: std::path::PathBuf,
    /// LLM provider to call for the rewrite proposal.
    pub provider: Arc<dyn Provider>,
    /// Model name to pass through to `provider.chat`.
    pub model: String,
    /// Sampling temperature.  Default conservative — these rewrites
    /// should be deterministic-ish.
    pub temperature: f64,
    /// Kumiho client used to publish the new revision.  Shared with the
    /// effectiveness refresh task so a single daemon talks to one
    /// Kumiho process.
    pub kumiho_client: Arc<KumihoClient>,
    /// Memory project name from `config.kumiho.memory_project`.  Used
    /// by `publish_skill_revision` to verify the manifest's kref is
    /// addressed in the right project before creating a revision.
    pub memory_project: String,
}

/// Default temperature for skill rewrite LLM calls.  Low so the proposed
/// content stays close to the original structure rather than wandering.
pub const DEFAULT_REWRITE_TEMPERATURE: f64 = 0.3;

/// Outcome of [`attempt_skill_improvement`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SkillImprovementOutcome {
    /// Slug of the improved skill.
    pub slug: String,
    /// Kref of the freshly published revision.  Future-proofing for
    /// step 6f's per-revision outcome scoring.
    pub revision_kref: String,
    /// Relative `content_file` path SKILL.toml now points at.
    pub content_file: String,
}

/// Attempt to improve a single regressed skill.
///
/// Returns:
/// - `Ok(Some(outcome))` — improvement was generated, validated,
///   written, and published as a fresh Kumiho revision.  `improver`'s
///   cooldown is now armed for this skill.
/// - `Ok(None)` — skipped (cooldown not expired, manifest missing,
///   `content_file` unset, current content unreadable, LLM response
///   unparseable).  No state was changed.
/// - `Err(_)` — fatal error (filesystem, network, validation).  Caller
///   should log and continue with the next candidate.
pub async fn attempt_skill_improvement(
    ctx: &AutoImproveContext,
    candidate: &SkillImprovementCandidate,
    improver: &mut SkillImprover,
) -> Result<Option<SkillImprovementOutcome>> {
    if !improver.should_improve_skill(&candidate.skill_name) {
        return Ok(None);
    }

    let skill_dir = ctx.workspace_dir.join("skills").join(&candidate.skill_name);
    let manifest_path = skill_dir.join("SKILL.toml");

    // Manifest missing or unreadable → skip silently.  The cache may be
    // tracking outcomes for a skill that lives somewhere else; we
    // can't safely rewrite it.
    let manifest_text = match tokio::fs::read_to_string(&manifest_path).await {
        Ok(s) => s,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            tracing::debug!(
                skill = %candidate.skill_name,
                path = %manifest_path.display(),
                "auto-improve: SKILL.toml not found on disk; skipping",
            );
            return Ok(None);
        }
        Err(e) => {
            return Err(anyhow!(e).context(format!(
                "auto-improve: failed to read {}",
                manifest_path.display()
            )));
        }
    };

    let manifest: SkillManifest = toml::from_str(&manifest_text)
        .with_context(|| format!("auto-improve: parsing {}", manifest_path.display()))?;
    let Some(content_rel) = manifest.skill.content_file.clone() else {
        tracing::debug!(
            skill = %candidate.skill_name,
            "auto-improve: skill has no content_file; daemon-startup will migrate it on next run",
        );
        return Ok(None);
    };

    let content_path = skill_dir.join(&content_rel);
    let current_content = match tokio::fs::read_to_string(&content_path).await {
        Ok(s) => s,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            tracing::debug!(
                skill = %candidate.skill_name,
                path = %content_path.display(),
                "auto-improve: content_file not found on disk; skipping",
            );
            return Ok(None);
        }
        Err(e) => {
            return Err(anyhow!(e).context(format!(
                "auto-improve: failed to read {}",
                content_path.display()
            )));
        }
    };

    let prompt = build_improvement_prompt(&current_content, candidate);

    let messages = [
        ChatMessage::system(
            "You revise the markdown prompts that guide an agent through a skill so they avoid the failure patterns the user reports. \
             Return ONLY the complete revised markdown body inside a ```markdown ... ``` fenced code block. \
             Preserve the original structure and headings; tighten or expand sections as needed. \
             Do not include explanation outside the fence.",
        ),
        ChatMessage::user(prompt),
    ];

    let response = ctx
        .provider
        .chat(
            ChatRequest {
                messages: &messages,
                tools: None,
            },
            &ctx.model,
            ctx.temperature,
        )
        .await
        .context("auto-improve: LLM chat call failed")?;

    let new_content = match extract_markdown_fence(response.text_or_empty()) {
        Some(s) => s,
        None => {
            tracing::warn!(
                skill = %candidate.skill_name,
                rate = candidate.rate,
                "auto-improve: LLM response missing ```markdown fence; skipping",
            );
            return Ok(None);
        }
    };

    let reason = format!(
        "auto-improve: rolling success {:.0}% over last {} outcomes",
        candidate.rate * 100.0,
        candidate.total
    );

    // Write the new content file (cooldown-aware).  Returns None when
    // the cooldown is still active — improver.should_improve_skill
    // already filtered that case, but the inner check is a defence in
    // depth in case a long LLM call raced past it.
    let new_file = match improver
        .improve_skill(&candidate.skill_name, &new_content, &reason)
        .await?
    {
        Some(p) => p,
        None => return Ok(None),
    };

    // Publish through Kumiho: create_revision + create_artifact +
    // tag_revision("published") + sync_published_content_path.
    let published = publish_skill_revision(
        &skill_dir,
        &new_file,
        &reason,
        &ctx.kumiho_client,
        &ctx.memory_project,
    )
    .await
    .with_context(|| {
        format!(
            "auto-improve: publish_skill_revision for {}",
            candidate.skill_name
        )
    })?;

    Ok(Some(SkillImprovementOutcome {
        slug: candidate.skill_name.clone(),
        revision_kref: published.revision_kref,
        content_file: published.new_content_file,
    }))
}

/// Build the user-side prompt content.  Pure function — exposed so tests
/// can assert it includes what we need without spinning up an LLM.
pub fn build_improvement_prompt(
    current_skill_content: &str,
    candidate: &SkillImprovementCandidate,
) -> String {
    format!(
        "The skill below has regressed.  Recent rolling success rate is \
         **{rate_pct:.0}%** over **{total}** outcomes — well below our \
         {threshold_pct:.0}% threshold for healthy skills.\n\n\
         Please propose an improved markdown body that addresses common \
         failure modes.  Focus on:\n\
         - clearer step-by-step instructions,\n\
         - explicit handling of edge cases the original may have missed,\n\
         - safer defaults and tighter guard rails,\n\
         - preserving headings and overall structure so the next agent \
         can find the same sections.\n\n\
         Return ONLY the complete revised markdown content inside a \
         ```markdown fenced block — no commentary outside the fence.\n\n\
         ## Current skill content\n\n\
         ```markdown\n\
         {current}\n\
         ```\n",
        rate_pct = candidate.rate * 100.0,
        total = candidate.total,
        threshold_pct = crate::skills::effectiveness_cache::DEFAULT_IMPROVEMENT_THRESHOLD * 100.0,
        current = current_skill_content.trim_end(),
    )
}

/// Extract the contents of the first ```markdown ... ``` fenced code
/// block.  Mirrors the toml-fence extractor in earlier steps, but
/// requires the explicit `markdown` language tag so we don't pick up
/// an unrelated block.
///
/// Returns `None` if no markdown fence is found.  Returns the contents
/// trimmed of leading newlines and trailing whitespace.
pub fn extract_markdown_fence(text: &str) -> Option<String> {
    extract_fenced_block(text, "markdown")
}

fn extract_fenced_block(text: &str, lang: &str) -> Option<String> {
    let opener = format!("```{lang}");
    let start = text.find(&opener)?;
    let after_open = &text[start + opener.len()..];

    let body_start = after_open.find('\n').map(|i| i + 1).unwrap_or(0);
    let body = &after_open[body_start..];

    let close = body.find("\n```").or_else(|| body.find("```"))?;
    let inner = &body[..close];

    let trimmed = inner.trim_matches(|c: char| c == '\n' || c == '\r');
    let final_trim = trimmed.trim_end();
    if final_trim.is_empty() {
        None
    } else {
        Some(final_trim.to_string())
    }
}

/// Validate that a workspace directory is shaped the way `improve_skill`
/// expects (has `skills/<slug>/SKILL.toml`).  Used by the daemon hook
/// before it bothers calling the LLM, so a misconfigured workspace fails
/// fast with a clear error.
pub fn skill_toml_path(workspace_dir: &Path, skill_name: &str) -> std::path::PathBuf {
    workspace_dir
        .join("skills")
        .join(skill_name)
        .join("SKILL.toml")
}

/// Convenience guard for callers that want to bail before any I/O when
/// the skill isn't on disk.  Returns true when `SKILL.toml` exists.
pub async fn skill_is_writable(workspace_dir: &Path, skill_name: &str) -> bool {
    tokio::fs::try_exists(skill_toml_path(workspace_dir, skill_name))
        .await
        .unwrap_or(false)
}

// ---------------------------------------------------------------------------
// Tests — focus on the pure helpers (extract_markdown_fence + build_improvement_prompt).
// The end-to-end async path is exercised by the daemon integration.
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn cand(name: &str, rate: f64, total: u32) -> SkillImprovementCandidate {
        SkillImprovementCandidate {
            skill_name: name.to_string(),
            rate,
            total,
        }
    }

    // ── extract_markdown_fence ─────────────────────────────────────

    #[test]
    fn extract_markdown_fence_basic() {
        let text = "Here is the rewrite:\n\
                    ```markdown\n\
                    # Heading\n\
                    Body text.\n\
                    ```\n";
        let got = extract_markdown_fence(text).expect("fence present");
        assert!(got.contains("# Heading"));
        assert!(got.contains("Body text."));
    }

    #[test]
    fn extract_markdown_fence_handles_trailing_whitespace_on_opener() {
        let text = "```markdown   \n\
                    body\n\
                    ```";
        let got = extract_markdown_fence(text).expect("fence present");
        assert_eq!(got, "body");
    }

    #[test]
    fn extract_markdown_fence_returns_none_when_missing() {
        assert!(extract_markdown_fence("no fence here").is_none());
        // Different language tag — must not match.
        assert!(extract_markdown_fence("```toml\nx = 1\n```").is_none());
        assert!(extract_markdown_fence("```python\nprint('x')\n```").is_none());
    }

    #[test]
    fn extract_markdown_fence_returns_none_when_empty_body() {
        let text = "```markdown\n```";
        assert!(extract_markdown_fence(text).is_none());
    }

    #[test]
    fn extract_markdown_fence_first_block_only() {
        let text = "```markdown\n\
                    first\n\
                    ```\n\
                    \n\
                    ```markdown\n\
                    second\n\
                    ```";
        let got = extract_markdown_fence(text).expect("fence present");
        assert!(got.contains("first"));
        assert!(!got.contains("second"));
    }

    #[test]
    fn extract_markdown_fence_strips_leading_newlines() {
        let text = "```markdown\n\n\nbody\n```";
        let got = extract_markdown_fence(text).expect("fence present");
        assert!(got.starts_with("body"));
    }

    // ── build_improvement_prompt ───────────────────────────────────

    #[test]
    fn build_improvement_prompt_includes_stats() {
        let current = "# my-skill\n\nDo the thing.\n";
        let prompt = build_improvement_prompt(current, &cand("x", 0.25, 40));
        assert!(prompt.contains("**25%**"), "rate not in prompt: {prompt}");
        assert!(prompt.contains("**40** outcomes"));
        assert!(prompt.contains("40%"));
    }

    #[test]
    fn build_improvement_prompt_includes_current_skill() {
        let current = "# sentinel-skill\n\nVersion 0.4.2 instructions.\n";
        let prompt = build_improvement_prompt(current, &cand("sentinel-skill", 0.1, 20));
        assert!(prompt.contains("sentinel-skill"));
        assert!(prompt.contains("0.4.2 instructions"));
        // Wrapped in a ```markdown fence so the LLM sees a clean block.
        assert!(prompt.contains("```markdown"));
    }

    #[test]
    fn build_improvement_prompt_keeps_response_format_strict() {
        let prompt = build_improvement_prompt("# x\n", &cand("x", 0.2, 15));
        assert!(prompt.contains("Return ONLY"));
        assert!(prompt.contains("markdown fenced block"));
    }

    // ── skill_toml_path ────────────────────────────────────────────

    #[test]
    fn skill_toml_path_composes_correctly() {
        let p = skill_toml_path(Path::new("/tmp/ws"), "my-skill");
        assert!(p.ends_with("skills/my-skill/SKILL.toml"));
    }
}
