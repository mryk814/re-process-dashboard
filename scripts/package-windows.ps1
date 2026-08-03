param(
    [switch]$KeepPrevious
)

$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$version = (Get-Content -LiteralPath (Join-Path $repositoryRoot "package.json") -Raw | ConvertFrom-Json).version
$releaseRoot = Join-Path $repositoryRoot "release"
$portableRoot = Join-Path $releaseRoot "Evidence-Decision-Workbench-folder"
$portableZip = Join-Path $releaseRoot "Evidence-Decision-Workbench-folder-$version.zip"

Push-Location $repositoryRoot
try {
    if (-not $KeepPrevious) {
        & (Join-Path $PSScriptRoot "clean-generated.ps1") -ReleaseOnly
    }

    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "application build failed with exit code $LASTEXITCODE" }

    uv run --extra dev python -m PyInstaller --noconfirm --clean --distpath dist/sidecar --workpath build/sidecar packaging/sidecar.spec
    if ($LASTEXITCODE -ne 0) { throw "sidecar build failed with exit code $LASTEXITCODE" }

    $releasePrefix = $releaseRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    $staleArtifacts = @(
        (Join-Path $releaseRoot "win-unpacked")
        (Join-Path $releaseRoot "Evidence-Decision-Workbench-Setup-$version.exe")
        (Join-Path $releaseRoot "Evidence-Decision-Workbench-Setup-$version.exe.blockmap")
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
        "sidecar/decision-workbench-sidecar.exe"
        "docs/contracts/capability-atlas.json"
        "models/active-packages.json"
        "models/available-packages.json"
        "models/active-transforms.json"
        "models/evaluations/welding-consumable-a-b-c-v1.json"
        "data/source/welding_consumable_multistage_synthetic_dataset.xlsx"
    )
    $sourceInventoryJson = uv run python backend/scripts/operations/task_inventory.py --print-source-paths
    if ($LASTEXITCODE -ne 0) { throw "source inventory failed with exit code $LASTEXITCODE" }
    $requiredPackagedFiles += $sourceInventoryJson | ConvertFrom-Json
    $activePackages = Get-Content -LiteralPath (Join-Path $repositoryRoot "models/active-packages.json") -Raw | ConvertFrom-Json
    $requiredPackagedFiles += $activePackages.tasks.PSObject.Properties.Value | ForEach-Object {
        "models/$($_.active)/manifest.json"
    }
    $availablePackages = Get-Content -LiteralPath (Join-Path $repositoryRoot "models/available-packages.json") -Raw | ConvertFrom-Json
    $requiredPackagedFiles += $availablePackages.packages | ForEach-Object {
        "models/$($_)/manifest.json"
    }
    $activeTransforms = Get-Content -LiteralPath (Join-Path $repositoryRoot "models/active-transforms.json") -Raw | ConvertFrom-Json
    $requiredPackagedFiles += $activeTransforms.transforms.PSObject.Properties.Value | ForEach-Object {
        "models/$($_.active)/manifest.json"
        "models/$($_.commercial_catalog)"
        "models/$($_.design_space)"
        $_.available | ForEach-Object { "models/$($_)/manifest.json" }
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
    foreach ($expandedDirectory in @(
        (Join-Path $releaseRoot "win-unpacked"),
        $portableRoot
    )) {
        if (Test-Path -LiteralPath $expandedDirectory) {
            Remove-Item -LiteralPath $expandedDirectory -Recurse -Force
        }
    }
    Write-Host "Installer: $(Join-Path $releaseRoot "Evidence-Decision-Workbench-Setup-$version.exe")"
    Write-Host "Folder ZIP: $portableZip"
} finally {
    Pop-Location
}
