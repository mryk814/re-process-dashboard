param(
    [switch]$KeepSmokeOnFailure,
    [string]$PreviousInstallerPath,
    [string]$PreviousExecutableName = "Material Decision Workbench.exe",
    [switch]$AllowUserInstallerState
)

$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$version = (Get-Content -LiteralPath (Join-Path $repositoryRoot "package.json") -Raw | ConvertFrom-Json).version
$releaseRoot = Join-Path $repositoryRoot "release"
$smokeRoot = Join-Path $releaseRoot "smoke"
$zipPath = Join-Path $releaseRoot "Evidence-Decision-Workbench-folder-$version.zip"
$installerPath = Join-Path $releaseRoot "Evidence-Decision-Workbench-Setup-$version.exe"
$extractedRoot = Join-Path $smokeRoot "extracted"
$installedRoot = Join-Path $smokeRoot "installed"
$workspaceDatabasePath = Join-Path $smokeRoot "local-app-data/Material Decision Workbench/workbench.db"

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

function Test-SmokeShortcutOwned {
    param(
        [string]$ShortcutPath,
        [string]$OwnedRoot
    )

    if (-not (Test-Path -LiteralPath $ShortcutPath)) {
        return $false
    }
    $shell = New-Object -ComObject WScript.Shell
    $targetPath = $shell.CreateShortcut($ShortcutPath).TargetPath
    if (-not $targetPath) {
        return $false
    }
    $resolvedRoot = [IO.Path]::GetFullPath($OwnedRoot).TrimEnd("\") + "\"
    return [IO.Path]::GetFullPath($targetPath).StartsWith(
        $resolvedRoot,
        [StringComparison]::OrdinalIgnoreCase
    )
}

function Remove-SmokeInstallation {
    param([string]$InstalledRootPath)

    Stop-PackagedProcessesUnder $InstalledRootPath
    foreach ($uninstallerName in @(
        "Uninstall Evidence Decision Workbench.exe",
        "Uninstall Material Decision Workbench.exe"
    )) {
        $uninstallerPath = Join-Path $InstalledRootPath $uninstallerName
        if (Test-Path -LiteralPath $uninstallerPath) {
            $uninstaller = Start-Process -FilePath $uninstallerPath -ArgumentList "/S" -Wait -PassThru
            if ($uninstaller.ExitCode -ne 0) {
                Write-Warning "smoke cleanup uninstaller exited with code $($uninstaller.ExitCode): $uninstallerPath"
            }
            break
        }
    }
    foreach ($shortcutPath in @(
        (Join-Path ([Environment]::GetFolderPath("Desktop")) "Evidence Decision Workbench.lnk"),
        (Join-Path ([Environment]::GetFolderPath("Programs")) "Evidence Decision Workbench.lnk"),
        (Join-Path ([Environment]::GetFolderPath("Desktop")) "Material Decision Workbench.lnk"),
        (Join-Path ([Environment]::GetFolderPath("Programs")) "Material Decision Workbench.lnk")
    )) {
        if (Test-SmokeShortcutOwned -ShortcutPath $shortcutPath -OwnedRoot $InstalledRootPath) {
            Remove-Item -LiteralPath $shortcutPath -Force
        }
    }
}

function Assert-NoNonSmokeInstallerState {
    param([string]$OwnedRoot)

    foreach ($shortcutPath in @(
        (Join-Path ([Environment]::GetFolderPath("Desktop")) "Evidence Decision Workbench.lnk"),
        (Join-Path ([Environment]::GetFolderPath("Programs")) "Evidence Decision Workbench.lnk"),
        (Join-Path ([Environment]::GetFolderPath("Desktop")) "Material Decision Workbench.lnk"),
        (Join-Path ([Environment]::GetFolderPath("Programs")) "Material Decision Workbench.lnk")
    )) {
        if ((Test-Path -LiteralPath $shortcutPath) -and
            -not (Test-SmokeShortcutOwned -ShortcutPath $shortcutPath -OwnedRoot $OwnedRoot)) {
            throw "refusing to replace a non-smoke shortcut: $shortcutPath"
        }
    }

    $resolvedRoot = [IO.Path]::GetFullPath($OwnedRoot).TrimEnd("\")
    foreach ($registryRoot in @(
        "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKCU:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )) {
        foreach ($entry in @(Get-ItemProperty -Path $registryRoot -ErrorAction SilentlyContinue)) {
            if ($entry.DisplayName -notin @("Evidence Decision Workbench", "Material Decision Workbench")) {
                continue
            }
            $locationEvidence = @($entry.InstallLocation, $entry.UninstallString) |
                Where-Object { $_ } |
                Select-Object -First 1
            if (-not $locationEvidence -or
                $locationEvidence.ToString().IndexOf(
                    $resolvedRoot,
                    [StringComparison]::OrdinalIgnoreCase
                ) -lt 0) {
                throw "refusing to replace a non-smoke installer registration: $($entry.PSPath)"
            }
        }
    }
}

if ($PreviousInstallerPath -and -not $AllowUserInstallerState) {
    throw "-PreviousInstallerPath updates the current user's HKCU installer registration and shortcuts. Re-run only in a disposable Windows user or VM with -AllowUserInstallerState."
}
Assert-NoNonSmokeInstallerState -OwnedRoot $installedRoot

if (Test-Path -LiteralPath $smokeRoot) {
    Stop-PackagedProcessesUnder $smokeRoot
    Remove-SmokeTree $smokeRoot
}
New-Item -ItemType Directory -Path $smokeRoot | Out-Null

$completed = $false
try {
    Expand-Archive -LiteralPath $zipPath -DestinationPath $extractedRoot
    $portableAppRoot = (Get-ChildItem -LiteralPath $extractedRoot -Directory | Select-Object -First 1).FullName
    node (Join-Path $repositoryRoot "scripts/smoke-packaged.mjs") $portableAppRoot portable
    if ($LASTEXITCODE -ne 0) { throw "folder smoke failed with code $LASTEXITCODE" }
    Stop-PackagedProcessesUnder $portableAppRoot

    $initialInstallerPath = if ($PreviousInstallerPath) {
        (Resolve-Path -LiteralPath $PreviousInstallerPath).Path
    } else {
        $installerPath
    }
    $initialExecutableName = if ($PreviousInstallerPath) {
        $PreviousExecutableName
    } else {
        "Evidence Decision Workbench.exe"
    }
    $installer = Start-Process -FilePath $initialInstallerPath -ArgumentList "/S", "/D=$installedRoot" -Wait -PassThru
    if ($installer.ExitCode -ne 0) {
        throw "installer exited with code $($installer.ExitCode)"
    }
    node (Join-Path $repositoryRoot "scripts/smoke-packaged.mjs") $installedRoot installed $initialExecutableName
    if ($LASTEXITCODE -ne 0) { throw "installed smoke failed with code $LASTEXITCODE" }
    Stop-PackagedProcessesUnder $installedRoot

    # appIdとlegacy user-data pathを維持した状態で上書きinstallし、
    # rename後も既存Workspaceが同じ場所から開けることを確認する。
    & (Join-Path $PSScriptRoot "smoke-windows-upgrade.ps1") -InstallerPath $installerPath -InstalledRoot $installedRoot -WorkspaceDatabasePath $workspaceDatabasePath
    Stop-PackagedProcessesUnder $installedRoot
    $replacesRetiredDisplayArtifacts =
        $PreviousInstallerPath -and
        $PreviousExecutableName -ne "Evidence Decision Workbench.exe"
    if ($replacesRetiredDisplayArtifacts) {
        $legacyShortcutPaths = @(
            (Join-Path ([Environment]::GetFolderPath("Desktop")) "Material Decision Workbench.lnk"),
            (Join-Path ([Environment]::GetFolderPath("Programs")) "Material Decision Workbench.lnk")
        )
        foreach ($legacyArtifact in @(
            "Material Decision Workbench.exe",
            "Uninstall Material Decision Workbench.exe"
        )) {
            if (Test-Path -LiteralPath (Join-Path $installedRoot $legacyArtifact)) {
                throw "legacy installed artifact remained after upgrade: $legacyArtifact"
            }
        }
        foreach ($legacyShortcutPath in $legacyShortcutPaths) {
            if (Test-SmokeShortcutOwned -ShortcutPath $legacyShortcutPath -OwnedRoot $installedRoot) {
                throw "legacy shortcut remained after upgrade: $legacyShortcutPath"
            }
        }
    }

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
    if (-not (Test-Path -LiteralPath $workspaceDatabasePath)) {
        throw "user database was removed by uninstall"
    }

    Write-Host "Folder ZIP extract/run/delete: OK"
    if ($PreviousInstallerPath) {
        Write-Host "Legacy installer to renamed installer upgrade/run/uninstall: OK"
    } else {
        Write-Host "Per-user installer install/reinstall/run/uninstall: OK"
    }
    $completed = $true
} finally {
    if (Test-Path -LiteralPath $smokeRoot) {
        Remove-SmokeInstallation $installedRoot
        Stop-PackagedProcessesUnder $smokeRoot
        if ($completed -or -not $KeepSmokeOnFailure) {
            Remove-SmokeTree $smokeRoot
        } else {
            Write-Host "Smoke Workspace retained for focused diagnosis: $smokeRoot"
        }
    }
}
