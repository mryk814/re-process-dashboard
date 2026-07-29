# Backend command inventory

`backend/scripts/` は、アプリ本体からimportするライブラリではなく、開発・生成・検証の
コマンド入口を置く場所です。
ファイル数だけでは削除可否を判断できないため、各コマンドを用途と寿命で分類します。

## 置いてよいもの

- 人が明示的に実行する薄いCLI入口
- Packageや教材Datasetなど、追跡成果物を再生成するauthoring command
- release gateや再現可能なbenchmarkの入口

再利用する処理は `backend/src/material_workbench/` に置き、CLIからimportします。
新しい実装では別のscriptをライブラリ代わりにimportしないでください。
現行`task_modules.py`から一部builderを遅延importする負債は
[#501](https://github.com/mryk814/re-process-dashboard/issues/501)でsource packageへ移します。
一度限りの調査コードは、根拠文書と一緒に `spikes/` へ置くか、結果を残して削除します。

## 日常の入口

| 目的 | Command |
|---|---|
| Datasetをinspect / validate / register | `profile_workbench.py` |
| Profile inheritanceをstandalone JSONへ展開 | `profile_workbench.py materialize` |
| Dataset sourceを検証 | `verify_dataset_source.py` |
| Model workflow | `model_workflow.py` |
| Model Package単体検証 | `verify_model_package.py` |
| Task inventory | `task_inventory.py` |
| 開発環境診断 | `developer_doctor.py` |
| Workspace整合検査・保守・seed | `workspace_check.py`, `workspace_maintenance.py`, `seed_review_workspace.py` |
| OpenAPI生成 | `export_openapi.py` |
| Sample gallery | `sample_gallery.py` |

これらはREADME、`package.json`、またはrootのNode wrapperから到達できる公開入口です。
移動するときはコマンド契約と案内を同じ変更で更新します。

## 追跡成果物のauthoring

### Dataset / training view

- `build_tutorial_dataset_revision.py`
- `build_welding_consumable_sample_dataset.py`
- `materialize_observation_training_views.py`
- `prepare_calce_battery_dataset.py`
- `prepare_secom_stress_dataset.py`

### Model Package / example

- `build_additive_model_examples.py`
- `build_annealed_individual_model_packages.py`
- `build_annealed_lightgbm_model_package.py`
- `build_default_model_package.py`
- `build_external_tabular_packages.py`
- `build_flank_wear_model_package.py`
- `build_hot_rolling_model_package.py`
- `build_numpyro_package_examples.py`
- `build_posterior_linear_model_example.py`
- `build_quantile_model_example.py`
- `build_welding_stage_a_package.py`
- `build_welding_stage_b_assets.py`
- `build_welding_stage_c_model_package.py`

これらは毎日の操作ではありませんが、Dataset revisionやdata-only Model Packageを
再現するために保持します。
生成先と入力のdigestが追跡成果物のprovenanceに接続している限り、
単に呼出し回数が少ないことを理由に削除しません。

## Acceptance / fixture orchestration

- `build_welding_chain_evaluation.py`
- `golden_path_smoke.py`
- `reference_data_loop_acceptance.py`
- `run_chain_degraded_e2e.py`

通常のライブラリテストでは表現しにくい複数工程・外部データ・起動経路をまとめて検証します。
対応するgateや受入テストを置換してから削除します。

## Benchmark / research evidence

- `analyze_secom_sensor_selection.py`
- `benchmark_data_lifecycle.py`
- `benchmark_proposal_pool.py`
- `compare_annealing_feature_pipelines.py`
- `evaluate_gmr_inverse.py`
- `evaluate_shared_multioutput_gp.py`

結果の正本は `docs/benchmarks/`, `docs/decisions/`, `docs/research/` に置きます。
新しい調査scriptには、結果文書からの逆リンクまたはテストを必須とします。
再実行する意味がなくなったものは、結果文書に最終条件を残して削除します。

## Spikes

`spikes/` は採用前の境界検証です。
採用済みのproduction pathをここからimportしてはいけません。
各spikeの状態と再現手順は [`spikes/README.md`](spikes/README.md) に記録します。

## 整理するときの判定順

1. 追跡成果物のprovenanceや再生成手順から参照されているか。
2. `package.json`、verification gate、test、docsから到達できるか。
3. 実装本体を重複して持たず、薄いCLIになっているか。
4. 調査が完了済みなら、結果文書を残して削除できるか。

「ファイル名が検索に出ない」だけでは削除しません。
反対に、用途・成果物・根拠文書のどれも説明できないscriptは追加しません。
