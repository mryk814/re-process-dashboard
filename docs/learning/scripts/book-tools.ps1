Set-StrictMode -Version 2.0

function Get-BookToolRoot {
    param([string]$ToolRoot)

    if (-not [string]::IsNullOrWhiteSpace($env:MATERIAL_WORKBENCH_BOOK_TOOLS)) {
        throw "MATERIAL_WORKBENCH_BOOK_TOOLS is no longer supported. Use DECISION_WORKBENCH_BOOK_TOOLS."
    }
    if (-not [string]::IsNullOrWhiteSpace($ToolRoot)) {
        return [IO.Path]::GetFullPath($ToolRoot)
    }
    if (-not [string]::IsNullOrWhiteSpace($env:DECISION_WORKBENCH_BOOK_TOOLS)) {
        return [IO.Path]::GetFullPath($env:DECISION_WORKBENCH_BOOK_TOOLS)
    }
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        throw "LOCALAPPDATA is unavailable. Pass -ToolRoot or set DECISION_WORKBENCH_BOOK_TOOLS."
    }
    return [IO.Path]::GetFullPath(
        (Join-Path $env:LOCALAPPDATA "decision-workbench-book-tools")
    )
}

function Read-BookToolLock {
    param([Parameter(Mandatory = $true)][string]$LockFile)

    if (-not (Test-Path -LiteralPath $LockFile -PathType Leaf)) {
        throw "Book tool lock file is missing: $LockFile"
    }
    $lock = Get-Content -LiteralPath $LockFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($lock.schemaVersion -ne 1 -or $lock.platform -ne "windows-x86_64") {
        throw "Unsupported book tool lock schema or platform in $LockFile."
    }
    if (@($lock.tools).Count -eq 0) {
        throw "Book tool lock contains no tools: $LockFile"
    }
    foreach ($tool in @($lock.tools)) {
        $executablePath = ([string]$tool.executable).Replace('/', '\')
        if (
            $tool.name -notmatch '^[a-z0-9-]+$' -or
            [string]::IsNullOrWhiteSpace($tool.version) -or
            [string]::IsNullOrWhiteSpace($tool.url) -or
            $tool.sha256 -notmatch '^[0-9a-fA-F]{64}$' -or
            [int64]$tool.size -le 0 -or
            [string]::IsNullOrWhiteSpace($tool.executable) -or
            [IO.Path]::IsPathRooted($executablePath) -or
            $executablePath -match '(^|\\)\.\.(\\|$)' -or
            [string]::IsNullOrWhiteSpace($tool.versionPattern)
        ) {
            throw "Book tool lock contains an incomplete entry."
        }
        $toolUri = $null
        if (-not (Test-Path -LiteralPath ([string]$tool.url) -PathType Leaf)) {
            if (
                -not [Uri]::TryCreate(
                    [string]$tool.url,
                    [UriKind]::Absolute,
                    [ref]$toolUri
                ) -or
                $toolUri.Scheme -notin @("https", "file")
            ) {
                throw "Book tool lock requires an HTTPS URL: $($tool.url)"
            }
        }
    }
    return $lock
}

function Get-BookToolPaths {
    param(
        [Parameter(Mandatory = $true)]$Tool,
        [Parameter(Mandatory = $true)][string]$ToolRoot,
        [Parameter(Mandatory = $true)][string]$Platform
    )

    $identity = "{0}-{1}" -f $Tool.version, $Tool.sha256.ToLowerInvariant()
    $toolBase = Join-Path $ToolRoot ("tools\{0}\{1}" -f $Tool.name, $Platform)
    $installRoot = Join-Path $toolBase $identity
    [pscustomobject]@{
        ToolBase = $toolBase
        InstallRoot = $installRoot
        Executable = Join-Path $installRoot $Tool.executable
        ReadyMarker = Join-Path $installRoot ".ready.json"
        Archive = Join-Path $ToolRoot (
            "archives\{0}\{1}\{2}.zip" -f $Tool.name, $Platform, $Tool.sha256.ToLowerInvariant()
        )
    }
}

function Assert-PathUnderRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )

    $fullPath = [IO.Path]::GetFullPath($Path)
    $fullRoot = [IO.Path]::GetFullPath($Root).TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
    if (
        -not $fullPath.StartsWith(
            $fullRoot + [IO.Path]::DirectorySeparatorChar,
            [StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "Refusing to modify a path outside the book tool root: $fullPath"
    }

    $pathRoot = [IO.Path]::GetPathRoot($fullPath)
    $currentPath = $pathRoot
    $relativePath = $fullPath.Substring($pathRoot.Length).TrimStart(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
    $segments = @($relativePath -split '[\\/]')
    foreach ($segment in $segments) {
        $currentPath = Join-Path $currentPath $segment
        if (Test-Path -LiteralPath $currentPath) {
            $item = Get-Item -LiteralPath $currentPath -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Book tool paths must not cross a reparse point: $currentPath"
            }
        }
    }
}

function Test-BookToolArchive {
    param(
        [Parameter(Mandatory = $true)][string]$Archive,
        [Parameter(Mandatory = $true)]$Tool
    )

    if (-not (Test-Path -LiteralPath $Archive -PathType Leaf)) {
        return $false
    }
    $item = Get-Item -LiteralPath $Archive
    if ($item.Length -ne [int64]$Tool.size) {
        return $false
    }
    $actualHash = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
    return $actualHash -eq $Tool.sha256.ToLowerInvariant()
}

function Test-BookToolReady {
    param(
        [Parameter(Mandatory = $true)]$Tool,
        [Parameter(Mandatory = $true)]$Paths
    )

    if (
        -not (Test-Path -LiteralPath $Paths.ReadyMarker -PathType Leaf) -or
        -not (Test-Path -LiteralPath $Paths.Executable -PathType Leaf)
    ) {
        return $false
    }
    try {
        $marker = Get-Content -LiteralPath $Paths.ReadyMarker -Raw -Encoding UTF8 |
            ConvertFrom-Json
        if (
            $marker.name -ne $Tool.name -or
            $marker.version -ne $Tool.version -or
            $marker.sha256 -ne $Tool.sha256.ToLowerInvariant()
        ) {
            return $false
        }
        $version = ((& $Paths.Executable --version) | Out-String).Trim()
        return $LASTEXITCODE -eq 0 -and $version -match $Tool.versionPattern
    } catch {
        return $false
    }
}

function Resolve-BookTools {
    param(
        [string]$ToolRoot,
        [string]$LockFile = (Join-Path (Split-Path -Parent $PSScriptRoot) "tools.lock.json")
    )

    $resolvedRoot = Get-BookToolRoot -ToolRoot $ToolRoot
    $lock = Read-BookToolLock -LockFile $LockFile
    $resolved = @{}
    foreach ($tool in @($lock.tools)) {
        $paths = Get-BookToolPaths -Tool $tool -ToolRoot $resolvedRoot -Platform $lock.platform
        if (-not (Test-BookToolReady -Tool $tool -Paths $paths)) {
            throw ((
                "Book tool {0} {1} is not ready under {2}. Run " +
                "docs/learning/scripts/bootstrap-book-tools.ps1 first."
            ) -f
                $tool.name, $tool.version, $resolvedRoot
            )
        }
        $resolved[$tool.name] = $paths.Executable
    }
    return [pscustomobject]@{
        Root = $resolvedRoot
        Lock = $lock
        Executables = $resolved
    }
}
