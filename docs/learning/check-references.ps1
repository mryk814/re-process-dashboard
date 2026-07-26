$ErrorActionPreference = "Stop"
$learningRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repositoryRoot = Resolve-Path (Join-Path $learningRoot "..\..")
$referenceFiles = Get-ChildItem -Path $learningRoot -Recurse -File -Include *.qmd,*.md |
    Where-Object {
        $_.FullName -notmatch '[\\/](?:_build|\.quarto)[\\/]'
    }
$missing = [System.Collections.Generic.List[string]]::new()

foreach ($file in $referenceFiles) {
    $content = Get-Content -Raw -LiteralPath $file.FullName
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
            $missing.Add("$($file.FullName): $candidate")
        }
    }
}

if ($missing.Count -gt 0) {
    $missing | Sort-Object -Unique | ForEach-Object { Write-Host $_ -ForegroundColor Red }
    throw "One or more repository references do not exist."
}

Write-Host "All repository path references exist."
