//! Sidecar installation logic.
//!
//! At compile time we embed:
//! - The two Python launcher scripts (as strings).
//! - The `operator-mcp/` Python package source (via `include_dir!`).
//!
//! At install time we detect Python, create per-sidecar venvs, pip-install
//! `kumiho[mcp]` into the Kumiho venv, extract the embedded operator-mcp
//! source into a temp dir, pip-install it into the Operator venv, sync the
//! flat Operator package tree used by dashboard/scheduler readers, and
//! materialize the launchers. No shell scripts involved.

use anyhow::{Context, Result, anyhow};
use include_dir::{Dir, DirEntry, include_dir};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use super::python::{detect_npm, detect_python};
use super::{construct_root, kumiho_launcher_path, operator_launcher_path};

const KUMIHO_LAUNCHER_SRC: &str = include_str!("../../resources/sidecars/run_kumiho_mcp.py");
const KUMIHO_SDK_BRIDGE_SRC: &str = include_str!("../../resources/sidecars/kumiho_sdk_bridge.py");
const OPERATOR_LAUNCHER_SRC: &str = include_str!("../../resources/sidecars/run_operator_mcp.py");

static OPERATOR_MCP_SRC: Dir<'_> = include_dir!("$CARGO_MANIFEST_DIR/operator-mcp");

/// Embedded session-manager sidecar tree (TypeScript build output + package
/// manifest). Cargo's package include rules in `Cargo.toml` keep this to
/// `dist/` + `package.json` only — no `node_modules/` (that gets installed
/// fresh at deploy time via `npm install --omit=dev`) and no `src/` (we
/// ship the prebuilt JS, not the source).
static SESSION_MANAGER_SRC: Dir<'_> =
    include_dir!("$CARGO_MANIFEST_DIR/operator-mcp/session-manager");

/// The PyPI version pin for the Kumiho package. Must match
/// `operator-mcp/requirements.txt`.
const KUMIHO_PIN: &str = "kumiho[mcp]>=0.9.20";

#[derive(Debug, Default, Clone)]
pub struct SidecarInstallOptions {
    pub skip_kumiho: bool,
    pub skip_operator: bool,
    /// Opt-in: install the Node.js Session Manager sidecar.
    ///
    /// Defaults to `false`. The Session Manager drives spawned agents via
    /// the Claude Agent SDK, which only accepts `ANTHROPIC_API_KEY`
    /// (pay-per-token) — it cannot use the user's Claude Pro/Max
    /// subscription OAuth. The default subprocess path
    /// (`claude --print` + `codex exec`) uses each CLI's own OAuth and
    /// routes spawned-agent calls against the subscription, which is
    /// roughly 15–30× cheaper for equivalent work. See
    /// https://github.com/anthropics/claude-agent-sdk-python/issues/559.
    pub with_session_manager: bool,
    pub dry_run: bool,
    pub python: Option<String>,
    /// Dev-mode: install `operator-mcp` from a local source tree instead of
    /// the embedded copy. Path should point at a construct-os repo root —
    /// we'll use `<path>/operator-mcp/` as the pip install source. Lets
    /// developers iterate on the Python side without rebuilding the Rust
    /// binary (whose `include_dir!` snapshot is fixed at compile time).
    pub from_source: Option<PathBuf>,
}

