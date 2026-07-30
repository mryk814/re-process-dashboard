# Repository structure audit — 2026-07-30

## 結論

現状の主な問題は総ファイル数ではなく、次の二つです。

1. `backend/src/material_workbench/` の責任分割より、いくつかの巨大ファイルの
   内部責任が大きくなっている。
2. `backend/scripts/` では、日常のCLI、成果物authoring、受入、benchmarkの寿命を
   directoryと索引で継続して管理する必要がある。

さらに静的なtop-level import graphでは、
`adapters/tasks/task_modules/domain/persistence/contracts/data/modeling` が
一つの循環成分になっています。
directory名だけを整えるより、この依存方向を切る方が優先です。

この監査では、source dataやModel Packageを「利用頻度が低い」という理由で削除しません。
provenanceと再生成性を先に確認します。

## 規模

`git ls-files` とPythonの物理行数を使った時点計測です。

| 領域 | tracked files / Python lines |
|---|---:|
| Repository | 1,352 files |
| `backend/src` | 226 files |
| `backend/tests` | 104 files |
| `backend/scripts` | 44 Python files（うち`spikes/` 5） |
| `models/packages` | 319 files |
| `apps/web` | 203 files |
| `docs/learning` | 148 files |
| `backend/src/material_workbench` | 196 Python files / 61,514 lines |

`models/packages` はファイル数が多いものの、data-only artifactをdirectory contractで
保持する設計なので、source moduleの肥大と同じ問題として数えません。

## Backend package

第一階層の責任名はおおむね妥当です。
再編単位はdirectoryの改名ではなく、次の巨大ファイルから責任を抜き出すことです。

| File | Lines | 分離候補 |
|---|---:|---|
| `persistence/store.py` | 2,544 | schema/migration、project、candidate、activity、snapshot persistence |
| `application/workspace_bundle.py` | 2,105 | export、validation、restore/import |
| `contracts/schemas.py` | 1,714 | project/candidate、prediction、exploration、workspace transport |
| `data/dataset_profile.py` | 1,627 | document inheritance、schema validation、runtime mapping |
| `modeling/tabular_regression.py` | 1,539 | fit、artifact serialization、prediction |
| `application/chain_execution.py` | 1,386 | graph planning、execution、result assembly |
| `developer_experience/data_lifecycle_benchmark.py` | 1,307 | benchmark scenarioとmeasurement/report |
| `task_modules.py` | 1,165 | built-in TaskModule composition |
| `domain/services.py` | 1,136 | candidate、prediction、actual/decision services |
| `data/importer.py` | 1,086 | workbook read、relation resolution、canonical rows |

`app.py` と `task_modules.py` はpackage rootに置ける責任ではありますが、
composition rootとして読むには大きすぎます。
Taskごとのdefinitionを再び各層へ散らさず、TaskModule単位で分ける必要があります。

追跡Issue:

- [#501 Task compositionの循環依存を解消する](https://github.com/mryk814/re-process-dashboard/issues/501)
- [#502 StoreとWorkspace Bundleをtransaction境界ごとに分割する](https://github.com/mryk814/re-process-dashboard/issues/502)
- [#504 API routerをtransport境界へ薄くする](https://github.com/mryk814/re-process-dashboard/issues/504)

### 分割の原則

- public import pathを先に決め、移動と振る舞い変更を同じPRに混ぜない。
- schemaやrepositoryを「ファイル行数が均等」になるようには分けない。
  保存・復元・不変条件を共有する単位で分ける。
- 元Excel、保存済みSnapshot、Package contractにmigrationを発生させない。
- 巨大ファイルの分割後は、旧ファイルを互換shimとして残さない。

## Backend scripts

平坦な一覧だけでは用途を判断できなかったため、#503で
[`backend/scripts/README.md`](../../backend/scripts/README.md) を正本として
`operations/`、`generators/`、`acceptance/`、`experiments/`へ配置し、
全commandのowner、output、referenceを索引化しました。

呼出し箇所の検索だけでは、次のような誤判定が起きます。

- `build_tutorial_dataset_revision.py` は日常コマンドではないが、
  `material_workbench_tutorial_v2.xlsx` の再生成根拠になる。
- `build_annealed_lightgbm_model_package.py` はactive runtimeでなくても、
  追跡中のLightGBM Packageを再生成する。
- `materialize_dataset_profile.py` と `verify_dataset_source.py` は
  `operations/profile_workbench.py`へ統合し、重複scriptを削除した。
- research scriptは文書から参照されるものとtestから直接importされるものがある。

したがって、呼出し頻度を根拠とする一括削除は行いません。
directory移動は寿命を明示するために行い、CLIの再利用処理はsource packageへ置きます。

一方、現行契約に一致しないv1 Packageを固定していた`npm run dev:process`と
`scripts/dev-process-v1.ps1`は、正式入口として壊れており他の案内からも参照されないため
削除しました。
また、Dataset authoring scriptの既定出力を`artifacts/derived-data/`へ移し、
`data/source/`への書込みを拒否するようにしました。

build cleanup、evidence cleanup、明示的Workspace pruneも別commandへ分離しました。

## Agent guidance

`.claude/skills/` は二つだけで、数の過剰はありませんでした。
ただし現行リポジトリとのdriftがあり、次を修正しました。

- `add-prediction-task`: 存在しない `verify:focused` を `verify:edit` に更新。
- `add-model-runtime`: `model_packages.py` の移動後のpathを反映。

データを追加する人は `docs/operations/data-contributor-start-here.md`、
アプリを開発する人は `docs/developer-start-here.md` を入口にします。
データ追加だけの作業へ、アプリ変更用のtest一式を要求しません。

## 次の構造変更

この順で独立PRにします。

1. [#501](https://github.com/mryk814/re-process-dashboard/issues/501):
   `task_modules.py`をTask単位のcomposition moduleへ分割し、最大の循環を切る。
2. [#504](https://github.com/mryk814/re-process-dashboard/issues/504):
   APIから具象persistence/modelingへの依存をapplication use-caseへ移す。
3. [#502](https://github.com/mryk814/re-process-dashboard/issues/502):
   `persistence/store.py`と`workspace_bundle.py`をtransaction境界ごとに分割する。
scriptの追加・削除では、`backend/scripts/README.md`の寿命と索引を同時に更新します。
