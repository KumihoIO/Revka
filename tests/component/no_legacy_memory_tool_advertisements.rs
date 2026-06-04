//! Repo-wide guard against re-introducing bare legacy memory-tool names.
//!
//! Audit rows 11/12 found `memory_store` / `memory_recall` / `memory_forget`
//! / `memory_search` advertised in prompts, defaults, scaffolds, and locale
//! files even though only `memory_store` and `memory_search` are real
//! Operator MCP tools (and dispatched under the `revka-operator__`
//! prefix). The bare names were uncallable.
//!
//! Round-1 guard tests were local: each one only checked a single string
//! (a prompt constant, a scaffold output, an alias map). The reviewer
//! pointed out that no repo-wide guard would have caught the inventory
//! gaps, so a fresh PR could re-introduce a bare advertisement and slip
//! past CI.
//!
//! This test walks the repo, greps for the four legacy bare names in
//! `*.rs` / `*.py` / `*.md` / `*.toml`, and fails if any match falls
//! outside the documented allowlist. The allowlist mirrors the Cat A /
//! D / F / G / H buckets in
//! `docs/audit-row-5-10-11-12-scrub-inventory.md` — anything else is a
//! regression.
//!
//! Adding a new legitimate site? Update both the allowlist below AND
//! the inventory, with a one-line classification.

use std::fs;
use std::path::{Path, PathBuf};

const LEGACY_NAMES: &[&str] = &[
    "memory_store",
    "memory_recall",
    "memory_forget",
    "memory_search",
];

/// Files that are allowed to mention the bare legacy names.
///
/// Each entry is a relative path from the repo root. Directories end with
/// `/` and match any descendant. Match is by `starts_with` after stripping
/// the leading `./`.
const ALLOWLIST: &[&str] = &[
    // ── A: Operator MCP source-of-truth (untouched, real registrations) ──
    "operator-mcp/",
    // ── A→clarify: IAM policy `memory_search` fixture (Operator MCP tool)
    "src/security/iam_policy.rs",
    // ── C: comment-only references marking the audit-row deletions ──
    // (covered implicitly by the other allowlists since the comments
    // live in files we already permit; listed here for clarity if a
    // reviewer searches.)
    // ── D: intentional kumiho/operator-prefixed surfaces ──
    ".claude/skills/revka/references/cli-reference.md",
    "docs/assets/architecture-diagrams.md",
    "docs/contributing/kumiho-memory-integration.md",
    "docs/i18n/zh-CN/maintainers/repo-map.zh-CN.md",
    "docs/i18n/zh-CN/reference/sop/cookbook.zh-CN.md",
    "docs/maintainers/repo-map.md",
    "docs/reference/sop/cookbook.md",
    "scripts/rpi-config.toml",
    "src/agent/kumiho.rs",
    "src/agent/prompt.rs",
    "src/onboard/wizard.rs",
    // ── C: replaced fixtures (now use kumiho_memory_*; bare names appear
    //       only in audit-row comments) ──
    "src/agent/loop_.rs",
    "src/skills/creator.rs",
    "src/sop/mod.rs",
    "tests/component/gemini_capabilities.rs",
    // ── F: parser / registry test fixtures with literal bare names ──
    "src/providers/compatible.rs",
    "src/providers/reliable.rs",
    // ── G/H: deprecation-warning machinery + tests ──
    "src/config/schema.rs",
    // ── meta: the audit doc + the inventory itself + this guard test ──
    "docs/coherence-audit-2026-05.md",
    "docs/audit-row-5-10-11-12-scrub-inventory.md",
    "docs/audit-row-5-10-11-12-scrub-review.md",
    "tests/component/no_legacy_memory_tool_advertisements.rs",
    // The companion positive-guard contract test references this file
    // by name in its docstring (`memory_store, etc.`).
    "tests/component/kumiho_prompt_tool_contract.rs",
    // ── pre-existing review docs / audit notes (mention names in prose) ──
    "docs/p0-2-row1-13-review.md",
];

/// Directories whose entire subtree is excluded from the walk.
const SKIP_DIRS: &[&str] = &["target", "node_modules", ".git", "dist", "build"];

fn is_local_worktree_cache(path: &Path) -> bool {
    path.ends_with(Path::new(".claude").join("worktrees"))
}

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn relevant_extension(p: &Path) -> bool {
    matches!(
        p.extension().and_then(|e| e.to_str()),
        Some("rs") | Some("py") | Some("md") | Some("toml")
    )
}

fn is_allowed(rel_path: &str) -> bool {
    // Normalise leading `./` if present.
    let rel = rel_path.strip_prefix("./").unwrap_or(rel_path);
    ALLOWLIST.iter().any(|allowed| {
        if let Some(dir) = allowed.strip_suffix('/') {
            rel.starts_with(dir)
        } else {
            rel == *allowed
        }
    })
}

