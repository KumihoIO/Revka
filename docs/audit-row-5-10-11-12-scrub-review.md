# Audit Rows 5 + 10 + 11 + 12 Scrub Review

**Current verdict:** PASS
**Reviewed by:** codex/gpt-5.5 (auto mode)
**Round 3 reviewed at:** 2026-05-06T10:44:23+09:00
**Branch:** fix/scrub-legacy-memory-tools

## Round 3

**Verdict:** PASS

### Summary

Round 3 resolves the sole remaining Round 2 failure. I reran the exact mandatory grep from the brief and got **131 grep-loop hits / 111 unique `file:line` entries**, matching the updated inventory. The inventory now explicitly accounts for the eight unique lines added by `tests/component/no_legacy_memory_tool_advertisements.rs` and classifies them under bucket G as guard-test forbidden-alphabet references.

The new G entries are defensible: the file must name the legacy bare tools in its module docstring and `LEGACY_NAMES` matcher in order to enforce the repo-wide guard. The added allowlist comment at line 43 names the IAM `memory_search` fixture as an attribution note, not as a tool advertisement.

All earlier Round 2 fixes still stand: reachability for `kumiho_memory_store` / `kumiho_memory_retrieve` is proven via `kumiho/mcp_server.py`, wizard scaffolding distinguishes reflex-vs-direct usage, and the guard test passed 2/2 in Round 2. With the inventory arithmetic now matching the current branch, the review passes.

### Verification

Mandatory grep:

```text
131 grep-loop hits
111 unique file:line entries
```

Inventory coverage summary now says:

```text
G — Guard test (forbidden-alphabet entry): 27
Total: 111 unique lines (= 131 grep-loop hits)
```

Spot-traces of new G entries:

| File:line | Actual content | Assessment |
| --- | --- | --- |
| `tests/component/no_legacy_memory_tool_advertisements.rs:3` | Module docstring names `memory_store` / `memory_recall` / `memory_forget` | Defensible bucket G: describes the forbidden alphabet the guard scans for. |
| `tests/component/no_legacy_memory_tool_advertisements.rs:29` | `LEGACY_NAMES` matcher entry `"memory_store"` | Defensible bucket G: required matcher constant for the repo-wide guard. |
| `tests/component/no_legacy_memory_tool_advertisements.rs:43` | Allowlist comment naming IAM `memory_search` fixture | Defensible bucket G/A-clarify: explanatory allowlist comment, not user-facing tool advertising. |

### Substantive challenge

If redoing this scrub, I would make the repo-wide guard line-oriented rather than file-allowlist-oriented. Today, broad allowlisted files such as `src/onboard/wizard.rs`, `src/config/schema.rs`, and `src/agent/kumiho.rs` can still hide a future bare advertisement unless local tests catch it. A line-pattern allowlist or checked inventory file generated from the grep would make the guard and inventory impossible to drift apart.

### Phase 0-2 Follow-up

For Phase 0-2 sequencing, keep the two-package Kumiho registration model explicit in the plan: low-level always-available tools come from `kumiho/mcp_server.py`, high-level reflexes are merged from `kumiho_memory.mcp_tools`. Future prompt or scaffold changes should verify names against the effective MCP registry, not just one package file.

## Round 2

**Verdict:** FAIL-fix

### Summary

Round 2 fixes the core technical error behind my Round 1 category-accuracy failure. I verified directly that `kumiho_memory_store` and `kumiho_memory_retrieve` are registered in the bare `kumiho` package MCP server, while the high-level reflex tools are merged from the sibling `kumiho_memory` package. The two-package registration defense is accepted.

The wizard scaffold is also materially improved: `src/onboard/wizard.rs:5914-5917` now distinguishes canonical reflexes (`kumiho_memory_engage` / `kumiho_memory_reflect`) from low-level direct calls (`kumiho_memory_store` / `kumiho_memory_recall`), and all four advertised tools are reachable under the two-package model. The new repo-wide guard test passes and is the right shape for preventing accidental reintroduction of bare advertisements.

