param(
    [switch]$MysqlEnabled = $true,
    [string]$MysqlUser = "root",
    [string]$MysqlPassword = $env:MYSQL_PWD,
    [string]$MysqlHost = "127.0.0.1",
    [int]$MysqlPort = 3306
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$logDir = Join-Path $root "runs\logs"
$pidPath = Join-Path $root "runs\windowed_stock_scout_agent.pid"
$logPath = Join-Path $logDir "windowed_stock_scout_agent.log"
$errPath = Join-Path $logDir "windowed_stock_scout_agent.err.log"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$secretPath = $env:OPENAI_ENV_FILE
if ($secretPath -and (Test-Path -LiteralPath $secretPath)) {
    $content = Get-Content -LiteralPath $secretPath -Raw -Encoding UTF8
    foreach ($name in @("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")) {
        $pattern = '(?im)^\s*(?:\$env:)?' + [regex]::Escape($name) + '\s*=\s*["'']?([^"''\r\n]+)["'']?\s*$'
        $match = [regex]::Match($content, $pattern)
        if ($match.Success) {
            [Environment]::SetEnvironmentVariable($name, $match.Groups[1].Value.Trim(), "Process")
        }
    }
}

if (Test-Path $pidPath) {
    $oldPid = Get-Content $pidPath -ErrorAction SilentlyContinue
    if ($oldPid -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
        Write-Output "windowed_stock_scout_agent already running. PID=$oldPid"
        exit 0
    }
}

$script = Join-Path $root "scripts\windowed_stock_scout_agent.py"
$args = @(
    $script,
    "--scan-interval", "15",
    "--window-seconds", "300",
    "--scan-top", "20",
    "--min-speed-signal", "1.0",
    "--min-amount-delta-15s", "30000000",
    "--min-amount-delta-speed", "0.5",
    "--aggregate-top", "5",
    "--evidence-top", "5",
    "--min-evidence-pct-change", "0",
    "--community-top", "3",
    "--community-mode", "cache",
    "--community-cache-hours", "72",
    "--community-manual-verify-wait", "8",
    "--community-verify-retries", "0",
    "--community-bridge-timeout", "40",
    "--mysql-primary",
    "--no-file-output"
)

if ($MysqlEnabled) {
    $args += @(
        "--mysql-enabled",
        "--mysql-user", $MysqlUser,
        "--mysql-host", $MysqlHost,
        "--mysql-port", "$MysqlPort"
    )
    if ($MysqlPassword) {
        $args += @("--mysql-password", $MysqlPassword)
    }
}

$process = Start-Process -FilePath "python" -ArgumentList $args -WorkingDirectory $root -RedirectStandardOutput $logPath -RedirectStandardError $errPath -PassThru -WindowStyle Hidden
$process.Id | Set-Content -Path $pidPath -Encoding ASCII

Write-Output "Started windowed_stock_scout_agent. PID=$($process.Id)"
Write-Output "Log: $logPath"
Write-Output "Err: $errPath"
