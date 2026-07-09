//! Turn-scoped git workspace telemetry for the Operator chat.
//!
//! The dashboard chat needs two things the plain tool-event stream cannot
//! provide: which git repository/branch the Operator is working in, and a
//! git-verified summary of what actually changed on disk during a turn —
//! including edits made through `shell` or delegated coding CLIs, which
//! never pass through `file_edit`/`file_write` and are therefore invisible
//! to the client-side diff synthesis in `useAgentChatSession`.
//!
//! Security posture (this module runs automatically against whatever
//! repository contains `workspace_dir`, so it must assume a hostile repo):
//! - every git invocation uses fixed, read-only arguments;
//! - `--no-ext-diff`/`--no-textconv` disable repo-configured external diff
//!   and textconv commands, and `-c core.fsmonitor=` disables a repo-local
//!   fsmonitor hook that `status`/`diff` would otherwise execute;
//! - all content-listing invocations are scoped to the workspace subtree
//!   (`-- .` with the workspace as cwd) so an enclosing repository above
//!   `workspace_dir` cannot leak unrelated files into the payload;
//! - git stdout is read through a hard byte limit before any parsing;
//! - untracked-file previews never follow symlinks.
//!
//! Everything is strictly best-effort: any git failure, timeout, or
//! non-repo workspace degrades to `None` and the chat turn proceeds
//! unaffected. Snapshots fail closed — a failed baseline yields no
//! snapshot (and therefore no change summary) rather than misattributing
//! pre-existing dirt to the turn.

use std::collections::{BTreeSet, HashMap, HashSet};
use std::hash::{Hash, Hasher};
use std::path::{Path, PathBuf};

use serde::Serialize;

/// Upper bound for a single git invocation. Snapshots run on the turn's
/// critical path, so a hung git (e.g. stale index lock) must not stall chat.
const GIT_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(10);
/// Hard cap on bytes read from a single git invocation's stdout. Diffs can
/// be arbitrarily large (a turn can `tee` gigabytes into a tracked file);
/// beyond this the invocation is treated as failed rather than buffered.
const MAX_GIT_OUTPUT_BYTES: usize = 16 * 1024 * 1024;
/// Per-file patch budget shipped to the dashboard.
const MAX_PATCH_BYTES_PER_FILE: usize = 16 * 1024;
/// Total patch budget across all files in one `CodeChanges` payload.
const MAX_TOTAL_PATCH_BYTES: usize = 192 * 1024;
/// Maximum number of files carrying a patch body; the remainder are listed
/// with stats only and the payload is flagged truncated.
const MAX_FILES_WITH_PATCH: usize = 50;
/// Hard cap on total file rows in one payload (a turn that runs
/// `git clone`/`python -m venv` can otherwise produce tens of thousands of
/// untracked entries).
const MAX_TOTAL_FILES: usize = 500;
/// Untracked files larger than this get no synthesized preview patch.
const MAX_UNTRACKED_PREVIEW_BYTES: u64 = 64 * 1024;
/// Git's well-known empty tree object — the diff base for unborn branches.
const EMPTY_TREE: &str = "4b825dc642cb6eb9a060e54bf8d69288fbee4904";

/// Repository identity shown as the workspace badge in the dashboard chat.
#[derive(Debug, Clone, Serialize)]
pub struct WorkspaceContext {
    /// Absolute repository root (git toplevel).
    pub root: String,
    /// Repository directory name, e.g. `Revka`.
    pub repo: String,
    /// Current branch, or `(detached)` when HEAD is not on a branch.
    pub branch: String,
    /// Short HEAD sha; `None` on an unborn branch.
    pub head: Option<String>,
    /// Number of dirty entries under the workspace subtree.
    pub dirty_files: usize,
}

/// Pre-turn git state used to compute what a turn actually changed.
#[derive(Debug, Clone)]
pub struct WorkspaceSnapshot {
    /// Repository toplevel (used to resolve root-relative git paths).
    root: PathBuf,
    /// The workspace directory itself — cwd + pathspec scope for all
    /// content-listing git calls.
    workspace: PathBuf,
    /// Full HEAD sha at turn start; `None` on an unborn branch.
    head: Option<String>,
    /// Hash of each file's working-tree patch (vs the pre-turn base) at
    /// turn start. Files whose patch hash is unchanged at turn end were
    /// dirty before the turn and are filtered out of the change summary.
    file_patches: HashMap<String, u64>,
    untracked: BTreeSet<String>,
}

/// One changed file in a turn's change summary.
#[derive(Debug, Clone, Serialize)]
pub struct FileChange {
    pub path: String,
    /// `added` | `modified` | `deleted` | `binary`.
    pub status: &'static str,
    /// Line stats from `git diff --numstat`; `None` for binary files and
    /// synthesized untracked previews without stats.
    pub insertions: Option<u64>,
    pub deletions: Option<u64>,
    /// Unified diff for this file, capped and secret-redacted. `None` when
    /// the file is binary or the payload budget was exhausted.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub patch: Option<String>,
    pub truncated: bool,
}