The remaining failure is inventory completeness as measured by the exact mandatory grep from the brief. On the current branch, that command returns 131 grep-loop hits and 111 unique `file:line` entries, not the inventory's stated 120 / 103. The extra unique entries are the new guard test's own bare-name literals in `tests/component/no_legacy_memory_tool_advertisements.rs`; the inventory mentions the guard conceptually, but its "Classification of every grep hit" table and coverage summary are now stale. This is fixable by updating the inventory counts and classifying those guard-file lines explicitly, or by documenting and using a revised grep exclusion that excludes the guard file.

### Checklist results

1. Inventory completeness: ✗ — mandatory grep now returns 131 loop hits / 111 unique lines. Inventory still claims 120 / 103.
2. Category accuracy: ✓ — reachability defense accepted; `kumiho_memory_store` / `kumiho_memory_retrieve` are registered in `kumiho/mcp_server.py`, and high-level tools are merged from `kumiho_memory.mcp_tools`.
3. Backwards-compat preservation: ✓ — unchanged from Round 1; warning machinery remains in code and IAM string policy compatibility is preserved.
4. Prompt rewrite (Row 5): ✓ — old Paseo directives remain absent from prompt constants; lite prompt names the registered always-available pair.
5. install.sh + wizard scaffolding (Row 10): ✓ — wizard memory guidance now cleanly separates reflex-vs-direct usage and advertises reachable tools.
6. i18n sync: ✓ for the scoped scrub surfaces I checked; no remaining i18n drift on the edited zh-CN mirrors, and the previously challenged `kumiho_memory_store` / `retrieve` names are now proven reachable.
7. Test coverage: ✓ — new guard test passes 2/2 and includes both repo-wide allowlist enforcement and a meta-test preventing `src/agent/kumiho.rs` from being silently absolved without its local guard.
8. Cargo check: not rerun in Round 2; Round 1 `cargo check --lib` passed. Required Round 2 guard test passed.
9. Scope discipline: ✓ — the inventory now gives per-directory justification for the broad touched set, and no automatic-fail areas (`src/gateway/`, `src/tunnel/`, `src/cron/`, `web/`) are modified.

### Reachability verification

Commands run:

```text
$ ~/.construct/kumiho/venv/bin/python -c "from kumiho.mcp_server import TOOL_HANDLERS; print('store?', 'kumiho_memory_store' in TOOL_HANDLERS, 'retrieve?', 'kumiho_memory_retrieve' in TOOL_HANDLERS)"
store? True retrieve? True

$ grep -n 'kumiho_memory_store\|kumiho_memory_retrieve' ~/.construct/kumiho/venv/lib/python3.11/site-packages/kumiho/mcp_server.py
2006:        "name": "kumiho_memory_store",
2073:        "name": "kumiho_memory_retrieve",
2896:    "kumiho_memory_store": lambda args: tool_memory_store(
2916:    "kumiho_memory_retrieve": lambda args: tool_memory_retrieve(
```

Assessment: accepted. My Round 1 check only inspected `kumiho_memory/mcp_tools.py`, which is incomplete for this sidecar because `kumiho/mcp_server.py` owns the low-level always-available pair and then merges in the high-level memory package.

### Spot-checks

Five sampled mandatory-grep lines all have defensible inventory buckets:

| Grep line | Inventory bucket | Assessment |
| --- | --- | --- |
| `operator-mcp/tests/test_tool_handlers/test_record_agent_outcome.py:32` | A | Defensible: Operator MCP source-of-truth/tests for real `memory_store`. |
| `docs/maintainers/repo-map.md:104` | D | Defensible: intentional Kumiho / Operator-prefixed documentation surface. |
| `src/agent/kumiho.rs:178` | D | Defensible: lite prompt uses registered `kumiho_memory_store`. |
| `src/providers/reliable.rs:2568` | F | Defensible: literal Groq error-message fixture; opaque tool name. |
| `src/config/schema.rs:15984` | G | Defensible: guard assertion checking Docker template does not bake stale names. |

