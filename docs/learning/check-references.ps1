$ErrorActionPreference = "Stop"

$learningRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repositoryRoot = Resolve-Path (Join-Path $learningRoot "..\..")
$bibliographyPath = Join-Path $learningRoot "references.bib"
$annotationsPath = Join-Path $learningRoot "reference-annotations.json"
$errors = [System.Collections.Generic.List[string]]::new()
$warnings = [System.Collections.Generic.List[string]]::new()

function Add-ValidationError {
    param([string]$Code, [string]$Message)
    $errors.Add("ERROR $Code $Message")
}

function Test-NonEmptyValue {
    param($Value)
    if ($null -eq $Value) {
        return $false
    }
    if ($Value -is [string]) {
        return -not [string]::IsNullOrWhiteSpace($Value)
    }
    if ($Value -is [System.Collections.IEnumerable]) {
        return @($Value).Count -gt 0
    }
    return $true
}

$referenceFiles = Get-ChildItem -Path $learningRoot -Recurse -File -Include *.qmd,*.md |
    Where-Object {
        $_.FullName -notmatch '[\\/](?:_build|\.quarto)[\\/]'
    }

foreach ($file in $referenceFiles) {
    $content = Get-Content -Raw -Encoding UTF8 -LiteralPath $file.FullName
    $pathMatches = [regex]::Matches(
        $content,
        '(?<![\w./-])(?:backend|apps|e2e|docs|models|data)/[A-Za-z0-9_./-]+'
    )
    foreach ($pathMatch in $pathMatches) {
        $candidate = $pathMatch.Value.TrimEnd([char[]]@('.', ',', ':', ';', ')', ']', '`'))
        if (
            $candidate -like '*<*' -or
            $candidate -like '*{*' -or
            $candidate -like '*_build/*'
        ) {
            continue
        }
        $resolved = Join-Path $repositoryRoot ($candidate -replace '/', '\')
        if (-not (Test-Path -LiteralPath $resolved)) {
            Add-ValidationError "missing-repo-path" "$($file.FullName): $candidate"
        }
    }
}

$bibContent = Get-Content -Raw -Encoding UTF8 -LiteralPath $bibliographyPath
$bibMatches = [regex]::Matches(
    $bibContent,
    '(?m)^\s*@[A-Za-z]+\s*\{\s*([A-Za-z][A-Za-z0-9-]*)\s*,'
)
$bibKeys = @($bibMatches | ForEach-Object { $_.Groups[1].Value })
$bibKeySet = @{}

foreach ($keyGroup in $bibKeys | Group-Object { $_.ToLowerInvariant() }) {
    if ($keyGroup.Count -gt 1) {
        Add-ValidationError "duplicate-bib-key" ($keyGroup.Group -join ", ")
    }
    $bibKeySet[$keyGroup.Group[0]] = $true
}

if ($bibContent -match '(?i)https?://(?:www\.)?(?:qiita\.com|zenn\.dev)/') {
    Add-ValidationError "disallowed-source" "Qiita or Zenn URL is present in references.bib."
}

$annotationDocument = Get-Content -Raw -Encoding UTF8 -LiteralPath $annotationsPath |
    ConvertFrom-Json
if ($annotationDocument.schema_version -ne 1) {
    Add-ValidationError "annotation-schema" "schema_version must be 1."
}

$allowedRoles = @(
    "prerequisite",
    "primary-explanation",
    "alternative-view",
    "deep-dive",
    "historical-context",
    "implementation-reference",
    "standard",
    "caution"
)
$allowedLevels = @("beginner", "intermediate", "advanced")
$allowedSourceTiers = @(
    "official-documentation",
    "standard",
    "maintainer-publication",
    "specialist-book",
    "official-handbook",
    "organization-book"
)
$allowedOpenAccess = @("full", "partial", "none")
$requiredFields = @(
    "citekey",
    "roles",
    "level",
    "source_tier",
    "related_concepts",
    "repo_routes",
    "recommended_sections",
    "why_read",
    "reading_question",
    "caution",
    "open_access",
    "edition_verified",
    "official_url_checked_on"
)
$annotationKeys = @()

foreach ($entry in @($annotationDocument.entries)) {
    $annotationKeys += [string]$entry.citekey
    foreach ($field in $requiredFields) {
        if (-not (Test-NonEmptyValue $entry.$field)) {
            Add-ValidationError "missing-annotation-field" "$($entry.citekey): $field"
        }
    }

    foreach ($role in @($entry.roles)) {
        if ($allowedRoles -notcontains $role) {
            Add-ValidationError "unknown-role" "$($entry.citekey): $role"
        }
    }
    if (@($entry.roles | Group-Object).Where({ $_.Count -gt 1 }).Count -gt 0) {
        Add-ValidationError "duplicate-role" "$($entry.citekey)"
    }
    if ($allowedLevels -notcontains $entry.level) {
        Add-ValidationError "unknown-level" "$($entry.citekey): $($entry.level)"
    }
    if ($allowedSourceTiers -notcontains $entry.source_tier) {
        Add-ValidationError "unknown-source-tier" "$($entry.citekey): $($entry.source_tier)"
    }
    if ($allowedOpenAccess -notcontains $entry.open_access) {
        Add-ValidationError "unknown-open-access" "$($entry.citekey): $($entry.open_access)"
    }
    if ($entry.edition_verified -ne $true) {
        Add-ValidationError "edition-unverified" "$($entry.citekey)"
    }

    $checkedOn = [datetime]::MinValue
    if (-not [datetime]::TryParseExact(
        [string]$entry.official_url_checked_on,
        "yyyy-MM-dd",
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::None,
        [ref]$checkedOn
    )) {
        Add-ValidationError "invalid-check-date" "$($entry.citekey): $($entry.official_url_checked_on)"
    } elseif ($checkedOn.Date -gt (Get-Date).Date) {
        Add-ValidationError "future-check-date" "$($entry.citekey): $($entry.official_url_checked_on)"
    }

    foreach ($route in @($entry.repo_routes)) {
        if (
            $route -match '(^|/)\.\.(/|$)' -or
            [System.IO.Path]::IsPathRooted($route)
        ) {
            Add-ValidationError "unsafe-repo-route" "$($entry.citekey): $route"
            continue
        }
        $resolvedRoute = Join-Path $repositoryRoot ($route -replace '/', '\')
        if (-not (Test-Path -LiteralPath $resolvedRoute)) {
            Add-ValidationError "missing-repo-route" "$($entry.citekey): $route"
        }
    }
}

foreach ($keyGroup in $annotationKeys | Group-Object { $_.ToLowerInvariant() }) {
    if ($keyGroup.Count -gt 1) {
        Add-ValidationError "duplicate-annotation-key" ($keyGroup.Group -join ", ")
    }
}

$annotationKeySet = @{}
foreach ($key in $annotationKeys) {
    $annotationKeySet[$key] = $true
}

foreach ($key in $bibKeys) {
    if (-not $annotationKeySet.ContainsKey($key)) {
        Add-ValidationError "missing-annotation" $key
    }
}
foreach ($key in $annotationKeys) {
    if (-not $bibKeySet.ContainsKey($key)) {
        Add-ValidationError "missing-bib-entry" $key
    }
}

$citationKeys = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::Ordinal
)
foreach ($file in $referenceFiles) {
    $content = Get-Content -Raw -Encoding UTF8 -LiteralPath $file.FullName
    $citationBlocks = [regex]::Matches($content, '\[[^\]\r\n]*@[^\]\r\n]+\]')
    foreach ($block in $citationBlocks) {
        foreach ($citation in [regex]::Matches(
            $block.Value,
            '@([A-Za-z][A-Za-z0-9-]*)'
        )) {
            $key = $citation.Groups[1].Value
            [void]$citationKeys.Add($key)
            if (-not $bibKeySet.ContainsKey($key)) {
                Add-ValidationError "missing-citation-entry" "$($file.FullName): $key"
            }
        }
    }
}

foreach ($key in $bibKeys) {
    if (-not $citationKeys.Contains($key)) {
        $warnings.Add("WARNING unused-bib-entry $key")
    }
}

$majorChapters = Get-ChildItem -Path (Join-Path $learningRoot "chapters") -File -Filter *.qmd
foreach ($chapter in $majorChapters) {
    $chapterContent = Get-Content -Raw -Encoding UTF8 -LiteralPath $chapter.FullName
    if ($chapterContent -notmatch '(?m)^## Further Reading\s*$') {
        Add-ValidationError "missing-further-reading" $chapter.FullName
    }
}

$warnings | Sort-Object -Unique | ForEach-Object {
    Write-Host $_ -ForegroundColor Yellow
}
$errors | Sort-Object -Unique | ForEach-Object {
    Write-Host $_ -ForegroundColor Red
}

if ($errors.Count -gt 0) {
    throw "Reference validation failed with $($errors.Count) error(s)."
}

Write-Host (
    "Reference validation passed: {0} BibTeX entries, {1} annotations, {2} citations, {3} warning(s)." -f
    $bibKeys.Count,
    $annotationKeys.Count,
    $citationKeys.Count,
    $warnings.Count
)