/// Git-verified summary of everything a turn changed in the workspace.
#[derive(Debug, Clone, Serialize)]
pub struct CodeChanges {
    pub repo: String,
    pub branch: String,
    pub head_before: Option<String>,
    pub head_after: Option<String>,
    /// True when HEAD moved during the turn (the agent committed).
    pub committed: bool,
    pub files: Vec<FileChange>,
    pub total_insertions: u64,
    pub total_deletions: u64,
    pub truncated: bool,
}

/// Run `git` with fixed args in `dir`, returning stdout on success.
///
/// `None` on spawn failure, non-zero exit, timeout, or oversized output.
/// stdout is read through a hard byte cap so a pathological diff cannot
/// balloon daemon memory; `kill_on_drop` plus explicit `start_kill` ensure
/// a timed-out or over-limit git process does not linger.
async fn run_git(dir: &Path, args: &[&str]) -> Option<String> {
    use tokio::io::AsyncReadExt;

    let mut child = tokio::process::Command::new("git")
        // A repo-local `core.fsmonitor` command would be executed by git
        // when `status`/`diff` refresh the index — `--no-ext-diff` does not
        // cover it. Disable it so a hostile workspace `.git/config` cannot
        // run code under the daemon's account.
        .arg("-c")
        .arg("core.fsmonitor=")
        .args(args)
        .current_dir(dir)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .kill_on_drop(true)
        .spawn()
        .ok()?;

    let mut stdout = child.stdout.take()?;
    let read_fut = async {
        let mut out = Vec::new();
        let mut limited = (&mut stdout).take(MAX_GIT_OUTPUT_BYTES as u64 + 1);
        limited.read_to_end(&mut out).await.ok().map(|_| out)
    };
    let out = match tokio::time::timeout(GIT_TIMEOUT, read_fut).await {
        Ok(Some(out)) if out.len() <= MAX_GIT_OUTPUT_BYTES => out,
        _ => {
            let _ = child.start_kill();
            return None;
        }
    };
    let status = match tokio::time::timeout(GIT_TIMEOUT, child.wait()).await {
        Ok(Ok(status)) => status,
        _ => {
            let _ = child.start_kill();
            return None;
        }
    };
    if !status.success() {
        return None;
    }
    Some(String::from_utf8_lossy(&out).into_owned())
}

fn trimmed_non_empty(raw: Option<String>) -> Option<String> {
    raw.map(|s| s.trim().to_string()).filter(|s| !s.is_empty())
}

/// Detect the git repository containing `workspace`, if any.
pub async fn detect_context(workspace: &Path) -> Option<WorkspaceContext> {
    if !workspace.is_dir() {
        return None;
    }
    let root = trimmed_non_empty(run_git(workspace, &["rev-parse", "--show-toplevel"]).await)?;
    let repo = PathBuf::from(&root)
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| root.clone());
    // `symbolic-ref -q` succeeds on any branch (born or unborn) and fails
    // quietly when HEAD is detached.
    let branch =
        trimmed_non_empty(run_git(workspace, &["symbolic-ref", "--short", "-q", "HEAD"]).await)
            .unwrap_or_else(|| "(detached)".to_string());
    let head = trimmed_non_empty(run_git(workspace, &["rev-parse", "--short", "HEAD"]).await);
    // Dirty count scoped to the workspace subtree — an enclosing repo's
    // unrelated dirt should not light up the badge.
    let dirty_files = run_git(workspace, &["status", "--porcelain", "--", "."])
        .await
        .map(|s| s.lines().filter(|l| !l.is_empty()).count())
        .unwrap_or(0);
    Some(WorkspaceContext {
        root,
        repo,
        branch,
        head,
        dirty_files,
    })
}

/// Capture the pre-turn git state of `workspace`.
///
/// `None` when the workspace is not inside a git repository, git is
/// unavailable, or any baseline read fails — failing closed here prevents
/// a later `compute_changes` from blaming pre-existing dirt on the turn.
pub async fn snapshot(workspace: &Path) -> Option<WorkspaceSnapshot> {
    if !workspace.is_dir() {
        return None;
    }
    let root = PathBuf::from(trimmed_non_empty(
        run_git(workspace, &["rev-parse", "--show-toplevel"]).await,
    )?);
    let head = trimmed_non_empty(run_git(workspace, &["rev-parse", "HEAD"]).await);
    // On an unborn branch diff against the empty tree so files staged
    // before the turn still land in the baseline.
    let base = head.as_deref().unwrap_or(EMPTY_TREE);
    let diff = run_git(workspace, &diff_args(base, false)).await?;
    let file_patches = split_patch_by_file(&diff)
        .into_iter()
        .map(|(path, segment)| (path, hash_str(&segment)))
        .collect();
    let untracked = list_untracked(workspace).await?;
    Some(WorkspaceSnapshot {
        root,
        workspace: workspace.to_path_buf(),
        head,
        file_patches,
        untracked,
    })
}

