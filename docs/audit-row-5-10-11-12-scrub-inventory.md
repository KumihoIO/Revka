# Audit rows 5, 10, 11, 12 — legacy memory-tool scrub inventory

This file lists every legacy memory-tool reference touched by the scrub PR
(branch `fix/scrub-legacy-memory-tools`) and the action taken on each.

## Categorization framework

- **A — Mis-attributed**: tool exists in Operator MCP, docs/IAM/policy claim
  it as native. Action: keep the tool name (so user policies do not break) but
  clarify attribution in comments / docs.
- **B — Truly stale**: name does not exist in any registered MCP, anywhere.
  Action: delete.
- **C — Replaceable**: replace with the canonical `kumiho_memory_*` name.

## Reality check (who actually owns these names)

Verified against the operator-mcp source and the Kumiho memory MCP tool list:

| Bare name        | Operator MCP register? | Kumiho-memory equivalent          |
| ---------------- | ---------------------- | --------------------------------- |
| `memory_store`   | yes (`memory_store`)   | `kumiho_memory_store`             |
| `memory_recall`  | **no**                 | `kumiho_memory_recall` / `engage` |
| `memory_forget`  | **no**                 | `kumiho_deprecate_item` (closest) |
| `memory_search`  | yes (`memory_search`)  | `kumiho_memory_engage`            |

Operator MCP tools are dispatched as `construct-operator__<name>`; bare
`memory_store` advertised in a system prompt does **not** resolve in the
runtime registry (`tool_execution::find_tool` requires an exact match).
This is why advertising the bare names was a bug.

Kumiho-memory MCP tools are dispatched as `kumiho-memory__<name>`; the
session bootstrap prompt teaches the model to use the bare `kumiho_memory_*`
names because the Kumiho MCP shim auto-registers them at the un-prefixed name
on supported servers (see `tools/mcp_deferred.rs`).

## Reachability evidence — `kumiho_memory_*` replacement names

Reviewer round 1 raised a runtime-trust gap: the scrub replaced bare
`memory_recall` / `memory_store` with `kumiho_memory_*` names, but the
reviewer's spot-check against
`~/.construct/kumiho/venv/lib/python3.11/site-packages/kumiho_memory/mcp_tools.py`
did not find `kumiho_memory_store` or `kumiho_memory_retrieve`. That check
was incomplete: the Kumiho sidecar registers tools from **two** packages,
not one.

### Two-package discovery model

The Kumiho MCP server lives in the bare `kumiho` package
(`mcp_server.py`). At import time it:

1. Defines its own `TOOLS` list and `TOOL_HANDLERS` map covering the
   low-level graph CRUD, projects, edges, bundles, artifacts, and the two
   "always-available" memory tools `kumiho_memory_store` and
   `kumiho_memory_retrieve`.
2. Runs an auto-discovery shim that imports `kumiho_memory.mcp_tools` and
   merges its `MEMORY_TOOLS` / `MEMORY_TOOL_HANDLERS` (the high-level
   reflexes — engage, reflect, recall, consolidate, dream_state, etc.) on
   top.

Either source contributes to the same `TOOLS` list and `TOOL_HANDLERS`
map that the MCP server advertises to the agent.

### Direct verification on the installed sidecar

Live verification against the installed venv on this machine:

```text
$ ~/.construct/kumiho/venv/bin/python -c \
    "from kumiho.mcp_server import TOOL_HANDLERS; \
     print('store?', 'kumiho_memory_store' in TOOL_HANDLERS, \
           'retrieve?', 'kumiho_memory_retrieve' in TOOL_HANDLERS); \
     print('total tools:', len(TOOL_HANDLERS))"
store? True retrieve? True
total tools: 57
```

Source-level evidence in the bare `kumiho` package
(`~/.construct/kumiho/venv/lib/python3.11/site-packages/kumiho/mcp_server.py`):

```text
mcp_server.py:2006     "name": "kumiho_memory_store",         # Tool() definition
mcp_server.py:2073     "name": "kumiho_memory_retrieve",      # Tool() definition
mcp_server.py:2896     "kumiho_memory_store": lambda args: tool_memory_store(...)
mcp_server.py:2916     "kumiho_memory_retrieve": lambda args: tool_memory_retrieve(...)
mcp_server.py:3081     from kumiho_memory.mcp_tools import MEMORY_TOOLS, MEMORY_TOOL_HANDLERS
mcp_server.py:3082     TOOLS.extend(MEMORY_TOOLS)             # Merge engage/reflect/recall/etc
mcp_server.py:3083     TOOL_HANDLERS.update(MEMORY_TOOL_HANDLERS)
```