fn walk(dir: &Path, out: &mut Vec<PathBuf>) {
    let entries = match fs::read_dir(dir) {
        Ok(e) => e,
        Err(_) => return,
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let name = entry.file_name();
        let name_str = name.to_string_lossy();
        if SKIP_DIRS.iter().any(|s| *s == name_str.as_ref()) {
            continue;
        }
        if is_local_worktree_cache(&path) {
            continue;
        }
        if path.is_dir() {
            walk(&path, out);
        } else if relevant_extension(&path) {
            out.push(path);
        }
    }
}

/// A line in a tracked file that mentions one of the LEGACY_NAMES, except
/// when the surrounding context shows it is the namespaced form
/// (`kumiho_memory_<name>` or `revka-operator__memory_<name>`).
struct Hit {
    rel_path: String,
    line_no: usize,
    line: String,
    legacy_name: &'static str,
}

fn line_has_bare_legacy(line: &str, name: &str) -> bool {
    // Find every occurrence of the legacy name in the line. A hit
    // counts as "bare" only if the immediate prefix is not
    // `kumiho_` (Kumiho MCP) or `__` (Operator MCP prefix
    // `revka-operator__`).
    let bytes = line.as_bytes();
    let needle = name.as_bytes();
    let mut start = 0usize;
    while let Some(rel) = line[start..].find(name) {
        let abs = start + rel;
        let prefix_kumiho = b"kumiho_";
        let prefix_op = b"__";
        let preceded_by_kumiho =
            abs >= prefix_kumiho.len() && &bytes[abs - prefix_kumiho.len()..abs] == prefix_kumiho;
        let preceded_by_operator_prefix =
            abs >= prefix_op.len() && &bytes[abs - prefix_op.len()..abs] == prefix_op;
        if !preceded_by_kumiho && !preceded_by_operator_prefix {
            return true;
        }
        start = abs + needle.len();
    }
    false
}

#[test]
fn no_bare_legacy_memory_tool_names_outside_allowlist() {
    let root = repo_root();
    let mut files = Vec::new();
    walk(&root, &mut files);

    let mut violations: Vec<Hit> = Vec::new();

    for file in &files {
        let rel = match file.strip_prefix(&root) {
            Ok(p) => p.to_string_lossy().into_owned(),
            Err(_) => continue,
        };
        if is_allowed(&rel) {
            continue;
        }
        let content = match fs::read_to_string(file) {
            Ok(c) => c,
            Err(_) => continue, // binary or unreadable — skip
        };
        for (idx, line) in content.lines().enumerate() {
            for name in LEGACY_NAMES {
                if line_has_bare_legacy(line, name) {
                    violations.push(Hit {
                        rel_path: rel.clone(),
                        line_no: idx + 1,
                        line: line.to_string(),
                        legacy_name: name,
                    });
                    break;
                }
            }
        }
    }

    if !violations.is_empty() {
        let mut msg = String::from(
            "Repo-wide guard FAILED: bare legacy memory-tool name found outside allowlist.\n\
             See docs/audit-row-5-10-11-12-scrub-inventory.md for the categorisation.\n\
             If the new site is legitimate, add it to ALLOWLIST in this file AND\n\
             classify it in the inventory.\n\n",
        );
        for v in &violations {
            msg.push_str(&format!(
                "  {path}:{line_no}: bare `{name}` in: {line}\n",
                path = v.rel_path,
                line_no = v.line_no,
                name = v.legacy_name,
                line = v.line.trim(),
            ));
        }
        panic!("{msg}");
    }
}

#[test]
fn allowlist_does_not_silence_bootstrap_prompts() {
    // Sanity check: the bootstrap-prompt source (`src/agent/kumiho.rs`)
    // is on the allowlist because it contains forbidden-alphabet guard
    // tests, but the actual prompt CONSTANTS must still be free of bare
    // legacy names. The local guard in
    // `agent::kumiho::tests::bootstrap_prompts_have_no_bare_legacy_memory_tool_names`
    // already enforces this — this assertion just makes the cross-file
    // contract explicit so a future reviewer notices if the local guard
    // is removed.
    let kumiho_rs = fs::read_to_string(repo_root().join("src/agent/kumiho.rs"))
        .expect("read src/agent/kumiho.rs");
    assert!(
        kumiho_rs.contains("bootstrap_prompts_have_no_bare_legacy_memory_tool_names"),
        "src/agent/kumiho.rs is on the allowlist for legitimate reasons \
         (deprecation guard tests live there), but the local guard test \
         `bootstrap_prompts_have_no_bare_legacy_memory_tool_names` MUST \
         remain — without it the allowlist would silently absolve bare \
         legacy names from the prompt constants."
    );
}
