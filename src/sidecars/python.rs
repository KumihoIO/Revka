//! Python interpreter detection for sidecar provisioning.
//!
//! Hard-fails with a download link when no usable Python ≥3.11 is found.
//! Probes candidates with `-c "import sys"` to skip the Windows Store stub
//! which exits 9009 and writes to stderr rather than actually running.

use anyhow::{Result, anyhow};
use std::path::{Path, PathBuf};
use std::process::Command;

const MIN_MAJOR: u32 = 3;
const MIN_MINOR: u32 = 11;

const DOWNLOAD_URL: &str = "https://www.python.org/downloads/";

/// Platform-native default name for the Python interpreter when spawning a
/// sidecar launcher. On Windows there is no `python3` executable — installs
/// from python.org expose `python.exe` (or `py.exe`), so hardcoding
/// `python3` causes "program not found" at spawn time. On Unix `python3`
/// remains the convention.
pub fn default_python_command() -> &'static str {
    if cfg!(windows) { "python" } else { "python3" }
}

/// Detect the `npm` executable on PATH. Returns the resolved absolute path
/// on success, or an error with a download link when missing. Used by the
/// session-manager sidecar installer (Node.js sidecar that drives the
/// Claude Agent SDK and codex CLI).
pub fn detect_npm() -> Result<PathBuf> {
    // On Windows the executable is npm.cmd; Command will resolve either name.
    let candidate = if cfg!(windows) { "npm.cmd" } else { "npm" };
    let output = Command::new(candidate)
        .arg("--version")
        .output()
        .map_err(|_| {
            anyhow!(
                "npm not found on PATH. Install Node.js LTS (includes npm) from \
                 https://nodejs.org/ or via your package manager (`brew install node`, \
                 `apt install nodejs npm`, `winget install OpenJS.NodeJS`)."
            )
        })?;

    if !output.status.success() {
        return Err(anyhow!(
            "npm found on PATH but `npm --version` exited with status {:?}. \
             Verify your Node.js install: {}",
            output.status.code(),
            "https://nodejs.org/"
        ));
    }

    Ok(PathBuf::from(candidate))
}

/// Resolve a usable Python interpreter, honoring `explicit` if provided.
///
/// Returns the absolute path to the interpreter on success.
pub fn detect_python(explicit: Option<&str>) -> Result<PathBuf> {
    if let Some(path) = explicit {
        let candidate = PathBuf::from(path);
        return probe(&candidate).ok_or_else(|| missing_python_error(Some(path)));
    }

    let candidates: &[&str] = if cfg!(windows) {
        &["py", "python3", "python"]
    } else {
        &["python3", "python"]
    };

    for name in candidates {
        if let Some(resolved) = probe(Path::new(name)) {
            return Ok(resolved);
        }
    }

    Err(missing_python_error(None))
}

/// Probe a candidate interpreter. Returns its resolved absolute path if it
/// runs, reports version ≥ MIN_MAJOR.MIN_MINOR, and is not a Store stub.
fn probe(candidate: &Path) -> Option<PathBuf> {
    let output = Command::new(candidate)
        .args([
            "-c",
            "import sys; print(sys.executable); print(sys.version_info[0]); print(sys.version_info[1])",
        ])
        .output()
        .ok()?;

    if !output.status.success() {
        return None;
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    let mut lines = stdout.lines();
    let exe = lines.next()?.trim();
    let major: u32 = lines.next()?.trim().parse().ok()?;
    let minor: u32 = lines.next()?.trim().parse().ok()?;

    if exe.is_empty() {
        return None;
    }
    if major < MIN_MAJOR || (major == MIN_MAJOR && minor < MIN_MINOR) {
        return None;
    }

    Some(PathBuf::from(exe))
}

fn missing_python_error(tried: Option<&str>) -> anyhow::Error {
    let prefix = match tried {
        Some(p) => format!("Python interpreter `{p}` is unusable"),
        None => format!("No usable Python ≥{MIN_MAJOR}.{MIN_MINOR} found on PATH"),
    };
    anyhow!(
        "{prefix}.\n\n\
         Construct requires Python {MIN_MAJOR}.{MIN_MINOR}+ to run its MCP sidecars.\n\
         Install it from {DOWNLOAD_URL} and re-run `construct install --sidecars-only`.\n\n\
         On Windows, avoid the Microsoft Store Python stub — install from python.org directly."
    )
}
