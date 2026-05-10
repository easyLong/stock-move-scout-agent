$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$pidFiles = @()
$pidFiles += Join-Path $root "runs\stock_scout_scheduler.pid"
$pidFiles += Get-ChildItem -Path (Join-Path $root "runs") -Filter "stock_scout_worker_*.pid" -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName }

foreach ($pidPath in $pidFiles) {
    if (-not (Test-Path $pidPath)) {
        continue
    }
    $pidValue = Get-Content $pidPath -ErrorAction SilentlyContinue
    if ($pidValue -and (Get-Process -Id $pidValue -ErrorAction SilentlyContinue)) {
        Stop-Process -Id $pidValue -Force
        Write-Output "Stopped PID=$pidValue ($pidPath)"
    }
    Remove-Item $pidPath -Force -ErrorAction SilentlyContinue
}

Write-Output "stock scout scheduler/workers stopped."
