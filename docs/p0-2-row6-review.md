# P0-2 Remediation Review — Row 6 (unify system-prompt builders)

## Round 2

**Verdict:** FAIL-fix
**Reviewed by:** codex/gpt-5.5 (auto mode)
**Reviewed at:** 2026-05-05T16:16:30+09:00
**Branch:** fix/unify-system-prompt-builders

### Summary

Round 2 resolves the substantive Row 6 concerns from Round 1. The channel path now routes through `SystemPromptBuilder`, the canonical section block is in the requested order, and channel-only sections are appended after the canonical block. The old `CHANNEL_FILE_ORDER` is gone, and daemon/channel personality loading now share `personality::load_personality_with_options` with channel behavior expressed as filters (`HEARTBEAT.md` denylist and `BOOTSTRAP.md` conditional list).

The remaining blocker is scope and test hygiene, not the Row 6 architecture. The branch diff against `origin/dev` includes an unexplained `src/gateway/api.rs` edit that removes `auth_profiles` and `service_token` from a test `AppState` literal. That file is outside the Row 6 scope and outside the coder's justified plumbing list. It also reintroduces the exact `AppState` test-compile breakage the brief said was fixed on `origin/dev`: `cargo test --lib agent::prompt::tests` and `cargo test --lib agent::personality::tests` both fail at compile time because of this unrelated gateway change. `cargo check --lib` passes.

I accept the AntiNarration move. Canonical section-order convergence is the point of Row 6, and the branch still includes the no-narration instruction in channel mode plus repeats the warning in `ChannelCapabilities`. The old early placement was justified only by a comment, with no telemetry proving it is more effective. If narration suppression regresses, it should be handled with an explicit prompt-priority experiment or channel telemetry rather than by violating the canonical order in this unification PR.

### Checklist results

1. Real unification (not wrapper): ✓
2. Channel behavior preserved: ✓
3. Section-order convergence: ✓
4. Bootstrap-file loading unified: ✓
5. Test coverage: ✓ for coverage design, ✗ for runnable `--lib` tests due unrelated gateway compile break
6. Cargo check --lib: pass
7. Scope discipline: ✗

### Spot-checks

`git status --short --branch` confirms the corrected branch:
```text
## fix/unify-system-prompt-builders
 M src/agent/agent.rs
 M src/agent/kumiho.rs
 M src/agent/loop_.rs
 M src/agent/personality.rs
 M src/agent/prompt.rs
 M src/channels/mod.rs
 M src/tools/delegate.rs
?? docs/p0-2-row6-review.md
```

`git diff origin/dev --stat` shows `src/channels/mod.rs` net-deletes lines, but also shows the out-of-scope gateway file:
```text
src/agent/agent.rs       |    7 +-
src/agent/kumiho.rs      |    2 +-
src/agent/loop_.rs       |    4 +-
src/agent/personality.rs |  314 +++++++++++--
src/agent/prompt.rs      | 1139 ++++++++++++++++++++++++++++++++++------------
src/channels/mod.rs      |  402 +++-------------
src/gateway/api.rs       |    2 -
src/tools/delegate.rs    |    4 +-
```

`src/agent/prompt.rs:158` documents the canonical block in the requested order:
```rust
/// DateTime → Identity → OperatorIdentity → KumihoBootstrap →
/// ToolHonesty → Tools → Safety → Skills → Workspace → Runtime →
/// ChannelMedia.
```

`src/agent/prompt.rs:168` implements that order, with channel-only sections appended afterward:
```rust
Box::new(DateTimeSection),
Box::new(IdentitySection),
Box::new(OperatorIdentitySection),
Box::new(KumihoBootstrapSection),
Box::new(ToolHonestySection),
Box::new(ToolsSection),
Box::new(SafetySection),
Box::new(SkillsSection),
Box::new(WorkspaceSection),
Box::new(RuntimeSection),
Box::new(ChannelMediaSection),
Box::new(AntiNarrationSection),
Box::new(HardwareSection),
Box::new(ActionInstructionSection),
Box::new(ChannelCapabilitiesSection),
```

`src/agent/prompt.rs:323` shows channel mode using the shared personality loader with filters, not a private file list:
```rust
let load_opts = PersonalityLoadOptions {
    files: PERSONALITY_FILES,
    exclude: opts.exclude_personality_files,
    conditional: opts.conditional_personality_files,
    max_chars: opts.bootstrap_max_chars,
};
let profile =
    personality::load_personality_with_options(ctx.workspace_dir, &load_opts);
prompt.push_str(&profile.render_with_missing_markers(PERSONALITY_FILES));
```

`src/agent/personality.rs:18` makes `PERSONALITY_FILES` the single source of truth:
```rust
/// Canonical, well-known personality files loaded from the workspace root.
/// This is the **single source of truth** for the daemon and channel prompt
/// builders
pub const PERSONALITY_FILES: &[&str] = &[
```

