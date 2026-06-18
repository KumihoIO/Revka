# Revka installer (Windows).
#
# One-liner:
#   irm https://revka.ai/install.ps1 | iex
#
# Downloads the latest prebuilt revka.exe, verifies its SHA-256 checksum
# against the release SHA256SUMS (fail-closed), installs it to
# %USERPROFILE%\.revka\bin, adds that directory to your User PATH, and then
# launches `revka onboard` for interactive provider + API-key setup.
#
# The Python MCP sidecars (Kumiho memory + Operator) are provisioned on first
# agent run, or explicitly with `revka install --sidecars-only`.
#
# To build from source instead (all features, dev workflow), clone the repo and
# run setup.ps1 / setup.bat.
#
# Environment overrides:
#   REVKA_VERSION       release tag to install (default: "latest")
#   REVKA_INSTALL_DIR   install location (default: %USERPROFILE%\.revka\bin)
#   REVKA_SKIP_ONBOARD  set to any value to skip the onboarding wizard
$ErrorActionPreference = "Stop"

# GitHub's API and release CDN require TLS 1.2; Windows PowerShell 5.1 does not
# always negotiate it by default. Newer PowerShell already does, so ignore if
# the enum value is unavailable.
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch {}

$Repo = "KumihoIO/Revka"
$Version = if ($env:REVKA_VERSION) { $env:REVKA_VERSION } else { "latest" }
$InstallDir = if ($env:REVKA_INSTALL_DIR) { $env:REVKA_INSTALL_DIR } else { Join-Path $env:USERPROFILE ".revka\bin" }

# Revka publishes a prebuilt Windows binary for x86_64 only. On ARM64 it runs
# under the built-in x64 emulation, so we install the same asset there.
$assetPattern = "^revka-x86_64-pc-windows-msvc\.zip$"

$headers = @{ "User-Agent" = "revka-installer" }
$uri = if ($Version -eq "latest") {
    "https://api.github.com/repos/$Repo/releases/latest"
} else {
    "https://api.github.com/repos/$Repo/releases/tags/$Version"
}
$release = Invoke-RestMethod -Headers $headers -Uri $uri

$asset = $release.assets | Where-Object { $_.name -match $assetPattern } | Select-Object -First 1
if (-not $asset) {
    throw "No Windows x86_64 release asset found in $Repo $($release.tag_name)"
}

$tmp = Join-Path $env:TEMP "revka-$($release.tag_name)"
if (Test-Path $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
New-Item -ItemType Directory -Path $tmp | Out-Null

$archive = Join-Path $tmp $asset.name
Write-Host "Downloading $($asset.name) ($($release.tag_name))..."
Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $archive

# Verify the download against SHA256SUMS (fail-closed).
$sumsAsset = $release.assets | Where-Object { $_.name -eq "SHA256SUMS" } | Select-Object -First 1
if (-not $sumsAsset) {
    throw "SHA256SUMS not found in release $($release.tag_name); refusing to install unverified binary"
}
$sumsPath = Join-Path $tmp "SHA256SUMS"
Invoke-WebRequest -Uri $sumsAsset.browser_download_url -OutFile $sumsPath
$expectedLine = Get-Content $sumsPath |
    Where-Object { $_ -match ("\s\*?" + [regex]::Escape($asset.name) + "$") } |
    Select-Object -First 1
if (-not $expectedLine) {
    throw "No checksum entry for $($asset.name) in SHA256SUMS; refusing to install unverified binary"
}
$expected = ($expectedLine -split "\s+")[0].ToLowerInvariant()
# Get-FileHash is PowerShell 4.0+; compute SHA-256 via the .NET API so the
# installer also works on Windows PowerShell 3.0 (Windows 8 / Server 2012).
$sha256 = [System.Security.Cryptography.SHA256]::Create()
$fileStream = [System.IO.File]::OpenRead($archive)
try {
    $actual = ([System.BitConverter]::ToString($sha256.ComputeHash($fileStream)) -replace "-", "").ToLowerInvariant()
} finally {
    $fileStream.Close()
    $sha256.Dispose()
}
if ($expected -ne $actual) {
    throw "SHA256 mismatch for $($asset.name): expected $expected, got $actual"
}

$extract = Join-Path $tmp "extract"
# Expand-Archive is PowerShell 5.0+; use the .NET ZipFile API (PS 3.0 + .NET 4.5).
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::ExtractToDirectory($archive, $extract)
$binary = Get-ChildItem -Path $extract -Recurse -Filter "revka.exe" | Select-Object -First 1
if (-not $binary) {
    throw "revka.exe not found in release archive"
}
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$dest = Join-Path $InstallDir "revka.exe"
Copy-Item -LiteralPath $binary.FullName -Destination $dest -Force
Remove-Item -LiteralPath $tmp -Recurse -Force

# Add the install directory to the *User* PATH (idempotent) and to this session,
# so `revka` works immediately and in new terminals.
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $userPath) { $userPath = "" }
if (($userPath -split ";") -notcontains $InstallDir) {
    $trimmed = $userPath.TrimEnd(";")
    $newPath = if ($trimmed) { "$trimmed;$InstallDir" } else { $InstallDir }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Write-Host "Added $InstallDir to your User PATH (open a new terminal to pick it up)."
}
if (($env:Path -split ";") -notcontains $InstallDir) {
    $env:Path = "$env:Path;$InstallDir"
}

Write-Host "Installed revka $($release.tag_name) to $dest"

# Hand off to the onboarding wizard (provider + API key). The Kumiho/Operator
# sidecars install on first agent run, or via `revka install --sidecars-only`.
if ($env:REVKA_SKIP_ONBOARD) {
    Write-Host "Skipping onboarding (REVKA_SKIP_ONBOARD set). Run 'revka onboard' when ready."
} else {
    Write-Host "Starting setup..."
    & $dest onboard
}
