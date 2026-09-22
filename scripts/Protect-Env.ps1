$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $projectRoot ".env"
if (-not (Test-Path -LiteralPath $envFile)) { throw "Missing .env" }
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls.exe $envFile /inheritance:r | Out-Null
& icacls.exe $envFile /grant:r "${currentUser}:(M)" "*S-1-5-18:(F)" "*S-1-5-32-544:(F)" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Failed to protect .env ACL" }
Write-Host ".env ACL restricted to $currentUser, SYSTEM and local Administrators."
