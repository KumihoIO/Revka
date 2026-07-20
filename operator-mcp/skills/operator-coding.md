# Operator Coding Skill

**Pattern:** Autonomous code-update loop — repo context → plan → edit → verify → commit → report.

Use this to update code end-to-end with Revka's own tools (`git_operations`,
`file_read`/`file_edit`/`file_write`, `glob_search`/`content_search`/
`semantic_code_search`, `shell`), or by delegating to a coding sub-agent
(`create_agent` with `agent_type: claude|codex`) for large tasks. The
dashboard renders a git-verified **Code changes** card for every turn that
touches the workspace, plus a repo badge (`repo:branch*`) — you do not need
to paste diffs into chat; summarize instead.

## When to Use

- "Fix/implement/refactor X in this repo" requests from any channel
- Post-review fix-up rounds on an existing branch
- Scheduled maintenance (dependency bumps, lint cleanups) via cron/workflows

## Loop Structure

```
1. Orient   — repo? branch? dirty? conventions? prior decisions?
2. Contract — restate the task as acceptance criteria
3. Edit     — smallest change that satisfies the contract
4. Verify   — build/lint/tests; on failure, fix and re-verify (max 3 reflections)
5. Commit   — one logical unit per commit, attributed; capture the why
6. Report   — summary + what was verified; escalate what needs approval
```

### 1. Orient

```
git_operations(operation="status")           # repo root, branch, dirty state
git_operations(operation="log", limit=5)     # commit-message conventions
```
- **Never work on a dirty tree you didn't dirty.** If there are pre-existing
  uncommitted changes, report them and ask (or stash only with approval).
- **Never commit directly to main/master.** Create a task branch first:
  `git_operations(operation="checkout", branch="fix/<slug>", create=true)`.
- Read the project's agent instructions before editing: `AGENTS.md`,
  `CLAUDE.md`, or harness-imported skills (via `read_skill`/Kumiho). They
  define build commands, risk tiers, and anti-patterns — follow them.
- **Read the why before you write.** For each file central to the task,
  ask Decision Memory what shaped it:
  `kumiho_code_why(file="src/gateway/ws.rs", question="why is X done this way?")`.
  It returns prior decisions with rationale, evidence, and whether they
  were later reversed (`superseded_by`). Do not re-litigate a decision
  the graph already explains — unless changing it *is* the task.

### 2. Contract

Restate the task as verifiable acceptance criteria *before* editing
(e.g. "test X passes", "clippy clean", "endpoint returns Y"). Weak criteria
("make it better") → ask one clarifying question, then proceed.
Constraints recalled via `kumiho_code_why` count as criteria too — a
change that violates a still-standing recorded decision needs explicit
user sign-off, not a silent override.

### 3. Edit

- Prefer `file_edit` (surgical, shows per-edit diffs in the dashboard) over
  whole-file `file_write`.
- Match the surrounding style; no drive-by refactors.
- For multi-file or long-running work, delegate to a coding sub-agent and
  monitor with `wait_for_agent` — its edits still land in the same
  workspace and the turn's Code changes card.

### 4. Verify

- Use the project's own commands (from AGENTS.md or the repo's CI config);
  fall back to the ecosystem default (`cargo check`/`npm test`/`pytest`).
- On failure: read the error, fix, re-run. **Max 3 fix-verify reflections**;
  if still failing, stop and report the failure honestly — do not commit
  broken code, do not weaken tests to pass.

### 5. Commit

```
git_operations(operation="add", files=[...])
git_operations(operation="commit", message="<type>(<scope>): <what/why>")
```
- Conventional commit style unless the repo's log says otherwise.
- One logical change per commit; never mix formatting-only churn with
  behavior changes.
- **Record the why after you commit.** If the commit embodies a real
  choice — an alternative picked over another, a default/policy set, a
  reversal, a measured trade-off — store it immediately:
  `kumiho_code_capture(decisions=[{title, decision, rationale,
  why_question, files, evidence}], repo_path="<workspace root>")`.
  Always pass `repo_path` (the absolute repo root from Orient) — the
  memory sidecar does not run inside your workspace, so the default
  `repo_path="."` silently misses the commit (`commits_seen: 0`). Fold
  Verify results in as `measurement` evidence and rejected approaches as
  `rejected_alternative` — that context is unrecoverable once the session
  ends. Skip mechanical edits (renames, lint fixes, version bumps).

### 6. Report

- Post a short summary: what changed (the card carries the diff), what was
  verified (paste the *result*, not the log), what remains.
- For multi-commit or architectural work, reflect the outcome
  (`kumiho_memory_reflect`) and decompose it into the typed graph —
  `kumiho_memory_decompose` on the kref reflect returned, with the
  entities/facts/relations you distilled — so future sessions reach this
  work through the systems it touched, not just keyword luck.
- **Push / PR / anything leaving the machine requires approval** under
  `supervised` autonomy — request it explicitly (`shell` with
  `git push` / `gh pr create`), never bypass. In `read_only` mode this
  entire skill is unavailable; say so instead of trying.

## Decision Memory

`kumiho_code_why`, `kumiho_code_capture`, and `kumiho_memory_decompose`
come from the Kumiho MCP sidecar; older sidecar builds lack them. **If
they are not in your tool catalog, skip those steps** — the loop above
works unchanged without them.

- `kumiho_code_why` is the deterministic, git-anchored lookup for *code*
  decisions. It complements `kumiho_memory_engage` (semantic recall):
  engage for task/user history, `code_why` for "why is this code the way
  it is?".
- Captures are summaries that leave the machine (Kumiho control plane).
  **Never put secrets, tokens, or personal data in capture content.**
  Patch text stays local — the diff card carries the *what*; Decision
  Memory carries the *why*.
- One well-evidenced capture per real decision beats many shallow ones.

## Guidelines

- **The diff card is the review surface.** Keep chat summaries to counts and
  intent ("3 files, +42 −7 on fix/auth-timeout; clippy + tests green").
- **Small steps converge.** Edit → verify per logical unit beats one big
  batch; each turn's card then reads as one reviewable step.
- **Escalate, don't improvise** on: force-push, history rewrites, deleting
  branches, touching CI/security config, or working outside the workspace.
- **Pair with other skills**: `operator-loop` for worker/verifier cycles on
  big tasks, `operator-committee` for design decisions before editing,
  `kumiho-sdk` for krefs and the low-level graph tool surface, workflows
  with `human_approval` steps for scheduled autonomous updates.
