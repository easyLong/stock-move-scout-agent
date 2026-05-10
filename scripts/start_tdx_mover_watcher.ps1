$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$logDir = Join-Path $root "runs\logs"
$pidPath = Join-Path $root "runs\tdx_mover_watcher.pid"
$logPath = Join-Path $logDir "tdx_mover_watcher.log"
$errPath = Join-Path $logDir "tdx_mover_watcher.err.log"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

if (Test-Path $pidPath) {
    $oldPid = Get-Content $pidPath -ErrorAction SilentlyContinue
    if ($oldPid -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
        Write-Output "tdx_mover_watcher already running. PID=$oldPid"
        exit 0
    }
}

$script = Join-Path $root "scripts\tdx_mover_watcher.py"
$args = @($script, "--interval", "60", "--top", "10")
$process = Start-Process -FilePath "python" -ArgumentList $args -WorkingDirectory $root -RedirectStandardOutput $logPath -RedirectStandardError $errPath -PassThru -WindowStyle Hidden
$process.Id | Set-Content -Path $pidPath -Encoding ASCII

Write-Output "Started tdx_mover_watcher. PID=$($process.Id)"
Write-Output "Log: $logPath"
Write-Output "Err: $errPath"
