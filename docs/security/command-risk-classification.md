# Command Risk Classification

This page documents the **runtime command-risk classifier** that drives the
`[autonomy]` settings `require_approval_for_medium_risk` and
`block_high_risk_commands`. The source of truth is
[`src/security/policy.rs`][src] (`CommandRiskLevel::Low / Medium / High`);
this doc is generated/maintained against that file. If the table here ever
disagrees with the code, **the code wins** — file an issue.

[src]: https://github.com/KumihoIO/construct-os/blob/main/src/security/policy.rs

The classifier evaluates each shell-execution candidate after argv parsing.
A single command is classified by:
1. Looking up its **basename** (`rm`, `git`, `powershell`, etc.).
2. For commands whose risk depends on a **subcommand verb** (`git`, `npm`,
   `cargo`), checking that verb against a whitelist.
3. Scanning the **joined command string** for known **destructive patterns**
   even when the basename alone is benign (`rm -rf /`, fork-bomb, Windows
   recursive deletes, `format c:`).

A pipeline / chain of commands inherits the **highest** risk level of any
segment.

## High risk

> Default: blocked entirely when `block_high_risk_commands = true`
> (the shipped default). When set to `false`, runs through the same
> approval gate as medium-risk.

Basenames classified High:

| Category | Commands |
|---|---|
| File destruction | `rm`, `mkfs`, `dd` |
| Power / boot | `shutdown`, `reboot`, `halt`, `poweroff` |
| Privilege escalation | `sudo`, `su`, `runas` |
| Permissions / ownership | `chown`, `chmod`, `icacls`, `takeown` |
| Account management | `useradd`, `userdel`, `usermod`, `passwd` |
| Filesystem mounting | `mount`, `umount` |
| Firewall | `iptables`, `ufw`, `firewall-cmd`, `netsh` |
| Outbound network | `curl`, `wget`, `nc`, `ncat`, `netcat`, `scp`, `ssh`, `ftp`, `telnet` |
| Windows registry / service | `reg`, `net`, `wmic`, `sc` |
| Windows shells / scripts | `powershell`, `pwsh` |
| Windows destructive | `del`, `rmdir`, `format` |

Destructive-pattern matchers (also High):
- `rm -rf /` and `rm -fr /` anywhere in the joined segment
- The classic fork-bomb literal `:(){:|:&};:`
- Windows recursive-delete literals: `del /s /q`, `rmdir /s /q`
- `format c:` literal

## Medium risk

> Default: requires approval when `require_approval_for_medium_risk = true`
> (the shipped default). When `level = "full"` is set in `[autonomy]`,
> this gate is skipped (other guardrails still apply).

Per-tool verb whitelists:

| Tool | Verbs that flip to Medium |
|---|---|
| `git` | `commit`, `push`, `reset`, `clean`, `rebase`, `merge`, `cherry-pick`, `revert`, `branch`, `checkout`, `switch`, `tag` |
| `npm` / `pnpm` / `yarn` | `install`, `add`, `remove`, `uninstall`, `update`, `publish` |
| `cargo` | `add`, `remove`, `install`, `clean`, `publish` |

Bare-basename Medium (any args):
`touch`, `mkdir`, `mv`, `cp`, `ln`, plus Windows equivalents `copy`,
`xcopy`, `robocopy`, `move`, `ren`, `rename`, `mklink`.

## Low risk

Everything else — read-only or trivially-safe commands run without
approval gating. Examples: `ls`, `cat`, `grep`, `find`, `git status`,
`git log`, `npm ls`, `cargo check`, `cargo test`, `which`, `ps`, `df`.
The classifier deliberately defaults to Low rather than enumerating
every safe command, on the principle that the High and Medium lists
should grow conservatively when new dangerous commands appear.

## Where the gates fire

- **Block**: `[autonomy].block_high_risk_commands` (default `true`) — High
  commands are rejected before dispatch.
- **Approve**: `[autonomy].require_approval_for_medium_risk` (default
  `true`) — Medium commands route through the approval queue
  (see [`docs/security/approval-flows.md`](approval-flows.md) if it
  exists; otherwise see `src/approval/`).
- **`level = "full"`**: skips the medium-risk approval step but still
  runs the High-risk block.

Per-policy budgets (`max_actions_per_hour`, `max_cost_per_day_cents`)
apply on top of the risk classifier — exhausting the budget halts
even Low-risk commands.

## Updating this page

The lists here are checked into the docs because the risk classifier is
**part of the security posture** Construct ships with — it is meaningful
for operators to be able to read what gets gated without spelunking the
Rust source. When `src/security/policy.rs` changes the High or Medium
sets, this doc must be updated in the same PR; the audit-row-17 cleanup
established this expectation explicitly.
