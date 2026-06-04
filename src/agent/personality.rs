//! Personality system — loads workspace identity files (SOUL.md, IDENTITY.md,
//! USER.md) and injects them into the system prompt pipeline.
//!
//! Ported from RustyClaw `src/agent/personality.rs`.  The loader reads markdown
//! files from the workspace root, validates size limits, and produces a
//! [`PersonalityProfile`] that the prompt builder can render.
//!
//! Both daemon and channel prompt-builder paths share this single loader; the
//! channel mode supplies a denylist (`HEARTBEAT.md`) via
//! [`PersonalityLoadOptions`]. The `conditional` list slot is currently empty
//! after the audit-row-3 deletion of `BOOTSTRAP.md`.

use std::fmt::Write;
use std::path::{Path, PathBuf};

/// Default per-file character cap before truncation.
pub const MAX_FILE_CHARS: usize = 20_000;

/// Canonical, well-known personality files loaded from the workspace root.
/// This is the **single source of truth** for the daemon and channel prompt
/// builders; channel-specific behavior (HEARTBEAT.md exclusion) is expressed
/// via [`PersonalityLoadOptions`] filters rather than a parallel list.
///
/// `BOOTSTRAP.md` was removed per audit row 3: the file's "first-run ritual"
/// semantics were brittle (auto-loaded as runtime authority, then
/// "self-deleting" prose telling the agent to delete it once it knew the
/// user). The first-turn responsibilities now live in the runtime's Kumiho
/// bootstrap prompt, not as a workspace file.
pub const PERSONALITY_FILES: &[&str] = &[
    "SOUL.md",
    "IDENTITY.md",
    "USER.md",
    "AGENTS.md",
    "TOOLS.md",
    "HEARTBEAT.md",
    "MEMORY.md",
];

/// A single personality file loaded from the workspace.
#[derive(Debug, Clone)]
pub struct PersonalityFile {
    /// Filename (e.g. `SOUL.md`).
    pub name: String,
    /// Raw content (possibly truncated).
    pub content: String,
    /// Whether the content was truncated due to size limits.
    pub truncated: bool,
    /// Per-file character cap used when this entry was loaded.  Used by
    /// rendering to emit the correct truncation marker text.
    pub max_chars_used: usize,
    /// Full path on disk.
    pub path: PathBuf,
}

/// Aggregated personality profile loaded from a workspace.
///
/// Three disjoint vectors:
/// - [`Self::files`] — files read successfully with non-empty content.
/// - [`Self::missing`] — names listed in the canonical/`files` input but not
///   found on disk (`std::fs::read_to_string` returned `Err`).  Channel-mode
///   rendering surfaces these as `[File not found: X]` markers via
///   [`Self::render_with_missing_markers`].
/// - [`Self::empty`] — files present on disk but with empty/whitespace-only
///   content; both rendering paths skip these silently to mirror the
///   historical channel behavior at `inject_workspace_file`.
#[derive(Debug, Clone, Default)]
pub struct PersonalityProfile {
    pub files: Vec<PersonalityFile>,
    pub missing: Vec<String>,
    pub empty: Vec<String>,
}

/// Options for [`load_personality_with_options`] — the unified loader used by
/// both the daemon and channel prompt-builder paths.
#[derive(Debug, Clone, Copy)]
pub struct PersonalityLoadOptions<'a> {
    /// Canonical file list to iterate.  Defaults to [`PERSONALITY_FILES`].
    pub files: &'a [&'a str],
    /// Files to skip entirely — no read attempt, no missing marker.
    /// Channel mode passes `["HEARTBEAT.md"]` here per audit row 7 contract.
    pub exclude: &'a [&'a str],
    /// Files that render only when present on disk and never produce a
    /// missing-file marker. Currently empty by default; kept as a hook for
    /// future opt-in workspace files. (`BOOTSTRAP.md` previously used this
    /// slot before its audit-row-3 deletion.)
    pub conditional: &'a [&'a str],
    /// Per-file character cap.  Zero means "use [`MAX_FILE_CHARS`]".
    pub max_chars: usize,
}

impl Default for PersonalityLoadOptions<'_> {
    fn default() -> Self {
        Self {
            files: PERSONALITY_FILES,
            exclude: &[],
            conditional: &[],
            max_chars: MAX_FILE_CHARS,
        }
    }
}

impl PersonalityProfile {
    /// Returns the content of a specific file by name, if loaded.
    pub fn get(&self, name: &str) -> Option<&str> {
        self.files
            .iter()
            .find(|f| f.name == name)
            .map(|f| f.content.as_str())
    }

    /// Returns `true` if no personality files were loaded.
    pub fn is_empty(&self) -> bool {
        self.files.is_empty()
    }

