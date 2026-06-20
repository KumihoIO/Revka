# Windows Setup Guide

This guide covers building and installing Revka on Windows.

## Quick Start

### Option A: One-click setup script

From the repository root:

```cmd
setup.bat
```

The script auto-detects your environment and walks you through installation.
You can also pass flags to skip the interactive menu:

| Flag | Description |
|------|-------------|
| `--prebuilt` | Download pre-compiled binary (fastest) |
| `--minimal` | Build with default features only |
| `--standard` | Build with Matrix + Lark/Feishu + Postgres |
| `--full` | Build with all features |

### Option B: Scoop (package manager)

```powershell
scoop bucket add revka https://github.com/KumihoIO/scoop-revka
scoop install revka
```

### Option C: Manual build

```cmd
rustup target add x86_64-pc-windows-msvc
cargo build --release --locked --features channel-matrix,channel-lark --target x86_64-pc-windows-msvc
copy target\x86_64-pc-windows-msvc\release\revka.exe %USERPROFILE%\.revka\bin\
```

## Prerequisites

| Requirement | Required? | Notes |
|-------------|-----------|-------|
| Git | Yes | [git-scm.com/download/win](https://git-scm.com/download/win) |
| Rust 1.89+ | Yes | Auto-installed by `setup.bat` if missing |
| Visual Studio Build Tools | Yes (source builds) | C++ workload required for MSVC linker |
| Node.js | No | Only needed to build the web dashboard from source |

<!-- TODO screenshot: Visual Studio Build Tools installer dialog with C++ build tools workload selected -->
![Visual Studio Build Tools installer dialog with C++ build tools workload selected](../assets/setup/windows-setup-01-vs-build-tools-installer.png)

### Installing Visual Studio Build Tools

If you don't have Visual Studio installed, install the Build Tools:

1. Download from [visualstudio.microsoft.com/visual-cpp-build-tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)
2. Select the **"Desktop development with C++"** workload
3. Install and restart your terminal

Alternatively, if you have Visual Studio 2019+ installed with the C++ workload, you're already set.

## Feature Flags

Revka uses Cargo feature flags to control which integrations are compiled in:

| Feature | Description | Default? |
|---------|-------------|----------|
| `channel-lark` | Lark/Feishu messaging | Yes |
| `channel-nostr` | Nostr protocol | Yes |
| `observability-prometheus` | Prometheus metrics | Yes |
| `skill-creation` | Auto skill creation | Yes |
| `channel-matrix` | Matrix protocol | No |
| `browser-native` | Headless browser | No |
| `hardware` | USB device support | No |
| `rag-pdf` | PDF extraction for RAG | No |
| `observability-otel` | OpenTelemetry | No |

To build with specific features:

```cmd
cargo build --release --locked --features channel-matrix,channel-lark --target x86_64-pc-windows-msvc
```

<!-- TODO screenshot: PowerShell terminal showing successful `revka onboard` completion on Windows -->
![PowerShell terminal showing successful revka onboard completion on Windows](../assets/setup/windows-setup-02-revka-onboard-terminal.png)

<!-- TODO screenshot: Edge browser displaying the Revka dashboard at http://127.0.0.1:42617 after onboarding -->
![Edge browser displaying the Revka dashboard at 127.0.0.1:42617 after onboarding](../assets/setup/windows-setup-03-dashboard-browser.png)

## Post-Installation

1. **Restart your terminal** for PATH changes to take effect
2. **Initialize Revka:**
   ```cmd
   revka onboard
   ```
3. **Configure your API key** in `%USERPROFILE%\.revka\config.toml`
4. **Start the gateway + dashboard** at `http://127.0.0.1:42617`:
   ```cmd
   revka gateway
   ```
   Or run the full runtime (gateway + channels + heartbeat + cron scheduler):
   ```cmd
   revka daemon
   ```

## Troubleshooting

### Build fails with linker errors

Install Visual Studio Build Tools with the C++ workload. The MSVC linker is required.

### `cargo build` runs out of memory

Source builds need at least 2 GB free RAM. Use `setup.bat --prebuilt` to download a pre-compiled binary instead.

### Feishu/Lark not available

Feishu and Lark are the same platform. Build with the `channel-lark` feature:

```cmd
cargo build --release --locked --features channel-lark --target x86_64-pc-windows-msvc
```

### Web dashboard missing

The web dashboard requires Node.js and npm at build time. Install Node.js and rebuild, or use the pre-built binary which includes the dashboard.
