param(
    [string]$OutputDirectory = "dist\linux-wheelhouse",
    [switch]$WithMiniconda
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$distRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot "dist"))
$wheelhouse = [System.IO.Path]::GetFullPath((Join-Path $projectRoot $OutputDirectory))
$requirements = Join-Path $distRoot "linux-requirements.txt"
$minicondaTarget = Join-Path $distRoot "Miniconda3-py311_24.3.0-0-Linux-x86_64.sh"
$minicondaUrl = "https://repo.anaconda.com/miniconda/Miniconda3-py311_24.3.0-0-Linux-x86_64.sh"
$minicondaHash = "4da8dde69eca0d9bc31420349a204851bfa2a1c87aeb87fe0c05517797edaac4"

if (-not (Test-Path -LiteralPath $python)) { throw "Missing .venv. Install dependencies first." }
New-Item -ItemType Directory -Path $distRoot -Force | Out-Null

Write-Host "Resolving the Linux CPython 3.11 runtime closure"
& $python (Join-Path $PSScriptRoot "export_linux_requirements.py") |
    Set-Content -LiteralPath $requirements -Encoding ascii

if (Test-Path -LiteralPath $wheelhouse) {
    Remove-Item -LiteralPath $wheelhouse -Recurse -Force
}
New-Item -ItemType Directory -Path $wheelhouse -Force | Out-Null

Write-Host "Downloading manylinux2014 x86_64 wheels (glibc 2.17, CentOS 7 compatible)"
& $python -m pip download `
    --disable-pip-version-check `
    --quiet `
    --no-deps `
    --requirement $requirements `
    --only-binary=:all: `
    --platform manylinux2014_x86_64 `
    --platform manylinux_2_17_x86_64 `
    --platform manylinux2010_x86_64 `
    --platform manylinux_2_12_x86_64 `
    --python-version 311 `
    --implementation cp `
    --dest $wheelhouse
if ($LASTEXITCODE -ne 0) { throw "pip download failed with exit code $LASTEXITCODE" }

$windowsWheels = Get-ChildItem -LiteralPath $wheelhouse -Filter "*.whl" |
    Where-Object { $_.Name -match "win32|win_amd64" }
if ($windowsWheels) { throw "Windows wheels were downloaded: $($windowsWheels.Name -join ', ')" }
$wheels = Get-ChildItem -LiteralPath $wheelhouse -Filter "*.whl" -File
if ($wheels.Count -eq 0) { throw "The Linux wheelhouse is empty." }

Write-Host "Verifying the dependency closure"
& $python (Join-Path $PSScriptRoot "validate_linux_wheelhouse.py") $wheelhouse (Join-Path $projectRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Wheelhouse validation failed with exit code $LASTEXITCODE" }

if ($WithMiniconda) {
    if ((Test-Path -LiteralPath $minicondaTarget) -and
        (Get-FileHash -Algorithm SHA256 -LiteralPath $minicondaTarget).Hash.ToLowerInvariant() -eq $minicondaHash) {
        Write-Host "Bundled Miniconda installer is already present and verified"
    } else {
        Write-Host "Downloading the Miniconda Python 3.11 installer"
        $previousProgress = $ProgressPreference
        $ProgressPreference = "SilentlyContinue"
        try {
            Invoke-WebRequest -Uri $minicondaUrl -OutFile $minicondaTarget
        } finally {
            $ProgressPreference = $previousProgress
        }
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $minicondaTarget).Hash.ToLowerInvariant() -ne $minicondaHash) {
            throw "Unexpected Miniconda installer checksum."
        }
        Write-Host "Miniconda installer verified: $minicondaTarget"
    }
}

Write-Host "Linux wheelhouse ready: $wheelhouse ($($wheels.Count) wheels)"
if (-not $WithMiniconda) {
    Write-Host "Run again with -WithMiniconda to also fetch the bundled Python runtime."
}
