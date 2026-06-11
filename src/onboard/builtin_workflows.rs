//! Built-in workflow YAMLs bundled into the `revka` binary and seeded
//! into the user's workspace during onboarding (and on demand via
//! `revka workflows sync`).
//!
//! Source of truth lives at `operator-mcp/operator_mcp/workflow/builtins/`;
//! the files are embedded here at compile time so users don't need the
//! Python operator checked out to pick them up.

use std::path::{Path, PathBuf};

use crate::t;
use anyhow::{Context, Result};
use console::style;
use include_dir::{Dir, include_dir};
use tokio::fs;

/// Directory inside the workspace where the gateway and cron scheduler
/// look for built-in workflow YAMLs. Must match
/// `src/gateway/api_workflows.rs::BUILTIN_WORKFLOWS_DIR` (sans the `.revka/` prefix).
pub const WORKSPACE_WORKFLOWS_SUBDIR: &str = "operator_mcp/workflow/builtins";

/// Embedded YAML templates. Compiled into the binary at build time.
pub static BUILTIN_WORKFLOWS: Dir<'_> =
    include_dir!("$CARGO_MANIFEST_DIR/operator-mcp/operator_mcp/workflow/builtins");

#[derive(Debug, Default, Clone, Copy)]
pub struct SeedReport {
    pub written: usize,
    pub skipped: usize,
    pub overwritten: usize,
}

/// Seed the embedded workflows into `<workspace_dir>/operator_mcp/workflow/builtins/`.
///
/// Files are written only if missing unless `force` is set, in which case existing
/// files are overwritten. Returns counts for caller-side reporting.
pub async fn seed_builtin_workflows(workspace_dir: &Path, force: bool) -> Result<SeedReport> {
    let dest_dir = workspace_dir.join(WORKSPACE_WORKFLOWS_SUBDIR);
    fs::create_dir_all(&dest_dir)
        .await
        .with_context(|| format!("creating {}", dest_dir.display()))?;

    let mut report = SeedReport::default();
    for file in BUILTIN_WORKFLOWS.files() {
        let Some(filename) = file.path().file_name() else {
            continue;
        };
        let dest = dest_dir.join(filename);
        let exists = fs::try_exists(&dest).await.unwrap_or(false);
        if exists && !force {
            report.skipped += 1;
            continue;
        }
        fs::write(&dest, file.contents())
            .await
            .with_context(|| format!("writing {}", dest.display()))?;
        if exists {
            report.overwritten += 1;
        } else {
            report.written += 1;
        }
    }
    Ok(report)
}

/// CLI handler for `revka workflows sync [--force]`.
pub async fn run_sync(workspace_dir: PathBuf, force: bool) -> Result<()> {
    let report = seed_builtin_workflows(&workspace_dir, force).await?;
    let total = BUILTIN_WORKFLOWS.files().count();
    let dest = workspace_dir.join(WORKSPACE_WORKFLOWS_SUBDIR);

    println!(
        "  {} {}",
        style("✓").green().bold(),
        style(t!("workflows-available", count = total))
            .green()
            .bold()
    );
    println!(
        "  {} {}",
        style("·").dim(),
        style(t!(
            "workflows-destination",
            path = dest.display().to_string()
        ))
        .dim()
    );
    if report.written > 0 {
        println!(
            "  {} {}",
            style("+").green(),
            t!("workflows-wrote", count = report.written)
        );
    }
    if report.overwritten > 0 {
        println!(
            "  {} {}",
            style("~").yellow(),
            t!("workflows-overwrote", count = report.overwritten)
        );
    }
    if report.skipped > 0 {
        println!(
            "  {} {}",
            style("·").dim(),
            t!("workflows-skipped", count = report.skipped)
        );
    }
    Ok(())
}

/// CLI handler for `revka workflows list` — reports what's embedded.
pub fn run_list() {
    let mut names: Vec<&str> = BUILTIN_WORKFLOWS
        .files()
        .filter_map(|f| f.path().file_name().and_then(|n| n.to_str()))
        .collect();
    names.sort_unstable();
    println!(
        "  {} {}",
        style("✓").green().bold(),
        style(t!("workflows-summary", count = names.len()))
            .green()
            .bold()
    );
    for name in names {
        println!("  {} {}", style("·").dim(), name);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn embed_contains_expected_workflows() {
        let names: Vec<&str> = BUILTIN_WORKFLOWS
            .files()
            .filter_map(|f| f.path().file_name().and_then(|n| n.to_str()))
            .collect();
        assert!(!names.is_empty(), "expected embedded workflows");
        assert!(
            names.iter().any(|n| *n == "code-review.yaml"),
            "code-review.yaml should be embedded"
        );
    }

    #[tokio::test]
    async fn seed_writes_files_when_missing() {
        let tmp = TempDir::new().unwrap();
        let report = seed_builtin_workflows(tmp.path(), false).await.unwrap();
        assert!(report.written > 0);
        assert_eq!(report.overwritten, 0);
        assert_eq!(report.skipped, 0);
        let dest = tmp
            .path()
            .join(WORKSPACE_WORKFLOWS_SUBDIR)
            .join("code-review.yaml");
        assert!(dest.exists());
    }

    #[tokio::test]
    async fn seed_skips_existing_without_force() {
        let tmp = TempDir::new().unwrap();
        seed_builtin_workflows(tmp.path(), false).await.unwrap();
        let again = seed_builtin_workflows(tmp.path(), false).await.unwrap();
        assert_eq!(again.written, 0);
        assert_eq!(again.overwritten, 0);
        assert!(again.skipped > 0);
    }

    #[tokio::test]
    async fn seed_overwrites_with_force() {
        let tmp = TempDir::new().unwrap();
        seed_builtin_workflows(tmp.path(), false).await.unwrap();
        let dest = tmp
            .path()
            .join(WORKSPACE_WORKFLOWS_SUBDIR)
            .join("code-review.yaml");
        fs::write(&dest, "# tampered\n").await.unwrap();
        let forced = seed_builtin_workflows(tmp.path(), true).await.unwrap();
        assert!(forced.overwritten > 0);
        let content = fs::read_to_string(&dest).await.unwrap();
        assert_ne!(content, "# tampered\n");
    }
}