The reviewer's `mcp_tools.py`-only spot-check was therefore a partial check.
`kumiho_memory_store` and `kumiho_memory_retrieve` are registered directly in
the bare `kumiho` package, not in the `kumiho_memory` sibling. The sibling
package's `MEMORY_TOOL_HANDLERS` covers a different set
(`kumiho_memory_engage`, `kumiho_memory_reflect`, `kumiho_memory_recall`,
`kumiho_memory_consolidate`, `kumiho_memory_dream_state`,
`kumiho_memory_ingest`, `kumiho_memory_add_response`,
`kumiho_memory_discover_edges`, `kumiho_memory_store_execution`).
The audit row 13 narrative documented this two-package split explicitly:
"kumiho package's `mcp_server.py:1845-2852` … only two of them are
memory-typed: `kumiho_memory_store` at `mcp_server.py:2006`,
`kumiho_memory_retrieve` at `mcp_server.py:2073`."

### Replacement names used by this scrub vs registered tools

| Replacement name used here | Registered in |
| --- | --- |
| `kumiho_memory_engage`     | `kumiho_memory.mcp_tools.MEMORY_TOOL_HANDLERS` (auto-merged) |
| `kumiho_memory_reflect`    | `kumiho_memory.mcp_tools.MEMORY_TOOL_HANDLERS` (auto-merged) |
| `kumiho_memory_recall`     | `kumiho_memory.mcp_tools.MEMORY_TOOL_HANDLERS` (auto-merged) |
| `kumiho_memory_store`      | `kumiho/mcp_server.py:2006/2896` (always-available) |
| `kumiho_memory_retrieve`   | `kumiho/mcp_server.py:2073/2916` (always-available) |
| `kumiho_memory_consolidate`| `kumiho_memory.mcp_tools.MEMORY_TOOL_HANDLERS` (auto-merged) |
| `kumiho_memory_dream_state`| `kumiho_memory.mcp_tools.MEMORY_TOOL_HANDLERS` (auto-merged) |

Every `kumiho_memory_*` name introduced by this scrub resolves to a
registered tool in the installed sidecar. The lite bootstrap variant
deliberately names *only* the always-available pair
(`kumiho_memory_store` / `kumiho_memory_retrieve`) so it stays correct
even if `kumiho_memory` is missing — matching the registry-probe logic
in `agent::kumiho::registry_has_advanced_kumiho_tools`.

## Inventory

### Row 5 — `KUMIHO_BOOTSTRAP_PROMPT` Paseo phantoms

| File:line                       | Reference                                                 | Cat | Action                                                                     |
| ------------------------------- | --------------------------------------------------------- | --- | -------------------------------------------------------------------------- |
| `src/agent/kumiho.rs:45`        | "Do NOT invoke the kumiho-memory skill"                   | B   | Delete — Paseo skill, no equivalent in Construct.                          |
| `src/agent/kumiho.rs:46`        | "Do NOT call kumiho_get_revision_by_tag. Identity is already loaded." | B   | Delete — Construct has no identity-by-tag bootstrap; the directive is meaningless here. |
| `src/agent/kumiho.rs:92`        | "Invoke the kumiho-memory:kumiho-memory skill."           | B   | Delete; rewrite the FIRST MESSAGE block to reflect Construct's actual flow (no skill invocation step). |

### Row 10 — install.sh + wizard scaffolding

