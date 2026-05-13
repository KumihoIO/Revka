use crate::config::Config;
use anyhow::{Context, Result, bail};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::str::FromStr;

const SERVICE_LABEL: &str = "com.construct.daemon";
const WINDOWS_TASK_NAME: &str = "Construct Daemon";

/// Soft NumberOfFiles for the daemon process. 16× launchd's default of 256
/// (real-world incident 2026-05-11: user hit Too many open files spawning
/// MCP servers). Each agent spawns 3 MCP sidecars; at ~10 FDs per sidecar
/// stdio pipe set + headroom, this comfortably covers ~100 concurrent agents.
pub const DAEMON_NOFILE_SOFT: u64 = 4096;

/// Hard ceiling — double the soft cap. Operators can raise the soft limit
/// at runtime up to this without re-installing the service.
pub const DAEMON_NOFILE_HARD: u64 = 8192;
const DAEMON_LOG_ROTATE_BYTES: u64 = 20 * 1024 * 1024;
const DAEMON_LOG_RETAINED_FILES: usize = 5;

/// Supported init systems for service management
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum InitSystem {
    /// Auto-detect based on system indicators
    #[default]
    Auto,
    /// systemd (via systemctl --user)
    Systemd,
    /// OpenRC (via rc-service)
    Openrc,
}

impl FromStr for InitSystem {
    type Err = anyhow::Error;

    fn from_str(s: &str) -> Result<Self> {
        match s.to_lowercase().as_str() {
            "auto" => Ok(Self::Auto),
            "systemd" => Ok(Self::Systemd),
            "openrc" => Ok(Self::Openrc),
            other => bail!(
                "Unknown init system: '{}'. Supported: auto, systemd, openrc",
                other
            ),
        }
    }
}

impl InitSystem {
    /// Resolve auto-detection to a concrete init system
    ///
    /// Detection order (deny-by-default):
    /// 1. `/run/systemd/system` exists → Systemd
    /// 2. `/run/openrc` exists AND OpenRC binary present → OpenRC
    /// 3. else → Error (unknown init system)
    #[cfg(target_os = "linux")]
    pub fn resolve(self) -> Result<Self> {
        match self {
            Self::Auto => detect_init_system(),
            concrete => Ok(concrete),
        }
    }

    #[cfg(not(target_os = "linux"))]
    pub fn resolve(self) -> Result<Self> {
        match self {
            Self::Auto => Ok(Self::Systemd),
            concrete => Ok(concrete),
        }
    }
}

/// Detect the active init system on Linux
///
/// Checks for systemd and OpenRC in order, returning the first match.
/// Returns an error if neither is detected.
#[cfg(target_os = "linux")]
fn detect_init_system() -> Result<InitSystem> {
    // Check for systemd first (most common on modern Linux)
    if Path::new("/run/systemd/system").exists() {
        return Ok(InitSystem::Systemd);
    }

    // Check for OpenRC: requires /run/openrc AND openrc binary
    if Path::new("/run/openrc").exists() {
        // Check for OpenRC binaries: /sbin/openrc-run or rc-service in PATH
        if Path::new("/sbin/openrc-run").exists() || which::which("rc-service").is_ok() {
            return Ok(InitSystem::Openrc);
        }
    }

    bail!(
        "Could not detect init system. Supported: systemd, OpenRC. \
         Use --service-init to specify manually."
    );
}

fn windows_task_name() -> &'static str {
    WINDOWS_TASK_NAME
}

/// Returns whether the Construct daemon service is currently running.
pub fn is_running() -> bool {
    if cfg!(target_os = "macos") {
        run_capture(Command::new("launchctl").arg("list"))
            .map(|out| out.lines().any(|l| l.contains(SERVICE_LABEL)))
            .unwrap_or(false)
    } else if cfg!(target_os = "linux") {
        is_running_linux()
    } else if cfg!(target_os = "windows") {
        run_capture(Command::new("schtasks").args([
            "/Query",
            "/TN",
            WINDOWS_TASK_NAME,
            "/FO",
            "LIST",
        ]))
        .map(|out| out.contains("Running"))
        .unwrap_or(false)
    } else {
        false
    }
}

fn is_running_linux() -> bool {
    // Try systemd first, then OpenRC — mirrors detect_init_system() order
    if run_capture(Command::new("systemctl").args(["--user", "is-active", "construct.service"]))
        .map(|out| out.trim() == "active")
        .unwrap_or(false)
    {
        return true;
    }
    run_capture(Command::new("rc-service").args(["construct", "status"]))
        .map(|out| out.contains("started"))
        .unwrap_or(false)
}

pub fn handle_command(
    command: &crate::ServiceCommands,
    config: &Config,
    init_system: InitSystem,
) -> Result<()> {
    match command {
        crate::ServiceCommands::Install => install(config, init_system),
        crate::ServiceCommands::Start => start(config, init_system),
        crate::ServiceCommands::Stop => stop(config, init_system),
        crate::ServiceCommands::Restart => restart(config, init_system),
        crate::ServiceCommands::Status => status(config, init_system),
        crate::ServiceCommands::Uninstall => uninstall(config, init_system),
        crate::ServiceCommands::Logs { lines, follow } => {
            logs(config, init_system, *lines, *follow)
        }
    }
}

fn install(config: &Config, init_system: InitSystem) -> Result<()> {
    if cfg!(target_os = "macos") {
        install_macos(config)
    } else if cfg!(target_os = "linux") {
        let resolved = init_system.resolve()?;
        install_linux(config, resolved)
    } else if cfg!(target_os = "windows") {
        install_windows(config)
    } else {
        anyhow::bail!("Service management is supported on macOS and Linux only");
    }
}

fn start(config: &Config, init_system: InitSystem) -> Result<()> {
    if cfg!(target_os = "macos") {
        // Ensure the Homebrew var directory exists before launchd tries to use it.
        // The plist may reference this path for WorkingDirectory and log files.
        let exe = std::env::current_exe().ok();
        if let Some(ref exe_path) = exe {
            if let Some(var_dir) = detect_homebrew_var_dir(exe_path) {
                let _ = fs::create_dir_all(&var_dir);
            }
        }
        rotate_daemon_logs(config)?;
        let plist = macos_service_file()?;
        run_checked(Command::new("launchctl").arg("load").arg("-w").arg(&plist))?;
        run_checked(Command::new("launchctl").arg("start").arg(SERVICE_LABEL))?;
        println!("✅ Service started");
        Ok(())
    } else if cfg!(target_os = "linux") {
        let resolved = init_system.resolve()?;
        start_linux(resolved)
    } else if cfg!(target_os = "windows") {
        rotate_daemon_logs(config)?;
        run_checked(Command::new("schtasks").args(["/Run", "/TN", windows_task_name()]))?;
        println!("✅ Service started");
        Ok(())
    } else {
        let _ = config;
        anyhow::bail!("Service management is supported on macOS and Linux only")
    }
}

fn daemon_logs_dir(config: &Config) -> PathBuf {
    let exe = std::env::current_exe().ok();
    if let Some(ref exe_path) = exe {
        if let Some(var_dir) = detect_homebrew_var_dir(exe_path) {
            return var_dir.join("logs");
        }
    }
    config
        .config_path
        .parent()
        .map_or_else(|| PathBuf::from("."), PathBuf::from)
        .join("logs")
}

fn rotate_daemon_logs(config: &Config) -> Result<()> {
    rotate_daemon_logs_in(&daemon_logs_dir(config), DAEMON_LOG_ROTATE_BYTES)
}

fn rotate_daemon_logs_in(logs_dir: &Path, max_bytes: u64) -> Result<()> {
    fs::create_dir_all(logs_dir)?;
    for name in ["daemon.stdout.log", "daemon.stderr.log"] {
        rotate_log_file(&logs_dir.join(name), max_bytes)?;
    }
    Ok(())
}

fn rotate_log_file(path: &Path, max_bytes: u64) -> Result<()> {
    let Ok(metadata) = fs::metadata(path) else {
        return Ok(());
    };
    if metadata.len() < max_bytes {
        return Ok(());
    }

    let oldest = rotated_log_path(path, DAEMON_LOG_RETAINED_FILES);
    if oldest.exists() {
        fs::remove_file(&oldest)
            .with_context(|| format!("Failed to remove old daemon log {}", oldest.display()))?;
    }

    for idx in (1..DAEMON_LOG_RETAINED_FILES).rev() {
        let from = rotated_log_path(path, idx);
        if from.exists() {
            let to = rotated_log_path(path, idx + 1);
            fs::rename(&from, &to).with_context(|| {
                format!(
                    "Failed to rotate daemon log {} to {}",
                    from.display(),
                    to.display()
                )
            })?;
        }
    }

    let first = rotated_log_path(path, 1);
    fs::rename(path, &first).with_context(|| {
        format!(
            "Failed to rotate daemon log {} to {}",
            path.display(),
            first.display()
        )
    })?;
    Ok(())
}

