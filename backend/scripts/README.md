# Backend command inventory

`backend/scripts/` はアプリ本体からimportするライブラリではなく、用途と寿命が明示された
薄いCLI入口です。再利用処理は `backend/src/decision_workbench/` に置きます。
各表の `Command` は `uv run python backend/scripts/<Command>` で直接再実行できる
entrypointです。commandを追加するときは、次の表へowner、purpose／output、
参照docs／test、retention classを追加してください。

## 寿命

| Retention class | 寿命 | 削除条件 |
|---|---|---|
| `operations/` | 日常運用 | 公開commandと移行案内を同時に置換したとき |
| `generators/` | 追跡成果物の再生成期間 | provenanceを別の再生成経路へ移したとき |
| `acceptance/` | 受入契約の存続期間 | 同じ境界を検証するgateへ置換したとき |
| `experiments/` | 根拠文書の再検証期間 | 最終条件と結果を文書へ固定し、再実行が不要になったとき |
| `experiments/spikes/` | 採否判断まで | 結果文書へ採否と限界を固定したとき |

## Migration / legacy assets outside scripts

Migrationは任意実行する薄いCLIではなく、Workspace読込やshared-lab起動の一部です。
そのため `backend/scripts/` へ移動せず、次のruntime/test境界で保持します。

| Location / entrypoint | Owner | Purpose | Docs / test | Retention |
|---|---|---|---|---|
| `backend/src/decision_workbench/persistence/*_migration.py` / Store bootstrap | Workspace persistence | 既存Workspaceを現行schemaへ段階移行 | `backend/tests/test_candidate_migration.py`, `backend/tests/test_chain_catalog_migration.py`, `backend/tests/test_workspace_catalog_migration.py` | 保存済みWorkspaceを対応対象とする間 |
| `infrastructure/compose/migrations/*.sql` / Compose database startup | Shared lab persistence | shared PostgreSQL schemaを順序付きで構築 | `npm run compose:test` | 対応するshared-lab schemaの存続期間 |
| legacy identity paths / normal Workspace load | Persistence contracts | 旧identityを現在の不変snapshotへ解決 | `backend/tests/test_legacy_workspace_acceptance.py` | release済みWorkspaceの互換期間 |

## Operations

| Command / entrypoint | Owner | Purpose / output | Docs / test | Retention |
|---|---|---|---|---|
| `operations/capability_atlas.py` | Developer experience | Task／Package／Project mode capability台帳の生成・drift検出 | `npm run capability:atlas`, `backend/tests/test_capability_atlas.py` | `operations/` |
| `operations/missingness_promotion_report.py` | Missingness research | MPEA実Taskで欠損patternと補完候補のproduction昇格可否を再評価 | `docs/reports/mpea-missingness-promotion.json`, `backend/tests/test_missingness_promotion.py` | `operations/` |
| `operations/profile_workbench.py` | Data/Profile contracts | Profile検証・登録、JSON／Dataset registration | `docs/operations/data-contributor-start-here.md` | `operations/` |
| `operations/model_workflow.py` | Model lifecycle | Model診断・build・切替、外部Package／status JSON | `npm run model:*` | `operations/` |
| `operations/task_scaffold.py` | Personal Task onboarding | data-only Task／Profile／recipe scaffold | `npm run task:scaffold` | `operations/` |
| `operations/verify_model_package.py` | Model runtime | Model Package契約の検証結果 | `docs/model-runtime-examples/index.md` | `operations/` |
| `operations/task_inventory.py` | Task registry | production Task台帳の生成・drift検出 | `npm run task:inventory`, `backend/tests/test_developer_command_contracts.py` | `operations/` |
| `operations/readiness_inventory.py` | Task intake | source shape／Profile family／split policyの導入前台帳を生成 | `docs/contracts/readiness-inventory.json`, `backend/tests/test_readiness.py` | `operations/` |
| `operations/standard_estimator_readiness.py` | Model authoring | bounded Estimator／runtime／artifact台帳の生成・drift検出 | `npm run estimator:readiness:generate`, `backend/tests/test_standard_estimator_readiness.py` | `operations/` |
| `operations/developer_doctor.py` | Developer experience | 開発環境診断JSON | `npm run dev:doctor` | `operations/` |
| `operations/workspace_check.py` | Workspace preflight | Workspaceのread-only整合検査 | `npm run workspace:check` | `operations/` |
| `operations/workspace_maintenance.py` | Workspace catalog | Package登録のinspect/deactivate監査 | `npm run workspace:maintenance` | `operations/` |
| `operations/workspace_lifecycle.py` | Developer experience | branch Workspace inventory / explicit prune | `npm run workspace:list`, `npm run workspace:prune` | `operations/` |
| `operations/seed_review_workspace.py` | Review fixtures | branch既定Workspaceを固定seedへ戻す | `npm run workspace:seed` | `operations/` |
| `operations/export_openapi.py` | API contracts | OpenAPI正本・Web型の生成／drift検出 | `npm run api:generate`, `npm run api:check` | `operations/` |
| `operations/sample_gallery.py` | Sample projects | branch Workspace内の同梱sampleを管理 | `npm run samples` | `operations/` |
| `operations/exact_gp_capacity_benchmark.py` | Model capacity | Issue #780のbounded exact-GP capacity matrix、preflight、同一cohort比較を再生成 | `docs/contracts/exact-gp-capacity.md`, `docs/benchmarks/exact-gp-capacity-v1.json`, `backend/tests/test_exact_gp_capacity_benchmark.py` | `operations/` |

