param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$bootstrap = Join-Path $PSScriptRoot "bootstrap-book-tools.ps1"
. (Join-Path $PSScriptRoot "book-tools.ps1")
$testRoot = Join-Path $env:TEMP (
    "material workbench bootstrap test {0}" -f [guid]::NewGuid().ToString("N")
)
$fixtureRoot = Join-Path $testRoot "fixture"
$sourceRoot = Join-Path $testRoot "source"
$toolRoot = Join-Path $testRoot "tool root with spaces"
$lockFile = Join-Path $testRoot "test-tools.lock.json"

function Assert-True {
    param(
        [Parameter(Mandatory = $true)][bool]$Condition,
        [Parameter(Mandatory = $true)][string]$Message
    )
    if (-not $Condition) {
        throw $Message
    }
}

function Write-TestLock {
    param(
        [Parameter(Mandatory = $true)][string]$Hash,
        [Parameter(Mandatory = $true)][int64]$Size,
        [Parameter(Mandatory = $true)][string]$Source
    )
    @{
        schemaVersion = 1
        platform = "windows-x86_64"
        tools = @(
            @{
                name = "fixture"
                version = "1.2.3"
                url = $Source
                sha256 = $Hash
                size = $Size
                executable = "bin/fixture.cmd"
                versionPattern = "^fixture 1\.2\.3$"
            }
        )
    } | ConvertTo-Json -Depth 5 |
        Set-Content -LiteralPath $lockFile -Encoding UTF8
}

function Invoke-TestBootstrap {
    param(
        [switch]$Offline,
        [switch]$Force,
        [switch]$ExpectFailure
    )
    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $bootstrap,
        "-ToolRoot", $toolRoot,
        "-LockFile", $lockFile
    )
    if ($Offline) {
        $arguments += "-Offline"
    }
    if ($Force) {
        $arguments += "-Force"
    }
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = (& powershell.exe @arguments 2>&1 | Out-String).Trim()
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($ExpectFailure) {
        if ($exitCode -eq 0) {
            throw "Bootstrap unexpectedly succeeded.`n$output"
        }
    } else {
        if ($exitCode -ne 0) {
            throw "Bootstrap failed with exit code $exitCode.`n$output"
        }
        if (-not [string]::IsNullOrWhiteSpace($output)) {
            Write-Host $output
        }
    }
}

New-Item -ItemType Directory -Force -Path (Join-Path $fixtureRoot "bin"),$sourceRoot |
    Out-Null
Set-Content -LiteralPath (Join-Path $fixtureRoot "bin\fixture.cmd") -Encoding ASCII -Value @(
    "@echo off",
    "echo fixture 1.2.3",
    "exit /b 0"
)
$archive = Join-Path $sourceRoot "fixture.zip"
Compress-Archive -Path (Join-Path $fixtureRoot "*") -DestinationPath $archive
$archiveItem = Get-Item -LiteralPath $archive
$archiveHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()

try {
    $previousToolRootEnvironment = $env:MATERIAL_WORKBENCH_BOOK_TOOLS
    $environmentRoot = Join-Path $testRoot "environment root"
    $explicitRoot = Join-Path $testRoot "explicit root"
    try {
        $env:MATERIAL_WORKBENCH_BOOK_TOOLS = $environmentRoot
        Assert-True (
            (Get-BookToolRoot) -eq [IO.Path]::GetFullPath($environmentRoot)
        ) "Environment tool root was not resolved."
        Assert-True (
            (Get-BookToolRoot -ToolRoot $explicitRoot) -eq [IO.Path]::GetFullPath($explicitRoot)
        ) "Explicit tool root did not override the environment."
    } finally {
        if ($null -eq $previousToolRootEnvironment) {
            Remove-Item Env:MATERIAL_WORKBENCH_BOOK_TOOLS -ErrorAction SilentlyContinue
        } else {
            $env:MATERIAL_WORKBENCH_BOOK_TOOLS = $previousToolRootEnvironment
        }
    }

    Write-TestLock -Hash $archiveHash -Size $archiveItem.Length -Source $archive
    Invoke-TestBootstrap

    $identityRoot = Join-Path $toolRoot (
        "tools\fixture\windows-x86_64\1.2.3-{0}" -f $archiveHash
    )
    $readyMarker = Join-Path $identityRoot ".ready.json"
    $readyExecutable = Join-Path $identityRoot "bin\fixture.cmd"
    Assert-True (Test-Path -LiteralPath $readyMarker -PathType Leaf) "Ready marker is missing."
    Assert-True (Test-Path -LiteralPath $readyExecutable -PathType Leaf) "Executable is missing."

    $heldBootstrapLock = [IO.File]::Open(
        (Join-Path $toolRoot ".bootstrap.lock"),
        [IO.FileMode]::OpenOrCreate,
        [IO.FileAccess]::ReadWrite,
        [IO.FileShare]::None
    )
    try {
        Invoke-TestBootstrap -ExpectFailure
    } finally {
        $heldBootstrapLock.Dispose()
    }

    $markerHashBefore = (Get-FileHash -LiteralPath $readyMarker -Algorithm SHA256).Hash
    $executableHashBefore = (Get-FileHash -LiteralPath $readyExecutable -Algorithm SHA256).Hash
    $wrongHash = ("0" * 64)
    Write-TestLock -Hash $wrongHash -Size $archiveItem.Length -Source $archive
    Invoke-TestBootstrap -ExpectFailure
    Assert-True (
        (Get-FileHash -LiteralPath $readyMarker -Algorithm SHA256).Hash -eq $markerHashBefore
    ) "Checksum failure changed the ready marker."
    Assert-True (
        (Get-FileHash -LiteralPath $readyExecutable -Algorithm SHA256).Hash -eq $executableHashBefore
    ) "Checksum failure changed the ready executable."

    Write-TestLock -Hash $archiveHash -Size $archiveItem.Length -Source $archive
    Invoke-TestBootstrap -Force
    Remove-Item -LiteralPath $archive -Force
    Invoke-TestBootstrap -Offline

    $staleStage = Join-Path (
        Split-Path -Parent $identityRoot
    ) ".staging-interrupted"
    New-Item -ItemType Directory -Force -Path $staleStage | Out-Null
    Set-Content -LiteralPath (Join-Path $staleStage "partial.txt") -Value "partial"
    Invoke-TestBootstrap -Offline -Force
    Assert-True (-not (Test-Path -LiteralPath $staleStage)) "Stale staging was not removed."

    Write-Host (
        "Bootstrap tests passed: checksum rejection, cache reuse, Force, " +
        "interrupted staging, concurrency lock, root precedence, custom path."
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
            throw "Refusing to remove test data outside TEMP: $resolvedTestRoot"
        }
        Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
    }
}
