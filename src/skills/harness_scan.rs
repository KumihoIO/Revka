//! Scan existing AI-agent "harnesses" on disk and turn each discovered
//! skill/instruction into a [`DiscoveredHarnessSkill`] descriptor.
//!
//! This module is **read-only and network-free**: it never mutates, moves, or
//! copies the user's harness files.  Each descriptor records the *original*
//! absolute `source_path`; [`crate::skills::harness_import`] later registers a
//! Kumiho artifact pointing at that path in place.
//!
//! Supported harnesses (all read where they sit):
//! - **Claude Code** — `.claude/skills/*/SKILL.md`, `.claude/commands/**/*.md`,
//!   `.claude/agents/*.md` (discrete skills), `CLAUDE.md` (instructions).
//! - **Codex** — `AGENTS.md` (instructions), global `~/.codex/AGENTS.md`.
//! - **Gemini** — `GEMINI.md`, global `~/.gemini/*.md` (instructions).
//! - **Cursor** — `.cursor/rules/*.mdc` (skills), `.cursorrules` (instructions).
//! - **Windsurf** — `.windsurfrules` (instructions), `.windsurf/rules/*.md` (skills).
//! - **Cline/Roo** — `.clinerules` (file → instructions; dir → per-`*.md` skills).
//! - **Copilot** — `.github/copilot-instructions.md` (instructions).
//! - **Aider** — `CONVENTIONS.md` (instructions).
//!
//! A *skill* is discrete and triggerable; an *instruction* is an always-on
//! persona/conventions doc.  Both register as Kumiho skill items, but
//! instruction docs get a descriptive, project-scoped name + description so the
//! Operator only loads them when relevant.

use crate::gateway::kumiho_client::slugify;
use std::collections::hash_map::DefaultHasher;
use std::collections::{BTreeMap, HashMap, HashSet};
use std::hash::{Hash, Hasher};
use std::path::{Path, PathBuf};

/// Maximum length stored for a skill description (keeps Kumiho metadata sane).
const MAX_DESCRIPTION_LEN: usize = 500;

/// Directory names never descended during the project-tree walk (dependency,
/// build, and VCS trees that cannot hold a user-authored harness).
const PRUNED_DIRS: &[&str] = &[
    "node_modules",
    "target",
    ".git",
    "dist",
    "build",
    "out",
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".gradle",
    ".idea",
    "vendor",
    ".terraform",
    "coverage",
    ".cache",
    ".turbo",
    ".parcel-cache",
    "obj",
    "Pods",
    ".cargo",
    ".rustup",
    "site-packages",
];

/// Dotdirs that ARE harness roots — handled by the per-directory collectors
/// rather than descended into generically.
const HARNESS_DOTDIRS: &[&str] = &[".claude", ".cursor", ".windsurf", ".github"];

/// Files whose presence marks a directory as a project root, gating bare
/// instruction docs (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, `CONVENTIONS.md`)
/// so a monorepo doesn't register every package's copy.
const REPO_MARKERS: &[&str] = &[
    ".git",
    "Cargo.toml",
    "package.json",
    "pyproject.toml",
    "go.mod",
];

/// Which agent tool a discovered harness came from.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum HarnessTool {
    Claude,
    Codex,
    Gemini,
    Cursor,
    Windsurf,
    Cline,
    Copilot,
    Aider,
}

impl HarnessTool {
    pub fn as_str(self) -> &'static str {
        match self {
            HarnessTool::Claude => "claude",
            HarnessTool::Codex => "codex",
            HarnessTool::Gemini => "gemini",
            HarnessTool::Cursor => "cursor",
            HarnessTool::Windsurf => "windsurf",
            HarnessTool::Cline => "cline",
            HarnessTool::Copilot => "copilot",
            HarnessTool::Aider => "aider",
        }
    }
}

/// Discrete, triggerable skill vs always-on instruction/persona doc.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HarnessKind {
    Skill,
    Instruction,
}