`src/agent/personality.rs:176` is the unified loader path:
```rust
/// Unified loader.  Both the daemon and channel prompt-builder paths call
/// this with mode-specific [`PersonalityLoadOptions`] — the loader code path
/// itself is identical.
pub fn load_personality_with_options(
```

`src/agent/prompt.rs:1044` adds byte-index canonical order assertions for daemon mode:
```rust
fn daemon_canonical_section_order_byte_indices() {
```

`src/agent/prompt.rs:1117` adds byte-index assertions that channel-only sections follow the channel canonical block:
```rust
fn channel_canonical_block_is_followed_by_channel_only_block() {
```

`src/agent/prompt.rs:1297` verifies the Kumiho lite fallback when advanced memory tools are unavailable:
```rust
fn kumiho_advanced_unavailable_falls_back_to_lite_prompt_in_both_modes() {
```

`src/agent/prompt.rs:600` keeps AntiNarration in channel mode, now appended after the canonical block:
```rust
impl PromptSection for AntiNarrationSection {
```

`src/gateway/api.rs:2366` is the out-of-scope regression:
```rust
-            auth_profiles: None,
-            service_token: Arc::<str>::from(""),
```

Search results:
```text
rg -n "CHANNEL_FILE_ORDER|load_openclaw_bootstrap_files" src
<no live matches; only one test comment mentions no parallel CHANNEL_FILE_ORDER>

rg -n "build_system_prompt_with_mode_and_autonomy" src
src/channels/mod.rs:3729:/// ad-hoc string builder (`build_system_prompt_with_mode_and_autonomy`)
```

Validation:
```text
cargo check --lib
Finished `dev` profile [optimized + debuginfo] target(s) in 1m 08s
```

Targeted test attempts:
```text
cargo test --lib agent::prompt::tests
cargo test --lib agent::personality::tests

error[E0063]: missing fields `auth_profiles` and `service_token` in initializer of `gateway::AppState`
    --> src/gateway/api.rs:2330:9
```

### Required fixes

1. Revert or otherwise remove the unrelated `src/gateway/api.rs` deletion so `cargo test --lib` compiles again.
2. After that, rerun at least `cargo test --lib agent::prompt::tests` and `cargo test --lib agent::personality::tests`; the new tests are well targeted but currently cannot be proven runnable from this worktree.
3. Decide whether the stale doc comment in `src/channels/mod.rs:3729` should keep naming the removed `build_system_prompt_with_mode_and_autonomy` function. I do not consider it a blocker because it explicitly says it was the previous ad-hoc builder, but it is still a grep hit from the reviewer brief.

## Round 1

**Verdict:** FAIL-fix
**Reviewed by:** codex/gpt-5.5 (auto mode)
**Reviewed at:** 2026-05-05T00:49:50+09:00
**Branch:** fix/unify-system-prompt-builders

## Summary

The old channel builder body is gone and live channel callers now route through `SystemPromptBuilder`, so this is a real step toward consolidation. `cargo check --lib` passes. Channel prompts also retain the major visible channel affordances I checked: project context, tool/action guidance, channel capability text, prompt truncation, and the intentional `HEARTBEAT.md` exclusion.

This is not ready to pass. The refactor still keeps a separate channel personality/bootstrap file loader inside `src/agent/prompt.rs` with its own `CHANNEL_FILE_ORDER`, explicitly documenting that it differs from `personality::PERSONALITY_FILES`. That leaves the original drift mechanism alive, just moved out of `src/channels/mod.rs`. The default section order also does not match the required canonical order: channel-only `AntiNarration`, `Hardware`, and `ActionInstruction` sections are inserted between canonical sections instead of being appended after the canonical sequence.

There is also scope drift. The brief allowed `src/agent/prompt.rs`, `src/channels/mod.rs`, possibly `src/agent/personality.rs`, and tests. The current diff also touches `src/agent/agent.rs`, `src/agent/kumiho.rs`, `src/agent/loop_.rs`, and `src/tools/delegate.rs`. Some of that may be mechanical `PromptContext` plumbing, but it was not flagged or justified, and the review target is currently a dirty `dev` worktree rather than a checked-out `fix/unify-system-prompt-builders` branch.

## Checklist results

1. Real unification (not wrapper): ✗
2. Channel behavior preserved: ✓
3. Section-order convergence: ✗
4. Bootstrap-file loading unified: ✗
5. Test coverage: ✗
6. Cargo check --lib: pass
7. Scope discipline: ✗

## Spot-checks

`src/channels/mod.rs:3727` shows the live channel entry point now delegates to the unified builder:
```rust
/// Build a channel-mode system prompt by delegating to the unified
/// [`crate::agent::prompt::SystemPromptBuilder`].
```

