param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$learningRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repositoryRoot = Resolve-Path (Join-Path $learningRoot "..\..")

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

Push-Location $learningRoot
try {
    if ($Clean -and (Test-Path "_build")) {
        Remove-Item -LiteralPath (Resolve-Path "_build") -Recurse -Force
    }
    quarto render --to html --output-dir _build/html
    if ($LASTEXITCODE -ne 0) {
        throw "Quarto HTML render failed with exit code $LASTEXITCODE."
    }
    quarto render --to typst --output-dir _build/typst
    if ($LASTEXITCODE -ne 0) {
        throw "Quarto Typst render failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}

Write-Host "Generated HTML and PDF from the same Quarto manuscript."
Write-Host "HTML: $learningRoot\_build\html\index.html"
Write-Host "PDF:  $learningRoot\_build\typst\material-decision-workbench-learning.pdf"
