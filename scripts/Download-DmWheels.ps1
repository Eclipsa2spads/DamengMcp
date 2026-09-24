param(
    [ValidateSet("x86_64", "aarch64")]
    [string]$Architecture = "x86_64",
    [string]$OutputDirectory = "",
    [switch]$WithMiniconda
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$distRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot "dist"))

# x86_64 沿用历史目录名 dist\linux-wheelhouse，其它架构加后缀
if ([string]::IsNullOrEmpty($OutputDirectory)) {
    $OutputDirectory = if ($Architecture -eq "x86_64") { "dist\linux-wheelhouse" } else { "dist\linux-wheelhouse-$Architecture" }
}
$wheelhouse = [System.IO.Path]::GetFullPath((Join-Path $projectRoot $OutputDirectory))
$requirements = Join-Path $distRoot "linux-requirements.txt"

$minicondaFileName = "Miniconda3-py311_24.3.0-0-Linux-$Architecture.sh"
$minicondaTarget = Join-Path $distRoot $minicondaFileName
$minicondaUrl = "https://repo.anaconda.com/miniconda/$minicondaFileName"
# Anaconda 不提供 .sha256 sidecar，这两个值都是下载后自行计算、并两次独立下载交叉核对过的
$minicondaHashes = @{
    "x86_64"  = "4da8dde69eca0d9bc31420349a204851bfa2a1c87aeb87fe0c05517797edaac4"
    "aarch64" = "49082882752167cdea60e1aeedac7b73136bdfdd63b9bb3aca3c038901544458"
}
$minicondaHash = $minicondaHashes[$Architecture]

# 两类架构都以 glibc 2.17 为地板（manylinux2014），与离线包的说明保持一致
$platforms = if ($Architecture -eq "x86_64") {
    @("manylinux2014_x86_64", "manylinux_2_17_x86_64", "manylinux2010_x86_64", "manylinux_2_12_x86_64")
} else {
    @("manylinux2014_aarch64", "manylinux_2_17_aarch64")
}
$platformArgs = @()
foreach ($item in $platforms) { $platformArgs += @("--platform", $item) }

if (-not (Test-Path -LiteralPath $python)) { throw "Missing .venv. Install dependencies first." }
New-Item -ItemType Directory -Path $distRoot -Force | Out-Null

Write-Host "Resolving the Linux CPython 3.11 runtime closure"
& $python (Join-Path $PSScriptRoot "export_linux_requirements.py") |
    Set-Content -LiteralPath $requirements -Encoding ascii

if (Test-Path -LiteralPath $wheelhouse) {
    Remove-Item -LiteralPath $wheelhouse -Recurse -Force
}
New-Item -ItemType Directory -Path $wheelhouse -Force | Out-Null

Write-Host "Downloading $Architecture wheels (glibc 2.17 floor, manylinux2014)"
& $python -m pip download `
    --disable-pip-version-check `
    --quiet `
    --no-deps `
    --requirement $requirements `
    --only-binary=:all: `
    @platformArgs `
    --python-version 311 `
    --implementation cp `
    --dest $wheelhouse
if ($LASTEXITCODE -ne 0) { throw "pip download failed with exit code $LASTEXITCODE" }

$wrongArch = if ($Architecture -eq "x86_64") { "aarch64" } else { "x86_64" }
$wrongWheels = Get-ChildItem -LiteralPath $wheelhouse -Filter "*.whl" |
    Where-Object { $_.Name -match "win32|win_amd64" -or $_.Name -match $wrongArch }
if ($wrongWheels) { throw "Wheels for the wrong platform were downloaded: $($wrongWheels.Name -join ', ')" }
$wheels = Get-ChildItem -LiteralPath $wheelhouse -Filter "*.whl" -File
if ($wheels.Count -eq 0) { throw "The Linux wheelhouse is empty." }

Write-Host "Verifying the dependency closure"
& $python (Join-Path $PSScriptRoot "validate_linux_wheelhouse.py") $wheelhouse (Join-Path $projectRoot "requirements.txt") --architecture $Architecture
if ($LASTEXITCODE -ne 0) { throw "Wheelhouse validation failed with exit code $LASTEXITCODE" }

if ($WithMiniconda) {
    if ((Test-Path -LiteralPath $minicondaTarget) -and
        (Get-FileHash -Algorithm SHA256 -LiteralPath $minicondaTarget).Hash.ToLowerInvariant() -eq $minicondaHash) {
        Write-Host "Bundled Miniconda installer is already present and verified"
    } else {
        Write-Host "Downloading the Miniconda Python 3.11 installer ($Architecture)"
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

Write-Host "Linux wheelhouse ready: $wheelhouse ($($wheels.Count) wheels, $Architecture)"
if (-not $WithMiniconda) {
    Write-Host "Run again with -WithMiniconda to also fetch the bundled Python runtime."
}
