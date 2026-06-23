//! UF2 flashing support — detect BOOTSEL-mode Pico and deploy firmware.
//!
//! # Workflow
//! 1. [`find_rpi_rp2_mount`] — check well-known mount points for the RPI-RP2 volume
//!    that appears when a Pico is held in BOOTSEL mode.
//! 2. [`ensure_firmware_dir`] — extract the bundled firmware files to
//!    `~/.revka/firmware/pico/` if they aren't there yet.
//! 3. [`flash_uf2`] — copy the UF2 to the mount point; the Pico reboots automatically.
//!
//! # Embedded assets
//! Both firmware files are compiled into the binary with `include_bytes!` so
//! users never need to download them separately.

use anyhow::{Result, bail};
use std::path::{Path, PathBuf};

// ── Embedded firmware ─────────────────────────────────────────────────────────

/// MicroPython UF2 binary — copied to RPI-RP2 to install the base runtime.
const PICO_UF2: &[u8] = include_bytes!("../../firmware/pico/revka-pico.uf2");

/// Revka serial protocol handler — written to the Pico after MicroPython boots.
pub const PICO_MAIN_PY: &[u8] = include_bytes!("../../firmware/pico/main.py");

/// UF2 magic word 1 (little-endian bytes at offset 0 of every UF2 block).
const UF2_MAGIC1: [u8; 4] = [0x55, 0x46, 0x32, 0x0A];

/// Minimum plausible size for a real MicroPython RPI_PICO UF2. A real image is
/// hundreds of KB; the bundled placeholder is a single 512-byte block with a
/// zeroed payload, so this floor reliably rejects the stub (#438). A
/// magic-only check is not enough — a structurally-valid empty UF2 has correct
/// magic and would flash zeros over the Pico's flash, bricking the runtime.
const MIN_REAL_UF2_BYTES: usize = 100 * 1024;

/// Validate that `data` is a *real* UF2 image, not a placeholder: correct magic
/// AND a plausible size. Returns a clear "supply the real UF2" error otherwise,
/// so a stub is never written to a Pico (#438).
fn validate_real_uf2(data: &[u8], source: &str) -> Result<()> {
    if data.len() < 8 || data[..4] != UF2_MAGIC1 {
        bail!(
            "{source} is not a valid UF2 file (magic mismatch). Download the real \
             MicroPython UF2 from https://micropython.org/download/RPI_PICO/."
        );
    }
    if data.len() < MIN_REAL_UF2_BYTES {
        bail!(
            "{source} is a {len}-byte placeholder, not real MicroPython firmware \
             (a real RPI_PICO UF2 is hundreds of KB). Flashing it would brick the \
             Pico's runtime. Download the real UF2 from \
             https://micropython.org/download/RPI_PICO/ and place it at \
             ~/.revka/firmware/pico/revka-pico.uf2 (existing files are never \
             overwritten), or replace firmware/pico/revka-pico.uf2 and rebuild Revka.",
            len = data.len()
        );
    }
    Ok(())
}

/// True if `main.py` is the placeholder stub — valid UTF-8 whose every non-blank
/// line is a comment, i.e. it carries no executable serial-protocol handler
/// (#438). Non-UTF-8 input is not treated as our placeholder.
fn main_py_is_placeholder(data: &[u8]) -> bool {
    match std::str::from_utf8(data) {
        Ok(src) => src
            .lines()
            .map(str::trim)
            .filter(|line| !line.is_empty())
            .all(|line| line.starts_with('#')),
        Err(_) => false,
    }
}

// ── Volume detection ──────────────────────────────────────────────────────────

/// Find the RPI-RP2 mount point if a Pico is connected in BOOTSEL mode.
///
/// Checks:
/// - macOS:  `/Volumes/RPI-RP2`
/// - Linux:  `/media/*/RPI-RP2` and `/run/media/*/RPI-RP2`
pub fn find_rpi_rp2_mount() -> Option<PathBuf> {
    // macOS
    let mac = PathBuf::from("/Volumes/RPI-RP2");
    if mac.exists() {
        return Some(mac);
    }

    // Linux — /media/<user>/RPI-RP2  or  /run/media/<user>/RPI-RP2
    for base in &["/media", "/run/media"] {
        if let Ok(entries) = std::fs::read_dir(base) {
            for entry in entries.flatten() {
                let candidate = entry.path().join("RPI-RP2");
                if candidate.exists() {
                    return Some(candidate);
                }
            }
        }
    }

    None
}