impl HarnessKind {
    pub fn as_str(self) -> &'static str {
        match self {
            HarnessKind::Skill => "skill",
            HarnessKind::Instruction => "instruction",
        }
    }
}

/// A harness artifact discovered on disk, ready to register with Kumiho.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DiscoveredHarnessSkill {
    /// Human-readable display name.
    pub name: String,
    /// kref-safe, namespaced item name (bare — the kref adds the `.skill` suffix).
    pub slug: String,
    pub description: String,
    pub harness_tool: HarnessTool,
    pub harness_kind: HarnessKind,
    /// Absolute, canonicalised path — read in place, never moved.
    pub source_path: PathBuf,
    /// Change-detection hash of the file body (not cryptographic).
    pub content_hash: String,
    /// Cursor `.mdc` scoping globs, if any.
    pub globs: Option<String>,
}

/// Tunable inputs for [`scan_harnesses`].
#[derive(Debug, Clone)]
pub struct HarnessScanOptions {
    /// Project directories whose trees are walked (bounded depth).
    pub project_roots: Vec<PathBuf>,
    /// Global agent config dirs (`~/.claude`, `~/.codex`, `~/.gemini`).
    pub global_roots: Vec<PathBuf>,
    pub max_depth: usize,
    pub max_file_bytes: u64,
}

impl HarnessScanOptions {
    /// Build options from an optional explicit project path plus, when
    /// `include_global` is set, the standard global agent config dirs.
    ///
    /// With no explicit path the current working directory is scanned.
    pub fn with_defaults(explicit_path: Option<PathBuf>, include_global: bool) -> Self {
        let project_roots = match explicit_path {
            Some(p) => vec![p],
            None => std::env::current_dir().map(|d| vec![d]).unwrap_or_default(),
        };
        let global_roots = if include_global {
            default_global_roots()
        } else {
            Vec::new()
        };
        Self {
            project_roots,
            global_roots,
            max_depth: 6,
            max_file_bytes: 512 * 1024,
        }
    }
}

/// `~/.claude`, `~/.codex`, `~/.gemini` if a home directory is resolvable.
pub fn default_global_roots() -> Vec<PathBuf> {
    let Some(home) = directories::UserDirs::new().map(|u| u.home_dir().to_path_buf()) else {
        return Vec::new();
    };
    [".claude", ".codex", ".gemini"]
        .iter()
        .map(|d| home.join(d))
        .filter(|p| p.exists())
        .collect()
}

/// What a scan turned up — discovered skills plus accounting for reporting.
#[derive(Debug, Clone, Default)]
pub struct HarnessScanReport {
    pub discovered: Vec<DiscoveredHarnessSkill>,
    pub per_tool: BTreeMap<&'static str, usize>,
    pub skills: usize,
    pub instructions: usize,
    pub skipped: Vec<(PathBuf, String)>,
    pub duplicates: usize,
    pub pruned_dirs: usize,
}

/// Scan the configured roots and return everything discovered.
pub fn scan_harnesses(opts: &HarnessScanOptions) -> HarnessScanReport {
    let mut report = HarnessScanReport::default();
    let mut seen: HashSet<PathBuf> = HashSet::new();
    let mut slugs: HashMap<String, PathBuf> = HashMap::new();
    let mut ctx = ScanCtx {
        opts,
        report: &mut report,
        seen: &mut seen,
        slugs: &mut slugs,
    };

    for root in &opts.project_roots {
        let is_root = true;
        ctx.walk_dir(root, 0, is_root);
    }
    for groot in &opts.global_roots {
        ctx.scan_global_root(groot);
    }

    ctx.report.discovered.sort_by(|a, b| a.slug.cmp(&b.slug));
    report
}

/// Mutable state threaded through the recursive walk.
struct ScanCtx<'a> {
    opts: &'a HarnessScanOptions,
    report: &'a mut HarnessScanReport,
    seen: &'a mut HashSet<PathBuf>,
    slugs: &'a mut HashMap<String, PathBuf>,
}