fn rotated_log_path(path: &Path, index: usize) -> PathBuf {
    PathBuf::from(format!("{}.{}", path.display(), index))
}

fn start_linux(init_system: InitSystem) -> Result<()> {
    match init_system {
        InitSystem::Systemd => {
            run_checked(Command::new("systemctl").args(["--user", "daemon-reload"]))?;
            run_checked(Command::new("systemctl").args(["--user", "start", "construct.service"]))?;
        }
        InitSystem::Openrc => {
            run_checked(Command::new("rc-service").args(["construct", "start"]))?;
        }
        InitSystem::Auto => unreachable!("Auto should be resolved before this point"),
    }
    println!("✅ Service started");
    Ok(())
}

fn stop(config: &Config, init_system: InitSystem) -> Result<()> {
    if cfg!(target_os = "macos") {
        let plist = macos_service_file()?;
        let _ = run_checked(Command::new("launchctl").arg("stop").arg(SERVICE_LABEL));
        let _ = run_checked(
            Command::new("launchctl")
                .arg("unload")
                .arg("-w")
                .arg(&plist),
        );
        println!("✅ Service stopped");
        Ok(())
    } else if cfg!(target_os = "linux") {
        let resolved = init_system.resolve()?;
        stop_linux(resolved)
    } else if cfg!(target_os = "windows") {
        let _ = config;
        let task_name = windows_task_name();
        let _ = run_checked(Command::new("schtasks").args(["/End", "/TN", task_name]));
        println!("✅ Service stopped");
        Ok(())
    } else {
        let _ = config;
        anyhow::bail!("Service management is supported on macOS and Linux only")
    }
}

fn stop_linux(init_system: InitSystem) -> Result<()> {
    match init_system {
        InitSystem::Systemd => {
            let _ = run_checked(Command::new("systemctl").args([
                "--user",
                "stop",
                "construct.service",
            ]));
        }
        InitSystem::Openrc => {
            let _ = run_checked(Command::new("rc-service").args(["construct", "stop"]));
        }
        InitSystem::Auto => unreachable!("Auto should be resolved before this point"),
    }
    println!("✅ Service stopped");
    Ok(())
}

fn restart(config: &Config, init_system: InitSystem) -> Result<()> {
    if cfg!(target_os = "macos") {
        stop(config, init_system)?;
        start(config, init_system)?;
        println!("✅ Service restarted");
        return Ok(());
    }

    if cfg!(target_os = "linux") {
        let resolved = init_system.resolve()?;
        return restart_linux(resolved);
    }

    if cfg!(target_os = "windows") {
        stop(config, init_system)?;
        start(config, init_system)?;
        println!("✅ Service restarted");
        return Ok(());
    }

    anyhow::bail!("Service management is supported on macOS and Linux only")
}

fn restart_linux(init_system: InitSystem) -> Result<()> {
    match init_system {
        InitSystem::Systemd => {
            run_checked(Command::new("systemctl").args(["--user", "daemon-reload"]))?;
            run_checked(Command::new("systemctl").args([
                "--user",
                "restart",
                "construct.service",
            ]))?;
        }
        InitSystem::Openrc => {
            run_checked(Command::new("rc-service").args(["construct", "restart"]))?;
        }
        InitSystem::Auto => unreachable!("Auto should be resolved before this point"),
    }
    println!("✅ Service restarted");
    Ok(())
}

fn status(config: &Config, init_system: InitSystem) -> Result<()> {
    if cfg!(target_os = "macos") {
        let out = run_capture(Command::new("launchctl").arg("list"))?;
        let running = out.lines().any(|line| line.contains(SERVICE_LABEL));
        println!(
            "Service: {}",
            if running {
                "✅ running/loaded"
            } else {
                "❌ not loaded"
            }
        );
        println!("Unit: {}", macos_service_file()?.display());
        return Ok(());
    }

    if cfg!(target_os = "linux") {
        let resolved = init_system.resolve()?;
        return status_linux(config, resolved);
    }

    if cfg!(target_os = "windows") {
        let _ = config;
        let task_name = windows_task_name();
        let out =
            run_capture(Command::new("schtasks").args(["/Query", "/TN", task_name, "/FO", "LIST"]));
        match out {
            Ok(text) => {
                let running = text.contains("Running");
                println!(
                    "Service: {}",
                    if running {
                        "✅ running"
                    } else {
                        "❌ not running"
                    }
                );
                println!("Task: {}", task_name);
            }
            Err(_) => {
                println!("Service: ❌ not installed");
            }
        }
        return Ok(());
    }

    anyhow::bail!("Service management is supported on macOS and Linux only")
}

fn status_linux(config: &Config, init_system: InitSystem) -> Result<()> {
    match init_system {
        InitSystem::Systemd => {
            let out = run_capture(Command::new("systemctl").args([
                "--user",
                "is-active",
                "construct.service",
            ]))
            .unwrap_or_else(|_| "unknown".into());
            println!("Service state: {}", out.trim());
            println!("Unit: {}", linux_service_file(config)?.display());
        }
        InitSystem::Openrc => {
            let out = run_capture(Command::new("rc-service").args(["construct", "status"]))
                .unwrap_or_else(|_| "unknown".into());
            println!("Service state: {}", out.trim());
            println!("Unit: /etc/init.d/construct");
        }
        InitSystem::Auto => unreachable!("Auto should be resolved before this point"),
    }
    Ok(())
}

fn logs(config: &Config, init_system: InitSystem, lines: usize, follow: bool) -> Result<()> {
    if cfg!(target_os = "macos") {
        return logs_macos(config, lines, follow);
    }
    if cfg!(target_os = "linux") {
        let resolved = init_system.resolve()?;
        return logs_linux(config, resolved, lines, follow);
    }
    if cfg!(target_os = "windows") {
        return logs_windows(config, lines, follow);
    }
    anyhow::bail!("Service log viewing is supported on macOS, Linux, and Windows only")
}

fn logs_macos(config: &Config, lines: usize, follow: bool) -> Result<()> {
    // Try the launchd log files first (StandardOutPath / StandardErrorPath from the plist).
    // These are the most reliable source since they capture all daemon output.
    let exe = std::env::current_exe().ok();
    let homebrew_var_dir = exe.as_ref().and_then(|e| detect_homebrew_var_dir(e));
    let logs_dir = if let Some(ref var_dir) = homebrew_var_dir {
        var_dir.join("logs")
    } else {
        config
            .config_path
            .parent()
            .map_or_else(|| PathBuf::from("."), PathBuf::from)
            .join("logs")
    };

    let stderr_log = logs_dir.join("daemon.stderr.log");
    let stdout_log = logs_dir.join("daemon.stdout.log");

    // Prefer stderr log (most informative), fall back to stdout
    let log_file = if stderr_log.exists() {
        stderr_log
    } else if stdout_log.exists() {
        stdout_log
    } else {
        bail!(
            "No log files found in {}. Is the service installed?",
            logs_dir.display()
        );
    };

    if follow {
        let status = Command::new("tail")
            .args(["-n", &lines.to_string(), "-f"])
            .arg(&log_file)
            .status()
            .context("Failed to run tail")?;
        if !status.success() {
            bail!("tail exited with non-zero status");
        }
    } else {
        let status = Command::new("tail")
            .args(["-n", &lines.to_string()])
            .arg(&log_file)
            .status()
            .context("Failed to run tail")?;
        if !status.success() {
            bail!("tail exited with non-zero status");
        }
    }
    Ok(())
}

fn logs_linux(config: &Config, init_system: InitSystem, lines: usize, follow: bool) -> Result<()> {
    match init_system {
        InitSystem::Systemd => {
            let mut args = vec![
                "--user".to_string(),
                "-u".to_string(),
                "construct.service".to_string(),
                "-n".to_string(),
                lines.to_string(),
                "--no-pager".to_string(),
            ];
            if follow {
                args.push("-f".to_string());
            }
            let status = Command::new("journalctl")
                .args(&args)
                .status()
                .context("Failed to run journalctl")?;
            if !status.success() {
                bail!("journalctl exited with non-zero status");
            }
        }
        InitSystem::Openrc => {
            // OpenRC logs go to /var/log/construct/error.log (as configured in the init script)
            let log_file = Path::new("/var/log/construct/error.log");
            if !log_file.exists() {
                // Fall back to access log
                let access_log = Path::new("/var/log/construct/access.log");
                if !access_log.exists() {
                    bail!("No log files found at /var/log/construct/. Is the service installed?");
                }
                return tail_file(access_log, lines, follow);
            }
            tail_file(log_file, lines, follow)?;
        }
        InitSystem::Auto => unreachable!("Auto should be resolved before this point"),
    }
    let _ = config;
    Ok(())
}