    /// Render loaded personality files into a prompt fragment.
    /// Missing-on-disk files are *not* surfaced — daemon-mode behavior.
    pub fn render(&self) -> String {
        let mut out = String::new();
        for file in &self.files {
            render_file(&mut out, file);
        }
        out
    }

    /// Channel-mode rendering: walks `canonical_order` and interleaves loaded
    /// files with `[File not found: X]` markers, in the same sequence the
    /// loader iterated.  Files in `self.empty` and files filtered by the
    /// `exclude` / `conditional` options are silently skipped (preserves the
    /// historical channel behavior at `inject_workspace_file`).
    pub fn render_with_missing_markers(&self, canonical_order: &[&str]) -> String {
        let mut out = String::new();
        for &name in canonical_order {
            if let Some(file) = self.files.iter().find(|f| f.name == name) {
                render_file(&mut out, file);
            } else if self.missing.iter().any(|m| m == name) {
                let _ = writeln!(out, "### {name}\n\n[File not found: {name}]\n");
            }
            // else: file was excluded, conditional+missing, or empty → skip.
        }
        out
    }
}

fn render_file(out: &mut String, file: &PersonalityFile) {
    let _ = writeln!(out, "### {}\n", file.name);
    out.push_str(&file.content);
    if file.truncated {
        let _ = writeln!(
            out,
            "\n\n[... truncated at {} chars — use `read` for full file]\n",
            file.max_chars_used
        );
    } else {
        out.push_str("\n\n");
    }
}

/// Load the canonical personality file set from a workspace directory.
///
/// Each well-known file is read and validated.  Missing files are recorded
/// in [`PersonalityProfile::missing`]; empty files are recorded in
/// [`PersonalityProfile::empty`] (silently rendered as nothing in either
/// rendering path).
pub fn load_personality(workspace_dir: &Path) -> PersonalityProfile {
    load_personality_with_options(workspace_dir, &PersonalityLoadOptions::default())
}

/// Load a specific set of personality files from a workspace directory.
/// Thin shim over [`load_personality_with_options`] for callers that just
/// want an explicit list with default cap and no filters.
pub fn load_personality_files(workspace_dir: &Path, filenames: &[&str]) -> PersonalityProfile {
    load_personality_with_options(
        workspace_dir,
        &PersonalityLoadOptions {
            files: filenames,
            ..PersonalityLoadOptions::default()
        },
    )
}

/// Unified loader.  Both the daemon and channel prompt-builder paths call
/// this with mode-specific [`PersonalityLoadOptions`] — the loader code path
/// itself is identical.
pub fn load_personality_with_options(
    workspace_dir: &Path,
    opts: &PersonalityLoadOptions<'_>,
) -> PersonalityProfile {
    let max_chars = if opts.max_chars == 0 {
        MAX_FILE_CHARS
    } else {
        opts.max_chars
    };
    let mut profile = PersonalityProfile::default();

    for &filename in opts.files {
        if opts.exclude.iter().any(|e| *e == filename) {
            continue;
        }
        let conditional = opts.conditional.iter().any(|c| *c == filename);
        let path = workspace_dir.join(filename);
        match std::fs::read_to_string(&path) {
            Ok(raw) => {
                let trimmed = raw.trim();
                if trimmed.is_empty() {
                    profile.empty.push(filename.to_string());
                    continue;
                }
                let (content, truncated) = truncate_content(trimmed, max_chars);
                profile.files.push(PersonalityFile {
                    name: filename.to_string(),
                    content,
                    truncated,
                    max_chars_used: max_chars,
                    path,
                });
            }
            Err(_) => {
                if !conditional {
                    profile.missing.push(filename.to_string());
                }
                // Conditional files that are absent on disk leave no trace —
                // no missing entry, no marker.  Currently no canonical files
                // use this slot (BOOTSTRAP.md was deleted in audit row 3);
                // kept as a hook for future opt-in workspace files.
            }
        }
    }

    profile
}

