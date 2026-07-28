param(
    [string]$Against = "origin/main",
    [string]$LearningRoot,
    [string]$RepositoryRoot
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($LearningRoot)) {
    $LearningRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}
$LearningRoot = (Resolve-Path -LiteralPath $LearningRoot).Path
if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    $RepositoryRoot = Resolve-Path (Join-Path $LearningRoot "..\..")
}
$RepositoryRoot = (Resolve-Path -LiteralPath $RepositoryRoot).Path

function Assert-RepositoryRelativePath {
    param(
        [Parameter(Mandatory = $true)][string]$Reference,
        [Parameter(Mandatory = $true)][string]$ChapterPath
    )

    $segments = $Reference -split "/"
    if (
        [string]::IsNullOrWhiteSpace($Reference) -or
        [IO.Path]::IsPathRooted($Reference) -or
        $Reference.Contains("\") -or
        $Reference.StartsWith(":") -or
        $segments -contains "" -or
        $segments -contains "." -or
        $segments -contains ".."
    ) {
        throw "Invalid code reference path in ${ChapterPath}: $Reference"
    }

    $resolvedReference = [IO.Path]::GetFullPath(
        (Join-Path $RepositoryRoot $Reference)
    )
    $repositoryPrefix = $RepositoryRoot + [IO.Path]::DirectorySeparatorChar
    if (
        -not $resolvedReference.StartsWith(
            $repositoryPrefix,
            [StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "Code reference escapes repository root in ${ChapterPath}: $Reference"
    }
}

function Resolve-ComparisonRevision {
    param(
        [Parameter(Mandatory = $true)][string]$VerifiedCommit,
        [Parameter(Mandatory = $true)][string]$PreferredRevision
    )

    git merge-base --is-ancestor $VerifiedCommit $PreferredRevision
    if ($LASTEXITCODE -eq 0) {
        return $PreferredRevision
    }
    git merge-base --is-ancestor $VerifiedCommit HEAD
    if ($LASTEXITCODE -eq 0) {
        return "HEAD"
    }
    throw (
        "verified commit is not an ancestor of {0} or HEAD: {1}" -f `
            $PreferredRevision, $VerifiedCommit
    )
}

Push-Location $RepositoryRoot
try {
    git rev-parse --verify "$Against^{commit}" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Comparison revision does not exist: $Against"
    }

    $divergence = git rev-list --left-right --count "$Against...HEAD"
    $counts = $divergence -split '\s+'
    Write-Host (
        "Branch divergence: {0}-only={1}, HEAD-only={2}" -f `
            $Against, $counts[0], $counts[1]
    )

    $chapterFiles = Get-ChildItem -LiteralPath $LearningRoot -Recurse -Filter *.qmd
    $driftFound = $false

    foreach ($chapter in $chapterFiles) {
        $content = Get-Content -Raw -LiteralPath $chapter.FullName
        $commitMatch = [regex]::Match(
            $content,
            '(?m)^verified_commit:\s*"([0-9a-f]{40})"\s*$'
        )
        if (-not $commitMatch.Success) {
            continue
        }

        $verifiedCommit = $commitMatch.Groups[1].Value
        git cat-file -e "$verifiedCommit^{commit}"
        if ($LASTEXITCODE -ne 0) {
            throw "Unknown verified_commit in $($chapter.FullName): $verifiedCommit"
        }
        $comparisonRevision = Resolve-ComparisonRevision `
            -VerifiedCommit $verifiedCommit `
            -PreferredRevision $Against

        $references = [System.Collections.Generic.List[string]]::new()
        $insideReferences = $false
        foreach ($line in ($content -split '\r?\n')) {
            if ($line -eq "code_references:") {
                $insideReferences = $true
                continue
            }
            if (-not $insideReferences) {
                continue
            }
            if ($line -match '^[^\s]') {
                break
            }
            if ($line -match '^  - path:\s*"([^"]+)"\s*$') {
                $references.Add($Matches[1])
            }
            if ($line -match '^  -\s+"') {
                throw "Legacy string code_reference in $($chapter.FullName)"
            }
        }
        if ($references.Count -eq 0) {
            throw "No structured code_references in $($chapter.FullName)"
        }

        $changed = [System.Collections.Generic.List[string]]::new()
        foreach ($reference in $references) {
            Assert-RepositoryRelativePath `
                -Reference $reference `
                -ChapterPath $chapter.FullName
            $literalPathspec = ":(literal)$reference"
            $diff = git diff `
                --name-status `
                "$verifiedCommit..$comparisonRevision" `
                -- `
                $literalPathspec
            if ($LASTEXITCODE -ne 0) {
                throw (
                    "git diff failed for code reference in {0}: {1}" -f `
                        $chapter.FullName, $reference
                )
            }
            if ($diff) {
                $changed.AddRange([string[]]$diff)
            }
        }

        if ($changed.Count -gt 0) {
            $driftFound = $true
            $relativeChapter = Resolve-Path -Relative -LiteralPath $chapter.FullName
            Write-Host ""
            Write-Host "Implementation drift: $relativeChapter" -ForegroundColor Yellow
            Write-Host "Verified at: $verifiedCommit"
            $changed | Sort-Object -Unique | ForEach-Object { Write-Host "  $_" }
        }
    }

    $figureRegistryPath = Join-Path $LearningRoot "figures\registry.json"
    if (Test-Path -LiteralPath $figureRegistryPath) {
        $figureRegistry = [System.IO.File]::ReadAllText(
            $figureRegistryPath,
            [System.Text.Encoding]::UTF8
        ) | ConvertFrom-Json
        foreach ($figure in $figureRegistry.figures) {
            $verifiedCommit = [string]$figure.verified_commit
            git cat-file -e "$verifiedCommit^{commit}"
            if ($LASTEXITCODE -ne 0) {
                throw (
                    "Unknown verified_commit for figure {0}: {1}" -f `
                        $figure.id, $verifiedCommit
                )
            }
            if (-not $figure.drift_refs -or $figure.drift_refs.Count -eq 0) {
                throw "No drift_refs for figure $($figure.id)"
            }
            $comparisonRevision = Resolve-ComparisonRevision `
                -VerifiedCommit $verifiedCommit `
                -PreferredRevision $Against

            $changed = [System.Collections.Generic.List[string]]::new()
            foreach ($reference in $figure.drift_refs) {
                Assert-RepositoryRelativePath `
                    -Reference $reference `
                    -ChapterPath "figure $($figure.id)"
                $literalPathspec = ":(literal)$reference"
                $diff = git diff `
                    --name-status `
                    "$verifiedCommit..$comparisonRevision" `
                    -- `
                    $literalPathspec
                if ($LASTEXITCODE -ne 0) {
                    throw (
                        "git diff failed for figure {0}: {1}" -f `
                            $figure.id, $reference
                    )
                }
                if ($diff) {
                    $changed.AddRange([string[]]$diff)
                }
            }

            if ($changed.Count -gt 0) {
                $driftFound = $true
                Write-Host ""
                Write-Host (
                    "Figure implementation drift: {0}" -f $figure.id
                ) -ForegroundColor Yellow
                Write-Host "Verified at: $verifiedCommit"
                $changed |
                    Sort-Object -Unique |
                    ForEach-Object { Write-Host "  $_" }
            }
        }
    }

    if ($driftFound) {
        Write-Host ""
        Write-Host "Review the changed references before updating verified_commit." -ForegroundColor Yellow
        exit 2
    }

    Write-Host "No referenced implementation drift was found."
} finally {
    Pop-Location
}
