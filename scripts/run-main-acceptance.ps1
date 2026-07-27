param(
    [string]$ReportPath = "artifacts/main-acceptance/latest.json"
)

$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
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
    $output = @(& $Executable @Arguments 2>&1 | Tee-Object -FilePath $logPath)
    $exitCode = $LASTEXITCODE
    $timer.Stop()
    $summary = @(
        $output |
            ForEach-Object { "$_" } |
            Where-Object {
                $_ -match "(passed|failed|skipped|tests|pass|fail|Installer:|Folder ZIP:|OK$)"
            } |
            Select-Object -Last 20
    )
    $results.Add([ordered]@{
        name = $Name
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

$testedCommit = (git -C $repositoryRoot rev-parse HEAD).Trim()
$trackedChanges = @(git -C $repositoryRoot status --porcelain --untracked-files=no)
if ($trackedChanges.Count -gt 0) {
    throw "main acceptance requires a tracked-clean worktree; commit or restore tracked changes first"
}
$environment = [ordered]@{
    os = [Environment]::OSVersion.VersionString
    powershell = "$($PSVersionTable.PSVersion)"
    node = Read-Version "node.exe" @("--version")
    npm = Read-Version "npm.cmd" @("--version")
    uv = Read-Version "uv.exe" @("--version")
    python = Read-Version "uv.exe" @("run", "python", "--version")
}
$failure = $null

Push-Location $repositoryRoot
try {
    Invoke-Captured "Backend pytest" "uv.exe" @(
        "run", "--extra", "dev", "python", "-m", "pytest"
    )
    Invoke-Captured "Web unit tests" "npm.cmd" @(
        "run", "test", "-w", "apps/web"
    )
    Invoke-Captured "Desktop unit tests" "npm.cmd" @(
        "run", "test", "-w", "apps/desktop"
    )
    Invoke-Captured "Generated contracts and typecheck" "npm.cmd" @(
        "run", "typecheck"
    )
    Invoke-Captured "Web and Desktop build" "npm.cmd" @(
        "run", "build"
    )
    Invoke-Captured "All default Playwright on isolated DB" "npx.cmd" @(
        "playwright", "test"
    )
    Invoke-Captured "Failure-state Playwright" "npm.cmd" @(
        "run", "test:e2e:failure-states"
    )
    Invoke-Captured "Chain degraded Playwright" "npx.cmd" @(
        "playwright", "test", "--config", "playwright.chain-degraded.config.ts"
    )
    Invoke-Captured "Legacy workspace migration smoke" "uv.exe" @(
        "run", "--extra", "dev", "python", "-m", "pytest",
        "backend/tests/test_legacy_workspace_acceptance.py", "-q"
    )
    Invoke-Captured "Windows installer and moved portable delivery" "npm.cmd" @(
        "run", "package:windows"
    )
} catch {
    $failure = "$_"
} finally {
    Pop-Location
}

$version = (Get-Content -LiteralPath (Join-Path $repositoryRoot "package.json") -Raw |
    ConvertFrom-Json).version
$artifactPaths = @(
    Join-Path $repositoryRoot "release/Material-Decision-Workbench-Setup-$version.exe"
    Join-Path $repositoryRoot "release/Material-Decision-Workbench-folder-$version.zip"
)
$deliveryPassed = @(
    $results | Where-Object {
        $_.name -eq "Windows installer and moved portable delivery" -and
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
$report = [ordered]@{
    schemaVersion = "main-acceptance/v1"
    runId = $runId
    testedCommit = $testedCommit
    trackedChangesAtStart = $trackedChanges
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
