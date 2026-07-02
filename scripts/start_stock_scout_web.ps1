param(
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8788,
    [string]$MysqlUser = "root",
    [string]$MysqlPassword = $env:MYSQL_PWD,
    [string]$MysqlHost = "127.0.0.1",
    [int]$MysqlPort = 3306,
    [int]$MysqlTimeout = 180
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$logDir = Join-Path $root "runs\logs"
$pidPath = Join-Path $root "runs\stock_scout_web.pid"
$logPath = Join-Path $logDir "stock_scout_web.log"
$errPath = Join-Path $logDir "stock_scout_web.err.log"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

if (Test-Path $pidPath) {
    $oldPid = Get-Content $pidPath -ErrorAction SilentlyContinue
    if ($oldPid -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
        Write-Output "stock_scout_web already running. PID=$oldPid"
        Write-Output "URL: http://$HostName`:$Port/"
        exit 0
    }
}

$script = Join-Path $root "scripts\stock_scout_web.py"
$args = @(
    $script,
    "--host", $HostName,
    "--port", "$Port",
    "--mysql-enabled",
    "--mysql-user", $MysqlUser,
    "--mysql-host", $MysqlHost,
    "--mysql-port", "$MysqlPort",
    "--mysql-timeout", "$MysqlTimeout"
)

if ($MysqlPassword) {
    $args += @("--mysql-password", $MysqlPassword)
}

$process = Start-Process -FilePath "python" -ArgumentList $args -WorkingDirectory $root -RedirectStandardOutput $logPath -RedirectStandardError $errPath -PassThru -WindowStyle Hidden
$process.Id | Set-Content -Path $pidPath -Encoding ASCII

Write-Output "Started stock_scout_web. PID=$($process.Id)"
Write-Output "URL: http://$HostName`:$Port/"
Write-Output "Log: $logPath"
Write-Output "Err: $errPath"