/// Compute the git-verified change summary for a turn, relative to the
/// snapshot taken at turn start. Returns `None` when nothing changed (or
/// when git degraded — absence over misattribution).
pub async fn compute_changes(snap: &WorkspaceSnapshot) -> Option<CodeChanges> {
    let workspace = &snap.workspace;
    let ctx = detect_context(workspace).await?;
    let head_after = trimmed_non_empty(run_git(workspace, &["rev-parse", "HEAD"]).await);
    let committed = snap.head != head_after;

    // Diff base: the pre-turn HEAD covers both committed and uncommitted
    // edits in one pass (commit-to-worktree diff); the empty tree covers a
    // pre-turn unborn branch.
    let base = snap.head.as_deref().unwrap_or(EMPTY_TREE);
    let diff_text = run_git(workspace, &diff_args(base, false)).await?;
    let numstat_text = run_git(workspace, &diff_args(base, true))
        .await
        .unwrap_or_default();
    let numstat = parse_numstat(&numstat_text);

    let mut files: Vec<FileChange> = Vec::new();
    let mut seen_paths: HashSet<String> = HashSet::new();
    let mut total_insertions = 0u64;
    let mut total_deletions = 0u64;
    let mut payload_truncated = false;
    let mut patch_budget = MAX_TOTAL_PATCH_BYTES;

    let mut segments = split_patch_by_file(&diff_text);
    segments.sort_by(|a, b| a.0.cmp(&b.0));
    for (path, segment) in segments {
        // Unchanged pre-existing dirty files carry the same patch as at
        // turn start — those are not this turn's work.
        if snap.file_patches.get(&path) == Some(&hash_str(&segment)) {
            continue;
        }
        if files.len() >= MAX_TOTAL_FILES {
            payload_truncated = true;
            break;
        }
        let status = segment_status(&segment);
        let (insertions, deletions) = numstat.get(&path).copied().unwrap_or((None, None));
        total_insertions += insertions.unwrap_or(0);
        total_deletions += deletions.unwrap_or(0);
        let (patch, truncated) = if status == "binary" || files.len() >= MAX_FILES_WITH_PATCH {
            if files.len() >= MAX_FILES_WITH_PATCH {
                payload_truncated = true;
            }
            (None, false)
        } else {
            cap_patch(&segment, &mut patch_budget, &mut payload_truncated)
        };
        seen_paths.insert(path.clone());
        files.push(FileChange {
            path,
            status,
            insertions,
            deletions,
            patch,
            truncated,
        });
    }

    // Untracked files created during the turn never appear in `git diff`;
    // synthesize an add-preview for small text files.
    let untracked_now = list_untracked(workspace).await.unwrap_or_default();
    for path in untracked_now.difference(&snap.untracked) {
        if seen_paths.contains(path) {
            continue;
        }
        if files.len() >= MAX_TOTAL_FILES {
            payload_truncated = true;
            break;
        }
        let Some(change) = synthesize_untracked_change(
            &snap.root,
            path,
            &mut patch_budget,
            &mut payload_truncated,
        ) else {
            continue;
        };
        total_insertions += change.insertions.unwrap_or(0);
        files.push(change);
    }

    if files.is_empty() && !committed {
        return None;
    }

    Some(CodeChanges {
        repo: ctx.repo,
        branch: ctx.branch,
        head_before: snap.head.clone(),
        head_after,
        committed,
        files,
        total_insertions,
        total_deletions,
        truncated: payload_truncated,
    })
}

/// Fixed, read-only diff invocation scoped to the workspace subtree.
/// `--no-ext-diff`/`--no-textconv` keep repo-configured external diff and
/// textconv commands from running; `--no-renames` keeps per-file
/// bookkeeping deterministic (a rename is reported as delete + add);
/// `core.quotepath=false` keeps non-ASCII paths byte-exact instead of
/// octal-escaped; the trailing `-- .` pathspec (with the workspace as cwd)
/// keeps an enclosing repository's unrelated files out of the payload.
fn diff_args(base: &str, numstat: bool) -> Vec<&str> {
    let mut args = vec![
        "-c",
        "core.quotepath=false",
        "diff",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--no-renames",
    ];
    if numstat {
        args.push("--numstat");
    }
    args.push(base);
    args.push("--");
    args.push(".");
    args
}

fn hash_str(s: &str) -> u64 {
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    s.hash(&mut hasher);
    hasher.finish()
}

