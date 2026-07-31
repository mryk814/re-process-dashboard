param(
    [switch]$DryRun,
    [switch]$ReleaseOnly
)

$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repositoryPrefix = $repositoryRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
$relativeTargets = if ($ReleaseOnly) {
    @("release")
} else {
    @("release", "dist", "build")
}

foreach ($relativeTarget in $relativeTargets) {
    $target = [IO.Path]::GetFullPath((Join-Path $repositoryRoot $relativeTarget))
    if (-not $target.StartsWith($repositoryPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "refusing to clean a path outside the repository: $target"
    }
    if ($relativeTarget -in @("data", "models")) {
        throw "refusing to clean protected project data: $relativeTarget"
    }
    if ($DryRun) {
        Write-Host "Would remove: $target"
    } elseif (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
        Write-Host "Removed: $target"
    }
}

if (-not $ReleaseOnly) {
    $backendScriptsRoot = [IO.Path]::GetFullPath(
        (Join-Path $repositoryRoot "backend/scripts")
    )
    if (-not $backendScriptsRoot.StartsWith($repositoryPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "refusing to inspect a scripts path outside the repository: $backendScriptsRoot"
    }
    if (Test-Path -LiteralPath $backendScriptsRoot) {
        $cacheDirectories = @(
            Get-ChildItem -LiteralPath $backendScriptsRoot -Directory -Filter "__pycache__" -Recurse |
                Sort-Object { $_.FullName.Length } -Descending
        )
        foreach ($cacheDirectory in $cacheDirectories) {
            $cachePath = [IO.Path]::GetFullPath($cacheDirectory.FullName)
            $cachePrefix = $backendScriptsRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
            if (-not $cachePath.StartsWith($cachePrefix, [StringComparison]::OrdinalIgnoreCase)) {
                throw "refusing to clean a cache outside backend/scripts: $cachePath"
            }
            if ($cacheDirectory.Attributes.HasFlag([IO.FileAttributes]::ReparsePoint)) {
                throw "refusing to clean a cache through a reparse point: $cachePath"
            }
            if ($DryRun) {
                Write-Host "Would remove Python cache: $cachePath"
            } else {
                Remove-Item -LiteralPath $cachePath -Recurse -Force
                Write-Host "Removed Python cache: $cachePath"
            }
        }
    }
}
