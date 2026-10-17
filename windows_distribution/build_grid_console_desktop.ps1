# Build actual existing Finnish Grid Console HTML UI wrapped in Tkinter.
# Unsigned developer-only, no live trading or account access.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'WINDOWS_ONLY' }
if (-not (Test-Path 'product_shell/assets/index.html')) { throw 'RUN_FROM_REPO_ROOT' }
$assetDir=(Resolve-Path 'product_shell/assets').Path
$configPath=(Resolve-Path 'configs/eth_eur_grid_shadow_v1.toml').Path
python -m PyInstaller --noconfirm --clean --onedir --windowed --name GridConsoleDesktop --distpath dist --workpath build --specpath build --paths . --add-data "$assetDir;product_shell/assets" --add-data "$configPath;configs" windows_distribution/grid_console_desktop.py
if ($LASTEXITCODE -ne 0) { throw 'GRID_DESKTOP_BUILD_FAILED' }
$exe = 'dist/GridConsoleDesktop/GridConsoleDesktop.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw 'GRID_DESKTOP_EXE_MISSING' }
$out = 'windows-artifacts/grid-desktop-build-smoke.json'
New-Item -ItemType Directory -Force -Path 'windows-artifacts' | Out-Null
$p = Start-Process -FilePath (Resolve-Path $exe).Path -ArgumentList @('--self-test','--report',(Join-Path (Get-Location) $out)) -Wait -PassThru
if ($p.ExitCode -ne 0) { throw 'GRID_DESKTOP_EXE_SELF_TEST_FAILED' }
$report = Get-Content -LiteralPath $out -Raw | ConvertFrom-Json
if ($report.status -ne 'DEV_DESKTOP_PASS' -or
    $report.ui -ne 'GRID_CONSOLE_LOCAL_GUI' -or
    $report.account_connection -ne 'NOT_CONNECTED' -or
    $report.live_orders -ne 'DISABLED' -or
    $report.exchange_mutations -ne 0 -or
    $report.sellable_product -ne 'NO_GO') {
  throw 'GRID_DESKTOP_BUILD_REPORT_REJECTED'
}
$iscc = Join-Path ([Environment]::GetFolderPath('ProgramFilesX86')) 'Inno Setup 6/ISCC.exe'
if (-not (Test-Path -LiteralPath $iscc)) { throw 'INNO_SETUP_6_NOT_INSTALLED' }
& $iscc '/Qp' 'windows_distribution/GridConsoleDesktop.iss'
if ($LASTEXITCODE -ne 0) { throw 'GRID_DESKTOP_INSTALLER_FAILED' }
if (-not (Test-Path -LiteralPath 'installer-dist/GridConsoleDesktop-UNSIGNED-DEV-Setup.exe')) {
  throw 'GRID_DESKTOP_INSTALLER_MISSING'
}
Write-Host 'PASS: local-only GUI built and smoke-tested; unsigned, NOT FOR CUSTOMERS.'