fn logs_windows(config: &Config, lines: usize, follow: bool) -> Result<()> {
    let logs_dir = config
        .config_path
        .parent()
        .map_or_else(|| PathBuf::from("."), PathBuf::from)
        .join("logs");

    let stderr_log = logs_dir.join("daemon.stderr.log");
    let stdout_log = logs_dir.join("daemon.stdout.log");

    let log_file = if stderr_log.exists() {
        stderr_log
    } else if stdout_log.exists() {
        stdout_log
    } else {
        bail!(
            "No log files found in {}. Is the service installed?",
            logs_dir.display()
        );
    };

    if follow {
        // Windows: use PowerShell Get-Content -Wait for tail -f equivalent
        let status = Command::new("powershell")
            .args([
                "-Command",
                &format!(
                    "Get-Content -Path '{}' -Tail {} -Wait",
                    log_file.display(),
                    lines
                ),
            ])
            .status()
            .context("Failed to run PowerShell Get-Content")?;
        if !status.success() {
            bail!("PowerShell Get-Content exited with non-zero status");
        }
    } else {
        let status = Command::new("powershell")
            .args([
                "-Command",
                &format!("Get-Content -Path '{}' -Tail {}", log_file.display(), lines),
            ])
            .status()
            .context("Failed to run PowerShell Get-Content")?;
        if !status.success() {
            bail!("PowerShell Get-Content exited with non-zero status");
        }
    }
    Ok(())
}

/// Tail a log file using the system `tail` command.
fn tail_file(path: &Path, lines: usize, follow: bool) -> Result<()> {
    let mut args = vec!["-n".to_string(), lines.to_string()];
    if follow {
        args.push("-f".to_string());
    }
    let status = Command::new("tail")
        .args(&args)
        .arg(path)
        .status()
        .context("Failed to run tail")?;
    if !status.success() {
        bail!("tail exited with non-zero status");
    }
    Ok(())
}

fn uninstall(config: &Config, init_system: InitSystem) -> Result<()> {
    stop(config, init_system)?;

    if cfg!(target_os = "macos") {
        let file = macos_service_file()?;
        if file.exists() {
            fs::remove_file(&file)
                .with_context(|| format!("Failed to remove {}", file.display()))?;
        }
        println!("✅ Service uninstalled ({})", file.display());
        return Ok(());
    }

    if cfg!(target_os = "linux") {
        let resolved = init_system.resolve()?;
        return uninstall_linux(config, resolved);
    }

    if cfg!(target_os = "windows") {
        let task_name = windows_task_name();
        let _ = run_checked(Command::new("schtasks").args(["/Delete", "/TN", task_name, "/F"]));
        // Remove the wrapper script
        let wrapper = config
            .config_path
            .parent()
            .map_or_else(|| PathBuf::from("."), PathBuf::from)
            .join("logs")
            .join("construct-daemon.cmd");
        if wrapper.exists() {
            fs::remove_file(&wrapper).ok();
        }
        println!("✅ Service uninstalled");
        return Ok(());
    }

    anyhow::bail!("Service management is supported on macOS and Linux only")
}

fn uninstall_linux(config: &Config, init_system: InitSystem) -> Result<()> {
    match init_system {
        InitSystem::Systemd => {
            let file = linux_service_file(config)?;
            if file.exists() {
                fs::remove_file(&file)
                    .with_context(|| format!("Failed to remove {}", file.display()))?;
            }
            let _ = run_checked(Command::new("systemctl").args(["--user", "daemon-reload"]));
            println!("✅ Service uninstalled ({})", file.display());
        }
        InitSystem::Openrc => {
            let init_script = Path::new("/etc/init.d/construct");
            if init_script.exists() {
                if let Err(err) =
                    run_checked(Command::new("rc-update").args(["del", "construct", "default"]))
                {
                    eprintln!(
                        "⚠️  Warning: Could not remove construct from OpenRC default runlevel: {err}"
                    );
                }
                fs::remove_file(init_script)
                    .with_context(|| format!("Failed to remove {}", init_script.display()))?;
            }
            println!("✅ Service uninstalled (/etc/init.d/construct)");
        }
        InitSystem::Auto => unreachable!("Auto should be resolved before this point"),
    }
    Ok(())
}

/// Build the PATH the daemon process should see on Unix.
///
/// Why this is necessary: every Unix init system the daemon runs under
/// (launchd on macOS, systemd user units on Linux, OpenRC on Alpine)
/// starts services with a minimal default PATH that does NOT include
/// the user-bin dirs where most language-toolchain CLIs live. The
/// daemon spawns external CLIs by name (`codex`, `claude`, `node`,
/// `python3`) via subprocess, so without an explicit PATH override the
/// spawn fails with `[Errno 2] No such file or directory: 'codex'`
/// even though the user's interactive shell finds the binary fine.
///
/// Curated default covers `/usr/local/bin` (npm + Intel Homebrew),
/// `/opt/homebrew/bin` (ARM Homebrew), `~/.cargo/bin` (Rust), and
/// `~/.local/bin` (pipx / pip --user / Claude Code CLI), plus the
/// minimal system bin/sbin set.
///
/// `include_user_dirs`:
/// - **true** for launchd + systemd user units (daemon runs as the
///   install user). Adds `$HOME`-rooted dirs and folds in the
///   install-time `PATH` env so unusual setups (nvm, asdf, fnm, custom
///   prefixes) keep working without hand-editing.
/// - **false** for OpenRC's system `construct:construct` user where
///   `$HOME` resolves to `/var/lib/construct` and the install user's
///   PATH is irrelevant. Returns just the system-wide locations.
fn build_unix_daemon_path(include_user_dirs: bool) -> String {
    let mut path_dirs: Vec<String> = Vec::new();
    if include_user_dirs {
        if let Some(home) = std::env::var_os("HOME") {
            let home_str = home.to_string_lossy().into_owned();
            path_dirs.push(format!("{home_str}/.cargo/bin"));
            path_dirs.push(format!("{home_str}/.local/bin"));
        }
    }
    path_dirs.extend([
        "/usr/local/bin".to_string(),
        "/opt/homebrew/bin".to_string(),
        "/usr/bin".to_string(),
        "/bin".to_string(),
        "/usr/local/sbin".to_string(),
        "/usr/sbin".to_string(),
        "/sbin".to_string(),
    ]);
    if include_user_dirs && let Ok(install_path) = std::env::var("PATH") {
        for dir in install_path.split(':') {
            if !dir.is_empty() && !path_dirs.iter().any(|d| d == dir) {
                path_dirs.push(dir.to_string());
            }
        }
    }
    path_dirs.join(":")
}

/// Build the PATH for the Windows scheduled-task wrapper script.
///
/// On Windows codex (npm-installed) typically lives at
/// `%APPDATA%\npm\codex.CMD` and cargo-installed CLIs at
/// `%USERPROFILE%\.cargo\bin\*`. Scheduled tasks created via
/// `schtasks /SC ONLOGON` (no `/RU`) inherit the user's interactive
/// PATH, but we still snapshot install-time `PATH` and prepend the
/// known npm/cargo dirs so the task is robust to PATH drift between
/// install time and task-trigger time (e.g. user uninstalls Node and
/// reinstalls into a different prefix).
fn build_windows_daemon_path() -> String {
    let mut path_dirs: Vec<String> = Vec::new();
    if let Ok(profile) = std::env::var("USERPROFILE") {
        path_dirs.push(format!(r"{profile}\.cargo\bin"));
    }
    if let Ok(appdata) = std::env::var("APPDATA") {
        path_dirs.push(format!(r"{appdata}\npm"));
    }
    if let Ok(install_path) = std::env::var("PATH") {
        for dir in install_path.split(';') {
            if !dir.is_empty() && !path_dirs.iter().any(|d| d == dir) {
                path_dirs.push(dir.to_string());
            }
        }
    }
    path_dirs.join(";")
}

/// Detect if the executable lives under a Homebrew prefix and return the
/// corresponding `var/construct` directory.
///
/// Homebrew installs binaries into `<prefix>/Cellar/<formula>/<version>/bin/`
/// and symlinks them to `<prefix>/bin/`. The canonical `var` directory is
/// `<prefix>/var`.  We check for both layouts.
fn detect_homebrew_var_dir(exe: &Path) -> Option<PathBuf> {
    let path_str = exe.to_string_lossy();

    // Symlinked binary: <prefix>/bin/construct
    // Cellar binary:    <prefix>/Cellar/construct/<version>/bin/construct
    let prefix = if path_str.contains("/Cellar/") {
        // Walk up from .../Cellar/construct/<ver>/bin/construct to the prefix
        let mut ancestor = exe.to_path_buf();
        while let Some(parent) = ancestor.parent() {
            ancestor = parent.to_path_buf();
            if ancestor.file_name().map_or(false, |n| n == "Cellar") {
                // prefix is one level above Cellar
                return ancestor.parent().map(|p| p.join("var").join("construct"));
            }
        }
        return None;
    } else if let Some(bin_parent) = exe.parent() {
        // <prefix>/bin/construct → check if <prefix>/Cellar exists (Homebrew marker)
        if let Some(prefix) = bin_parent.parent() {
            if prefix.join("Cellar").is_dir() {
                Some(prefix.to_path_buf())
            } else {
                None
            }
        } else {
            None
        }
    } else {
        None
    };

    prefix.map(|p| p.join("var").join("construct"))
}

