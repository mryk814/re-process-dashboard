# Model Package lifecycle

## 目的

TaskDefinitionとDataset Input Profileを起点に、Canonical training datasetの確認、学習、Package構築、実runtimeでの検証、active切替、rollbackまでを同じ手順で再現します。

アプリ内学習、任意コードplugin、自動モデル選択、remote registryは扱いません。学習済みartifactはallow-list adapterだけが読み込みます。

## 標準ルート

PowerShellから次の順に実行します。`<task>` は `annealed-properties-v1`、`hot-rolled-properties-v1`、`flank-wear-v1` のいずれかです。taskごとのsource/profile/active Packageは [Task inventory](task-inventory.json) で確認できます。

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

npm run model:status
```

`model:build` はCanonical datasetを出力してからtask-specific builderで学習・Package構築を行い、そのPackageをproduction runtimeへ読み込んでsmokeを再現します。既存Package directoryは既定で上書きしません。明示した `--replace` は、再生成する対象を確認済みの場合だけ使います。

active切替後はアプリを再起動します。`models/active-packages.json` は信頼済みPackageだけを指し、任意の外部pathは保存しません。

## 検証されるもの

`model:verify` とactive化時の検証は、次を同じproduction実装で確認します。

- manifest、artifact path、hash、bytes
- TaskDefinition input contract digest
- RuntimeCapability digest
- Dataset Input Profile digest
- Canonical training dataset digestとsource workbook digest
- Feature Pipeline id/version/feature順序
- allow-list adapterとartifact shape/finite値
- quality reportのtarget、件数、MAE、RMSE、90% coverage
- task-specific runtimeによるsmoke `PredictiveSummary` 再現

quality reportの数値閾値は共通CLIで固定しません。各タスクの受入基準としてレビューします。

## rollback

active化すると直前のPackage参照が `previous` に残ります。検証後に次で入れ替えます。

```powershell
npm run model:rollback -- --task <task>
npm run model:status
```

rollback後もアプリを再起動します。保存済みsnapshotは再計算されず、保存時のPackage ID・version・manifest hashを保持します。プロジェクト画面では、保存時のPackageが現在と同じか別かを明示します。

## 開発時の一時override

設定を書き換えずに候補Packageを試す場合は、起動前に環境変数を設定します。この経路でもアプリ起動時の完全検証は省略されません。

```powershell
$env:MATERIAL_WORKBENCH_MODEL_PACKAGE = "C:\trusted-models\annealed-candidate"
$env:MATERIAL_WORKBENCH_HOT_ROLLING_MODEL_PACKAGE = "C:\trusted-models\hot-rolling-candidate"
$env:MATERIAL_WORKBENCH_FLANK_WEAR_MODEL_PACKAGE = "C:\trusted-models\flank-wear-candidate"
npm run dev
```