impl ScanCtx<'_> {
    /// Walk a project directory: extract harnesses here, then recurse into
    /// non-pruned, non-symlink subdirectories up to `max_depth`.
    fn walk_dir(&mut self, dir: &Path, depth: usize, is_root: bool) {
        self.collect_dir(dir, is_root);
        if depth >= self.opts.max_depth {
            return;
        }
        let Ok(entries) = std::fs::read_dir(dir) else {
            return;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let Ok(ft) = entry.file_type() else { continue };
            if !ft.is_dir() || ft.is_symlink() {
                continue;
            }
            let Some(name) = path.file_name().and_then(|n| n.to_str()) else {
                continue;
            };
            // Harness dotdirs are read by the collectors, not descended into;
            // other dotdirs and heavy build/dep dirs are pruned.
            if HARNESS_DOTDIRS.contains(&name) {
                continue;
            }
            if name.starts_with('.') || is_pruned_dir(name) {
                self.report.pruned_dirs += 1;
                continue;
            }
            self.walk_dir(&path, depth + 1, false);
        }
    }

    /// Extract every harness artifact rooted at `dir` (not its subdirectories).
    fn collect_dir(&mut self, dir: &Path, is_root: bool) {
        let repo = repo_name(dir);

        // Claude: project-local `.claude/` skills/commands/agents.
        let claude_dir = dir.join(".claude");
        if claude_dir.is_dir() {
            self.collect_claude_dir(&claude_dir);
        }

        // Cursor rules (skills) + .cursorrules (instruction).
        let cursor_rules = dir.join(".cursor").join("rules");
        if cursor_rules.is_dir() {
            for p in list_files_with_ext(&cursor_rules, "mdc") {
                self.push_skill(&p, HarnessTool::Cursor, HarnessKind::Skill);
            }
        }
        self.push_instruction(&dir.join(".cursorrules"), HarnessTool::Cursor, &repo);

        // Windsurf rules.
        self.push_instruction(&dir.join(".windsurfrules"), HarnessTool::Windsurf, &repo);
        let windsurf_rules = dir.join(".windsurf").join("rules");
        if windsurf_rules.is_dir() {
            for p in list_files_with_ext(&windsurf_rules, "md") {
                self.push_skill(&p, HarnessTool::Windsurf, HarnessKind::Skill);
            }
        }

        // Cline/Roo: `.clinerules` may be a file or a directory of rules.
        let clinerules = dir.join(".clinerules");
        if clinerules.is_dir() {
            for p in list_files_with_ext(&clinerules, "md") {
                self.push_skill(&p, HarnessTool::Cline, HarnessKind::Skill);
            }
        } else if clinerules.is_file() {
            self.push_instruction(&clinerules, HarnessTool::Cline, &repo);
        }

        // Copilot.
        self.push_instruction(
            &dir.join(".github").join("copilot-instructions.md"),
            HarnessTool::Copilot,
            &repo,
        );

        // Bare instruction docs — gated on a repo marker (or being a passed root)
        // so a monorepo doesn't register every nested package's copy.
        if is_root || has_repo_marker(dir) {
            self.push_instruction(&dir.join("CLAUDE.md"), HarnessTool::Claude, &repo);
            self.push_instruction(&dir.join("AGENTS.md"), HarnessTool::Codex, &repo);
            self.push_instruction(&dir.join("GEMINI.md"), HarnessTool::Gemini, &repo);
            self.push_instruction(&dir.join("CONVENTIONS.md"), HarnessTool::Aider, &repo);
        }
    }

    /// Scan a `.claude` directory (project-local or a global `~/.claude`) for
    /// skills, commands, and agents.
    fn collect_claude_dir(&mut self, claude_dir: &Path) {
        let skills = claude_dir.join("skills");
        if skills.is_dir() {
            if let Ok(entries) = std::fs::read_dir(&skills) {
                for entry in entries.flatten() {
                    let skill_md = entry.path().join("SKILL.md");
                    if skill_md.is_file() {
                        self.push_skill(&skill_md, HarnessTool::Claude, HarnessKind::Skill);
                    }
                }
            }
        }
        let commands = claude_dir.join("commands");
        if commands.is_dir() {
            for p in list_files_with_ext_recursive(&commands, "md", 4) {
                self.push_skill(&p, HarnessTool::Claude, HarnessKind::Skill);
            }
        }
        let agents = claude_dir.join("agents");
        if agents.is_dir() {
            for p in list_files_with_ext(&agents, "md") {
                self.push_skill(&p, HarnessTool::Claude, HarnessKind::Skill);
            }
        }
    }

    /// Scan a global agent config dir (`~/.claude`, `~/.codex`, `~/.gemini`).
    fn scan_global_root(&mut self, groot: &Path) {
        if !groot.is_dir() {
            return;
        }
        let name = groot.file_name().and_then(|n| n.to_str()).unwrap_or("");
        match name {
            ".claude" => {
                self.collect_claude_dir(groot);
                self.push_instruction(&groot.join("CLAUDE.md"), HarnessTool::Claude, "global");
            }
            ".codex" => {
                self.push_instruction(&groot.join("AGENTS.md"), HarnessTool::Codex, "global");
            }
            ".gemini" => {
                for p in list_files_with_ext(groot, "md") {
                    self.push_instruction(&p, HarnessTool::Gemini, "global");
                }
            }
            _ => {}
        }
    }

    /// Register an instruction doc if it exists.  `repo`-scoped name +
    /// discovery-steering description; the body stays on disk.
    fn push_instruction(&mut self, path: &Path, tool: HarnessTool, repo: &str) {
        if !path.is_file() {
            return;
        }
        let name = format!("harness-conventions-{}-{}", tool.as_str(), repo);
        let description = format!(
            "Always-on {} conventions imported from project '{}'. Load only when working inside that project.",
            tool.as_str(),
            repo
        );
        self.push(
            path,
            tool,
            HarnessKind::Instruction,
            Some(name),
            Some(description),
        );
    }

    /// Register a discrete skill if the file exists, parsing name/description
    /// (and globs) from its frontmatter/body.
    fn push_skill(&mut self, path: &Path, tool: HarnessTool, kind: HarnessKind) {
        self.push(path, tool, kind, None, None);
    }

    /// Shared registration path: dedupe, size/encoding guards, parse, slug,
    /// collision-suffix, and push onto the report.
    fn push(
        &mut self,
        path: &Path,
        tool: HarnessTool,
        kind: HarnessKind,
        name_override: Option<String>,
        desc_override: Option<String>,
    ) {
        let canonical = match std::fs::canonicalize(path) {
            Ok(c) => c,
            Err(_) => return,
        };
        if self.seen.contains(&canonical) {
            self.report.duplicates += 1;
            return;
        }

        match std::fs::metadata(&canonical) {
            Ok(m) if m.len() > self.opts.max_file_bytes => {
                self.skip(&canonical, "file-too-large");
                return;
            }
            Ok(_) => {}
            Err(_) => return,
        }

        let bytes = match std::fs::read(&canonical) {
            Ok(b) => b,
            Err(_) => return,
        };
        let content = match String::from_utf8(bytes) {
            Ok(s) => s,
            Err(_) => {
                self.skip(&canonical, "non-utf8");
                return;
            }
        };
        if content.trim().is_empty() {
            self.skip(&canonical, "empty");
            return;
        }

        let parsed = parse_doc(&content);
        let display_name = name_override.unwrap_or_else(|| {
            parsed
                .name
                .clone()
                .unwrap_or_else(|| default_name_for(path))
        });
        let description = desc_override.unwrap_or_else(|| {
            cap(
                parsed
                    .description
                    .clone()
                    .unwrap_or_else(|| format!("Imported {} {}", tool.as_str(), kind.as_str())),
                MAX_DESCRIPTION_LEN,
            )
        });

        let base_slug = if display_name.starts_with("harness-conventions-") {
            slugify(&display_name)
        } else {
            slugify(&format!("harness-{}-{}", tool.as_str(), display_name))
        };
        let slug = self.dedupe_slug(base_slug, &canonical);

        self.seen.insert(canonical.clone());
        self.slugs.insert(slug.clone(), canonical.clone());
        *self.report.per_tool.entry(tool.as_str()).or_insert(0) += 1;
        match kind {
            HarnessKind::Skill => self.report.skills += 1,
            HarnessKind::Instruction => self.report.instructions += 1,
        }
        self.report.discovered.push(DiscoveredHarnessSkill {
            name: display_name,
            slug,
            description,
            harness_tool: tool,
            harness_kind: kind,
            source_path: canonical,
            content_hash: content_hash(&content),
            globs: parsed.globs,
        });
    }

    fn dedupe_slug(&self, base: String, source: &Path) -> String {
        match self.slugs.get(&base) {
            Some(existing) if existing != source => {
                format!("{base}-{}", short_hash(&source.to_string_lossy()))
            }
            _ => base,
        }
    }

    fn skip(&mut self, path: &Path, reason: &str) {
        self.report
            .skipped
            .push((path.to_path_buf(), reason.to_string()));
    }
}

