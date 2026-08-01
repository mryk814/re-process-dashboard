param(
    [string]$ReportPath = "artifacts/main-acceptance/latest.json",
    [string[]]$IncludeGate = @(),
    [string]$VerificationCatalogPath = ""
)

$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$verificationCatalogPath = if ($VerificationCatalogPath) {
    if ([IO.Path]::IsPathRooted($VerificationCatalogPath)) {
        $VerificationCatalogPath
    } else {
        Join-Path $repositoryRoot $VerificationCatalogPath
    }
} else {
    Join-Path $PSScriptRoot "verification-gates.json"
}
$verificationCatalog = Get-Content -LiteralPath $verificationCatalogPath -Raw -Encoding utf8 |
    ConvertFrom-Json
$releaseLevel = @($verificationCatalog.levels | Where-Object { $_.id -eq "release" })
if ($releaseLevel.Count -ne 1) {
    throw "verification catalog must define one release level"
}
$selectedGateIds = [Collections.Generic.List[string]]::new()
foreach ($gateId in @($releaseLevel[0].gates) + $IncludeGate) {
    if (-not $selectedGateIds.Contains("$gateId")) {
        $selectedGateIds.Add("$gateId")
    }
}
$knownGateIds = @($verificationCatalog.gates.PSObject.Properties.Name)
foreach ($gateId in $selectedGateIds) {
    if ($gateId -notin $knownGateIds) {
        throw "unknown verification gate: $gateId"
    }
    $gate = $verificationCatalog.gates.PSObject.Properties[$gateId].Value
    if ($gate.manual) {
        throw "manual verification gate cannot be automated by acceptance: $gateId"
    }
}
$startedAt = Get-Date
$runId = $startedAt.ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$logRoot = Join-Path $repositoryRoot "artifacts/main-acceptance/$runId"
$resolvedReportPath = if ([IO.Path]::IsPathRooted($ReportPath)) {
    $ReportPath
} else {
    Join-Path $repositoryRoot $ReportPath
}
$results = [Collections.Generic.List[object]]::new()
$playwrightEnvironmentKeys = @(
    "PLAYWRIGHT_REUSE_SERVER",
    "PLAYWRIGHT_DB_PATH",
    "PLAYWRIGHT_OWNED_DB_PATH",
    "PLAYWRIGHT_API_PORT",
    "PLAYWRIGHT_WEB_PORT",
    "PLAYWRIGHT_BROKEN_TASK_PACKAGE"
)
$inheritedPlaywrightEnvironment = [ordered]@{}
foreach ($key in $playwrightEnvironmentKeys) {
    $value = [Environment]::GetEnvironmentVariable($key, "Process")
    if ($null -ne $value) {
        $inheritedPlaywrightEnvironment[$key] = $value
        Remove-Item -LiteralPath "Env:$key"
    }
}

New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
New-Item -ItemType Directory -Path (Split-Path -Parent $resolvedReportPath) -Force | Out-Null

function Invoke-Captured {
    param(
        [string]$Name,
        [string]$Executable,
        [string[]]$Arguments
    )

    $safeName = $Name.ToLowerInvariant() -replace "[^a-z0-9]+", "-"
    $logPath = Join-Path $logRoot "$safeName.log"
    $timer = [Diagnostics.Stopwatch]::StartNew()
    Write-Host "`n== $Name =="
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        # Native stderr contains expected warnings (for example openpyxl).
        # Capture it in the log and use the process exit code as the gate.
        $ErrorActionPreference = "Continue"
        $output = @(
            & $Executable @Arguments 2>&1 |
                Tee-Object -FilePath $logPath |
                ForEach-Object {
                    Write-Host "$_"
                    $_
                }
        )
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    $timer.Stop()
    $textOutput = @($output | ForEach-Object { "$_" })
    $summary = if ($exitCode -eq 0) {
        @(
            $textOutput |
                Where-Object {
                    $_ -match "(passed|failed|skipped|tests|pass|fail|Installer:|Folder ZIP:|OK$)"
                } |
                Select-Object -Last 20
        )
    } else {
        @($textOutput | Select-Object -Last 200)
    }
    $results.Add([ordered]@{
        name = $Name
        status = if ($exitCode -eq 0) { "passed" } else { "failed" }
        command = "$Executable $($Arguments -join ' ')"
        exitCode = $exitCode
        durationSeconds = [Math]::Round($timer.Elapsed.TotalSeconds, 3)
        log = "artifacts/main-acceptance/$runId/$safeName.log"
        summary = $summary
    })
    if ($exitCode -ne 0) {
        throw "$Name failed with exit code $exitCode"
    }
}

function Read-Version {
    param([string]$Executable, [string[]]$Arguments)
    return (@(& $Executable @Arguments 2>&1) -join " ").Trim()
}

function Get-Sha256 {
    param([string]$Path)
    $stream = [IO.File]::OpenRead($Path)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = $hasher.ComputeHash($stream)
        return ([BitConverter]::ToString($bytes) -replace "-", "").ToLowerInvariant()
    } finally {
        $hasher.Dispose()
        $stream.Dispose()
    }
}