pub async fn install_sidecars(opts: &SidecarInstallOptions) -> Result<()> {
    let python = detect_python(opts.python.as_deref())?;
    eprintln!("==> construct install --sidecars-only");
    eprintln!("    python: {}", python.display());

    let root = construct_root()?;
    std::fs::create_dir_all(&root).with_context(|| format!("creating {}", root.display()))?;

    if !opts.skip_operator {
        install_operator(&python, opts.dry_run, opts.from_source.as_deref())?;
    } else {
        eprintln!("    [skip] Operator (--skip-operator)");
    }

    if !opts.skip_kumiho {
        install_kumiho(&python, opts.dry_run)?;
    } else {
        eprintln!("    [skip] Kumiho (--skip-kumiho)");
    }

    if opts.with_session_manager {
        // Best-effort: a session-manager install failure (missing npm,
        // network blip) shouldn't tank the whole sidecar provisioning.
        // Operator falls back to direct subprocess spawning when the
        // session-manager isn't available, so the runtime still works
        // — just without streaming timeline events.
        if let Err(err) = install_session_manager(opts.dry_run, opts.from_source.as_deref()) {
            eprintln!(
                "    [warn] Session manager install failed: {err:#}\n    \
                 Operator will fall back to subprocess mode for spawned \
                 agents (uses Claude Pro/Max subscription via OAuth — see \
                 below). Re-run with `--with-session-manager` after fixing \
                 the underlying issue (typically: install Node.js + npm)."
            );
        }
    } else {
        eprintln!(
            "    [info] Session Manager (Node.js sidecar) NOT installed.\n    \
                    Operator-spawned agents will use direct subprocess mode\n    \
                    (`claude --print` + `codex exec`), which routes calls\n    \
                    through each CLI's own OAuth → your Claude Pro/Max + Codex\n    \
                    CLI subscriptions. No per-call API spend on spawned agents.\n    \
                    To enable the streaming-event sidecar (uses ANTHROPIC_API_KEY,\n    \
                    NOT subscription), re-run with `--with-session-manager`."
        );
    }

    eprintln!("==> sidecars ready");
    eprintln!("    kumiho   : {}", kumiho_launcher_path()?.display());
    eprintln!("    operator : {}", operator_launcher_path()?.display());
    Ok(())
}

fn install_kumiho(python: &Path, dry_run: bool) -> Result<()> {
    let dir = construct_root()?.join("kumiho");
    let venv = dir.join("venv");
    let launcher = dir.join("run_kumiho_mcp.py");
    let bridge = dir.join("kumiho_sdk_bridge.py");

    eprintln!("==> Installing Kumiho MCP → {}", dir.display());
    if dry_run {
        eprintln!("    + create {}", venv.display());
        eprintln!("    + pip install {KUMIHO_PIN}");
        eprintln!("    + write {}", launcher.display());
        eprintln!("    + write {}", bridge.display());
        return Ok(());
    }

    std::fs::create_dir_all(&dir).with_context(|| format!("creating {}", dir.display()))?;
    ensure_venv(python, &venv)?;
    let venv_py = venv_python(&venv)?;

    run(
        &venv_py,
        &["-m", "pip", "install", "--quiet", "--upgrade", "pip"],
    )?;
    run(&venv_py, &["-m", "pip", "install", "--quiet", KUMIHO_PIN])?;
    eprintln!("    [ok] kumiho[mcp] installed");

    write_launcher(&launcher, KUMIHO_LAUNCHER_SRC)?;
    eprintln!("    [ok] launcher: {}", launcher.display());
    write_launcher(&bridge, KUMIHO_SDK_BRIDGE_SRC)?;
    eprintln!("    [ok] sdk bridge: {}", bridge.display());
    Ok(())
}

