# Dataset Input Profile

`backend/src/material_workbench/dataset-input-profile-v1.json` is the only place that maps the current Excel workbook's sheet names, headers, units, entity keys, relations, eligibility values, and technical metadata to application semantics.

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

## Changing an input field

1. Update the production `TaskDefinition`.
2. Update the Dataset Input Profile mapping.
3. Update and version the Feature Pipeline.
4. Retrain and build a new Model Package.
5. Run package contract, feature golden, smoke, and dataset-profile tests.

The package loader verifies canonical input order across TaskDefinition, pipeline document, package manifest, and predictor feature order. A stale package is rejected at runtime startup.

Frontend field rendering is cut over to TaskDefinition in #7. Runtime registry activation is handled in #5, and the reproducible training/package activation route is completed in #19.