function Get-NormalizedTextSha256 {
    param([string]$Path)
    $text = [IO.File]::ReadAllText($Path)
    $normalized = $text.Replace("`r`n", "`n").Replace("`r", "`n")
    $bytes = [Text.Encoding]::UTF8.GetBytes($normalized)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        $digest = $hasher.ComputeHash($bytes)
        return ([BitConverter]::ToString($digest) -replace "-", "").ToLowerInvariant()
    } finally {
        $hasher.Dispose()
    }
}

$testedCommit = (git -C $repositoryRoot rev-parse HEAD).Trim()
$worktreeChanges = @(git -C $repositoryRoot status --porcelain)
if ($worktreeChanges.Count -gt 0) {
    throw "release acceptance requires a clean worktree; commit or remove scoped changes first"
}
$environment = [ordered]@{
    os = [Environment]::OSVersion.VersionString
    powershell = "$($PSVersionTable.PSVersion)"
    node = Read-Version "node.exe" @("--version")
    npm = Read-Version "npm.cmd" @("--version")
    uv = Read-Version "uv.exe" @("--version")
    python = Read-Version "uv.exe" @("run", "python", "--version")
    verificationCatalog = if ($VerificationCatalogPath) {
        $VerificationCatalogPath.Replace("\", "/")
    } else {
        "scripts/verification-gates.json"
    }
}
$failure = $null

Push-Location $repositoryRoot
try {
    foreach ($gateId in $selectedGateIds) {
        $gate = $verificationCatalog.gates.PSObject.Properties[$gateId].Value
        $executable = switch ("$($gate.runner.executable)") {
            "npm" { "npm.cmd" }
            "npx" { "npx.cmd" }
            "uv" { "uv.exe" }
            "powershell" { "powershell.exe" }
            default { "$($gate.runner.executable)" }
        }
        $arguments = @(
            foreach ($argument in @($gate.runner.args)) {
                if ("$argument" -eq '$BASE...HEAD') {
                    "origin/main...HEAD"
                } else {
                    "$argument"
                }
            }
        )
        Invoke-Captured "$gateId" $executable $arguments
    }
} catch {
    $failure = "$_"
} finally {
    Pop-Location
}

$version = (Get-Content -LiteralPath (Join-Path $repositoryRoot "package.json") -Raw |
    ConvertFrom-Json).version
$artifactPaths = @(
    Join-Path $repositoryRoot "release/Evidence-Decision-Workbench-Setup-$version.exe"
    Join-Path $repositoryRoot "release/Evidence-Decision-Workbench-folder-$version.zip"
)
$deliveryPassed = @(
    $results | Where-Object {
        $_.name -eq "windows-delivery" -and
        $_.exitCode -eq 0
    }
).Count -eq 1
$artifacts = @(
    foreach ($artifactPath in $(if ($deliveryPassed) { $artifactPaths } else { @() })) {
        if (Test-Path -LiteralPath $artifactPath -PathType Leaf) {
            $item = Get-Item -LiteralPath $artifactPath
            [ordered]@{
                name = $item.Name
                bytes = $item.Length
                sha256 = Get-Sha256 $artifactPath
            }
        }
    }
)
$finishedAt = Get-Date
$omittedGates = @(
    $completedGateIds = @($results | ForEach-Object { $_.name })
    foreach ($gateId in $selectedGateIds) {
        if ($gateId -notin $completedGateIds) {
            [ordered]@{
                id = $gateId
                status = "not_run"
                reason = "an earlier selected gate failed"
                priorEvidence = @()
            }
        }
    }
    foreach ($gateId in $knownGateIds) {
        if ($gateId -notin $selectedGateIds) {
            $gate = $verificationCatalog.gates.PSObject.Properties[$gateId].Value
            [object[]]$priorEvidence = @()
            if ($null -ne $gate.priorEvidence) {
                $priorEvidence = @($gate.priorEvidence)
            }
            [ordered]@{
                id = $gateId
                status = "not_run"
                reason = if ($gate.manual) {
                    "manual evidence is outside the automated release profile"
                } else {
                    "not selected by the release profile or -IncludeGate"
                }
                priorEvidence = $priorEvidence
            }
        }
    }
)
$report = [ordered]@{
    schemaVersion = "main-acceptance/v2"
    runId = $runId
    level = "release"
    testedCommit = $testedCommit
    currentCommitAtInspection = $testedCommit
    commitsAhead = 0
    changedRiskCategories = @()
    applicability = "current"
    worktreeChangesAtStart = $worktreeChanges
    verificationCatalogSha256 = Get-NormalizedTextSha256 -Path $verificationCatalogPath
    selectedGates = @($selectedGateIds)
    omittedGates = $omittedGates
    clearedInheritedPlaywrightEnvironment = $inheritedPlaywrightEnvironment
    cleanIsolatedPlaywright = $true
    startedAt = $startedAt.ToUniversalTime().ToString("o")
    finishedAt = $finishedAt.ToUniversalTime().ToString("o")
    durationSeconds = [Math]::Round(($finishedAt - $startedAt).TotalSeconds, 3)
    environment = $environment
    gates = $results
    artifacts = $artifacts
    status = if ($failure) { "failed" } else { "passed" }
    failure = $failure
}
$report | ConvertTo-Json -Depth 8 |
    Set-Content -LiteralPath $resolvedReportPath -Encoding utf8
Write-Host "`nAcceptance report: $resolvedReportPath"

if ($failure) {
    throw $failure
}