fn install_operator(python: &Path, dry_run: bool, from_source: Option<&Path>) -> Result<()> {
    let dir = construct_root()?.join("operator_mcp");
    let venv = dir.join("venv");
    let launcher = dir.join("run_operator_mcp.py");

    eprintln!("==> Installing Operator MCP → {}", dir.display());
    if dry_run {
        match from_source {
            Some(repo) => eprintln!(
                "    + use local source: {}",
                repo.join("operator-mcp").display()
            ),
            None => eprintln!("    + extract embedded operator-mcp source"),
        }
        eprintln!("    + create {}", venv.display());
        eprintln!("    + pip install operator-mcp");
        eprintln!("    + sync flat operator package into {}", dir.display());
        eprintln!("    + write {}", launcher.display());
        return Ok(());
    }

    std::fs::create_dir_all(&dir).with_context(|| format!("creating {}", dir.display()))?;

    // Determine the pip install source. With --from-source we point pip
    // straight at the repo's operator-mcp/ dir and skip the embedded
    // extraction entirely. The TempDir holder keeps the staging dir alive
    // until after the pip install completes — drop order matters here.
    let (install_src, _staging_holder): (PathBuf, Option<tempfile::TempDir>) = match from_source {
        Some(repo_root) => {
            let local_src = repo_root.join("operator-mcp");
            let pyproject = local_src.join("pyproject.toml");
            if !pyproject.exists() {
                return Err(anyhow!(
                    "--from-source {} doesn't look like a construct-os repo: \
                     missing operator-mcp/pyproject.toml",
                    repo_root.display()
                ));
            }
            eprintln!("    [ok] using local source: {}", local_src.display());
            (local_src, None)
        }
        None => {
            let staging = tempfile::tempdir().context("creating operator-mcp staging dir")?;
            extract_operator_source(staging.path())?;
            eprintln!("    [ok] extracted operator-mcp source → staging");
            let path = staging.path().to_path_buf();
            (path, Some(staging))
        }
    };

    ensure_venv(python, &venv)?;
    let venv_py = venv_python(&venv)?;

    run(
        &venv_py,
        &["-m", "pip", "install", "--quiet", "--upgrade", "pip"],
    )?;
    let install_src_str = install_src.to_string_lossy().to_string();
    // --from-source iteration: force-reinstall + skip deps so pip doesn't
    // see the same version-pin already installed and no-op. Skipping deps
    // keeps the loop fast (mcp/httpx/etc don't get re-resolved every time).
    // Embedded path stays unchanged — end-user installs don't need either.
    if from_source.is_some() {
        run(
            &venv_py,
            &[
                "-m",
                "pip",
                "install",
                "--quiet",
                "--force-reinstall",
                "--no-deps",
                &install_src_str,
            ],
        )?;
    } else {
        run(
            &venv_py,
            &["-m", "pip", "install", "--quiet", &install_src_str],
        )?;
    }
    eprintln!("    [ok] operator-mcp installed");

    sync_operator_flat_package(&install_src.join("operator_mcp"), &dir)?;
    eprintln!("    [ok] flat operator package synced: {}", dir.display());

    write_launcher(&launcher, OPERATOR_LAUNCHER_SRC)?;
    eprintln!("    [ok] launcher: {}", launcher.display());
    Ok(())
}

/// Extract the embedded `operator-mcp/` tree into `dest`, skipping the files
/// that pip doesn't need (tests, session-manager, node bits, caches).
fn extract_operator_source(dest: &Path) -> Result<()> {
    walk_dir(&OPERATOR_MCP_SRC, dest)?;
    for required in ["pyproject.toml", "operator_mcp/__init__.py"] {
        if !dest.join(required).exists() {
            return Err(anyhow!(
                "embedded operator-mcp source missing `{required}` after extraction; \
                 check Cargo.toml `include` whitelist"
            ));
        }
    }
    Ok(())
}

