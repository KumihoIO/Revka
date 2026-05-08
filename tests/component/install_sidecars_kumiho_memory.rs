//! Sanity tests for `scripts/install-sidecars.{sh,bat}` ensuring that the
//! Kumiho sidecar venv installs the `kumiho_memory` package alongside the
//! bare `kumiho` package.
//!
//! Without `kumiho_memory`, the high-level memory tools mandated by the
//! Construct session-bootstrap prompt (`kumiho_memory_engage`,
//! `kumiho_memory_reflect`, etc.) are NOT registered in the MCP server,
//! and the agent fires tool calls into the void on stock install.
//! Coherence audit 2026-05, row 13.

use std::path::PathBuf;

fn repo_root() -> PathBuf {
    let manifest_dir = env!("CARGO_MANIFEST_DIR");
    PathBuf::from(manifest_dir)
}

#[test]
fn install_sidecars_sh_installs_kumiho_memory() {
    let path = repo_root().join("scripts").join("install-sidecars.sh");
    let content = std::fs::read_to_string(&path)
        .unwrap_or_else(|e| panic!("failed to read {}: {e}", path.display()));

    let pip_lines: Vec<&str> = content
        .lines()
        .filter(|l| l.contains("pip install") && l.contains("kumiho"))
        .collect();
    assert!(
        !pip_lines.is_empty(),
        "expected at least one `pip install kumiho...` line in {}",
        path.display()
    );

    let installs_kumiho_memory = pip_lines
        .iter()
        .any(|l| l.contains("kumiho_memory") || l.contains("kumiho-memory"));
    assert!(
        installs_kumiho_memory,
        "scripts/install-sidecars.sh must install `kumiho_memory` so the \
         high-level memory tools (engage / reflect / recall / consolidate / \
         dream_state) are registered in the sidecar MCP. \
         Found pip lines: {pip_lines:?}"
    );
}

#[test]
fn install_sidecars_bat_installs_kumiho_memory() {
    let path = repo_root().join("scripts").join("install-sidecars.bat");
    let content = std::fs::read_to_string(&path)
        .unwrap_or_else(|e| panic!("failed to read {}: {e}", path.display()));

    let pip_lines: Vec<&str> = content
        .lines()
        .filter(|l| l.contains("pip install") && l.contains("kumiho"))
        .collect();
    assert!(
        !pip_lines.is_empty(),
        "expected at least one `pip install kumiho...` line in {}",
        path.display()
    );

    let installs_kumiho_memory = pip_lines
        .iter()
        .any(|l| l.contains("kumiho_memory") || l.contains("kumiho-memory"));
    assert!(
        installs_kumiho_memory,
        "scripts/install-sidecars.bat must install `kumiho_memory` so the \
         high-level memory tools (engage / reflect / recall / consolidate / \
         dream_state) are registered in the sidecar MCP. \
         Found pip lines: {pip_lines:?}"
    );
}