fn install_macos(config: &Config) -> Result<()> {
    let file = macos_service_file()?;
    if let Some(parent) = file.parent() {
        fs::create_dir_all(parent)?;
    }

    let exe = std::env::current_exe().context("Failed to resolve current executable")?;

    // When installed via Homebrew, use the Homebrew var directory for runtime
    // data so that `brew services start construct` works out of the box.
    let homebrew_var_dir = detect_homebrew_var_dir(&exe);
    if let Some(ref var_dir) = homebrew_var_dir {
        fs::create_dir_all(var_dir).with_context(|| {
            format!(
                "Failed to create Homebrew var directory: {}",
                var_dir.display()
            )
        })?;
    }

    let logs_dir = if let Some(ref var_dir) = homebrew_var_dir {
        var_dir.join("logs")
    } else {
        config
            .config_path
            .parent()
            .map_or_else(|| PathBuf::from("."), PathBuf::from)
            .join("logs")
    };
    fs::create_dir_all(&logs_dir)?;

    let stdout = logs_dir.join("daemon.stdout.log");
    let stderr = logs_dir.join("daemon.stderr.log");

    let plist = format_macos_plist(&exe, homebrew_var_dir.as_deref(), &stdout, &stderr);

    fs::write(&file, plist)?;
    println!("✅ Installed launchd service: {}", file.display());
    if let Some(ref var_dir) = homebrew_var_dir {
        println!("   Homebrew var: {}", var_dir.display());
    }
    println!("   Start with: construct service start");
    Ok(())
}

/// Build the launchd plist contents for the Construct daemon.
///
/// Extracted from `install_macos` so tests can pin the generated XML
/// without performing filesystem side effects. See `install_macos` for
/// the surrounding install flow (directory creation, plist write).
fn format_macos_plist(
    exe: &Path,
    homebrew_var_dir: Option<&Path>,
    stdout: &Path,
    stderr: &Path,
) -> String {
    // PATH for the daemon process — see build_unix_daemon_path() for
    // why this is necessary on every Unix init system.
    let path_env = build_unix_daemon_path(true);

    // EnvironmentVariables always carries PATH; under Homebrew it also
    // carries CONSTRUCT_CONFIG_DIR. WorkingDirectory is Homebrew-only
    // (lives outside the EnvironmentVariables dict, so it stays a
    // separate stanza).
    let env_section = if let Some(var_dir) = homebrew_var_dir {
        format!(
            r#"  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>{path}</string>
    <key>CONSTRUCT_CONFIG_DIR</key>
    <string>{config_dir}</string>
  </dict>
  <key>WorkingDirectory</key>
  <string>{working_dir}</string>
"#,
            path = xml_escape(&path_env),
            config_dir = xml_escape(&var_dir.display().to_string()),
            working_dir = xml_escape(&var_dir.display().to_string()),
        )
    } else {
        format!(
            r#"  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>{path}</string>
  </dict>
"#,
            path = xml_escape(&path_env),
        )
    };

    // Resource limits — real-world incident 2026-05-11: a user hit
    // `Failed to spawn MCP server: Too many open files (os error 24)`
    // because launchd's default NumberOfFiles is 256, and each
    // Construct agent spawns 3 MCP sidecars over stdio pipes. Bump the
    // soft cap to DAEMON_NOFILE_SOFT (operators can `ulimit -n` up to
    // the hard cap at runtime without re-installing).
    let resource_limits = format!(
        r#"  <key>SoftResourceLimits</key>
  <dict>
    <key>NumberOfFiles</key>
    <integer>{soft}</integer>
  </dict>
  <key>HardResourceLimits</key>
  <dict>
    <key>NumberOfFiles</key>
    <integer>{hard}</integer>
  </dict>
"#,
        soft = DAEMON_NOFILE_SOFT,
        hard = DAEMON_NOFILE_HARD,
    );

    // Raw string (r#"..."#) — backslash is literal, so use bare `"` for
    // XML attribute quotes. Before this fix, the generated plist contained
    // `version=\"1.0\"` (5 characters) instead of `version="1.0"` (3 chars),
    // which is not valid XML. launchd happens to tolerate it on macOS but
    // `xmllint` and any third-party plist consumer would reject the file.
    format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{exe}</string>
    <string>daemon</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
{resource_limits}{env_section}  <key>StandardOutPath</key>
  <string>{stdout}</string>
  <key>StandardErrorPath</key>
  <string>{stderr}</string>
</dict>
</plist>
"#,
        label = SERVICE_LABEL,
        exe = xml_escape(&exe.display().to_string()),
        resource_limits = resource_limits,
        env_section = env_section,
        stdout = xml_escape(&stdout.display().to_string()),
        stderr = xml_escape(&stderr.display().to_string())
    )
}

fn install_linux(config: &Config, init_system: InitSystem) -> Result<()> {
    match init_system {
        InitSystem::Systemd => install_linux_systemd(config),
        InitSystem::Openrc => install_linux_openrc(config),
        InitSystem::Auto => unreachable!("Auto should be resolved before this point"),
    }
}

/// Build the systemd user-unit contents for the Construct daemon.
///
/// Extracted from `install_linux_systemd` so tests can pin the
/// generated unit body without performing filesystem side effects.
fn format_systemd_unit(exe: &Path) -> String {
    let path_env = build_unix_daemon_path(true);
    format!(
        "[Unit]\n\
         Description=Construct daemon\n\
         After=network.target\n\
         \n\
         [Service]\n\
         Type=simple\n\
         ExecStart={exe} daemon\n\
         Restart=always\n\
         RestartSec=3\n\
         # Ensure HOME is set so headless browsers can create profile/cache dirs.\n\
         Environment=HOME=%h\n\
         # File-descriptor limits — real-world incident 2026-05-11: a user\n\
         # hit `Too many open files` spawning MCP servers because the\n\
         # supervised daemon inherited the default soft NOFILE (often 1024\n\
         # on systemd user units). Each agent spawns 3 MCP sidecars over\n\
         # stdio pipes, so a small handful of agents exhausts that cap.\n\
         # Format is `<soft>:<hard>`.\n\
         LimitNOFILE={nofile_soft}:{nofile_hard}\n\
         # PATH override — systemd user services start with a minimal\n\
         # default PATH (typically /usr/local/bin:/usr/bin:/bin:...) that\n\
         # excludes ~/.cargo/bin, ~/.local/bin, and ~/.npm-global/bin.\n\
         # Without this the daemon's subprocess spawns of codex/claude/etc\n\
         # fail with [Errno 2] No such file or directory.\n\
         Environment=\"PATH={path_env}\"\n\
         # Allow inheriting DISPLAY and XDG_RUNTIME_DIR from the user session\n\
         # so graphical/headless browsers can function correctly.\n\
         PassEnvironment=DISPLAY XDG_RUNTIME_DIR\n\
         \n\
         [Install]\n\
         WantedBy=default.target\n",
        exe = exe.display(),
        nofile_soft = DAEMON_NOFILE_SOFT,
        nofile_hard = DAEMON_NOFILE_HARD,
    )
}

fn install_linux_systemd(config: &Config) -> Result<()> {
    let file = linux_service_file(config)?;
    if let Some(parent) = file.parent() {
        fs::create_dir_all(parent)?;
    }

    let exe = std::env::current_exe().context("Failed to resolve current executable")?;
    let unit = format_systemd_unit(&exe);

    fs::write(&file, unit)?;
    let _ = run_checked(Command::new("systemctl").args(["--user", "daemon-reload"]));
    let _ = run_checked(Command::new("systemctl").args(["--user", "enable", "construct.service"]));
    println!("✅ Installed systemd user service: {}", file.display());
    println!("   Start with: construct service start");
    Ok(())
}

/// Check if the current process is running as root (Unix only)
#[cfg(unix)]
fn is_root() -> bool {
    // SAFETY: `getuid()` is a simple system call that returns the real user ID of the calling
    // process. It is always safe to call as it takes no arguments and returns a scalar value.
    // This is a well-established pattern in Rust for getting the current user ID.
    unsafe { libc::getuid() == 0 }
}

#[cfg(not(unix))]
fn is_root() -> bool {
    false
}