/// Install the Node.js session-manager sidecar.
///
/// Lays down the prebuilt `dist/` + `package.json` into
/// `~/.construct/operator_mcp/session-manager/`, then runs
/// `npm install --omit=dev` to fetch its node_modules. The Operator MCP
/// (Python) discovers and spawns this sidecar at runtime to drive the
/// Claude Agent SDK and codex CLI with structured streaming events.
///
/// Subprocess fallback in `agents.tool_create_agent` is what runs when
/// this sidecar isn't installed — works, but loses the streaming
/// timeline + cross-turn session preservation. So fresh installs without
/// this step end up in degraded mode by default.
fn install_session_manager(dry_run: bool, from_source: Option<&Path>) -> Result<()> {
    let dir = construct_root()?
        .join("operator_mcp")
        .join("session-manager");

    eprintln!("==> Installing Session Manager → {}", dir.display());
    if dry_run {
        match from_source {
            Some(repo) => eprintln!(
                "    + use local session-manager dist: {}",
                repo.join("operator-mcp").join("session-manager").display()
            ),
            None => eprintln!("    + extract embedded session-manager dist + package.json"),
        }
        eprintln!("    + npm install --omit=dev");
        return Ok(());
    }

    // Detect npm BEFORE writing files so a missing-npm machine doesn't
    // get a half-installed session-manager dir it has to clean up.
    let npm = detect_npm()?;

    std::fs::create_dir_all(&dir).with_context(|| format!("creating {}", dir.display()))?;

    if let Some(repo_root) = from_source {
        let local_src = repo_root.join("operator-mcp").join("session-manager");
        sync_session_manager_from_local(&local_src, &dir)?;
        eprintln!("    [ok] local dist + package.json laid down");
    } else {
        // Write embedded dist/ tree + package.json. Same shape as
        // extract_operator_source but no need to filter — Cargo's package
        // include already restricts SESSION_MANAGER_SRC to dist + package.json.
        walk_session_manager(&SESSION_MANAGER_SRC, &dir)?;
        eprintln!("    [ok] dist + package.json laid down");
    }
    let dist_index = dir.join("dist").join("index.js");
    if !dist_index.exists() {
        return Err(anyhow!(
            "embedded session-manager missing dist/index.js after extraction; \
             check Cargo.toml `include` whitelist (need /operator-mcp/session-manager/dist/**/*)"
        ));
    }
    // npm install --omit=dev fetches the production deps listed in
    // package.json (no dev deps — TypeScript compiler etc. aren't needed
    // since dist/ is prebuilt). The session-manager isn't a publishable
    // package so we don't need --no-save quirks.
    let mut cmd = Command::new(&npm);
    cmd.arg("install")
        .arg("--omit=dev")
        .arg("--no-audit")
        .arg("--no-fund")
        .current_dir(&dir);
    let status = cmd
        .status()
        .with_context(|| format!("running `{} install` in {}", npm.display(), dir.display()))?;
    if !status.success() {
        return Err(anyhow!(
            "`npm install` failed with status {:?}. Check npm output above; \
             a network blip is the most common cause — re-running usually fixes it.",
            status.code()
        ));
    }
    eprintln!("    [ok] session-manager dependencies installed");
    eprintln!(
        "    [ok] entrypoint: node {}",
        dir.join("dist").join("index.js").display()
    );
    Ok(())
}

fn sync_session_manager_from_local(src: &Path, dest: &Path) -> Result<()> {
    let package_json = src.join("package.json");
    let dist = src.join("dist");
    let dist_index = dist.join("index.js");
    if !package_json.is_file() || !dist_index.is_file() {
        return Err(anyhow!(
            "--from-source {} doesn't have a built session-manager; expected \
             operator-mcp/session-manager/package.json and dist/index.js. \
             Run `npm run build` in operator-mcp first.",
            src.display()
        ));
    }

    std::fs::create_dir_all(dest).with_context(|| format!("creating {}", dest.display()))?;
    let dest_dist = dest.join("dist");
    if dest_dist.exists() {
        std::fs::remove_dir_all(&dest_dist)
            .with_context(|| format!("removing {}", dest_dist.display()))?;
    }
    copy_dir_recursive(&dist, &dest_dist)?;
    std::fs::copy(&package_json, dest.join("package.json")).with_context(|| {
        format!(
            "copying {} to {}",
            package_json.display(),
            dest.join("package.json").display()
        )
    })?;
    Ok(())
}

fn copy_dir_recursive(src: &Path, dest: &Path) -> Result<()> {
    std::fs::create_dir_all(dest).with_context(|| format!("creating {}", dest.display()))?;
    for entry in std::fs::read_dir(src).with_context(|| format!("reading {}", src.display()))? {
        let entry = entry.with_context(|| format!("reading entry under {}", src.display()))?;
        let src_path = entry.path();
        let dest_path = dest.join(entry.file_name());
        let file_type = entry
            .file_type()
            .with_context(|| format!("reading file type for {}", src_path.display()))?;
        if file_type.is_dir() {
            copy_dir_recursive(&src_path, &dest_path)?;
        } else if file_type.is_file() {
            if let Some(parent) = dest_path.parent() {
                std::fs::create_dir_all(parent)
                    .with_context(|| format!("creating {}", parent.display()))?;
            }
            std::fs::copy(&src_path, &dest_path).with_context(|| {
                format!("copying {} to {}", src_path.display(), dest_path.display())
            })?;
        }
    }
    Ok(())
}

