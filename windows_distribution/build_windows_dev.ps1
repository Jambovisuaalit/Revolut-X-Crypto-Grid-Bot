# Build unsigned OFFLINE executable and installer; no certificate, no venue.
param([switch]$SkipInstaller)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'WINDOWS_ONLY' }
if (-not (Test-Path 'windows_distribution/offline_preview.py')) {
  throw 'RUN_FROM_REPO_ROOT'
}
python -m PyInstaller --noconfirm --clean --onedir --name GridConsoleOfflinePreview --distpath dist --workpath build --specpath build --paths . windows_distribution/offline_preview.py
if ($LASTEXITCODE -ne 0) { throw 'PYINSTALLER_FAILED' }
$exe = Join-Path (Get-Location) 'dist/GridConsoleOfflinePreview/GridConsoleOfflinePreview.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw 'PREVIEW_EXE_MISSING' }
$smoke = & $exe --self-test
if ($LASTEXITCODE -ne 0) { throw 'OFFLINE_EXE_SMOKE_FAILED' }
$report = ($smoke | Select-Object -Last 1 | ConvertFrom-Json)
if (($report.status -ne 'DEV_DEMO_PASS') -or
    ($report.live_trading -ne 'DISABLED') -or
    ($report.orders_sent -ne 0) -or
    ($report.real_account_test -ne 'NOT_RUN') -or
    ($report.account_connected -ne $false)) {
  throw 'PREVIEW_EXE_SAFETY_REJECTED'
}
Write-Host 'PASS: bounded OFFLINE executable. No account, orders or collector.'
if ($SkipInstaller) { return }
$iscc = Join-Path ([Environment]::GetFolderPath('ProgramFilesX86')) 'Inno Setup 6/ISCC.exe'
if (-not (Test-Path -LiteralPath $iscc)) {
  throw 'INNO_SETUP_6_NOT_INSTALLED'
}
& $iscc '/Qp' 'windows_distribution/WindowsOfflinePreview.iss'
if ($LASTEXITCODE -ne 0) { throw 'INNO_SETUP_BUILD_FAILED' }
$setup = 'installer-dist/GridConsoleOfflinePreview-UNSIGNED-DEV-Setup.exe'
if (-not (Test-Path $setup)) { throw 'INNO_SETUP_OUTPUT_MISSING' }
Write-Host 'UNSIGNED_DEV_ONLY: installer created. Not for customer installation.'