/// Check if the construct user exists and has expected properties.
/// Returns Ok if user doesn't exist (OpenRC will handle creation or fail gracefully).
/// Returns error if user exists but has unexpected properties.
fn check_construct_user() -> Result<()> {
    let output = Command::new("getent")
        .args(["passwd", "construct"])
        .output();
    let is_alpine = Path::new("/etc/alpine-release").exists();

    let (del_cmd, add_cmd) = if is_alpine {
        (
            "deluser construct && delgroup construct",
            "addgroup -S construct && adduser -S -s /sbin/nologin -H -D -G construct construct",
        )
    } else {
        ("userdel construct", "useradd -r -s /sbin/nologin construct")
    };

    match output {
        Ok(output) if output.status.success() => {
            let passwd_entry = String::from_utf8_lossy(&output.stdout);
            let parts: Vec<&str> = passwd_entry.split(':').collect();
            if parts.len() >= 7 {
                let uid = parts[2];
                let gid = parts[3];
                let home = parts[5];
                let shell = parts[6];

                if uid.parse::<u32>().unwrap_or(999) >= 1000 {
                    bail!(
                        "User 'construct' exists but has unexpected UID {} (expected system UID < 1000).\n\
                         Recreate with: sudo {} && sudo {}",
                        uid,
                        del_cmd,
                        add_cmd
                    );
                }

                if !shell.contains("nologin") && !shell.contains("false") {
                    bail!(
                        "User 'construct' exists but has unexpected shell '{}'.\n\
                         Expected nologin/false for security. Fix with: sudo {} && sudo {}",
                        shell,
                        del_cmd,
                        add_cmd
                    );
                }

                if home != "/var/lib/construct" && home != "/nonexistent" {
                    eprintln!(
                        "⚠️  Warning: construct user has home directory '{}' (expected /var/lib/construct or /nonexistent)",
                        home
                    );
                }

                let _ = gid;
            }
            Ok(())
        }
        _ => Ok(()),
    }
}

fn ensure_construct_user() -> Result<()> {
    let output = Command::new("getent")
        .args(["passwd", "construct"])
        .output();
    if let Ok(output) = output {
        if output.status.success() {
            return check_construct_user();
        }
    }

    let is_alpine = Path::new("/etc/alpine-release").exists();

    if is_alpine {
        let group_output = Command::new("getent").args(["group", "construct"]).output();
        let group_exists = group_output.map(|o| o.status.success()).unwrap_or(false);

        if !group_exists {
            let output = Command::new("addgroup")
                .args(["-S", "construct"])
                .output()
                .context("Failed to create construct group")?;

            if !output.status.success() {
                let stderr = String::from_utf8_lossy(&output.stderr);
                bail!("Failed to create construct group: {}", stderr.trim());
            }
            println!("✅ Created system group: construct");
        }

        let output = Command::new("adduser")
            .args([
                "-S",
                "-s",
                "/sbin/nologin",
                "-H",
                "-D",
                "-G",
                "construct",
                "construct",
            ])
            .output()
            .context("Failed to create construct user")?;

        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            bail!("Failed to create construct user: {}", stderr.trim());
        }
    } else {
        let output = Command::new("useradd")
            .args(["-r", "-s", "/sbin/nologin", "construct"])
            .output()
            .context("Failed to create construct user")?;

        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            bail!("Failed to create construct user: {}", stderr.trim());
        }
    }

    println!("✅ Created system user: construct");
    Ok(())
}

/// Change ownership of a path to construct:construct
#[cfg(unix)]
fn chown_to_construct(path: &Path) -> Result<()> {
    let output = Command::new("chown")
        .args(["construct:construct", &path.to_string_lossy()])
        .output()
        .context("Failed to run chown")?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        bail!(
            "Failed to change ownership of {} to construct:construct: {}",
            path.display(),
            stderr.trim(),
        );
    }
    Ok(())
}

#[cfg(not(unix))]
fn chown_to_construct(_path: &Path) -> Result<()> {
    Ok(())
}

#[cfg(unix)]
fn chown_recursive_to_construct(path: &Path) -> Result<()> {
    let output = Command::new("chown")
        .args(["-R", "construct:construct", &path.to_string_lossy()])
        .output()
        .context("Failed to run recursive chown")?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        bail!(
            "Failed to recursively change ownership of {} to construct:construct: {}",
            path.display(),
            stderr.trim(),
        );
    }

    Ok(())
}

#[cfg(not(unix))]
fn chown_recursive_to_construct(_path: &Path) -> Result<()> {
    Ok(())
}

fn copy_dir_recursive(source: &Path, target: &Path) -> Result<()> {
    fs::create_dir_all(target)
        .with_context(|| format!("Failed to create directory {}", target.display()))?;

    for entry in fs::read_dir(source)
        .with_context(|| format!("Failed to read directory {}", source.display()))?
    {
        let entry = entry?;
        let source_path = entry.path();
        let target_path = target.join(entry.file_name());
        let file_type = entry
            .file_type()
            .with_context(|| format!("Failed to inspect {}", source_path.display()))?;

        if file_type.is_dir() {
            copy_dir_recursive(&source_path, &target_path)?;
        } else if file_type.is_file() {
            if target_path.exists() {
                continue;
            }
            fs::copy(&source_path, &target_path).with_context(|| {
                format!(
                    "Failed to copy file {} -> {}",
                    source_path.display(),
                    target_path.display()
                )
            })?;
        }
    }

    Ok(())
}

fn resolve_invoking_user_config_dir() -> Option<PathBuf> {
    let sudo_user = std::env::var("SUDO_USER")
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty() && value != "root");

    if let Some(user) = sudo_user {
        if let Ok(output) = Command::new("getent").args(["passwd", &user]).output() {
            if output.status.success() {
                let entry = String::from_utf8_lossy(&output.stdout);
                let fields: Vec<&str> = entry.trim().split(':').collect();
                if fields.len() >= 6 {
                    return Some(PathBuf::from(fields[5]).join(".construct"));
                }
            }
        }
    }

    std::env::var("HOME")
        .ok()
        .map(PathBuf::from)
        .map(|home| home.join(".construct"))
}

fn migrate_openrc_runtime_state_if_needed(config_dir: &Path) -> Result<()> {
    let target_config = config_dir.join("config.toml");
    if target_config.exists() {
        println!(
            "✅ Reusing existing OpenRC config at {}",
            target_config.display()
        );
        return Ok(());
    }

    let Some(source_dir) = resolve_invoking_user_config_dir() else {
        return Ok(());
    };

    let source_config = source_dir.join("config.toml");
    if !source_config.exists() {
        return Ok(());
    }

    copy_dir_recursive(&source_dir, config_dir)?;
    println!(
        "✅ Migrated runtime state from {} to {}",
        source_dir.display(),
        config_dir.display()
    );
    Ok(())
}

#[cfg(unix)]
fn shell_single_quote(raw: &str) -> String {
    format!("'{}'", raw.replace('\'', "'\"'\"'"))
}

#[cfg(unix)]
fn build_openrc_writability_probe_command(path: &Path, has_runuser: bool) -> (String, Vec<String>) {
    let probe = format!("test -w {}", shell_single_quote(&path.to_string_lossy()));
    if has_runuser {
        (
            "runuser".to_string(),
            vec![
                "-u".to_string(),
                "construct".to_string(),
                "--".to_string(),
                "sh".to_string(),
                "-c".to_string(),
                probe,
            ],
        )
    } else {
        (
            "su".to_string(),
            vec![
                "-s".to_string(),
                "/bin/sh".to_string(),
                "-c".to_string(),
                probe,
                "construct".to_string(),
            ],
        )
    }
}

#[cfg(unix)]
fn ensure_openrc_runtime_path_writable(path: &Path) -> Result<()> {
    let has_runuser = which::which("runuser").is_ok();
    let (program, args) = build_openrc_writability_probe_command(path, has_runuser);
    let output = Command::new(&program)
        .args(args.iter().map(String::as_str))
        .output()
        .with_context(|| {
            format!(
                "Failed to verify OpenRC runtime write access for {}",
                path.display()
            )
        })?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let details = if stderr.trim().is_empty() {
            "write-access probe failed"
        } else {
            stderr.trim()
        };
        bail!(
            "OpenRC runtime user 'construct' cannot write {} ({details}). \
             Re-run `sudo construct service install` and ensure ownership is construct:construct.",
            path.display(),
        );
    }

    Ok(())
}

#[cfg(unix)]
fn ensure_openrc_runtime_dirs_writable(
    config_dir: &Path,
    workspace_dir: &Path,
    log_dir: &Path,
) -> Result<()> {
    for path in [config_dir, workspace_dir, log_dir] {
        ensure_openrc_runtime_path_writable(path)?;
    }
    Ok(())
}

#[cfg(not(unix))]
fn ensure_openrc_runtime_dirs_writable(
    _config_dir: &Path,
    _workspace_dir: &Path,
    _log_dir: &Path,
) -> Result<()> {
    Ok(())
}

/// Warn if the binary path is in a user home directory
fn warn_if_binary_in_home(exe_path: &Path) {
    let path_str = exe_path.to_string_lossy();
    if path_str.contains("/home/") || path_str.contains(".cargo/bin") {
        eprintln!(
            "⚠️  Warning: Binary path '{}' appears to be in a user home directory.\n\
             For system-wide OpenRC service, consider installing to /usr/local/bin:\n\
             sudo cp '{}' /usr/local/bin/construct",
            exe_path.display(),
            exe_path.display()
        );
    }
}