### Wizard scaffold

`src/onboard/wizard.rs:5914-5917` now says:

```text
- `kumiho_memory_engage` — canonical reflex for recall. Call BEFORE responding...
- `kumiho_memory_reflect` — canonical reflex for capture...
- `kumiho_memory_store` — low-level write... Prefer `reflect`...
- `kumiho_memory_recall` — low-level fuzzy lookup... Prefer `engage`...
```

Assessment: pass. The guidance is coherent and all four names are reachable under the verified two-package registration model.

### Guard test

Command run:

```text
$ cargo test --test component no_legacy_memory_tool_advertisements
running 2 tests
test component::no_legacy_memory_tool_advertisements::allowlist_does_not_silence_bootstrap_prompts ... ok
test component::no_legacy_memory_tool_advertisements::no_bare_legacy_memory_tool_names_outside_allowlist ... ok
```

The guard does what it claims. The main assertion builds violations for non-allowlisted bare-name hits and panics with `Repo-wide guard FAILED: bare legacy memory-tool name found outside allowlist`; the meta-test asserts `src/agent/kumiho.rs` still contains `bootstrap_prompts_have_no_bare_legacy_memory_tool_names`, so the allowlist cannot silently absolve bootstrap prompts.

### Mandatory grep

Exact mandatory grep rerun:

```text
for name in memory_store memory_recall memory_forget memory_search; do
  grep -rn "$name" --include="*.rs" --include="*.py" --include="*.md" --include="*.toml" 2>/dev/null \
    | grep -v target/ \
    | grep -v node_modules/ \
    | grep -v "kumiho_memory_$name" \
    | grep -v "docs/coherence-audit\|docs/p0-2\|docs/audit-row-5-10-11-12"
done
```

Observed results:

```text
131 grep-loop hits
111 unique file:line entries
```

The inventory's current coverage summary says **103 unique lines (= 120 grep-loop hits)**. The delta is the new guard test file's own literal alphabet:

```text
tests/component/no_legacy_memory_tool_advertisements.rs:3
tests/component/no_legacy_memory_tool_advertisements.rs:4
tests/component/no_legacy_memory_tool_advertisements.rs:5
tests/component/no_legacy_memory_tool_advertisements.rs:29
tests/component/no_legacy_memory_tool_advertisements.rs:30
tests/component/no_legacy_memory_tool_advertisements.rs:31
tests/component/no_legacy_memory_tool_advertisements.rs:32
tests/component/no_legacy_memory_tool_advertisements.rs:43
```

Those are legitimate Cat G/meta references, but the inventory must count and classify them if it claims coverage of the exact mandatory grep.

### Required fixes

1. Update `docs/audit-row-5-10-11-12-scrub-inventory.md` so the mandatory-grep coverage summary matches the current branch: 131 loop hits / 111 unique lines, unless the review procedure is explicitly changed to exclude the guard test file.
2. Add the eight unique `tests/component/no_legacy_memory_tool_advertisements.rs` lines to the inventory classification table as guard/meta entries.
3. Keep the reachability evidence section; it resolves the Round 1 category-accuracy failure.

### Substantive challenge

The new allowlist guard is useful, but it is file-level, not line-level. A future bare advertisement added to an already-allowlisted file such as `src/agent/kumiho.rs`, `src/onboard/wizard.rs`, or `src/config/schema.rs` can still pass the repo-wide guard unless a local targeted assertion catches it. The meta-test covers `src/agent/kumiho.rs`; equivalent local guards are still needed for other broad allowlist files that contain user-facing prompt, scaffold, or config text.

## Round 1 (original FAIL-fix review)

## Summary

The scrub removes the most visible bare `memory_recall` / `memory_forget` advertisements from prompts, defaults, onboarding, and tool descriptions, and the targeted Rust tests pass. However, the inventory is not complete enough to be used as the audit ledger: the mandatory grep returned 120 surviving matches outside the excluded audit docs, and at least 18 of those are either absent from the inventory or only indirectly mentioned in the prose reality-check instead of being classified as untouched Category A entries.

