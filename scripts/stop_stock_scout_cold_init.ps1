$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$pidPath = Join-Path $root "runs\stock_scout_cold_init.pid"

if (-not (Test-Path $pidPath)) {
    Write-Output "stock_scout_cold_init is not running: no pid file"
    exit 0
}

$pidValue = Get-Content $pidPath -ErrorAction SilentlyContinue
if ($pidValue -and (Get-Process -Id $pidValue -ErrorAction SilentlyContinue)) {
    Stop-Process -Id $pidValue -Force
    Write-Output "Stopped stock_scout_cold_init. PID=$pidValue"
} else {
    Write-Output "stock_scout_cold_init process not found. PID=$pidValue"
}

Remove-Item $pidPath -Force -ErrorAction SilentlyContinue