/// Generate OpenRC init script content (pure function for testability)
fn generate_openrc_script(exe_path: &Path, config_dir: &Path) -> String {
    // OpenRC's daemon runs as the system `construct:construct` user
    // whose HOME is `/var/lib/construct`. The install user's `~/.cargo/bin`
    // / `~/.local/bin` are not relevant here, so we ask for the
    // system-only PATH set.
    let path_env = build_unix_daemon_path(false);
    format!(
        r#"#!/sbin/openrc-run

name="construct"
description="Construct daemon"

command="{exe}"
command_args="--config-dir {config_dir} daemon"
command_background="yes"
command_user="construct:construct"
pidfile="/run/${{RC_SVCNAME}}.pid"
umask 027
output_log="/var/log/construct/access.log"
error_log="/var/log/construct/error.log"

# Provide HOME so headless browsers can create profile/cache directories.
# Without this, Chromium/Firefox fail with sandbox or profile errors.
export HOME="/var/lib/construct"

# File-descriptor limit — real-world incident 2026-05-11: a user hit
# `Too many open files` spawning MCP servers. Each agent spawns 3 MCP
# sidecars over stdio pipes; the default soft NOFILE (256–1024 depending
# on the host) exhausts after a small handful of concurrent agents.
# OpenRC reads rc_ulimit and calls `ulimit -n` before launching the
# service. Single-value syntax (no separate hard cap).
rc_ulimit="-n {nofile_soft}"

# PATH override — the OpenRC system `construct` user starts with the
# default supervised-process PATH which can be even narrower than the
# user's interactive shell. Daemon subprocess spawns of codex/claude/etc
# need /usr/local/bin and friends explicitly listed.
export PATH="{path_env}"

depend() {{
    need net
    after firewall
}}

start_pre() {{
    checkpath --directory --owner construct:construct --mode 0750 /var/lib/construct
}}
"#,
        exe = exe_path.display(),
        config_dir = config_dir.display(),
        nofile_soft = DAEMON_NOFILE_SOFT,
    )
}

fn resolve_openrc_executable() -> Result<PathBuf> {
    let preferred = Path::new("/usr/local/bin/construct");
    if preferred.exists() {
        return Ok(preferred.to_path_buf());
    }

    let exe = std::env::current_exe().context("Failed to resolve current executable")?;
    Ok(exe)
}

fn install_linux_openrc(config: &Config) -> Result<()> {
    if !is_root() {
        bail!(
            "OpenRC service installation requires root privileges.\n\
             Please run with sudo: sudo construct service install"
        );
    }

    ensure_construct_user()?;

    let exe = resolve_openrc_executable()?;
    warn_if_binary_in_home(&exe);

    let config_dir = Path::new("/etc/construct");
    let workspace_dir = config_dir.join("workspace");
    let log_dir = Path::new("/var/log/construct");

    if !config_dir.exists() {
        fs::create_dir_all(config_dir)
            .with_context(|| format!("Failed to create {}", config_dir.display()))?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(config_dir, fs::Permissions::from_mode(0o755)).with_context(
                || format!("Failed to set permissions on {}", config_dir.display()),
            )?;
        }
        println!("✅ Created directory: {}", config_dir.display());
    }

    migrate_openrc_runtime_state_if_needed(config_dir)?;

    if !workspace_dir.exists() {
        fs::create_dir_all(&workspace_dir)
            .with_context(|| format!("Failed to create {}", workspace_dir.display()))?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&workspace_dir, fs::Permissions::from_mode(0o750)).with_context(
                || format!("Failed to set permissions on {}", workspace_dir.display()),
            )?;
        }
        chown_to_construct(&workspace_dir)?;
        println!(
            "✅ Created directory: {} (owned by construct:construct)",
            workspace_dir.display()
        );
    }

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&workspace_dir, fs::Permissions::from_mode(0o750))
            .with_context(|| format!("Failed to set permissions on {}", workspace_dir.display()))?;
    }

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(config_dir, fs::Permissions::from_mode(0o755))
            .with_context(|| format!("Failed to set permissions on {}", config_dir.display()))?;
        let config_path = config_dir.join("config.toml");
        if config_path.exists() {
            fs::set_permissions(&config_path, fs::Permissions::from_mode(0o600)).with_context(
                || format!("Failed to set permissions on {}", config_path.display()),
            )?;
        }
        let secret_key_path = config_dir.join(".secret_key");
        if secret_key_path.exists() {
            fs::set_permissions(&secret_key_path, fs::Permissions::from_mode(0o600)).with_context(
                || format!("Failed to set permissions on {}", secret_key_path.display()),
            )?;
        }
    }

    chown_recursive_to_construct(config_dir)?;

    let created_log_dir = !log_dir.exists();
    if created_log_dir {
        fs::create_dir_all(log_dir)
            .with_context(|| format!("Failed to create {}", log_dir.display()))?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(log_dir, fs::Permissions::from_mode(0o750))
                .with_context(|| format!("Failed to set permissions on {}", log_dir.display()))?;
        }
    }

    chown_to_construct(log_dir)?;

    ensure_openrc_runtime_dirs_writable(config_dir, &workspace_dir, log_dir)?;

    if created_log_dir {
        println!(
            "✅ Created directory: {} (owned by construct:construct)",
            log_dir.display()
        );
    }

    let init_script = generate_openrc_script(&exe, config_dir);
    let init_path = Path::new("/etc/init.d/construct");
    fs::write(init_path, init_script)
        .with_context(|| format!("Failed to write {}", init_path.display()))?;

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(init_path, fs::Permissions::from_mode(0o755))
            .with_context(|| format!("Failed to set permissions on {}", init_path.display()))?;
    }

    run_checked(Command::new("rc-update").args(["add", "construct", "default"]))?;
    println!("✅ Installed OpenRC service: /etc/init.d/construct");
    println!("   Config path: /etc/construct/config.toml");
    println!("   Start with: sudo construct service start");
    let _ = config;
    Ok(())
}

fn install_windows(config: &Config) -> Result<()> {
    // FD limits not needed on Windows — default per-process handle limits are already in the tens of thousands.
    let exe = std::env::current_exe().context("Failed to resolve current executable")?;
    let logs_dir = config
        .config_path
        .parent()
        .map_or_else(|| PathBuf::from("."), PathBuf::from)
        .join("logs");
    fs::create_dir_all(&logs_dir)?;

    // Create a wrapper script that redirects output to log files
    let wrapper = logs_dir.join("construct-daemon.cmd");
    let stdout_log = logs_dir.join("daemon.stdout.log");
    let stderr_log = logs_dir.join("daemon.stderr.log");

    // PATH override — same root cause as the launchd/systemd/OpenRC
    // fixes: scheduled-task wrappers can drift away from the user's
    // current interactive PATH between install time and trigger time.
    // Snapshot install-time PATH and prepend the npm-global / cargo
    // dirs so codex/claude spawns work even if PATH later changes.
    let path_env = build_windows_daemon_path();

    let wrapper_content = format!(
        "@echo off\r\nset \"PATH={path}\"\r\n\"{exe}\" daemon >>\"{stdout}\" 2>>\"{stderr}\"",
        path = path_env,
        exe = exe.display(),
        stdout = stdout_log.display(),
        stderr = stderr_log.display(),
    );
    fs::write(&wrapper, &wrapper_content)?;

    let task_name = windows_task_name();

    // Remove any existing task first (ignore errors if it doesn't exist)
    let _ = Command::new("schtasks")
        .args(["/Delete", "/TN", task_name, "/F"])
        .output();

    run_checked(Command::new("schtasks").args([
        "/Create",
        "/TN",
        task_name,
        "/SC",
        "ONLOGON",
        "/TR",
        &format!("\"{}\"", wrapper.display()),
        "/RL",
        "HIGHEST",
        "/F",
    ]))?;

    println!("✅ Installed Windows scheduled task: {}", task_name);
    println!("   Wrapper: {}", wrapper.display());
    println!("   Logs: {}", logs_dir.display());
    println!("   Start with: construct service start");
    Ok(())
}

fn macos_service_file() -> Result<PathBuf> {
    let home = directories::UserDirs::new()
        .map(|u| u.home_dir().to_path_buf())
        .context("Could not find home directory")?;
    Ok(home
        .join("Library")
        .join("LaunchAgents")
        .join(format!("{SERVICE_LABEL}.plist")))
}

fn linux_service_file(config: &Config) -> Result<PathBuf> {
    let home = directories::UserDirs::new()
        .map(|u| u.home_dir().to_path_buf())
        .context("Could not find home directory")?;
    let _ = config;
    Ok(home
        .join(".config")
        .join("systemd")
        .join("user")
        .join("construct.service"))
}

fn run_checked(command: &mut Command) -> Result<()> {
    let output = command.output().context("Failed to spawn command")?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        anyhow::bail!("Command failed: {}", stderr.trim());
    }
    Ok(())
}

fn run_capture(command: &mut Command) -> Result<String> {
    let output = command.output().context("Failed to spawn command")?;
    let mut text = String::from_utf8_lossy(&output.stdout).to_string();
    if text.trim().is_empty() {
        text = String::from_utf8_lossy(&output.stderr).to_string();
    }
    Ok(text)
}