The biggest correctness problem is that several replacement paths now advertise `kumiho_memory_store` and `kumiho_memory_retrieve` as canonical or always available, but the required runtime source check against `~/.construct/kumiho/venv/lib/python3.11/site-packages/kumiho_memory/mcp_tools.py` does not show either tool registered. That preserves the same runtime-trust gap in a new shape: the agent is still told to call tool names that the checked MCP registry source does not expose.

This is coder-revisable. The A/B/C framework can still work, but the inventory needs to cover every remaining grep hit explicitly, the Kumiho replacement names must be verified against actual registered tool names, and the guard tests need to move beyond prompt-only substring checks.

## Checklist results

1. Inventory completeness: ✗ — mandatory grep returned 120 surviving matches; at least 18 are not explicitly inventoried as untouched / Category A / no-action entries. Coverage score: at most 102 of 120.
2. Category accuracy: ✗ — 5 spot-traces performed; Operator MCP A traces for `memory_store` / `memory_search` check out, but C replacements to `kumiho_memory_store` / `kumiho_memory_retrieve` are not reachable in the checked Kumiho MCP tool list.
3. Backwards-compat preservation: ✓ — IAM policy stores tool permissions as strings and the `memory_search` fixture still parses/evaluates; config warnings are emitted in code via `warn_on_legacy_memory_tool_names()`.
4. Prompt rewrite (Row 5): ✓ with caveat — old Paseo directives are absent from prompt constants and only remain in negative tests; the lite prompt is internally coherent only if `kumiho_memory_store` / `kumiho_memory_retrieve` actually exist, which the required MCP check did not confirm.
5. install.sh + wizard scaffolding (Row 10): ✗ — `install.sh` uses `kumiho_memory_engage`, but `src/onboard/wizard.rs:5917` still advertises `kumiho_memory_recall` despite the inventory saying the scaffold line was replaced with `kumiho_memory_engage`.
6. i18n sync: ✗ — changed English docs were mirrored in zh-CN for the touched files, but the same edited maintainer/cookbook content has no corresponding ko mirrors in the tree, and remaining docs still advertise unverified `kumiho_memory_store` / `kumiho_memory_retrieve`.
7. Test coverage: partial — required tests pass: `cargo test --lib agent::kumiho` 22/22, `cargo test --lib agent::prompt` 26/26, `cargo test --lib security::iam_policy` 16/16, `cargo test --lib onboard::wizard` 71/71. The claimed orphan-reference guard is only local to prompt/scaffold strings; I found no repo-wide grep/regex guard that would catch the inventory gaps.
8. Cargo check: pass — `cargo check --lib` passed. `cargo clippy --lib` passed with 5 warnings (`clippy::elidable_lifetime_names` in `src/agent/personality.rs` and `src/agent/prompt.rs`, plus `clippy::match_wildcard_for_single_variants` in `src/agent/prompt.rs`).
9. Scope discipline: ✗ — no automatic fail paths under `src/gateway/`, `src/tunnel/`, `src/cron/`, or `web/` were modified, but `git diff origin/dev --stat` includes broad files outside the brief's expected list: `.github/labeler.yml`, Dockerfiles, `dev/`, `scripts/`, `src/config/`, `src/providers/`, `src/skills/`, `src/sop/`, and `tool_descriptions/`.

## Spot-traces

### 1. Operator MCP `memory_store` reality check — Category A confirmed, but inventory incomplete

Inventory row/prose:
`memory_store` is listed as Operator MCP registered in the reality-check table and described as remaining source-of-truth in the "Out of scope" section.

Actual content:
```text
operator-mcp/operator_mcp/operator_mcp.py:2160
Tool(
    name="memory_store",
    description=(
        "Store a memory bundle (decision/fact/preference/summary). Auto-stacks revisions on "
        "similar items by default. Use this to record orchestration decisions, sub-agent outcomes, "
        "or cross-session lessons."
    ),
)

operator-mcp/operator_mcp/operator_mcp.py:2547
if name == "memory_store":
    from .tool_handlers.memory import tool_memory_store_op
    return await tool_memory_store_op(args)
```