`src/channels/mod.rs:3774` confirms the channel path calls `SystemPromptBuilder::with_defaults()`:
```rust
SystemPromptBuilder::with_defaults()
    .build(&ctx)
    .unwrap_or_default()
```

`src/agent/prompt.rs:149` shows the implemented order is not the required canonical order:
```rust
/// Order: DateTime → Identity → OperatorIdentity → KumihoBootstrap →
/// AntiNarration → ToolHonesty → Tools → Hardware → ActionInstruction →
/// Safety → Skills → Workspace → Runtime → ChannelMedia →
/// ChannelCapabilities.
```

`src/agent/prompt.rs:160` confirms those channel-only sections are inserted before canonical `ToolHonesty`, `Safety`, and later sections:
```rust
Box::new(AntiNarrationSection),
Box::new(ToolHonestySection),
Box::new(ToolsSection),
Box::new(HardwareSection),
Box::new(ActionInstructionSection),
Box::new(SafetySection),
```

`src/agent/prompt.rs:291` shows daemon and channel still branch to different personality/bootstrap loaders:
```rust
BuilderMode::Daemon => {
    let profile = personality::load_personality(ctx.workspace_dir);
    prompt.push_str(&profile.render());
}
BuilderMode::Channel(opts) => {
    render_channel_personality(&mut prompt, ctx.workspace_dir, opts);
}
```

`src/agent/prompt.rs:311` makes the remaining drift explicit:
```rust
/// Channel-mode file order — preserves the historical OpenClaw layout
/// (AGENTS first, MEMORY last).  Differs from
/// [`personality::PERSONALITY_FILES`] both in order and in the
/// HEARTBEAT.md exclusion
```

`src/agent/prompt.rs:315` defines a second file list instead of parameterizing the shared personality file source:
```rust
const CHANNEL_FILE_ORDER: &[&str] = &[
    "AGENTS.md",
    "SOUL.md",
    "TOOLS.md",
    "IDENTITY.md",
    "USER.md",
    "BOOTSTRAP.md",
    "MEMORY.md",
];
```

`src/agent/prompt.rs:414` still gates Kumiho only on `kumiho_enabled`; I found no `kumiho_memory_advanced_available` context field or test:
```rust
if !ctx.kumiho_enabled {
    return Ok(String::new());
}
Ok(crate::agent::kumiho::KUMIHO_BOOTSTRAP_PROMPT.to_string())
```

`src/agent/prompt.rs:753` preserves channel-specific capability guidance:
```rust
let mut out = String::from("## Channel Capabilities\n\n");
out.push_str("- You are running as a messaging bot. Your response is automatically sent back to the user's channel.\n");
```

`src/agent/prompt.rs:1171` preserves the Row 7 channel exclusion behavior in tests:
```rust
assert!(
    !prompt.contains("### HEARTBEAT.md"),
    "HEARTBEAT.md must stay out of channel prompts"
);
```

`cargo check --lib` result:
```text
Finished `dev` profile [optimized + debuginfo] target(s) in 37.68s
```

Diff scope observed with `git diff origin/dev --name-only`:
```text
src/agent/agent.rs
src/agent/kumiho.rs
src/agent/loop_.rs
src/agent/prompt.rs
src/channels/mod.rs
src/tools/delegate.rs
```

Search results:
```text
rg -n "build_system_prompt_with_mode_and_autonomy" src
src/channels/mod.rs:3729:/// ad-hoc string builder (`build_system_prompt_with_mode_and_autonomy`)

rg -n "load_openclaw_bootstrap_files" src
<no matches>
```

## Required fixes (if FAIL-fix)

1. Move personality/bootstrap file loading into one shared source of truth. Do not keep `CHANNEL_FILE_ORDER` as a second hard-coded list that differs from `personality::PERSONALITY_FILES`; express channel exclusions/conditionals as metadata or mode filters on the shared list.
2. Restore the required canonical order: `DateTime → Identity → OperatorIdentity → KumihoBootstrap → ToolHonesty → Tools → Safety → Skills → Workspace → Runtime → ChannelMedia`. Channel-only additions should not interrupt that canonical sequence.
3. Add tests that assert full ordered section sequences, not only presence and identity-before-tools. Cover daemon and channel outputs.
4. Add coverage for the requested common Kumiho behavior, including the `kumiho_memory_advanced_available` gate or whatever equivalent signal this branch intends to use.
5. Resolve or justify the out-of-scope file changes. If they are only compile plumbing, call that out explicitly in the review notes; otherwise move them out of this remediation.
6. Put the worktree on the stated `fix/unify-system-prompt-builders` branch, or update the review metadata if this dirty `dev` worktree is the intended review target.

## Required rescope (if FAIL-rescope)

Not applicable. The section-based approach can work, but this implementation needs coder-revisable cleanup before it satisfies Row 6.
