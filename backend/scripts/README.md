# Backend command inventory

`backend/scripts/` はアプリ本体からimportするライブラリではなく、用途と寿命が明示された
薄いCLI入口です。再利用処理は `backend/src/material_workbench/` に置きます。
commandを追加するときは、次の表へowner、出力、根拠文書または公開入口を追加してください。

## 寿命

| Directory | 寿命 | 削除条件 |
|---|---|---|
| `operations/` | 日常運用 | 公開commandと移行案内を同時に置換したとき |
| `generators/` | 追跡成果物の再生成期間 | provenanceを別の再生成経路へ移したとき |
| `acceptance/` | 受入契約の存続期間 | 同じ境界を検証するgateへ置換したとき |
| `experiments/` | 根拠文書の再検証期間 | 最終条件と結果を文書へ固定し、再実行が不要になったとき |
| `experiments/spikes/` | 採否判断まで | 結果文書へ採否と限界を固定したとき |

## Operations

| Command | Owner | Output | Reference / public entry |
|---|---|---|---|
| `operations/profile_workbench.py` | Data/Profile contracts | JSON、Dataset registration | `docs/operations/data-contributor-start-here.md` |
| `operations/model_workflow.py` | Model lifecycle | 外部Model Package、status JSON | `npm run model:*` |
| `operations/verify_model_package.py` | Model runtime | Package検証結果 | `docs/model-runtime-examples/index.md` |
| `operations/task_inventory.py` | Task registry | `docs/task-inventory.json` | `npm run task:inventory` |
| `operations/developer_doctor.py` | Developer experience | 環境診断JSON | `npm run dev:doctor` |
| `operations/workspace_check.py` | Workspace preflight | read-only整合検査 | `npm run workspace:check` |
| `operations/workspace_maintenance.py` | Workspace catalog | Package登録のinspect/deactivate監査 | `npm run workspace:maintenance` |
| `operations/workspace_lifecycle.py` | Developer experience | branch Workspace inventory / explicit prune | `npm run workspace:list`, `npm run workspace:prune` |
| `operations/seed_review_workspace.py` | Review fixtures | branch既定Workspace | `npm run workspace:seed` |
| `operations/export_openapi.py` | API contracts | `apps/web/src/generated/openapi.json` | `npm run api:generate` |
| `operations/sample_gallery.py` | Sample projects | branch Workspace内の同梱sample | `npm run samples` |

Profile inheritanceの展開は `operations/profile_workbench.py materialize`、source検証は
同じCLIの `validate` に統合しています。`validate` は `--profile` を省略するとProfileを
自動検出します。旧 `materialize_dataset_profile.py` と `verify_dataset_source.py` は
重複入口として削除済みです。

## Generators

| Command | Owner | Output | Reference / provenance |
|---|---|---|---|
| `generators/build_tutorial_dataset_revision.py` | Tutorial data | `artifacts/derived-data/` | `docs/examples/tutorial-data-pipeline.md` |
| `generators/build_welding_consumable_sample_dataset.py` | Welding sample | `artifacts/derived-data/` | `npm run data:build:welding-sample` |
| `generators/materialize_observation_training_views.py` | Observation Profile | training view | `npm run data:build:welding-stage-c-views` |
| `generators/prepare_calce_battery_dataset.py` | External reference data | derived CSV/Profile evidence | `docs/contracts/reference-data-loop.md` |
| `generators/prepare_secom_stress_dataset.py` | External reference data | derived CSV/report | `docs/examples/external-dataset-tasks.md` |
| `generators/build_default_model_package.py` | Annealing model | data-only Model Package | `npm run models:build:annealed` |
| `generators/build_hot_rolling_model_package.py` | Hot-rolling model | data-only Model Package | `npm run models:build:hot-rolling` |
| `generators/build_flank_wear_model_package.py` | Flank-wear model | data-only Model Package | `npm run models:build:flank-wear` |
| `generators/build_annealed_individual_model_packages.py` | Annealing model research | tracked individual-observation Packages | Package manifests |
| `generators/build_annealed_lightgbm_model_package.py` | Annealing model research | tracked LightGBM Package | `docs/model-runtime-examples/existing-runtimes.md` |
| `generators/build_external_tabular_packages.py` | External tabular tasks | portable v2 Packages | `docs/reports/model-package-portable-digest-v2-2026-07-28.md` |
| `generators/build_numpyro_package_examples.py` | Runtime examples | `examples/model-packages/numpyro/` | `npm run models:build:examples` |
| `generators/build_posterior_linear_model_example.py` | Runtime examples | posterior-linear example | `docs/model-runtime-examples/sparse-bayesian.md` |
| `generators/build_quantile_model_example.py` | Runtime examples | quantile-linear example | `docs/model-runtime-examples/quantile-only.md` |
| `generators/build_additive_model_examples.py` | Runtime examples | additive-term examples | `docs/model-runtime-examples/additive-terms.md` |
| `generators/build_welding_stage_a_package.py` | Welding chain | Stage A Package | Package manifest |
| `generators/build_welding_stage_b_assets.py` | Welding chain | Stage B Dataset/Profile/Package | `docs/contracts/chain-evaluation.md` |
| `generators/build_welding_stage_c_model_package.py` | Welding chain | Stage C Package | `backend/tests/test_welding_stage_c.py` |

