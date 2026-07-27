param(
    [switch]$Clean,
    [string]$ToolRoot
)

$ErrorActionPreference = "Stop"
$learningRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$buildRoot = Join-Path $learningRoot "_build"
$siteProfile = Join-Path $learningRoot "_quarto-site.yml"
$readerProfile = Join-Path $learningRoot "_quarto-reader.yml"
$referenceCheck = Join-Path $learningRoot "check-references.ps1"
$exerciseCheck = Join-Path $learningRoot "check-exercise-solutions.ps1"
$codeReferenceCheck = Join-Path $learningRoot "check-code-references.mjs"
$driftReviewCheck = Join-Path $learningRoot "check-drift-reviews.mjs"
$reviewCheck = Join-Path $learningRoot "reviews\check-reviews.mjs"
$conceptTest = Join-Path $learningRoot "test-concepts.mjs"
$conceptOrderCheck = Join-Path $learningRoot "check-concept-order.mjs"
$conceptOrderTest = Join-Path $learningRoot "test-concept-order.mjs"
$figureCheck = Join-Path $learningRoot "check-figures.mjs"
$figureTest = Join-Path $learningRoot "test-figures.mjs"
$labCheck = Join-Path $learningRoot "check-labs.mjs"
$labTest = Join-Path $learningRoot "test-labs.mjs"
$toolLibrary = Join-Path $learningRoot "scripts\book-tools.ps1"
$toolLock = Join-Path $learningRoot "tools.lock.json"

foreach ($profilePath in @(
    $siteProfile,
    $readerProfile,
    $referenceCheck,
    $exerciseCheck,
    $codeReferenceCheck,
    $driftReviewCheck,
    $reviewCheck,
    $conceptTest,
    $conceptOrderCheck,
    $conceptOrderTest,
    $figureCheck,
    $figureTest,
    $labCheck,
    $labTest,
    $toolLibrary,
    $toolLock
)) {
    if (-not (Test-Path -LiteralPath $profilePath -PathType Leaf)) {
        throw "Required learning build input is missing: $profilePath"
    }
}

. $toolLibrary
$bookTools = Resolve-BookTools -ToolRoot $ToolRoot -LockFile $toolLock
$quartoPath = [string]$bookTools.Executables["quarto"]
$typstPath = [string]$bookTools.Executables["typst"]
$quartoTool = @($bookTools.Lock.tools | Where-Object { $_.name -eq "quarto" })
$typstTool = @($bookTools.Lock.tools | Where-Object { $_.name -eq "typst" })
if ($quartoTool.Count -ne 1 -or $typstTool.Count -ne 1) {
    throw "Book tool lock must contain exactly one quarto and one typst entry."
}
$quartoTool = $quartoTool[0]
$typstTool = $typstTool[0]

$quartoVersion = ((& $quartoPath --version) | Out-String).Trim()
$quartoVersionExit = $LASTEXITCODE
$typstVersion = ((& $typstPath --version) | Out-String).Trim()
$typstVersionExit = $LASTEXITCODE
if ($quartoVersionExit -ne 0 -or $quartoVersion -notmatch $quartoTool.versionPattern) {
    throw "Expected locked Quarto $($quartoTool.version), found '$quartoVersion'."
}
if ($typstVersionExit -ne 0 -or $typstVersion -notmatch $typstTool.versionPattern) {
    throw "Expected locked Typst $($typstTool.version), found '$typstVersion'."
}

$previousQuartoTypst = $env:QUARTO_TYPST
$env:QUARTO_TYPST = $typstPath
try {
    $rendererProcessInfo = New-Object Diagnostics.ProcessStartInfo
    $rendererProcessInfo.FileName = $quartoPath
    $rendererProcessInfo.Arguments = "typst --version"
    $rendererProcessInfo.UseShellExecute = $false
    $rendererProcessInfo.CreateNoWindow = $true
    $rendererProcessInfo.RedirectStandardOutput = $true
    $rendererProcessInfo.RedirectStandardError = $true
    $rendererProcess = [Diagnostics.Process]::Start($rendererProcessInfo)
    $rendererStdout = $rendererProcess.StandardOutput.ReadToEnd()
    $rendererStderr = $rendererProcess.StandardError.ReadToEnd()
    $rendererProcess.WaitForExit()
    $rendererVersionExit = $rendererProcess.ExitCode
    $rendererVersion = ($rendererStdout + $rendererStderr).Trim()
    if (
        $rendererVersionExit -ne 0 -or
        $rendererVersion -notmatch $typstTool.versionPattern
    ) {
        throw (
            "Quarto did not resolve the locked Typst {0} renderer: '{1}'." -f
            $typstTool.version, $rendererVersion
        )
    }
} finally {
    if ($null -eq $previousQuartoTypst) {
        Remove-Item Env:QUARTO_TYPST -ErrorAction SilentlyContinue
    } else {
        $env:QUARTO_TYPST = $previousQuartoTypst
    }
}

