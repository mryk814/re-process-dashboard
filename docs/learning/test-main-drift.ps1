param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$checkScript = Join-Path $PSScriptRoot "check-main-drift.ps1"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$testRoot = Join-Path $env:TEMP (
    "material-workbench-drift-test-{0}" -f [guid]::NewGuid().ToString("N")
)
$prParent = "894ecdc47af5d40a764219cfd8b831afda8050f0"
$prMerge = "ae175ed425e6de190161dea280136f7e0dd1d1d9"
$changedReference = (
    "apps/web/src/features/workbench/decisionActivities/" +
    "CandidateDifferenceActivityView.tsx"
)
$unchangedReference = (
    "backend/src/material_workbench/contracts/" +
    "decision_activity_contracts.py"
)

function Assert-True {
    param(
        [Parameter(Mandatory = $true)][bool]$Condition,
        [Parameter(Mandatory = $true)][string]$Message
    )
    if (-not $Condition) {
        throw $Message
    }
}

function Write-TestChapter {
    param(
        [Parameter(Mandatory = $true)][string]$VerifiedCommit,
        [Parameter(Mandatory = $true)][string]$Reference
    )
    @"
---
verified_commit: "$VerifiedCommit"
code_references:
  - path: "$Reference"
    role: "frontend"
---

# Drift fixture
"@ | Set-Content -LiteralPath (Join-Path $testRoot "fixture.qmd") -Encoding ASCII
}

function Invoke-DriftCheck {
    param(
        [Parameter(Mandatory = $true)][int]$ExpectedExitCode,
        [Parameter(Mandatory = $true)][string]$ExpectedText
    )
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = (
            & powershell.exe -NoProfile -ExecutionPolicy Bypass `
                -File $checkScript `
                -Against $prMerge `
                -LearningRoot $testRoot `
                -RepositoryRoot $repositoryRoot 2>&1 |
                Out-String
        ).Trim()
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    Assert-True (
        $exitCode -eq $ExpectedExitCode
    ) "Expected exit $ExpectedExitCode, found $exitCode.`n$output"
    Assert-True (
        $output -match [regex]::Escape($ExpectedText)
    ) "Expected output '$ExpectedText'.`n$output"
}

New-Item -ItemType Directory -Force -Path $testRoot | Out-Null
try {
    Write-TestChapter -VerifiedCommit $prParent -Reference $changedReference
    Invoke-DriftCheck -ExpectedExitCode 2 -ExpectedText $changedReference

    Write-TestChapter -VerifiedCommit $prParent -Reference $unchangedReference
    Invoke-DriftCheck -ExpectedExitCode 0 -ExpectedText "No referenced implementation drift"

    Write-TestChapter -VerifiedCommit $prMerge -Reference $changedReference
    Invoke-DriftCheck -ExpectedExitCode 0 -ExpectedText "No referenced implementation drift"

    Write-TestChapter -VerifiedCommit $prParent -Reference "../outside.ts"
    Invoke-DriftCheck -ExpectedExitCode 1 -ExpectedText "Invalid code reference path"

    Write-TestChapter -VerifiedCommit $prParent -Reference ":(glob)**"
    Invoke-DriftCheck -ExpectedExitCode 1 -ExpectedText "Invalid code reference path"

    Write-Host (
        "Main drift tests passed: PR #283 reference detected, " +
        "unreferenced change ignored, reviewed commit clean, " +
        "unsafe paths rejected."
    )
} finally {
    if (Test-Path -LiteralPath $testRoot) {
        $resolvedTestRoot = (Resolve-Path -LiteralPath $testRoot).Path
        $resolvedTemp = (Resolve-Path -LiteralPath $env:TEMP).Path
        if (
            -not $resolvedTestRoot.StartsWith(
                $resolvedTemp + [IO.Path]::DirectorySeparatorChar,
                [StringComparison]::OrdinalIgnoreCase
            )
        ) {
            throw "Refusing to remove drift test data outside TEMP: $resolvedTestRoot"
        }
        Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
    }
}
