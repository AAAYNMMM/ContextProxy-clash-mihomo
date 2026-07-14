[CmdletBinding()]
param(
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$extensionDirectory = Join-Path $projectRoot "extension"
$manifestPath = Join-Path $extensionDirectory "manifest.json"
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $projectRoot "dist"
}

if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Firefox extension manifest not found: $manifestPath"
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.manifest_version -ne 3) {
    throw "Expected a Manifest V3 extension."
}
if (-not $manifest.browser_specific_settings.gecko.id) {
    throw "Firefox packaging requires browser_specific_settings.gecko.id."
}
if (-not $manifest.background.scripts -or -not $manifest.background.service_worker) {
    throw "Manifest must include Firefox background scripts and Chrome service worker fallbacks."
}

$requiredFiles = @(
    "manifest.json",
    "background.js",
    "options.html",
    "options.js"
)
foreach ($file in $requiredFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $extensionDirectory $file) -PathType Leaf)) {
        throw "Extension file is missing: $file"
    }
}

$resolvedOutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $resolvedOutputDirectory -Force | Out-Null

$packageName = "ContextProxy-Reporter-firefox-$($manifest.version).xpi"
$packagePath = Join-Path $resolvedOutputDirectory $packageName
$zipPath = "$packagePath.zip"
Remove-Item -LiteralPath $packagePath, $zipPath -Force -ErrorAction SilentlyContinue

Compress-Archive -Path (Join-Path $extensionDirectory "*") -DestinationPath $zipPath -Force
Move-Item -LiteralPath $zipPath -Destination $packagePath -Force

$archive = [System.IO.Compression.ZipFile]::OpenRead($packagePath)
try {
    $entryNames = @($archive.Entries | ForEach-Object FullName)
    foreach ($file in $requiredFiles) {
        if ($file -notin $entryNames) {
            throw "Packaged XPI is missing: $file"
        }
    }
}
finally {
    $archive.Dispose()
}

$hash = (Get-FileHash -LiteralPath $packagePath -Algorithm SHA256).Hash
Write-Output "Created unsigned Firefox XPI: $packagePath"
Write-Output "SHA256: $hash"
Write-Output "Firefox Release/Beta requires Mozilla signing before installation."