Profile inheritanceの展開は `operations/profile_workbench.py materialize`、source検証は
同じCLIの `validate` に統合しています。`validate` は `--profile` を省略するとProfileを
自動検出します。旧 `materialize_dataset_profile.py` と `verify_dataset_source.py` は
重複入口として削除済みです。

## Generators

| Command / entrypoint | Owner | Purpose / output | Docs / test | Retention |
|---|---|---|---|---|
| `generators/build_tutorial_dataset_revision.py` | Tutorial data | 教材Dataset revisionを再生成 | `docs/examples/tutorial-data-pipeline.md` | `generators/` |
| `generators/build_welding_consumable_sample_dataset.py` | Welding sample | 溶接sampleをderived artifactへ生成 | `npm run data:build:welding-sample` | `generators/` |
| `generators/materialize_observation_training_views.py` | Observation Profile | 観測Profileからtraining viewを生成 | `npm run data:build:welding-stage-c-views` | `generators/` |
| `generators/prepare_calce_battery_dataset.py` | External reference data | CALCE由来CSV／Profile evidenceを生成 | `docs/contracts/reference-data-loop.md` | `generators/` |
| `generators/prepare_secom_stress_dataset.py` | External reference data | SECOM由来CSV／reportを生成 | `docs/examples/external-dataset-tasks.md` | `generators/` |
| `generators/build_default_model_package.py` | Annealing model | 焼鈍data-only Model Packageを再生成 | `npm run models:build:annealed` | `generators/` |
| `generators/build_hot_rolling_model_package.py` | Hot-rolling model | 熱延data-only Model Packageを再生成 | `npm run models:build:hot-rolling` | `generators/` |
| `generators/build_flank_wear_model_package.py` | Flank-wear model | 工具摩耗data-only Model Packageを再生成 | `npm run models:build:flank-wear` | `generators/` |
| `generators/build_annealed_individual_model_packages.py` | Annealing model research | 個別観測Package群を再生成 | Package manifests | `generators/` |
| `generators/build_annealed_lightgbm_model_package.py` | Annealing model research | LightGBM Packageを再生成 | `docs/model-runtime-examples/existing-runtimes.md` | `generators/` |
| `generators/build_external_tabular_packages.py` | External tabular tasks | portable v2 Packagesを再生成 | `docs/reports/model-package-portable-digest-v2-2026-07-28.md` | `generators/` |
| `generators/build_numpyro_package_examples.py` | Runtime examples | NumPyro Package examplesを再生成 | `npm run models:build:examples` | `generators/` |
| `generators/build_posterior_linear_model_example.py` | Runtime examples | posterior-linear exampleを再生成 | `docs/model-runtime-examples/sparse-bayesian.md` | `generators/` |
| `generators/build_quantile_model_example.py` | Runtime examples | quantile-linear exampleを再生成 | `docs/model-runtime-examples/quantile-only.md` | `generators/` |
| `generators/build_additive_model_examples.py` | Runtime examples | additive-term examplesを再生成 | `docs/model-runtime-examples/additive-terms.md` | `generators/` |
| `generators/build_welding_stage_a_package.py` | Welding chain | Stage A Packageを再生成 | Package manifest | `generators/` |
| `generators/build_welding_stage_b_assets.py` | Welding chain | Stage B Dataset／Profile／Packageを再生成 | `docs/contracts/chain-evaluation.md` | `generators/` |
| `generators/build_welding_stage_c_model_package.py` | Welding chain | Stage C Packageを再生成 | `backend/tests/test_welding_stage_c.py` | `generators/` |

元データ `data/source/` は読取専用です。generatorの既定出力はderived artifactまたは
明示したPackage directoryとし、利用頻度だけを理由に追跡成果物を削除しません。

## Acceptance

