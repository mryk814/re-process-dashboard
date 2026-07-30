param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPath,
    [Parameter(Mandatory = $true)]
    [string]$InstalledRoot,
    [Parameter(Mandatory = $true)]
    [string]$WorkspaceDatabasePath
)

$ErrorActionPreference = "Stop"

function Get-Sha256Hex {
    param(
        [Parameter(Mandatory = $true, Position = 0)]
        [string]$FilePath
    )

    $stream = [IO.File]::OpenRead($FilePath)
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($sha256.ComputeHash($stream)).Replace("-", "")
    } finally {
        $sha256.Dispose()
        $stream.Dispose()
    }
}

$resolvedInstaller = (Resolve-Path -LiteralPath $InstallerPath).Path
$resolvedInstalledRoot = [IO.Path]::GetFullPath($InstalledRoot)
$resolvedWorkspaceDatabase = (Resolve-Path -LiteralPath $WorkspaceDatabasePath).Path
$databaseBeforeUpgrade = Get-Sha256Hex $resolvedWorkspaceDatabase

# appIdのupdate identityから既存install locationを解決できること自体が
# rename upgradeの契約なので、ここでは /D を指定しない。
$upgrade = Start-Process `
    -FilePath $resolvedInstaller `
    -ArgumentList "/S" `
    -Wait `
    -PassThru
if ($upgrade.ExitCode -ne 0) {
    throw "upgrade installer exited with code $($upgrade.ExitCode)"
}
if (-not (Test-Path -LiteralPath (Join-Path $resolvedInstalledRoot "Evidence Decision Workbench.exe"))) {
    throw "upgrade installer did not inherit the existing install location"
}

$databaseAfterUpgrade = Get-Sha256Hex $resolvedWorkspaceDatabase
if ($databaseAfterUpgrade -ne $databaseBeforeUpgrade) {
    throw "installer upgrade modified the existing Workspace database"
}

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
node (Join-Path $repositoryRoot "scripts/smoke-packaged-upgrade.mjs") $resolvedInstalledRoot
if ($LASTEXITCODE -ne 0) {
    throw "installed upgrade smoke failed with code $LASTEXITCODE"
}

Write-Host "Installer upgrade kept the legacy Workspace database: OK"
