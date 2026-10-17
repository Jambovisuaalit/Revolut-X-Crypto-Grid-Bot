# Developer artifact gate: reject any accidental signed/release branding.
# This script does not approve a customer installation.
param(
  [Parameter(Mandatory=$true)][string]$PreviewExe,
  [Parameter(Mandatory=$true)][string]$InstallerExe,
  [Parameter(Mandatory=$true)][string]$CommitSha,
  [Parameter(Mandatory=$true)][string]$Output
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'WINDOWS_ONLY' }
if ($CommitSha -notmatch '^[0-9a-f]{40}$') { throw 'INVALID_COMMIT_SHA' }
$entries = [ordered]@{}
foreach ($entry in @(@('preview',$PreviewExe),@('installer',$InstallerExe))) {
  $name = [string]$entry[0]
  $path = [string]$entry[1]
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'DEV_ARTIFACT_MISSING' }
  $signed = Get-AuthenticodeSignature -LiteralPath $path
  # The CI artifacts must remain unsigned and never be mistaken for a
  # signed release even if a machine later acquires a signing key.
  if ($signed.Status -ne 'NotSigned') { throw 'DEV_ARTIFACT_SIGNATURE_UNEXPECTED' }
  $entries[$name] = [ordered]@{
    name = [IO.Path]::GetFileName($path)
    sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToUpperInvariant()
    authenticode = 'NotSigned'
  }
}
$manifest = [ordered]@{
  schema = 'grid-console-windows-dev-manifest-v1'
  git_commit_sha = $CommitSha
  status = 'PASS_UNSIGNED_DEV_ONLY'
  windows_customer_release = 'NO_GO'
  signed_installer = $false
  signed_executable = $false
  signed_release_verification = 'NOT_RUN'
  authenticated_account_calls = 'NOT_RUN'
  actual_exchange_mutations = 0
  live_trading = 'DISABLED'
  artifacts = $entries
}
$parent = Split-Path -Parent $Output
if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $Output -Encoding utf8
Write-Host 'PASS: Both Windows artifacts are unsigned, development-only.'
