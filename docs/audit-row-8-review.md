# Audit Row 8 Scrub Review

**Verdict:** PASS
**Reviewed by:** codex/gpt-5.5 (auto mode)
**Reviewed at:** 2026-05-06T11:26:57+09:00
**Branch:** fix/audit-row-8-wizard-flat-file-memory

## Summary

The rewrite removes the flat-file daily-memory fiction from the generated `MEMORY.md` and `AGENTS.md` templates. The new prose describes `MEMORY.md` as curated standing context that is auto-injected by the personality loader, and it correctly separates that file from Kumiho's persistent graph memory.

PR #130's adjacent Kumiho tool hierarchy is preserved in the wizard template: `kumiho_memory_engage` and `kumiho_memory_reflect` remain the reflex tools, with `kumiho_memory_store` and `kumiho_memory_recall` still listed as direct memory operations. The generated `AGENTS.md` Kumiho path now routes "remember this" to `kumiho_memory_reflect`, not to a fictional daily file.

One review caveat: the mandatory forbidden-phrase grep returns matches inside the new negative guard test and its comments. I am treating that as acceptable because the brief also requires such a guard test, and there are no matches in generated template prose or non-test scaffold code.

## Checklist results

1. Flat-file fiction gone: ✓
2. PR #130 work preserved: ✓
3. MEMORY.md honest: ✓
4. AGENTS.md honest: ✓
5. Tests updated: ✓
6. Cargo: pass
7. Scope: ✓

## Spot-quotes

`MEMORY.md is a separate, free-form context file you (or the user) curate; it is auto-injected by the personality loader and complements - does not replace - the Kumiho graph.`
Assessment: honest split between prompt-injected file context and persistent Kumiho memory.

`When someone says "remember this" -> call kumiho_memory_reflect so it sticks across sessions.`
Assessment: correct canonical reflex for durable memory capture.

`This file is auto-injected into the system prompt verbatim by the personality loader.`
Assessment: matches the runtime path without inventing a daily-file mechanism.

`Anything worth remembering across sessions but cheaper as live recall belongs in the memory backend (e.g. via kumiho_memory_reflect), not here.`
Assessment: good guidance on when to use Kumiho instead of pinning context into every prompt.

`Re-read MEMORY.md and call kumiho_memory_engage to surface recent decisions before duplicating work.`
Assessment: preserves Kumiho recall reflex for crash recovery.

## Required fixes (if FAIL-fix)

None.

## Substantive challenge (required even on PASS)

The `memory_backend == "none"` generated `AGENTS.md` path says "`MEMORY.md` will not be created or injected" but its "remember this" section still says to update `MEMORY.md` or another workspace file. That is not row-8 flat-file fiction, and the tests pass because `MEMORY.md` is indeed skipped, but the guidance is internally confusing for the none backend. Consider changing that path to name only files that exist by default, such as `AGENTS.md`, `TOOLS.md`, or a user-created workspace note.

## Command results

- `grep -n "memory/YYYY-MM-DD\|daily file\|daily notes\|daily log\|MAIN SESSION\|NEVER in group chats\|NEVER loaded in group\|Memory is limited\|WRITE IT TO A FILE" src/onboard/wizard.rs`: returned test-only matches in `scaffold_templates_omit_flat_file_fiction`; no generated template/non-test scaffold matches found.
- `grep -n "kumiho_memory_engage\|kumiho_memory_reflect\|kumiho_memory_store\|kumiho_memory_recall" src/onboard/wizard.rs`: found Kumiho tool references in the wizard tool list and generated guidance.
- `cargo check --lib`: passed.
- `cargo test --lib onboard::wizard`: passed, 72 passed; 0 failed; 0 ignored; 5582 filtered out.
- `git diff origin/dev --stat`: `src/onboard/wizard.rs` only.
