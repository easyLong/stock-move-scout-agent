param(
    [string]$MysqlExe = "C:\Program Files\MySQL\MySQL Server 8.4\bin\mysql.exe",
    [string]$HostName = "127.0.0.1",
    [int]$Port = 3306,
    [string]$User = "root",
    [string]$Password = "",
    [string]$SchemaSql = "database\mysql\stock_scout_schema.sql"
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$SchemaPath = Join-Path $Root $SchemaSql

if (-not (Test-Path $MysqlExe)) {
    throw "mysql.exe not found: $MysqlExe"
}

if (-not (Test-Path $SchemaPath)) {
    throw "schema sql not found: $SchemaPath"
}

Write-Host "MySQL: $MysqlExe"
Write-Host "Schema: $SchemaPath"
Write-Host "Target: $User@$HostName`:$Port"
if (-not $Password) {
    Write-Host "You will be prompted for the MySQL password."
}

$originalMysqlPwd = $env:MYSQL_PWD
if ($Password) {
    $env:MYSQL_PWD = $Password
}

try {
    $passwordArg = @()
    if (-not $Password) {
        $passwordArg = @("--password")
    }
    Get-Content -Raw -Encoding UTF8 $SchemaPath | & $MysqlExe `
        --host=$HostName `
        --port=$Port `
        --user=$User `
        @passwordArg `
        --default-character-set=utf8mb4 `
        --comments `
        --show-warnings `
        --binary-mode
} finally {
    if ($Password) {
        if ($null -eq $originalMysqlPwd) {
            Remove-Item Env:\MYSQL_PWD -ErrorAction SilentlyContinue
        } else {
            $env:MYSQL_PWD = $originalMysqlPwd
        }
    }
}

if ($LASTEXITCODE -ne 0) {
    throw "mysql schema initialization failed with exit code $LASTEXITCODE"
}

Write-Host "stock_scout schema initialized."
