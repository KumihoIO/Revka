# Operator Coding Harness

The Operator dashboard chat carries first-class coding telemetry: a
**workspace badge** showing which git repository the session works in, and a
git-verified **Code changes card** summarizing what each turn actually
changed on disk. Together with the `operator-coding` skill they form the
harness that lets Revka update code autonomously while keeping every change
one glance away.

## Workspace badge

When the configured `workspace_dir` is inside a git repository, the gateway
sends a `workspace_context` WebSocket message on session start and after
every turn:

```json
{
  "type": "workspace_context",
  "workspace": {
    "root": "/abs/path/to/repo",
    "repo": "Revka",
    "branch": "fix/auth-timeout",
    "head": "984d796",
    "dirty_files": 3
  }
}
```

The dashboard renders it in the chat status strip as `Revka:fix/auth-timeout*`
(`*` = dirty working tree; tooltip shows the root path and HEAD). Outside a
git repository the message is absent and the badge is hidden.

## Code changes card

Before each turn the gateway snapshots the workspace's git state (HEAD plus
per-file working-tree patch hashes). When the turn finishes — including
stopped, timed-out, and failed turns — it diffs the workspace against that
snapshot and, if anything changed, sends:

```json
{
  "type": "code_changes",
  "changes": {
    "repo": "Revka",
    "branch": "fix/auth-timeout",
    "head_before": "984d7967…",
    "head_after": "a1b2c3d4…",
    "committed": true,
    "files": [
      {
        "path": "src/gateway/ws.rs",
        "status": "modified",
        "insertions": 42,
        "deletions": 7,
        "patch": "diff --git a/src/gateway/ws.rs …",
        "truncated": false
      }
    ],
    "total_insertions": 42,
    "total_deletions": 7,
    "truncated": false
  }
}
```

Properties worth knowing:

- **Tool-agnostic.** The summary comes from git, so it captures edits made
  through `file_edit`/`file_write`, `shell`, and delegated coding CLIs alike.
- **Turn-scoped.** Files that were already dirty before the turn and were
  not touched again are filtered out; only this turn's work is shown.
  Commits made during the turn are folded in (`committed: true`,
  `head_before → head_after`).
- **Untracked files** created during the turn get a synthesized `+`-only
  preview (small text files only; binaries and oversized files are listed
  without a patch body).
- **Workspace-scoped.** All content-listing git calls are scoped to the
  workspace subtree (`-- .`), so an enclosing repository above
  `workspace_dir` cannot leak unrelated files into the payload.
- **Capped.** Patches are capped per file (16 KB) and per payload (192 KB,
  50 files with patch bodies, 500 file rows); git stdout itself is read
  through a 16 MB hard limit; anything beyond is flagged `truncated`.
- **Redacted.** Patch text passes the same credential-leak scrubber as the
  chat stream before it leaves the gateway, and untracked previews never
  follow symlinks.
- **Best-effort.** Every git invocation is read-only (`--no-ext-diff`,
  `--no-textconv`, repo-local `core.fsmonitor` disabled), time-boxed
  per-invocation and per-turn (15 s aggregate), and failure-tolerant — a
  broken git never blocks a chat turn, and a failed pre-turn snapshot
  yields no card rather than misattributing pre-existing dirt.
- **Tool-gated.** Turns that ran no tools skip the diff entirely — a pure
  Q&A turn can't have changed the workspace, and skipping avoids blaming
  it for a concurrent session's edits.

The dashboard renders the card in the turn's activity log: per-file
sections with A/M/D/B status, `+/-` line stats, and collapsible unified
diffs (single-file changes expand automatically).

## The `operator-coding` skill

`operator-mcp/skills/operator-coding.md` (installed to `~/.revka/skills/`)
packages the autonomous code-update loop the operator follows:

```
orient → contract → edit → verify → commit → report
```

Highlights: read the repo's own agent instructions (`AGENTS.md`,
`CLAUDE.md`, harness-imported skills) before editing, restate the task as
acceptance criteria, verify with the project's own commands (max 3
fix-verify reflections), commit one logical unit at a time, and require
explicit approval for anything that leaves the machine (push, PR). The
security posture is unchanged: `git_operations` remains commit-level
(no push), and pushing goes through `shell` under the standard autonomy
policy and approval flow.

## Scope notes

- Telemetry covers the configured `workspace_dir` repository. Edits the
  agent makes in other directories do not appear in the card (the per-tool
  activity entries still show them).
- The events flow over the dashboard chat WebSocket (`/ws/chat`) only;
  messaging channels (Telegram, Discord, …) are unaffected.
