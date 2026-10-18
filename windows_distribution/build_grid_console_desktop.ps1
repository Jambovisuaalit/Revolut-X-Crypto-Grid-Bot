# Build compiled React Grid Console + existing Python localhost/Tkinter shell.
# Unsigned developer-only. No live trading, external account access or payment.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'WINDOWS_ONLY' }
if (-not (Test-Path 'frontend/package.json') -or
    -not (Test-Path 'product_shell/server.py')) { throw 'RUN_FROM_REPO_ROOT' }

# Build frontend explicitly; NEVER silently fall back to old static UI.
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
  throw 'NODEJS_NPM_REQUIRED_FOR_REACT_BUILD'
}
Push-Location 'frontend'
try {
  & npm install --no-audit --no-fund
  if ($LASTEXITCODE -ne 0) { throw 'REACT_NPM_INSTALL_FAILED' }
  & npm run build
  if ($LASTEXITCODE -ne 0) { throw 'REACT_BUILD_FAILED' }
} finally {
  Pop-Location
}
if (-not (Test-Path 'frontend/dist/index.html')) { throw 'REACT_DIST_MISSING' }
$reactDist=(Resolve-Path 'frontend/dist').Path
$assetDir=(Resolve-Path 'product_shell/assets').Path
$configPath=(Resolve-Path 'configs/eth_eur_grid_shadow_v1.toml').Path
python -m PyInstaller --noconfirm --clean --onedir --windowed --name GridConsoleDesktop --distpath dist --workpath build --specpath build --paths . --add-data "$assetDir;product_shell/assets" --add-data "$reactDist;product_shell/react_dist" --add-data "$configPath;configs" windows_distribution/grid_console_desktop.py
if ($LASTEXITCODE -ne 0) { throw 'GRID_DESKTOP_BUILD_FAILED' }
$exe = 'dist/GridConsoleDesktop/GridConsoleDesktop.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw 'GRID_DESKTOP_EXE_MISSING' }
New-Item -ItemType Directory -Force -Path 'windows-artifacts' | Out-Null
$out = 'windows-artifacts/grid-desktop-build-smoke.json'
$p = Start-Process -FilePath (Resolve-Path $exe).Path -ArgumentList @('--self-test','--report',(Join-Path (Get-Location) $out)) -Wait -PassThru
if ($p.ExitCode -ne 0) { throw 'GRID_DESKTOP_EXE_SELF_TEST_FAILED' }
$report = Get-Content -LiteralPath $out -Raw | ConvertFrom-Json
if ($report.status -ne 'DEV_DESKTOP_PASS' -or
    $report.ui -ne 'GRID_CONSOLE_REACT_LOCAL_GUI' -or
    $report.react_assets -lt 2 -or
    $report.runtime_status -ne 'NOT_CONFIGURED' -or
    $report.account_connection -ne 'NOT_CONNECTED' -or
    $report.live_orders -ne 'DISABLED' -or
    $report.exchange_mutations -ne 0 -or
    $report.sellable_product -ne 'NO_GO') {
  throw 'GRID_DESKTOP_BUILD_REPORT_REJECTED'
}
# Test an actual packaged EXE reading an offline synthetic SQLite fixture.
# Do not include fixture data in artifacts or commit it to the repository.
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('grid-console-offline-'+[guid]::NewGuid().ToString('N')+'.sqlite')
try {
  python scripts/create_grid_console_offline_fixture.py --output $fixture
  if ($LASTEXITCODE -ne 0) { throw 'SYNTHETIC_FIXTURE_CREATION_FAILED' }
  $before=(Get-FileHash -LiteralPath $fixture -Algorithm SHA256).Hash
  $outDb='windows-artifacts/grid-desktop-local-risk-smoke.json'
  $probe=Start-Process -FilePath (Resolve-Path $exe).Path -ArgumentList @('--self-test','--supervisor-db',$fixture,'--report',(Join-Path (Get-Location) $outDb)) -Wait -PassThru
  if ($probe.ExitCode -ne 0) { throw 'PACKAGED_SQLITE_RUNTIME_SELF_TEST_FAILED' }
  $raw=Get-Content -LiteralPath $outDb -Raw
  $risk=$raw | ConvertFrom-Json
  if ($risk.runtime_status -ne 'RISK_LOCKED' -or
      $risk.risk_lock -ne 'TRIGGERED' -or
      $risk.uncertain_order_count -ne 1 -or
      $risk.local_event_count -ne 2 -or
      $raw.Contains('SECRET-')) { throw 'PACKAGED_SQLITE_RISK_GATE_FAILED' }
  $after=(Get-FileHash -LiteralPath $fixture -Algorithm SHA256).Hash
  if ($before -ne $after) { throw 'SQLITE_READ_ONLY_VIOLATION' }
} finally {
  if (Test-Path -LiteralPath $fixture) { Remove-Item -LiteralPath $fixture -Force }
}
$iscc = Join-Path ([Environment]::GetFolderPath('ProgramFilesX86')) 'Inno Setup 6/ISCC.exe'
if (-not (Test-Path -LiteralPath $iscc)) { throw 'INNO_SETUP_6_NOT_INSTALLED' }
& $iscc '/Qp' 'windows_distribution/GridConsoleDesktop.iss'
if ($LASTEXITCODE -ne 0) { throw 'GRID_DESKTOP_INSTALLER_FAILED' }
if (-not (Test-Path -LiteralPath 'installer-dist/GridConsoleDesktop-UNSIGNED-DEV-Setup.exe')) {
  throw 'GRID_DESKTOP_INSTALLER_MISSING'
}
Write-Host 'PASS: React Grid Console and local SQLite risk gate verified; UNSIGNED DEV ONLY.'
