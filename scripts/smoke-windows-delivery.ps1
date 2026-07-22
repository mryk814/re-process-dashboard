$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$version = (Get-Content -LiteralPath (Join-Path $repositoryRoot "package.json") -Raw | ConvertFrom-Json).version
$releaseRoot = Join-Path $repositoryRoot "release"
$smokeRoot = Join-Path $releaseRoot "smoke"
$zipPath = Join-Path $releaseRoot "Material-Decision-Workbench-folder-$version.zip"
$installerPath = Join-Path $releaseRoot "Material-Decision-Workbench-Setup-$version.exe"
$extractedRoot = Join-Path $smokeRoot "extracted"
$installedRoot = Join-Path $smokeRoot "installed"

if (Test-Path -LiteralPath $smokeRoot) {
    Remove-Item -LiteralPath $smokeRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $smokeRoot | Out-Null

try {
    Expand-Archive -LiteralPath $zipPath -DestinationPath $extractedRoot
    $portableAppRoot = (Get-ChildItem -LiteralPath $extractedRoot -Directory | Select-Object -First 1).FullName
    node (Join-Path $repositoryRoot "scripts/smoke-packaged.mjs") $portableAppRoot portable
    if ($LASTEXITCODE -ne 0) { throw "folder smoke failed with code $LASTEXITCODE" }

    $installer = Start-Process -FilePath $installerPath -ArgumentList "/S", "/D=$installedRoot" -Wait -PassThru
    if ($installer.ExitCode -ne 0) {
        throw "installer exited with code $($installer.ExitCode)"
    }
    node (Join-Path $repositoryRoot "scripts/smoke-packaged.mjs") $installedRoot installed
    if ($LASTEXITCODE -ne 0) { throw "installed smoke failed with code $LASTEXITCODE" }

    $uninstallerPath = Join-Path $installedRoot "Uninstall Material Decision Workbench.exe"
    if (-not (Test-Path -LiteralPath $uninstallerPath)) {
        throw "uninstaller was not created: $uninstallerPath"
    }
    $uninstaller = Start-Process -FilePath $uninstallerPath -ArgumentList "/S" -Wait -PassThru
    if ($uninstaller.ExitCode -ne 0) {
        throw "uninstaller exited with code $($uninstaller.ExitCode)"
    }
    if (Test-Path -LiteralPath (Join-Path $installedRoot "Material Decision Workbench.exe")) {
        throw "installed executable remained after uninstall"
    }
    $retainedDatabase = Join-Path $smokeRoot "local-app-data/Material Decision Workbench/workbench.db"
    if (-not (Test-Path -LiteralPath $retainedDatabase)) {
        throw "user database was removed by uninstall"
    }

    Write-Host "Folder ZIP extract/run/delete: OK"
    Write-Host "Per-user installer install/run/uninstall: OK"
} finally {
    if (Test-Path -LiteralPath $smokeRoot) {
        Remove-Item -LiteralPath $smokeRoot -Recurse -Force
    }
}
