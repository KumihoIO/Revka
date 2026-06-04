# macOS Update and Uninstall Guide

This page documents supported update and uninstall procedures for Revka on macOS (OS X).

Last verified: **February 22, 2026**.

## 1) Check current install method

```bash
which revka
revka --version
```

Typical locations:

- Homebrew: `/opt/homebrew/bin/revka` (Apple Silicon) or `/usr/local/bin/revka` (Intel)
- Cargo/bootstrap/manual: `~/.cargo/bin/revka`

If both exist, your shell `PATH` order decides which one runs.

## 2) Update on macOS

### A) Homebrew install

```bash
brew update
brew upgrade revka
revka --version
```

### B) Clone + bootstrap install

From your local repository checkout:

```bash
git pull --ff-only
./install.sh --prefer-prebuilt
revka --version
```

If you want source-only update:

```bash
git pull --ff-only
cargo install --path . --force --locked
revka --version
```

### C) Manual prebuilt binary install

Re-run your download/install flow with the latest release asset, then verify:

```bash
revka --version
```

## 3) Uninstall on macOS

### A) Stop and remove background service first

This prevents the daemon from continuing to run after binary removal.

```bash
revka service stop || true
revka service uninstall || true
```

Service artifacts removed by `service uninstall`:

- `~/Library/LaunchAgents/com.revka.daemon.plist`

### B) Remove the binary by install method

Homebrew:

```bash
brew uninstall revka
```

Cargo/bootstrap/manual (`~/.cargo/bin/revka`):

```bash
cargo uninstall revka || true
rm -f ~/.cargo/bin/revka
```

### C) Optional: remove local runtime data

Only run this if you want a full cleanup of config, auth profiles, logs, and workspace state.

```bash
rm -rf ~/.revka
```

## 4) Verify uninstall completed

```bash
command -v revka || echo "revka binary not found"
pgrep -fl revka || echo "No running revka process"
```

If `pgrep` still finds a process, stop it manually and re-check:

```bash
pkill -f revka
```

## Related docs

- [One-Click Bootstrap](one-click-bootstrap.md)
- [Commands Reference](../reference/cli/commands-reference.md)
- [Troubleshooting](../ops/troubleshooting.md)
