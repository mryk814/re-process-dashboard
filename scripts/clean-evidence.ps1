param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repositoryPrefix = $repositoryRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
$relativeTargets = @(
    "output",
    "test-results",
    "playwright-report",
    ".playwright-cli",
    "artifacts/main-acceptance"
)

foreach ($relativeTarget in $relativeTargets) {
    $target = [IO.Path]::GetFullPath((Join-Path $repositoryRoot $relativeTarget))
    if (-not $target.StartsWith($repositoryPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "refusing to clean a path outside the repository: $target"
    }
    if (
        $relativeTarget -eq ".dev-workspaces" -or
        $relativeTarget.StartsWith("data/", [StringComparison]::OrdinalIgnoreCase) -or
        $relativeTarget.StartsWith("models/", [StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "refusing to clean protected project data: $relativeTarget"
    }
    if ($DryRun) {
        Write-Host "Would remove: $target"
    } elseif (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
        Write-Host "Removed: $target"
    }
}