/// Walk variant for the dedicated `SESSION_MANAGER_SRC` tree. The tree's
/// content is already pre-filtered by Cargo's package include rules, so we
/// don't need to re-apply the operator-mcp `is_relevant` filter here.
fn walk_session_manager(dir: &Dir<'_>, dest: &Path) -> Result<()> {
    for entry in dir.entries() {
        let rel = entry.path();
        match entry {
            DirEntry::Dir(sub) => {
                let out = dest.join(rel);
                std::fs::create_dir_all(&out)
                    .with_context(|| format!("creating {}", out.display()))?;
                walk_session_manager(sub, dest)?;
            }
            DirEntry::File(file) => {
                let out = dest.join(rel);
                if let Some(parent) = out.parent() {
                    std::fs::create_dir_all(parent)
                        .with_context(|| format!("creating {}", parent.display()))?;
                }
                std::fs::write(&out, file.contents())
                    .with_context(|| format!("writing {}", out.display()))?;
            }
        }
    }
    Ok(())
}

fn walk_dir(dir: &Dir<'_>, dest: &Path) -> Result<()> {
    for entry in dir.entries() {
        let rel = entry.path();
        if !is_relevant(rel) {
            continue;
        }
        match entry {
            DirEntry::Dir(sub) => {
                let out = dest.join(rel);
                std::fs::create_dir_all(&out)
                    .with_context(|| format!("creating {}", out.display()))?;
                walk_dir(sub, dest)?;
            }
            DirEntry::File(file) => {
                let out = dest.join(rel);
                if let Some(parent) = out.parent() {
                    std::fs::create_dir_all(parent)
                        .with_context(|| format!("creating {}", parent.display()))?;
                }
                std::fs::write(&out, file.contents())
                    .with_context(|| format!("writing {}", out.display()))?;
            }
        }
    }
    Ok(())
}

fn is_relevant(rel: &Path) -> bool {
    let s = rel.to_string_lossy();
    if s.contains("__pycache__")
        || s.contains("/.venv")
        || s.contains("/venv/")
        || s.starts_with("tests/")
        || s.starts_with("session-manager/")
        || s.starts_with("node_modules/")
        || s.ends_with(".pyc")
    {
        return false;
    }
    true
}

fn sync_operator_flat_package(package_src: &Path, install_dir: &Path) -> Result<()> {
    if !package_src.join("__init__.py").is_file() {
        return Err(anyhow!(
            "operator_mcp package source missing __init__.py: {}",
            package_src.display()
        ));
    }
    std::fs::create_dir_all(install_dir)
        .with_context(|| format!("creating {}", install_dir.display()))?;
    sync_operator_flat_dir(package_src, install_dir)
}

