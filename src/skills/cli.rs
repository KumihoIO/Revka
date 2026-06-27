//! `revka import-harness` — scan existing agent harnesses and register them as
//! on-demand Kumiho skills.
//!
//! This is the manual / re-runnable surface for the same scan + import the
//! onboarding step and the daemon-startup hook use. It never moves the user's
//! files: each registered skill points at the original harness file in place.

use crate::config::Config;
use crate::gateway::kumiho_client::build_client_from_config;
use crate::skills::{
    HarnessImportReport, HarnessScanOptions, HarnessScanReport, default_ledger_path,
    import_harness_skills, scan_harnesses,
};
use anyhow::{Result, bail};
use console::style;
use std::io::IsTerminal;
use std::path::PathBuf;

/// Handle `revka import-harness`.
pub async fn run_harness_import(
    config: &Config,
    path: Option<PathBuf>,
    include_global: bool,
    dry_run: bool,
    yes: bool,
) -> Result<()> {
    if !config.kumiho.enabled {
        bail!(
            "Kumiho memory is disabled in your config (kumiho.enabled = false). \
             Harness skills are stored in Kumiho; enable it with `revka onboard` first."
        );
    }

    let opts = HarnessScanOptions::with_defaults(path, include_global);
    println!("{}", style("Scanning for agent harnesses…").cyan().bold());
    for root in opts.project_roots.iter().chain(opts.global_roots.iter()) {
        println!("  {} {}", style("•").dim(), root.display());
    }

    let report = scan_harnesses(&opts);
    print_scan_report(&report);

    if report.discovered.is_empty() {
        println!(
            "{}",
            style("No harnesses found — nothing to import.").yellow()
        );
        return Ok(());
    }

    if dry_run {
        println!(
            "\n{}",
            style("Dry run — nothing was registered.").yellow().bold()
        );
        return Ok(());
    }

    if !yes && std::io::stdin().is_terminal() && std::io::stdout().is_terminal() {
        let proceed = dialoguer::Confirm::new()
            .with_prompt(format!(
                "Register {} harness skill(s) into Kumiho ({})?",
                report.discovered.len(),
                config.kumiho.memory_project
            ))
            .default(true)
            .interact()?;
        if !proceed {
            println!("{}", style("Aborted.").yellow());
            return Ok(());
        }
    }

    let client = build_client_from_config(config);
    let ledger_path = default_ledger_path();
    println!("\n{}", style("Registering with Kumiho…").cyan().bold());
    let import = import_harness_skills(
        &client,
        &config.kumiho.memory_project,
        &report.discovered,
        &ledger_path,
    )
    .await?;
    print_import_report(&import);
    Ok(())
}

fn print_scan_report(report: &HarnessScanReport) {
    println!(
        "\n{} {} skill(s), {} instruction doc(s) across {} tool(s):",
        style("Found").green().bold(),
        report.skills,
        report.instructions,
        report.per_tool.len()
    );
    for (tool, count) in &report.per_tool {
        println!("  {} {tool}: {count}", style("·").dim());
    }
    if report.duplicates > 0 {
        println!(
            "  {} {} duplicate path(s) skipped",
            style("·").dim(),
            report.duplicates
        );
    }
    if !report.skipped.is_empty() {
        println!(
            "  {} {} file(s) skipped (size/encoding/empty)",
            style("·").dim(),
            report.skipped.len()
        );
    }
}

fn print_import_report(import: &HarnessImportReport) {
    println!(
        "{} {} new, {} updated, {} unchanged",
        style("Done:").green().bold(),
        import.registered,
        import.updated,
        import.unchanged
    );
    if !import.failed.is_empty() {
        println!(
            "{}",
            style(format!("{} failed:", import.failed.len()))
                .red()
                .bold()
        );
        for (path, why) in &import.failed {
            println!("  {} {}: {why}", style("✗").red(), path.display());
        }
        if import.registered == 0 && import.updated == 0 {
            println!(
                "{}",
                style(
                    "Hint: all registrations failed — is the Kumiho backend running and reachable?"
                )
                .yellow()
            );
        }
    }
}
