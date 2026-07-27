$ErrorActionPreference = "Stop"

$learningRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$expectedExerciseDocuments = [ordered]@{
    "labs\extend-contract.qmd" = "contract"
    "labs\trace-source-evidence.qmd" = "source"
    "labs\trace-revision-conflict.qmd" = "revision"
    "labs\break-and-repair-migration.qmd" = "migration"
    "labs\trace-project-lifecycle.qmd" = "project-lifecycle"
    "labs\trace-workspace-restore.qmd" = "workspace"
    "chapters\system-map.qmd" = "system-map"
    "chapters\decision-safety.qmd" = "decision-safety"
    "chapters\materials-domain-primer.qmd" = "materials-domain-primer"
    "chapters\model-package-runtime.qmd" = "model-package-runtime"
    "chapters\prediction-calibration-support.qmd" = "prediction-math"
    "chapters\design-space-acquisition.qmd" = "acquisition"
    "chapters\multi-stage-chain.qmd" = "multi-stage-chain"
    "chapters\frontend-desktop-state.qmd" = "frontend-desktop-state"
    "chapters\verification-operations.qmd" = "verification"
    "chapters\security-trust-boundaries.qmd" = "security-trust-boundaries"
    "chapters\performance-execution.qmd" = "performance"
}

$errors = New-Object System.Collections.Generic.List[string]
$allExerciseIds = @{}
$allAnswerIds = @{}
$exerciseCount = 0
$answerCount = 0
$chapterCheckCount = 0

function Add-ValidationError {
    param([string]$Message)
    $script:errors.Add($Message)
}

