# Google Agents CLI Demo Readiness

## 1. Summary

- **Purpose:** Provide a pre-recording readiness matrix for demos that show Construct agents using Google Agents CLI lifecycle commands.
- **Audience:** Operators, reviewers, and demo authors.
- **Scope:** The Construct `google_agents_cli` integration path in the Rust runtime and Operator MCP sidecar.
- **Non-goals:** This document does not set up Google Cloud projects, authenticate Google credentials, design the business demo story, or prove live deploy/eval/publish results.

## 2. Architecture Guardrail

`agents-cli` is a lifecycle CLI for Google ADK / Agent Platform work. It is not a Construct `agent_type`, session provider, or replacement for Claude/Codex.

For demo narration, use this model:

1. Construct starts or coordinates an existing coding agent such as `claude` or `codex`.
2. That agent calls the `google_agents_cli` tool when it needs `agents-cli run`, `eval`, `deploy`, `publish`, or `info`.
3. Construct executes the bounded tool call with argv tokens, workspace-bound cwd validation, timeout/output limits, and noninteractive defaults.

Avoid this model:

1. Do not say `agents-cli` is a third coding agent.
2. Do not configure `agent_type = "agents-cli"`.
3. Do not imply Construct injects MCP tools into `agents-cli run`.

## 3. Prerequisites

- Enable the Rust tool only when needed:

```toml
[google_agents_cli]
enabled = true
```

- Install and authenticate `agents-cli` outside Construct.
- Keep demo working directories under the configured Construct workspace.
- Treat `deploy`, `publish`, `infra`, and some `eval` flows as externally side-effecting Google operations.
- Prefer `agents-cli info` and `agents-cli login --status` for non-mutating environment checks.

## 4. Demo Outcome Matrix

| Outcome to show | Expected Construct behavior | Evidence to check before recording |
|---|---|---|
| Existing agent uses Google lifecycle tooling | Operator guidance tells users to spawn `claude`/`codex` and call `google_agents_cli`; tool schemas keep `create_agent.agent_type` limited to `claude` or `codex` | `operator-mcp/operator_mcp/operator_mcp.py`; `src/agent/operator/core.rs`; `src/gateway/ws.rs` |
| Current CLI project/tooling inspection | `agents-cli info` is accepted by both Rust and Operator MCP handlers | `google_agents_cli_accepts_current_info_command`; `test_google_agents_cli_accepts_info_command` |
| Prompt-only run | A prompt without `command` defaults to `agents-cli run`, and command previews redact the prompt as `["run", "..."]` | `test_google_agents_cli_prompt_defaults_to_run_and_redacts_preview` |
| Successful lifecycle command | Result reports success/completed status, exit code `0`, cwd, command preview, and stdout | `test_google_agents_cli_success_command` |
| CLI failure | Result reports failure, preserves exit code, stdout, stderr, and a concise error string | `test_google_agents_cli_failed_command_preserves_stdout_and_stderr` |
| Missing `agents-cli` binary | Operator MCP returns structured `agents_cli_missing` / `runtime_env_error`; Rust tool returns a direct missing-binary message | `test_google_agents_cli_missing_binary_returns_structured_error`; `src/tools/google_agents_cli.rs` |
| Malformed command input | Non-string command tokens, object command shapes, empty tokens, NUL bytes, unsupported commands, and whitespace-padded tokens are rejected before spawn | `test_google_agents_cli_rejects_non_string_command_tokens`; `test_google_agents_cli_rejects_whitespace_padded_command_tokens`; Rust `normalize_command` and `validate_command` tests |
| Interactive login attempt | `login --interactive`, `-i`, and bare `login` are blocked unless explicitly allowed; use `login --status` for demos | `test_google_agents_cli_rejects_interactive_login_by_default`; `google_agents_cli_rejects_interactive_login` |
| Bad working directory | Paths outside the workspace fail validation before `agents-cli` starts | `test_google_agents_cli_rejects_working_directory_outside_workspace`; `google_agents_cli_rejects_path_outside_workspace` |
| Timeout | Long-running commands are killed and return timeout status instead of hanging the demo | `test_google_agents_cli_timeout_returns_demo_safe_result`; Rust timeout branch in `src/tools/google_agents_cli.rs` |
| Large output | stdout is truncated with an explicit marker; Rust also truncates stderr without splitting UTF-8 | `test_google_agents_cli_truncates_large_output`; `google_agents_cli_truncates_stderr_without_splitting_utf8` |
| Spawn failure | OS-level spawn errors return structured `agents_cli_spawn_failed` / retryable runtime errors in Operator MCP | `test_google_agents_cli_spawn_error_returns_structured_error` |
| Gemini Enterprise publish context | `GEMINI_ENTERPRISE_APP_ID` is included in the safe env passthrough set when present | `google_agents_cli_safe_env_includes_enterprise_publish_id`; `operator-mcp/operator_mcp/tool_handlers/google_agents_cli.py` |
| Runtime safety policy | Rust tool respects read-only mode and rate limits before executing external actions | `google_agents_cli_blocks_readonly`; `google_agents_cli_blocks_rate_limited` |

## 5. Pre-Recording Validation

Run these checks after rebasing the demo branch onto current `origin/main`:

```bash
cargo fmt --all -- --check
cargo test --lib google_agents_cli -- --nocapture
python3 -m compileall -q operator-mcp/operator_mcp/tool_handlers/google_agents_cli.py operator-mcp/operator_mcp/operator_mcp.py
pytest operator-mcp/tests/test_google_agents_cli_tool.py operator-mcp/tests/test_google_agents_cli_demo_probe.py operator-mcp/tests/test_google_agents_cli_track2_evidence_gate.py operator-mcp/tests/test_google_agents_cli_pre_recording_gate.py -q
python3 scripts/demo/google_agents_cli_demo_probe.py --output /tmp/google_agents_cli_demo_probe.json
git diff --check
```

