param(
    [string]$WorkerTypes = "maintenance,cold,warm",
    [string]$WorkerId = "",
    [string]$MysqlUser = "root",
    [string]$MysqlPassword = $env:MYSQL_PWD,
    [string]$MysqlHost = "127.0.0.1",
    [int]$MysqlPort = 3306,
    [double]$PollSeconds = 3
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$logDir = Join-Path $root "runs\logs"
$safeWorkerTypes = ($WorkerTypes -replace "[^A-Za-z0-9_,.-]", "_")
$pidPath = Join-Path $root "runs\stock_scout_worker_$safeWorkerTypes.pid"
$logPath = Join-Path $logDir "stock_scout_worker_$safeWorkerTypes.log"
$errPath = Join-Path $logDir "stock_scout_worker_$safeWorkerTypes.err.log"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

if (Test-Path $pidPath) {
    $oldPid = Get-Content $pidPath -ErrorAction SilentlyContinue
    if ($oldPid -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
        Write-Output "stock_scout_worker already running. WorkerTypes=$WorkerTypes PID=$oldPid"
        exit 0
    }
}

$script = Join-Path $root "scripts\stock_scout_task_scheduler.py"
$args = @(
    $script,
    "--mode", "worker",
    "--worker-types", $WorkerTypes,
    "--poll-seconds", "$PollSeconds",
    "--mysql-enabled",
    "--mysql-user", $MysqlUser,
    "--mysql-host", $MysqlHost,
    "--mysql-port", "$MysqlPort"
)

if ($WorkerId) {
    $args += @("--worker-id", $WorkerId)
}

if ($MysqlPassword) {
    $args += @("--mysql-password", $MysqlPassword)
}

$process = Start-Process -FilePath "python" -ArgumentList $args -WorkingDirectory $root -RedirectStandardOutput $logPath -RedirectStandardError $errPath -PassThru -WindowStyle Hidden
$process.Id | Set-Content -Path $pidPath -Encoding ASCII

Write-Output "Started stock_scout_worker. WorkerTypes=$WorkerTypes PID=$($process.Id)"
Write-Output "Log: $logPath"
Write-Output "Err: $errPath"