$readerConfig = Get-Content -LiteralPath $readerProfile -Raw -Encoding UTF8
$requiredReaderChapters = @(
    "chapters/system-map.qmd",
    "chapters/contract-through-stack.qmd",
    "chapters/source-to-training-evidence.qmd",
    "chapters/materials-domain-primer.qmd",
    "chapters/revision-and-digest.qmd",
    "chapters/trust-a-migrated-database.qmd",
    "chapters/separate-archive-from-purge.qmd",
    "chapters/restore-workspace-safely.qmd",
    "chapters/model-package-runtime.qmd",
    "chapters/prediction-calibration-support.qmd",
    "chapters/design-space-acquisition.qmd",
    "chapters/multi-stage-chain.qmd",
    "chapters/decision-safety.qmd",
    "chapters/frontend-desktop-state.qmd",
    "chapters/performance-execution.qmd",
    "chapters/security-trust-boundaries.qmd",
    "chapters/verification-operations.qmd",
    "chapters/implementation-case-studies.qmd"
)
$maintenanceOnlyChapters = @(
    "foundations.qmd",
    "edition-1.qmd",
    "math-style-guide.qmd",
    "writer-persona.md",
    "code-map.qmd",
    "learning-paths/backend.qmd",
    "learning-paths/frontend-desktop.qmd",
    "learning-paths/ml-data.qmd",
    "tooling.qmd",
    "drift-reviews/index.qmd",
    "reviews/index.qmd",
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

& $referenceCheck
& $exerciseCheck
node $conceptTest
if ($LASTEXITCODE -ne 0) {
    throw "Concept validation failed with exit code $LASTEXITCODE."
}
node $conceptOrderTest
if ($LASTEXITCODE -ne 0) {
    throw "Concept order fixture tests failed with exit code $LASTEXITCODE."
}
node $conceptOrderCheck
if ($LASTEXITCODE -ne 0) {
    throw "Concept order validation failed with exit code $LASTEXITCODE."
}
node $figureTest
if ($LASTEXITCODE -ne 0) {
    throw "Figure fixture tests failed with exit code $LASTEXITCODE."
}
node $figureCheck
if ($LASTEXITCODE -ne 0) {
    throw "Figure validation failed with exit code $LASTEXITCODE."
}
node $labTest
if ($LASTEXITCODE -ne 0) {
    throw "Lab fixture tests failed with exit code $LASTEXITCODE."
}
node $labCheck
if ($LASTEXITCODE -ne 0) {
    throw "Lab validation failed with exit code $LASTEXITCODE."
}
node $codeReferenceCheck --write-manifest
if ($LASTEXITCODE -ne 0) {
    throw "Code reference validation failed with exit code $LASTEXITCODE."
}
node $driftReviewCheck
if ($LASTEXITCODE -ne 0) {
    throw "Drift review validation failed with exit code $LASTEXITCODE."
}
node $reviewCheck
if ($LASTEXITCODE -ne 0) {
    throw "Acceptance review validation failed with exit code $LASTEXITCODE."
}

Push-Location $learningRoot
$previousQuartoTypst = $env:QUARTO_TYPST
$env:QUARTO_TYPST = $typstPath
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
    & $quartoPath render --profile site --to html --output-dir _build/site
    if ($LASTEXITCODE -ne 0) {
        throw "Quarto integrated HTML render failed with exit code $LASTEXITCODE."
    }
    $siteWatch.Stop()

    $readerWatch = [Diagnostics.Stopwatch]::StartNew()
    & $quartoPath render --profile reader --to typst --output-dir _build/reader
    if ($LASTEXITCODE -ne 0) {
        throw "Quarto reader PDF render failed with exit code $LASTEXITCODE."
    }
    $readerWatch.Stop()
} finally {
    if ($null -eq $previousQuartoTypst) {
        Remove-Item Env:QUARTO_TYPST -ErrorAction SilentlyContinue
    } else {
        $env:QUARTO_TYPST = $previousQuartoTypst
    }
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

$searchIndex = Get-Content -LiteralPath $siteSearch -Raw -Encoding UTF8
foreach ($expectedHref in @(
    "chapters/contract-through-stack.html",
    "chapters/decision-safety.html",
    "concept-map.html",
    "glossary.html",
    "writer-persona.html",
    "drift-reviews/index.html",
    "evaluation.html"
)) {
    if ($searchIndex -notmatch [regex]::Escape($expectedHref)) {
        throw "Integrated HTML search index is missing: $expectedHref"
    }
}

$exerciseDocumentRoots = @(
    (Join-Path $learningRoot "labs"),
    (Join-Path $learningRoot "chapters")
)
$expectedSolutionCount = (
    $exerciseDocumentRoots |
        ForEach-Object { Get-ChildItem -LiteralPath $_ -Filter "*.qmd" -File } |
        Select-String -Pattern '^:::\s+\{#answer-[a-z0-9-]+\s+\.exercise-solution(?:\s|})'
).Count
$siteHtml = (
    Get-ChildItem -LiteralPath (Join-Path $buildRoot "site") -Filter "*.html" -File -Recurse |
        Get-Content -Raw -Encoding UTF8
) -join "`n"
$renderedSolutions = [regex]::Matches(
    $siteHtml,
    '<details\s+id="answer-[^"]+"\s+class="exercise-solution"\s+data-answer-content(?:="")?>'
).Count
$renderedSummaries = [regex]::Matches(
    $siteHtml,
    '<summary>解答例を見る</summary>'
).Count
$initiallyOpenSolutions = [regex]::Matches(
    $siteHtml,
    '<details\b[^>]*\bopen(?:\s|=|>)'
).Count
$expectedCodeReferencePath = Join-Path $learningRoot (
    "_extensions\code-reference\generated-expected-links.json"
)
$expectedCodeReferenceUrls = [string[]](
    Get-Content -LiteralPath $expectedCodeReferencePath -Raw -Encoding UTF8 |
        ConvertFrom-Json
)
$codeReferenceTags = [regex]::Matches(
    $siteHtml,
    '<a\b(?=[^>]*\bclass="[^"]*\bcode-reference\b)[^>]*>'
)
$renderedCodeReferenceUrls = @(
    foreach ($tag in $codeReferenceTags) {
        $href = [regex]::Match($tag.Value, '\bhref="([^"]+)"')
        if (-not $href.Success) {
            throw "Rendered code reference is missing href: $($tag.Value)"
        }
        $href.Groups[1].Value
    }
)

if ($renderedSolutions -ne $expectedSolutionCount) {
    throw (
        "Expected {0} HTML solution disclosures, found {1}." -f `
            $expectedSolutionCount, $renderedSolutions
    )
}
if ($renderedSummaries -ne $expectedSolutionCount) {
    throw (
        "Expected {0} fixed solution summaries, found {1}." -f `
            $expectedSolutionCount, $renderedSummaries
    )
}
if ($initiallyOpenSolutions -ne 0) {
    throw "HTML contains $initiallyOpenSolutions solution disclosure(s) that are initially open."
}
if ($renderedCodeReferenceUrls.Count -ne $expectedCodeReferenceUrls.Count) {
    throw (
        "Expected {0} rendered code reference links, found {1}." -f `
            $expectedCodeReferenceUrls.Count, $renderedCodeReferenceUrls.Count
    )
}
$linkDifference = Compare-Object `
    ($expectedCodeReferenceUrls | Sort-Object) `
    ($renderedCodeReferenceUrls | Sort-Object)
if ($linkDifference) {
    throw (
        "Rendered code reference hrefs differ from the verified metadata:`n{0}" -f `
            ($linkDifference | Out-String)
    )
}

Write-Host "Generated an integrated HTML site and a reader-only PDF from shared manuscripts."
Write-Host ("HTML: {0}" -f $siteIndex)
Write-Host ("PDF:  {0}" -f $readerPdf)
Write-Host ("Exercise disclosures: {0}, all initially closed." -f $renderedSolutions)
Write-Host (
    "Verified code links: {0}, all matched exact metadata hrefs." -f `
        $renderedCodeReferenceUrls.Count
)
Write-Host ("Integrated HTML build: {0:N2} seconds" -f $siteWatch.Elapsed.TotalSeconds)
Write-Host ("Reader PDF build:     {0:N2} seconds" -f $readerWatch.Elapsed.TotalSeconds)
Write-Host ("Tool root:            {0}" -f $bookTools.Root)