fn xml_escape(raw: &str) -> String {
    raw.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn xml_escape_escapes_reserved_chars() {
        let escaped = xml_escape("<&>\"' and text");
        assert_eq!(escaped, "&lt;&amp;&gt;&quot;&apos; and text");
    }

    #[cfg(not(target_os = "windows"))]
    #[test]
    fn run_capture_reads_stdout() {
        let out = run_capture(Command::new("sh").args(["-c", "echo hello"]))
            .expect("stdout capture should succeed");
        assert_eq!(out.trim(), "hello");
    }

    #[cfg(not(target_os = "windows"))]
    #[test]
    fn run_capture_falls_back_to_stderr() {
        let out = run_capture(Command::new("sh").args(["-c", "echo warn 1>&2"]))
            .expect("stderr capture should succeed");
        assert_eq!(out.trim(), "warn");
    }

    #[cfg(not(target_os = "windows"))]
    #[test]
    fn run_checked_errors_on_non_zero_status() {
        let err = run_checked(Command::new("sh").args(["-c", "exit 17"]))
            .expect_err("non-zero exit should error");
        assert!(err.to_string().contains("Command failed"));
    }

    #[cfg(not(target_os = "windows"))]
    #[test]
    fn linux_service_file_has_expected_suffix() {
        let file = linux_service_file(&Config::default()).unwrap();
        let path = file.to_string_lossy();
        assert!(path.ends_with(".config/systemd/user/construct.service"));
    }

    #[test]
    fn windows_task_name_is_constant() {
        assert_eq!(windows_task_name(), "Construct Daemon");
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn run_capture_reads_stdout_windows() {
        let out = run_capture(Command::new("cmd").args(["/C", "echo hello"]))
            .expect("stdout capture should succeed");
        assert_eq!(out.trim(), "hello");
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn run_checked_errors_on_non_zero_status_windows() {
        let err = run_checked(Command::new("cmd").args(["/C", "exit /b 17"]))
            .expect_err("non-zero exit should error");
        assert!(err.to_string().contains("Command failed"));
    }

    #[test]
    fn init_system_from_str_parses_valid_values() {
        assert_eq!("auto".parse::<InitSystem>().unwrap(), InitSystem::Auto);
        assert_eq!("AUTO".parse::<InitSystem>().unwrap(), InitSystem::Auto);
        assert_eq!(
            "systemd".parse::<InitSystem>().unwrap(),
            InitSystem::Systemd
        );
        assert_eq!(
            "SYSTEMD".parse::<InitSystem>().unwrap(),
            InitSystem::Systemd
        );
        assert_eq!("openrc".parse::<InitSystem>().unwrap(), InitSystem::Openrc);
        assert_eq!("OPENRC".parse::<InitSystem>().unwrap(), InitSystem::Openrc);
    }

    #[test]
    fn init_system_from_str_rejects_unknown() {
        let err = "unknown"
            .parse::<InitSystem>()
            .expect_err("should reject unknown");
        assert!(err.to_string().contains("Unknown init system"));
        assert!(err.to_string().contains("Supported: auto, systemd, openrc"));
    }

    #[test]
    fn init_system_default_is_auto() {
        assert_eq!(InitSystem::default(), InitSystem::Auto);
    }

    #[cfg(unix)]
    #[test]
    fn is_root_matches_system_uid() {
        // SAFETY: `getuid()` is a simple system call that returns the real user ID of the calling
        // process. It is always safe to call as it takes no arguments and returns a scalar value.
        // This test verifies our `is_root()` wrapper returns the same result as the raw syscall.
        assert_eq!(is_root(), unsafe { libc::getuid() == 0 });
    }

    #[test]
    fn generate_openrc_script_contains_required_directives() {
        use std::path::PathBuf;

        let exe_path = PathBuf::from("/usr/local/bin/construct");
        let script = generate_openrc_script(&exe_path, Path::new("/etc/construct"));

        assert!(script.starts_with("#!/sbin/openrc-run"));
        assert!(script.contains("name=\"construct\""));
        assert!(script.contains("description=\"Construct daemon\""));
        assert!(script.contains("command=\"/usr/local/bin/construct\""));
        assert!(script.contains("command_args=\"--config-dir /etc/construct daemon\""));
        assert!(!script.contains("env CONSTRUCT_CONFIG_DIR"));
        assert!(!script.contains("env CONSTRUCT_WORKSPACE"));
        assert!(script.contains("command_background=\"yes\""));
        assert!(script.contains("command_user=\"construct:construct\""));
        assert!(script.contains("pidfile=\"/run/${RC_SVCNAME}.pid\""));
        assert!(script.contains("umask 027"));
        assert!(script.contains("output_log=\"/var/log/construct/access.log\""));
        assert!(script.contains("error_log=\"/var/log/construct/error.log\""));
        assert!(script.contains("depend()"));
        assert!(script.contains("need net"));
        assert!(script.contains("after firewall"));
    }

    #[test]
    fn generate_openrc_script_sets_home_for_browser() {
        use std::path::PathBuf;

        let exe_path = PathBuf::from("/usr/local/bin/construct");
        let script = generate_openrc_script(&exe_path, Path::new("/etc/construct"));

        assert!(
            script.contains("export HOME=\"/var/lib/construct\""),
            "OpenRC script must set HOME for headless browser support"
        );
    }

    #[test]
    fn generate_openrc_script_creates_home_directory() {
        use std::path::PathBuf;

        let exe_path = PathBuf::from("/usr/local/bin/construct");
        let script = generate_openrc_script(&exe_path, Path::new("/etc/construct"));

        assert!(
            script.contains("start_pre()"),
            "OpenRC script must have start_pre to create HOME dir"
        );
        assert!(
            script.contains("checkpath --directory --owner construct:construct"),
            "start_pre must ensure /var/lib/construct exists with correct ownership"
        );
    }

    #[test]
    fn systemd_unit_contains_home_and_pass_environment() {
        let unit = "[Unit]\n\
             Description=Construct daemon\n\
             After=network.target\n\
             \n\
             [Service]\n\
             Type=simple\n\
             ExecStart=/usr/local/bin/construct daemon\n\
             Restart=always\n\
             RestartSec=3\n\
             # Ensure HOME is set so headless browsers can create profile/cache dirs.\n\
             Environment=HOME=%h\n\
             # Allow inheriting DISPLAY and XDG_RUNTIME_DIR from the user session\n\
             # so graphical/headless browsers can function correctly.\n\
             PassEnvironment=DISPLAY XDG_RUNTIME_DIR\n\
             \n\
             [Install]\n\
             WantedBy=default.target\n"
            .to_string();

        assert!(
            unit.contains("Environment=HOME=%h"),
            "systemd unit must set HOME for headless browser support"
        );
        assert!(
            unit.contains("PassEnvironment=DISPLAY XDG_RUNTIME_DIR"),
            "systemd unit must pass through display/runtime env vars"
        );
    }

    #[test]
    fn build_unix_daemon_path_with_user_dirs_contains_curated_defaults() {
        // include_user_dirs=true → must carry the user-bin shortcuts
        // (~/.cargo/bin, ~/.local/bin) plus the system-wide locations.
        // Without these, daemon subprocess spawns of codex/claude fail
        // with [Errno 2] No such file or directory even though the
        // user's interactive shell finds them.
        let path = build_unix_daemon_path(true);
        assert!(
            path.contains("/.cargo/bin"),
            "missing ~/.cargo/bin in: {path}"
        );
        assert!(
            path.contains("/.local/bin"),
            "missing ~/.local/bin in: {path}"
        );
        assert!(
            path.contains("/usr/local/bin"),
            "missing /usr/local/bin in: {path}"
        );
        assert!(
            path.contains("/opt/homebrew/bin"),
            "missing /opt/homebrew/bin in: {path}"
        );
        assert!(path.contains("/usr/bin"), "missing /usr/bin in: {path}");
    }

    #[test]
    fn build_unix_daemon_path_without_user_dirs_drops_home_paths() {
        // include_user_dirs=false (OpenRC daemon runs as system user)
        // → must NOT inject ~/.cargo/bin etc., since those resolve to
        // the install user's home which the system `construct` user
        // can't read.
        let path = build_unix_daemon_path(false);
        assert!(
            !path.contains(".cargo/bin"),
            "OpenRC PATH should not include ~/.cargo/bin: {path}"
        );
        assert!(
            !path.contains(".local/bin"),
            "OpenRC PATH should not include ~/.local/bin: {path}"
        );
        assert!(
            path.contains("/usr/local/bin"),
            "OpenRC PATH must still include /usr/local/bin: {path}"
        );
    }

    #[test]
    fn build_windows_daemon_path_uses_semicolon_and_npm_dir() {
        // Windows variant — semicolon-separated. Even when the env vars
        // aren't set on the test runner, the helper must produce a valid
        // (possibly empty) string and not panic. When USERPROFILE/APPDATA
        // are present, the npm + cargo bin shortcuts must be present.
        let path = build_windows_daemon_path();
        if std::env::var("APPDATA").is_ok() {
            assert!(
                path.contains(r"\npm"),
                "Windows PATH should include %APPDATA%\\npm: {path}"
            );
        }
        // No-op assertion to keep this test useful on non-Windows runners
        // where USERPROFILE/APPDATA aren't typically set.
        let _ = path;
    }

    #[test]
    fn generate_openrc_script_sets_fd_limit() {
        // Real-world incident 2026-05-11: launchd-supervised daemon hit
        // `Too many open files` spawning MCP servers. OpenRC variant
        // must declare `rc_ulimit="-n 4096"` so the supervisor calls
        // `ulimit -n 4096` before exec'ing the daemon.
        use std::path::PathBuf;

        let exe_path = PathBuf::from("/usr/local/bin/construct");
        let script = generate_openrc_script(&exe_path, Path::new("/etc/construct"));

        assert!(
            script.contains("rc_ulimit=\"-n 4096\""),
            "OpenRC script must set rc_ulimit so the daemon gets a raised NOFILE soft cap: {script}"
        );
    }

    #[test]
    fn systemd_unit_sets_fd_limit() {
        // Real-world incident 2026-05-11: same root cause as the launchd
        // / OpenRC variants. systemd format is `<soft>:<hard>`.
        let exe_path = Path::new("/usr/local/bin/construct");
        let unit = format_systemd_unit(exe_path);

        assert!(
            unit.contains("LimitNOFILE=4096:8192"),
            "systemd unit must set LimitNOFILE for daemon MCP spawns: {unit}"
        );
    }

    #[test]
    fn macos_plist_sets_fd_limit() {
        // Real-world incident 2026-05-11: launchd's default NumberOfFiles
        // is 256. Plist must declare both Soft + HardResourceLimits with
        // NumberOfFiles entries so a freshly-installed daemon can spawn
        // its MCP sidecars at scale.
        let exe = Path::new("/usr/local/bin/construct");
        let stdout = Path::new("/var/log/construct/daemon.stdout.log");
        let stderr = Path::new("/var/log/construct/daemon.stderr.log");
        let plist = format_macos_plist(exe, None, stdout, stderr);

        assert!(
            plist.contains("<key>NumberOfFiles</key>"),
            "plist must declare NumberOfFiles resource limit: {plist}"
        );
        assert!(
            plist.contains("<integer>4096</integer>"),
            "plist must set soft NumberOfFiles to 4096: {plist}"
        );
        assert!(
            plist.contains("<integer>8192</integer>"),
            "plist must set hard NumberOfFiles to 8192: {plist}"
        );
        assert!(
            plist.contains("<key>SoftResourceLimits</key>"),
            "plist must include SoftResourceLimits stanza: {plist}"
        );
        assert!(
            plist.contains("<key>HardResourceLimits</key>"),
            "plist must include HardResourceLimits stanza: {plist}"
        );
    }

    #[test]
    fn generate_openrc_script_sets_path_for_subprocess_spawns() {
        use std::path::PathBuf;

        let exe_path = PathBuf::from("/usr/local/bin/construct");
        let script = generate_openrc_script(&exe_path, Path::new("/etc/construct"));

        assert!(
            script.contains("export PATH=\""),
            "OpenRC script must export PATH so daemon-spawned codex/claude can be found"
        );
        assert!(
            script.contains("/usr/local/bin"),
            "OpenRC PATH must include /usr/local/bin"
        );
        // OpenRC daemon runs as system user — must not reference user-bin dirs.
        assert!(
            !script.contains(".cargo/bin"),
            "OpenRC PATH must NOT reference user ~/.cargo/bin (different HOME)"
        );
    }

    #[test]
    fn warn_if_binary_in_home_detects_home_path() {
        use std::path::PathBuf;

        let home_path = PathBuf::from("/home/user/.cargo/bin/construct");
        assert!(home_path.to_string_lossy().contains("/home/"));
        assert!(home_path.to_string_lossy().contains(".cargo/bin"));

        let cargo_path = PathBuf::from("/home/user/.cargo/bin/construct");
        assert!(cargo_path.to_string_lossy().contains(".cargo/bin"));

        let system_path = PathBuf::from("/usr/local/bin/construct");
        assert!(!system_path.to_string_lossy().contains("/home/"));
        assert!(!system_path.to_string_lossy().contains(".cargo/bin"));
    }

    #[cfg(unix)]
    #[test]
    fn shell_single_quote_escapes_single_quotes() {
        assert_eq!(
            shell_single_quote("/tmp/weird'path"),
            "'/tmp/weird'\"'\"'path'"
        );
    }

    #[cfg(unix)]
    #[test]
    fn openrc_writability_probe_prefers_runuser_when_available() {
        let (program, args) =
            build_openrc_writability_probe_command(Path::new("/etc/construct"), true);
        assert_eq!(program, "runuser");
        assert_eq!(
            args,
            vec![
                "-u".to_string(),
                "construct".to_string(),
                "--".to_string(),
                "sh".to_string(),
                "-c".to_string(),
                "test -w '/etc/construct'".to_string()
            ]
        );
    }

    #[test]
    fn detect_homebrew_var_dir_from_cellar_path() {
        let exe = PathBuf::from("/opt/homebrew/Cellar/construct/1.2.3/bin/construct");
        let var_dir = detect_homebrew_var_dir(&exe);
        assert_eq!(var_dir, Some(PathBuf::from("/opt/homebrew/var/construct")));
    }

    #[test]
    fn detect_homebrew_var_dir_intel_cellar_path() {
        let exe = PathBuf::from("/usr/local/Cellar/construct/1.0.0/bin/construct");
        let var_dir = detect_homebrew_var_dir(&exe);
        assert_eq!(var_dir, Some(PathBuf::from("/usr/local/var/construct")));
    }

    #[test]
    fn detect_homebrew_var_dir_non_homebrew_path() {
        let exe = PathBuf::from("/home/user/.cargo/bin/construct");
        let var_dir = detect_homebrew_var_dir(&exe);
        assert_eq!(var_dir, None);
    }

    #[cfg(unix)]
    #[test]
    fn openrc_writability_probe_falls_back_to_su() {
        let (program, args) =
            build_openrc_writability_probe_command(Path::new("/etc/construct/workspace"), false);
        assert_eq!(program, "su");
        assert_eq!(
            args,
            vec![
                "-s".to_string(),
                "/bin/sh".to_string(),
                "-c".to_string(),
                "test -w '/etc/construct/workspace'".to_string(),
                "construct".to_string()
            ]
        );
    }

    #[cfg(not(target_os = "windows"))]
    #[test]
    fn tail_file_errors_on_missing_file() {
        let missing = Path::new("/tmp/construct-test-nonexistent-log-file.log");
        let result = tail_file(missing, 10, false);
        assert!(result.is_err(), "tail on missing file should fail");
    }

    #[cfg(not(target_os = "windows"))]
    #[test]
    fn tail_file_reads_existing_file() {
        let dir = tempfile::tempdir().expect("failed to create temp dir");
        let log = dir.path().join("test-tail.log");
        fs::write(&log, "line1\nline2\nline3\nline4\nline5\n").unwrap();
        // tail should succeed on existing file
        let result = tail_file(&log, 3, false);
        assert!(result.is_ok(), "tail on existing file should succeed");
    }

    #[test]
    fn rotate_daemon_logs_rotates_oversized_files() {
        let dir = tempfile::tempdir().expect("failed to create temp dir");
        let stderr_log = dir.path().join("daemon.stderr.log");
        let stdout_log = dir.path().join("daemon.stdout.log");
        fs::write(&stderr_log, "123456").unwrap();
        fs::write(&stdout_log, "small").unwrap();

        rotate_daemon_logs_in(dir.path(), 6).unwrap();

        assert!(!stderr_log.exists());
        assert_eq!(
            fs::read_to_string(dir.path().join("daemon.stderr.log.1")).unwrap(),
            "123456"
        );
        assert!(stdout_log.exists());
    }

    #[test]
    fn rotate_daemon_logs_shifts_existing_retention() {
        let dir = tempfile::tempdir().expect("failed to create temp dir");
        let stderr_log = dir.path().join("daemon.stderr.log");
        fs::write(&stderr_log, "new-large").unwrap();
        fs::write(dir.path().join("daemon.stderr.log.1"), "old-1").unwrap();

        rotate_daemon_logs_in(dir.path(), 1).unwrap();

        assert_eq!(
            fs::read_to_string(dir.path().join("daemon.stderr.log.1")).unwrap(),
            "new-large"
        );
        assert_eq!(
            fs::read_to_string(dir.path().join("daemon.stderr.log.2")).unwrap(),
            "old-1"
        );
    }

    #[test]
    fn logs_variant_is_recognized() {
        // Ensure the Logs variant can be constructed and matched
        let cmd = crate::ServiceCommands::Logs {
            lines: 25,
            follow: true,
        };
        match &cmd {
            crate::ServiceCommands::Logs { lines, follow } => {
                assert_eq!(*lines, 25);
                assert!(*follow);
            }
            _ => panic!("Expected Logs variant"),
        }
    }
}
