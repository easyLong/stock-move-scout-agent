$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$pidPath = Join-Path $root "runs\windowed_stock_scout_agent.pid"
$workerPidPath = Join-Path $root "runs\stock_scout_evidence_worker.pid"

foreach ($path in @($pidPath, $workerPidPath)) {
    if (-not (Test-Path $path)) {
        continue
    }

    $pidValue = Get-Content $path -ErrorAction SilentlyContinue
    if ($pidValue -and (Get-Process -Id $pidValue -ErrorAction SilentlyContinue)) {
        Stop-Process -Id $pidValue -Force
        Write-Output "Stopped process. PID=$pidValue"
    } else {
        Write-Output "pid file existed, but process was not running: $path"
    }

    Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
}

Write-Output "windowed_stock_scout_agent stop check complete."