fn sync_operator_flat_dir(src: &Path, dest: &Path) -> Result<()> {
    let mut source_names = std::collections::HashSet::new();
    for entry in std::fs::read_dir(src).with_context(|| format!("reading {}", src.display()))? {
        let entry = entry.with_context(|| format!("reading entry under {}", src.display()))?;
        let name = entry.file_name();
        let name_str = name.to_string_lossy();
        let src_path = entry.path();
        if should_skip_operator_flat_source_entry(&src_path, &name_str) {
            continue;
        }
        source_names.insert(name);
        let dest_path = dest.join(entry.file_name());
        let file_type = entry
            .file_type()
            .with_context(|| format!("reading file type for {}", src_path.display()))?;
        if file_type.is_dir() {
            std::fs::create_dir_all(&dest_path)
                .with_context(|| format!("creating {}", dest_path.display()))?;
            sync_operator_flat_dir(&src_path, &dest_path)?;
        } else if file_type.is_file() {
            if let Some(parent) = dest_path.parent() {
                std::fs::create_dir_all(parent)
                    .with_context(|| format!("creating {}", parent.display()))?;
            }
            std::fs::copy(&src_path, &dest_path).with_context(|| {
                format!("copying {} to {}", src_path.display(), dest_path.display())
            })?;
        }
    }

    if !dest.exists() {
        return Ok(());
    }
    for entry in std::fs::read_dir(dest).with_context(|| format!("reading {}", dest.display()))? {
        let entry = entry.with_context(|| format!("reading entry under {}", dest.display()))?;
        let name = entry.file_name();
        let name_str = name.to_string_lossy();
        let dest_path = entry.path();
        if should_preserve_operator_flat_dest_entry(&name_str) {
            continue;
        }
        if should_skip_operator_flat_source_entry(&dest_path, &name_str)
            || !source_names.contains(&name)
        {
            remove_operator_flat_path(&dest_path)?;
        }
    }
    Ok(())
}

fn should_skip_operator_flat_source_entry(path: &Path, name: &str) -> bool {
    if matches!(
        name,
        "__pycache__" | ".pytest_cache" | ".mypy_cache" | ".ruff_cache" | ".venv" | "venv"
    ) {
        return true;
    }
    matches!(
        path.extension().and_then(|ext| ext.to_str()),
        Some("pyc" | "pyo")
    )
}

fn should_preserve_operator_flat_dest_entry(name: &str) -> bool {
    matches!(
        name,
        "venv"
            | "session-manager"
            | "session-manager.sock"
            | "agents"
            | "runlogs"
            | "diffs"
            | "checkpoints"
    ) || name.ends_with(".json")
        || name.ends_with(".jsonl")
        || name.ends_with(".db")
        || name.ends_with(".sqlite")
        || name.ends_with(".sqlite3")
        || name.ends_with(".sock")
        || name.ends_with(".lock")
}

fn remove_operator_flat_path(path: &Path) -> Result<()> {
    let metadata = std::fs::symlink_metadata(path)
        .with_context(|| format!("reading metadata for {}", path.display()))?;
    if metadata.is_dir() {
        std::fs::remove_dir_all(path).with_context(|| format!("removing {}", path.display()))?;
    } else {
        std::fs::remove_file(path).with_context(|| format!("removing {}", path.display()))?;
    }
    Ok(())
}

fn ensure_venv(python: &Path, venv: &Path) -> Result<()> {
    if venv_python(venv).is_ok() {
        eprintln!("    [skip] venv already exists: {}", venv.display());
        return Ok(());
    }
    let venv_str = venv.to_string_lossy().to_string();
    run(python, &["-m", "venv", &venv_str])?;
    eprintln!("    [ok] venv created: {}", venv.display());
    Ok(())
}

fn venv_python(venv: &Path) -> Result<PathBuf> {
    let candidates = if cfg!(windows) {
        vec![venv.join("Scripts").join("python.exe")]
    } else {
        vec![
            venv.join("bin").join("python3"),
            venv.join("bin").join("python"),
        ]
    };
    for c in candidates {
        if c.exists() {
            return Ok(c);
        }
    }
    Err(anyhow!("venv python not found under {}", venv.display()))
}

fn write_launcher(path: &Path, contents: &str) -> Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .with_context(|| format!("creating {}", parent.display()))?;
    }
    std::fs::write(path, contents).with_context(|| format!("writing {}", path.display()))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut perm = std::fs::metadata(path)?.permissions();
        perm.set_mode(0o755);
        std::fs::set_permissions(path, perm)
            .with_context(|| format!("chmod +x {}", path.display()))?;
    }
    Ok(())
}

