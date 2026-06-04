#Requires -Version 5.1

<#
.SYNOPSIS
    Revka Windows Setup Script (PowerShell port of setup.bat).

.DESCRIPTION
    Builds and installs Revka on Windows. Native PowerShell port of
    setup.bat — picks up the same modes (Prebuilt/Minimal/Standard/Full)
    and ends in the same place: a working `revka` on User PATH.

    If the execution policy blocks running scripts, invoke as:
        powershell -ExecutionPolicy Bypass -File .\setup.ps1

.PARAMETER Mode
    Installation mode. Skip for interactive selection.
        Prebuilt   Download pre-compiled release zip (~2 min)
        Minimal    Build from source, default features (~15 min)
        Standard   Default + Lark/Feishu + Matrix (~20 min)
        Full       All features incl. hardware + browser (~30 min)

.EXAMPLE
    .\setup.ps1
    Interactive setup.

.EXAMPLE
    .\setup.ps1 -Mode Prebuilt
    Non-interactive: download the latest release binary.
#>

[CmdletBinding()]
param(
    [Parameter()]
    [ValidateSet('Prebuilt', 'Minimal', 'Standard', 'Full')]
    [string]$Mode
)

$ErrorActionPreference = 'Stop'

# ----- Constants ---------------------------------------------------------
# Derive version from Cargo.toml (single source of truth) so the banner
# never drifts from the workspace version we're actually setting up.
$CargoToml = Join-Path $PSScriptRoot 'Cargo.toml'
$VersionMatch = if (Test-Path $CargoToml) {
    Select-String -Path $CargoToml -Pattern '^version\s*=\s*"([^"]+)"' -ErrorAction SilentlyContinue | Select-Object -First 1
} else { $null }
$ScriptVersion = if ($VersionMatch) { $VersionMatch.Matches.Groups[1].Value } else { 'unknown' }
$RustMinVersion = '1.87'
$Target = 'x86_64-pc-windows-msvc'
$Repo = 'https://github.com/KumihoIO/Revka'
$InstallDir = Join-Path $env:USERPROFILE '.revka\bin'
$RepoRoot = $PSScriptRoot

# ----- Helpers -----------------------------------------------------------

function Write-Section([string]$Title) {
    Write-Host ''
    Write-Host -NoNewline -ForegroundColor Blue '========================================='
    Write-Host ''
    Write-Host -NoNewline -ForegroundColor Blue "  $Title"
    Write-Host ''
    Write-Host -NoNewline -ForegroundColor Blue '========================================='
    Write-Host ''
}

function Write-Step([string]$Text) {
    Write-Host ''
    Write-Host $Text -ForegroundColor White
}

function Write-Ok([string]$Text)    { Write-Host '  OK    ' -ForegroundColor Green -NoNewline; Write-Host $Text }
function Write-Warn([string]$Text)  { Write-Host '  WARN  ' -ForegroundColor Yellow -NoNewline; Write-Host $Text }
function Write-Err([string]$Text)   { Write-Host '  ERROR ' -ForegroundColor Red -NoNewline; Write-Host $Text }

function Test-Cmd([string]$Name) {
    $null = Get-Command $Name -ErrorAction SilentlyContinue
    return $?
}