// ── Firmware directory management ─────────────────────────────────────────────

/// Ensure `~/.revka/firmware/pico/` exists and contains the bundled assets.
///
/// Files are only written if they are absent — existing files are never overwritten
/// so users can substitute their own firmware.
///
/// Returns the firmware directory path.
pub fn ensure_firmware_dir() -> Result<PathBuf> {
    use directories::BaseDirs;

    let base = BaseDirs::new().ok_or_else(|| anyhow::anyhow!("cannot determine home directory"))?;

    let firmware_dir = base.home_dir().join(".revka").join("firmware").join("pico");
    std::fs::create_dir_all(&firmware_dir)?;

    // UF2 — only write a real image. The bundled UF2 is a placeholder stub, so
    // this fails loudly (rather than extracting a brick) until a real UF2 is
    // dropped in at `uf2_path` (#438).
    let uf2_path = firmware_dir.join("revka-pico.uf2");
    if !uf2_path.exists() {
        validate_real_uf2(PICO_UF2, "the bundled UF2")?;
        std::fs::write(&uf2_path, PICO_UF2)?;
        tracing::info!(path = %uf2_path.display(), "extracted bundled UF2");
    }

    // main.py — only write a real handler. The bundled main.py is a placeholder
    // stub (comments only), so refuse to extract it until a real serial-protocol
    // handler is dropped in at `main_py_path` (#438).
    let main_py_path = firmware_dir.join("main.py");
    if !main_py_path.exists() {
        if main_py_is_placeholder(PICO_MAIN_PY) {
            bail!(
                "The bundled main.py is a placeholder with no serial-protocol handler. \
                 Provide a real MicroPython main.py at {} (existing files are never \
                 overwritten), or replace firmware/pico/main.py and rebuild Revka.",
                main_py_path.display()
            );
        }
        std::fs::write(&main_py_path, PICO_MAIN_PY)?;
        tracing::info!(path = %main_py_path.display(), "extracted bundled main.py");
    }

    Ok(firmware_dir)
}

// ── Flashing ──────────────────────────────────────────────────────────────────

