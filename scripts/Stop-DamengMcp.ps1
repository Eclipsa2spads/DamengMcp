$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $projectRoot ".env"
$pidFile = Join-Path $projectRoot "dameng-mcp.pid"
$portSetting = Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^MCP_PORT=' } | Select-Object -Last 1
$port = if ($portSetting) { [int](($portSetting -split '=', 2)[1].Trim()) } else { 8082 }
if (-not (Test-Path -LiteralPath $pidFile)) { throw "PID file not found." }
$processId = [int](Get-Content -LiteralPath $pidFile)
$process = Get-Process -Id $processId -ErrorAction SilentlyContinue
if ($null -eq $process) {
    Remove-Item -LiteralPath $pidFile
    Write-Host "Process was already stopped."
    exit 0
}
$listenerPid = $null
foreach ($line in (& netstat -ano -p tcp)) {
    if ($line -match "^\s*TCP\s+\S+:$port\s+\S+\s+LISTENING\s+(\d+)\s*$") {
        $listenerPid = [int]$Matches[1]
        break
    }
}
if ($listenerPid -ne $processId) {
    throw "PID file does not identify the process listening on TCP $port."
}
Stop-Process -Id $processId
Remove-Item -LiteralPath $pidFile
Write-Host "Dameng MCP stopped. PID=$processId"
