$repoRoot = Split-Path -Parent $PSScriptRoot
$env:WORKBENCH_SOURCE_PATH = Join-Path $repoRoot "data/source/process_dashboard_two_equipment_v7.xlsx"
$env:MATERIAL_WORKBENCH_MODEL_PACKAGE = Join-Path $repoRoot "models/packages/annealed-gp-2026-07-v7"
$env:MATERIAL_WORKBENCH_HOT_ROLLING_MODEL_PACKAGE = Join-Path $repoRoot "models/packages/hot-rolled-horseshoe-2026-07-v7"

Push-Location $repoRoot
try {
    npm.cmd run dev
}
finally {
    Pop-Location
}
