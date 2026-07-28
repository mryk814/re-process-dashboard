# 拡張点インベントリ

この文書は「新しいデータ・Task・Activity・Chainを追加するとき、どのファイルが正本で、どこに登録が必要か」を計測した記録です。
リファクタリング計画そのものではなく、計画の前提となる**現状の測定結果**です。

- 対象コミット: この文書と同じcommit
- 目的: `docs/architecture/extensibility-spikes.md` の反証ケースが「どこを触るはずか」を予測できる状態にすること
- 非目的: 抽象化案の提示。共通化の是非は反証テスト後に判断します

反証ケースA〜Dは実行済みです。実測で確認できた項目には**実測済**と付けています。
結果と結論は [extensibility-spikes.md](extensibility-spikes.md) にあります。

この文書は測定時点の状態を記録しています。**P1で解消した項目もそのまま残しています**
（何をどう測ったかが、後の判断の根拠になるため）。解消済みかどうかは
[extensibility-spikes.md §6](extensibility-spikes.md#6-証拠にもとづくissue分割) を参照してください。
P1-dで `stage_c_regression.py` → `observation_regression.py`、
`stage_c_model_builder.py` → `observation_model_builder.py` へ改名しています。

`registration_point` のIDは `backend/tests/test_extensibility_registration_points.py` が参照します。
新しい登録点を作った場合、テストがこの文書の更新を要求します。

## 1. 概念ごとの正本と登録点

### 1.1 Task

| 側面 | 正本 |
| --- | --- |
| 契約定義 | [contracts/task_contracts.py](../../backend/src/material_workbench/contracts/task_contracts.py) の `TaskDefinition` / `TaskContractFixture` |
| 契約実体 | `backend/src/material_workbench/tasks/task_definitions/<task-id>.json`（13件） |
| registry | [task_modules.py:504](../../backend/src/material_workbench/task_modules.py#L504) `TASK_MODULES` |
| 集合検証 | [tasks/task_registry.py:91](../../backend/src/material_workbench/tasks/task_registry.py#L91) `TaskModule` / TaskDefinition / runtime / active packageの集合一致 |
| loader | `TaskModule.data_loader`（6系統: workbook / flank_wear / tabular / stage_c / stage_b / —） |
| persistence | `projects.task_id`（[persistence/candidate_migration.py:83](../../backend/src/material_workbench/persistence/candidate_migration.py#L83)） |
| API | `GET /api/tasks` 系（[api/catalog.py](../../backend/src/material_workbench/api/catalog.py)） |
| UI | [features/candidates/taskDefinition.ts](../../apps/web/src/features/candidates/taskDefinition.ts)、[CandidateUi.tsx](../../apps/web/src/features/candidates/CandidateUi.tsx) |
| generated schema | `apps/web/src/generated/api-types.ts`（`npm run api:generate`） |
| tests | `test_task_registry.py`、`test_task_contracts.py`、`test_external_tabular_tasks.py` |
| docs | [task-inventory.json](../contracts/task-inventory.json)（生成物）、[.claude/skills/add-prediction-task/SKILL.md](../../.claude/skills/add-prediction-task/SKILL.md) |
| packaging | `models/active-packages.json`、`models/packages/<package>/` |

**登録点（Task追加時に必ず触る箇所）**

| id | 場所 | data-onlyか |
| --- | --- | --- |
| `task.contract_json` | `tasks/task_definitions/<task-id>.json` | data |
| `task.module_entry` | `task_modules.py` の `TASK_MODULES` | code（1 entry） |
| `task.active_package` | `models/active-packages.json` | data |
| `task.package_artifact` | `models/packages/<package>/manifest.json` | data |
| `task.default_source` | `TaskModule.default_source` が指す実ファイル | data |
| `task.source_env` | `TaskModule.source_env`（環境変数名。全Taskで一意） | code |
| `task.package_override_env` | `TaskModule.package_override_env`（全Taskで一意） | code |
| `task.inventory_generated` | `docs/contracts/task-inventory.json`（`npm run task:inventory`） | 生成物 |

### 1.2 Dataset Profile

Profile familyは現在4系統あり、**共通基底型を共有していません**。`DatasetInputProfile` は tutorial / process / flank-wear 系だけの型です。

| family | 契約 | 例 |
| --- | --- | --- |
| Dataset Input Profile v2 | [data/dataset_profile.py:295](../../backend/src/material_workbench/data/dataset_profile.py#L295) `DatasetInputProfile` | `dataset-input-profile-tutorial.json`、`-process-v1`、`-flank-wear-v1` |
| Tabular Profile | [modeling/tabular_regression.py:176](../../backend/src/material_workbench/modeling/tabular_regression.py#L176) `TabularDatasetProfile` | `tabular-profile-*.json`（8件） |
| Observation Profile | [data/observation_profile.py:99](../../backend/src/material_workbench/data/observation_profile.py#L99) `ObservationDatasetProfile` | `observation-profile-welding-consumable-stage-c-v1.json` |
| Stage B Workbook Profile | [data/stage_b_training.py:71](../../backend/src/material_workbench/data/stage_b_training.py#L71) `StageBWorkbookProfile` | `welding-stage-b-profile-v1.json` |

Profileの選択は `TaskModule.data_loader` の中で `isinstance` により行われます（[task_modules.py:214](../../backend/src/material_workbench/task_modules.py#L214), [:223](../../backend/src/material_workbench/task_modules.py#L223), [:238](../../backend/src/material_workbench/task_modules.py#L238)）。中央のProfile registryは存在せず、Tabular Profileのパスは [task_modules.py:37](../../backend/src/material_workbench/task_modules.py#L37) の `_TABULAR_PROFILES` に固定されています。

**登録点**

| id | 場所 |
| --- | --- |
| `profile.document` | `backend/src/material_workbench/data/<profile>.json` |
| `profile.loader_binding` | `TaskModule.data_loader`（family別の `isinstance` 分岐） |
| `profile.tabular_path_map` | `task_modules.py` の `_TABULAR_PROFILES`（Tabular familyのみ） |
| `profile.workbench_registration` | Profile Workbench経由の登録（[data/profile_workbench.py](../../backend/src/material_workbench/data/profile_workbench.py)） |

### 1.3 Training View / データ記述子

**ここが最も不明瞭な境界です。** 各loaderが返す記述子には共通クラスがなく、共通なのは `DataDescriptor` Protocol（[task_modules.py:49](../../backend/src/material_workbench/task_modules.py#L49)）だけで、宣言されているのは6フィールドです。

```text
DataDescriptor（宣言済みの共通面）
  source_path / source_sha256 / profile_path / profile_id / observations / medians
```

実際の記述子:

| クラス | フィールド数 | 追加で持つもの |
| --- | --- | --- |
| `WorkbookData`（[data/importer.py:75](../../backend/src/material_workbench/data/importer.py#L75)） | 30 | sheets, composition, lineage, entities, relation_routes, policy_columns, … |
| `TabularData`（[modeling/tabular_regression.py:243](../../backend/src/material_workbench/modeling/tabular_regression.py#L243)） | 15 | profile, quality, detected_quality, technical_columns, lifecycle_profile |
| `StageCData`（[modeling/observation_regression.py:142](../../backend/src/material_workbench/modeling/observation_regression.py#L142)） | 16 | profile_digest, `training_dataset: ObservationTrainingDataset` |
| `FlankWearData`（[modeling/flank_wear.py:67](../../backend/src/material_workbench/modeling/flank_wear.py#L67)） | 10 | measurement_labels, run_count（**quality系を持たない**） |
| `StageBTrainingData`（[data/stage_b_training.py:142](../../backend/src/material_workbench/data/stage_b_training.py#L142)） | 9 | `TabularData` をラップ + fold/cohort digest |

観測できた具体的な帰結:

- `DataExplorationService.quality()` は `data.quality` / `data.detected_quality` / `data.technical_columns` を使いますが（[application/data_exploration.py:56](../../backend/src/material_workbench/application/data_exploration.py#L56)）、これらは `DataDescriptor` に宣言されていません。**未宣言の暗黙インターフェース**です。
- `DataExplorerEntry.data` の型注釈は `WorkbookData` ですが（[tasks/task_registry.py:56](../../backend/src/material_workbench/tasks/task_registry.py#L56)）、Tabular Taskでは実際に `TabularData` が入ります（[app.py:175](../../backend/src/material_workbench/app.py#L175)）。型注釈が実態と一致していません。
- 明示的なTraining View契約を持つのは Observation family だけです（`ObservationTrainingDataset` / `ObservationTrainingView`、[data/observation_profile.py:182](../../backend/src/material_workbench/data/observation_profile.py#L182)）。Tabular / Workbook / FlankWear familyは `observations: list[dict]` を直接学習・品質表示へ渡します。

**登録点**

| id | 場所 |
| --- | --- |
| `training_view.descriptor_class` | family別データクラス（共通基底なし） |
| `training_view.protocol_conformance` | `DataDescriptor` Protocol（6フィールドのみ） |
| `training_view.quality_surface` | `quality` / `detected_quality` / `technical_columns`（未宣言） |

### 1.4 Model Package

| 側面 | 正本 |
| --- | --- |
| 契約 | [docs/contracts/model-package-contract.md](../contracts/model-package-contract.md)、[modeling/model_packages.py](../../backend/src/material_workbench/modeling/model_packages.py) |
| adapter allow-list | `backend/src/material_workbench/adapters/`（allow-listされたadapter群） |
| lifecycle | [modeling/model_lifecycle.py](../../backend/src/material_workbench/modeling/model_lifecycle.py)、`models/active-packages.json` |
| builder | `TaskModule.model_builder` |
| Task固有分岐 | [modeling/model_lifecycle.py:236](../../backend/src/material_workbench/modeling/model_lifecycle.py#L236) に `if task_id == "annealed-properties-v1"` / `elif task_id == "hot-rolled-properties-v1"` が残存 |

**登録点**: `package.adapter_allowlist`、`package.builder`、`package.active_entry`、`package.lifecycle_task_branch`（既存の2件の分岐）

**TaskDefinitionの1フィールド変更が、そのTaskの全Packageの作り直しになります（実測済）**

[modeling/model_lifecycle.py:131](../../backend/src/material_workbench/modeling/model_lifecycle.py#L131)
`task_input_contract_digest` は `input_groups` 全体をdumpして digest にします。
そこには `training_range` も含まれます。

`training_range` は**入力契約ではなく学習データの観測結果**です。
実データに合わせて更新しただけで digest が変わり、`validate_lifecycle_metadata` が
そのTaskの全Package（active に限らず `models/available-packages.json` のものも）を拒否します。
放置すると起動時に
`WorkspaceCatalogBootstrapError: Model Packageが現在のPrediction Task契約と一致しません` になります。

実測（annealed / hot-rolled の `training_range` を4フィールド更新した場合）:

| 項目 | 実測 |
| --- | --- |
| 作り直しになるPackage | annealed系 6件 ＋ hot-rolled系 2件 |
| うちサンプリングを伴うモデル | heteroscedastic-gp / hierarchical-bayes / horseshoe（numpyro） |
| 再構築で変わったもの | **8件すべてcontract re-stampのみ**。`model-artifacts/`、`reference/training_stats.json`、`smoke/expected.json` は byte一致 |
| 変わったもの | `input_contract_digest`、`provenance.feature_dataset_id`、後から追加されたschemaフィールド |

サンプリング系も固定seedのため bit一致で再現しました。
**モデルは変わらないので、実質は契約の押し直しです。**

**`training_range` はTask単位の宣言だがPackage単位の事実です（未解決）**

1つのTaskDefinitionが複数データセットのPackageを持ちます
（annealed / hot-rolled はどちらも tutorial と process の両workbookでPackageがあります）。
`training_range` はその両方を同時に正しく記述できません。

さらに `training_range` は**応答曲線の掃引軸**を決めます
（[modeling/hot_rolling.py:424](../../backend/src/material_workbench/modeling/hot_rolling.py#L424)）。
両データセットのunionへ広げると、activeなPackageの学習範囲を超えて曲線が伸び、
支持の外まで外挿します。実測では hot-rolled の仕上げ温度が 931 → 1276 ℃ まで広がりました。

そのため現在は**activeなPackageのデータセットに合わせて**宣言しています。
別データセットのPackageを有効化すると宣言が実データより狭くなりますが、
広すぎるより狭すぎるほうが安全側です。

**本来の解決**は、UIが参照する支持範囲をTask契約ではなくPackage側
（`reference/training_stats.json`）から取ることです。
そうすれば `training_range` を入力契約から外せて、digestの結合も同時に切れます。
この状態は `backend/tests/test_training_range_contract.py` が固定しています。

**Package構築は登録の後でしかできません（実測済）**

| 場所 | 内容 |
| --- | --- |
| [modeling/tabular_model_builder.py:376](../../backend/src/material_workbench/modeling/tabular_model_builder.py#L376) | `load_task_contracts()` をcontract root注入なしで呼ぶ。TaskDefinition JSONが本番 `task_definitions/` にないとPackageを作れない |
| [modeling/model_lifecycle.py:234](../../backend/src/material_workbench/modeling/model_lifecycle.py#L234) | `task_module()` 経由でmodule-levelの `TASK_MODULES` を直接読む。`task_modules.py` へ登録する前はPackageを作れない |

未登録Taskのartifactを作れないという安全側の性質でもあるため、負債とは断定しません。
ただし「Packageを先に作って検証してから登録する」順序は取れません。

### 1.5 Runtime

| 側面 | 正本 |
| --- | --- |
| Protocol | [task_modules.py:59](../../backend/src/material_workbench/task_modules.py#L59) `PredictionRuntime` / `:74` `StageSampleRuntime` / `:90` `SupportProvider` |
| capability宣言 | `RuntimeCapability`（task_definition JSON内） |
| factory | `TaskModule.runtime_factory`（5系統） |
| 実装 | `modeling/runtime.py`, `hot_rolling.py`, `flank_wear.py`, `tabular_regression.py`, `observation_regression.py` |
| 契約整合検証 | [tasks/task_registry.py:168](../../backend/src/material_workbench/tasks/task_registry.py#L168) `_validate_runtime` |

`response_curve` / `curve_family` は capability宣言とhandlerの有無が起動時に一致検証されます（[tasks/task_registry.py:209](../../backend/src/material_workbench/tasks/task_registry.py#L209), [:216](../../backend/src/material_workbench/tasks/task_registry.py#L216)）。これは良い形の登録点です。

**runtime factoryのパラメタ化はfamilyごとに非対称です（実測済）**

| family | パラメタ化 | 2つ目のTaskを追加できるか |
| --- | --- | --- |
| Tabular | `_tabular_loader(task_id)` / `_tabular_features(task_id)` / `_tabular_builder(task_id)` / `_tabular_starter(task_id, name)` で完全にパラメタ化 | **できる**。新規Python関数0件（ケースA実測） |
| Observation | `observation_regression.TASK_ID` / `PROFILE_PATH` がmodule定数（[observation_regression.py:32](../../backend/src/material_workbench/modeling/observation_regression.py#L32)）。`observation_model_builder.build(source, destination, *, replace)` にprofile引数がない（[:225](../../backend/src/material_workbench/modeling/observation_model_builder.py#L225)） | **できない**。runtimeとbuilderのパラメタ化が先に必要（ケースB実測） |
| Workbook / FlankWear | 1 Task専用のmodule（`runtime.py` / `hot_rolling.py` / `flank_wear.py`） | 縦スライスとして意図的 |

### 1.6 Candidate Shape

**現在は単一形状で、typed unionではありません。**

| 層 | 型 | 形状 |
| --- | --- | --- |
| API入力 | `CandidateInputs`（[contracts/schemas.py:243](../../backend/src/material_workbench/contracts/schemas.py#L243)） | composition / process / categorical / heat_pattern / heat_time_basis |
| 正規形 | `CanonicalCandidate`（[contracts/task_contracts.py:357](../../backend/src/material_workbench/contracts/task_contracts.py#L357)） | 同上（heat_time_basisなし） |
| 契約側の許容path | [contracts/task_contracts.py:40](../../backend/src/material_workbench/contracts/task_contracts.py#L40) 正規表現 `^(composition\|process\|categorical)\.…$\|^heat_pattern$` |
| group key | [contracts/task_contracts.py:83](../../backend/src/material_workbench/contracts/task_contracts.py#L83) `Literal["composition","process","heat_pattern","categorical"]` |
| UI側のgroup key | [taskDefinition.ts:31](../../apps/web/src/features/candidates/taskDefinition.ts#L31) 同じ4値をSetで固定 |

共有Candidateに載っているドメイン固有フィールド:

- `CandidateInputs.heat_time_basis`（焼鈍ライン固有。[schemas.py:248](../../backend/src/material_workbench/contracts/schemas.py#L248)）
- `CandidateInput.blend: SparseBlend | None`、`editor_state: BlendEditorState`（溶接固有。[schemas.py:272](../../backend/src/material_workbench/contracts/schemas.py#L272)）
- UI側の `prominentHeatPatternInputPaths = new Set(["process.ls_mpm"])`（[CandidateUi.tsx:36](../../apps/web/src/features/candidates/CandidateUi.tsx#L36)）

**帰結**: 可変長系列・疎明細集合・複数明細集合・スペクトル・画像・グラフ・nested inputは、現在の契約では表現できません。`heat_pattern` は「最大30点の時刻―温度列」（[schemas.py:247](../../backend/src/material_workbench/contracts/schemas.py#L247)）という単一の例外形状として存在します。

### 1.7 Decision Activity

**共通名だがロバストネス専用の型です。**

| 側面 | 現状 |
| --- | --- |
| definition | [contracts/decision_activity_contracts.py:17](../../backend/src/material_workbench/contracts/decision_activity_contracts.py#L17) `DecisionActivityDefinition`（汎用） |
| registry | 同 `:189` `DECISION_ACTIVITY_REGISTRY = (ROBUSTNESS_ACTIVITY,)` — 1件 |
| request | 同 `:97` `DecisionActivityRunRequest.parameters: RobustnessParameters`（**union不可**） |
| result | 同 `:167` `DecisionActivityRun.parameters: RobustnessParameters` / `result: RobustnessSummary`（**union不可**） |
| required_operations | 同 `:13` `Literal["preview"]` のみ |
| required_resources | 同 `:14` `Literal["candidate"]` のみ |
| service | [application/decision_activities.py:230](../../backend/src/material_workbench/application/decision_activities.py#L230) `if definition != ROBUSTNESS_ACTIVITY: raise` |
| service内のTask固有処理 | 同 `:98`–`:115` `_with_values` が `process.ls_mpm` と `heat_time_basis` を直接扱う（焼鈍Task固有） |
| persistence | [persistence/decision_activity_migration.py](../../backend/src/material_workbench/persistence/decision_activity_migration.py) `decision_activity_runs` |
| API | [api/decision_activities.py](../../backend/src/material_workbench/api/decision_activities.py) 4 endpoint（activity_idはpath paramだがschemaは単一） |
| UI | [DecisionActivityPanel.tsx:103](../../apps/web/src/features/workbench/DecisionActivityPanel.tsx#L103) `"robustness-analysis-v1"` をハードコード、`:142` `robustness-parameters/v1` を直接構築 |

**登録点**: `activity.definition_entry`、`activity.parameters_union`（現在は存在しない）、`activity.result_union`（現在は存在しない）、`activity.service_dispatch`、`activity.ui_panel`

### 1.8 Design Space / Commercial Catalog

Design Spaceは**Project単位ではなく、決定論的Transform（Stage A）に紐づいています**。

| 側面 | 正本 |
| --- | --- |
| 契約 | [contracts/design_space_contracts.py:47](../../backend/src/material_workbench/contracts/design_space_contracts.py#L47) `DesignSpaceDefinition` |
| 実体 | `models/design-spaces/welding-stage-a-v1.json`, `-v2.json` のみ |
| 登録 | `models/active-transforms.json` の `design_space` / `commercial_catalog` |
| 他用途 | [application/screening.py:109](../../backend/src/material_workbench/application/screening.py#L109) が範囲探索用にTaskDefinitionから **その場で組み立てる**（保存された正本ではない） |

Chain snapshotのidentityは `design_space` と `commercial_catalog` を必須にしています（[contracts/chain_contracts.py:196](../../backend/src/material_workbench/contracts/chain_contracts.py#L196)）。つまり疎配合を持たないChainはsnapshotを保存できません。

### 1.9 Chain Stage / Chain Core

| 側面 | 正本 |
| --- | --- |
| 契約 | [contracts/chain_contracts.py](../../backend/src/material_workbench/contracts/chain_contracts.py) `ChainDefinition` / `ChainRevision` / `StageContractSurface` |
| stage種別 | 同 `:36` `Literal["task","deterministic_transform"]` — 2種のみ |
| 実行 | [application/chain_execution.py](../../backend/src/material_workbench/application/chain_execution.py)（1131行） |
| 不確かさ伝播 | [application/chain_uncertainty.py](../../backend/src/material_workbench/application/chain_uncertainty.py)（417行） |
| 評価 | [application/chain_evaluation.py](../../backend/src/material_workbench/application/chain_evaluation.py)、[modeling/chain_evaluation_builder.py:49](../../backend/src/material_workbench/modeling/chain_evaluation_builder.py#L49) |
| bootstrap | [persistence/welding_chain_bootstrap.py:31](../../backend/src/material_workbench/persistence/welding_chain_bootstrap.py#L31) |
| API | [api/chains.py](../../backend/src/material_workbench/api/chains.py)（542行、19 endpoint） |
| 実体 | Chain templateは `welding-consumable-a-b-c-v1` の1本のみ |

**Chain Coreに残る溶接固有symbol** → 第3章で全列挙します。

### 1.10 Data Explorer

| 側面 | 正本 |
| --- | --- |
| capability | [contracts/task_contracts.py:453](../../backend/src/material_workbench/contracts/task_contracts.py#L453) `DataExplorerCapability(quality, lineage, candidate_creation)` |
| 登録 | `TaskModule.data_explorer`（`_EXPLORER` / `_TABULAR_EXPLORER` の2種、[task_modules.py:501](../../backend/src/material_workbench/task_modules.py#L501)） |
| service | [application/data_exploration.py](../../backend/src/material_workbench/application/data_exploration.py) |
| lineageの前提 | `lineage_neighborhood` / `lineage_node_detail` が `WorkbookData` 固有（[data/importer.py](../../backend/src/material_workbench/data/importer.py)）。そのため `lineage=True` はWorkbook familyのみ |

### 1.11 Source Connector

**存在しません。** 現在のsource解決は次の3点で完結しています。

- `TaskModule.default_source`（リポジトリ相対パス）
- `TaskModule.source_env`（環境変数による上書き）
- [app.py:136](../../backend/src/material_workbench/app.py#L136) の `source_kind == "primary"` / `"flank_wear"` の2つの明示分岐

`source_kind` はloaderの選択には使われず、`data_by_source` のキー共有（[app.py:148](../../backend/src/material_workbench/app.py#L148)）と `app.state.data` の既定選択（[app.py:228](../../backend/src/material_workbench/app.py#L228) の `get("primary")`）にのみ使われます。定期取得・スナップショット・外部接続の仕組みはありません（計画P3のまま）。

## 2. Profile familyごとの共通出力差分

反証ケースBとPhase 2.1（Canonical Training View境界）の判断材料です。
「学習・品質表示・Data Libraryが、familyのどこを直接見ているか」を比較します。

| 消費側 | 参照フィールド | Workbook | Tabular | StageC | FlankWear | StageB |
| --- | --- | --- | --- | --- | --- | --- |
| `DataDescriptor`（宣言済み） | `source_path` `source_sha256` `profile_path` `profile_id` `observations` `medians` | ✓ | ✓ | ✓ | ✓ | ✓（内部の`TabularData`経由） |
| 品質表示 | `quality` `detected_quality` `technical_columns` | ✓ | ✓ | ✓ | **✗** | ✓ |
| 学習行 | `observations[*]["features"] / ["outputs"] / ["eligible"]` | ✓ | ✓ | ✓ | ✓ | ✓ |
| 学習単位 | `parent_key` / `condition_context_id` | ✓（`training_context_key`） | `group_column`任意 | family別 | run別 | cohort別 |
| 明示Training View契約 | `ObservationTrainingDataset` | ✗ | ✗ | **✓** | ✗ | ✓（`stage_b_inspection_dataset`で変換） |
| lineage | `lineage` `entities` `relation_routes` | **✓** | ✗ | ✗ | ✗ | ✗ |
| fold / cohort identity | `fold_digests` `cohort_digests` | ✗ | ✗ | ✗ | ✗ | **✓** |
| Profile型 | `profile` | `DatasetInputProfile` | `TabularDatasetProfile` | `ObservationDatasetProfile` | `DatasetInputProfile` | `TabularDatasetProfile`（変換後） |

読み取れること:

1. **共通なのは「観測行の平坦なdict列」だけ**で、それは契約ではなく慣習です。`observations[*]` のキー構成はfamilyごとにloaderが決めています。
2. 品質表示は3フィールドの暗黙インターフェースに依存し、`FlankWearData` がそれを満たさないため `data_explorer=None` という**データではなくコードの判断**で回避されています。
3. Training Viewを型で持つのはObservation familyだけで、Stage Bはそこへ**変換**しています（[data/stage_b_training.py:153](../../backend/src/material_workbench/data/stage_b_training.py#L153)）。つまり共通境界の候補は既に存在しますが、全familyが通っていません。
4. Profile形式を統合する必要はありませんが、**「loaderの戻り値の共通契約」は現在ほぼ空**です。Phase 2.1で狭めるべきはProfileではなくこの境界です。
5. **Observation familyのProfile契約とTraining View契約は溶接語彙なしで再利用できます（実測済）**。`build_observation_training_dataset` はfamily idで分岐せず、target別cohortが入力行の適格性と別に数えられます。よって共通境界は**新設ではなくこの契約の昇格**で足ります。
6. 単位変換を実際に適用しているのは Dataset Input Profile family（`dataset_profile.py` の `unit_conversion`）だけです。Observation familyは `source_unit` / `canonical_unit` を宣言できますが、[observation_profile.py:452](../../backend/src/material_workbench/data/observation_profile.py#L452)–`:464` は生値をそのまま入れます（実測済）。現行の溶接Profileが宣言しているのは相対的なラベル付け替えのみなので**実バグではなく潜在リスク**です。

## 3. Chain Coreに残る溶接固有symbol（全列挙）

`chain_execution.py` / `chain_uncertainty.py` / `chain_contracts.py` を対象に、
「溶接／疎配合を前提にしないChainでは成立しない記述」を列挙します。

| # | 場所 | 内容 | 汎用Chainでの帰結 |
| --- | --- | --- | --- |
| 1 | [chain_execution.py:70](../../backend/src/material_workbench/application/chain_execution.py#L70)–`:80` `_external_values` | `candidate.process` / `candidate.categorical` の全キーを `candidate.welding_context.*` と `candidate.test_context.*` **両方**へ複製 | 外部入力pathの名前空間が溶接語彙に固定。他分野は `welding_context.` を名乗るしかない |
| 2 | [chain_execution.py:73](../../backend/src/material_workbench/application/chain_execution.py#L73) | `candidate.blend` を `candidate.blend` pathへ露出 | 疎配合以外の外部入力形状がない |
| 3 | [chain_execution.py:250](../../backend/src/material_workbench/application/chain_execution.py#L250)–`:258` `_deterministic_stage` | 決定論的Stageが**ちょうど1段**必要 | 決定論的Stageなしの二段Chainは候補契約・初期候補APIが成立しない |
| 4 | [chain_execution.py:263](../../backend/src/material_workbench/application/chain_execution.py#L263) `prepare_candidate` | `blend is None` を即エラー | 疎配合なしのChain候補を保存できない |
| 5 | [chain_execution.py:328](../../backend/src/material_workbench/application/chain_execution.py#L328) `_resolve` | 同じく `blend is None` を即エラー | 実行・snapshot・variantの全経路が疎配合必須 |
| 6 | [chain_execution.py:168](../../backend/src/material_workbench/application/chain_execution.py#L168) `starter_candidate` | `port.path == "candidate.blend"` をskipし、残りを `process` / `categorical` へ振り分け | 初期候補が「配合＋スカラーcontext」形状に固定 |
| 7 | [chain_execution.py:229](../../backend/src/material_workbench/application/chain_execution.py#L229) | `CandidateInputs(composition={}, …, heat_time_basis="line_speed")` を常に構築 | Chain候補は組成空・焼鈍時間基準という無意味な既定値を持つ |
| 8 | [chain_execution.py:414](../../backend/src/material_workbench/application/chain_execution.py#L414)–`:424` `_run_stage` | 決定論的Stage出力を `material_composition` + `auxiliary_features` の2キーと仮定 | 決定論的変換の出力schemaがStage A固有 |
| 9 | [chain_execution.py:436](../../backend/src/material_workbench/application/chain_execution.py#L436) | Stage実行時に `heat_pattern: None`、`blend: None` を強制 | 系列入力を持つStageをChainへ入れられない |
| 10 | [chain_execution.py:456](../../backend/src/material_workbench/application/chain_execution.py#L456)–`:463` `_outputs_from_payload` | #8と同じ2キー仮定（復元経路） | 同上 |
| 11 | [chain_execution.py:1002](../../backend/src/material_workbench/application/chain_execution.py#L1002)–`:1012` `snapshot` | `blend.design_space` と `blend.commercial_catalog` からsnapshot identityを構築 | 疎配合のないChainはsnapshotを作れない |
| 12 | [chain_execution.py:1071](../../backend/src/material_workbench/application/chain_execution.py#L1071)–`:1079` `actual_conditioned_variant` | 中間実測の対象を `composition.` prefix のbindingに限定 | 中間実測が「組成」であるChainしか実測条件付き解析ができない |
| 13 | [chain_execution.py:1057](../../backend/src/material_workbench/application/chain_execution.py#L1057) | 終端Stage固定（`revision.stages[-1]`）＋変数名 `stage_c` | 3段A→B→Cを前提とした命名と構造 |
| 14 | [chain_contracts.py:196](../../backend/src/material_workbench/contracts/chain_contracts.py#L196)–`:205` `ChainSnapshotIdentity` | `design_space` / `commercial_catalog` が**必須フィールド** | #11の契約側の根拠。Core契約に疎配合が漏れている |
| 15 | [chain_contracts.py:244](../../backend/src/material_workbench/contracts/chain_contracts.py#L244)–`:252` `basis()` | 単位文字列に `"whole wire"` / `"deposited metal"` を検出して物理基準を決定 | binding検証の基準判定が溶接語彙依存 |
| 16 | [chain_contracts.py:259](../../backend/src/material_workbench/contracts/chain_contracts.py#L259)–`:264` `task_contract_surface` | 必須 `heat_pattern` 入力を持つTaskをChain surfaceから拒否 | 可変長系列Stageが原理的に入らない（意図的な明示制約） |
| 17 | [chain_contracts.py:18](../../backend/src/material_workbench/contracts/chain_contracts.py#L18) `ChainPort.value_kind` | `Literal["number","categorical","sparse_blend"]` | Chain Portの値種に `sparse_blend` が入っている |
| 18 | [chain_uncertainty.py:89](../../backend/src/material_workbench/application/chain_uncertainty.py#L89)–`:92` `_point_estimates` | #8と同じ2キー仮定 | 不確かさ伝播も決定論的Stage形状に依存 |
| 19 | [chain_uncertainty.py:310](../../backend/src/material_workbench/application/chain_uncertainty.py#L310) | サンプリング用candidateで `blend: None` を強制 | Chain Coreが常にblendフィールドの存在を意識している |
| 20 | [welding_chain_bootstrap.py:31](../../backend/src/material_workbench/persistence/welding_chain_bootstrap.py#L31)–`:32` | Stage B / Stage C のtask idを定数化し起動時に登録 | Chain templateの登録経路が溶接専用 |
| 21 | [chain_evaluation_builder.py:49](../../backend/src/material_workbench/modeling/chain_evaluation_builder.py#L49)–`:50` | 同じ2つのtask idを定数化 | Chain評価成果物の生成も溶接専用 |

**分類**

- **Chain Coreに残すべき（汎用）**: stage順序、binding解決、単位変換、部分再計算、freshness、stale応答拒否、memo、generation競合制御、provenance — `_canonical_input` / `_binding_value` / `_memo_key` / `_retained` / `save_chain_execution_if_current` 周辺は溶接非依存です。
- **domain adapterへ出すべき**: #1〜#8, #10〜#15, #17〜#19（14件）
- **意図的に残す明示制約**: #16（系列StageはCandidate Shape拡張が前提。今は失敗させるのが正しい）
- **縦スライスとして残してよい**: #20, #21（溶接Chainのbootstrapと評価）

**実測で確認できた範囲（ケースD）**

疎配合なしの二段Chainを実際に組んだ結果、**塞がったのは6点だけ**でした。

| 実測で塞がった | #3, #4, #5（候補層）、#1（`_external_values`）、#11, #14（snapshot identity） |
| --- | --- |
| **実測で変更不要と確認できた** | `ChainDefinition` / binding検証 / `build_chain_revision` / store登録 / Chain Project作成 / `api/chains.py` / `welding_chain_bootstrap.py` |
| 実測に到達しなかった | #2, #6〜#8, #10, #12, #13, #15, #17〜#19（候補を保存できず実行層へ到達しないため） |

重要な追加知見: **#1の名前空間固定は契約ではなく実装1関数に閉じています。**
`candidate.process.barrel_temperature_c` という外部入力pathで `validate_chain_definition` は通り、
`_external_values` だけがそのpathを生成しませんでした。切り離しやすい箇所です。

## 4. Decision Activity追加時の変更点

「候補差分説明」を2つ目のActivityとして追加する場合に必要な変更を列挙します。

| # | ファイル | 変更内容 | 種別 |
| --- | --- | --- | --- |
| 1 | `contracts/decision_activity_contracts.py` | 新しい `*Parameters` / `*Summary` を追加 | 追加 |
| 2 | 同上 `:97` `DecisionActivityRunRequest` | `parameters` を discriminated union へ変更 | **既存契約の破壊的変更** |
| 3 | 同上 `:167` `DecisionActivityRun` | `parameters` / `result` を union へ変更 | **既存契約の破壊的変更** |
| 4 | 同上 `:13`–`:14` | `ActivityOperation` / `ActivityResource` の Literal を拡張 | 既存変更 |
| 5 | 同上 `:189` `DECISION_ACTIVITY_REGISTRY` | entry追加 | 追加 |
| 6 | `application/decision_activities.py:230` | `if definition != ROBUSTNESS_ACTIVITY` の分岐を差し替え | **既存service分岐の追加** |
| 7 | 同上 `:334` `_validate_tolerances` | ロバストネス専用の前処理をActivity別へ分離 | 既存変更 |
| 8 | 同上 `:98` `_with_values` | 焼鈍固有の `ls_mpm` / `heat_time_basis` 処理をどこが持つか再決定 | 既存変更 |
| 9 | `api/decision_activities.py` | request/response modelがunionを受けるよう更新 | 既存変更 |
| 10 | `persistence/decision_activity_migration.py` | 保存済みrunのresult_kind別読み出し | 既存変更 |
| 11 | `apps/web/src/features/workbench/DecisionActivityPanel.tsx:103` | `"robustness-analysis-v1"` ハードコードの解消、Activity別surface | **既存UI分岐の追加** |
| 12 | `apps/web/src/generated/api-types.ts` | `npm run api:generate` | 生成物 |
| 13 | `backend/tests/test_decision_activities.py` | Activity別テスト | 追加 |

**計測結果**: 新規Activity1件の追加に対し、**既存契約の破壊的変更が2件、既存分岐への追加が3件**必要です。
計画§2.4（typed union化）は、2つ目のActivityを作る**前に**行うべきという結論になります。後からでは保存済みrunのschemaが混ざります。

## 5. まとめ（この時点で言えること）

| 領域 | 状態 |
| --- | --- |
| 標準表形式Taskの追加 | **実測済：data-onlyに近い**。既存ファイル変更2件（`task_modules.py` の `TASK_MODULES` と `_TABULAR_PROFILES`、`models/active-packages.json`）、新規Python関数0件、API/UI分岐0件で、preview・response curve・類似観測・品質表示・学習データInspector・ロバストネス・範囲探索がすべて動作。loader / feature builder / model builder / starterは `task_id` でパラメタ化済みのfactory（[task_modules.py:207](../../backend/src/material_workbench/task_modules.py#L207), [:303](../../backend/src/material_workbench/task_modules.py#L303), [:357](../../backend/src/material_workbench/task_modules.py#L357), [:420](../../backend/src/material_workbench/task_modules.py#L420)）を再利用できる |
| Profile family | 4系統。**統合不要だが、loader戻り値の共通契約がほぼ空**。Observation familyのProfile契約とTraining View契約は実測で再利用可能（昇格候補） |
| Observation family実装 | **実測済：契約は汎用、実装は単一インスタンス**。runtimeとbuilderが1 Task / 1 Profileに固定されており、2つ目を追加できない |
| Candidate Shape | 単一形状。unionではない。共有schemaに焼鈍・溶接固有フィールドが混在。**実測済：可変長系列は7項目すべて表現できない** |
| Decision Activity | 共通名でロバストネス専用型。2件目の追加前にunion化が必要 |
| Chain | **実測済：契約層は汎用、候補層が溶接固有**。疎配合なしの二段Chainは6点で塞がる。Definition / binding / Revision / store登録 / Project作成は変更不要 |
| Source Connector | 未実装（計画通りP3） |
| Design Space | Project単位ではなくStage Aに紐づく |

実測結果の詳細と、証拠にもとづくIssue分割は [extensibility-spikes.md](extensibility-spikes.md) にあります。