/// Split a unified diff into `(path, segment)` pairs.
///
/// The path is resolved, in priority order, from the `+++ b/` header, the
/// `--- a/` header (deletions), or the `diff --git a/X b/X` line itself —
/// the latter is what keeps binary and mode-only segments (which have no
/// `---`/`+++` lines) attributed. Quoted paths (`"a/say \"hi\".sh"`) are
/// unquoted. Header scanning stops at the first hunk so patch content can
/// never be misread as a header.
fn split_patch_by_file(diff: &str) -> Vec<(String, String)> {
    struct Segment {
        path: Option<String>,
        header_path: Option<String>,
        in_hunks: bool,
        text: String,
    }
    let mut out: Vec<(String, String)> = Vec::new();
    let mut current: Option<Segment> = None;
    let flush = |seg: Option<Segment>, out: &mut Vec<(String, String)>| {
        if let Some(seg) = seg {
            if let Some(path) = seg.path.or(seg.header_path) {
                out.push((path, seg.text));
            }
        }
    };
    for line in diff.split_inclusive('\n') {
        if line.starts_with("diff --git ") {
            flush(current.take(), &mut out);
            current = Some(Segment {
                path: None,
                header_path: path_from_diff_header(line),
                in_hunks: false,
                text: String::new(),
            });
        }
        if let Some(seg) = current.as_mut() {
            if !seg.in_hunks {
                if line.starts_with("@@") {
                    seg.in_hunks = true;
                } else if seg.path.is_none() {
                    if let Some(rest) = line.strip_prefix("+++ ") {
                        if rest.trim_end() == "/dev/null" {
                            // Deleted file: fall back to the pre-image path.
                            if let Some(pre) = seg
                                .text
                                .lines()
                                .rev()
                                .find_map(|l| l.strip_prefix("--- "))
                                .and_then(|raw| parse_diff_path(raw, "a/"))
                            {
                                seg.path = Some(pre);
                            }
                        } else if let Some(path) = parse_diff_path(rest, "b/") {
                            seg.path = Some(path);
                        }
                    }
                }
            }
            seg.text.push_str(line);
        }
    }
    flush(current, &mut out);
    out
}

/// Parse a `--- `/`+++ ` header path, stripping `prefix` (`a/` or `b/`)
/// and unquoting git's C-style quoting when present.
fn parse_diff_path(raw: &str, prefix: &str) -> Option<String> {
    let raw = raw.trim_end();
    let unquoted;
    let candidate = if let Some(inner) = raw.strip_prefix('"').and_then(|r| r.strip_suffix('"')) {
        unquoted = unquote_c_style(inner)?;
        unquoted.as_str()
    } else {
        raw
    };
    candidate
        .strip_prefix(prefix)
        .filter(|p| !p.is_empty())
        .map(|p| p.to_string())
}

/// Extract the path from a `diff --git a/X b/X` header line. Handles the
/// quoted form by unquoting the first token; the unquoted form relies on
/// `--no-renames` guaranteeing both sides are identical (equal halves).
fn path_from_diff_header(line: &str) -> Option<String> {
    let rest = line.strip_prefix("diff --git ")?.trim_end();
    if let Some(after_quote) = rest.strip_prefix('"') {
        // Find the closing quote of the first token, honoring escapes.
        let bytes = after_quote.as_bytes();
        let mut escaped = false;
        for (i, &b) in bytes.iter().enumerate() {
            if escaped {
                escaped = false;
            } else if b == b'\\' {
                escaped = true;
            } else if b == b'"' {
                let inner = &after_quote[..i];
                return unquote_c_style(inner)?
                    .strip_prefix("a/")
                    .filter(|p| !p.is_empty())
                    .map(|p| p.to_string());
            }
        }
        return None;
    }
    // Unquoted: `a/X b/X` — equal halves around a single space.
    let bytes = rest.as_bytes();
    if bytes.len() < 5 || bytes.len() % 2 == 0 {
        return None;
    }
    let n = (bytes.len() - 1) / 2;
    if bytes[n] != b' '
        || !bytes[..n].starts_with(b"a/")
        || !bytes[n + 1..].starts_with(b"b/")
        || bytes[2..n] != bytes[n + 3..]
    {
        return None;
    }
    let path = String::from_utf8_lossy(&bytes[n + 3..]).into_owned();
    (!path.is_empty()).then_some(path)
}