// ── Parsing helpers (pure; unit-tested) ───────────────────────────────────

/// Name/description/globs extracted from a harness markdown file.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub(crate) struct ParsedDoc {
    pub name: Option<String>,
    pub description: Option<String>,
    pub globs: Option<String>,
}

/// Parse a harness markdown file: prefer YAML-frontmatter `name`/`description`
/// (reusing the skills module's lenient parser), with sensible fallbacks for
/// files that have no frontmatter (Codex AGENTS.md, operator-style `.md`).
pub(crate) fn parse_doc(content: &str) -> ParsedDoc {
    if let Some((frontmatter, body)) = super::split_skill_frontmatter(content) {
        let meta = super::parse_simple_frontmatter(&frontmatter);
        let description = meta
            .description
            .filter(|d| !d.trim().is_empty())
            .or_else(|| first_meaningful_line(&body));
        ParsedDoc {
            name: meta.name.filter(|n| !n.trim().is_empty()),
            description,
            globs: extract_globs(&frontmatter),
        }
    } else {
        ParsedDoc {
            name: None,
            description: first_meaningful_line(content),
            globs: None,
        }
    }
}

/// First non-empty line, with any leading markdown heading `#` markers stripped.
/// Covers `# Skill: github-issue` (→ "Skill: github-issue") and prose openers
/// like "Use this skill when ...".
pub(crate) fn first_meaningful_line(s: &str) -> Option<String> {
    s.lines()
        .map(str::trim)
        .find(|l| !l.is_empty())
        .map(|l| l.trim_start_matches('#').trim().to_string())
        .filter(|l| !l.is_empty())
}

