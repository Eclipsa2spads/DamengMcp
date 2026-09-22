$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
& (Join-Path $projectRoot ".venv\Scripts\python.exe") (Join-Path $PSScriptRoot "check_dameng.py")
exit $LASTEXITCODE