fn run(program: &Path, args: &[&str]) -> Result<()> {
    let status = Command::new(program)
        .args(args)
        .stdin(Stdio::null())
        .status()
        .with_context(|| format!("invoking {} {}", program.display(), args.join(" ")))?;
    if !status.success() {
        return Err(anyhow!(
            "`{} {}` exited with status {}",
            program.display(),
            args.join(" "),
            status.code().unwrap_or(-1)
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sync_operator_flat_package_updates_code_and_preserves_runtime_state() -> Result<()> {
        let tmp = tempfile::tempdir()?;
        let src = tmp.path().join("src").join("operator_mcp");
        let dest = tmp.path().join("install");

        std::fs::create_dir_all(src.join("workflow").join("__pycache__"))?;
        std::fs::write(src.join("__init__.py"), "")?;
        std::fs::write(src.join("workflow").join("executor.py"), "new executor\n")?;
        std::fs::write(src.join("workflow").join("generated.pyc"), "bytecode")?;
        std::fs::write(
            src.join("workflow")
                .join("__pycache__")
                .join("executor.pyc"),
            "cache",
        )?;

        std::fs::create_dir_all(dest.join("workflow").join("__pycache__"))?;
        std::fs::write(dest.join("__init__.py"), "old init\n")?;
        std::fs::write(dest.join("workflow").join("executor.py"), "old executor\n")?;
        std::fs::write(dest.join("workflow").join("stale_module.py"), "stale\n")?;
        std::fs::write(
            dest.join("workflow")
                .join("__pycache__")
                .join("executor.pyc"),
            "stale cache",
        )?;

        std::fs::create_dir_all(dest.join("venv"))?;
        std::fs::write(dest.join("venv").join("keep.txt"), "venv")?;
        std::fs::create_dir_all(dest.join("session-manager"))?;
        std::fs::write(dest.join("session-manager").join("package.json"), "{}")?;
        std::fs::create_dir_all(dest.join("runlogs"))?;
        std::fs::write(dest.join("runlogs").join("agent.jsonl"), "{}\n")?;
        std::fs::write(dest.join("workflow_presets.json"), "{}")?;

        sync_operator_flat_package(&src, &dest)?;

        assert_eq!(
            std::fs::read_to_string(dest.join("workflow").join("executor.py"))?,
            "new executor\n"
        );
        assert!(!dest.join("workflow").join("stale_module.py").exists());
        assert!(!dest.join("workflow").join("generated.pyc").exists());
        assert!(
            !dest
                .join("workflow")
                .join("__pycache__")
                .join("executor.pyc")
                .exists()
        );
        assert!(dest.join("venv").join("keep.txt").exists());
        assert!(dest.join("session-manager").join("package.json").exists());
        assert!(dest.join("runlogs").join("agent.jsonl").exists());
        assert!(dest.join("workflow_presets.json").exists());
        Ok(())
    }

    #[test]
    fn sync_session_manager_from_local_replaces_embedded_dist() -> Result<()> {
        let tmp = tempfile::tempdir()?;
        let src = tmp.path().join("src").join("session-manager");
        let dest = tmp.path().join("install").join("session-manager");

        std::fs::create_dir_all(src.join("dist").join("providers"))?;
        std::fs::write(src.join("package.json"), r#"{"name":"local"}"#)?;
        std::fs::write(src.join("dist").join("index.js"), "local index\n")?;
        std::fs::write(
            src.join("dist").join("providers").join("codex.js"),
            "local codex\n",
        )?;

        std::fs::create_dir_all(dest.join("dist").join("providers"))?;
        std::fs::write(dest.join("package.json"), r#"{"name":"embedded"}"#)?;
        std::fs::write(
            dest.join("dist").join("providers").join("codex.js"),
            "old codex\n",
        )?;
        std::fs::write(dest.join("dist").join("stale.js"), "stale\n")?;

        sync_session_manager_from_local(&src, &dest)?;

        assert_eq!(
            std::fs::read_to_string(dest.join("dist").join("providers").join("codex.js"))?,
            "local codex\n"
        );
        assert_eq!(
            std::fs::read_to_string(dest.join("package.json"))?,
            r#"{"name":"local"}"#
        );
        assert!(!dest.join("dist").join("stale.js").exists());
        Ok(())
    }
}