/// Undo git's C-style path quoting (`\"`, `\\`, `\t`, `\n`, `\r`, octal
/// escapes). Returns `None` on a malformed escape.
fn unquote_c_style(input: &str) -> Option<String> {
    let bytes = input.as_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] != b'\\' {
            out.push(bytes[i]);
            i += 1;
            continue;
        }
        i += 1;
        let &escape = bytes.get(i)?;
        match escape {
            b'"' | b'\\' => out.push(escape),
            b't' => out.push(b'\t'),
            b'n' => out.push(b'\n'),
            b'r' => out.push(b'\r'),
            b'a' => out.push(0x07),
            b'b' => out.push(0x08),
            b'f' => out.push(0x0c),
            b'v' => out.push(0x0b),
            b'0'..=b'7' => {
                let mut value = 0u32;
                let mut digits = 0;
                while digits < 3 {
                    match bytes.get(i) {
                        Some(&d @ b'0'..=b'7') => {
                            value = value * 8 + u32::from(d - b'0');
                            i += 1;
                            digits += 1;
                        }
                        _ => break,
                    }
                }
                if digits == 0 || value > 0xff {
                    return None;
                }
                out.push(value as u8);
                continue;
            }
            _ => return None,
        }
        i += 1;
    }
    Some(String::from_utf8_lossy(&out).into_owned())
}

fn segment_status(segment: &str) -> &'static str {
    if segment.contains("\nBinary files ") || segment.contains("\nGIT binary patch") {
        "binary"
    } else if segment.contains("\nnew file mode ") {
        "added"
    } else if segment.contains("\ndeleted file mode ") {
        "deleted"
    } else {
        "modified"
    }
}

/// Parse `git diff --numstat` output into `path -> (insertions, deletions)`.
/// Binary entries (`-` counts) map to `(None, None)`.
fn parse_numstat(text: &str) -> HashMap<String, (Option<u64>, Option<u64>)> {
    let mut map = HashMap::new();
    for line in text.lines() {
        let mut parts = line.splitn(3, '\t');
        let (Some(ins), Some(del), Some(path)) = (parts.next(), parts.next(), parts.next()) else {
            continue;
        };
        let path = if let Some(inner) = path.strip_prefix('"').and_then(|p| p.strip_suffix('"')) {
            match unquote_c_style(inner) {
                Some(unquoted) => unquoted,
                None => continue,
            }
        } else {
            path.to_string()
        };
        map.insert(path, (ins.parse::<u64>().ok(), del.parse::<u64>().ok()));
    }
    map
}

/// List untracked files under the workspace subtree (root-relative paths).
/// `None` on git failure so snapshot baselines fail closed.
async fn list_untracked(workspace: &Path) -> Option<BTreeSet<String>> {
    // `-uall` lists files inside untracked directories individually instead
    // of collapsing them to `dir/`, so new files in new directories get a
    // preview patch too.
    let out = run_git(
        workspace,
        &[
            "-c",
            "core.quotepath=false",
            "status",
            "--porcelain",
            "-z",
            "-uall",
            "--",
            ".",
        ],
    )
    .await?;
    Some(
        out.split('\0')
            .filter_map(|entry| entry.strip_prefix("?? "))
            .map(|p| p.to_string())
            .collect(),
    )
}

/// Cap a patch to per-file and total budgets, applying secret redaction.
fn cap_patch(
    segment: &str,
    budget: &mut usize,
    payload_truncated: &mut bool,
) -> (Option<String>, bool) {
    if *budget == 0 {
        *payload_truncated = true;
        return (None, true);
    }
    let limit = MAX_PATCH_BYTES_PER_FILE.min(*budget);
    let (text, truncated) = if segment.len() > limit {
        // Never slice mid-character — the diff text can carry multibyte
        // UTF-8 (source content, U+FFFD replacements from lossy decode).
        let mut limit = limit;
        while !segment.is_char_boundary(limit) {
            limit -= 1;
        }
        // Cut on a line boundary so the diff renderer never sees a torn line.
        let cut = segment[..limit].rfind('\n').unwrap_or(limit);
        (&segment[..cut], true)
    } else {
        (segment, false)
    };
    if truncated {
        *payload_truncated = true;
    }
    *budget = budget.saturating_sub(text.len());
    // Diffs routinely surface .env-style files; scrub credentials before the
    // patch leaves the gateway, mirroring the chat-stream guardrail.
    let (redacted, _) = crate::security::redact_outbound(text);
    (Some(redacted), truncated)
}