| Command / entrypoint | Owner | Purpose / output | Docs / test | Retention |
|---|---|---|---|---|
| `acceptance/build_welding_chain_evaluation.py` | Chain evaluation | 固定Chain評価JSONを再構築 | `docs/contracts/chain-evaluation.md` | `acceptance/` |
| `acceptance/golden_path_smoke.py` | Model lifecycle | temp storeでModel lifecycleを受入確認 | `npm run model:golden-path:smoke` | `acceptance/` |
| `acceptance/reference_data_loop_acceptance.py` | Reference data loop | temp Workspace／Packageで参照データloopを受入確認 | `docs/contracts/reference-data-loop.md` | `acceptance/` |
| `acceptance/run_chain_degraded_e2e.py` | Chain degraded mode | Chain degraded-mode Playwrightを起動 | `playwright.chain-degraded.config.ts` | `acceptance/` |

## Experiments

各結果文書からこの表のcommandへ逆リンクします。出力の正本は
`docs/benchmarks/`, `docs/decisions/`, `docs/research/`, `docs/reports/`です。

| Command / entrypoint | Owner | Purpose / output | Docs / test | Retention |
|---|---|---|---|---|
| `experiments/analyze_secom_sensor_selection.py` | External task research | sensor-selection reportを再計算 | `docs/examples/external-dataset-tasks.md` | `experiments/` |
| `experiments/benchmark_data_lifecycle.py` | Persistence performance | lifecycle benchmark JSON／Markdownを再計測 | `docs/benchmarks/2026-07-27-data-lifecycle.md` | `experiments/` |
| `experiments/benchmark_proposal_pool.py` | Proposal performance | proposal timing tableを再計測 | `docs/benchmarks/2026-07-26-proposal-pool.md` | `experiments/` |
| `experiments/compare_annealing_feature_pipelines.py` | Feature engineering | feature pipeline comparison JSONを再計算 | `docs/reports/annealing-feature-pipeline-v4-comparison.md` | `experiments/` |
| `experiments/evaluate_sampling_strategies.py` | Screening sampling research | sampling strategy comparison JSONを再計算 | `docs/research/sampling-strategy-comparison.md` | `experiments/` |
| `experiments/evaluate_gmr_inverse.py` | Inverse proposal research | GMR inverse evaluation JSONを再計算 | `docs/research/gmr-inverse-candidate-poc.md` | `experiments/` |
| `experiments/run_generative_design_lab.py` | Generative design research | kNN／copula／diffusion候補生成の比較reportを再計算 | `docs/research/generative-design-lab-adoption-memo.md` | `experiments/` |
| `experiments/run_real_task_design_prior_replay.py` | Design-prior research | MPEA実Taskで候補生成priorのproduction昇格可否をreplay | `docs/research/real-task-design-prior-replay.md`, `backend/tests/test_real_task_design_prior_replay.py` | `experiments/` |
| `experiments/evaluate_shared_multioutput_gp.py` | Multi-output research | shared multi-output GP評価JSONを再計算 | `docs/decisions/shared-multi-output.md` | `experiments/` |
| `experiments/spikes/spike_case_a.py` | Extensibility research | case Aのtemp fixture／resultを再現 | `docs/architecture/extensibility-spikes.md` | `experiments/spikes/` |
| `experiments/spikes/spike_case_b.py` | Extensibility research | case Bのtemp fixture／resultを再現 | `docs/architecture/extensibility-spikes.md` | `experiments/spikes/` |
| `experiments/spikes/spike_case_c.py` | Extensibility research | case Cのread-only limitationを再現 | `docs/architecture/extensibility-spikes.md` | `experiments/spikes/` |
| `experiments/spikes/spike_case_d.py` | Extensibility research | case Dのtemp fixture／resultを再現 | `docs/architecture/extensibility-spikes.md`, `backend/tests/test_chain_candidate_adapters.py` | `experiments/spikes/` |
| `experiments/spikes/spike_case_e.py` | Extensibility research | case Eのtemp fixture／resultを再現 | `docs/architecture/extensibility-spikes.md` | `experiments/spikes/` |

Spikeの再現手順と中断時のcleanupは
[`experiments/spikes/README.md`](experiments/spikes/README.md)に置きます。

## Cleanupの境界

- `npm run clean`: build生成物と `backend/scripts/**/__pycache__` だけを削除する。
- `npm run clean:dry-run`: 同じ対象を一覧し、削除しない。
- `npm run clean:evidence`: Playwright、test result、明示したacceptance evidenceだけを削除する。
- `npm run workspace:list`: `.dev-workspaces`を変更せず一覧する。
- `npm run workspace:prune -- --database <exact-path>`: launcher marker付きsandboxまたは旧形式の未参照branch DBだけを明示削除する。

どのcleanupも `data/source/`、`models/packages/`、`data/workbench.db`、
`.dev-workspaces/`全体を対象にしません。Workspace pruneはmain、現branch、登録済み
worktree、branchへ対応づかないDBと、`--database`でpathを明示しない実行を拒否します。
