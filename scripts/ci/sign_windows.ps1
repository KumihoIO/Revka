# Sign a Windows PE binary with a self-signed code-signing certificate.
#
# Required environment:
#   WIN_SIGNING_PFX_BASE64  — base64-encoded PFX bundle containing the cert + private key
#   WIN_SIGNING_PFX_PASSWORD — password protecting the PFX
#
# Arguments:
#   -BinaryPath   Path to the .exe to sign
#   -Description  Human-readable description embedded in the signature
#
# If the signing material is absent, the script is a no-op and exits 0 so
# forks without access to the secret still produce unsigned (but runnable)
# binaries. Presence of a signature is enforced by a separate verify step.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BinaryPath,
    [string]$Description = "Revka — memory-native AI agent runtime"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $BinaryPath)) {
    Write-Error "Binary not found at $BinaryPath"
    exit 1
}

$pfxBase64 = $env:WIN_SIGNING_PFX_BASE64
$pfxPassword = $env:WIN_SIGNING_PFX_PASSWORD

if ([string]::IsNullOrWhiteSpace($pfxBase64) -or [string]::IsNullOrWhiteSpace($pfxPassword)) {
    Write-Host "WIN_SIGNING_PFX_BASE64 / WIN_SIGNING_PFX_PASSWORD not set — skipping Windows signing."
    Write-Host "Fork builds and PRs from forks run without signing (expected)."
    exit 0
}

# Resolve signtool.exe — it lives under Windows Kits.
$signtool = Get-ChildItem `
    -Path 'C:\Program Files (x86)\Windows Kits\10\bin' `
    -Recurse -Filter 'signtool.exe' -ErrorAction SilentlyContinue `
    | Where-Object { $_.FullName -like '*\x64\signtool.exe' } `
    | Sort-Object -Property FullName -Descending `
    | Select-Object -First 1

if (-not $signtool) {
    Write-Error "signtool.exe not found in Windows 10 SDK path."
    exit 1
}
Write-Host "Using signtool: $($signtool.FullName)"

$pfxPath = Join-Path $env:RUNNER_TEMP "revka-codesign.pfx"
try {
    [System.IO.File]::WriteAllBytes($pfxPath, [Convert]::FromBase64String($pfxBase64))

    # Sign with SHA256 + RFC3161 timestamp to preserve validity after cert expiry.
    & $signtool.FullName sign `
        /f $pfxPath `
        /p $pfxPassword `
        /fd SHA256 `
        /td SHA256 `
        /tr "http://timestamp.digicert.com" `
        /d "$Description" `
        $BinaryPath
    if ($LASTEXITCODE -ne 0) {
        Write-Error "signtool sign failed with exit code $LASTEXITCODE"
        exit $LASTEXITCODE
    }

    # Verify the signature we just applied.
    & $signtool.FullName verify /pa /v $BinaryPath
    if ($LASTEXITCODE -ne 0) {
        Write-Error "signtool verify failed — binary does not have a valid signature"
        exit $LASTEXITCODE
    }
    Write-Host "Signed and verified: $BinaryPath"
}
finally {
    if (Test-Path $pfxPath) {
        Remove-Item $pfxPath -Force
    }
}