# Idempotently append a directory to the *User* PATH. This writes through
# the same registry path Windows reads when spawning new terminals, so the
# entry survives logoff and is picked up by every new shell. Re-running
# this script does not add duplicates.
#
# The legacy setup.bat used a `powershell -Command "..."` round-trip for
# this very reason — `setx` would dupe the merged System+User PATH and
# silently truncate at 1024 chars. Native PowerShell has neither problem.
function Add-ToUserPath([string]$Dir) {
    $current = [Environment]::GetEnvironmentVariable('Path', 'User')
    $entries = if ($current) { $current -split ';' } else { @() }

    if ($entries -contains $Dir) {
        Write-Ok "$Dir already on User PATH"
    }
    else {
        $newPath = if ([string]::IsNullOrEmpty($current)) { $Dir }
                   else { $current.TrimEnd(';') + ';' + $Dir }
        [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
        Write-Ok "Added $Dir to User PATH"
    }

    # Update the current session too, so the verification step below can
    # resolve `revka` without making the user restart their shell.
    if (-not (($env:Path -split ';') -contains $Dir)) {
        $env:Path = "$env:Path;$Dir"
    }
}

function Install-Rust {
    Write-Step 'Installing Rust...'
    $rustupExe = Join-Path $env:TEMP 'rustup-init.exe'
    Write-Host '  Downloading rustup-init.exe...'
    try {
        Invoke-WebRequest -Uri 'https://win.rustup.rs' -OutFile $rustupExe -UseBasicParsing
    }
    catch {
        Write-Err 'Failed to download rustup-init.exe'
        Write-Host '  Install Rust manually from https://rustup.rs'
        throw
    }

    & $rustupExe -y --default-toolchain stable --target $Target
    if ($LASTEXITCODE -ne 0) {
        Write-Err 'Rust installation failed'
        throw 'rustup-init failed'
    }

    # Add cargo bin to current session PATH so the rest of the script
    # can call cargo without a shell restart.
    $cargoBin = Join-Path $env:USERPROFILE '.cargo\bin'
    if (-not (($env:Path -split ';') -contains $cargoBin)) {
        $env:Path = "$cargoBin;$env:Path"
    }
    Write-Ok 'Rust installed'
    Write-Warn 'You may need to restart your terminal for PATH changes to apply globally.'
}

function Install-Prebuilt {
    Write-Step '[3/5] Downloading prebuilt binary...'
    $downloadUrl = $null

    if (Test-Cmd 'gh') {
        # Pull JSON and filter in PowerShell rather than passing a jq
        # query through the native-exe quoting boundary (PowerShell does
        # not consistently escape embedded double-quotes for child procs).
        try {
            $release = & gh release view --repo $Repo --json assets 2>$null | ConvertFrom-Json
            $asset = $release.assets | Where-Object { $_.name -match 'windows-msvc' } | Select-Object -First 1
            if ($asset) { $downloadUrl = $asset.url }
        }
        catch { $downloadUrl = $null }
    }

    if (-not $downloadUrl) {
        $downloadUrl = "$Repo/releases/latest/download/revka-$Target.zip"
    }

    $zipPath = Join-Path $env:TEMP 'revka-windows.zip'
    try {
        Invoke-WebRequest -Uri $downloadUrl -OutFile $zipPath -UseBasicParsing
    }
    catch {
        Write-Warn "Prebuilt binary not available at $downloadUrl. Falling back to source build (Standard)."
        Build-FromSource -BuildMode 'Standard'
        return
    }

    Write-Host '  Extracting...'
    New-Item -Path $InstallDir -ItemType Directory -Force | Out-Null
    Expand-Archive -Path $zipPath -DestinationPath $InstallDir -Force

    Add-ToUserPath $InstallDir
    Write-Ok "Binary installed to $InstallDir\revka.exe"
}

function Build-FromSource {
    param(
        [Parameter(Mandatory)]
        [ValidateSet('Minimal', 'Standard', 'Full')]
        [string]$BuildMode
    )

    $featureArgs = @()
    $desc = ''
    switch ($BuildMode) {
        'Minimal'  { $desc = 'minimal (default features)' }
        'Standard' {
            $featureArgs = @('--features', 'channel-matrix,channel-lark')
            $desc = 'standard (Matrix + Lark/Feishu)'
        }
        'Full' {
            $featureArgs = @('--features', 'channel-matrix,channel-lark,browser-native,hardware,rag-pdf,observability-otel')
            $desc = 'full (all features)'
        }
    }

    Write-Step "[3/5] Building Revka ($desc)..."
    Write-Host "  Target: $Target"

    $cargoToml = Join-Path $RepoRoot 'Cargo.toml'
    if (-not (Test-Path $cargoToml)) {
        Write-Err "Cargo.toml not found at $cargoToml. Run this script from the revka repository root."
        Write-Host '  Example:'
        Write-Host "    git clone $Repo"
        Write-Host '    cd Revka'
        Write-Host '    .\setup.ps1'
        throw 'missing Cargo.toml'
    }

    & rustup target add $Target *>$null

    Write-Host '  This may take 15-30 minutes on first build...'
    Write-Host ''

    Push-Location $RepoRoot
    try {
        & cargo build --release --locked @featureArgs --target $Target
        if ($LASTEXITCODE -ne 0) {
            Write-Host ''
            Write-Err 'Build failed.'
            Write-Host '  Common fixes:'
            Write-Host '  - Ensure Visual Studio Build Tools are installed (C++ workload)'
            Write-Host '  - Run: rustup update'
            Write-Host '  - Check disk space (6 GB needed)'
            throw "cargo build returned $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }

    Write-Ok 'Build succeeded'

    Write-Step '[4/5] Installing binary...'
    New-Item -Path $InstallDir -ItemType Directory -Force | Out-Null
    Copy-Item -Path (Join-Path $RepoRoot "target\$Target\release\revka.exe") `
              -Destination (Join-Path $InstallDir 'revka.exe') -Force
    Write-Ok "Installed to $InstallDir\revka.exe"

    Add-ToUserPath $InstallDir
}

function Install-Sidecars {
    $sidecarRoot = Join-Path $RepoRoot 'operator-mcp'
    if (-not (Test-Path $sidecarRoot)) { return }

    if (-not (Test-Cmd 'python')) {
        Write-Host ''
        Write-Warn 'Python not found — skipping MCP sidecar install.'
        Write-Warn 'Install Python 3.11+ and run: scripts\install-sidecars.bat'
        return
    }

    $marker = Join-Path $env:USERPROFILE '.revka\kumiho\run_kumiho_mcp.py'
    if (Test-Path $marker) { return }

    Write-Step '[4.5/5] Installing Python MCP sidecars (Kumiho + Operator)...'
    $bat = Join-Path $RepoRoot 'scripts\install-sidecars.bat'
    if (Test-Path $bat) {
        & cmd /c "`"$bat`""
        if ($LASTEXITCODE -ne 0) {
            Write-Warn 'Sidecar install reported errors — see docs\setup-guides\kumiho-operator-setup.md'
        }
        else {
            Write-Ok 'Sidecars installed'
        }
    }
    else {
        Write-Warn "scripts\install-sidecars.bat not found at $bat — skipping."
    }
}

# ----- Main flow ---------------------------------------------------------

Write-Section "Revka Windows Setup  v$ScriptVersion"

# ----- [1/5] Prerequisites
Write-Step '[1/5] Checking prerequisites...'

# Free RAM (rough — uses Win32_OperatingSystem.FreePhysicalMemory in KB).
try {
    $freeRamMb = [int]((Get-CimInstance -ClassName Win32_OperatingSystem).FreePhysicalMemory / 1024)
    if ($freeRamMb -lt 2048) {
        Write-Warn "Only $freeRamMb MB free RAM detected. 2048 MB recommended for source builds."
        Write-Warn 'Consider using -Mode Prebuilt instead.'
    }
    else {
        Write-Ok "Free RAM: $freeRamMb MB"
    }
}
catch { }   # CIM unavailable — skip silently, RAM check is informational only.

if (Test-Cmd 'cargo') {
    $rustVer = (& rustc --version) -replace '^rustc\s+', '' -replace '\s.*$', ''
    Write-Ok "Rust $rustVer found"
}
else {
    Write-Warn 'Rust not found.'
    Install-Rust
}

if (Test-Cmd 'node') {
    $nodeVer = & node --version
    Write-Ok "Node.js $nodeVer found"
}
else {
    Write-Warn 'Node.js not found (optional - web dashboard will use stub).'
}

if (Test-Cmd 'git') {
    Write-Ok 'Git found'
}
else {
    Write-Err 'Git is required but not found.'
    Write-Host '  Install Git from https://git-scm.com/download/win'
    exit 1
}

# ----- [2/5] Mode selection
if (-not $Mode) {
    Write-Step '[2/5] Choose installation method:'
    Write-Host ''
    Write-Host '  1) Prebuilt binary   - Download pre-compiled release (fastest, ~2 min)'
    Write-Host '  2) Minimal build     - Default features only (~15 min)'
    Write-Host '  3) Standard build    - Default + Lark/Feishu + Matrix (~20 min)'
    Write-Host '  4) Full build        - All features including hardware + browser (~30 min)'
    Write-Host ''

    while (-not $Mode) {
        $choice = Read-Host '  Select [1-4] (default: 1)'
        if ([string]::IsNullOrWhiteSpace($choice)) { $choice = '1' }
        switch ($choice) {
            '1' { $Mode = 'Prebuilt' }
            '2' { $Mode = 'Minimal' }
            '3' { $Mode = 'Standard' }
            '4' { $Mode = 'Full' }
            default { Write-Err 'Invalid choice. Please enter 1-4.' }
        }
    }
}

# ----- [3-4/5] Install
if ($Mode -eq 'Prebuilt') {
    Install-Prebuilt
}
else {
    Build-FromSource -BuildMode $Mode
}

Install-Sidecars

# ----- [5/5] Verify
Write-Step '[5/5] Verifying installation...'

$exe = Join-Path $InstallDir 'revka.exe'
if (-not (Test-Path $exe)) {
    Write-Err "Binary not found at $exe"
    exit 1
}

# Step A: prove the binary itself runs (absolute path; independent of
# whatever the current session's $env:Path looks like).
$versionLine = & $exe --version 2>$null
if ($LASTEXITCODE -eq 0 -and $versionLine) {
    Write-Ok $versionLine
}
else {
    Write-Err 'Binary exists but failed to report --version'
    exit 1
}

# Step B: confirm User PATH (registry, what NEW terminals will inherit
# — not just this session's in-memory copy).
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (($userPath -split ';') -contains $InstallDir) {
    Write-Ok "On User PATH (close + reopen your terminal to use 'revka')"
}
else {
    Write-Warn 'Not on User PATH yet. Add manually:'
    Write-Host "    $InstallDir"
}

# ----- Done
Write-Host ''
Write-Host -NoNewline -ForegroundColor Green '========================================='; Write-Host ''
Write-Host -NoNewline -ForegroundColor Green '  Revka setup complete!'; Write-Host ''
Write-Host -NoNewline -ForegroundColor Green '========================================='; Write-Host ''
Write-Host ''
Write-Host '  Next steps:'
Write-Host '    1. Restart your terminal (for PATH changes)'
Write-Host '    2. Run: revka onboard           (guided provider + config setup)'
Write-Host '    3. Run: revka gateway           (starts the web dashboard at http://127.0.0.1:42617)'
Write-Host ''
Write-Host '  Useful commands:'
Write-Host '    revka status                    (health check)'
Write-Host '    revka agent -m "Hello"          (one-shot message)'
Write-Host '    revka doctor                    (diagnose issues)'
Write-Host ''
Write-Host '  Alternative install via Scoop:'
Write-Host '    scoop bucket add revka https://github.com/KumihoIO/scoop-revka'
Write-Host '    scoop install revka'
Write-Host ''
Write-Host '  Documentation: https://www.kumiho.io/docs'
Write-Host ''

exit 0
