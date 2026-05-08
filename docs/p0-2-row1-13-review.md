# P0-2 Remediation Review — Rows 1 + 13 paired fix

## Round 2

**Verdict:** PASS
**Reviewed by:** codex/gpt-5.5 (auto mode)
**Reviewed at:** 2026-05-04T20:00:00+09:00
**Branch:** fix/kumiho-stock-install-coherence

### Summary

Round 2 satisfies the rescope. The old filesystem/package-presence probe is gone; `src/agent/kumiho.rs` now exposes `registry_has_advanced_kumiho_tools(tool_names: &[String])`, and every traced caller feeds it `registry.tool_names()` after `McpRegistry::connect_all` succeeds. That gates the prompt on actual MCP-registered tool names instead of whether `site-packages/kumiho_memory/` exists.

The lite prompts are now properly stripped. `KUMIHO_BOOTSTRAP_PROMPT_LITE` and `KUMIHO_CHANNEL_BOOTSTRAP_PROMPT_LITE` name only `kumiho_memory_store` and `kumiho_memory_retrieve`; they do not mention `kumiho_memory_engage`, `kumiho_memory_reflect`, `kumiho_memory_recall`, `kumiho_memory_consolidate`, or `kumiho_memory_dream_state` in either positive or negative phrasing. The prompt tests were tightened to plain substring rejection, fixing the Round 1 weak-test issue.

I accept the three coder-flagged deviations. Moving the warning out of `inject_kumiho` is the correct shape because `inject_kumiho` runs before MCP startup and cannot know registry state without creating a second async/probe path. I also accept relying on the existing `connect_all` path rather than adding a new 5s wrapper, because the new probe itself is pure and nonblocking after registry connection; adding a separate timeout would be a broader MCP lifecycle change. I accept `src/gateway/mod.rs` as an additional warning site because it is directly tied to the same runtime registry state and makes the gateway lifecycle visible before the first agent run.

### Checklist results

1. Installer change: ✓ — `scripts/install-sidecars.sh` and `scripts/install-sidecars.bat` still install `kumiho_memory>=0.5.0`; Round 1 already verified the spec exists on PyPI.
2. Prompt builder conditional: ✓ — `PromptContext.kumiho_memory_advanced_available` drives full vs lite selection, and the flag is now supplied from post-connect registry contents.
3. Test coverage: ✓ — prompt-builder tests cover disabled / lite / full; lite assertions are plain substring rejections for all high-level memory reflex tool names; `kumiho.rs` includes registry probe tests for empty, unprefixed, bare-only, and prefixed-sentinel registries.
4. Scope discipline: ✓ — `git diff origin/dev --stat` no longer shows the prior unrelated frontend/gateway committed changes. The current source diff is limited to Row 1/13 implementation, wiring, tests, and the accepted gateway warning site.
5. Wizard correction: N/A — `src/onboard/wizard.rs` was not modified in Round 2.
6. Loud-failure warning: ✓ — `warn_if_kumiho_advanced_missing` is actionable and mentions `~/.construct/kumiho/venv/bin/pip install 'kumiho_memory>=0.5.0'` plus `scripts/install-sidecars.sh`.
7. Net-deletes ≥ net-adds: ✓ with caveat — this is still a net-add patch, but the new prose is materially smaller and stripped, and the main fix is now a structured runtime registry check. I would not block on net line count here.

### Spot-checks

`src/agent/kumiho.rs:277-280`:

```rust
pub fn registry_has_advanced_kumiho_tools(tool_names: &[String]) -> bool {
    let target = prefixed_kumiho_tool(ADVANCED_PROBE_TOOL_SUFFIX);
    tool_names.iter().any(|n| n == &target)
}
```

This is a pure registry-name check; no filesystem path, venv scan, import, or `site-packages` heuristic remains.

`src/agent/kumiho.rs:171-191`:

```rust
pub const KUMIHO_BOOTSTRAP_PROMPT_LITE: &str = "\
...
Available tools:
  - kumiho_memory_store    — store a memory item to the graph.
  - kumiho_memory_retrieve — retrieve a memory item by id or filter.
...
```

The daemon lite prompt only names the always-available pair.

`src/agent/kumiho.rs:225-233`:

```rust
pub const KUMIHO_CHANNEL_BOOTSTRAP_PROMPT_LITE: &str = "\
...
For 'remember this' requests, use kumiho_memory_store ...
in the title. For recall, use kumiho_memory_retrieve ...
```