/// Copy the UF2 file to the RPI-RP2 mount point.
///
/// macOS often returns "Operation not permitted" for `std::fs::copy` on FAT
/// volumes presented by BOOTSEL-mode Picos.  We try four approaches in order
/// and return a clear manual-fallback message if all fail:
///
/// 1. `std::fs::copy`  — fast, no subprocess; works on most Linux setups.
/// 2. `cp <src> <dst>` — bypasses some macOS VFS permission layers.
/// 3. `sudo cp …`      — escalates for locked volumes.
/// 4. Error — instructs the user to run the `sudo cp` manually.
pub async fn flash_uf2(mount_point: &Path, firmware_dir: &Path) -> Result<()> {
    let uf2_src = firmware_dir.join("revka-pico.uf2");
    let uf2_dst = mount_point.join("firmware.uf2");
    let src_str = uf2_src.to_string_lossy().into_owned();
    let dst_str = uf2_dst.to_string_lossy().into_owned();

    tracing::info!(
        src = %src_str,
        dst = %dst_str,
        "flashing UF2"
    );

    // Validate magic AND size before any copy attempt — prevents flashing a
    // structurally-valid-but-empty placeholder that would brick the Pico (#438).
    let data = std::fs::read(&uf2_src)?;
    validate_real_uf2(&data, &format!("UF2 at {}", uf2_src.display()))?;

    // ── Attempt 1: std::fs::copy (works on Linux, sometimes blocked on macOS) ─
    {
        let src = uf2_src.clone();
        let dst = uf2_dst.clone();
        let result = tokio::task::spawn_blocking(move || std::fs::copy(&src, &dst))
            .await
            .map_err(|e| anyhow::anyhow!("copy task panicked: {e}"));

        match result {
            Ok(Ok(_)) => {
                tracing::info!("UF2 copy complete (std::fs::copy) — Pico will reboot");
                return Ok(());
            }
            Ok(Err(e)) => tracing::warn!("std::fs::copy failed ({}), trying cp", e),
            Err(e) => tracing::warn!("std::fs::copy task failed ({}), trying cp", e),
        }
    }

    // ── Attempt 2: cp via subprocess ──────────────────────────────────────────
    {
        /// Timeout for subprocess copy attempts (seconds).
        const CP_TIMEOUT_SECS: u64 = 10;

        let out = tokio::time::timeout(
            std::time::Duration::from_secs(CP_TIMEOUT_SECS),
            tokio::process::Command::new("cp")
                .arg(&src_str)
                .arg(&dst_str)
                .output(),
        )
        .await;

        match out {
            Err(_elapsed) => {
                tracing::warn!("cp timed out after {}s, trying sudo cp", CP_TIMEOUT_SECS);
            }
            Ok(Ok(o)) if o.status.success() => {
                tracing::info!("UF2 copy complete (cp) — Pico will reboot");
                return Ok(());
            }
            Ok(Ok(o)) => {
                let stderr = String::from_utf8_lossy(&o.stderr);
                tracing::warn!("cp failed ({}), trying sudo cp", stderr.trim());
            }
            Ok(Err(e)) => tracing::warn!("cp spawn failed ({}), trying sudo cp", e),
        }
    }

    // ── Attempt 3: sudo cp (non-interactive) ─────────────────────────────────
    {
        const SUDO_CP_TIMEOUT_SECS: u64 = 10;

        let out = tokio::time::timeout(
            std::time::Duration::from_secs(SUDO_CP_TIMEOUT_SECS),
            tokio::process::Command::new("sudo")
                .args(["-n", "cp", &src_str, &dst_str])
                .output(),
        )
        .await;

        match out {
            Err(_elapsed) => {
                tracing::warn!("sudo cp timed out after {}s", SUDO_CP_TIMEOUT_SECS);
            }
            Ok(Ok(o)) if o.status.success() => {
                tracing::info!("UF2 copy complete (sudo cp) — Pico will reboot");
                return Ok(());
            }
            Ok(Ok(o)) => {
                let stderr = String::from_utf8_lossy(&o.stderr);
                tracing::warn!("sudo cp failed: {}", stderr.trim());
            }
            Ok(Err(e)) => tracing::warn!("sudo cp spawn failed: {}", e),
        }
    }

    // ── All attempts failed — give the user a clear manual command ────────────
    bail!(
        "All copy methods failed. Run this command manually, then restart Revka:\n\
         \n  sudo cp {src_str} {dst_str}\n"
    )
}

/// Wait for `/dev/cu.usbmodem*` (macOS) or `/dev/ttyACM*` (Linux) to appear.
///
/// Polls every `interval` for up to `timeout`. Returns the first matching path
/// found, or `None` if the deadline expires.
pub async fn wait_for_serial_port(
    timeout: std::time::Duration,
    interval: std::time::Duration,
) -> Option<PathBuf> {
    #[cfg(target_os = "macos")]
    let patterns = &["/dev/cu.usbmodem*"];
    #[cfg(target_os = "linux")]
    let patterns = &["/dev/ttyACM*"];
    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    let patterns: &[&str] = &[];

    let deadline = tokio::time::Instant::now() + timeout;

    loop {
        for pattern in patterns {
            if let Ok(mut hits) = glob::glob(pattern) {
                if let Some(Ok(path)) = hits.next() {
                    return Some(path);
                }
            }
        }

        if tokio::time::Instant::now() >= deadline {
            return None;
        }

        tokio::time::sleep(interval).await;
    }
}

// ── Deploy main.py via mpremote ───────────────────────────────────────────────

