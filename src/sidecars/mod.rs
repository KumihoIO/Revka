//! Pure-Rust sidecar provisioning.
//!
//! Replaces the legacy `install-sidecars.{sh,bat}` scripts. Creates per-sidecar
//! Python venvs under `~/.revka/{kumiho,operator_mcp}/` and materializes
//! embedded launchers so Revka itself does not depend on any particular
//! Python on PATH at runtime.

mod install;
pub mod python;

pub use install::{SidecarInstallOptions, install_sidecars};

use anyhow::Result;
use std::path::PathBuf;

/// Installation status of a single sidecar.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SidecarStatus {
    Ready,
    Missing,
}

/// Return the `~/.revka` root directory.
pub fn revka_root() -> Result<PathBuf> {
    let home = directories::UserDirs::new()
        .map(|u| u.home_dir().to_path_buf())
        .ok_or_else(|| anyhow::anyhow!("could not determine home directory"))?;
    Ok(home.join(".revka"))
}

/// Path to the Kumiho sidecar launcher.
pub fn kumiho_launcher_path() -> Result<PathBuf> {
    Ok(revka_root()?.join("kumiho").join("run_kumiho_mcp.py"))
}

/// Path to the Operator sidecar launcher.
pub fn operator_launcher_path() -> Result<PathBuf> {
    Ok(revka_root()?
        .join("operator_mcp")
        .join("run_operator_mcp.py"))
}

/// Probe current state of a sidecar (both venv interpreter and launcher).
pub fn status(sidecar: Sidecar) -> SidecarStatus {
    let Ok(root) = revka_root() else {
        return SidecarStatus::Missing;
    };
    let (dir, launcher) = match sidecar {
        Sidecar::Kumiho => (root.join("kumiho"), "run_kumiho_mcp.py"),
        Sidecar::Operator => (root.join("operator_mcp"), "run_operator_mcp.py"),
    };
    let interp = if cfg!(windows) {
        dir.join("venv").join("Scripts").join("python.exe")
    } else {
        dir.join("venv").join("bin").join("python3")
    };
    if interp.exists() && dir.join(launcher).exists() {
        SidecarStatus::Ready
    } else {
        SidecarStatus::Missing
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Sidecar {
    Kumiho,
    Operator,
}

/// Ensure both sidecars are provisioned. If any are missing and `interactive`
/// is true, prompt the user before installing. Otherwise install silently.
///
/// This is the hook point called from `inject_kumiho` / `inject_operator`
/// and every command that launches an MCP-consuming agent.
pub async fn ensure_sidecars_ready(interactive: bool) -> Result<()> {
    let kumiho = status(Sidecar::Kumiho);
    let operator = status(Sidecar::Operator);
    if kumiho == SidecarStatus::Ready && operator == SidecarStatus::Ready {
        return Ok(());
    }

    if interactive && !prompt_install(kumiho, operator)? {
        anyhow::bail!(
            "sidecars not installed; re-run with `revka install --sidecars-only` when ready"
        );
    }

    install_sidecars(&SidecarInstallOptions {
        skip_kumiho: kumiho == SidecarStatus::Ready,
        skip_operator: operator == SidecarStatus::Ready,
        ..Default::default()
    })
    .await
}

fn prompt_install(kumiho: SidecarStatus, operator: SidecarStatus) -> Result<bool> {
    use std::io::{BufRead, Write};
    let mut missing = Vec::new();
    if kumiho == SidecarStatus::Missing {
        missing.push("Kumiho");
    }
    if operator == SidecarStatus::Missing {
        missing.push("Operator");
    }
    eprintln!(
        "==> Revka needs to install the {} MCP sidecar{} (one-time, ~60s).",
        missing.join(" + "),
        if missing.len() == 1 { "" } else { "s" }
    );
    eprint!("    Install now? [Y/n] ");
    std::io::stderr().flush().ok();

    let stdin = std::io::stdin();
    let mut line = String::new();
    stdin.lock().read_line(&mut line)?;
    let ans = line.trim().to_lowercase();
    Ok(ans.is_empty() || ans == "y" || ans == "yes")
}