Assessment: Category A is correct for Operator MCP. Inventory problem: the mandatory grep also finds Operator MCP tests and handler imports/calls, but they are not classified as untouched Category A rows.

### 2. Operator MCP `memory_search` reality check — Category A confirmed

Inventory row:
`src/security/iam_policy.rs:230, 236, 339` keeps `memory_search` as a real Operator MCP tool.

Actual content:
```text
operator-mcp/operator_mcp/operator_mcp.py:2095
Tool(
    name="memory_search",
    description=(
        "Structured search by name/kind/context. Use for exact-match lookups when you "
        "already know the item kind or partial name. For natural-language queries, prefer memory_retrieve."
    ),
)

operator-mcp/operator_mcp/operator_mcp.py:2532
if name == "memory_search":
    from .tool_handlers.memory import tool_memory_search_op
    return await tool_memory_search_op(args)
```

Assessment: Category A is correct. The IAM policy comment accurately says runtime dispatch is prefixed while policy matching keeps the bare string.

### 3. Kumiho replacement `kumiho_memory_store` — Category C not verified

Inventory rows:
`install.sh`, wizard scaffolding, docs, SOP examples, tests, and prompt lite variants replace stale bare `memory_store` with `kumiho_memory_store`.

Actual content:
```text
src/agent/kumiho.rs:173
Available tools:
  - kumiho_memory_store    — store a memory item to the graph.
  - kumiho_memory_retrieve — retrieve a memory item by id or filter.

/Users/neo/.construct/kumiho/venv/lib/python3.11/site-packages/kumiho_memory/mcp_tools.py:1042
"kumiho_chat_clear": tool_chat_clear,
"kumiho_memory_ingest": tool_memory_ingest,
"kumiho_memory_add_response": tool_memory_add_response,
"kumiho_memory_consolidate": tool_memory_consolidate,
"kumiho_memory_recall": tool_memory_recall,
"kumiho_memory_discover_edges": tool_memory_discover_edges,
"kumiho_memory_engage": tool_memory_engage,
"kumiho_memory_reflect": tool_memory_reflect,
"kumiho_memory_store_execution": tool_memory_store_execution,
"kumiho_memory_dream_state": tool_memory_dream_state,
```

Assessment: FAIL. `kumiho_memory_store` and `kumiho_memory_retrieve` are not present in `MEMORY_TOOL_HANDLERS` in the required file. Advertising them as "always available" repeats the trust gap with new names unless another registry source is explicitly documented and tested.

### 4. Wizard scaffold replacement — inventory does not match code

Inventory row:
`src/onboard/wizard.rs:5943` says "Use `memory_recall` for recent context" was replaced with `kumiho_memory_engage`; `src/onboard/wizard.rs:6074` says the TOOLS.md scaffold `memory_recall` was replaced with `kumiho_memory_engage`.

Actual content:
```text
src/onboard/wizard.rs:5913
Memory is provided by the **Kumiho MCP server** (auto-injected). Use these tools:

- **`kumiho_memory_engage`** — retrieve relevant memories by query (call BEFORE responding)
- **`kumiho_memory_reflect`** — save durable decisions, preferences, facts, and outcomes worth remembering
- **`kumiho_memory_store`** — directly store a memory item
- **`kumiho_memory_recall`** — directly recall memory items
```

Assessment: FAIL. The Row 10 generated `AGENTS.md` template no longer mandates the bare `memory_recall`, but it still advertises direct `kumiho_memory_recall` in the same scaffold section where the inventory implies `engage` is the canonical recall instruction.

### 5. Backwards compatibility warning path — confirmed

Inventory decision:
Existing user configs still parse and a startup deprecation warning fires.

