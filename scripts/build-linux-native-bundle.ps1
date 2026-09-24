param(
    [ValidateSet("x86_64", "aarch64")]
    [string]$Architecture = "x86_64",
    [string]$OutputDirectory = "dist"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$distRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot $OutputDirectory))
$bundleName = "dameng-mcp-linux-native"
# x86_64 沿用历史包名（已发布的 Release 与文档都引用它），其它架构用纯架构名
$archiveName = if ($Architecture -eq "x86_64") {
    "dameng-mcp-linux-native-centos7-x86_64.tar.gz"
} else {
    "dameng-mcp-linux-native-$Architecture.tar.gz"
}
$stageContainer = [System.IO.Path]::GetFullPath((Join-Path $distRoot ".native-bundle-stage-$Architecture"))
$stageRoot = Join-Path $stageContainer $bundleName
$archivePath = Join-Path $distRoot $archiveName
$checksumPath = "${archivePath}.sha256"

$minicondaFileName = "Miniconda3-py311_24.3.0-0-Linux-$Architecture.sh"
$minicondaSource = Join-Path $distRoot $minicondaFileName
$wheelhouseSource = Join-Path $distRoot $(if ($Architecture -eq "x86_64") { "linux-wheelhouse" } else { "linux-wheelhouse-$Architecture" })
$minicondaHashes = @{
    "x86_64"  = "4da8dde69eca0d9bc31420349a204851bfa2a1c87aeb87fe0c05517797edaac4"
    "aarch64" = "49082882752167cdea60e1aeedac7b73136bdfdd63b9bb3aca3c038901544458"
}
$expectedMinicondaHash = $minicondaHashes[$Architecture]

foreach ($required in @($minicondaSource, $wheelhouseSource)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Missing offline asset: $required. Run scripts\Download-DmWheels.ps1 -Architecture $Architecture -WithMiniconda first." }
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $minicondaSource).Hash.ToLowerInvariant() -ne $expectedMinicondaHash) {
    throw "Unexpected Miniconda installer checksum."
}
$wheels = Get-ChildItem -LiteralPath $wheelhouseSource -Filter "*.whl" -File
if ($wheels.Count -eq 0) { throw "Linux wheelhouse is empty." }
if ($wheels.Name -match "win32|win_amd64") { throw "Windows wheels must not enter the Linux bundle." }
$wrongArch = if ($Architecture -eq "x86_64") { "aarch64" } else { "x86_64" }
if ($wheels.Name -match $wrongArch) { throw "$wrongArch wheels must not enter the $Architecture bundle." }

New-Item -ItemType Directory -Path $distRoot -Force | Out-Null
if (-not $stageContainer.StartsWith($distRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe staging path: $stageContainer"
}
if (Test-Path -LiteralPath $stageContainer) { Remove-Item -LiteralPath $stageContainer -Recurse -Force }

foreach ($directory in @(
    "app\core", "app\tools", "app\scripts", "deploy\systemd", "scripts", "vendor", "wheelhouse"
)) {
    New-Item -ItemType Directory -Path (Join-Path $stageRoot $directory) -Force | Out-Null
}

Copy-Item -LiteralPath (Join-Path $projectRoot "server.py") -Destination (Join-Path $stageRoot "app\server.py")
Copy-Item -LiteralPath (Join-Path $projectRoot "requirements.txt") -Destination (Join-Path $stageRoot "app\requirements.txt")
Copy-Item -Path (Join-Path $projectRoot "core\*.py") -Destination (Join-Path $stageRoot "app\core")
Copy-Item -Path (Join-Path $projectRoot "tools\*.py") -Destination (Join-Path $stageRoot "app\tools")

foreach ($script in @("check_dameng.py", "probe_mcp.py", "probe_sse.py", "probe-linux-native.sh")) {
    Copy-Item -LiteralPath (Join-Path $projectRoot "scripts\$script") -Destination (Join-Path $stageRoot "app\scripts\$script")
}
foreach ($script in @("install-linux-native.sh", "quick-install.sh", "check-linux-native-host.sh", "axis-dameng-mcp.sh", "configure-firewall-linux.sh")) {
    Copy-Item -LiteralPath (Join-Path $projectRoot "scripts\$script") -Destination (Join-Path $stageRoot "scripts\$script")
}

Copy-Item -LiteralPath (Join-Path $projectRoot "deploy\systemd\axis-dameng-mcp.service") -Destination (Join-Path $stageRoot "deploy\systemd\axis-dameng-mcp.service")
Copy-Item -LiteralPath (Join-Path $projectRoot ".env.linux-native.example") -Destination (Join-Path $stageRoot ".env.linux-native.example")
Copy-Item -LiteralPath (Join-Path $projectRoot "docs\LINUX_NATIVE_DEPLOYMENT.md") -Destination (Join-Path $stageRoot "README.md")
Copy-Item -LiteralPath $minicondaSource -Destination (Join-Path $stageRoot "vendor\$minicondaFileName")
Copy-Item -Path (Join-Path $wheelhouseSource "*.whl") -Destination (Join-Path $stageRoot "wheelhouse")

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$textFiles = @(
    Get-ChildItem -LiteralPath (Join-Path $stageRoot "scripts") -Filter "*.sh" -File
    Get-ChildItem -LiteralPath (Join-Path $stageRoot "app\scripts") -Filter "*.sh" -File
    Get-Item -LiteralPath (Join-Path $stageRoot "deploy\systemd\axis-dameng-mcp.service")
    Get-Item -LiteralPath (Join-Path $stageRoot ".env.linux-native.example")
    Get-Item -LiteralPath (Join-Path $stageRoot "README.md")
)
foreach ($textFile in $textFiles) {
    $text = [System.IO.File]::ReadAllText($textFile.FullName).Replace("`r`n", "`n")
    [System.IO.File]::WriteAllText($textFile.FullName, $text, $utf8NoBom)
}

$stagedMiniconda = Join-Path $stageRoot "vendor\$minicondaFileName"
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $stagedMiniconda).Hash.ToLowerInvariant() -ne $expectedMinicondaHash) {
    throw "The staged Miniconda self-extracting binary was modified."
}

$manifestLines = Get-ChildItem -LiteralPath $stageRoot -Recurse -File |
    Sort-Object FullName |
    ForEach-Object {
        $relative = [System.IO.Path]::GetRelativePath($stageRoot, $_.FullName).Replace("\", "/")
        $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant()
        "$hash  $relative"
    }
[System.IO.File]::WriteAllText(
    (Join-Path $stageRoot "SHA256SUMS"),
    (($manifestLines -join "`n") + "`n"),
    $utf8NoBom
)

foreach ($oldFile in @($archivePath, $checksumPath)) {
    if (Test-Path -LiteralPath $oldFile) { Remove-Item -LiteralPath $oldFile -Force }
}
& tar.exe -czf $archivePath -C $stageContainer $bundleName
if ($LASTEXITCODE -ne 0) { throw "tar failed with exit code $LASTEXITCODE" }
& tar.exe -tzf $archivePath | Out-Null
if ($LASTEXITCODE -ne 0) { throw "The generated archive cannot be read." }

$archiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archivePath).Hash.ToLowerInvariant()
[System.IO.File]::WriteAllText($checksumPath, "$archiveHash  $archiveName`n", $utf8NoBom)
Remove-Item -LiteralPath $stageContainer -Recurse -Force

Get-Item -LiteralPath $archivePath, $checksumPath | Select-Object FullName, Length, LastWriteTime
Write-Host "SHA256: $archiveHash"
