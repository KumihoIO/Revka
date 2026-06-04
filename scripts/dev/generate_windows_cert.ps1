# Generate a self-signed code-signing certificate for Revka's Windows binaries.
#
# Run this ONCE on a Windows machine. The script prints two values:
#   WIN_SIGNING_PFX_BASE64
#   WIN_SIGNING_PFX_PASSWORD
# Add both to the GitHub repository secrets. The release workflows will then
# sign revka.exe with this certificate on every Windows build.
#
# Certificate validity: 5 years. Re-run this script before expiry.
# Self-signed certs trigger "Unknown Publisher" on first run — this is expected
# until we obtain an EV code-signing certificate (future upgrade).

[CmdletBinding()]
param(
    [string]$Subject = "CN=Revka by Kumiho, O=Kumiho Clouds, C=US",
    [int]$ValidityYears = 5
)

$ErrorActionPreference = "Stop"

if ($PSVersionTable.Platform -and $PSVersionTable.Platform -ne "Win32NT") {
    Write-Error "This script requires Windows PowerShell — New-SelfSignedCertificate is Windows-only."
    exit 1
}

Write-Host "Generating self-signed code-signing certificate..."
$cert = New-SelfSignedCertificate `
    -Type CodeSigningCert `
    -Subject $Subject `
    -KeyUsage DigitalSignature `
    -KeyAlgorithm RSA `
    -KeyLength 4096 `
    -HashAlgorithm SHA256 `
    -NotAfter (Get-Date).AddYears($ValidityYears) `
    -CertStoreLocation "Cert:\CurrentUser\My"

Write-Host "Thumbprint: $($cert.Thumbprint)"
Write-Host "Subject:    $($cert.Subject)"
Write-Host "Expires:    $($cert.NotAfter)"

# Generate a cryptographically-random password for the PFX.
$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$plainPassword = [Convert]::ToBase64String($bytes).Substring(0, 32)
$securePassword = ConvertTo-SecureString -String $plainPassword -Force -AsPlainText

$pfxPath = Join-Path $env:TEMP "revka-codesign-$($cert.Thumbprint).pfx"
try {
    Export-PfxCertificate -Cert $cert -FilePath $pfxPath -Password $securePassword | Out-Null
    $pfxBase64 = [Convert]::ToBase64String([System.IO.File]::ReadAllBytes($pfxPath))

    Write-Host ""
    Write-Host "=========================================================="
    Write-Host "Add these two values to GitHub repository secrets:"
    Write-Host "  https://github.com/KumihoIO/Revka/settings/secrets/actions"
    Write-Host "=========================================================="
    Write-Host ""
    Write-Host "Secret name:  WIN_SIGNING_PFX_PASSWORD"
    Write-Host "Secret value: $plainPassword"
    Write-Host ""
    Write-Host "Secret name:  WIN_SIGNING_PFX_BASE64"
    Write-Host "Secret value (copy the single line below):"
    Write-Host $pfxBase64
    Write-Host ""
    Write-Host "=========================================================="
    Write-Host "Keep these values SECRET. Anyone with them can sign as Revka."
    Write-Host "Expires: $($cert.NotAfter) — regenerate before this date."
    Write-Host "=========================================================="
}
finally {
    if (Test-Path $pfxPath) {
        Remove-Item $pfxPath -Force
    }
    Remove-Item "Cert:\CurrentUser\My\$($cert.Thumbprint)" -Force
    Write-Host "Cleaned up local cert store and PFX file."
}
