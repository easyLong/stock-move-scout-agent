param(
    [string]$MysqlUser = "root",
    [string]$MysqlPassword = $env:MYSQL_PWD,
    [string]$MysqlHost = "127.0.0.1",
    [int]$MysqlPort = 3306,
    [int]$IntervalSeconds = 60
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$logDir = Join-Path $root "runs\logs"
$pidPath = Join-Path $root "runs\market_width_loop.pid"
$logPath = Join-Path $logDir "market_width_loop.log"
$errPath = Join-Path $logDir "market_width_loop.err.log"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

if (Test-Path $pidPath) {
    $oldPid = Get-Content $pidPath -ErrorAction SilentlyContinue
    if ($oldPid -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
        Write-Output "market_width_loop already running. PID=$oldPid"
        exit 0
    }
}

$script = Join-Path $root "scripts\market_width_loop.ps1"
$args = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", $script,
    "-MysqlUser", $MysqlUser,
    "-MysqlHost", $MysqlHost,
    "-MysqlPort", "$MysqlPort",
    "-IntervalSeconds", "$IntervalSeconds"
)

if ($MysqlPassword) {
    $args += @("-MysqlPassword", $MysqlPassword)
}

$process = Start-Process -FilePath "powershell" -ArgumentList $args -WorkingDirectory $root -RedirectStandardOutput $logPath -RedirectStandardError $errPath -PassThru -WindowStyle Hidden
$process.Id | Set-Content -Path $pidPath -Encoding ASCII

Write-Output "Started market_width_loop. PID=$($process.Id)"
Write-Output "Log: $logPath"
Write-Output "Err: $errPath"
