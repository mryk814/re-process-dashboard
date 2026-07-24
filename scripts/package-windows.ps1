$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$version = (Get-Content -LiteralPath (Join-Path $repositoryRoot "package.json") -Raw | ConvertFrom-Json).version
$releaseRoot = Join-Path $repositoryRoot "release"
$portableRoot = Join-Path $releaseRoot "Material-Decision-Workbench-folder"
$portableZip = Join-Path $releaseRoot "Material-Decision-Workbench-folder-$version.zip"

Push-Location $repositoryRoot
try {
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "application build failed with exit code $LASTEXITCODE" }

    uv run --extra dev python -m PyInstaller --noconfirm --clean --distpath dist/sidecar --workpath build/sidecar packaging/sidecar.spec
    if ($LASTEXITCODE -ne 0) { throw "sidecar build failed with exit code $LASTEXITCODE" }

    $releasePrefix = $releaseRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    $staleArtifacts = @(
        (Join-Path $releaseRoot "win-unpacked")
        (Join-Path $releaseRoot "Material-Decision-Workbench-Setup-$version.exe")
        (Join-Path $releaseRoot "Material-Decision-Workbench-Setup-$version.exe.blockmap")
        (Join-Path $releaseRoot "latest.yml")
        $portableRoot
        $portableZip
    )
    foreach ($artifact in $staleArtifacts) {
        $artifactPath = [IO.Path]::GetFullPath($artifact)
        if (-not $artifactPath.StartsWith($releasePrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "refusing to remove a stale artifact outside release: $artifactPath"
        }
        if (Test-Path -LiteralPath $artifactPath) {
            Remove-Item -LiteralPath $artifactPath -Recurse -Force
        }
    }

    npm.cmd exec electron-builder -- --config packaging/electron-builder.yml --win nsis
    if ($LASTEXITCODE -ne 0) { throw "installer build failed with exit code $LASTEXITCODE" }

    $unpackedResources = Join-Path $releaseRoot "win-unpacked/resources"
    $requiredPackagedFiles = @(
        "sidecar/material-workbench-sidecar.exe"
        "models/active-packages.json"
        "models/available-packages.json"
        "data/source/external/heat_treatment_tradeoff_samples.csv"
        "data/source/external/concrete_mix_samples.csv"
        "data/source/external/wear_curve_samples.csv"
        "data/source/external/battery_cycle_samples.csv"
    )
    $activePackages = Get-Content -LiteralPath (Join-Path $repositoryRoot "models/active-packages.json") -Raw | ConvertFrom-Json
    $requiredPackagedFiles += $activePackages.tasks.PSObject.Properties.Value | ForEach-Object {
        "models/$($_.active)/manifest.json"
    }
    $availablePackages = Get-Content -LiteralPath (Join-Path $repositoryRoot "models/available-packages.json") -Raw | ConvertFrom-Json
    $requiredPackagedFiles += $availablePackages.packages | ForEach-Object {
        "models/$($_)/manifest.json"
    }
    foreach ($relativePath in $requiredPackagedFiles) {
        $packagedPath = Join-Path $unpackedResources $relativePath
        if (-not (Test-Path -LiteralPath $packagedPath -PathType Leaf)) {
            throw "required packaged resource is missing: $packagedPath"
        }
    }

    Copy-Item -LiteralPath (Join-Path $releaseRoot "win-unpacked") -Destination $portableRoot -Recurse
    New-Item -ItemType File -Path (Join-Path $portableRoot "portable.marker") -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $repositoryRoot "packaging/PORTABLE-README.txt") -Destination (Join-Path $portableRoot "はじめに.txt")
    Compress-Archive -LiteralPath $portableRoot -DestinationPath $portableZip -CompressionLevel Optimal

    & (Join-Path $PSScriptRoot "smoke-windows-delivery.ps1")
    Write-Host "Installer: $(Join-Path $releaseRoot "Material-Decision-Workbench-Setup-$version.exe")"
    Write-Host "Folder ZIP: $portableZip"
} finally {
    Pop-Location
}