/// Extract a Cursor `.mdc` `globs:` value from a frontmatter block — scalar
/// (`globs: src/**/*.ts`) or inline list (`globs: [a, b]`) → comma-joined.
pub(crate) fn extract_globs(frontmatter: &str) -> Option<String> {
    for line in frontmatter.lines() {
        let Some((key, val)) = line.split_once(':') else {
            continue;
        };
        if key.trim() != "globs" {
            continue;
        }
        let val = val.trim();
        let val = val.trim_start_matches('[').trim_end_matches(']');
        let joined = val
            .split(',')
            .map(|g| g.trim().trim_matches('"').trim_matches('\''))
            .filter(|g| !g.is_empty())
            .collect::<Vec<_>>()
            .join(",");
        return (!joined.is_empty()).then_some(joined);
    }
    None
}

/// Fall-back display name from a path: a `SKILL.md` uses its parent directory
/// name; everything else uses the file stem.
fn default_name_for(path: &Path) -> String {
    let stem = path.file_stem().and_then(|s| s.to_str()).unwrap_or("");
    if path.file_name().and_then(|n| n.to_str()) == Some("SKILL.md") {
        path.parent()
            .and_then(|p| p.file_name())
            .and_then(|n| n.to_str())
            .unwrap_or(stem)
            .to_string()
    } else {
        stem.to_string()
    }
}

