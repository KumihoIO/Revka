//! Positive guard: every Kumiho tool name advertised in Construct's
//! prompt-emitting source files must exist as a real MCP tool registration.
//!
//! Coherence audit 2026-05, Row 1 (and the broader "Aspirational" pattern)
//! was driven by prompts naming tools the sidecar didn't actually register.
//! The negative guard at `tests/component/no_legacy_memory_tool_advertisements.rs`
//! catches regressions to known-bad bare names (`memory_store`, etc.). This
//! test is the inverse: it catches a *new* advertisement of a fake or
//! mistyped Kumiho tool before it ships.
//!
//! ## Scope
//!
//! Scans the two files that bake `kumiho_memory_*` references into agent
//! system prompts and workspace scaffolds:
//! - `src/agent/kumiho.rs` — `KUMIHO_BOOTSTRAP_PROMPT*` constants
//! - `src/onboard/wizard.rs` — `MEMORY.md`, `AGENTS.md`, `TOOLS.md` templates
//!
//! Operator-MCP and other sidecar prompts are out of scope for this MVP
//! and would each need their own contract test.
//!
//! ## Source of truth
//!
//! `REGISTERED_KUMIHO_TOOLS` mirrors the `name="…"` registrations in:
//! - `kumiho/mcp_server.py` (the bare-tools package)
//! - `kumiho_memory/mcp_tools.py` (the high-level reflexes package)
//!
//! Both packages install into `~/.construct/kumiho/venv/` via
//! `scripts/install-sidecars.sh`. They aren't vendored in this repo, so the
//! allowlist below is the test-time mirror — keep it in sync when adding
//! new Kumiho tools upstream.

use regex::Regex;
use std::collections::BTreeSet;
use std::fs;
use std::path::PathBuf;

/// Every Kumiho tool name registered by the two MCP packages installed in the
/// sidecar venv. Update when upstream adds a new tool.
///
/// Last surveyed 2026-05-06 against:
/// - `kumiho` 0.9.24 (`kumiho/mcp_server.py:2006, 2073`)
/// - `kumiho_memory` 0.5.2 (`kumiho_memory/mcp_tools.py:555-975`)
const REGISTERED_KUMIHO_TOOLS: &[&str] = &[
    // ── kumiho/mcp_server.py — bare always-available tools ──
    "kumiho_memory_store",
    "kumiho_memory_retrieve",
    // ── kumiho_memory/mcp_tools.py — high-level reflexes ──
    "kumiho_memory_engage",
    "kumiho_memory_reflect",
    "kumiho_memory_recall",
    "kumiho_memory_consolidate",
    "kumiho_memory_discover_edges",
    "kumiho_memory_dream_state",
    "kumiho_memory_ingest",
    "kumiho_memory_add_response",
    "kumiho_memory_store_execution",
];

/// Files whose contents may bake Kumiho tool names into agent prompts or
/// workspace scaffolds. Anything matching `\bkumiho_memory_[a-z_]+\b` in
/// these files must resolve to a real registration.
const PROMPT_SOURCES: &[&str] = &["src/agent/kumiho.rs", "src/onboard/wizard.rs"];

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

#[test]
fn every_advertised_kumiho_tool_is_registered() {
    let pattern = Regex::new(r"\bkumiho_memory_[a-z_]+\b").expect("valid regex");
    let registered: BTreeSet<&str> = REGISTERED_KUMIHO_TOOLS.iter().copied().collect();

    let mut failures: Vec<String> = Vec::new();
    let mut seen_any = false;

    for rel in PROMPT_SOURCES {
        let path = repo_root().join(rel);
        let content = fs::read_to_string(&path)
            .unwrap_or_else(|e| panic!("could not read prompt source {}: {e}", path.display()));

        let mut tokens_in_file: BTreeSet<String> = BTreeSet::new();
        for cap in pattern.find_iter(&content) {
            tokens_in_file.insert(cap.as_str().to_string());
        }

        if !tokens_in_file.is_empty() {
            seen_any = true;
        }

        for token in &tokens_in_file {
            if !registered.contains(token.as_str()) {
                failures.push(format!("{rel}: {token}"));
            }
        }
    }

    assert!(
        seen_any,
        "expected at least one kumiho_memory_* token across {PROMPT_SOURCES:?} — \
         the regex or source list is broken"
    );

    assert!(
        failures.is_empty(),
        "Kumiho tool names advertised in prompts that are NOT in the registered set:\n  {}\n\n\
         Either the prompt is wrong (typo / removed tool) or REGISTERED_KUMIHO_TOOLS is stale. \
         Source of truth: kumiho/mcp_server.py and kumiho_memory/mcp_tools.py.",
        failures.join("\n  ")
    );
}

#[test]
fn registered_allowlist_is_internally_consistent() {
    // Guard the allowlist itself: every entry must match the Kumiho naming
    // convention. Catches typos / accidental edits that would silently break
    // the positive contract above.
    let pattern = Regex::new(r"^kumiho_memory_[a-z_]+$").expect("valid regex");
    for tool in REGISTERED_KUMIHO_TOOLS {
        assert!(
            pattern.is_match(tool),
            "REGISTERED_KUMIHO_TOOLS entry {tool:?} does not match the kumiho_memory_* convention"
        );
    }

    let unique: BTreeSet<&&str> = REGISTERED_KUMIHO_TOOLS.iter().collect();
    assert_eq!(
        unique.len(),
        REGISTERED_KUMIHO_TOOLS.len(),
        "REGISTERED_KUMIHO_TOOLS contains duplicates"
    );
}
