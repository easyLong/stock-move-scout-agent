param(
    [string]$MysqlUser = "root",
    [string]$MysqlPassword = $env:MYSQL_PWD,
    [string]$MysqlHost = "127.0.0.1",
    [int]$MysqlPort = 3306,
    [int]$BatchSize = 50,
    [int]$StartBatch = 0,
    [int]$MaxPages = 1,
    [int]$RequestTimeout = 5,
    [int]$Workers = 6,
    [switch]$RefreshAll
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$logDir = Join-Path $root "runs\logs"
$pidPath = Join-Path $root "runs\stock_scout_cold_init.pid"
$logPath = Join-Path $logDir "stock_scout_cold_init.log"
$errPath = Join-Path $logDir "stock_scout_cold_init.err.log"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

if (Test-Path $pidPath) {
    $oldPid = Get-Content $pidPath -ErrorAction SilentlyContinue
    if ($oldPid -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
        Write-Output "stock_scout_cold_init already running. PID=$oldPid"
        exit 0
    }
}

$script = Join-Path $root "scripts\init_stock_scout_cold_data.py"
$args = @(
    $script,
    "--mysql-enabled",
    "--mysql-user", $MysqlUser,
    "--mysql-host", $MysqlHost,
    "--mysql-port", "$MysqlPort",
    "--batch-size", "$BatchSize",
    "--start-batch", "$StartBatch",
    "--max-batches", "0",
    "--max-pages", "$MaxPages",
    "--request-timeout", "$RequestTimeout",
    "--workers", "$Workers",
    "--timeout", "1200"
)

if ($RefreshAll) {
    $args += @("--refresh-all")
}

if ($MysqlPassword) {
    $args += @("--mysql-password", $MysqlPassword)
}

$process = Start-Process -FilePath "python" -ArgumentList $args -WorkingDirectory $root -RedirectStandardOutput $logPath -RedirectStandardError $errPath -PassThru -WindowStyle Hidden
$process.Id | Set-Content -Path $pidPath -Encoding ASCII

Write-Output "Started stock_scout_cold_init. PID=$($process.Id)"
Write-Output "State: $(Join-Path $root 'runs\cold_data_init\cold_data_init_state.json')"
Write-Output "Log: $logPath"
Write-Output "Err: $errPath"
