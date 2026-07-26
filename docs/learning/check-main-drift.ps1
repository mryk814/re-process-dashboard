param(
    [string]$Against = "origin/main"
)

$ErrorActionPreference = "Stop"
$learningRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repositoryRoot = Resolve-Path (Join-Path $learningRoot "..\..")

Push-Location $repositoryRoot
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

    $chapterFiles = Get-ChildItem -Path $learningRoot -Recurse -Filter *.qmd
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
            $diff = git diff --name-status "$verifiedCommit..$Against" -- $reference
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

    if ($driftFound) {
        Write-Host ""
        Write-Host "Review the changed references before updating verified_commit." -ForegroundColor Yellow
        exit 2
    }

    Write-Host "No referenced implementation drift was found."
} finally {
    Pop-Location
}