foreach ($entry in $expectedExerciseDocuments.GetEnumerator()) {
    $documentPath = Join-Path $learningRoot $entry.Key
    $expectedSlug = $entry.Value
    if (-not (Test-Path -LiteralPath $documentPath -PathType Leaf)) {
        Add-ValidationError "Missing exercise document: $documentPath"
        continue
    }

    $lines = Get-Content -LiteralPath $documentPath -Encoding UTF8
    $raw = $lines -join "`n"
    $exerciseSections = New-Object System.Collections.Generic.List[object]
    $answerIdsInFile = New-Object System.Collections.Generic.List[string]
    $answerPositionsInFile = @{}
    $solutionDepth = 0
    $currentAnswer = $null
    $requiredHeadings = $null

    for ($lineIndex = 0; $lineIndex -lt $lines.Count; $lineIndex++) {
        $line = $lines[$lineIndex]
        $lineNumber = $lineIndex + 1
        if ($solutionDepth -eq 0 -and $line -match '^#{2,3}\s+(?:演習|checkpoint)\s*([0-9]+)[^{}]*\{#(exercise-([a-z0-9-]+)-([0-9]{2}))\}\s*$') {
            $displayNumber = [int]$Matches[1]
            $exerciseId = $Matches[2]
            $slug = $Matches[3]
            $idNumber = [int]$Matches[4]
            if ($slug -ne $expectedSlug) {
                Add-ValidationError "${documentPath}:${lineNumber}: expected exercise slug '$expectedSlug', found '$slug'"
            }
            if ($displayNumber -ne $idNumber) {
                Add-ValidationError "${documentPath}:${lineNumber}: display number $displayNumber does not match ID number $idNumber"
            }
            if ($allExerciseIds.ContainsKey($exerciseId)) {
                Add-ValidationError "${documentPath}:${lineNumber}: duplicate exercise ID '$exerciseId'"
            } else {
                $allExerciseIds[$exerciseId] = "${documentPath}:${lineNumber}"
            }
            $exerciseSections.Add([pscustomobject]@{
                Id = $exerciseId
                Start = $lineIndex
                LineNumber = $lineNumber
            })
            $exerciseCount++
        }

        if ($solutionDepth -eq 0 -and $line -match '^:::\s+\{#(answer-([a-z0-9-]+)-([0-9]{2}|chapter-check))\s+\.exercise-solution\s+data-label="([^"]+)"\}\s*$') {
            $answerId = $Matches[1]
            $slug = $Matches[2]
            $suffix = $Matches[3]
            if ($slug -ne $expectedSlug) {
                Add-ValidationError "${documentPath}:${lineNumber}: expected answer slug '$expectedSlug', found '$slug'"
            }
            if ($allAnswerIds.ContainsKey($answerId)) {
                Add-ValidationError "${documentPath}:${lineNumber}: duplicate answer ID '$answerId'"
            } else {
                $allAnswerIds[$answerId] = "${documentPath}:${lineNumber}"
            }
            $answerIdsInFile.Add($answerId)
            $answerPositionsInFile[$answerId] = $lineIndex
            $answerCount++
            if ($suffix -eq "chapter-check") {
                $chapterCheckCount++
            }
            $currentAnswer = $answerId
            $requiredHeadings = @{
                "解答例" = $false
                "解答の理由" = $false
                "よくある不十分な回答" = $false
            }
            $solutionDepth = 1
            continue
        }

        if ($solutionDepth -gt 0) {
            if ($line -match '^####\s+(解答例|解答の理由|よくある不十分な回答)\s*$') {
                $requiredHeadings[$Matches[1]] = $true
            }
            if ($line -match '^:::+\s+\{') {
                $solutionDepth++
            } elseif ($line -match '^:::+\s*$') {
                $solutionDepth--
                if ($solutionDepth -eq 0) {
                    foreach ($heading in $requiredHeadings.Keys) {
                        if (-not $requiredHeadings[$heading]) {
                            Add-ValidationError "${documentPath}:${lineNumber}: answer '$currentAnswer' is missing '#### $heading'"
                        }
                    }
                    $currentAnswer = $null
                    $requiredHeadings = $null
                }
            }
        } elseif ($line -match '^###\s+解答例\s*$') {
            Add-ValidationError "${documentPath}:${lineNumber}: immediate '### 解答例' remains outside an exercise-solution block"
        }

        if ($solutionDepth -eq 0 -and $line -match '^##\s+章末チェックの解答\s*$') {
            Add-ValidationError "${documentPath}:${lineNumber}: chapter check answers must use an exercise-solution block"
        }
    }

    if ($solutionDepth -ne 0) {
        Add-ValidationError "${documentPath}: unclosed exercise-solution block '$currentAnswer'"
    }

    for ($sectionIndex = 0; $sectionIndex -lt $exerciseSections.Count; $sectionIndex++) {
        $section = $exerciseSections[$sectionIndex]
        $endIndex = if ($sectionIndex + 1 -lt $exerciseSections.Count) {
            $exerciseSections[$sectionIndex + 1].Start - 1
        } else {
            $lines.Count - 1
        }
        $expectedAnswerId = $section.Id -replace '^exercise-', 'answer-'
        if ($expectedAnswerId -notin $answerIdsInFile) {
            Add-ValidationError "${documentPath}:$($section.LineNumber): exercise '$($section.Id)' has no matching answer '$expectedAnswerId'"
        }

        $problemEndIndex = $endIndex
        if (
            $answerPositionsInFile.ContainsKey($expectedAnswerId) -and
            $answerPositionsInFile[$expectedAnswerId] -gt $section.Start -and
            $answerPositionsInFile[$expectedAnswerId] -le $endIndex
        ) {
            $problemEndIndex = $answerPositionsInFile[$expectedAnswerId] - 1
        }
        $problemLines = New-Object System.Collections.Generic.List[string]
        $embeddedSolutionDepth = 0
        for ($problemIndex = $section.Start; $problemIndex -le $problemEndIndex; $problemIndex++) {
            $problemLine = $lines[$problemIndex]
            if (
                $embeddedSolutionDepth -eq 0 -and
                $problemLine -match '^:::\s+\{#answer-[a-z0-9-]+-(?:[0-9]{2}|chapter-check)\s+\.exercise-solution\b'
            ) {
                $embeddedSolutionDepth = 1
                continue
            }
            if ($embeddedSolutionDepth -gt 0) {
                if ($problemLine -match '^:::+\s+\{') {
                    $embeddedSolutionDepth++
                } elseif ($problemLine -match '^:::+\s*$') {
                    $embeddedSolutionDepth--
                }
                continue
            }
            $problemLines.Add($problemLine)
        }
        $problemRaw = $problemLines -join "`n"

        if (-not ($problemLines -match '^#{3,4}\s+成功条件\s*$')) {
            Add-ValidationError "${documentPath}:$($section.LineNumber): exercise '$($section.Id)' is missing a success-criteria heading before its answer"
        }
        if ($problemRaw -notmatch [regex]::Escape("](#$expectedAnswerId)")) {
            Add-ValidationError "${documentPath}:$($section.LineNumber): exercise '$($section.Id)' has no problem-side link to '#$expectedAnswerId'"
        }
        if ($problemLines -match '^#{1,6}\s+(解答例|解答の理由|よくある不十分な回答)\s*$') {
            Add-ValidationError "${documentPath}:$($section.LineNumber): exercise '$($section.Id)' leaks an answer heading into the problem"
        }
    }

    $expectedChapterCheckId = "answer-$expectedSlug-chapter-check"
    if ($expectedChapterCheckId -notin $answerIdsInFile) {
        Add-ValidationError (
            "${documentPath}: missing chapter check answer '$expectedChapterCheckId'; found: " +
                ($answerIdsInFile -join ", ")
        )
    }
    if ($raw -notmatch [regex]::Escape("](#$expectedChapterCheckId)")) {
        Add-ValidationError "${documentPath}: missing link to chapter check answer '#$expectedChapterCheckId'"
    }
}

