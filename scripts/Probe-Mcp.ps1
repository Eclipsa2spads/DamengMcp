param(
    [string]$Url,
    [switch]$Call,
    [string]$Sql = "SELECT 1 FROM DUAL"
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $projectRoot ".env"
if (-not $Url) {
    $portSetting = Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^MCP_PORT=' } | Select-Object -Last 1
    $port = if ($portSetting) { [int](($portSetting -split '=', 2)[1].Trim()) } else { 8082 }
    $Url = "http://127.0.0.1:$port/mcp"
}
$arguments = @((Join-Path $PSScriptRoot "probe_mcp.py"), "--url", $Url)
if ($Call) { $arguments += @("--call", "--sql", $Sql) }
& (Join-Path $projectRoot ".venv\Scripts\python.exe") @arguments
exit $LASTEXITCODE
