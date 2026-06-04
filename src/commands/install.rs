//! `revka install` — unified post-build install flow.
//!
//! Today only the `--sidecars-only` path is implemented: it provisions the
//! Kumiho + Operator Python MCP sidecars under `~/.revka/`. The full
//! install flow (prerequisite checks, onboard, dashboard launch) will migrate
//! into this module over time; until then, `install.sh` / `setup.bat` remain
//! canonical for a full install.

use std::path::PathBuf;

use anyhow::{Result, anyhow};

use crate::sidecars::{self, SidecarInstallOptions};

/// Options for `revka install`.
#[derive(Debug, Default, Clone)]
pub struct InstallOptions {
    /// Install only the Python MCP sidecars (Kumiho + Operator).
    pub sidecars_only: bool,
    /// Skip installing the Kumiho sidecar.
    pub skip_kumiho: bool,
    /// Skip installing the Operator sidecar.
    pub skip_operator: bool,
    /// Opt-in: install the Node.js Session Manager sidecar.
    ///
    /// Default `false`. When false, agents run via the subprocess path
    /// (`claude --print` + `codex exec`) which uses each CLI's own OAuth
    /// → routes spawned-agent calls through the user's Claude Pro/Max +
    /// Codex CLI subscriptions. When true, agents run via the Claude
    /// Agent SDK in the Session Manager — supports streaming timeline
    /// events but requires `ANTHROPIC_API_KEY` (pay-per-token).
    pub with_session_manager: bool,
    /// Print what would be done without executing.
    pub dry_run: bool,
    /// Optional explicit Python interpreter.
    pub python: Option<String>,
    /// Dev-mode: install operator-mcp from a local Revka repo
    /// rather than the binary's embedded snapshot. Avoids the Rust
    /// rebuild cycle when iterating on the Python side.
    pub from_source: Option<PathBuf>,
}

/// Run the install command with the given options.
pub async fn run(opts: InstallOptions) -> Result<()> {
    if !opts.sidecars_only {
        return Err(anyhow!(
            "Full install is not yet implemented as a Rust subcommand.\n\
             Use one of:\n  \
               revka install --sidecars-only    # install Kumiho + Operator Python MCP sidecars\n  \
               ./install.sh                         # full POSIX install (source build + sidecars + onboard)\n  \
               setup.bat                            # full Windows install"
        ));
    }

    sidecars::install_sidecars(&SidecarInstallOptions {
        skip_kumiho: opts.skip_kumiho,
        skip_operator: opts.skip_operator,
        with_session_manager: opts.with_session_manager,
        dry_run: opts.dry_run,
        python: opts.python,
        from_source: opts.from_source,
    })
    .await
}
