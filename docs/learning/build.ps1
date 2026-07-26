param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$learningRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$buildRoot = Join-Path $learningRoot "_build"
$siteProfile = Join-Path $learningRoot "_quarto-site.yml"
$readerProfile = Join-Path $learningRoot "_quarto-reader.yml"

foreach ($profilePath in @($siteProfile, $readerProfile)) {
    if (-not (Test-Path -LiteralPath $profilePath -PathType Leaf)) {
        throw "Required Quarto profile is missing: $profilePath"
    }
}

if (-not (Get-Command quarto -ErrorAction SilentlyContinue)) {
    throw "Quarto 1.10.18 is required. Install it from https://quarto.org/docs/get-started/."
}
if (-not (Get-Command typst -ErrorAction SilentlyContinue)) {
    throw "Typst 0.15.1 is required. Install it from https://github.com/typst/typst/releases/tag/v0.15.1."
}

$quartoVersion = (quarto --version).Trim()
$typstVersion = (typst --version).Trim()
if ($quartoVersion -ne "1.10.18") {
    throw "Expected Quarto 1.10.18, found $quartoVersion."
}
if ($typstVersion -notmatch '^typst 0\.15\.1(?:\s|$)') {
    throw "Expected Typst 0.15.1, found $typstVersion."
}

$readerConfig = Get-Content -LiteralPath $readerProfile -Raw
$requiredReaderChapters = @(
    "chapters/contract-through-stack.qmd",
    "chapters/source-to-training-evidence.qmd",
    "chapters/revision-and-digest.qmd",
    "chapters/trust-a-migrated-database.qmd",
    "chapters/separate-archive-from-purge.qmd",
    "chapters/restore-workspace-safely.qmd"
)
$maintenanceOnlyChapters = @(
    "foundations.qmd",
    "writer-persona.md",
    "code-map.qmd",
    "learning-paths/backend.qmd",
    "learning-paths/frontend-desktop.qmd",
    "learning-paths/ml-data.qmd",
    "tooling.qmd",
    "evaluation.qmd"
)
foreach ($chapter in $requiredReaderChapters) {
    if ($readerConfig -notmatch [regex]::Escape($chapter)) {
        throw "Reader profile is missing required chapter: $chapter"
    }
}
foreach ($chapter in $maintenanceOnlyChapters) {
    if ($readerConfig -match [regex]::Escape($chapter)) {
        throw "Reader profile contains maintenance-only chapter: $chapter"
    }
}

Push-Location $learningRoot
try {
    if ($Clean -and (Test-Path -LiteralPath $buildRoot)) {
        $resolvedBuildRoot = (Resolve-Path -LiteralPath $buildRoot).Path
        $resolvedLearningRoot = (Resolve-Path -LiteralPath $learningRoot).Path
        if (-not $resolvedBuildRoot.StartsWith(
            $resolvedLearningRoot + [IO.Path]::DirectorySeparatorChar,
            [StringComparison]::OrdinalIgnoreCase
        )) {
            throw "Refusing to remove build directory outside learning root: $resolvedBuildRoot"
        }
        Remove-Item -LiteralPath $resolvedBuildRoot -Recurse -Force
    }

    $siteWatch = [Diagnostics.Stopwatch]::StartNew()
    quarto render --profile site --to html --output-dir _build/site
    if ($LASTEXITCODE -ne 0) {
        throw "Quarto integrated HTML render failed with exit code $LASTEXITCODE."
    }
    $siteWatch.Stop()

    $readerWatch = [Diagnostics.Stopwatch]::StartNew()
    quarto render --profile reader --to typst --output-dir _build/reader
    if ($LASTEXITCODE -ne 0) {
        throw "Quarto reader PDF render failed with exit code $LASTEXITCODE."
    }
    $readerWatch.Stop()
} finally {
    Pop-Location
}

$siteIndex = Join-Path $buildRoot "site\index.html"
$siteSearch = Join-Path $buildRoot "site\search.json"
$readerPdf = Join-Path $buildRoot "reader\material-decision-workbench-reader.pdf"
foreach ($artifact in @($siteIndex, $siteSearch, $readerPdf)) {
    if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
        throw "Expected learning artifact was not generated: $artifact"
    }
}

$searchIndex = Get-Content -LiteralPath $siteSearch -Raw
foreach ($expectedHref in @(
    "chapters/contract-through-stack.html",
    "writer-persona.html",
    "evaluation.html"
)) {
    if ($searchIndex -notmatch [regex]::Escape($expectedHref)) {
        throw "Integrated HTML search index is missing: $expectedHref"
    }
}

Write-Host "Generated an integrated HTML site and a reader-only PDF from shared manuscripts."
Write-Host ("HTML: {0}" -f $siteIndex)
Write-Host ("PDF:  {0}" -f $readerPdf)
Write-Host ("Integrated HTML build: {0:N2} seconds" -f $siteWatch.Elapsed.TotalSeconds)
Write-Host ("Reader PDF build:     {0:N2} seconds" -f $readerWatch.Elapsed.TotalSeconds)
