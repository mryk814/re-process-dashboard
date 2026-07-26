param(
    [string]$ToolRoot,
    [string]$LockFile = (Join-Path (Split-Path -Parent $PSScriptRoot) "tools.lock.json"),
    [switch]$Offline,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0
. (Join-Path $PSScriptRoot "book-tools.ps1")

function Enter-BootstrapLock {
    param([Parameter(Mandatory = $true)][string]$Root)

    New-Item -ItemType Directory -Force -Path $Root | Out-Null
    $lockPath = Join-Path $Root ".bootstrap.lock"
    try {
        return [IO.File]::Open(
            $lockPath,
            [IO.FileMode]::OpenOrCreate,
            [IO.FileAccess]::ReadWrite,
            [IO.FileShare]::None
        )
    } catch {
        throw "Another book tool bootstrap is already using $Root."
    }
}

function Assert-SafeZip {
    param([Parameter(Mandatory = $true)][string]$Archive)

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
    try {
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName.Replace('/', '\')
            $unixFileType = (($entry.ExternalAttributes -shr 16) -band 0xF000)
            if (
                [IO.Path]::IsPathRooted($name) -or
                $name -match '(^|\\)\.\.(\\|$)' -or
                $name -match ':' -or
                $unixFileType -eq 0xA000
            ) {
                throw "Archive contains an unsafe path: $($entry.FullName)"
            }
        }
    } finally {
        $zip.Dispose()
    }
}

function Copy-OrDownloadArchive {
    param(
        [Parameter(Mandatory = $true)]$Tool,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    $sourceUri = $null
    if ([Uri]::TryCreate([string]$Tool.url, [UriKind]::Absolute, [ref]$sourceUri)) {
        if ($sourceUri.Scheme -eq "file") {
            Copy-Item -LiteralPath $sourceUri.LocalPath -Destination $Destination
            return
        }
        if ($sourceUri.Scheme -in @("https", "http")) {
            Invoke-WebRequest -Uri $sourceUri.AbsoluteUri -OutFile $Destination -UseBasicParsing
            return
        }
    }
    if (Test-Path -LiteralPath ([string]$Tool.url) -PathType Leaf) {
        Copy-Item -LiteralPath ([string]$Tool.url) -Destination $Destination
        return
    }
    throw "Unsupported or missing archive source for $($Tool.name): $($Tool.url)"
}

function Install-BookTool {
    param(
        [Parameter(Mandatory = $true)]$Tool,
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Platform
    )

    $paths = Get-BookToolPaths -Tool $Tool -ToolRoot $Root -Platform $Platform
    if ((Test-BookToolReady -Tool $Tool -Paths $paths) -and -not $Force) {
        Write-Host ("Reusing {0} {1}: {2}" -f $Tool.name, $Tool.version, $paths.Executable)
        return
    }

    $archiveParent = Split-Path -Parent $paths.Archive
    Assert-PathUnderRoot -Path $archiveParent -Root $Root
    New-Item -ItemType Directory -Force -Path $archiveParent | Out-Null
    $archiveIsValid = Test-BookToolArchive -Archive $paths.Archive -Tool $Tool
    $shouldDownload = -not $archiveIsValid -or ($Force -and -not $Offline)
    if ($shouldDownload) {
        if ((Test-Path -LiteralPath $paths.Archive) -and -not $archiveIsValid) {
            Assert-PathUnderRoot -Path $paths.Archive -Root $Root
            Remove-Item -LiteralPath $paths.Archive -Force
        }
        if ($Offline) {
            throw ((
                "Offline bootstrap cannot find a verified archive for {0} {1}. " +
                "Expected SHA256 {2} at {3}."
            ) -f
                $Tool.name, $Tool.version, $Tool.sha256, $paths.Archive
            )
        }
        $download = Join-Path $archiveParent (
            ".{0}.{1}.partial.zip" -f $Tool.sha256, [guid]::NewGuid().ToString("N")
        )
        try {
            Copy-OrDownloadArchive -Tool $Tool -Destination $download
            if (-not (Test-BookToolArchive -Archive $download -Tool $Tool)) {
                throw ((
                    "Downloaded archive failed size or SHA256 validation for {0} {1}. " +
                    "Expected {2}."
                ) -f $Tool.name, $Tool.version, $Tool.sha256
                )
            }
            if (Test-Path -LiteralPath $paths.Archive) {
                Remove-Item -LiteralPath $download -Force
            } else {
                Move-Item -LiteralPath $download -Destination $paths.Archive
            }
        } finally {
            if (Test-Path -LiteralPath $download) {
                Assert-PathUnderRoot -Path $download -Root $Root
                Remove-Item -LiteralPath $download -Force
            }
        }
    }

    Assert-SafeZip -Archive $paths.Archive
    Assert-PathUnderRoot -Path $paths.ToolBase -Root $Root
    New-Item -ItemType Directory -Force -Path $paths.ToolBase | Out-Null
    Get-ChildItem -LiteralPath $paths.ToolBase -Directory -Filter ".staging-*" |
        ForEach-Object {
            Assert-PathUnderRoot -Path $_.FullName -Root $Root
            Remove-Item -LiteralPath $_.FullName -Recurse -Force
        }
    $stage = Join-Path $paths.ToolBase (
        ".staging-{0}" -f [guid]::NewGuid().ToString("N")
    )
    try {
        Expand-Archive -LiteralPath $paths.Archive -DestinationPath $stage
        $stageExecutable = Join-Path $stage $Tool.executable
        if (-not (Test-Path -LiteralPath $stageExecutable -PathType Leaf)) {
            throw "Archive for $($Tool.name) is missing $($Tool.executable)."
        }
        $version = ((& $stageExecutable --version) | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or $version -notmatch $Tool.versionPattern) {
            throw (
                "Archive for {0} returned unexpected version '{1}'." -f
                $Tool.name, $version
            )
        }
        @{
            name = [string]$Tool.name
            version = [string]$Tool.version
            sha256 = $Tool.sha256.ToLowerInvariant()
            platform = $Platform
        } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $stage ".ready.json") -Encoding UTF8

        if (Test-BookToolReady -Tool $Tool -Paths $paths) {
            Write-Host (
                "Revalidated {0} {1}: {2}" -f
                $Tool.name, $Tool.version, $paths.Executable
            )
            return
        }
        if (Test-Path -LiteralPath $paths.InstallRoot) {
            Assert-PathUnderRoot -Path $paths.InstallRoot -Root $Root
            Remove-Item -LiteralPath $paths.InstallRoot -Recurse -Force
        }
        Move-Item -LiteralPath $stage -Destination $paths.InstallRoot
        $stage = $null
        Write-Host ("Ready {0} {1}: {2}" -f $Tool.name, $Tool.version, $paths.Executable)
    } finally {
        if ($stage -and (Test-Path -LiteralPath $stage)) {
            Assert-PathUnderRoot -Path $stage -Root $Root
            Remove-Item -LiteralPath $stage -Recurse -Force
        }
    }
}

$resolvedRoot = Get-BookToolRoot -ToolRoot $ToolRoot
$lock = Read-BookToolLock -LockFile $LockFile
$rootBoundaryProbe = Join-Path $resolvedRoot ".boundary-probe"
Assert-PathUnderRoot -Path $rootBoundaryProbe -Root $resolvedRoot
$bootstrapLock = Enter-BootstrapLock -Root $resolvedRoot
try {
    foreach ($tool in @($lock.tools)) {
        Install-BookTool -Tool $tool -Root $resolvedRoot -Platform $lock.platform
    }
} finally {
    $bootstrapLock.Dispose()
}

$resolved = Resolve-BookTools -ToolRoot $resolvedRoot -LockFile $LockFile
Write-Host "Book tools are ready without changing the persistent PATH."
Write-Host ("Tool root: {0}" -f $resolved.Root)