fn content_hash(content: &str) -> String {
    let mut h = DefaultHasher::new();
    content.hash(&mut h);
    format!("{:016x}", h.finish())
}

fn short_hash(s: &str) -> String {
    let mut h = DefaultHasher::new();
    s.hash(&mut h);
    format!("{:016x}", h.finish())[..6].to_string()
}

fn cap(mut s: String, max: usize) -> String {
    if s.chars().count() > max {
        s = s.chars().take(max).collect();
    }
    s
}

fn is_pruned_dir(name: &str) -> bool {
    PRUNED_DIRS.iter().any(|d| d.eq_ignore_ascii_case(name))
}

fn has_repo_marker(dir: &Path) -> bool {
    REPO_MARKERS.iter().any(|m| dir.join(m).exists())
}

fn repo_name(dir: &Path) -> String {
    dir.file_name()
        .and_then(|n| n.to_str())
        .map(|n| {
            let s = slugify(n);
            if s.is_empty() {
                "project".to_string()
            } else {
                s
            }
        })
        .unwrap_or_else(|| "project".to_string())
}

/// Non-recursive list of files with the given extension in `dir`.
fn list_files_with_ext(dir: &Path, ext: &str) -> Vec<PathBuf> {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return Vec::new();
    };
    entries
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.is_file() && p.extension().and_then(|e| e.to_str()) == Some(ext))
        .collect()
}

/// Depth-bounded recursive list of files with the given extension.
fn list_files_with_ext_recursive(dir: &Path, ext: &str, max_depth: usize) -> Vec<PathBuf> {
    let mut out = Vec::new();
    collect_ext_recursive(dir, ext, 0, max_depth, &mut out);
    out
}

