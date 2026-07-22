$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$version = (Get-Content -LiteralPath (Join-Path $repositoryRoot "package.json") -Raw | ConvertFrom-Json).version
$releaseRoot = Join-Path $repositoryRoot "release"
$portableRoot = Join-Path $releaseRoot "Material-Decision-Workbench-folder"
$portableZip = Join-Path $releaseRoot "Material-Decision-Workbench-folder-$version.zip"

Push-Location $repositoryRoot
try {
    npm.cmd run build
    uv run pyinstaller --noconfirm --clean --distpath dist/sidecar --workpath build/sidecar packaging/sidecar.spec
    npm.cmd exec electron-builder -- --config packaging/electron-builder.yml --win nsis

    if (Test-Path -LiteralPath $portableRoot) {
        Remove-Item -LiteralPath $portableRoot -Recurse -Force
    }
    Copy-Item -LiteralPath (Join-Path $releaseRoot "win-unpacked") -Destination $portableRoot -Recurse
    New-Item -ItemType File -Path (Join-Path $portableRoot "portable.marker") -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $repositoryRoot "packaging/PORTABLE-README.txt") -Destination (Join-Path $portableRoot "はじめに.txt")
    if (Test-Path -LiteralPath $portableZip) {
        Remove-Item -LiteralPath $portableZip -Force
    }
    Compress-Archive -LiteralPath $portableRoot -DestinationPath $portableZip -CompressionLevel Optimal
    Write-Host "Installer: $(Join-Path $releaseRoot "Material-Decision-Workbench-Setup-$version.exe")"
    Write-Host "Folder ZIP: $portableZip"
} finally {
    Pop-Location
}