元データ `data/source/` は読取専用です。generatorの既定出力はderived artifactまたは
明示したPackage directoryとし、利用頻度だけを理由に追跡成果物を削除しません。

## Acceptance

| Command | Owner | Output | Reference / gate |
|---|---|---|---|
| `acceptance/build_welding_chain_evaluation.py` | Chain evaluation | 評価JSON | `docs/contracts/chain-evaluation.md` |
| `acceptance/golden_path_smoke.py` | Model lifecycle | temp storeでのgolden-path結果 | `npm run model:golden-path:smoke` |
| `acceptance/reference_data_loop_acceptance.py` | Reference data loop | temp Workspace/Package evidence | `docs/contracts/reference-data-loop.md` |
| `acceptance/run_chain_degraded_e2e.py` | Chain degraded mode | Playwright result | `playwright.chain-degraded.config.ts` |

## Experiments

各結果文書からこの表のcommandへ逆リンクします。出力の正本は
`docs/benchmarks/`, `docs/decisions/`, `docs/research/`, `docs/reports/`です。

| Command | Owner | Output | Evidence document |
|---|---|---|---|
| `experiments/analyze_secom_sensor_selection.py` | External task research | sensor-selection report | `docs/examples/external-dataset-tasks.md` |
| `experiments/benchmark_data_lifecycle.py` | Persistence performance | benchmark JSON/Markdown | `docs/benchmarks/2026-07-27-data-lifecycle.md` |
| `experiments/benchmark_proposal_pool.py` | Proposal performance | timing table | `docs/benchmarks/2026-07-26-proposal-pool.md` |
| `experiments/compare_annealing_feature_pipelines.py` | Feature engineering | comparison JSON | `docs/reports/annealing-feature-pipeline-v4-comparison.md` |
| `experiments/evaluate_gmr_inverse.py` | Inverse proposal research | evaluation JSON | `docs/research/gmr-inverse-candidate-poc.md` |
| `experiments/evaluate_shared_multioutput_gp.py` | Multi-output research | evaluation JSON | `docs/decisions/shared-multi-output.md` |
| `experiments/spikes/spike_case_a.py` | Extensibility research | temp fixture/result | `docs/architecture/extensibility-spikes.md` |
| `experiments/spikes/spike_case_b.py` | Extensibility research | temp fixture/result | `docs/architecture/extensibility-spikes.md` |
| `experiments/spikes/spike_case_c.py` | Extensibility research | read-only limitation result | `docs/architecture/extensibility-spikes.md` |
| `experiments/spikes/spike_case_d.py` | Extensibility research | temp fixture/result | `docs/architecture/extensibility-spikes.md` |
| `experiments/spikes/spike_case_e.py` | Extensibility research | temp fixture/result | `docs/architecture/extensibility-spikes.md` |

Spikeの再現手順と中断時のcleanupは
[`experiments/spikes/README.md`](experiments/spikes/README.md)に置きます。

## Cleanupの境界

- `npm run clean`: build生成物だけを削除する。
- `npm run clean:evidence`: Playwright、test result、明示したacceptance evidenceだけを削除する。
- `npm run workspace:list`: `.dev-workspaces`を変更せず一覧する。
- `npm run workspace:prune -- --database <exact-path>`: 未参照の既知branch DBだけを明示削除する。

どのcleanupも `data/source/`、`models/packages/`、`data/workbench.db`、
`.dev-workspaces/`全体を対象にしません。Workspace pruneはmain、現branch、登録済み
worktreeと、`--database`でpathを明示しない実行を拒否します。