/// Copy `main.py` to the Pico's MicroPython filesystem and soft-reset it.
///
/// After the UF2 is flashed the Pico reboots into MicroPython but has no
/// `main.py` on its internal filesystem.  This function uses `mpremote` to
/// upload the bundled `main.py` and issue a reset so it starts executing
/// immediately.
///
/// Returns `Ok(())` on success or an error with a helpful fallback command.
pub async fn deploy_main_py(port: &Path, firmware_dir: &Path) -> Result<()> {
    let main_py_src = firmware_dir.join("main.py");
    let src_str = main_py_src.to_string_lossy().into_owned();
    let port_str = port.to_string_lossy().into_owned();

    if !main_py_src.exists() {
        bail!(
            "main.py not found at {} — run ensure_firmware_dir() first",
            main_py_src.display()
        );
    }

    // Refuse to deploy a placeholder stub — it would leave the Pico without a
    // working serial-protocol handler (#438).
    if main_py_is_placeholder(&std::fs::read(&main_py_src)?) {
        bail!(
            "main.py at {} is a placeholder with no serial-protocol handler. \
             Replace it with a real MicroPython handler before deploying.",
            main_py_src.display()
        );
    }

    tracing::info!(
        src = %src_str,
        port = %port_str,
        "deploying main.py via mpremote"
    );

    let out = tokio::process::Command::new("mpremote")
        .args([
            "connect", &port_str, "cp", &src_str, ":main.py", "+", "reset",
        ])
        .output()
        .await;

    match out {
        Ok(o) if o.status.success() => {
            tracing::info!("main.py deployed and Pico reset via mpremote");
            Ok(())
        }
        Ok(o) => {
            let stderr = String::from_utf8_lossy(&o.stderr);
            bail!(
                "mpremote failed (exit {}): {}.\n\
                 Run manually:\n  mpremote connect {port_str} cp {src_str} :main.py + reset",
                o.status,
                stderr.trim()
            )
        }
        Err(e) => {
            bail!(
                "mpremote not found or could not start ({e}).\n\
                 Install it with: pip install mpremote\n\
                 Then run: mpremote connect {port_str} cp {src_str} :main.py + reset"
            )
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validate_real_uf2_accepts_real_sized_image() {
        let mut img = UF2_MAGIC1.to_vec();
        img.resize(MIN_REAL_UF2_BYTES, 0);
        assert!(validate_real_uf2(&img, "test").is_ok());
    }

    #[test]
    fn validate_real_uf2_rejects_wrong_magic() {
        let img = vec![0u8; MIN_REAL_UF2_BYTES];
        let err = validate_real_uf2(&img, "test").unwrap_err().to_string();
        assert!(err.contains("magic"), "got: {err}");
    }

    #[test]
    fn validate_real_uf2_rejects_placeholder_sized_image() {
        // Correct magic, but only a single 512-byte block — the placeholder shape.
        let mut img = UF2_MAGIC1.to_vec();
        img.resize(512, 0);
        let err = validate_real_uf2(&img, "test").unwrap_err().to_string();
        assert!(err.contains("placeholder"), "got: {err}");
    }

    #[test]
    fn bundled_uf2_is_currently_a_placeholder() {
        // Tripwire (#438): the bundled UF2 is a zeroed placeholder, so the guard
        // MUST reject it (it can never be flashed). When a real MicroPython UF2
        // is bundled, this assertion flips — update it to assert the image is
        // real (validate_real_uf2(PICO_UF2, ..).is_ok()).
        assert!(
            validate_real_uf2(PICO_UF2, "bundled").is_err(),
            "bundled UF2 now looks real — replace this tripwire with an is_ok() check"
        );
    }

    #[test]
    fn pico_main_py_is_non_empty() {
        assert!(!PICO_MAIN_PY.is_empty(), "bundled main.py is empty");
    }

    #[test]
    fn bundled_main_py_is_currently_a_placeholder() {
        // Tripwire (#438): the bundled main.py is a comment-only stub, so the
        // guard MUST flag it. When a real handler is bundled, flip this to assert
        // !main_py_is_placeholder(PICO_MAIN_PY).
        assert!(
            main_py_is_placeholder(PICO_MAIN_PY),
            "bundled main.py now has executable code — replace this tripwire"
        );
    }

    #[test]
    fn main_py_is_placeholder_detects_real_code() {
        assert!(!main_py_is_placeholder(
            b"# comment\nimport sys\nprint('hi')\n"
        ));
        assert!(main_py_is_placeholder(b"# only\n# comments\n\n"));
    }

    #[test]
    fn find_rpi_rp2_mount_returns_none_when_not_connected() {
        // This test runs on CI without a Pico attached — just verify it doesn't panic.
        let _ = find_rpi_rp2_mount(); // may be Some or None depending on environment
    }

    #[test]
    fn uf2_magic_constant_is_correct() {
        // UF2 magic word 1 as per the UF2 spec: 0x0A324655
        assert_eq!(UF2_MAGIC1, [0x55, 0x46, 0x32, 0x0A]);
    }

    #[test]
    fn ensure_firmware_dir_creates_directory() {
        // This test verifies ensure_firmware_dir creates the ~/.revka/firmware/pico/ path.
        // It may fail on the UF2 magic check (placeholder UF2) — that's expected and OK.
        let result = ensure_firmware_dir();
        // Either succeeds (real UF2) or fails with a clear placeholder message.
        match result {
            Ok(dir) => {
                assert!(
                    dir.exists(),
                    "firmware dir should exist after ensure_firmware_dir"
                );
                assert!(dir.ends_with("pico"), "firmware dir should end with 'pico'");
            }
            Err(e) => {
                let msg = e.to_string();
                assert!(
                    msg.contains("placeholder") || msg.contains("UF2"),
                    "error should mention placeholder UF2; got: {msg}"
                );
            }
        }
    }

    #[tokio::test]
    async fn flash_uf2_rejects_invalid_magic() {
        let tmp = tempfile::tempdir().expect("create temp dir");
        let firmware_dir = tmp.path();

        // Write a fake UF2 with wrong magic
        std::fs::write(firmware_dir.join("revka-pico.uf2"), b"NOT_A_UF2_FILE").unwrap();

        let mount = tempfile::tempdir().expect("create mount dir");
        let result = flash_uf2(mount.path(), firmware_dir).await;
        assert!(result.is_err(), "flash_uf2 should reject invalid UF2 magic");
        let err = result.unwrap_err().to_string();
        assert!(
            err.contains("magic"),
            "error should mention magic mismatch; got: {err}"
        );
    }

    #[tokio::test]
    async fn flash_uf2_rejects_too_small_file() {
        let tmp = tempfile::tempdir().expect("create temp dir");
        let firmware_dir = tmp.path();

        // Write a tiny file (less than 8 bytes)
        std::fs::write(firmware_dir.join("revka-pico.uf2"), b"tiny").unwrap();

        let mount = tempfile::tempdir().expect("create mount dir");
        let result = flash_uf2(mount.path(), firmware_dir).await;
        assert!(result.is_err(), "flash_uf2 should reject too-small UF2");
    }

    #[tokio::test]
    async fn flash_uf2_rejects_placeholder_sized_uf2() {
        // Valid magic but a single 512-byte block (the placeholder shape) must
        // be rejected on size before any copy attempt (#438).
        let tmp = tempfile::tempdir().expect("create temp dir");
        let firmware_dir = tmp.path();
        let mut stub = UF2_MAGIC1.to_vec();
        stub.resize(512, 0);
        std::fs::write(firmware_dir.join("revka-pico.uf2"), &stub).unwrap();

        let mount = tempfile::tempdir().expect("create mount dir");
        let err = flash_uf2(mount.path(), firmware_dir)
            .await
            .unwrap_err()
            .to_string();
        assert!(err.contains("placeholder"), "got: {err}");
    }

    #[tokio::test]
    async fn deploy_main_py_rejects_placeholder() {
        // A comment-only main.py must fail loudly rather than deploying a no-op
        // handler to the Pico (#438).
        let tmp = tempfile::tempdir().expect("create temp dir");
        let firmware_dir = tmp.path();
        std::fs::write(
            firmware_dir.join("main.py"),
            b"# Placeholder: replace with real firmware\n",
        )
        .unwrap();

        let port = std::path::Path::new("/dev/ttyACM_fake_test");
        let err = deploy_main_py(port, firmware_dir)
            .await
            .unwrap_err()
            .to_string();
        assert!(err.contains("placeholder"), "got: {err}");
    }

    #[tokio::test]
    async fn deploy_main_py_fails_when_file_missing() {
        let tmp = tempfile::tempdir().expect("create temp dir");
        let firmware_dir = tmp.path();
        // Don't create main.py — deploy should fail

        let port = std::path::Path::new("/dev/ttyACM_fake_test");
        let result = deploy_main_py(port, firmware_dir).await;
        assert!(
            result.is_err(),
            "deploy should fail when main.py is missing"
        );
        let err = result.unwrap_err().to_string();
        assert!(
            err.contains("main.py not found"),
            "error should mention missing main.py; got: {err}"
        );
    }
}
