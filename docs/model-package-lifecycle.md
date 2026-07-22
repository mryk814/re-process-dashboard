# モデルPackageのライフサイクル

## 目的

TaskDefinitionとDataset Input Profileを起点に、アプリ共通形式の学習データセットを確認し、学習、Package構築、実際の推論環境での検証、使用Packageの切替、ロールバックまでを同じ手順で再現します。

アプリ内学習、任意コードのプラグイン、自動モデル選択、リモートRegistryは扱いません。
学習済みartifactは、許可リストへ登録したアダプターだけが読み込みます。

## 標準ルート

PowerShellから次の順に実行します。
`<task>` は `annealed-properties-v1`、`hot-rolled-properties-v1`、`flank-wear-v1` のいずれかです。
タスクごとのソース、プロファイル、現在使用中のPackageは [タスク一覧](task-inventory.json) で確認できます。

`--source`を省略すると、TaskModuleに登録されたタスク固有の既定ソースを使います。
`annealed-properties-v1` と `hot-rolled-properties-v1` は既定の工程ワークブック、`flank-wear-v1` は専用の切削摩耗ワークブックへ解決されます。

```powershell
npm run model:data -- --task <task> --output artifacts/model-data/<task>.json

npm run model:build -- `
  --task <task> `
  --output models/packages/<new-package-directory> `
  --dataset-output artifacts/model-data/<task>.json

npm run model:verify -- `
  --task <task> `
  --package models/packages/<new-package-directory>

npm run model:activate -- `
  --task <task> `
  --package models/packages/<new-package-directory>

npm run task:inventory
npm run model:status
```

切削逃げ面摩耗で、既定値に依存せず使用ソースを記録したい場合は、次のように明示できます。

```powershell
$source = "data/source/cutting_tool_flank_wear_synthetic_dataset.xlsx"

npm run model:data -- `
  --task flank-wear-v1 `
  --source $source `
  --output artifacts/model-data/flank-wear-v1.json

npm run model:build -- `
  --task flank-wear-v1 `
  --source $source `
  --output models/packages/<new-package-directory> `
  --dataset-output artifacts/model-data/flank-wear-v1.json

npm run model:verify -- `
  --task flank-wear-v1 `
  --source $source `
  --package models/packages/<new-package-directory>

npm run model:activate -- `
  --task flank-wear-v1 `
  --source $source `
  --package models/packages/<new-package-directory>

npm run task:inventory
```

`model:build` はアプリ共通形式のデータセットを出力してから、タスク専用builderで学習とPackage構築を行います。
続いて、そのPackageを本番と同じ推論環境へ読み込み、スモーク結果を再現します。
既存のPackageディレクトリは、既定では上書きしません。
`--replace` は、再生成する対象を確認済みの場合だけ明示します。

使用Packageの切替後はアプリを再起動します。
`models/active-packages.json` は信頼済みPackageだけを参照し、任意の外部パスは保存しません。

## 検証されるもの

`model:verify` と使用Packageの切替時には、次の項目を本番と同じ実装で確認します。

- manifest、artifactのパス、hash、byte数
- TaskDefinitionの入力契約ダイジェスト
- RuntimeCapabilityのダイジェスト
- Dataset Input Profileのダイジェスト
- アプリ共通形式の学習データセットと元ワークブックのダイジェスト
- Feature PipelineのID、バージョン、特徴量順序
- 許可リストへ登録したアダプター、artifactの形状、有限値
- 品質レポートの目的変数、件数、MAE、RMSE、90%被覆率
- タスク専用推論環境によるスモーク `PredictiveSummary` の再現

品質レポートの数値閾値は共通CLIで固定しません。
各タスクの受入基準としてレビューします。

## ロールバック

Packageを使用対象へ切り替えると、直前の参照が `previous` に残ります。
検証後に次のコマンドで入れ替えます。

```powershell
npm run model:rollback -- --task <task>
npm run task:inventory
npm run model:status
```

切削逃げ面摩耗のロールバックも、`--source`を省略すれば同じタスク固有ソースへ解決されます。
別の検証済みソースを使う場合だけ、activate時とrollback時に同じ `--source` を明示します。

ロールバック後もアプリを再起動します。
保存済みスナップショットは再計算されず、保存時のPackage ID、バージョン、manifest hashを保持します。
プロジェクト画面では、保存時のPackageが現在と同じか別かを明示します。

## 開発時の一時上書き

設定を書き換えずに候補Packageを試す場合は、起動前に環境変数を設定します。この経路でもアプリ起動時の完全検証は省略されません。

```powershell
$env:MATERIAL_WORKBENCH_MODEL_PACKAGE = "C:\trusted-models\annealed-candidate"
$env:MATERIAL_WORKBENCH_HOT_ROLLING_MODEL_PACKAGE = "C:\trusted-models\hot-rolling-candidate"
$env:MATERIAL_WORKBENCH_FLANK_WEAR_MODEL_PACKAGE = "C:\trusted-models\flank-wear-candidate"
npm run dev
```
