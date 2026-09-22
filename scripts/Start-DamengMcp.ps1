param([switch]$Foreground)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$envFile = Join-Path $projectRoot ".env"
$pidFile = Join-Path $projectRoot "dameng-mcp.pid"
$logDir = Join-Path $projectRoot "logs"
if (-not (Test-Path -LiteralPath $python)) { throw "Missing .venv. Install dependencies first." }
if (-not (Test-Path -LiteralPath $envFile)) { throw "Missing .env. Copy .env.example and protect it." }
$portSetting = Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^MCP_PORT=' } | Select-Object -Last 1
$port = if ($portSetting) { [int](($portSetting -split '=', 2)[1].Trim()) } else { 8082 }
function Test-LocalTcpPort([int]$TcpPort) {
    $client = [Net.Sockets.TcpClient]::new()
    try {
        $pending = $client.BeginConnect('127.0.0.1', $TcpPort, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne(250)) { return $false }
        $client.EndConnect($pending)
        return $true
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}
function Get-ListeningProcessId([int]$TcpPort) {
    foreach ($line in (& netstat -ano -p tcp)) {
        if ($line -match "^\s*TCP\s+\S+:$TcpPort\s+\S+\s+LISTENING\s+(\d+)\s*$") {
            return [int]$Matches[1]
        }
    }
    return $null
}
function Get-PrimaryIPv4 {
    foreach ($address in [System.Net.Dns]::GetHostAddresses([System.Net.Dns]::GetHostName())) {
        if ($address.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork -and $address.IPAddressToString -notlike "127.*") {
            return $address.IPAddressToString
        }
    }
    return "127.0.0.1"
}
if (Test-LocalTcpPort $port) {
    throw "TCP port $port is already in use."
}
if ($Foreground) {
    Push-Location $projectRoot
    try { & $python "server.py"; exit $LASTEXITCODE } finally { Pop-Location }
}
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$process = Start-Process -FilePath $python -ArgumentList @("server.py") -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir "stdout.log") -RedirectStandardError (Join-Path $logDir "server.log") -PassThru
$deadline = (Get-Date).AddSeconds(20)
$servicePid = $null
do {
    Start-Sleep -Milliseconds 250
    $servicePid = Get-ListeningProcessId $port
    if ($process.HasExited) { break }
} while ($null -eq $servicePid -and (Get-Date) -lt $deadline)
if ($null -eq $servicePid) {
    if (-not $process.HasExited) { Stop-Process -Id $process.Id }
    throw "Dameng MCP did not start listening on TCP $port. Check logs\server.log."
}
Set-Content -LiteralPath $pidFile -Value $servicePid -Encoding ascii
$hostIp = Get-PrimaryIPv4
Write-Host "Dameng MCP started. PID=$servicePid"
Write-Host "Streamable HTTP: http://${hostIp}:$port/mcp"
Write-Host "HTTP+SSE:       http://${hostIp}:$port/sse"