fn collect_ext_recursive(
    dir: &Path,
    ext: &str,
    depth: usize,
    max_depth: usize,
    out: &mut Vec<PathBuf>,
) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let Ok(ft) = entry.file_type() else { continue };
        if ft.is_symlink() {
            continue;
        }
        if ft.is_dir() {
            if depth < max_depth {
                collect_ext_recursive(&path, ext, depth + 1, max_depth, out);
            }
        } else if path.extension().and_then(|e| e.to_str()) == Some(ext) {
            out.push(path);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn write(path: &Path, content: &str) {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(path, content).unwrap();
    }

    fn scan_dir(dir: &Path) -> HarnessScanReport {
        let opts = HarnessScanOptions {
            project_roots: vec![dir.to_path_buf()],
            global_roots: vec![],
            max_depth: 6,
            max_file_bytes: 512 * 1024,
        };
        scan_harnesses(&opts)
    }

    fn find<'a>(r: &'a HarnessScanReport, slug: &str) -> Option<&'a DiscoveredHarnessSkill> {
        r.discovered.iter().find(|s| s.slug == slug)
    }

    #[test]
    fn parse_doc_reads_frontmatter() {
        let p = parse_doc("---\nname: my-skill\ndescription: Does a thing\n---\n# Body\n");
        assert_eq!(p.name.as_deref(), Some("my-skill"));
        assert_eq!(p.description.as_deref(), Some("Does a thing"));
    }

    #[test]
    fn parse_doc_no_frontmatter_uses_first_line() {
        let p = parse_doc("# Skill: github-issue\n\nbody text\n");
        assert!(p.name.is_none());
        assert_eq!(p.description.as_deref(), Some("Skill: github-issue"));
    }

    #[test]
    fn parse_doc_frontmatter_without_description_falls_back_to_body() {
        let p = parse_doc("---\nname: x\n---\nUse this skill when needed\n");
        assert_eq!(p.description.as_deref(), Some("Use this skill when needed"));
    }

    #[test]
    fn extract_globs_scalar_and_list() {
        assert_eq!(
            extract_globs("globs: src/**/*.ts").as_deref(),
            Some("src/**/*.ts")
        );
        assert_eq!(
            extract_globs("globs: [a.ts, b.ts]").as_deref(),
            Some("a.ts,b.ts")
        );
        assert!(extract_globs("name: x").is_none());
    }

    #[test]
    fn scans_claude_skill_with_frontmatter() {
        let tmp = tempfile::tempdir().unwrap();
        write(
            &tmp.path().join(".claude/skills/deployer/SKILL.md"),
            "---\nname: deployer\ndescription: Deploys the app\n---\nbody",
        );
        let r = scan_dir(tmp.path());
        let s = find(&r, "harness-claude-deployer").expect("deployer skill");
        assert_eq!(s.name, "deployer");
        assert_eq!(s.description, "Deploys the app");
        assert_eq!(s.harness_tool, HarnessTool::Claude);
        assert_eq!(s.harness_kind, HarnessKind::Skill);
    }

    #[test]
    fn skill_without_frontmatter_names_from_dir() {
        let tmp = tempfile::tempdir().unwrap();
        write(
            &tmp.path().join(".claude/skills/github-issue/SKILL.md"),
            "# Skill: github-issue\n\nDoes issues",
        );
        let r = scan_dir(tmp.path());
        assert!(find(&r, "harness-claude-github-issue").is_some());
    }

    #[test]
    fn scans_claude_commands_and_agents() {
        let tmp = tempfile::tempdir().unwrap();
        write(
            &tmp.path().join(".claude/commands/deploy.md"),
            "# Deploy\nrun it",
        );
        write(
            &tmp.path().join(".claude/agents/reviewer.md"),
            "---\nname: reviewer\ndescription: Reviews code\n---\nx",
        );
        let r = scan_dir(tmp.path());
        assert!(find(&r, "harness-claude-deploy").is_some());
        assert!(find(&r, "harness-claude-reviewer").is_some());
    }

    #[test]
    fn scans_cursor_rules_with_globs() {
        let tmp = tempfile::tempdir().unwrap();
        write(
            &tmp.path().join(".cursor/rules/style.mdc"),
            "---\ndescription: Style rules\nglobs: src/**/*.ts\n---\nbe consistent",
        );
        let r = scan_dir(tmp.path());
        let s = find(&r, "harness-cursor-style").expect("cursor style rule");
        assert_eq!(s.globs.as_deref(), Some("src/**/*.ts"));
        assert_eq!(s.harness_kind, HarnessKind::Skill);
    }

    #[test]
    fn instruction_docs_require_repo_marker() {
        // No repo marker at a nested dir → CLAUDE.md not picked up.
        let tmp = tempfile::tempdir().unwrap();
        write(&tmp.path().join("nested/CLAUDE.md"), "# conventions");
        let r = scan_dir(tmp.path());
        assert!(
            r.discovered
                .iter()
                .all(|s| s.harness_tool != HarnessTool::Claude
                    || s.harness_kind != HarnessKind::Instruction)
        );

        // Add a repo marker → it is picked up.
        write(&tmp.path().join("nested/Cargo.toml"), "[package]");
        let r2 = scan_dir(tmp.path());
        assert!(
            r2.discovered
                .iter()
                .any(|s| s.harness_kind == HarnessKind::Instruction
                    && s.harness_tool == HarnessTool::Claude)
        );
    }

    #[test]
    fn root_instruction_doc_picked_up_without_marker() {
        let tmp = tempfile::tempdir().unwrap();
        write(&tmp.path().join("AGENTS.md"), "# agents conventions");
        let r = scan_dir(tmp.path());
        let s = r
            .discovered
            .iter()
            .find(|s| s.harness_tool == HarnessTool::Codex)
            .expect("AGENTS.md at root");
        assert_eq!(s.harness_kind, HarnessKind::Instruction);
        assert!(s.slug.starts_with("harness-conventions-codex-"));
    }

    #[test]
    fn cline_file_is_instruction_dir_is_skills() {
        let tmp = tempfile::tempdir().unwrap();
        write(&tmp.path().join(".clinerules"), "be careful");
        let r = scan_dir(tmp.path());
        assert!(
            r.discovered
                .iter()
                .any(|s| s.harness_tool == HarnessTool::Cline
                    && s.harness_kind == HarnessKind::Instruction)
        );

        let tmp2 = tempfile::tempdir().unwrap();
        write(&tmp2.path().join(".clinerules/rule-a.md"), "# Rule A\nx");
        let r2 = scan_dir(tmp2.path());
        assert!(
            r2.discovered
                .iter()
                .any(|s| s.harness_tool == HarnessTool::Cline
                    && s.harness_kind == HarnessKind::Skill)
        );
    }

    #[test]
    fn prunes_node_modules() {
        let tmp = tempfile::tempdir().unwrap();
        write(
            &tmp.path()
                .join("node_modules/pkg/.claude/skills/x/SKILL.md"),
            "---\nname: x\ndescription: d\n---\nb",
        );
        let r = scan_dir(tmp.path());
        assert!(r.pruned_dirs > 0);
        assert!(find(&r, "harness-claude-x").is_none());
    }

    #[test]
    fn respects_max_depth() {
        let tmp = tempfile::tempdir().unwrap();
        // a/b/c/d/e/f/.claude (7 levels down) — beyond max_depth 2 below.
        write(
            &tmp.path().join("a/b/c/.claude/skills/deep/SKILL.md"),
            "---\nname: deep\ndescription: d\n---\nb",
        );
        let opts = HarnessScanOptions {
            project_roots: vec![tmp.path().to_path_buf()],
            global_roots: vec![],
            max_depth: 1,
            max_file_bytes: 512 * 1024,
        };
        let r = scan_harnesses(&opts);
        assert!(find(&r, "harness-claude-deep").is_none());
    }

    #[test]
    fn dedupes_same_source_path() {
        // A file reachable as both .clinerules-dir rule and nothing else: ensure
        // canonical dedupe — register the same file twice via two roots.
        let tmp = tempfile::tempdir().unwrap();
        write(&tmp.path().join("AGENTS.md"), "# conv");
        let opts = HarnessScanOptions {
            project_roots: vec![tmp.path().to_path_buf(), tmp.path().to_path_buf()],
            global_roots: vec![],
            max_depth: 2,
            max_file_bytes: 512 * 1024,
        };
        let r = scan_harnesses(&opts);
        assert_eq!(r.duplicates, 1);
        assert_eq!(
            r.discovered
                .iter()
                .filter(|s| s.harness_tool == HarnessTool::Codex)
                .count(),
            1
        );
    }

    #[test]
    fn skips_oversize_and_empty() {
        let tmp = tempfile::tempdir().unwrap();
        write(&tmp.path().join("AGENTS.md"), &"x".repeat(2000));
        write(&tmp.path().join("CONVENTIONS.md"), "   \n  \n");
        let opts = HarnessScanOptions {
            project_roots: vec![tmp.path().to_path_buf()],
            global_roots: vec![],
            max_depth: 2,
            max_file_bytes: 100, // AGENTS.md (2000 bytes) exceeds this
        };
        let r = scan_harnesses(&opts);
        assert!(r.skipped.iter().any(|(_, why)| why == "file-too-large"));
        assert!(r.skipped.iter().any(|(_, why)| why == "empty"));
    }

    #[test]
    fn content_hash_changes_with_content() {
        assert_ne!(content_hash("a"), content_hash("b"));
        assert_eq!(content_hash("a"), content_hash("a"));
    }
}