The demo probe uses a temporary fake `agents-cli` binary. It does not touch
Google Cloud, but it produces a JSON evidence bundle for local readiness
outcomes: source-level architecture guardrails, info, prompt-run redaction, eval
failure diagnostics, malformed input, interactive login blocking, workspace
escape blocking, timeout, truncation, Gemini Enterprise env passthrough, deploy
command acceptance, and missing-binary handling.

For the actual recording machine, also verify that the real installed
`agents-cli` supports the command surface used in the demo:

```bash
python3 scripts/demo/google_agents_cli_pre_recording_gate.py \
  --skip-track2-evidence \
  --require-real-agents-cli \
  --output /tmp/google_agents_cli_real_cli_gate.json
```

Add `--require-real-agents-cli-auth` for the final rehearsal if the recording
will show live Google Cloud deployment or publishing. That stricter mode fails
when `agents-cli login --status` does not report an authenticated session.

For PR-backed demos, also verify:

```bash
gh pr checks <PR_NUMBER> --repo KumihoIO/construct-os --watch --interval 30
gh pr view <PR_NUMBER> --repo KumihoIO/construct-os --json headRefOid,reviewDecision,mergeStateStatus,isDraft,state
```

Expected PR state before recording:

- CI and Quality Gate checks are green on the current head.
- `isDraft` is `false`.
- `reviewDecision: REVIEW_REQUIRED` is acceptable when human approval is the only remaining gate.
- There are no unresolved review threads.
- The local branch is clean and not behind `origin/main`.

## 6. Claims That Need Separate Demo Evidence

Do not treat the integration tests above as proof for these higher-level claims. Capture separate evidence before including them in the video.

| Claim | Evidence needed |
|---|---|
| Track 2 optimization improvement | Before/after eval scores, latency/cost/error-rate deltas, or a visible improvement in a repeatable scenario |
| Agent Simulation coverage | Synthetic edge-case scenario definitions and run output |
| Agent Observability debugging | Trace screenshots/logs showing stalled reasoning, tool calls, retries, or conflict resolution |
| Agent Optimizer refinement | The original instructions, optimized instructions, and measured behavior delta |
| Live Google Cloud deployment | Project ID, region, deploy command output, service URL or Agent Platform resource, and rollback plan |
| Mandatory Google platform technologies | Evidence that the demo uses Gemini or a third-party LLM through Agent Platform, ADK/LangChain/CrewAI orchestration, and Google Cloud infrastructure such as Agent Runtime, Cloud Run, or GKE |
| B2B value proposition | A concrete business workflow, user persona, inputs, actions taken, and measurable business outcome |

Use the Track 2 evidence gate to fail closed before recording if any of these
claims are missing concrete artifacts:

```bash
python3 scripts/demo/google_agents_cli_track2_evidence_gate.py \
  --evidence-dir .demo/google-agents-cli-track2 \
  --output /tmp/google_agents_cli_track2_evidence_gate.json
```

To scaffold the expected manifest and directory shape:

```bash
python3 scripts/demo/google_agents_cli_track2_evidence_gate.py \
  --evidence-dir .demo/google-agents-cli-track2 \
  --write-template
```

To write the artifact capture checklist that maps each claim to the files and
content the gate will require:

```bash
python3 scripts/demo/google_agents_cli_track2_evidence_gate.py \
  --evidence-dir .demo/google-agents-cli-track2 \
  --write-capture-plan
```

The gate also rejects placeholder-only evidence files such as `TODO` or
`evidence`, validates `.json` / `.jsonl` evidence as structured data, and
cross-checks core artifact content against the manifest: before/after metrics,
simulation scenario counts, observability trace IDs, optimizer deltas,
deployment project/region/resource text, rollback wording, mandatory Google
platform technologies, and B2B narrative specificity.

For the final pre-recording rehearsal, run the umbrella gate so local code
readiness, Track 2 evidence, and optional PR health are captured in one JSON
report:

```bash
python3 scripts/demo/google_agents_cli_pre_recording_gate.py \
  --evidence-dir .demo/google-agents-cli-track2 \
  --require-real-agents-cli-auth \
  --pr-number 324 \
  --output /tmp/google_agents_cli_pre_recording_gate.json
```

Use `--skip-track2-evidence` only for code-only smoke checks before live Agent
Platform evidence exists. Do not use that skip flag for final video readiness.
The umbrella report includes `strict_final_recording_ready`; treat it as the
final go/no-go field for recording. It remains `false` when Track 2 evidence is
skipped, real `agents-cli` authentication is not required, or any child gate
fails.

## 7. Related Docs

- [../reference/api/config-reference.md](../reference/api/config-reference.md) - `[google_agents_cli]` configuration.
- [./operations-runbook.md](./operations-runbook.md) - day-2 runtime operations.
- [./troubleshooting.md](./troubleshooting.md) - failure signatures and recovery.
- [../contributing/pr-workflow.md](../contributing/pr-workflow.md) - PR readiness expectations.

## 8. Maintenance Notes

- **Owner:** Operator and tool integration maintainers.
- **Update trigger:** Update when `agents-cli` command names, tool validation behavior, Operator MCP result fields, or Google Agent Platform demo requirements change.
- **Last reviewed:** 2026-06-01.
