param(
    [int]$TimeoutSeconds = 20
)

$ErrorActionPreference = "Stop"
$learningRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$bibliographyPath = Join-Path $learningRoot "references.bib"
$bibContent = Get-Content -Raw -Encoding UTF8 -LiteralPath $bibliographyPath
$entryMatches = [regex]::Matches(
    $bibContent,
    '(?ms)^\s*@[A-Za-z]+\s*\{\s*([A-Za-z][A-Za-z0-9-]*)\s*,(.*?)(?=^\s*@[A-Za-z]+\s*\{|\z)'
)
$failures = [System.Collections.Generic.List[string]]::new()
$checked = 0

foreach ($entryMatch in $entryMatches) {
    $citekey = $entryMatch.Groups[1].Value
    $body = $entryMatch.Groups[2].Value
    $urlMatch = [regex]::Match($body, '(?m)^\s*url\s*=\s*\{([^}]+)\}')
    if (-not $urlMatch.Success) {
        $failures.Add("$citekey has no URL.")
        continue
    }

    $url = $urlMatch.Groups[1].Value
    if ($url -notmatch '^https://') {
        $failures.Add("$citekey does not use HTTPS: $url")
        continue
    }

    $response = $null
    try {
        $response = Invoke-WebRequest `
            -Uri $url `
            -Method Head `
            -MaximumRedirection 8 `
            -TimeoutSec $TimeoutSeconds `
            -UseBasicParsing
    } catch {
        try {
            $response = Invoke-WebRequest `
                -Uri $url `
                -Method Get `
                -MaximumRedirection 8 `
                -TimeoutSec $TimeoutSeconds `
                -UseBasicParsing
        } catch {
            $failures.Add("$citekey could not be reached: $url ($($_.Exception.Message))")
            continue
        }
    }

    $statusCode = [int]$response.StatusCode
    if ($statusCode -lt 200 -or $statusCode -ge 400) {
        $failures.Add("$citekey returned HTTP ${statusCode}: $url")
        continue
    }
    $checked += 1
    Write-Host "OK $citekey -> HTTP $statusCode"
}

if ($failures.Count -gt 0) {
    $failures | ForEach-Object { Write-Host "ERROR $_" -ForegroundColor Red }
    throw "Reference URL check failed with $($failures.Count) error(s)."
}

Write-Host "Reference URL check passed for $checked URL(s)."