/// Truncate content to `max_chars` if necessary.  Char-boundary safe.
fn truncate_content(content: &str, max_chars: usize) -> (String, bool) {
    if content.chars().count() <= max_chars {
        return (content.to_string(), false);
    }
    let truncated = content
        .char_indices()
        .nth(max_chars)
        .map(|(idx, _)| &content[..idx])
        .unwrap_or(content);
    (truncated.to_string(), true)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn setup_workspace(files: &[(&str, &str)]) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("revka_personality_test_{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        for (name, content) in files {
            std::fs::write(dir.join(name), content).unwrap();
        }
        dir
    }

    #[test]
    fn load_personality_reads_existing_files() {
        let ws = setup_workspace(&[
            ("SOUL.md", "I am a helpful assistant."),
            ("IDENTITY.md", "Name: Nova"),
        ]);

        let profile = load_personality(&ws);
        assert_eq!(profile.files.len(), 2);
        assert_eq!(profile.get("SOUL.md").unwrap(), "I am a helpful assistant.");
        assert_eq!(profile.get("IDENTITY.md").unwrap(), "Name: Nova");
        assert!(!profile.is_empty());

        let _ = std::fs::remove_dir_all(ws);
    }

    #[test]
    fn load_personality_records_missing_files() {
        let ws = setup_workspace(&[("SOUL.md", "soul content")]);

        let profile = load_personality(&ws);
        assert_eq!(profile.files.len(), 1);
        assert!(profile.missing.contains(&"IDENTITY.md".to_string()));
        assert!(profile.missing.contains(&"USER.md".to_string()));

        let _ = std::fs::remove_dir_all(ws);
    }

    #[test]
    fn load_personality_separates_empty_from_missing() {
        // SOUL.md is present-but-empty; IDENTITY.md is missing-on-disk.
        let ws = setup_workspace(&[("SOUL.md", "   \n  ")]);

        let profile = load_personality(&ws);
        assert!(profile.is_empty(), "no loaded files");
        assert!(
            profile.empty.contains(&"SOUL.md".to_string()),
            "SOUL.md should be classified as empty (present but blank)"
        );
        assert!(
            !profile.missing.contains(&"SOUL.md".to_string()),
            "SOUL.md must NOT appear in `missing` — that's reserved for files \
             that don't exist on disk"
        );
        assert!(
            profile.missing.contains(&"IDENTITY.md".to_string()),
            "IDENTITY.md is genuinely missing-on-disk"
        );

        let _ = std::fs::remove_dir_all(ws);
    }

    #[test]
    fn load_personality_truncates_large_files() {
        let large = "x".repeat(MAX_FILE_CHARS + 500);
        let ws = setup_workspace(&[("SOUL.md", &large)]);

        let profile = load_personality(&ws);
        let soul = profile.files.iter().find(|f| f.name == "SOUL.md").unwrap();
        assert!(soul.truncated);
        assert_eq!(soul.content.chars().count(), MAX_FILE_CHARS);
        assert_eq!(soul.max_chars_used, MAX_FILE_CHARS);

        let _ = std::fs::remove_dir_all(ws);
    }

    #[test]
    fn render_produces_markdown_sections() {
        let ws = setup_workspace(&[("SOUL.md", "Be kind."), ("IDENTITY.md", "Name: Nova")]);

        let profile = load_personality(&ws);
        let rendered = profile.render();
        assert!(rendered.contains("### SOUL.md"));
        assert!(rendered.contains("Be kind."));
        assert!(rendered.contains("### IDENTITY.md"));
        assert!(rendered.contains("Name: Nova"));

        let _ = std::fs::remove_dir_all(ws);
    }

    #[test]
    fn render_truncated_file_shows_notice() {
        let large = "y".repeat(MAX_FILE_CHARS + 100);
        let ws = setup_workspace(&[("SOUL.md", &large)]);

        let profile = load_personality(&ws);
        let rendered = profile.render();
        assert!(rendered.contains("[... truncated at"));

        let _ = std::fs::remove_dir_all(ws);
    }

    #[test]
    fn render_truncation_marker_uses_per_file_cap() {
        let large = "z".repeat(7_000);
        let ws = setup_workspace(&[("SOUL.md", &large)]);
        let profile = load_personality_with_options(
            &ws,
            &PersonalityLoadOptions {
                files: &["SOUL.md"],
                max_chars: 6_000,
                ..PersonalityLoadOptions::default()
            },
        );
        let rendered = profile.render();
        assert!(
            rendered.contains("[... truncated at 6000 chars"),
            "truncation marker must reflect the cap actually used during load"
        );
        let _ = std::fs::remove_dir_all(ws);
    }

    #[test]
    fn get_returns_none_for_missing_file() {
        let ws = setup_workspace(&[]);
        let profile = load_personality(&ws);
        assert!(profile.get("SOUL.md").is_none());
        let _ = std::fs::remove_dir_all(ws);
    }

    #[test]
    fn load_personality_files_custom_subset() {
        let ws = setup_workspace(&[("SOUL.md", "soul"), ("USER.md", "user")]);

        let profile = load_personality_files(&ws, &["SOUL.md", "USER.md"]);
        assert_eq!(profile.files.len(), 2);
        assert!(profile.missing.is_empty());

        let _ = std::fs::remove_dir_all(ws);
    }

    #[test]
    fn empty_workspace_yields_empty_profile() {
        let ws = setup_workspace(&[]);
        let profile = load_personality(&ws);
        assert!(profile.is_empty());
        assert!(!profile.missing.is_empty());
        let _ = std::fs::remove_dir_all(ws);
    }

    #[test]
    fn load_personality_with_options_excludes_filter_skips_silently() {
        let ws = setup_workspace(&[("SOUL.md", "soul"), ("HEARTBEAT.md", "ignore me")]);
        let profile = load_personality_with_options(
            &ws,
            &PersonalityLoadOptions {
                files: PERSONALITY_FILES,
                exclude: &["HEARTBEAT.md"],
                conditional: &[],
                max_chars: 0,
            },
        );
        assert!(
            !profile.files.iter().any(|f| f.name == "HEARTBEAT.md"),
            "excluded file must not appear in profile.files"
        );
        assert!(
            !profile.missing.contains(&"HEARTBEAT.md".to_string()),
            "excluded file must not appear in profile.missing"
        );
        let _ = std::fs::remove_dir_all(ws);
    }

    #[test]
    fn load_personality_with_options_conditional_missing_is_silent() {
        // Verifies the `conditional` slot still works generically even though
        // BOOTSTRAP.md (its original sole user) was deleted in audit row 3.
        // We pass a custom `files` list with a synthetic conditional entry so
        // the loader iterates it; absent-on-disk + listed-as-conditional
        // should leave no trace.
        let ws = setup_workspace(&[("SOUL.md", "soul")]);
        let custom_files: &[&str] = &["SOUL.md", "OPTIONAL.md", "IDENTITY.md"];
        let profile = load_personality_with_options(
            &ws,
            &PersonalityLoadOptions {
                files: custom_files,
                exclude: &[],
                conditional: &["OPTIONAL.md"],
                max_chars: 0,
            },
        );
        assert!(
            !profile.missing.contains(&"OPTIONAL.md".to_string()),
            "conditional+missing must not record a missing marker"
        );
        // Non-conditional missing files are still tracked.
        assert!(profile.missing.contains(&"IDENTITY.md".to_string()));
        let _ = std::fs::remove_dir_all(ws);
    }

    #[test]
    fn render_with_missing_markers_interleaves_in_canonical_order() {
        let ws = setup_workspace(&[("SOUL.md", "soul"), ("USER.md", "user")]);
        let profile = load_personality_with_options(
            &ws,
            &PersonalityLoadOptions {
                files: PERSONALITY_FILES,
                exclude: &["HEARTBEAT.md"],
                conditional: &[],
                max_chars: 0,
            },
        );
        let rendered = profile.render_with_missing_markers(PERSONALITY_FILES);

        // Canonical order: SOUL → IDENTITY → USER → AGENTS → TOOLS → (HEARTBEAT skipped) → MEMORY
        let soul_idx = rendered.find("### SOUL.md").expect("SOUL header");
        let id_idx = rendered.find("### IDENTITY.md").expect("IDENTITY marker");
        let user_idx = rendered.find("### USER.md").expect("USER header");
        let agents_idx = rendered.find("### AGENTS.md").expect("AGENTS marker");
        let memory_idx = rendered.find("### MEMORY.md").expect("MEMORY marker");

        assert!(soul_idx < id_idx);
        assert!(id_idx < user_idx);
        assert!(user_idx < agents_idx);
        assert!(agents_idx < memory_idx);

        // Excluded file never appears.
        assert!(
            !rendered.contains("HEARTBEAT.md"),
            "excluded file must not appear in rendered output"
        );
        // BOOTSTRAP.md is gone (audit row 3) — no longer in PERSONALITY_FILES,
        // so the loader doesn't process it and it never appears anywhere.
        assert!(
            !rendered.contains("BOOTSTRAP.md"),
            "deleted file must never appear in rendered output"
        );
        // Missing-on-disk files surface markers.
        assert!(rendered.contains("[File not found: IDENTITY.md]"));
        assert!(rendered.contains("[File not found: AGENTS.md]"));
        assert!(rendered.contains("[File not found: MEMORY.md]"));

        let _ = std::fs::remove_dir_all(ws);
    }

    #[test]
    fn render_skips_empty_files_silently_in_both_modes() {
        let ws = setup_workspace(&[("SOUL.md", "soul"), ("TOOLS.md", "")]);
        let profile = load_personality_with_options(
            &ws,
            &PersonalityLoadOptions {
                files: &["SOUL.md", "TOOLS.md"],
                ..PersonalityLoadOptions::default()
            },
        );
        // Empty file goes to `empty`, NOT `missing`.
        assert!(profile.empty.contains(&"TOOLS.md".to_string()));
        assert!(!profile.missing.contains(&"TOOLS.md".to_string()));

        let daemon = profile.render();
        let channel = profile.render_with_missing_markers(&["SOUL.md", "TOOLS.md"]);
        assert!(!daemon.contains("TOOLS.md"));
        assert!(!channel.contains("TOOLS.md"));

        let _ = std::fs::remove_dir_all(ws);
    }
}
