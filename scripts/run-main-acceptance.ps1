param(
    [string]$ReportPath = "artifacts/main-acceptance/latest.json",
    [string[]]$IncludeGate = @(),
    [string]$VerificationCatalogPath = "",
    [switch]$SelectionOnly
)

$ErrorActionPreference = "Stop"
$isWindowsPlatform = [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT

function Get-PlatformExecutable {
    param([string]$Executable)
    if ($isWindowsPlatform) {
        $resolved = switch ($Executable) {
            "node" { "node.exe" }
            "npm" { "npm.cmd" }
            "npx" { "npx.cmd" }
            "uv" { "uv.exe" }
            "powershell" { "powershell.exe" }
            default { $Executable }
        }
        return $resolved
    }
    $resolved = switch ($Executable) {
        "powershell" { "pwsh" }
        default { $Executable }
    }
    return $resolved
}

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

$absorbedGates = [ordered]@{}
foreach ($ownerGateId in $selectedGateIds) {
    $ownerGate = $verificationCatalog.gates.PSObject.Properties[$ownerGateId].Value
    foreach ($gateId in @($ownerGate.absorbs) | Where-Object { "$_" }) {
        if ($gateId -notin $knownGateIds) {
            throw "gate $ownerGateId absorbs unknown gate: $gateId"
        }
        if (-not $selectedGateIds.Contains("$gateId") -or $gateId -eq $ownerGateId) {
            continue
        }
        if ($absorbedGates.Contains("$gateId") -and $absorbedGates[$gateId] -ne $ownerGateId) {
            throw "gate $gateId is absorbed by both $($absorbedGates[$gateId]) and $ownerGateId"
        }
        $absorbedGates[$gateId] = $ownerGateId
    }
}
$executionGateIds = @(
    $selectedGateIds | Where-Object { -not $absorbedGates.Contains($_) }
)
$gateEnvironment = [ordered]@{}
if (
    $executionGateIds -contains "default-playwright" -and
    $executionGateIds -contains "failure-state-e2e"
) {
    $gateEnvironment["failure-state-e2e"] = [ordered]@{
        VERIFICATION_SKIP_STANDARD_FAILURE_SPECS = "1"
    }
}

if ($SelectionOnly) {
    [ordered]@{
        selectedGates = @($selectedGateIds)
        executionGates = @($executionGateIds)
        absorbedGates = $absorbedGates
        gateEnvironment = $gateEnvironment
    } | ConvertTo-Json -Depth 6 -Compress
    return
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
        [string[]]$Arguments,
        [Collections.IDictionary]$Environment = @{}
    )

    $safeName = $Name.ToLowerInvariant() -replace "[^a-z0-9]+", "-"
    $logPath = Join-Path $logRoot "$safeName.log"
    $timer = [Diagnostics.Stopwatch]::StartNew()
    Write-Host "`n== $Name =="
    $previousErrorActionPreference = $ErrorActionPreference
    $previousEnvironment = [ordered]@{}
    try {
        foreach ($key in $Environment.Keys) {
            $previousEnvironment[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
            [Environment]::SetEnvironmentVariable($key, "$($Environment[$key])", "Process")
        }
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
        foreach ($key in $previousEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable($key, $previousEnvironment[$key], "Process")
        }
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
    node = Read-Version (Get-PlatformExecutable "node") @("--version")
    npm = Read-Version (Get-PlatformExecutable "npm") @("--version")
    uv = Read-Version (Get-PlatformExecutable "uv") @("--version")
    python = Read-Version (Get-PlatformExecutable "uv") @("run", "python", "--version")
    verificationCatalog = if ($VerificationCatalogPath) {
        $VerificationCatalogPath.Replace("\", "/")
    } else {
        "scripts/verification-gates.json"
    }
}
$failure = $null

Push-Location $repositoryRoot
try {
    foreach ($gateId in $executionGateIds) {
        $gate = $verificationCatalog.gates.PSObject.Properties[$gateId].Value
        $executable = Get-PlatformExecutable "$($gate.runner.executable)"
        $arguments = @(
            foreach ($argument in @($gate.runner.args)) {
                if ("$argument" -eq '$BASE...HEAD') {
                    "origin/main...HEAD"
                } else {
                    "$argument"
                }
            }
        )
        $gateSpecificEnvironment = if ($gateEnvironment.Contains($gateId)) {
            $gateEnvironment[$gateId]
        } else {
            @{}
        }
        Invoke-Captured "$gateId" $executable $arguments $gateSpecificEnvironment
    }
} catch {
    $failure = "$_"
} finally {
    Pop-Location
}

foreach ($gateId in $absorbedGates.Keys) {
    $ownerGateId = "$($absorbedGates[$gateId])"
    $ownerResult = @($results | Where-Object { $_.name -eq $ownerGateId })
    $passed = $ownerResult.Count -eq 1 -and $ownerResult[0].status -eq "passed"
    $gate = $verificationCatalog.gates.PSObject.Properties[$gateId].Value
    $results.Add([ordered]@{
        name = "$gateId"
        status = if ($passed) { "passed" } else { "failed" }
        command = "$($gate.command)"
        exitCode = if ($passed) { 0 } else { 1 }
        durationSeconds = 0
        log = $null
        summary = if ($passed) {
            @("covered by $ownerGateId")
        } else {
            @("absorbing gate failed or was not run: $ownerGateId")
        }
        evidenceSource = $ownerGateId
    })
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
    executionGates = @($executionGateIds)
    absorbedGates = $absorbedGates
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
