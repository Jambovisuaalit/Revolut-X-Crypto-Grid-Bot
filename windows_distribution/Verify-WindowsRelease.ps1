# Strict, fail-closed Authenticode RELEASE gate for a single Windows .exe.
# No signing operation is performed; certificate and trust are verified only.
param(
  [Parameter(Mandatory=$true)][string]$FilePath,
  [Parameter(Mandatory=$true)][string]$ExpectedSha256,
  [Parameter(Mandatory=$true)][string]$ExpectedSignerThumbprint,
  [Parameter(Mandatory=$true)][string]$ReportPath
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$report = [ordered]@{
  schema = 'grid-console-authenticode-verification-v1'
  release_approval = 'NO_GO'
  status = 'FAIL'
  signature_status = 'NOT_VERIFIED'
  file_sha256_verified = $false
  signer_thumbprint_verified = $false
  trusted_chain_verified = $false
  timestamp_present = $false
  signtool_verified = $false
  real_account_test = 'NOT_RUN'
  live_trading = 'DISABLED'
  reason = 'VALIDATION_NOT_COMPLETED'
}
$exitCode = 2
try {
  if ($env:OS -ne 'Windows_NT') { throw 'WINDOWS_ONLY' }
  if ($ExpectedSha256 -notmatch '^[0-9a-fA-F]{64}$') { throw 'EXPECTED_SHA256_REQUIRED' }
  if ($ExpectedSignerThumbprint -notmatch '^[0-9a-fA-F]{40}$') { throw 'APPROVED_SIGNER_THUMBPRINT_REQUIRED' }
  $resolved = (Resolve-Path -LiteralPath $FilePath -ErrorAction Stop).ProviderPath
  if ([IO.Path]::GetExtension($resolved) -ne '.exe') { throw 'ONLY_EXECUTABLES_CAN_BE_RELEASE_VERIFIED' }
  $realHash = (Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash.ToUpperInvariant()
  if ($realHash -ne $ExpectedSha256.ToUpperInvariant()) { throw 'SHA256_MISMATCH' }
  $report.file_sha256_verified = $true
  $signature = Get-AuthenticodeSignature -LiteralPath $resolved
  $report.signature_status = [string]$signature.Status
  if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate) {
    throw 'WINDOWS_TRUST_OR_SIGNATURE_NOT_VALID'
  }
  # Catalog signatures alone are not sufficient for portable customer EXE.
  if ([string]$signature.SignatureType -ne 'Authenticode') {
    throw 'EMBEDDED_AUTHENTICODE_REQUIRED'
  }
  $report.trusted_chain_verified = $true
  if ($signature.SignerCertificate.Thumbprint.ToUpperInvariant() -ne $ExpectedSignerThumbprint.ToUpperInvariant()) {
    throw 'PUBLISHER_THUMBPRINT_MISMATCH'
  }
  $report.signer_thumbprint_verified = $true
  $ekus = @($signature.SignerCertificate.EnhancedKeyUsageList | ForEach-Object { $_.ObjectId.Value })
  if ('1.3.6.1.5.5.7.3.3' -notin $ekus) { throw 'CODE_SIGNING_EKU_MISSING' }
  if ($null -eq $signature.TimeStamperCertificate) { throw 'TRUSTED_TIMESTAMP_REQUIRED' }
  $report.timestamp_present = $true
  $signTool = Get-Command signtool.exe -ErrorAction SilentlyContinue
  if ($null -eq $signTool) { throw 'SIGNTOOL_NOT_INSTALLED' }
  & $signTool.Source verify /pa /all /v $resolved | Out-Null
  if ($LASTEXITCODE -ne 0) { throw 'SIGNTOOL_AUTHENTICODE_VERIFY_FAILED' }
  $report.signtool_verified = $true
  $report.status = 'PASS'
  # A verified file still needs separate product/real-account release gates.
  $report.release_approval = 'SIGNATURE_CHECK_ONLY'
  $report.reason = 'NONE'
  $exitCode = 0
} catch {
  # Never echo exception details that could contain paths or user identifiers.
  $message = [string]$_.Exception.Message
  $known = @(
    'WINDOWS_ONLY', 'EXPECTED_SHA256_REQUIRED',
    'APPROVED_SIGNER_THUMBPRINT_REQUIRED', 'ONLY_EXECUTABLES_CAN_BE_RELEASE_VERIFIED',
    'SHA256_MISMATCH', 'WINDOWS_TRUST_OR_SIGNATURE_NOT_VALID',
    'EMBEDDED_AUTHENTICODE_REQUIRED', 'PUBLISHER_THUMBPRINT_MISMATCH',
    'CODE_SIGNING_EKU_MISSING', 'TRUSTED_TIMESTAMP_REQUIRED',
    'SIGNTOOL_NOT_INSTALLED', 'SIGNTOOL_AUTHENTICODE_VERIFY_FAILED'
  )
  if ($message -in $known) { $report.reason = $message } else { $report.reason = 'VERIFICATION_ERROR' }
} finally {
  try {
    $parent = Split-Path -Parent $ReportPath
    if (-not [string]::IsNullOrWhiteSpace($parent)) {
      New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $report | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $ReportPath -Encoding utf8
  } catch {
    $exitCode = 2
  }
}
if ($exitCode -eq 0) { Write-Host 'PASS: Authenticode trusted signing verified; NO live authorization.' }
else { Write-Host ('FAIL: Authenticode release check: ' + $report.reason) }
exit $exitCode