/// Build the `FileChange` for a file created this turn (untracked), with a
/// `+`-only preview patch for small text files. `None` when the path is not
/// a readable regular file (symlinks are deliberately not followed — a
/// turn-created symlink must not exfiltrate out-of-workspace content).
fn synthesize_untracked_change(
    root: &Path,
    path: &str,
    budget: &mut usize,
    payload_truncated: &mut bool,
) -> Option<FileChange> {
    let stats_only = |status: &'static str, truncated: bool| FileChange {
        path: path.to_string(),
        status,
        insertions: None,
        deletions: None,
        patch: None,
        truncated,
    };
    if *budget == 0 {
        // Patch budget exhausted — list the file without touching the disk.
        *payload_truncated = true;
        return Some(stats_only("added", true));
    }
    let full = root.join(path);
    let meta = std::fs::symlink_metadata(&full).ok()?;
    if !meta.is_file() {
        return None;
    }
    if meta.len() > MAX_UNTRACKED_PREVIEW_BYTES {
        // Too large to preview: list it, but ship no patch body.
        *payload_truncated = true;
        return Some(stats_only("added", true));
    }
    let bytes = std::fs::read(&full).ok()?;
    // Binary heuristic: NUL byte or invalid UTF-8 anywhere in the file.
    if bytes.contains(&0) || std::str::from_utf8(&bytes).is_err() {
        return Some(stats_only("binary", false));
    }
    let content = String::from_utf8(bytes).unwrap_or_default();
    let line_count = content.lines().count() as u64;
    let mut patch = format!("--- /dev/null\n+++ b/{path}\n");
    if line_count > 0 {
        patch.push_str(&format!("@@ -0,0 +1,{line_count} @@\n"));
    }
    for line in content.lines() {
        patch.push('+');
        patch.push_str(line);
        patch.push('\n');
    }
    let (capped, truncated) = cap_patch(&patch, budget, payload_truncated);
    Some(FileChange {
        path: path.to_string(),
        status: "added",
        insertions: Some(line_count),
        deletions: Some(0),
        patch: capped,
        truncated,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    fn git(dir: &Path, args: &[&str]) {
        let out = std::process::Command::new("git")
            .args(args)
            .current_dir(dir)
            .output()
            .expect("git spawn");
        assert!(
            out.status.success(),
            "git {args:?} failed: {}",
            String::from_utf8_lossy(&out.stderr)
        );
    }

    fn init_repo(dir: &Path) {
        git(dir, &["init", "-b", "main"]);
        git(dir, &["config", "user.email", "test@example.com"]);
        git(dir, &["config", "user.name", "Test"]);
        git(dir, &["config", "core.autocrlf", "false"]);
    }

    fn write(dir: &Path, rel: &str, content: &str) {
        let path = dir.join(rel);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).unwrap();
        }
        std::fs::write(path, content).unwrap();
    }

    #[tokio::test]
    async fn detect_context_none_outside_repo() {
        let tmp = TempDir::new().unwrap();
        assert!(detect_context(tmp.path()).await.is_none());
    }

    #[tokio::test]
    async fn detect_context_reports_repo_branch_and_dirt() {
        let tmp = TempDir::new().unwrap();
        init_repo(tmp.path());
        write(tmp.path(), "a.txt", "one\n");
        git(tmp.path(), &["add", "."]);
        git(tmp.path(), &["commit", "-m", "init"]);
        write(tmp.path(), "b.txt", "untracked\n");

        let ctx = detect_context(tmp.path()).await.expect("context");
        assert_eq!(ctx.branch, "main");
        assert!(ctx.head.is_some());
        assert_eq!(ctx.dirty_files, 1);
        assert_eq!(
            ctx.repo,
            tmp.path().file_name().unwrap().to_string_lossy().as_ref()
        );
    }

    #[tokio::test]
    async fn compute_changes_reports_only_turn_edits() {
        let tmp = TempDir::new().unwrap();
        init_repo(tmp.path());
        write(tmp.path(), "keep.txt", "keep\n");
        write(tmp.path(), "edited.txt", "before\n");
        git(tmp.path(), &["add", "."]);
        git(tmp.path(), &["commit", "-m", "init"]);

        // Pre-existing dirty edit — must be filtered out of the summary.
        write(tmp.path(), "keep.txt", "keep dirty\n");

        let snap = snapshot(tmp.path()).await.expect("snapshot");

        // Turn edits: modify a tracked file, add an untracked file.
        write(tmp.path(), "edited.txt", "after\n");
        write(tmp.path(), "new.txt", "hello\nworld\n");

        let changes = compute_changes(&snap).await.expect("changes");
        assert!(!changes.committed);
        let paths: Vec<_> = changes.files.iter().map(|f| f.path.as_str()).collect();
        assert!(paths.contains(&"edited.txt"), "paths: {paths:?}");
        assert!(paths.contains(&"new.txt"), "paths: {paths:?}");
        assert!(
            !paths.contains(&"keep.txt"),
            "pre-existing dirt leaked: {paths:?}"
        );

        let edited = changes
            .files
            .iter()
            .find(|f| f.path == "edited.txt")
            .unwrap();
        assert_eq!(edited.status, "modified");
        let patch = edited.patch.as_deref().unwrap();
        assert!(patch.contains("-before"), "patch: {patch}");
        assert!(patch.contains("+after"), "patch: {patch}");

        let added = changes.files.iter().find(|f| f.path == "new.txt").unwrap();
        assert_eq!(added.status, "added");
        assert_eq!(added.insertions, Some(2));
        let added_patch = added.patch.as_deref().unwrap();
        assert!(added_patch.contains("+hello"));
        // Hunk header must stay parseable by `git apply`.
        assert!(
            added_patch.contains("@@ -0,0 +1,2 @@"),
            "patch: {added_patch}"
        );
    }

    #[tokio::test]
    async fn compute_changes_none_when_nothing_happened() {
        let tmp = TempDir::new().unwrap();
        init_repo(tmp.path());
        write(tmp.path(), "a.txt", "one\n");
        git(tmp.path(), &["add", "."]);
        git(tmp.path(), &["commit", "-m", "init"]);
        // Pre-existing dirt only; no turn edits.
        write(tmp.path(), "a.txt", "dirty\n");

        let snap = snapshot(tmp.path()).await.expect("snapshot");
        assert!(compute_changes(&snap).await.is_none());
    }

    #[tokio::test]
    async fn compute_changes_marks_commits() {
        let tmp = TempDir::new().unwrap();
        init_repo(tmp.path());
        write(tmp.path(), "a.txt", "one\n");
        git(tmp.path(), &["add", "."]);
        git(tmp.path(), &["commit", "-m", "init"]);

        let snap = snapshot(tmp.path()).await.expect("snapshot");

        write(tmp.path(), "a.txt", "two\n");
        git(tmp.path(), &["add", "."]);
        git(tmp.path(), &["commit", "-m", "turn edit"]);

        let changes = compute_changes(&snap).await.expect("changes");
        assert!(changes.committed);
        assert_ne!(changes.head_before, changes.head_after);
        let file = changes.files.iter().find(|f| f.path == "a.txt").unwrap();
        assert_eq!(file.status, "modified");
        assert!(file.patch.as_deref().unwrap().contains("+two"));
    }

    #[tokio::test]
    async fn compute_changes_reports_deletions() {
        let tmp = TempDir::new().unwrap();
        init_repo(tmp.path());
        write(tmp.path(), "gone.txt", "bye\n");
        git(tmp.path(), &["add", "."]);
        git(tmp.path(), &["commit", "-m", "init"]);

        let snap = snapshot(tmp.path()).await.expect("snapshot");
        std::fs::remove_file(tmp.path().join("gone.txt")).unwrap();

        let changes = compute_changes(&snap).await.expect("changes");
        let file = changes.files.iter().find(|f| f.path == "gone.txt").unwrap();
        assert_eq!(file.status, "deleted");
    }

    #[tokio::test]
    async fn compute_changes_reports_tracked_binary_edits() {
        let tmp = TempDir::new().unwrap();
        init_repo(tmp.path());
        std::fs::write(tmp.path().join("blob.bin"), [0u8, 1, 2, 3]).unwrap();
        git(tmp.path(), &["add", "."]);
        git(tmp.path(), &["commit", "-m", "init"]);

        let snap = snapshot(tmp.path()).await.expect("snapshot");
        std::fs::write(tmp.path().join("blob.bin"), [9u8, 9, 9, 9, 9]).unwrap();

        let changes = compute_changes(&snap).await.expect("changes");
        let file = changes.files.iter().find(|f| f.path == "blob.bin").unwrap();
        assert_eq!(file.status, "binary");
        assert!(file.patch.is_none());
    }

    #[tokio::test]
    async fn compute_changes_reports_staged_files_on_unborn_head() {
        let tmp = TempDir::new().unwrap();
        init_repo(tmp.path());

        let snap = snapshot(tmp.path()).await.expect("snapshot");

        write(tmp.path(), "scaffold.txt", "new project\n");
        git(tmp.path(), &["add", "."]);

        let changes = compute_changes(&snap).await.expect("changes");
        let file = changes
            .files
            .iter()
            .find(|f| f.path == "scaffold.txt")
            .unwrap();
        assert_eq!(file.status, "added");
    }

    #[tokio::test]
    async fn oversized_multibyte_patch_truncates_without_panicking() {
        let tmp = TempDir::new().unwrap();
        init_repo(tmp.path());
        write(tmp.path(), "big.txt", "start\n");
        git(tmp.path(), &["add", "."]);
        git(tmp.path(), &["commit", "-m", "init"]);

        let snap = snapshot(tmp.path()).await.expect("snapshot");
        // Multibyte (Hangul) content sized well past the per-file cap so the
        // byte limit lands mid-character unless the cut is boundary-floored.
        let mut big = String::new();
        for i in 0..2000 {
            use std::fmt::Write as _;
            let _ = writeln!(big, "줄 {i} 한국어 패딩 텍스트 가나다라마바사");
        }
        write(tmp.path(), "big.txt", &big);

        let changes = compute_changes(&snap).await.expect("changes");
        assert!(changes.truncated);
        let file = changes.files.iter().find(|f| f.path == "big.txt").unwrap();
        assert!(file.truncated);
        let patch = file.patch.as_deref().unwrap();
        assert!(patch.len() <= MAX_PATCH_BYTES_PER_FILE);
        // Cut lands on a line boundary — the last line is a complete diff line.
        let last = patch.lines().last().unwrap();
        assert!(
            last.starts_with('+')
                || last.starts_with('-')
                || last.starts_with(' ')
                || last.starts_with('@'),
            "torn last line: {last:?}"
        );
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn untracked_symlink_is_not_followed() {
        let tmp = TempDir::new().unwrap();
        init_repo(tmp.path());
        write(tmp.path(), "a.txt", "one\n");
        git(tmp.path(), &["add", "."]);
        git(tmp.path(), &["commit", "-m", "init"]);

        let secret = TempDir::new().unwrap();
        std::fs::write(secret.path().join("secret.txt"), "token=abc123\n").unwrap();

        let snap = snapshot(tmp.path()).await.expect("snapshot");
        std::os::unix::fs::symlink(
            secret.path().join("secret.txt"),
            tmp.path().join("link.txt"),
        )
        .unwrap();

        // The symlink itself is a turn-created untracked entry, but its
        // target content must never ship.
        if let Some(changes) = compute_changes(&snap).await {
            for file in &changes.files {
                if let Some(patch) = &file.patch {
                    assert!(!patch.contains("abc123"), "symlink content leaked: {patch}");
                }
            }
        }
    }

    #[test]
    fn split_patch_handles_added_and_deleted_files() {
        let diff = "diff --git a/x.txt b/x.txt\nnew file mode 100644\n--- /dev/null\n+++ b/x.txt\n@@ -0,0 +1 @@\n+hi\ndiff --git a/y.txt b/y.txt\ndeleted file mode 100644\n--- a/y.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-bye\n";
        let segments = split_patch_by_file(diff);
        let paths: Vec<_> = segments.iter().map(|(p, _)| p.as_str()).collect();
        assert_eq!(paths, vec!["x.txt", "y.txt"]);
        assert_eq!(segment_status(&segments[0].1), "added");
        assert_eq!(segment_status(&segments[1].1), "deleted");
    }

    #[test]
    fn split_patch_keeps_binary_and_mode_only_segments() {
        // Binary and mode-only segments carry no ---/+++ lines; the path
        // must come from the `diff --git` header.
        let diff = "diff --git a/blob.bin b/blob.bin\nindex 20b5be9..6423431 100644\nBinary files a/blob.bin and b/blob.bin differ\ndiff --git a/run.sh b/run.sh\nold mode 100644\nnew mode 100755\n";
        let segments = split_patch_by_file(diff);
        let paths: Vec<_> = segments.iter().map(|(p, _)| p.as_str()).collect();
        assert_eq!(paths, vec!["blob.bin", "run.sh"]);
        assert_eq!(segment_status(&segments[0].1), "binary");
        assert_eq!(segment_status(&segments[1].1), "modified");
    }

    #[test]
    fn split_patch_unquotes_quoted_paths() {
        let diff = "diff --git \"a/say \\\"hi\\\".sh\" \"b/say \\\"hi\\\".sh\"\nindex 1111111..2222222 100644\n--- \"a/say \\\"hi\\\".sh\"\n+++ \"b/say \\\"hi\\\".sh\"\n@@ -1 +1 @@\n-old\n+new\n";
        let segments = split_patch_by_file(diff);
        assert_eq!(segments.len(), 1);
        assert_eq!(segments[0].0, "say \"hi\".sh");
    }

    #[test]
    fn split_patch_ignores_header_lookalikes_inside_hunks() {
        // A content line starting with `+++ b/` after the first hunk must
        // not be misread as a header.
        let diff = "diff --git a/notes.md b/notes.md\nindex 1111111..2222222 100644\n--- a/notes.md\n+++ b/notes.md\n@@ -1 +1,2 @@\n old\n++++ b/fake.txt\n";
        let segments = split_patch_by_file(diff);
        assert_eq!(segments.len(), 1);
        assert_eq!(segments[0].0, "notes.md");
    }

    #[test]
    fn unquote_c_style_handles_escapes_and_octal() {
        assert_eq!(
            unquote_c_style("say \\\"hi\\\".sh").as_deref(),
            Some("say \"hi\".sh")
        );
        assert_eq!(unquote_c_style("tab\\there").as_deref(), Some("tab\there"));
        // \354\236\204 is the UTF-8 octal encoding of '임'.
        assert_eq!(
            unquote_c_style("\\354\\236\\204.txt").as_deref(),
            Some("임.txt")
        );
        assert!(unquote_c_style("bad\\q").is_none());
    }
}
