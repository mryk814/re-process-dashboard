param(
    [Parameter(Mandatory = $true)]
    [int]$ParentProcessId,
    [Parameter(Mandatory = $true)]
    [string]$StdoutPath,
    [Parameter(Mandatory = $true)]
    [string]$OutputPath,
    [Parameter(Mandatory = $true)]
    [string]$StopPath,
    [int]$PollMilliseconds = 10000
)

$ErrorActionPreference = "Stop"
$peakBytes = [int64]0
$peakTree = @()
$sampleCount = 0
$lastCompletedTest = $null
$totalPhysicalMemory = try {
    [int64](Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory
} catch {
    $null
}

function Get-DescendantRows {
    param([int]$RootProcessId)
    $rows = @(Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, Name)
    $ids = [System.Collections.Generic.HashSet[int]]::new()
    [void]$ids.Add($RootProcessId)
    do {
        $added = $false
        foreach ($row in $rows) {
            if ($ids.Contains([int]$row.ParentProcessId) -and $ids.Add([int]$row.ProcessId)) {
                $added = $true
            }
        }
    } while ($added)
    return @($rows | Where-Object {
        $ids.Contains([int]$_.ProcessId) -and [int]$_.ProcessId -ne $PID
    })
}

function Read-LastCompletedTest {
    if (-not (Test-Path -LiteralPath $StdoutPath -PathType Leaf)) {
        return $null
    }
    $match = Get-Content -LiteralPath $StdoutPath -Tail 400 -ErrorAction SilentlyContinue |
        Select-String -Pattern '^(?:(backend/tests/\S+::\S+)\s+(?:PASSED|FAILED|SKIPPED|XFAIL|XPASS)|\[gw\d+\]\s+\[\s*\d+%\]\s+(?:PASSED|FAILED|SKIPPED|XFAIL|XPASS)\s+(backend/tests/\S+::\S+))(?:\s|$)' |
        Select-Object -Last 1
    if ($null -eq $match) {
        return $null
    }
    $directTest = $match.Matches[0].Groups[1].Value
    if ($directTest) {
        return $directTest
    }
    return $match.Matches[0].Groups[2].Value
}

function Write-Heartbeat {
    param(
        [array]$Tree,
        [bool]$ParentAlive
    )
    $payload = [ordered]@{
        schemaVersion = "verification-runner-heartbeat/v1"
        parentProcessId = $ParentProcessId
        observerProcessId = $PID
        parentAlive = $ParentAlive
        observedAt = (Get-Date).ToUniversalTime().ToString("o")
        sampleCount = $sampleCount
        peakTreeWorkingSetBytes = $peakBytes
        peakProcessTree = $peakTree
        currentProcessTree = $Tree
        totalPhysicalMemoryBytes = $totalPhysicalMemory
        lastCompletedTest = $lastCompletedTest
        githubRunId = $env:GITHUB_RUN_ID
        githubRunAttempt = $env:GITHUB_RUN_ATTEMPT
        runnerName = $env:RUNNER_NAME
        runnerOS = $env:RUNNER_OS
        runnerArchitecture = $env:RUNNER_ARCH
    }
    $directory = Split-Path -Parent $OutputPath
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $temporary = "$OutputPath.$PID.tmp"
    $payload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporary -Encoding utf8
    Move-Item -LiteralPath $temporary -Destination $OutputPath -Force
}

while ($true) {
    $parentAlive = $null -ne (Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue)
    $rows = if ($parentAlive) { @(Get-DescendantRows -RootProcessId $ParentProcessId) } else { @() }
    $tree = @()
    $workingSet = [int64]0
    foreach ($row in $rows) {
        $process = Get-Process -Id $row.ProcessId -ErrorAction SilentlyContinue
        if ($null -eq $process) {
            continue
        }
        $workingSet += [int64]$process.WorkingSet64
        $tree += [ordered]@{
            processId = [int]$row.ProcessId
            parentProcessId = [int]$row.ParentProcessId
            name = [string]$row.Name
            workingSetBytes = [int64]$process.WorkingSet64
            peakWorkingSetBytes = [int64]$process.PeakWorkingSet64
        }
    }
    $sampleCount += 1
    if ($workingSet -gt $peakBytes) {
        $peakBytes = $workingSet
        $peakTree = $tree
    }
    $observedTest = Read-LastCompletedTest
    if ($null -ne $observedTest) {
        $lastCompletedTest = $observedTest
    }
    Write-Heartbeat -Tree $tree -ParentAlive $parentAlive
    if ((Test-Path -LiteralPath $StopPath) -or -not $parentAlive) {
        break
    }
    Start-Sleep -Milliseconds $PollMilliseconds
}
