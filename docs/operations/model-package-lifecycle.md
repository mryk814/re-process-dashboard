# モデルPackageのライフサイクル

## 目的

TaskDefinitionとDataset Input Profileを起点に、アプリ共通形式の学習データセットを確認し、学習、Package構築、実際の推論環境での検証、使用Packageの切替、ロールバックまでを同じ手順で再現します。

アプリ内学習、任意コードのプラグイン、自動モデル選択、リモートRegistryは扱いません。
学習済みartifactは、許可リストへ登録したアダプターだけが読み込みます。

承認済みSource LifecycleからPackage、Project、Actualまでを接続する代表例は
[CALCE電池データのSourceから実測評価までの参照ループ](../contracts/reference-data-loop.md)
を参照してください。この経路でも自動学習・自動active化は行わず、
Training Snapshot digest、materialization adapter version、materialized training
assetのSHA-256、training builder revisionごとに新しい不変Packageを作ります。
Training Snapshot v2は目的変数別cohortとgroup splitの完全な割当まで固定しますが、
Feature Pipeline定義は持ちません。
Feature Pipelineの入力path、変換、特徴量順序とdigestはModel Packageが固定し、
Package provenanceがTraining Snapshot digestを参照します。
この分離により、同じcohortで特徴量だけを変えた比較ではSnapshotを改変せず、
異なるPackageとして検証できます。

## 標準ルート

PowerShellから次の順に実行します。
`<task>` は `annealed-properties-v1`、`hot-rolled-properties-v1`、`flank-wear-v1` のいずれかです。
タスクごとのソース、プロファイル、現在使用中のPackageは [タスク一覧](../contracts/task-inventory.json) で確認できます。

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
`models/packages/`に登録済みのPackageは、`--replace`を指定しても上書きできません。
契約、学習データ、成果物のどれかが変わる場合は、新しいPackage IDとディレクトリを使います。
`--replace`を使えるのは、`artifacts/model-package-candidates/`などの未登録の作業出力だけです。

使用Packageの切替後はアプリを再起動します。
`models/active-packages.json` は信頼済みPackageだけを参照し、任意の外部パスは保存しません。

## 焼鈍特性の標準モデル

焼鈍特性では、次の2種類を「まず試す」標準モデルとして用意します。どちらも工程条件ごとの平均を一行として学習し、同じ親条件に属する反復測定を行分割しません。

### GP（安定ARD）

- 入力は学習データの列ごとの平均と標準偏差で標準化する
- 目的変数もハイパーパラメータ推定中は標準化し、artifactへ保存するときに元単位へ戻す
- RBFカーネルの長さ尺度は特徴量ごとに持つ（ARD）
- 長さ尺度は共通中心へ弱く縮約し、情報の弱い特徴量が極端な尺度を取るのを抑える
- L-BFGS-Bを決定的な3初期値から実行し、周辺尤度が最良の解を採用する
- 観測ノイズの初期値と弱い基準値には、同一親条件内の反復測定分散を使う

最適化診断はPackage内の `reports/training-diagnostics.json` に保存します。標準化後のハイパーパラメータだけを推定し、推論結果の値と標準偏差は元の目的変数単位で返します。

### LightGBM

- 小規模データ向けに浅い葉数、最小葉データ数、L1/L2正則化、行・列サンプリングを固定する
- 親条件単位の決定的な5-fold交差検証とearly stoppingで木の本数を決める
- 最終モデルは全学習行で再学習する
- 予測区間はout-of-fold残差の標準偏差による正規近似として校正する

LightGBMの予測区間は、GPのような入力位置ごとの潜在不確かさではありません。学習範囲外の不確かさを評価したい場合はGPを優先し、LightGBMは非線形な点予測の堅実な比較対象として使います。

ここでの品質指標は合成デモデータ上の動作確認です。材料的な因果や実データでの優劣を示すものではありません。

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

## activeの切替は必ずコマンドを通す

`models/active-packages.json` の `active` を直接編集しないでください。
`previous` を書くのは `npm run model:activate`（`set_active_package`）だけで、
手編集した場合は `previous` が置き去りになり、以後 `npm run model:rollback` が
「no previous active package is recorded」で必ず失敗します。

直接編集は `npm run task:inventory:check` と `npm run dev:doctor` が検出します。
検査はgit履歴上で `active` が最後に変わった版を取り出し、そこで置き換えられたPackageが
いまも `npm run model:activate` で戻せる状態（`models/packages/` に存在し、同じTaskで、
現在のTaskDefinitionと入力契約が一致する）なら、`previous` がそれを指していることを求めます。

`previous: null` が正しい場合もあります。切替が一度も無いTask、そして戻し先のPackageが
削除された、別TaskのPackageだった、入力契約の移行で現行TaskDefinitionと一致しなくなった場合です。
このときは戻し先が存在しないため、検査は理由付きで正当と報告します。
復旧は旧版への `rollback` ではなく、現行契約を満たすPackageを `model:build` して
`model:activate` する経路になります。

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

`rollback` は戻し先のPackageを `activate` と同じ完全検証にかけます。
検証に落ちる版へは戻しません。

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
