# Task-driven Workbench epic verification

Epic #3 is accepted against the repository contracts below. The issue checkboxes are a summary; these executable checks are the evidence.

| Acceptance area | Evidence |
|---|---|
| All production tasks: candidate, preview, detailed, snapshot, actual | `e2e/shared-workbench.spec.ts`, `backend/tests/test_hot_rolling.py`, `backend/tests/test_flank_wear.py`, `backend/tests/test_step4_to_6.py` |
| Project, explore, stock, compare, save, review journey | `e2e/project-hub.spec.ts`, `e2e/screening-workbench.spec.ts`, `e2e/navigation-intent.spec.ts` |
| Lineage/screening/copy provenance and return navigation | `e2e/navigation-intent.spec.ts`, `backend/tests/test_api.py`, `backend/tests/test_project_history.py` |
| Flexible external columns; strict required mapping and units | `backend/tests/test_dataset_profile.py`, `backend/tests/test_importer.py` |
| TaskDefinition, Feature Pipeline, Package, runtime boundaries | `backend/tests/test_task_contracts.py`, `backend/tests/test_feature_pipeline.py`, `backend/tests/test_model_packages.py`, `backend/tests/test_task_registry.py` |
| Training, verification, activation, rollback, immutable snapshots | `backend/tests/test_model_lifecycle.py`, `backend/tests/test_hot_rolling_model_package_builder.py` |
| Candidate revision, archive, domain errors, migration preservation | `backend/tests/test_candidate_safety.py`, `backend/tests/test_candidate_migration.py` |
| OpenAPI-generated frontend contract and feature boundaries | `backend/tests/test_openapi_contract.py`, `apps/web/tests/importBoundaries.test.mjs` |
| Changed-candidate and visible-surface inference only | `backend/tests/test_inference_work_graph.py`, `e2e/inference-p0.spec.ts`, `docs/inference-execution.md` |

Removal checks are part of closure:

- no `HotRollingWorkbench.tsx` or `/api/hot-rolling/*` route;
- no plural response-curve endpoint or production direct `fetch` outside the generated client transport;
- legacy `hot_rolling_candidates` appears only in the one-time guarded migration reader/drop path;
- task outputs, input groups, composition fields, process fields, and categorical choices are rendered from the resolved task contract.

Independent follow-up work such as output plausibility/display ranges (#43) and additional model runtime examples (#45 and its children) may improve the product, but does not reopen the unified Workbench architecture or Epic #3 acceptance boundary.
