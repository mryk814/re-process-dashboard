# Dataset Input Profile

各Excelデータフローの外部sheet・列・単位・entity key・relation・適格性・技術メタデータは、`backend/src/material_workbench/dataset-input-profile-*.json` で管理します。既存のv2フローは `dataset-input-profile-v1.json`、別命名・生履歴入力のv3フローは `dataset-input-profile-v3.json`、具体的な2設備工程名を持つv5フローは `dataset-input-profile-v5.json` です。v5はv3の正規化・予測契約を継承し、焼鈍履歴の工程名だけを標準工程カテゴリへ対応付けます。起動時はWorkbookのsheet構成とsource markerから最も具体的に一致するprofileを自動選択します。明示したい場合は `load_workbook_data(..., profile_path=...)` または検証コマンドの `--profile` を使います。

Production task contracts live in `backend/src/material_workbench/task_definitions/`. At startup the backend validates the profile against every production `TaskDefinition`, then preflights the workbook before model or database initialization.

```text
Raw workbook + Dataset Input Profile + TaskDefinition
                         |
                         v
                 Canonical Dataset
                         |
                         v
       lineage / feature pipeline / model runtime / API
```

## Contract rules

- Required sheets, mapped headers, units, numeric signal, categorical choices, observation parents, and observation targets are blocking requirements.
- Header order and unmapped extra columns are not part of the model input contract.
- Unknown fields, implicit unit conversions, duplicate mappings, missing task profiles, and incomplete output mappings are rejected.
- Entity identity is `(entity_type, key)`, so equal source key strings in different entity types do not merge.
- `median_by_parent/v1` and `stage_local_clock/v1` are the only executable normalizers. The profile cannot contain arbitrary code.
- Eligibility policies declare their accepted source values. A policy with no accepted signal in the workbook is rejected during preflight.
- Relation parent consistency is enforced only when a join declares `parent_consistency: exactly_one`. Known source-quality anomalies remain inspectable when the profile declares `allow_many`.
- A profile can `extends` another profile. Object mappings are merged and arrays are replaced, so a renamed/new workbook flow can reuse the canonical contract without copying the old profile.
- A source flow may declare `optional_roles`, `optional_technical_fields`, and explicit `policy_defaults` when that source genuinely does not contain the corresponding signal. Defaults are part of the profile contract; they are never inferred from a missing cell.
- `task_definition_ids` freezes the task set supported by a profile. This lets a new task with completely different explanatory variables or targets be added without making old data flows pretend to support it.

## Adding a new workbook flow with the same canonical task

1. Update the production `TaskDefinition`.
2. Update the Dataset Input Profile mapping.
3. Update and version the Feature Pipeline.
4. Retrain and build a new Model Package.
5. Run package contract, feature golden, smoke, and dataset-profile tests.

For the v3 flow, this procedure is represented by `dataset-input-profile-v3.json`: it reuses the v2 task definitions and feature pipelines, maps the renamed sheets/headers, and explicitly declares that the curated `quality` sheet and hot-rolling learning flag are absent from v3.

For the v5 flow, `dataset-input-profile-v5.json` extends v3 rather than replacing it. Its source markers distinguish `CGL-1` and concrete history labels such as `予熱1`, `加熱1`, `均熱出口`, and `水冷`. Its stage mappings add `stage_category` and `mapping_status` to the inspectable heat points, so the lineage screen can show the concrete source label on a time-aligned process track while preserving the original `stage_name`.

## Adding genuinely new data (different variables or targets)

Do not force a new scientific quantity into `hot-rolled-properties-v1` or `annealed-properties-v1`. Add a new vertical slice:

1. Add `backend/src/material_workbench/task_definitions/<task-id>.json`. Define the new input groups, units, editable/allowed/training ranges, output targets, constraints, and runtime capability. Keep the file name equal to the task id.
2. Add a new profile under `backend/src/material_workbench/dataset-input-profile-<flow>.json`. Set `task_definition_ids` to the task(s) this flow actually supports. Use `extends` only when the shared entity/relation contract is truly the same; otherwise write a separate profile.
3. Add or extend a feature pipeline module with a new pipeline id/version. Keep raw source columns, canonical inputs, and derived features separately inspectable. Do not reuse an old feature vector merely because the column count happens to match.
4. Add an allow-listed runtime/model adapter or task-specific runtime, then build a Model Package whose manifest records the new task id, input contract digest, profile digest, feature pipeline version, source digest, and output targets.
5. Add focused contract, feature golden, source preflight, package smoke, and one API/E2E test. Only after those pass, add the package to `models/active-packages.json` for that task. Existing v2/v3 packages remain untouched.

The current application has two production runtime implementations, so a genuinely new task also requires one explicit runtime registration seam in `app.py`/`task_registry.py`; the profile and TaskDefinition alone must not make an unsupported task appear runnable.

## Repeatable source preflight

Run this before touching the runtime or database. It never writes to the source workbook:

```powershell
uv run python backend/scripts/verify_dataset_source.py data/source/process_dashboard_realistic_excel_v3.xlsx
uv run python backend/scripts/verify_dataset_source.py path/to/new-source.xlsx --profile backend/src/material_workbench/dataset-input-profile-new.json --json
```

Then run the focused contract checks and the normal full gate:

```powershell
uv run pytest backend/tests/test_dataset_profile.py backend/tests/test_importer.py
uv run pytest
npm run typecheck
npm run build
```

The preflight report includes the selected profile id, source SHA-256, sheet row counts, relation row count, observation eligibility counts, and detected structural issues. A model package must be rebuilt for the selected source/profile; a package trained from another source is rejected by provenance validation.

The package loader verifies canonical input order across TaskDefinition, pipeline document, package manifest, and predictor feature order. A stale package is rejected at runtime startup.

Frontend field rendering is cut over to TaskDefinition in #7. Runtime registry activation is handled in #5, and the reproducible training/package activation route is completed in #19.
