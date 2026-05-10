$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$pidPath = Join-Path $root "runs\tdx_mover_watcher.pid"

if (-not (Test-Path $pidPath)) {
    Write-Output "tdx_mover_watcher is not running."
    exit 0
}

$pidValue = Get-Content $pidPath -ErrorAction SilentlyContinue
if ($pidValue) {
    $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
    if ($process) {
        Stop-Process -Id $pidValue
        Write-Output "Stopped tdx_mover_watcher. PID=$pidValue"
    }
}

Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
