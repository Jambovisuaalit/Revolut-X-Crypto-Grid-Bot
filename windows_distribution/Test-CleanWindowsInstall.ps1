# Fresh Windows runner validation of the exact CI-built unsigned development installer.
# Never connects to an exchange or enables trading.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$reportDir = Join-Path (Get-Location) 'clean-install-evidence'
New-Item -ItemType Directory -Path $reportDir -Force | Out-Null
$target = Join-Path $env:RUNNER_TEMP 'grid-console-clean-install'
$fixture = Join-Path $env:RUNNER_TEMP 'grid-console-clean-risk.sqlite'
$reportFile = Join-Path $reportDir 'clean-windows-install-acceptance.json'
$checks = [ordered]@{
  clean_environment = 'PENDING'
  setup_verified = 'PENDING'
  installation = 'PENDING'
  react_localhost = 'PENDING'
  offline_risk_lock = 'PENDING'
  read_only_sqlite = 'PENDING'
  safe_shutdown = 'PENDING'
  uninstallation = 'PENDING'
}
$status = 'FAIL'
$reason = 'NOT_COMPLETED'
$gui = $null
$installAttempted = $false
$installerSha = $null
try {
  if ($env:LIVE_TRADING -ne 'false' -or $env:ACCOUNT_NETWORK_TESTS -ne 'DISABLED') {
    throw 'UNSAFE_CI_ENVIRONMENT'
  }
  if (Test-Path -LiteralPath $target) { throw 'INSTALL_DIRECTORY_ALREADY_EXISTS' }
  if (Get-Process -Name 'GridConsoleDesktop' -ErrorAction SilentlyContinue) { throw 'EXISTING_GRID_CONSOLE_PROCESS' }
  $checks.clean_environment = 'PASS'
  $setup = Get-ChildItem -LiteralPath 'downloaded-package' -Recurse -File -Filter 'GridConsoleDesktop-UNSIGNED-DEV-Setup.exe' | Select-Object -First 1
  if ($null -eq $setup) { throw 'EXACT_CI_INSTALLER_NOT_FOUND' }
  $installerSha = (Get-FileHash -LiteralPath $setup.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
  $signature = Get-AuthenticodeSignature -FilePath $setup.FullName
  if ($signature.Status -eq 'Valid') { throw 'UNEXPECTED_SIGNED_INSTALLER' }
  $checks.setup_verified = 'PASS'
  $installAttempted = $true
  $p = Start-Process -FilePath $setup.FullName -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',('/DIR='+$target)) -Wait -PassThru
  if ($p.ExitCode -ne 0) { throw 'CLEAN_INSTALL_FAILED' }
  $exe = Join-Path $target 'GridConsoleDesktop.exe'
  $uninstaller = Join-Path $target 'unins000.exe'
  if (-not (Test-Path -LiteralPath $exe) -or -not (Test-Path -LiteralPath $uninstaller)) {
    throw 'INSTALLED_FILES_MISSING'
  }
  $checks.installation = 'PASS'
  $selfReport = Join-Path $reportDir 'installed-self-test.json'
  $probe = Start-Process -FilePath $exe -ArgumentList @('--self-test','--report',$selfReport) -Wait -PassThru
  if ($probe.ExitCode -ne 0) { throw 'INSTALLED_SELF_TEST_FAILED' }
  $self = Get-Content -LiteralPath $selfReport -Raw | ConvertFrom-Json
  if ($self.status -ne 'DEV_DESKTOP_PASS' -or $self.ui -ne 'GRID_CONSOLE_REACT_LOCAL_GUI' -or
      $self.http_bind -ne '127.0.0.1' -or $self.react_assets -lt 2 -or
      $self.account_connection -ne 'NOT_CONNECTED' -or $self.live_orders -ne 'DISABLED' -or
      $self.exchange_mutations -ne 0 -or $self.sellable_product -ne 'NO_GO') {
    throw 'UNSAFE_INSTALLED_SELF_TEST'
  }
  $gui = Start-Process -FilePath $exe -ArgumentList @('--no-auto-browser') -PassThru
  $handle = [IntPtr]::Zero
  $port = $null
  for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    $gui.Refresh()
    if ($gui.HasExited) { throw 'GUI_EXITED_EARLY' }
    $handle = $gui.MainWindowHandle
    $tcp = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
      Where-Object { $_.OwningProcess -eq $gui.Id -and $_.LocalAddress -eq '127.0.0.1' } |
      Select-Object -First 1
    if ($handle -ne [IntPtr]::Zero -and $null -ne $tcp) { $port = $tcp.LocalPort; break }
  }
  if ($null -eq $port -or $handle -eq [IntPtr]::Zero) { throw 'WINDOW_OR_LOOPBACK_NOT_STARTED' }
  $base = 'http://127.0.0.1:'+$port
  $index = Invoke-WebRequest -Uri ($base+'/') -UseBasicParsing -TimeoutSec 8
  $runtime = Invoke-RestMethod -Uri ($base+'/api/runtime') -TimeoutSec 8
  if ($index.StatusCode -ne 200 -or $index.Content -notmatch 'id="root"' -or
      $runtime.live_orders -ne 'DISABLED' -or $runtime.account_connection -ne 'NOT_CONNECTED') {
    throw 'REACT_OR_LOCAL_RUNTIME_UNSAFE'
  }
  $assets = [regex]::Matches($index.Content, '(?:src|href)="\./assets/([^"]+)"')
  if ($assets.Count -lt 2) { throw 'COMPILED_REACT_ASSETS_MISSING' }
  foreach ($asset in $assets) {
    $assetResponse = Invoke-WebRequest -Uri ($base+'/assets/'+$asset.Groups[1].Value) -UseBasicParsing -TimeoutSec 8
    if ($assetResponse.StatusCode -ne 200) { throw 'REACT_ASSET_FAILED' }
  }
  foreach ($method in @('POST','PUT','PATCH','DELETE')) {
    $response = Invoke-WebRequest -Uri ($base+'/api/runtime') -Method $method -SkipHttpErrorCheck -TimeoutSec 8
    if ($response.StatusCode -ne 405) { throw 'MUTATION_ROUTE_ACCEPTED' }
  }
  $checks.react_localhost = 'PASS'
  python scripts/create_grid_console_offline_fixture.py --output $fixture
  if ($LASTEXITCODE -ne 0) { throw 'RISK_FIXTURE_FAILED' }
  $before = (Get-FileHash -LiteralPath $fixture -Algorithm SHA256).Hash
  $riskReport = Join-Path $reportDir 'installed-risk-test.json'
  $riskProbe = Start-Process -FilePath $exe -ArgumentList @('--self-test','--supervisor-db',$fixture,'--report',$riskReport) -Wait -PassThru
  if ($riskProbe.ExitCode -ne 0) { throw 'RISK_PROBE_FAILED' }
  $risk = Get-Content -LiteralPath $riskReport -Raw | ConvertFrom-Json
  if ($risk.runtime_status -ne 'RISK_LOCKED' -or $risk.risk_lock -ne 'TRIGGERED' -or
      $risk.uncertain_order_count -ne 1 -or $risk.exchange_mutations -ne 0) {
    throw 'RISK_LOCK_NOT_ENFORCED'
  }
  $checks.offline_risk_lock = 'PASS'
  $after = (Get-FileHash -LiteralPath $fixture -Algorithm SHA256).Hash
  if ($before -ne $after) { throw 'SQLITE_MODIFIED' }
  $checks.read_only_sqlite = 'PASS'
  Add-Type -Namespace CleanInstallNative -Name User32 -MemberDefinition '[DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);'
  if (-not [CleanInstallNative.User32]::PostMessage($handle, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)) {
    throw 'GUI_CLOSE_FAILED'
  }
  if (-not $gui.WaitForExit(12000) -or $gui.ExitCode -ne 0) { throw 'GUI_NOT_CLOSED_CLEANLY' }
  $stillListening = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.OwningProcess -eq $gui.Id }
  if ($stillListening) { throw 'LOOPBACK_SERVER_LEFT_RUNNING' }
  $checks.safe_shutdown = 'PASS'
  $gui = $null
  $un = Start-Process -FilePath $uninstaller -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART') -Wait -PassThru
  if ($un.ExitCode -ne 0) { throw 'UNINSTALL_FAILED' }
  for ($i=0; $i -lt 20 -and (Test-Path -LiteralPath $target); $i++) { Start-Sleep -Milliseconds 500 }
  if (Test-Path -LiteralPath $exe) { throw 'INSTALLED_BINARY_REMAINED' }
  if (Get-Process -Name 'GridConsoleDesktop' -ErrorAction SilentlyContinue) { throw 'PROCESS_REMAINED_AFTER_UNINSTALL' }
  $checks.uninstallation = 'PASS'
  $status = 'PASS'
  $reason = $null
} catch {
  $reason = $_.Exception.Message
  Write-Error -Message ('CLEAN_INSTALL_GATE_FAILED: '+$reason) -ErrorAction Continue
} finally {
  if ($null -ne $gui) {
    try { if (-not $gui.HasExited) { Stop-Process -Id $gui.Id -Force } } catch {}
  }
  if (Test-Path -LiteralPath $fixture) { Remove-Item -LiteralPath $fixture -Force }
  if ($installAttempted) {
    $un = Join-Path $target 'unins000.exe'
    if (Test-Path -LiteralPath $un) {
      try { Start-Process -FilePath $un -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART') -Wait | Out-Null } catch {}
    }
  }
  [ordered]@{
    schema = 'grid-console-clean-windows-acceptance-v1'
    source_run_id = '39098477277'
    source_feature_commit = '6475dc94418ce6e5ba5803828d4b0f776a33ecd1'
    platform = 'windows-latest'
    status = $status
    failure_reason = $reason
    installer_sha256 = $installerSha
    checks = $checks
    live_trading = 'DISABLED'
    authenticated_account_connection = 'NOT_CONNECTED'
    release_approval = 'NO_GO'
    signed_installer = $false
  } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $reportFile -Encoding utf8
}
if ($status -ne 'PASS') { exit 1 }
