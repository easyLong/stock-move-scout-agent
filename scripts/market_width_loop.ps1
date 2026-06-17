param(
    [string]$MysqlUser = "root",
    [string]$MysqlPassword = $env:MYSQL_PWD,
    [string]$MysqlHost = "127.0.0.1",
    [int]$MysqlPort = 3306,
    [int]$IntervalSeconds = 60
)

$ErrorActionPreference = "Continue"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$script = Join-Path $root "scripts\collect_market_width_snapshot.py"

function Test-TradingTime {
    $now = Get-Date
    if ($now.DayOfWeek -eq "Saturday" -or $now.DayOfWeek -eq "Sunday") {
        return $false
    }
    $minute = $now.Hour * 60 + $now.Minute
    return (($minute -ge (9 * 60 + 30) -and $minute -le (11 * 60 + 30)) -or
            ($minute -ge (13 * 60) -and $minute -le (15 * 60)))
}

while ($true) {
    if (Test-TradingTime) {
        $args = @(
            $script,
            "--mysql-enabled",
            "--mysql-user", $MysqlUser,
            "--mysql-host", $MysqlHost,
            "--mysql-port", "$MysqlPort",
            "--mysql-timeout", "120",
            "--source", "tdx",
            "--tdx-timeout", "8",
            "--batch-size", "80",
            "--skip-kpl-market-capacity",
            "--skip-ensure-tables"
        )
        if ($MysqlPassword) {
            $args += @("--mysql-password", $MysqlPassword)
        }
        $startedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        Write-Output "[$startedAt] collect_market_width_snapshot start"
        & python @args
        $finishedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        Write-Output "[$finishedAt] collect_market_width_snapshot exit=$LASTEXITCODE"
    }
    Start-Sleep -Seconds ([Math]::Max(10, $IntervalSeconds))
}
