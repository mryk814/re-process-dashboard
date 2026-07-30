$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$version = (Get-Content -LiteralPath (Join-Path $repositoryRoot "package.json") -Raw | ConvertFrom-Json).version
$releaseRoot = Join-Path $repositoryRoot "release"
$smokeRoot = Join-Path $releaseRoot "smoke"
$zipPath = Join-Path $releaseRoot "Evidence-Decision-Workbench-folder-$version.zip"
$installerPath = Join-Path $releaseRoot "Evidence-Decision-Workbench-Setup-$version.exe"
$extractedRoot = Join-Path $smokeRoot "extracted"
$installedRoot = Join-Path $smokeRoot "installed"

function Stop-PackagedProcessesUnder {
    param([string]$RootPath)

    if (-not (Test-Path -LiteralPath $RootPath)) {
        return
    }
    $resolvedRoot = [IO.Path]::GetFullPath($RootPath).TrimEnd("\") + "\"
    $matches = @(
        Get-CimInstance Win32_Process |
            Where-Object {
                $_.ExecutablePath -and
                [IO.Path]::GetFullPath($_.ExecutablePath).StartsWith(
                    $resolvedRoot,
                    [StringComparison]::OrdinalIgnoreCase
                )
            }
    )
    foreach ($process in $matches) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if ($matches.Count -gt 0) {
        Wait-Process -Id $matches.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
    }
    $remaining = @(
        Get-CimInstance Win32_Process |
            Where-Object {
                $_.ExecutablePath -and
                [IO.Path]::GetFullPath($_.ExecutablePath).StartsWith(
                    $resolvedRoot,
                    [StringComparison]::OrdinalIgnoreCase
                )
            }
    )
    if ($remaining.Count -gt 0) {
        throw "packaged smoke processes remained under $resolvedRoot"
    }
}

function Remove-SmokeTree {
    param([string]$RootPath)

    for ($attempt = 1; $attempt -le 5; $attempt += 1) {
        if (-not (Test-Path -LiteralPath $RootPath)) {
            return
        }
        try {
            Remove-Item -LiteralPath $RootPath -Recurse -Force -ErrorAction Stop
            return
        } catch {
            if ($attempt -eq 5) {
                throw
            }
            Start-Sleep -Milliseconds 500
        }
    }
}

if (Test-Path -LiteralPath $smokeRoot) {
    Stop-PackagedProcessesUnder $smokeRoot
    Remove-SmokeTree $smokeRoot
}
New-Item -ItemType Directory -Path $smokeRoot | Out-Null

try {
    Expand-Archive -LiteralPath $zipPath -DestinationPath $extractedRoot
    $portableAppRoot = (Get-ChildItem -LiteralPath $extractedRoot -Directory | Select-Object -First 1).FullName
    node (Join-Path $repositoryRoot "scripts/smoke-packaged.mjs") $portableAppRoot portable
    if ($LASTEXITCODE -ne 0) { throw "folder smoke failed with code $LASTEXITCODE" }
    Stop-PackagedProcessesUnder $portableAppRoot

    $installer = Start-Process -FilePath $installerPath -ArgumentList "/S", "/D=$installedRoot" -Wait -PassThru
    if ($installer.ExitCode -ne 0) {
        throw "installer exited with code $($installer.ExitCode)"
    }
    node (Join-Path $repositoryRoot "scripts/smoke-packaged.mjs") $installedRoot installed
    if ($LASTEXITCODE -ne 0) { throw "installed smoke failed with code $LASTEXITCODE" }
    Stop-PackagedProcessesUnder $installedRoot

    # appIdとlegacy user-data pathを維持した状態で上書きinstallし、
    # rename後も既存Workspaceが同じ場所から開けることを確認する。
    $legacyDatabase = Join-Path $smokeRoot "local-app-data/Material Decision Workbench/workbench.db"
    $databaseBeforeUpgrade = (Get-FileHash -LiteralPath $legacyDatabase -Algorithm SHA256).Hash
    $upgrade = Start-Process -FilePath $installerPath -ArgumentList "/S", "/D=$installedRoot" -Wait -PassThru
    if ($upgrade.ExitCode -ne 0) {
        throw "upgrade installer exited with code $($upgrade.ExitCode)"
    }
    $databaseAfterUpgrade = (Get-FileHash -LiteralPath $legacyDatabase -Algorithm SHA256).Hash
    if ($databaseAfterUpgrade -ne $databaseBeforeUpgrade) {
        throw "installer upgrade modified the existing Workspace database"
    }
    node (Join-Path $repositoryRoot "scripts/smoke-packaged-upgrade.mjs") $installedRoot
    if ($LASTEXITCODE -ne 0) { throw "installed upgrade smoke failed with code $LASTEXITCODE" }
    Stop-PackagedProcessesUnder $installedRoot

    $uninstallerPath = Join-Path $installedRoot "Uninstall Evidence Decision Workbench.exe"
    if (-not (Test-Path -LiteralPath $uninstallerPath)) {
        throw "uninstaller was not created: $uninstallerPath"
    }
    $uninstaller = Start-Process -FilePath $uninstallerPath -ArgumentList "/S" -Wait -PassThru
    if ($uninstaller.ExitCode -ne 0) {
        throw "uninstaller exited with code $($uninstaller.ExitCode)"
    }
    if (Test-Path -LiteralPath (Join-Path $installedRoot "Evidence Decision Workbench.exe")) {
        throw "installed executable remained after uninstall"
    }
    $retainedDatabase = $legacyDatabase
    if (-not (Test-Path -LiteralPath $retainedDatabase)) {
        throw "user database was removed by uninstall"
    }

    Write-Host "Folder ZIP extract/run/delete: OK"
    Write-Host "Per-user installer install/upgrade/run/uninstall: OK"
} finally {
    if (Test-Path -LiteralPath $smokeRoot) {
        Stop-PackagedProcessesUnder $smokeRoot
        Remove-SmokeTree $smokeRoot
    }
}