Actual content:
```text
src/config/schema.rs:5335
pub fn warn_on_legacy_memory_tool_names(config: &Config) {
    const LEGACY: &[(&str, &str)] = &[
        ("memory_recall", "use `kumiho_memory_engage` (Kumiho MCP) for recall"),
        ("memory_forget", "no direct replacement; use `kumiho_deprecate_item` via the Kumiho MCP if you need to retract a memory"),
        ("memory_store", "use `kumiho_memory_store` (Kumiho MCP) or `construct-operator__memory_store` (Operator MCP)"),
        ("memory_search", "use `kumiho_memory_engage` (Kumiho MCP) or `construct-operator__memory_search` (Operator MCP)"),
    ];
}

src/config/schema.rs:8953
warn_on_legacy_memory_tool_names(&config);
```

Assessment: PASS for warning emission and non-crash intent. Caveat: the suggested `kumiho_memory_store` replacement is not verified by the required Kumiho MCP source check.

### 6. i18n/doc mirror — partial sync but unverified tool names

Inventory row:
English `docs/maintainers/repo-map.md` and zh-CN mirror were synced to Kumiho/Operator attribution.

Actual content:
```text
docs/maintainers/repo-map.md:104
- **Memory**: provided by the Kumiho-memory MCP — `kumiho_memory_engage`, `kumiho_memory_reflect`, `kumiho_memory_store`, `kumiho_memory_retrieve`, `kumiho_memory_consolidate`, `kumiho_memory_dream_state`. The Operator MCP also exposes `memory_store` / `memory_search` under the `construct-operator__` prefix for legacy callers.

docs/i18n/zh-CN/maintainers/repo-map.zh-CN.md:104
- **内存**: 由 Kumiho-memory MCP 提供 — `kumiho_memory_engage`、`kumiho_memory_reflect`、`kumiho_memory_store`、`kumiho_memory_retrieve`、`kumiho_memory_consolidate`、`kumiho_memory_dream_state`。Operator MCP 也通过 `construct-operator__` 前缀提供 `memory_store` / `memory_search` 以兼容旧调用。
```

Assessment: Partial. zh-CN mirrors the English scrub, but the shared content lists `kumiho_memory_store` / `kumiho_memory_retrieve`, which the checked Kumiho MCP tool list does not expose. I did not find a ko mirror for this maintainer doc in the tree.

## Required fixes (if FAIL-fix)

1. Rebuild the inventory from the mandatory grep output and explicitly classify every surviving match, including Operator MCP tests/handlers and docs that remain intentionally prefixed or kumiho-namespaced.
2. Resolve the replacement-name mismatch: either prove where `kumiho_memory_store` and `kumiho_memory_retrieve` are actually registered at runtime, or replace prompt/docs/scaffold examples with registered tools from `mcp_tools.py` such as `kumiho_memory_reflect`, `kumiho_memory_recall`, or `kumiho_memory_engage`.
3. Fix `src/onboard/wizard.rs` so the generated memory guidance aligns with the inventory's stated canonical recall path.
4. Add a repo-wide guard test or script-backed test that checks the same legacy-name surfaces the audit required, not only bootstrap/scaffold local strings.
5. Revisit i18n/doc sync after the replacement-name decision so translated docs do not preserve uncallable Kumiho tool names.
6. Justify the broad non-source/doc scope in the inventory or PR notes, especially Dockerfiles, dev scripts, `.github/labeler.yml`, `src/config/`, `src/providers/`, `src/skills/`, `src/sop/`, and `tool_descriptions/`.

## Required rescope (if FAIL-rescope)

N/A. The categorization framework is usable, but this implementation needs fixes before it closes the audit rows.

## Substantive challenge (required even on PASS)

The scrub assumes "canonical Kumiho replacement" means changing bare legacy names to plausible `kumiho_memory_*` names, but the runtime trust boundary is the actual connected registry, not the naming convention. The audit should require each replacement name to be backed by a registry-source assertion and a test that fails if the sidecar package no longer exports that tool. Otherwise future scrubs can pass by renaming stale tool names into different stale tool names.