The channel lite prompt is also stripped.

`src/agent/prompt.rs:838-860`:

```rust
assert!(!out.contains("kumiho_memory_engage"));
assert!(!out.contains("kumiho_memory_reflect"));
assert!(!out.contains("kumiho_memory_recall"));
assert!(!out.contains("kumiho_memory_consolidate"));
assert!(!out.contains("kumiho_memory_dream_state"));
```

These are plain substring checks, not phrase-specific checks.

Connect-site traces:

```rust
// src/agent/loop_.rs:3751-3760
match crate::tools::McpRegistry::connect_all(&config.mcp.servers).await {
    Ok(registry) => {
        let registry = std::sync::Arc::new(registry);
        kumiho_advanced = crate::agent::kumiho::registry_has_advanced_kumiho_tools(
            &registry.tool_names(),
        );
```

```rust
// src/channels/mod.rs:5227-5234
match crate::tools::McpRegistry::connect_all(&config.mcp.servers).await {
    Ok(registry) => {
        let registry = std::sync::Arc::new(registry);
        ch_mcp_registry = Some(std::sync::Arc::clone(&registry));
        kumiho_advanced = crate::agent::kumiho::registry_has_advanced_kumiho_tools(
            &registry.tool_names(),
        );
```

```rust
// src/agent/agent.rs:510-520
match tools::McpRegistry::connect_all(&config.mcp.servers).await {
    Ok(registry) => {
        let registry = std::sync::Arc::new(registry);
        kumiho_advanced = crate::agent::kumiho::registry_has_advanced_kumiho_tools(
            &registry.tool_names(),
        );
```

```rust
// src/gateway/mod.rs:580-592
match tools::McpRegistry::connect_all(&gateway_mcp_config.servers).await {
    Ok(registry) => {
        let registry = std::sync::Arc::new(registry);
        let kumiho_advanced = crate::agent::kumiho::registry_has_advanced_kumiho_tools(
            &registry.tool_names(),
        );
```

### Deviation Decisions

A. Warning location moved: accept — the warning needs post-connect registry state; keeping it in sync `inject_kumiho` would reintroduce guesswork or require a second startup/probe path.

B. No 5s timeout wrapper: accept — the probe itself is constant-time after `connect_all`; a new timeout wrapper belongs to broader MCP startup policy, not this remediation. Existing error / empty / disabled fallback keeps the prompt conservative.

C. `src/gateway/mod.rs` warning site: accept — it is relevant to the gateway lifecycle and does not broaden behavior beyond Row 1's prompt/tool-availability contract.

### Verification

`cargo check --lib` passed cleanly in 4m36s.

### Follow-up

Nonblocking challenge: `registry_has_advanced_kumiho_tools` uses `kumiho_memory_engage` as a sentinel for the whole advanced set. That is defensible given the documented all-or-none merge behavior, but if the MCP package ever supports partial registration, this should become an explicit required-set check for at least engage + reflect.

---

## Round 1

**Verdict:** FAIL-rescope
**Reviewed by:** codex/gpt-5.5 (auto mode)
**Reviewed at:** 2026-05-04T19:42:20+09:00
**Branch:** fix/kumiho-stock-install-coherence

## Summary

The installer half is directionally correct: both POSIX and Windows sidecar installers now install `kumiho_memory>=0.5.0` alongside `kumiho[mcp]>=0.9.20`, and an escalated `python3 -m pip index versions kumiho_memory` lookup confirmed that `0.5.0` through `0.5.2` exist on PyPI. `cargo check --lib` passed.

The Row 1 runtime contract is not satisfied. The implementation does not probe actual registered tool availability; it checks whether a `site-packages/kumiho_memory/` directory exists next to the configured launcher. That can report true while the MCP registry still lacks the required tools due to import failure, incompatible package contents, broken startup, tool exclusion, or a nonstandard runtime layout. This is the core audit prescription, so this is a rescope failure rather than a small fix.

There are also coder-fixable issues: the lite prompt still names the high-level tools it is supposed to avoid, references low-level tools beyond the prescribed always-available `kumiho_memory_store` / `kumiho_memory_retrieve` pair, and the scope check against `origin/dev..HEAD` is not clean.

## Checklist results