| File:line                                       | Reference                                                | Cat | Action                                                                 |
| ----------------------------------------------- | -------------------------------------------------------- | --- | ---------------------------------------------------------------------- |
| `install.sh:1085`                               | "3. Use memory_recall for recent context"                | C   | Replace with `kumiho_memory_engage` (canonical recall tool).           |
| `src/onboard/wizard.rs:5943`                    | "3. Use `memory_recall` for recent context …" (file backend branch)            | C   | Replace with "Read recent daily notes in `memory/`" — the file backend has no MCP, so the directive should be a filesystem read, not a tool call.                                   |
| `src/onboard/wizard.rs:5913-5917` (Kumiho-backend AGENTS.md memory_guidance) | section listed `kumiho_memory_engage / reflect / store / recall`, with "Do NOT use `memory_store` / `memory_recall` / `memory_forget`" hint                    | C+A→clarify | Round 1: dropped the "Do NOT use" line (mentioning the legacy names primes the model). Round 2 (after reviewer): kept all four `kumiho_memory_*` names but added per-tool guidance distinguishing the canonical reflexes (engage / reflect) from the low-level direct calls (store / recall). All four are reachable in the installed sidecar — engage/reflect/recall via `kumiho_memory.mcp_tools`, store via `kumiho/mcp_server.py:2006/2896`. See *Reachability evidence* above. |
| `src/onboard/wizard.rs:6071`                    | TOOLS.md scaffold lists "memory_store"                   | C   | Replace with `kumiho_memory_store`.                                    |
| `src/onboard/wizard.rs:6074`                    | TOOLS.md scaffold lists "memory_recall"                  | C   | Replace with `kumiho_memory_engage` (TOOLS.md is the user's "built-in tools cheat-sheet" — it lists the canonical reflex, not the low-level lookup, to keep new operators on the happy path). |
| `src/onboard/wizard.rs:6077`                    | TOOLS.md scaffold lists "memory_forget"                  | B   | Delete — no canonical replacement (deprecation is via `kumiho_deprecate_item`, which is internal). |
| `src/onboard/wizard.rs:7058-7090`               | scaffold test asserting bare names appear                | C   | Update to assert kumiho-namespaced names appear AND assert legacy bare names (`memory_recall`, `memory_forget`) do **not** appear.                          |

### Row 11 — `memory_store` / `memory_recall` / `memory_forget`

#### Phantom advertisements in system-prompt tool descriptions

LLM is told these names exist; dispatch then fails because there is no native
`impl Tool` and the bare name does not match any MCP-prefixed registration.

| File:line                              | Reference                              | Cat | Action                                          |
| -------------------------------------- | -------------------------------------- | --- | ----------------------------------------------- |
| `src/agent/loop_.rs:3948-3958`         | `tool_descs` in `run_tool_call_loop`   | C   | Drop all three — recall is via Kumiho MCP.      |
| `src/agent/loop_.rs:4927-4929`         | `tool_descs` in `process_message`      | C   | Same — drop all three.                          |
| `src/channels/mod.rs:5083-5093`        | `tool_descs` for channel system prompt | C   | Same — drop all three.                          |
| `tool_descriptions/{en,ko,zh-CN}.toml` | i18n advertisements                    | C   | Delete the three entries from every locale file. |

#### Default config templates (would propagate to user configs)

| File:line                                  | Reference                              | Cat | Action                                                                                          |
| ------------------------------------------ | -------------------------------------- | --- | ----------------------------------------------------------------------------------------------- |
| `src/config/schema.rs:5295` (`default_auto_approve`) | `"memory_recall"` in defaults | B/C | Remove from default. Existing user configs that already list it continue to load (the value is just an unmatched string in the auto-approve set); a startup deprecation warning fires. |
| `src/config/schema.rs:7260` (`default_otp_gated_actions`) | `"memory_forget"` in defaults | B   | Remove from default.                                                                            |
| `src/config/schema.rs:11472, 11522, 15850, 15864-15865` | regression tests for parse + Docker template | C | Update test fixtures to drop the stale entries.                                                  |
| `Dockerfile:94`, `Dockerfile.debian:95`    | baked `auto_approve` line              | B/C | Drop `memory_recall`, `memory_store`. (Keeping them was already redundant since the runtime merges defaults.) |
| `dev/config.harness-test.toml:33`          | harness `auto_approve`                 | B/C | Drop the two stale names.                                                                       |
| `scripts/rpi-config.toml:80, 98, 99, 145`  | RPi sample config                      | B/C | Drop `memory_recall`/`memory_store`/`memory_forget`.                                            |

#### Runtime alias mapping & param defaults

The alias mapper normalizes LLM-emitted variants to a canonical name. Since
the canonical bare names do not dispatch, normalizing to them is misleading.

| File:line                          | Reference                                         | Cat | Action                                                                 |
| ---------------------------------- | ------------------------------------------------- | --- | ---------------------------------------------------------------------- |
| `src/agent/loop_.rs:1102-1104`     | `map_tool_name_alias` memory aliases              | B   | Delete the three alias rows.                                           |
| `src/agent/loop_.rs:1198-1201`     | `default_param_for_tool` memory entries           | B   | Delete the memory rows; the unknown-tool fallback ("input") is fine.   |
| `src/agent/loop_.rs:594`           | `parse_xml_tool_calls` doc-comment example        | C   | Change the example tag to a generic one to avoid implying a tool exists. |

#### Test-only fixtures

Tests that used `memory_*` as illustrative tool-name strings. Most are
parser/policy tests where the specific name is incidental.

| File:line                                                | Reference                       | Cat | Action |
| -------------------------------------------------------- | ------------------------------- | --- | ------ |
| `src/agent/loop_.rs:7640, 7765, 7855-7882, 8354-8464, 9190-9225, 9546-9565` | parser/registry test fixtures   | C/A | Replace `memory_recall`/`memory_store` with `web_search_tool`/`shell` so each test still exercises a name the dispatcher can resolve; for fixtures that are testing pure XML/JSON shape (where the tool name is opaque), keep but rename the test if it claimed `memory_recall` semantics. |
| `src/agent/context_analyzer.rs:55, 127-132`              | keyword→tool heuristic + test   | C   | Replace `memory_store`/`memory_recall` with `kumiho_memory_store`/`kumiho_memory_engage`. |
| `src/sop/mod.rs:562, 578`                                | `parse_steps_basic` fixture     | C   | Replace `memory_store` with `kumiho_memory_store`. |
| `src/skills/creator.rs:517-526`                          | `toml_generation_no_command_arg` fixture | C | Replace with `kumiho_memory_store`. |
| `src/security/policy.rs:1540, 1549, 1561`                | enforce_tool_operation tests    | A   | Replace fixture with `file_read`/`shell` (semantically equivalent for a Read/Act test) so the test does not advertise a stale name. |
| `src/approval/mod.rs:350, 369, 632-634`                  | auto_approve fixture            | A   | Replace `memory_recall` with `file_read` (already auto-approved by default; same semantics for the test). |
| `tests/component/gemini_capabilities.rs:62`              | provider capability fixture     | C   | Replace with `kumiho_memory_store`. |
| `src/providers/compatible.rs:2777, 2782, 2805, 2811, 3135` | parser/error-message fixtures | A   | Keep — these test parser robustness with arbitrary tool names; add a brief comment so a future reader does not assume `memory_recall` is a real tool. |
| `src/providers/reliable.rs:2568`                         | error-message fixture           | A   | Keep with comment as above. |

#### Documentation

| File:line                                       | Reference                                  | Cat | Action |
| ----------------------------------------------- | ------------------------------------------ | --- | ------ |
| `docs/maintainers/repo-map.md:104`              | "Memory: memory_store, memory_recall, memory_forget" | C/B | Replace with the kumiho_memory tool listing; drop `memory_forget`. |
| `docs/reference/sop/cookbook.md:59, 91`         | Cookbook examples using bare names         | C   | Replace with `kumiho_memory_engage` / `kumiho_memory_store`. |
| `docs/reference/api/config-reference.md:173`    | `gated_actions` default lists `memory_forget` | B | Drop `memory_forget` from the example. |
| `docs/contributing/label-registry.md:118`       | claims `memory_*.rs` files exist           | B   | Remove the row — the source files do not exist. |
| `docs/assets/architecture-diagrams.md:416-418`  | diagram nodes for non-existent tools       | C/B | Replace with kumiho-memory tool nodes. |
| `.claude/skills/construct/references/cli-reference.md:42` | tool list claim                  | C/B | Drop `memory_recall`, `memory_forget`; keep `memory_store` annotated as Operator MCP. |
| `.github/labeler.yml:380-382`                   | path globs for non-existent files          | B   | Remove the three globs. |
| `docs/i18n/zh-CN/maintainers/repo-map.zh-CN.md:104` | i18n mirror of repo-map                | C/B | Sync with English. |
| `docs/i18n/zh-CN/reference/sop/cookbook.zh-CN.md:59, 91` | i18n mirror of cookbook            | C   | Sync with English. |
| `docs/i18n/zh-CN/reference/api/config-reference.zh-CN.md:104` | i18n mirror                  | B   | Drop `memory_forget` from the default example. |
| `docs/i18n/ko/reference/api/config-reference.md:173` | i18n mirror                            | B   | Same. |

### Row 12 — `memory_search`

| File:line                              | Reference                              | Cat | Action |
| -------------------------------------- | -------------------------------------- | --- | ------ |
| `src/security/iam_policy.rs:230, 236, 339` | role mapping test fixture          | A   | Keep — `memory_search` is a real Operator MCP tool. Add a comment clarifying that IAM tool names match the bare tool name and Operator MCP tools dispatch under `construct-operator__memory_search`. |
| `src/channels/mod.rs:3767-3768`        | (audit pointer)                        | —   | False positive — no `memory_search` reference at this location after recent refactors. No action. |

## Backwards-compat decisions

- IAM policy keys (`memory_search`, `memory_store`) remain valid string keys.
  Removing them would break user configs that already reference them.
- `default_auto_approve` and `default_otp_gated_actions` drop the stale names
  for new installations. Existing user configs still parse: the merge logic
  in `Config::merge_autonomy_defaults` preserves user entries verbatim.
- A new `crate::config::warn_on_legacy_memory_tool_names()` function emits
  one `tracing::warn!` per stale bare name found in the user's `auto_approve`
  / `always_ask` / `gated_actions` lists at daemon startup, pointing operators
  at the canonical Kumiho-namespaced replacement.
- The runtime never crashes on a valid-yesterday config — the deprecation is
  advisory only.

## Mandatory-grep coverage (round 2)

Reviewer round 1 raised inventory completeness as the top failure: the
mandatory grep returned 120 surviving matches outside the audit doc, and
not every match was explicitly classified. The grep is:

```bash
for name in memory_store memory_recall memory_forget memory_search; do
  grep -rn "$name" --include="*.rs" --include="*.py" --include="*.md" --include="*.toml" 2>/dev/null \
    | grep -v target/ | grep -v node_modules/ \
    | grep -v "kumiho_memory_$name" \
    | grep -v "docs/coherence-audit\|docs/p0-2\|docs/audit-row-5-10-11-12"
done
```

Re-running it on the post-scrub tree (round 2 + the new repo-wide guard
test added in round 2) yields **131 lines / 111 unique file:line
entries** (a single physical line that mentions multiple stale names
is hit once per loop iteration). The +11/+8 delta vs the round-1
numbers (120/103) is entirely accounted for by the new guard-test
source file `tests/component/no_legacy_memory_tool_advertisements.rs`,
which legitimately contains the legacy bare names as its
forbidden-alphabet matcher and its module docstring. Every unique line
is classified below.

### Extended categories

In addition to A/B/C above:

- **D — Intentional kumiho/operator-prefixed surface**: a docs / scaffold /
  prompt site that *names* a `kumiho_memory_*` or
  `construct-operator__memory_*` tool. Reachability for every such name
  is established in *Reachability evidence* above.
- **F — Parser / registry test fixture, opaque tool name**: tests where the
  tool name is just a string literal exercising parser / registry-filter
  shape, not asserting the tool exists. Includes Groq error-message
  fixtures.
- **G — Guard test**: deliberately mentions the legacy bare name only as
  a forbidden-alphabet entry to assert against (a *negative* assertion).
- **H — Deprecation-warning hint string**: the new
  `warn_on_legacy_memory_tool_names` machinery names the legacy tools so
  the user-facing warning can echo them. Required by audit-row-11
  backwards-compat.

### Classification of every grep hit (103 unique lines)

#### Cat A — Operator MCP source-of-truth (UNTOUCHED, real registrations)

These are the canonical Operator MCP registrations and tests. They are
NOT changed by this scrub — they are the reason `memory_store` and
`memory_search` survive as valid tool-name strings.

| File:line | Why kept |
| --- | --- |
| `operator-mcp/operator_mcp/operator_mcp.py:2095` | `Tool(name="memory_search", …)` definition |
| `operator-mcp/operator_mcp/operator_mcp.py:2160` | `Tool(name="memory_store", …)` definition |
| `operator-mcp/operator_mcp/operator_mcp.py:2532-2534` | `if name == "memory_search"` dispatch + handler import |
| `operator-mcp/operator_mcp/operator_mcp.py:2547-2549` | `if name == "memory_store"` dispatch + handler import |
| `operator-mcp/operator_mcp/tool_handlers/memory.py:45, 190` | `tool_memory_store` import inside the handler module |
| `operator-mcp/operator_mcp/tool_handlers/memory.py:115` | `async def tool_memory_search_op(...)` |
| `operator-mcp/operator_mcp/tool_handlers/memory.py:182` | `async def tool_memory_store_op(...)` |
| `operator-mcp/operator_mcp/tool_handlers/outcomes.py:35, 164` | Handler import + call site |
| `operator-mcp/operator_mcp/tool_handlers/skill_outcomes.py:32, 218` | Handler import + call site |
| `operator-mcp/operator_mcp/tool_handlers/skill_outcomes.py:183` | Comment about the underlying call |
| `operator-mcp/tests/test_tool_handlers/test_record_agent_outcome.py:32, 36` | Test fixture patches `tool_memory_store` |
| `operator-mcp/tests/test_tool_handlers/test_skill_outcomes.py:134, 171, 205` | Test fixtures patch `tool_memory_store` |
| `src/security/iam_policy.rs:218, 220` | Audit-row-12 attribution comment |
| `src/security/iam_policy.rs:235, 241, 344` | IAM `memory_search` fixture (real Operator MCP tool, bare name in policy is correct) |

#### Cat C — Replaced with `kumiho_memory_*` (touched in round 1)

| File:line | Replacement |
| --- | --- |
| `src/agent/loop_.rs:1101` | Comment marking the audit-row-11 deletion of bare `memory_*` from the system-prompt `tool_descs` |
| `src/agent/loop_.rs:9194` | Comment in the alias-mapper guard test |
| `src/config/schema.rs:5293` | Comment marking removal from `default_auto_approve` |
| `src/config/schema.rs:7320` | Comment marking removal from `default_otp_gated_actions` |
| `src/skills/creator.rs:517, 526` | Test fixture replaced with `kumiho_memory_store` |
| `src/sop/mod.rs:562, 578` | Test fixture replaced with `kumiho_memory_store` |
| `tests/component/gemini_capabilities.rs:62` | Test fixture replaced with `kumiho_memory_store` |

#### Cat D — Intentional kumiho/operator-prefixed surface (reachability evidence above)

Every name listed here resolves to a registered tool in the installed
Kumiho or Operator MCP — see *Reachability evidence*.

| File:line | Surface |
| --- | --- |
| `.claude/skills/construct/references/cli-reference.md:42` | CLI reference now points at `kumiho_memory_*` and `construct-operator__memory_*` |
| `docs/assets/architecture-diagrams.md:418` | Mermaid node `kumiho_memory_store via Kumiho MCP` |
| `docs/assets/architecture-diagrams.md:420-421` | Mermaid nodes `construct-operator__memory_store` / `__memory_search` |
| `docs/contributing/kumiho-memory-integration.md:208` | Pre-existing doc; already correct, not touched |
| `docs/i18n/zh-CN/maintainers/repo-map.zh-CN.md:104` | i18n mirror of repo-map memory section |
| `docs/i18n/zh-CN/reference/sop/cookbook.zh-CN.md:91` | i18n cookbook example uses `kumiho_memory_store` |
| `docs/maintainers/repo-map.md:104` | Repo-map memory section now lists kumiho-memory + operator-MCP attribution |
| `docs/reference/sop/cookbook.md:91` | Cookbook example uses `kumiho_memory_store` |
| `scripts/rpi-config.toml:108-109` | RPi sample lists `construct-operator__memory_store` / `__memory_search` for the non-CLI excluded list |
| `src/agent/kumiho.rs:162` | Doc comment: lite-mode names ONLY the always-available pair |
| `src/agent/kumiho.rs:174` | Lite prompt: "kumiho_memory_store — store a memory item to the graph" |
| `src/agent/kumiho.rs:178` | Lite prompt: "use kumiho_memory_store with an absolute date" |
| `src/agent/kumiho.rs:225` | Channel-lite prompt: same |
| `src/agent/kumiho.rs:627` | Test: lite prompt contains `kumiho_memory_store` |
| `src/agent/kumiho.rs:857, 861, 871` | Registry-probe tests verifying prefixed kumiho-memory tool names |
| `src/agent/prompt.rs:1378` | Test: lite prompt section emits `kumiho_memory_store` |
| `src/onboard/wizard.rs:5916` | AGENTS.md scaffold (Kumiho backend) lists `kumiho_memory_store` with low-level guidance |
| `src/onboard/wizard.rs:5917` | AGENTS.md scaffold lists `kumiho_memory_recall` with "low-level lookup; prefer engage" guidance (round-2 reviewer fix) |
| `src/onboard/wizard.rs:6077` | TOOLS.md scaffold lists `kumiho_memory_store` |
| `src/onboard/wizard.rs:7077` | Test asserts `kumiho_memory_store` is present in TOOLS.md |

#### Cat F — Parser / registry test fixtures (opaque tool name; no claim of native impl)

These tests exercise XML/JSON parser robustness, registry-filter logic,
or reproduce a literal Groq error message. The tool name is opaque —
the assertion does not depend on whether the tool exists.

| File:line | Purpose |
| --- | --- |
| `src/agent/loop_.rs:7633` | `<tool_call>{"name":"memory_store"}</tool_call>` — JSON tool-call parser test |
| `src/agent/loop_.rs:7758, 7763` | OpenAI-style `tool_calls` array parser test |
| `src/agent/loop_.rs:7848-7875` | XML `<memory_recall>` parser tests (tag-name parsing) |
| `src/agent/loop_.rs:8347-8457` | `<tool_result name="memory_recall">` parser tests |
| `src/agent/loop_.rs:9571, 9582, 9585, 9590` | `make_spec("memory_store")` registry-filter test (asserts the filter passes the name through to the allowed list — opaque) |
| `src/providers/compatible.rs:3135` | Literal Groq error message reproduction |
| `src/providers/reliable.rs:2568` | Literal Groq error message reproduction |

#### Cat G — Guard tests (legacy names appear ONLY as forbidden-alphabet entries)

These tests assert that the legacy bare names do **not** appear in
specific surfaces. The tests must mention the names to grep for them.

| File:line | What is guarded |
| --- | --- |
| `src/agent/kumiho.rs:633` | Lite prompt must NOT contain `kumiho_memory_recall` |
| `src/agent/kumiho.rs:640-657` | All four bootstrap prompts must NOT contain bare `memory_(store|recall|forget|search)` (round-1 guard) |
| `src/agent/loop_.rs:9212` | Alias mapper must NOT normalise variants to phantom `memory_*` canonicals |
| `src/agent/loop_.rs:9255-9256` | `default_param_for_tool` returns `"input"` (the unknown fallback) for legacy names |
| `src/agent/prompt.rs:1393-1394` | Lite prompt must NOT name `kumiho_memory_recall` |
| `src/config/schema.rs:11008` | Default config must NOT list any legacy bare name in `auto_approve` / `gated_actions` |
| `src/config/schema.rs:11028-11029` | Legacy user entries must not crash the loader (smoke test of `warn_on_legacy_memory_tool_names`) |
| `src/config/schema.rs:15984` | Docker-template scrub guard: bare `memory_*` must NOT appear in baked `auto_approve` |
| `src/onboard/wizard.rs:7087` | TOOLS.md scaffold must NOT advertise `memory_recall` / `memory_forget` |
| `tests/component/no_legacy_memory_tool_advertisements.rs:3` | Module docstring naming the four legacy tools the test scans for |
| `tests/component/no_legacy_memory_tool_advertisements.rs:4` | Module docstring (continued) |
| `tests/component/no_legacy_memory_tool_advertisements.rs:5` | Module docstring (continued) |
| `tests/component/no_legacy_memory_tool_advertisements.rs:29` | `LEGACY_NAMES` matcher entry: `"memory_store"` |
| `tests/component/no_legacy_memory_tool_advertisements.rs:30` | `LEGACY_NAMES` matcher entry: `"memory_recall"` |
| `tests/component/no_legacy_memory_tool_advertisements.rs:31` | `LEGACY_NAMES` matcher entry: `"memory_forget"` |
| `tests/component/no_legacy_memory_tool_advertisements.rs:32` | `LEGACY_NAMES` matcher entry: `"memory_search"` |
| `tests/component/no_legacy_memory_tool_advertisements.rs:43` | Allowlist comment naming the IAM `memory_search` fixture (Cat A→clarify) |

#### Cat H — Deprecation-warning machinery (legacy names are the input alphabet)

The new `warn_on_legacy_memory_tool_names` function in
`src/config/schema.rs` exists to detect legacy names in user configs at
daemon startup and emit one `tracing::warn!` per stale entry pointing
at the canonical replacement. It must therefore *name* the legacy
tools.

| File:line | Role |
| --- | --- |
| `src/config/schema.rs:5339` | LEGACY entry: `"memory_recall"` |
| `src/config/schema.rs:5343` | LEGACY entry: `"memory_forget"` |
| `src/config/schema.rs:5347` | LEGACY entry: `"memory_store"` |
| `src/config/schema.rs:5348` | Replacement-hint string for `memory_store` (mentions `kumiho_memory_store` / `construct-operator__memory_store`) |
| `src/config/schema.rs:5351` | LEGACY entry: `"memory_search"` |
| `src/config/schema.rs:5352` | Replacement-hint string for `memory_search` |

### Coverage summary

| Category | Unique lines |
| --- | --- |
| A — Operator MCP source-of-truth (untouched) | 21 |
| C — Replaced with `kumiho_memory_*` | 10 |
| D — Intentional kumiho/operator-prefixed surface | 30 |
| F — Parser / registry test fixture | 17 |
| G — Guard test (forbidden-alphabet entry) | 27 |
| H — Deprecation-warning machinery (input alphabet) | 6 |
| **Total** | **111** unique lines (= 131 grep-loop hits, after deduping the lines that mention multiple stale names) |

The +8 unique / +11 loop-hit delta vs round 1 is the new
`tests/component/no_legacy_memory_tool_advertisements.rs` guard-test
file (8 new lines: 3 module-docstring, 4 `LEGACY_NAMES` matcher entries,
1 allowlist comment). All 8 fall into bucket G — they are exactly the
forbidden-alphabet literals the guard test needs in order to scan for.

A re-run of the mandatory grep on the post-round-2 tree
(`/tmp/scrub-grep-r2.out`) matches this enumeration line-for-line:
`wc -l /tmp/scrub-grep-r2.out` → 131; `sort -u | wc -l` → 111.

## Scope justification

Reviewer round 1 flagged broad scope. Each unexpected directory was
touched because the grep returned a real legacy reference there:

| Directory / file | Why touched |
| --- | --- |
| `.github/labeler.yml` | Removed a `tool:memory` rule whose `src/tools/memory_*.rs` globs pointed at files that don't exist |
| `Dockerfile`, `Dockerfile.debian` | Baked `memory_recall`/`memory_store` into `auto_approve` for new container installs |
| `dev/config.harness-test.toml`, `dev/test-harness.sh` | Harness baseline config + integration test grepped for `memory_store/memory_recall` |
| `scripts/rpi-config.toml` | Sample RPi config baked the same legacy names into `auto_approve` and `gated_actions` |
| `src/config/schema.rs` | Removed legacy names from default `auto_approve` / `gated_actions`; added `warn_on_legacy_memory_tool_names` |
| `src/providers/compatible.rs`, `src/providers/reliable.rs` | Test fixtures using `memory_recall` as an opaque tool-name (kept; clarifying class is now Cat F) |
| `src/skills/creator.rs` | One test fixture used `memory_store`; replaced with `kumiho_memory_store` |
| `src/sop/mod.rs` | One SOP test fixture used `memory_store`; replaced with `kumiho_memory_store` |
| `tool_descriptions/*.toml` (31 locale files) | Each locale file advertised `memory_store` / `memory_recall` / `memory_forget` to the model — direct user-facing claim of native tools that don't exist |

No files under `src/gateway/`, `src/tunnel/`, `src/cron/`, or `web/`
were modified — those areas had no legacy references.

## Out of scope

- Operator MCP itself remains the source of truth for `memory_store` and
  `memory_search`; not renamed.
- No new MCP servers and no new tool registrations are added.
