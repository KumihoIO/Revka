//! Positive guard: every Operator MCP tool name advertised in
//! `OPERATOR_CORE_PROMPT` / `OPERATOR_CHANNEL_PROMPT` must exist as a real
//! `name="…"` registration in `operator-mcp/operator_mcp/operator_mcp.py`.
//!
//! Companion to `kumiho_prompt_tool_contract.rs` (PR #137). Together the
//! two contract tests cover Revka's two prompt-emitting sidecars:
//! Kumiho memory (always-on, vendored externally) and the Operator
//! orchestration MCP (vendored in-repo at `operator-mcp/`).
//!
//! ## Why
//!
//! Coherence audit 2026-05 surfaced multiple "Aspirational" rows where
//! Revka prompts named tools that the runtime didn't actually
//! register (Row 1 / Row 7 / Row 14). The negative guard at
//! `no_legacy_memory_tool_advertisements.rs` catches regressions to
//! known-bad names. The positive guards catch new typos / renames /
//! removed-but-still-advertised tools before they ship.
//!
//! ## Source of truth
//!
//! Tool registrations live in the `Tool(name="…", …)` literals in
//! `operator-mcp/operator_mcp/operator_mcp.py`. Because that file is
//! vendored in this repo, the test reads it at run time — no vendored
//! allowlist to keep in sync.
//!
//! ## Extraction strategy
//!
//! The Operator prompts mention tools two ways: function-call syntax
//! (`wait_for_agent()`) and comma-separated tool lists (`Agent tools:
//! create_agent, wait_for_agent, ….`). We extract candidates via two
//! regexes:
//!   - `\b(<snake>)\s*\(` — function-call style.
//!   - Inside any sentence intro'd by a `tools:` header, every
//!     `\b(<snake>)\b` until the next period.
//!
//! `<snake>` requires at least one underscore so prose words like
//! "agents" / "patterns" don't false-positive.

use regex::Regex;
use std::collections::BTreeSet;
use std::fs;
use std::path::PathBuf;

const PROMPT_SOURCES: &[&str] = &["src/agent/operator/core.rs", "src/agent/operator/mod.rs"];

const OPERATOR_MCP_SOURCE: &str = "operator-mcp/operator_mcp/operator_mcp.py";

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

/// Read every `name="…"` literal from the Operator MCP server source.
/// The filter requires snake_case-with-underscore so we don't pick up
/// stray non-tool string literals.
fn read_registered_operator_tools() -> BTreeSet<String> {
    let path = repo_root().join(OPERATOR_MCP_SOURCE);
    let content = fs::read_to_string(&path)
        .unwrap_or_else(|e| panic!("could not read {}: {e}", path.display()));
    // Match `name="ident"` where ident is snake_case with at least one underscore.
    let re = Regex::new(r#"\bname="([a-z][a-z0-9]*(?:_[a-z0-9]+)+)""#).expect("valid regex");
    re.captures_iter(&content)
        .map(|c| c.get(1).unwrap().as_str().to_string())
        .collect()
}

/// Extract the contents of every `(pub )?const NAME: &str = "…";` block
/// in the file. We must scan only inside string literals — scanning raw
/// Rust would false-positive on method names like `to_string`, `with_capacity`.
///
/// The prompt constants in `src/agent/operator/{core,mod}.rs` use the
/// `"\<line-continuation>…";` form, so dot-all matching captures the full
/// multiline body up to the closing `";`.
fn extract_const_string_bodies(content: &str) -> Vec<String> {
    let re =
        Regex::new(r#"(?s)(?:pub\s+)?const\s+\w+:\s*&str\s*=\s*"(.*?)";"#).expect("valid regex");
    re.captures_iter(content)
        .map(|c| c.get(1).unwrap().as_str().to_string())
        .collect()
}

/// Extract candidate tool tokens advertised in a single prompt body.
fn extract_advertised_tokens(prompt_body: &str) -> BTreeSet<String> {
    let snake = r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+";
    let call_re = Regex::new(&format!(r"\b({snake})\s*\(")).expect("valid regex");
    let list_re = Regex::new(
        r"(?i)(?:Available tools|Agent tools|Team tools|Plan tools|Goal tools|Other tools|tools)\s*:\s*([^.]*)\.",
    )
    .expect("valid regex");
    let token_re = Regex::new(&format!(r"\b({snake})\b")).expect("valid regex");

    let mut tokens: BTreeSet<String> = BTreeSet::new();
    for cap in call_re.captures_iter(prompt_body) {
        tokens.insert(cap.get(1).unwrap().as_str().to_string());
    }
    for cap in list_re.captures_iter(prompt_body) {
        let block = cap.get(1).unwrap().as_str();
        for t in token_re.captures_iter(block) {
            tokens.insert(t.get(1).unwrap().as_str().to_string());
        }
    }
    tokens
}

#[test]
fn every_advertised_operator_tool_is_registered() {
    let registered = read_registered_operator_tools();
    assert!(
        registered.len() >= 30,
        "operator-mcp registry parse looks broken — only {} tools found, expected 30+",
        registered.len()
    );

    let mut failures: Vec<String> = Vec::new();
    let mut total_seen = 0usize;

    for rel in PROMPT_SOURCES {
        let path = repo_root().join(rel);
        let content = fs::read_to_string(&path)
            .unwrap_or_else(|e| panic!("could not read prompt source {}: {e}", path.display()));
        let bodies = extract_const_string_bodies(&content);
        for body in &bodies {
            let tokens = extract_advertised_tokens(body);
            total_seen += tokens.len();
            for token in &tokens {
                if !registered.contains(token) {
                    failures.push(format!("{rel}: {token}"));
                }
            }
        }
    }

    assert!(
        total_seen > 0,
        "extracted zero advertised tokens across {PROMPT_SOURCES:?} — extraction is broken"
    );

    assert!(
        failures.is_empty(),
        "Operator tool names advertised in prompts that are NOT registered in {OPERATOR_MCP_SOURCE}:\n  {}\n\n\
         Either the prompt is wrong (typo / removed tool) or the registration was renamed.",
        failures.join("\n  ")
    );
}

#[test]
fn registered_set_contains_known_anchor_tools() {
    // Cheap sanity check: confirm a handful of well-known Operator tools
    // appear in the parsed registry. Catches regex breakage that would
    // otherwise yield an empty set and silently pass the main contract.
    let registered = read_registered_operator_tools();
    for anchor in &[
        "create_agent",
        "wait_for_agent",
        "send_agent_prompt",
        "list_agents",
        "spawn_team",
        "save_plan",
    ] {
        assert!(
            registered.contains(*anchor),
            "anchor tool {anchor:?} missing from parsed Operator MCP registry — \
             registry parse is broken"
        );
    }
}