1. Installer change: ✓ — `scripts/install-sidecars.sh:179` and `scripts/install-sidecars.bat:111` install `"kumiho_memory>=0.5.0"`. The spec is a lower bound, not an exact pin, and PyPI currently has `0.5.0`, `0.5.1`, and `0.5.2`. Initial sandboxed `python3 -m pip index versions kumiho_memory` failed due network restriction; escalated lookup passed.
2. Prompt builder conditional: ✗ — `PromptContext.kumiho_memory_advanced_available` exists and `KumihoBootstrapSection::build` selects full vs lite, but the flag is driven by package-directory probing, not actual registry contents. The lite constant also still mentions `kumiho_memory_engage` / `reflect` and additional low-level tools.
3. Test coverage: ✗ — prompt-builder tests cover disabled / lite / full states, and installer grep tests exist. However the lite tests only reject narrow substrings such as `"Call kumiho_memory_engage"` while the lite prompt still contains `kumiho_memory_engage`. `cargo check --lib` passed. `cargo test --test component install_sidecars_kumiho_memory` was started twice but did not complete after extended compilation time, so I did not confirm the installer grep test result.
4. Scope discipline: ✗ — `git diff origin/dev..HEAD --stat` shows unrelated committed `src/gateway/*` and `web/src/*` changes. The working tree also modifies `src/agent/agent.rs`, `src/agent/loop_.rs`, `src/channels/mod.rs`, `src/tools/delegate.rs`, and `tests/component/mod.rs`, which are outside the brief's allowed file list, even if some are mechanically related to wiring the new flag.
5. Wizard correction: N/A — `src/onboard/wizard.rs` was not modified. I could not spot-check `kumiho_memory/mcp_tools.py` locally because `~/.construct/kumiho/venv` does not currently have the `kumiho_memory` package installed.
6. Loud-failure warning: ✓ — `src/agent/kumiho.rs:476-484` logs an actionable warning including `~/.construct/kumiho/venv/bin/pip install 'kumiho_memory>=0.5.0'` and the installer rerun path. Caveat: it is based on the same package-directory heuristic.
7. Net-deletes ≥ net-adds: ✗ — the runtime probe is the right kind of artifact, but the prompt changes add duplicated prose and the lite prompt is not truly stripped. The working tree is net +363 lines by `git diff --stat`, and the lite variants duplicate warning/instruction text instead of parameterizing the common parts.

## Spot-checks

`scripts/install-sidecars.sh:179`:

```bash
run "'$kumiho_py' -m pip install --quiet 'kumiho[mcp]>=0.9.20' 'kumiho_memory>=0.5.0'"
```

`scripts/install-sidecars.bat:111`:

```bat
"%K_PY%" -m pip install --quiet "kumiho[mcp]>=0.9.20" "kumiho_memory>=0.5.0"
```

`src/agent/kumiho.rs:171-192`:

```rust
pub const KUMIHO_BOOTSTRAP_PROMPT_LITE: &str = "\
...
memory reflexes (kumiho_memory_engage / reflect / recall / consolidate / \
...
  - kumiho_search_items, kumiho_fulltext_search — low-level search.
...
  - Do NOT call kumiho_memory_engage / reflect / recall / consolidate / \
...
```

This is not the prescribed stripped/lite contract. It references high-level unavailable tools by name and adds low-level tools beyond `kumiho_memory_store` / `kumiho_memory_retrieve`.

`src/agent/kumiho.rs:276-309`:

```rust
pub fn probe_kumiho_memory_advanced(kumiho_cfg: &KumihoConfig) -> bool {
    ...
    if entry.path().join("site-packages").join("kumiho_memory").exists() {
        return true;
    }
    ...
}
```

This probes filesystem package presence, not the MCP registry contents after tool registration.

`src/agent/prompt.rs:841-849`:

```rust
assert!(!out.contains("Call kumiho_memory_engage"));
assert!(!out.contains("kumiho_memory_reflect only"));
```

The assertions are too weak; they pass even though the lite prompt contains `kumiho_memory_engage` and `reflect`.

## Required fixes (if FAIL-fix)

Not the primary verdict. If the approach is kept after rescope, tighten the lite prompt/tests and clean the branch scope before requesting another review.

## Required rescope (if FAIL-rescope)

Replace the filesystem `site-packages/kumiho_memory` heuristic with a check based on actual registered tool availability. The acceptance criterion should be whether the runtime registry contains the required high-level tool names, not whether a Python package directory exists. Also make the lite prompt reference only the guaranteed tools named in the audit prescription: `kumiho_memory_store` and `kumiho_memory_retrieve`.