$chapterChecks = [ordered]@{
    "chapters\contract-through-stack.qmd" = "../labs/extend-contract.qmd#answer-contract-chapter-check"
    "chapters\source-to-training-evidence.qmd" = "../labs/trace-source-evidence.qmd#answer-source-chapter-check"
    "chapters\revision-and-digest.qmd" = "../labs/trace-revision-conflict.qmd#answer-revision-chapter-check"
    "chapters\trust-a-migrated-database.qmd" = "../labs/break-and-repair-migration.qmd#answer-migration-chapter-check"
    "chapters\separate-archive-from-purge.qmd" = "../labs/trace-project-lifecycle.qmd#answer-project-lifecycle-chapter-check"
    "chapters\restore-workspace-safely.qmd" = "../labs/trace-workspace-restore.qmd#answer-workspace-chapter-check"
    "chapters\system-map.qmd" = "#answer-system-map-chapter-check"
    "chapters\decision-safety.qmd" = "#answer-decision-safety-chapter-check"
    "chapters\materials-domain-primer.qmd" = "#answer-materials-domain-primer-chapter-check"
    "chapters\model-package-runtime.qmd" = "#answer-model-package-runtime-chapter-check"
    "chapters\prediction-calibration-support.qmd" = "#answer-prediction-math-chapter-check"
    "chapters\design-space-acquisition.qmd" = "#answer-acquisition-chapter-check"
    "chapters\multi-stage-chain.qmd" = "#answer-multi-stage-chain-chapter-check"
    "chapters\frontend-desktop-state.qmd" = "#answer-frontend-desktop-state-chapter-check"
    "chapters\verification-operations.qmd" = "#answer-verification-chapter-check"
    "chapters\security-trust-boundaries.qmd" = "#answer-security-trust-boundaries-chapter-check"
    "chapters\performance-execution.qmd" = "#answer-performance-chapter-check"
}

foreach ($entry in $chapterChecks.GetEnumerator()) {
    $chapterPath = Join-Path $learningRoot $entry.Key
    $lines = Get-Content -LiteralPath $chapterPath -Encoding UTF8
    $chapterCheckStart = -1
    for ($lineIndex = 0; $lineIndex -lt $lines.Count; $lineIndex++) {
        if ($lines[$lineIndex] -match '^##\s+章末チェック(?:\s+\{#[^}]+\})?\s*$') {
            $chapterCheckStart = $lineIndex
            break
        }
    }
    if ($chapterCheckStart -lt 0) {
        Add-ValidationError "${chapterPath}: missing '## 章末チェック'"
        continue
    }

    $chapterCheckEnd = $lines.Count - 1
    for ($lineIndex = $chapterCheckStart + 1; $lineIndex -lt $lines.Count; $lineIndex++) {
        if ($lines[$lineIndex] -match '^##\s+') {
            $chapterCheckEnd = $lineIndex - 1
            break
        }
    }
    $chapterCheckRaw = $lines[$chapterCheckStart..$chapterCheckEnd] -join "`n"
    $expectedTarget = $entry.Value
    if ($chapterCheckRaw -notmatch [regex]::Escape("]($expectedTarget)")) {
        Add-ValidationError (
            "${chapterPath}: chapter-check problem has no direct link to " +
                "'$expectedTarget'"
        )
    }
}

$siteProfile = Get-Content -LiteralPath (Join-Path $learningRoot "_quarto-site.yml") -Raw -Encoding UTF8
$readerProfile = Get-Content -LiteralPath (Join-Path $learningRoot "_quarto-reader.yml") -Raw -Encoding UTF8
if ($siteProfile -notmatch '(?m)^solution-placement:\s+inline-disclosure\s*$') {
    Add-ValidationError "_quarto-site.yml must set solution-placement: inline-disclosure"
}
if ($readerProfile -notmatch '(?m)^solution-placement:\s+answer-chapter\s*$') {
    Add-ValidationError "_quarto-reader.yml must set solution-placement: answer-chapter"
}

$filterPath = Join-Path $learningRoot "filters\exercise-solutions.lua"
if (-not (Test-Path -LiteralPath $filterPath -PathType Leaf)) {
    Add-ValidationError "Missing exercise solution filter: $filterPath"
}

if ($answerCount -ne ($exerciseCount + $chapterCheckCount)) {
    Add-ValidationError (
        "Expected one answer per exercise plus one chapter check per lab; " +
            "found $exerciseCount exercises, $answerCount answers, and " +
            "$chapterCheckCount chapter checks."
    )
}

if ($errors.Count -gt 0) {
    $errors | ForEach-Object { Write-Error $_ }
    throw "Exercise solution validation failed with $($errors.Count) error(s)."
}

Write-Host (
    "Exercise solution validation passed: {0} exercises, {1} solution blocks, {2} chapter checks." -f `
        $exerciseCount, $answerCount, $chapterCheckCount
)
