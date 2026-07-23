$repoRoot = Split-Path -Parent $PSScriptRoot
$env:WORKBENCH_SOURCE_PATH = Join-Path $repoRoot "data/source/material_workbench_process_v1.xlsx"
$env:MATERIAL_WORKBENCH_MODEL_PACKAGE = Join-Path $repoRoot "models/packages/annealed-gp-stable-ard-process-v1"
$env:MATERIAL_WORKBENCH_HOT_ROLLING_MODEL_PACKAGE = Join-Path $repoRoot "models/packages/hot-rolled-horseshoe-process-v1"

Push-Location $repoRoot
try {
    npm.cmd run dev
}
finally {
    Pop-Location
}
